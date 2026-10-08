#!/usr/bin/env python3
"""Disposable-repository proof for canonical image/chart publication primitives."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SKILLS = Path(__file__).resolve().parents[2]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="publication-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "origin.git"
        self.repo = self.root / "work"
        self.env = dict(
            os.environ,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        self.run_cmd(
            ["git", "init", "--bare", "--initial-branch=main", str(self.remote)],
            cwd=self.root,
        )
        self.run_cmd(["git", "clone", str(self.remote), str(self.repo)], cwd=self.root)
        (self.repo / "CHANGELOG.md").write_text(
            "# Changes\n\n## [Unreleased]\n\n- Initial release.\n"
        )
        (self.repo / "Chart.yaml").write_text(
            "apiVersion: v2\nname: sample\nversion: 0.0.1\n"
        )
        self.git("add", "-A")
        self.git("commit", "-m", "initial")
        self.git("push", "origin", "main")
        self.initial = self.git("rev-parse", "HEAD")
        fakebin = self.root / "bin"
        fakebin.mkdir()
        helm = fakebin / "helm"
        helm.write_text("#!/bin/sh\nexit 0\n")
        helm.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env["PATH"]

    def run_cmd(self, argv, cwd=None, ok=True):
        result = subprocess.run(
            argv,
            cwd=cwd or self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if ok and result.returncode:
            self.fail(result.stderr + result.stdout)
        if not ok:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout.strip()

    def git(self, *args):
        return self.run_cmd(["git", *args])

    def helper(self, kind, mode, *args, ok=True):
        argv = [
            "bash",
            str(SKILLS / f"publish-{kind}/scripts/publish-{kind}-doer.sh"),
            "--mode",
            mode,
            "--tag",
            "1.2.3",
            "--tag-prefix",
            "sample",
            "--project-dir",
            str(self.repo),
        ]
        if kind == "helm":
            argv += ["--chart-dir", ".", "--chart-name", "sample"]
        if mode == "push" and "--origin-digest" not in args:
            frozen = hashlib.sha256(
                json.dumps([[str(self.remote)], [str(self.remote)]]).encode()
            ).hexdigest()
            argv += ["--origin-digest", frozen]
        return self.run_cmd([*argv, *args], ok=ok)

    def test_default_branch_prep_refused_before_changes(self):
        for kind in ["image", "helm"]:
            self.helper(kind, "prep", ok=False)
            self.assertEqual(self.git("status", "--porcelain"), "")
            self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)

    def test_prep_preserves_index_branch_history_and_unrelated_work(self):
        for kind in ["image", "helm"]:
            with self.subTest(kind=kind):
                self.git("switch", "-C", "feature", self.initial)
                self.git("restore", "--source=HEAD", "--staged", "--worktree", ".")
                (self.repo / "unrelated.txt").write_text("preserve\n")
                self.git("add", "unrelated.txt")
                before_index = self.git("write-tree")
                self.helper(kind, "prep")
                self.assertEqual(self.git("write-tree"), before_index)
                self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
                self.assertEqual(self.git("branch", "--show-current"), "feature")
                self.assertIn("sample-v1.2.3", (self.repo / "CHANGELOG.md").read_text())
                self.assertEqual(
                    (self.repo / "unrelated.txt").read_text(), "preserve\n"
                )
                self.assertEqual(
                    self.git("ls-remote", "--heads", "origin", "feature"), ""
                )
                self.git("reset", "--mixed", "HEAD")  # Only this disposable fixture.
                (self.repo / "unrelated.txt").unlink()

    def prepare_merged(self, kind):
        self.git("switch", "-c", "feature")
        self.helper(kind, "prep")
        self.git("add", "-A")
        self.git("commit", "-m", "prepared")
        commit = self.git("rev-parse", "HEAD")
        self.git(
            "push", "origin", "HEAD:main"
        )  # Fixture setup, not a production workflow.
        (self.repo / "later.txt").write_text("later\n")
        self.git("add", "-A")
        self.git("commit", "-m", "later")
        self.git("push", "origin", "HEAD:main")
        self.git("checkout", "--detach", commit)
        return commit

    def test_tag_uses_exact_result_when_main_advanced_and_push_is_idempotent(self):
        commit = self.prepare_merged("image")
        self.helper("image", "tag", "--release-commit", commit)
        obj = self.git("rev-parse", "refs/tags/sample-v1.2.3")
        self.helper("image", "push", "--release-commit", commit, "--tag-object", obj)
        self.helper("image", "push", "--release-commit", commit, "--tag-object", obj)
        self.assertEqual(self.git("rev-parse", "sample-v1.2.3^{commit}"), commit)
        self.assertIn(
            commit,
            self.git("ls-remote", "--tags", "origin", "refs/tags/sample-v1.2.3^{}"),
        )
        self.assertNotEqual(commit, self.git("rev-parse", "origin/main"))

    def test_helm_exact_tag_and_wrong_object_block(self):
        commit = self.prepare_merged("helm")
        self.helper("helm", "tag", "--release-commit", commit)
        self.helper(
            "helm",
            "push",
            "--release-commit",
            commit,
            "--tag-object",
            self.initial,
            ok=False,
        )
        self.assertEqual(self.git("ls-remote", "--tags", "origin"), "")

    def test_unmerged_result_refused(self):
        self.git("switch", "-c", "feature")
        self.helper("image", "prep")
        self.git("add", "-A")
        self.git("commit", "-m", "unmerged")
        self.helper(
            "image", "tag", "--release-commit", self.git("rev-parse", "HEAD"), ok=False
        )
        self.assertEqual(self.git("tag", "--list"), "")

    def test_wrong_checkout_and_default_override_refused(self):
        self.helper("image", "tag", "--release-commit", "c" * 40, ok=False)
        self.helper("helm", "prep", "--main-branch", "feature", ok=False)
        self.assertEqual(self.git("tag", "--list"), "")

    def test_remote_read_error_is_not_absent_tag(self):
        self.git("switch", "-c", "feature")
        self.git("remote", "set-url", "origin", str(self.root / "missing"))
        self.helper("image", "prep", ok=False)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_pushurl_mismatch_and_multipush_are_refused(self):
        commit = self.prepare_merged("image")
        self.helper("image", "tag", "--release-commit", commit)
        obj = self.git("rev-parse", "refs/tags/sample-v1.2.3")
        other = self.root / "other.git"
        self.run_cmd(["git", "init", "--bare", str(other)], cwd=self.root)
        self.git("remote", "set-url", "--push", "origin", str(other))
        self.helper(
            "image", "push", "--release-commit", commit, "--tag-object", obj, ok=False
        )
        self.assertEqual(
            self.run_cmd(["git", "--git-dir", str(other), "tag", "--list"]), ""
        )
        self.git("remote", "set-url", "--add", "--push", "origin", str(self.remote))
        self.helper(
            "helm", "push", "--release-commit", commit, "--tag-object", obj, ok=False
        )
        self.assertEqual(self.git("ls-remote", "--tags", "origin"), "")

    def test_frozen_origin_digest_is_required_and_bound(self):
        commit = self.prepare_merged("image")
        self.helper("image", "tag", "--release-commit", commit)
        obj = self.git("rev-parse", "refs/tags/sample-v1.2.3")
        self.helper(
            "image",
            "push",
            "--release-commit",
            commit,
            "--tag-object",
            obj,
            "--origin-digest",
            "a" * 64,
            ok=False,
        )
        self.assertEqual(self.git("ls-remote", "--tags", "origin"), "")

    def test_templates_match_canonical_body(self):
        for kind in ["helm", "image"]:
            canonical = (
                SKILLS / f"publish-{kind}/scripts/publish-{kind}-doer.sh"
            ).read_text()
            template = (
                SKILLS / f"publish-{kind}/assets/publish-{kind}.sh.template"
            ).read_text()
            self.assertEqual(
                canonical[canonical.index("S_RESET=") :],
                template[template.index("S_RESET=") :],
            )


if __name__ == "__main__":
    unittest.main()
