#!/usr/bin/env python3
"""Disposable v3 contract tests for project_agent_instructions.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Optional
import unittest
from unittest import mock

import project_agent_instructions as project_agent_cli
from project_agent_instructions_lib import contracts
from project_agent_instructions_lib import discovery
from project_agent_instructions_lib import private_state
from project_agent_instructions_lib import render_state
from project_agent_instructions_lib import target_io
from project_agent_instructions_lib import workflow


SCRIPT = Path(__file__).resolve().with_name("project_agent_instructions.py")
AGENTIC_VALIDATOR = (
    SCRIPT.parents[2] / "sdlc-start" / "scripts" / "validate_project_specs.py"
)
REQUIREMENTS = """<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
---
schema: maintain-project-specs/requirements-v1
project: Example
status: ready
created_by_skill: maintain-project-specs
updated_by_skill: maintain-project-specs
---

# Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Enforce project instructions

#### User Story

As a maintainer, I want durable project rules, so that agents act consistently.

#### Acceptance Criteria

- AC-001: The selected project receives only evidence-backed rules.

#### Negative Criteria

- NC-001: Personal global instructions are not copied.

#### Validation Method

Contract validation.

#### Test Method

Unit and integration tests.

#### Evaluation Method

Manual review.

<!-- /REQUIREMENT: REQ-001 -->
<!-- maintain-project-specs:requirements:end -->
"""
DESIGN = """<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v1 -->
---
schema: maintain-project-specs/design-v1
project: Example
status: ready
created_by_skill: maintain-project-specs
updated_by_skill: maintain-project-specs
source_requirements: docs/requirements.md
---

# Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready priority=P0 version=1 -->
### FEAT-001: Project instructions

#### Requirements Covered

- REQ-001

#### Context Evidence

- Codebase contract tests.

#### Design Details

Generate deterministic rules after validation.

#### Selected Option

Use a receipt-bound helper.

#### Alternatives Considered

- Human-only maintenance has drift risk.

#### Implementation Boundaries

- The helper owns generated AGENTS.md files.

#### Test-First Success Criteria

- TDD-001: Invalid ownership fails closed.

#### Validation Plan

Run syntax and contract checks.

#### Test Plan

Run unit and integration tests.

#### Evaluation Plan

Review the generated file.

#### Rollout And Rollback

Adopt and retire only with exact-digest approval.

#### Done Definition

Requirements mapped and checks passing.

