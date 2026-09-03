#!/usr/bin/env python3
"""Focused tests for the canonical project-spec owner."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from project_specs_lib import lifecycle as lifecycle_module
from project_specs_lib import migration
from project_specs_lib.contracts import (
    ProjectSpecError,
    _managed_region,
    _parse_design,
    _parse_requirements,
    canonical_document,
    inspect_project,
    validate_project,
)
from project_specs_lib.impact import (
    CLAIM_SCHEMA,
    public_impact_status,
    validate_prompt_impact,
)
from project_specs_lib.impact import ProjectSpecError as ImpactError
from project_specs_lib.lifecycle import lifecycle_dir
from project_specs_lib.migration import migrate_project, recover_migration

SCRIPT = Path(__file__).resolve().with_name("project_specs.py")


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def requirement_body(identifier: str = "TI-REQ-001") -> str:
    return f"""# Task Implementer Requirements

## Requirement Records

### {identifier}: Keep behavior current

- Status: active
- Requirement: The selected project contract reflects accepted behavior.
- Constraints: Preserve unrelated documentation.
- Non-goals: Runtime hook installation.

#### Acceptance criteria

- Canonical validation succeeds.

#### Verification

- Run the focused owner tests.

## Task Implementer Open Questions

- None.

## Task Implementer Requirements Change Log

- 2026-08-08: Established the canonical contract.
"""


def design_body(identifier: str = "TI-DES-001", requirement: str = "TI-REQ-001") -> str:
    return f"""# Task Implementer Designs

## Design Records

### {identifier}: Maintain one owner

- Status: planned
- Requirements: {requirement}
- Selected approach: Validate both documents through one shared owner.
- Boundaries and interfaces: Canonical managed regions and receipts.
- Validation: Focused owner and adapter tests.
- Rollback: Recover the paired migration backup.

#### Alternatives considered

- Separate writers were rejected because ownership would be ambiguous.

#### Implementation evidence

- Shared validator and migration tests.

## Task Implementer Design Change Log

- 2026-08-08: Established the canonical design.
"""


def rich_requirement_body(status: str = "active", identifier: str = "REQ-001") -> str:
    return f"""# Requirements

<!-- REQUIREMENT: {identifier} status={status} priority=P0 type=feature -->
### {identifier}: Keep behavior current

#### User Story

As a maintainer, I need a current project contract.

#### Acceptance Criteria

- AC-001: The shared validator accepts only current records.

#### Negative Criteria

- NC-001: Draft work does not authorize implementation.

#### Validation Method

Run the canonical validator.

#### Test Method

Run the focused owner tests.

#### Evaluation Method

Review the receipt status.

<!-- /REQUIREMENT: {identifier} -->
"""


def rich_design_body(
    status: str = "ready",
    identifier: str = "FEAT-001",
    requirement: str = "REQ-001",
    delivery: str = "unassessed",
) -> str:
    return f"""# Design

<!-- FEATURE: {identifier} reqs={requirement} status={status} delivery={delivery} priority=P0 version=1 -->
### {identifier}: Maintain one owner

#### Requirements Covered

- {requirement}

#### Context Evidence

- Canonical owner tests.

#### Design Details

Validate both documents through one shared owner.

#### Selected Option

Use one receipt issuer.

#### Alternatives Considered

Separate validators would create ownership ambiguity.

#### Implementation Boundaries

The owner validates repository specifications only.

#### Test-First Success Criteria

- TDD-001: Incomplete records fail closed.

#### Validation Plan

Run contract validation.

#### Test Plan

Run focused tests.

#### Evaluation Plan

Inspect the emitted receipt.

#### Rollout And Rollback

Use the paired migration recovery path.

#### Done Definition

The current pair has total traceability.

#### Implementation Evidence

The canonical owner implementation is present.

#### Verification Evidence

The focused canonical owner tests passed.

