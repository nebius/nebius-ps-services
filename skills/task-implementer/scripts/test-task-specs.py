#!/usr/bin/env python3
"""Focused tests for steering ledgers and managed specification documents."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import prompt_workspace_lanes as lanes


SCRIPT = Path(__file__).resolve().with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("prompt_workspace_specs_test", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant.
    raise RuntimeError("could not load prompt_workspace.py")
pw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pw
SPEC.loader.exec_module(pw)
specs = sys.modules[pw.inspect_spec_documents.__module__]


FIXED = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def requirement_body(identifier: str = "TI-REQ-001") -> str:
    return f"""## Task Implementer Requirements

<!-- REQUIREMENT: {identifier} status=active priority=P1 type=feature -->
### {identifier}: Safe steering

#### User Story

Apply steering at a safe boundary while preserving completed work and without
live interruption.

#### Acceptance Criteria

- AC-001: Queued steering is applied once.

#### Negative Criteria

- NC-001: Completed work is not discarded.

#### Validation Method

Run focused validation.

#### Test Method

Run focused tests.

#### Evaluation Method

Inspect the steering outcome.

<!-- /REQUIREMENT: {identifier} -->

## Open Questions

- None.

## Change Log

- 2026-07-13: Added {identifier}.
"""


def design_body(
    identifier: str = "TI-DES-001",
    requirement: str = "TI-REQ-001",
) -> str:
    return f"""## Task Implementer Designs

<!-- FEATURE: {identifier} reqs={requirement} status=ready delivery=not-started priority=P1 version=1 -->
### {identifier}: Boundary steering

#### Requirements Covered

- {requirement}: Apply steering at a safe boundary.

#### Context Evidence

Focused Task Implementer contract tests.

#### Design Details

Queue edits during implementation at the private intake boundary.

#### Selected Option

Queue edits during implementation.

#### Alternatives Considered

- Live interruption was rejected.

#### Implementation Boundaries

Private intake state only.

#### Test-First Success Criteria

- TDD-001: Queued steering is applied exactly once.

#### Validation Plan

Run focused tests.

#### Test Plan

Run focused Task Implementer tests.

#### Evaluation Plan

Inspect the steering outcome.

#### Rollout And Rollback

Revert the task commit.

#### Done Definition

The mapped requirement and focused checks pass.

#### Implementation Evidence

- Pending checkpoint.

#### Verification Evidence

- Independent verification is pending.

<!-- /FEATURE: {identifier} -->

## Change Log

