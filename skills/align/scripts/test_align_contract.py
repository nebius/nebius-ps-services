"""Deterministic contract tests for align's child-review boundary."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALIGN = ROOT / "align"


class AlignContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def normalized(self, path: Path) -> str:
        return " ".join(self.read(path).split())

    def test_child_code_review_is_report_only(self) -> None:
        skill = self.normalized(ALIGN / "SKILL.md")

        self.assertIn("load `code-review` in nested report-only mode", skill)
        self.assertIn(
            "The child review must not edit files, invoke `align`", skill
        )
        self.assertIn("only `align` may apply its resulting safe findings", skill)
        self.assertIn(
            "Run the nested report-only `code-review` lane against the final diff",
            skill,
        )

    def test_parent_ledger_constraints_remain_generic(self) -> None:
        skill = self.normalized(ALIGN / "SKILL.md")

        self.assertIn(
            "When a parent workflow supplies a finding ledger", skill
        )
        self.assertIn(
            "preserve its IDs, classifications, and dispositions as handoff "
            "constraints",
            skill,
        )
        self.assertIn(
            "Do not remediate or reclassify parent-gated", skill
        )

    def test_metadata_and_docs_match_child_lane_ownership(self) -> None:
        metadata = self.read(ALIGN / "agents" / "openai.yaml")
        readme = self.normalized(ALIGN / "README.md")

        self.assertIn("report-only child lane", metadata)
        self.assertIn("`code-review` lane is report-only", readme)
        self.assertIn("`align` owns safe remediation", readme)

    def test_final_gate_cleans_task_created_validation_artifacts(self) -> None:
        skill = self.normalized(ALIGN / "SKILL.md")

        self.assertIn("Prefer repository-native no-cache settings", skill)
        self.assertIn(
            "confirm repository status contains no new validation residue",
            skill,
        )
        self.assertIn(
            "do not leave task-created validation artifacts in the repository",
            skill,
        )


if __name__ == "__main__":
    unittest.main()
