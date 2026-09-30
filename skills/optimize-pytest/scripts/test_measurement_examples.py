"""Execute documented diagnostics against disposable suites, never real projects."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def block(reference: str, heading: str) -> str:
    source = (ROOT / "references" / reference).read_text()
    section = source.split(heading, 1)[1]
    return section.split("```bash\n", 1)[1].split("```", 1)[0]


class MeasurementExamples(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pytest-doc-example-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.perf = self.root / "artifacts"
        self.perf.mkdir()
        self.init = block(
            "safe-measurement.md", "### Effective Arguments Before Every Diagnostic"
        )
        self.init += "\npytest_base_args+=(-p socket -p pytest_cov -p xdist.plugin)\n"
        self.init += f"pytest_python={shlex.quote(sys.executable)}\n"
        self.init += f"perf_dir={shlex.quote(str(self.perf))}\npytest_target=tests/test_selected.py\n"
        (self.project / "tests").mkdir()
        (self.project / "sample_pkg.py").write_text("def value():\n    return 42\n")
        (self.project / "pyproject.toml").write_text("""
[tool.pytest.ini_options]
addopts = "--cov=sample_pkg --cov-report=xml:unwanted.xml -n 2"
[tool.coverage.run]
source = ["sample_pkg"]
""")
        (self.project / "tests/test_selected.py").write_text("""
import socket
import pytest
from pytest_socket import SocketBlockedError

def test_selected(pytestconfig):
    assert pytestconfig.getoption('numprocesses') in (None, 0)
    with pytest.raises(SocketBlockedError): socket.socket()
""")
        # An unselected module must never be collected, not just deselected.
        (self.project / "tests/test_unrelated.py").write_text(
            'raise RuntimeError("selection expanded")\n'
        )

    def execute(self, commands: str):
        env = {
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "--cov=sample_pkg --junitxml=unwanted.xml",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            ["bash", "-euc", self.init + commands],
            cwd=self.project,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_serial_baseline_removes_instrumentation_but_keeps_guard(self) -> None:
        result = self.execute(
            block("safe-measurement.md", "### Serial Wall-Time Baseline")
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 passed", result.stdout)
        self.assertNotIn("workers", result.stdout)
        self.assertFalse((self.project / ".coverage").exists())
        self.assertFalse((self.project / "unwanted.xml").exists())

    def test_failed_feedback_never_expands_empty_or_successful_cache(self) -> None:
        example = block("scaling-and-ci.md", "## Targeted And Affected-Test Feedback")
        lines = example.replace("\\\n", "").splitlines()
        rerun = next(line for line in lines if " --lf " in line)
        prefix = 'feedback_cache_dir="${perf_dir}/feedback-cache"\n'
        for prepare in (
            "",
            '"${pytest_python}" -m pytest "${pytest_base_args[@]}" "${pytest_target}" -o "cache_dir=${feedback_cache_dir}"\n',
        ):
            with self.subTest(prepare=bool(prepare)):
                result = self.execute(prefix + prepare + rerun)
                self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                self.assertIn("1 deselected", result.stdout)
                self.assertNotIn("selection expanded", result.stdout + result.stderr)

    def test_failed_feedback_reruns_selected_failure(self) -> None:
        test = self.project / "tests/test_selected.py"
        test.write_text("def test_selected(): assert False\n")
        example = block("scaling-and-ci.md", "## Targeted And Affected-Test Feedback")
        rerun = next(
            line
            for line in example.replace("\\\n", "").splitlines()
            if " --lf " in line
        )
        prefix = 'feedback_cache_dir="${perf_dir}/feedback-cache"\n'
        initial = self.execute(
            prefix
            + '"${pytest_python}" -m pytest "${pytest_base_args[@]}" "${pytest_target}" -o "cache_dir=${feedback_cache_dir}"'
        )
        self.assertEqual(initial.returncode, 1)
        result = self.execute(prefix + rerun)
        self.assertEqual(result.returncode, 1)
        self.assertIn("1 failed", result.stdout)
        self.assertNotIn("selection expanded", result.stdout + result.stderr)

    def test_coverage_example_preserves_existing_repository_artifacts(self) -> None:
        sentinel = self.project / ".coverage"
        sentinel.write_bytes(b"preserve-user-coverage")
        comparison = block("safe-measurement.md", "### Coverage Comparison").replace(
            "pytest_package=your_package", "pytest_package=sample_pkg"
        )
        result = self.execute(comparison)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"preserve-user-coverage")
        self.assertTrue((self.perf / "coverage-data").is_file())
        self.assertFalse((self.project / "unwanted.xml").exists())

    def test_testmon_feedback_intersects_changes_with_safety_selection(self) -> None:
        self.init += "\npytest_base_args+=(-p testmon.pytest_testmon)\n"
        (self.project / "tests/test_selected.py").write_text("""
import socket
import pytest
from pytest_socket import SocketBlockedError
import sample_pkg

def test_changed():
    assert sample_pkg.value() > 0
    with pytest.raises(SocketBlockedError): socket.socket()

def test_independent():
    assert 2 + 2 == 4

@pytest.mark.external
def test_external():
    raise AssertionError("external selection escaped")
""")
        config = self.project / "pyproject.toml"
        config.write_text(
            config.read_text().replace(
                "[tool.pytest.ini_options]\n",
                '[tool.pytest.ini_options]\nmarkers = ["external"]\n',
            )
        )
        example = block("scaling-and-ci.md", "### Pytest-Testmon")
        first, feedback = example.split("# After a relevant source change", 1)
        feedback = "# After a relevant source change" + feedback
        initial = self.execute(first)
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        self.assertIn("2 passed", initial.stdout)
        self.assertIn("1 deselected", initial.stdout)
        (self.project / "sample_pkg.py").write_text("def value():\n    return 43\n")
        result = self.execute('testmon_datafile="${perf_dir}/testmondata"\n' + feedback)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 passed", result.stdout)
        self.assertIn("2 deselected", result.stdout)
        self.assertNotIn("selection escaped", result.stdout + result.stderr)
        self.assertFalse((self.project / ".testmondata").exists())

    def test_command_blocks_parse_as_bash(self) -> None:
        for name in ("safe-measurement.md", "scaling-and-ci.md"):
            source = (ROOT / "references" / name).read_text()
            for number, code in enumerate(
                re.findall(r"```bash\n(.*?)```", source, re.S)
            ):
                with self.subTest(reference=name, block=number):
                    result = subprocess.run(
                        ["bash", "-n"],
                        input=code,
                        text=True,
                        capture_output=True,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
