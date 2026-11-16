#!/usr/bin/env python3
"""Invoke public Implement and retain immutable, bounded native observations.

This observer never performs private workflow transitions. Native event output is
hashed and discarded; only session hashes, statuses, bounded diagnostics and
scheduler samples remain. Diagnostic mentions never certify workflow transitions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib

import implement_lifecycle as lifecycle
from agent_runtime import agent_name, runtime_environment

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worktree/scripts"))
from workflow_objectives import observe_binding, session_hash, validate_run
from workflow_state import read, write, digest


DIAGNOSTIC_CODES = frozenset({
    "BASIS_STALE", "BINDING_INVALID", "CLEANUP_BLOCKED", "COMPLETION_BLOCKED",
    "CONTINUE_REQUIRED", "EXECUTION_FENCED", "NATIVE_IDENTITY_REQUIRED",
    "OBJECTIVE_ACTIVE", "OBJECTIVE_INVALID", "OWNERSHIP_INVALID", "POLICY_BLOCK",
    "PROMOTION_BLOCKED", "RECOVERY_REQUIRED", "REPLAN_REQUIRED", "SENSITIVE_INPUT",
    "SOURCE_DIRTY", "SOURCE_DRIFT", "SPEC_DRIFT", "STATE_INVALID", "UNSAFE_STATE",
    "UNSUPPORTED_GIT_STATE", "WORKFLOW_BUSY", "WORKFLOW_UPGRADE_REQUIRED",
    "WORKER_SCOPE_VIOLATION", "WRITERS_ACTIVE",
})

OUTER_MEMORY_HANDOFF = (
    "Verifier environment: the outer verifier owns physical-host memory monitoring "
    "for this disposable trial, with a fresh reliable prelaunch observation and an "
    "active bounded monitor throughout the native sessions. It owns exact workload "
    "cancellation at the existing critical/unknown visibility boundary. Repeat this "
    "setup context in every worker assignment. Sandboxed workers may rely on that "
    "active outer owner without launching a duplicate host-memory probe; do not "
    "claim the worker measured the host. For any tool call, preserve output and "
    "exit status before reading its optional session handle. An immediately "
    "completed command has no running session: never store or poll a missing handle."
)


def diagnostic_metadata(event, host):
    """Extract a finite vocabulary, never retain arbitrary output or commands."""
    failed, mentions, output = 0, 0, ""
    item = event.get("item", {}) if host == "codex" else {}
    if event.get("type") == "item.completed":
        if item.get("type") == "command_execution":
            failed = int(isinstance(item.get("exit_code"), int) and item["exit_code"] != 0)
            mentions = int("workflow_intake.py" in str(item.get("command", "")))
            output = str(item.get("aggregated_output", ""))
        elif item.get("type") == "agent_message":
            output = str(item.get("text", ""))
    words = set(re.findall(r"\b[A-Z][A-Z_]+\b", output))
    codes = words & DIAGNOSTIC_CODES
    if failed and any(message in output for message in (
        "Operation not permitted", "Permission denied", "SandboxDenied",
    )):
        codes.add("PERMISSION_DENIED")
    return {"failed_commands": failed, "intake_command_mentions": mentions, "codes": sorted(codes)}


def initial_failure(native, binding):
    if native["exit_code"] != 0 or native["forced_cleanup"]:
        return {"status": "FAIL", "reason": "public native invocation failed or required forced cleanup"}
    if not native["observer_complete"] or not native["cleanup_verified"]:
        return {"status": "PARTIAL", "reason": "native observation or cleanup is incomplete"}
    if not native["completed"]:
        return {"status": "FAIL", "reason": "public native invocation did not complete"}
    if binding is None:
        return {"status": "FAIL", "reason": "public native invocation completed without an accepted objective"}
    return None


def combined_outcome(results):
    """An observed failed attempt cannot be hidden by incomplete peer evidence."""
    statuses = {value["status"] for value in results}
    return "FAIL" if "FAIL" in statuses else "PARTIAL" if statuses != {"PASS"} else "PASS"


def observe_event(event, host):
    """Allowlist metadata only; no messages, commands, arguments or tool outputs."""
    result = {"session": None, "children": {}, "complete": False,
              "diagnostics": diagnostic_metadata(event, host)}
    if host == "claude":
        if event.get("type") == "system" and event.get("subtype") == "init":
            result["session"] = event.get("session_id")
        result["complete"] = (
            event.get("type") == "result"
            and event.get("subtype") == "success"
            and event.get("is_error") is False
        )
        # Claude tool-use IDs are not native worker identities. Do not invent a mapping.
    return result


def sample(home, session, project, children, elapsed):
    binding = observe_binding(home, "implement", session, project)
    if binding is None:
        return None
    run_dir, run, objective = binding
    waves = []
    for path in sorted((run_dir / "execution").glob("*/waves/*.json")):
        wave = read(path)
        running = {}
        for task_id in wave.get("active_task_ids", []):
            task_file = (
                path.parent.parent / "tasks" / wave["wave_id"] / (task_id + ".json")
            )
            if task_file.exists():
                task = read(task_file)
                native = task.get("worker_session_hash")
                if (
                    task.get("status") == "running"
                    and children.get(native) == "running"
                ):
                    running[task_id] = native
        admissions = {
            task: digest(read(path.parent.parent / "admissions" / (task + ".json")))
            for task in wave.get("admitted_task_ids", [])
        }
        waves.append(
            {
                "wave": path.parent.parent.name + "/" + wave["wave_id"],
                "native_running": running,
                "admitted": admissions,
                "ready": wave.get("ready_task_ids", []),
            }
        )
    capacity_path = home / "workflow-capacity/reservations.json"
    capacity = read(capacity_path)["runs"] if capacity_path.exists() else {}
    admitted = sorted(key for key, row in capacity.items() if row["admitted"])
    worker_count = sum(len(row["workers"]) for row in capacity.values())
    worktree_count = sum(len(row["worktrees"]) for row in capacity.values())
    return {
        "shared_admitted": admitted, "shared_workers": worker_count,
        "shared_worktrees": worktree_count,
        "elapsed_ms": elapsed,
        "run_id": run_dir.name,
        "objective_sha256": objective["revisions"][-1]["sha256"],
        "waves": waves,
    }


def concurrency(samples):
    maximum, refill = 0, False
    previous, departed = {}, {}
    for snapshot in samples:
        for wave in snapshot["waves"]:
            current = wave["native_running"]
            if len(set(current.values())) != len(current):
                raise ValueError("multiple tasks claim one native worker")
            maximum = max(maximum, len(current))
            candidates = departed.setdefault(wave["wave"], {})
            prior = previous.get(wave["wave"])
            if prior:
                old = prior["native_running"]
                # Native departure alone does not release the task's slot.
                # Keep its ready queue until independently observed admission.
                for task in set(old) - set(current) - set(prior["admitted"]):
                    if prior["ready"]:
                        candidates[task] = {"peers": None, "ready": set(prior["ready"])}
            for task, candidate in list(candidates.items()):
                if task in current:
                    candidates.pop(task)
                    continue
                if candidate["peers"] is None:
                    # A replacement must first appear in a later snapshot than
                    # admission; one sample cannot establish their ordering.
                    candidate["ready"].difference_update(current)
                    if task in wave["admitted"]:
                        candidate["peers"] = {
                            peer: native for peer, native in prior["native_running"].items()
                            if current.get(peer) == native
                        }
                    if not candidate["ready"] or candidate["peers"] == {}:
                        candidates.pop(task)
                    continue
                candidate["peers"] = {peer: native for peer, native in candidate["peers"].items()
                                      if current.get(peer) == native}
                if not candidate["peers"] or task not in wave["admitted"]:
                    candidates.pop(task)
                    continue
                if candidate["ready"] & set(current):
                    refill = True
                    candidates.pop(task)
            previous[wave["wave"]] = wave
    return maximum, refill


def permission_roots(project, home):
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, check=True, timeout=30)
    repo = Path(result.stdout.strip()).resolve()
    common = subprocess.run(["git", "-C", str(project), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True, timeout=30)
    return (home, repo, Path(common.stdout.strip()), repo.parent / (repo.name + "-worktrees"))


def codex_command(project, home):
    """Keep the sandbox while permitting this owned fixture's Git transactions."""
    config_path = home / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    if any(key in config for key in ("sandbox_mode", "sandbox_workspace_write")):
        raise ValueError("isolated native home must omit legacy sandbox settings before using permission profiles")
    # A forced workspace-write mode makes .git read-only even inside writable
    # roots. Extend the native profile and reopen only this fixture's Git data.
    filesystem = ", ".join(
        json.dumps(str(path)) + ' = "write"' for path in permission_roots(project, home)
    )
    profile = '{ extends = ":workspace", filesystem = { ' + filesystem + ' }, network = { enabled = true } }'
    return [
        "codex", "app-server", "--strict-config",
        "-c", 'default_permissions="implement-test"',
        "-c", "permissions.implement-test=" + profile,
    ]