<!-- /FEATURE: FEAT-001 -->
<!-- maintain-project-specs:design:end -->
"""


class ProjectAgentInstructionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="project-agent-instructions-v3-"
        )
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.private = self.root / "private"
        self.codex_home = self.root / "codex-home"
        self.repo.mkdir()
        self.private.mkdir()
        self.codex_home.mkdir()
        os.chmod(self.private, 0o700)
        subprocess.run(
            ["git", "init", "-q", "-b", "feature/test"], cwd=self.repo, check=True
        )
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "requirements.md").write_text(
            REQUIREMENTS, encoding="utf-8"
        )
        (self.repo / "docs" / "design.md").write_text(DESIGN, encoding="utf-8")
        subprocess.run(["git", "add", "docs"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)
        private_state._ensure_private_root(self.private, self.repo)
        self.receipt_path = self.private / "spec-receipt.json"
        self.write_json("spec-receipt.json", self.receipt())
        self.runtime_path = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": None,
                "overrides": {},
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receipt(self, project: Optional[Path] = None) -> dict[str, object]:
        selected = (project or self.repo).resolve()
        result = subprocess.run(
            [sys.executable, str(AGENTIC_VALIDATOR), "--project-root", str(selected)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    def write_json(self, name: str, value: object) -> Path:
        path = self.private / name
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(path, 0o600)
        return path

    def inspect(
        self,
        *,
        project: Optional[Path] = None,
        runtime_config: Optional[Path] = None,
    ) -> dict[str, object]:
        return discovery._manifest(
            project or self.repo,
            "maintain-project-specs",
            "docs/requirements.md",
            "docs/design.md",
            self.codex_home,
            self.receipt_path,
            runtime_config or self.runtime_path,
        )

    def evidence(self, project: Optional[Path] = None) -> list[dict[str, str]]:
        selected = project or self.repo
        path = selected / "docs" / "design.md"
        return [
            {
                "path": "docs/design.md",
                "sha256": contracts._sha256_bytes(path.read_bytes()),
                "locator": "# Design",
            }
        ]

    def rules(self) -> list[dict[str, object]]:
        return [
            {
                "section": "Testing and verification",
                "instruction": "Run the focused contract tests for every behavior change.",
                "evidence": ["docs/design.md"],
            }
        ]

    def compatibility_rules(self) -> list[dict[str, object]]:
        return [
            {
                "section": "Change requirements",
                "instruction": (
                    "This project has existing users. Preserve supported behavior and "
                    "public interfaces across changes; treat unintended compatibility "
                    "breakage as a regression."
                ),
                "evidence": ["docs/design.md"],
            },
            {
                "section": "Change requirements",
                "instruction": (
                    "Breaking a supported API, CLI contract, configuration or persisted "
                    "format, or upgrade path requires explicit approval, a deprecation or "
                    "migration plan, and regression coverage. Keep internals on one "
                    "canonical path."
                ),
                "evidence": ["docs/design.md"],
            },
        ]

    def decision(
        self,
        manifest: dict[str, object],
        disposition: str,
        *,
        approval: Optional[dict[str, str]] = None,
    ) -> dict[str, object]:
        return {
            "schema": contracts.DECISION_SCHEMA,
            "manifest_sha256": manifest["manifest_sha256"],
            "disposition": disposition,
            "rationale": "Tracked project evidence supports this decision.",
            "evidence": self.evidence(),
            "rules": self.rules() if disposition == "needed" else [],
            "budget_exception": None,
            "ownership_approval": approval,
        }

    def apply(
        self,
        manifest: dict[str, object],
        decision: dict[str, object],
    ) -> dict[str, object]:
        return workflow.apply_decision(
            self.write_json("manifest.json", manifest),
            self.write_json("decision.json", decision),
            self.private / "ownership.json",
            self.private / "state.json",
            self.private,
        )

    def test_missing_target_is_created_with_v3_provenance_and_verified(self) -> None:
        manifest = self.inspect()
        state = self.apply(manifest, self.decision(manifest, "needed"))
        target = self.repo / "AGENTS.md"
        self.assertEqual(state["outcome"], "created")
        self.assertTrue(state["reload_required"])
        self.assertTrue(
            target.read_bytes().startswith(contracts.GENERATED_MARKER_PREFIX)
        )
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        ownership = json.loads((self.private / "ownership.json").read_text())
        parsed = contracts._parse_generated(target.read_bytes())
        assert parsed is not None
        self.assertEqual(ownership["body_sha256"], parsed["body_sha256"])
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "created",
        )

    def test_missing_target_not_needed_records_verified_no_file_outcome(self) -> None:
        manifest = self.inspect()
        decision = self.decision(manifest, "not-needed")
        rendered = workflow.render_decision(
            self.write_json("manifest.json", manifest),
            self.write_json("decision.json", decision),
            self.private / "rules.md",
            self.private / "render-state.json",
            self.private,
        )

        self.assertEqual(rendered["disposition"], "not-needed")
        self.assertFalse(rendered["repository_mutated"])
        self.assertEqual((self.private / "rules.md").read_bytes(), b"")
        self.assertFalse((self.repo / "AGENTS.md").exists())

        state = self.apply(manifest, decision)
        self.assertEqual(state["outcome"], "not-needed")
        self.assertIsNone(state["target_sha256"])
        self.assertFalse(state["reload_required"])
        self.assertFalse((self.repo / "AGENTS.md").exists())
        verified = workflow.verify_state(self.private / "state.json", self.private)
        self.assertEqual(verified["outcome"], "not-needed")
        self.assertIsNone(verified["target_sha256"])
        self.assertFalse(verified["reload_required"])

    def test_render_is_deterministic_and_does_not_copy_global_instructions(
        self,
    ) -> None:
        (self.codex_home / "AGENTS.md").write_text(
            "# Global\n\n- Do not preserve backward compatibility by default.\n",
            encoding="utf-8",
        )
        manifest = self.inspect()
        decision = self.decision(manifest, "needed")
        decision["rules"] = self.compatibility_rules()
        body1, normalized1 = contracts._render_body(
            manifest, decision["rules"], None, {"docs/design.md"}
        )
        body2, normalized2 = contracts._render_body(
            manifest, list(reversed(decision["rules"])), None, {"docs/design.md"}
        )
        self.assertEqual(body1, body2)
        self.assertEqual(normalized1, normalized2)
        self.assertIn(b"This project has existing users.", body1)
        self.assertIn(b"Breaking a supported API, CLI contract", body1)
        self.assertNotIn(b"Do not preserve backward compatibility", body1)
        without_global = dict(manifest)
        without_global["global_instructions"] = []
        self.assertEqual(
            target_io._generated_content(
                body1,
                contracts._generation_manifest_sha256(manifest),
                contracts._generation_decision_sha256("needed", body1, self.evidence()),
            ),
            target_io._generated_content(
                body2,
                contracts._generation_manifest_sha256(without_global),
                contracts._generation_decision_sha256("needed", body2, self.evidence()),
            ),
        )

    def test_render_prepares_exact_rules_without_repository_mutation(self) -> None:
        manifest = self.inspect()
        decision = self.decision(manifest, "needed")
        decision["rules"] = self.compatibility_rules()
        result = workflow.render_decision(
            self.write_json("manifest.json", manifest),
            self.write_json("decision.json", decision),
            self.private / "rules.md",
            self.private / "render-state.json",
            self.private,
        )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["repository_mutated"])
        self.assertFalse((self.repo / "AGENTS.md").exists())
        rendered = (self.private / "rules.md").read_text(encoding="utf-8")
        self.assertIn("This project has existing users.", rendered)
        self.assertIn("Keep internals on one canonical path.", rendered)
        rendered = (self.private / "rules.md").read_bytes()
        self.assertEqual(result["rules_sha256"], contracts._sha256_bytes(rendered))
        self.assertIn(b"Breaking a supported API, CLI contract", rendered)
        render_state = json.loads((self.private / "render-state.json").read_text())
        self.assertEqual(
            render_state["schema"], "project-agent-instructions.render-state.v1"
        )

    def test_render_rejects_existing_sufficient_for_managed_target(self) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        current = self.inspect()

        with self.assertRaises(contracts.ProjectInstructionsError) as raised:
            workflow.render_decision(
                self.write_json("manifest.json", current),
                self.write_json(
                    "decision.json", self.decision(current, "existing-sufficient")
                ),
                self.private / "rules.md",
                self.private / "render-state.json",
                self.private,
            )

        self.assertEqual(raised.exception.code, "EXISTING_INSTRUCTIONS_GAP")
        self.assertFalse((self.private / "rules.md").exists())
        self.assertFalse((self.private / "render-state.json").exists())

    def test_render_revises_exactly_owned_empty_rules_to_needed_rules(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        prior_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(rules_path.read_bytes(), b"")

        self.write_json("decision.json", self.decision(manifest, "needed"))
        revised = workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )

        rendered = rules_path.read_bytes()
        current_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn(b"Run the focused contract tests", rendered)
        self.assertEqual(revised["rules_sha256"], contracts._sha256_bytes(rendered))
        self.assertNotEqual(
            current_state["decision_sha256"], prior_state["decision_sha256"]
        )
        self.assertEqual(current_state["rules_sha256"], revised["rules_sha256"])
        self.assertFalse(revised["repository_mutated"])
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_revision_rejects_mismatched_predecessor_state(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        predecessor = json.loads(state_path.read_text(encoding="utf-8"))
        predecessor["rules_sha256"] = "0" * 64
        self.write_json("render-state.json", predecessor)
        self.write_json("decision.json", self.decision(manifest, "needed"))

        with self.assertRaises(contracts.ProjectInstructionsError) as raised:
            workflow.render_decision(
                manifest_path,
                decision_path,
                rules_path,
                state_path,
                self.private,
            )

        self.assertEqual(raised.exception.code, "UNSAFE_TARGET")
        self.assertEqual(rules_path.read_bytes(), b"")
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_revision_rechecks_predecessor_state_at_final_cas(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        self.write_json("decision.json", self.decision(manifest, "needed"))
        original_validate = workflow.validate_render_predecessor

        def mutate_state_after_validation(*args: object, **kwargs: object) -> object:
            predecessor = original_validate(*args, **kwargs)
            changed = json.loads(state_path.read_text(encoding="utf-8"))
            changed["decision_sha256"] = "0" * 64
            self.write_json("render-state.json", changed)
            return predecessor

        with (
            mock.patch.object(
                workflow,
                "validate_render_predecessor",
                side_effect=mutate_state_after_validation,
            ),
            self.assertRaises(contracts.ProjectInstructionsError) as raised,
        ):
            workflow.render_decision(
                manifest_path,
                decision_path,
                rules_path,
                state_path,
                self.private,
            )

        self.assertEqual(raised.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(rules_path.read_bytes(), b"")
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8"))["decision_sha256"],
            "0" * 64,
        )
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_revision_rejects_hard_linked_predecessor(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        os.link(rules_path, self.private / "unexpected-rules-link")
        self.write_json("decision.json", self.decision(manifest, "needed"))

        with self.assertRaises(contracts.ProjectInstructionsError) as raised:
            workflow.render_decision(
                manifest_path,
                decision_path,
                rules_path,
                state_path,
                self.private,
            )

        self.assertEqual(raised.exception.code, "UNSAFE_TARGET")
        self.assertEqual(rules_path.read_bytes(), b"")
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_rejects_a_concurrent_private_bundle_writer(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "needed")
        )

        with render_state.render_lock(self.private):
            with self.assertRaises(contracts.ProjectInstructionsError) as raised:
                workflow.render_decision(
                    manifest_path,
                    decision_path,
                    self.private / "rules.md",
                    self.private / "render-state.json",
                    self.private,
                )

        self.assertEqual(raised.exception.code, "CONCURRENT_MODIFICATION")
        self.assertFalse((self.private / "rules.md").exists())
        self.assertFalse((self.private / "render-state.json").exists())

    def test_render_lock_creation_failure_returns_structured_blocker(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "needed")
        )
        os.chmod(self.private, 0o500)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "render",
                    "--manifest",
                    str(manifest_path),
                    "--decision",
                    str(decision_path),
                    "--private-root",
                    str(self.private),
                    "--output",
                    str(self.private / "rules.md"),
                    "--state",
                    str(self.private / "render-state.json"),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            os.chmod(self.private, 0o700)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["code"], "UNSAFE_TARGET")
        self.assertEqual(completed.stderr, "")
        self.assertFalse((self.private / ".render.lock").exists())
        self.assertFalse((self.private / "rules.md").exists())
        self.assertFalse((self.private / "render-state.json").exists())

    def test_render_cli_classifies_and_recovers_state_publish_failure(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        predecessor_state = state_path.read_bytes()
        self.write_json("decision.json", self.decision(manifest, "needed"))

        render_args = [
            "render",
            "--manifest",
            str(manifest_path),
            "--decision",
            str(decision_path),
            "--private-root",
            str(self.private),
            "--output",
            str(rules_path),
            "--state",
            str(state_path),
        ]
        original_write = workflow._write_private_json

        def fail_state_publication(*args: object, **kwargs: object) -> None:
            with mock.patch.object(
                private_state.tempfile,
                "mkstemp",
                side_effect=OSError("injected state publication failure"),
            ):
                original_write(*args, **kwargs)

        blocked_output = io.StringIO()
        with (
            mock.patch.object(
                workflow,
                "_write_private_json",
                side_effect=fail_state_publication,
            ),
            contextlib.redirect_stdout(blocked_output),
        ):
            blocked_code = project_agent_cli.main(render_args)

        blocked = json.loads(blocked_output.getvalue())
        self.assertEqual(blocked_code, 2)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(
            blocked["code"], "RENDER_STATE_PUBLICATION_INCOMPLETE"
        )
        self.assertIn(b"Run the focused contract tests", rules_path.read_bytes())
        self.assertEqual(state_path.read_bytes(), predecessor_state)
        recovered_output = io.StringIO()
        with contextlib.redirect_stdout(recovered_output):
            recovered_code = project_agent_cli.main(render_args)
        recovered = json.loads(recovered_output.getvalue())
        current_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered_code, 0)
        self.assertEqual(current_state["rules_sha256"], recovered["rules_sha256"])
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_cli_retries_state_directory_sync_after_replace(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        predecessor_state = state_path.read_bytes()
        self.write_json("decision.json", self.decision(manifest, "needed"))
        render_args = [
            "render",
            "--manifest",
            str(manifest_path),
            "--decision",
            str(decision_path),
            "--private-root",
            str(self.private),
            "--output",
            str(rules_path),
            "--state",
            str(state_path),
        ]
        original_write = workflow._write_private_json
        original_fsync = private_state.os.fsync

        def fail_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("injected directory sync failure")
            original_fsync(descriptor)

        def fail_state_publication(*args: object, **kwargs: object) -> None:
            with mock.patch.object(
                private_state.os,
                "fsync",
                side_effect=fail_directory_sync,
            ):
                original_write(*args, **kwargs)

        blocked_output = io.StringIO()
        with (
            mock.patch.object(
                workflow,
                "_write_private_json",
                side_effect=fail_state_publication,
            ),
            contextlib.redirect_stdout(blocked_output),
        ):
            blocked_code = project_agent_cli.main(render_args)

        self.assertEqual(blocked_code, 2)
        self.assertEqual(
            json.loads(blocked_output.getvalue())["code"],
            "RENDER_STATE_PUBLICATION_INCOMPLETE",
        )
        self.assertNotEqual(state_path.read_bytes(), predecessor_state)
        directory_synced = False

        def observe_directory_sync(descriptor: int) -> None:
            nonlocal directory_synced
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_synced = True
            original_fsync(descriptor)

        def observe_state_publication(*args: object, **kwargs: object) -> None:
            with mock.patch.object(
                private_state.os,
                "fsync",
                side_effect=observe_directory_sync,
            ):
                original_write(*args, **kwargs)

        recovered_output = io.StringIO()
        with (
            mock.patch.object(
                workflow,
                "_write_private_json",
                side_effect=observe_state_publication,
            ),
            contextlib.redirect_stdout(recovered_output),
        ):
            recovered_code = project_agent_cli.main(render_args)

        self.assertEqual(recovered_code, 0)
        self.assertTrue(directory_synced)
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_render_cli_rejects_hard_linked_state_during_recovery(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )
        rules_path = self.private / "rules.md"
        state_path = self.private / "render-state.json"
        workflow.render_decision(
            manifest_path,
            decision_path,
            rules_path,
            state_path,
            self.private,
        )
        self.write_json("decision.json", self.decision(manifest, "needed"))
        render_args = [
            "render",
            "--manifest",
            str(manifest_path),
            "--decision",
            str(decision_path),
            "--private-root",
            str(self.private),
            "--output",
            str(rules_path),
            "--state",
            str(state_path),
        ]
        original_write = workflow._write_private_json

        def fail_state_publication(*args: object, **kwargs: object) -> None:
            with mock.patch.object(
                private_state.tempfile,
                "mkstemp",
                side_effect=OSError("injected state publication failure"),
            ):
                original_write(*args, **kwargs)

        blocked_output = io.StringIO()
        with (
            mock.patch.object(
                workflow,
                "_write_private_json",
                side_effect=fail_state_publication,
            ),
            contextlib.redirect_stdout(blocked_output),
        ):
            blocked_code = project_agent_cli.main(render_args)

        self.assertEqual(blocked_code, 2)
        self.assertEqual(
            json.loads(blocked_output.getvalue())["code"],
            "RENDER_STATE_PUBLICATION_INCOMPLETE",
        )
        linked_state = self.private / "unexpected-state-link.json"
        os.link(state_path, linked_state)
        predecessor_bytes = state_path.read_bytes()
        recovered_output = io.StringIO()
        with contextlib.redirect_stdout(recovered_output):
            recovered_code = project_agent_cli.main(render_args)

        recovered = json.loads(recovered_output.getvalue())
        self.assertEqual(recovered_code, 2)
        self.assertEqual(recovered["code"], "UNSAFE_TARGET")
        self.assertEqual(state_path.read_bytes(), predecessor_bytes)
        self.assertEqual(linked_state.read_bytes(), predecessor_bytes)
        self.assertEqual(state_path.stat().st_nlink, 2)
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_same_managed_body_with_receipt_preserves_target(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        before = (self.repo / "AGENTS.md").read_bytes()
        current = self.inspect()
        state = self.apply(current, self.decision(current, "needed"))
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertFalse(state["reload_required"])
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), before)

    def test_spec_change_refreshes_provenance_when_body_is_unchanged(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        target = self.repo / "AGENTS.md"
        before = target.read_bytes()
        requirements = self.repo / "docs" / "requirements.md"
        requirements.write_text(
            REQUIREMENTS.replace(
                "durable project rules",
                "durable selected-project rules",
            ),
            encoding="utf-8",
        )
        self.write_json("spec-receipt.json", self.receipt())

        current = self.inspect()
        state = self.apply(current, self.decision(current, "needed"))

        self.assertEqual(state["outcome"], "refreshed")
        self.assertTrue(state["reload_required"])
        self.assertNotEqual(target.read_bytes(), before)
        self.assertEqual(
            contracts._parse_generated(target.read_bytes())["body_sha256"],
            contracts._parse_generated(before)["body_sha256"],
        )

    def test_changed_rules_refresh_exact_owned_target(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        current = self.inspect()
        decision = self.decision(current, "needed")
        decision["rules"][0]["instruction"] = (
            "Run focused tests and inspect their exact failure output."
        )
        state = self.apply(current, decision)
        self.assertEqual(state["outcome"], "refreshed")
        self.assertIn(
            "inspect their exact failure", (self.repo / "AGENTS.md").read_text()
        )
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "refreshed",
        )

    def test_managed_region_edit_transfers_ownership_and_blocks_refresh(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        target = self.repo / "AGENTS.md"
        target.write_text(target.read_text() + "\nHuman note.\n", encoding="utf-8")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        current = self.inspect()
        self.assertEqual(current["target"]["file_status"], "human-edited")
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "needed"))
        self.assertEqual(caught.exception.code, "STALE_GENERATED_FILE")

    def test_unreceipted_v3_region_requires_exact_adoption(self) -> None:
        initial = self.inspect()
        decision = self.decision(initial, "needed")
        _, body, _, _ = workflow._validate_decision(decision, initial)
        assert body is not None
        (self.repo / "AGENTS.md").write_bytes(
            target_io._generated_content(
                body,
                contracts._generation_manifest_sha256(initial),
                contracts._generation_decision_sha256("needed", body, self.evidence()),
            )
        )
        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "needed"))
        self.assertEqual(caught.exception.code, "ADOPTION_APPROVAL_REQUIRED")
        approved = self.decision(
            current,
            "needed",
            approval={
                "action": "adopt",
                "target_sha256": str(current["target"]["sha256"]),
            },
        )
        state = self.apply(current, approved)
        self.assertEqual(state["outcome"], "adopted")

    def test_retirement_requires_exact_approval_and_is_verified(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "not-needed"))
        self.assertEqual(caught.exception.code, "RETIREMENT_APPROVAL_REQUIRED")
        decision = self.decision(
            current,
            "not-needed",
            approval={
                "action": "retire",
                "target_sha256": str(current["target"]["sha256"]),
            },
        )
        state = self.apply(current, decision)
        self.assertEqual(state["outcome"], "retired")
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "retired",
        )

    def test_retirement_preserves_a_concurrent_replacement(self) -> None:
        target = self.repo / "AGENTS.md"
        original = b"generated bytes\n"
        replacement = b"human replacement\n"
        target.write_bytes(original)
        actual_replace = os.replace

        def replace_after_concurrent_write(
            source: object, destination: object, **kwargs: object
        ) -> None:
            target.write_bytes(replacement)
            actual_replace(source, destination, **kwargs)

        with (
            mock.patch.object(
                target_io.os,
                "replace",
                side_effect=replace_after_concurrent_write,
            ),
            self.assertRaises(contracts.ProjectInstructionsError) as caught,
        ):
            target_io._guarded_delete(target, contracts._sha256_bytes(original))
        self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")
        self.assertEqual(target.read_bytes(), replacement)
        self.assertFalse(
            (self.repo / ".AGENTS.md.project-agent-instructions.backup").exists()
        )

    def test_retirement_keeps_recovery_backup_until_state_is_durable(self) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        target = self.repo / "AGENTS.md"
        before = target.read_bytes()
        current = self.inspect()
        decision = self.decision(
            current,
            "not-needed",
            approval={
                "action": "retire",
                "target_sha256": str(current["target"]["sha256"]),
            },
        )
        write_private_json = workflow._write_private_json

        def fail_state_write(
            path: Path,
            value: object,
            git_root: Path,
            private_root: Path,
        ) -> None:
            if path.name == "state.json":
                raise contracts.ProjectInstructionsError(
                    "UNSAFE_TARGET", "simulated state write failure"
                )
            write_private_json(path, value, git_root, private_root)

        with (
            mock.patch.object(
                workflow,
                "_write_private_json",
                side_effect=fail_state_write,
            ),
            self.assertRaises(contracts.ProjectInstructionsError),
        ):
            self.apply(current, decision)
        backup = self.repo / ".AGENTS.md.project-agent-instructions.backup"
        self.assertFalse(target.exists())
        self.assertEqual(backup.read_bytes(), before)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")

    def test_parent_directory_swap_cannot_redirect_creation(self) -> None:
        identity = (self.repo.stat().st_dev, self.repo.stat().st_ino)
        original = self.root / "original-repo"
        self.repo.rename(original)
        self.repo.mkdir()
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            target_io._exclusive_create(self.repo / "AGENTS.md", b"content\n", identity)
        self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((original / "AGENTS.md").exists())

    def test_create_rechecks_recovery_artifacts_at_mutation_boundary(self) -> None:
        target = self.repo / "AGENTS.md"
        for name in discovery.RECOVERY_NAMES:
            with self.subTest(name=name):
                artifact = self.repo / name
                artifact.write_text("recovery\n", encoding="utf-8")
                try:
                    with self.assertRaises(
                        contracts.ProjectInstructionsError
                    ) as caught:
                        target_io._exclusive_create(target, b"content\n")
                    self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
                    self.assertFalse(target.exists())
                finally:
                    if target.exists():
                        target.unlink()
                    artifact.unlink()

    def test_human_owned_file_can_only_be_existing_sufficient(self) -> None:
        target = self.repo / "AGENTS.md"
        target.write_text(
            "# Human instructions\n\n- Preserve this file.\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        manifest = self.inspect()
        state = self.apply(manifest, self.decision(manifest, "existing-sufficient"))
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertEqual(
            target.read_text(), "# Human instructions\n\n- Preserve this file.\n"
        )

    def test_human_project_instructions_can_satisfy_compatibility_intent(self) -> None:
        target = self.repo / "AGENTS.md"
        target.write_text(
            "# Compatibility\n\n"
            "- Preserve supported behavior and public interfaces for existing users.\n"
            "- Breaking supported interfaces requires approval, migration planning, "
            "and regression tests.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)

        manifest = self.inspect()
        state = self.apply(manifest, self.decision(manifest, "existing-sufficient"))

        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertFalse(state["reload_required"])

    def test_active_override_blocks_missing_compatibility_rules(self) -> None:
        override = self.repo / "AGENTS.override.md"
        override.write_text(
            "# Local override\n\n- Run the focused tests.\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "AGENTS.override.md"], cwd=self.repo, check=True
        )
        manifest = self.inspect()
        decision = self.decision(manifest, "needed")
        decision["rules"] = self.compatibility_rules()

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(manifest, decision)

        self.assertEqual(caught.exception.code, "EXISTING_INSTRUCTIONS_GAP")
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_needed_rules_attach_to_human_file_and_preserve_prefix(self) -> None:
        target = self.repo / "AGENTS.md"
        human = b"# Human instructions\r\n\r\n- Preserve these exact bytes.\r\n"
        target.write_bytes(human)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)

        manifest = self.inspect()
        state = self.apply(manifest, self.decision(manifest, "needed"))

        self.assertEqual(state["outcome"], "attached")
        parsed = contracts._parse_generated(target.read_bytes())
        assert parsed is not None
        self.assertEqual(parsed["prefix"], human)
        self.assertEqual(parsed["version"], 3)
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "attached",
        )

    def test_human_prefix_edit_preserves_managed_region_ownership(self) -> None:
        target = self.repo / "AGENTS.md"
        original = b"# Human instructions\n\n- Original.\n"
        target.write_bytes(original)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        parsed = contracts._parse_generated(target.read_bytes())
        assert parsed is not None
        generated = target.read_bytes()
        managed_body = generated[generated.find(contracts.GENERATED_MARKER_PREFIX) :]

        changed = b"# Human instructions\n\n- Edited by the project.\n"
        target.write_bytes(changed + b"\n\n" + managed_body)
        current = self.inspect()
        self.assertEqual(current["target"]["file_status"], "managed")

        state = self.apply(current, self.decision(current, "needed"))

        self.assertEqual(state["outcome"], "existing-sufficient")
        final = contracts._parse_generated(target.read_bytes())
        assert final is not None
        self.assertEqual(final["prefix"], changed)

    def test_retirement_removes_only_managed_region(self) -> None:
        target = self.repo / "AGENTS.md"
        human = b"# Human instructions\n\n- Keep me exactly.\n"
        target.write_bytes(human)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        current = self.inspect()

        state = self.apply(
            current,
            self.decision(
                current,
                "not-needed",
                approval={
                    "action": "retire",
                    "target_sha256": str(current["target"]["sha256"]),
                },
            ),
        )

        self.assertEqual(state["outcome"], "retired")
        self.assertEqual(target.read_bytes(), human)
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "retired",
        )

    def test_untracked_human_instruction_source_is_rejected(self) -> None:
        (self.repo / "AGENTS.md").write_text(
            "# Human instructions\n\n- Preserve this file.\n", encoding="utf-8"
        )

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()

        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")

    def test_dormant_managed_file_conflicts_with_active_override(self) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        (self.repo / "AGENTS.override.md").write_text(
            "# Human override\n\n- Follow this active file.\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "AGENTS.override.md"], cwd=self.repo, check=True)
        current = self.inspect()
        self.assertEqual(current["target"]["file_status"], "managed")
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "existing-sufficient"))
        self.assertEqual(caught.exception.code, "EXISTING_INSTRUCTIONS_GAP")

    def test_legacy_markers_fail_closed_without_compatibility(self) -> None:
        body = b"# Legacy\n"
        digest = contracts._sha256_bytes(body).encode()
        markers = (
            b"<!-- project-agent-instructions:generated-v1 body-sha256="
            + digest
            + b" -->\n\n",
            b"<!-- project-agent-instructions:managed-v2 manifest-sha256="
            + b"1" * 64
            + b" decision-sha256="
            + b"2" * 64
            + b" body-sha256="
            + digest
            + b" -->\n\n",
        )
        for marker in markers:
            with self.subTest(marker=marker.split()[1]):
                (self.repo / "AGENTS.md").write_bytes(marker + body)
                subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
                manifest = self.inspect()
                self.assertEqual(manifest["target"]["file_status"], "legacy")
                with self.assertRaises(contracts.ProjectInstructionsError) as caught:
                    self.apply(manifest, self.decision(manifest, "needed"))
                self.assertEqual(caught.exception.code, "LEGACY_GENERATED_FILE")

    def test_malformed_or_multiple_managed_markers_fail_closed(self) -> None:
        target = self.repo / "AGENTS.md"
        malformed = b"<!-- project-agent-instructions:managed-v3 invalid -->\n"
        target.write_bytes(malformed)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "UNSAFE_TARGET")

        target.unlink()
        initial = self.inspect()
        decision = self.decision(initial, "needed")
        _, body, _, _ = workflow._validate_decision(decision, initial)
        assert body is not None
        region = target_io._generated_content(
            body,
            contracts._generation_manifest_sha256(initial),
            contracts._generation_decision_sha256("needed", body, self.evidence()),
        )
        target.write_bytes(region + b"\n\n" + region)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "UNSAFE_TARGET")

    def test_lock_or_backup_blocks_every_inspection(self) -> None:
        for name in discovery.RECOVERY_NAMES:
            with self.subTest(name=name):
                artifact = self.repo / name
                artifact.write_text("recovery\n", encoding="utf-8")
                try:
                    with self.assertRaises(
                        contracts.ProjectInstructionsError
                    ) as caught:
                        self.inspect()
                    self.assertEqual(caught.exception.code, "RECOVERY_REQUIRED")
                finally:
                    artifact.unlink()

    def test_stale_spec_receipt_is_rejected(self) -> None:
        (self.repo / "docs/design.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "SPEC_VALIDATION_REQUIRED")

    def test_unchanged_specs_carry_receipt_to_descendant_commit(self) -> None:
        (self.repo / "README.md").write_text("product-only change\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "advance without spec drift"],
            cwd=self.repo,
            check=True,
        )

        manifest = self.inspect()

        self.assertEqual(manifest["spec_receipt"]["path"], str(self.receipt_path))

    def test_forged_receipt_cannot_authorize_marker_only_specs(self) -> None:
        requirements = self.repo / "docs/requirements.md"
        design = self.repo / "docs/design.md"
        requirements.write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n# Requirements\n",
            encoding="utf-8",
        )
        design.write_text(
            "---\nschema: agentic-sdlc.design.v1\n---\n# Design\n",
            encoding="utf-8",
        )
        forged = json.loads(self.receipt_path.read_text())
        forged["validator"] = "forged"
        forged["traceability_sha256"] = "f" * 64
        forged["requirements"]["sha256"] = contracts._sha256_bytes(
            requirements.read_bytes()
        )
        forged["design"]["sha256"] = contracts._sha256_bytes(design.read_bytes())
        self.write_json("spec-receipt.json", forged)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "SPEC_VALIDATION_REQUIRED")

    def test_evidence_requires_tracking_digest_and_locator(self) -> None:
        manifest = self.inspect()
        for field, value in (("locator", "absent"), ("sha256", "0" * 64)):
            decision = self.decision(manifest, "needed")
            decision["evidence"][0][field] = value
            with (
                self.subTest(field=field),
                self.assertRaises(contracts.ProjectInstructionsError),
            ):
                self.apply(manifest, decision)

    def test_layered_profile_and_runtime_overrides_are_fingerprinted(self) -> None:
        (self.codex_home / "config.toml").write_text(
            "project_doc_max_bytes = 8000\n", encoding="utf-8"
        )
        profile = self.codex_home / "small.config.toml"
        profile.write_text("project_doc_max_bytes = 7000\n", encoding="utf-8")
        base_manifest = self.inspect()
        self.assertEqual(
            base_manifest["config_context"]["project_doc_max_bytes"],
            8000,
        )
        runtime = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": "small",
                "overrides": {"project_doc_max_bytes": 6000},
            },
        )
        manifest = self.inspect(runtime_config=runtime)
        config = manifest["config_context"]
        self.assertEqual(config["project_doc_max_bytes"], 6000)
        self.assertEqual(
            [Path(item["path"]).resolve() for item in config["sources"]],
            [
                (self.codex_home / "config.toml").resolve(),
                profile.resolve(),
                runtime.resolve(),
            ],
        )
        self.assertEqual(
            config["runtime_config_sha256"],
            contracts._sha256_bytes(runtime.read_bytes()),
        )
        profile.write_text("project_doc_max_bytes = 6500\n", encoding="utf-8")
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(manifest, self.decision(manifest, "needed"))
        self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")

    def test_trusted_project_config_overrides_user_config_in_order(self) -> None:
        (self.codex_home / "config.toml").write_text(
            f'[projects."{self.repo}"]\ntrust_level = "trusted"\n'
            "project_doc_max_bytes = 9000\n",
            encoding="utf-8",
        )
        project_config = self.repo / ".codex" / "config.toml"
        project_config.parent.mkdir()
        project_config.write_text("project_doc_max_bytes = 5000\n", encoding="utf-8")
        manifest = self.inspect()
        config = manifest["config_context"]
        self.assertEqual(config["project_doc_max_bytes"], 5000)
        self.assertEqual(
            [Path(item["path"]).resolve() for item in config["sources"]],
            [
                (self.codex_home / "config.toml").resolve(),
                project_config.resolve(),
                self.runtime_path.resolve(),
            ],
        )

    def test_stale_ownership_receipt_blocks_managed_refresh(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        receipt = json.loads((self.private / "ownership.json").read_text())
        receipt["body_sha256"] = "0" * 64
        self.write_json("ownership.json", receipt)
        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "needed"))
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")

    def test_marker_only_receipt_drift_requires_exact_readoption(self) -> None:
        first = self.inspect()
        self.apply(first, self.decision(first, "needed"))
        target = self.repo / "AGENTS.md"
        content = target.read_bytes()
        parsed = contracts._parse_generated(content)
        assert parsed is not None
        marker = contracts.GENERATED_MARKER_RE.match(content)
        assert marker is not None
        body = content[marker.end() :]
        target.write_bytes(target_io._generated_content(body, "1" * 64, "2" * 64))

        current = self.inspect()
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, self.decision(current, "needed"))
        self.assertEqual(caught.exception.code, "ADOPTION_APPROVAL_REQUIRED")

        wrong = self.decision(
            current,
            "needed",
            approval={"action": "adopt", "target_sha256": "0" * 64},
        )
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(current, wrong)
        self.assertEqual(caught.exception.code, "ADOPTION_APPROVAL_REQUIRED")

        approved = self.decision(
            current,
            "needed",
            approval={
                "action": "adopt",
                "target_sha256": str(current["target"]["sha256"]),
            },
        )
        state = self.apply(current, approved)
        self.assertEqual(state["outcome"], "refreshed")
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)["outcome"],
            "refreshed",
        )

    def test_config_change_after_inspect_blocks_apply(self) -> None:
        (self.codex_home / "config.toml").write_text(
            "project_doc_max_bytes = 9000\n", encoding="utf-8"
        )
        manifest = self.inspect()
        (self.codex_home / "config.toml").write_text(
            "project_doc_max_bytes = 8000\n", encoding="utf-8"
        )
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(manifest, self.decision(manifest, "needed"))
        self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")

    def test_selected_subproject_uses_enclosing_git_root_marker(self) -> None:
        project = self.repo / "service"
        (project / "docs").mkdir(parents=True)
        for name in ("requirements.md", "design.md"):
            (project / "docs" / name).write_bytes(
                (self.repo / "docs" / name).read_bytes()
            )
        ancestor = self.repo / "AGENTS.md"
        ancestor.write_text(
            "# Repository instructions\n\n- Preserve supported behavior.\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "service/docs", "AGENTS.md"],
            cwd=self.repo,
            check=True,
        )
        self.write_json("spec-receipt.json", self.receipt(project))

        manifest = self.inspect(project=project)

        self.assertEqual(manifest["project_scope"], "service")
        self.assertEqual(manifest["target"]["path"], str(project / "AGENTS.md"))
        self.assertEqual(
            [entry["path"] for entry in manifest["ancestor_project_instructions"]],
            [str(ancestor.resolve())],
        )

    def test_nearest_matching_ancestor_bounds_instruction_chain(self) -> None:
        project = self.repo / "packages" / "service"
        (project / "docs").mkdir(parents=True)
        for name in ("requirements.md", "design.md"):
            (project / "docs" / name).write_bytes(
                (self.repo / "docs" / name).read_bytes()
            )
        root_agents = self.repo / "AGENTS.md"
        root_agents.write_text("# Repository instructions\n", encoding="utf-8")
        package_root = self.repo / "packages"
        (package_root / ".project-root").write_text("marker\n", encoding="utf-8")
        package_agents = package_root / "AGENTS.md"
        package_agents.write_text("# Package instructions\n", encoding="utf-8")
        subprocess.run(
            [
                "git",
                "add",
                "AGENTS.md",
                "packages/.project-root",
                "packages/AGENTS.md",
                "packages/service/docs",
            ],
            cwd=self.repo,
            check=True,
        )
        self.write_json("spec-receipt.json", self.receipt(project))
        runtime = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": None,
                "overrides": {"project_root_markers": [".project-root"]},
            },
        )

        manifest = self.inspect(project=project, runtime_config=runtime)

        self.assertEqual(
            [entry["path"] for entry in manifest["ancestor_project_instructions"]],
            [str(package_agents.resolve())],
        )
        self.assertNotIn(
            str(root_agents.resolve()),
            [entry["path"] for entry in manifest["ancestor_project_instructions"]],
        )

    def test_missing_effective_root_marker_within_git_root_fails_closed(self) -> None:
        project = self.repo / "service"
        (self.root / ".project-root").write_text("outside Git\n", encoding="utf-8")
        (project / "docs").mkdir(parents=True)
        for name in ("requirements.md", "design.md"):
            (project / "docs" / name).write_bytes(
                (self.repo / "docs" / name).read_bytes()
            )
        subprocess.run(["git", "add", "service/docs"], cwd=self.repo, check=True)
        self.write_json("spec-receipt.json", self.receipt(project))
        runtime = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": None,
                "overrides": {"project_root_markers": [".project-root"]},
            },
        )

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect(project=project, runtime_config=runtime)

        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")

    def test_parent_directory_is_not_a_valid_project_root_marker(self) -> None:
        project = self.repo / "service"
        (project / "docs").mkdir(parents=True)
        for name in ("requirements.md", "design.md"):
            (project / "docs" / name).write_bytes(
                (self.repo / "docs" / name).read_bytes()
            )
        subprocess.run(["git", "add", "service/docs"], cwd=self.repo, check=True)
        self.write_json("spec-receipt.json", self.receipt(project))
        runtime = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": None,
                "overrides": {"project_root_markers": [".."]},
            },
        )
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect(project=project, runtime_config=runtime)
        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")

    def test_empty_root_markers_make_selected_directory_the_project_root(self) -> None:
        project = self.repo / "service"
        (project / "docs").mkdir(parents=True)
        for name in ("requirements.md", "design.md"):
            (project / "docs" / name).write_bytes(
                (self.repo / "docs" / name).read_bytes()
            )
        ancestor = self.repo / "AGENTS.md"
        ancestor.write_text("# Repository instructions\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "service/docs", "AGENTS.md"],
            cwd=self.repo,
            check=True,
        )
        self.write_json("spec-receipt.json", self.receipt(project))
        runtime = self.write_json(
            "runtime.json",
            {
                "schema": contracts.RUNTIME_CONFIG_SCHEMA,
                "profile": None,
                "overrides": {"project_root_markers": []},
            },
        )

        manifest = self.inspect(project=project, runtime_config=runtime)

        self.assertEqual(manifest["project_scope"], "service")
        self.assertEqual(manifest["config_context"]["project_root_markers"], [])
        self.assertEqual(manifest["ancestor_project_instructions"], [])

    def test_ignored_target_is_rejected_before_generation(self) -> None:
        (self.repo / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.repo, check=True)

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()

        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")
        (self.repo / "AGENTS.md").write_text(
            "# Human instructions\n\n- Keep this tracked file.\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-f", "AGENTS.md"], cwd=self.repo, check=True)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")

    def test_unsafe_rule_and_unjustified_budget_are_rejected(self) -> None:
        manifest = self.inspect()
        decision = self.decision(manifest, "needed")
        decision["rules"][0]["instruction"] = (
            "Use https://private.example.invalid for tests."
        )
        with self.assertRaises(contracts.ProjectInstructionsError):
            self.apply(manifest, decision)
        for separator in ("\r", "\u2028"):
            changed_decision = self.decision(manifest, "needed")
            changed_decision["rules"][0]["instruction"] = (
                f"Run focused tests.{separator}## Injected"
            )
            with self.assertRaises(contracts.ProjectInstructionsError):
                self.apply(manifest, changed_decision)
        decision = self.decision(manifest, "needed")
        decision["budget_exception"] = "No exception is needed."
        with self.assertRaises(contracts.ProjectInstructionsError):
            self.apply(manifest, decision)

    def test_rendered_paths_and_common_secret_forms_are_rejected(self) -> None:
        manifest = self.inspect()
        decision = self.decision(manifest, "needed")
        for field, value in (
            ("project_scope", "service`\n\n## Injected"),
            ("requirements", "docs/requirements`bad.md"),
        ):
            changed = dict(manifest)
            if field == "requirements":
                changed[field] = dict(manifest[field])
                changed[field]["path"] = value
            else:
                changed[field] = value
            with (
                self.subTest(field=field),
                self.assertRaises(contracts.ProjectInstructionsError),
            ):
                contracts._render_body(
                    changed, decision["rules"], None, {"docs/design.md"}
                )
        for unsafe in (
            "Use password=correct-horse-battery-staple.",
            "Use npm_abcdefghijklmnopqrstuvwxyz123456 for releases.",
            "Use eyJabcdefgh.eyJijklmnop.qrstuvwxyz12 for access.",
        ):
            changed_decision = self.decision(manifest, "needed")
            changed_decision["rules"][0]["instruction"] = unsafe
            with self.assertRaises(contracts.ProjectInstructionsError):
                self.apply(manifest, changed_decision)

    def test_symlinked_project_config_directory_is_rejected(self) -> None:
        (self.codex_home / "config.toml").write_text(
            f'[projects."{self.repo}"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        outside = self.root / "outside-codex"
        outside.mkdir()
        (outside / "config.toml").write_text(
            "project_doc_max_bytes = 5000\n", encoding="utf-8"
        )
        (self.repo / ".codex").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "DISCOVERY_CONTEXT_UNVERIFIED")

    def test_create_io_failure_removes_only_owned_partial_file(self) -> None:
        target = self.repo / "AGENTS.md"
        with mock.patch.object(
            target_io.os, "fchmod", side_effect=PermissionError("simulated")
        ):
            with self.assertRaises(contracts.ProjectInstructionsError) as caught:
                target_io._exclusive_create(target, b"content\n")
        self.assertEqual(caught.exception.code, "UNSAFE_TARGET")
        self.assertFalse(target.exists())

    def test_private_state_fsyncs_file_then_directory(self) -> None:
        observed_directory: list[bool] = []
        actual_fsync = os.fsync

        def track_fsync(descriptor: int) -> None:
            observed_directory.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
            actual_fsync(descriptor)

        with mock.patch.object(
            private_state.os,
            "fsync",
            side_effect=track_fsync,
        ):
            private_state._write_private_json(
                self.private / "durable.json",
                {"schema": "test.durable.v1"},
                self.repo,
                self.private,
            )
        self.assertEqual(observed_directory, [False, True])

    def test_cli_inspect_requires_private_receipt_and_emits_v2_manifest(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "inspect",
                "--project-root",
                str(self.repo),
                "--spec-owner",
                "maintain-project-specs",
                "--codex-home",
                str(self.codex_home),
                "--private-root",
                str(self.private),
                "--spec-receipt",
                str(self.receipt_path),
                "--runtime-config",
                str(self.runtime_path),
                "--output",
                "cli-manifest.json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.private / "cli-manifest.json").read_text())
        self.assertEqual(manifest["schema"], contracts.MANIFEST_SCHEMA)

    def test_cli_inspect_requires_explicit_codex_home(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "inspect",
                "--project-root",
                str(self.repo),
                "--spec-owner",
                "maintain-project-specs",
                "--private-root",
                str(self.private),
                "--spec-receipt",
                str(self.receipt_path),
                "--runtime-config",
                str(self.runtime_path),
                "--output",
                "cli-manifest.json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--codex-home", result.stderr)


if __name__ == "__main__":
    unittest.main()
