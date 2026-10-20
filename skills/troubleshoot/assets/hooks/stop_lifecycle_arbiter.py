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
    "stop_sdlc_continue.py",
)
REPORT_READY_FIELD = "_troubleshootReportReady"
REPORT_FINALIZE_FIELD = "_troubleshootFinalizeReport"
REPORT_FINALIZED_FIELD = "_troubleshootReportFinalized"
ARBITER_BUDGET_SECONDS = 25.0
DELEGATE_TIMEOUT_SECONDS = 8.0
FINALIZATION_WARNING = (
    "Troubleshoot report delivery could not be recorded at the trusted state "
    "boundary. Preserve the private state and start a fresh Codex session."
)


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


def _terminal_with_finalization_result(
    terminal: dict[str, Any], finalized: dict[str, Any]
) -> dict[str, Any]:
    finalized_ok = bool(finalized.pop(REPORT_FINALIZED_FIELD, False))
    if finalized_ok:
        return terminal
    combined = dict(terminal)
    existing = combined.get("systemMessage")
    combined["systemMessage"] = (
        existing + "\n" + FINALIZATION_WARNING
        if isinstance(existing, str) and existing
        else FINALIZATION_WARNING
    )
    return combined


def _finalization_failure() -> dict[str, Any]:
    return {
        "continue": False,
        "stopReason": "Troubleshoot report delivery could not be recorded.",
        "systemMessage": FINALIZATION_WARNING,
    }


def evaluate(payload: dict[str, Any], hook_dir: Path | None = None) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        return {"continue": True}
    deadline = time.monotonic() + ARBITER_BUDGET_SECONDS
    directory = hook_dir or Path(__file__).resolve().parent
    blockers: list[tuple[str, dict[str, Any]]] = []
    report_ready = False
    terminal: dict[str, Any] | None = None
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
            if terminal is None:
                terminal = result
            continue
        if result.get("decision") == "block":
            blockers.append((name, result))
    if terminal is not None:
        if report_ready:
            terminal = _terminal_with_finalization_result(
                terminal, _finalize_report(directory, payload, deadline)
            )
        return terminal
    if len(blockers) == 1:
        return blockers[0][1]
    if blockers:
        reasons = [
            f"- {name}: {result.get('reason', 'continuation required')}"
            for name, result in blockers
        ]
        return {
            "decision": "block",
            "reason": "Complete every active Stop-policy continuation:\n" + "\n".join(reasons),
        }
    if report_ready:
        finalized = _finalize_report(directory, payload, deadline)
        if not finalized.pop(REPORT_FINALIZED_FIELD, False):
            return _finalization_failure()
        return finalized
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