def validate_codex_permissions(config, project, home):
    """Reject effective policy drift without exposing any configuration values."""
    if config.get("features", {}).get("hooks") is not True:
        raise ValueError("effective native configuration disabled required hooks")
    if any(config.get(key) is not None for key in ("sandbox_mode", "sandbox_workspace_write")):
        raise ValueError("effective native config contains legacy sandbox settings")
    expected = {"extends": ":workspace", "filesystem": {
        **{str(path): "write" for path in permission_roots(project, home)}}, "network": {"enabled": True}}
    def omit_nulls(value):
        if isinstance(value, dict):
            return {key: omit_nulls(item) for key, item in value.items() if item is not None}
        return value

    if (config.get("default_permissions") != "implement-test"
            or omit_nulls(config.get("permissions", {}).get("implement-test")) != expected):
        raise ValueError("effective native config changed the scoped permission profile")


def invoke(project, home, host, text, observation_interval, *, on_launch=None):
    started = time.monotonic_ns()
    options = {"on_launch": on_launch} if on_launch else {}
    value, session = (invoke_codex(project, home, text, observation_interval, **options) if host == "codex"
                      else invoke_stream(project, home, host, text, observation_interval, **options))
    value.update(started_monotonic_ns=started, finished_monotonic_ns=time.monotonic_ns())
    return value, session


