"""Stable schemas, rendering rules, and shared safety primitives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Optional
import unicodedata


MANIFEST_SCHEMA = "project-agent-instructions.manifest.v3"
DECISION_SCHEMA = "project-agent-instructions.decision.v3"
STATE_SCHEMA = "project-agent-instructions.state.v3"
SPEC_RECEIPT_SCHEMA = "maintain-project-specs.spec-validation.v4"
OWNERSHIP_SCHEMA = "project-agent-instructions.ownership.v3"
RUNTIME_CONFIG_SCHEMA = "project-agent-instructions.runtime-config.v1"
PRIVATE_ROOT_SCHEMA = "project-agent-instructions.private-root.v1"
PRIVATE_ROOT_MARKER = ".project-agent-instructions-root.json"
RENDERER_VERSION = 3
PREFERRED_BODY_BYTES = 2 * 1024
MAX_BODY_BYTES = 4 * 1024
PREFERRED_RULES = 8
MAX_RULES = 12
MAX_RULE_BYTES = 256
GENERATED_MARKER_PREFIX = b"<!-- project-agent-instructions:managed-v3 "
GENERATED_MARKER_SUFFIX = b" -->\n\n"
GENERATED_MARKER_TOKEN = b"<!-- project-agent-instructions:managed-v3"
LEGACY_GENERATED_MARKER_TOKEN = b"<!-- project-agent-instructions:generated-v1"
LEGACY_MANAGED_MARKER_TOKEN = b"<!-- project-agent-instructions:managed-v2"
GENERATED_MARKER_RE = re.compile(
    rb"<!-- project-agent-instructions:managed-v3 "
    rb"manifest-sha256=([0-9a-f]{64}) "
    rb"decision-sha256=([0-9a-f]{64}) "
    rb"body-sha256=([0-9a-f]{64}) -->\n\n"
)
LEGACY_GENERATED_MARKER_RE = re.compile(
    rb"^<!-- project-agent-instructions:generated-v1 "
    rb"body-sha256=([0-9a-f]{64}) -->\n\n"
)
LEGACY_MANAGED_MARKER_RE = re.compile(
    rb"^<!-- project-agent-instructions:managed-v2 "
    rb"manifest-sha256=([0-9a-f]{64}) "
    rb"decision-sha256=([0-9a-f]{64}) "
    rb"body-sha256=([0-9a-f]{64}) -->\n\n"
)
GENERATED_MARKER_BYTES = (
    len(GENERATED_MARKER_PREFIX)
    + len("manifest-sha256=")
    + 64
    + 1
    + len("decision-sha256=")
    + 64
    + 1
    + len("body-sha256=")
    + 64
    + len(GENERATED_MARKER_SUFFIX)
)
RULE_SECTIONS = (
    "Architecture and boundaries",
    "Development commands",
    "Change requirements",
    "Testing and verification",
    "Security and operations",
    "Definition of done",
)
TASK_REQUIREMENTS_MARKER = (
    "maintain-project-specs:requirements:start "
    "schema=maintain-project-specs/requirements-v2"
)
TASK_DESIGN_MARKER = (
    "maintain-project-specs:design:start schema=maintain-project-specs/design-v2"
)
AGENTIC_REQUIREMENTS_SCHEMA = "schema: maintain-project-specs/requirements-v2"
AGENTIC_DESIGN_SCHEMA = "schema: maintain-project-specs/design-v2"
URL_RE = re.compile(r"(?i)\bhttps?://")
IPV4_RE = re.compile(
    r"(?<![0-9])(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}(?![0-9])"
)
BRACKETED_HOST_RE = re.compile(r"\[[0-9A-Fa-f:.%]+\]")
SECRET_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bAIza[0-9A-Za-z_-]{30,}\b|"
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b|"
    r"\bglpat-[A-Za-z0-9_-]{20,}\b|"
    r"\bnpm_[A-Za-z0-9]{20,}\b|"
    r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}|"
    r"(?i:\b(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)"
    r"\s*[:=]\s*['\"]?[^\s'\"`]{8,}))"
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


def _generation_manifest_sha256(manifest: dict[str, object]) -> str:
    """Hash only repository-portable inputs that can affect committed bytes."""

    return _sha256_bytes(
        _canonical_json(
            {
                "schema": "project-agent-instructions.generation-manifest.v1",
                "renderer_version": manifest["renderer_version"],
                "project_scope": manifest["project_scope"],
                "requirements": manifest["requirements"],
                "design": manifest["design"],
            }
        )
    )


def _generation_decision_sha256(
    disposition: str,
    body: bytes,
    evidence: list[dict[str, str]],
) -> str:
    """Hash the portable semantic decision while excluding private rationale."""

    return _sha256_bytes(
        _canonical_json(
            {
                "schema": "project-agent-instructions.generation-decision.v1",
                "disposition": disposition,
                "body_sha256": _sha256_bytes(body),
                "evidence": sorted(
                    evidence,
                    key=lambda item: (
                        item["path"],
                        item["sha256"],
                        item["locator"],
                    ),
                ),
            }
        )
    )


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


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _parse_generated(content: bytes) -> Optional[dict[str, object]]:
    matches = list(GENERATED_MARKER_RE.finditer(content))
    reserved_tokens = sum(
        content.count(token)
        for token in (
            GENERATED_MARKER_TOKEN,
            LEGACY_GENERATED_MARKER_TOKEN,
            LEGACY_MANAGED_MARKER_TOKEN,
        )
    )
    if len(matches) > 1 or (matches and reserved_tokens != 1):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md has multiple managed regions"
        )
    if matches:
        match = matches[0]
        if match.start() == 0:
            prefix = b""
        elif (
            match.start() >= 2 and content[match.start() - 2 : match.start()] == b"\n\n"
        ):
            prefix = content[: match.start() - 2]
        else:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "project AGENTS.md managed region separator is invalid",
            )
        body = content[match.end() :]
        return {
            "version": 3,
            "manifest_sha256": match.group(1).decode("ascii"),
            "decision_sha256": match.group(2).decode("ascii"),
            "body_sha256": match.group(3).decode("ascii"),
            "actual_body_sha256": _sha256_bytes(body),
            "prefix_bytes": len(prefix),
            "prefix_sha256": _sha256_bytes(prefix),
            "prefix": prefix,
        }
    legacy = LEGACY_GENERATED_MARKER_RE.match(content)
    if legacy is not None:
        if reserved_tokens != 1:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "project AGENTS.md has multiple managed regions"
            )
        body = content[legacy.end() :]
        return {
            "version": 1,
            "manifest_sha256": None,
            "decision_sha256": None,
            "body_sha256": legacy.group(1).decode("ascii"),
            "actual_body_sha256": _sha256_bytes(body),
            "prefix_bytes": 0,
            "prefix_sha256": _sha256_bytes(b""),
            "prefix": b"",
        }
    legacy_managed = LEGACY_MANAGED_MARKER_RE.match(content)
    if legacy_managed is None:
        if reserved_tokens:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "project AGENTS.md managed marker is malformed"
            )
        return None
    if reserved_tokens != 1:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project AGENTS.md has multiple managed regions"
        )
    body = content[legacy_managed.end() :]
    return {
        "version": 2,
        "manifest_sha256": legacy_managed.group(1).decode("ascii"),
        "decision_sha256": legacy_managed.group(2).decode("ascii"),
        "body_sha256": legacy_managed.group(3).decode("ascii"),
        "actual_body_sha256": _sha256_bytes(body),
        "prefix_bytes": 0,
        "prefix_sha256": _sha256_bytes(b""),
        "prefix": b"",
    }


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


def _valid_path_digest(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"path", "sha256"}
        and isinstance(value.get("path"), str)
        and bool(value.get("path"))
        and _valid_sha256(value.get("sha256"))
    )


def _valid_project_relative_path(value: object, *, allow_root: bool = False) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if allow_root and value == ".":
        return True
    if value.startswith("/") or "\\" in value or "`" in value:
        return False
    if any(unicodedata.category(character).startswith("C") for character in value):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _unsafe_persistent_text(value: str) -> bool:
    return bool(
        PLACEHOLDER_RE.search(value)
        or ABSOLUTE_HOME_RE.search(value)
        or URL_RE.search(value)
        or IPV4_RE.search(value)
        or BRACKETED_HOST_RE.search(value)
        or SECRET_RE.search(value)
    )


def _validate_manifest_shape(manifest: dict[str, object]) -> None:
    required = {
        "schema",
        "renderer_version",
        "project_root",
        "git_root",
        "project_scope",
        "project_name",
        "spec_owner",
        "requirements",
        "design",
        "spec_receipt",
        "codex_home",
        "config_context",
        "generated_body_max_bytes",
        "global_instructions",
        "ancestor_project_instructions",
        "active_project_instruction",
        "target",
        "manifest_sha256",
    }
    target = manifest.get("target")
    active = manifest.get("active_project_instruction")
    global_entries = manifest.get("global_instructions")
    ancestor_entries = manifest.get("ancestor_project_instructions")
    config = manifest.get("config_context")
    basic_valid = (
        set(manifest) == required
        and manifest.get("schema") == MANIFEST_SCHEMA
        and manifest.get("renderer_version") == RENDERER_VERSION
        and isinstance(manifest.get("project_root"), str)
        and bool(manifest.get("project_root"))
        and isinstance(manifest.get("git_root"), str)
        and bool(manifest.get("git_root"))
        and _valid_project_relative_path(manifest.get("project_scope"), allow_root=True)
        and isinstance(manifest.get("project_name"), str)
        and bool(manifest.get("project_name"))
        and manifest.get("spec_owner") == "maintain-project-specs"
        and _valid_path_digest(manifest.get("requirements"))
        and _valid_path_digest(manifest.get("design"))
        and _valid_project_relative_path(dict(manifest["requirements"]).get("path"))
        and _valid_project_relative_path(dict(manifest["design"]).get("path"))
        and _valid_path_digest(manifest.get("spec_receipt"))
        and isinstance(manifest.get("codex_home"), str)
        and bool(manifest.get("codex_home"))
        and type(manifest.get("generated_body_max_bytes")) is int
        and 0 <= int(manifest["generated_body_max_bytes"]) <= MAX_BODY_BYTES
        and isinstance(global_entries, list)
        and all(_valid_instruction_entry(item) for item in global_entries)
        and isinstance(ancestor_entries, list)
        and all(_valid_instruction_entry(item) for item in ancestor_entries)
        and (active is None or _valid_instruction_entry(active))
        and _valid_sha256(manifest.get("manifest_sha256"))
    )
    config_valid = (
        isinstance(config, dict)
        and set(config)
        == {
            "fallback_filenames",
            "project_doc_max_bytes",
            "project_root_markers",
            "sources",
            "runtime_config_sha256",
            "sha256",
        }
        and isinstance(config.get("fallback_filenames"), list)
        and all(isinstance(item, str) and item for item in config["fallback_filenames"])
        and type(config.get("project_doc_max_bytes")) is int
        and int(config["project_doc_max_bytes"]) > 0
        and isinstance(config.get("project_root_markers"), list)
        and all(
            isinstance(item, str) and item for item in config["project_root_markers"]
        )
        and isinstance(config.get("sources"), list)
        and all(_valid_path_digest(item) for item in config["sources"])
        and _valid_sha256(config.get("runtime_config_sha256"))
        and _valid_sha256(config.get("sha256"))
    )
    target_valid = (
        isinstance(target, dict)
        and set(target)
        == {
            "path",
            "file_status",
            "sha256",
            "marker_version",
            "manifest_sha256",
            "decision_sha256",
            "body_sha256",
            "managed_prefix_bytes",
            "managed_prefix_sha256",
            "active_path",
            "active_kind",
            "parent_device",
            "parent_inode",
        }
        and isinstance(target.get("path"), str)
        and bool(target.get("path"))
        and target.get("file_status")
        in {"missing", "managed", "legacy", "human-edited", "human-owned"}
        and (target.get("sha256") is None or _valid_sha256(target.get("sha256")))
        and target.get("marker_version") in {None, 1, 2, 3}
        and (
            target.get("manifest_sha256") is None
            or _valid_sha256(target.get("manifest_sha256"))
        )
        and (
            target.get("decision_sha256") is None
            or _valid_sha256(target.get("decision_sha256"))
        )
        and (
            target.get("body_sha256") is None
            or _valid_sha256(target.get("body_sha256"))
        )
        and type(target.get("managed_prefix_bytes")) is int
        and int(target["managed_prefix_bytes"]) >= 0
        and (
            target.get("managed_prefix_sha256") is None
            or _valid_sha256(target.get("managed_prefix_sha256"))
        )
        and (
            target.get("active_path") is None
            or isinstance(target.get("active_path"), str)
        )
        and (
            target.get("active_kind") is None
            or isinstance(target.get("active_kind"), str)
        )
        and type(target.get("parent_device")) is int
        and int(target["parent_device"]) >= 0
        and type(target.get("parent_inode")) is int
        and int(target["parent_inode"]) > 0
    )
    if isinstance(target, dict):
        status = target.get("file_status")
        target_sha = target.get("sha256")
        marker_version = target.get("marker_version")
        marker_manifest = target.get("manifest_sha256")
        marker_decision = target.get("decision_sha256")
        marker_body = target.get("body_sha256")
        prefix_bytes = target.get("managed_prefix_bytes")
        prefix_sha = target.get("managed_prefix_sha256")
        target_valid = target_valid and (
            (
                status == "missing"
                and target_sha is None
                and marker_version is None
                and marker_manifest is None
                and marker_decision is None
                and marker_body is None
                and prefix_bytes == 0
                and prefix_sha is None
            )
            or (
                status == "human-owned"
                and _valid_sha256(target_sha)
                and marker_version is None
                and marker_manifest is None
                and marker_decision is None
                and marker_body is None
                and type(prefix_bytes) is int
                and int(prefix_bytes) >= 0
                and _valid_sha256(prefix_sha)
            )
            or (
                status == "legacy"
                and _valid_sha256(target_sha)
                and marker_version in {1, 2}
                and (
                    (
                        marker_version == 1
                        and marker_manifest is None
                        and marker_decision is None
                    )
                    or (
                        marker_version == 2
                        and _valid_sha256(marker_manifest)
                        and _valid_sha256(marker_decision)
                    )
                )
                and _valid_sha256(marker_body)
                and prefix_bytes == 0
                and _valid_sha256(prefix_sha)
            )
            or (
                status in {"managed", "human-edited"}
                and _valid_sha256(target_sha)
                and marker_version == 3
                and _valid_sha256(marker_manifest)
                and _valid_sha256(marker_decision)
                and _valid_sha256(marker_body)
                and type(prefix_bytes) is int
                and int(prefix_bytes) >= 0
                and _valid_sha256(prefix_sha)
            )
        )
        target_valid = target_valid and (
            (target.get("active_path") is None and target.get("active_kind") is None)
            or (
                isinstance(target.get("active_path"), str)
                and isinstance(target.get("active_kind"), str)
                and bool(target.get("active_path"))
                and bool(target.get("active_kind"))
            )
        )
    if not (basic_valid and config_valid and target_valid):
        raise ProjectInstructionsError("UNSAFE_TARGET", "manifest structure is invalid")
    config_payload = dict(config)
    config_digest = config_payload.pop("sha256")
    if _sha256_bytes(_canonical_json(config_payload)) != config_digest:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "config context fingerprint is invalid"
        )
    payload = dict(manifest)
    recorded_digest = payload.pop("manifest_sha256")
    if _sha256_bytes(_canonical_json(payload)) != recorded_digest:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "manifest fingerprint is invalid"
        )


def _validate_rules(
    rules: object,
    evidence_paths: set[str],
) -> list[dict[str, object]]:
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"needed decision requires 1-{MAX_RULES} rules"
        )
    normalized: list[dict[str, object]] = []
    seen_instructions: set[str] = set()
    for raw in rules:
        if not isinstance(raw, dict) or set(raw) != {
            "section",
            "instruction",
            "evidence",
        }:
            raise ProjectInstructionsError("UNSAFE_TARGET", "rule structure is invalid")
        section = raw.get("section")
        instruction = raw.get("instruction")
        evidence = raw.get("evidence")
        if (
            section not in RULE_SECTIONS
            or not isinstance(instruction, str)
            or instruction != instruction.strip()
            or not instruction
            or instruction.splitlines() != [instruction]
            or any(
                unicodedata.category(character).startswith("C")
                for character in instruction
            )
            or instruction.startswith(("-", "*", "#"))
            or len(instruction.encode("utf-8")) > MAX_RULE_BYTES
            or _unsafe_persistent_text(instruction)
            or not isinstance(evidence, list)
            or not evidence
            or len(evidence) != len(set(evidence))
            or any(path not in evidence_paths for path in evidence)
        ):
            raise ProjectInstructionsError("UNSAFE_TARGET", "rule content is invalid")
        identity = instruction.casefold()
        if identity in seen_instructions:
            raise ProjectInstructionsError("UNSAFE_TARGET", "duplicate rule is invalid")
        rule_payload = {
            "section": section,
            "instruction": instruction,
            "evidence": sorted(evidence),
        }
        normalized.append(
            {
                "id": "PAI-" + _sha256_bytes(_canonical_json(rule_payload))[:10],
                **rule_payload,
            }
        )
        seen_instructions.add(identity)
    normalized.sort(
        key=lambda item: (
            RULE_SECTIONS.index(str(item["section"])),
            str(item["instruction"]).casefold(),
            str(item["id"]),
        )
    )
    return normalized


def _render_body(
    manifest: dict[str, object],
    rules: object,
    budget_exception: object,
    evidence_paths: set[str],
) -> tuple[bytes, list[dict[str, object]]]:
    if (
        not _valid_project_relative_path(manifest.get("project_scope"), allow_root=True)
        or not isinstance(manifest.get("requirements"), dict)
        or not _valid_project_relative_path(dict(manifest["requirements"]).get("path"))
        or not isinstance(manifest.get("design"), dict)
        or not _valid_project_relative_path(dict(manifest["design"]).get("path"))
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered project paths are unsafe"
        )
    normalized = _validate_rules(rules, evidence_paths)
    if budget_exception is not None and (
        not isinstance(budget_exception, str)
        or not budget_exception.strip()
        or len(budget_exception.encode("utf-8")) > 500
        or _unsafe_persistent_text(budget_exception)
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "budget exception must be compact and public-safe"
        )
    lines = [
        "# Project Agent Instructions",
        "",
        "## Scope",
        "",
        "These instructions apply to this directory and all descendants.",
        "",
        f"Project root: `{manifest['project_scope']}`",
        "",
        "Closer nested instruction files may refine these defaults for their subtree.",
        "",
        "## Context authority",
        "",
        f"- Requirements: `{dict(manifest['requirements'])['path']}`",
        f"- Design: `{dict(manifest['design'])['path']}`",
        "- Read only the sections relevant to the boundary being changed.",
        "",
    ]
    for section in RULE_SECTIONS:
        selected = [item for item in normalized if item["section"] == section]
        if not selected:
            continue
        lines.extend([f"## {section}", ""])
        lines.extend(f"- {item['instruction']}" for item in selected)
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    encoded = body.encode("utf-8")
    maximum = int(manifest["generated_body_max_bytes"])
    preferred_exceeded = (
        len(encoded) > PREFERRED_BODY_BYTES or len(normalized) > PREFERRED_RULES
    )
    if len(encoded) > maximum or len(encoded) > MAX_BODY_BYTES:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered instructions exceed the hard byte budget"
        )
    if preferred_exceeded and budget_exception is None:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "preferred instruction budget requires justification"
        )
    if not preferred_exceeded and budget_exception is not None:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "budget exception is unnecessary"
        )
    if _unsafe_persistent_text(body):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "rendered instructions contain unsafe content"
        )
    return encoded, normalized
