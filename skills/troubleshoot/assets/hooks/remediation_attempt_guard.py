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
import secrets
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


SCHEMA = "codex/remediation-budget-v4"
PREVIOUS_SCHEMA = "codex/remediation-budget-v3"
LEGACY_SCHEMA = "codex/remediation-budget-v1"
AUTHORIZATION_SCHEMA = "codex/remediation-budget-authorization-v1"
AUTHORIZATION_FILE_NAME = "remediation-budget-authorization.json"
REPORT_OBLIGATION_SCHEMA = "codex/troubleshoot-report-obligation-v1"
REPORT_OBLIGATION_FILE_NAME = "troubleshoot-report-obligation.json"
MARKER_START = "<!-- codex-remediation-budget:v1\n"
MARKER_END = "\n-->"
MAX_MARKER_PREFIX_BYTES = 12 * 1024
MAX_TASK_STATE_BYTES = 1024 * 1024
MAX_AUTHORIZATION_BYTES = 4096
MAX_REPORT_OBLIGATION_BYTES = 2048
MAX_REPORT_CORRECTIONS = 1
MAX_FALLBACK_PREVIEW_CHARS = 9000
REPORT_READY_FIELD = "_troubleshootReportReady"
REPORT_FINALIZE_FIELD = "_troubleshootFinalizeReport"
DEFAULT_ATTEMPT_LIMIT = 5
MAX_ATTEMPT_LIMIT = 10
MAX_RECORDED_ATTEMPTS = 10
DEFAULT_TIME_LIMIT_MINUTES = 120
MAX_TIME_LIMIT_MINUTES = 180
HISTORICAL_MAX_ATTEMPT_LIMIT = 3
HISTORICAL_DEFAULT_TIME_LIMIT_MINUTES = 60
SUPPORTED_ATTEMPT_RESULTS = ("failed_same_blocker", "succeeded")
CANONICAL_ATTEMPT_FIELDS = (
    "blocker_key",
    "distinct_key",
    "hypothesis",
    "new_evidence",
    "remediation",
    "verification",
    "result",
)
CANONICAL_MARKER_FIELDS = {
    "schema",
    "blocker_key",
    "blocker_summary",
    "tranche",
    "started_at",
    "active_seconds",
    "attempt_limit",
    "time_limit_minutes",
    "budget_authorization_id",
    "attempts",
    "status",
    "stop_trigger",
    "override_summary",
}
SAFE_SESSION_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
AUTHORIZATION_ID_RE = re.compile(r"[0-9a-f]{32}")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
SENSITIVE_REPORT_RE = re.compile(
    r"(?i)(?:"
    r"-----BEGIN [A-Z ]+-----|"
    r"""["']?(?:access[_-]?key|api[_-]?key|authorization|certificate|cookie|"""
    r"credential|password|passwd|private[_-]?key|"
    r"""pwd|secret|session|token)["']?\s*[:=]\s*\S+|"""
    r"bearer\s+\S+|"
    r"/(?:Users|home)/\S+|"
    r"[A-Z]:[\\/]+Users[\\/]+\S+"
    r")"
)
URL_CANDIDATE_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
HOST_FIELD_RE = re.compile(
    r"(?i)(?:^|\s)-?\s*(?:public\s+)?(?:endpoint|host|hostname)\s*[:=]\s*"
    r"(?P<value>\S+)"
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
MAX_REPORT_FIELD_CHARS = 70
LEGACY_EVIDENCE_NOTE = (
    "Retry-admission evidence summaries were not recorded by the earlier "
    "terminal marker contract."
)
REPORT_HEADINGS = (
    "## Outcome",
    "## Failure Contract",
    "## Architecture Verdict",
    "## Component Verification Matrix",
    "## Incident Timeline",
    "## Logs Examined",
    "## Hypotheses And Experiments",
    "## Code Debugging",
    "## Root Cause",
    "## Remediation",
    "## Post-Fix Validation",
    "## Completion Gate",
    "## Remaining Unknowns And Residual Risks",
)
GENERAL_REPORT_OUTCOMES = (
    "VERIFIED_FIXED",
    "MITIGATED_NOT_PROVEN",
    "DIAGNOSED_NOT_FIXED",
    "BLOCKED_MISSING_EVIDENCE",
    "UNRESOLVED",
)
COMPLETION_CRITERIA = (
    "Design",
    "Infrastructure",
    "Connectivity",
    "Configuration",
    "Runtime health",
    "Logs",
    "Relevant code paths",
)
COMPLETION_VERDICTS = ("PASS", "FAIL", "UNKNOWN")
PASS_NO_GAP = "None after scoped verification."
PASS_EVIDENCE_BY_CRITERION = {
    "Design": "Verified: Architecture Verdict.",
    "Infrastructure": "Verified: Component Verification Matrix.",
    "Connectivity": "Verified: Component Verification Matrix.",
    "Configuration": "Verified: Component Verification Matrix.",
    "Runtime health": "Verified: Component Verification Matrix.",
    "Logs": "Verified: Logs Examined.",
    "Relevant code paths": "Verified: Code Debugging and Post-Fix Validation.",
}
COMPONENT_PASS_COLUMNS = {
    "Infrastructure": (1, 5),
    "Connectivity": (4,),
    "Configuration": (2,),
    "Runtime health": (3, 6),
}
INSUFFICIENT_PASS_DETAIL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:unavailable|unverified|unproven|unexamined|unrun|unknown|"
    r"incomplete|insufficient|missing|absent)\b|"
    r"\b(?:not|never)\s+(?:available|run|examined|checked|verified|"
    r"proven|collected|captured)\b|"
    r"\bno(?:\s+[a-z][a-z-]*){0,8}\s+"
    r"(?:evidence|logs?|proof|verification|coverage)\b"
    r")"
)
INSUFFICIENT_PASS_FINDING_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:unavailable|unverified|unproven|unexamined|unrun|unknown|"
    r"incomplete|insufficient|missing|absent)\b|"
    r"\b(?:not|never)\s+(?:available|run|examined|checked|verified|"
    r"proven|collected|captured)\b|"
    r"\bno(?:\s+[a-z][a-z-]*){0,8}\s+"
    r"(?:evidence|logs?|proof|verification|coverage)\s+"
    r"(?:(?:was|were|is|are)\s+)?"
    r"(?:available|collected|captured|retained)\b"
    r")"
)
REPORT_TABLE_HEADERS = {
    "## Component Verification Matrix": (
        "Component",
        "Version and existence",
        "Active configuration",
        "Runtime health",
        "Dependencies, authentication, and DNS",
        "Resources and time sync",
        "Restart history and recent changes",
        "Evidence",
    ),
    "## Incident Timeline": (
        "Time",
        "Source and clock basis",
        "Correlation identifier",
        "Event",
        "Evidence or inference",
    ),
    "## Logs Examined": (
        "Layer",
        "Source",
        "Window and filters",
        "Finding",
        "Coverage status",
    ),
    "## Hypotheses And Experiments": (
        "Hypothesis",
        "Prediction and falsifier",
        "Bounded experiment",
        "Observation",
        "Decision",
    ),
}
CANONICAL_LOG_LAYERS = (
    "Component",
    "Application or job",
    "Container or orchestrator",
    "Service manager",
    "OS and kernel",
    "Network and firewall",
    "Storage",
    "GPU or hardware",
)
LOG_COVERAGE_STATUSES = (
    "examined",
    "unavailable",
    "unsafe",
    "not applicable",
)
REQUIRED_REPORT_FIELDS = {
    "## Outcome": ("- Confidence:", "- Current impact:", "- Stabilization status:"),
    "## Failure Contract": (
        "- Expected:",
        "- Actual:",
        "- Scope and signature:",
        "- Reproduction or characterization:",
        "- Success criteria and constraints:",
        "- Target, environment, blast radius, and allowed mutations:",
        "- Included system boundary:",
        "- Excluded system boundary:",
        "- Exercised control and data paths:",
        "- Incident-window start:",
        "- Incident-window end:",
    ),
    "## Architecture Verdict": (
        "- Observed technologies, versions, and deployment model:",
        "- Configuration authorities:",
        "- Components, dependencies, ports, protocols, and authentication:",
        "- Control and data flows:",
        "- Official vendor architecture comparison and verdict:",
    ),
    "## Code Debugging": (
        "- Reproduction and execution or data path:",
        "- Stack trace, core dump, or equivalent runtime evidence:",
        "- Configuration, environment, and data inputs:",
        "- Recent changes and affected or unaffected comparison:",
        "- Focused tests, static or dynamic analysis, and instrumentation:",
        "- Instrumentation cleanup and limitations:",
    ),
    "## Root Cause": (
        "- Earliest divergence:",
        "- Causal chain:",
        "- Counterfactual and reintroduction:",
        "- Alternatives eliminated:",
        "- Confidence:",
    ),
    "## Remediation": (
        "- Design classification and handoff:",
        "- Changes made:",
        "- Authority and safety basis:",
        "- Rollback or recovery state:",
    ),
    "## Post-Fix Validation": (
        "- Original reproducer:",
        "- Regression oracle:",
        "- Targeted and boundary checks:",
        "- Repeated or dynamic diagnostics:",
        "- Live trial status and claim scope:",
        "- Candidate, target, checkpoint, and replay range:",
        "- Intervention ledger and first contaminated boundary:",
        "- Product-owned transitions and independent postconditions:",
    ),
    "## Remaining Unknowns And Residual Risks": (
        "- Unknowns and coverage gaps:",
        "- Residual risks:",
        "- Exact next action:",
    ),
}
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


@dataclass(frozen=True)
class AuthorizationState:
    kind: str
    authorization_file: Path | None
    data: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReportObligationState:
    kind: str
    obligation_file: Path | None
    data: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TroubleshootInvocation:
    attempt_limit: int | None
    time_limit_minutes: int | None
    supplied_fields: frozenset[str]


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


def authorization_file_for_payload(payload: dict[str, Any]) -> Path | None:
    state_file = state_file_for_payload(payload)
    if state_file is None:
        return None
    return state_file.with_name(AUTHORIZATION_FILE_NAME)


def report_obligation_file_for_payload(payload: dict[str, Any]) -> Path | None:
    state_file = state_file_for_payload(payload)
    if state_file is None:
        return None
    return state_file.with_name(REPORT_OBLIGATION_FILE_NAME)


def _payload_binding_hashes(payload: dict[str, Any]) -> tuple[str, str] | None:
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if session_id is None or str(session_id) == "" or not isinstance(cwd, str):
        return None
    workspace = hashlib.sha256(resolve_root(cwd).encode("utf-8")).hexdigest()
    session = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
    return workspace, session


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


