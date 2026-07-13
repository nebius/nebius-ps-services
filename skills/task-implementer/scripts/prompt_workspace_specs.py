#!/usr/bin/env python3
"""Private steering ledger and committed specification document validation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
from pathlib import Path
import re
import stat
import subprocess

from prompt_workspace_core import (
    REVISION_RE,
    PromptWorkspaceError,
    iso_seconds,
    load_json_object,
    now_utc,
    require_mode,
    required_string,
    stable_json,
    write_atomic,
)


STEERING_SCHEMA = "task-implementer/steering-ledger-v1"
STEERING_DISPOSITIONS = {"pending", "applied", "blocked", "no_effect"}
REQUIREMENTS_SCHEMA = "task-implementer/requirements-v1"
DESIGN_SCHEMA = "task-implementer/design-v1"
MAX_SPEC_BYTES = 1024 * 1024
REQUIREMENT_ID_RE = re.compile(r"TI-REQ-((?!0+\Z)[0-9]{3,})\Z")
DESIGN_ID_RE = re.compile(r"TI-DES-((?!0+\Z)[0-9]{3,})\Z")
INTERNAL_STATE_PATTERNS = (
    re.compile(r"prompt-[0-9a-f]{32}"),
    re.compile(r"run-[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}"),
    re.compile(r"inputs/r[0-9]{4}/prompt\.md"),
    re.compile(r"(?:^|/)task-implementer/projects/"),
    re.compile(r"\br[0-9]{4}\b"),
    re.compile(r"\btask-[0-9]+\b"),
    re.compile(r"\bcheckpoint-[0-9]+\b"),
    re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])"),
)


def steering_path(run_dir: Path) -> Path:
    return run_dir / "steering.json"


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} has no UTC offset"
        )
    return iso_seconds(parsed)


def load_steering_ledger(
    run_dir: Path,
    revisions: list[dict[str, object]],
) -> dict[str, object]:
    """Load and validate optional mutable steering dispositions."""

    path = steering_path(run_dir)
    if not path.exists():
        return {"schema": STEERING_SCHEMA, "events": []}
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "steering ledger is unsafe")
    require_mode(path, 0o600, "steering ledger")
    value = load_json_object(path, "steering ledger")
    if set(value) != {"schema", "events"} or value.get("schema") != STEERING_SCHEMA:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering ledger schema is invalid"
        )
    events = value.get("events")
    if not isinstance(events, list):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering ledger events are invalid"
        )
    revision_index = {
        str(revision.get("revision")): revision for revision in revisions
    }
    seen: set[str] = set()
    previous_number = 0
    pending_seen = False
    validated: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict) or set(event) != {
            "revision",
            "sha256",
            "submitted_at",
            "disposition",
            "resolved_at",
        }:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger event is invalid"
            )
        revision_id = event.get("revision")
        match = REVISION_RE.fullmatch(str(revision_id or ""))
        if match is None or revision_id in seen:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger revision is invalid"
            )
        number = int(match.group(1))
        if number <= previous_number or revision_id not in revision_index:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger revisions are out of order"
            )
        manifest_revision = revision_index[str(revision_id)]
        if event.get("sha256") != manifest_revision.get("sha256"):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger digest is invalid"
            )
        disposition = event.get("disposition")
        if disposition not in STEERING_DISPOSITIONS:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering disposition is invalid"
            )
        submitted_at = _timestamp(event.get("submitted_at"), "steering submission")
        resolved_at = event.get("resolved_at")
        if disposition == "pending":
            pending_seen = True
            if resolved_at is not None:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "pending steering has a resolution time"
                )
        elif pending_seen:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "steering resolutions are not an ordered prefix",
            )
        elif resolved_at is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "resolved steering has no resolution time"
            )
        else:
            resolved_at = _timestamp(resolved_at, "steering resolution")
            if datetime.fromisoformat(str(resolved_at)) < datetime.fromisoformat(
                submitted_at
            ):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "steering resolved before submission"
                )
        validated.append(
            {
                "revision": revision_id,
                "sha256": event["sha256"],
                "submitted_at": submitted_at,
                "disposition": disposition,
                "resolved_at": resolved_at,
            }
        )
        seen.add(str(revision_id))
        previous_number = number
    return {"schema": STEERING_SCHEMA, "events": validated}


def record_steering_revision(
    run_dir: Path,
    revisions: list[dict[str, object]],
    revision_id: str,
    submitted_at: datetime,
) -> dict[str, object]:
    """Record one accepted immutable revision as pending steering."""

    ledger = load_steering_ledger(run_dir, revisions)
    events = list(ledger["events"])
    existing = [event for event in events if event["revision"] == revision_id]
    if existing:
        return existing[0]
    revision = next(
        (item for item in revisions if item.get("revision") == revision_id),
        None,
    )
    if revision is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revision is not in the manifest"
        )
    event: dict[str, object] = {
        "revision": revision_id,
        "sha256": revision["sha256"],
        "submitted_at": iso_seconds(submitted_at),
        "disposition": "pending",
        "resolved_at": None,
    }
    events.append(event)
    ledger["events"] = events
    write_atomic(steering_path(run_dir), stable_json(ledger))
    return event


def resolve_steering_revision(
    run_dir: Path,
    revisions: list[dict[str, object]],
    revision_id: str,
    disposition: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Resolve one pending steering event without changing immutable snapshots."""

    if disposition not in STEERING_DISPOSITIONS - {"pending"}:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering resolution disposition is invalid"
        )
    ledger = load_steering_ledger(run_dir, revisions)
    events = list(ledger["events"])
    matches = [event for event in events if event["revision"] == revision_id]
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revision has no unique ledger event"
        )
    event = matches[0]
    if event["disposition"] != "pending":
        if event["disposition"] != disposition:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering revision is already resolved"
            )
        return event
    oldest_pending = next(
        item for item in events if item["disposition"] == "pending"
    )
    if oldest_pending["revision"] != revision_id:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revisions must resolve in order"
        )
    resolved = clock()
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering resolution clock must be timezone-aware"
        )
    event["disposition"] = disposition
    event["resolved_at"] = iso_seconds(resolved)
    ledger["events"] = events
    write_atomic(steering_path(run_dir), stable_json(ledger))
    return event


