from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from click import unstyle
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from nebius_vpngw.cli import (
    _COMMAND_APPLICABILITY,
    _enforce_command_applicability,
    app,
)
from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
)
from nebius_vpngw.deploy.route_manager import (
    RouteManagementError,
    VMHAStaticRouteConvergence,
)


def _plan(*, vm_ha: bool) -> ResolvedDeploymentPlan:
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="gateway",
            instance_count=1,
            region="region-a",
            external_ips=[["203.0.113.10"]],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.0.0.0/24"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="gateway-0",
                external_ip="203.0.113.10",
                config_yaml="version: 1\n",
            )
        ],
        manage_routes=True,
    )
    return replace(plan, vm_ha=SimpleNamespace(cluster_id="cluster-a")) if vm_ha else plan


def _config(mode: str) -> dict:
    return {
        "project_id": "project-a",
        "defaults": {"routing": {"mode": mode}},
        "gateway": {"local_prefixes": ["10.0.0.0/24"]},
        "connections": [
            {
                "name": "connection-a",
                "routing_mode": mode,
                "remote_prefixes": ["10.10.0.0/24"],
                "tunnels": [
                    {
                        "name": "tunnel-a",
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }


def test_applicability_registry_covers_every_executable_leaf_and_action_mode() -> None:
    assert set(_COMMAND_APPLICABILITY) == {
        "create-config",
        "vm-ha",
        "prep-network",
        "validate-config",
        "apply",
        "status",
        "vm-ha --rotate-mtls",
        "add-routes-local",
        "list-routes-local",
        "list-routes-remote",
        "restart-tunnel",
        "create-from-peer-config",
        "destroy",
        "failover vm",
        "failover tunnel",
        "failback vm",
        "failback tunnel",
    }


@pytest.mark.parametrize("command", tuple(_COMMAND_APPLICABILITY))
@pytest.mark.parametrize(
    ("vm_ha", "mode"),
    (
        (False, "static"),
        (False, "bgp"),
        (True, "static"),
        (True, "bgp"),
    ),
)
def test_every_executable_leaf_has_a_four_mode_applicability_contract(
    command: str,
    vm_ha: bool,
    mode: str,
) -> None:
    all_topologies = {
        "create-config",
        "vm-ha",
        "prep-network",
        "validate-config",
        "apply",
        "status",
        "list-routes-local",
        "list-routes-remote",
        "create-from-peer-config",
        "destroy",
    }
    ordinary_only = {"restart-tunnel"}
    vm_ha_only = {
        "vm-ha --rotate-mtls",
        "failover vm",
        "failback vm",
    }
    ordinary_bgp_only = {"failover tunnel", "failback tunnel"}

    allowed = (
        command in all_topologies
        or (command in ordinary_only and not vm_ha)
        or (command in vm_ha_only and vm_ha)
        or (command in ordinary_bgp_only and not vm_ha and mode == "bgp")
        or command == "add-routes-local"
    )

    if allowed:
        _enforce_command_applicability(command, _plan(vm_ha=vm_ha), _config(mode))
    else:
        with pytest.raises(typer.BadParameter):
            _enforce_command_applicability(command, _plan(vm_ha=vm_ha), _config(mode))


@pytest.mark.parametrize(
    ("command", "vm_ha", "mode", "allowed"),
    (
        ("restart-tunnel", False, "static", True),
        ("restart-tunnel", False, "bgp", True),
        ("restart-tunnel", True, "static", False),
        ("restart-tunnel", True, "bgp", False),
        ("destroy", False, "static", True),
        ("destroy", True, "static", True),
        ("destroy", True, "bgp", True),
        ("failover tunnel", False, "bgp", True),
        ("failover tunnel", False, "static", False),
        ("failover tunnel", True, "bgp", False),
        ("failback tunnel", False, "bgp", True),
        ("failback tunnel", False, "static", False),
        ("failback tunnel", True, "bgp", False),
        ("failover vm", False, "bgp", False),
        ("failover vm", True, "static", True),
        ("failback vm", False, "static", False),
        ("failback vm", True, "bgp", True),
        ("vm-ha --rotate-mtls", False, "bgp", False),
        ("vm-ha --rotate-mtls", True, "static", True),
        ("add-routes-local", False, "static", True),
        ("add-routes-local", False, "bgp", True),
        ("add-routes-local", True, "static", True),
        ("add-routes-local", True, "bgp", True),
    ),
)
def test_command_applicability_matrix(
    command: str,
    vm_ha: bool,
    mode: str,
    allowed: bool,
) -> None:
    if allowed:
        _enforce_command_applicability(command, _plan(vm_ha=vm_ha), _config(mode))
    else:
        with pytest.raises(typer.BadParameter):
            _enforce_command_applicability(command, _plan(vm_ha=vm_ha), _config(mode))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"summarize": True}, "does not accept"),
        ({"swap_route_table": True}, "does not accept"),
        ({"yes": True}, "valid only"),
    ),
)
def test_vm_ha_add_routes_rejects_legacy_route_flags(kwargs, message: str) -> None:
    with pytest.raises(typer.BadParameter, match=message):
        _enforce_command_applicability(
            "add-routes-local",
            _plan(vm_ha=True),
            _config("bgp"),
            **kwargs,
        )


