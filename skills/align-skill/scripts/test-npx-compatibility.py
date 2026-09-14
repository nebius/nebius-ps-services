#!/usr/bin/env python3
"""Offline regressions for the npx verification harness, never model evidence."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("npx_check", Path(__file__).with_name("check-npx-compatibility.py"))
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "sample"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: Test skill\n---\nDo work.\n")
        (self.source / "scripts").mkdir()
        (self.source / "scripts/run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.source / "scripts/run.sh").chmod(0o755)
        self.installs = 0

    def fake_cli(self, command, cwd, env, timeout=120):
        self.assertEqual(env["DISABLE_TELEMETRY"], "1")
        self.assertNotEqual(env["HOME"], os.environ.get("HOME"))
        self.assertNotIn("TEST_SECRET", env)
        if "--version" in command:
            return 0, checker.SKILLS_VERSION
        if "--list" in command:
            return 0, f"│    {self.source.name}\n│      Test skill\n"
        self.installs += 1
        for relative in checker.AGENTS.values():
            destination = cwd / relative / self.source.name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(self.source, destination)
        return 0, "installed"

    def verify(self, runner=None):
        with patch.object(checker.shutil, "which", return_value="/fixture/npx"), \
                patch.object(checker, "run", side_effect=runner or self.fake_cli), \
                patch.dict(os.environ, {"TEST_SECRET": "do-not-forward"}):
            return checker.check([self.source])

    def test_discovery_copy_and_repeat_are_required(self):
        result = self.verify()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["runtime"], "NOT_RUN")
        self.assertEqual(self.installs, 2)

    def test_skill_name_cannot_collide_with_unrelated_sentinel(self):
        self.source = self.source.rename(self.source.with_name("unrelated-sentinel"))
        path = self.source / "SKILL.md"
        path.write_text(path.read_text().replace("name: sample", "name: unrelated-sentinel"))
        self.assertEqual(self.verify()["status"], "PASS")

    def test_upstream_excluded_required_file_is_a_failure(self):
        (self.source / "metadata.json").write_text('{"required": true}')
        def runner(command, cwd, env, timeout=120):
            result = self.fake_cli(command, cwd, env, timeout)
            if "--copy" in command:
                for relative in checker.AGENTS.values():
                    (cwd / relative / "sample/metadata.json").unlink()
            return result
        with self.assertRaisesRegex(checker.CheckError, "installed payload differs"):
            self.verify(runner)

    def test_mode_loss_or_second_install_change_fails(self):
        for defect in ("mode", "repeat"):
            self.installs = 0
            def runner(command, cwd, env, timeout=120):
                result = self.fake_cli(command, cwd, env, timeout)
                if "--copy" in command:
                    path = cwd / ".claude/skills/sample/scripts/run.sh"
                    if defect == "mode":
                        path.chmod(0o644)
                    elif self.installs == 2:
                        path.write_text("changed")
                return result
            with self.subTest(defect=defect), self.assertRaises(checker.CheckError):
                self.verify(runner)

    def test_hooks_and_unrelated_files_cannot_change(self):
        for relative in (".claude/settings.json", ".codex/hooks.json", "unrelated.txt"):
            def runner(command, cwd, env, timeout=120):
                result = self.fake_cli(command, cwd, env, timeout)
                if "--copy" in command:
                    (cwd / relative).write_text("changed")
                return result
            with self.subTest(path=relative), self.assertRaises(checker.CheckError):
                self.verify(runner)

    def test_discovery_mismatch_and_list_mutation_fail(self):
        for defect in ("missing", "write"):
            def runner(command, cwd, env, timeout=120):
                result = self.fake_cli(command, cwd, env, timeout)
                if "--list" in command:
                    if defect == "missing":
                        return 0, "│    other\n"
                    (cwd / "unexpected").write_text("changed")
                return result
            with self.subTest(defect=defect), self.assertRaises(checker.CheckError):
                self.verify(runner)

    def test_name_normalization_and_duplicates_fail_before_install(self):
        with patch.object(checker.shutil, "which", return_value="/fixture/npx"):
            with self.assertRaisesRegex(checker.CheckError, "duplicate"):
                checker.check([self.source, self.source])
        path = self.source / "SKILL.md"
        path.write_text(path.read_text().replace("name: sample", "name: café"))
        with self.assertRaisesRegex(checker.CheckError, "normalization"):
            checker.sources(self.source)

    def test_unsafe_payloads_fail_without_following_links(self):
        (self.source / "outside").symlink_to(Path(self.temp.name))
        with self.assertRaisesRegex(checker.CheckError, "symlink"):
            checker.payload(self.source)

    def test_missing_toolchain_is_unavailable(self):
        with patch.object(checker.shutil, "which", return_value=None):
            with self.assertRaises(checker.Unavailable):
                checker.check([self.source])
        with self.assertRaises(checker.Unavailable):
            self.verify(lambda *args: (1, "private diagnostic must not escape"))

    def test_timeout_stops_process(self):
        with self.assertRaises(checker.Unavailable):
            checker.run([sys.executable, "-c", "import time; time.sleep(10)"],
                        self.source, dict(os.environ), timeout=0.05)

    def test_standalone_copied_validator_has_its_dependencies(self):
        scripts = Path(__file__).parent
        copied = Path(self.temp.name) / "isolated-validator"
        copied.mkdir()
        for name in ("validate-skill-structure.py", "skill_frontmatter.py", "skill_resources.py", "requirements.txt"):
            shutil.copy2(scripts / name, copied / name)
        result = subprocess.run([sys.executable, "-B", str(copied / "validate-skill-structure.py"),
                                 "--policy", "agentskills", str(self.source)],
                                cwd=self.temp.name, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
