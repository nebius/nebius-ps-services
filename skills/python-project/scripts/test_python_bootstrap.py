"""Readiness failure/isolation contracts and opt-in real uv bootstrap evidence."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_python_project import ROOT, P, S, fixture, load

B = load("python_bootstrap_test", ROOT / "python-project/scripts/python_bootstrap.py")


class BootstrapTests(unittest.TestCase):
    def applied(self, directory, request=None):
        target, bundle, draft = fixture(directory, request or P.default_request())
        result = S.finalize_bundle(target, bundle)
        S.apply_bundle(target, bundle, result["bundle_digest"])
        return target, bundle, draft, result

    def test_failure_receipt_and_resume_without_static_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, draft, result = self.applied(directory)
            before = {
                op["path"]: (target / op["path"]).stat().st_mtime_ns
                for op in draft["operations"]
            }

            def fake_run(argv, project, env):
                if argv == ["uv", "lock"]:
                    (project / "uv.lock").write_text("fixture\n")
                if argv[:3] == ["uv", "sync", "--locked"]:
                    raise ValueError("resolver fixture failure")
                return "fixture-version"

            with (
                mock.patch.object(B, "run", side_effect=fake_run),
                self.assertRaisesRegex(ValueError, "not ready"),
            ):
                B.bootstrap(target, bundle, "python-main")
            receipt = json.loads((bundle / "readiness-python-main.json").read_text())
            self.assertEqual(receipt["status"], "applied-not-ready")
            with mock.patch.object(B, "run", return_value="fixture-version") as runner:
                B.bootstrap(target, bundle, "python-main")
            self.assertNotIn(
                ["uv", "lock"], [call.args[0] for call in runner.call_args_list]
            )
            self.assertEqual(
                before,
                {
                    op["path"]: (target / op["path"]).stat().st_mtime_ns
                    for op in draft["operations"]
                },
            )
            self.assertEqual(
                json.loads((bundle / "readiness-python-main.json").read_text())[
                    "status"
                ],
                "ready",
            )
            S.apply_bundle(target, bundle, result["bundle_digest"])

    def test_environment_and_symlink_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _, _ = self.applied(directory)
            with mock.patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": "/unrelated/env",
                    "UV_PROJECT_ENVIRONMENT": "/unrelated/project",
                    "UV_WORKING_DIRECTORY": "/unrelated",
                    "UV_WORKING_DIR": "/unrelated",
                },
            ):
                env = B.environment(target)
                self.assertFalse("VIRTUAL_ENV" in env)
                self.assertFalse("UV_WORKING_DIRECTORY" in env)
                self.assertFalse("UV_WORKING_DIR" in env)
                self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], str(target / ".venv"))
            foreign = Path(directory) / "foreign"
            foreign.mkdir()
            (target / ".venv").symlink_to(foreign, target_is_directory=True)
            with mock.patch.object(B, "run") as runner, self.assertRaises(ValueError):
                B.bootstrap(target, bundle, "python-main")
            runner.assert_not_called()
            self.assertEqual(list(foreign.iterdir()), [])
            alias = Path(directory).resolve() / "target-alias"
            alias.symlink_to(target, target_is_directory=True)
            with mock.patch.object(B, "run") as runner, self.assertRaises(ValueError):
                B.bootstrap(alias, bundle, "python-main")
            runner.assert_not_called()

    def test_parent_workspace_rejected_before_uv_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _, _ = self.applied(
                directory, P.default_request(root="apps/api")
            )
            parent_metadata = target / "pyproject.toml"
            parent_metadata.write_text('[tool.uv.workspace]\nmembers = ["apps/*"]\n')
            parent_lock = target / "uv.lock"
            parent_lock.write_text("existing-parent-lock\n")
            before = (parent_metadata.read_bytes(), parent_lock.read_bytes())
            with (
                mock.patch.object(B, "run", return_value="fixture-version") as runner,
                self.assertRaisesRegex(ValueError, "parent uv workspace"),
            ):
                B.bootstrap(target, bundle, "python-main")
            runner.assert_not_called()
            self.assertEqual(
                before, (parent_metadata.read_bytes(), parent_lock.read_bytes())
            )
            receipt = json.loads((bundle / "readiness-python-main.json").read_text())
            self.assertEqual(receipt["status"], "applied-not-ready")

    def test_preflight_failure_invalidates_prior_ready_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _, _ = self.applied(directory)
            (target / "uv.lock").write_text("fixture\n")
            with mock.patch.object(B, "run", return_value="fixture-version"):
                B.bootstrap(target, bundle, "python-main")
            (target / "pyproject.toml").write_text("drift\n")
            with mock.patch.object(B, "run") as runner, self.assertRaises(ValueError):
                B.bootstrap(target, bundle, "python-main")
            runner.assert_not_called()
            receipt = json.loads((bundle / "readiness-python-main.json").read_text())
            self.assertEqual(receipt["status"], "applied-not-ready")

    def test_setup_serializes_with_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _, _ = self.applied(directory)
            with (
                S.target_lock(target),
                mock.patch.object(B, "run") as runner,
                self.assertRaisesRegex(ValueError, "another scaffold"),
            ):
                B.bootstrap(target, bundle, "python-main")
            runner.assert_not_called()

    def test_generated_outputs_cannot_follow_foreign_links(self):
        for relative, hardlink in [
            ("src/example_project/_version.py", False),
            ("src/example_project/_version.py", True),
            ("src/example_project.egg-info/PKG-INFO", False),
            ("build/lib/example_project/_version.py", False),
            (".pytest_cache/v/cache/nodeids", False),
        ]:
            with (
                self.subTest(relative=relative, hardlink=hardlink),
                tempfile.TemporaryDirectory() as directory,
            ):
                target, bundle, _, _ = self.applied(directory)
                foreign = Path(directory).resolve() / "foreign"
                foreign.write_text("preserve\n")
                link = target / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                if hardlink:
                    os.link(foreign, link)
                else:
                    link.symlink_to(foreign)
                with (
                    mock.patch.object(B, "run") as runner,
                    self.assertRaises(ValueError),
                ):
                    B.bootstrap(target, bundle, "python-main")
                runner.assert_not_called()
                self.assertEqual(foreign.read_text(), "preserve\n")

    @unittest.skipUnless(
        os.environ.get("RUN_PYTHON_BOOTSTRAP_TESTS") == "1",
        "real uv environment lane not selected",
    )
    def test_real_core_profiles(self):
        for root, kind, profiles in [
            (".", "library", ()),
            (".", "application", ("cli",)),
            ("apps/api", "application", ("cli", "api", "systemd")),
        ]:
            with (
                self.subTest(root=root, kind=kind, profiles=profiles),
                tempfile.TemporaryDirectory(prefix="python-ready-") as directory,
            ):
                request = P.default_request(root=root, kind=kind, profiles=profiles)
                target, bundle, draft, result = self.applied(directory, request)
                before = {
                    op["path"]: (target / op["path"]).read_bytes()
                    for op in draft["operations"]
                }
                ready = B.bootstrap(target, bundle, "python-main")
                self.assertEqual(ready["status"], "ready")
                lock = target / root / "uv.lock"
                lock_state = (lock.read_bytes(), lock.stat().st_mtime_ns)
                B.bootstrap(target, bundle, "python-main")
                self.assertEqual(
                    lock_state, (lock.read_bytes(), lock.stat().st_mtime_ns)
                )
                S.apply_bundle(target, bundle, result["bundle_digest"])
                self.assertEqual(
                    before,
                    {
                        op["path"]: (target / op["path"]).read_bytes()
                        for op in draft["operations"]
                    },
                )


if __name__ == "__main__":
    unittest.main()
