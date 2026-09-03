#!/usr/bin/env python3
"""Closed ownership-retention disposition tests."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))
from project_agent_instructions_lib import workflow  # noqa: E402


class ProjectSpecsRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name).resolve() / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.session = self.workspace / "session"
        self.session.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_json(self, path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def state(self, outcome: str) -> dict[str, object]:
        private_root = self.session / "project-instructions"
        state = {field: None for field in workflow.STATE_REQUIRED_FIELDS}
        state.update(
            {
                "schema": workflow.STATE_SCHEMA,
                "private_root": str(private_root),
                "outcome": outcome,
                "project_root": "/project",
                "git_root": "/project",
                "project_scope": ".",
                "spec_owner": "maintain-project-specs",
                "requirements": {
                    "path": "docs/requirements.md",
                    "sha256": "a" * 64,
                },
                "design": {"path": "docs/design.md", "sha256": "b" * 64},
                "spec_receipt": {
                    "path": str(self.session / "spec-receipt.json"),
                    "sha256": "4" * 64,
                },
                "codex_home": str(self.workspace.parent),
                "manifest_path": "manifest.json",
                "manifest_file_sha256": "c" * 64,
                "decision_path": "decision.json",
                "decision_file_sha256": "d" * 64,
                "ownership_path": "ownership.json",
                "ownership_file_sha256": None,
                "input_manifest_sha256": "e" * 64,
                "current_manifest_sha256": "f" * 64,
                "decision_sha256": "1" * 64,
                "target_path": "/project/AGENTS.md",
                "target_sha256": None,
                "active_instruction_path": None,
                "reload_required": False,
            }
        )
        if outcome == "created":
            state.update(
                {
                    "ownership_file_sha256": "2" * 64,
                    "target_sha256": "3" * 64,
                    "active_instruction_path": "/project/AGENTS.md",
                    "reload_required": True,
                }
            )
        return state

    def state_with_evidence(self, outcome: str) -> dict[str, object]:
        private_root = self.session / "project-instructions"
        manifest_path = private_root / "manifest.json"
        decision_path = private_root / "decision.json"
        self.write_json(manifest_path, {"test": "manifest"})
        self.write_json(decision_path, {"test": "decision"})
        state = self.state(outcome)
        state["manifest_file_sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        state["decision_file_sha256"] = hashlib.sha256(
            decision_path.read_bytes()
        ).hexdigest()
        return state

    def write_final_state(self, outcome: str) -> None:
        private_root = self.session / "project-instructions"
        state_path = private_root / "state.json"
        self.write_json(state_path, self.state_with_evidence(outcome))
        digest = hashlib.sha256(state_path.read_bytes()).hexdigest()
        self.write_json(
            self.session / "lifecycle.json",
            {
                "schema": workflow.SEALED_LIFECYCLE_SCHEMA,
                "project_scope": ".",
                "git_head_at_prompt": "5" * 40,
                "turn_sha256": "6" * 64,
                "phase": "sealed",
                "receipt_sha256": "4" * 64,
                "requirements_sha256": "a" * 64,
                "design_sha256": "b" * 64,
                "rules_path": workflow.SEALED_LIFECYCLE_RULES_PATH,
                "rules_sha256": "7" * 64,
                "project_instructions_state_sha256": digest,
                "project_instructions_reload_required": outcome == "created",
                "write_epoch": 3,
                "planned_write_epoch": 3,
                "waiver": None,
            },
        )

    def test_absent_project_instruction_evidence_is_deletable(self) -> None:
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("deletable", None, None),
        )

    def test_sealed_lifecycle_without_final_instruction_evidence_is_unsafe(
        self,
    ) -> None:
        self.write_json(
            self.session / "lifecycle.json",
            {
                "schema": workflow.SEALED_LIFECYCLE_SCHEMA,
                "project_scope": ".",
                "git_head_at_prompt": "5" * 40,
                "turn_sha256": "6" * 64,
                "phase": "sealed",
                "receipt_sha256": "4" * 64,
                "requirements_sha256": "a" * 64,
                "design_sha256": "b" * 64,
                "rules_path": workflow.SEALED_LIFECYCLE_RULES_PATH,
                "rules_sha256": "7" * 64,
                "project_instructions_state_sha256": "8" * 64,
                "project_instructions_reload_required": False,
                "write_epoch": 3,
                "planned_write_epoch": 3,
                "waiver": None,
            },
        )

        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("unsafe", None, None),
        )

    def test_incomplete_publication_is_pending(self) -> None:
        private_root = self.session / "project-instructions"
        self.write_json(private_root / "manifest.json", {"pending": True})
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("pending", None, None),
        )

    def test_not_needed_final_state_needs_no_ownership_registry(self) -> None:
        self.write_final_state("not-needed")
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("deletable", None, None),
        )

    def test_active_state_without_receipt_is_legacy_required(self) -> None:
        self.write_final_state("created")
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("legacy-required", None, None),
        )

    def test_malformed_state_is_unsafe(self) -> None:
        private_root = self.session / "project-instructions"
        self.write_json(private_root / "state.json", {"schema": workflow.STATE_SCHEMA})
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("unsafe", None, None),
        )

    def test_incomplete_sealed_lifecycle_is_unsafe(self) -> None:
        private_root = self.session / "project-instructions"
        state_path = private_root / "state.json"
        self.write_json(state_path, self.state_with_evidence("not-needed"))
        digest = hashlib.sha256(state_path.read_bytes()).hexdigest()
        self.write_json(
            self.session / "lifecycle.json",
            {
                "schema": workflow.SEALED_LIFECYCLE_SCHEMA,
                "phase": "sealed",
                "project_instructions_state_sha256": digest,
            },
        )
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("unsafe", None, None),
        )

    def test_missing_final_manifest_is_unsafe(self) -> None:
        self.write_final_state("not-needed")
        (self.session / "project-instructions/manifest.json").unlink()
        self.assertEqual(
            workflow.retention_disposition(self.session, self.workspace),
            ("unsafe", None, None),
        )


if __name__ == "__main__":
    unittest.main()
