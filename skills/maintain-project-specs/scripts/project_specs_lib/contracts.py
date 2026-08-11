"""Canonical parsers, validators, renderers, and receipts for project specs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any


MAX_SPEC_BYTES = 1024 * 1024
REQUIREMENTS_SCHEMA = "maintain-project-specs/requirements-v1"
DESIGN_SCHEMA = "maintain-project-specs/design-v1"
RECEIPT_SCHEMA = "project-agent-instructions.spec-validation.v3"
RECEIPT_OWNER = "maintain-project-specs"
VALIDATOR = "maintain-project-specs/spec-validation"
VALIDATOR_VERSION = 1
CONFIG_SCHEMA = "maintain-project-specs.project.v1"
CONFIG_RELATIVE_PATH = Path(".codex/project-specs.json")

LEGACY_TASK_REQUIREMENTS_SCHEMA = "task-implementer/requirements-v1"
LEGACY_TASK_DESIGN_SCHEMA = "task-implementer/design-v1"
LEGACY_SDLC_REQUIREMENTS_SCHEMA = "agentic-sdlc.requirements.v1"
LEGACY_SDLC_DESIGN_SCHEMA = "agentic-sdlc.design.v1"

REQ_ID_PATTERN = r"(?:REQ|TI-REQ)-[0-9]{3,}"
DESIGN_ID_PATTERN = r"(?:FEAT|TI-DES)-[0-9]{3,}"
REQ_ID_RE = re.compile(rf"{REQ_ID_PATTERN}")
DESIGN_ID_RE = re.compile(rf"{DESIGN_ID_PATTERN}")
PLACEHOLDER_RE = re.compile(r"(?i)(?:\bTODO\b|\bTBD\b|<[^>\n]+>)")
SECRET_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}|"
    r"(?i:\b(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)"
    r"\s*[:=]\s*['\"]?[^\s'\"`]{8,}))"
)


class ProjectSpecError(RuntimeError):
    """Stable fail-closed project-spec error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def stable_json(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_digest(value: object) -> str:
    return digest(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def spec_markers(kind: str) -> tuple[str, str]:
    if kind == "requirements":
        schema = REQUIREMENTS_SCHEMA
    elif kind == "design":
        schema = DESIGN_SCHEMA
    else:
        raise AssertionError(kind)
    return (
        f"<!-- maintain-project-specs:{kind}:start schema={schema} -->",
        f"<!-- maintain-project-specs:{kind}:end -->",
    )


def legacy_task_markers(kind: str) -> tuple[str, str]:
    schema = (
        LEGACY_TASK_REQUIREMENTS_SCHEMA
        if kind == "requirements"
        else LEGACY_TASK_DESIGN_SCHEMA
    )
    return (
        f"<!-- task-implementer:{kind}:start schema={schema} -->",
        f"<!-- task-implementer:{kind}:end -->",
    )


def _run_git(root: Path, *arguments: str, check: bool = True) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectSpecError(
            "ENVIRONMENT_BLOCKER", "Git inspection failed"
        ) from error
    if check and completed.returncode != 0:
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "project is not in a Git worktree"
        )
    return completed.stdout.strip()


def project_identity(project_root: Path) -> tuple[Path, Path, str, str]:
    raw = Path(os.path.abspath(project_root.expanduser()))
    if raw.is_symlink() or not raw.is_dir():
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "project root must be a real directory"
        )
    git_root = Path(_run_git(raw, "rev-parse", "--show-toplevel")).resolve()
    project = raw.resolve()
    try:
        relative = project.relative_to(git_root)
    except ValueError as error:
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "project root escaped its Git worktree"
        ) from error
    scope = "." if relative == Path(".") else relative.as_posix()
    head = _run_git(git_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "project requires a committed Git baseline"
        )
    return project, git_root, scope, head


