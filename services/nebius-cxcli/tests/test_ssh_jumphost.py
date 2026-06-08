from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import nebius_cxcli.cli as cli_module
from nebius_cxcli.cli import app
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.ssh_jumphost import (
    SshJumphostAllowedCidrRequest,
    SshJumphostAllowedCidrResult,
    normalize_allowed_cidr_csv,
    select_ssh_jumphost_component,
    update_ssh_jumphost_allowed_cidrs,
)

runner = CliRunner()


def _project_paths(tmp_path: Path) -> ProjectPaths:
    deployments_dir = tmp_path / "deployments"
    project_dir = deployments_dir / "tenant" / "project"
    config_path = project_dir / "config.yaml"
    return ProjectPaths(
        config_path=config_path,
        repo_root=tmp_path,
        deployments_dir=deployments_dir,
        project_dir=project_dir,
        generated_dir=project_dir / "generated",
        infra_dir=project_dir / "generated" / "infra",
        flux_dir=project_dir / "generated" / "flux",
        reports_dir=project_dir / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _write_ssh_jumphost_config(
    config_path: Path,
    *,
    instance_id: str = "ssh-jumphost",
) -> None:
    config_path.write_text(
        f"""version: v1
client_info:
  client_name: demo
  nebius:
    tenant_id: tenant-1
    project_id: project-1
    region_id: eu-north1
  notifications:
    email_enabled: false
    email: null
infra:
  components:
  - id: ssh-jumphost
    instance_id: {instance_id}
    enabled: true
    inputs:
      ssh_user_name: ubuntu
apps:
  charts: []
""",
        encoding="utf-8",
    )


def _write_config_without_ssh_jumphost(config_path: Path) -> None:
    config_path.write_text(
        """version: v1
client_info:
  client_name: demo
  nebius:
    tenant_id: tenant-1
    project_id: project-1
    region_id: eu-north1
  notifications:
    email_enabled: false
    email: null
infra:
  components: []
apps:
  charts: []
""",
        encoding="utf-8",
    )


def _ssh_jumphost_payload() -> dict[str, Any]:
    return {
        "infra": {
            "components": [
                {
                    "id": "ssh-jumphost",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }


def test_select_ssh_jumphost_component_requires_selector_when_multiple() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "ssh-jumphost",
                    "instance_id": "bastion-a",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                },
                {
                    "id": "ssh-jumphost",
                    "instance_id": "bastion-b",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                },
            ]
        }
    }

    selected = select_ssh_jumphost_component(payload, component_selector="bastion-b")

    assert selected.instance_id == "bastion-b"


def test_normalize_allowed_cidr_csv_canonicalizes_and_dedupes() -> None:
    assert normalize_allowed_cidr_csv(
        "203.0.113.10/32, 198.51.100.4/24,198.51.100.0/24"
    ) == (
        "203.0.113.10/32",
        "198.51.100.0/24",
    )


def test_update_ssh_jumphost_allowed_cidrs_runs_vm_local_helper() -> None:
    component = select_ssh_jumphost_component(_ssh_jumphost_payload())
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        payload = {
            "allowed_cidrs": ["203.0.113.10/32", "198.51.100.0/24"],
            "added": ["198.51.100.0/24"],
            "removed": [],
            "unchanged": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    result = update_ssh_jumphost_allowed_cidrs(
        SshJumphostAllowedCidrRequest(
            component=component,
            public_ip="203.0.113.10",
            ssh_user="ubuntu",
            ssh_private_key=None,
            operation="add",
            allowed_cidrs=("198.51.100.0/24",),
        ),
        run_command=fake_run,
    )

    assert "add-allowed-cidrs" in calls[0][-1]
    assert "--allowed-cidr 198.51.100.0/24" in calls[0][-1]
    assert result.added == ("198.51.100.0/24",)
    assert result.allowed_cidrs == ("203.0.113.10/32", "198.51.100.0/24")


def test_ssh_jumphost_command_adds_allowed_cidrs_from_comma_separated_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_ssh_jumphost_config(paths.config_path)
    captured: dict[str, SshJumphostAllowedCidrRequest] = {}

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (_ssh_jumphost_payload(), paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"ssh_jumphost_public_ip": {"value": "203.0.113.10"}},
    )

    def fake_update(
        request: SshJumphostAllowedCidrRequest,
    ) -> SshJumphostAllowedCidrResult:
        captured["request"] = request
        return SshJumphostAllowedCidrResult(
            allowed_cidrs=("203.0.113.10/32", "198.51.100.0/24"),
            added=("203.0.113.10/32", "198.51.100.0/24"),
        )

    monkeypatch.setattr(cli_module, "update_ssh_jumphost_allowed_cidrs", fake_update)

    result = runner.invoke(
        app,
        [
            "ssh-jumphost",
            "--add-allowed-cidrs",
            str(paths.config_path),
            "--allowed-cidr",
            "203.0.113.10/32,198.51.100.4/24",
        ],
    )

    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.operation == "add"
    assert request.allowed_cidrs == ("203.0.113.10/32", "198.51.100.0/24")
    assert "Added: 203.0.113.10/32, 198.51.100.0/24" in result.output


