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
from git_fixture_environment import isolated_git_configuration


class OwnedGitOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_git_configuration())
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.fixture(self.root)
        self.origin = self.root / owner.ORIGIN
        self.owner_id = "lightweight"

    def fixture(self, root):
        root.mkdir(mode=0o700, exist_ok=True)
        marker = root / ".sdlc-workflow-test-root.json"
        marker.write_text(json.dumps({"schema": "agentic-sdlc/verification-root-v1"}))
        marker.chmod(0o600)
        project = root / "disposable-project"
        project.mkdir()
        (project / ".sdlc-workflow-test-fixture.json").write_text('{"schema":"agentic-sdlc/disposable-fixture-v2"}\n')
        (project / "README.md").write_text("# Fixture\n")
        owner.prepare_configuration(project, root, "lightweight", kind="lightweight")
        owner.create_baseline(project)
        return project

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> dict[str, str]:
        return owner.initialize(self.project, self.root, self.owner_id)

    def test_git_fixture_configuration_restores_environment_after_failure(self):
        before = dict(os.environ)
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with isolated_git_configuration() as user_home:
                self.assertNotEqual(str(user_home), before["HOME"])
                raise RuntimeError("fixture failure")
        self.assertEqual(dict(os.environ), before)
        self.assertFalse(user_home.exists())

    def git(self, root: Path, *arguments: str, check: bool = True):
        return subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True,
            text=True, check=check, timeout=15, env=owner.environment(root, bootstrap=True),
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

    def test_clean_validation_does_not_refresh_index_stat_cache(self):
        self.initialize()
        tracked = self.project / "README.md"
        info = tracked.stat()
        os.utime(tracked, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        index = self.project / ".git/index"
        before = (index.stat().st_ino, index.read_bytes())
        owner.validate(self.project, self.root, self.owner_id, require_clean=True)
        self.assertEqual((index.stat().st_ino, index.read_bytes()), before)

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
        with self.assertRaisesRegex(owner.OriginError, "configuration|hook"):
            owner.validate(self.project, self.root, self.owner_id)

    def test_inherited_effective_hook_override_is_rejected(self):
        self.initialize()
        self.git(self.origin, "config", "--unset", "core.hooksPath")
        home = self.root / "home"
        home.mkdir()
        (home / ".gitconfig").write_text('[core]\n hooksPath = /nonexistent-hooks\n')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            with self.assertRaises(owner.OriginError):
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
        with self.assertRaisesRegex(owner.OriginError, "UNSAFE_GIT_ENVIRONMENT.*borrows"):
            self.initialize()
        self.assertFalse(self.origin.exists())
        alternate.unlink()
        with mock.patch.dict(os.environ, {"GIT_OBJECT_DIRECTORY": str(self.root / "foreign")}):
            with self.assertRaisesRegex(owner.OriginError, "overrides"):
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

    def test_inherited_filter_is_isolated_before_baseline_and_origin_mutation(self):
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
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            new_project = self.fixture(self.root / "new-scope")
            self.initialize()
        self.assertTrue((new_project / ".git").is_dir())
        self.assertTrue(self.origin.exists())
        self.assertFalse(sentinel.exists())

    def test_inherited_transport_is_isolated_and_environment_overrides_are_rejected(self):
        self.initialize()
        home = self.root / "transport-home"
        home.mkdir()
        (home / ".gitconfig").write_text('[uploadpack]\n packObjectsHook = false\n')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            owner.validate(self.project, self.root, self.owner_id)
        for variable in ("GIT_EXEC_PATH", "GIT_ALLOW_PROTOCOL", "GIT_SSH_COMMAND"):
            with self.subTest(variable=variable), mock.patch.dict(os.environ, {variable: "forbidden"}):
                with self.assertRaisesRegex(owner.OriginError, "overrides"):
                    owner.validate(self.project, self.root, self.owner_id)

    def test_disabled_fsmonitor_configuration_remains_safe(self):
        home = self.root / "disabled-home"
        home.mkdir()
        (home / ".gitconfig").write_text('[core]\n fsmonitor = false\n')
        self.git(self.project, "config", "core.fsmonitor", "false")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.initialize()
            owner.validate(self.project, self.root, self.owner_id)
            self.fixture(self.root / "fresh-scope")

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
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.fixture(self.root / "fresh-scope")
            self.initialize()
        config = self.project / ".git/config"
        original = config.read_bytes()
        self.git(self.project, "config", "core.hooksPath", str(hooks))
        with self.assertRaisesRegex(owner.OriginError, "configuration|hook"):
            owner.validate(self.project, self.root, self.owner_id)
        config.write_bytes(original)
        default_hooks = self.project / ".git" / "hooks"
        default_hooks.mkdir(exist_ok=True)
        (default_hooks / "reference-transaction").write_bytes(hook.read_bytes())
        (default_hooks / "reference-transaction").chmod(0o700)
        with self.assertRaisesRegex(owner.OriginError, "hook"):
            owner.validate(self.project, self.root, self.owner_id)
        self.assertFalse(sentinel.exists())

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
