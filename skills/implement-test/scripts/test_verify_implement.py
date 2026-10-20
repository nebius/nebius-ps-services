#!/usr/bin/env python3

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from verify_implement import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    HARNESS_TEST_SCRIPTS,
    TEST_SCRIPTS,
    contract,
    dependency_parity,
    main,
    run_harness_tests,
    verify,
)


class VerifyTests(unittest.TestCase):
    def test_default_suite_timeout_covers_full_worktree_regression(self) -> None:
        self.assertEqual(DEFAULT_SUITE_TIMEOUT_SECONDS, 900)

    def make_skill(self, root: Path, failing: str | None = None) -> Path:
        skill = root / "implement"
        (skill / "agents").mkdir(parents=True)
        (skill / "scripts").mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: implement\n"
            'description: "test"\n'
            "---\n\n"
            "$implement\n"
            "$implement <implementation request>\n"
            "$implement continue [project-folder]\n"
            "$implement -h\n"
            "$implement --help\n",
            encoding="utf-8",
        )
        (skill / "agents" / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )
        for name in TEST_SCRIPTS:
            exit_code = 1 if name == failing else 0
            (skill.parent / name).parent.mkdir(parents=True, exist_ok=True)
            (skill.parent / name).write_text(
                f"raise SystemExit({exit_code})\n", encoding="utf-8"
            )
        return skill

    def test_passes_deterministic_profile_with_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_skill(root / "source")
            installed = root / "installed"
            shutil.copytree(source, installed)
            result = verify(source, installed, 10)
            self.assertEqual(result["deterministic"], "PASS")
            self.assertEqual(result["live"], "NOT_RUN")
            self.assertEqual(result["overall"], "PARTIAL")

    def test_failing_suite_forces_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_skill(root / "source", TEST_SCRIPTS[0])
            installed = root / "installed"
            shutil.copytree(source, installed)
            result = verify(source, installed, 10)
            self.assertEqual(result["deterministic"], "FAIL")
            self.assertEqual(result["overall"], "FAIL")

    def test_security_suite_failure_blocks_deterministic_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = self.make_skill(Path(temp))
            suite = "worktree/scripts/test_workflow_security.py"
            (source.parent / suite).write_text("raise SystemExit(1)\n", encoding="utf-8")
            result = verify(source, source, 10)
            self.assertEqual(result["deterministic"], "FAIL")
            self.assertEqual(
                [row["status"] for row in result["stages"] if row["name"] == suite],
                ["FAIL"],
            )

    def test_missing_installed_copy_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_skill(root / "source")
            result = verify(source, root / "missing", 10)
            self.assertEqual(result["deterministic"], "PARTIAL")

    def test_project_agent_instructions_parity_is_required_when_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_skill(root / "source")
            installed = root / "installed"
            shutil.copytree(source, installed)
            dependency = root / "worktree"
            dependency.mkdir()
            (dependency / "SKILL.md").write_text("source\n", encoding="utf-8")
            missing = root / "missing-dependency"
            result = verify(
                source,
                installed,
                10,
                dependency_source=dependency,
                dependency_installed=missing,
            )
            self.assertEqual(result["deterministic"], "PARTIAL")
            shutil.copytree(dependency, missing)
            self.assertEqual(dependency_parity(dependency, missing)[0], "PASS")

    def test_contract_rejects_wrong_name_and_extra_public_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = self.make_skill(Path(temp))
            skill = source / "SKILL.md"
            original = skill.read_text(encoding="utf-8")
            skill.write_text(
                original.replace("name: implement", "name: other"),
                encoding="utf-8",
            )
            self.assertEqual(contract(source)[0], "FAIL")
            skill.write_text(original + "$implement cleanup\n", encoding="utf-8")
            self.assertEqual(contract(source)[0], "FAIL")

    def test_contract_rejects_commented_duplicate_or_true_implicit_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = self.make_skill(Path(temp))
            metadata = source / "agents" / "openai.yaml"
            metadata.write_text(
                "# allow_implicit_invocation: false\n"
                "policy:\n  allow_implicit_invocation: true\n",
                encoding="utf-8",
            )
            self.assertEqual(contract(source)[0], "FAIL")
            metadata.write_text(
                "policy:\n"
                "  allow_implicit_invocation: false\n"
                "  allow_implicit_invocation: false\n",
                encoding="utf-8",
            )
            self.assertEqual(contract(source)[0], "FAIL")
            metadata.write_text(
                "interface:\n"
                "  allow_implicit_invocation: false\n"
                "policy:\n"
                "  unrelated: false\n",
                encoding="utf-8",
            )
            self.assertEqual(contract(source)[0], "FAIL")

    def test_harness_suite_failure_blocks_deterministic_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_skill(root / "source")
            harness = root / "implement-test"
            scripts = harness / "scripts"
            scripts.mkdir(parents=True)
            for name in HARNESS_TEST_SCRIPTS:
                (scripts / name).write_text("", encoding="utf-8")
            with patch("verify_implement.subprocess.run") as run:
                run.return_value.returncode = 1
                check = run_harness_tests(harness, 10)
            self.assertEqual(check["status"], "FAIL")
            with patch("verify_implement.run_harness_tests", return_value=check):
                result = verify(source, source, 10, harness)
            self.assertEqual(result["deterministic"], "FAIL")

    def test_default_report_initializes_owned_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = self.make_skill(root / "source")
            installed = root / "installed"
            shutil.copytree(source, installed)
            dependency_source = root / "dependency-source"
            dependency_source.mkdir()
            (dependency_source / "SKILL.md").write_text(
                "dependency\n",
                encoding="utf-8",
            )
            dependency_installed = root / "dependency-installed"
            shutil.copytree(dependency_source, dependency_installed)
            arguments = [
                "verify_implement.py",
                "--source-root",
                str(source),
                "--installed-root",
                str(installed),
                "--worktree-source-root",
                str(dependency_source),
                "--worktree-installed-root",
                str(dependency_installed),
            ]
            harness_pass = {
                "name": "implement-test helper suites",
                "status": "PASS",
                "detail": "helper tests passed",
            }
            with (
                patch.dict(os.environ, {"CODEX_HOME": str(root / "codex-home")}),
                patch.object(sys, "argv", arguments),
                patch(
                    "verify_implement.run_harness_tests",
                    return_value=harness_pass,
                ),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(), 0)
            private = root / "codex-home" / "implement-test"
            self.assertTrue((private / "owner.json").is_file())
            self.assertTrue((private / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()
