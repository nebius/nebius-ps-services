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
    if not native["observer_complete"] or not native["cleanup_verified"]:
        return {"status": "PARTIAL", "reason": "native observation or cleanup is incomplete"}
    if native["exit_code"] != 0 or not native["completed"] or native["forced_cleanup"]:
        return {"status": "FAIL", "reason": "public native invocation did not complete"}
    if binding is None:
        return {"status": "FAIL", "reason": "public native invocation completed without an accepted objective"}
    return None


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
    return {
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
                peers = {task: native for task, native in old.items()
                         if current.get(task) == native}
                # Termination can precede admission by several observations.
                # Keep the departure, never stale running status as overlap.
                for task in set(old) - set(current) - set(prior["admitted"]):
                    if peers and prior["ready"]:
                        candidates[task] = {"peers": peers.copy(), "ready": set(prior["ready"])}
            for task, candidate in list(candidates.items()):
                candidate["peers"] = {peer: native for peer, native in candidate["peers"].items()
                                      if current.get(peer) == native}
                if not candidate["peers"] or task in current:
                    candidates.pop(task)
                    continue
                if task not in wave["admitted"]:
                    # Starting a replacement before admission is not a refill
                    # after admission, even if a later snapshot contains both.
                    candidate["ready"].difference_update(current)
                elif candidate["ready"] & set(current):
                    refill = True
                    candidates.pop(task)
                    continue
                if not candidate["ready"]:
                    candidates.pop(task)
            previous[wave["wave"]] = wave
    return maximum, refill


def codex_command(project, home):
    """Keep the sandbox while permitting this owned fixture's Git transactions."""
    config_path = home / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    if any(key in config for key in ("sandbox_mode", "sandbox_workspace_write")):
        raise ValueError("isolated native home must omit legacy sandbox settings before using permission profiles")
    # A forced workspace-write mode makes .git read-only even inside writable
    # roots. Extend the native profile and reopen only this fixture's Git data.
    filesystem = ", ".join(
        json.dumps(str(path)) + ' = "write"' for path in (home, project / ".git")
    )
    profile = '{ extends = ":workspace", filesystem = { ' + filesystem + ' }, network = { enabled = true } }'
    return [
        "codex", "app-server", "--strict-config",
        "-c", 'default_permissions="implement-test"',
        "-c", "permissions.implement-test=" + profile,
    ]


def validate_codex_permissions(config, project, home):
    """Reject effective policy drift without exposing any configuration values."""
    if any(config.get(key) is not None for key in ("sandbox_mode", "sandbox_workspace_write")):
        raise ValueError("effective native config contains legacy sandbox settings")
    expected = {"extends": ":workspace", "filesystem": {
        str(home): "write", str(project / ".git"): "write"}, "network": {"enabled": True}}
    def omit_nulls(value):
        if isinstance(value, dict):
            return {key: omit_nulls(item) for key, item in value.items() if item is not None}
        return value

    if (config.get("default_permissions") != "implement-test"
            or omit_nulls(config.get("permissions", {}).get("implement-test")) != expected):
        raise ValueError("effective native config changed the scoped permission profile")


def invoke(project, home, host, text, timeout):
    if host == "codex":
        return invoke_codex(project, home, text, timeout)
    return invoke_stream(project, home, host, text, timeout)


def invoke_codex(project, home, text, timeout):
    from native_app_server import NativeServer, child_snapshot

    started = time.monotonic()
    session = None
    completed = finished = timed_out = failed = False
    transitions = 0
    samples, gaps, rounds = [], 0, 0
    diagnostics = {"failed_commands": 0, "intake_command_mentions": 0, "codes": []}

    def notify(event):
        nonlocal completed, finished, transitions
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
                          runtime_environment("codex", home=home, fresh_session=True), notify,
                          deadline=started + timeout)
    try:
        server.request("initialize", {"clientInfo": {"name": "implement-test", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
        server.send({"method": "initialized"})
        config = server.request("config/read", {"cwd": str(project), "includeLayers": False})["config"]
        validate_codex_permissions(config, project, home)
        session = server.request("thread/start", {"cwd": str(project), "ephemeral": True,
                                                  "permissions": "implement-test"})["thread"]["id"]
        server.request("turn/start", {"threadId": session, "input": [{"type": "text", "text": text}]})
        next_sample = 0
        while not finished:
            if time.monotonic() - started >= timeout:
                timed_out = True
                break
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
                    first = sample(home, session, project, running, elapsed)
                    second = sample(home, session, project, running, elapsed)
                    checked = child_snapshot(server, session, deadline)
                    # Fresh reads bracket product state. Any native transition,
                    # inventory change, state race or slow round invalidates it.
                    if (before != transitions or children != checked or first != second
                            or time.monotonic() - observed_at > .5):
                        gaps += 1
                    elif first is not None and (not samples or first["waves"] != samples[-1]["waves"]):
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
        "schema": "implement-test/native-invocation-v2", "host": "codex",
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
    }, session


def invoke_stream(project, home, host, text, timeout):
    command = [host, "--print", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence"]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=project,
        env=runtime_environment(host, home=home, fresh_session=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
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
    try:
        while not (
            process.poll() is not None and output_done.is_set() and events.empty()
        ):
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
                                failed.set()
                        else:
                            gaps += 1
                    except (OSError, ValueError, RuntimeError):
                        gaps += 1  # Observation races must not stop the product.
            if time.monotonic() - started > timeout:
                timed_out = True
                break
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
        "schema": "implement-test/native-invocation-v2",
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
        receipt.get("schema") != "implement-test/public-invocations-v2"
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
            or value.get("schema") != "implement-test/native-invocation-v2"
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
    if maximum < 2 or not refill or first["session_sha256"] == again["session_sha256"]:
        raise ValueError(
            "native overlap, slot refill or fresh-session continuation was not observed"
        )
    if any(
        sample["run_id"] != run_dir.name or sample["objective_sha256"] != objective_sha
        for sample in first["samples"]
    ):
        raise ValueError("native observation belongs to a changed objective")
    return {"observed_max_workers": maximum, "observed_slot_refill": refill}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--objective", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    host = agent_name()
    root = lifecycle.default_root()
    if not 1 <= args.timeout <= 7200:
        raise ValueError("timeout must be between 1 and 7200 seconds")
    trial = None
    try:
        with lifecycle._locked_root(root) as owned:
            active = lifecycle._active(owned)
            if active is None:
                raise ValueError("prepare the owned fixture first")
            state, fixture = active
            lifecycle._validate_generation(state, args.generation)
            trial = lifecycle.public_trial_lock(fixture)
            trial.__enter__()
            home, project = fixture / (host + "-home"), fixture / "project"
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
                },
                exclusive=True,
            )
        prefix = "$implement" if host == "codex" else "/implement"
        first, session = invoke(project, home, host, prefix + " " + text, args.timeout)
        write(evidence / "initial-invocation.json", first, exclusive=True)
        binding = (
            observe_binding(home, "implement", session, project) if session else None
        )
        failure = initial_failure(first, binding)
        if failure:
            print(json.dumps({**failure, "diagnostics": first.get("diagnostics", {})}))
            return 2
        run_dir, run, objective = binding
        if run["status"] != "done":
            print(
                json.dumps(
                    {"status": "PARTIAL", "reason": "public workflow did not deliver"}
                )
            )
            return 2
        delivered = digest(read(run_dir / "execution/FEAT-001/coordinator.json"))
        again, fresh = invoke(
            project, home, host, prefix + " continue " + str(project), args.timeout
        )
        write(evidence / "continuation-invocation.json", again, exclusive=True)
        continued = (
            observe_binding(home, "implement", fresh, project) if fresh else None
        )
        same = (
            continued is not None
            and continued[0] == run_dir
            and continued[1]["status"] == "done"
            and continued[2]["revisions"][-1]["sha256"]
            == objective["revisions"][-1]["sha256"]
        )
        with lifecycle._locked_root(root) as owned:
            current = lifecycle._active(owned)
            if current is None or current[1] != fixture:
                raise ValueError("fixture generation changed during native execution")
            lifecycle._validate_generation(current[0], args.generation)
            receipt = {
                "schema": "implement-test/public-invocations-v2",
                "skill": "implement",
                "run_id": run_dir.name,
                "objective_sha256": objective["revisions"][-1]["sha256"],
                "entry": "direct",
                "intervened": False,
                "rerun_status": "ALREADY_COMPLETE" if same else "UNPROVEN",
                "delivery_sha256": delivered,
                "initial": {"path": "initial-invocation.json", "sha256": digest(first)},
                "continuation": {
                    "path": "continuation-invocation.json",
                    "sha256": digest(again),
                },
            }
            # Invocation evidence belongs to the verifier generation. Keep it
            # beside the immutable native observations, outside product state.
            path = evidence / "public-invocations.json"
            write(path, receipt, exclusive=True)
            try:
                observed = validate_receipt(
                    path, run_dir, objective["revisions"][-1]["sha256"]
                )
            except ValueError as error:
                print(json.dumps({"status": "PARTIAL", "reason": str(error)}))
                return 2
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "run_dir": str(run_dir),
                    "receipt": str(path),
                    **observed,
                }
            )
        )
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
