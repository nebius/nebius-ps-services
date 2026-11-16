#!/usr/bin/env python3

import os
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from verify_implement import (
    OBSERVATION_INTERVAL_SECONDS,
    HARNESS_TEST_SCRIPTS,
    TEST_SCRIPTS,
    contract,
    dependency_parity,
    main,
    scheduler,
    verify,
)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        owner = scheduler()
        memory = owner.source_module(
            "host_memory",
            Path(owner.__file__).resolve().parents[2]
            / "global-context-management/scripts/host_memory.py",
        )
        # These fixture suites test verifier results, not the execution host.
        probe = patch.object(memory, "sample", return_value={"status": "normal"})
        self.memory_probe = probe.start()
        self.addCleanup(probe.stop)

    def test_unavailable_or_critical_memory_launches_no_fixture_tests(self):
        for status in ("unknown", "critical"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                source = self.make_skill(Path(temp))
                self.memory_probe.return_value = {"status": status}
                with patch.object(scheduler(), "_run") as run:
                    result = verify(source, source, 10)
                run.assert_not_called()
                self.assertEqual(result["deterministic"], "PARTIAL")
                self.assertTrue(all(
                    row["status"] == "NOT_RUN" for row in result["test_groups"]
                ))

    def test_completed_inventory_survives_later_verifier_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.md"
            row = {"id": "complete", "status": "PASS", "inventory": ["fixture.Case.test_ok"],
                   "outcomes": {"fixture.Case.test_ok": "passed"}}
            def failed(*args, **kwargs):
                kwargs["on_result"](row)
                raise RuntimeError("later verifier failure")
            with (patch.object(sys, "argv", ["verify_implement.py", "--report", str(report)]),
                  patch("implement_catalog.snapshot", return_value={"files": {}}),
                  patch("verify_implement.verify", side_effect=failed), redirect_stdout(StringIO())):
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(report.with_suffix(".inventory.json").read_text())["groups"], [row])

    def test_default_observation_interval_does_not_expire_healthy_suites(self) -> None:
        self.assertEqual(OBSERVATION_INTERVAL_SECONDS, 30)

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
            "$implement status [project-folder]\n"
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
                f"import unittest\nclass Fixture(unittest.TestCase):\n def test_result(self): self.assertEqual({exit_code}, 0)\n", encoding="utf-8"
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

    def test_readiness_suite_failure_blocks_deterministic_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = self.make_skill(Path(temp))
            suite = "worktree/scripts/test_workflow_readiness.py"
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
            # Empty discovered modules cannot produce a successful exact inventory.
            result = verify(source, source, 10, harness)
            self.assertEqual(result["deterministic"], "FAIL")

    def test_prerequisite_failure_launches_no_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self.make_skill(Path(temp))
            (source / "agents/openai.yaml").write_text("policy:\n  allow_implicit_invocation: true\n")
            with patch("verify_implement.run_profile") as run:
                result = verify(source, source, 10)
            run.assert_not_called()
            self.assertEqual(result["deterministic"], "FAIL")
            self.assertTrue(any(row["status"] == "NOT_RUN" for row in result["stages"]))

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
                    "verify_implement.run_profile",
                    return_value=([harness_pass], []),
                ),
                redirect_stdout(StringIO()),
                patch("implement_catalog.snapshot", return_value={"schema": "test", "files": {}}),
                patch("implement_catalog.validate_record"),
                patch("implement_catalog.publish_preflight"),
            ):
                self.assertEqual(main(), 0)
            private = root / "codex-home" / "implement-test"
            self.assertTrue((private / "owner.json").is_file())
            self.assertTrue((private / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()