def load_project_config(project_root: Path) -> dict[str, Any]:
    path = project_root / CONFIG_RELATIVE_PATH
    try:
        path.lstat()
    except FileNotFoundError:
        return {"schema": CONFIG_SCHEMA, "mode": "managed", "scope": "."}
    raw, text = _read_file(path, "project spec policy", max_bytes=4096)
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProjectSpecError(
            "PROJECT_POLICY_INVALID", "project spec policy is invalid JSON"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "mode", "scope"}
        or value.get("schema") != CONFIG_SCHEMA
        or value.get("mode") not in {"managed", "disabled"}
        or value.get("scope") != "."
    ):
        raise ProjectSpecError(
            "PROJECT_POLICY_INVALID", "project spec policy has an invalid contract"
        )
    git_root = Path(_run_git(project_root, "rev-parse", "--show-toplevel")).resolve()
    try:
        path.relative_to(git_root)
    except ValueError as error:
        raise ProjectSpecError(
            "PROJECT_POLICY_INVALID", "project spec policy escaped its Git worktree"
        ) from error
    if not _tracked(git_root, path) or not _committed_exact(git_root, path, raw):
        raise ProjectSpecError(
            "PROJECT_POLICY_INVALID",
            "project spec policy must exactly match the committed Git blob",
        )
    return value


def _read_file(
    path: Path, label: str, *, max_bytes: int = MAX_SPEC_BYTES
) -> tuple[bytes, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError("SPEC_REQUIRED", f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
        or metadata.st_mode & 0o022
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} path or mode is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{label} changed while opening"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ProjectSpecError("UNSAFE_SPEC", f"{label} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
            after.st_nlink,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_mode,
            opened.st_nlink,
        ):
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", f"{label} changed while reading"
            )
    except OSError as error:
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} could not be read") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} disappeared while reading"
        ) from error
    if (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_mode,
        current.st_nlink,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_mode,
        opened.st_nlink,
    ):
        raise ProjectSpecError(
            "CONCURRENT_MODIFICATION", f"{label} changed while reading"
        )
    raw = b"".join(chunks)
    if b"\x00" in raw:
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} contains invalid bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectSpecError("UNSAFE_SPEC", f"{label} must be UTF-8") from error
    if SECRET_RE.search(text):
        raise ProjectSpecError("SENSITIVE_SPEC", f"{label} appears to contain a secret")
    return raw, text


def _managed_region(text: str, kind: str) -> tuple[str, str, str]:
    start, end = spec_markers(kind)
    legacy_start, legacy_end = legacy_task_markers(kind)
    legacy_schema = (
        LEGACY_SDLC_REQUIREMENTS_SCHEMA
        if kind == "requirements"
        else LEGACY_SDLC_DESIGN_SCHEMA
    )
    if (
        f"<!-- task-implementer:{kind}:start" in text
        or legacy_end in text
        or re.search(
            rf"(?m)^schema:\s*['\"]?{re.escape(legacy_schema)}['\"]?\s*$", text
        )
    ):
        raise ProjectSpecError(
            "SPEC_MIGRATION_REQUIRED", f"{kind} still contains legacy ownership"
        )
    if text.count(start) != 1 or text.count(end) != 1:
        raise ProjectSpecError(
            "SPEC_REQUIRED", f"{kind} has no canonical managed region"
        )
    start_index = text.index(start)
    body_index = start_index + len(start)
    end_index = text.index(end)
    if end_index <= body_index:
        raise ProjectSpecError("SPEC_CONFLICT", f"{kind} markers are reversed")
    return text[:start_index], text[body_index:end_index], text[end_index + len(end) :]


def _section(block: str, heading: str, identifier: str) -> str:
    match = re.search(
        rf"(?ms)^#### {re.escape(heading)}\s*\n(.*?)(?=^#### |^### |^## |\Z)",
        block,
    )
    if match is None or not match.group(1).strip():
        raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} is missing {heading}")
    return match.group(1).strip()


def _field(block: str, label: str, identifier: str) -> str:
    matches = re.findall(rf"(?m)^- {re.escape(label)}:\s*(\S.*?)\s*$", block)
    if len(matches) != 1:
        raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} has no unique {label}")
    return matches[0]


def _require_bullets(block: str, heading: str, identifier: str) -> None:
    if re.search(r"(?m)^-\s+\S", _section(block, heading, identifier)) is None:
        raise ProjectSpecError(
            "SPEC_CONFLICT", f"{identifier} has no {heading} entries"
        )


