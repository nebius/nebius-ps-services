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


SCHEMA = "codex/remediation-budget-v4"
PREVIOUS_SCHEMA = "codex/remediation-budget-v3"
LEGACY_SCHEMA = "codex/remediation-budget-v1"
AUTHORIZATION_SCHEMA = "codex/remediation-budget-authorization-v1"
AUTHORIZATION_FILE_NAME = "remediation-budget-authorization.json"
MARKER_START = "<!-- codex-remediation-budget:v1\n"
MARKER_END = "\n-->"
MAX_MARKER_PREFIX_BYTES = 12 * 1024
MAX_TASK_STATE_BYTES = 1024 * 1024
MAX_AUTHORIZATION_BYTES = 4096
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
MAX_REPORT_FIELD_CHARS = 70
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


@dataclass(frozen=True)
class AuthorizationState:
    kind: str
    authorization_file: Path | None
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
    if blocker is None:
        return False, "Blocking Error requires one substantive `Blocker:` line"
    if blocker != expected_blocker:
        return (
            False,
            "Blocking Error `Blocker:` line must exactly match the bounded "
            "marker-derived value",
        )
    blocker_key = _prefixed_report_value(
        section_lines_by_heading["## Source"], "Blocker key:"
    )
    expected_blocker_key = _bounded_report_value(
        data.get("blocker_key"), "The recorded blocker source is unavailable."
    )
    if blocker_key is None:
        return False, "Source requires one substantive `Blocker key:` line"
    if blocker_key != expected_blocker_key:
        return (
            False,
            "Source `Blocker key:` line must exactly match the bounded "
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
    if authorization.kind != "valid" or state.kind != "valid" or state.data is None:
        return "pending authorization requires a valid marker and sidecar"
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


def _pending_context(pending: dict[str, Any]) -> str:
    if pending["mode"] == "resize_active":
        action = (
            "Before another tool call, update only attempt_limit, "
            "time_limit_minutes, and budget_authorization_id in the exact "
            "advertised current.md marker; preserve its blocker, tranche, "
            "attempt ledger, counters, lifecycle, and timestamps."
        )
    else:
        action = (
            "Before continuing troubleshooting, replace the exhausted marker with "
            "fresh active state: reset attempts and active_seconds, use a fresh "
            "started_at, and either increment the same blocker's tranche with a "
            "continuation-only override_summary or start a causally independent "
            "blocker at tranche 1 with override_summary null."
        )
    return (
        f"{action} Use schema {SCHEMA}, attempt_limit={pending['attempt_limit']}, "
        f"time_limit_minutes={pending['time_limit_minutes']}, and "
        f"budget_authorization_id={pending['authorization_id']}."
    )


def evaluate_user_prompt(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
) -> dict[str, Any]:
    try:
        invocation = _parse_troubleshoot_invocation(payload.get("prompt"))
    except GuardStateError as exc:
        return {"decision": "block", "reason": _public_reason(str(exc))}

    pending = (
        (authorization.data or {}).get("pending")
        if authorization.kind == "valid"
        else None
    )
    if isinstance(pending, dict):
        return _prompt_context(_pending_context(pending))

    if authorization.kind == "invalid":
        reason = "Troubleshoot budget authorization is invalid"
        if authorization.reason:
            reason += f": {authorization.reason}"
        if invocation is not None:
            return {"decision": "block", "reason": reason}
        return _prompt_context(reason + ". Repair the private sidecar before use.")

    if state.kind == "invalid":
        reason = "Troubleshoot remediation marker requires exact repair"
        if state.reason:
            reason += f": {state.reason}"
        return _prompt_context(
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
        return _prompt_context(
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
        return _prompt_context(_pending_context((updated.data or {})["pending"]))

    if invocation is None:
        if authorization.kind == "valid":
            return _prompt_context(
                _profile_context(current_attempts, current_minutes, current_id)
            )
        return {}

    if (
        state.kind == "valid"
        and state.data is not None
        and state.data["status"] == "active"
    ):
        if not supplied_fields:
            return _prompt_context(
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
        return _prompt_context(_pending_context((updated.data or {})["pending"]))

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
        return _prompt_context(_pending_context((updated.data or {})["pending"]))

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
        return _prompt_context(
            _profile_context(
                current["attempt_limit"],
                current["time_limit_minutes"],
                current["authorization_id"],
            )
        )
    return _prompt_context(
        _profile_context(current_attempts, current_minutes, current_id)
    )


def evaluate_pre_tool(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
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
        if _patch_updates_only_state(payload, state.state_file):
            return {}
        return _deny(
            "A user-authorized troubleshoot budget update is pending. "
            + _pending_context(pending)
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


def evaluate_stop(
    payload: dict[str, Any],
    state: GuardState,
    authorization: AuthorizationState,
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
        if payload.get("stop_hook_active"):
            return _stop(
                "Troubleshoot budget update remains pending after one marker request.",
                warning=_pending_context(pending),
            )
        return {
            "decision": "block",
            "reason": _pending_context(pending),
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
    event = payload.get("hook_event_name")
    authorization = load_authorization_state(payload)
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
        return evaluate_user_prompt(payload, state, authorization)
    if event == "PreToolUse":
        return evaluate_pre_tool(payload, state, authorization)
    if event == "Stop":
        return evaluate_stop(payload, state, authorization)
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
