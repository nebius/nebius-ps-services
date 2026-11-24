#!/usr/bin/env python3
"""Standalone filesystem publication must retain the paired transaction boundary."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from project_specs_lib import transaction
from project_specs_lib.contracts import (
    CONFIG_SCHEMA,
    ProjectSpecError,
    canonical_document,
    inspect_project,
    validate_project,
)
from project_specs_lib.targets import resolve_target
from test_project_specs_transaction import REQUIREMENTS, DESIGN


class FilesystemPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.base / "home")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.req = canonical_document("requirements", REQUIREMENTS)
        self.des = canonical_document("design", DESIGN)
        self.basis = resolve_target(self.project).basis

    def publish(self, operation="first", **kwargs):
        return transaction.publish_spec_pair(
            self.project,
            requirements_candidate=self.req,
            design_candidate=self.des,
            expected_filesystem_basis=self.basis,
            expected_requirements_sha256="absent",
            expected_design_sha256="absent",
            operation_id=operation,
            **kwargs,
        )

    def test_create_inspect_validate_replay_without_git(self):
        before = inspect_project(self.project)
        self.assertEqual(before["status"], "pending")
        self.assertEqual(before["requirements"]["sha256"], "absent")
        receipt = self.publish()
        self.assertEqual(
            receipt["schema"], "maintain-project-specs.filesystem-transaction.v1"
        )
        self.assertNotIn("git_head", receipt)
        self.assertEqual(self.publish(), receipt)
        self.assertEqual(validate_project(self.project)["status"], "current")
        self.assertFalse((self.project / ".git").exists())
        self.assertFalse((self.project / ".codex").exists())
        with self.assertRaises(ProjectSpecError):
            transaction.read_spec_publication(self.project, "first")

    def test_conflicting_predecessor_and_workflow_identity_rejected(self):
        self.publish()
        (self.project / "docs/requirements.md").write_text("human edit")
        with self.assertRaises(ProjectSpecError):
            self.publish()
        with self.assertRaises(ProjectSpecError):
            self.publish(expected_objective_sha256="a" * 64)

    def test_second_write_failure_rolls_back_and_replays(self):
        original = transaction._replace_at

        def replace(fd, name, data, before):
            if name == "design.md":
                raise OSError("injected failure")
            return original(fd, name, data, before)

        with mock.patch.object(transaction, "_replace_at", side_effect=replace):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse((self.project / "docs/requirements.md").exists())
        self.assertFalse((self.project / "docs/design.md").exists())
        self.assertEqual(self.publish()["contract_status"], "current")

    def test_policy_changes_and_disabled_policy(self):
        config = self.project / ".codex"
        config.mkdir()
        path = config / "project-specs.json"
        path.write_text(
            json.dumps({"schema": CONFIG_SCHEMA, "scope": ".", "mode": "disabled"})
        )
        self.assertEqual(inspect_project(self.project)["status"], "disabled")
        with self.assertRaises(ProjectSpecError):
            self.publish()
        self.basis = resolve_target(self.project).basis
        with self.assertRaises(ProjectSpecError) as error:
            self.publish()
        self.assertEqual(error.exception.code, "PROJECT_SPECS_DISABLED")

    def test_no_filesystem_fallback_for_broken_unborn_or_unavailable_git(self):
        with mock.patch(
            "project_specs_lib.targets.subprocess.run", side_effect=FileNotFoundError
        ):
            with self.assertRaises(ProjectSpecError):
                resolve_target(self.project)
        (self.project / ".git").write_text("broken")
        with self.assertRaises(ProjectSpecError):
            resolve_target(self.project)
        (self.project / ".git").unlink()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        with self.assertRaises(ProjectSpecError):
            resolve_target(self.project)
        with self.assertRaises(ProjectSpecError):
            self.publish()

    def test_git_creation_during_publication_rolls_back(self):
        original = transaction._replace_at

        def replace(fd, name, data, before):
            result = original(fd, name, data, before)
            if name == "requirements.md":
                subprocess.run(["git", "init", "-q", str(self.project)], check=True)
            return result

        with mock.patch.object(transaction, "_replace_at", side_effect=replace):
            with self.assertRaises(ProjectSpecError):
                self.publish()
        self.assertFalse((self.project / "docs/requirements.md").exists())

    def test_symlink_target_and_docs_rejected(self):
        link = self.base / "link"
        link.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(ProjectSpecError):
            resolve_target(link)
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ProjectSpecError):
            self.publish()
        self.assertEqual(list(outside.iterdir()), [])

    def test_lock_identity_is_mode_independent(self):
        workspace = self.base / "workspace"
        nested = workspace / "nested"
        nested.mkdir(parents=True)
        plain = transaction._lock_root(nested, nested)
        self.assertEqual(plain, transaction._lock_root(nested, workspace))

    def test_root_replacement_invalidates_basis(self):
        self.project.rename(self.base / "old")
        self.project.mkdir()
        with self.assertRaises(ProjectSpecError):
            self.publish()


if __name__ == "__main__":
    unittest.main()
