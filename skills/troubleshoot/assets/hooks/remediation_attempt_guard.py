#!/usr/bin/env python3
"""Enforce the parent-authored remediation budget in private task state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


SCHEMA = "codex/remediation-budget-v2"
LEGACY_SCHEMA = "codex/remediation-budget-v1"
MARKER_START = "<!-- codex-remediation-budget:v1\n"
MARKER_END = "\n-->"
MAX_MARKER_PREFIX_BYTES = 12 * 1024
MAX_TASK_STATE_BYTES = 1024 * 1024
DEFAULT_ATTEMPT_LIMIT = 3
MAX_ATTEMPT_LIMIT = 3
MAX_RECORDED_ATTEMPTS = 3
DEFAULT_TIME_LIMIT_MINUTES = 60
SUPPORTED_ATTEMPT_RESULTS = ("failed_same_blocker", "succeeded")
SAFE_SESSION_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
SENSITIVE_REPORT_RE = re.compile(
    r"(?i)(?:"
    r"https?://\S+|"
    r"-----BEGIN [A-Z ]+-----|"
    r"""["']?(?:access[_-]?key|api[_-]?key|authorization|certificate|cookie|"""
    r"credential|endpoint|host|hostname|password|passwd|private[_-]?key|"
    r"""pwd|secret|session|token|uri|url)["']?\s*[:=]\s*\S+|"""
    r"bearer\s+\S+|"
    r"/(?:Users|home)/\S+|"
    r"[A-Z]:[\\/]+Users[\\/]+\S+|"
    r"\b[A-Za-z0-9_+/=]{32,}\b"
    r")"
)
IPV4_CANDIDATE_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_CANDIDATE_RE = re.compile(
    r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])"
)
PRIVATE_HOST_RE = re.compile(
    r"(?i)\b(?:localhost|"
    r"(?:[a-z0-9-]+\.)+(?:internal|corp|private|local)(?:\.[a-z0-9-]+)*|"
    r"(?:internal|corp|private|local)(?:\.[a-z0-9-]+)+)\b"
)
CLOUD_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
REPORT_MARKER = "REMEDIATION_BUDGET_EXHAUSTED"
LEGACY_EVIDENCE_NOTE = (
    "Retry-admission evidence summaries were not recorded by the earlier "
    "terminal marker contract."
)
REPORT_HEADINGS = (
    "## Outcome",
    "## Blocking Error",
    "## Source",
    "## Attempts",
    "## Evidence",
    "## Current State",
    "## Next Action",
)
PLACEHOLDER_TOKENS = {
    "a",
    "n",
    "none",
    "placeholder",
    "pending",
    "tbd",
    "todo",
    "unknown",
    "unavailable",
}


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


def _attempt_limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_ATTEMPT_LIMIT
    ):
        raise GuardStateError(
            f"attempt_limit must be an integer from 1 to {MAX_ATTEMPT_LIMIT}"
        )
    return value


