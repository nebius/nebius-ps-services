#!/usr/bin/env python3
"""Own a native job's stage lifecycle and reader-facing result document.

Native commands remain in the selected batch file. Original measurements are
retained unchanged; this document is an index and explanation, not publication
input. Scheduler accounting remains authoritative after abrupt termination.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from course_execution import capabilities, option_values, parse, select

SCHEMA = "course-job-result/v1"
FINALIZATION_SECONDS = 60
MAX_JSON_BYTES = 8 * 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def private_directory(path: Path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(f"Unsafe output directory: {path.name}")
    status = path.stat()
    if (
        not path.is_dir()
        or status.st_uid != os.getuid()
        or status.st_mode & 0o777 != 0o700
    ):
        raise ValueError(
            f"Run lab preparation: require private owned directory {path.name}"
        )


def atomic_write(path: Path, document: dict):
    private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("Result must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".result-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def time_limit_seconds(value: str) -> int:
    """Parse Slurm's minutes, minutes:seconds, hours:minutes:seconds or days form."""
    if not re.fullmatch(r"(?:\d+-)?\d+(?::\d+){0,2}", value):
        raise ValueError("Unusable scheduler time limit")
    days, clock = value.split("-", 1) if "-" in value else ("0", value)
    fields = [int(item) for item in clock.split(":")]
    if "-" in value:
        fields += [0] * (3 - len(fields))
    elif len(fields) == 1:
        fields = [0, fields[0], 0]
    elif len(fields) == 2:
        fields = [0, *fields]
    return int(days) * 86400 + fields[0] * 3600 + fields[1] * 60 + fields[2]