def _parse_compact_requirements(body: str) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^###\s+(TI-REQ-[0-9]{3,}):\s+\S.*$", body))
    if not headings:
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, match in enumerate(headings):
        identifier = match.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        block = body[match.start() : end]
        if identifier in seen:
            raise ProjectSpecError(
                "SPEC_CONFLICT", f"duplicate requirement {identifier}"
            )
        status = _field(block, "Status", identifier)
        if status not in {"active", "satisfied", "superseded"}:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} status is invalid")
        for label in ("Requirement", "Constraints", "Non-goals"):
            _field(block, label, identifier)
        _require_bullets(block, "Acceptance criteria", identifier)
        _require_bullets(block, "Verification", identifier)
        records.append(
            {
                "id": identifier,
                "status": status,
                "priority": None,
                "type": "task",
                "sha256": digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return records


def _parse_rich_requirements(body: str) -> list[dict[str, Any]]:
    markers = list(
        re.finditer(
            r"<!-- REQUIREMENT: (REQ-[0-9]{3,}) status=([a-z-]+) "
            r"priority=(P[0-9]+) type=([a-z-]+) -->",
            body,
        )
    )
    if not markers:
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, marker in enumerate(markers):
        identifier = marker.group(1)
        close = f"<!-- /REQUIREMENT: {identifier} -->"
        end = body.find(close, marker.end())
        next_start = (
            markers[index + 1].start() if index + 1 < len(markers) else len(body)
        )
        if end < 0 or end >= next_start or identifier in seen:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} markers are invalid")
        block = body[marker.end() : end]
        status = marker.group(2)
        if status not in {
            "draft",
            "active",
            "accepted",
            "satisfied",
            "superseded",
            "blocked",
        }:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} status is invalid")
        if re.search(rf"(?m)^### {re.escape(identifier)}:\s+\S", block) is None:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} heading is invalid")
        for heading in (
            "User Story",
            "Validation Method",
            "Test Method",
            "Evaluation Method",
        ):
            value = _section(block, heading, identifier)
            if status not in {"draft", "blocked"} and PLACEHOLDER_RE.search(value):
                raise ProjectSpecError(
                    "SPEC_CONFLICT", f"{identifier} has unresolved {heading}"
                )
        for heading in ("Acceptance Criteria", "Negative Criteria"):
            _require_bullets(block, heading, identifier)
        records.append(
            {
                "id": identifier,
                "status": status,
                "priority": marker.group(3),
                "type": marker.group(4),
                "sha256": digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return records


def _parse_requirements(body: str) -> list[dict[str, Any]]:
    compact = _parse_compact_requirements(body)
    rich = _parse_rich_requirements(body)
    if compact and rich:
        raise ProjectSpecError("SPEC_CONFLICT", "requirements mixes record formats")
    records = compact or rich
    if not records:
        raise ProjectSpecError("SPEC_CONFLICT", "requirements has no managed records")
    return records


def _parse_compact_design(body: str) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^###\s+(TI-DES-[0-9]{3,}):\s+\S.*$", body))
    if not headings:
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, match in enumerate(headings):
        identifier = match.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        block = body[match.start() : end]
        if identifier in seen:
            raise ProjectSpecError("SPEC_CONFLICT", f"duplicate design {identifier}")
        status = _field(block, "Status", identifier)
        if status not in {"planned", "implemented", "superseded"}:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} status is invalid")
        mapping = _field(block, "Requirements", identifier)
        requirements = [item.strip() for item in mapping.split(",")]
        if (
            not requirements
            or len(requirements) != len(set(requirements))
            or any(
                re.fullmatch(r"TI-REQ-[0-9]{3,}", item) is None for item in requirements
            )
        ):
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} mapping is invalid")
        for label in (
            "Selected approach",
            "Boundaries and interfaces",
            "Validation",
            "Rollback",
        ):
            _field(block, label, identifier)
        _require_bullets(block, "Alternatives considered", identifier)
        _require_bullets(block, "Implementation evidence", identifier)
        records.append(
            {
                "id": identifier,
                "requirements": requirements,
                "status": status,
                "priority": None,
                "version": 1,
                "sha256": digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return records


def _parse_rich_design(body: str) -> list[dict[str, Any]]:
    markers = list(
        re.finditer(
            r"<!-- FEATURE: (FEAT-[0-9]{3,}) reqs=([^ ]+) "
            r"status=(ready|draft|blocked|stale) priority=(P[0-9]+) version=([0-9]+) -->",
            body,
        )
    )
    if not markers:
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, marker in enumerate(markers):
        identifier = marker.group(1)
        close = f"<!-- /FEATURE: {identifier} -->"
        end = body.find(close, marker.end())
        next_start = (
            markers[index + 1].start() if index + 1 < len(markers) else len(body)
        )
        if end < 0 or end >= next_start or identifier in seen:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} markers are invalid")
        block = body[marker.end() : end]
        requirements = marker.group(2).split(",")
        if (
            not requirements
            or len(requirements) != len(set(requirements))
            or any(
                re.fullmatch(r"REQ-[0-9]{3,}", item) is None for item in requirements
            )
        ):
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} mapping is invalid")
        if re.search(rf"(?m)^### {re.escape(identifier)}:\s+\S", block) is None:
            raise ProjectSpecError("SPEC_CONFLICT", f"{identifier} heading is invalid")
        for heading in (
            "Requirements Covered",
            "Context Evidence",
            "Design Details",
            "Selected Option",
            "Alternatives Considered",
            "Implementation Boundaries",
            "Test-First Success Criteria",
            "Validation Plan",
            "Test Plan",
            "Evaluation Plan",
            "Rollout And Rollback",
            "Done Definition",
        ):
            _section(block, heading, identifier)
        covered: list[str] = []
        for line in _section(block, "Requirements Covered", identifier).splitlines():
            item = re.fullmatch(r"- (REQ-[0-9]{3,})(?:: \S.*)?", line.strip())
            if item is None:
                raise ProjectSpecError(
                    "SPEC_CONFLICT",
                    f"{identifier} Requirements Covered has an invalid entry",
                )
            covered.append(item.group(1))
        if len(covered) != len(set(covered)) or set(covered) != set(requirements):
            raise ProjectSpecError(
                "SPEC_CONFLICT",
                f"{identifier} Requirements Covered does not match its marker",
            )
        if marker.group(3) == "ready" and PLACEHOLDER_RE.search(block):
            raise ProjectSpecError(
                "SPEC_CONFLICT", f"{identifier} is ready but incomplete"
            )
        records.append(
            {
                "id": identifier,
                "requirements": requirements,
                "status": marker.group(3),
                "priority": marker.group(4),
                "version": int(marker.group(5)),
                "sha256": digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return records


def _parse_design(body: str) -> list[dict[str, Any]]:
    compact = _parse_compact_design(body)
    rich = _parse_rich_design(body)
    if compact and rich:
        raise ProjectSpecError("SPEC_CONFLICT", "design mixes record formats")
    records = compact or rich
    if not records:
        raise ProjectSpecError("SPEC_CONFLICT", "design has no managed records")
    return records


def _tracked(git_root: Path, path: Path) -> bool:
    relative = path.relative_to(git_root).as_posix()
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--error-unmatch", "--", relative],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectSpecError(
            "ENVIRONMENT_BLOCKER", "Git tracking check failed"
        ) from error
    return completed.returncode == 0


def _committed_exact(git_root: Path, path: Path, expected: bytes) -> bool:
    relative = path.relative_to(git_root).as_posix()
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), "show", f"HEAD:{relative}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectSpecError(
            "ENVIRONMENT_BLOCKER", "Git policy content check failed"
        ) from error
    return completed.returncode == 0 and completed.stdout == expected


