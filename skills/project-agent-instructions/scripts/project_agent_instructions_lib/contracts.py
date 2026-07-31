"""Stable schemas, validation rules, and shared safety primitives."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from typing import Optional


MANIFEST_SCHEMA = "project-agent-instructions.manifest.v1"
DECISION_SCHEMA = "project-agent-instructions.decision.v1"
STATE_SCHEMA = "project-agent-instructions.state.v1"
PRIVATE_ROOT_SCHEMA = "project-agent-instructions.private-root.v1"
PRIVATE_ROOT_MARKER = ".project-agent-instructions-root.json"
RENDERER_VERSION = 1
MAX_BODY_BYTES = 7 * 1024
GENERATED_MARKER_PREFIX = b"<!-- project-agent-instructions:generated-v1 body-sha256="
GENERATED_MARKER_SUFFIX = b" -->\n\n"
GENERATED_MARKER_BYTES = (
    len(GENERATED_MARKER_PREFIX) + 64 + len(GENERATED_MARKER_SUFFIX)
)
GENERATED_MARKER_RE = re.compile(
    rb"^<!-- project-agent-instructions:generated-v1 "
    rb"body-sha256=([0-9a-f]{64}) -->\n\n"
)
ALLOWED_SECTIONS = (
    "Scope",
    "Project purpose",
    "Read before changing",
    "Architecture and boundaries",
    "Development commands",
    "Change requirements",
    "Testing strategy",
    "Security requirements",
    "Operational requirements",
    "Verification requirements",
    "Context authority",
    "Definition of done",
)
MANDATORY_SECTIONS = {"Scope", "Read before changing"}
TASK_REQUIREMENTS_MARKER = (
    "task-implementer:requirements:start schema=task-implementer/requirements-v1"
)
TASK_DESIGN_MARKER = "task-implementer:design:start schema=task-implementer/design-v1"
AGENTIC_REQUIREMENTS_SCHEMA = "schema: agentic-sdlc.requirements.v1"
AGENTIC_DESIGN_SCHEMA = "schema: agentic-sdlc.design.v1"
PRIVATE_URL_RE = re.compile(
    r"https?://(?:localhost|127(?:\.[0-9]+){3}|10(?:\.[0-9]+){3}|"
    r"192\.168(?:\.[0-9]+){2}|172\.(?:1[6-9]|2[0-9]|3[01])"
    r"(?:\.[0-9]+){2}|[^/\s]+(?:\.internal|\.local))(?:[/:]|\b)",
    re.IGNORECASE,
)
BRACKETED_HOST_URL_RE = re.compile(
    r"https?://\[([^\]\s/]+)\]",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,})"
)
PLACEHOLDER_RE = re.compile(r"(?i)(?:<[^>\n]+>|\bTODO\b|\bTBD\b)")
ABSOLUTE_HOME_RE = re.compile(r"(?m)(?:^|[\s`'\"])/(?:Users|home)/")


class ProjectInstructionsError(RuntimeError):
    """Structured fail-closed helper error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _stable_json(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _lstat_optional(path: Path) -> Optional[os.stat_result]:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _read_regular(path: Path, label: str) -> bytes:
    metadata = _lstat_optional(path)
    if metadata is None:
        raise ProjectInstructionsError("PREREQUISITE_MISSING", f"{label} is missing")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} must be a regular non-symlink file"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} could not be read"
        ) from error


def _remove_owned_file(path: Path, expected: os.stat_result) -> None:
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        ):
            path.unlink()
    except FileNotFoundError:
        return


def _parse_generated(content: bytes) -> Optional[tuple[str, str]]:
    match = GENERATED_MARKER_RE.match(content)
    if match is None:
        return None
    body = content[match.end() :]
    recorded = match.group(1).decode("ascii")
    actual = _sha256_bytes(body)
    return recorded, actual


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_instruction_entry(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "scope",
        "kind",
        "sha256",
        "bytes",
        "project_relative_path",
    }:
        return False
    relative = value.get("project_relative_path")
    return (
        isinstance(value.get("path"), str)
        and bool(value.get("path"))
        and value.get("scope") in {"global", "project"}
        and isinstance(value.get("kind"), str)
        and bool(value.get("kind"))
        and _valid_sha256(value.get("sha256"))
        and type(value.get("bytes")) is int
        and int(value["bytes"]) >= 0
        and (relative is None or isinstance(relative, str))
    )


def _contains_private_url(body: str) -> bool:
    if PRIVATE_URL_RE.search(body):
        return True
    for match in BRACKETED_HOST_URL_RE.finditer(body):
        host = match.group(1).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version == 6 and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
        ):
            return True
    return False