<!-- /FEATURE: {identifier} -->
"""


class ProjectSpecsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "test@example.com")
        git(self.project, "config", "user.name", "Test User")
        (self.project / "README.md").write_text("# Example\n", encoding="utf-8")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "baseline")
        self.docs = self.project / "docs"
        self.docs.mkdir()
        self.previous_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.root / "codex")

    def tearDown(self) -> None:
        if self.previous_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_codex_home
        self.temp.cleanup()

    def write_canonical(self) -> None:
        (self.docs / "requirements.md").write_bytes(
            canonical_document(
                "requirements",
                rich_requirement_body(identifier="TI-REQ-001"),
            )
        )
        (self.docs / "design.md").write_bytes(
            canonical_document(
                "design",
                rich_design_body(identifier="TI-DES-001", requirement="TI-REQ-001"),
            )
        )
        git(self.project, "add", "docs")

    def prompt_impact_inputs(self) -> tuple[dict[str, object], dict[str, object]]:
        refinement: dict[str, object] = {
            "schema": "test/refinement-v1",
            "prompt_id": "prompt-" + "a" * 32,
            "revision": "r0002",
            "intent_sha256": "b" * 64,
            "status": "ready",
            "extracted": {
                "constraints": ["Preserve the canonical owner."],
                "outcomes": ["Report the validated impact."],
            },
        }
        claim: dict[str, object] = {
            "schema": CLAIM_SCHEMA,
            "prompt_id": refinement["prompt_id"],
            "revision": refinement["revision"],
            "intent_sha256": refinement["intent_sha256"],
            "dispositions": [
                {
                    "statement": "constraints:0001",
                    "disposition": "existing_contract",
                    "requirements": ["TI-REQ-001"],
                    "design": ["TI-DES-001"],
                    "effects": [],
                    "reason": None,
                },
                {
                    "statement": "outcomes:0001",
                    "disposition": "non_contract",
                    "requirements": [],
                    "design": [],
                    "effects": [],
                    "reason": "workflow_directive",
                },
            ],
            "declared_effects": [],
            "declared_plan_action": "retain_plan",
        }
        return refinement, claim

    def validate_impact(
        self,
        refinement: dict[str, object],
        claim: dict[str, object],
        *,
        prior_impact_sha256: str | None = None,
        prior_spec_receipt_sha256: str | None = None,
        generation: int = 1,
    ) -> dict[str, object]:
        return validate_prompt_impact(
            self.project,
            workflow="task-implementer",
            prompt_id=str(refinement["prompt_id"]),
            revision=str(refinement["revision"]),
            prompt_sha256="c" * 64,
            intent_sha256=str(refinement["intent_sha256"]),
            refinement=refinement,
            claim=claim,
            prior_impact_sha256=prior_impact_sha256,
            prior_spec_receipt_sha256=prior_spec_receipt_sha256,
            generation=generation,
        )

    def test_prompt_impact_requires_complete_current_statement_coverage(self) -> None:
        self.write_canonical()
        refinement, claim = self.prompt_impact_inputs()
        receipt = self.validate_impact(refinement, claim)
        self.assertEqual(receipt["effects"], [])
        self.assertEqual(receipt["plan_action"], "retain_plan")
        self.assertEqual(len(receipt["coverage"]), 2)

        missing = dict(claim)
        missing["dispositions"] = list(claim["dispositions"])[1:]
        with self.assertRaisesRegex(ImpactError, "every extracted statement"):
            self.validate_impact(refinement, missing)

        duplicate = dict(claim)
        duplicate["dispositions"] = [
            *list(claim["dispositions"]),
            dict(list(claim["dispositions"])[0]),
        ]
        with self.assertRaisesRegex(ImpactError, "missing, duplicate, or unknown"):
            self.validate_impact(refinement, duplicate)

    def test_prompt_impact_derives_effect_and_rejects_aggregate_or_record_drift(
        self,
    ) -> None:
        self.write_canonical()
        refinement, claim = self.prompt_impact_inputs()
        changed = dict(list(claim["dispositions"])[0])
        changed["disposition"] = "changed_contract"
        changed["effects"] = ["design", "requirements"]
        claim["dispositions"] = [changed, list(claim["dispositions"])[1]]
        claim["declared_effects"] = ["design", "requirements"]
        claim["declared_plan_action"] = "replan_required"
        receipt = self.validate_impact(refinement, claim)
        self.assertEqual(receipt["effects"], ["design", "requirements"])

        claim["declared_effects"] = []
        with self.assertRaisesRegex(ImpactError, "declared impact effects"):
            self.validate_impact(refinement, claim)

        claim["declared_effects"] = ["design", "requirements"]
        changed["requirements"] = ["TI-REQ-999"]
        with self.assertRaisesRegex(ImpactError, "current requirement and design"):
            self.validate_impact(refinement, claim)

    def test_prompt_impact_public_status_exposes_only_bounded_records_and_action(
        self,
    ) -> None:
        self.write_canonical()
        refinement, claim = self.prompt_impact_inputs()
        receipt = self.validate_impact(refinement, claim)
        status = public_impact_status(receipt)
        self.assertEqual(
            status,
            {
                "classification": "no_effect",
                "requirements": ["TI-REQ-001"],
                "design": ["TI-DES-001"],
                "reasons": ["workflow_directive"],
                "plan_action": "retain_plan",
            },
        )
        serialized = json.dumps(status, sort_keys=True)
        self.assertNotIn("sha256", serialized)
        self.assertNotIn("Preserve the canonical owner", serialized)

    def test_prompt_impact_records_append_only_owner_spec_transition(self) -> None:
        self.write_canonical()
        refinement, claim = self.prompt_impact_inputs()
        first = self.validate_impact(refinement, claim)
        requirements = self.docs / "requirements.md"
        requirements.write_bytes(b"Human context.\n\n" + requirements.read_bytes())
        second = self.validate_impact(
            refinement,
            claim,
            prior_impact_sha256="e" * 64,
            prior_spec_receipt_sha256=str(first["spec_receipt_sha256"]),
            generation=2,
        )
        transition = second["spec_transition"]
        self.assertEqual(transition["prior_spec_receipt_sha256"], first["spec_receipt_sha256"])
        self.assertEqual(transition["next_spec_receipt_sha256"], second["spec_receipt_sha256"])
        self.assertEqual(transition["reason"], "owner_reconciliation")
        self.assertRegex(str(second["spec_transition_sha256"]), r"^[0-9a-f]{64}$")

    def test_missing_project_templates_have_parseable_draft_records(self) -> None:
        templates = Path(__file__).resolve().parents[1] / "assets/templates"
        requirements = (templates / "requirements.md.template").read_text(encoding="utf-8")
        design = (templates / "design.md.template").read_text(encoding="utf-8")
        _prefix, requirements_body, _suffix = _managed_region(requirements, "requirements")
        _prefix, design_body, _suffix = _managed_region(design, "design")
        self.assertEqual(_parse_requirements(requirements_body)[0]["status"], "draft")
        self.assertEqual(_parse_design(design_body)[0]["status"], "draft")

    def test_compatibility_intent_contract_is_semantic_and_owner_controlled(
        self,
    ) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        cases = (skill_root / "evals/reconciliation-cases.md").read_text(encoding="utf-8")

        self.assertIn("even without `GA`, `backward", skill)
        self.assertIn("compatibility`, or another prescribed phrase", skill)
        self.assertIn("Hooks are intake and observation guardrails", skill)
        self.assertIn("Personal global instructions are conflict context only", skill)
        self.assertIn("## Existing users require compatibility", cases)
        self.assertIn("without using `GA` or `backward compatibility`", cases)
        self.assertIn("private internals on one canonical", cases)

    def test_lifecycle_transition_commands_are_retired_from_docs(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        lifecycle_reference = (skill_root / "references/lifecycle.md").read_text(encoding="utf-8")

        for content in (skill, lifecycle_reference):
            self.assertNotIn("<hook-python>", content)
            self.assertNotIn("<canonical-maintain-project-specs-helper>", content)
            self.assertNotIn("literal absolute trusted Python", content)
        self.assertIn("`start-prompt`, `plan`, `open`, `seal`, and `waive` are retired", skill)
        self.assertIn("not an authorization state machine", lifecycle_reference)

    def test_transition_api_is_absent(self) -> None:
        for name in (
            "start_prompt",
            "plan",
            "open_implementation",
            "mark_material_write",
            "waive",
            "seal",
            "rules_context",
            "load_for_project",
        ):
            self.assertFalse(hasattr(lifecycle_module, name), name)

    def test_active_specs_do_not_restore_retired_lifecycle_authority(self) -> None:
        retired_phrases = (
            "selected-project phase gates",
            "current selected-project lifecycle remains",
            "project-contract owner's explicit state transition",
            "sealed selected-lane lifecycle",
            "contract-delta adoption",
            "denied by the project lifecycle",
            "at the lifecycle boundary",
            "lifecycle git parsing",
            "raw staging and commit remain lifecycle-denied",
            "requires the current selected-project lifecycle",
            "outer lifecycle cwd",
            "selected lifecycle,",
            "unchanged lifecycle write epochs",
        )

        def findings(content: str, marker: str, end_marker: str, live_status: str) -> list[str]:
            found: list[str] = []
            record: list[str] | None = None
            identifier = ""
            status = ""
            for line in content.splitlines():
                match = re.fullmatch(marker, line)
                if match:
                    identifier, status = match.groups()
                    record = [line]
                    continue
                if record is None:
                    continue
                record.append(line)
                if line == end_marker.format(identifier=identifier):
                    body = "\n".join(record).lower()
                    if status == live_status:
                        found.extend(
                            f"{identifier}: {phrase}"
                            for phrase in retired_phrases
                            if phrase in body
                        )
                    record = None
            return found

        docs_root = Path(__file__).resolve().parents[2] / "docs"
        if not docs_root.is_dir():
            self.skipTest("repository canonical docs are not installed skill assets")
        requirements = (docs_root / "requirements.md").read_text(encoding="utf-8")
        design = (docs_root / "design.md").read_text(encoding="utf-8")
        requirement_marker = (
            r"<!-- REQUIREMENT: (REQ-\d+) status=([a-z-]+) "
            r"priority=P[0-3] type=[a-z-]+ -->"
        )
        feature_marker = (
            r"<!-- FEATURE: (FEAT-\d+) reqs=[A-Z0-9,-]+ status=([a-z-]+) "
            r"delivery=[a-z-]+ priority=P[0-3] version=\d+ -->"
        )

        self.assertEqual(
            findings(
                requirements,
                requirement_marker,
                "<!-- /REQUIREMENT: {identifier} -->",
                "active",
            ),
            [],
        )
        self.assertEqual(
            findings(
                design,
                feature_marker,
                "<!-- /FEATURE: {identifier} -->",
                "ready",
            ),
            [],
        )
        historical = """<!-- REQUIREMENT: REQ-999 status=superseded priority=P0 type=constraint -->