def _normalized_ledger_value(value: str) -> str:
    return " ".join(value.split()).casefold()


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
    if not isinstance(data, dict):
        raise GuardStateError("marker must contain a JSON object")
    schema = data.get("schema")
    if schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise GuardStateError(f"marker schema must be {SCHEMA}")
    blocker_key = _bounded_string(data.get("blocker_key"), "blocker_key", 256)
    _bounded_string(data.get("blocker_summary"), "blocker_summary", 512)
    tranche = data.get("tranche")
    if isinstance(tranche, bool) or not isinstance(tranche, int) or tranche < 1:
        raise GuardStateError("tranche must be a positive integer")
    _parse_started_at(data.get("started_at"))
    active_seconds = _nonnegative_int(data.get("active_seconds"), "active_seconds")
    attempt_limit = _attempt_limit(data.get("attempt_limit"))
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

    status = data.get("status")
    if status not in {"active", "exhausted", "resolved"}:
        raise GuardStateError("status must be active, exhausted, or resolved")
    legacy_report_only = schema == LEGACY_SCHEMA
    if legacy_report_only and status != "exhausted":
        raise GuardStateError(
            f"{LEGACY_SCHEMA} markers are accepted only as exhausted report-only "
            f"state; write {SCHEMA} for active or resolved state"
        )
    stop_trigger = data.get("stop_trigger")
    if stop_trigger not in {None, "attempt_limit", "time_limit"}:
        raise GuardStateError("stop_trigger is invalid")

    attempts = data.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > MAX_RECORDED_ATTEMPTS:
        raise GuardStateError(
            f"attempts must be a list with at most {MAX_RECORDED_ATTEMPTS} entries"
        )
    if len(attempts) > attempt_limit:
        raise GuardStateError(
            "attempts must not contain more entries than the configured attempt_limit"
        )
    failed_keys: set[str] = set()
    hypotheses: set[str] = set()
    evidence_entries: set[str] = set()
    succeeded_at: int | None = None
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            raise GuardStateError(f"attempt {index} must be an object")
        if not legacy_report_only:
            attempt_blocker_key = _bounded_string(
                attempt.get("blocker_key"), f"attempt {index} blocker_key", 256
            )
            if attempt_blocker_key != blocker_key:
                raise GuardStateError(
                    f"attempt {index} blocker_key must match the marker blocker_key; "
                    "a causally independent blocker requires a fresh marker with "
                    "an empty attempt ledger"
                )
        distinct_key = _bounded_string(
            attempt.get("distinct_key"), f"attempt {index} distinct_key", 256
        )
        if distinct_key in failed_keys:
            raise GuardStateError(
                "each retry must use a new distinct_key for a materially different "
                "hypothesis, changed variable, and bounded target"
            )
        hypothesis = _bounded_string(
            attempt.get("hypothesis"), f"attempt {index} hypothesis", 512
        )
        normalized_hypothesis = _normalized_ledger_value(hypothesis)
        if normalized_hypothesis in hypotheses:
            raise GuardStateError(
                "each retry must record a new evidence-derived hypothesis"
            )
        hypotheses.add(normalized_hypothesis)
        raw_new_evidence = attempt.get("new_evidence")
        if raw_new_evidence is None and legacy_report_only:
            new_evidence = None
        else:
            new_evidence = _bounded_string(
                raw_new_evidence, f"attempt {index} new_evidence", 768
            )
        if new_evidence is not None:
            normalized_evidence = _normalized_ledger_value(new_evidence)
            if normalized_evidence in evidence_entries:
                raise GuardStateError(
                    "each retry must record new evidence from logs, stack traces, "
                    "code inspection, or an equivalent observation"
                )
            evidence_entries.add(normalized_evidence)
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
        else:
            if succeeded_at is not None:
                raise GuardStateError("attempts may contain at most one success")
            succeeded_at = index

    if succeeded_at is not None and succeeded_at != len(attempts):
        raise GuardStateError("a successful attempt must be the final attempt")

    attempt_exhausted = len(failed_keys) >= attempt_limit
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
        if succeeded_at is None:
            raise GuardStateError("resolved state requires a successful final attempt")
        if attempt_exhausted or time_exhausted:
            raise GuardStateError(
                "resolved state cannot bypass an exhausted attempt or time limit"
            )
        return data, False, None
    if succeeded_at is not None:
        raise GuardStateError("a successful final attempt requires resolved state")

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


def _attempt_label(index: int) -> str:
    return f"attempt-{index}"