- 2026-07-13: Added {identifier}.
"""


class TaskSpecificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        (self.scope / "scope.txt").write_text("scope\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git(
            "-c",
            "user.name=Spec Test",
            "-c",
            "user.email=spec@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=self.repo,
        )
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=self.repo,
        )
        git("switch", "-qc", "spec-feature", cwd=self.repo)
        self.codex_home = self.root / "codex"
        lane = lanes.ensure_project_lane(self.scope)
        lane_root = Path(str(lane["worktree"]))
        result = pw.init_workspace(
            lane_root,
            "services/example",
            self.codex_home,
            lane=lane,
            clock=lambda: FIXED,
        )
        self.scope = Path(str(lane["scope_cwd"]))
        self.workspace = Path(result["workspace"])
        self.workspace_value = pw.verify_workspace(self.workspace)
        self.docs = self.scope / "docs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_specs(self, *, tracked: bool = True) -> tuple[Path, Path]:
        self.docs.mkdir(exist_ok=True)
        requirements = self.docs / "requirements.md"
        design = self.docs / "design.md"
        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(specs.new_spec_document("design", design_body()))
        requirements.chmod(0o644)
        design.chmod(0o644)
        if tracked:
            git(
                "add",
                "services/example/docs",
                cwd=Path(str(self.workspace_value["repo_root"])),
            )
        return requirements, design

    def assert_error(
        self, code: str, function: object, *args: object, **kwargs: object
    ) -> None:
        with self.assertRaises(pw.PromptWorkspaceError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_managed_region_assets_follow_the_canonical_v2_record_shape(self) -> None:
        assets = Path(__file__).resolve().parents[1] / "assets"

        inspected = specs.owner_inspect_pair_bytes(
            (assets / "requirements-managed-region.md").read_bytes(),
            (assets / "design-managed-region.md").read_bytes(),
        )

        self.assertEqual(inspected["status"], "pending")
        self.assertEqual(inspected["requirements"]["ids"], ["TI-REQ-001"])
        self.assertEqual(inspected["design"]["ids"], ["TI-DES-001"])

    def test_missing_and_created_specs_have_stable_ids(self) -> None:
        missing = pw.inspect_spec_documents(self.workspace_value)
        self.assertEqual(missing["next_requirement_id"], "REQ-001")
        self.assertEqual(missing["next_design_id"], "FEAT-001")
        self.assertNotIn("project_agent_spec_receipt", missing)
        requirements, design = self.write_specs()

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertEqual(inspected["requirements"]["ids"], ["TI-REQ-001"])
        self.assertEqual(inspected["design"]["ids"], ["TI-DES-001"])
        self.assertEqual(
            inspected["design"]["requirements"]["TI-DES-001"],
            ["TI-REQ-001"],
        )
        self.assertEqual(inspected["next_requirement_id"], "REQ-002")
        self.assertEqual(inspected["next_design_id"], "FEAT-002")
        self.assertNotIn("project_agent_spec_receipt", inspected)
        before = (requirements.read_bytes(), design.read_bytes())
        repeated = pw.inspect_spec_documents(self.workspace_value)
        self.assertEqual(repeated, inspected)
        self.assertEqual(before, (requirements.read_bytes(), design.read_bytes()))

    def test_untracked_specs_do_not_emit_a_validation_receipt(self) -> None:
        self.write_specs(tracked=False)

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertNotIn("project_agent_spec_receipt", inspected)

    def test_commit_snapshot_does_not_fabricate_authoritative_receipt(self) -> None:
        self.write_specs()
        repo_root = Path(str(self.workspace_value["repo_root"]))
        git(
            "-c",
            "user.name=Spec Test",
            "-c",
            "user.email=spec@example.invalid",
            "commit",
            "-qm",
            "record specs",
            cwd=repo_root,
        )
        commit = git("rev-parse", "HEAD", cwd=repo_root)

        inspected = pw.inspect_spec_documents(self.workspace_value, commit=commit)

        self.assertNotIn("project_agent_spec_receipt", inspected)

    def test_every_applicable_requirement_must_be_mapped(self) -> None:
        requirements, design = self.write_specs()
        second_record = """<!-- REQUIREMENT: TI-REQ-002 status=active priority=P1 type=feature -->
### TI-REQ-002: Preserve traceability

#### User Story

Map every applicable requirement while ignoring superseded requirements and
without duplicate mappings.

#### Acceptance Criteria

- AC-001: Every applicable requirement is covered.

#### Negative Criteria

- NC-001: A mapping is not duplicated.

#### Validation Method

Run focused validation.

#### Test Method

Run focused tests.

#### Evaluation Method

Inspect total traceability.

<!-- /REQUIREMENT: TI-REQ-002 -->

