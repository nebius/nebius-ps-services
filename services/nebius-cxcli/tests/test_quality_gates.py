from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _git(project_root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=project_root, check=True, capture_output=True, text=True)


def _git_init_with_baseline(project_root: Path, relative_path: str, payload: object) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "tests@example.invalid")
    _git(project_root, "config", "user.name", "Tests")
    _git(project_root, "add", relative_path)
    _git(project_root, "commit", "-qm", "baseline")
    return path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_coverage_gate_uses_global_baseline(tmp_path: Path) -> None:
    project_root = _project_root()
    baseline = json.loads(
        (project_root / "scripts" / "coverage-baseline.json").read_text(encoding="utf-8")
    )
    baseline["global_combined_percent"] = 99.0
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    files = {
        path: {"summary": {"percent_covered": 100.0}}
        for path in baseline["critical_combined_percent"]
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(
        json.dumps({"totals": {"percent_covered": 98.0}, "files": files}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(project_root / ".venv" / "bin" / "python"),
            str(project_root / "scripts" / "check_coverage.py"),
            str(report_path),
            "--baseline",
            str(baseline_path),
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "global coverage 98.00% is below 99.00%" in result.stderr


def test_diff_gate_compares_ci_base_to_head() -> None:
    makefile = (_project_root() / "Makefile").read_text(encoding="utf-8")

    assert "DIFF_BASE" in makefile
    assert 'git diff --check "$${DIFF_BASE}...HEAD"' in makefile


@pytest.mark.parametrize(
    ("field", "weakened"),
    [
        ("mypy_max_errors", 11),
        ("ruff_format_unformatted_files", ["src/a.py", "src/b.py"]),
    ],
)
def test_quality_baseline_rejects_same_change_weakening(
    tmp_path: Path,
    field: str,
    weakened: object,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    baseline = {
        "mypy_max_errors": 10,
        "ruff_format_unformatted_files": ["src/a.py"],
        "schema": "nebius-cxcli.quality-ratchets.v1",
    }
    baseline_path = _git_init_with_baseline(project_root, "scripts/quality-baseline.json", baseline)
    baseline[field] = weakened
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_quality_ratchets.py"),
            "baseline",
            "--project-root",
            str(project_root),
            "--baseline",
            str(baseline_path),
            "--base-ref",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "quality baseline regression" in result.stderr


def test_quality_baseline_accepts_stricter_change(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    baseline = {
        "mypy_max_errors": 10,
        "ruff_format_unformatted_files": ["src/a.py", "src/b.py"],
        "schema": "nebius-cxcli.quality-ratchets.v1",
    }
    baseline_path = _git_init_with_baseline(project_root, "scripts/quality-baseline.json", baseline)
    baseline["mypy_max_errors"] = 9
    baseline["ruff_format_unformatted_files"] = ["src/a.py"]
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_quality_ratchets.py"),
            "baseline",
            "--project-root",
            str(project_root),
            "--baseline",
            str(baseline_path),
            "--base-ref",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "direction passed" in result.stdout


def test_quality_baseline_allows_proven_initial_adoption(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "tests@example.invalid")
    _git(project_root, "config", "user.name", "Tests")
    marker = project_root / "marker.txt"
    marker.write_text("initial\n", encoding="utf-8")
    _git(project_root, "add", "marker.txt")
    _git(project_root, "commit", "-qm", "initial")
    baseline_path = project_root / "scripts" / "quality-baseline.json"
    baseline_path.parent.mkdir()
    baseline_path.write_text(
        json.dumps(
            {
                "mypy_max_errors": 10,
                "ruff_format_unformatted_files": [],
                "schema": "nebius-cxcli.quality-ratchets.v1",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_quality_ratchets.py"),
            "baseline",
            "--project-root",
            str(project_root),
            "--baseline",
            str(baseline_path),
            "--base-ref",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "initial adoption accepted" in result.stdout


def test_quality_baseline_rejects_unresolvable_base_ref(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    baseline = {
        "mypy_max_errors": 10,
        "ruff_format_unformatted_files": [],
        "schema": "nebius-cxcli.quality-ratchets.v1",
    }
    baseline_path = _git_init_with_baseline(project_root, "scripts/quality-baseline.json", baseline)

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_quality_ratchets.py"),
            "baseline",
            "--project-root",
            str(project_root),
            "--baseline",
            str(baseline_path),
            "--base-ref",
            "does-not-exist",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "git rev-parse" in result.stderr


def test_coverage_baseline_rejects_lower_floor(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    current = json.loads(
        (_project_root() / "scripts" / "coverage-baseline.json").read_text(encoding="utf-8")
    )
    baseline_path = _git_init_with_baseline(project_root, "scripts/coverage-baseline.json", current)
    current["global_combined_percent"] -= 1
    baseline_path.write_text(json.dumps(current), encoding="utf-8")
    report = {
        "files": {
            path: {"summary": {"percent_covered": 100.0}}
            for path in current["critical_combined_percent"]
        },
        "totals": {"percent_covered": 100.0},
    }
    report_path = project_root / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_coverage.py"),
            str(report_path),
            "--baseline",
            str(baseline_path),
            "--project-root",
            str(project_root),
            "--base-ref",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "coverage baseline regression" in result.stderr


def test_cli_architecture_ratchet_rejects_new_definition(tmp_path: Path) -> None:
    source = tmp_path / "cli.py"
    source.write_text("def allowed():\n    pass\n\ndef added():\n    pass\n", encoding="utf-8")
    baseline = {
        "allowed_top_level_definitions": ["function:allowed"],
        "schema": "nebius-cxcli.cli-architecture-ratchet.v1",
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    result = subprocess.run(
        [
            str(_project_root() / ".venv" / "bin" / "python"),
            str(_project_root() / "scripts" / "check_cli_architecture.py"),
            "check",
            "--source",
            str(source),
            "--baseline",
            str(baseline_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "adds top-level CLI implementation definitions" in result.stderr