def scheduler_budget(job: str, required: int):
    # Query the actual job, so sbatch --time overrides cannot silently shorten
    # an existing watchdog. This read does not alter scheduler state.
    result = subprocess.run(
        ["scontrol", "show", "job", "--oneliner", job],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    match = re.search(r"\bTimeLimit=(\S+)", result.stdout)
    if not match:
        raise ValueError("Cannot establish the job's scheduler time limit")
    if match[1] != "UNLIMITED" and time_limit_seconds(match[1]) < required:
        raise ValueError(
            f"Scheduler time limit must allow {required} seconds including teardown and JSON finalization"
        )


def runtime_scope(scope: dict, allocation: dict) -> dict:
    """Resolve fabric steps that intentionally use every allocated node."""
    if scope.get("node_source") != "allocation":
        return dict(scope)
    value = allocation.get("SLURM_JOB_NUM_NODES")
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError("Cannot establish the fabric allocation's node count")
    nodes = int(value)
    maximum = scope["nodes"]
    if not 1 <= nodes <= maximum or scope["tasks"] % maximum:
        raise ValueError("Fabric allocation is outside its supported node scope")
    tasks = nodes * (scope["tasks"] // maximum)
    return {
        **scope,
        "nodes": nodes,
        "tasks": tasks,
        "description": f"{nodes} node(s), {tasks} task(s), {scope['gpus_per_task']} GPU(s) per task",
    }


def strict_json(value: str):
    def invalid_constant(token):
        raise ValueError(f"Non-finite JSON value: {token}")

    def finite_float(token):
        value = float(token)
        if not math.isfinite(value):
            return invalid_constant(token)
        return value

    return json.loads(value, parse_constant=invalid_constant, parse_float=finite_float)


def scalar(value: str):
    try:
        return strict_json(value)
    except (ValueError, TypeError):
        return value


def text_adapter(path: Path) -> dict | None:
    """Adapt native CUDA/trial key=value output or a local diagnostic JSON line."""
    if path.stat().st_size > MAX_JSON_BYTES:
        return None
    text = path.read_text(errors="replace")
    fields = {}
    records = []
    for line in text.splitlines():
        if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*=.*", line):
            key, value = line.split("=", 1)
            # Repeated trial values are retained, never silently last-value-wins.
            if key in fields:
                fields[key] = (
                    fields[key] + [scalar(value)]
                    if isinstance(fields[key], list)
                    else [fields[key], scalar(value)]
                )
            else:
                fields[key] = scalar(value)
        elif line.startswith("{"):
            try:
                value = strict_json(line)
                if isinstance(value, dict):
                    records.append(value)
            except ValueError:
                pass
    if not records:
        try:
            value = strict_json(text)
            if isinstance(value, dict):
                records.append(value)
        except ValueError:
            pass
    return {"fields": fields, "records": records} if fields or records else None


def collect(stage_dir: Path, row: dict, exit_code: int) -> dict:
    """Keep actual data and source paths; never synthesize benchmark numbers."""
    measurements, diagnostics, text_results, reports, outcomes, errors = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for path in sorted(stage_dir.rglob("*")):
        if (
            path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or not path.is_file()
        ):
            continue
        relative = path.relative_to(stage_dir.parent.parent).as_posix()
        if path.suffix in {".nsys-rep", ".ncu-rep"}:
            reports.append(relative)
        elif path.suffix == ".json":
            if path.stat().st_size > MAX_JSON_BYTES:
                errors.append(
                    {
                        "path": relative,
                        "reason": f"JSON exceeds the {MAX_JSON_BYTES}-byte reader limit",
                    }
                )
                continue
            try:
                data = strict_json(path.read_text())
            except (ValueError, OSError) as exc:
                errors.append({"path": relative, "reason": str(exc)})
                continue
            record = {"path": relative, "data": data}
            if isinstance(data, dict) and data.get("schema") == "gpu-course-result/v1":
                experiment = data.get("experiment", {})
                values = data.get("measurements", {})
                valid_sections = isinstance(experiment, dict) and isinstance(
                    values, dict
                )
                if not valid_sections:
                    errors.append(
                        {
                            "path": relative,
                            "reason": "Measurement experiment and measurements must be objects",
                        }
                    )
                # Eligibility is a hint only; publication still validates the
                # selected original measurement and its acceptance contract.
                record["measurement_candidate"] = (
                    valid_sections
                    and row["profiler"] == "none"
                    and row["experiment"] != "sanitizer"
                    and experiment.get("instrumented") is False
                    and values.get("acceptance_timing") is not False
                )
                measurements.append(record)
                outcomes.append(
                    {"path": relative, "correctness": data.get("correctness")}
                )
            else:
                diagnostics.append(record)
        elif path.suffix in {".txt", ".out", ".err"}:
            adapted = text_adapter(path)
            if adapted:
                text_results.append({"path": relative, **adapted})
                checks = {
                    key: value
                    for key, value in adapted["fields"].items()
                    if key == "course_checks"
                    or key.endswith("_correctness")
                    or key in {"both_cpu_references", "outputs_match_each_other"}
                }
                if checks:
                    outcomes.append({"path": relative, "correctness": checks})
                for record in adapted["records"]:
                    if "gradient_validation" in record:
                        outcomes.append(
                            {
                                "path": relative,
                                "correctness": record["gradient_validation"],
                            }
                        )
    result = {
        "measurements": measurements,
        "diagnostics": diagnostics,
        "text_results": text_results,
        "correctness": outcomes,
        "native_reports": reports,
        "data_errors": errors,
    }
    if row["experiment"] == "sanitizer":
        result["sanitizer"] = {
            "tool": row["sanitizer"],
            "exit_code": exit_code,
            "status": "passed" if exit_code == 0 else "failed",
            "output": [
                f"stages/{row['id']}/logs/stdout.txt",
                f"stages/{row['id']}/logs/stderr.txt",
            ],
        }
    if row["experiment"] == "trials":
        result["trial_summaries"] = [
            entry
            for entry in [*text_results, *diagnostics, *measurements]
            if "summary" in Path(entry["path"]).name
        ]
    return result


def adopt_orphans():
    """Linux Slurm jobs adopt detached descendants even if a stage exits early."""
    if sys.platform != "linux":
        return False  # Local non-Slurm fixtures still verify process groups.
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Cannot establish child ownership")
    return True


def reap_orphans():
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break


def process_tree():
    """Observe identities only, never process arguments or environment values."""
    output = subprocess.check_output(
        ["ps", "-eo", "pid=,ppid=,pgid=,stat=,lstart="], text=True, timeout=5
    )
    return {
        int(fields[0]): (int(fields[1]), int(fields[2]), " ".join(fields[4:]))
        for line in output.splitlines()
        if len(fields := line.split()) >= 9 and fields[3][0] not in {"Z", "X"}
    }


def owned_groups(pid: int, observed: dict):
    processes = process_tree()
    descendants = {pid}
    while True:
        found = {
            child
            for child, (parent, _, _) in processes.items()
            if parent in descendants
        }
        if found <= descendants:
            break
        descendants.update(found)
    for child in descendants:
        if child in processes:
            _, group, identity = processes[child]
            if group != os.getpgrp():
                observed.setdefault(group, {})[child] = identity
    # A detached leader can exit before its workers. An observed living member
    # proves that the group still belongs to this stage; remember its siblings
    # too, without trusting a historical PGID after every member has disappeared.
    for group, members in observed.items():
        if any(
            child in processes and processes[child][1:] == (group, identity)
            for child, identity in members.items()
        ):
            members.update(
                (child, identity)
                for child, (_, current_group, identity) in processes.items()
                if current_group == group
            )


def live_owned_groups(pid: int, observed: dict, processes: dict) -> set[int]:
    groups = {pid} if any(group == pid for _, group, _ in processes.values()) else set()
    groups.update(
        group
        for group, members in observed.items()
        if any(
            child in processes and processes[child][1:] == (group, identity)
            for child, identity in members.items()
        )
    )
    return groups


def signal_owned_groups(pid: int, observed: dict, signum: int):
    processes = process_tree()
    for group in live_owned_groups(pid, observed, processes):
        try:
            os.killpg(group, signum)
        except ProcessLookupError:
            pass


def groups_remain(pid: int, observed: dict) -> bool:
    processes = process_tree()
    return bool(live_owned_groups(pid, observed, processes))


def run(lab: str, launcher: Path, arguments: list[str]) -> int:
    before_boundary = (
        arguments[: arguments.index("--")] if "--" in arguments else arguments
    )
    if any(item in {"-h", "--help"} for item in before_boundary):
        parse(arguments)
    course = Path.cwd()
    if lab not in capabilities(course):
        raise ValueError("Unknown course lab")
    stages = []
    job = os.environ.get("SLURM_JOB_ID", "")
    if (
        not re.fullmatch(r"[1-9][0-9]*", job)
        or os.environ.get("SLURM_RESTART_COUNT", "0") != "0"
    ):
        raise ValueError(
            "Submit a fresh job with sbatch; requeued jobs are unsupported"
        )
    for relative in (
        "results",
        f"results/{lab}",
        f"results/{lab}/logs",
        f"results/{lab}/jobs",
    ):
        private_directory(course / relative)
    root = course / "results" / lab / "jobs" / job
    root.mkdir(mode=0o700)
    (root / "stages").mkdir(mode=0o700)
    document = {
        "schema": SCHEMA,
        "course": course.name,
        "lab_id": lab,
        "job_id": job,
        "requested": {"arguments": arguments},
        "allocation": {
            key: os.environ.get(key)
            for key in (
                "SLURM_JOB_NUM_NODES",
                "SLURM_JOB_NODELIST",
                "SLURM_NTASKS",
                "SLURM_CPUS_PER_TASK",
                "SLURM_GPUS_ON_NODE",
            )
        },
        "status": "incomplete",
        "started_at": timestamp(),
        "stages": stages,
    }
    path = root / "result.json"
    atomic_write(path, document)
    print(f"Job results: {path}", flush=True)
    child = None
    observed_groups = {}
    interrupted = None

    def stop(signum, _frame):
        nonlocal interrupted
        interrupted = signum
        if child is not None and child.poll() is None:
            # The polling loop owns bounded process cleanup and JSON updates.
            pass

    previous = {
        sig: signal.signal(sig, stop)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    }
    try:
        requested, program_args = parse(arguments)
        document["requested"].update(requested, program_arguments=program_args)
        stages = select(course, lab, requested, program_args)
        document["stages"] = stages
        for row in stages:
            if row["status"] != "skipped":
                row["scope"] = runtime_scope(row["scope"], document["allocation"])
        atomic_write(path, document)
        subreaper = adopt_orphans()
        required = (
            sum(
                row["timeout_seconds"] + row["teardown_seconds"]
                for row in stages
                if row["status"] != "skipped"
            )
            + FINALIZATION_SECONDS
        )
        scheduler_budget(job, required)
        # Optional monitoring must never change experiment execution or status.
        from course_publication import supervise

        try:
            document["publication"] = supervise(
                course, path, register_only=True, cancelled=lambda: bool(interrupted)
            )
        except (OSError, ValueError, KeyError):
            document["publication"] = {
                "status": "failed",
                "reason": "Publication registration unavailable",
            }
        atomic_write(path, document)
        for row in stages:
            if row["status"] == "skipped":
                continue
            if interrupted:
                raise InterruptedError("Job interrupted before the next stage")
            stage = root / "stages" / row["id"]
            stage.mkdir(mode=0o700)
            for name in ("results", "profiles", "logs", "artifacts"):
                (stage / name).mkdir(mode=0o700)
            row.update(status="running", started_at=timestamp())
            row["sanitizer"] = requested.get("sanitizer")
            atomic_write(path, document)
            env = {
                **os.environ,
                "COURSE_JOB_ROOT": str(root),
                "COURSE_JOB_DIR": str(stage),
                "COURSE_RESULTS_DIR": str(stage / "results"),
                "COURSE_PROFILES_DIR": str(stage / "profiles"),
                "COURSE_WORKLOAD": requested["workload"],
                "COURSE_PROFILE_TOOL": row["profiler"],
                "COURSE_CAPTURE": "0" if row["profiler"] == "none" else "1",
                "COURSE_SANITIZER": requested.get("sanitizer") or "",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            stage_args = list(program_args)
            if capabilities(course)[lab]["device_option"] and not option_values(
                stage_args, "--device"
            ):
                stage_args += ["--device", row["device"]]
            command = [
                "bash",
                "-c",
                'source "$1"; shift; course_stage "$@"',
                "bash",
                str(launcher.absolute()),
                row["key"],
                requested["workload"],
                *stage_args,
            ]
            with (
                (stage / "logs/stdout.txt").open("x") as stdout,
                (stage / "logs/stderr.txt").open("x") as stderr,
            ):
                child = subprocess.Popen(
                    command,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                observed_groups = {}
                deadline = time.monotonic() + row["timeout_seconds"]
                timed_out = False
                cleanup_deadline = None
                next_observation = 0
                while child.poll() is None:
                    if time.monotonic() >= next_observation:
                        owned_groups(
                            os.getpid() if subreaper else child.pid, observed_groups
                        )
                        next_observation = time.monotonic() + 1
                    if interrupted or time.monotonic() >= deadline:
                        timed_out = not interrupted
                        # The stage's own trap gets a bounded chance to reap
                        # servers and export reports before the whole group dies.
                        owned_groups(
                            os.getpid() if subreaper else child.pid, observed_groups
                        )
                        signal_owned_groups(child.pid, observed_groups, signal.SIGTERM)
                        try:
                            cleanup_deadline = (
                                time.monotonic() + row["teardown_seconds"]
                            )
                            child.wait(timeout=row["teardown_seconds"])
                        except subprocess.TimeoutExpired:
                            signal_owned_groups(
                                child.pid, observed_groups, signal.SIGKILL
                            )
                            child.wait()
                        break
                    time.sleep(0.1)
                code = child.returncode
                # The direct child was collected by poll/wait. Reap already-dead
                # adopted children before deciding whether live work remains.
                if subreaper:
                    reap_orphans()
                owned_groups(os.getpid() if subreaper else child.pid, observed_groups)
                # A successful shell exit is insufficient when an owned server
                # or step is still alive. Cleanup failure stops the job.
                if groups_remain(child.pid, observed_groups):
                    signal_owned_groups(child.pid, observed_groups, signal.SIGTERM)
                    cleanup_deadline = (
                        cleanup_deadline or time.monotonic() + row["teardown_seconds"]
                    )
                    while (
                        groups_remain(child.pid, observed_groups)
                        and time.monotonic() < cleanup_deadline
                    ):
                        if subreaper:
                            reap_orphans()
                        time.sleep(0.1)
                    signal_owned_groups(child.pid, observed_groups, signal.SIGKILL)
                    if subreaper:
                        reap_orphans()
                    code = code or 2
                    row["cleanup_error"] = (
                        "Stage exited with owned processes still running"
                    )
                if timed_out:
                    code = 124
                if interrupted:
                    code = 128 + interrupted
            data = collect(stage, row, code)
            if data["data_errors"]:
                code = code or 2
            row.update(
                status="completed" if code == 0 else "failed",
                exit_code=code,
                finished_at=timestamp(),
                **data,
            )
            atomic_write(path, document)
            child = None
            if code:
                document["status"] = "failed"
                document["error"] = (
                    "Stage interrupted"
                    if interrupted
                    else "Stage timed out"
                    if timed_out
                    else f"Stage {row['id']} failed; inspect its logs"
                )
                return code if 0 < code < 256 else 1
        document["status"] = (
            "skipped"
            if all(row["status"] == "skipped" for row in stages)
            else "completed"
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        document.update(status="failed", error=str(exc))
        for row in stages:
            if row["status"] == "running":
                row.update(status="failed", reason=str(exc), finished_at=timestamp())
        raise
    finally:
        if child is not None:
            # Unexpected observer/IO failures must also terminate the stage.
            try:
                signal_owned_groups(child.pid, observed_groups, signal.SIGKILL)
            except (OSError, subprocess.SubprocessError) as exc:
                document["cleanup_error"] = str(exc)
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            for row in stages:
                if row["status"] == "failed" and "exit_code" not in row:
                    row.update(
                        exit_code=child.returncode,
                        **collect(root / "stages" / row["id"], row, child.returncode),
                    )
        document["finished_at"] = timestamp()
        atomic_write(path, document)
        try:
            from course_publication import supervise

            document["publication"] = supervise(
                course,
                path,
                document.get("publication"),
                cancelled=lambda: bool(interrupted),
            )
        except (OSError, ValueError, KeyError):
            document["publication"] = {
                **document.get("publication", {}),
                "status": "failed",
                "reason": "Publication finalization unavailable",
            }
        atomic_write(path, document)
        print(
            f"Publication: {document['publication']['status']} (details in result.json)",
            flush=True,
        )
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--launcher", required=True, type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    os.umask(0o077)
    try:
        return run(args.lab, args.launcher, arguments)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
