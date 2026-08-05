#!/usr/bin/env python3
"""Static contract checks for local and worktree-delegated commits."""

from __future__ import annotations

from pathlib import Path
import unittest


SKILLS_ROOT = Path(__file__).resolve().parents[2]


class CommitContractTest(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (SKILLS_ROOT / relative).read_text(encoding="utf-8")

    def test_commit_keeps_whole_repo_local_only_safety(self) -> None:
        skill = self.text("commit/SKILL.md")
        for required in (
            "git add -A",
            "normal hooks",
            "Never push",
            "Stop before staging obvious secrets",
            "Never use `--no-verify`",
            "Never use `--allow-empty`",
            "git write-tree",
            "HEAD^{tree}",
        ):
            self.assertIn(required, skill)

    def test_worktree_delegation_is_fresh_bounded_and_exact(self) -> None:
        commit_skill = self.text("commit/SKILL.md")
        worktree_skill = self.text("worktree/SKILL.md")
        for required in (
            "fresh explicit `$worktree integrate <name>`",
            "ordinary child first or its primary source",
            "same branch, exactly one",
            "direct-descendant commit",
            "Never commit a nested/coordinated child",
            "durable source-scoped preparation claim",
        ):
            self.assertIn(required, commit_skill)
        for required in (
            "commit_order",
            "ordinary child first, primary source second",
            "Retain every successful commit",
            "integration-preflight",
            "--expected-source-head",
            "--expected-child-head",
            "integration-commit",
            "--expected-tree",
            "--preparation-token",
        ):
            self.assertIn(required, worktree_skill)

    def test_both_git_skills_remain_explicit_only(self) -> None:
        for relative in (
            "commit/agents/openai.yaml",
            "worktree/agents/openai.yaml",
        ):
            with self.subTest(relative=relative):
                self.assertIn("allow_implicit_invocation: false", self.text(relative))


if __name__ == "__main__":
    unittest.main()
