"""Exercise qualification accounting including real fail-fast pytest output."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).parents[1] / "systemd"
SPEC = importlib.util.spec_from_file_location("qualification", HARNESS / "qualification.py")
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


@pytest.mark.parametrize("selection,count", [("all", 20), ("ha", 10), ("ordinary", 10)])
def test_package_matrix_preserves_all_predecessors_modes_and_interruptions(selection, count):
    cases = qualification.package_cases(["0.6.0", "0.6.1", "0.6.2", "0.6.3"], "0.6.3", selection)
    assert len(cases) == len(set(cases)) == count
    assert sum("interrupt" in case for case in cases) == count // 5
    if selection != "all":
        assert all(f"-{selection}" in case for case in cases)
    assert len(qualification.package_cases(["0.6.0"], "0.6.3", selection)) == count // 5


def test_ha_native_selection_keeps_shared_coverage_and_names_ordinary_omissions():
    ha = qualification.native_files("ha")
    ordinary = qualification.native_files("ordinary")
    all_files = qualification.native_files("all")
    assert set(ha) & set(ordinary) == set(qualification.SHARED)
    assert set(ha) | set(ordinary) == set(all_files)
    assert set(all_files) - set(ha) == set(qualification.ORDINARY)
    with pytest.raises(ValueError):
        qualification.native_files("typo")


@pytest.mark.parametrize(
    "body,exit_code,status,unrun",
    [
        ("def test_a(): pass\ndef test_b(): pass\n", 0, "passed", 0),
        ("def test_a(): assert False\ndef test_b(): pass\n", 1, "incomplete", 1),
        ("import pytest\ndef test_a(): pytest.skip('unavailable')\n", 0, "incomplete", 0),
    ],
)
@pytest.mark.parametrize("driver", ["restart_driver", "owner_driver"])
def test_native_report_accounts_for_real_pass_failure_skip_and_unrun(
    tmp_path, body, exit_code, status, unrun, driver
):
    test = tmp_path / "test_fixture.py"
    timeline = "\n".join(json.dumps({"restart_step": {"sequence": i}}) for i in range(500))
    body = body.replace(
        "def test_a():",
        f"def test_a(record_property): "
        f"record_property('{driver}.stderr', {timeline!r}); "
        f"record_property('{driver}.stdout', 'final observation'); "
        "record_property('unrelated_property', 'not exported');",
    )
    test.write_text(body)
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            "-p",
            "qualification_plugin",
            str(test),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(HARNESS),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "VPNGW_NATIVE_REPORT": str(report),
            "VPNGW_NATIVE_TOPOLOGY": "ha",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    saved = json.loads(report.read_text())
    assert saved["status"] == status
    assert saved["topology"] == "ha"
    assert saved["expected_cases"] == len(saved["expected_nodeids"])
    assert len(saved["unrun_nodeids"]) == unrun
    first_case = saved["expected_nodeids"][0]
    assert saved[driver + "_output"] == {
        first_case: {"stdout": "final observation", "stderr": timeline}
    }