def pending_steering_revisions(
    run_dir: Path,
    revisions: list[dict[str, object]],
) -> list[str]:
    ledger = load_steering_ledger(run_dir, revisions)
    return [
        str(event["revision"])
        for event in ledger["events"]
        if event["disposition"] == "pending"
    ]


def _spec_contract(kind: str) -> tuple[str, str, str, re.Pattern[str], str]:
    if kind == "requirements":
        return (
            REQUIREMENTS_SCHEMA,
            "requirements.md",
            "agentic-sdlc.requirements.v1",
            REQUIREMENT_ID_RE,
            "Requirements",
        )
    if kind == "design":
        return (
            DESIGN_SCHEMA,
            "design.md",
            "agentic-sdlc.design.v1",
            DESIGN_ID_RE,
            "Design",
        )
    raise AssertionError(kind)


def spec_markers(kind: str) -> tuple[str, str]:
    schema, _, _, _, _ = _spec_contract(kind)
    return (
        f"<!-- task-implementer:{kind}:start schema={schema} -->",
        f"<!-- task-implementer:{kind}:end -->",
    )


def spec_repo_path(workspace: dict[str, object], kind: str) -> tuple[Path, str]:
    _, filename, _, _, _ = _spec_contract(kind)
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    source_root = Path(
        required_string(workspace, "source_root", "workspace manifest")
    )
    path = source_root / "docs" / filename
    relative = path.relative_to(repo_root).as_posix()
    return path, relative


def _require_safe_spec_path(path: Path) -> None:
    docs = path.parent
    if docs.exists() and (docs.is_symlink() or not docs.is_dir()):
        raise PromptWorkspaceError("SPEC_CONFLICT", "specification docs path is unsafe")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "specification document path is unsafe"
            )
        if stat.S_IMODE(path.stat().st_mode) & 0o022:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "specification document is group or world writable"
            )


def _canonical_managed_text(value: str) -> bytes:
    normalized = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    return (normalized + "\n").encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _surrounding_digest(prefix: bytes, suffix: bytes) -> str:
    return _digest(prefix + b"\0" + suffix)


def _required_record_field(section: str, label: str, identifier: str) -> str:
    matches = re.findall(
        rf"(?m)^- {re.escape(label)}:\s*(\S.*?)\s*$",
        section,
    )
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{identifier} has no unique {label} field"
        )
    return matches[0]


def _require_bulleted_subsection(
    section: str, heading: str, identifier: str
) -> None:
    match = re.search(
        rf"(?ms)^#### {re.escape(heading)}\s*\n"
        r"(.*?)(?=^#### |^### |^## |\Z)",
        section,
    )
    if match is None or re.search(r"(?m)^-\s+\S", match.group(1)) is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{identifier} has no {heading} evidence"
        )


def _require_bulleted_heading(body: str, heading: str, relative: str) -> None:
    if body.count(f"## {heading}") != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has no unique {heading} section"
        )
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        body,
    )
    if match is None or re.search(r"(?m)^-\s+\S", match.group(1)) is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has no {heading} entries"
        )


def _append_envelope(raw: bytes) -> tuple[bytes, bytes]:
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    if raw.endswith(newline + newline):
        separator = b""
    elif raw.endswith(newline):
        separator = newline
    else:
        separator = newline + newline
    return raw + separator, newline