def _continue_with_report(
    reason: str, state: GuardState, report_issue: str
) -> dict[str, Any]:
    attempts = (state.data or {}).get("attempts")
    attempt_count = len(attempts) if isinstance(attempts, list) else 0
    attempt_labels = ", ".join(
        _attempt_label(index) for index in range(1, attempt_count + 1)
    )
    legacy_evidence = isinstance(attempts, list) and any(
        isinstance(attempt, dict) and attempt.get("new_evidence") is None
        for attempt in attempts
    )
    details = [
        reason,
        f"Report validation issue: {report_issue}.",
        "Do not troubleshoot further or call tools.",
        "Return the existing Troubleshooting Report with these exact sections:",
        *[f"- {heading}" for heading in REPORT_HEADINGS],
        f"Include the marker `{REPORT_MARKER}` and the stop trigger.",
        "Use an Outcome classification of UNRESOLVED, "
        "BLOCKED_MISSING_EVIDENCE, or DIAGNOSED_NOT_FIXED.",
        "Use `Blocker: ...` under Blocking Error and `Blocker key: ...` under Source.",
        "For each attempt use exactly: "
        "`- attempt-N | Remediation: ... | Verification: ... | Result: ...`.",
        "For each evidence entry use exactly: `- attempt-N | Evidence: ...`.",
    ]
    if attempt_labels:
        details.append(
            "Attempt labels are derived from list order; cover these labels in "
            f"both Attempts and Evidence: {attempt_labels}."
        )
    if legacy_evidence:
        details.append(
            f"Include this limitation under Evidence: {LEGACY_EVIDENCE_NOTE}"
        )
    details.extend(
        [
            "Do not use empty or placeholder-only report sections.",
            "Redact secrets, private endpoints, customer data, and raw logs.",
            "Return at least this hook-generated, bounded, redacted report:",
            _fallback_report(state, report_issue),
        ]
    )
    return {
        "decision": "block",
        "reason": "\n".join(details),
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


def _substantive_report_value(value: str) -> bool:
    normalized_tokens = re.findall(r"[a-z]+", value.casefold())
    return len(value.strip()) >= 12 and not (
        normalized_tokens
        and all(token in PLACEHOLDER_TOKENS for token in normalized_tokens)
    )


def _prefixed_report_value(section_lines: list[str], prefix: str) -> str | None:
    matches = [
        line[len(prefix) :].strip()
        for line in section_lines
        if line.casefold().startswith(prefix.casefold())
    ]
    if len(matches) != 1 or not _substantive_report_value(matches[0]):
        return None
    return matches[0]


def _attempt_report_fields(
    section_lines: list[str], label: str
) -> tuple[str, str, str] | None:
    pattern = re.compile(
        rf"^-\s*{re.escape(label)}\s*\|\s*"
        r"Remediation:\s*(?P<remediation>.*?)\s*\|\s*"
        r"Verification:\s*(?P<verification>.*?)\s*\|\s*"
        r"Result:\s*(?P<result>[a-z_]+)\.?\s*$",
        re.IGNORECASE,
    )
    matches = [match for line in section_lines if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        return None
    match = matches[0]
    remediation = match.group("remediation").strip()
    verification = match.group("verification").strip()
    result = match.group("result")
    if not (
        _substantive_report_value(remediation)
        and _substantive_report_value(verification)
    ):
        return None
    return remediation, verification, result


def _evidence_report_value(section_lines: list[str], label: str) -> str | None:
    pattern = re.compile(
        rf"^-\s*{re.escape(label)}\s*\|\s*Evidence:\s*(?P<evidence>.+?)\s*$",
        re.IGNORECASE,
    )
    matches = [match for line in section_lines if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        return None
    evidence = matches[0].group("evidence").strip()
    return evidence if _substantive_report_value(evidence) else None


def _report_complete(message: object, state: GuardState) -> tuple[bool, str]:
    if not isinstance(message, str) or REPORT_MARKER not in message:
        return False, f"missing `{REPORT_MARKER}`"
    if _contains_sensitive_report_value(message):
        return False, "report contains a sensitive value that must be redacted"
    if state.stop_trigger is not None and state.stop_trigger not in message:
        return False, f"missing stop trigger `{state.stop_trigger}`"
    lines = [line.strip() for line in message.splitlines()]
    positions: list[int] = []
    search_from = 0
    for heading in REPORT_HEADINGS:
        try:
            position = lines.index(heading, search_from)
        except ValueError:
            return False, f"missing required heading `{heading}`"
        positions.append(position)
        search_from = position + 1
    positions.append(len(lines))
    sections: dict[str, str] = {}
    section_lines_by_heading: dict[str, list[str]] = {}
    for index, position in enumerate(positions[:-1]):
        section_lines = [
            line
            for line in lines[position + 1 : positions[index + 1]]
            if line and not line.startswith("## ")
        ]
        section_text = " ".join(section_lines).strip()
        if not _substantive_report_value(section_text):
            return False, f"section `{REPORT_HEADINGS[index]}` is not substantive"
        sections[REPORT_HEADINGS[index]] = section_text
        section_lines_by_heading[REPORT_HEADINGS[index]] = section_lines

    if not any(
        outcome in sections["## Outcome"]
        for outcome in (
            "UNRESOLVED",
            "BLOCKED_MISSING_EVIDENCE",
            "DIAGNOSED_NOT_FIXED",
        )
    ):
        return False, "Outcome lacks a supported unresolved classification"

    data = state.data or {}
    blocker = _prefixed_report_value(
        section_lines_by_heading["## Blocking Error"], "Blocker:"
    )
    expected_blocker = _bounded_report_value(
        data.get("blocker_summary"), "The recorded blocker remains unresolved."
    )
    if blocker != expected_blocker:
        return False, "Blocking Error requires one substantive `Blocker:` line"
    blocker_key = _prefixed_report_value(
        section_lines_by_heading["## Source"], "Blocker key:"
    )
    expected_blocker_key = _bounded_report_value(
        data.get("blocker_key"), "The recorded blocker source is unavailable."
    )
    if blocker_key != expected_blocker_key:
        return False, "Source requires one substantive `Blocker key:` line"

    attempts = data.get("attempts")
    if isinstance(attempts, list):
        legacy_evidence = False
        for index, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, dict):
                return False, f"{_attempt_label(index)} is not a valid attempt object"
            attempt_label = _attempt_label(index)
            attempt_fields = _attempt_report_fields(
                section_lines_by_heading["## Attempts"], attempt_label
            )
            if attempt_fields is None:
                return (
                    False,
                    f"Attempts requires substantive Remediation, Verification, "
                    f"and Result fields for {attempt_label}",
                )
            result = attempt.get("result")
            expected_remediation = _bounded_attempt_report_value(
                attempt.get("remediation"), "Remediation summary unavailable."
            )
            expected_verification = _bounded_attempt_report_value(
                attempt.get("verification"), "Verification summary unavailable."
            )
            if (
                attempt_fields[0] != expected_remediation
                or attempt_fields[1] != expected_verification
                or not isinstance(result, str)
                or attempt_fields[2] != result
            ):
                return (
                    False,
                    f"Attempts does not match the bounded marker-derived fields "
                    f"for {attempt_label}",
                )
            evidence = _evidence_report_value(
                section_lines_by_heading["## Evidence"], attempt_label
            )
            new_evidence = attempt.get("new_evidence")
            expected_evidence = (
                "Historical evidence summary unavailable."
                if new_evidence is None
                else _bounded_report_value(
                    new_evidence, "Evidence summary unavailable."
                )
            )
            if evidence != expected_evidence:
                return (
                    False,
                    f"Evidence requires a substantive entry for {attempt_label}",
                )
            if new_evidence is None:
                legacy_evidence = True
        if legacy_evidence and LEGACY_EVIDENCE_NOTE not in sections["## Evidence"]:
            return False, "Evidence omits the historical marker limitation"
    return True, ""


def _contains_sensitive_report_value(text: str) -> bool:
    if (
        SENSITIVE_REPORT_RE.search(text)
        or PRIVATE_HOST_RE.search(text)
        or CLOUD_ACCESS_KEY_RE.search(text)
    ):
        return True
    for candidate in (
        *IPV4_CANDIDATE_RE.findall(text),
        *IPV6_CANDIDATE_RE.findall(text),
    ):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local:
            return True
    return False


def _bounded_report_value(value: object, fallback: str, limit: int = 140) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text or _contains_sensitive_report_value(text):
        text = fallback
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _bounded_attempt_report_value(
    value: object, fallback: str, limit: int = 140
) -> str:
    return _bounded_report_value(value, fallback, limit).replace("|", "/")


def _fallback_report(state: GuardState, report_issue: str) -> str:
    data = state.data or {}
    attempts = data.get("attempts")
    attempt_lines: list[str] = []
    evidence_lines: list[str] = []
    legacy_evidence = False
    if isinstance(attempts, list):
        for index, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, dict):
                continue
            label = _attempt_label(index)
            remediation = _bounded_attempt_report_value(
                attempt.get("remediation"), "Remediation summary unavailable."
            )
            verification = _bounded_attempt_report_value(
                attempt.get("verification"), "Verification summary unavailable."
            )
            result = _bounded_report_value(
                attempt.get("result"), "Result unavailable.", limit=40
            )
            attempt_lines.append(
                f"- {label} | Remediation: {remediation} | "
                f"Verification: {verification} | Result: {result}"
            )
            evidence = attempt.get("new_evidence")
            if evidence is None:
                legacy_evidence = True
                evidence_lines.append(
                    f"- {label} | Evidence: Historical evidence summary unavailable."
                )
            else:
                evidence_lines.append(
                    f"- {label} | Evidence: "
                    f"{_bounded_report_value(evidence, 'Evidence summary unavailable.')}"
                )
    if legacy_evidence:
        evidence_lines.append(f"- {LEGACY_EVIDENCE_NOTE}")
    if not attempt_lines:
        attempt_lines.append("- No remediation attempts were recorded.")
    if not evidence_lines:
        evidence_lines.append("- The active-time ledger reached its configured limit.")

    stop_trigger = state.stop_trigger or "unknown"
    return "\n".join(
        [
            REPORT_MARKER,
            f"Stop trigger: {stop_trigger}",
            "## Outcome",
            "UNRESOLVED. The bounded remediation tranche is exhausted.",
            "## Blocking Error",
            "Blocker: "
            + _bounded_report_value(
                data.get("blocker_summary"), "The recorded blocker remains unresolved."
            ),
            "## Source",
            "Blocker key: "
            + _bounded_report_value(
                data.get("blocker_key"), "The recorded blocker source is unavailable."
            ),
            "## Attempts",
            *attempt_lines,
            "## Evidence",
            *evidence_lines,
            "## Current State",
            (
                "No further tool or remediation was authorized. The assistant's "
                f"report remained invalid because {report_issue}."
            ),
            "## Next Action",
            (
                "A new explicit user instruction is required before another "
                "bounded troubleshooting tranche."
            ),
        ]
    )


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
    report_complete, report_issue = _report_complete(
        payload.get("last_assistant_message"), state
    )
    if report_complete:
        return _stop(
            "Remediation budget exhausted and troubleshooting report delivered."
        )
    if payload.get("stop_hook_active"):
        return _stop(
            "Remediation budget exhausted; a bounded fallback report was emitted.",
            warning=_fallback_report(state, report_issue),
        )
    return _continue_with_report(
        "The remediation budget is exhausted and the previous response did not "
        "contain the complete user-visible report. Produce it now.",
        state,
        report_issue,
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