def test_vm_ha_add_routes_rejects_mixed_routing_before_effects() -> None:
    config = _config("static")
    config["connections"].append(
        {
            "name": "connection-b",
            "routing_mode": "bgp",
            "tunnels": [],
        }
    )

    with pytest.raises(typer.BadParameter, match="entirely static or entirely BGP"):
        _enforce_command_applicability(
            "add-routes-local",
            _plan(vm_ha=True),
            config,
        )


def test_add_routes_installed_skew_fails_before_route_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=True)
    calls: list[str] = []

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._build_route_ssh_policy",
        lambda _config, _plan, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def require_agent_capabilities(self, *_args):
            calls.append("capability")
            raise RouteManagementError("installed agent capability skew")

        def add_routes(self, *_args, **_kwargs):
            calls.append("route-mutation")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert calls == ["capability"]
    assert "installed agent capability skew" in result.output
    assert "Local route management completed" not in result.output


def test_vm_ha_bgp_add_routes_repairs_exports_without_legacy_vpc_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=True)
    calls: list[str] = []

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    ssh_policy = object()
    monkeypatch.setattr(
        "nebius_vpngw.cli._build_route_ssh_policy",
        lambda _config, _plan, **_kwargs: ssh_policy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    constructed: list[object] = []

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            constructed.append(_kwargs.get("ssh_policy"))

        def require_agent_capabilities(self, *_args):
            calls.append("capability")

        def add_routes(self, *_args, **_kwargs):
            calls.append("route-mutation")

        def ensure_bgp_advertisements_current(self, *_args, **_kwargs):
            calls.append("advertisement-repair")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert constructed == [ssh_policy]
    assert calls == ["capability", "advertisement-repair"]
    assert "controller-owned" in result.output
    assert "Local route management completed" in result.output


def test_ordinary_bgp_add_routes_reconciles_vpc_routes_and_advertisements(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=False)
    calls: list[str] = []

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def require_agent_capabilities(self, *_args):
            calls.append("capability")

        def add_routes(self, *_args, **_kwargs):
            calls.append("route-mutation")

        def ensure_bgp_advertisements_current(self, *_args, **_kwargs):
            calls.append("advertisement-repair")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["capability", "route-mutation", "advertisement-repair"]
    assert "controller-owned" not in result.output
    assert "Local route management completed" in result.output


def test_vm_ha_static_add_routes_waits_for_controller_without_legacy_route_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("static")
    plan = _plan(vm_ha=True)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    monkeypatch.setattr(
        "nebius_vpngw.cli._build_route_ssh_policy",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )
    calls: list[str] = []

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def add_routes(self, *_args, **_kwargs):
            calls.append("legacy-route-mutation")

        def ensure_vm_ha_static_routes_current(self, *_args, **kwargs):
            calls.append("static-controller-wait")
            kwargs["on_wait"]()
            return VMHAStaticRouteConvergence.CONVERGED

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["static-controller-wait"]
    assert "controller-owned static-route reconciliation" in result.output
    assert "now match the installed generation" in result.output
    assert "Local route management completed" in result.output


def test_vm_ha_static_add_routes_failure_has_no_completion_banner(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: _config("static"))
    monkeypatch.setattr(
        "nebius_vpngw.cli.merge_with_peer_configs",
        lambda *_args: _plan(vm_ha=True),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._build_route_ssh_policy",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def ensure_vm_ha_static_routes_current(self, *_args, **_kwargs):
            raise RouteManagementError("static controller did not converge")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert "static controller did not converge" in result.output
    assert "Local route management completed" not in result.output


def test_vm_ha_route_ssh_pin_failure_precedes_authentication(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=True)
    calls: list[str] = []

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.require_vm_ha_ssh_policy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("missing pin")),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: calls.append("auth"),
    )

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert calls == []
    assert "exact pinned SSH trust" in " ".join(result.output.split())
    assert "Local route management completed" not in result.output


