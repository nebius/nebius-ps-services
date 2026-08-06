#!/usr/bin/env python3
"""Private sequential Codex worker fallback for Agentic SDLC assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys


class DispatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


TERMINAL_WORKER_STATUSES = frozenset(
    {
        "WORKER_PRESTART_TIMEOUT",
        "WORKER_PRESTART_MUTATION",
        "WORKER_STALLED",
        "WORKER_READ_ONLY_TIMEOUT",
        "WORKER_SCOPE_VIOLATION",
        "WORKER_TIMEOUT",
    }
)


def stable_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def canonical_execution_helper() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "sdlc-prepare-execution"
        / "scripts"
        / "sdlc_execution.py"
    ).resolve()


def load_assignment(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment is unreadable"
        ) from exc
    if isinstance(value, dict) and value.get("schema") in {
        "agentic-sdlc/worker-assignment-v1",
        "agentic-sdlc/worker-assignment-v2",
    }:
        raise DispatchError(
            "WORKFLOW_UPGRADE_REQUIRED", "legacy worker assignment is unsupported"
        )
    if not isinstance(value, dict) or value.get("schema") != (
        "agentic-sdlc/worker-assignment-v3"
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment schema is invalid"
        )
    digest = value.get("assignment_digest")
    unsigned = dict(value)
    unsigned.pop("assignment_digest", None)
    if not isinstance(digest, str) or stable_digest(unsigned) != digest:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
        )
    scope_cwd = Path(str(value.get("scope_cwd") or ""))
    if not scope_cwd.is_absolute() or scope_cwd.is_symlink() or not scope_cwd.is_dir():
        raise DispatchError("WORKTREE_CONFLICT", "worker scope cwd is invalid")
    helper = Path(str(value.get("execution_helper") or ""))
    expected_helper = canonical_execution_helper()
    run_dir = Path(str(value.get("run_dir") or ""))
    profile = value.get("worker_profile")
    expected_read_only = (240, 300) if profile == "standard" else (360, 420)
    if (
        not helper.is_absolute()
        or helper.is_symlink()
        or not helper.is_file()
        or helper.resolve() != expected_helper
        or not expected_helper.is_file()
        or not run_dir.is_absolute()
        or run_dir.is_symlink()
        or value.get("heartbeat_seconds") != 30
        or value.get("start_seconds") != 60
        or value.get("stall_seconds") != 240
        or value.get("max_seconds") != 1800
        or profile not in {"standard", "integration"}
        or value.get("read_only_warning_seconds") != expected_read_only[0]
        or value.get("read_only_seconds") != expected_read_only[1]
        or value.get("worker_phases")
        != ["preflight", "implementing", "validating", "reviewing", "reporting"]
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment liveness is invalid"
        )
    handoff_path = Path(str(value.get("incoming_handoff_path") or ""))
    if (
        not handoff_path.is_absolute()
        or handoff_path.is_symlink()
        or not handoff_path.is_file()
        or (handoff_path.stat().st_mode & 0o777) != 0o600
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff path or mode is invalid"
        )
    try:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff is unreadable"
        ) from exc
    unsigned_handoff = dict(handoff) if isinstance(handoff, dict) else {}
    handoff_digest = unsigned_handoff.pop("handoff_digest", None)
    if (
        not isinstance(handoff, dict)
        or handoff.get("schema") != "agentic-sdlc/incoming-handoff-v1"
        or handoff.get("feature_id") != value.get("feature_id")
        or handoff.get("wave_id") != value.get("wave_id")
        or handoff.get("task_id") != value.get("task_id")
        or handoff_digest != value.get("incoming_handoff_digest")
        or handoff_digest != stable_digest(unsigned_handoff)
    ):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "incoming handoff context is invalid"
        )
    return value


def worker_prompt(assignment_path: Path, assignment: dict[str, object]) -> str:
    run_dir = Path(str(assignment["run_dir"]))
    helper = Path(str(assignment["execution_helper"]))
    start = [
        sys.executable,
        str(helper),
        "task-start",
        "--run-dir",
        str(run_dir),
        "--feature",
        str(assignment["feature_id"]),
        "--wave",
        str(assignment["wave_id"]),
        "--task",
        str(assignment["task_id"]),
        "--assignment-digest",
        str(assignment["assignment_digest"]),
        "--scope-cwd",
        str(assignment["scope_cwd"]),
    ]
    instructions = [
        "Implement exactly one immutable Agentic SDLC worker assignment.",
        "Run the task-start command represented by this JSON argv before editing:",
        json.dumps(start),
        "Run direct task-heartbeat commands at least every "
        f"{assignment['heartbeat_seconds']} seconds while working. Use one of "
        "the assignment's worker_phases and this JSON argv prefix:",
        json.dumps(
            [
                sys.executable,
                str(helper),
                "task-heartbeat",
                "--run-dir",
                str(run_dir),
                "--feature",
                str(assignment["feature_id"]),
                "--wave",
                str(assignment["wave_id"]),
                "--task",
                str(assignment["task_id"]),
                "--assignment-digest",
                str(assignment["assignment_digest"]),
                "--phase",
            ]
        ),
        "Invoke heartbeats directly; never create a background heartbeat loop.",
        "Read the assignment JSON at:",
        str(assignment_path.resolve()),
        "Work only from its scope_cwd and write claims.",
        "Do not commit, merge, replan, edit coordinator state, or start another task.",
    ]
    if assignment.get("diagnosis_id") is not None:
        instructions.extend(
            [
                "This is a corrective task bound to the assignment's diagnosis_id.",
                "After the smallest bounded repair, run the assignment's original "
                "regression_oracle first, then its affected-boundary validation.",
                "Do not reinterpret or modify any completed task definition.",
            ]
        )
    instructions.extend(
        [
            "Validate the change and perform a focused code review.",
            "Return only the schema-conforming worker result.",
        ]
    )
    return "\n".join(instructions)


def _transition(
    assignment: dict[str, object], action: str, *extra: str
) -> dict[str, object]:
    command = [
        sys.executable,
        str(assignment["execution_helper"]),
        action,
        "--run-dir",
        str(assignment["run_dir"]),
        "--feature",
        str(assignment["feature_id"]),
        "--wave",
        str(assignment["wave_id"]),
        "--task",
        str(assignment["task_id"]),
        "--assignment-digest",
        str(assignment["assignment_digest"]),
        *extra,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(assignment["scope_cwd"]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", f"{action} transition failed"
        ) from exc
    if completed.returncode != 0 or not isinstance(value, dict):
        code = value.get("code") if isinstance(value, dict) else None
        raise DispatchError(
            str(code or "WORKER_FAILED"), f"{action} transition was rejected"
        )
    result = value.get("result")
    if value.get("status") != "ok" or not isinstance(result, dict):
        raise DispatchError(
            "EXECUTION_STATE_INVALID", f"{action} transition result is invalid"
        )
    return result


def _stop_worker(
    process: subprocess.Popen[str], *, terminate_grace: float
) -> tuple[str, str]:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=terminate_grace)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass
        return process.communicate()


def _codex_available(codex_binary: str) -> bool:
    candidate = Path(codex_binary)
    if candidate.parent != Path("."):
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(codex_binary) is not None


def dispatch_sequential(
    assignment_paths: list[Path],
    output_schema: Path,
    *,
    codex_binary: str = "codex",
    watch_interval: float = 30,
    terminate_grace: float = 5,
) -> list[dict[str, object]]:
    if output_schema.is_symlink() or not output_schema.is_file():
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker output schema is unavailable"
        )
    results: list[dict[str, object]] = []
    for assignment_path in assignment_paths:
        assignment = load_assignment(assignment_path)
        if not _codex_available(codex_binary):
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "codex executable is unavailable"
            )
        command = [
            codex_binary,
            "exec",
            "--cd",
            str(assignment["scope_cwd"]),
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--output-schema",
            str(output_schema.resolve()),
            "-",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "codex executable is unavailable"
            ) from exc
        try:
            _transition(assignment, "task-arm")
            prompt: str | None = worker_prompt(assignment_path, assignment)
            while True:
                try:
                    stdout, _stderr = process.communicate(
                        input=prompt, timeout=watch_interval
                    )
                    break
                except subprocess.TimeoutExpired:
                    prompt = None
                    watched = _transition(assignment, "task-watch")
                    status = str(watched.get("status") or "")
                    if status in TERMINAL_WORKER_STATUSES:
                        raise DispatchError(
                            status, "sequential worker was interrupted"
                        )
                    if status not in {"PENDING_START", "ACTIVE"}:
                        raise DispatchError(
                            "EXECUTION_STATE_INVALID",
                            "sequential worker liveness state is invalid",
                        )
            if process.returncode != 0:
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker exited unsuccessfully"
                )
            final_watch = _transition(assignment, "task-watch")
            final_status = str(final_watch.get("status") or "")
            if final_status in TERMINAL_WORKER_STATUSES:
                raise DispatchError(
                    final_status, "sequential worker exceeded its budget"
                )
            if final_status != "ACTIVE":
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker exited before task-start"
                )
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker returned invalid JSON"
                ) from exc
            if (
                not isinstance(result, dict)
                or result.get("task_id") != assignment["task_id"]
                or result.get("assignment_digest")
                != assignment["assignment_digest"]
                or result.get("status") != "implemented"
                or not isinstance(result.get("validation"), str)
                or not isinstance(result.get("review"), str)
                or not isinstance(result.get("summary"), str)
                or not isinstance(result.get("decisions"), list)
                or not isinstance(result.get("open_risks"), list)
            ):
                raise DispatchError(
                    "WORKER_FAILED", "sequential worker result is invalid"
                )
            results.append(result)
        except BaseException:
            _stop_worker(process, terminate_grace=terminate_grace)
            raise
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, action="append", required=True)
    parser.add_argument(
        "--output-schema",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "assets"
        / "worker-result.schema.json",
    )
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--watch-interval", type=float, default=30)
    parser.add_argument("--terminate-grace", type=float, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = dispatch_sequential(
            args.assignment,
            args.output_schema,
            codex_binary=args.codex_binary,
            watch_interval=args.watch_interval,
            terminate_grace=args.terminate_grace,
        )
    except DispatchError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}))
        return 2
    print(json.dumps({"status": "ok", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
