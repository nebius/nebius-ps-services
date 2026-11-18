#!/usr/bin/env python3
"""Behavioral regressions using disposable local Git repositories only."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("create_branch.py")
SPEC = importlib.util.spec_from_file_location("create_branch", SCRIPT)
assert SPEC and SPEC.loader
skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skill)


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="create-branch-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Skill Test",
            "GIT_AUTHOR_EMAIL": "skill-test@example.invalid",
            "GIT_COMMITTER_NAME": "Skill Test",
            "GIT_COMMITTER_EMAIL": "skill-test@example.invalid",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.origin = self.directory / "sample-repo.git"
        self.seed = self.directory / "seed"
        self.root = self.directory / "checkout"
        self.command(self.directory, "init", "--bare", "--initial-branch=main", str(self.origin))
        self.command(self.directory, "clone", str(self.origin), str(self.seed))
        (self.seed / "README.md").write_text("base\n")
        (self.seed / ".gitignore").write_text("local-only.txt\n")
        self.commit(self.seed, "base")
        self.command(self.seed, "push", "origin", "main")
        self.command(self.directory, "clone", str(self.origin), str(self.root))
        self.command(self.root, "switch", "-c", "work")
        self.base = self.value("HEAD")

    def command(self, cwd, *args, check=True):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=False)
        if check and result.returncode:
            self.fail(f"Fixture Git failed: {args[0]}: {result.stderr.decode()}")
        return result.stdout.decode().strip()

    def value(self, ref):
        return self.command(self.root, "rev-parse", ref)

    def branch(self):
        return self.command(self.root, "symbolic-ref", "--short", "HEAD")

    def commit(self, cwd, message):
        self.command(cwd, "add", "-A")
        self.command(cwd, "commit", "-m", message)

    def advance_remote(self):
        (self.seed / "README.md").write_text("updated remote\n")
        self.commit(self.seed, "remote update")
        self.command(self.seed, "push", "origin", "HEAD:main")
        return self.command(self.seed, "rev-parse", "HEAD")

    def run_skill(self, name="new-work", cwd=None):
        workflow = skill.Workflow(cwd or self.root)
        return workflow.run(name)

    def assert_success(self, result, previous=None):
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.branch(), result["branch"])
        self.assertEqual(self.value("HEAD"), self.value(result["default_branch"]))
        self.assertEqual(self.value("HEAD"), result["fetched_commit"])
        self.assertEqual(self.value("work"), previous or self.base)
        self.assertEqual(self.command(self.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.command(self.root, "for-each-ref", "--format=%(upstream)",
                                      "refs/heads/" + result["branch"]), "")
        remote = self.command(self.root, "ls-remote", "--heads", "origin", result["branch"])
        self.assertEqual(remote, "")

    def assert_blocked_unchanged(self, reason, name="new-work"):
        before = (self.branch(), self.value("HEAD"), self.value("main"))
        with self.assertRaisesRegex(skill.Blocked, reason):
            self.run_skill(name)
        self.assertEqual((self.branch(), self.value("HEAD"), self.value("main")), before)

    def test_feature_already_current(self):
        result = self.run_skill()
        self.assert_success(result)
        self.assertEqual(result["default_update"], "unchanged")

    def test_feature_fast_forward_preserves_unmerged_work(self):
        (self.root / "feature.txt").write_text("preserve this commit\n")
        self.commit(self.root, "unmerged feature")
        previous = self.value("HEAD")
        target = self.advance_remote()
        result = self.run_skill()
        self.assert_success(result, previous)
        self.assertEqual(result["head"], target)
        self.assertFalse((self.root / "feature.txt").exists())
        self.assertEqual(result["default_update"], "fast-forwarded")

    def test_current_default_fast_forward(self):
        self.command(self.root, "switch", "main")
        target = self.advance_remote()
        result = self.run_skill()
        self.assert_success(result)
        self.assertEqual(result["previous_branch"], "main")
        self.assertEqual(result["head"], target)

    def test_current_default_already_current(self):
        self.command(self.root, "switch", "main")
        self.assert_success(self.run_skill())

    def test_missing_local_default_and_tracking_ref(self):
        self.command(self.root, "branch", "-D", "main")
        self.command(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        result = self.run_skill()
        self.assert_success(result)
        self.assertEqual(result["default_update"], "created")
        self.assertEqual(self.command(self.root, "rev-parse", "--abbrev-ref", "main@{upstream}"),
                         "origin/main")

    def test_missing_default_with_narrow_fetch_mapping(self):
        self.command(self.root, "branch", "-D", "main")
        self.command(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        existing = "+refs/heads/work:refs/remotes/origin/work"
        self.command(self.root, "config", "remote.origin.fetch", existing)
        result = self.run_skill()
        self.assert_success(result)
        self.assertEqual(self.command(self.root, "rev-parse", "--abbrev-ref", "main@{upstream}"),
                         "origin/main")
        self.assertEqual(self.command(self.root, "config", "--get-all", "remote.origin.fetch").splitlines(),
                         [existing, "+refs/heads/main:refs/remotes/origin/main"])
        self.assertEqual(self.command(self.root, "config", "branch.main.remote"), "origin")
        self.assertEqual(self.command(self.root, "config", "branch.main.merge"), "refs/heads/main")

    def test_missing_default_rejects_excluded_or_conflicting_tracking(self):
        self.command(self.seed, "push", "origin", "HEAD:ain")
        self.command(self.root, "branch", "-D", "main")
        self.command(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        for mappings in [
            ["+refs/heads/*:refs/remotes/origin/m*"],
            ["+refs/heads/*:refs/remotes/origin/*", "+refs/heads/*:refs/remotes/origin/m*"],
            ["+refs/heads/ain:refs/remotes/origin/main"],
            ["+refs/heads/*:refs/remotes/origin/*", "^refs/heads/main"],
            ["+refs/heads/*:refs/remotes/origin/*", "^refs/heads/m*"],
        ]:
            with self.subTest(mappings=mappings):
                self.command(self.root, "config", "--unset-all", "remote.origin.fetch")
                for mapping in mappings:
                    self.command(self.root, "config", "--add", "remote.origin.fetch", mapping)
                with self.assertRaisesRegex(skill.Blocked, "excludes|conflicts"):
                    self.run_skill()
                self.assertEqual(self.branch(), "work")
                self.assertEqual(self.value("HEAD"), self.base)
                self.assertEqual(self.command(self.root, "for-each-ref", "--format=%(refname)",
                                              "refs/heads/main", "refs/heads/new-work"), "")
                self.assertEqual(self.command(self.root, "config", "--get-all", "remote.origin.fetch").splitlines(),
                                 mappings)

    def test_renamed_remote_default_ignores_cached_head(self):
        self.command(self.seed, "push", "origin", "HEAD:trunk")
        self.command(self.origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
        result = self.run_skill()
        self.assert_success(result)
        self.assertEqual(result["default_branch"], "trunk")

    def test_nested_directory_invocation(self):
        nested = self.root / "nested" / "directory"
        nested.mkdir(parents=True)
        self.assert_success(self.run_skill(cwd=nested))

    def test_checkout_path_whitespace_cannot_target_sibling(self):
        sibling = self.root
        intended = self.directory / "checkout "
        self.root.rename(intended)
        self.command(self.directory, "clone", str(self.origin), str(sibling))
        self.command(sibling, "switch", "-c", "work")
        self.root = intended
        self.assert_success(self.run_skill())
        self.assertEqual(self.command(sibling, "symbolic-ref", "--short", "HEAD"), "work")
        self.assertEqual(self.command(sibling, "for-each-ref", "--format=%(refname)",
                                      "refs/heads/new-work"), "")

    def test_generated_name_format_and_repeat_invocation(self):
        first = self.run_skill(None)
        self.assertRegex(first["branch"], r"^sample-repo-[a-z0-9]{8}$")
        self.assert_success(first)
        second = self.run_skill(None)
        self.assert_success(second)
        self.assertNotEqual(first["branch"], second["branch"])
        self.assertEqual(self.value(first["branch"]), first["head"])

    def test_generated_collision_retries_local_and_remote(self):
        self.command(self.root, "branch", "sample-repo-aaaaaaaa")
        self.command(self.seed, "push", "origin", "HEAD:sample-repo-bbbbbbbb")
        sequence = iter("a" * 8 + "b" * 8 + "c" * 8)
        with mock.patch.object(skill.secrets, "choice", side_effect=lambda _: next(sequence)):
            result = self.run_skill(None)
        self.assert_success(result)
        self.assertEqual(result["branch"], "sample-repo-cccccccc")

    def test_generated_collision_limit(self):
        self.command(self.root, "branch", "sample-repo-aaaaaaaa")
        with mock.patch.object(skill.secrets, "choice", return_value="a") as generator:
            self.assert_blocked_unchanged("ten times", None)
        self.assertEqual(generator.call_count, 80)

    def test_explicit_local_remote_and_namespace_collisions(self):
        self.command(self.root, "branch", "local-taken")
        self.command(self.seed, "push", "origin", "HEAD:remote-taken")
        for name in ["local-taken", "remote-taken", "local-taken/child", "MAIN"]:
            with self.subTest(name=name):
                self.assert_blocked_unchanged("occupied|conflicts", name)

    def test_literal_name_validation(self):
        for name in ["", "-bad", "bad name", "bad..name", "@{-1}", "HEAD", "@"]:
            with self.subTest(name=name):
                self.assert_blocked_unchanged("Invalid", name)

    def test_valid_explicit_name_preserved(self):
        self.assert_success(self.run_skill("feature/Exact-Name_42"))
        self.assertEqual(self.branch(), "feature/Exact-Name_42")

    def test_local_default_ahead_stops(self):
        self.command(self.root, "switch", "main")
        (self.root / "ahead.txt").write_text("local main commit\n")
        self.commit(self.root, "ahead")
        self.command(self.root, "switch", "work")
        self.assert_blocked_unchanged("ahead, diverged")

    def test_local_default_diverged_stops(self):
        self.command(self.root, "switch", "main")
        (self.root / "ahead.txt").write_text("local main commit\n")
        self.commit(self.root, "ahead")
        self.command(self.root, "switch", "work")
        self.advance_remote()
        self.assert_blocked_unchanged("ahead, diverged")

    def test_dirty_staged_unstaged_and_untracked_preserved(self):
        for mode in ["unstaged", "staged", "untracked"]:
            with self.subTest(mode=mode):
                path = self.root / ("README.md" if mode != "untracked" else "new.txt")
                before = path.read_bytes() if path.exists() else None
                path.write_text("user work\n")
                if mode == "staged":
                    self.command(self.root, "add", str(path))
                self.assert_blocked_unchanged("dirty")
                self.assertEqual(path.read_text(), "user work\n")
                if mode == "staged":
                    self.command(self.root, "restore", "--staged", str(path))
                if before is None:
                    path.unlink()
                else:
                    path.write_bytes(before)

    def test_ignored_collision_preserves_user_file(self):
        (self.seed / "local-only.txt").write_text("tracked remote\n")
        self.command(self.seed, "add", "-f", "local-only.txt")
        self.command(self.seed, "commit", "-m", "track ignored path")
        self.command(self.seed, "push", "origin", "main")
        (self.root / "local-only.txt").write_text("precious ignored content\n")
        with self.assertRaisesRegex(skill.Blocked, "git merge failed"):
            self.run_skill()
        self.assertEqual((self.root / "local-only.txt").read_text(), "precious ignored content\n")
        self.assertEqual(self.value("main"), self.base)
        self.assertEqual(self.value("work"), self.base)

    def test_assume_unchanged_cannot_hide_local_work(self):
        self.command(self.root, "update-index", "--assume-unchanged", "README.md")
        (self.root / "README.md").write_text("hidden user work\n")
        self.assert_blocked_unchanged("Index flags hide")
        self.assertEqual((self.root / "README.md").read_text(), "hidden user work\n")

    def test_skip_worktree_cannot_hide_local_work(self):
        self.command(self.root, "update-index", "--skip-worktree", "README.md")
        (self.root / "README.md").write_text("hidden user work\n")
        self.assert_blocked_unchanged("Index flags hide")
        self.assertEqual((self.root / "README.md").read_text(), "hidden user work\n")

    def test_pending_operations_and_locks_preserved(self):
        for marker in ["MERGE_HEAD", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                       "sequencer", "BISECT_START", "index.lock"]:
            with self.subTest(marker=marker):
                path = self.root / ".git" / marker
                path.write_text(self.base + "\n")
                with self.assertRaises(skill.Blocked):
                    self.run_skill()
                self.assertTrue(path.exists())
                self.assertEqual(self.branch(), "work")
                path.unlink()

    def test_detached_head_stops(self):
        self.command(self.root, "switch", "--detach")
        with self.assertRaises(skill.Blocked):
            self.run_skill()
        self.assertEqual(self.value("HEAD"), self.base)

    def test_occupied_default_stops(self):
        other = self.directory / "other"
        self.command(self.root, "worktree", "add", str(other), "main")
        self.assert_blocked_unchanged("registered in another worktree")

    def test_current_branch_linked_registration_stops(self):
        other = self.directory / "other"
        self.command(self.root, "worktree", "add", "--force", str(other), "work")
        self.assert_blocked_unchanged("registered in another worktree")

    def test_linked_checkout_invocation_stops(self):
        other = self.directory / "other"
        self.command(self.root, "worktree", "add", "-b", "linked", str(other))
        with self.assertRaisesRegex(skill.Blocked, "ordinary primary"):
            self.run_skill(cwd=other)
        self.assertEqual(self.command(other, "symbolic-ref", "--short", "HEAD"), "linked")

    def test_unrelated_prunable_detached_worktree_is_allowed(self):
        other = self.directory / "other"
        self.command(self.root, "worktree", "add", "--detach", str(other))
        shutil.rmtree(other)
        self.assert_success(self.run_skill())

    def test_fetch_failure_never_uses_cached_target(self):
        discover = skill.Workflow.discover
        def break_transport(workflow):
            result = discover(workflow)
            self.origin.rename(self.directory / "offline.git")
            return result
        with mock.patch.object(skill.Workflow, "discover", break_transport):
            self.assert_blocked_unchanged("git fetch failed")

    def test_conflicting_merge_options_stops(self):
        self.command(self.root, "config", "branch.main.mergeOptions", "--squash --autostash")
        self.assert_blocked_unchanged("merge options conflict")

    def test_symbolic_tracking_ref_stops_before_fetch(self):
        self.command(self.root, "symbolic-ref", "refs/remotes/origin/main", "refs/heads/work")
        self.assert_blocked_unchanged("symbolic")

    def test_hook_failure_reports_actual_changed_checkout(self):
        hook = self.root / ".git/hooks/post-checkout"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o700)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], cwd=self.root,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["observed"]["branch"], "main")
        self.assertEqual(self.value("work"), self.base)
        self.assertNotIn("origin", data["reason"])

    def test_hook_created_dirt_is_preserved_and_blocks(self):
        hook = self.root / ".git/hooks/post-checkout"
        hook.write_text("#!/bin/sh\nprintf 'hook work\\n' >> README.md\n")
        hook.chmod(0o700)
        with self.assertRaisesRegex(skill.Blocked, "dirty"):
            self.run_skill()
        self.assertIn("hook work", (self.root / "README.md").read_text())

    def test_unexpected_tracking_fails_final_verification(self):
        hook = self.root / ".git/hooks/post-checkout"
        hook.write_text("#!/bin/sh\ngit branch --set-upstream-to=origin/main >/dev/null\n")
        hook.chmod(0o700)
        with self.assertRaisesRegex(skill.Blocked, "upstream tracking"):
            self.run_skill()
        self.assertEqual(self.branch(), "new-work")
        self.assertEqual(self.value("work"), self.base)

    def test_concurrent_default_update_detected(self):
        (self.root / "race.txt").write_text("another writer\n")
        self.commit(self.root, "race commit")
        race = self.value("HEAD")
        original_git = skill.git
        def change_after_fetch(cwd, *args, **kwargs):
            result = original_git(cwd, *args, **kwargs)
            if args[0] == "fetch":
                self.command(self.root, "update-ref", "refs/heads/main", race)
            return result
        with mock.patch.object(skill, "git", change_after_fetch):
            with self.assertRaisesRegex(skill.Blocked, "Local default changed"):
                self.run_skill()
        self.assertEqual(self.branch(), "work")
        self.assertEqual(self.value("main"), race)

    def test_concurrent_name_creation_detected(self):
        original_git = skill.git
        def change_after_fetch(cwd, *args, **kwargs):
            result = original_git(cwd, *args, **kwargs)
            if args[0] == "fetch":
                self.command(self.root, "branch", "new-work")
            return result
        with mock.patch.object(skill, "git", change_after_fetch):
            self.assert_blocked_unchanged("became occupied")
        self.assertEqual(self.value("new-work"), self.base)

    def test_dirty_submodule_is_preserved(self):
        self.command(self.root, "-c", "protocol.file.allow=always", "submodule", "add",
                     str(self.origin), "submodule")
        self.commit(self.root, "add submodule")
        path = self.root / "submodule/README.md"
        path.write_text("submodule work\n")
        self.assert_blocked_unchanged("dirty")
        self.assertEqual(path.read_text(), "submodule work\n")

    def test_environment_override_stops(self):
        with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(self.directory / "other-index")}):
            with self.assertRaisesRegex(skill.Blocked, "GIT_INDEX_FILE"):
                self.run_skill()

    def test_help_does_not_inspect_git(self):
        with mock.patch.object(skill, "Workflow") as workflow:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stop:
                skill.main(["--help"])
            self.assertEqual(stop.exception.code, 0)
            workflow.assert_not_called()

    def test_slug_and_collision_boundaries(self):
        for url in ["https://example.invalid/team/Sample_Repo.git",
                    "git@example.invalid:team/Sample_Repo.git", "/tmp/Sample_Repo.git"]:
            self.assertEqual(skill.repository_slug(url), "sample-repo")
        self.assertTrue(skill.conflicts("a", {"a/b"}))
        self.assertFalse(skill.conflicts("a", {"ab"}))


if __name__ == "__main__":
    unittest.main()
