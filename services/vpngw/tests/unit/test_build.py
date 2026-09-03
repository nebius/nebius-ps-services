from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from nebius_vpngw import build

VM_HA_COMPOSED_SUITES = (
    "tests/integration/test_vm_ha_runtime_composed.py",
    "tests/integration/test_vm_ha_failover.py",
)

VM_HA_SAFETY_NODES = (
    "tests/unit/test_cli_helpers.py::test_vm_ha_apply_prepares_default_directory_but_not_a_retained_member_key",
    "tests/unit/test_cli_helpers.py::test_vm_ha_apply_ssh_recovery_is_shared_across_routing_modes",
    "tests/unit/test_cli_helpers.py::"
    "test_vm_ha_apply_treats_lifecycle_bound_missing_compute_as_retained",
    "tests/unit/test_vm_manager_allocations.py::"
    "test_vm_ha_ssh_compute_reread_uses_bounded_request_policy",
    "tests/unit/test_ssh_policy.py::test_required_vm_ha_trust_rejects_missing_enrollment",
    "tests/unit/test_ssh_policy.py::test_vm_ha_policy_rejects_public_only_host_key",
    "tests/unit/test_ssh_policy.py::"
    "test_apply_imports_exact_retained_ed25519_pin_from_default_known_hosts",
    "tests/unit/test_ssh_policy.py::"
    "test_apply_recovers_retained_product_host_key_only_at_publication",
    "tests/unit/test_ssh_push.py::test_vm_ha_stage_classifies_missing_pinned_host_identity",
    "tests/unit/test_ssh_push.py::test_vm_ha_activation_classifies_host_identity_rejection",
)

GCP_SHELL_HELPERS = (
    "misc/gcp-vpngw-vm-ha.sh",
    "misc/gcp-vpngw-classic-vm-ha.sh",
)
RUFF_ROOTS = ("src", "tests", "misc")


def _ruff_roots(command: str, action: str) -> tuple[str, ...]:
    tokens = shlex.split(command)
    marker = ("-m", "ruff", action)
    marker_index = next(
        index
        for index in range(len(tokens) - len(marker) + 1)
        if tuple(tokens[index : index + len(marker)]) == marker
    )
    roots: list[str] = []
    for token in tokens[marker_index + len(marker) :]:
        if token.startswith("-"):
            break
        roots.append(token)
    return tuple(roots)


def test_build_binary_exits_when_pyinstaller_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(build.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit) as exc_info:
        build.build_binary()

    assert exc_info.value.code == 1


def test_build_binary_invokes_pyinstaller_with_systemd_assets(tmp_path, monkeypatch) -> None:
    run_mock = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(build.shutil, "which", lambda _: "/usr/bin/pyinstaller")
    monkeypatch.setattr(build.subprocess, "run", run_mock)
    monkeypatch.chdir(tmp_path)
    dist_binary = tmp_path / "dist" / "nebius-vpngw"
    dist_binary.parent.mkdir(parents=True, exist_ok=True)
    dist_binary.write_text("", encoding="utf-8")

    build.build_binary()

    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "pyinstaller"
    assert "--onefile" in cmd
    assert "--add-data" in cmd
    assert str(build.Path(build.__file__).parent / "__main__.py") in cmd
    systemd_dir = build.Path(build.__file__).parent / "systemd"
    assert (systemd_dir / "nebius-vpngw-fix-routes.service").exists()
    assert (systemd_dir / "nebius-vpngw-fix-routes.timer").exists()
    assert (systemd_dir / "nebius-vpngw-vm-ha-guard.service").exists()
    assert (systemd_dir / "nebius-vpngw-vm-ha-peer-firewall.sh").exists()
    assert (systemd_dir / "nebius-vpngw-vm-ha-rearm.service").exists()
    assert (systemd_dir / "nebius-vpngw-vm-ha.service").exists()


def test_pull_request_ci_selects_vm_ha_safety_regressions_exactly_once() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load(
        (repo_root / ".github/workflows/vpngw-ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["unit-tests"]["steps"]
    runs = {step["name"]: step["run"] for step in steps if "name" in step and "run" in step}

    safety_run = runs["VM-HA safety regressions"]
    unit_run = runs["Unit tests"]
    for suite in VM_HA_COMPOSED_SUITES:
        assert safety_run.count(suite) == 1
        assert f"{suite}::" not in safety_run
    for node in VM_HA_SAFETY_NODES:
        assert safety_run.count(node) == 1
        assert f"--deselect={node}" in unit_run

    manual_run = next(
        step["run"]
        for step in workflow["jobs"]["integration-tests"]["steps"]
        if step.get("name") == "Integration tests"
    )
    assert manual_run == "python -m pytest -n auto tests/integration"


def test_ci_runs_project_mypy_and_one_build_per_execution_lane() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load(
        (repo_root / ".github/workflows/vpngw-ci.yml").read_text(encoding="utf-8")
    )

    lint_runs = [step.get("run") for step in workflow["jobs"]["lint"]["steps"] if step.get("run")]
    assert lint_runs.count("python -m mypy") == 1
    assert workflow["jobs"]["unit-tests"]["needs"] == "lint"

    build_job = workflow["jobs"]["build"]
    packaging_job = workflow["jobs"]["packaging"]
    assert build_job["if"] == "github.event_name != 'workflow_dispatch'"
    assert packaging_job["if"] == "github.event_name == 'workflow_dispatch'"
    for job in (build_job, packaging_job):
        build_runs = [
            step["run"]
            for step in job["steps"]
            if "run" in step and "python -m build --wheel --no-isolation" in step["run"]
        ]
        assert len(build_runs) == 1


def test_ruff_roots_match_across_makefile_ci_and_release() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    project_root = Path(__file__).resolve().parents[2]
    makefile = (project_root / "Makefile").read_text(encoding="utf-8")
    make_format = next(line.strip() for line in makefile.splitlines() if "-m ruff format" in line)
    make_check = next(line.strip() for line in makefile.splitlines() if "-m ruff check" in line)
    observed = {
        "make-format": _ruff_roots(make_format, "format"),
        "make-check": _ruff_roots(make_check, "check"),
    }

    for workflow_name in ("vpngw-ci.yml", "vpngw-release.yml"):
        workflow = yaml.safe_load(
            (repo_root / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        )
        runs = [
            step["run"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", ())
            if "run" in step
        ]
        ruff = next(command for command in runs if "python -m ruff check" in command)
        observed[workflow_name] = _ruff_roots(ruff, "check")

    assert set(observed.values()) == {RUFF_ROOTS}


def test_ci_and_release_cover_both_gcp_vm_ha_shell_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    for workflow_name in ("vpngw-ci.yml", "vpngw-release.yml"):
        workflow = yaml.safe_load(
            (repo_root / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        )
        runs = [
            step["run"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", ())
            if "run" in step
        ]
        shell = next(command for command in runs if "bash -n misc/gcp-vpngw.sh" in command)
        for helper in GCP_SHELL_HELPERS:
            assert shell.count(f"bash -n {helper}") == 1
        assert shell.count("./misc/gcp-vpngw.sh --vm-ha-peer --help") == 1
        assert shell.count("./misc/gcp-vpngw.sh --classic-vm-ha-peer --help") == 1
