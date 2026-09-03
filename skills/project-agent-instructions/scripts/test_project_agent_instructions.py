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
import threading
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
REQUIREMENTS = """<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
---
schema: maintain-project-specs/requirements-v2
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
DESIGN = """<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
---
schema: maintain-project-specs/design-v2
project: Example
status: ready
created_by_skill: maintain-project-specs
updated_by_skill: maintain-project-specs
source_requirements: docs/requirements.md
---

# Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=unassessed priority=P0 version=1 -->
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

#### Implementation Evidence

The implementation predates schema v2 evidence tracking.

#### Verification Evidence

Independent verification predates schema v2 evidence tracking.

<!-- /FEATURE: FEAT-001 -->
<!-- maintain-project-specs:design:end -->
"""


class ProjectAgentInstructionsTests(unittest.TestCase):
    PRIOR_SESSION = "01900000-0000-7fff-bfff-ffffffffffff"
    RETIRED_SESSION = "01900000-0000-7000-8000-000000000001"
    CURRENT_SESSION = "018fffff-ffff-7fff-bfff-ffffffffffff"

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
        return self.write_json_at(path, value)

    def write_json_at(self, path: Path, value: object) -> Path:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(path, 0o600)
        return path

    def sealed_session(
        self, workspace: Path, name: str
    ) -> tuple[Path, dict[str, object]]:
        session_root = workspace / name
        session_root.mkdir(mode=0o700)
        private_root = session_root / "project-instructions"
        private_state._ensure_private_root(private_root, self.repo)
        manifest = self.inspect()
        workflow.carry_forward_ownership(manifest, private_root)
        state = workflow.apply_decision(
            self.write_json_at(private_root / "manifest.json", manifest),
            self.write_json_at(
                private_root / "decision.json",
                self.decision(manifest, "needed"),
            ),
            private_root / "ownership.json",
            private_root / "state.json",
            private_root,
        )
        self.seal_session(private_root, state, manifest)
        return private_root, state

    def seal_session(
        self,
        private_root: Path,
        state: dict[str, object],
        manifest: dict[str, object],
    ) -> None:
        state_raw = (private_root / "state.json").read_bytes()
        self.write_json_at(
            private_root.parent / "lifecycle.json",
            {
                "schema": workflow.SEALED_LIFECYCLE_SCHEMA,
                "git_head_at_prompt": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip(),
                "turn_sha256": "1" * 64,
                "phase": "sealed",
                "project_scope": manifest["project_scope"],
                "receipt_sha256": "2" * 64,
                "requirements_sha256": dict(state["requirements"])["sha256"],
                "design_sha256": dict(state["design"])["sha256"],
                "rules_path": workflow.SEALED_LIFECYCLE_RULES_PATH,
                "rules_sha256": "3" * 64,
                "project_instructions_state_sha256": contracts._sha256_bytes(
                    state_raw
                ),
                "project_instructions_reload_required": state["reload_required"],
                "write_epoch": 1,
                "planned_write_epoch": 1,
                "waiver": None,
            },
        )

    def retired_session(
        self, workspace: Path, name: str
    ) -> tuple[Path, dict[str, object]]:
        session_root = workspace / name
        session_root.mkdir(mode=0o700)
        private_root = session_root / "project-instructions"
        private_state._ensure_private_root(private_root, self.repo)
        manifest = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(manifest, private_root),
            "carried-forward",
        )
        decision = self.decision(
            manifest,
            "not-needed",
            approval={
                "action": "retire",
                "target_sha256": str(dict(manifest["target"])["sha256"]),
            },
        )
        state = workflow.apply_decision(
            self.write_json_at(private_root / "manifest.json", manifest),
            self.write_json_at(private_root / "decision.json", decision),
            private_root / "ownership.json",
            private_root / "state.json",
            private_root,
        )
        self.seal_session(private_root, state, manifest)
        return private_root, state

    def reseal_session(self, private_root: Path) -> None:
        ownership_path = private_root / "ownership.json"
        state_path = private_root / "state.json"
        lifecycle_path = private_root.parent / "lifecycle.json"
        state = json.loads(state_path.read_text())
        state["ownership_file_sha256"] = contracts._sha256_bytes(
            ownership_path.read_bytes()
        )
        self.write_json_at(state_path, state)
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["project_instructions_state_sha256"] = contracts._sha256_bytes(
            state_path.read_bytes()
        )
        lifecycle["project_instructions_reload_required"] = state["reload_required"]
        self.write_json_at(lifecycle_path, lifecycle)

    def drop_workspace_registry(self, private_root: Path) -> None:
        _, registry_root = workflow._workspace_ownership_root(
            private_root, self.repo
        )
        for child in registry_root.iterdir():
            child.unlink()
        registry_root.rmdir()

    def workspace_registry_path(self, private_root: Path) -> Path:
        _, registry_root = workflow._workspace_ownership_root(
            private_root, self.repo
        )
        return registry_root / workflow.WORKSPACE_OWNERSHIP_REGISTRY

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

    def test_cli_inherited_lock_failure_is_structured(self) -> None:
        manifest = self.inspect()
        manifest_path = self.write_json("manifest.json", manifest)
        decision_path = self.write_json(
            "decision.json", self.decision(manifest, "not-needed")
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "render",
                "--private-root",
                str(self.private),
                "--manifest",
                str(manifest_path),
                "--decision",
                str(decision_path),
                "--output",
                str(self.private / "rules.md"),
                "--state",
                str(self.private / "render-state.json"),
                "--workspace-lock-fd",
                "1",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout)["code"], "ENVIRONMENT_BLOCKER")

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

    def test_sealed_sibling_session_carries_exact_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        self.drop_workspace_registry(prior_private)
        target = self.repo / "AGENTS.md"
        before = target.read_bytes()

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        current = self.inspect()

        self.assertEqual(
            workflow.carry_forward_ownership(current, current_private),
            "carried-forward",
        )
        state = workflow.apply_decision(
            self.write_json_at(current_private / "manifest.json", current),
            self.write_json_at(
                current_private / "decision.json",
                self.decision(current, "needed"),
            ),
            current_private / "ownership.json",
            current_private / "state.json",
            current_private,
        )
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertEqual(target.read_bytes(), before)

    def test_unanimous_sealed_history_bootstraps_one_registry_entry(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        first_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        _second_private, _state = self.sealed_session(
            workspace, "legacy-second-session"
        )
        self.drop_workspace_registry(first_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        manifest = self.inspect()

        self.assertEqual(
            workflow.carry_forward_ownership(manifest, current_private),
            "carried-forward",
        )
        registry = json.loads(
            self.workspace_registry_path(current_private).read_text()
        )
        self.assertEqual(registry["schema"], workflow.WORKSPACE_OWNERSHIP_SCHEMA)
        self.assertEqual(registry["generation"], 1)
        self.assertEqual(len(registry["entries"]), 1)

    def test_legacy_bootstrap_ignores_regular_workspace_files(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        self.drop_workspace_registry(prior_private)
        (workspace / ".DS_Store").write_bytes(b"fixture metadata")

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "carried-forward",
        )

    def test_legacy_bootstrap_rejects_conflicting_active_generations(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        first_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        target = self.repo / "AGENTS.md"
        first_bytes = target.read_bytes()
        second_session = workspace / "conflicting-active-session"
        second_session.mkdir(mode=0o700)
        second_private = second_session / "project-instructions"
        private_state._ensure_private_root(second_private, self.repo)
        second_manifest = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(second_manifest, second_private),
            "carried-forward",
        )
        second_decision = self.decision(second_manifest, "needed")
        second_decision["rules"][0]["instruction"] = (
            "Run focused contract tests and inspect exact failures."
        )
        second_state = workflow.apply_decision(
            self.write_json_at(second_private / "manifest.json", second_manifest),
            self.write_json_at(second_private / "decision.json", second_decision),
            second_private / "ownership.json",
            second_private / "state.json",
            second_private,
        )
        self.seal_session(second_private, second_state, second_manifest)
        target.write_bytes(first_bytes)
        self.drop_workspace_registry(first_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())
        registry_path = self.workspace_registry_path(current_private)
        blocked = json.loads(registry_path.read_text())
        self.assertEqual(blocked["generation"], 1)
        self.assertEqual(
            next(iter(blocked["entries"].values()))["status"], "blocked"
        )

        for path in sorted(
            second_session.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        second_session.rmdir()
        retry_session = workspace / "retry-after-conflict-removed"
        retry_session.mkdir(mode=0o700)
        retry_private = retry_session / "project-instructions"
        private_state._ensure_private_root(retry_private, self.repo)
        retry_manifest = self.inspect()

        self.assertEqual(
            workflow.carry_forward_ownership(retry_manifest, retry_private),
            "unproven",
        )
        self.assertEqual(json.loads(registry_path.read_text()), blocked)
        approval = {
            "action": "adopt",
            "target_sha256": str(dict(retry_manifest["target"])["sha256"]),
        }
        state = workflow.apply_decision(
            self.write_json_at(retry_private / "manifest.json", retry_manifest),
            self.write_json_at(
                retry_private / "decision.json",
                self.decision(retry_manifest, "needed", approval=approval),
            ),
            retry_private / "ownership.json",
            retry_private / "state.json",
            retry_private,
        )
        self.assertEqual(state["outcome"], "adopted")
        adopted = json.loads(registry_path.read_text())
        self.assertEqual(adopted["generation"], 2)
        self.assertEqual(
            next(iter(adopted["entries"].values()))["status"], "active"
        )

    def test_workspace_registry_is_monotonic_and_retirement_is_a_tombstone(
        self,
    ) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        registry_path = self.workspace_registry_path(self.private)
        active = json.loads(registry_path.read_text())
        self.assertEqual(active["generation"], 1)
        entry = next(iter(active["entries"].values()))
        self.assertEqual(entry["status"], "active")

        current = self.inspect()
        decision = self.decision(
            current,
            "not-needed",
            approval={
                "action": "retire",
                "target_sha256": str(dict(current["target"])["sha256"]),
            },
        )
        self.apply(current, decision)
        retired = json.loads(registry_path.read_text())
        self.assertEqual(retired["generation"], 2)
        entry = next(iter(retired["entries"].values()))
        self.assertEqual(entry["status"], "retired")
        self.assertIsNone(entry["target_sha256"])

    def test_verify_requires_matching_workspace_publication(self) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        registry_path = self.workspace_registry_path(self.private)
        registry = json.loads(registry_path.read_text())
        entry = next(iter(registry["entries"].values()))
        entry["status"] = "retired"
        entry["target_sha256"] = None
        entry["ownership"]["status"] = "retired"
        self.write_json_at(registry_path, registry)

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            workflow.verify_state(self.private / "state.json", self.private)
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")

    def test_registry_tombstone_blocks_restored_active_bytes_without_ordering(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)
        target = self.repo / "AGENTS.md"
        active_bytes = target.read_bytes()
        self.retired_session(workspace, self.RETIRED_SESSION)
        target.write_bytes(active_bytes)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_missing_registry_does_not_trust_current_receipt_past_retirement(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        active_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        target = self.repo / "AGENTS.md"
        active_bytes = target.read_bytes()
        retired_private, _state = self.retired_session(
            workspace, self.RETIRED_SESSION
        )
        target.write_bytes(active_bytes)
        self.drop_workspace_registry(retired_private)

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            workflow.carry_forward_ownership(self.inspect(), active_private)
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")

    def test_deleted_registry_file_fails_closed_without_retirement_history(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        active_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        target = self.repo / "AGENTS.md"
        active_bytes = target.read_bytes()
        retired_private, _state = self.retired_session(
            workspace, self.RETIRED_SESSION
        )
        target.write_bytes(active_bytes)
        self.workspace_registry_path(retired_private).unlink()
        retired_session = retired_private.parent
        for path in sorted(
            retired_session.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        retired_session.rmdir()

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            workflow.carry_forward_ownership(self.inspect(), active_private)
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")

    def test_malformed_registry_never_falls_back_to_exact_sealed_history(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        registry_path = self.workspace_registry_path(prior_private)
        original = registry_path.read_bytes()
        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        cases = ("malformed", "mode", "hardlink")
        for case in cases:
            with self.subTest(case=case):
                link_path = registry_path.with_name("registry-link.json")
                link_path.unlink(missing_ok=True)
                registry_path.write_bytes(original)
                os.chmod(registry_path, 0o600)
                if case == "malformed":
                    registry = json.loads(original)
                    registry["generation"] = "1"
                    self.write_json_at(registry_path, registry)
                elif case == "mode":
                    os.chmod(registry_path, 0o640)
                else:
                    os.link(registry_path, link_path)
                with self.assertRaises(
                    contracts.ProjectInstructionsError
                ) as caught:
                    workflow.carry_forward_ownership(
                        self.inspect(), current_private
                    )
                self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")
                self.assertFalse((current_private / "ownership.json").exists())
        link_path.unlink(missing_ok=True)

    def test_malformed_registry_blocks_creation_before_project_mutation(
        self,
    ) -> None:
        manifest = self.inspect()
        registry_path = self.workspace_registry_path(self.private)
        self.write_json_at(
            registry_path,
            {
                "schema": workflow.WORKSPACE_OWNERSHIP_SCHEMA,
                "generation": "invalid",
                "entries": {},
            },
        )

        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            self.apply(manifest, self.decision(manifest, "needed"))
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_retirement_lock_blocks_stale_active_writer_from_resurrection(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)

        writer_session = workspace / "stale-active-writer"
        writer_session.mkdir(mode=0o700)
        writer_private = writer_session / "project-instructions"
        private_state._ensure_private_root(writer_private, self.repo)
        writer_manifest = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(writer_manifest, writer_private),
            "carried-forward",
        )
        writer_manifest_path = self.write_json_at(
            writer_private / "manifest.json", writer_manifest
        )
        writer_decision_path = self.write_json_at(
            writer_private / "decision.json",
            self.decision(writer_manifest, "needed"),
        )

        retire_session = workspace / "retirement-writer"
        retire_session.mkdir(mode=0o700)
        retire_private = retire_session / "project-instructions"
        private_state._ensure_private_root(retire_private, self.repo)
        retire_manifest = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(retire_manifest, retire_private),
            "carried-forward",
        )
        retire_manifest_path = self.write_json_at(
            retire_private / "manifest.json", retire_manifest
        )
        retire_decision_path = self.write_json_at(
            retire_private / "decision.json",
            self.decision(
                retire_manifest,
                "not-needed",
                approval={
                    "action": "retire",
                    "target_sha256": str(
                        dict(retire_manifest["target"])["sha256"]
                    ),
                },
            ),
        )
        entered = threading.Event()
        release = threading.Event()
        retirement_errors: list[BaseException] = []
        actual_delete = workflow._guarded_delete

        def paused_delete(*args: object, **kwargs: object) -> None:
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("timed out waiting to release retirement")
            actual_delete(*args, **kwargs)

        def run_retirement() -> None:
            try:
                workflow.apply_decision(
                    retire_manifest_path,
                    retire_decision_path,
                    retire_private / "ownership.json",
                    retire_private / "state.json",
                    retire_private,
                )
            except BaseException as error:  # pragma: no cover - asserted below
                retirement_errors.append(error)

        with mock.patch.object(
            workflow, "_guarded_delete", side_effect=paused_delete
        ):
            thread = threading.Thread(target=run_retirement)
            thread.start()
            self.assertTrue(entered.wait(timeout=10))
            with self.assertRaises(contracts.ProjectInstructionsError) as caught:
                workflow.apply_decision(
                    writer_manifest_path,
                    writer_decision_path,
                    writer_private / "ownership.json",
                    writer_private / "state.json",
                    writer_private,
                )
            self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")
            release.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(retirement_errors, [])
        self.assertFalse((self.repo / "AGENTS.md").exists())
        registry = json.loads(
            self.workspace_registry_path(retire_private).read_text()
        )
        self.assertEqual(next(iter(registry["entries"].values()))["status"], "retired")

    def test_registry_lock_contention_fails_as_concurrent_modification(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        registry_root = self.workspace_registry_path(prior_private).parent
        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        descriptor = os.open(registry_root / ".ownership.lock", os.O_RDWR)
        try:
            render_state.fcntl.flock(descriptor, render_state.fcntl.LOCK_EX)
            with self.assertRaises(contracts.ProjectInstructionsError) as caught:
                workflow.carry_forward_ownership(
                    self.inspect(), current_private
                )
            self.assertEqual(caught.exception.code, "CONCURRENT_MODIFICATION")
        finally:
            render_state.fcntl.flock(descriptor, render_state.fcntl.LOCK_UN)
            os.close(descriptor)

    def test_first_use_registry_root_has_only_one_initializer(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        first_session = workspace / "first"
        second_session = workspace / "second"
        first_session.mkdir(mode=0o700)
        second_session.mkdir(mode=0o700)
        first_private = first_session / "project-instructions"
        second_private = second_session / "project-instructions"
        private_state._ensure_private_root(first_private, self.repo)
        private_state._ensure_private_root(second_private, self.repo)
        entered = threading.Event()
        release = threading.Event()
        results: list[tuple[Path, Path]] = []
        errors: list[BaseException] = []
        actual_rename = workflow.os.rename

        def paused_rename(source: Path, destination: Path) -> None:
            if (
                threading.current_thread().name == "registry-creator"
                and Path(destination).name == workflow.WORKSPACE_OWNERSHIP_ROOT
            ):
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("timed out waiting for first initializer")
            actual_rename(source, destination)

        def create_first() -> None:
            try:
                results.append(
                    workflow._workspace_ownership_root(first_private, self.repo)
                )
            except BaseException as error:  # pragma: no cover - asserted below
                errors.append(error)

        with mock.patch.object(
            workflow.os, "rename", side_effect=paused_rename
        ):
            thread = threading.Thread(
                target=create_first,
                name="registry-creator",
            )
            thread.start()
            self.assertTrue(entered.wait(timeout=10))
            second_result = workflow._workspace_ownership_root(
                second_private, self.repo
            )
            release.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], second_result[1])
        registry = workflow._load_workspace_registry(second_result[1])
        self.assertEqual(registry["generation"], 0)
        manifest = self.inspect()
        state = workflow.apply_decision(
            self.write_json_at(second_private / "manifest.json", manifest),
            self.write_json_at(
                second_private / "decision.json",
                self.decision(manifest, "needed"),
            ),
            second_private / "ownership.json",
            second_private / "state.json",
            second_private,
        )
        self.assertEqual(state["outcome"], "created")
        self.assertEqual(
            workflow._load_workspace_registry(second_result[1])["generation"],
            1,
        )

    def test_unsealed_sibling_session_does_not_confer_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        lifecycle_path = prior_private.parent / "lifecycle.json"
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["phase"] = "planned"
        self.write_json_at(lifecycle_path, lifecycle)
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        current = self.inspect()

        self.assertEqual(
            workflow.carry_forward_ownership(current, current_private),
            "unproven",
        )
        with self.assertRaises(contracts.ProjectInstructionsError) as caught:
            workflow.apply_decision(
                self.write_json_at(current_private / "manifest.json", current),
                self.write_json_at(
                    current_private / "decision.json",
                    self.decision(current, "needed"),
                ),
                current_private / "ownership.json",
                current_private / "state.json",
                current_private,
            )
        self.assertEqual(caught.exception.code, "ADOPTION_APPROVAL_REQUIRED")

    def test_tampered_sealed_state_digest_does_not_confer_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        lifecycle_path = prior_private.parent / "lifecycle.json"
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["project_instructions_state_sha256"] = "0" * 64
        self.write_json_at(lifecycle_path, lifecycle)
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_permission_unsafe_prior_evidence_does_not_confer_ownership(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        os.chmod(prior_private.parent / "lifecycle.json", 0o640)
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_linked_prior_ownership_does_not_confer_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        ownership_path = prior_private / "ownership.json"
        os.link(ownership_path, prior_private / "ownership-link.json")
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_mismatched_prior_evidence_does_not_confer_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(workspace, self.PRIOR_SESSION)
        ownership_path = prior_private / "ownership.json"
        state_path = prior_private / "state.json"
        lifecycle_path = prior_private.parent / "lifecycle.json"
        original_ownership = json.loads(ownership_path.read_text())
        original_state = json.loads(state_path.read_text())
        original_lifecycle = json.loads(lifecycle_path.read_text())
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        cases = {
            "retired": ("ownership", "status", "retired"),
            "subject": ("ownership", "project_scope", "other"),
            "body": ("ownership", "body_sha256", "0" * 64),
            "state-target": ("state", "target_sha256", "0" * 64),
            "lifecycle-scope": ("lifecycle", "project_scope", "other"),
        }
        for name, (document, field, value) in cases.items():
            with self.subTest(name=name):
                (current_private / "ownership.json").unlink(missing_ok=True)
                self.write_json_at(ownership_path, original_ownership)
                self.write_json_at(state_path, original_state)
                self.write_json_at(lifecycle_path, original_lifecycle)
                paths = {
                    "ownership": ownership_path,
                    "state": state_path,
                    "lifecycle": lifecycle_path,
                }
                changed = json.loads(paths[document].read_text())
                changed[field] = value
                self.write_json_at(paths[document], changed)
                self.reseal_session(prior_private)
                self.drop_workspace_registry(current_private)

                self.assertEqual(
                    workflow.carry_forward_ownership(
                        self.inspect(), current_private
                    ),
                    "unproven",
                )
                self.assertFalse((current_private / "ownership.json").exists())

    def test_malformed_prior_state_is_ignored_as_unproven(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        prior_private, _state = self.sealed_session(
            workspace, self.PRIOR_SESSION
        )
        state_path = prior_private / "state.json"
        state = json.loads(state_path.read_text())
        state["requirements"] = 1
        self.write_json_at(state_path, state)
        lifecycle_path = prior_private.parent / "lifecycle.json"
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["project_instructions_state_sha256"] = contracts._sha256_bytes(
            state_path.read_bytes()
        )
        self.write_json_at(lifecycle_path, lifecycle)
        self.drop_workspace_registry(prior_private)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_later_sealed_retirement_supersedes_older_active_receipt(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)
        target = self.repo / "AGENTS.md"
        retired_bytes = target.read_bytes()
        self.retired_session(workspace, self.RETIRED_SESSION)
        self.assertFalse(target.exists())
        target.write_bytes(retired_bytes)

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

    def test_legacy_bootstrap_blocks_damaged_retirement_evidence(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)
        target = self.repo / "AGENTS.md"
        active_bytes = target.read_bytes()
        retired_private, _state = self.retired_session(
            workspace, self.RETIRED_SESSION
        )
        target.write_bytes(active_bytes)
        self.drop_workspace_registry(retired_private)
        state_path = retired_private / "state.json"
        lifecycle_path = retired_private.parent / "lifecycle.json"
        original_state = state_path.read_bytes()
        original_lifecycle = lifecycle_path.read_bytes()

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        for case in ("digest", "malformed", "mode", "phase"):
            with self.subTest(case=case):
                state_path.write_bytes(original_state)
                lifecycle_path.write_bytes(original_lifecycle)
                os.chmod(state_path, 0o600)
                os.chmod(lifecycle_path, 0o600)
                if case == "digest":
                    lifecycle = json.loads(original_lifecycle)
                    lifecycle["project_instructions_state_sha256"] = "0" * 64
                    self.write_json_at(lifecycle_path, lifecycle)
                elif case == "malformed":
                    state = json.loads(original_state)
                    state["requirements"] = 1
                    self.write_json_at(state_path, state)
                    lifecycle = json.loads(original_lifecycle)
                    lifecycle["project_instructions_state_sha256"] = (
                        contracts._sha256_bytes(state_path.read_bytes())
                    )
                    self.write_json_at(lifecycle_path, lifecycle)
                else:
                    if case == "mode":
                        os.chmod(state_path, 0o640)
                    else:
                        lifecycle = json.loads(original_lifecycle)
                        lifecycle["phase"] = "planned"
                        self.write_json_at(lifecycle_path, lifecycle)
                self.assertEqual(
                    workflow.carry_forward_ownership(
                        self.inspect(), current_private
                    ),
                    "unproven",
                )
                self.assertFalse((current_private / "ownership.json").exists())

    def test_marker_only_drift_does_not_import_a_sibling_receipt(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)
        target = self.repo / "AGENTS.md"
        content = target.read_bytes()
        marker = contracts.GENERATED_MARKER_RE.match(content)
        assert marker is not None
        target.write_bytes(
            target_io._generated_content(
                content[marker.end() :], "1" * 64, "2" * 64
            )
        )

        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), current_private),
            "unproven",
        )
        self.assertFalse((current_private / "ownership.json").exists())

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

    def test_same_session_inspect_after_retirement_is_not_applicable(self) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        current = self.inspect()
        decision = self.decision(
            current,
            "not-needed",
            approval={
                "action": "retire",
                "target_sha256": str(dict(current["target"])["sha256"]),
            },
        )
        self.apply(current, decision)

        self.assertEqual(
            workflow.carry_forward_ownership(self.inspect(), self.private),
            "not-applicable",
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

    def test_registry_publication_failure_keeps_refresh_recovery_backup(
        self,
    ) -> None:
        initial = self.inspect()
        self.apply(initial, self.decision(initial, "needed"))
        target = self.repo / "AGENTS.md"
        before = target.read_bytes()
        current = self.inspect()
        decision = self.decision(current, "needed")
        decision["rules"][0]["instruction"] = (
            "Run focused contract tests and inspect exact failures."
        )
        with (
            mock.patch.object(
                workflow,
                "_publish_workspace_registry_locked",
                side_effect=contracts.ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "simulated registry publication failure",
                ),
            ),
            self.assertRaises(contracts.ProjectInstructionsError) as caught,
        ):
            self.apply(current, decision)
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")
        backup = self.repo / ".AGENTS.md.project-agent-instructions.backup"
        self.assertEqual(backup.read_bytes(), before)
        self.assertNotEqual(target.read_bytes(), before)
        with self.assertRaises(contracts.ProjectInstructionsError) as recovery:
            self.inspect()
        self.assertEqual(recovery.exception.code, "RECOVERY_REQUIRED")

    def test_attached_publication_failure_keeps_original_human_backup(self) -> None:
        target = self.repo / "AGENTS.md"
        original = b"# Human instructions\n\nKeep this prefix.\n"
        target.write_bytes(original)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.repo, check=True)
        manifest = self.inspect()
        with (
            mock.patch.object(
                workflow,
                "_publish_workspace_registry_locked",
                side_effect=contracts.ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "simulated registry publication failure",
                ),
            ),
            self.assertRaises(contracts.ProjectInstructionsError),
        ):
            self.apply(manifest, self.decision(manifest, "needed"))
        backup = self.repo / ".AGENTS.md.project-agent-instructions.backup"
        self.assertEqual(backup.read_bytes(), original)
        self.assertNotEqual(target.read_bytes(), original)
        with self.assertRaises(contracts.ProjectInstructionsError) as recovery:
            self.inspect()
        self.assertEqual(recovery.exception.code, "RECOVERY_REQUIRED")

    def test_created_publication_failure_is_retryable_from_durable_state(
        self,
    ) -> None:
        initial = self.inspect()
        with (
            mock.patch.object(
                workflow,
                "_publish_workspace_registry_locked",
                side_effect=contracts.ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "simulated registry publication failure",
                ),
            ),
            self.assertRaises(contracts.ProjectInstructionsError) as caught,
        ):
            self.apply(initial, self.decision(initial, "needed"))
        self.assertEqual(caught.exception.code, "OWNERSHIP_CONFLICT")
        self.assertTrue((self.repo / "AGENTS.md").exists())
        self.assertFalse(
            (self.repo / ".AGENTS.md.project-agent-instructions.backup").exists()
        )
        registry = json.loads(
            self.workspace_registry_path(self.private).read_text()
        )
        self.assertEqual(registry["generation"], 0)

        current = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(current, self.private),
            "current",
        )
        state = self.apply(current, self.decision(current, "needed"))
        self.assertEqual(state["outcome"], "existing-sufficient")
        self.assertEqual(
            workflow.verify_state(self.private / "state.json", self.private)[
                "outcome"
            ],
            "existing-sufficient",
        )

    def test_competing_inspect_preserves_created_publication_recovery(
        self,
    ) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        writer_session = workspace / "writer"
        observer_session = workspace / "observer"
        writer_session.mkdir(mode=0o700)
        observer_session.mkdir(mode=0o700)
        writer_private = writer_session / "project-instructions"
        observer_private = observer_session / "project-instructions"
        private_state._ensure_private_root(writer_private, self.repo)
        private_state._ensure_private_root(observer_private, self.repo)
        initial = self.inspect()

        with (
            mock.patch.object(
                workflow,
                "_publish_workspace_registry_locked",
                side_effect=contracts.ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "simulated registry publication failure",
                ),
            ),
            self.assertRaises(contracts.ProjectInstructionsError),
        ):
            workflow.apply_decision(
                self.write_json_at(writer_private / "manifest.json", initial),
                self.write_json_at(
                    writer_private / "decision.json",
                    self.decision(initial, "needed"),
                ),
                writer_private / "ownership.json",
                writer_private / "state.json",
                writer_private,
            )

        observed = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(observed, observer_private),
            "unproven",
        )
        registry_path = self.workspace_registry_path(observer_private)
        pending = json.loads(registry_path.read_text())
        self.assertEqual(pending["generation"], 1)
        self.assertEqual(
            next(iter(pending["entries"].values()))["status"], "pending"
        )

        current = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(current, writer_private),
            "current",
        )
        active = json.loads(registry_path.read_text())
        self.assertEqual(active["generation"], 2)
        self.assertEqual(
            next(iter(active["entries"].values()))["status"], "active"
        )
        state = workflow.apply_decision(
            self.write_json_at(writer_private / "manifest.json", current),
            self.write_json_at(
                writer_private / "decision.json",
                self.decision(current, "needed"),
            ),
            writer_private / "ownership.json",
            writer_private / "state.json",
            writer_private,
        )
        self.assertEqual(state["outcome"], "existing-sufficient")

    def test_adoption_publication_failure_is_retryable_without_readoption(
        self,
    ) -> None:
        initial = self.inspect()
        decision = self.decision(initial, "needed")
        _, body, _, _ = workflow._validate_decision(decision, initial)
        assert body is not None
        target = self.repo / "AGENTS.md"
        target.write_bytes(
            target_io._generated_content(
                body,
                contracts._generation_manifest_sha256(initial),
                contracts._generation_decision_sha256(
                    "needed", body, self.evidence()
                ),
            )
        )
        current = self.inspect()
        approved = self.decision(
            current,
            "needed",
            approval={
                "action": "adopt",
                "target_sha256": str(dict(current["target"])["sha256"]),
            },
        )
        before = target.read_bytes()
        with (
            mock.patch.object(
                workflow,
                "_publish_workspace_registry_locked",
                side_effect=contracts.ProjectInstructionsError(
                    "OWNERSHIP_CONFLICT",
                    "simulated registry publication failure",
                ),
            ),
            self.assertRaises(contracts.ProjectInstructionsError),
        ):
            self.apply(current, approved)
        self.assertEqual(target.read_bytes(), before)

        retry_manifest = self.inspect()
        self.assertEqual(
            workflow.carry_forward_ownership(retry_manifest, self.private),
            "current",
        )
        state = self.apply(
            retry_manifest,
            self.decision(retry_manifest, "needed"),
        )
        self.assertEqual(state["outcome"], "existing-sufficient")

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

    def test_marker_only_receipt_drift_uses_existing_ownership(self) -> None:
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
        state = self.apply(current, self.decision(current, "needed"))
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
        output = json.loads(result.stdout)
        self.assertEqual(output["ownership_continuity"], "not-applicable")
        manifest = json.loads((self.private / "cli-manifest.json").read_text())
        self.assertEqual(manifest["schema"], contracts.MANIFEST_SCHEMA)

    def test_cli_inspect_carries_sealed_sibling_ownership(self) -> None:
        workspace = self.root / "project-specs" / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        os.chmod(workspace.parent, 0o700)
        self.sealed_session(workspace, self.PRIOR_SESSION)
        current_session = workspace / self.CURRENT_SESSION
        current_session.mkdir(mode=0o700)
        current_private = current_session / "project-instructions"
        private_state._ensure_private_root(current_private, self.repo)
        manifest_path = current_private / "manifest.json"

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
                str(current_private),
                "--spec-receipt",
                str(self.receipt_path),
                "--runtime-config",
                str(self.runtime_path),
                "--output",
                str(manifest_path),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["ownership_continuity"],
            "carried-forward",
        )
        self.assertTrue((current_private / "ownership.json").exists())

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
