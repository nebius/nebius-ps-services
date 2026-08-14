from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from nebius_vpngw import build

VM_HA_SAFETY_NODES = (
    "tests/integration/test_vm_ha_runtime_composed.py::"
    "test_clean_owner_bootstrap_materializes_passive_before_promotion",
    "tests/integration/test_vm_ha_failover.py::"
    "test_healthy_nodes_keep_one_exact_owner_and_one_passive",
    "tests/unit/test_cli_helpers.py::"
    "test_vm_ha_apply_rejects_missing_host_trust_before_vm_manager",
    "tests/unit/test_ssh_policy.py::"
    "test_required_vm_ha_trust_rejects_missing_enrollment",
    "tests/unit/test_ssh_policy.py::"
    "test_vm_ha_policy_rejects_public_only_host_key",
    "tests/unit/test_ssh_push.py::"
    "test_vm_ha_stage_classifies_missing_pinned_host_identity",
    "tests/unit/test_ssh_push.py::"
    "test_vm_ha_activation_classifies_host_identity_rejection",
)


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
    assert (systemd_dir / "nebius-vpngw-vm-ha-guard.service").exists()
    assert (systemd_dir / "nebius-vpngw-vm-ha.service").exists()


def test_pull_request_ci_selects_vm_ha_safety_regressions_exactly_once() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load(
        (repo_root / ".github/workflows/vpngw-ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["unit-tests"]["steps"]
    runs = {
        step["name"]: step["run"]
        for step in steps
        if "name" in step and "run" in step
    }

    safety_run = runs["VM-HA safety regressions"]
    unit_run = runs["Unit tests"]
    for node in VM_HA_SAFETY_NODES:
        assert safety_run.count(node) == 1
        if node.startswith("tests/unit/"):
            assert f"--deselect={node}" in unit_run

    manual_run = next(
        step["run"]
        for step in workflow["jobs"]["integration-tests"]["steps"]
        if step.get("name") == "Integration tests"
    )
    assert manual_run == "python -m pytest -n auto tests/integration"