def invoke_codex(project, home, text, observation_interval, *, on_launch=None):
    from native_app_server import LaunchTrace, NativeServer, child_snapshot

    started = time.monotonic()
    session = None
    completed = finished = timed_out = failed = False
    transitions = 0
    samples, gaps, rounds = [], 0, 0
    trace = LaunchTrace()
    diagnostics = {"failed_commands": 0, "intake_command_mentions": 0, "codes": []}

    def notify(event):
        nonlocal completed, finished, transitions
        trace.observe(event, session, int((time.monotonic() - started) * 1000))
        if "id" in event:
            return  # Request metadata only; NativeServer rejects the interaction.
        method, params = event.get("method"), event.get("params", {})
        if method in {"thread/status/changed", "turn/started", "turn/completed"}:
            transitions += 1
        if method == "turn/completed" and params.get("threadId") == session:
            finished = True
            completed = params.get("turn", {}).get("status") == "completed"
        if method == "item/completed":
            item = params.get("item", {})
            mapped = {"type": "item.completed", "item": {
                "type": {"commandExecution": "command_execution", "agentMessage": "agent_message"}.get(item.get("type")),
                "exit_code": item.get("exitCode"), "command": item.get("command", ""),
                "aggregated_output": item.get("aggregatedOutput", ""), "text": item.get("text", ""),
            }}
            delta = diagnostic_metadata(mapped, "codex")
            for key in ("failed_commands", "intake_command_mentions"):
                diagnostics[key] = min(100000, diagnostics[key] + delta[key])
            diagnostics["codes"] = sorted(set(diagnostics["codes"]) | set(delta["codes"]))

    server = NativeServer(codex_command(project, home), project,
                          runtime_environment("codex", home=home, fresh_session=True, cwd=project), notify,
                          deadline=None)
    try:
        if on_launch:
            on_launch(server.process.pid)
        server.request("initialize", {"clientInfo": {"name": "implement-test", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
        server.send({"method": "initialized"})
        config = server.request("config/read", {"cwd": str(project), "includeLayers": False})["config"]
        validate_codex_permissions(config, project, home)
        session = server.request("thread/start", {"cwd": str(project), "ephemeral": True,
                                                  "permissions": "implement-test"}, timeout=None)["thread"]["id"]
        server.request("turn/start", {"threadId": session, "input": [{"type": "text", "text": text}]}, timeout=None)
        next_sample = 0
        next_observation = time.monotonic() + observation_interval
        while not finished:
            if time.monotonic() >= next_observation:
                print("Native implementation continues; observation checkpoint.", flush=True)
                next_observation = time.monotonic() + observation_interval
            server.receive(.2)
            if time.monotonic() >= next_sample and not finished:
                rounds += 1
                try:
                    before = transitions
                    observed_at = time.monotonic()
                    deadline = observed_at + .5
                    children = child_snapshot(server, session, deadline)
                    running = {session_hash(native): "running" for native, status in children.items() if status == "active"}
                    elapsed = int((observed_at - started) * 1000)
                    trace.children(children, elapsed)
                    first = sample(home, session, project, running, elapsed)
                    second = sample(home, session, project, running, elapsed)
                    checked = child_snapshot(server, session, deadline)
                    # Fresh reads bracket product state. Any native transition,
                    # inventory change, state race or slow round invalidates it.
                    if (before != transitions or children != checked or first != second
                            or time.monotonic() - observed_at > .5):
                        gaps += 1
                    elif first is not None and (not samples or any(
                        first[key] != samples[-1][key]
                        for key in ("waves", "shared_admitted", "shared_workers", "shared_worktrees")
                    )):
                        if len(samples) >= 256:
                            raise ValueError("native sample inventory exceeds its bound")
                        samples.append(first)
                except (OSError, ValueError, RuntimeError):
                    gaps += 1
                next_sample = time.monotonic() + 2
    except (OSError, ValueError, KeyError, TypeError):
        failed = True
    finally:
        server.close()
    return {
        "schema": "implement-test/native-invocation-v3", "host": "codex",
        "observer": "owning-app-server", "observation_rounds": rounds,
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "session_sha256": session_hash(session) if session else None,
        "process_group": server.process.pid, "exit_code": server.process.returncode,
        "completed": completed, "timed_out": timed_out,
        "observer_complete": not failed, "cleanup_verified": server.cleanup_verified,
        "forced_cleanup": server.forced_cleanup, "sampling_gaps": gaps,
        "stdout_sha256": server.stdout_hash.hexdigest(), "stderr_sha256": server.stderr_hash.hexdigest(),
        "elapsed_ms": int((time.monotonic() - started) * 1000), "samples": samples,
        "diagnostics": diagnostics,
        "launch_diagnostics": trace.result(gaps, not failed and not timed_out),
    }, session


def invoke_stream(project, home, host, text, observation_interval, *, on_launch=None):
    if on_launch:
        raise ValueError("registered companion launch requires Codex App Server")
    command = [host, "--print", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence"]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=project,
        env=runtime_environment(host, home=home, fresh_session=True, cwd=project),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        umask=0o077,
    )
    events = queue.Queue(maxsize=256)
    stream_hash, errors_hash = hashlib.sha256(), hashlib.sha256()
    output_done, errors_done, input_done = (threading.Event() for _ in range(3))
    failed = threading.Event()

    def output():
        try:
            while line := process.stdout.readline(1024 * 1024 + 1):
                stream_hash.update(line)
                if len(line) > 1024 * 1024:
                    failed.set()
                    while line and not line.endswith(b"\n"):
                        line = process.stdout.readline(1024 * 1024 + 1)
                        stream_hash.update(line)
                    continue
                try:
                    parsed = observe_event(json.loads(line), host)
                    parsed["observed_at"] = time.monotonic()
                    events.put_nowait(parsed)
                except (
                    ValueError,
                    UnicodeError,
                    TypeError,
                    AttributeError,
                    queue.Full,
                ):
                    failed.set()
        finally:
            output_done.set()

    def errors():
        try:
            while block := process.stderr.read(65536):
                errors_hash.update(block)
        finally:
            errors_done.set()

    def send_input():
        try:
            process.stdin.write(text.encode())
            process.stdin.close()
        except (BrokenPipeError, OSError):
            failed.set()
        finally:
            input_done.set()

    threads = [
        threading.Thread(target=fn, daemon=True) for fn in (output, errors, send_input)
    ]
    for thread in threads:
        thread.start()
    session, samples, gaps = None, [], 0
    diagnostics = {"failed_commands": 0, "intake_command_mentions": 0, "codes": []}
    completed = timed_out = False
    cleanup_verified = forced_cleanup = False
    exit_observed = None
    next_observation = time.monotonic() + observation_interval
    try:
        while not (
            process.poll() is not None and output_done.is_set() and events.empty()
        ):
            if process.poll() is not None:
                exit_observed = time.monotonic() if exit_observed is None else exit_observed
                if time.monotonic() - exit_observed >= 1 and not output_done.is_set():
                    failed.set()  # Leader ended; inherited pipes require owned cleanup.
                    break
            if time.monotonic() >= next_observation:
                print("Native implementation continues; observation checkpoint.", flush=True)
                next_observation = time.monotonic() + observation_interval
            try:
                parsed = events.get(timeout=0.2)
            except queue.Empty:
                parsed = None
            if parsed:
                session = parsed["session"] or session
                completed |= parsed["complete"]
                for counter in ("failed_commands", "intake_command_mentions"):
                    diagnostics[counter] = min(100000, diagnostics[counter] + parsed["diagnostics"][counter])
                diagnostics["codes"] = sorted(set(diagnostics["codes"]) | set(parsed["diagnostics"]["codes"]))
                # One fresh native observation must cover every counted worker.
                # Never union old per-worker statuses into synthetic overlap.
                if (
                    session
                    and parsed["children"]
                    and time.monotonic() - parsed["observed_at"] <= 0.5
                ):
                    try:
                        elapsed = int((time.monotonic() - started) * 1000)
                        first = sample(
                            home, session, project, parsed["children"], elapsed
                        )
                        second = sample(
                            home, session, project, parsed["children"], elapsed
                        )
                        if first is not None and first == second:
                            if len(samples) < 256:
                                samples.append(first)
                            else:
                                gaps += 1
                        else:
                            gaps += 1
                    except (OSError, ValueError, RuntimeError):
                        gaps += 1  # Observation races must not stop the product.
    finally:
        # This process owns a new native process group, including descendants
        # which may still hold pipes after the group leader has exited.
        for sig, delay in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            try:
                os.killpg(process.pid, sig)
                forced_cleanup = True
            except ProcessLookupError:
                break
            except PermissionError:
                failed.set()
                break
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                except PermissionError:
                    failed.set()
                    break
                time.sleep(0.05)
        try:
            process.wait(timeout=2)
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            cleanup_verified = True
        except PermissionError:
            failed.set()
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                failed.set()
        for thread in threads:
            thread.join(timeout=2)
        if not all(event.is_set() for event in (input_done, output_done, errors_done)):
            failed.set()
        for event, stream in (
            (input_done, process.stdin),
            (output_done, process.stdout),
            (errors_done, process.stderr),
        ):
            if event.is_set():
                try:
                    stream.close()
                except (OSError, BrokenPipeError):
                    failed.set()
    return {
        "schema": "implement-test/native-invocation-v3",
        "host": host,
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "session_sha256": session_hash(session) if session else None,
        "process_group": process.pid,
        "exit_code": process.returncode,
        "completed": completed,
        "timed_out": timed_out,
        "observer_complete": not failed.is_set(),
        "cleanup_verified": cleanup_verified,
        "forced_cleanup": forced_cleanup,
        "sampling_gaps": gaps,
        "stdout_sha256": stream_hash.hexdigest(),
        "stderr_sha256": errors_hash.hexdigest(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "samples": samples,
        "diagnostics": diagnostics,
    }, session


def validate_receipt(receipt_path, run_dir, objective_sha):
    receipt = read(receipt_path)
    if (
        receipt.get("schema") != "implement-test/public-invocations-v3"
        or receipt.get("skill") != "implement"
        or receipt.get("run_id") != run_dir.name
        or receipt.get("objective_sha256") != objective_sha
        or receipt.get("intervened") is not False
        or receipt.get("entry") != "direct"
        or receipt.get("rerun_status") != "ALREADY_COMPLETE"
    ):
        raise ValueError("fresh public workflow invocation identity is invalid")
    if digest(read(run_dir / "execution/FEAT-001/coordinator.json")) != receipt.get(
        "delivery_sha256"
    ):
        raise ValueError("completed continuation changed delivery evidence")
    artifacts = []
    for name in ("initial", "continuation"):
        row = receipt.get(name, {})
        if row.get("path") != name + "-invocation.json":
            raise ValueError("native receipt path is invalid")
        value = read(receipt_path.parent / row["path"])
        if (
            digest(value) != row.get("sha256")
            or value.get("schema") != "implement-test/native-invocation-v3"
            or value.get("exit_code") != 0
            or value.get("completed") is not True
            or value.get("timed_out") is not False
            or value.get("observer_complete") is not True
            or value.get("cleanup_verified") is not True
            or value.get("forced_cleanup") is not False
            or re.fullmatch("[0-9a-f]{64}", str(value.get("session_sha256"))) is None
        ):
            raise ValueError("native invocation did not complete or changed")
        artifacts.append(value)
    first, again = artifacts
    binding = read(
        run_dir.parent.parent / "bindings" / (again["session_sha256"] + ".json")
    )
    run, objective = validate_run(run_dir)
    if (
        binding.get("schema") != "workflow/session-binding-v1"
        or binding.get("run_dir") != str(run_dir)
        or binding.get("session_sha256") != again["session_sha256"]
        or binding.get("objective_id") != objective["objective_id"]
        or run.get("coordinator_session_sha256") != again["session_sha256"]
        or run["status"] != "done"
        or objective["revisions"][-1]["sha256"] != objective_sha
    ):
        raise ValueError("fresh continuation did not bind the same terminal objective")
    maximum, refill = concurrency(first["samples"])
    app = Path(run.get("logical_project_root", "")).name == "project-a"
    if ((app and (maximum < 2 or not refill)) or first["session_sha256"] == again["session_sha256"]):
        raise ValueError(
            "native overlap, slot refill or fresh-session continuation was not observed"
        )
    if any(
        sample["run_id"] != run_dir.name or sample["objective_sha256"] != objective_sha
        for sample in first["samples"]
    ):
        raise ValueError("native observation belongs to a changed objective")
    return {"observed_max_workers": maximum, "observed_slot_refill": refill}


def run_project(project, home, host, text, observation_interval, evidence, *, on_launch=None, verify_frozen=None):
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    prefix = "$implement" if host == "codex" else "/implement"
    options = {"on_launch": on_launch} if on_launch else {}
    initial = prefix + " " + text + "\n\n" + OUTER_MEMORY_HANDOFF
    if verify_frozen:
        verify_frozen()
    first, session = invoke(project, home, host, initial, observation_interval, **options)
    write(evidence / "initial-invocation.json", first, exclusive=True)
    binding = observe_binding(home, "implement", session, project) if session else None
    failure = initial_failure(first, binding)
    if failure:
        return {**failure, "diagnostics": first.get("diagnostics", {})}
    run_dir, run, objective = binding
    if run["status"] != "done":
        # A complete successful native invocation was already observed above.
        # Returning without its promised delivery is an attempted failure.
        return {"status": "FAIL", "reason": "public workflow did not deliver"}
    delivered = digest(read(run_dir / "execution/FEAT-001/coordinator.json"))
    if verify_frozen:
        verify_frozen()
    again, fresh = invoke(project, home, host, prefix + " continue " + str(project), observation_interval, **options)
    write(evidence / "continuation-invocation.json", again, exclusive=True)
    continued = observe_binding(home, "implement", fresh, project) if fresh else None
    same = (continued is not None and continued[0] == run_dir and continued[1]["status"] == "done"
            and continued[2]["revisions"][-1]["sha256"] == objective["revisions"][-1]["sha256"])
    receipt = {"schema": "implement-test/public-invocations-v3", "skill": "implement",
               "run_id": run_dir.name, "objective_sha256": objective["revisions"][-1]["sha256"],
               "entry": "direct", "intervened": False,
               "rerun_status": "ALREADY_COMPLETE" if same else "UNPROVEN", "delivery_sha256": delivered,
               "initial": {"path": "initial-invocation.json", "sha256": digest(first)},
               "continuation": {"path": "continuation-invocation.json", "sha256": digest(again)}}
    path = evidence / "public-invocations.json"
    write(path, receipt, exclusive=True)
    observed = validate_receipt(path, run_dir, objective["revisions"][-1]["sha256"])
    return {"status": "PASS", "run_dir": str(run_dir), "receipt": str(path), **observed}


def validate_shared_capacity(samples, runs):
    """Require overlap and all declared home limits, independent of product caps."""
    error = "shared admission overlap or capacity proof is missing or invalid"
    if not isinstance(samples, list) or not samples:
        raise ValueError(error)
    overlap = False
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError(error)
        admitted = sample.get("shared_admitted")
        if (not isinstance(admitted, list) or len(admitted) > 4
                or any(not isinstance(item, str) or not item for item in admitted)
                or len(admitted) != len(set(admitted))):
            raise ValueError(error)
        for field, limit in (("shared_workers", 4), ("shared_worktrees", 16)):
            if type(sample.get(field)) is not int or not 0 <= sample[field] <= limit:
                raise ValueError(error)
        overlap |= set(runs) <= set(admitted)
    if not overlap:
        raise ValueError(error)


def validate_projects(evidence, repo, home):
    from workflow_automatic import verify_delivery
    runs, initial = [], []
    for scope, folder in (("project-a", evidence), ("project-b", evidence / "project-b")):
        receipt = read(folder / "public-invocations.json")
        candidates = list((home / "implement").glob("*/" + receipt["run_id"]))
        if len(candidates) != 1:
            raise ValueError("project invocation has no unique registered run")
        run_dir = candidates[0]
        run, objective = validate_run(run_dir)
        if (run.get("logical_project_root") != str(repo / scope)
                or Path(run["git_common_dir"]) != repo / ".git" or not run.get("automatic_outer")):
            raise ValueError("multi-project registration identity differs")
        validate_receipt(folder / "public-invocations.json", run_dir, objective["revisions"][-1]["sha256"])
        verify_delivery(run_dir)
        from implement_statistics import require_complete
        require_complete(run_dir)
        runs.append(str(run_dir))
        initial.append(read(folder / "initial-invocation.json"))
    if initial[0]["session_sha256"] == initial[1]["session_sha256"]:
        raise ValueError("projects reused a native coordinator")
    if max(x["started_monotonic_ns"] for x in initial) >= min(x["finished_monotonic_ns"] for x in initial):
        raise ValueError("project native invocations did not overlap")
    samples = [sample for value in initial for sample in value["samples"]]
    validate_shared_capacity(samples, runs)
    for argument, expected in (("  Hello   WORLD  ", "hello world\n"), ("", "\n"),
                               ("One\tTwo\nTHREE", "one two three\n")):
        check = subprocess.run([sys.executable, "-I", "text_tool.py", argument], cwd=repo / "project-b",
                               capture_output=True, text=True, timeout=30, check=True)
        if check.stdout != expected or check.stderr:
            raise ValueError("companion CLI acceptance failed")
    return {"status": "PASS", "projects": runs, "shared_objective_limit": 4,
            "shared_worker_limit": 4, "shared_worktree_limit": 16}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--objective", type=Path, required=True)
    parser.add_argument("--observation-interval", type=int, default=30)
    args = parser.parse_args()
    host = agent_name()
    root = lifecycle.default_root()
    if not 1 <= args.observation_interval <= 300:
        raise ValueError("observation interval must be between 1 and 300 seconds")
    trial = None
    try:
        with lifecycle._locked_root(root) as owned:
            active = lifecycle._active(owned)
            if active is None:
                raise ValueError("prepare the owned fixture first")
            state, fixture = active
            lifecycle._validate_generation(state, args.generation)
            catalog_sha = lifecycle.catalog.validate(state, fixture, native=True)
            trial = lifecycle.public_trial_lock(fixture)
            trial.__enter__()
            home, project = fixture / (host + "-home"), fixture / "project"
            hooks = lifecycle.catalog.runtime_hooks(home, host)
            if "runtime_hooks" in state:
                raise ValueError("native hook binding already exists; create a fresh trial")
            state["runtime_hooks"] = hooks
            lifecycle._atomic_json(fixture / "lifecycle.json", state)
            text = args.objective.read_text()
            if len(text.encode()) > 128 * 1024:
                raise ValueError("fixture objective exceeds the bound")
            evidence = fixture / "evidence"
            write(
                evidence / "public-trial-reservation.json",
                {
                    "schema": "implement-test/public-trial-reservation-v1",
                    "generation_id": args.generation,
                    "objective_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "runtime_catalog_sha256": catalog_sha,
                },
                exclusive=True,
            )
        from concurrent.futures import ThreadPoolExecutor
        def verify_frozen():
            with lifecycle._locked_root(root) as owned:
                current = lifecycle._active(owned)
                if current is None or current[1] != fixture:
                    raise ValueError("fixture generation changed during native execution")
                lifecycle._validate_generation(current[0], args.generation)
                lifecycle.catalog.validate(current[0], fixture, native=True)
                lifecycle.catalog.validate_runtime_hooks(current[0], fixture, required=True)
        companion = ("Implement text_tool.py as a CLI taking one string argument; normalize whitespace "
                     "and lowercase it, print the normalized string followed by a newline. Add unittest "
                     "coverage for mixed case, repeated whitespace and empty input. Own only project-b. "
                     "Plan one task with at most one active worker; existing owner-authorized recovery "
                     "may replace a failed worker for that same task, preserving failure history and "
                     "repair budgets. Freeze Python validation commands with -B immediately after "
                     "the interpreter, including Python child commands used by tests, so validation "
                     "does not create out-of-claim bytecode caches. Do not rely on inherited "
                     "PYTHONDONTWRITEBYTECODE in the explicit validation environment or broaden write "
                     "claims for generated caches. Complete automatic aggregation and cleanup, then stop. "
                     "Do not push, publish, invoke continuation, run Docker or touch project-a.")
        with ThreadPoolExecutor(max_workers=2) as pool:
            app = pool.submit(run_project, project / "project-a", home, host, text, args.observation_interval, evidence,
                              verify_frozen=verify_frozen)
            peer = pool.submit(run_project, project / "project-b", home, host, companion, args.observation_interval, evidence / "project-b",
                               verify_frozen=verify_frozen)
            results = [app.result(), peer.result()]
        if any(value["status"] != "PASS" for value in results):
            print(json.dumps({"status": combined_outcome(results), "projects": results}))
            return 2
        with lifecycle._locked_root(root) as owned:
            current = lifecycle._active(owned)
            if current is None or current[1] != fixture:
                raise ValueError("fixture generation changed during native execution")
            lifecycle._validate_generation(current[0], args.generation)
            lifecycle.catalog.validate(current[0], fixture, native=True)
            lifecycle.catalog.validate_runtime_hooks(current[0], fixture, required=True)
            observed = validate_projects(evidence, project, home)
            write(evidence / "projects.json", observed, exclusive=True)
        print(json.dumps({"status": "PASS", **results[0], "projects": results}))
        return 0
    finally:
        if trial is not None:
            trial.__exit__(None, None, None)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)[:200]}))
        raise SystemExit(2)
