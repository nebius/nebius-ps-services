#!/usr/bin/env python3
"""Tests for advisory Agentic SDLC project-spec inspection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("validate_project_specs.py")
SPEC = importlib.util.spec_from_file_location("sdlc_spec_validation", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


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


class SpecValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="sdlc-spec-validation-")
        self.project = Path(self.temporary.name) / "project"
        (self.project / "docs").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=self.project, check=True
        )
        self.write(REQUIREMENTS, DESIGN)
        subprocess.run(["git", "add", "docs"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "baseline"], cwd=self.project, check=True
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, requirements: str, design: str) -> None:
        (self.project / "docs/requirements.md").write_text(
            requirements, encoding="utf-8"
        )
        (self.project / "docs/design.md").write_text(design, encoding="utf-8")

    def test_valid_specs_emit_current_advisory_snapshot(self) -> None:
        snapshot = validation.validate(self.project)
        self.assertEqual(snapshot["status"], "current")
        self.assertEqual(snapshot["owner"], "maintain-project-specs")
        self.assertEqual(snapshot["project_scope"], ".")
        self.assertRegex(snapshot["traceability_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(snapshot["requirements"]["sha256"], r"^[0-9a-f]{64}$")

    def test_ready_feature_with_placeholder_is_rejected(self) -> None:
        self.write(REQUIREMENTS, DESIGN.replace("Generate deterministic rules", "TODO"))
        self.assertEqual(validation.validate(self.project)["status"], "advisory")

    def test_unknown_requirement_mapping_is_rejected(self) -> None:
        self.write(REQUIREMENTS, DESIGN.replace("reqs=REQ-001", "reqs=REQ-999"))
        self.assertEqual(validation.validate(self.project)["status"], "advisory")

    def test_untracked_specs_are_rejected(self) -> None:
        subprocess.run(
            ["git", "rm", "--cached", "docs/design.md"],
            cwd=self.project,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        self.assertEqual(validation.validate(self.project)["status"], "advisory")

    def test_every_applicable_requirement_must_be_mapped(self) -> None:
        second_requirement = REQUIREMENTS[
            REQUIREMENTS.index("<!-- REQUIREMENT:") :
        ].replace("REQ-001", "REQ-002")
        self.write(REQUIREMENTS + "\n" + second_requirement, DESIGN)

        self.assertEqual(validation.validate(self.project)["status"], "advisory")

    def test_superseded_requirement_and_stale_feature_are_not_current_coverage(
        self,
    ) -> None:
        self.write(
            REQUIREMENTS.replace("status=active", "status=superseded"),
            DESIGN.replace("status=ready", "status=stale"),
        )

        receipt = validation.validate(self.project)

        self.assertEqual(receipt["validator_version"], 2)

    def test_requirements_covered_must_match_feature_marker(self) -> None:
        self.write(REQUIREMENTS, DESIGN.replace("- REQ-001", "- REQ-999"))

        self.assertEqual(validation.validate(self.project)["status"], "advisory")

    def test_requirements_covered_rejects_embedded_or_malformed_ids(self) -> None:
        for invalid in (
            "This does not cover REQ-001",
            "- TI-REQ-001",
            "- REQ-001-not-an-id",
        ):
            with self.subTest(invalid=invalid):
                self.write(REQUIREMENTS, DESIGN.replace("- REQ-001", invalid))
                self.assertEqual(
                    validation.validate(self.project)["status"], "advisory"
                )

    def test_foreign_owner_marker_is_rejected(self) -> None:
        self.write(
            REQUIREMENTS + "\n<!-- task-implementer:requirements:start -->\n", DESIGN
        )
        self.assertEqual(validation.validate(self.project)["status"], "advisory")


if __name__ == "__main__":
    unittest.main()
