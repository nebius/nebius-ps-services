#!/usr/bin/env python3
"""Private sequential Codex worker fallback for Agentic SDLC assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid


class DispatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def stable_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_assignment(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker assignment is unreadable"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "agentic-sdlc/worker-assignment-v1"
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
    return value


def worker_prompt(
    assignment_path: Path, assignment: dict[str, object], session_id: str
) -> str:
    run_dir = assignment_path.resolve().parents[5]
    helper = (
        Path(__file__).resolve().parents[2]
        / "sdlc-prepare-execution"
        / "scripts"
        / "sdlc_execution.py"
    )
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
        "--session-id",
        session_id,
        "--scope-cwd",
        str(assignment["scope_cwd"]),
    ]
    return "\n".join(
        [
            "Implement exactly one immutable Agentic SDLC worker assignment.",
            "Run the task-start command represented by this JSON argv before editing:",
            json.dumps(start),
            "Read the assignment JSON at:",
            str(assignment_path.resolve()),
            "Work only from its scope_cwd and write claims.",
            "Do not commit, merge, replan, edit coordinator state, or start another task.",
            "Validate the change and perform a focused code review.",
            "Return only the schema-conforming worker result.",
        ]
    )


def dispatch_sequential(
    assignment_paths: list[Path],
    output_schema: Path,
    *,
    codex_binary: str = "codex",
    timeout: int = 3600,
) -> list[dict[str, object]]:
    if output_schema.is_symlink() or not output_schema.is_file():
        raise DispatchError(
            "EXECUTION_STATE_INVALID", "worker output schema is unavailable"
        )
    results: list[dict[str, object]] = []
    for assignment_path in assignment_paths:
        assignment = load_assignment(assignment_path)
        session_id = f"sdlc-worker-{uuid.uuid4().hex}"
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
            completed = subprocess.run(
                command,
                input=worker_prompt(assignment_path, assignment, session_id),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise DispatchError(
                "ENVIRONMENT_BLOCKER", "codex executable is unavailable"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DispatchError("WORKER_FAILED", "sequential worker timed out") from exc
        if completed.returncode != 0:
            raise DispatchError(
                "WORKER_FAILED", "sequential worker exited unsuccessfully"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DispatchError(
                "WORKER_FAILED", "sequential worker returned invalid JSON"
            ) from exc
        if (
            not isinstance(result, dict)
            or result.get("task_id") != assignment["task_id"]
            or result.get("assignment_digest") != assignment["assignment_digest"]
            or result.get("status") != "implemented"
            or not isinstance(result.get("validation"), str)
            or not isinstance(result.get("review"), str)
        ):
            raise DispatchError("WORKER_FAILED", "sequential worker result is invalid")
        results.append(result)
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
    parser.add_argument("--timeout", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = dispatch_sequential(
            args.assignment,
            args.output_schema,
            codex_binary=args.codex_binary,
            timeout=args.timeout,
        )
    except DispatchError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}))
        return 2
    print(json.dumps({"status": "ok", "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