def test_ordinary_static_add_routes_never_requires_agent_repair_capability(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("static")
    plan = _plan(vm_ha=False)
    calls: list[str] = []

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def require_agent_capabilities(self, *_args):
            calls.append("capability")

        def add_routes(self, *_args, **_kwargs):
            calls.append("route-mutation")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["route-mutation"]


def test_add_routes_failure_exits_nonzero_without_false_completion(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("static")
    plan = _plan(vm_ha=False)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )

    class FakeRouteManager:
        def __init__(self, **_kwargs):
            pass

        def add_routes(self, *_args, **_kwargs):
            raise RouteManagementError("route postcondition failed")

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = CliRunner().invoke(
        app,
        ["add-routes-local", "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert "route postcondition failed" in result.output
    assert "Local route management completed" not in result.output


@pytest.mark.parametrize(
    ("argv", "expected_message"),
    (
        (
            ("failover", "tunnel", "tunnel-a"),
            "not supported for a VM-HA-enabled gateway",
        ),
        (
            ("failback", "tunnel", "tunnel-a"),
            "not supported for a VM-HA-enabled gateway",
        ),
    ),
)
def test_vm_ha_ordinary_only_commands_fail_before_external_effects(
    argv: tuple[str, ...],
    expected_message: str,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=True)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("external effect occurred before applicability rejection")

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", unexpected_effect)
    monkeypatch.setattr("subprocess.run", unexpected_effect)

    result = CliRunner().invoke(
        app,
        [*argv, "--local-config-file", str(config_path)],
    )

    assert result.exit_code != 0
    assert expected_message in " ".join(result.output.split())


@pytest.mark.parametrize("mode", ("static", "bgp"))
@pytest.mark.parametrize("tunnel_name", ("all", "tunnel-a"))
def test_vm_ha_tunnel_restart_reports_clean_guidance_before_external_effects(
    mode: str,
    tunnel_name: str,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config(mode)
    plan = _plan(vm_ha=True)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("external effect occurred before applicability rejection")

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", unexpected_effect)
    monkeypatch.setattr("nebius_vpngw.cli._build_ssh_base_cmd", unexpected_effect)
    monkeypatch.setattr("subprocess.run", unexpected_effect)

    result = CliRunner().invoke(
        app,
        ["restart-tunnel", tunnel_name, "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Tunnel restart is not supported for a VM-HA-enabled gateway. "
        "Tunnel recovery is controller-owned; use "
        "'nebius-vpngw status --local-config-file <file>' to inspect health and "
        "'nebius-vpngw apply --local-config-file <file>' only for configuration "
        "convergence.\n"
    )


@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (("failover", "tunnel", "tunnel-a"), "failover"),
        (("failback", "tunnel", "tunnel-a"), "failback"),
    ),
)
def test_static_tunnel_failover_commands_fail_before_ssh(
    argv: tuple[str, ...],
    action: str,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("static")
    plan = _plan(vm_ha=False)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("SSH occurred before routing-mode rejection")

    monkeypatch.setattr("subprocess.run", unexpected_effect)

    result = CliRunner().invoke(
        app,
        [*argv, "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Tunnel {action} is not supported for Static routing; it is available "
        "only for ordinary BGP configurations.\n"
    )


@pytest.mark.parametrize("mode", ("static", "bgp"))
@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (("failover", "tunnel"), "failover"),
        (("failover", "tunnel", "tunnel-a"), "failover"),
        (("failback", "tunnel"), "failback"),
        (("failback", "tunnel", "tunnel-a"), "failback"),
    ),
)
def test_vm_ha_tunnel_transfer_reports_clean_guidance_before_external_effects(
    mode: str,
    argv: tuple[str, ...],
    action: str,
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config(mode)
    plan = _plan(vm_ha=True)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("external effect occurred before applicability rejection")

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", unexpected_effect)
    monkeypatch.setattr("subprocess.run", unexpected_effect)

    result = CliRunner().invoke(
        app,
        [*argv, "--local-config-file", str(config_path)],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Tunnel {action} is not supported for a VM-HA-enabled gateway. "
        f"Use 'nebius-vpngw {action} vm --local-config-file <file>' only to transfer "
        "VM ownership; it does not select a tunnel.\n"
    )


@pytest.mark.parametrize(
    "argv",
    (
        ("vm-ha", "--rotate-mtls", "--dry-run"),
        ("failover", "vm"),
        ("failback", "vm"),
    ),
)
def test_vm_ha_only_commands_use_central_policy_before_external_effects(
    argv: tuple[str, ...],
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = _config("bgp")
    plan = _plan(vm_ha=False)

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _path: config)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("external effect occurred before applicability rejection")

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", unexpected_effect)

    result = CliRunner().invoke(
        app,
        [*argv, "--local-config-file", str(config_path)],
    )

    assert result.exit_code != 0
    normalized_output = " ".join(result.output.split())
    assert "requires" in normalized_output
    assert "gateway_group.vm_ha" in normalized_output


def test_public_command_parameter_and_alias_manifest_is_exact() -> None:
    expected = {
        ("create-config",): (
            ("config_file",),
            ("--force", "-f"),
            ("--interactive",),
            ("--no-interactive",),
        ),
        ("vm-ha",): (
            ("--local-config-file", "-c"),
            ("--rotate-mtls",),
            ("--output", "-o"),
            ("--force", "-f"),
            ("--standby-auto-healing",),
            ("--region",),
            ("--dry-run",),
            ("--approve",),
            ("--output-format",),
        ),
        ("prep-network",): (
            ("--local-config-file", "-c"),
            ("--region",),
            ("--interactive",),
            ("--no-interactive",),
        ),
        ("validate-config",): (("config_file",),),
        ("apply",): (
            ("--local-config-file", "-c"),
            ("--recreate-gw", "--no-recreate-gw"),
            ("--sa",),
            ("--project-id",),
            ("--region",),
            ("--dry-run",),
            ("--prepare-vm-ha-peer-rotation",),
            ("--approve-vm-ha-migration",),
            ("--recover-vm-ha-migration",),
            ("--replace-failed-vm-ha-passive",),
        ),
        ("status",): (("--local-config-file", "-c"), ("--project-id",), ("--region",)),
        ("add-routes-local",): (
            ("--local-config-file", "-c"),
            ("--project-id",),
            ("--summarize",),
            ("--swap-route-table",),
            ("--yes", "-y"),
        ),
        ("list-routes-local",): (("--local-config-file", "-c"), ("--project-id",)),
        ("list-routes-remote",): (("--local-config-file", "-c"), ("--connection",)),
        ("restart-tunnel",): (("tunnel_name",), ("--local-config-file", "-c")),
        ("create-from-peer-config",): (
            ("config_file",),
            ("--local-config-file", "-c"),
            ("--peer-config-file",),
            ("--force", "-f"),
        ),
        ("destroy",): (
            ("--local-config-file", "-c"),
            ("--project-id",),
            ("--region",),
            ("--yes", "-y"),
        ),
        ("failover", "vm"): (("--local-config-file", "-c"), ("--output-format",)),
        ("failover", "tunnel"): (("tunnel_name",), ("--local-config-file", "-c")),
        ("failback", "vm"): (("--local-config-file", "-c"), ("--output-format",)),
        ("failback", "tunnel"): (("tunnel_name",), ("--local-config-file", "-c")),
    }
    actual = {}

    def walk(command, path=()):
        if isinstance(command, TyperGroup):
            for name, child in command.commands.items():
                walk(child, (*path, name))
            return
        actual[path] = tuple(
            tuple(getattr(param, "opts", ()) + getattr(param, "secondary_opts", ()))
            for param in command.params
        )

    walk(get_command(app))

    assert actual == expected


@pytest.mark.parametrize(
    "command_path",
    (
        ("apply",),
        ("status",),
        ("add-routes-local",),
        ("list-routes-local",),
        ("list-routes-remote",),
        ("destroy",),
        ("failover", "vm"),
        ("failback", "vm"),
    ),
)
def test_operational_local_config_aliases_parse_identically(
    command_path: tuple[str, ...], tmp_path: Path
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    config_path.touch()
    parsed_paths: list[str] = []

    for option_name in ("--local-config-file", "-c"):
        command = get_command(app)
        for segment in command_path:
            assert isinstance(command, TyperGroup)
            command = command.commands[segment]
        with command.make_context(
            command_path[-1],
            [option_name, str(config_path)],
        ) as context:
            parsed_paths.append(context.params["local_config_file"])

    assert parsed_paths[0] == parsed_paths[1]
    assert Path(parsed_paths[0]) == config_path


def test_root_help_explains_operational_alias_and_positional_file_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    normalized_output = " ".join(unstyle(result.output).split())

    assert result.exit_code == 0
    assert (
        "Use --local-config-file or -c to select a different config for operational commands."
        in normalized_output
    )
    assert (
        "Use positional file arguments for create-config and validate-config." in normalized_output
    )


def test_config_short_alias_is_not_root_or_validate_config_syntax(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    config_path.touch()

    validate_result = CliRunner().invoke(
        app,
        ["validate-config", "-c", str(config_path)],
    )
    root_result = CliRunner().invoke(
        app,
        ["-c", str(config_path), "status"],
    )

    validate_output = unstyle(validate_result.output)
    root_output = unstyle(root_result.output)
    assert validate_result.exit_code == 2
    assert "No such option: -c" in validate_output
    assert root_result.exit_code == 2
    assert "No such option: -c" in root_output
