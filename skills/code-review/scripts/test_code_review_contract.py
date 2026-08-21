"""Deterministic contract tests for code-review behavior."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_REVIEW = ROOT / "code-review"


class CodeReviewContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def normalized(self, path: Path) -> str:
        return " ".join(self.read(path).split())

    def test_direct_invocation_owns_findings_first_closed_loop(self) -> None:
        skill = self.normalized(CODE_REVIEW / "SKILL.md")

        self.assertIn("Direct closed loop", skill)
        self.assertIn(
            "standalone attached skill invocation or explicit current-task "
            "directive to use `$code-review` defaults to review, safe scoped "
            "remediation, focused self-validation, and final reporting",
            skill,
        )
        self.assertIn("Complete the initial review before editing", skill)
        self.assertIn(
            "fix only findings classified `Auto-fix: Safe`", skill
        )
        self.assertIn("Before each remediation edit", skill)
        self.assertIn("establish its negative control", skill)
        self.assertIn(
            "observe it fail for the finding's expected reason",
            skill,
        )
        self.assertIn(
            "An already-green or unrelated check is not proof",
            skill,
        )
        self.assertIn(
            "rerun the same finding-specific proof and require it to pass",
            skill,
        )
        self.assertIn(
            "narrowest repository-native test target covering the changed "
            "behavior",
            skill,
        )
        self.assertIn(
            "configured syntax/lint/type checks scoped to changed files", skill
        )
        self.assertIn("`git diff --check`", skill)
        self.assertIn(
            "final review of only this skill's touched diff", skill
        )
        self.assertIn(
            "Prefer no-write and no-cache validation settings in every mode",
            skill,
        )
        self.assertIn(
            "confirm none remain before reporting",
            skill,
        )
        self.assertIn(
            "`code-review` workflow itself must never resolve, load, or invoke "
            "`align`",
            skill,
        )
        self.assertIn(
            "outer orchestrator remains responsible for any separate repository "
            "policy",
            skill,
        )
        self.assertLess(
            skill.index("Complete the initial review before editing"),
            skill.index("fix only findings classified `Auto-fix: Safe`"),
        )

    def test_implicit_report_only_and_no_write_overrides_are_explicit(self) -> None:
        skill = self.normalized(CODE_REVIEW / "SKILL.md")

        self.assertIn("Implicit report-only", skill)
        self.assertIn(
            "`$code-review` appears only in quoted text, discussion, examples, "
            "patches, or file content, never edit files or invoke remediation",
            skill,
        )
        for override in (
            "`review only`",
            "`audit only`",
            "`report only`",
            "`findings only`",
            "`do not edit`",
            "`do not fix`",
        ):
            self.assertIn(override, skill)
        self.assertIn(
            "Do not treat the word `review` alone as a report-only override", skill
        )
        self.assertIn(
            "If invocation intent is ambiguous, fail closed to report-only", skill
        )
        self.assertIn("snapshot the initial worktree state", skill)
        self.assertIn("repository-native no-write settings", skill)
        self.assertIn(
            "confirm the final worktree matches the initial state", skill
        )
        self.assertIn(
            "do not leave validation caches, reports, generated artifacts",
            skill,
        )

    def test_failed_focused_proof_cannot_be_reported_fixed(self) -> None:
        skill = self.normalized(CODE_REVIEW / "SKILL.md")
        rubric = self.normalized(
            CODE_REVIEW / "references" / "quality-rubric.md"
        )

        self.assertIn(
            "Do not mark a finding `Fixed` unless its focused proof passes", skill
        )
        self.assertIn(
            "restore only that attempted patch with a scoped inverse edit", rubric
        )
        self.assertIn("never a destructive Git command", rubric)
        self.assertIn("Mark the finding `Deferred`", rubric)
        self.assertIn("stop repairing that finding", rubric)
        self.assertIn(
            "If the focused proof passes and a broader check exposes a "
            "causally independent baseline failure",
            rubric,
        )
        self.assertIn(
            "If causality cannot be established, do not report the finding as "
            "`Fixed`",
            rubric,
        )

    def test_focused_proof_requires_a_negative_control(self) -> None:
        rubric = self.normalized(
            CODE_REVIEW / "references" / "quality-rubric.md"
        )

        self.assertIn("establish its negative control", rubric)
        self.assertIn(
            "observe it fail for the finding's expected reason", rubric
        )
        self.assertIn(
            "An already-green, irrelevant, or differently failing check cannot "
            "prove the finding",
            rubric,
        )
        self.assertIn(
            "If a safe negative control cannot be established, do not edit the "
            "implementation",
            rubric,
        )
        self.assertIn("Reclassify the remediation as `Auto-fix: Gated`", rubric)

    def test_priority_and_remediation_eligibility_are_independent(self) -> None:
        rubric = self.normalized(
            CODE_REVIEW / "references" / "quality-rubric.md"
        )

        self.assertIn("P0 is the highest priority", rubric)
        self.assertIn(
            "Priority describes impact and urgency; it does not authorize "
            "remediation",
            rubric,
        )
        self.assertIn(
            "A P0 or P1 may be gated, while a bounded P2 may be safe to fix",
            rubric,
        )
        self.assertIn("Mark `Auto-fix: Safe` only when all of these are true", rubric)
        self.assertIn("Mark `Auto-fix: Gated` when any of these apply", rubric)
        for gated_boundary in (
            "public API",
            "data lifecycle",
            "migration",
            "authentication",
            "authorization",
            "external systems",
            "broad rewrite",
        ):
            self.assertIn(gated_boundary, rubric)

    def test_final_report_preserves_prioritized_finding_ledger(self) -> None:
        skill = self.normalized(CODE_REVIEW / "SKILL.md")
        rubric = self.normalized(
            CODE_REVIEW / "references" / "quality-rubric.md"
        )

        self.assertIn("Preserve every initial finding in the final ledger", skill)
        self.assertIn("group findings by P0, P1, P2, P3, and Nit", skill)
        self.assertIn("stable finding ID and title", skill)
        self.assertIn("`Auto-fix: Safe` or `Auto-fix: Gated`", skill)
        for disposition in (
            "`Fixed`",
            "`Needs decision`",
            "`Needs owner review`",
            "`Deferred`",
            "`Not reproduced`",
        ):
            self.assertIn(disposition, skill)
            self.assertIn(disposition, rubric)
        self.assertIn(
            "the exact scope of focused validation and whether broader project "
            "alignment was intentionally not performed",
            skill,
        )
        self.assertIn("do not create a repository report artifact", skill)

    def test_nested_invocation_remains_parent_owned_and_report_only(self) -> None:
        skill = self.normalized(CODE_REVIEW / "SKILL.md")

        self.assertIn("Nested parent-owned", skill)
        self.assertIn(
            "inherit the parent's declared scope but remain report-only", skill
        )
        self.assertIn(
            "Return findings to the parent, which owns any separately "
            "authorized remediation",
            skill,
        )
        self.assertIn(
            "`code-review` workflow itself must never resolve, load, or invoke "
            "`align`",
            skill,
        )

    def test_isolated_install_has_no_sibling_dependency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="code-review-contract-") as tmp:
            isolated = Path(tmp) / "code-review"
            shutil.copytree(CODE_REVIEW, isolated)

            self.assertFalse((isolated.parent / "align").exists())
            skill = self.normalized(isolated / "SKILL.md")
            self.assertIn("Direct closed loop", skill)
            self.assertIn("focused-fix validation protocol", skill)
            self.assertIn(
                "`code-review` workflow itself must never resolve, load, or "
                "invoke `align`",
                skill,
            )
            self.assertNotIn("../align/SKILL.md", skill)
            self.assertNotIn(
                "Before the first edit in direct closed-loop mode, resolve and "
                "read `align`",
                skill,
            )

    def test_metadata_docs_and_evals_match_invocation_policy(self) -> None:
        metadata = self.read(CODE_REVIEW / "agents" / "openai.yaml")
        skill = self.normalized(CODE_REVIEW / "SKILL.md")
        readme = self.normalized(CODE_REVIEW / "README.md")
        evals = self.normalized(CODE_REVIEW / "evals" / "process-cases.md")

        self.assertIn("allow_implicit_invocation: true", metadata)
        self.assertIn("fix only safe in-scope findings", metadata)
        self.assertIn("focused repository-native checks", metadata)
        self.assertNotIn("$align", metadata)
        self.assertNotIn("revalidate changed files through align", metadata)
        explicit_no_write = (
            "explicit no-write intent such as review-only, audit-only, or "
            "report-only"
        )
        self.assertIn(explicit_no_write, skill)
        self.assertNotIn("review/audit/report-only", skill)
        self.assertIn("Implicit selection", readme)
        self.assertIn(
            "explicit no-write requests such as review-only, audit-only, or "
            "report-only",
            readme,
        )
        self.assertIn(
            "It is an explicit outer workflow: `code-review` itself never "
            "resolves, loads, or invokes it",
            readme,
        )
        self.assertNotIn("hands changed files to `$align`", readme)

        root_readme_path = ROOT / "README.md"
        if root_readme_path.is_file():
            root_readme = self.normalized(root_readme_path)
            self.assertIn(
                "explicit no-write requests such as review-only, audit-only, or "
                "report-only",
                root_readme,
            )
            self.assertIn(
                "The skill itself never resolves, loads, or invokes `align`",
                root_readme,
            )
            self.assertNotIn("revalidate changed files through align", root_readme)

        changelog_path = ROOT / "CHANGELOG.md"
        if changelog_path.is_file():
            changelog = self.normalized(changelog_path)
            self.assertIn(
                "explicit no-write requests such as review-only, audit-only, or "
                "report-only",
                changelog,
            )
            self.assertIn(
                "The skill itself no longer resolves, loads, or invokes `align`",
                changelog,
            )

        self.assertIn("Direct default", evals)
        self.assertIn("$code-review Review the current diff", evals)
        self.assertIn("Quoted token", evals)
        self.assertIn("the token is review data, not an invocation directive", evals)
        self.assertIn("Direct report-only", evals)
        self.assertIn("Implicit report-only", evals)
        self.assertIn(
            "must not leave caches, generated files, reports, or dependency "
            "drift",
            evals,
        )
        self.assertIn("Nested report-only", evals)
        self.assertIn("Isolated installation", evals)
        self.assertIn("Failed focused proof", evals)
        self.assertIn("Already-green proof", evals)
        self.assertNotIn("Missing align", evals)
        self.assertIn("gated P1 and safe P2", evals)
        self.assertIn(
            "outer-orchestrator policy requiring alignment after changes",
            evals,
        )


if __name__ == "__main__":
    unittest.main()
