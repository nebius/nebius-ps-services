"""Decision validation, managed ownership transitions, and verification."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Optional

from .contracts import DECISION_SCHEMA
from .contracts import MANIFEST_SCHEMA
from .contracts import OWNERSHIP_SCHEMA
from .contracts import PRIVATE_ROOT_MARKER
from .contracts import STATE_SCHEMA
from .contracts import ProjectInstructionsError
from .contracts import _canonical_json
from .contracts import _generation_decision_sha256
from .contracts import _generation_manifest_sha256
from .contracts import _lstat_optional
from .contracts import _parse_generated
from .contracts import _read_regular
from .contracts import _render_body
from .contracts import _sha256_bytes
from .contracts import _stable_json
from .contracts import _valid_path_digest
from .contracts import _valid_sha256
from .contracts import _validate_manifest_shape
from .discovery import _manifest
from .discovery import _validate_evidence
from .private_state import _ensure_private_root
from .private_state import _load_private_json_object
from .private_state import _private_member
from .private_state import _relative_private_path
from .private_state import _sync_private_directory
from .private_state import _write_private_json
from .render_state import RENDER_STATE_SCHEMA
from .render_state import RENDER_STATE_PUBLICATION_INCOMPLETE
from .render_state import replace_rendered_rules
from .render_state import render_lock
from .render_state import validate_render_disposition
from .render_state import validate_render_predecessor
from .target_io import _exclusive_create
from .target_io import _complete_retained_backup
from .target_io import _generated_content
from .target_io import _guarded_delete
from .target_io import _guarded_replace


SEALED_LIFECYCLE_SCHEMA = "maintain-project-specs.lifecycle.v1"
SEALED_LIFECYCLE_RULES_PATH = "pending-project-rules.md"
CONTINUITY_JSON_MAX_BYTES = 64 * 1024
WORKSPACE_REGISTRY_MAX_BYTES = 1024 * 1024
WORKSPACE_OWNERSHIP_ROOT = "project-agent-ownership"
WORKSPACE_OWNERSHIP_REGISTRY = "registry.json"
WORKSPACE_OWNERSHIP_SCHEMA = "project-agent-instructions.workspace-ownership.v1"
LIFECYCLE_REQUIRED_FIELDS = {
    "schema",
    "project_scope",
    "git_head_at_prompt",
    "turn_sha256",
    "phase",
    "receipt_sha256",
    "requirements_sha256",
    "design_sha256",
    "rules_path",
    "rules_sha256",
    "project_instructions_state_sha256",
    "project_instructions_reload_required",
    "write_epoch",
    "planned_write_epoch",
    "waiver",
}
ACTIVE_OWNERSHIP_OUTCOMES = {
    "created",
    "attached",
    "refreshed",
    "adopted",
    "existing-sufficient",
}
STATE_REQUIRED_FIELDS = {
    "schema",
    "project_root",
    "git_root",
    "project_scope",
    "spec_owner",
    "requirements",
    "design",
    "spec_receipt",
    "codex_home",
    "private_root",
    "manifest_path",
    "manifest_file_sha256",
    "decision_path",
    "decision_file_sha256",
    "ownership_path",
    "ownership_file_sha256",
    "input_manifest_sha256",
    "current_manifest_sha256",
    "decision_sha256",
    "outcome",
    "target_path",
    "target_sha256",
    "active_instruction_path",
    "reload_required",
}


def _validate_approval(value: object) -> Optional[dict[str, str]]:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"action", "target_sha256"}
        or value.get("action") not in {"adopt", "retire"}
        or not _valid_sha256(value.get("target_sha256"))
    ):
        raise ProjectInstructionsError("UNSAFE_TARGET", "ownership approval is invalid")
    return {
        "action": str(value["action"]),
        "target_sha256": str(value["target_sha256"]),
    }


def render_decision(
    manifest_path: Path,
    decision_path: Path,
    output_path: Path,
    state_path: Path,
    private_root: Path,
) -> dict[str, object]:
    """Serialize one render and its matching state publication."""

    provisional_root = Path(os.path.abspath(private_root.expanduser()))
    provisional_manifest = _private_member(provisional_root, manifest_path, "manifest")
    recorded = _load_private_json_object(
        provisional_manifest, "manifest", provisional_root
    )
    _validate_manifest_shape(recorded)
    locked_root = _ensure_private_root(
        provisional_root, Path(str(recorded["git_root"]))
    )
    with render_lock(locked_root):
        return _render_decision_locked(
            manifest_path,
            decision_path,
            output_path,
            state_path,
            locked_root,
        )


def _render_decision_locked(
    manifest_path: Path,
    decision_path: Path,
    output_path: Path,
    state_path: Path,
    private_root: Path,
) -> dict[str, object]:
    """Render exact rules while the current private-bundle lock is held."""

    provisional_root = Path(os.path.abspath(private_root.expanduser()))
    manifest_path = _private_member(provisional_root, manifest_path, "manifest")
    decision_path = _private_member(provisional_root, decision_path, "decision")
    recorded = _load_private_json_object(manifest_path, "manifest", provisional_root)
    _validate_manifest_shape(recorded)
    private_root = _ensure_private_root(
        provisional_root, Path(str(recorded["git_root"]))
    )
    current = _fresh_manifest(recorded)
    if current != recorded:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project inputs changed after inspection"
        )
    decision = _load_private_json_object(decision_path, "decision", private_root)
    disposition, body, decision_sha256, approval = _validate_decision(decision, current)
    validate_render_disposition(current, disposition, approval)
    rendered = body or b""
    output_path = _private_member(private_root, output_path, "rendered rules")
    state_path = _private_member(private_root, state_path, "render state")
    existing = _lstat_optional(output_path)
    if existing is not None:
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_IMODE(existing.st_mode) != 0o600
            or existing.st_nlink != 1
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "rendered rules target is not an exact owned file"
            )
        existing_bytes = _read_regular(output_path, "rendered rules")
        if existing_bytes != rendered:
            predecessor_state, predecessor_state_bytes = validate_render_predecessor(
                state_path,
                output_path,
                private_root,
                current,
                manifest_path,
                decision_path,
                existing,
                existing_bytes,
            )
            replace_rendered_rules(
                output_path,
                rendered,
                private_root,
                state_path,
                predecessor_state,
                predecessor_state_bytes,
                existing,
                existing_bytes,
            )
    else:
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{output_path.name}.", dir=str(private_root)
            )
            temporary_path = Path(temporary)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, output_path)
        except OSError as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "rendered rules could not be written safely"
            ) from error
        finally:
            if "temporary_path" in locals() and temporary_path.exists():
                temporary_path.unlink()
    result = {
        "status": "ok",
        "disposition": disposition,
        "decision_sha256": decision_sha256,
        "rules_path": str(output_path),
        "rules_sha256": _sha256_bytes(rendered),
        "repository_mutated": False,
    }
    render_state = {
        "schema": RENDER_STATE_SCHEMA,
        "project_root": current["project_root"],
        "git_root": current["git_root"],
        "project_scope": current["project_scope"],
        "spec_owner": current["spec_owner"],
        "requirements": current["requirements"],
        "design": current["design"],
        "spec_receipt": current["spec_receipt"],
        "manifest_path": _relative_private_path(private_root, manifest_path),
        "manifest_file_sha256": _sha256_bytes(_read_regular(manifest_path, "manifest")),
        "decision_path": _relative_private_path(private_root, decision_path),
        "decision_file_sha256": _sha256_bytes(_read_regular(decision_path, "decision")),
        "decision_sha256": decision_sha256,
        "disposition": disposition,
        "rules_path": _relative_private_path(private_root, output_path),
        "rules_sha256": _sha256_bytes(rendered),
        "repository_mutated": False,
    }
    _write_private_json(
        state_path,
        render_state,
        Path(str(current["git_root"])),
        private_root,
        write_failure_code=RENDER_STATE_PUBLICATION_INCOMPLETE,
        write_failure_message=(
            "matching render state publication is incomplete; rerun the exact render"
        ),
    )
    result["state_path"] = str(state_path)
    result["state_sha256"] = _sha256_bytes(_read_regular(state_path, "render state"))
    return result


def _validate_decision(
    decision: dict[str, object], manifest: dict[str, object]
) -> tuple[str, Optional[bytes], str, Optional[dict[str, str]]]:
    required = {
        "schema",
        "manifest_sha256",
        "disposition",
        "rationale",
        "evidence",
        "rules",
        "budget_exception",
        "ownership_approval",
    }
    if (
        set(decision) != required
        or decision.get("schema") != DECISION_SCHEMA
        or decision.get("manifest_sha256") != manifest.get("manifest_sha256")
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "decision does not match the current manifest"
        )
    disposition = decision.get("disposition")
    if disposition not in {"needed", "not-needed", "existing-sufficient"}:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "decision disposition is invalid"
        )
    rationale = decision.get("rationale")
    if (
        not isinstance(rationale, str)
        or not rationale.strip()
        or len(rationale.encode("utf-8")) > 2000
    ):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "decision rationale must be compact"
        )
    evidence = _validate_evidence(decision, manifest)
    evidence_paths = {item["path"] for item in evidence}
    if disposition == "needed":
        body, _ = _render_body(
            manifest,
            decision.get("rules"),
            decision.get("budget_exception"),
            evidence_paths,
        )
    else:
        if decision.get("rules") != [] or decision.get("budget_exception") is not None:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "non-generation decisions must not contain rules"
            )
        body = None
    approval = _validate_approval(decision.get("ownership_approval"))
    decision_sha256 = _sha256_bytes(_canonical_json(decision))
    return str(disposition), body, decision_sha256, approval


def _runtime_config_path(recorded: dict[str, object]) -> Path:
    config = dict(recorded["config_context"])
    digest = config.get("runtime_config_sha256")
    sources = list(config["sources"])
    if sources:
        entry = dict(sources[-1])
        if entry.get("sha256") == digest:
            return Path(str(entry["path"]))
    raise ProjectInstructionsError(
        "CONCURRENT_MODIFICATION", "runtime config provenance is incomplete"
    )


def _fresh_manifest(
    recorded: dict[str, object],
    permitted_backup_sha256: Optional[str] = None,
) -> dict[str, object]:
    receipt = dict(recorded["spec_receipt"])
    return _manifest(
        Path(str(recorded["project_root"])),
        str(recorded["spec_owner"]),
        str(dict(recorded["requirements"])["path"]),
        str(dict(recorded["design"])["path"]),
        Path(str(recorded["codex_home"])),
        Path(str(receipt["path"])),
        _runtime_config_path(recorded),
        permitted_backup_sha256,
    )


def _load_ownership(
    ownership_path: Path, private_root: Path
) -> Optional[dict[str, object]]:
    if _lstat_optional(ownership_path) is None:
        return None
    receipt = _load_private_json_object(
        ownership_path, "ownership receipt", private_root
    )
    return _validate_ownership_receipt(receipt)


def _validate_ownership_receipt(
    receipt: dict[str, object],
) -> dict[str, object]:
    required = {
        "schema",
        "status",
        "project_root",
        "git_root",
        "project_scope",
        "target_path",
        "manifest_sha256",
        "decision_sha256",
        "body_sha256",
    }
    if (
        set(receipt) != required
        or receipt.get("schema") != OWNERSHIP_SCHEMA
        or receipt.get("status") not in {"active", "retired"}
        or any(
            not isinstance(receipt.get(field), str) or not receipt.get(field)
            for field in ("project_root", "git_root", "project_scope", "target_path")
        )
        or any(
            not _valid_sha256(receipt.get(field))
            for field in (
                "manifest_sha256",
                "decision_sha256",
                "body_sha256",
            )
        )
    ):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "ownership receipt is invalid"
        )
    return receipt


def _ownership_matches(
    receipt: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
    *,
    status: str,
) -> bool:
    return (
        receipt.get("status") == status
        and receipt.get("project_root") == manifest.get("project_root")
        and receipt.get("git_root") == manifest.get("git_root")
        and receipt.get("project_scope") == manifest.get("project_scope")
        and receipt.get("target_path") == target.get("path")
        and receipt.get("manifest_sha256") == target.get("manifest_sha256")
        and receipt.get("decision_sha256") == target.get("decision_sha256")
        and receipt.get("body_sha256") == target.get("body_sha256")
    )


def _ownership_same_subject_and_body(
    receipt: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
) -> bool:
    """Return whether an active receipt can continue ownership safely."""
    return (
        receipt.get("status") == "active"
        and _ownership_same_subject(receipt, manifest, target)
        and receipt.get("body_sha256") == target.get("body_sha256")
    )


def _ownership_same_subject(
    receipt: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
) -> bool:
    return (
        receipt.get("project_root") == manifest.get("project_root")
        and receipt.get("git_root") == manifest.get("git_root")
        and receipt.get("project_scope") == manifest.get("project_scope")
        and receipt.get("target_path") == target.get("path")
    )


def _ownership_record(
    status: str,
    manifest: dict[str, object],
    target: dict[str, object],
) -> dict[str, object]:
    return {
        "schema": OWNERSHIP_SCHEMA,
        "status": status,
        "project_root": manifest["project_root"],
        "git_root": manifest["git_root"],
        "project_scope": manifest["project_scope"],
        "target_path": target["path"],
        "manifest_sha256": target["manifest_sha256"],
        "decision_sha256": target["decision_sha256"],
        "body_sha256": target["body_sha256"],
    }


def _safe_continuity_directory(path: Path) -> bool:
    metadata = _lstat_optional(path)
    return bool(
        metadata is not None
        and not stat.S_ISLNK(metadata.st_mode)
        and stat.S_ISDIR(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        and metadata.st_uid == os.getuid()
        and path.resolve(strict=False) == path
    )


def _read_continuity_json(
    path: Path,
    label: str,
    *,
    max_bytes: int = CONTINUITY_JSON_MAX_BYTES,
) -> tuple[dict[str, object], bytes]:
    metadata = _lstat_optional(path)
    if (
        metadata is None
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
        or metadata.st_size > max_bytes
    ):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", f"{label} is not safe continuity evidence"
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", f"{label} could not be read"
        ) from error
    confirmed = _lstat_optional(path)
    if (
        confirmed is None
        or not stat.S_ISREG(confirmed.st_mode)
        or stat.S_IMODE(confirmed.st_mode) != 0o600
        or confirmed.st_nlink != 1
        or confirmed.st_uid != os.getuid()
        or (confirmed.st_dev, confirmed.st_ino) != (metadata.st_dev, metadata.st_ino)
        or confirmed.st_size != metadata.st_size
        or confirmed.st_mtime_ns != metadata.st_mtime_ns
    ):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", f"{label} changed during validation"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", f"{label} is invalid"
        ) from error
    if not isinstance(value, dict):
        raise ProjectInstructionsError("OWNERSHIP_CONFLICT", f"{label} is invalid")
    return value, raw


def _valid_sealed_lifecycle(
    lifecycle: dict[str, object], manifest: dict[str, object]
) -> bool:
    git_head = lifecycle.get("git_head_at_prompt")
    write_epoch = lifecycle.get("write_epoch")
    return bool(
        set(lifecycle) == LIFECYCLE_REQUIRED_FIELDS
        and lifecycle.get("schema") == SEALED_LIFECYCLE_SCHEMA
        and lifecycle.get("phase") == "sealed"
        and lifecycle.get("project_scope") == manifest.get("project_scope")
        and isinstance(git_head, str)
        and 40 <= len(git_head) <= 64
        and all(character in "0123456789abcdef" for character in git_head)
        and _valid_sha256(lifecycle.get("turn_sha256"))
        and all(
            _valid_sha256(lifecycle.get(field))
            for field in (
                "receipt_sha256",
                "requirements_sha256",
                "design_sha256",
                "rules_sha256",
                "project_instructions_state_sha256",
            )
        )
        and lifecycle.get("rules_path") == SEALED_LIFECYCLE_RULES_PATH
        and type(lifecycle.get("project_instructions_reload_required")) is bool
        and type(write_epoch) is int
        and int(write_epoch) >= 0
        and lifecycle.get("planned_write_epoch") == write_epoch
        and lifecycle.get("waiver") is None
    )


def _ownership_subject(
    manifest: dict[str, object], target: dict[str, object]
) -> dict[str, str]:
    return {
        "project_root": str(manifest["project_root"]),
        "git_root": str(manifest["git_root"]),
        "project_scope": str(manifest["project_scope"]),
        "target_path": str(target["path"]),
    }


def _ownership_subject_key(
    manifest: dict[str, object], target: dict[str, object]
) -> str:
    return _sha256_bytes(_canonical_json(_ownership_subject(manifest, target)))


def _workspace_ownership_root(private_root: Path, git_root: Path) -> tuple[Path, Path]:
    if private_root.name == "project-instructions":
        session_root = private_root.parent
        workspace_root = session_root.parent
    else:
        workspace_root = private_root.parent
    if not _safe_continuity_directory(workspace_root):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership root is unsafe"
        )
    registry_root_path = workspace_root / WORKSPACE_OWNERSHIP_ROOT
    if _lstat_optional(registry_root_path) is None:
        try:
            temporary_root = Path(
                tempfile.mkdtemp(
                    prefix=f".{WORKSPACE_OWNERSHIP_ROOT}.",
                    dir=str(workspace_root),
                )
            )
        except OSError as error:
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "temporary ownership root could not be created",
            ) from error
        try:
            os.chmod(temporary_root, 0o700)
            _ensure_private_root(temporary_root, git_root)
            _write_private_json(
                temporary_root / WORKSPACE_OWNERSHIP_REGISTRY,
                _empty_workspace_registry(),
                git_root,
                temporary_root,
                write_failure_code="OWNERSHIP_CONFLICT",
                write_failure_message=(
                    "workspace ownership registry could not be initialized"
                ),
            )
            try:
                os.rename(temporary_root, registry_root_path)
                _sync_private_directory(
                    workspace_root,
                    "OWNERSHIP_CONFLICT",
                    "workspace ownership root could not be published",
                )
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "workspace ownership root could not be published",
                    ) from error
        finally:
            if _lstat_optional(temporary_root) is not None:
                for name in (WORKSPACE_OWNERSHIP_REGISTRY, PRIVATE_ROOT_MARKER):
                    child = temporary_root / name
                    metadata = _lstat_optional(child)
                    if metadata is not None:
                        if (
                            stat.S_ISLNK(metadata.st_mode)
                            or not stat.S_ISREG(metadata.st_mode)
                            or metadata.st_nlink != 1
                            or metadata.st_uid != os.getuid()
                        ):
                            raise ProjectInstructionsError(
                                "OWNERSHIP_CONFLICT",
                                "temporary ownership root is unsafe",
                            )
                        child.unlink()
                temporary_root.rmdir()
    registry_root = _ensure_private_root(
        registry_root_path,
        git_root,
    )
    _load_workspace_registry(registry_root)
    return workspace_root, registry_root


def _empty_workspace_registry() -> dict[str, object]:
    return {
        "schema": WORKSPACE_OWNERSHIP_SCHEMA,
        "generation": 0,
        "entries": {},
    }


def _validate_workspace_registry(
    registry: dict[str, object],
) -> dict[str, object]:
    generation = registry.get("generation")
    entries = registry.get("entries")
    if (
        set(registry) != {"schema", "generation", "entries"}
        or registry.get("schema") != WORKSPACE_OWNERSHIP_SCHEMA
        or type(generation) is not int
        or int(generation) < 0
        or not isinstance(entries, dict)
    ):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is invalid"
        )
    highest_generation = 0
    for key, value in entries.items():
        if not _valid_sha256(key) or not isinstance(value, dict):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "workspace ownership registry is invalid"
            )
        entry_generation = value.get("generation")
        status = value.get("status")
        target_sha256 = value.get("target_sha256")
        receipt_value = value.get("ownership")
        source_state_sha256 = value.get("source_state_sha256")
        if (
            set(value)
            != {
                "generation",
                "project_root",
                "git_root",
                "project_scope",
                "target_path",
                "status",
                "target_sha256",
                "ownership",
                "source_state_sha256",
            }
            or type(entry_generation) is not int
            or int(entry_generation) <= 0
            or int(entry_generation) > int(generation)
            or status not in {"active", "blocked", "pending", "retired"}
            or (
                status in {"active", "blocked", "pending"}
                and not _valid_sha256(target_sha256)
            )
            or (
                status == "retired"
                and target_sha256 is not None
                and not _valid_sha256(target_sha256)
            )
            or (
                status == "blocked"
                and (receipt_value is not None or source_state_sha256 is not None)
            )
            or (
                status != "blocked"
                and (
                    not isinstance(receipt_value, dict)
                    or not _valid_sha256(source_state_sha256)
                )
            )
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "workspace ownership registry is invalid"
            )
        subject = {
            "project_root": value.get("project_root"),
            "git_root": value.get("git_root"),
            "project_scope": value.get("project_scope"),
            "target_path": value.get("target_path"),
        }
        if (
            any(not isinstance(item, str) or not item for item in subject.values())
            or _sha256_bytes(_canonical_json(subject)) != key
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "workspace ownership registry is invalid"
            )
        if status != "blocked":
            assert isinstance(receipt_value, dict)
            receipt = _validate_ownership_receipt(receipt_value)
            expected_receipt_status = "active" if status == "pending" else status
            if receipt.get("status") != expected_receipt_status or any(
                receipt.get(field) != item for field, item in subject.items()
            ):
                raise ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "workspace ownership registry is invalid",
                )
        highest_generation = max(highest_generation, int(entry_generation))
    if highest_generation > int(generation):
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is invalid"
        )
    return registry


def _load_workspace_registry(registry_root: Path) -> dict[str, object]:
    path = registry_root / WORKSPACE_OWNERSHIP_REGISTRY
    if _lstat_optional(path) is None:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is missing"
        )
    value, _ = _read_continuity_json(
        path,
        "workspace ownership registry",
        max_bytes=WORKSPACE_REGISTRY_MAX_BYTES,
    )
    return _validate_workspace_registry(value)


def _publish_workspace_registry_locked(
    registry: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
    receipt: dict[str, object],
    target_sha256: object,
    source_state_sha256: str,
    registry_root: Path,
) -> dict[str, object]:
    generation = int(registry["generation"]) + 1
    subject = _ownership_subject(manifest, target)
    entry: dict[str, object] = {
        "generation": generation,
        **subject,
        "status": receipt["status"],
        "target_sha256": target_sha256,
        "ownership": receipt,
        "source_state_sha256": source_state_sha256,
    }
    updated_entries = dict(registry["entries"])
    updated_entries[_ownership_subject_key(manifest, target)] = entry
    updated = {
        "schema": WORKSPACE_OWNERSHIP_SCHEMA,
        "generation": generation,
        "entries": updated_entries,
    }
    _validate_workspace_registry(updated)
    if len(_stable_json(updated)) > WORKSPACE_REGISTRY_MAX_BYTES:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is too large"
        )
    _write_private_json(
        registry_root / WORKSPACE_OWNERSHIP_REGISTRY,
        updated,
        Path(str(manifest["git_root"])),
        registry_root,
        write_failure_code="OWNERSHIP_CONFLICT",
        write_failure_message="workspace ownership registry could not be published",
    )
    return updated


def _publish_workspace_blocked_locked(
    registry: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
    registry_root: Path,
) -> dict[str, object]:
    """Persist a one-time failed bootstrap without reusable authority."""

    generation = int(registry["generation"]) + 1
    subject = _ownership_subject(manifest, target)
    entry: dict[str, object] = {
        "generation": generation,
        **subject,
        "status": "blocked",
        "target_sha256": target.get("sha256"),
        "ownership": None,
        "source_state_sha256": None,
    }
    updated_entries = dict(registry["entries"])
    updated_entries[_ownership_subject_key(manifest, target)] = entry
    updated = {
        "schema": WORKSPACE_OWNERSHIP_SCHEMA,
        "generation": generation,
        "entries": updated_entries,
    }
    _validate_workspace_registry(updated)
    if len(_stable_json(updated)) > WORKSPACE_REGISTRY_MAX_BYTES:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is too large"
        )
    _write_private_json(
        registry_root / WORKSPACE_OWNERSHIP_REGISTRY,
        updated,
        Path(str(manifest["git_root"])),
        registry_root,
        write_failure_code="OWNERSHIP_CONFLICT",
        write_failure_message="workspace ownership registry could not be published",
    )
    return updated


def _publish_workspace_pending_locked(
    registry: dict[str, object],
    manifest: dict[str, object],
    target: dict[str, object],
    receipt: dict[str, object],
    source_state_sha256: str,
    registry_root: Path,
) -> dict[str, object]:
    """Bind an interrupted apply to its exact durable state for recovery."""

    generation = int(registry["generation"]) + 1
    subject = _ownership_subject(manifest, target)
    entry: dict[str, object] = {
        "generation": generation,
        **subject,
        "status": "pending",
        "target_sha256": target.get("sha256"),
        "ownership": receipt,
        "source_state_sha256": source_state_sha256,
    }
    updated_entries = dict(registry["entries"])
    updated_entries[_ownership_subject_key(manifest, target)] = entry
    updated = {
        "schema": WORKSPACE_OWNERSHIP_SCHEMA,
        "generation": generation,
        "entries": updated_entries,
    }
    _validate_workspace_registry(updated)
    if len(_stable_json(updated)) > WORKSPACE_REGISTRY_MAX_BYTES:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "workspace ownership registry is too large"
        )
    _write_private_json(
        registry_root / WORKSPACE_OWNERSHIP_REGISTRY,
        updated,
        Path(str(manifest["git_root"])),
        registry_root,
        write_failure_code="OWNERSHIP_CONFLICT",
        write_failure_message="workspace ownership registry could not be published",
    )
    return updated


def _sealed_ownership_event(
    session_root: Path,
    manifest: dict[str, object],
) -> tuple[str, Optional[dict[str, object]], Optional[str]]:
    lifecycle_path = session_root / "lifecycle.json"
    if _lstat_optional(lifecycle_path) is None:
        return "irrelevant", None, None
    if not _safe_continuity_directory(session_root):
        return "invalid", None, None
    try:
        lifecycle, _ = _read_continuity_json(lifecycle_path, "prior lifecycle state")
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "invalid", None, None
    lifecycle_scope = lifecycle.get("project_scope")
    if isinstance(lifecycle_scope, str) and lifecycle_scope != manifest.get(
        "project_scope"
    ):
        return "foreign", None, None
    if lifecycle.get("phase") != "sealed":
        if (
            _lstat_optional(session_root / "project-instructions" / "state.json")
            is not None
        ):
            return "invalid", None, None
        return "irrelevant", None, None
    if not _valid_sealed_lifecycle(lifecycle, manifest):
        return "invalid", None, None
    private_root = session_root / "project-instructions"
    if not _safe_continuity_directory(private_root):
        return "invalid", None, None
    target = dict(manifest["target"])
    try:
        state, state_raw = _read_continuity_json(
            private_root / "state.json", "prior project-instructions state"
        )
        requirements = state.get("requirements")
        design = state.get("design")
        outcome = state.get("outcome")
        if outcome in ACTIVE_OWNERSHIP_OUTCOMES:
            ownership_status = "active"
        elif outcome == "retired":
            ownership_status = "retired"
        else:
            return "irrelevant", None, None
        if (
            _sha256_bytes(state_raw)
            != lifecycle.get("project_instructions_state_sha256")
            or set(state) != STATE_REQUIRED_FIELDS
            or state.get("schema") != STATE_SCHEMA
            or state.get("private_root") != str(private_root)
            or state.get("ownership_path") != "ownership.json"
            or not isinstance(requirements, dict)
            or requirements.get("sha256") != lifecycle.get("requirements_sha256")
            or not isinstance(design, dict)
            or design.get("sha256") != lifecycle.get("design_sha256")
            or lifecycle.get("project_instructions_reload_required")
            is not state.get("reload_required")
            or not _valid_sha256(state.get("ownership_file_sha256"))
            or (
                ownership_status == "active"
                and (
                    not _valid_sha256(state.get("target_sha256"))
                    or state.get("active_instruction_path") != state.get("target_path")
                )
            )
            or (
                ownership_status == "retired"
                and state.get("target_sha256") is not None
                and not _valid_sha256(state.get("target_sha256"))
            )
        ):
            return "invalid", None, None
        state_subject = {
            "project_root": state.get("project_root"),
            "git_root": state.get("git_root"),
            "project_scope": state.get("project_scope"),
            "target_path": state.get("target_path"),
        }
        if state_subject != _ownership_subject(manifest, target):
            return "irrelevant", None, None
        ownership, ownership_raw = _read_continuity_json(
            private_root / "ownership.json", "prior ownership receipt"
        )
        receipt = _validate_ownership_receipt(ownership)
        if (
            _sha256_bytes(ownership_raw) != state.get("ownership_file_sha256")
            or receipt.get("status") != ownership_status
            or not _ownership_same_subject(receipt, manifest, target)
        ):
            return "invalid", None, None
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "invalid", None, None
    if ownership_status == "retired":
        return "retired", receipt, _sha256_bytes(state_raw)
    if state.get("target_sha256") != target.get("sha256") or not _ownership_matches(
        receipt, manifest, target, status="active"
    ):
        return "conflict", receipt, _sha256_bytes(state_raw)
    return "active", receipt, _sha256_bytes(state_raw)


def _bootstrap_workspace_ownership(
    workspace_root: Path,
    session_root: Path,
    manifest: dict[str, object],
) -> tuple[str, Optional[dict[str, object]], Optional[str]]:
    try:
        entries = list(workspace_root.iterdir())
    except OSError:
        return "blocked", None, None
    candidates: list[tuple[dict[str, object], str]] = []
    pending_candidates: list[tuple[dict[str, object], str]] = []
    for entry in entries:
        if entry == session_root or entry.name == WORKSPACE_OWNERSHIP_ROOT:
            continue
        metadata = _lstat_optional(entry)
        if metadata is None:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            return "blocked", None, None
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        status, receipt, source_state_sha256 = _sealed_ownership_event(entry, manifest)
        if status == "foreign":
            # A valid lifecycle for another selected project is neither
            # ownership evidence nor malformed evidence for this subject.
            continue
        if status == "retired":
            return "blocked", None, None
        if status == "active":
            assert receipt is not None and source_state_sha256 is not None
            candidates.append((receipt, source_state_sha256))
            continue
        pending_status, pending_receipt, pending_state_sha256 = (
            _pending_workspace_ownership_event(entry, manifest)
        )
        if pending_status == "pending":
            assert pending_receipt is not None and pending_state_sha256 is not None
            pending_candidates.append((pending_receipt, pending_state_sha256))
        elif status in {"invalid", "conflict"} or pending_status == "invalid":
            return "blocked", None, None
    if not candidates:
        if len(pending_candidates) == 1:
            return "pending", pending_candidates[0][0], pending_candidates[0][1]
        if pending_candidates:
            return "blocked", None, None
        return "none", None, None
    canonical_receipt = _canonical_json(candidates[0][0])
    if any(
        _canonical_json(receipt) != canonical_receipt
        for receipt, _ in candidates + pending_candidates
    ):
        return "blocked", None, None
    return "active", candidates[0][0], candidates[0][1]


def _current_active_state_evidence(
    private_root: Path,
    manifest: dict[str, object],
    target: dict[str, object],
    current: dict[str, object],
) -> tuple[Optional[str], bool]:
    """Validate a current-session state left before registry publication."""

    state_path = private_root / "state.json"
    ownership_path = private_root / "ownership.json"
    try:
        state, state_raw = _read_continuity_json(
            state_path, "current project-instructions state"
        )
        ownership, ownership_raw = _read_continuity_json(
            ownership_path, "current ownership receipt"
        )
        receipt = _validate_ownership_receipt(ownership)
        manifest_path = _private_member(
            private_root,
            private_root / str(state.get("manifest_path")),
            "manifest",
        )
        decision_path = _private_member(
            private_root,
            private_root / str(state.get("decision_path")),
            "decision",
        )
        recorded, manifest_raw = _read_continuity_json(
            manifest_path, "current manifest"
        )
        decision, decision_raw = _read_continuity_json(
            decision_path, "current decision"
        )
        _validate_manifest_shape(recorded)
        _disposition, _body, decision_sha256, approval = _validate_decision(
            decision, recorded
        )
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return None, False
    state_subject = {
        "project_root": state.get("project_root"),
        "git_root": state.get("git_root"),
        "project_scope": state.get("project_scope"),
        "target_path": state.get("target_path"),
    }
    if (
        set(state) != STATE_REQUIRED_FIELDS
        or state.get("schema") != STATE_SCHEMA
        or state_subject != _ownership_subject(manifest, target)
        or state.get("private_root") != str(private_root)
        or state.get("ownership_path") != "ownership.json"
        or state.get("outcome") not in ACTIVE_OWNERSHIP_OUTCOMES
        or state.get("target_sha256") != target.get("sha256")
        or state.get("active_instruction_path") != target.get("path")
        or state.get("ownership_file_sha256") != _sha256_bytes(ownership_raw)
        or state.get("manifest_file_sha256") != _sha256_bytes(manifest_raw)
        or state.get("decision_file_sha256") != _sha256_bytes(decision_raw)
        or state.get("input_manifest_sha256") != recorded.get("manifest_sha256")
        or state.get("decision_sha256") != decision_sha256
        or receipt != current
        or not _ownership_matches(receipt, manifest, target, status="active")
    ):
        return None, False
    recorded_target = dict(recorded["target"])
    adoption_proven = bool(
        state.get("outcome") == "adopted"
        and approval is not None
        and approval.get("action") == "adopt"
        and approval.get("target_sha256") == recorded_target.get("sha256")
        and recorded_target.get("sha256") == target.get("sha256")
    )
    return _sha256_bytes(state_raw), adoption_proven


def _pending_workspace_ownership_event(
    session_root: Path,
    manifest: dict[str, object],
) -> tuple[str, Optional[dict[str, object]], Optional[str]]:
    """Read an exact completed apply whose registry publication is pending."""

    private_root = session_root / "project-instructions"
    state_path = private_root / "state.json"
    if _lstat_optional(state_path) is None:
        return "irrelevant", None, None
    if not _safe_continuity_directory(session_root) or not _safe_continuity_directory(
        private_root
    ):
        return "invalid", None, None
    target = dict(manifest["target"])
    try:
        ownership, _ownership_raw = _read_continuity_json(
            private_root / "ownership.json", "pending ownership receipt"
        )
        receipt = _validate_ownership_receipt(ownership)
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "invalid", None, None
    state_sha256, _adoption_proven = _current_active_state_evidence(
        private_root, manifest, target, receipt
    )
    if state_sha256 is None:
        return "invalid", None, None
    return "pending", receipt, state_sha256


def retention_disposition(
    session_root: Path, workspace_root: Path
) -> tuple[str, Optional[int], Optional[str]]:
    """Classify historical evidence using the canonical ownership registry.

    This is an internal maintenance boundary. It never creates a registry and
    returns a closed disposition so missing, pending, legacy, or malformed
    evidence remains protected.
    """

    session_root = Path(os.path.abspath(session_root.expanduser()))
    workspace_root = Path(os.path.abspath(workspace_root.expanduser()))
    try:
        session_root.relative_to(workspace_root)
    except ValueError:
        return "unsafe", None, None
    if session_root.parent != workspace_root or not _safe_continuity_directory(
        session_root
    ):
        return "unsafe", None, None
    private_root = session_root / "project-instructions"
    metadata = _lstat_optional(private_root)
    if metadata is None:
        lifecycle_path = session_root / "lifecycle.json"
        if _lstat_optional(lifecycle_path) is None:
            return "deletable", None, None
        try:
            lifecycle, _lifecycle_raw = _read_continuity_json(
                lifecycle_path, "historical lifecycle state"
            )
        except (OSError, ProjectInstructionsError, TypeError, ValueError):
            return "unsafe", None, None
        if lifecycle.get("phase") == "sealed":
            return "unsafe", None, None
        return "deletable", None, None
    if not _safe_continuity_directory(private_root):
        return "unsafe", None, None
    state_path = private_root / "state.json"
    if _lstat_optional(state_path) is None:
        try:
            meaningful = {
                entry.name
                for entry in private_root.iterdir()
                if entry.name
                not in {PRIVATE_ROOT_MARKER, ".render.lock", ".ownership.lock"}
            }
        except OSError:
            return "unsafe", None, None
        return ("pending", None, None) if meaningful else ("deletable", None, None)
    try:
        state, state_raw = _read_continuity_json(
            state_path, "historical project-instructions state"
        )
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "unsafe", None, None
    digest_fields = (
        "manifest_file_sha256",
        "decision_file_sha256",
        "input_manifest_sha256",
        "current_manifest_sha256",
        "decision_sha256",
    )
    requirements = state.get("requirements")
    design = state.get("design")
    spec_receipt = state.get("spec_receipt")
    if (
        set(state) != STATE_REQUIRED_FIELDS
        or state.get("schema") != STATE_SCHEMA
        or state.get("private_root") != str(private_root)
        or state.get("spec_owner") != "maintain-project-specs"
        or any(
            not isinstance(state.get(field), str) or not state.get(field)
            for field in (
                "project_root",
                "git_root",
                "project_scope",
                "codex_home",
                "target_path",
            )
        )
        or state.get("manifest_path") != "manifest.json"
        or state.get("decision_path") != "decision.json"
        or state.get("ownership_path") != "ownership.json"
        or not all(_valid_sha256(state.get(field)) for field in digest_fields)
        or not _valid_path_digest(requirements)
        or not _valid_path_digest(design)
        or not _valid_path_digest(spec_receipt)
        or type(state.get("reload_required")) is not bool
    ):
        return "unsafe", None, None
    try:
        _manifest, manifest_raw = _read_continuity_json(
            private_root / "manifest.json", "historical manifest"
        )
        _decision, decision_raw = _read_continuity_json(
            private_root / "decision.json", "historical decision"
        )
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "unsafe", None, None
    if state.get("manifest_file_sha256") != _sha256_bytes(manifest_raw) or state.get(
        "decision_file_sha256"
    ) != _sha256_bytes(decision_raw):
        return "unsafe", None, None
    outcome = state.get("outcome")
    try:
        lifecycle, _lifecycle_raw = _read_continuity_json(
            session_root / "lifecycle.json", "historical lifecycle state"
        )
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "unsafe", None, None
    if lifecycle.get("phase") != "sealed":
        return "pending", None, None
    if (
        not _valid_sealed_lifecycle(
            lifecycle, {"project_scope": state.get("project_scope")}
        )
        or lifecycle.get("receipt_sha256") != spec_receipt.get("sha256")
        or lifecycle.get("requirements_sha256") != requirements.get("sha256")
        or lifecycle.get("design_sha256") != design.get("sha256")
        or lifecycle.get("project_instructions_reload_required")
        is not state.get("reload_required")
        or lifecycle.get("project_instructions_state_sha256")
        != _sha256_bytes(state_raw)
    ):
        return "unsafe", None, None
    if outcome == "not-needed":
        if (
            state.get("ownership_file_sha256") is not None
            or state.get("target_sha256") is not None
            or state.get("active_instruction_path") is not None
            or state.get("reload_required") is not False
        ):
            return "unsafe", None, None
        return "deletable", None, None
    if outcome not in ACTIVE_OWNERSHIP_OUTCOMES | {"retired"}:
        return "pending", None, None
    ownership_path = private_root / "ownership.json"
    try:
        ownership, ownership_raw = _read_continuity_json(
            ownership_path, "historical ownership receipt"
        )
        receipt = _validate_ownership_receipt(ownership)
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "legacy-required", None, None
    if (
        state.get("ownership_path") != "ownership.json"
        or state.get("ownership_file_sha256") != _sha256_bytes(ownership_raw)
        or receipt.get("status") != ("retired" if outcome == "retired" else "active")
    ):
        return "unsafe", None, None
    registry_root = workspace_root / WORKSPACE_OWNERSHIP_ROOT
    if not _safe_continuity_directory(registry_root):
        return "legacy-required", None, None
    try:
        with render_lock(
            registry_root,
            lock_name=".ownership.lock",
            label="workspace ownership retention",
        ):
            registry = _load_workspace_registry(registry_root)
            subject = {
                "project_root": state.get("project_root"),
                "git_root": state.get("git_root"),
                "project_scope": state.get("project_scope"),
                "target_path": state.get("target_path"),
            }
            key = _sha256_bytes(_canonical_json(subject))
            entry = dict(registry["entries"]).get(key)
            generation = int(registry["generation"])
            registry_sha256 = _sha256_bytes(_canonical_json(registry))
    except (OSError, ProjectInstructionsError, TypeError, ValueError):
        return "unsafe", None, None
    if not isinstance(entry, dict):
        return "legacy-required", generation, registry_sha256
    status = entry.get("status")
    if status == "pending":
        return "pending", generation, registry_sha256
    if status == "blocked":
        return "deletable", generation, registry_sha256
    if status not in {"active", "retired"}:
        return "unsafe", generation, registry_sha256
    if (
        entry.get("source_state_sha256") != _sha256_bytes(state_raw)
        or entry.get("ownership") != receipt
    ):
        return "legacy-required", generation, registry_sha256
    return "deletable", generation, registry_sha256


def carry_forward_ownership(manifest: dict[str, object], private_root: Path) -> str:
    """Import exact registry authority or one unanimous legacy bootstrap."""

    _validate_manifest_shape(manifest)
    private_root = _ensure_private_root(private_root, Path(str(manifest["git_root"])))
    target = dict(manifest["target"])
    current_path = private_root / "ownership.json"
    current = _load_ownership(current_path, private_root)
    if target.get("file_status") != "managed":
        if current is not None:
            if current.get("status") == "retired" and _ownership_same_subject(
                current, manifest, target
            ):
                return "not-applicable"
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "current ownership receipt is stale"
            )
        return "not-applicable"
    workspace_root, registry_root = _workspace_ownership_root(
        private_root, Path(str(manifest["git_root"]))
    )
    session_root = (
        private_root.parent
        if private_root.name == "project-instructions"
        else private_root
    )
    with render_lock(
        registry_root,
        lock_name=".ownership.lock",
        label="workspace ownership publication",
    ):
        registry = _load_workspace_registry(registry_root)
        subject_key = _ownership_subject_key(manifest, target)
        entry = dict(registry["entries"]).get(subject_key)
        if entry is not None:
            assert isinstance(entry, dict)
            registry_status = entry.get("status")
            if registry_status == "pending":
                if current is None:
                    return "unproven"
                registry_value = entry.get("ownership")
                assert isinstance(registry_value, dict)
                state_sha256, _adoption_proven = _current_active_state_evidence(
                    private_root, manifest, target, current
                )
                if (
                    current != registry_value
                    or state_sha256 is None
                    or state_sha256 != entry.get("source_state_sha256")
                    or entry.get("target_sha256") != target.get("sha256")
                ):
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "current ownership conflicts with pending publication",
                    )
                _publish_workspace_registry_locked(
                    registry,
                    manifest,
                    target,
                    current,
                    target.get("sha256"),
                    state_sha256,
                    registry_root,
                )
                return "current"
            if registry_status == "blocked":
                if current is None:
                    return "unproven"
                state_sha256, adoption_proven = _current_active_state_evidence(
                    private_root, manifest, target, current
                )
                if state_sha256 is None or not adoption_proven:
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "current ownership conflicts with workspace authority",
                    )
                _publish_workspace_registry_locked(
                    registry,
                    manifest,
                    target,
                    current,
                    target.get("sha256"),
                    state_sha256,
                    registry_root,
                )
                return "current"
            registry_value = entry.get("ownership")
            assert isinstance(registry_value, dict)
            registry_receipt = dict(registry_value)
            registry_exact = bool(
                registry_status == "active"
                and entry.get("target_sha256") == target.get("sha256")
                and _ownership_matches(
                    registry_receipt, manifest, target, status="active"
                )
            )
            current_continuity = bool(
                current is not None
                and registry_status == "active"
                and current == registry_receipt
                and _ownership_same_subject_and_body(current, manifest, target)
            )
            if not registry_exact and not current_continuity:
                if current is not None:
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "current ownership conflicts with workspace authority",
                    )
                return "unproven"
            receipt = current if current_continuity else registry_receipt
            if current is not None:
                if receipt != current:
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "current ownership conflicts with workspace authority",
                    )
                return "current"
            _write_private_json(
                current_path,
                receipt,
                Path(str(manifest["git_root"])),
                private_root,
            )
            return "carried-forward"
        bootstrap_status, receipt, source_state_sha256 = _bootstrap_workspace_ownership(
            workspace_root,
            session_root,
            manifest,
        )
        if current is not None:
            if not (
                _ownership_matches(current, manifest, target, status="active")
                or _ownership_same_subject_and_body(current, manifest, target)
            ):
                raise ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT", "current ownership receipt is stale"
                )
            current_state_sha256, _adoption_proven = _current_active_state_evidence(
                private_root, manifest, target, current
            )
            if bootstrap_status == "blocked" or (
                receipt is not None and receipt != current
            ):
                _publish_workspace_blocked_locked(
                    registry, manifest, target, registry_root
                )
                raise ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "current ownership conflicts with sealed history",
                )
            if bootstrap_status == "pending":
                if (
                    current_state_sha256 is None
                    or current_state_sha256 != source_state_sha256
                ):
                    _publish_workspace_blocked_locked(
                        registry, manifest, target, registry_root
                    )
                    raise ProjectInstructionsError(
                        "OWNERSHIP_CONFLICT",
                        "current ownership conflicts with pending publication",
                    )
                _publish_workspace_registry_locked(
                    registry,
                    manifest,
                    target,
                    current,
                    target.get("sha256"),
                    current_state_sha256,
                    registry_root,
                )
                return "current"
            if receipt is not None and source_state_sha256 is not None:
                _publish_workspace_registry_locked(
                    registry,
                    manifest,
                    target,
                    receipt,
                    target.get("sha256"),
                    source_state_sha256,
                    registry_root,
                )
                return "current"
            if current_state_sha256 is not None:
                _publish_workspace_registry_locked(
                    registry,
                    manifest,
                    target,
                    current,
                    target.get("sha256"),
                    current_state_sha256,
                    registry_root,
                )
                return "current"
            _publish_workspace_blocked_locked(registry, manifest, target, registry_root)
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "current ownership lacks durable publication evidence",
            )
        if (
            bootstrap_status not in {"active", "pending"}
            or receipt is None
            or source_state_sha256 is None
        ):
            _publish_workspace_blocked_locked(registry, manifest, target, registry_root)
            return "unproven"
        if bootstrap_status == "pending":
            _publish_workspace_pending_locked(
                registry,
                manifest,
                target,
                receipt,
                source_state_sha256,
                registry_root,
            )
            return "unproven"
        _publish_workspace_registry_locked(
            registry,
            manifest,
            target,
            receipt,
            target.get("sha256"),
            source_state_sha256,
            registry_root,
        )
        _write_private_json(
            current_path,
            receipt,
            Path(str(manifest["git_root"])),
            private_root,
        )
        return "carried-forward"


def _select_outcome(
    manifest: dict[str, object],
    disposition: str,
    body: Optional[bytes],
    marker_manifest_sha256: str,
    marker_decision_sha256: str,
    approval: Optional[dict[str, str]],
    ownership: Optional[dict[str, object]],
) -> str:
    target = dict(manifest["target"])
    status = str(target["file_status"])
    target_digest = target.get("sha256")
    validate_render_disposition(manifest, disposition, approval)
    if disposition == "existing-sufficient":
        return "existing-sufficient"
    if disposition == "not-needed":
        return "retired" if status == "managed" else "not-needed"
    assert body is not None
    if status == "missing":
        return "created"
    if status == "human-owned":
        return "attached"
    has_receipt = ownership is not None and _ownership_matches(
        ownership, manifest, target, status="active"
    )
    marker_only_receipt_drift = (
        ownership is not None
        and not has_receipt
        and _ownership_same_subject_and_body(ownership, manifest, target)
    )
    if ownership is not None and not has_receipt and not marker_only_receipt_drift:
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT", "ownership receipt is stale"
        )
    has_continuity = has_receipt or marker_only_receipt_drift
    if not has_continuity:
        if (
            approval is None
            or approval["action"] != "adopt"
            or approval["target_sha256"] != target_digest
        ):
            raise ProjectInstructionsError(
                "ADOPTION_APPROVAL_REQUIRED",
                "managed project AGENTS.md requires exact-digest adoption approval",
            )
    elif approval is not None:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "adoption approval is unnecessary"
        )
    if (
        target.get("body_sha256") == _sha256_bytes(body)
        and target.get("manifest_sha256") == marker_manifest_sha256
        and target.get("decision_sha256") == marker_decision_sha256
    ):
        return "existing-sufficient" if has_continuity else "adopted"
    return "refreshed"


def _validate_final_transition(
    initial: dict[str, object],
    final: dict[str, object],
    body: Optional[bytes],
    marker_manifest_sha256: str,
    marker_decision_sha256: str,
    outcome: str,
) -> None:
    stable = {
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
    }
    if any(initial.get(field) != final.get(field) for field in stable):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project inputs changed"
        )
    if outcome in {"not-needed", "existing-sufficient", "adopted"}:
        if final.get("target") != initial.get("target") or final.get(
            "active_project_instruction"
        ) != initial.get("active_project_instruction"):
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project instructions changed"
            )
        return
    final_target = dict(final["target"])
    initial_target = dict(initial["target"])
    target_path = str(initial_target["path"])
    prefix_bytes = int(initial_target["managed_prefix_bytes"])
    prefix_sha256 = initial_target.get("managed_prefix_sha256")
    if prefix_sha256 is None:
        prefix_sha256 = _sha256_bytes(b"")
    if outcome == "retired":
        if prefix_bytes == 0:
            expected_target = {
                "path": target_path,
                "file_status": "missing",
                "sha256": None,
                "marker_version": None,
                "manifest_sha256": None,
                "decision_sha256": None,
                "body_sha256": None,
                "managed_prefix_bytes": 0,
                "managed_prefix_sha256": None,
                "active_path": None,
                "active_kind": None,
                "parent_device": initial_target["parent_device"],
                "parent_inode": initial_target["parent_inode"],
            }
            active_valid = final.get("active_project_instruction") is None
        else:
            expected_target = {
                "path": target_path,
                "file_status": "human-owned",
                "sha256": prefix_sha256,
                "marker_version": None,
                "manifest_sha256": None,
                "decision_sha256": None,
                "body_sha256": None,
                "managed_prefix_bytes": prefix_bytes,
                "managed_prefix_sha256": prefix_sha256,
                "active_path": target_path,
                "active_kind": "project-agents",
                "parent_device": initial_target["parent_device"],
                "parent_inode": initial_target["parent_inode"],
            }
            active = final.get("active_project_instruction")
            active_valid = (
                isinstance(active, dict)
                and active.get("path") == target_path
                and active.get("sha256") == prefix_sha256
            )
        if final_target != expected_target or not active_valid:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "retirement did not complete"
            )
        return
    assert body is not None
    active = final.get("active_project_instruction")
    if (
        final_target.get("path") != target_path
        or final_target.get("file_status") != "managed"
        or final_target.get("marker_version") != 3
        or final_target.get("manifest_sha256") != marker_manifest_sha256
        or final_target.get("decision_sha256") != marker_decision_sha256
        or final_target.get("body_sha256") != _sha256_bytes(body)
        or final_target.get("managed_prefix_bytes") != prefix_bytes
        or final_target.get("managed_prefix_sha256") != prefix_sha256
        or final_target.get("active_path") != target_path
        or final_target.get("active_kind") != "project-agents"
        or final_target.get("parent_device") != initial_target["parent_device"]
        or final_target.get("parent_inode") != initial_target["parent_inode"]
        or not isinstance(active, dict)
        or active.get("path") != target_path
        or active.get("sha256") != final_target.get("sha256")
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "generated project AGENTS.md is not exact"
        )


def _target_prefix(target: dict[str, object]) -> bytes:
    status = str(target["file_status"])
    if status == "missing":
        return b""
    content = _read_regular(Path(str(target["path"])), "project AGENTS.md")
    if _sha256_bytes(content) != target.get("sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project AGENTS.md changed"
        )
    if status == "human-owned":
        prefix = content
    else:
        parsed = _parse_generated(content)
        if parsed is None:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "project AGENTS.md ownership changed"
            )
        prefix = bytes(parsed["prefix"])
    if len(prefix) != target.get("managed_prefix_bytes") or _sha256_bytes(
        prefix
    ) != target.get("managed_prefix_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project AGENTS.md prefix changed"
        )
    return prefix


def _validate_current_apply_authority(
    registry: dict[str, object],
    registry_root: Path,
    workspace_root: Path,
    session_root: Path,
    private_root: Path,
    manifest: dict[str, object],
    target: dict[str, object],
    ownership: Optional[dict[str, object]],
) -> None:
    """Reject a current receipt superseded by shared or sealed authority."""

    if (
        target.get("file_status") != "managed"
        or ownership is None
        or ownership.get("status") != "active"
    ):
        return
    entry = dict(registry["entries"]).get(_ownership_subject_key(manifest, target))
    if entry is not None:
        assert isinstance(entry, dict)
        if entry.get("status") == "pending":
            registry_value = entry.get("ownership")
            assert isinstance(registry_value, dict)
            state_sha256, _adoption_proven = _current_active_state_evidence(
                private_root,
                manifest,
                target,
                ownership,
            )
            if (
                ownership == registry_value
                and state_sha256 is not None
                and state_sha256 == entry.get("source_state_sha256")
                and entry.get("target_sha256") == target.get("sha256")
            ):
                return
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "current ownership conflicts with pending publication",
            )
        if entry.get("status") == "blocked":
            state_sha256, adoption_proven = _current_active_state_evidence(
                private_root,
                manifest,
                target,
                ownership,
            )
            if state_sha256 is not None and adoption_proven:
                return
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "current ownership conflicts with workspace authority",
            )
        registry_value = entry.get("ownership")
        assert isinstance(registry_value, dict)
        registry_receipt = dict(registry_value)
        if not (
            entry.get("status") == "active"
            and registry_receipt == ownership
            and (
                (
                    entry.get("target_sha256") == target.get("sha256")
                    and _ownership_matches(ownership, manifest, target, status="active")
                )
                or _ownership_same_subject_and_body(ownership, manifest, target)
            )
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "current ownership conflicts with workspace authority",
            )
        return
    bootstrap_status, receipt, _source_state_sha256 = _bootstrap_workspace_ownership(
        workspace_root,
        session_root,
        manifest,
    )
    if bootstrap_status == "blocked" or (receipt is not None and receipt != ownership):
        _publish_workspace_blocked_locked(registry, manifest, target, registry_root)
        raise ProjectInstructionsError(
            "OWNERSHIP_CONFLICT",
            "current ownership conflicts with sealed history",
        )
    if receipt is None:
        state_sha256, _adoption_proven = _current_active_state_evidence(
            private_root, manifest, target, ownership
        )
        if state_sha256 is None:
            _publish_workspace_blocked_locked(registry, manifest, target, registry_root)
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "current ownership lacks durable publication evidence",
            )


def apply_decision(
    manifest_path: Path,
    decision_path: Path,
    ownership_path: Path,
    state_path: Path,
    private_root: Path,
) -> dict[str, object]:
    provisional_root = Path(os.path.abspath(private_root.expanduser()))
    manifest_path = _private_member(provisional_root, manifest_path, "manifest")
    decision_path = _private_member(provisional_root, decision_path, "decision")
    ownership_path = _private_member(provisional_root, ownership_path, "ownership")
    state_path = _private_member(provisional_root, state_path, "state")
    recorded = _load_private_json_object(manifest_path, "manifest", provisional_root)
    _validate_manifest_shape(recorded)
    private_root = _ensure_private_root(
        provisional_root, Path(str(recorded["git_root"]))
    )
    workspace_root, registry_root = _workspace_ownership_root(
        private_root, Path(str(recorded["git_root"]))
    )
    session_root = (
        private_root.parent
        if private_root.name == "project-instructions"
        else private_root
    )
    with render_lock(
        registry_root,
        lock_name=".ownership.lock",
        label="workspace ownership publication",
    ):
        registry = _load_workspace_registry(registry_root)
        return _apply_decision_locked(
            manifest_path,
            decision_path,
            ownership_path,
            state_path,
            private_root,
            recorded,
            workspace_root,
            session_root,
            registry,
            registry_root,
        )


def _apply_decision_locked(
    manifest_path: Path,
    decision_path: Path,
    ownership_path: Path,
    state_path: Path,
    private_root: Path,
    recorded: dict[str, object],
    workspace_root: Path,
    session_root: Path,
    registry: dict[str, object],
    registry_root: Path,
) -> dict[str, object]:
    current = _fresh_manifest(recorded)
    if current != recorded:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project inputs changed after inspection"
        )
    decision = _load_private_json_object(decision_path, "decision", private_root)
    disposition, body, decision_sha256, approval = _validate_decision(decision, current)
    marker_manifest_sha256 = _generation_manifest_sha256(current)
    marker_decision_sha256 = (
        _generation_decision_sha256(
            disposition,
            body,
            _validate_evidence(decision, current),
        )
        if body is not None
        else ""
    )
    ownership = _load_ownership(ownership_path, private_root)
    target = dict(current["target"])
    _validate_current_apply_authority(
        registry,
        registry_root,
        workspace_root,
        session_root,
        private_root,
        current,
        target,
        ownership,
    )
    outcome = _select_outcome(
        current,
        disposition,
        body,
        marker_manifest_sha256,
        marker_decision_sha256,
        approval,
        ownership,
    )
    target_path = Path(str(target["path"]))
    prefix = _target_prefix(target)
    parent_identity = (
        int(target["parent_device"]),
        int(target["parent_inode"]),
    )
    retained_backup_sha256: Optional[str] = None
    if outcome == "created":
        assert body is not None
        _exclusive_create(
            target_path,
            _generated_content(
                body, marker_manifest_sha256, marker_decision_sha256, prefix
            ),
            parent_identity,
        )
    elif outcome in {"attached", "refreshed"}:
        assert body is not None and isinstance(target.get("sha256"), str)
        retained_backup_sha256 = str(target["sha256"])
        _guarded_replace(
            target_path,
            str(target["sha256"]),
            _generated_content(
                body, marker_manifest_sha256, marker_decision_sha256, prefix
            ),
            parent_identity,
            retain_backup=True,
        )
    elif outcome == "retired":
        assert isinstance(target.get("sha256"), str)
        retained_backup_sha256 = str(target["sha256"])
        if prefix:
            _guarded_replace(
                target_path,
                str(target["sha256"]),
                prefix,
                parent_identity,
                retain_backup=True,
            )
        else:
            _guarded_delete(
                target_path,
                str(target["sha256"]),
                parent_identity,
                retain_backup=True,
            )
    final = _fresh_manifest(current, retained_backup_sha256)
    _validate_final_transition(
        current,
        final,
        body,
        marker_manifest_sha256,
        marker_decision_sha256,
        outcome,
    )
    final_target = dict(final["target"])
    published_receipt: Optional[dict[str, object]] = None
    registry_target = final_target
    registry_target_sha256: object = final_target.get("sha256")
    if outcome in {"created", "attached", "refreshed", "adopted"} or (
        outcome == "existing-sufficient"
        and final_target.get("file_status") == "managed"
    ):
        published_receipt = _ownership_record("active", final, final_target)
        _write_private_json(
            ownership_path,
            published_receipt,
            Path(str(current["git_root"])),
            private_root,
        )
    elif outcome == "retired":
        published_receipt = _ownership_record("retired", current, target)
        registry_target = target
        _write_private_json(
            ownership_path,
            published_receipt,
            Path(str(current["git_root"])),
            private_root,
        )
    ownership_file_sha256 = (
        _sha256_bytes(_read_regular(ownership_path, "ownership receipt"))
        if _lstat_optional(ownership_path) is not None
        else None
    )
    state: dict[str, object] = {
        "schema": STATE_SCHEMA,
        "project_root": current["project_root"],
        "git_root": current["git_root"],
        "project_scope": current["project_scope"],
        "spec_owner": current["spec_owner"],
        "requirements": current["requirements"],
        "design": current["design"],
        "spec_receipt": current["spec_receipt"],
        "codex_home": current["codex_home"],
        "private_root": str(private_root),
        "manifest_path": _relative_private_path(private_root, manifest_path),
        "manifest_file_sha256": _sha256_bytes(_read_regular(manifest_path, "manifest")),
        "decision_path": _relative_private_path(private_root, decision_path),
        "decision_file_sha256": _sha256_bytes(_read_regular(decision_path, "decision")),
        "ownership_path": _relative_private_path(private_root, ownership_path),
        "ownership_file_sha256": ownership_file_sha256,
        "input_manifest_sha256": current["manifest_sha256"],
        "current_manifest_sha256": final["manifest_sha256"],
        "decision_sha256": decision_sha256,
        "outcome": outcome,
        "target_path": final_target["path"],
        "target_sha256": final_target["sha256"],
        "active_instruction_path": final_target["active_path"],
        "reload_required": outcome in {"created", "attached", "refreshed", "retired"},
    }
    _write_private_json(state_path, state, Path(str(current["git_root"])), private_root)
    state_sha256 = _sha256_bytes(_read_regular(state_path, "state"))
    if published_receipt is not None:
        _publish_workspace_registry_locked(
            registry,
            current,
            registry_target,
            published_receipt,
            registry_target_sha256,
            state_sha256,
            registry_root,
        )
    if retained_backup_sha256 is not None:
        _complete_retained_backup(
            target_path,
            retained_backup_sha256,
            (
                str(final_target["sha256"])
                if isinstance(final_target.get("sha256"), str)
                else None
            ),
            parent_identity,
        )
    return state


def verify_state(state_path: Path, private_root: Path) -> dict[str, object]:
    provisional_root = Path(os.path.abspath(private_root.expanduser()))
    state_path = _private_member(provisional_root, state_path, "state")
    state = _load_private_json_object(state_path, "state", provisional_root)
    if set(state) != STATE_REQUIRED_FIELDS or state.get("schema") != STATE_SCHEMA:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project-agent-instructions state is invalid"
        )
    private_root = _ensure_private_root(provisional_root, Path(str(state["git_root"])))
    if state.get("private_root") != str(private_root):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "private root changed"
        )
    manifest_path = _private_member(
        private_root, private_root / str(state["manifest_path"]), "manifest"
    )
    decision_path = _private_member(
        private_root, private_root / str(state["decision_path"]), "decision"
    )
    ownership_path = _private_member(
        private_root, private_root / str(state["ownership_path"]), "ownership"
    )
    manifest_bytes = _read_regular(manifest_path, "manifest")
    decision_bytes = _read_regular(decision_path, "decision")
    if _sha256_bytes(manifest_bytes) != state.get(
        "manifest_file_sha256"
    ) or _sha256_bytes(decision_bytes) != state.get("decision_file_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "private evidence changed"
        )
    ownership = _load_ownership(ownership_path, private_root)
    ownership_digest = (
        _sha256_bytes(_read_regular(ownership_path, "ownership receipt"))
        if ownership is not None
        else None
    )
    if ownership_digest != state.get("ownership_file_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "ownership evidence changed"
        )
    recorded = _load_private_json_object(manifest_path, "manifest", private_root)
    _validate_manifest_shape(recorded)
    if recorded.get("schema") != MANIFEST_SCHEMA or recorded.get(
        "manifest_sha256"
    ) != state.get("input_manifest_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "manifest provenance changed"
        )
    decision = _load_private_json_object(decision_path, "decision", private_root)
    disposition, body, decision_sha256, approval = _validate_decision(
        decision, recorded
    )
    marker_manifest_sha256 = _generation_manifest_sha256(recorded)
    marker_decision_sha256 = (
        _generation_decision_sha256(
            disposition,
            body,
            _validate_evidence(decision, recorded),
        )
        if body is not None
        else ""
    )
    verification_ownership: Optional[dict[str, object]] = None
    if dict(recorded["target"]).get("file_status") == "managed" and not (
        approval is not None and approval.get("action") == "adopt"
    ):
        verification_ownership = _ownership_record(
            "active", recorded, dict(recorded["target"])
        )
    expected_outcome = _select_outcome(
        recorded,
        disposition,
        body,
        marker_manifest_sha256,
        marker_decision_sha256,
        approval,
        verification_ownership,
    )
    if state.get("outcome") == "adopted" and expected_outcome == "existing-sufficient":
        expected_outcome = "adopted"
    final = _fresh_manifest(recorded)
    _validate_final_transition(
        recorded,
        final,
        body,
        marker_manifest_sha256,
        marker_decision_sha256,
        str(state["outcome"]),
    )
    final_target = dict(final["target"])
    expected = {
        "project_root": recorded["project_root"],
        "git_root": recorded["git_root"],
        "project_scope": recorded["project_scope"],
        "spec_owner": recorded["spec_owner"],
        "requirements": recorded["requirements"],
        "design": recorded["design"],
        "spec_receipt": recorded["spec_receipt"],
        "codex_home": recorded["codex_home"],
        "current_manifest_sha256": final["manifest_sha256"],
        "decision_sha256": decision_sha256,
        "outcome": expected_outcome,
        "target_path": final_target["path"],
        "target_sha256": final_target["sha256"],
        "active_instruction_path": final_target["active_path"],
        "reload_required": expected_outcome
        in {"created", "attached", "refreshed", "retired"},
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise ProjectInstructionsError("CONCURRENT_MODIFICATION", "state is stale")
    if (
        expected_outcome
        in {"created", "attached", "refreshed", "adopted", "existing-sufficient"}
        and final_target.get("file_status") == "managed"
    ):
        if ownership is None or not _ownership_matches(
            ownership, final, final_target, status="active"
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "active ownership is not proven"
            )
    if expected_outcome == "retired":
        if ownership is None or not _ownership_matches(
            ownership, recorded, dict(recorded["target"]), status="retired"
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT", "retirement ownership is not proven"
            )
    if ownership is not None and expected_outcome in (
        ACTIVE_OWNERSHIP_OUTCOMES | {"retired"}
    ):
        registry_target = (
            dict(recorded["target"]) if expected_outcome == "retired" else final_target
        )
        _, registry_root = _workspace_ownership_root(
            private_root, Path(str(recorded["git_root"]))
        )
        with render_lock(
            registry_root,
            lock_name=".ownership.lock",
            label="workspace ownership publication",
        ):
            registry = _load_workspace_registry(registry_root)
            entry = dict(registry["entries"]).get(
                _ownership_subject_key(recorded, registry_target)
            )
        if (
            not isinstance(entry, dict)
            or entry.get("status") != ownership.get("status")
            or entry.get("ownership") != ownership
            or entry.get("target_sha256") != final_target.get("sha256")
            or entry.get("source_state_sha256")
            != _sha256_bytes(_read_regular(state_path, "state"))
        ):
            raise ProjectInstructionsError(
                "OWNERSHIP_CONFLICT",
                "workspace ownership publication is not proven",
            )
    return {
        "status": "ok",
        "outcome": expected_outcome,
        "project_root": recorded["project_root"],
        "decision_sha256": decision_sha256,
        "target_path": final_target["path"],
        "target_sha256": final_target["sha256"],
        "active_instruction_path": final_target["active_path"],
        "reload_required": expected["reload_required"],
    }