def _new_envelope(kind: str) -> tuple[bytes, bytes]:
    _, _, _, _, title = _spec_contract(kind)
    return f"# {title}\n\n".encode("utf-8"), b"\n"


def _read_spec_at_commit(
    workspace: dict[str, object], relative: str, commit: str
) -> bytes | None:
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{commit}:{relative}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git could not inspect specification evidence"
        ) from exc
    if result.returncode != 0:
        return None
    return result.stdout


def inspect_spec_document(
    workspace: dict[str, object],
    kind: str,
    *,
    commit: str | None = None,
) -> dict[str, object]:
    """Validate one managed document without exposing private workflow state."""

    schema, _, owner_schema, id_re, title = _spec_contract(kind)
    path, relative = spec_repo_path(workspace, kind)
    if commit is None:
        _require_safe_spec_path(path)
        if not path.exists():
            raw = None
        else:
            if path.stat().st_size > MAX_SPEC_BYTES:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", "specification document is too large"
                )
            raw = path.read_bytes()
    else:
        raw = _read_spec_at_commit(workspace, relative, commit)
    if raw is None:
        return {
            "kind": kind,
            "path": relative,
            "exists": False,
            "managed": False,
            "managed_sha256": None,
            "surrounding_sha256": "absent",
            "rendered_surrounding_sha256": _surrounding_digest(
                *_new_envelope(kind)
            ),
            "ids": [],
            "requirements": {},
            "statuses": {},
            "record_sha256": {},
        }
    if len(raw) > MAX_SPEC_BYTES or b"\x00" in raw:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is invalid or too large"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    if re.search(
        rf"(?m)^schema:\s*['\"]?{re.escape(owner_schema)}['\"]?\s*$",
        text,
    ):
        raise PromptWorkspaceError(
            "SPEC_OWNER_CONFLICT",
            f"{relative} is owned by Agentic SDLC",
        )
    start_marker, end_marker = spec_markers(kind)
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count == end_count == 0:
        return {
            "kind": kind,
            "path": relative,
            "exists": True,
            "managed": False,
            "managed_sha256": None,
            "surrounding_sha256": _digest(raw),
            "rendered_surrounding_sha256": _surrounding_digest(
                *_append_envelope(raw)
            ),
            "ids": [],
            "requirements": {},
            "statuses": {},
            "record_sha256": {},
        }
    if start_count != 1 or end_count != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has malformed managed markers"
        )
    start = text.index(start_marker)
    body_start = start + len(start_marker)
    end = text.index(end_marker)
    if end <= body_start:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has reversed managed markers"
        )
    prefix = text[:start]
    body = text[body_start:end]
    suffix = text[end + len(end_marker) :]
    private_paths = {
        str(workspace.get(key))
        for key in ("repo_root", "source_root", "prompt_root", "runs_root")
        if isinstance(workspace.get(key), str) and str(workspace.get(key))
    }
    if any(pattern.search(body) for pattern in INTERNAL_STATE_PATTERNS) or any(
        private_path in body for private_path in private_paths
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} exposes private workflow state"
        )
    id_prefix = "TI-REQ-" if kind == "requirements" else "TI-DES-"
    heading_pattern = re.compile(
        rf"(?m)^###\s+({re.escape(id_prefix)}[0-9]{{3,}}):\s+.+$"
    )
    ids = [match.group(1) for match in heading_pattern.finditer(body)]
    managed_id_headings = re.findall(
        r"(?m)^###\s+(TI-(?:REQ|DES)-[^:\s]+):\s+.+$",
        body,
    )
    if (
        ids != managed_id_headings
        or len(ids) != len(set(ids))
        or any(id_re.fullmatch(item) is None for item in ids)
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has duplicate or invalid managed IDs"
        )
    requirements: dict[str, list[str]] = {}
    statuses: dict[str, str] = {}
    record_sha256: dict[str, str] = {}
    if kind == "requirements":
        _require_bulleted_heading(
            body, "Task Implementer Open Questions", relative
        )
        _require_bulleted_heading(
            body, "Task Implementer Requirements Change Log", relative
        )
        for item in ids:
            section_match = re.search(
                rf"(?ms)^###\s+{re.escape(item)}:\s+.*?\n"
                r"(.*?)(?=^###\s+TI-REQ-|^## |\Z)",
                body,
            )
            if section_match is None:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement record"
                )
            section = section_match.group(1)
            status = _required_record_field(section, "Status", item)
            if status not in {"active", "satisfied", "superseded"}:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement status"
                )
            statuses[item] = status
            for label in ("Requirement", "Constraints", "Non-goals"):
                _required_record_field(section, label, item)
            _require_bulleted_subsection(section, "Acceptance criteria", item)
            _require_bulleted_subsection(section, "Verification", item)
            record_sha256[item] = _digest(
                _canonical_managed_text(section_match.group(0))
            )
    else:
        _require_bulleted_heading(
            body, "Task Implementer Design Change Log", relative
        )
        for item in ids:
            section_match = re.search(
                rf"(?ms)^###\s+{re.escape(item)}:\s+.*?\n"
                r"(.*?)(?=^###\s+TI-DES-|^## |\Z)",
                body,
            )
            if section_match is None:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid design record"
                )
            section = section_match.group(1)
            status = _required_record_field(section, "Status", item)
            if status not in {"planned", "implemented", "superseded"}:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid design status"
                )
            statuses[item] = status
            mapping = _required_record_field(section, "Requirements", item)
            if re.fullmatch(
                r"TI-REQ-[0-9]{3,}(?:\s*,\s*TI-REQ-[0-9]{3,})*",
                mapping,
            ) is None:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement mapping"
                )
            refs = re.findall(r"TI-REQ-[0-9]{3,}", mapping)
            if len(refs) != len(set(refs)):
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} repeats a requirement mapping"
                )
            requirements[item] = list(dict.fromkeys(refs))
            for label in (
                "Selected approach",
                "Boundaries and interfaces",
                "Validation",
                "Rollback",
            ):
                _required_record_field(section, label, item)
            _require_bulleted_subsection(section, "Alternatives considered", item)
            _require_bulleted_subsection(section, "Implementation evidence", item)
            record_sha256[item] = _digest(
                _canonical_managed_text(section_match.group(0))
            )
    surrounding_sha256 = _surrounding_digest(
        prefix.encode("utf-8"), suffix.encode("utf-8")
    )
    return {
        "kind": kind,
        "path": relative,
        "exists": True,
        "managed": True,
        "schema": schema,
        "title": title,
        "managed_sha256": _digest(_canonical_managed_text(body)),
        "surrounding_sha256": surrounding_sha256,
        "rendered_surrounding_sha256": surrounding_sha256,
        "ids": ids,
        "requirements": requirements,
        "statuses": statuses,
        "record_sha256": record_sha256,
    }