def _validate_manifest_shape(manifest: dict[str, object]) -> None:
    required = {
        "schema",
        "renderer_version",
        "project_root",
        "git_root",
        "project_scope",
        "spec_owner",
        "requirements",
        "design",
        "codex_home",
        "fallback_filenames",
        "configured_project_doc_max_bytes",
        "generated_body_max_bytes",
        "inherited_instructions",
        "active_project_instruction",
        "target",
        "manifest_sha256",
    }
    requirements = manifest.get("requirements")
    design = manifest.get("design")
    target = manifest.get("target")
    inherited = manifest.get("inherited_instructions")
    active = manifest.get("active_project_instruction")
    fallbacks = manifest.get("fallback_filenames")
    basic_valid = (
        set(manifest) == required
        and manifest.get("schema") == MANIFEST_SCHEMA
        and manifest.get("renderer_version") == RENDERER_VERSION
        and isinstance(manifest.get("project_root"), str)
        and bool(manifest.get("project_root"))
        and isinstance(manifest.get("git_root"), str)
        and bool(manifest.get("git_root"))
        and isinstance(manifest.get("project_scope"), str)
        and bool(manifest.get("project_scope"))
        and manifest.get("spec_owner") in {"task-implementer", "agentic-sdlc"}
        and isinstance(manifest.get("codex_home"), str)
        and bool(manifest.get("codex_home"))
        and isinstance(fallbacks, list)
        and all(isinstance(item, str) and item for item in fallbacks)
        and type(manifest.get("configured_project_doc_max_bytes")) is int
        and int(manifest["configured_project_doc_max_bytes"]) > 0
        and type(manifest.get("generated_body_max_bytes")) is int
        and 0 <= int(manifest["generated_body_max_bytes"]) <= MAX_BODY_BYTES
        and isinstance(inherited, list)
        and all(_valid_instruction_entry(item) for item in inherited)
        and (active is None or _valid_instruction_entry(active))
        and _valid_sha256(manifest.get("manifest_sha256"))
    )
    record_valid = all(
        isinstance(record, dict)
        and set(record) == {"path", "sha256"}
        and isinstance(record.get("path"), str)
        and bool(record.get("path"))
        and _valid_sha256(record.get("sha256"))
        for record in (requirements, design)
    )
    target_valid = (
        isinstance(target, dict)
        and set(target)
        == {
            "path",
            "file_status",
            "sha256",
            "body_sha256",
            "active_path",
            "active_kind",
        }
        and isinstance(target.get("path"), str)
        and bool(target.get("path"))
        and target.get("file_status")
        in {"missing", "generated", "human-edited", "human-owned"}
        and (target.get("sha256") is None or _valid_sha256(target.get("sha256")))
        and (
            target.get("body_sha256") is None
            or _valid_sha256(target.get("body_sha256"))
        )
        and (
            target.get("active_path") is None
            or isinstance(target.get("active_path"), str)
        )
        and (
            target.get("active_kind") is None
            or isinstance(target.get("active_kind"), str)
        )
    )
    if not (basic_valid and record_valid and target_valid):
        raise ProjectInstructionsError("UNSAFE_TARGET", "manifest structure is invalid")
    payload = dict(manifest)
    recorded_digest = payload.pop("manifest_sha256")
    if _sha256_bytes(_canonical_json(payload)) != recorded_digest:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "manifest fingerprint is invalid"
        )


def _validate_body(body: object, manifest: dict[str, object]) -> bytes:
    if not isinstance(body, str):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "needed decision requires a Markdown body"
        )
    encoded = body.encode("utf-8")
    maximum = int(manifest["generated_body_max_bytes"])
    if (
        not body.endswith("\n")
        or body.endswith("\n\n")
        or "\r" in body
        or len(encoded) > maximum
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "generated body must use LF, one trailing newline, and the byte limit",
        )
    first_line = body.splitlines()[0] if body.splitlines() else ""
    if (
        re.fullmatch(r"# .+ Agent Instructions", first_line) is None
        or PLACEHOLDER_RE.search(body)
        or GENERATED_MARKER_RE.search(encoded)
        or ABSOLUTE_HOME_RE.search(body)
        or _contains_private_url(body)
        or SECRET_RE.search(body)
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "generated body contains unsafe or placeholder content",
        )
    headings = re.findall(r"(?m)^## (.+)$", body)
    if len(headings) != len(set(headings)) or not MANDATORY_SECTIONS.issubset(headings):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "generated body has missing or duplicate sections"
        )
    if any(heading not in ALLOWED_SECTIONS for heading in headings):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "generated body has an unsupported section"
        )
    indexes = [ALLOWED_SECTIONS.index(heading) for heading in headings]
    if indexes != sorted(indexes):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "generated sections are out of canonical order"
        )
    scope = str(manifest["project_scope"])
    required_text = (
        "These instructions apply to this directory and all descendants.",
        f"Project root: `{scope}`",
        "`docs/requirements.md`",
        "`docs/design.md`",
    )
    if any(item not in body for item in required_text):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "generated body is missing required scope or source text",
        )
    return encoded