selected-project phase gates
<!-- /REQUIREMENT: REQ-999 -->
"""
        live = historical.replace("status=superseded", "status=active")
        self.assertEqual(
            findings(
                historical,
                requirement_marker,
                "<!-- /REQUIREMENT: {identifier} -->",
                "active",
            ),
            [],
        )
        self.assertEqual(
            findings(
                live,
                requirement_marker,
                "<!-- /REQUIREMENT: {identifier} -->",
                "active",
            ),
            ["REQ-999: selected-project phase gates"],
        )

    def test_validate_emits_one_shared_owner_receipt(self) -> None:
        self.write_canonical()
        receipt = validate_project(self.project)
        self.assertEqual(receipt["schema"], "maintain-project-specs.spec-validation.v4")
        self.assertEqual(receipt["owner"], "maintain-project-specs")
        self.assertEqual(receipt["status"], "current")
        self.assertEqual(receipt["project_scope"], ".")
        self.assertEqual(receipt["git_head"], git(self.project, "rev-parse", "HEAD"))

    def test_validate_can_store_owner_receipt_only_under_private_root(self) -> None:
        self.write_canonical()
        session = "receipt-session"
        git_root = Path(git(self.project, "rev-parse", "--show-toplevel"))
        output = lifecycle_dir(git_root, session) / "spec-receipt.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                "--project-root",
                str(self.project),
                "--output",
                str(output),
                "--session-id",
                session,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(json.loads(output.read_text())["status"], "current")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

        rejected = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                "--project-root",
                str(self.project),
                "--output",
                str(self.project / "receipt.json"),
                "--session-id",
                session,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stdout)["status"], "advisory")
        self.assertFalse((self.project / "receipt.json").exists())

        wrong_session_output = lifecycle_dir(git_root, "other-session") / "spec-receipt.json"
        rejected = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                "--project-root",
                str(self.project),
                "--output",
                str(wrong_session_output),
                "--session-id",
                session,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stdout)["status"], "advisory")
        self.assertFalse(wrong_session_output.exists())

    def test_retired_lifecycle_command_is_not_accepted(self) -> None:
        self.write_canonical()
        session = "session-unsafe-lock"
        git_root = Path(git(self.project, "rev-parse", "--show-toplevel"))
        session_root = lifecycle_dir(git_root, session)
        maintenance_root = session_root.parent / ".maintenance"
        (maintenance_root / "sessions").mkdir(parents=True, mode=0o700)
        (maintenance_root / "staging").mkdir(mode=0o700)
        (maintenance_root / "journals").mkdir(mode=0o700)
        workspace_lock = maintenance_root / "workspace.lock"
        workspace_lock.write_bytes(b"")
        workspace_lock.chmod(0o644)

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "start-prompt",
                "--project-root",
                str(self.project),
                "--session-id",
                session,
                "--turn-id",
                "turn-unsafe-lock",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid choice: 'start-prompt'", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_retired_task_implementer_receipt_flags_are_not_accepted(self) -> None:
        self.write_canonical()

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate",
                "--project-root",
                str(self.project),
                "--task-implementer-workspace",
                str(self.project / ".tasks"),
                "--task-implementer-run-id",
                "run-retired-bridge",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("unrecognized arguments", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_task_migration_preserves_ids_and_surrounding_bytes(self) -> None:
        prefix = b"# Human requirements\n\nKeep this paragraph.\n\n"
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_bytes(
            prefix + f"{req_start}\n{requirement_body()}\n{req_end}\n".encode()
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )
        result = migrate_project(self.project)
        self.assertEqual(result["source"], "task-implementer")
        self.assertTrue((self.docs / "requirements.md").read_bytes().startswith(prefix))
        self.assertIn("TI-REQ-001", (self.docs / "requirements.md").read_text())
        self.assertIn("TI-DES-001", (self.docs / "design.md").read_text())
        git(self.project, "add", "docs")
        self.assertEqual(validate_project(self.project)["owner"], "maintain-project-specs")
        self.assertEqual(migrate_project(self.project)["status"], "unchanged")

    def test_canonical_v1_requires_explicit_migration_to_v2(self) -> None:
        requirements = canonical_document(
            "requirements", rich_requirement_body(identifier="REQ-001")
        ).replace(b"requirements-v2", b"requirements-v1")
        requirements = requirements.replace(b" status=active ", b" status=accepted ")
        design = canonical_document(
            "design", rich_design_body(identifier="FEAT-001", requirement="REQ-001")
        ).replace(b"design-v2", b"design-v1")
        design = design.replace(b" delivery=unassessed", b"")
        design = re.sub(
            rb"\n#### Implementation Evidence\n\n.*?\n\n"
            rb"#### Verification Evidence\n\n.*?\n(?=\n<!-- /FEATURE:)",
            b"",
            design,
            flags=re.DOTALL,
        )
        (self.docs / "requirements.md").write_bytes(requirements)
        (self.docs / "design.md").write_bytes(design)
        git(self.project, "add", "docs")
        with self.assertRaisesRegex(ProjectSpecError, "legacy ownership"):
            inspect_project(self.project)

        result = migrate_project(self.project)

        self.assertEqual(result["source"], "canonical-v1")
        self.assertIn(
            "<!-- REQUIREMENT: REQ-001 status=active",
            (self.docs / "requirements.md").read_text(),
        )
        self.assertEqual(
            (self.docs / "design.md").read_text().count("#### Implementation Evidence"),
            1,
        )
        self.assertEqual(
            (self.docs / "design.md").read_text().count("#### Verification Evidence"),
            1,
        )
        git(self.project, "add", "docs")
        self.assertEqual(validate_project(self.project)["validator_version"], 2)

    def test_canonical_v1_preserves_existing_implementation_evidence(self) -> None:
        requirements = canonical_document(
            "requirements", rich_requirement_body(identifier="REQ-001")
        ).replace(b"requirements-v2", b"requirements-v1")
        design = canonical_document(
            "design", rich_design_body(identifier="FEAT-001", requirement="REQ-001")
        ).replace(b"design-v2", b"design-v1")
        design = design.replace(b" delivery=unassessed", b"")
        design = re.sub(
            rb"\n#### Verification Evidence\n\n.*?\n(?=\n<!-- /FEATURE:)",
            b"",
            design,
            flags=re.DOTALL,
        )
        (self.docs / "requirements.md").write_bytes(requirements)
        (self.docs / "design.md").write_bytes(design)
        git(self.project, "add", "docs")

        migrate_project(self.project)

        migrated = (self.docs / "design.md").read_text()
        self.assertEqual(migrated.count("#### Implementation Evidence"), 1)
        self.assertIn("The canonical owner implementation is present.", migrated)
        self.assertEqual(migrated.count("#### Verification Evidence"), 1)
        git(self.project, "add", "docs")
        self.assertEqual(validate_project(self.project)["validator_version"], 2)

    def test_compact_canonical_v1_migrates_core_and_task_records(self) -> None:
        req_start = (
            "<!-- maintain-project-specs:requirements:start "
            "schema=maintain-project-specs/requirements-v1 -->"
        )
        req_end = "<!-- maintain-project-specs:requirements:end -->"
        des_start = (
            "<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v1 -->"
        )
        des_end = "<!-- maintain-project-specs:design:end -->"
        requirements = "\n\n".join(
            (
                requirement_body(identifier="REQ-001").replace(
                    "- Non-goals: Runtime hook installation.\n", ""
                ),
                requirement_body(identifier="TI-REQ-001").replace(
                    "- Requirement: The selected project contract reflects accepted behavior.",
                    "- Requirement: The selected project contract reflects accepted\n"
                    "  behavior across wrapped lines.",
                ),
            )
        )
        designs = "\n\n".join(
            (
                design_body(identifier="FEAT-001", requirement="REQ-001")
                .replace("- Status: planned", "- Status: active")
                .replace(
                    "- Boundaries and interfaces: Canonical managed regions and receipts.",
                    "- Authority and scope: Canonical managed regions and receipts.",
                )
                .replace(
                    "\n#### Alternatives considered\n\n"
                    "- Separate writers were rejected because ownership would be ambiguous.\n",
                    "",
                )
                .replace(
                    "\n#### Implementation evidence\n\n- Shared validator and migration tests.\n",
                    "",
                ),
                design_body(identifier="TI-DES-001", requirement="TI-REQ-001"),
            )
        )
        (self.docs / "requirements.md").write_text(
            f"# Human prefix\n\n{req_start}\n{requirements}\n{req_end}\n",
            encoding="utf-8",
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{designs}\n{des_end}\n\nHuman suffix.\n",
            encoding="utf-8",
        )
        git(self.project, "add", "docs")

        result = migrate_project(self.project)

        self.assertEqual(result["source"], "canonical-v1")
        migrated_requirements = (self.docs / "requirements.md").read_text()
        migrated_design = (self.docs / "design.md").read_text()
        self.assertIn("REQ-001", migrated_requirements)
        self.assertIn("TI-REQ-001", migrated_requirements)
        self.assertIn("behavior across wrapped lines", migrated_requirements)
        self.assertIn("No additional non-goal was recorded", migrated_requirements)
        self.assertIn("FEAT-001", migrated_design)
        self.assertIn("TI-DES-001", migrated_design)
        self.assertIn("delivery=unassessed", migrated_design)
        self.assertIn(
            "**Authority and scope:** Canonical managed regions and receipts.",
            migrated_design,
        )
        self.assertIn("No alternative was recorded", migrated_design)
        self.assertTrue(migrated_requirements.startswith("# Human prefix\n\n"))
        self.assertTrue(migrated_design.endswith("\n\nHuman suffix.\n"))
        self.assertEqual(validate_project(self.project)["validator_version"], 2)

    def test_current_records_ignore_placeholder_syntax_inside_code(self) -> None:
        coded = rich_requirement_body().replace(
            "current project contract.",
            "current `<gateway>` project contract and `TODO` literal.",
        )
        self.assertEqual(_parse_requirements(coded)[0]["id"], "REQ-001")

        unresolved = coded.replace("`<gateway>`", "<Design slice>")
        with self.assertRaisesRegex(ProjectSpecError, "unresolved User Story"):
            _parse_requirements(unresolved)

    def test_mixed_legacy_owners_fail_without_rewrite(self) -> None:
        task_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        task_end = "<!-- task-implementer:requirements:end -->"
        requirements = f"{task_start}\n{requirement_body()}\n{task_end}\n"
        design = "---\nschema: agentic-sdlc.design.v1\n---\n# Design\n"
        (self.docs / "requirements.md").write_text(requirements, encoding="utf-8")
        (self.docs / "design.md").write_text(design, encoding="utf-8")
        before = {path.name: path.read_bytes() for path in self.docs.iterdir()}
        with self.assertRaisesRegex(ProjectSpecError, "mixed or missing owner"):
            migrate_project(self.project)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.docs.iterdir()})

    def test_injected_pair_failure_restores_both_files(self) -> None:
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_text(
            f"{req_start}\n{requirement_body()}\n{req_end}\n", encoding="utf-8"
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )
        before = {path.name: path.read_bytes() for path in self.docs.iterdir()}

        def fail(point: str) -> None:
            if point == "after-requirements":
                raise RuntimeError("injected")

        with self.assertRaisesRegex(RuntimeError, "injected"):
            migrate_project(self.project, failure_injector=fail)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.docs.iterdir()})

    def test_mixed_canonical_and_legacy_markers_fail_closed(self) -> None:
        self.write_canonical()
        requirements = self.docs / "requirements.md"
        requirements.write_bytes(
            b"<!-- task-implementer:requirements:end -->\n" + requirements.read_bytes()
        )
        before = requirements.read_bytes()
        with self.assertRaisesRegex(ProjectSpecError, "mixed or missing owner"):
            migrate_project(self.project)
        self.assertEqual(requirements.read_bytes(), before)

    def test_rollback_failure_retains_exact_recovery_artifacts(self) -> None:
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_text(
            f"{req_start}\n{requirement_body()}\n{req_end}\n", encoding="utf-8"
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )

        def fail(point: str) -> None:
            if point == "after-requirements":
                raise RuntimeError("injected")

        with mock.patch.object(migration, "_restore_pair", side_effect=OSError("rollback")):
            with self.assertRaisesRegex(ProjectSpecError, "artifacts were retained"):
                migrate_project(self.project, failure_injector=fail)
        transaction = migration._transaction_dir(self.project)
        for name in (migration.JOURNAL_NAME, *migration.BACKUP_NAMES.values()):
            self.assertTrue((transaction / name).is_file())
            self.assertFalse((self.docs / name).exists())

        backup = transaction / migration.BACKUP_NAMES["requirements"]
        backup.write_bytes(backup.read_bytes() + b"tampered\n")
        backup.chmod(0o600)
        with self.assertRaisesRegex(ProjectSpecError, "backup is stale"):
            recover_migration(self.project)
        self.assertTrue((transaction / migration.JOURNAL_NAME).is_file())

    def test_partial_private_stage_after_crash_is_cleaned_on_retry(self) -> None:
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_text(
            f"{req_start}\n{requirement_body()}\n{req_end}\n", encoding="utf-8"
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )

        class SimulatedCrash(BaseException):
            pass

        def crash(point: str) -> None:
            if point == "after-requirements-backup":
                raise SimulatedCrash

        with self.assertRaises(SimulatedCrash):
            migrate_project(self.project, failure_injector=crash)
        root = migration._transaction_dir(self.project).parent
        self.assertTrue(
            any(path.name.startswith(migration.STAGING_PREFIX) for path in root.iterdir())
        )
        self.assertEqual(migrate_project(self.project)["status"], "migrated")
        self.assertFalse(
            any(path.name.startswith(migration.STAGING_PREFIX) for path in root.iterdir())
        )

    def test_published_private_transaction_recovers_after_crash(self) -> None:
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_text(
            f"{req_start}\n{requirement_body()}\n{req_end}\n", encoding="utf-8"
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )
        before = {path.name: path.read_bytes() for path in self.docs.iterdir()}

        class SimulatedCrash(BaseException):
            pass

        def crash(point: str) -> None:
            if point == "after-transaction-published":
                raise SimulatedCrash

        with self.assertRaises(SimulatedCrash):
            migrate_project(self.project, failure_injector=crash)
        transaction = migration._transaction_dir(self.project)
        self.assertEqual(
            {path.name for path in transaction.iterdir()},
            {migration.JOURNAL_NAME, *migration.BACKUP_NAMES.values()},
        )
        self.assertEqual(recover_migration(self.project)["status"], "recovered")
        self.assertFalse(transaction.exists())
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.docs.iterdir()})

    def test_partial_private_cleanup_after_crash_is_cleaned_on_retry(self) -> None:
        req_start = (
            "<!-- task-implementer:requirements:start schema=task-implementer/requirements-v1 -->"
        )
        req_end = "<!-- task-implementer:requirements:end -->"
        des_start = "<!-- task-implementer:design:start schema=task-implementer/design-v1 -->"
        des_end = "<!-- task-implementer:design:end -->"
        (self.docs / "requirements.md").write_text(
            f"{req_start}\n{requirement_body()}\n{req_end}\n", encoding="utf-8"
        )
        (self.docs / "design.md").write_text(
            f"{des_start}\n{design_body()}\n{des_end}\n", encoding="utf-8"
        )

        class SimulatedCrash(BaseException):
            pass

        original_unlink = Path.unlink
        crashed = False

        def crash_after_first_cleanup_child(path: Path, *args, **kwargs) -> None:
            nonlocal crashed
            original_unlink(path, *args, **kwargs)
            if not crashed and path.name in {
                migration.JOURNAL_NAME,
                *migration.BACKUP_NAMES.values(),
            }:
                crashed = True
                raise SimulatedCrash

        with mock.patch.object(Path, "unlink", crash_after_first_cleanup_child):
            with self.assertRaises(SimulatedCrash):
                migrate_project(self.project)

        transaction = migration._transaction_dir(self.project)
        self.assertFalse(transaction.exists())
        self.assertEqual(migrate_project(self.project)["status"], "unchanged")
        self.assertFalse(
            any(path.name.startswith(".cleanup.") for path in transaction.parent.iterdir())
        )

    def test_untracked_policy_cannot_disable_automation(self) -> None:
        policy = self.project / ".codex" / "project-specs.json"
        policy.parent.mkdir()
        policy.write_text(
            '{"schema":"maintain-project-specs.project.v1","mode":"disabled","scope":"."}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProjectSpecError, "committed Git blob"):
            inspect_project(self.project)

        git(self.project, "add", ".codex/project-specs.json")
        with self.assertRaisesRegex(ProjectSpecError, "committed Git blob"):
            inspect_project(self.project)
        git(self.project, "commit", "-qm", "disable project spec automation")
        self.assertEqual(inspect_project(self.project)["status"], "disabled")

    def test_incomplete_rich_records_cannot_issue_current_receipt(self) -> None:
        (self.docs / "requirements.md").write_bytes(
            canonical_document("requirements", rich_requirement_body("draft"))
        )
        (self.docs / "design.md").write_bytes(
            canonical_document("design", rich_design_body("ready"))
        )
        git(self.project, "add", "docs")
        self.assertEqual(inspect_project(self.project)["status"], "pending")
        with self.assertRaisesRegex(ProjectSpecError, "not current"):
            validate_project(self.project)

        (self.docs / "requirements.md").write_bytes(
            canonical_document("requirements", rich_requirement_body("active"))
        )
        (self.docs / "design.md").write_bytes(
            canonical_document("design", rich_design_body("draft"))
        )
        git(self.project, "add", "docs")
        self.assertEqual(inspect_project(self.project)["status"], "pending")
        with self.assertRaisesRegex(ProjectSpecError, "not current"):
            validate_project(self.project)


if __name__ == "__main__":
    unittest.main()