def inspect_spec_documents(
    workspace: dict[str, object], *, commit: str | None = None
) -> dict[str, object]:
    requirements = inspect_spec_document(workspace, "requirements", commit=commit)
    design = inspect_spec_document(workspace, "design", commit=commit)
    requirement_ids = set(map(str, requirements["ids"]))
    for design_id, refs in dict(design["requirements"]).items():
        unknown = set(map(str, refs)) - requirement_ids
        if unknown:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT",
                f"{design_id} maps unknown requirements: {', '.join(sorted(unknown))}",
            )
    next_requirement = max(
        (int(REQUIREMENT_ID_RE.fullmatch(item).group(1)) for item in requirement_ids),
        default=0,
    ) + 1
    design_ids = set(map(str, design["ids"]))
    next_design = max(
        (int(DESIGN_ID_RE.fullmatch(item).group(1)) for item in design_ids),
        default=0,
    ) + 1
    return {
        "requirements": requirements,
        "design": design,
        "next_requirement_id": f"TI-REQ-{next_requirement:03d}",
        "next_design_id": f"TI-DES-{next_design:03d}",
    }


def new_spec_document(kind: str, managed_body: str) -> bytes:
    """Render a missing specification document with its canonical envelope."""

    _, _, _, _, title = _spec_contract(kind)
    start, end = spec_markers(kind)
    body = managed_body.strip()
    return f"# {title}\n\n{start}\n{body}\n{end}\n".encode("utf-8")


def append_managed_region(raw: bytes, kind: str, managed_body: str) -> bytes:
    """Append one managed region to a generic document without changing its bytes."""

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    start, end = spec_markers(kind)
    if start.encode() in raw or end.encode() in raw:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document already has managed markers"
        )
    prefix, newline = _append_envelope(raw)
    body = managed_body.strip().replace("\r\n", "\n").encode("utf-8")
    if newline == b"\r\n":
        body = body.replace(b"\n", b"\r\n")
    return (
        prefix
        + start.encode("utf-8")
        + newline
        + body
        + newline
        + end.encode("utf-8")
        + newline
    )


def replace_managed_region(raw: bytes, kind: str, managed_body: str) -> bytes:
    """Replace only a validated managed region and preserve its envelope bytes."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    start_marker, end_marker = spec_markers(kind)
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document has malformed managed markers"
        )
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker)
    if end <= start:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document has reversed managed markers"
        )
    newline = "\r\n" if "\r\n" in text else "\n"
    body = managed_body.strip().replace("\r\n", "\n").replace("\n", newline)
    return (text[:start] + newline + body + newline + text[end:]).encode("utf-8")