def test_ssh_jumphost_command_lists_allowed_cidrs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_ssh_jumphost_config(paths.config_path)
    captured: dict[str, SshJumphostAllowedCidrRequest] = {}

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (_ssh_jumphost_payload(), paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"ssh_jumphost_public_ip": {"value": "203.0.113.10"}},
    )

    def fake_update(
        request: SshJumphostAllowedCidrRequest,
    ) -> SshJumphostAllowedCidrResult:
        captured["request"] = request
        return SshJumphostAllowedCidrResult(
            allowed_cidrs=("203.0.113.10/32",),
        )

    monkeypatch.setattr(cli_module, "update_ssh_jumphost_allowed_cidrs", fake_update)

    result = runner.invoke(
        app,
        ["ssh-jumphost", "--list-allowed-cidrs", str(paths.config_path)],
    )

    assert result.exit_code == 0, result.output
    assert captured["request"].operation == "list"
    assert "Current allowed CIDRs: 203.0.113.10/32" in result.output


def test_ssh_jumphost_command_requires_component_in_source_config_before_generated_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_config_without_ssh_jumphost(paths.config_path)

    def fail_load_context(_path: Path) -> tuple[Any, ProjectPaths, dict[str, Any]]:
        raise AssertionError(
            "generated context should not load when source config has no SSH jump-host"
        )

    monkeypatch.setattr(cli_module, "_load_deploy_context", fail_load_context)

    result = runner.invoke(
        app,
        ["ssh-jumphost", "--list-allowed-cidrs", str(paths.config_path)],
    )

    assert result.exit_code == 1, result.output
    assert "config.yaml does not enable an ssh-jumphost infra component" in result.output


def test_ssh_jumphost_command_rejects_source_generated_component_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_ssh_jumphost_config(paths.config_path)
    generated_payload = {
        "infra": {
            "components": [
                {
                    "id": "ssh-jumphost",
                    "instance_id": "old-bastion",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }

    def fail_backend(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("backend should not initialize for a stale generated bundle")

    monkeypatch.setattr(
        cli_module, "_load_deploy_context", lambda _path: (generated_payload, paths, {})
    )
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", fail_backend)

    result = runner.invoke(
        app,
        ["ssh-jumphost", "--list-allowed-cidrs", str(paths.config_path)],
    )

    assert result.exit_code == 1, result.output
    assert "ssh-jumphost is enabled in config.yaml" in result.output
    assert "render" in result.output
    assert "deploy" in result.output


def test_ssh_jumphost_command_rejects_repeated_allowed_cidr_for_add_remove(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("client_info: {}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "ssh-jumphost",
            "--add-allowed-cidrs",
            str(config_path),
            "--allowed-cidr",
            "203.0.113.10/32",
            "--allowed-cidr",
            "198.51.100.0/24",
        ],
    )

    assert result.exit_code == 1
    assert "require exactly one --allowed-cidr option" in " ".join(result.output.split())
