#!/usr/bin/env python3
"""Tests for append-only corrective Agentic SDLC plans."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("corrective_plan.py")
SPEC = importlib.util.spec_from_file_location("corrective_plan_contract", MODULE_PATH)
assert SPEC and SPEC.loader
corrective_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(corrective_plan)

DIAGNOSIS_ID = "d" * 64
ORACLE = "Run the original AC-001 evaluator oracle."
TASK_ONE = """### TASK-001

- Requirements: REQ-001
- Goal: implement the original behavior
- Depends on: none
- Write claims: exact: src/original.py
- Conflict domains: code:original
- Validation: python3 -m unittest tests.test_original
- Done criteria: original behavior passes
- Rollback or stop conditions: stop on contract drift
"""
TASK_TWO = """### TASK-002

- Requirements: REQ-001
- Goal: integrate the original behavior
- Depends on: TASK-001
- Write claims: exact: src/integration.py
- Conflict domains: code:integration
- Validation: python3 -m unittest tests.test_integration
- Done criteria: original integration passes
- Rollback or stop conditions: stop on integration drift
"""
CORRECTIVE_TASK = f"""### TASK-003

- Requirements: REQ-001
- Goal: repair the diagnosed response mapper defect
- Depends on: TASK-002
- Write claims: exact: src/response_mapper.py
- Conflict domains: code:response-mapper
- Validation: python3 -m unittest tests.test_response_mapper
- Done criteria: original evaluator oracle and affected tests pass
- Rollback or stop conditions: stop if the public response schema changes
- Diagnosis: {DIAGNOSIS_ID}
- Regression oracle: {ORACLE}
"""


def base_plan(version: int, tasks: str) -> str:
    return f"""# FEAT-001 Plan v{version}

## Scope

- Feature: FEAT-001
- Requirements: REQ-001
- Plan status: locked when matching `.lock` exists

## Task Graph

{tasks}
## Planned Dependency Waves

- WAVE-001: TASK-001
"""


class CorrectivePlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous = self.root / "FEAT-001.plan.v1.md"
        self.previous.write_text(
            base_plan(1, TASK_ONE + "\n" + TASK_TWO + "\n"), encoding="utf-8"
        )
        self.previous.with_suffix(self.previous.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )
        self.manifest = self.root / "FEAT-001.completed-tasks.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema": corrective_plan.MANIFEST_SCHEMA,
                    "feature_id": "FEAT-001",
                    "plan_digest": corrective_plan.sha256_bytes(
                        self.previous.read_bytes()
                    ),
                    "completed_tasks": corrective_plan.task_definition_digests(
                        self.previous.read_text(encoding="utf-8")
                    ),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.corrective = self.root / "FEAT-001.plan.v2.md"
        manifest_digest = corrective_plan.sha256_bytes(self.manifest.read_bytes())
        self.corrective.write_text(
            base_plan(
                2,
                TASK_ONE
                + "\n"
                + TASK_TWO
                + "\n"
                + CORRECTIVE_TASK
                + "\n",
            )
            + f"""
## Corrective Plan

- Plan kind: corrective
- Supersedes: {self.previous.name}
- Diagnosis: {DIAGNOSIS_ID}
- Regression oracle: {ORACLE}
- Completed task manifest digest: sha256:{manifest_digest}
- Corrective tasks: TASK-003
""",
            encoding="utf-8",
        )
        self.corrective.with_suffix(self.corrective.suffix + ".lock").write_text(
            "locked\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self) -> dict[str, object]:
        return corrective_plan.validate_corrective_plan(
            self.previous,
            self.corrective,
            self.manifest,
            DIAGNOSIS_ID,
            ORACLE,
        )

    def test_valid_corrective_plan_preserves_history_and_appends_task(self) -> None:
        result = self.validate()
        self.assertEqual(result["previous_version"], 1)
        self.assertEqual(result["corrective_version"], 2)
        self.assertEqual(result["preserved_task_ids"], ["TASK-001", "TASK-002"])
        self.assertEqual(result["corrective_task_ids"], ["TASK-003"])

    def test_completed_task_definition_drift_fails_closed(self) -> None:
        self.corrective.write_text(
            self.corrective.read_text(encoding="utf-8").replace(
                "implement the original behavior",
                "reinterpret the completed behavior",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            corrective_plan.CorrectivePlanError, "changes existing task"
        ):
            self.validate()

    def test_non_adjacent_plan_version_fails_closed(self) -> None:
        self.corrective.write_text(
            self.corrective.read_text(encoding="utf-8").replace(
                "Plan v2", "Plan v4"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            corrective_plan.CorrectivePlanError, "adjacent feature plan version"
        ):
            self.validate()

    def test_missing_diagnosis_or_regression_binding_fails_closed(self) -> None:
        self.corrective.write_text(
            self.corrective.read_text(encoding="utf-8").replace(
                f"- Diagnosis: {DIAGNOSIS_ID}\n",
                "- Diagnosis: wrong\n",
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            corrective_plan.CorrectivePlanError, "exact diagnosis"
        ):
            self.validate()

    def test_append_only_correction_requires_new_task(self) -> None:
        text = self.corrective.read_text(encoding="utf-8")
        start = text.index("### TASK-003")
        end = text.index("## Planned Dependency Waves", start)
        text = text[:start] + text[end:]
        text = text.replace("- Corrective tasks: TASK-003", "- Corrective tasks: none")
        self.corrective.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(
            corrective_plan.CorrectivePlanError, "append"
        ):
            self.validate()

    def test_duplicate_task_id_fails_before_execution_preparation(self) -> None:
        text = self.corrective.read_text(encoding="utf-8")
        text = text.replace(
            CORRECTIVE_TASK,
            TASK_ONE + "\n" + CORRECTIVE_TASK,
            1,
        )
        self.corrective.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(
            corrective_plan.CorrectivePlanError, "duplicate task ID: TASK-001"
        ):
            self.validate()


if __name__ == "__main__":
    unittest.main()
