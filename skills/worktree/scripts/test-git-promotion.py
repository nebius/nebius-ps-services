#!/usr/bin/env python3
"""Offline real-Git tests for shared promotion primitives."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("git_promotion.py")
SPEC = importlib.util.spec_from_file_location("git_promotion", MODULE_PATH)
assert SPEC and SPEC.loader
promotion = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = promotion
SPEC.loader.exec_module(promotion)


def git(*arguments: str, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class GitPromotionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        git("init", "-q", "-b", "trunk", str(self.repo), cwd=self.root)
        git("config", "user.name", "Promotion Test", cwd=self.repo)
        git("config", "user.email", "promotion@example.invalid", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        git("commit", "-qm", "initial", cwd=self.repo)
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-qu", "origin", "trunk", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/trunk", cwd=self.origin)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resolves_symbolic_remote_default_without_name_guessing(self) -> None:
        result = promotion.resolve_remote_default(self.repo)
        self.assertEqual(result["default_branch"], "trunk")
        self.assertEqual(result["default_ref"], "origin/trunk")
        self.assertEqual(
            result["default_head"], git("rev-parse", "HEAD", cwd=self.repo)
        )

    def test_default_checkout_is_promoted_to_deterministic_feature_branch(self) -> None:
        initial = git("rev-parse", "HEAD", cwd=self.repo)
        result = promotion.ensure_promotion_branch(
            self.repo,
            lifecycle_id="run-123",
            task_slug="Fix unsafe cleanup",
        )
        self.assertEqual(result["promotion_source"], "auto-created")
        self.assertEqual(
            result["promotion_branch"], "feature/fix-unsafe-cleanup-272812a7"
        )
        self.assertEqual(
            git("branch", "--show-current", cwd=self.repo), result["promotion_branch"]
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), initial)

    def test_existing_non_default_branch_is_reused(self) -> None:
        git("switch", "-qc", "feature/already-here", cwd=self.repo)
        result = promotion.ensure_promotion_branch(
            self.repo,
            lifecycle_id="run-123",
            task_slug="ignored",
        )
        self.assertEqual(result["promotion_source"], "existing")
        self.assertEqual(result["promotion_branch"], "feature/already-here")

    def test_dirty_default_checkout_is_not_switched(self) -> None:
        (self.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(promotion.GitPromotionError, "must be clean"):
            promotion.ensure_promotion_branch(
                self.repo,
                lifecycle_id="run-123",
                task_slug="work",
            )
        self.assertEqual(git("branch", "--show-current", cwd=self.repo), "trunk")

    def test_recorded_remote_default_head_drift_is_rejected(self) -> None:
        recorded = promotion.resolve_remote_default(self.repo)
        tree = git("rev-parse", "HEAD^{tree}", cwd=self.repo)
        advanced = git(
            "commit-tree",
            tree,
            "-p",
            recorded["default_head"],
            "-m",
            "advance remote default",
            cwd=self.repo,
        )
        git("push", "-q", "origin", f"{advanced}:refs/heads/trunk", cwd=self.repo)

        with self.assertRaisesRegex(
            promotion.GitPromotionError, "remote default changed"
        ):
            promotion.verify_remote_default(
                self.repo,
                expected_remote=recorded["remote"],
                expected_branch=recorded["default_branch"],
                expected_ref=recorded["default_ref"],
                expected_head=recorded["default_head"],
            )

    def test_ff_only_promotion_and_exact_branch_deletion(self) -> None:
        base = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-qc", "feature/integration", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("integrated\n", encoding="utf-8")
        git("commit", "-qam", "integrated", cwd=self.repo)
        target = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "trunk", cwd=self.repo)

        result = promotion.promote_ff_only(
            self.repo,
            expected_branch="trunk",
            expected_base=base,
            target="feature/integration",
        )
        self.assertEqual(result["head"], target)
        promotion.delete_local_branch_exact(
            self.repo,
            branch="feature/integration",
            expected_head=target,
        )
        exists = subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/feature/integration",
            ],
            cwd=self.repo,
            check=False,
        )
        self.assertEqual(exists.returncode, 1)

    def test_promotion_lock_regular_file_fails_with_structured_error(self) -> None:
        lock_directory = promotion.common_git_dir(self.repo) / "codex-workflows"
        lock_directory.write_text("not a directory\n", encoding="utf-8")

        with self.assertRaisesRegex(
            promotion.GitPromotionError,
            "promotion lock directory could not be prepared safely",
        ):
            with promotion.promotion_lock(self.repo):
                self.fail("unsafe lock path unexpectedly acquired")


if __name__ == "__main__":
    unittest.main()
