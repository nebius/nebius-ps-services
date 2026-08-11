#!/usr/bin/env python3
"""Single deterministic Stop arbiter for installed workflow policies."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


DELEGATES = (
    "remediation_attempt_guard.py",
    "project_specs_lifecycle.py",
    "stop_sdlc_continue.py",
    "stop_prompt_session_intake.py",
)
REPORT_READY_FIELD = "_troubleshootReportReady"
REPORT_FINALIZE_FIELD = "_troubleshootFinalizeReport"
PROMPT_CONTINUATION_FIELD = "_promptSessionMarkStopContinuation"
ARBITER_BUDGET_SECONDS = 25.0
DELEGATE_TIMEOUT_SECONDS = 8.0


def _run_delegate(
    path: Path, payload: dict[str, Any], deadline: float
) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {
            "continue": False,
            "stopReason": f"Stop policy delegate {path.name} exceeded the shared arbiter deadline.",
        }
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=min(DELEGATE_TIMEOUT_SECONDS, remaining),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "continue": False,
            "stopReason": f"Stop policy delegate {path.name} failed: {error}",
        }
    if completed.returncode != 0:
        return {
            "continue": False,
            "stopReason": f"Stop policy delegate {path.name} exited unsuccessfully.",
        }
    if not completed.stdout.strip():
        return {"continue": True}
    try:
        value: Any = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "continue": False,
            "stopReason": f"Stop policy delegate {path.name} returned invalid JSON.",
        }
    if not isinstance(value, dict):
        return {
            "continue": False,
            "stopReason": f"Stop policy delegate {path.name} returned an invalid result.",
        }
    return value


def _mark_prompt_continuation(
    directory: Path,
    payload: dict[str, Any],
    result: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    if result.get("decision") != "block":
        return result
    delegate = directory / "stop_prompt_session_intake.py"
    if not delegate.is_file():
        return result
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason:
        return {
            "continue": False,
            "stopReason": "Stop policy continuation reason is invalid.",
        }
    marked = _run_delegate(
        delegate, {**payload, PROMPT_CONTINUATION_FIELD: reason}, deadline
    )
    if marked.get("continue") is False:
        return marked
    return result


def _finalize_report(
    directory: Path, payload: dict[str, Any], deadline: float
) -> dict[str, Any]:
    finalized = _run_delegate(
        directory / "remediation_attempt_guard.py",
        {**payload, REPORT_FINALIZE_FIELD: True},
        deadline,
    )
    finalized.pop(REPORT_READY_FIELD, None)
    return finalized


def evaluate(payload: dict[str, Any], hook_dir: Path | None = None) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        return {"continue": True}
    deadline = time.monotonic() + ARBITER_BUDGET_SECONDS
    directory = hook_dir or Path(__file__).resolve().parent
    blockers: list[tuple[str, dict[str, Any]]] = []
    report_ready = False
    for name in DELEGATES:
        delegate = directory / name
        if not delegate.is_file() or delegate.resolve() == Path(__file__).resolve():
            continue
        result = _run_delegate(delegate, payload, deadline)
        if name == "remediation_attempt_guard.py" and result.pop(
            REPORT_READY_FIELD, False
        ):
            report_ready = True
        if result.get("continue") is False:
            if report_ready:
                _finalize_report(directory, payload, deadline)
            return result
        if result.get("decision") == "block":
            blockers.append((name, result))
    if len(blockers) == 1:
        return _mark_prompt_continuation(
            directory, payload, blockers[0][1], deadline
        )
    if blockers:
        reasons = [
            f"- {name}: {result.get('reason', 'continuation required')}"
            for name, result in blockers
        ]
        return _mark_prompt_continuation(
            directory,
            payload,
            {
                "decision": "block",
                "reason": "Complete every active Stop-policy continuation:\n"
                + "\n".join(reasons),
            },
            deadline,
        )
    if report_ready:
        finalized = _finalize_report(directory, payload, deadline)
        return _mark_prompt_continuation(directory, payload, finalized, deadline)
    return {"continue": True}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        output = evaluate(payload)
    except Exception as error:
        output = {
            "continue": False,
            "stopReason": f"Stop lifecycle arbiter failed closed: {error}",
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
