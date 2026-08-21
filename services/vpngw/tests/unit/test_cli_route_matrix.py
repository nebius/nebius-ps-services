from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
import typer
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
from nebius_vpngw.deploy.route_manager import RouteManagementError


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


def test_applicability_registry_covers_every_executable_leaf() -> None:
    assert set(_COMMAND_APPLICABILITY) == {
        "create-config",
        "configure-vm-ha",
        "prep-network",
        "validate-config",
        "apply",
        "status",
        "set-vm-ha-mtls",
        "vm-ha-rearm",
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
        "prep-network",
        "validate-config",
        "apply",
        "status",
        "list-routes-local",
        "list-routes-remote",
        "create-from-peer-config",
    }
    ordinary_only = {"configure-vm-ha", "restart-tunnel", "destroy"}
    vm_ha_only = {
        "set-vm-ha-mtls",
        "vm-ha-rearm",
        "failover vm",
        "failback vm",
    }
    ordinary_bgp_only = {"failover tunnel", "failback tunnel"}

    allowed = (
        command in all_topologies
        or (command in ordinary_only and not vm_ha)
        or (command in vm_ha_only and vm_ha)
        or (command in ordinary_bgp_only and not vm_ha and mode == "bgp")
        or (command == "add-routes-local" and (not vm_ha or mode == "bgp"))
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
        ("destroy", True, "bgp", False),
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
        ("set-vm-ha-mtls", False, "bgp", False),
        ("set-vm-ha-mtls", True, "static", True),
        ("vm-ha-rearm", False, "static", False),
        ("vm-ha-rearm", True, "bgp", True),
        ("add-routes-local", False, "static", True),
        ("add-routes-local", False, "bgp", True),
        ("add-routes-local", True, "static", False),
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
    "argv",
    (
        ("destroy", "--yes"),
        ("restart-tunnel", "all"),
        ("failover", "tunnel", "tunnel-a"),
        ("failback", "tunnel", "tunnel-a"),
    ),
)
def test_vm_ha_ordinary_only_commands_fail_before_external_effects(
    argv: tuple[str, ...],
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
    assert "not supported for explicit VM HA" in " ".join(result.output.split())


@pytest.mark.parametrize(
    "argv",
    (
        ("failover", "tunnel", "tunnel-a"),
        ("failback", "tunnel", "tunnel-a"),
    ),
)
def test_static_tunnel_failover_commands_fail_before_ssh(
    argv: tuple[str, ...],
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

    assert result.exit_code != 0
    assert "supported only for BGP connections" in " ".join(result.output.split())


@pytest.mark.parametrize(
    "argv",
    (
        ("set-vm-ha-mtls", "--dry-run"),
        ("vm-ha-rearm",),
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
    assert "requires an explicit gateway_group.vm_ha" in " ".join(result.output.split())


def test_public_command_parameter_and_alias_manifest_is_exact() -> None:
    expected = {
        ("create-config",): (("config_file",), ("--force", "-f"), ("--interactive",), ("--no-interactive",)),
        ("configure-vm-ha",): (("--local-config-file", "-c"), ("--output", "-o"), ("--force", "-f"), ("--zone",)),
        ("prep-network",): (("--local-config-file", "-c"), ("--zone",)),
        ("validate-config",): (("config_file",),),
        ("apply",): (("--local-config-file",), ("--recreate-gw", "--no-recreate-gw"), ("--sa",), ("--project-id",), ("--zone",), ("--dry-run",), ("--approve-vm-ha-migration",), ("--recover-vm-ha-migration",), ("--replace-failed-vm-ha-passive",)),
        ("status",): (("--local-config-file",), ("--project-id",), ("--zone",)),
        ("set-vm-ha-mtls",): (("--local-config-file",), ("--dry-run",), ("--approve",)),
        ("vm-ha-rearm",): (("--local-config-file",),),
        ("add-routes-local",): (("--local-config-file",), ("--project-id",), ("--summarize",), ("--swap-route-table",), ("--yes", "-y")),
        ("list-routes-local",): (("--local-config-file",), ("--project-id",)),
        ("list-routes-remote",): (("--local-config-file",), ("--connection",)),
        ("restart-tunnel",): (("tunnel_name",), ("--local-config-file", "-c")),
        ("create-from-peer-config",): (("config_file",), ("--local-config-file", "-c"), ("--peer-config-file",), ("--force", "-f")),
        ("destroy",): (("--local-config-file",), ("--project-id",), ("--zone",), ("--yes", "-y")),
        ("failover", "vm"): (("--local-config-file",),),
        ("failover", "tunnel"): (("tunnel_name",), ("--local-config-file", "-c")),
        ("failback", "vm"): (("--local-config-file",),),
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
