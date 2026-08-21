"""Decision validation, managed ownership transitions, and verification."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from typing import Optional

from .contracts import DECISION_SCHEMA
from .contracts import MANIFEST_SCHEMA
from .contracts import OWNERSHIP_SCHEMA
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
from .contracts import _valid_sha256
from .contracts import _validate_manifest_shape
from .discovery import _manifest
from .discovery import _validate_evidence
from .private_state import _ensure_private_root
from .private_state import _load_private_json_object
from .private_state import _private_member
from .private_state import _relative_private_path
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
    provisional_manifest = _private_member(
        provisional_root, manifest_path, "manifest"
    )
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
    disposition, body, decision_sha256, approval = _validate_decision(
        decision, current
    )
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
    """Return whether an active receipt can be explicitly re-adopted safely."""
    return (
        receipt.get("status") == "active"
        and receipt.get("project_root") == manifest.get("project_root")
        and receipt.get("git_root") == manifest.get("git_root")
        and receipt.get("project_scope") == manifest.get("project_scope")
        and receipt.get("target_path") == target.get("path")
        and receipt.get("body_sha256") == target.get("body_sha256")
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
    if not has_receipt:
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
        return "existing-sufficient" if has_receipt else "adopted"
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
    outcome = _select_outcome(
        current,
        disposition,
        body,
        marker_manifest_sha256,
        marker_decision_sha256,
        approval,
        ownership,
    )
    target = dict(current["target"])
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
    if outcome in {"created", "attached", "refreshed", "adopted"} or (
        outcome == "existing-sufficient"
        and final_target.get("file_status") == "managed"
    ):
        _write_private_json(
            ownership_path,
            _ownership_record("active", final, final_target),
            Path(str(current["git_root"])),
            private_root,
        )
    elif outcome == "retired":
        _write_private_json(
            ownership_path,
            _ownership_record("retired", current, target),
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
    required = {
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
    if set(state) != required or state.get("schema") != STATE_SCHEMA:
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
