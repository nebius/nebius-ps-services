from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import nebius_cxcli.cli as cli_module
import nebius_cxcli.wireguard_clients as wireguard_module
from nebius_cxcli.cli import app
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.wireguard_clients import (
    WireGuardClientGenerationRequest,
    WireGuardClientGenerationResult,
    WireGuardLocalSubnetUpdateRequest,
    WireGuardLocalSubnetUpdateResult,
    generate_wireguard_client_config,
    normalize_local_subnet_csv,
    normalize_local_subnets,
    select_wireguard_component,
    update_wireguard_local_subnets,
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
        inventory_dir=project_dir / "generated" / "inventory",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _write_wireguard_config(
    config_path: Path,
    *,
    instance_id: str = "wireguard-gw",
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
  - id: wireguard-gw
    instance_id: {instance_id}
    enabled: true
    inputs:
      ssh_user_name: ubuntu
apps:
  charts: []
""",
        encoding="utf-8",
    )


def _write_config_without_wireguard(config_path: Path) -> None:
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


def test_select_wireguard_component_requires_selector_when_multiple() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "wg-a",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                },
                {
                    "id": "wireguard-gw",
                    "instance_id": "wg-b",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                },
            ]
        }
    }

    selected = select_wireguard_component(payload, component_selector="wg-b")

    assert selected.instance_id == "wg-b"


def test_generate_wireguard_client_config_writes_secret_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = select_wireguard_component(
        {
            "infra": {
                "components": [
                    {
                        "id": "wireguard-gw",
                        "enabled": True,
                        "inputs": {"ssh_user_name": "ubuntu"},
                    }
                ]
            }
        }
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(wireguard_module.secrets, "token_hex", lambda _bytes: "a" * 12)

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        payload = {
            "name": "wg-aaaaaaaaaaaa",
            "client_wg_tunnel_address": "10.8.0.2/32",
            "local_subnets": ["10.0.0.0/8"],
            "config_path": "/var/lib/wireguard/clients/wg-aaaaaaaaaaaa/wg-aaaaaaaaaaaa.conf",
            "config": "[Interface]\nPrivateKey = secret\n",
            "clients_created": 1,
            "remaining_client_slots": 252,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout="login banner\n" + json.dumps(payload))

    result = generate_wireguard_client_config(
        WireGuardClientGenerationRequest(
            component=component,
            public_ip="203.0.113.10",
            ssh_user="ubuntu",
            ssh_private_key=None,
            client_name=None,
            local_subnets=normalize_local_subnets(["10.0.0.0/8"]),
            dns=(),
            persistent_keepalive=None,
            output_dir=tmp_path / "wireguard-clients",
        ),
        run_command=fake_run,
    )

    assert calls
    assert "--name wg-aaaaaaaaaaaa" in calls[0][-1]
    assert "--local-subnet" in calls[0][-1]
    assert result.output_path.name == "wg-aaaaaaaaaaaa.conf"
    assert result.output_path.read_text(encoding="utf-8") == "[Interface]\nPrivateKey = secret\n"
    assert oct(result.output_path.stat().st_mode & 0o777) == "0o600"
    assert result.remaining_client_slots == 252


def test_generate_wireguard_client_config_rejects_wg_quick_invalid_name(tmp_path: Path) -> None:
    component = select_wireguard_component(
        {
            "infra": {
                "components": [
                    {
                        "id": "wireguard-gw",
                        "enabled": True,
                        "inputs": {"ssh_user_name": "ubuntu"},
                    }
                ]
            }
        }
    )

    with pytest.raises(ValueError, match="up to 15 characters"):
        generate_wireguard_client_config(
            WireGuardClientGenerationRequest(
                component=component,
                public_ip="203.0.113.10",
                ssh_user="ubuntu",
                ssh_private_key=None,
                client_name="client-20260514224002-8e7cbf",
                local_subnets=(),
                dns=(),
                persistent_keepalive=None,
                output_dir=tmp_path / "wireguard-clients",
            )
        )


def test_normalize_local_subnet_csv_canonicalizes_and_dedupes() -> None:
    assert normalize_local_subnet_csv("10.20.0.1/16, 10.30.0.0/16,10.20.0.0/16") == (
        "10.20.0.0/16",
        "10.30.0.0/16",
    )


def test_update_wireguard_local_subnets_runs_vm_local_helper() -> None:
    component = select_wireguard_component(
        {
            "infra": {
                "components": [
                    {
                        "id": "wireguard-gw",
                        "enabled": True,
                        "inputs": {"ssh_user_name": "ubuntu"},
                    }
                ]
            }
        }
    )
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        payload = {
            "local_subnets": ["10.0.0.0/8", "10.20.0.0/16"],
            "added": ["10.20.0.0/16"],
            "removed": [],
            "unchanged": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    result = update_wireguard_local_subnets(
        WireGuardLocalSubnetUpdateRequest(
            component=component,
            public_ip="203.0.113.10",
            ssh_user="ubuntu",
            ssh_private_key=None,
            operation="add",
            local_subnets=("10.20.0.0/16",),
        ),
        run_command=fake_run,
    )

    assert "add-local-subnets" in calls[0][-1]
    assert "--local-subnet 10.20.0.0/16" in calls[0][-1]
    assert result.added == ("10.20.0.0/16",)
    assert result.local_subnets == ("10.0.0.0/8", "10.20.0.0/16")


def test_wireguard_command_uses_deployed_output_and_default_ignored_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }
    captured: dict[str, WireGuardClientGenerationRequest] = {}

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )

    def fake_generate(request: WireGuardClientGenerationRequest) -> WireGuardClientGenerationResult:
        captured["request"] = request
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = request.output_dir / "client-1.conf"
        output_path.write_text("[Interface]\n", encoding="utf-8")
        return WireGuardClientGenerationResult(
            client_name="client-1",
            client_wg_tunnel_address="10.8.0.2/32",
            local_subnets=("10.0.0.0/8",),
            output_path=output_path,
            remote_config_path="/var/lib/wireguard/clients/client-1/client-1.conf",
            clients_created=1,
            remaining_client_slots=252,
        )

    monkeypatch.setattr(cli_module, "generate_wireguard_client_config", fake_generate)

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--gen-client-conf",
            str(paths.config_path),
            "--local-subnet",
            "10.0.0.0/8",
        ],
    )

    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.public_ip == "203.0.113.10"
    assert request.output_dir == paths.project_dir / "wireguard-clients"
    assert request.local_subnets == ("10.0.0.0/8",)
    assert "WireGuard client config written" in result.output
    assert "Client config ignore rule" not in result.output
    connect_command = f"wg-quick up {shlex.quote(str(request.output_dir / 'client-1.conf'))}"
    disconnect_command = f"wg-quick down {shlex.quote(str(request.output_dir / 'client-1.conf'))}"
    assert f"Run this command to connect: {connect_command}" in result.output
    assert f"Run this command to disconnect: {disconnect_command}" in result.output


def test_wireguard_command_prints_macos_install_hint_when_wg_quick_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )
    monkeypatch.setattr(cli_module.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(cli_module.sys, "platform", "darwin")

    def fake_generate(request: WireGuardClientGenerationRequest) -> WireGuardClientGenerationResult:
        output_path = request.output_dir / "client-1.conf"
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text("[Interface]\n", encoding="utf-8")
        return WireGuardClientGenerationResult(
            client_name="client-1",
            client_wg_tunnel_address="10.8.0.2/32",
            local_subnets=("10.0.0.0/8",),
            output_path=output_path,
            remote_config_path="/var/lib/wireguard/clients/client-1/client-1.conf",
            clients_created=1,
            remaining_client_slots=252,
        )

    monkeypatch.setattr(cli_module, "generate_wireguard_client_config", fake_generate)

    result = runner.invoke(app, ["wireguard", "--gen-client-conf", str(paths.config_path)])

    assert result.exit_code == 0, result.output
    assert "WireGuard client tool not found locally: wg-quick" in result.output
    assert "Install it with: brew install wireguard-tools" in result.output


def test_wireguard_client_install_command_is_os_specific(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=fedora\nID_LIKE="rhel fedora"\n', encoding="utf-8")

    assert (
        cli_module._wireguard_client_install_command(platform_name="darwin")
        == "brew install wireguard-tools"
    )
    assert (
        cli_module._wireguard_client_install_command(
            platform_name="linux",
            os_release_path=os_release,
        )
        == "sudo dnf install -y wireguard-tools"
    )


def test_wireguard_command_adds_local_subnets_from_comma_separated_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }
    captured: dict[str, WireGuardLocalSubnetUpdateRequest] = {}

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )

    def fake_update(
        request: WireGuardLocalSubnetUpdateRequest,
    ) -> WireGuardLocalSubnetUpdateResult:
        captured["request"] = request
        return WireGuardLocalSubnetUpdateResult(
            local_subnets=("10.0.0.0/8", "10.20.0.0/16", "10.30.0.0/16"),
            added=("10.20.0.0/16", "10.30.0.0/16"),
            removed=(),
            unchanged=(),
        )

    monkeypatch.setattr(cli_module, "update_wireguard_local_subnets", fake_update)

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--add-local-subnets",
            str(paths.config_path),
            "--local-subnet",
            "10.20.0.1/16,10.30.0.0/16",
        ],
    )

    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.operation == "add"
    assert request.local_subnets == ("10.20.0.0/16", "10.30.0.0/16")
    assert "Added: 10.20.0.0/16, 10.30.0.0/16" in result.output


def test_wireguard_command_removes_local_subnets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }
    captured: dict[str, WireGuardLocalSubnetUpdateRequest] = {}

    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )

    def fake_update(
        request: WireGuardLocalSubnetUpdateRequest,
    ) -> WireGuardLocalSubnetUpdateResult:
        captured["request"] = request
        return WireGuardLocalSubnetUpdateResult(
            local_subnets=("10.0.0.0/8",),
            added=(),
            removed=("10.20.0.0/16",),
            unchanged=("10.30.0.0/16",),
        )

    monkeypatch.setattr(cli_module, "update_wireguard_local_subnets", fake_update)

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--remove-local-subnets",
            str(paths.config_path),
            "--local-subnet",
            "10.20.0.0/16,10.30.0.0/16",
        ],
    )

    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.operation == "remove"
    assert request.local_subnets == ("10.20.0.0/16", "10.30.0.0/16")
    assert "Removed: 10.20.0.0/16" in result.output
    assert "Unchanged: 10.30.0.0/16" in result.output


def test_wireguard_command_requires_component_in_source_config_before_generated_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_config_without_wireguard(paths.config_path)

    def fail_load_context(_path: Path) -> tuple[Any, ProjectPaths, dict[str, Any]]:
        raise AssertionError(
            "generated context should not load when source config has no WireGuard component"
        )

    monkeypatch.setattr(cli_module, "_load_deploy_context", fail_load_context)

    result = runner.invoke(app, ["wireguard", "--gen-client-conf", str(paths.config_path)])

    assert result.exit_code == 1, result.output
    assert "config.yaml does not enable a wireguard-gw infra component" in result.output


def test_wireguard_command_rejects_source_generated_component_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    generated_payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "old-wg",
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

    result = runner.invoke(app, ["wireguard", "--gen-client-conf", str(paths.config_path)])

    assert result.exit_code == 1, result.output
    assert "wireguard-gw is enabled in config.yaml" in result.output
    assert "render" in result.output
    assert "deploy" in result.output


def test_wireguard_command_rejects_multiple_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    result = runner.invoke(
        app,
        [
            "wireguard",
            "--gen-client-conf",
            str(config_path),
            "--add-local-subnets",
            str(config_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "Use exactly one of --gen-client-conf" in result.output


def test_wireguard_command_rejects_add_remove_only_flags_before_loading_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"

    def fail_load_context(_path: Path) -> tuple[Any, ProjectPaths, dict[str, Any]]:
        raise AssertionError("config context should not load for invalid flags")

    monkeypatch.setattr(cli_module, "_load_deploy_context", fail_load_context)

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--add-local-subnets",
            str(config_path),
            "--client-name",
            "laptop",
            "--local-subnet",
            "10.20.0.0/16",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "only apply to --gen-client-conf" in result.output


def test_wireguard_command_rejects_invalid_local_subnet_before_loading_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"

    def fail_load_context(_path: Path) -> tuple[Any, ProjectPaths, dict[str, Any]]:
        raise AssertionError("config context should not load for invalid CIDRs")

    monkeypatch.setattr(cli_module, "_load_deploy_context", fail_load_context)

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--add-local-subnets",
            str(config_path),
            "--local-subnet",
            "10.20.0.0/16,2001:db8::/64",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "--local-subnet currently supports IPv4 CIDRs only" in result.output


def test_wireguard_command_rejects_repeated_local_subnet_for_add_remove(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }
    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--add-local-subnets",
            str(paths.config_path),
            "--local-subnet",
            "10.20.0.0/16",
            "--local-subnet",
            "10.30.0.0/16",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "require exactly one --local-subnet option" in " ".join(result.output.split())


def test_wireguard_command_rejects_invalid_local_subnet_csv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.project_dir.mkdir(parents=True)
    _write_wireguard_config(paths.config_path)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                }
            ]
        }
    }
    monkeypatch.setattr(cli_module, "_load_deploy_context", lambda _path: (payload, paths, {}))
    monkeypatch.setattr(cli_module, "_ensure_terraform_backend_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_terraform_runtime_env", lambda _config: {})
    monkeypatch.setattr(
        cli_module,
        "terraform_output_json",
        lambda _infra_dir, **_kw: {"wireguard_gw_public_ip": {"value": "203.0.113.10"}},
    )

    result = runner.invoke(
        app,
        [
            "wireguard",
            "--add-local-subnets",
            str(paths.config_path),
            "--local-subnet",
            "10.20.0.0/16,2001:db8::/64",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "--local-subnet currently supports IPv4 CIDRs only" in result.output
