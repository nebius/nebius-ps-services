"""Real local transport coverage for clean pushes and zero-effect closure."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import io
import json
from contextlib import redirect_stdout

spec = importlib.util.spec_from_file_location("commit_push_fixture", Path(__file__).with_name("test-commit-transaction.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
tx, git = fixture.transaction, fixture.git


class PushCurrentTests(fixture.TransactionFixture):
    def setUp(self):
        super().setUp()
        self.origin = self.base / "origin.git"
        git(self.root, "init", "--bare", str(self.origin))
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "origin", "HEAD:refs/heads/main")
        git(self.root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    def run_helper(self, *arguments, expected=0, classify=True):
        output = io.StringIO()
        with redirect_stdout(output):
            code = tx.main(arguments)
        self.assertEqual(code, expected, output.getvalue())
        return json.loads(output.getvalue())

    def begin_push(self):
        self.authorize("commit and push")
        self.begin("commit-push")

    def push(self, expected=0):
        return self.run_helper(*self.task_args("push-current", action="commit-push"),
                               "--reviewed-head", git(self.root, "rev-parse", "HEAD"), expected=expected)

    def ahead(self):
        (self.root / "new.txt").write_text("new\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "local work")

    def test_empty_branch_finishes_without_publishing(self):
        self.begin_push()
        self.assertEqual(self.finish(action="commit-push")["status"], "completed")
        self.assertEqual(git(self.root, "ls-remote", "--heads", "origin", "refs/heads/feature/test"), "")

    def test_published_branch_finishes_without_transport(self):
        self.ahead()
        git(self.root, "push", "origin", "HEAD:refs/heads/feature/test")
        self.begin_push()
        self.assertEqual(self.finish(action="commit-push")["status"], "completed")

    def test_clean_ahead_push_and_replay(self):
        self.ahead()
        self.begin_push()
        result = self.push()
        self.assertEqual(result["status"], "published")
        self.assertEqual(self.push(), result)
        self.assertEqual(git(self.root, "rev-parse", "@{upstream}"), result["head"])
        self.assertEqual(self.finish(action="commit-push")["status"], "completed")

    def test_active_reservation_blocks_before_remote_read(self):
        self.ahead()
        self.begin_push()
        with mock.patch.object(tx, "_active_worktree_claims", return_value=["owned"]), mock.patch.object(tx._pr, "remote_head") as remote:
            self.assertEqual(self.push(expected=2)["code"], "history_changed")
            remote.assert_not_called()

    def test_remote_drift_and_mismatched_upstream_fail_closed(self):
        self.ahead()
        self.begin_push()
        git(self.root, "push", "origin", "HEAD~1:refs/heads/feature/test")
        self.assertEqual(self.push(expected=2)["code"], "history_changed")
        git(self.root, "config", "branch.feature/test.remote", "foreign")
        self.assertEqual(self.push(expected=2)["code"], "scope_changed")

    def test_active_hook_cannot_run_as_a_push_side_effect(self):
        self.ahead()
        self.begin_push()
        hook = self.root / ".git/hooks/pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o700)
        with mock.patch.object(tx._pr, "remote_head") as remote:
            self.assertEqual(self.push(expected=2)["code"], "scope_changed")
            remote.assert_not_called()

    def test_lost_acknowledgement_recovers_from_remote_readback(self):
        self.ahead()
        self.begin_push()
        original = tx._run_git
        def lost(root, args, **kwargs):
            result = original(root, args, **kwargs)
            if args[0] == "push":
                raise OSError("lost acknowledgement")
            return result
        with mock.patch.object(tx, "_run_git", side_effect=lost), self.assertRaises(OSError):
            self.push()
        self.assertEqual(self.push()["status"], "published")
        self.assertEqual(self.finish(action="commit-push")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