def inspect_project(
    project_root: Path, *, require_tracked: bool = True
) -> dict[str, Any]:
    project, git_root, scope, head = project_identity(project_root)
    policy = load_project_config(project)
    policy_path = project / CONFIG_RELATIVE_PATH
    try:
        policy_path.lstat()
    except FileNotFoundError:
        policy_present = False
        policy_raw = stable_json(policy)
    else:
        policy_present = True
        policy_raw, policy_text = _read_file(
            policy_path, "project spec policy", max_bytes=4096
        )
        try:
            current_policy: Any = json.loads(policy_text)
        except json.JSONDecodeError as error:
            raise ProjectSpecError(
                "PROJECT_POLICY_INVALID", "project spec policy is invalid JSON"
            ) from error
        if current_policy != policy or not _tracked(git_root, policy_path):
            raise ProjectSpecError(
                "PROJECT_POLICY_INVALID",
                "project spec policy must be stable and tracked in Git",
            )
    if policy["mode"] == "disabled":
        return {
            "schema": RECEIPT_SCHEMA,
            "owner": RECEIPT_OWNER,
            "status": "disabled",
            "project_root": str(project),
            "git_root": str(git_root),
            "project_scope": scope,
            "git_head": head,
            "policy": {
                "path": CONFIG_RELATIVE_PATH.as_posix(),
                "sha256": digest(policy_raw),
            },
        }
    paths = {
        "requirements": project / "docs" / "requirements.md",
        "design": project / "docs" / "design.md",
    }
    raw: dict[str, bytes] = {}
    bodies: dict[str, str] = {}
    envelopes: dict[str, dict[str, str]] = {}
    for kind, path in paths.items():
        value, text = _read_file(path, kind)
        prefix, body, suffix = _managed_region(text, kind)
        if require_tracked and not _tracked(git_root, path):
            raise ProjectSpecError("SPEC_REQUIRED", f"{kind} must be tracked in Git")
        raw[kind] = value
        bodies[kind] = body
        envelopes[kind] = {
            "prefix_sha256": digest(prefix.encode("utf-8")),
            "suffix_sha256": digest(suffix.encode("utf-8")),
        }
    requirements = _parse_requirements(bodies["requirements"])
    design = _parse_design(bodies["design"])
    incomplete_requirements = {
        str(item["id"])
        for item in requirements
        if str(item["id"]).startswith("REQ-") and item["status"] in {"draft", "blocked"}
    }
    if incomplete_requirements:
        raise ProjectSpecError(
            "SPEC_CONFLICT",
            "requirements are not current: "
            + ", ".join(sorted(incomplete_requirements)),
        )
    requirement_ids = {str(item["id"]) for item in requirements}
    applicable = {
        str(item["id"]) for item in requirements if item["status"] != "superseded"
    }
    all_mapped = {
        str(requirement) for item in design for requirement in item["requirements"]
    }
    covered = {
        str(requirement)
        for item in design
        if (
            (str(item["id"]).startswith("TI-DES-") and item["status"] != "superseded")
            or (str(item["id"]).startswith("FEAT-") and item["status"] == "ready")
        )
        for requirement in item["requirements"]
    }
    unknown = all_mapped - requirement_ids
    if unknown:
        raise ProjectSpecError(
            "SPEC_CONFLICT",
            "design maps unknown requirements: " + ", ".join(sorted(unknown)),
        )
    if covered != applicable:
        missing = applicable - covered
        extra = covered - applicable
        details: list[str] = []
        if missing:
            details.append("unmapped requirements: " + ", ".join(sorted(missing)))
        if extra:
            details.append("non-applicable mappings: " + ", ".join(sorted(extra)))
        raise ProjectSpecError("SPEC_CONFLICT", "; ".join(details))
    traceability = {
        "requirements": requirements,
        "design": design,
        "envelopes": envelopes,
        "policy": {
            "present": policy_present,
            "sha256": digest(policy_raw),
        },
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "owner": RECEIPT_OWNER,
        "status": "current",
        "project_root": str(project),
        "git_root": str(git_root),
        "project_scope": scope,
        "git_head": head,
        "validator": VALIDATOR,
        "validator_version": VALIDATOR_VERSION,
        "requirements": {
            "path": "docs/requirements.md",
            "sha256": digest(raw["requirements"]),
        },
        "design": {"path": "docs/design.md", "sha256": digest(raw["design"])},
        "traceability_sha256": canonical_digest(traceability),
    }


def validate_project(project_root: Path) -> dict[str, Any]:
    receipt = inspect_project(project_root, require_tracked=True)
    if receipt.get("status") != "current":
        raise ProjectSpecError(
            "PROJECT_SPECS_DISABLED", "project spec automation is disabled"
        )
    return receipt


def canonical_document(kind: str, managed_body: str, *, prefix: bytes = b"") -> bytes:
    try:
        prefix.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectSpecError("UNSAFE_SPEC", f"{kind} prefix must be UTF-8") from error
    start, end = spec_markers(kind)
    newline = b"\r\n" if b"\r\n" in prefix else b"\n"
    if prefix:
        if prefix.endswith(newline + newline):
            envelope = prefix
        elif prefix.endswith(newline):
            envelope = prefix + newline
        else:
            envelope = prefix + newline + newline
    else:
        title = b"# Requirements" if kind == "requirements" else b"# Design"
        envelope = title + newline + newline
    body = managed_body.strip().replace("\r\n", "\n").encode("utf-8")
    if newline == b"\r\n":
        body = body.replace(b"\n", b"\r\n")
    return envelope + start.encode() + newline + body + newline + end.encode() + newline