"""
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace(
                    "## Open Questions",
                    second_record + "## Open Questions",
                ),
            )
        )

        self.assert_error(
            "SPEC_CONFLICT", pw.inspect_spec_documents, self.workspace_value
        )

    def test_superseded_requirement_and_design_are_not_current_coverage(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace("status=active", "status=superseded"),
            )
        )
        design.write_bytes(
            specs.new_spec_document(
                "design",
                design_body().replace(
                    "status=ready delivery=not-started",
                    "status=superseded delivery=unassessed",
                ),
            )
        )

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertNotIn("project_agent_spec_receipt", inspected)

    def test_project_agent_state_is_not_part_of_spec_inspection(self) -> None:
        self.write_specs()
        (self.scope / "AGENTS.md").write_text(
            "# Human project instructions\n\n- Preserve the validated contract.\n",
            encoding="utf-8",
        )

        inspected = pw.inspect_spec_documents(self.workspace_value)

        self.assertNotIn("project_agent_spec_receipt", inspected)
        self.assertEqual(inspected["requirements"]["ids"], ["TI-REQ-001"])
        self.assertEqual(inspected["design"]["ids"], ["TI-DES-001"])

    def test_generic_unicode_crlf_envelope_is_byte_preserved(self) -> None:
        self.docs.mkdir()
        requirements = self.docs / "requirements.md"
        original = "# Existing\r\n\r\nHuman-owned café.\r\n".encode("utf-8")
        requirements.write_bytes(original)
        requirements.chmod(0o644)
        generic = pw.inspect_spec_documents(self.workspace_value)["requirements"]

        merged = specs.append_managed_region(
            original, "requirements", requirement_body()
        )
        requirements.write_bytes(merged)
        requirements.chmod(0o644)
        inspected = pw.inspect_spec_documents(self.workspace_value)["requirements"]

        self.assertTrue(merged.startswith(original))
        self.assertEqual(
            inspected["surrounding_sha256"],
            generic["rendered_surrounding_sha256"],
        )
        replaced = specs.replace_managed_region(
            merged, "requirements", requirement_body()
        )
        self.assertEqual(replaced, merged)

    def test_owner_markers_and_path_conflicts_fail_closed(self) -> None:
        self.docs.mkdir()
        requirements = self.docs / "requirements.md"
        requirements.write_text(
            "---\nschema: agentic-sdlc.requirements.v1\n---\n# Requirements\n",
            encoding="utf-8",
        )
        requirements.chmod(0o644)
        self.assert_error(
            "SPEC_MIGRATION_REQUIRED",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        requirements.write_text(
            "---\nschema: 'agentic-sdlc.requirements.v1'\n---\n# Requirements\n",
            encoding="utf-8",
        )
        self.assert_error(
            "SPEC_MIGRATION_REQUIRED",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_text(
            "<!-- task-implementer:requirements:start "
            "schema=task-implementer/requirements-v1 -->\n",
            encoding="utf-8",
        )
        self.assert_error(
            "SPEC_MIGRATION_REQUIRED",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        if os.name == "posix":
            requirements.unlink()
            outside = self.root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            requirements.symlink_to(outside)
            self.assert_error(
                "SPEC_CONFLICT",
                pw.inspect_spec_documents,
                self.workspace_value,
            )

    def test_duplicate_ids_fail_closed(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body() + "\n" + requirement_body(),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements", requirement_body(identifier="TI-REQ-00")
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

    def test_required_structure_unknown_and_private_mappings_fail_closed(self) -> None:
        requirements, design = self.write_specs()
        requirements.write_bytes(
            specs.new_spec_document(
                "requirements",
                requirement_body().replace(
                    "#### Acceptance Criteria",
                    "#### Missing Acceptance Criteria",
                ),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(
            specs.new_spec_document(
                "design",
                design_body().replace(" reqs=TI-REQ-001 ", " reqs=TI-REQ-999 "),
            )
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        requirements.write_bytes(
            specs.new_spec_document("requirements", requirement_body())
        )
        design.write_bytes(
            specs.new_spec_document("design", design_body(requirement="TI-REQ-999"))
        )
        self.assert_error(
            "SPEC_CONFLICT",
            pw.inspect_spec_documents,
            self.workspace_value,
        )

        for leaked_state in (
            "run-20260713t150000z-deadbeef",
            "task-2",
            "a" * 64,
        ):
            with self.subTest(leaked_state=leaked_state):
                design.write_bytes(
                    specs.new_spec_document(
                        "design", design_body() + f"\n{leaked_state}\n"
                    )
                )
                self.assert_error(
                    "SPEC_CONFLICT",
                    pw.inspect_spec_documents,
                    self.workspace_value,
                )

    def test_requirements_refinement_tracks_material_questions_and_ready_digest(
        self,
    ) -> None:
        requirements, design = self.write_specs()
        created = pw.create_prompt(
            self.workspace,
            "Implement prompt refinement",
            clock=lambda: FIXED,
            id_factory=lambda: "a" * 32,
        )
        snapshot = pw.snapshot_prompt(
            self.workspace,
            Path(str(created["path"])),
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        run_dir = Path(str(snapshot["manifest"])).parent
        state = specs.load_requirements_refinement(run_dir, required=True)
        assert state is not None
        self.assertEqual(state["status"], "extracting")
        self.assertEqual(state["revision"], "r0001")
        state["questions"] = [
            {
                "id": "Q-001",
                "question": "Which external contract is authoritative?",
                "material": True,
                "status": "open",
                "answer": None,
                "source": None,
                "source_revision": None,
                "conflict": None,
            }
        ]
        requirements_digest = specs.inspect_spec_documents(self.workspace_value)[
            "requirements"
        ]["managed_sha256"]
        state["compiled_requirements_sha256"] = requirements_digest
        state["extracted"]["constraints"] = ["Use the documented canonical contract."]
        state["status"] = "ready"
        refinement_path = run_dir / "requirements-refinement.json"
        valid_bytes = refinement_path.read_bytes()
        self.assert_error(
            "RUN_STATE_INVALID",
            specs.save_requirements_refinement,
            run_dir,
            state,
        )
        self.assertEqual(refinement_path.read_bytes(), valid_bytes)
        state["questions"][0].update(
            {
                "status": "answered",
                "answer": "Use the documented v2 contract.",
                "source": "chat",
                "source_revision": "r0001",
            }
        )
        saved = specs.save_requirements_refinement(run_dir, state)
        self.assertEqual(saved["status"], "ready")
        impact_claim = {
            "schema": specs.IMPACT_CLAIM_SCHEMA,
            "prompt_id": state["prompt_id"],
            "revision": state["revision"],
            "intent_sha256": state["intent_sha256"],
            "dispositions": [
                {
                    "statement": "constraints:0001",
                    "disposition": "existing_contract",
                    "requirements": ["TI-REQ-001"],
                    "design": ["TI-DES-001"],
                    "effects": [],
                    "reason": None,
                }
            ],
            "declared_effects": [],
            "declared_plan_action": "retain_plan",
        }
        specs.write_atomic(
            specs.prompt_impact_claim_path(run_dir), specs.stable_json(impact_claim)
        )
        run_state = pw.verify_run(self.workspace_value, str(snapshot["run_id"]), None)
        verified = specs.verify_requirements_refinement_contract(
            self.workspace_value, run_dir, run_state
        )
        self.assertEqual(
            verified["refinement"]["compiled_requirements_sha256"],
            requirements_digest,
        )
        self.assertEqual(verified["impact"]["plan_action"], "retain_plan")
        self.assertEqual(verified["public_impact"]["requirements"], ["TI-REQ-001"])
        row = next(
            item
            for item in pw.prompt_rows(self.workspace, None, None)
            if item.get("prompt_ref") == created["prompt_ref"]
        )
        self.assertEqual(
            row["impact"],
            {
                "classification": "no_effect",
                "requirements": ["TI-REQ-001"],
                "design": ["TI-DES-001"],
                "reasons": [],
                "plan_action": "retain_plan",
            },
        )
        serialized_impact = json.dumps(row["impact"], sort_keys=True)
        for forbidden in ("sha256", "statement", "prompt-", str(run_dir)):
            self.assertNotIn(forbidden, serialized_impact)
        impact_sha256 = verified["impact_sha256"]
        (run_dir / "prompt-impact" / "ledger.json").unlink()
        recovered = specs.verify_requirements_refinement_contract(
            self.workspace_value, run_dir, run_state
        )
        self.assertEqual(recovered["impact_sha256"], impact_sha256)
        ledger = run_dir / "prompt-impact" / "ledger.json"
        ledger_backing = ledger.with_name("ledger.backing")
        ledger.rename(ledger_backing)
        os.link(ledger_backing, ledger)
        self.assert_error(
            "RUN_STATE_INVALID",
            specs.verify_requirements_refinement_contract,
            self.workspace_value,
            run_dir,
            run_state,
        )
        ledger.unlink()
        ledger_backing.rename(ledger)
        impact_claim["dispositions"][0].update(
            {
                "disposition": "non_contract",
                "requirements": [],
                "design": [],
                "reason": "clarification_context",
            }
        )
        specs.write_atomic(
            specs.prompt_impact_claim_path(run_dir), specs.stable_json(impact_claim)
        )
        specs.write_atomic(
            run_dir / "prompt-impact" / "attempt-0002.json",
            specs.stable_json({"orphan": True}),
        )
        verified = specs.verify_requirements_refinement_contract(
            self.workspace_value, run_dir, run_state
        )
        ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(ledger_value["current"]["generation"], 3)
        ledger_bytes = ledger.read_bytes()
        self.assert_error(
            "CONCURRENT_MODIFICATION",
            specs._write_impact_ledger_cas,
            run_dir,
            "0" * 64,
            ledger_value,
        )
        self.assertEqual(ledger.read_bytes(), ledger_bytes)
        coordinator = {
            "plan_sha256": "f" * 64,
            "prompt_revision": run_state["latest_revision"],
            "prompt_intent_sha256": run_state["latest_intent_sha256"],
        }
        later_no_effect = {
            **verified["impact"],
            "revision": "r0002",
            "intent_sha256": "e" * 64,
            "plan_action": "retain_plan",
            "effects": [],
        }
        retained = specs.settle_prompt_impact_plan(
            run_dir, coordinator, later_no_effect, "d" * 64
        )
        self.assertEqual(retained["plan_basis_revision"], "r0001")
        self.assertEqual(retained["latest_settled_revision"], "r0002")
        later_material = {
            **later_no_effect,
            "plan_action": "replan_required",
            "effects": ["execution"],
        }
        self.assert_error(
            "REPLAN_REQUIRED",
            specs.settle_prompt_impact_plan,
            run_dir,
            coordinator,
            later_material,
            "c" * 64,
        )
        specs.settle_prompt_impact_plan(
            run_dir,
            coordinator,
            verified["impact"],
            verified["impact_sha256"],
        )
        specs.verify_prompt_impact_plan(run_dir, coordinator, self.scope)
        design_bytes = design.read_bytes()
        design.write_bytes(design_bytes + b"\n")
        self.assert_error(
            "REPLAN_REQUIRED",
            specs.verify_prompt_impact_plan,
            run_dir,
            coordinator,
            self.scope,
        )
        design.write_bytes(design_bytes)
        requirements.write_text(
            requirements.read_text(encoding="utf-8").replace(
                "<!-- /REQUIREMENT: TI-REQ-001 -->",
                "Requirement semantics changed after refinement.\n\n"
                "<!-- /REQUIREMENT: TI-REQ-001 -->",
            ),
            encoding="utf-8",
        )
        self.assert_error(
            "REQUIREMENTS_REFINEMENT_REQUIRED",
            specs.verify_requirements_refinement_contract,
            self.workspace_value,
            run_dir,
            run_state,
        )

    def test_steering_ledger_orders_aba_and_resolves_idempotently(self) -> None:
        prompt = Path(
            pw.create_prompt(
                self.workspace,
                "Steer one prompt",
                clock=lambda: FIXED,
                id_factory=lambda: "b" * 32,
            )["path"]
        )
        text = prompt.read_text(encoding="utf-8")
        text = text.rstrip() + (
            "\n\n## Outcome\n\nOutcome A.\n"
            "\n## Acceptance criteria\n\n- [ ] Acceptance A.\n"
            "\n## Verification\n\nVerification A.\n"
        )
        prompt.write_text(text, encoding="utf-8")
        prompt.chmod(0o600)
        first = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED,
        )
        run_dir = Path(first["manifest"]).parent
        prompt.write_text(text.replace("Outcome A.", "Outcome B."), encoding="utf-8")
        prompt.chmod(0o600)
        second = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=str(first["run_id"]),
            force_new_run=False,
            clock=lambda: FIXED.replace(second=1),
        )
        prompt.write_text(text, encoding="utf-8")
        prompt.chmod(0o600)
        third = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=str(first["run_id"]),
            force_new_run=False,
            clock=lambda: FIXED.replace(second=2),
        )
        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(second["revision"]), FIXED
        )
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(third["revision"]), FIXED
        )
        specs.record_steering_revision(
            run_dir, manifest["revisions"], str(third["revision"]), FIXED
        )
        ledger = specs.load_steering_ledger(run_dir, manifest["revisions"])
        self.assertEqual(
            [event["revision"] for event in ledger["events"]],
            ["r0002", "r0003"],
        )
        self.assert_error(
            "RUN_STATE_INVALID",
            specs.resolve_steering_revision,
            run_dir,
            manifest["revisions"],
            "r0003",
            "applied",
            clock=lambda: FIXED.replace(second=30),
        )
        resolved = specs.resolve_steering_revision(
            run_dir,
            manifest["revisions"],
            "r0002",
            "no_effect",
            clock=lambda: FIXED.replace(minute=1),
        )
        repeated = specs.resolve_steering_revision(
            run_dir,
            manifest["revisions"],
            "r0002",
            "no_effect",
            clock=lambda: FIXED.replace(minute=2),
        )
        self.assertEqual(resolved, repeated)
        self.assertEqual(resolved["disposition"], "no_effect")


if __name__ == "__main__":
    unittest.main()
