#!/usr/bin/env python3
"""Enforce the parent-authored remediation budget in private task state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


SCHEMA = "codex/remediation-budget-v1"
MARKER_START = "<!-- codex-remediation-budget:v1\n"
MARKER_END = "\n-->"
MAX_MARKER_PREFIX_BYTES = 12 * 1024
MAX_TASK_STATE_BYTES = 1024 * 1024
MAX_ATTEMPTS = 100
DEFAULT_ATTEMPT_LIMIT = 3
DEFAULT_TIME_LIMIT_MINUTES = 60
SUPPORTED_ATTEMPT_RESULTS = ("failed_same_blocker", "succeeded")
SAFE_SESSION_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
REPORT_MARKER = "REMEDIATION_BUDGET_EXHAUSTED"
REPORT_HEADINGS = (
    "## Outcome",
    "## Blocking Error",
    "## Source",
    "## Attempts",
    "## Evidence",
    "## Current State",
    "## Next Action",
)


class GuardStateError(RuntimeError):
    """Raised when a present remediation marker cannot be trusted."""


@dataclass(frozen=True)
class GuardState:
    kind: str
    state_file: Path | None
    data: dict[str, Any] | None = None
    reason: str | None = None
    exhausted: bool = False
    stop_trigger: str | None = None


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def resolve_root(cwd: str) -> str:
    cwd = os.path.abspath(cwd)
    try:
        root = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        root = ""
    return os.path.abspath(root or cwd)


def safe_segment(value: str, *, limit: int = 80) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return safe[:limit]


def session_segment(value: object) -> str:
    raw = str(value)
    if SAFE_SESSION_RE.fullmatch(raw) and raw not in {".", ".."}:
        return raw
    prefix = safe_segment(raw, limit=48) or "session"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def state_file_for_payload(payload: dict[str, Any]) -> Path | None:
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if session_id is None or str(session_id) == "" or not isinstance(cwd, str):
        return None
    root = resolve_root(cwd)
    home = codex_home()
    workspace_name = safe_segment(Path(root).name or "workspace") or "workspace"
    workspace_hash = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
    return (
        home
        / "task-state"
        / f"{workspace_name}-{workspace_hash}"
        / session_segment(session_id)
        / "current.md"
    )


def _assert_safe_state_file(state_file: Path) -> None:
    home = codex_home()
    if home.is_symlink():
        raise GuardStateError("CODEX_HOME is a symbolic link")
    task_root = home / "task-state"
    try:
        relative = state_file.relative_to(task_root)
    except ValueError as exc:
        raise GuardStateError("task-state path is outside the private root") from exc
    if (
        len(relative.parts) != 3
        or relative.parts[-1] != "current.md"
        or any(part in {"", ".", ".."} for part in relative.parts[:-1])
    ):
        raise GuardStateError("task-state path has an unexpected shape")
    current = task_root
    for part in relative.parts[:-1]:
        if current.is_symlink():
            raise GuardStateError("task-state directory contains a symbolic link")
        if current.exists() and not current.is_dir():
            raise GuardStateError("task-state parent is not a directory")
        current = current / part
    if current.is_symlink():
        raise GuardStateError("task-state directory contains a symbolic link")
    if state_file.is_symlink():
        raise GuardStateError("task-state file is a symbolic link")
    if state_file.exists() and not stat.S_ISREG(state_file.lstat().st_mode):
        raise GuardStateError("task-state path is not a regular file")


def _bounded_string(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise GuardStateError(f"{field} must be a non-empty bounded string")
    if any(ord(character) < 32 and character not in "\t\n" for character in value):
        raise GuardStateError(f"{field} contains control characters")
    return value


def _positive_int_or_none(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GuardStateError(f"{field} must be a positive integer or null")
    return value


def _parse_started_at(value: object) -> datetime:
    text = _bounded_string(value, "started_at", 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GuardStateError("started_at must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise GuardStateError("started_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GuardStateError(f"{field} must be a non-negative integer")
    return value


def _public_reason(reason: str) -> str:
    normalized = " ".join(reason.split())
    if not normalized:
        return "marker validation failed"
    return normalized[:512]


def _validate_data(data: object) -> tuple[dict[str, Any], bool, str | None]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise GuardStateError(f"marker schema must be {SCHEMA}")
    _bounded_string(data.get("blocker_key"), "blocker_key", 256)
    _bounded_string(data.get("blocker_summary"), "blocker_summary", 512)
    tranche = data.get("tranche")
    if isinstance(tranche, bool) or not isinstance(tranche, int) or tranche < 1:
        raise GuardStateError("tranche must be a positive integer")
    _parse_started_at(data.get("started_at"))
    active_seconds = _nonnegative_int(data.get("active_seconds"), "active_seconds")
    attempt_limit = _positive_int_or_none(data.get("attempt_limit"), "attempt_limit")
    time_limit = _positive_int_or_none(
        data.get("time_limit_minutes"), "time_limit_minutes"
    )
    override = data.get("override_summary")
    if override is not None:
        _bounded_string(override, "override_summary", 512)
    uses_default_limits = (
        attempt_limit == DEFAULT_ATTEMPT_LIMIT
        and time_limit == DEFAULT_TIME_LIMIT_MINUTES
    )
    if (not uses_default_limits or tranche > 1) and override is None:
        raise GuardStateError(
            "non-default limits and continuation tranches require an explicit "
            "override summary"
        )

    attempts = data.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise GuardStateError("attempts must be a bounded list")
    failed_keys: set[str] = set()
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            raise GuardStateError(f"attempt {index} must be an object")
        _bounded_string(attempt.get("id"), f"attempt {index} id", 80)
        distinct_key = _bounded_string(
            attempt.get("distinct_key"), f"attempt {index} distinct_key", 256
        )
        _bounded_string(attempt.get("hypothesis"), f"attempt {index} hypothesis", 512)
        _bounded_string(attempt.get("remediation"), f"attempt {index} remediation", 512)
        _bounded_string(
            attempt.get("verification"), f"attempt {index} verification", 512
        )
        result = attempt.get("result")
        if result not in SUPPORTED_ATTEMPT_RESULTS:
            supported_results = " or ".join(SUPPORTED_ATTEMPT_RESULTS)
            raise GuardStateError(
                f"attempt {index} result must be {supported_results}; "
                "record unverified progress in prose, and replace the marker with "
                "a fresh empty attempt ledger for a causally independent blocker"
            )
        if result == "failed_same_blocker":
            failed_keys.add(distinct_key)

    status = data.get("status")
    if status not in {"active", "exhausted", "resolved"}:
        raise GuardStateError("status must be active, exhausted, or resolved")
    stop_trigger = data.get("stop_trigger")
    if stop_trigger not in {None, "attempt_limit", "time_limit"}:
        raise GuardStateError("stop_trigger is invalid")

    attempt_exhausted = attempt_limit is not None and len(failed_keys) >= attempt_limit
    time_exhausted = time_limit is not None and active_seconds >= time_limit * 60
    if status in {"active", "resolved"} and stop_trigger is not None:
        raise GuardStateError(f"{status} state requires a null stop_trigger")
    if status == "exhausted":
        if stop_trigger is None:
            raise GuardStateError("exhausted state requires a stop trigger")
        if stop_trigger == "attempt_limit" and not attempt_exhausted:
            raise GuardStateError(
                "attempt_limit stop_trigger requires the failed-attempt limit "
                "to be reached"
            )
        if stop_trigger == "time_limit" and not time_exhausted:
            raise GuardStateError(
                "time_limit stop_trigger requires the active-time limit to be reached"
            )
    if status == "resolved":
        return data, False, None

    exhausted = status == "exhausted" or attempt_exhausted or time_exhausted
    if stop_trigger is None:
        if attempt_exhausted:
            stop_trigger = "attempt_limit"
        elif time_exhausted:
            stop_trigger = "time_limit"
    return data, exhausted, stop_trigger


def load_guard_state(payload: dict[str, Any]) -> GuardState:
    state_file = state_file_for_payload(payload)
    if state_file is None:
        return GuardState("missing", None)
    try:
        _assert_safe_state_file(state_file)
        if not state_file.exists():
            return GuardState("missing", state_file)
        with state_file.open("rb") as handle:
            raw = handle.read(MAX_TASK_STATE_BYTES + 1)
        if len(raw) > MAX_TASK_STATE_BYTES:
            raise GuardStateError("task state exceeds the 1 MiB safety limit")
        marker_start = MARKER_START.encode("utf-8")
        marker_end = MARKER_END.encode("utf-8")
        prefix = raw[:MAX_MARKER_PREFIX_BYTES]
        start = prefix.find(marker_start)
        if start < 0:
            if raw.find(marker_start, MAX_MARKER_PREFIX_BYTES) >= 0:
                raise GuardStateError(
                    "remediation marker begins after the first 12 KiB"
                )
            return GuardState("missing", state_file)
        body_start = start + len(marker_start)
        end = prefix.find(marker_end, body_start)
        if end < 0:
            raise GuardStateError(
                "remediation marker is not terminated within the first 12 KiB"
            )
        if raw.find(marker_start, body_start) >= 0:
            raise GuardStateError("multiple remediation markers are not allowed")
        data = json.loads(raw[body_start:end].decode("utf-8"))
        validated, exhausted, stop_trigger = _validate_data(data)
        return GuardState(
            "valid",
            state_file,
            validated,
            exhausted=exhausted,
            stop_trigger=stop_trigger,
        )
    except GuardStateError as exc:
        reason = _public_reason(str(exc))
    except json.JSONDecodeError:
        reason = "marker JSON is malformed"
    except UnicodeError:
        reason = "marker text is not valid UTF-8"
    except OSError:
        reason = "task-state marker could not be read safely"
    return GuardState("invalid", state_file, reason=reason)


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _stop(reason: str, *, warning: str | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"continue": False, "stopReason": reason}
    if warning:
        output["systemMessage"] = warning
    return output


def _continue_with_report(reason: str) -> dict[str, Any]:
    return {
        "decision": "block",
        "reason": "\n".join(
            [
                reason,
                "Do not troubleshoot further or call tools.",
                "Return the existing Troubleshooting Report with these exact sections:",
                *[f"- {heading}" for heading in REPORT_HEADINGS],
                f"Include the marker `{REPORT_MARKER}` and the stop trigger.",
                "Redact secrets, private endpoints, customer data, and raw logs.",
            ]
        ),
    }


def _continue_for_marker_repair(reason: str) -> dict[str, Any]:
    return {
        "decision": "block",
        "reason": "\n".join(
            [
                reason,
                "Repair only the exact advertised current.md marker before "
                "calling another tool.",
                "Marker validation and repair do not consume a remediation "
                "attempt and do not exhaust the active blocker budget.",
                "After repair, continue only if the marker is valid and its "
                "current blocker budget is still active.",
            ]
        ),
    }


def _patch_updates_only_state(payload: dict[str, Any], state_file: Path | None) -> bool:
    if state_file is None or payload.get("tool_name") != "apply_patch":
        return False
    try:
        _assert_safe_state_file(state_file)
    except GuardStateError:
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(
        tool_input.get("command"), str
    ):
        return False
    patch = tool_input["command"]
    if "*** Move to:" in patch or "*** Delete File:" in patch:
        return False
    targets = re.findall(r"^\*\*\* (?:Update|Add) File: (.+)$", patch, re.MULTILINE)
    if not targets:
        return False
    cwd = str(payload.get("cwd") or ".")
    expected = state_file.resolve(strict=False)
    for target in targets:
        candidate = Path(target.strip())
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        if candidate.resolve(strict=False) != expected:
            return False
    return True


def _report_complete(message: object, stop_trigger: str | None) -> bool:
    if not isinstance(message, str) or REPORT_MARKER not in message:
        return False
    if stop_trigger is not None and stop_trigger not in message:
        return False
    lines = [line.strip() for line in message.splitlines()]
    positions: list[int] = []
    search_from = 0
    for heading in REPORT_HEADINGS:
        try:
            position = lines.index(heading, search_from)
        except ValueError:
            return False
        positions.append(position)
        search_from = position + 1
    positions.append(len(lines))
    for index, position in enumerate(positions[:-1]):
        section = lines[position + 1 : positions[index + 1]]
        if not any(line and not line.startswith("## ") for line in section):
            return False
    return True


def evaluate_pre_tool(payload: dict[str, Any], state: GuardState) -> dict[str, Any]:
    if state.kind == "missing":
        return {}
    if state.kind == "invalid":
        if _patch_updates_only_state(payload, state.state_file):
            return {}
        return _deny(
            "Remediation budget state is present but invalid"
            + (f": {state.reason}" if state.reason else "")
            + ". Repair only the exact advertised current.md marker before another "
            "tool call. Marker repair does not consume an attempt or exhaust the "
            "current blocker budget; resume only after the marker validates."
        )
    if not state.exhausted:
        return {}
    if _patch_updates_only_state(payload, state.state_file):
        return {}
    return _deny(
        "Remediation budget exhausted. Stop all tool use and return the complete "
        f"troubleshooting report with `{REPORT_MARKER}`. A new user instruction is "
        "required before another bounded tranche."
    )


def evaluate_stop(payload: dict[str, Any], state: GuardState) -> dict[str, Any]:
    if state.kind == "missing" or (state.kind == "valid" and not state.exhausted):
        return {"continue": True}
    if state.kind == "invalid":
        if payload.get("stop_hook_active"):
            return _stop(
                "The remediation budget marker remained invalid after one repair request.",
                warning=(
                    "The remediation guard stopped to avoid an infinite loop. "
                    "Marker repair is still required, but no remediation attempt "
                    "was consumed or exhausted."
                ),
            )
        return _continue_for_marker_repair(
            "The remediation budget marker is invalid"
            + (f": {state.reason}" if state.reason else "")
            + "."
        )
    if _report_complete(payload.get("last_assistant_message"), state.stop_trigger):
        return _stop(
            "Remediation budget exhausted and troubleshooting report delivered."
        )
    if payload.get("stop_hook_active"):
        return _stop(
            "Remediation budget exhausted, but the required report remained incomplete.",
            warning=(
                "The remediation guard stopped further work to avoid an infinite loop; "
                "the required troubleshooting report was incomplete."
            ),
        )
    return _continue_with_report(
        "The remediation budget is exhausted. Produce the user-visible report now."
    )


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    state = load_guard_state(payload)
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        return evaluate_pre_tool(payload, state)
    if event == "Stop":
        return evaluate_stop(payload, state)
    return {"continue": True}


def main() -> int:
    payload: object = {}
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        print(json.dumps(evaluate(payload), sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - command-hook safety boundary
        event = payload.get("hook_event_name") if isinstance(payload, dict) else None
        if event == "PreToolUse":
            output = _deny(f"Remediation guard failed closed: {exc}")
        else:
            output = _stop(f"Remediation guard failed closed: {exc}")
        print(json.dumps(output, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
