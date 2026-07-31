"""Decision validation, state transitions, and final verification."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .contracts import DECISION_SCHEMA
from .contracts import MANIFEST_SCHEMA
from .contracts import STATE_SCHEMA
from .contracts import ProjectInstructionsError
from .contracts import _canonical_json
from .contracts import _read_regular
from .contracts import _sha256_bytes
from .contracts import _validate_body
from .contracts import _validate_manifest_shape
from .discovery import _manifest
from .discovery import _validate_evidence
from .private_state import _ensure_private_root
from .private_state import _load_private_json_object
from .private_state import _private_member
from .private_state import _relative_private_path
from .private_state import _write_private_json
from .target_io import _exclusive_create
from .target_io import _generated_content
from .target_io import _guarded_replace


def _validate_decision(
    decision: dict[str, object], manifest: dict[str, object]
) -> tuple[str, Optional[bytes], str]:
    required = {
        "schema",
        "manifest_sha256",
        "disposition",
        "rationale",
        "evidence",
        "body",
    }
    if (
        set(decision) != required
        or decision.get("schema") != DECISION_SCHEMA
        or decision.get("manifest_sha256") != manifest.get("manifest_sha256")
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "decision does not match the current manifest",
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
    _validate_evidence(decision, manifest)
    body = (
        _validate_body(decision.get("body"), manifest)
        if disposition == "needed"
        else None
    )
    if disposition != "needed" and decision.get("body") is not None:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "non-creation decisions must use a null body"
        )
    return str(disposition), body, _sha256_bytes(_canonical_json(decision))


def _fresh_manifest(recorded: dict[str, object]) -> dict[str, object]:
    return _manifest(
        Path(str(recorded["project_root"])),
        str(recorded["spec_owner"]),
        str(dict(recorded["requirements"])["path"]),
        str(dict(recorded["design"])["path"]),
        Path(str(recorded["codex_home"])),
    )


def _expected_outcome(
    manifest: dict[str, object],
    disposition: str,
    body: Optional[bytes],
) -> str:
    target = dict(manifest["target"])
    file_status = str(target["file_status"])
    active_path = target.get("active_path")
    target_path = str(target["path"])
    if disposition == "not-needed":
        return "not-needed"
    if disposition == "existing-sufficient":
        active = manifest.get("active_project_instruction")
        active_is_human_owned = (
            isinstance(active, dict)
            and active.get("path") == active_path
            and (
                (
                    active_path == target_path
                    and file_status in {"human-owned", "human-edited"}
                )
                or (
                    active_path != target_path
                    and active.get("kind") in {"project-override", "project-fallback"}
                )
            )
        )
        if not active_is_human_owned:
            raise ProjectInstructionsError(
                "EXISTING_INSTRUCTIONS_GAP",
                "existing-sufficient requires an active human-owned "
                "project instruction file",
            )
        return "existing-sufficient"
    assert body is not None
    if active_path is not None and active_path != target_path:
        raise ProjectInstructionsError(
            "EXISTING_INSTRUCTIONS_GAP",
            "an alternate human-owned project instruction file is active",
        )
    if file_status == "missing":
        return "created"
    if file_status == "generated":
        expected_content = _generated_content(body)
        return (
            "existing-sufficient"
            if target.get("sha256") == _sha256_bytes(expected_content)
            else "refreshed"
        )
    if file_status == "human-edited":
        raise ProjectInstructionsError(
            "STALE_GENERATED_FILE",
            "generated project AGENTS.md was edited and is now human-owned",
        )
    raise ProjectInstructionsError(
        "EXISTING_INSTRUCTIONS_GAP",
        "human-owned project AGENTS.md requires an explicit resolution",
    )


def _validate_final_transition(
    initial: dict[str, object],
    final: dict[str, object],
    disposition: str,
    body: Optional[bytes],
    outcome: str,
) -> None:
    if disposition != "needed":
        if final != initial:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION",
                "project instructions changed during a no-write decision",
            )
        return
    assert body is not None
    expected_outcome = _expected_outcome(initial, disposition, body)
    content = _generated_content(body)
    target_path = str(dict(initial["target"])["path"])
    expected_target = {
        "path": target_path,
        "file_status": "generated",
        "sha256": _sha256_bytes(content),
        "body_sha256": _sha256_bytes(body),
        "active_path": target_path,
        "active_kind": "project-agents",
    }
    stable_fields = {
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
    }
    active = final.get("active_project_instruction")
    if (
        outcome != expected_outcome
        or any(final.get(field) != initial.get(field) for field in stable_fields)
        or final.get("target") != expected_target
        or not isinstance(active, dict)
        or active.get("path") != target_path
        or active.get("kind") != "project-agents"
        or active.get("sha256") != expected_target["sha256"]
    ):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project AGENTS.md does not satisfy the selected transition",
        )


def apply_decision(
    manifest_path: Path,
    decision_path: Path,
    state_path: Path,
    private_root: Path,
) -> dict[str, object]:
    provisional_root = Path(os.path.abspath(private_root.expanduser()))
    manifest_path = _private_member(provisional_root, manifest_path, "manifest")
    decision_path = _private_member(provisional_root, decision_path, "decision")
    state_path = _private_member(provisional_root, state_path, "state")
    recorded_manifest = _load_private_json_object(
        manifest_path, "manifest", provisional_root
    )
    _validate_manifest_shape(recorded_manifest)
    private_root = _ensure_private_root(
        provisional_root, Path(str(recorded_manifest["git_root"]))
    )
    current = _fresh_manifest(recorded_manifest)
    if current != recorded_manifest:
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION", "project inputs changed after inspection"
        )
    decision = _load_private_json_object(decision_path, "decision", private_root)
    disposition, body, decision_sha256 = _validate_decision(decision, current)
    outcome = _expected_outcome(current, disposition, body)
    target = dict(current["target"])
    target_path = Path(str(target["path"]))
    file_status = str(target["file_status"])
    active_path = target.get("active_path")
    active_is_target = active_path == str(target_path)
    if disposition == "needed":
        assert body is not None
        content = _generated_content(body)
        if active_path is not None and not active_is_target:
            raise ProjectInstructionsError(
                "EXISTING_INSTRUCTIONS_GAP",
                "an alternate human-owned project instruction file is active",
            )
        if file_status == "missing":
            _exclusive_create(target_path, content)
        elif file_status == "generated":
            if outcome == "refreshed":
                expected = target.get("sha256")
                if not isinstance(expected, str):
                    raise ProjectInstructionsError(
                        "STALE_GENERATED_FILE",
                        "generated project AGENTS.md has invalid provenance",
                    )
                _guarded_replace(target_path, expected, content)
        elif file_status == "human-edited":
            raise ProjectInstructionsError(
                "STALE_GENERATED_FILE",
                "generated project AGENTS.md was edited and is now human-owned",
            )
        else:
            raise ProjectInstructionsError(
                "EXISTING_INSTRUCTIONS_GAP",
                "human-owned project AGENTS.md requires an explicit resolution",
            )
    elif disposition == "not-needed":
        if file_status == "generated":
            raise ProjectInstructionsError(
                "STALE_GENERATED_FILE",
                "generated project AGENTS.md is no longer needed but is preserved",
            )
        if file_status != "missing" or active_path is not None:
            raise ProjectInstructionsError(
                "INSTRUCTION_CONFLICT",
                "not-needed is invalid while project instructions already exist",
            )
    final_manifest = _fresh_manifest(current)
    _validate_final_transition(current, final_manifest, disposition, body, outcome)
    final_target = dict(final_manifest["target"])
    state: dict[str, object] = {
        "schema": STATE_SCHEMA,
        "project_root": current["project_root"],
        "git_root": current["git_root"],
        "project_scope": current["project_scope"],
        "spec_owner": current["spec_owner"],
        "requirements": current["requirements"],
        "design": current["design"],
        "codex_home": current["codex_home"],
        "private_root": str(private_root),
        "manifest_path": _relative_private_path(private_root, manifest_path),
        "manifest_file_sha256": _sha256_bytes(_read_regular(manifest_path, "manifest")),
        "decision_path": _relative_private_path(private_root, decision_path),
        "decision_file_sha256": _sha256_bytes(_read_regular(decision_path, "decision")),
        "input_manifest_sha256": current["manifest_sha256"],
        "current_manifest_sha256": final_manifest["manifest_sha256"],
        "decision_sha256": decision_sha256,
        "outcome": outcome,
        "target_path": final_target["path"],
        "target_sha256": final_target["sha256"],
        "active_instruction_path": final_target["active_path"],
    }
    _write_private_json(
        state_path,
        state,
        Path(str(current["git_root"])),
        private_root,
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
        "codex_home",
        "private_root",
        "manifest_path",
        "manifest_file_sha256",
        "decision_path",
        "decision_file_sha256",
        "input_manifest_sha256",
        "current_manifest_sha256",
        "decision_sha256",
        "outcome",
        "target_path",
        "target_sha256",
        "active_instruction_path",
    }
    if set(state) != required or state.get("schema") != STATE_SCHEMA:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project-agent-instructions state is invalid"
        )
    private_root = _ensure_private_root(provisional_root, Path(str(state["git_root"])))
    if state.get("private_root") != str(private_root):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project-agent-instructions private root changed",
        )
    manifest_path = _private_member(
        private_root,
        private_root / str(state["manifest_path"]),
        "manifest",
    )
    decision_path = _private_member(
        private_root,
        private_root / str(state["decision_path"]),
        "decision",
    )
    manifest_bytes = _read_regular(manifest_path, "manifest")
    decision_bytes = _read_regular(decision_path, "decision")
    if _sha256_bytes(manifest_bytes) != state.get(
        "manifest_file_sha256"
    ) or _sha256_bytes(decision_bytes) != state.get("decision_file_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project-agent-instructions evidence changed",
        )
    recorded_manifest = _load_private_json_object(
        manifest_path, "manifest", private_root
    )
    _validate_manifest_shape(recorded_manifest)
    if recorded_manifest.get("schema") != MANIFEST_SCHEMA or recorded_manifest.get(
        "manifest_sha256"
    ) != state.get("input_manifest_sha256"):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project-agent-instructions manifest provenance changed",
        )
    decision = _load_private_json_object(decision_path, "decision", private_root)
    disposition, body, decision_sha256 = _validate_decision(decision, recorded_manifest)
    expected_outcome = _expected_outcome(recorded_manifest, disposition, body)
    manifest = _fresh_manifest(recorded_manifest)
    _validate_final_transition(
        recorded_manifest,
        manifest,
        disposition,
        body,
        expected_outcome,
    )
    current_target = dict(manifest["target"])
    expected_state = {
        "project_root": recorded_manifest["project_root"],
        "git_root": recorded_manifest["git_root"],
        "project_scope": recorded_manifest["project_scope"],
        "spec_owner": recorded_manifest["spec_owner"],
        "requirements": recorded_manifest["requirements"],
        "design": recorded_manifest["design"],
        "codex_home": recorded_manifest["codex_home"],
        "current_manifest_sha256": manifest["manifest_sha256"],
        "decision_sha256": decision_sha256,
        "outcome": expected_outcome,
        "target_path": current_target["path"],
        "target_sha256": current_target["sha256"],
        "active_instruction_path": current_target["active_path"],
    }
    if any(state.get(key) != value for key, value in expected_state.items()):
        raise ProjectInstructionsError(
            "CONCURRENT_MODIFICATION",
            "project-agent-instructions state is stale",
        )
    return {
        "status": "ok",
        "outcome": expected_outcome,
        "project_root": recorded_manifest["project_root"],
        "decision_sha256": decision_sha256,
        "target_path": current_target["path"],
        "target_sha256": current_target["sha256"],
        "active_instruction_path": current_target["active_path"],
    }
