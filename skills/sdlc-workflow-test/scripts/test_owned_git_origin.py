"""Real Git regressions for the disposable origin ownership boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import owned_git_origin as owner


class OwnedGitOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("# Fixture\n")
        owner.create_baseline(self.project)
        self.origin = self.root / owner.ORIGIN
        self.owner_id = "test-generation"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> dict[str, str]:
        return owner.initialize(self.project, self.root, self.owner_id)

    def git(self, root: Path, *arguments: str, check: bool = True):
        return subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True,
            text=True, check=check, timeout=15,
        )

    def test_owned_origin_is_idempotent_and_remains_frozen_after_project_commit(self):
        receipt = self.initialize()
        before = (self.root / owner.RECEIPT).read_bytes()
        self.assertEqual(self.initialize(), receipt)
        self.assertEqual((self.root / owner.RECEIPT).read_bytes(), before)
        (self.project / "feature.txt").write_text("real implementation\n")
        self.git(self.project, "add", "-A")
        self.git(self.project, "commit", "-m", "implement feature")
        owner.validate(self.project, self.root, self.owner_id)
        self.assertEqual(self.git(self.origin, "rev-parse", "HEAD").stdout.strip(), receipt["baseline_sha"])
        self.assertNotEqual(self.git(self.project, "rev-parse", "HEAD").stdout.strip(), receipt["baseline_sha"])

    def test_push_to_owned_origin_is_rejected(self):
        receipt = self.initialize()
        (self.project / "feature.txt").write_text("change\n")
        self.git(self.project, "add", "-A")
        self.git(self.project, "commit", "-m", "feature")
        result = self.git(self.project, "push", "origin", "HEAD:refs/heads/main", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-receive hook declined", result.stderr)
        self.assertEqual(self.git(self.origin, "rev-parse", "HEAD").stdout.strip(), receipt["baseline_sha"])

    def test_remote_and_hook_configuration_tampering_is_rejected(self):
        cases = (
            ("extra-remote", ("remote", "add", "extra", str(self.root / "foreign.git"))),
            ("multiple-urls", ("config", "--add", "remote.origin.url", str(self.root / "foreign.git"))),
            ("pushurl", ("config", "remote.origin.pushurl", str(self.origin))),
            ("rewrite", ("config", "url.https://example.invalid/.insteadOf", str(self.root))),
        )
        for label, arguments in cases:
            with self.subTest(label=label):
                self.initialize()
                config = self.project / ".git" / "config"
                before = config.read_bytes()
                self.git(self.project, *arguments)
                with self.assertRaises(owner.OriginError):
                    owner.validate(self.project, self.root, self.owner_id)
                config.write_bytes(before)
        self.git(self.origin, "config", "core.hooksPath", str(self.root / "foreign-hooks"))
        with self.assertRaisesRegex(owner.OriginError, "hooks configuration"):
            owner.validate(self.project, self.root, self.owner_id)

    def test_inherited_effective_hook_override_is_rejected(self):
        self.initialize()
        self.git(self.origin, "config", "--unset", "core.hooksPath")
        home = self.root / "home"
        home.mkdir()
        (home / ".gitconfig").write_text('[core]\n hooksPath = /nonexistent-hooks\n')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            with self.assertRaisesRegex(owner.OriginError, "hooks configuration"):
                owner.validate(self.project, self.root, self.owner_id)

    def test_receipt_generation_baseline_and_link_tampering_is_rejected(self):
        receipt = self.initialize()
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, "other-generation")
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, self.owner_id, baseline="0" * 40)
        path = self.root / owner.RECEIPT
        value = dict(receipt, project_root=str(self.root / "foreign"))
        path.write_text(json.dumps(value))
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, self.owner_id)
        path.write_text(json.dumps(receipt))
        os.link(path, self.root / "receipt-alias")
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, self.owner_id)

    def test_origin_ref_and_symlink_tampering_is_rejected(self):
        receipt = self.initialize()
        self.git(self.origin, "update-ref", "refs/heads/extra", receipt["baseline_sha"])
        with self.assertRaisesRegex(owner.OriginError, "references changed"):
            owner.validate(self.project, self.root, self.owner_id)
        self.git(self.origin, "update-ref", "-d", "refs/heads/extra")
        self.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/other")
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, self.owner_id)
        self.git(self.origin, "symbolic-ref", "HEAD", receipt["default_ref"])
        foreign = self.root / "foreign"
        foreign.mkdir()
        (self.origin / "objects" / "foreign").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(owner.OriginError):
            owner.validate(self.project, self.root, self.owner_id)

    def test_borrowed_objects_and_git_environment_overrides_reject_before_clone(self):
        alternate = self.project / ".git" / "objects" / "info" / "alternates"
        alternate.parent.mkdir(exist_ok=True)
        alternate.write_text(str(self.root / "foreign") + "\n")
        with self.assertRaisesRegex(owner.OriginError, "Borrowed"):
            self.initialize()
        self.assertFalse(self.origin.exists())
        alternate.unlink()
        with mock.patch.dict(os.environ, {"GIT_OBJECT_DIRECTORY": str(self.root / "foreign")}):
            with self.assertRaisesRegex(owner.OriginError, "environment overrides"):
                self.initialize()
        self.assertFalse(self.origin.exists())

    def test_unreceipted_origin_is_preserved_and_never_adopted(self):
        self.origin.mkdir()
        sentinel = self.origin / "foreign.txt"
        sentinel.write_text("preserve\n")
        with self.assertRaises(owner.OriginError):
            self.initialize()
        self.assertEqual(sentinel.read_text(), "preserve\n")
        self.assertEqual(self.git(self.project, "remote").stdout, "")

    def test_inherited_filter_is_rejected_before_baseline_or_origin_mutation(self):
        home = self.root / "filter-home"
        home.mkdir()
        sentinel = self.root / "unexpected-filter-execution"
        attributes = home / "attributes"
        attributes.write_text("* filter=probe\n")
        command = home / "filter.sh"
        command.write_text(f'#!/bin/sh\ntouch "{sentinel}"\ncat\n')
        command.chmod(0o700)
        (home / ".gitconfig").write_text(
            f'[core]\n attributesFile = {attributes}\n[filter "probe"]\n clean = {command}\n'
        )
        new_project = self.root / "new-project"
        new_project.mkdir()
        (new_project / "marker.json").write_text("{}\n")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            for operation in (lambda: owner.create_baseline(new_project), self.initialize):
                with self.assertRaisesRegex(owner.OriginError, "Command-bearing"):
                    operation()
        self.assertFalse((new_project / ".git").exists())
        self.assertFalse(self.origin.exists())
        self.assertFalse((self.root / owner.RECEIPT).exists())
        self.assertFalse(sentinel.exists())

    def test_inherited_transport_command_and_environment_are_rejected(self):
        self.initialize()
        home = self.root / "transport-home"
        home.mkdir()
        (home / ".gitconfig").write_text('[uploadpack]\n packObjectsHook = false\n')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            with self.assertRaisesRegex(owner.OriginError, "Command-bearing"):
                owner.validate(self.project, self.root, self.owner_id)
        for variable in ("GIT_EXEC_PATH", "GIT_ALLOW_PROTOCOL", "GIT_SSH_COMMAND"):
            with self.subTest(variable=variable), mock.patch.dict(os.environ, {variable: "forbidden"}):
                with self.assertRaisesRegex(owner.OriginError, "environment overrides"):
                    owner.validate(self.project, self.root, self.owner_id)

    def test_disabled_fsmonitor_configuration_remains_safe(self):
        home = self.root / "disabled-home"
        home.mkdir()
        (home / ".gitconfig").write_text('[core]\n fsmonitor = false\n')
        self.git(self.project, "config", "core.fsmonitor", "false")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.initialize()
            owner.validate(self.project, self.root, self.owner_id)
            fresh = self.root / "fresh"
            fresh.mkdir()
            (fresh / "README.md").write_text("# Fixture\n")
            owner.create_baseline(fresh)

    def test_inherited_or_local_hooks_cannot_execute_during_creation(self):
        home = self.root / "hook-home"
        home.mkdir()
        hooks = self.root / "foreign-hooks"
        hooks.mkdir()
        sentinel = self.root / "unexpected-hook-execution"
        hook = hooks / "reference-transaction"
        hook.write_text(f'#!/bin/sh\ntouch "{sentinel}"\n')
        hook.chmod(0o700)
        (home / ".gitconfig").write_text(f'[core]\n hooksPath = {hooks}\n')
        fresh = self.root / "fresh"
        fresh.mkdir()
        (fresh / "README.md").write_text("# Fixture\n")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            for operation in (lambda: owner.create_baseline(fresh), self.initialize):
                with self.assertRaisesRegex(owner.OriginError, "hooks configuration"):
                    operation()
        self.assertFalse((fresh / ".git").exists())
        self.git(self.project, "config", "core.hooksPath", str(hooks))
        with self.assertRaisesRegex(owner.OriginError, "hooks configuration"):
            self.initialize()
        self.git(self.project, "config", "--unset", "core.hooksPath")
        default_hooks = self.project / ".git" / "hooks"
        default_hooks.mkdir(exist_ok=True)
        (default_hooks / "reference-transaction").write_bytes(hook.read_bytes())
        (default_hooks / "reference-transaction").chmod(0o700)
        with self.assertRaisesRegex(owner.OriginError, "Active project Git hooks"):
            self.initialize()
        self.assertFalse(sentinel.exists())
        self.assertFalse(self.origin.exists())
        self.assertFalse((self.root / owner.RECEIPT).exists())

    def test_interrupted_receipt_publication_is_not_adopted(self):
        with mock.patch.object(owner.os, "replace", side_effect=OSError("injected publication failure")):
            with self.assertRaises(OSError):
                self.initialize()
        self.assertTrue(self.origin.is_dir())
        self.assertFalse((self.root / owner.RECEIPT).exists())
        with self.assertRaises(owner.OriginError):
            self.initialize()


if __name__ == "__main__":
    unittest.main()