def _assert_safe_authorization_file(authorization_file: Path) -> None:
    state_file = authorization_file.with_name("current.md")
    if authorization_file.name != AUTHORIZATION_FILE_NAME:
        raise GuardStateError("authorization path has an unexpected name")
    _assert_safe_state_file(state_file)
    session_dir = authorization_file.parent
    if session_dir.is_symlink():
        raise GuardStateError("authorization directory is a symbolic link")
    if authorization_file.exists() and session_dir.exists():
        directory_stat = session_dir.lstat()
        directory_mode = stat.S_IMODE(directory_stat.st_mode)
        if directory_mode != 0o700:
            raise GuardStateError("authorization directory permissions must be 0700")
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise GuardStateError("authorization directory owner does not match")
    if authorization_file.is_symlink():
        raise GuardStateError("authorization file is a symbolic link")
    if authorization_file.exists():
        file_stat = authorization_file.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise GuardStateError("authorization path is not a regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise GuardStateError("authorization file permissions must be 0600")
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise GuardStateError("authorization file owner does not match")


def _authorization_record(
    value: object, field: str, *, pending: bool
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GuardStateError(f"authorization {field} must be an object or null")
    expected_fields = {
        "authorization_id",
        "attempt_limit",
        "time_limit_minutes",
        "issued_turn_hash",
    }
    if pending:
        expected_fields.update(
            {
                "mode",
                "expected_marker_digest",
                "previous_blocker_key",
                "previous_tranche",
                "previous_started_at",
            }
        )
    if set(value) != expected_fields:
        raise GuardStateError(
            f"authorization {field} fields do not match the canonical schema"
        )
    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or not AUTHORIZATION_ID_RE.fullmatch(
        authorization_id
    ):
        raise GuardStateError(f"authorization {field} id is invalid")
    issued_turn_hash = value.get("issued_turn_hash")
    if not isinstance(issued_turn_hash, str) or not DIGEST_RE.fullmatch(
        issued_turn_hash
    ):
        raise GuardStateError(f"authorization {field} turn binding is invalid")
    _attempt_limit(value.get("attempt_limit"))
    _time_limit(value.get("time_limit_minutes"))
    if pending:
        if value.get("mode") not in {"resize_active", "next_tranche"}:
            raise GuardStateError("authorization pending mode is invalid")
        expected_digest = value.get("expected_marker_digest")
        if not isinstance(expected_digest, str) or not DIGEST_RE.fullmatch(
            expected_digest
        ):
            raise GuardStateError("authorization pending marker binding is invalid")
        _bounded_string(
            value.get("previous_blocker_key"),
            "authorization pending previous_blocker_key",
            256,
        )
        previous_tranche = value.get("previous_tranche")
        if (
            isinstance(previous_tranche, bool)
            or not isinstance(previous_tranche, int)
            or previous_tranche < 1
        ):
            raise GuardStateError(
                "authorization pending previous_tranche must be positive"
            )
        _bounded_string(
            value.get("previous_started_at"),
            "authorization pending previous_started_at",
            64,
        )
    return value


def _validate_authorization_data(
    data: object, payload: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise GuardStateError("authorization sidecar must contain a JSON object")
    if set(data) != {
        "schema",
        "workspace_hash",
        "session_hash",
        "current",
        "pending",
        "terminal",
    }:
        raise GuardStateError(
            "authorization sidecar fields do not match the canonical schema"
        )
    if data.get("schema") != AUTHORIZATION_SCHEMA:
        raise GuardStateError("authorization sidecar schema is invalid")
    bindings = _payload_binding_hashes(payload)
    if bindings is None:
        raise GuardStateError("authorization payload binding is unavailable")
    if data.get("workspace_hash") != bindings[0]:
        raise GuardStateError("authorization workspace binding does not match")
    if data.get("session_hash") != bindings[1]:
        raise GuardStateError("authorization session binding does not match")
    current = _authorization_record(data.get("current"), "current", pending=False)
    pending = _authorization_record(data.get("pending"), "pending", pending=True)
    terminal = data.get("terminal")
    if terminal is not None:
        if not isinstance(terminal, dict):
            raise GuardStateError("authorization terminal lock must be an object")
        if set(terminal) != {"marker_digest", "blocker_key", "tranche"}:
            raise GuardStateError(
                "authorization terminal fields do not match the canonical schema"
            )
        marker_digest = terminal.get("marker_digest")
        if not isinstance(marker_digest, str) or not DIGEST_RE.fullmatch(marker_digest):
            raise GuardStateError("authorization terminal marker binding is invalid")
        _bounded_string(
            terminal.get("blocker_key"),
            "authorization terminal blocker_key",
            256,
        )
        terminal_tranche = terminal.get("tranche")
        if (
            isinstance(terminal_tranche, bool)
            or not isinstance(terminal_tranche, int)
            or terminal_tranche < 1
        ):
            raise GuardStateError("authorization terminal tranche must be positive")
    if current is None and pending is None and terminal is None:
        raise GuardStateError("authorization sidecar has no active record")
    current_id = current.get("authorization_id") if current else None
    pending_id = pending.get("authorization_id") if pending else None
    if current_id is not None and current_id == pending_id:
        raise GuardStateError("authorization current and pending ids must differ")
    return data


def load_authorization_state(payload: dict[str, Any]) -> AuthorizationState:
    authorization_file = authorization_file_for_payload(payload)
    if authorization_file is None:
        return AuthorizationState("missing", None)
    try:
        _assert_safe_authorization_file(authorization_file)
        if not authorization_file.exists():
            return AuthorizationState("missing", authorization_file)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(authorization_file, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise GuardStateError("authorization path is not a regular file")
            if stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise GuardStateError("authorization file permissions must be 0600")
            if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
                raise GuardStateError("authorization file owner does not match")
            chunks: list[bytes] = []
            remaining = MAX_AUTHORIZATION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_AUTHORIZATION_BYTES:
            raise GuardStateError("authorization sidecar exceeds 4096 bytes")
        data = _load_json_without_duplicate_keys(
            raw.decode("utf-8"), "authorization sidecar"
        )
        return AuthorizationState(
            "valid", authorization_file, _validate_authorization_data(data, payload)
        )
    except GuardStateError as exc:
        reason = _public_reason(str(exc))
    except json.JSONDecodeError:
        reason = "authorization sidecar JSON is malformed"
    except UnicodeError:
        reason = "authorization sidecar is not valid UTF-8"
    except OSError:
        reason = "authorization sidecar could not be read safely"
    return AuthorizationState("invalid", authorization_file, reason=reason)


def _write_authorization_data(
    payload: dict[str, Any], data: dict[str, Any]
) -> AuthorizationState:
    authorization_file = authorization_file_for_payload(payload)
    state_file = state_file_for_payload(payload)
    if authorization_file is None or state_file is None:
        raise GuardStateError("authorization path is unavailable")
    validated = _validate_authorization_data(data, payload)
    encoded = (
        json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_AUTHORIZATION_BYTES:
        raise GuardStateError("authorization sidecar exceeds 4096 bytes")

    _assert_safe_state_file(state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_safe_state_file(state_file)
    os.chmod(state_file.parent, 0o700, follow_symlinks=False)
    _assert_safe_authorization_file(authorization_file)

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = os.open(state_file.parent, directory_flags)
    temporary_name = f".{AUTHORIZATION_FILE_NAME}.{secrets.token_hex(12)}.tmp"
    try:
        directory_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise GuardStateError("authorization directory is not a directory")
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise GuardStateError("authorization directory permissions must be 0700")
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise GuardStateError("authorization directory owner does not match")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        descriptor = os.open(
            temporary_name, file_flags, 0o600, dir_fd=directory_descriptor
        )
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("authorization sidecar write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary_name,
            AUTHORIZATION_FILE_NAME,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.chmod(
            AUTHORIZATION_FILE_NAME,
            0o600,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.fsync(directory_descriptor)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)
    return AuthorizationState("valid", authorization_file, validated)


def _assert_safe_report_obligation_file(obligation_file: Path) -> None:
    state_file = obligation_file.with_name("current.md")
    if obligation_file.name != REPORT_OBLIGATION_FILE_NAME:
        raise GuardStateError("report obligation path has an unexpected name")
    _assert_safe_state_file(state_file)
    session_dir = obligation_file.parent
    if session_dir.is_symlink():
        raise GuardStateError("report obligation directory is a symbolic link")
    if obligation_file.exists() and session_dir.exists():
        directory_stat = session_dir.lstat()
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise GuardStateError(
                "report obligation directory permissions must be 0700"
            )
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise GuardStateError("report obligation directory owner does not match")
    if obligation_file.is_symlink():
        raise GuardStateError("report obligation file is a symbolic link")
    if obligation_file.exists():
        file_stat = obligation_file.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise GuardStateError("report obligation path is not a regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise GuardStateError("report obligation file permissions must be 0600")
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise GuardStateError("report obligation file owner does not match")


def _validate_report_obligation_data(
    data: object, payload: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise GuardStateError("report obligation must contain a JSON object")
    if set(data) != {
        "schema",
        "workspace_hash",
        "session_hash",
        "turn_hash",
        "status",
        "corrections",
    }:
        raise GuardStateError(
            "report obligation fields do not match the canonical schema"
        )
    if data.get("schema") != REPORT_OBLIGATION_SCHEMA:
        raise GuardStateError("report obligation schema is invalid")
    bindings = _payload_binding_hashes(payload)
    if bindings is None:
        raise GuardStateError("report obligation payload binding is unavailable")
    if data.get("workspace_hash") != bindings[0]:
        raise GuardStateError("report obligation workspace binding does not match")
    if data.get("session_hash") != bindings[1]:
        raise GuardStateError("report obligation session binding does not match")
    turn_hash = data.get("turn_hash")
    if not isinstance(turn_hash, str) or not DIGEST_RE.fullmatch(turn_hash):
        raise GuardStateError("report obligation turn binding is invalid")
    if data.get("status") not in {"active", "delivered", "fallback"}:
        raise GuardStateError("report obligation status is invalid")
    corrections = data.get("corrections")
    if (
        isinstance(corrections, bool)
        or not isinstance(corrections, int)
        or corrections < 0
        or corrections > MAX_REPORT_CORRECTIONS
    ):
        raise GuardStateError("report obligation correction count is invalid")
    return data


def load_report_obligation_state(
    payload: dict[str, Any],
) -> ReportObligationState:
    obligation_file = report_obligation_file_for_payload(payload)
    if obligation_file is None:
        return ReportObligationState("missing", None)
    try:
        _assert_safe_report_obligation_file(obligation_file)
        if not obligation_file.exists():
            return ReportObligationState("missing", obligation_file)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(obligation_file, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise GuardStateError("report obligation path is not a regular file")
            if stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise GuardStateError("report obligation file permissions must be 0600")
            if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
                raise GuardStateError("report obligation file owner does not match")
            chunks: list[bytes] = []
            remaining = MAX_REPORT_OBLIGATION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_REPORT_OBLIGATION_BYTES:
            raise GuardStateError("report obligation exceeds 2048 bytes")
        data = _load_json_without_duplicate_keys(
            raw.decode("utf-8"), "report obligation"
        )
        return ReportObligationState(
            "valid",
            obligation_file,
            _validate_report_obligation_data(data, payload),
        )
    except GuardStateError as exc:
        reason = _public_reason(str(exc))
    except json.JSONDecodeError:
        reason = "report obligation JSON is malformed"
    except UnicodeError:
        reason = "report obligation is not valid UTF-8"
    except OSError:
        reason = "report obligation could not be read safely"
    return ReportObligationState("invalid", obligation_file, reason=reason)


def _write_report_obligation_data(
    payload: dict[str, Any], data: dict[str, Any]
) -> ReportObligationState:
    obligation_file = report_obligation_file_for_payload(payload)
    state_file = state_file_for_payload(payload)
    if obligation_file is None or state_file is None:
        raise GuardStateError("report obligation path is unavailable")
    validated = _validate_report_obligation_data(data, payload)
    encoded = (
        json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_REPORT_OBLIGATION_BYTES:
        raise GuardStateError("report obligation exceeds 2048 bytes")

    _assert_safe_state_file(state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_safe_state_file(state_file)
    os.chmod(state_file.parent, 0o700, follow_symlinks=False)
    _assert_safe_report_obligation_file(obligation_file)

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = os.open(state_file.parent, directory_flags)
    temporary_name = f".{REPORT_OBLIGATION_FILE_NAME}.{secrets.token_hex(12)}.tmp"
    try:
        directory_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise GuardStateError("report obligation directory is not a directory")
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise GuardStateError(
                "report obligation directory permissions must be 0700"
            )
        if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
            raise GuardStateError("report obligation directory owner does not match")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        descriptor = os.open(
            temporary_name, file_flags, 0o600, dir_fd=directory_descriptor
        )
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("report obligation write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary_name,
            REPORT_OBLIGATION_FILE_NAME,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.chmod(
            REPORT_OBLIGATION_FILE_NAME,
            0o600,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.fsync(directory_descriptor)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)
    return ReportObligationState("valid", obligation_file, validated)


def _report_obligation_active(obligation: ReportObligationState) -> bool:
    return (
        obligation.kind == "valid"
        and isinstance(obligation.data, dict)
        and obligation.data.get("status") == "active"
    )


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


def _time_limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_TIME_LIMIT_MINUTES
    ):
        raise GuardStateError(
            f"time_limit_minutes must be an integer from 1 to {MAX_TIME_LIMIT_MINUTES}"
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


def _load_json_without_duplicate_keys(raw: str, label: str) -> object:
    def build_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise GuardStateError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=build_object)


def _marker_core_digest(data: dict[str, Any]) -> str:
    core = {
        key: value
        for key, value in data.items()
        if key
        not in {
            "attempt_limit",
            "time_limit_minutes",
            "budget_authorization_id",
        }
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authorization_profile_matches(
    data: dict[str, Any], record: dict[str, Any] | None
) -> bool:
    return bool(
        record
        and data.get("budget_authorization_id") == record.get("authorization_id")
        and data.get("attempt_limit") == record.get("attempt_limit")
        and data.get("time_limit_minutes") == record.get("time_limit_minutes")
    )


def _marker_authorization_slot(
    data: dict[str, Any], authorization: AuthorizationState
) -> str | None:
    authorization_id = data.get("budget_authorization_id")
    if authorization_id is not None and (
        not isinstance(authorization_id, str)
        or not AUTHORIZATION_ID_RE.fullmatch(authorization_id)
    ):
        raise GuardStateError("budget_authorization_id must be null or a valid id")
    if authorization.kind == "invalid":
        raise GuardStateError(
            "authorization sidecar is invalid"
            + (f": {authorization.reason}" if authorization.reason else "")
        )
    if authorization.kind == "missing":
        if (
            authorization_id is None
            and data.get("attempt_limit") == DEFAULT_ATTEMPT_LIMIT
            and data.get("time_limit_minutes") == DEFAULT_TIME_LIMIT_MINUTES
        ):
            return "implicit_default"
        raise GuardStateError(
            "non-default or explicitly authorized budgets require the private "
            "session authorization sidecar"
        )

    authorization_data = authorization.data or {}
    current = authorization_data.get("current")
    pending = authorization_data.get("pending")
    terminal = authorization_data.get("terminal")
    if _authorization_profile_matches(data, current):
        slot = "current"
    elif _authorization_profile_matches(data, pending):
        slot = "pending"
    elif (
        current is None
        and authorization_id is None
        and data.get("attempt_limit") == DEFAULT_ATTEMPT_LIMIT
        and data.get("time_limit_minutes") == DEFAULT_TIME_LIMIT_MINUTES
    ):
        slot = "implicit_default"
    else:
        slot = None
    if slot is None:
        raise GuardStateError(
            "marker budget values and authorization id do not match the private "
            "session authorization"
        )
    if isinstance(terminal, dict) and _marker_core_digest(data) != terminal.get(
        "marker_digest"
    ):
        if not (
            slot == "pending"
            and isinstance(pending, dict)
            and pending.get("mode") == "next_tranche"
        ):
            raise GuardStateError(
                "an exhausted tranche cannot be reopened without a fresh-tranche "
                "authorization"
            )
    return slot


def _validate_data(
    data: object, authorization: AuthorizationState
) -> tuple[dict[str, Any], bool, str | None]:
    if not isinstance(data, dict):
        raise GuardStateError("marker must contain a JSON object")
    schema = data.get("schema")
    if schema == PREVIOUS_SCHEMA:
        raise GuardStateError(
            f"{PREVIOUS_SCHEMA} markers are not reinterpreted; replace the exact "
            f"marker with {SCHEMA} before continuing"
        )
    if schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise GuardStateError(f"marker schema must be {SCHEMA}")
    legacy_report_only = schema == LEGACY_SCHEMA
    if not legacy_report_only and set(data) != CANONICAL_MARKER_FIELDS:
        missing = sorted(CANONICAL_MARKER_FIELDS - set(data))
        unknown = sorted(set(data) - CANONICAL_MARKER_FIELDS)
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown fields present")
        raise GuardStateError(
            "v4 marker fields do not match the canonical schema ("
            + "; ".join(details)
            + ")"
        )
    uses_historical_limits = legacy_report_only
    blocker_key = _bounded_string(data.get("blocker_key"), "blocker_key", 256)
    _bounded_string(data.get("blocker_summary"), "blocker_summary", 512)
    tranche = data.get("tranche")
    if isinstance(tranche, bool) or not isinstance(tranche, int) or tranche < 1:
        raise GuardStateError("tranche must be a positive integer")
    _parse_started_at(data.get("started_at"))
    active_seconds = _nonnegative_int(data.get("active_seconds"), "active_seconds")
    attempt_limit = _attempt_limit(data.get("attempt_limit"))
    if legacy_report_only:
        time_limit = _positive_int_or_none(
            data.get("time_limit_minutes"), "time_limit_minutes"
        )
    else:
        time_limit = _time_limit(data.get("time_limit_minutes"))
    if uses_historical_limits and attempt_limit > HISTORICAL_MAX_ATTEMPT_LIMIT:
        raise GuardStateError(
            f"{schema} attempt_limit must be an integer from 1 to "
            f"{HISTORICAL_MAX_ATTEMPT_LIMIT}"
        )
    if not uses_historical_limits:
        _marker_authorization_slot(data, authorization)
    override = data.get("override_summary")
    if override is not None:
        _bounded_string(override, "override_summary", 512)
    if uses_historical_limits:
        uses_historical_defaults = (
            attempt_limit == HISTORICAL_MAX_ATTEMPT_LIMIT
            and time_limit == HISTORICAL_DEFAULT_TIME_LIMIT_MINUTES
        )
        if (not uses_historical_defaults or tranche > 1) and override is None:
            raise GuardStateError(
                "historical non-default limits and continuation tranches require "
                "an explicit override summary"
            )
    elif tranche == 1 and override is not None:
        raise GuardStateError(
            "initial v4 tranche requires a null override_summary; "
            "override_summary records continuation authorization only"
        )
    elif tranche > 1 and override is None:
        raise GuardStateError(
            "continuation tranches require an explicit override summary"
        )

    status = data.get("status")
    if status not in {"active", "exhausted", "resolved"}:
        raise GuardStateError("status must be active, exhausted, or resolved")
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
            missing_fields = [
                field for field in CANONICAL_ATTEMPT_FIELDS if field not in attempt
            ]
            if missing_fields:
                raise GuardStateError(
                    f"attempt {index} is incomplete; missing canonical fields: "
                    + ", ".join(missing_fields)
                    + ". Attempt ledger entries record completed remediation and "
                    "verification only; if this attempt has not executed and been "
                    "verified, remove it and keep planned or in-progress work in "
                    "prose; otherwise repair every missing field atomically with the "
                    "verified result"
                )
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
                "attempt ledger entries record completed remediation and verification "
                "only. If this attempt was not verified, remove it and keep unverified "
                "progress in prose. A causally independent blocker requires replacing "
                "the marker with a fresh empty attempt ledger"
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


def load_guard_state(
    payload: dict[str, Any], authorization: AuthorizationState | None = None
) -> GuardState:
    state_file = state_file_for_payload(payload)
    if state_file is None:
        return GuardState("missing", None)
    if authorization is None:
        authorization = load_authorization_state(payload)
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
        data = _load_json_without_duplicate_keys(
            raw[body_start:end].decode("utf-8"), "remediation marker"
        )
        validated, exhausted, stop_trigger = _validate_data(data, authorization)
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
    return {
        "decision": "block",
        "reason": "\n".join(
            [
                reason,
                "Do not troubleshoot further or call tools.",
                "Return this hook-generated, bounded, redacted report exactly:",
                _fallback_report(state, report_issue),
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


def _substantive_report_value(value: str) -> bool:
    normalized_tokens = re.findall(r"[a-z]+", value.casefold())
    return len(value.strip()) >= 12 and not (
        normalized_tokens
        and all(token in PLACEHOLDER_TOKENS for token in normalized_tokens)
    )


def _non_placeholder_report_identifier(value: str) -> bool:
    normalized_tokens = re.findall(r"[a-z]+", value.casefold())
    return (
        bool(value.strip())
        and any(character.isalnum() for character in value)
        and not (
            normalized_tokens
            and all(token in PLACEHOLDER_TOKENS for token in normalized_tokens)
        )
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


def _completion_rows(
    section_lines: list[str], classification: str
) -> tuple[dict[str, str] | None, str]:
    expected_header = "| Criterion | Verdict | Evidence | Gap or next action |"
    expected_separator = "| --- | --- | --- | --- |"
    if section_lines.count(expected_header) != 1:
        return None, "Completion Gate requires exactly one canonical table header"
    header_position = section_lines.index(expected_header)
    if (
        header_position + 1 >= len(section_lines)
        or section_lines[header_position + 1] != expected_separator
        or section_lines.count(expected_separator) != 1
    ):
        return None, "Completion Gate requires one canonical table separator"
    if any(line.startswith("|") for line in section_lines[:header_position]):
        return None, "Completion Gate contains a table row before its header"
    rows: dict[str, str] = {}
    for line in section_lines:
        if not line.startswith("|"):
            continue
        if not line.endswith("|"):
            return None, "Completion Gate contains a malformed table row"
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if cells == ["Criterion", "Verdict", "Evidence", "Gap or next action"]:
            continue
        if len(cells) == 4 and all(
            re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells
        ):
            continue
        if len(cells) != 4:
            return None, "Completion Gate rows require exactly four columns"
        criterion, verdict, evidence, gap = cells
        if criterion not in COMPLETION_CRITERIA:
            return None, f"Completion Gate contains unsupported criterion `{criterion}`"
        if criterion in rows:
            return None, f"Completion Gate duplicates criterion `{criterion}`"
        if verdict not in COMPLETION_VERDICTS:
            return None, f"Completion Gate verdict for `{criterion}` is unsupported"
        if not _substantive_report_value(evidence):
            return (
                None,
                f"Completion Gate evidence for `{criterion}` is not substantive",
            )
        if not _substantive_report_value(gap):
            return (
                None,
                f"Completion Gate next action for `{criterion}` is not substantive",
            )
        if verdict == "PASS":
            expected_evidence = PASS_EVIDENCE_BY_CRITERION[criterion]
            if evidence != expected_evidence:
                return (
                    None,
                    f"Completion Gate PASS evidence for `{criterion}` must be "
                    f"`{expected_evidence}`",
                )
            if gap != PASS_NO_GAP:
                return (
                    None,
                    f"Completion Gate PASS gap for `{criterion}` must be "
                    f"`{PASS_NO_GAP}`",
                )
        elif gap == PASS_NO_GAP:
            return (
                None,
                f"Completion Gate `{verdict}` row for `{criterion}` must name "
                "a real gap or next action",
            )
        rows[criterion] = verdict
    missing = [criterion for criterion in COMPLETION_CRITERIA if criterion not in rows]
    if missing:
        return None, "Completion Gate is missing: " + ", ".join(missing)
    if classification == "VERIFIED_FIXED":
        non_pass = [
            criterion for criterion, verdict in rows.items() if verdict != "PASS"
        ]
        if non_pass:
            return (
                None,
                "VERIFIED_FIXED requires PASS for: " + ", ".join(non_pass),
            )
    return rows, ""


def _report_table_issue(section_lines: list[str], header: tuple[str, ...]) -> str:
    expected_header = "| " + " | ".join(header) + " |"
    expected_separator = "| " + " | ".join("---" for _ in header) + " |"
    if section_lines.count(expected_header) != 1:
        return f"requires exactly one table header `{expected_header}`"
    header_position = section_lines.index(expected_header)
    if any(line.startswith("|") for line in section_lines[:header_position]):
        return "contains a table row before its canonical header"
    if (
        header_position + 1 >= len(section_lines)
        or section_lines[header_position + 1] != expected_separator
    ):
        return "requires the canonical table separator immediately after its header"
    rows = [
        line for line in section_lines[header_position + 2 :] if line.startswith("|")
    ]
    if not rows:
        return "requires at least one substantive data row"
    for line in rows:
        if not line.endswith("|"):
            return "contains a malformed table row"
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != len(header) or any(not cell for cell in cells):
            return "contains a row with missing or extra cells"
        if not _substantive_report_value(" ".join(cells)):
            return "contains a non-substantive data row"
    return ""


def _report_table_cells(
    section_lines: list[str], header: tuple[str, ...]
) -> list[list[str]]:
    expected_header = "| " + " | ".join(header) + " |"
    header_position = section_lines.index(expected_header)
    return [
        [cell.strip() for cell in line[1:-1].split("|")]
        for line in section_lines[header_position + 2 :]
        if line.startswith("|")
    ]


def _table_evidence_issue(
    section_lines: list[str],
    header: tuple[str, ...],
    evidence_columns: tuple[int, ...],
) -> str:
    for row in _report_table_cells(section_lines, header):
        if header == REPORT_TABLE_HEADERS[
            "## Component Verification Matrix"
        ] and not _non_placeholder_report_identifier(row[0]):
            return "contains a placeholder component identity"
        for column in evidence_columns:
            if not _substantive_report_value(row[column]):
                return (
                    f"contains non-substantive `{header[column]}` evidence "
                    f"for `{row[0]}`"
                )
    return ""


def _log_ledger_issue(section_lines: list[str]) -> str:
    rows = _report_table_cells(
        section_lines,
        REPORT_TABLE_HEADERS["## Logs Examined"],
    )
    observed_layers = tuple(row[0] for row in rows)
    if observed_layers != CANONICAL_LOG_LAYERS:
        missing = [
            layer for layer in CANONICAL_LOG_LAYERS if layer not in observed_layers
        ]
        duplicates = sorted(
            {layer for layer in observed_layers if observed_layers.count(layer) > 1}
        )
        unknown = [
            layer for layer in observed_layers if layer not in CANONICAL_LOG_LAYERS
        ]
        if missing:
            return "is missing canonical layers: " + ", ".join(missing)
        if duplicates:
            return "duplicates canonical layers: " + ", ".join(duplicates)
        if unknown:
            return "contains unsupported layers: " + ", ".join(unknown)
        return "requires canonical log layers in the documented order"
    invalid_statuses = [row[4] for row in rows if row[4] not in LOG_COVERAGE_STATUSES]
    if invalid_statuses:
        return "contains unsupported coverage status values: " + ", ".join(
            sorted(set(invalid_statuses))
        )
    return ""


def _affirmative_pass_value(value: str) -> bool:
    if not value.startswith("PASS:"):
        return False
    detail = value.removeprefix("PASS:").strip()
    return _substantive_report_value(detail) and not INSUFFICIENT_PASS_DETAIL_RE.search(
        detail
    )


def _pass_evidence_issue(
    sections: dict[str, list[str]], completion_rows: dict[str, str]
) -> str:
    if completion_rows["Design"] == "PASS":
        for prefix in REQUIRED_REPORT_FIELDS["## Architecture Verdict"]:
            value = _prefixed_report_value(sections["## Architecture Verdict"], prefix)
            if value is None or not _affirmative_pass_value(value):
                return (
                    "Design PASS requires affirmative `PASS:` state for every "
                    "Architecture Verdict field"
                )

    component_rows = _report_table_cells(
        sections["## Component Verification Matrix"],
        REPORT_TABLE_HEADERS["## Component Verification Matrix"],
    )
    for criterion, columns in COMPONENT_PASS_COLUMNS.items():
        if completion_rows[criterion] != "PASS":
            continue
        for row in component_rows:
            evidence_values = [row[column] for column in columns] + [row[7]]
            if any(not _affirmative_pass_value(value) for value in evidence_values):
                return (
                    f"{criterion} PASS requires affirmative `PASS:` state in "
                    "every relevant component-matrix cell and its evidence"
                )

    if completion_rows["Logs"] == "PASS":
        log_rows = _report_table_cells(
            sections["## Logs Examined"],
            REPORT_TABLE_HEADERS["## Logs Examined"],
        )
        if any(
            row[4].casefold() not in {"examined", "not applicable"}
            or INSUFFICIENT_PASS_DETAIL_RE.search(" ".join(row[1:3]))
            or INSUFFICIENT_PASS_FINDING_RE.search(row[3])
            for row in log_rows
        ):
            return (
                "Logs PASS requires affirmative source and finding detail plus "
                "`examined` or `not applicable` coverage for every log-ledger row"
            )

    if completion_rows["Relevant code paths"] == "PASS":
        for heading in ("## Code Debugging", "## Post-Fix Validation"):
            for prefix in REQUIRED_REPORT_FIELDS[heading]:
                value = _prefixed_report_value(sections[heading], prefix)
                if value is None or not _affirmative_pass_value(value):
                    return (
                        "Relevant code paths PASS requires affirmative `PASS:` "
                        "state for every Code Debugging and Post-Fix Validation "
                        "field"
                    )
    return ""


def _canonical_report_sections(
    message: object,
) -> tuple[dict[str, list[str]] | None, str]:
    if not isinstance(message, str):
        return None, "response is not text"
    if _contains_sensitive_report_value(message):
        return None, "report contains a sensitive value that must be redacted"
    lines = [line.strip() for line in message.splitlines()]
    if lines.count("# Troubleshooting Report") != 1:
        return None, "report requires exactly one `# Troubleshooting Report` title"
    title_position = lines.index("# Troubleshooting Report")
    actual_headings = [
        line for line in lines[title_position + 1 :] if line.startswith("## ")
    ]
    if actual_headings != list(REPORT_HEADINGS):
        for heading in REPORT_HEADINGS:
            if heading not in actual_headings:
                return None, f"missing required heading `{heading}`"
        return None, "report headings are duplicated, reordered, or unsupported"

    positions = [
        lines.index(heading, title_position + 1) for heading in REPORT_HEADINGS
    ]
    positions.append(len(lines))
    sections: dict[str, list[str]] = {}
    for index, position in enumerate(positions[:-1]):
        section_lines = [
            line for line in lines[position + 1 : positions[index + 1]] if line
        ]
        heading = REPORT_HEADINGS[index]
        if not _substantive_report_value(" ".join(section_lines)):
            return None, f"section `{heading}` is not substantive"
        sections[heading] = section_lines

    classification_pattern = re.compile(
        r"^-\s*Classification:\s*(?P<classification>[A-Z_]+)\.?$"
    )
    classifications = [
        match.group("classification")
        for line in sections["## Outcome"]
        if (match := classification_pattern.fullmatch(line))
    ]
    if len(classifications) != 1 or classifications[0] not in GENERAL_REPORT_OUTCOMES:
        return None, "Outcome requires one supported `- Classification:` line"
    workflow_states = [
        line
        for line in sections["## Outcome"]
        if line.casefold().startswith("- current workflow state:")
    ]
    if workflow_states != ["- Current workflow state: REPORTED"]:
        return None, "Outcome requires `- Current workflow state: REPORTED`"
    for heading, prefixes in REQUIRED_REPORT_FIELDS.items():
        for prefix in prefixes:
            if _prefixed_report_value(sections[heading], prefix) is None:
                return None, f"{heading} requires one substantive `{prefix}` line"
    for heading, header in REPORT_TABLE_HEADERS.items():
        table_issue = _report_table_issue(sections[heading], header)
        if table_issue:
            return None, f"{heading} {table_issue}"
    evidence_columns = {
        "## Component Verification Matrix": tuple(range(1, 8)),
        "## Logs Examined": (1, 2, 3),
    }
    for heading, columns in evidence_columns.items():
        evidence_issue = _table_evidence_issue(
            sections[heading], REPORT_TABLE_HEADERS[heading], columns
        )
        if evidence_issue:
            return None, f"{heading} {evidence_issue}"
    log_ledger_issue = _log_ledger_issue(sections["## Logs Examined"])
    if log_ledger_issue:
        return None, f"## Logs Examined {log_ledger_issue}"
    completion_rows, completion_issue = _completion_rows(
        sections["## Completion Gate"], classifications[0]
    )
    if completion_issue:
        return None, completion_issue
    assert completion_rows is not None
    pass_evidence_issue = _pass_evidence_issue(sections, completion_rows)
    if pass_evidence_issue:
        return None, pass_evidence_issue
    return sections, ""


def _general_report_complete(message: object) -> tuple[bool, str]:
    sections, issue = _canonical_report_sections(message)
    return sections is not None, issue


def _general_report_correction(report_issue: str) -> str:
    headings = "\n".join(REPORT_HEADINGS)
    return "\n".join(
        [
            "The explicit $troubleshoot invocation must end with a complete "
            "structured troubleshooting report before this turn can stop.",
            f"Current report issue: {_public_reason(report_issue)}.",
            "Do not call tools or continue troubleshooting. Return one "
            "evidence-backed report using this exact title and heading order:",
            "# Troubleshooting Report",
            headings,
            "Under `## Outcome`, include exactly one supported "
            "`- Classification:` value and `- Current workflow state: REPORTED`.",
            "Supported classifications: " + ", ".join(GENERAL_REPORT_OUTCOMES) + ".",
            "Use every canonical labeled field and table documented by the "
            "troubleshoot report contract; each required table needs its exact "
            "header, separator, and at least one substantive data row.",
            "The Logs Examined table needs each canonical layer exactly once, "
            "in order, with one lower-case canonical coverage status.",
            "Under `## Completion Gate`, include exactly one table row for "
            + ", ".join(COMPLETION_CRITERIA)
            + "; use only PASS, FAIL, or UNKNOWN with substantive evidence and "
            "a gap or next action. VERIFIED_FIXED requires every row to PASS.",
            "Use honest unavailable or unverified statements when evidence is "
            "missing; do not use placeholders or fabricate results.",
        ]
    )


def _fallback_log_rows() -> list[str]:
    return [
        f"| {layer} | source not reported | window not reported | "
        "finding unavailable | unavailable |"
        for layer in CANONICAL_LOG_LAYERS
    ]


def _general_fallback_report(report_issue: str) -> str:
    issue = _bounded_report_value(
        _public_reason(report_issue),
        "The assistant response omitted required report content.",
        limit=120,
    )
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            "- Classification: UNRESOLVED",
            "- Current workflow state: REPORTED",
            "- Confidence: unknown because the assistant report was incomplete.",
            "- Current impact: the requested troubleshooting outcome was not fully reported.",
            "- Stabilization status: no stabilization state is inferred by this fallback.",
            "## Failure Contract",
            "- Expected: the explicit troubleshoot turn ends with the documented report.",
            "- Actual: Stop observed an incomplete assistant response.",
            f"- Scope and signature: terminal report validation failed because {issue}",
            "- Reproduction or characterization: Stop rejected the incomplete response.",
            "- Success criteria and constraints: return the full canonical report safely.",
            "- Target, environment, blast radius, and allowed mutations: report-only fallback; no target mutation.",
            "- Included system boundary: the explicit troubleshoot report obligation and Stop validator.",
            "- Excluded system boundary: the unreported target system and installed runtime state.",
            "- Exercised control and data paths: assistant response through canonical Stop validation.",
            "- Incident-window start: unavailable because the assistant response omitted it.",
            "- Incident-window end: Stop evaluation after the incomplete assistant response.",
            "## Architecture Verdict",
            "- Observed technologies, versions, and deployment model: unavailable from the incomplete response.",
            "- Configuration authorities: the Stop hook owns only report validation.",
            "- Components, dependencies, ports, protocols, and authentication: not established by the response.",
            "- Control and data flows: only the prompt, response, and Stop validation flow is proven.",
            "- Official vendor architecture comparison and verdict: unavailable; architecture remains UNKNOWN.",
            "## Component Verification Matrix",
            "| Component | Version and existence | Active configuration | Runtime health | Dependencies, authentication, and DNS | Resources and time sync | Restart history and recent changes | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| target components | existence unproven | configuration unproven | health unproven | dependency, authentication, and DNS evidence unproven | pressure and clocks unproven | restart and change history unproven | response omitted evidence |",
            "## Incident Timeline",
            "| Time | Source and clock basis | Correlation identifier | Event | Evidence or inference |",
            "| --- | --- | --- | --- | --- |",
            "| Stop evaluation | local hook sequence only | report obligation | incomplete report rejected | direct hook evidence |",
            "## Logs Examined",
            "| Layer | Source | Window and filters | Finding | Coverage status |",
            "| --- | --- | --- | --- | --- |",
            *_fallback_log_rows(),
            "## Hypotheses And Experiments",
            "| Hypothesis | Prediction and falsifier | Bounded experiment | Observation | Decision |",
            "| --- | --- | --- | --- | --- |",
            "| report completeness failed | canonical validation should reject missing evidence | one bounded Stop validation | response was rejected | supported for report failure only |",
            "## Code Debugging",
            "- Reproduction and execution or data path: target code path was not reported.",
            "- Stack trace, core dump, or equivalent runtime evidence: no artifact was reported.",
            "- Configuration, environment, and data inputs: target inputs were not reported.",
            "- Recent changes and affected or unaffected comparison: no comparison was reported.",
            "- Focused tests, static or dynamic analysis, and instrumentation: no result was reported.",
            "- Instrumentation cleanup and limitations: instrumentation state is unknown.",
            "## Root Cause",
            "- Earliest divergence: required terminal report content was missing.",
            "- Causal chain: incomplete response caused canonical validation to fail.",
            "- Counterfactual and reintroduction: a complete report would satisfy this reporting gate.",
            "- Alternatives eliminated: no target-system alternative was eliminated.",
            "- Confidence: high for report incompleteness; unknown for the target cause.",
            "## Remediation",
            "- Design classification and handoff: no target design conclusion is inferred.",
            "- Changes made: the hook emitted this report; no target change was made.",
            "- Authority and safety basis: Stop may close one failed report obligation.",
            "- Rollback or recovery state: no target rollback state is inferred.",
            "## Post-Fix Validation",
            "- Original reproducer: not represented as verified by this fallback.",
            "- Regression oracle: not represented as passed by this fallback.",
            "- Targeted and boundary checks: only canonical report validation ran.",
            "- Repeated or dynamic diagnostics: no repeated target diagnostic is proven.",
            "- Live trial status and claim scope: no live target claim is made.",
            "- Candidate, target, checkpoint, and replay range: target lineage was not reported.",
            "- Intervention ledger and first contaminated boundary: intervention state is unknown.",
            "- Product-owned transitions and independent postconditions: not proven by this fallback.",
            "## Completion Gate",
            "| Criterion | Verdict | Evidence | Gap or next action |",
            "| --- | --- | --- | --- |",
            *[
                f"| {criterion} | UNKNOWN | The assistant report omitted decisive evidence. | "
                "Resume with the missing scoped evidence before claiming completion. |"
                for criterion in COMPLETION_CRITERIA
            ],
            "## Remaining Unknowns And Residual Risks",
            "- Unknowns and coverage gaps: all target-system criteria lack decisive evidence.",
            "- Residual risks: the target condition and any stabilization remain unknown.",
            "- Exact next action: resume with the missing scoped evidence before any completion claim.",
        ]
    )


def _report_complete(message: object, state: GuardState) -> tuple[bool, str]:
    if not isinstance(message, str) or REPORT_MARKER not in message:
        return False, f"missing `{REPORT_MARKER}`"
    sections, issue = _canonical_report_sections(message)
    if sections is None:
        return False, issue
    markers = [line for line in sections["## Outcome"] if line == f"- {REPORT_MARKER}"]
    if markers != [f"- {REPORT_MARKER}"]:
        return False, f"Outcome requires exactly one `- {REPORT_MARKER}` line"
    classification_pattern = re.compile(
        r"^-\s*Classification:\s*(?P<classification>[A-Z_]+)\.?$"
    )
    classification = next(
        match.group("classification")
        for line in sections["## Outcome"]
        if (match := classification_pattern.fullmatch(line))
    )
    if classification not in (
        "UNRESOLVED",
        "BLOCKED_MISSING_EVIDENCE",
        "DIAGNOSED_NOT_FIXED",
    ):
        return False, "Outcome lacks a supported unresolved classification"
    stop_trigger_lines = [
        line.removeprefix("- Stop trigger:").strip()
        for line in sections["## Outcome"]
        if line.startswith("- Stop trigger:")
    ]
    if stop_trigger_lines != [state.stop_trigger]:
        return False, f"Outcome requires exact stop trigger `{state.stop_trigger}`"

    data = state.data or {}
    blocker = _prefixed_report_value(sections["## Root Cause"], "- Blocker:")
    expected_blocker = _bounded_report_value(
        data.get("blocker_summary"), "The recorded blocker remains unresolved."
    )
    if blocker is None:
        return False, "Root Cause requires one substantive `- Blocker:` line"
    if blocker != expected_blocker:
        return (
            False,
            "Root Cause `- Blocker:` line must exactly match the bounded "
            "marker-derived value",
        )
    blocker_key = _prefixed_report_value(sections["## Root Cause"], "- Blocker key:")
    expected_blocker_key = _bounded_report_value(
        data.get("blocker_key"), "The recorded blocker source is unavailable."
    )
    if blocker_key is None:
        return False, "Root Cause requires one substantive `- Blocker key:` line"
    if blocker_key != expected_blocker_key:
        return (
            False,
            "Root Cause `- Blocker key:` line must exactly match the bounded "
            "marker-derived value",
        )

    attempts = data.get("attempts")
    if isinstance(attempts, list):
        legacy_evidence = False
        for index, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, dict):
                return False, f"{_attempt_label(index)} is not a valid attempt object"
            attempt_label = _attempt_label(index)
            attempt_fields = _attempt_report_fields(
                sections["## Remediation"], attempt_label
            )
            if attempt_fields is None:
                return (
                    False,
                    f"Remediation requires substantive Remediation, Verification, "
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
                sections["## Post-Fix Validation"], attempt_label
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
                    f"Post-Fix Validation requires evidence for {attempt_label}",
                )
            if new_evidence is None:
                legacy_evidence = True
        if legacy_evidence and not any(
            LEGACY_EVIDENCE_NOTE in line for line in sections["## Post-Fix Validation"]
        ):
            return False, "Post-Fix Validation omits the historical marker limitation"
    return True, ""


def _contains_sensitive_report_value(text: str) -> bool:
    if (
        SENSITIVE_REPORT_RE.search(text)
        or PRIVATE_HOST_RE.search(text)
        or CLOUD_ACCESS_KEY_RE.search(text)
    ):
        return True
    for match in HOST_FIELD_RE.finditer(text):
        value = match.group("value").strip("[](),.;")
        if value.casefold().startswith(("http://", "https://")):
            continue
        hostname = value.rsplit(":", 1)[0].strip("[]")
        if PRIVATE_HOST_RE.search(hostname) or "." not in hostname:
            return True
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private or address.is_loopback or address.is_link_local
        ):
            return True
    for candidate in URL_CANDIDATE_RE.findall(text):
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            if parsed.username or parsed.password or not hostname:
                return True
            if PRIVATE_HOST_RE.search(hostname) or "." not in hostname:
                return True
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                address = None
            if address is not None and (
                address.is_private or address.is_loopback or address.is_link_local
            ):
                return True
        except ValueError:
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


def _bounded_report_value(
    value: object, fallback: str, limit: int = MAX_REPORT_FIELD_CHARS
) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text or _contains_sensitive_report_value(text):
        text = fallback
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _bounded_attempt_report_value(
    value: object, fallback: str, limit: int = MAX_REPORT_FIELD_CHARS
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
    issue = _bounded_report_value(
        _public_reason(report_issue),
        "The assistant response omitted required report content.",
        limit=120,
    )
    return "\n".join(
        [
            "# Troubleshooting Report",
            "## Outcome",
            "- Classification: UNRESOLVED",
            "- Current workflow state: REPORTED",
            f"- {REPORT_MARKER}",
            f"- Stop trigger: {stop_trigger}",
            "- Confidence: the recorded blocker remains unresolved after the bounded tranche.",
            "- Current impact: no further remediation is authorized in this tranche.",
            "- Stabilization status: no additional state change is authorized.",
            "## Failure Contract",
            "- Expected: the recorded blocker is removed and its verification oracle passes.",
            "- Actual: the marker-derived blocker persisted through the recorded attempts.",
            "- Scope and signature: bounded to the stable blocker and attempt ledger.",
            "- Reproduction or characterization: each recorded verification retained the blocker.",
            "- Success criteria and constraints: clear the blocker within the authorized budget.",
            "- Target, environment, blast radius, and allowed mutations: frozen at exhaustion.",
            "- Included system boundary: the stable blocker and recorded remediation ledger.",
            "- Excluded system boundary: unrecorded target components and unexercised paths.",
            "- Exercised control and data paths: marker-recorded attempts and verification results only.",
            "- Incident-window start: the bounded tranche start recorded by the marker.",
            "- Incident-window end: the marker exhaustion event for this report.",
            "## Architecture Verdict",
            "- Observed technologies, versions, and deployment model: unavailable at exhaustion.",
            "- Configuration authorities: decisive authority remains unproven.",
            "- Components, dependencies, ports, protocols, and authentication: coverage incomplete.",
            "- Control and data flows: the failing flow remains unresolved.",
            "- Official vendor architecture comparison and verdict: incomplete and UNKNOWN.",
            "## Component Verification Matrix",
            "| Component | Version and existence | Active configuration | Runtime health | Dependencies, authentication, and DNS | Resources and time sync | Restart history and recent changes | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| unresolved component | version unproven | config unproven | health unproven | dependency, authentication, and DNS evidence unproven | pressure and clocks unproven | restart and change history unproven | marker evidence only |",
            "## Incident Timeline",
            "| Time | Source and clock basis | Correlation identifier | Event | Evidence or inference |",
            "| --- | --- | --- | --- | --- |",
            "| bounded tranche | clock basis unavailable | blocker key | attempts remained unsuccessful | marker-derived order only |",
            "## Logs Examined",
            "| Layer | Source | Window and filters | Finding | Coverage status |",
            "| --- | --- | --- | --- | --- |",
            *_fallback_log_rows(),
            "## Hypotheses And Experiments",
            "| Hypothesis | Prediction and falsifier | Bounded experiment | Observation | Decision |",
            "| --- | --- | --- | --- | --- |",
            "| recorded attempt hypotheses | predictions unavailable | bounded verifications | blocker persisted | unresolved |",
            "## Code Debugging",
            "- Reproduction and execution or data path: relevant path evidence is incomplete.",
            "- Stack trace, core dump, or equivalent runtime evidence: artifact unavailable.",
            "- Configuration, environment, and data inputs: input coverage is incomplete.",
            "- Recent changes and affected or unaffected comparison: comparison unavailable.",
            "- Focused tests, static or dynamic analysis, and instrumentation: coverage incomplete.",
            "- Instrumentation cleanup and limitations: cleanup state cannot be inferred.",
            "## Root Cause",
            "- Blocker: "
            + _bounded_report_value(
                data.get("blocker_summary"), "The recorded blocker remains unresolved."
            ),
            "- Blocker key: "
            + _bounded_report_value(
                data.get("blocker_key"), "The recorded blocker source is unavailable."
            ),
            "- Earliest divergence: decisive divergence remains unproven.",
            "- Causal chain: persistence is proven, but the complete chain is not.",
            "- Counterfactual and reintroduction: decisive evidence is unavailable.",
            "- Alternatives eliminated: competing alternatives remain unresolved.",
            "- Confidence: unresolved after the bounded remediation attempts.",
            "## Remediation",
            *attempt_lines,
            "- Design classification and handoff: no additional handoff is inferred.",
            "- Changes made: only marker-recorded attempts are represented.",
            "- Authority and safety basis: another attempt needs a new instruction.",
            "- Rollback or recovery state: unavailable to this fallback.",
            "## Post-Fix Validation",
            *evidence_lines,
            "- Original reproducer: not represented as passing by this fallback.",
            "- Regression oracle: the recorded blocker verification remained unsuccessful.",
            "- Targeted and boundary checks: boundary evidence is incomplete.",
            "- Repeated or dynamic diagnostics: only marker evidence is represented.",
            "- Live trial status and claim scope: no verified live-fix claim is made.",
            "- Candidate, target, checkpoint, and replay range: lineage is unavailable.",
            "- Intervention ledger and first contaminated boundary: state is unknown.",
            "- Product-owned transitions and independent postconditions: not proven at exhaustion.",
            "## Completion Gate",
            "| Criterion | Verdict | Evidence | Gap or next action |",
            "| --- | --- | --- | --- |",
            *[
                f"| {criterion} | UNKNOWN | The exhausted marker does not prove this criterion. | "
                "Acquire criterion-specific evidence in a new authorized tranche. |"
                for criterion in COMPLETION_CRITERIA
            ],
            "## Remaining Unknowns And Residual Risks",
            f"- Unknowns and coverage gaps: report validation failed because {issue}",
            "- Residual risks: the recorded blocker and unverified criteria remain unresolved.",
            "- Exact next action: provide a new explicit instruction before another bounded tranche.",
        ]
    )


def _parse_troubleshoot_invocation(prompt: object) -> TroubleshootInvocation | None:
    if not isinstance(prompt, str):
        return None
    stripped = prompt.lstrip()
    skill_name = "$troubleshoot"
    if not stripped.startswith(skill_name):
        return None
    if len(stripped) > len(skill_name) and not stripped[len(skill_name)].isspace():
        return None

    supplied: dict[str, int] = {}
    remainder = stripped[len(skill_name) :]
    for match in re.finditer(r"\S+", remainder):
        token = match.group(0)
        known_prefix = token.startswith("--attempt-limit") or token.startswith(
            "--time-limit-minutes"
        )
        if not known_prefix:
            break
        if len(token) > 64:
            raise GuardStateError("budget flag is too long")
        parsed = re.fullmatch(
            r"--(?P<name>attempt-limit|time-limit-minutes)=(?P<value>[0-9]+)",
            token,
        )
        if parsed is None:
            raise GuardStateError(
                "budget flags must use --attempt-limit=N or --time-limit-minutes=N"
            )
        name = parsed.group("name")
        if name in supplied:
            raise GuardStateError(f"duplicate --{name} budget flag")
        value = int(parsed.group("value"))
        if name == "attempt-limit":
            _attempt_limit(value)
        else:
            _time_limit(value)
        supplied[name] = value
    return TroubleshootInvocation(
        attempt_limit=supplied.get("attempt-limit"),
        time_limit_minutes=supplied.get("time-limit-minutes"),
        supplied_fields=frozenset(supplied),
    )


def _turn_hash(payload: dict[str, Any]) -> str:
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 256:
        raise GuardStateError("UserPromptSubmit turn_id is required for authorization")
    return hashlib.sha256(turn_id.encode("utf-8")).hexdigest()


def _begin_report_obligation(
    payload: dict[str, Any], obligation: ReportObligationState
) -> ReportObligationState:
    if obligation.kind == "invalid":
        raise GuardStateError(
            "report obligation is invalid"
            + (f": {obligation.reason}" if obligation.reason else "")
        )
    if _report_obligation_active(obligation):
        return obligation
    bindings = _payload_binding_hashes(payload)
    if bindings is None:
        raise GuardStateError("session and workspace bindings are required")
    return _write_report_obligation_data(
        payload,
        {
            "schema": REPORT_OBLIGATION_SCHEMA,
            "workspace_hash": bindings[0],
            "session_hash": bindings[1],
            "turn_hash": _turn_hash(payload),
            "status": "active",
            "corrections": 0,
        },
    )


def _update_report_obligation(
    payload: dict[str, Any],
    obligation: ReportObligationState,
    *,
    status: str | None = None,
    corrections: int | None = None,
) -> ReportObligationState:
    if obligation.kind != "valid" or not isinstance(obligation.data, dict):
        raise GuardStateError("a valid report obligation is required")
    data = json.loads(json.dumps(obligation.data))
    if status is not None:
        data["status"] = status
    if corrections is not None:
        data["corrections"] = corrections
    return _write_report_obligation_data(payload, data)


def _report_turn_matches(
    payload: dict[str, Any], obligation: ReportObligationState
) -> bool:
    if not _report_obligation_active(obligation):
        return True
    try:
        current_turn_hash = _turn_hash(payload)
    except GuardStateError:
        return False
    return current_turn_hash == (obligation.data or {}).get("turn_hash")


def _authorization_base_data(
    payload: dict[str, Any], authorization: AuthorizationState
) -> dict[str, Any]:
    if authorization.kind == "valid" and authorization.data is not None:
        return json.loads(json.dumps(authorization.data))
    if authorization.kind == "invalid":
        raise GuardStateError(
            "authorization sidecar is invalid"
            + (f": {authorization.reason}" if authorization.reason else "")
        )
    bindings = _payload_binding_hashes(payload)
    if bindings is None:
        raise GuardStateError("session and workspace bindings are required")
    return {
        "schema": AUTHORIZATION_SCHEMA,
        "workspace_hash": bindings[0],
        "session_hash": bindings[1],
        "current": None,
        "pending": None,
        "terminal": None,
    }


def _profile_from_authorization(
    authorization: AuthorizationState,
) -> tuple[int, int, str | None]:
    if authorization.kind == "valid":
        current = (authorization.data or {}).get("current")
        if isinstance(current, dict):
            return (
                int(current["attempt_limit"]),
                int(current["time_limit_minutes"]),
                str(current["authorization_id"]),
            )
    return DEFAULT_ATTEMPT_LIMIT, DEFAULT_TIME_LIMIT_MINUTES, None


def _new_authorization_record(
    payload: dict[str, Any], attempt_limit: int, time_limit_minutes: int
) -> dict[str, Any]:
    return {
        "authorization_id": secrets.token_hex(16),
        "attempt_limit": _attempt_limit(attempt_limit),
        "time_limit_minutes": _time_limit(time_limit_minutes),
        "issued_turn_hash": _turn_hash(payload),
    }


def _save_current_profile(
    payload: dict[str, Any],
    authorization: AuthorizationState,
    attempt_limit: int,
    time_limit_minutes: int,
) -> AuthorizationState:
    data = _authorization_base_data(payload, authorization)
    data["current"] = _new_authorization_record(
        payload, attempt_limit, time_limit_minutes
    )
    data["pending"] = None
    data["terminal"] = None
    return _write_authorization_data(payload, data)


def _save_pending_profile(
    payload: dict[str, Any],
    authorization: AuthorizationState,
    state: GuardState,
    attempt_limit: int,
    time_limit_minutes: int,
    mode: str,
) -> AuthorizationState:
    if state.kind != "valid" or state.data is None:
        raise GuardStateError("a valid marker is required for a pending budget change")
    data = _authorization_base_data(payload, authorization)
    pending = _new_authorization_record(payload, attempt_limit, time_limit_minutes)
    pending.update(
        {
            "mode": mode,
            "expected_marker_digest": _marker_core_digest(state.data),
            "previous_blocker_key": state.data["blocker_key"],
            "previous_tranche": state.data["tranche"],
            "previous_started_at": state.data["started_at"],
        }
    )
    data["pending"] = pending
    return _write_authorization_data(payload, data)


def _pending_transition_issue(
    state: GuardState, authorization: AuthorizationState
) -> str | None:
    if authorization.kind != "valid":
        return "pending authorization requires a valid authorization sidecar"
    if state.kind == "missing":
        return "the remediation marker is missing from current.md"
    if state.kind == "invalid":
        return "the remediation marker is invalid" + (
            f": {state.reason}" if state.reason else ""
        )
    if state.kind != "valid" or state.data is None:
        return "pending authorization requires a valid remediation marker"
    pending = (authorization.data or {}).get("pending")
    if not isinstance(pending, dict):
        return None
    try:
        slot = _marker_authorization_slot(state.data, authorization)
    except GuardStateError as exc:
        return _public_reason(str(exc))
    if slot != "pending":
        return "pending authorization has not yet been applied to current.md"

    if pending["mode"] == "resize_active":
        if _marker_core_digest(state.data) != pending["expected_marker_digest"]:
            return (
                "active resize must preserve the blocker, tranche, ledger, "
                "counters, lifecycle, and timestamps"
            )
        return None

    if (
        state.data.get("status") != "active"
        or state.data.get("attempts") != []
        or state.data.get("active_seconds") != 0
        or state.data.get("stop_trigger") is not None
        or state.data.get("started_at") == pending.get("previous_started_at")
    ):
        return "a fresh tranche requires active empty state and fresh counters"
    same_blocker = state.data.get("blocker_key") == pending["previous_blocker_key"]
    if same_blocker:
        if (
            state.data.get("tranche") != pending["previous_tranche"] + 1
            or state.data.get("override_summary") is None
        ):
            return (
                "same-blocker continuation requires the next tranche and a "
                "continuation-only override_summary"
            )
    elif (
        state.data.get("tranche") != 1 or state.data.get("override_summary") is not None
    ):
        return (
            "a causally independent blocker requires tranche 1 and a null "
            "override_summary"
        )
    return None


def _promote_pending_authorization(
    payload: dict[str, Any], authorization: AuthorizationState
) -> AuthorizationState:
    data = _authorization_base_data(payload, authorization)
    pending = data.get("pending")
    if not isinstance(pending, dict):
        return authorization
    data["current"] = {
        field: pending[field]
        for field in (
            "authorization_id",
            "attempt_limit",
            "time_limit_minutes",
            "issued_turn_hash",
        )
    }
    data["pending"] = None
    data["terminal"] = None
    return _write_authorization_data(payload, data)


def _ensure_terminal_lock(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
) -> AuthorizationState:
    if state.kind != "valid" or state.data is None or not state.exhausted:
        return authorization
    if authorization.kind == "invalid":
        return authorization
    if authorization.kind == "valid" and isinstance(
        (authorization.data or {}).get("terminal"), dict
    ):
        return authorization
    data = _authorization_base_data(payload, authorization)
    data["terminal"] = {
        "marker_digest": _marker_core_digest(state.data),
        "blocker_key": state.data["blocker_key"],
        "tranche": state.data["tranche"],
    }
    return _write_authorization_data(payload, data)


def _prompt_context(message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": message,
        }
    }


def _profile_context(
    attempt_limit: int,
    time_limit_minutes: int,
    authorization_id: str | None,
) -> str:
    marker_id = "null" if authorization_id is None else authorization_id
    return (
        "Troubleshoot remediation profile for this session: "
        f"attempt_limit={attempt_limit}, "
        f"time_limit_minutes={time_limit_minutes}, "
        f"budget_authorization_id={marker_id}. New markers must use schema "
        f"{SCHEMA} and these exact values."
    )


def _pending_context(
    pending: dict[str, Any],
    issue: str | None = None,
    state_kind: str | None = None,
) -> str:
    issue_context = (
        f"Pending authorization cannot be promoted: {_public_reason(issue)}. "
        if issue
        else ""
    )
    if pending["mode"] == "resize_active":
        if issue and state_kind == "missing":
            action = (
                "Before another tool call, restore the exact pre-resize canonical "
                "marker in the advertised current.md and apply the authorized "
                "attempt_limit, time_limit_minutes, and budget_authorization_id. "
                "The authorization sidecar cannot reconstruct a deleted marker. "
                "If the exact prior marker is unavailable, end this session and "
                "request a fresh user-authorized troubleshoot session; do not reset "
                "or invent blocker state."
            )
        elif issue:
            action = (
                "Before another tool call, repair the exact advertised current.md "
                "marker and apply the authorized profile change atomically. Restore "
                "every non-profile field to its exact pre-resize value, then set only "
                "attempt_limit, time_limit_minutes, and budget_authorization_id to "
                "the authorized values; do not reset or invent blocker state."
            )
        else:
            action = (
                "Before another tool call, update only attempt_limit, "
                "time_limit_minutes, and budget_authorization_id in the exact "
                "advertised current.md marker; preserve its blocker, tranche, "
                "attempt ledger, counters, lifecycle, and timestamps."
            )
    else:
        action = (
            "Before continuing troubleshooting, replace the prior terminal marker "
            "with one complete canonical fresh active marker: set blocker_key and a "
            "concise public-safe blocker_summary; set attempts to [], active_seconds "
            "to 0, a fresh started_at, status to active, and stop_trigger to null. "
            "For the same blocker, preserve its exact blocker_key, increment tranche, "
            "and use a continuation-only override_summary. For a causally independent "
            "blocker, use its new blocker_key at tranche 1 with override_summary null."
        )
    return (
        f"{issue_context}{action} Use schema {SCHEMA}, "
        f"attempt_limit={pending['attempt_limit']}, "
        f"time_limit_minutes={pending['time_limit_minutes']}, and "
        f"budget_authorization_id={pending['authorization_id']}."
    )


def evaluate_user_prompt(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
    report_obligation: ReportObligationState,
) -> dict[str, Any]:
    try:
        invocation = _parse_troubleshoot_invocation(payload.get("prompt"))
    except GuardStateError as exc:
        return {"decision": "block", "reason": _public_reason(str(exc))}

    if invocation is not None:
        try:
            report_obligation = _begin_report_obligation(payload, report_obligation)
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}

    report_notice = ""
    if _report_obligation_active(report_obligation):
        if _report_turn_matches(payload, report_obligation):
            report_notice = (
                "Every explicit $troubleshoot invocation must end with the "
                "structured terminal report before Stop can complete."
            )
        else:
            report_notice = (
                "An earlier troubleshoot report remains undelivered after an "
                "interrupted turn. Report that state before starting new work."
            )
    elif report_obligation.kind == "invalid":
        report_notice = (
            "The private troubleshoot report obligation is invalid"
            + (f": {report_obligation.reason}" if report_obligation.reason else "")
            + "."
        )

    def prompt_context(message: str) -> dict[str, Any]:
        combined = "\n".join(part for part in (report_notice, message) if part)
        return _prompt_context(combined)

    pending = (
        (authorization.data or {}).get("pending")
        if authorization.kind == "valid"
        else None
    )
    if isinstance(pending, dict):
        return prompt_context(
            _pending_context(
                pending, _pending_transition_issue(state, authorization), state.kind
            )
        )

    if authorization.kind == "invalid":
        reason = "Troubleshoot budget authorization is invalid"
        if authorization.reason:
            reason += f": {authorization.reason}"
        if invocation is not None:
            return {"decision": "block", "reason": reason}
        return prompt_context(reason + ". Repair the private sidecar before use.")

    if state.kind == "invalid":
        reason = "Troubleshoot remediation marker requires exact repair"
        if state.reason:
            reason += f": {state.reason}"
        return prompt_context(
            reason + ". Repair the exact advertised current.md marker before applying "
            "any requested budget change."
        )

    current_attempts, current_minutes, current_id = _profile_from_authorization(
        authorization
    )
    requested_attempts = current_attempts
    requested_minutes = current_minutes
    supplied_fields: frozenset[str] = frozenset()
    if invocation is not None:
        supplied_fields = invocation.supplied_fields
        if invocation.attempt_limit is not None:
            requested_attempts = invocation.attempt_limit
        if invocation.time_limit_minutes is not None:
            requested_minutes = invocation.time_limit_minutes

    if (
        state.kind == "missing"
        and authorization.kind == "valid"
        and isinstance((authorization.data or {}).get("terminal"), dict)
    ):
        try:
            authorization = _save_current_profile(
                payload,
                authorization,
                requested_attempts,
                requested_minutes,
            )
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}
        current_attempts, current_minutes, current_id = _profile_from_authorization(
            authorization
        )
        return prompt_context(
            _profile_context(current_attempts, current_minutes, current_id)
        )

    if state.kind == "valid" and state.exhausted:
        try:
            updated = _save_pending_profile(
                payload,
                authorization,
                state,
                requested_attempts,
                requested_minutes,
                "next_tranche",
            )
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}
        return prompt_context(_pending_context((updated.data or {})["pending"]))

    if invocation is None:
        if authorization.kind == "valid":
            return prompt_context(
                _profile_context(current_attempts, current_minutes, current_id)
            )
        return {}

    if (
        state.kind == "valid"
        and state.data is not None
        and state.data["status"] == "active"
    ):
        if not supplied_fields:
            return prompt_context(
                _profile_context(current_attempts, current_minutes, current_id)
            )
        consumed_attempts = len(state.data["attempts"])
        consumed_seconds = state.data["active_seconds"]
        if requested_attempts <= consumed_attempts:
            return {
                "decision": "block",
                "reason": (
                    "attempt_limit must remain strictly greater than the "
                    f"{consumed_attempts} completed attempts"
                ),
            }
        if requested_minutes * 60 <= consumed_seconds:
            return {
                "decision": "block",
                "reason": (
                    "time_limit_minutes must remain strictly above the "
                    f"{consumed_seconds} consumed active seconds"
                ),
            }
        try:
            updated = _save_pending_profile(
                payload,
                authorization,
                state,
                requested_attempts,
                requested_minutes,
                "resize_active",
            )
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}
        return prompt_context(_pending_context((updated.data or {})["pending"]))

    if (
        state.kind == "valid"
        and state.data is not None
        and state.data["status"] == "resolved"
    ):
        try:
            updated = _save_pending_profile(
                payload,
                authorization,
                state,
                requested_attempts,
                requested_minutes,
                "next_tranche",
            )
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}
        return prompt_context(_pending_context((updated.data or {})["pending"]))

    if supplied_fields:
        try:
            updated = _save_current_profile(
                payload,
                authorization,
                requested_attempts,
                requested_minutes,
            )
        except GuardStateError as exc:
            return {"decision": "block", "reason": _public_reason(str(exc))}
        current = (updated.data or {})["current"]
        return prompt_context(
            _profile_context(
                current["attempt_limit"],
                current["time_limit_minutes"],
                current["authorization_id"],
            )
        )
    return prompt_context(
        _profile_context(current_attempts, current_minutes, current_id)
    )


def evaluate_pre_tool(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
    report_obligation: ReportObligationState,
) -> dict[str, Any]:
    report_denial: dict[str, Any] | None = None
    if _report_obligation_active(report_obligation):
        corrections = int((report_obligation.data or {}).get("corrections", 0))
        if corrections > 0:
            report_denial = _deny(
                "The troubleshoot report correction is pending. Return the "
                "structured report without calling another tool."
            )
    terminal = (
        (authorization.data or {}).get("terminal")
        if authorization.kind == "valid"
        else None
    )
    pending = (
        (authorization.data or {}).get("pending")
        if authorization.kind == "valid"
        else None
    )
    if isinstance(pending, dict):
        if _patch_updates_only_state(payload, state.state_file):
            return {}
        pending_context = _pending_context(
            pending, _pending_transition_issue(state, authorization), state.kind
        )
        return _deny(
            "A user-authorized troubleshoot budget update is pending. "
            + pending_context
        )
    if state.kind == "missing" and isinstance(terminal, dict):
        if _patch_updates_only_state(payload, state.state_file):
            return {}
        return _deny(
            "The exhausted tranche terminal lock remains active while its "
            "current.md marker is missing. Restore only that exact marker or wait "
            "for a new user instruction before another tool call."
        )
    if state.kind == "missing":
        return report_denial or {}
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
        return report_denial or {}
    if _patch_updates_only_state(payload, state.state_file):
        return {}
    return _deny(
        "Remediation budget exhausted. Stop all tool use and return the complete "
        f"troubleshooting report with `{REPORT_MARKER}`. A new user instruction is "
        "required before another bounded tranche."
    )


def _close_report_obligation(
    payload: dict[str, Any],
    report_obligation: ReportObligationState,
    status: str,
) -> None:
    if not _report_obligation_active(report_obligation):
        return
    _update_report_obligation(payload, report_obligation, status=status)


def _evaluate_general_report_stop(
    payload: dict[str, Any], report_obligation: ReportObligationState
) -> dict[str, Any]:
    if report_obligation.kind == "invalid":
        report_issue = "private report obligation is invalid" + (
            f": {report_obligation.reason}" if report_obligation.reason else ""
        )
        if payload.get("stop_hook_active"):
            return _stop(
                "Troubleshoot reporting state remained invalid after one correction request.",
                warning=_general_fallback_report(report_issue),
            )
        return {
            "decision": "block",
            "reason": _general_report_correction(report_issue),
        }
    if not _report_obligation_active(report_obligation):
        return {"continue": True}

    report_complete, report_issue = _general_report_complete(
        payload.get("last_assistant_message")
    )
    if report_complete:
        if not payload.get(REPORT_FINALIZE_FIELD):
            return {"continue": True, REPORT_READY_FIELD: True}
        try:
            _close_report_obligation(payload, report_obligation, "delivered")
        except GuardStateError as exc:
            return {
                "decision": "block",
                "reason": _general_report_correction(
                    "the validated report could not be recorded: "
                    + _public_reason(str(exc))
                ),
            }
        return {"continue": True}

    corrections = int((report_obligation.data or {}).get("corrections", 0))
    if corrections < MAX_REPORT_CORRECTIONS:
        try:
            _update_report_obligation(
                payload,
                report_obligation,
                corrections=corrections + 1,
            )
        except GuardStateError as exc:
            report_issue = (
                "report correction state could not be recorded: "
                + _public_reason(str(exc))
            )
        return {
            "decision": "block",
            "reason": _general_report_correction(report_issue),
        }

    fallback = _general_fallback_report(report_issue)
    try:
        _close_report_obligation(payload, report_obligation, "fallback")
    except GuardStateError:
        fallback += (
            "\n- Report-state warning: the fallback status could not be recorded "
            "safely."
        )
    return _stop(
        "A bounded troubleshoot UI fallback report was emitted after the "
        "assistant report remained incomplete.",
        warning=fallback,
    )


def evaluate_stop(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
    report_obligation: ReportObligationState,
) -> dict[str, Any]:
    terminal = (
        (authorization.data or {}).get("terminal")
        if authorization.kind == "valid"
        else None
    )
    pending = (
        (authorization.data or {}).get("pending")
        if authorization.kind == "valid"
        else None
    )
    if isinstance(pending, dict):
        pending_context = _pending_context(
            pending, _pending_transition_issue(state, authorization), state.kind
        )
        if payload.get("stop_hook_active"):
            return _stop(
                "Troubleshoot budget update remains pending after one marker request.",
                warning=pending_context,
            )
        return {
            "decision": "block",
            "reason": pending_context,
        }
    if state.kind == "missing" and isinstance(terminal, dict):
        message = (
            "The exhausted tranche terminal lock remains active while its "
            "current.md marker is missing. Restore only that exact marker or wait "
            "for a new user instruction."
        )
        if payload.get("stop_hook_active"):
            return _stop(
                "The exhausted marker remained missing after one restore request.",
                warning=message,
            )
        return {"decision": "block", "reason": message}
    if state.kind == "missing" or (state.kind == "valid" and not state.exhausted):
        return _evaluate_general_report_stop(payload, report_obligation)
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
        if not payload.get(REPORT_FINALIZE_FIELD):
            return {"continue": True, REPORT_READY_FIELD: True}
        try:
            _close_report_obligation(payload, report_obligation, "delivered")
        except GuardStateError as exc:
            return {
                "decision": "block",
                "reason": _general_report_correction(
                    "the validated exhausted report could not be recorded: "
                    + _public_reason(str(exc))
                ),
            }
        return _stop(
            "Remediation budget exhausted and troubleshooting report delivered."
        )
    if payload.get("stop_hook_active"):
        try:
            _close_report_obligation(payload, report_obligation, "fallback")
        except GuardStateError:
            pass
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
    event = payload.get("hook_event_name")
    authorization = load_authorization_state(payload)
    report_obligation = load_report_obligation_state(payload)
    state = load_guard_state(payload, authorization)
    if state.kind == "valid" and state.exhausted:
        authorization = _ensure_terminal_lock(payload, state, authorization)
        state = load_guard_state(payload, authorization)
    if event in {"PreToolUse", "Stop"} and authorization.kind == "valid":
        pending = (authorization.data or {}).get("pending")
        if (
            isinstance(pending, dict)
            and _pending_transition_issue(state, authorization) is None
        ):
            authorization = _promote_pending_authorization(payload, authorization)
            state = load_guard_state(payload, authorization)
    if event == "UserPromptSubmit":
        return evaluate_user_prompt(payload, state, authorization, report_obligation)
    if event == "PreToolUse":
        return evaluate_pre_tool(payload, state, authorization, report_obligation)
    if event == "Stop":
        return evaluate_stop(payload, state, authorization, report_obligation)
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
    except Exception:  # pragma: no cover - command-hook safety boundary
        event = payload.get("hook_event_name") if isinstance(payload, dict) else None
        if event == "PreToolUse":
            output = _deny("Remediation guard failed closed due to an internal error.")
        elif event == "UserPromptSubmit":
            output = {
                "decision": "block",
                "reason": (
                    "Remediation authorization failed closed due to an internal error."
                ),
            }
        else:
            output = _stop("Remediation guard failed closed due to an internal error.")
        print(json.dumps(output, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
