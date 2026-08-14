from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from typer.main import get_command
from typer.testing import CliRunner

from nebius_vpngw import vpngw_sa
from nebius_vpngw.cli import (
    _detect_connection_role_overrides,
    _detect_cross_connection_ecmp_warnings,
    _external_ips_assigned,
    _format_ecmp_warning_lines,
    _format_role_override_lines,
    _format_traffic_state,
    _registered_command_name,
    _run_vm_ha_operator_command,
    _select_carrying_tunnel_for_connection,
    _should_prompt_add_routes_after_apply,
    _update_external_ips_in_yaml,
    _vm_ha_activation_blockers,
    _vm_ha_apply_order,
    app,
)
from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
    load_local_config,
)
from nebius_vpngw.deploy.vm_diff import ChangeType, VMDiff
from nebius_vpngw.schema import HARole, RoutingMode

HELP_ENV = {"COLUMNS": "120"}


def test_load_local_config_drops_unset_gateway_group_network_id_placeholder(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["gateway_group"]["network_id"] = "${NETWORK_ID}"
    config_path = tmp_path / "placeholder.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    loaded = load_local_config(config_path)

    assert loaded["gateway_group"]["network_id"] is None


def test_update_external_ips_in_yaml_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "rewrite.config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "gateway_group:",
                '  name: "nebius-vpn-gw"',
                "  instance_count: 1",
                "  external_ips: []",
                '  # network_id: "vpcnetwork-abc123def456"',
                "  subnet:",
                '    name: "vpngw-subnet"',
                "    cidr: null",
                "    prefix_length: 24",
                "",
            ]
        ),
        encoding="utf-8",
    )

    external_ips = [["203.0.113.10"]]

    _update_external_ips_in_yaml(config_path, external_ips)
    first_render = config_path.read_text(encoding="utf-8")

    _update_external_ips_in_yaml(config_path, external_ips)
    second_render = config_path.read_text(encoding="utf-8")

    assert second_render == first_render
    assert '  external_ips:\n    - ["203.0.113.10"]\n' in first_render
    assert '  # network_id: "vpcnetwork-abc123def456"\n  subnet:\n' in first_render


def test_external_ips_assigned_ignores_placeholders() -> None:
    assert not _external_ips_assigned([["${VPNGW_IP}"], []])
    assert _external_ips_assigned([["203.0.113.10"], []])


def _static_route_plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.0.0.0/16"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
        manage_routes=True,
    )


def test_should_prompt_add_routes_after_initial_static_creation() -> None:
    plan = _static_route_plan()
    changes = [
        (
            "nebius-vpn-gw-0",
            VMDiff(
                change_type=ChangeType.SAFE,
                differences=["VM does not exist (will create)"],
                destructive_fields=[],
            ),
        )
    ]

    assert _should_prompt_add_routes_after_apply(plan, changes, recreate_gw=False)
    assert not _should_prompt_add_routes_after_apply(plan, changes, recreate_gw=True)


def test_apply_prints_add_routes_hint_after_initial_static_creation(tmp_path: Path) -> None:
    config_path = tmp_path / "static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    changes = [
        (
            "nebius-vpn-gw-0",
            VMDiff(
                change_type=ChangeType.SAFE,
                differences=["VM does not exist (will create)"],
                destructive_fields=[],
            ),
        )
    ]

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return changes

        def ensure_group(self, spec, recreate=False, local_prefixes=None) -> dict[str, str]:
            return {}

    class FakeSSHPush:
        def deactivate_vm_ha(self, target, cfg) -> bool:
            return False

        def push_config_and_reload(self, target, inst_cfg, cfg, *, fail_closed=False) -> None:
            return None

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert "Apply completed successfully." in result.stdout
    assert "IMPORTANT: For static routing, run:" in result.stdout
    assert "add-routes-local --local-config-file" in result.stdout
    assert "<your-config.yaml>" not in result.stdout


def test_apply_waits_for_esp4_ready_before_config_push(tmp_path: Path) -> None:
    config_path = tmp_path / "static.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "tenant_id": "tenant-test",
        "project_id": "project-test",
        "region_id": "eu-west1",
        "gateway_group": {"vm_spec": {}},
        "gateway": {"local_prefixes": ["10.0.0.0/16"]},
        "defaults": {"routing": {"mode": "static"}},
    }
    plan = _static_route_plan()
    pushed_targets: list[str] = []

    pending_health = {
        "reachable": True,
        "cloud_init_complete": True,
        "strongswan_installed": True,
        "frr_installed": True,
        "agent_installed": False,
        "esp4_ready": False,
        "esp4_reboot_pending": True,
        "message": "ESP4/kernel update prepared; waiting for gateway reboot",
    }
    ready_health = {
        "reachable": True,
        "cloud_init_complete": True,
        "strongswan_installed": True,
        "frr_installed": True,
        "agent_installed": True,
        "esp4_ready": True,
        "esp4_reboot_pending": False,
        "message": "VM ready",
    }
    health_results = [pending_health, pending_health, ready_health]

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None) -> dict[str, str]:
            return {"nebius-vpn-gw-0": "203.0.113.10"}

        def wait_for_vm_network(self, vm_name, vm_ip, timeout=180) -> bool:
            return True

        def check_vm_health(self, vm_name, vm_ip) -> dict[str, object]:
            return health_results.pop(0)

    class FakeSSHPush:
        def deactivate_vm_ha(self, target, cfg) -> bool:
            return False

        def push_config_and_reload(self, target, inst_cfg, cfg, *, fail_closed=False) -> None:
            pushed_targets.append(target)

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
        patch("time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert pushed_targets == ["203.0.113.10"]
    assert "waiting for reboot" in result.stdout
    assert "Config push gate passed" in result.stdout


def test_prep_network_allows_missing_peer_psk_placeholders(
    tmp_path: Path,
    sample_config: dict,
) -> None:
    sample_config["connections"][0]["tunnels"][0]["psk"] = "${GCP_TUNNEL_1_PSK}"
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-2",
            "gateway_instance_index": 0,
            "ha_role": "passive",
            "remote_public_ip": "198.51.100.11",
            "psk": "${GCP_TUNNEL_2_PSK}",
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    config_path = tmp_path / "prep-network.config.yaml"
    config_path.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def prepare_network(
            self,
            spec: GatewayGroupSpec,
            *,
            allocate_ips: bool = True,
            desired_external_ips: list[list[str]] | None = None,
        ) -> list[list[str]]:
            assert spec.name == "nebius-vpn-gw"
            assert spec.instance_count == 1
            assert allocate_ips is True
            assert desired_external_ips == []
            return [["203.0.113.10"]]

    with (
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli._update_external_ips_in_yaml", return_value=None),
    ):
        result = CliRunner().invoke(app, ["prep-network", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert "Reserved public IPs:" in result.stdout
    assert "203.0.113.10" in result.stdout


def test_select_carrying_tunnel_is_scoped_per_connection() -> None:
    tunnel_bgp_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "169.254.10.2",
            "conn1-passive": "169.254.11.2",
            "conn2-active": "169.254.12.2",
            "conn2-passive": "169.254.13.2",
        }
    }
    tunnel_role_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "active",
            "conn1-passive": "passive",
            "conn2-active": "active",
            "conn2-passive": "passive",
        }
    }
    tunnel_connection_map = {
        "nebius-vpn-gw-0": {
            "conn1-active": "conn1",
            "conn1-passive": "conn1",
            "conn2-active": "conn2",
            "conn2-passive": "conn2",
        }
    }
    tunnel_statuses = {
        "conn1-active": "ESTABLISHED",
        "conn1-passive": "ESTABLISHED",
        "conn2-active": "ESTABLISHED",
        "conn2-passive": "ESTABLISHED",
    }
    bgp_states = {
        "169.254.10.2": "Established",
        "169.254.11.2": "Established",
        "169.254.12.2": "Established",
        "169.254.13.2": "Established",
    }
    tunnel_names = list(tunnel_statuses.keys())

    assert (
        _select_carrying_tunnel_for_connection(
            "nebius-vpn-gw-0",
            "conn1",
            tunnel_names,
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
            tunnel_role_map,
            tunnel_connection_map,
        )
        == "conn1-active"
    )
    assert (
        _select_carrying_tunnel_for_connection(
            "nebius-vpn-gw-0",
            "conn2",
            tunnel_names,
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
            tunnel_role_map,
            tunnel_connection_map,
        )
        == "conn2-active"
    )


def test_format_traffic_state_distinguishes_active_path_standby_and_admin_down() -> None:
    tunnel_bgp_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "169.254.10.2",
            "conn-passive": "169.254.11.2",
        }
    }
    tunnel_statuses = {
        "conn-active": "ESTABLISHED",
        "conn-passive": "ESTABLISHED",
    }
    bgp_states = {
        "169.254.10.2": "Idle (Admin)",
        "169.254.11.2": "Established",
    }

    assert (
        _format_traffic_state(
            "nebius-vpn-gw-0",
            "conn-passive",
            "conn-passive",
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
        )
        == "[green]active path[/green]"
    )
    assert (
        _format_traffic_state(
            "nebius-vpn-gw-0",
            "conn-active",
            "conn-passive",
            tunnel_statuses,
            bgp_states,
            tunnel_bgp_map,
        )
        == "[red]admin down[/red]"
    )


def test_detect_connection_role_overrides_reports_manual_failover() -> None:
    tunnel_bgp_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "169.254.10.2",
            "conn-passive": "169.254.11.2",
        }
    }
    tunnel_role_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "active",
            "conn-passive": "passive",
        }
    }
    tunnel_connection_map = {
        "nebius-vpn-gw-0": {
            "conn-active": "gcp-ha-vpn",
            "conn-passive": "gcp-ha-vpn",
        }
    }
    tunnel_statuses = {
        "conn-active": "ESTABLISHED",
        "conn-passive": "ESTABLISHED",
    }
    bgp_states = {
        "169.254.10.2": "Idle (Admin)",
        "169.254.11.2": "Established",
    }

    overrides = _detect_connection_role_overrides(
        "nebius-vpn-gw-0",
        ["conn-active", "conn-passive"],
        tunnel_statuses,
        bgp_states,
        tunnel_bgp_map,
        tunnel_role_map,
        tunnel_connection_map,
    )

    assert overrides == [
        {
            "connection": "gcp-ha-vpn",
            "configured_active_tunnel": "conn-active",
            "selected_tunnel": "conn-passive",
            "reason": "manual failover",
            "detail": "configured active tunnel BGP is administratively down",
        }
    ]

    warning_lines = _format_role_override_lines({"nebius-vpn-gw-0": overrides})
    assert "Configured roles remain unchanged by design." in warning_lines[1]
    assert "Current traffic path: conn-passive" in "\n".join(warning_lines)


def test_detect_cross_connection_ecmp_warnings_for_active_paths() -> None:
    routes = {
        "10.10.0.0/24": [
            {"peerId": "169.254.10.2", "multipath": True},
            {"peerId": "169.254.12.2", "multipath": True},
            {"peerId": "169.254.11.2", "multipath": False},
            {"peerId": "169.254.13.2", "multipath": False},
        ]
    }
    peer_connection_map = {
        "169.254.10.2": "gcp-ha-vpn",
        "169.254.11.2": "gcp-ha-vpn",
        "169.254.12.2": "gcp-ha-vpn2",
        "169.254.13.2": "gcp-ha-vpn2",
    }
    peer_tunnel_map = {
        "169.254.10.2": "nebius-204-12-170-147-tunnel-1",
        "169.254.11.2": "nebius-204-12-170-147-tunnel-2",
        "169.254.12.2": "gcp-ha-vpn2-tunnel-1",
        "169.254.13.2": "gcp-ha-vpn2-tunnel-2",
    }
    peer_role_map = {
        "169.254.10.2": "active",
        "169.254.11.2": "passive",
        "169.254.12.2": "active",
        "169.254.13.2": "passive",
    }

    warnings = _detect_cross_connection_ecmp_warnings(
        routes,
        peer_connection_map,
        peer_tunnel_map,
        peer_role_map,
    )

    assert len(warnings) == 1
    assert warnings[0]["prefix"] == "10.10.0.0/24"
    assert warnings[0]["connections"] == ["gcp-ha-vpn", "gcp-ha-vpn2"]


def test_format_ecmp_warning_lines_groups_prefix_and_tunnels() -> None:
    warning_lines = _format_ecmp_warning_lines(
        {
            "nebius-vpn-gw-0": [
                {
                    "prefix": "10.10.0.0/24",
                    "entries": [
                        {
                            "connection": "gcp-ha-vpn",
                            "tunnel": "nebius-204-12-170-147-tunnel-1",
                            "peer_ip": "169.254.10.2",
                        },
                        {
                            "connection": "gcp-ha-vpn2",
                            "tunnel": "gcp-ha-vpn2-tunnel-1",
                            "peer_ip": "169.254.12.2",
                        },
                    ],
                }
            ]
        }
    )

    assert "Gateway VM: nebius-vpn-gw-0" in warning_lines
    assert "  Overlapping prefix: 10.10.0.0/24" in warning_lines
    assert "  Active tunnels carrying this prefix:" in warning_lines
    assert "    - nebius-204-12-170-147-tunnel-1 (connection: gcp-ha-vpn)" in warning_lines
    assert "    - gcp-ha-vpn2-tunnel-1 (connection: gcp-ha-vpn2)" in warning_lines


def test_cli_help_command_order() -> None:
    command_names = [_registered_command_name(command) for command in app.registered_commands]

    assert command_names[:15] == [
        "create-config",
        "prep-network",
        "validate-config",
        "apply",
        "status",
        "vm-ha-recover",
        "vm-ha-failback",
        "add-routes-local",
        "list-routes-local",
        "list-routes-remote",
        "restart-tunnel",
        "failover",
        "failback",
        "create-from-peer-config",
        "destroy",
    ]


def test_vm_ha_apply_order_is_passive_first_and_non_ha_order_is_unchanged() -> None:
    active = SimpleNamespace(vm_ha_node=SimpleNamespace(role=SimpleNamespace(value="active")))
    passive = SimpleNamespace(vm_ha_node=SimpleNamespace(role=SimpleNamespace(value="passive")))
    ha_plan = SimpleNamespace(vm_ha=object(), iter_instance_configs=lambda: iter([active, passive]))
    ordinary_plan = SimpleNamespace(
        vm_ha=None, iter_instance_configs=lambda: iter([active, passive])
    )

    assert _vm_ha_apply_order(ha_plan) == [passive, active]
    assert _vm_ha_apply_order(ordinary_plan) == [active, passive]


def test_vm_ha_apply_stops_before_external_mutation_when_runtime_is_blocked(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "vm-ha-blocked.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    plan = SimpleNamespace(vm_ha=object(), validate=lambda: None)

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value={}),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch(
            "nebius_vpngw.cli._vm_ha_activation_blockers",
            return_value=("authoritative-runtime-unavailable",),
        ),
        patch("nebius_vpngw.cli.VMManager") as manager,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "BLOCKED before external mutation" in result.stdout
    manager.assert_not_called()


def test_vm_ha_apply_rejects_missing_host_trust_before_vm_manager(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    plan = SimpleNamespace(vm_ha=object(), validate=lambda: None)
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value={}),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch("nebius_vpngw.cli.VMManager") as manager,
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    assert result.exit_code == 1
    assert "SSH trust preflight failed before external mutation" in result.stdout
    manager.assert_not_called()


@pytest.mark.parametrize("failing_stage_role", [None, "passive", "active"])
def test_vm_ha_apply_delivers_credentials_passive_first_and_never_activates_partial_stage(
    tmp_path: Path,
    failing_stage_role: str | None,
) -> None:
    config_path = tmp_path / "vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    binding = object()
    generation = SimpleNamespace(generation_id="a" * 64)
    active = SimpleNamespace(
        hostname="gateway-0",
        external_ip="",
        vm_ha_node=SimpleNamespace(
            node_id="node-a", role=SimpleNamespace(value="active"), credential_sources=object()
        ),
        vm_ha_generation=generation,
    )
    passive = SimpleNamespace(
        hostname="gateway-1",
        external_ip="",
        vm_ha_node=SimpleNamespace(
            node_id="node-b", role=SimpleNamespace(value="passive"), credential_sources=object()
        ),
        vm_ha_generation=generation,
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        gateway_group=SimpleNamespace(region="eu-west1"),
        gateway={},
        manage_routes=False,
        should_manage_routes=lambda: False,
        validate=lambda: None,
        iter_instance_configs=lambda: iter([active, passive]),
    )
    local_cfg = {"gateway_group": {"vm_spec": {}}}
    observed: list[tuple[str, str, object]] = []
    source_bundles: list[object] = []

    class BoundResult(dict):
        vm_ha_runtime_binding = binding

    class FakeVMManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def check_changes(self, spec) -> list[tuple[str, VMDiff]]:
            return []

        def ensure_group(self, spec, recreate=False, local_prefixes=None):
            return BoundResult({"gateway-0": "203.0.113.10", "gateway-1": "203.0.113.11"})

        def wait_for_vm_network(self, vm_name, vm_ip, timeout=180) -> bool:
            return True

        def check_vm_health(self, vm_name, vm_ip) -> dict[str, object]:
            return {
                "reachable": True,
                "cloud_init_complete": True,
                "strongswan_installed": True,
                "frr_installed": True,
                "agent_installed": True,
                "esp4_ready": True,
                "esp4_reboot_pending": False,
                "message": "ready",
            }

    class FakeSSHPush:
        def stage_vm_ha_config(self, target, inst_cfg, cfg, *, runtime_binding, credential_sources):
            observed.append(("stage", inst_cfg.vm_ha_node.role.value, runtime_binding))
            source_bundles.append(credential_sources)
            if inst_cfg.vm_ha_node.role.value == failing_stage_role:
                raise RuntimeError("injected credential staging failure")
            return SimpleNamespace(
                node_id=inst_cfg.vm_ha_node.node_id,
                generation_id="a" * 64,
                configuration_digest="a" * 64,
                static_routes_digest="b" * 64,
                bgp_policy_digest="c" * 64,
            )

        def push_config_and_reload(
            self, target, inst_cfg, cfg, *, staged_receipt, runtime_binding
        ) -> None:
            observed.append(("activate", inst_cfg.vm_ha_node.role.value, runtime_binding))

    with (
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("nebius_vpngw.cli._ensure_authentication", return_value="token"),
        patch("nebius_vpngw.cli._vm_ha_activation_blockers", return_value=()),
        patch(
            "nebius_vpngw.cli.require_explicit_known_hosts_file",
            return_value=tmp_path / "known_hosts",
        ),
        patch("nebius_vpngw.cli.VMManager", FakeVMManager),
        patch("nebius_vpngw.cli.SSHPush", return_value=FakeSSHPush()),
    ):
        result = CliRunner().invoke(app, ["apply", "--local-config-file", str(config_path)])

    if failing_stage_role is None:
        assert result.exit_code == 0, result.stdout
        assert [(phase, role) for phase, role, _ in observed] == [
            ("stage", "passive"),
            ("stage", "active"),
            ("activate", "passive"),
            ("activate", "active"),
        ]
    else:
        assert result.exit_code == 1
        assert all(phase != "activate" for phase, _, _ in observed)
    expected_sources = [passive.vm_ha_node.credential_sources]
    if failing_stage_role != "passive":
        expected_sources.append(active.vm_ha_node.credential_sources)
    assert source_bundles == expected_sources
    assert all(item is binding for _, _, item in observed)


def test_vm_ha_activation_has_no_static_runtime_blockers_after_complete_wiring() -> None:
    assert _vm_ha_activation_blockers() == ()


def test_vm_ha_service_account_requires_verified_non_broad_roles(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_ensure(*args, **kwargs):
        observed["roles"] = kwargs["role_ids"]
        observed["strict"] = kwargs["strict_role_grants"]
        return "token"

    monkeypatch.setattr(vpngw_sa, "ensure_service_account_and_token", fake_ensure)

    assert (
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("compute.editor", "vpc.editor"),
        )
        == "token"
    )
    assert observed["roles"] == (
        "compute.editor",
        "vpc.editor",
    )
    assert observed["strict"] is True

    with pytest.raises(ValueError, match="reviewed allowlist"):
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("roles/editor",),
        )

    with pytest.raises(ValueError, match="requires every reviewed"):
        vpngw_sa.ensure_vm_ha_service_account_and_token(
            "gateway-ha",
            "tenant",
            "project",
            "region",
            verified_role_ids=("compute.editor",),
        )


def test_vm_ha_operator_command_rejects_stale_agent_identity(monkeypatch) -> None:
    generation = SimpleNamespace(generation_id="a" * 64)
    active = SimpleNamespace(
        external_ip="203.0.113.10",
        vm_ha_node=SimpleNamespace(node_id="node-a", role=SimpleNamespace(value="active")),
        vm_ha_generation=generation,
    )
    passive = SimpleNamespace(
        external_ip="203.0.113.11",
        vm_ha_node=SimpleNamespace(node_id="node-b", role=SimpleNamespace(value="passive")),
        vm_ha_generation=generation,
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        iter_instance_configs=lambda: iter([active, passive]),
    )
    local_cfg = {"gateway_group": {"vm_spec": {}}}
    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda _: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-status-v1",
                    "cluster_id": "cluster-a",
                    "node_id": "another-node",
                    "configured_role": "passive",
                    "generation_id": "a" * 64,
                }
            ),
        ),
    )

    with pytest.raises(ValueError, match="stale node identity"):
        _run_vm_ha_operator_command(
            local_config_file=Path("config.yaml"),
            agent_flag="--vm-ha-recover",
        )


def test_each_cli_command_help_renders() -> None:
    runner = CliRunner()
    command_names = [_registered_command_name(command) for command in app.registered_commands]

    for command_name in command_names:
        result = runner.invoke(app, [command_name, "--help"], env=HELP_ENV)
        assert result.exit_code == 0, command_name
        assert "Usage:" in result.stdout


def test_route_and_operator_help_mentions_multi_connection_behavior() -> None:
    click_app = get_command(app)

    list_remote_help = click_app.commands["list-routes-remote"].help or ""
    add_routes_cmd = click_app.commands["add-routes-local"]
    add_routes_help = add_routes_cmd.help or ""
    failover_help = click_app.commands["failover"].params[0].help or ""
    restart_help = click_app.commands["restart-tunnel"].params[0].help or ""

    assert "owning gateway VM" in list_remote_help
    assert "selected connection" in list_remote_help

    assert "--swap-route-table" in add_routes_help
    assert "rollback command" in add_routes_help

    option_help_by_name = {param.name: param.help or "" for param in add_routes_cmd.params}
    assert "rollback command" in option_help_by_name["swap_route_table"]
    assert "Skip the confirmation prompt for" in option_help_by_name["yes"]
    assert "--swap-route-table" in option_help_by_name["yes"]

    assert "multi-connection topologies" in failover_help
    assert "only the owning gateway VM" in restart_help


def test_add_routes_local_swap_route_table_requires_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "swap.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {"project_id": "project-test"}
    plan = _static_route_plan()
    auth_calls = {"count": 0}

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda path: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda cfg, peers: plan)

    def fake_authentication(*, required: bool, show_progress: bool) -> str:
        auth_calls["count"] += 1
        return "token"

    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", fake_authentication)

    result = runner.invoke(
        app,
        [
            "add-routes-local",
            "--local-config-file",
            str(config_path),
            "--swap-route-table",
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "--swap-route-table performs a blue/green subnet route-table cutover" in result.stdout
    assert "Proceed with route-table swap? [y/N]:" in result.stdout
    assert "Aborted. No changes made." in result.stdout
    assert auth_calls["count"] == 0


def test_add_routes_local_swap_route_table_passes_mode_and_rollback_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "swap.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {"project_id": "project-test"}
    plan = _static_route_plan()
    captured: dict[str, object] = {}

    monkeypatch.setattr("nebius_vpngw.cli.load_local_config", lambda path: local_cfg)
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda cfg, peers: plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda *, required, show_progress: "token",
    )

    class FakeRouteManager:
        def __init__(self, project_id, auth_token=None):
            captured["project_id"] = project_id
            captured["auth_token"] = auth_token

        def add_routes(
            self,
            plan_obj,
            local_cfg_obj,
            *,
            summarize: bool = False,
            swap_route_table: bool = False,
            rollback_dir=None,
        ) -> None:
            captured["plan"] = plan_obj
            captured["local_cfg"] = local_cfg_obj
            captured["summarize"] = summarize
            captured["swap_route_table"] = swap_route_table
            captured["rollback_dir"] = rollback_dir

        def ensure_bgp_advertisements_current(self, plan_obj, local_cfg_obj) -> None:
            captured["ensured_bgp"] = (plan_obj, local_cfg_obj)

    monkeypatch.setattr("nebius_vpngw.cli.RouteManager", FakeRouteManager)

    result = runner.invoke(
        app,
        [
            "add-routes-local",
            "--local-config-file",
            str(config_path),
            "--swap-route-table",
        ],
        input="yes\n",
    )

    assert result.exit_code == 0
    assert captured["project_id"] == "project-test"
    assert captured["auth_token"] == "token"
    assert captured["plan"] is plan
    assert captured["local_cfg"] is local_cfg
    assert captured["summarize"] is False
    assert captured["swap_route_table"] is True
    assert captured["rollback_dir"] == config_path.parent / ".nebius-vpngw-rollbacks"
    assert captured["ensured_bgp"] == (plan, local_cfg)


def test_build_ssh_base_cmd_enforces_host_verification(tmp_path: Path) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    ssh_cmd = _build_ssh_base_cmd(tmp_path / "id_ed25519")

    assert "LogLevel=ERROR" in ssh_cmd
    assert "StrictHostKeyChecking=yes" in ssh_cmd
    assert "StrictHostKeyChecking=no" not in ssh_cmd
    assert not any(option.startswith("UserKnownHostsFile=") for option in ssh_cmd)


def test_build_ssh_base_cmd_uses_exact_explicit_known_hosts(tmp_path: Path, monkeypatch) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAAfixture\n", encoding="utf-8")
    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    ssh_cmd = _build_ssh_base_cmd(None)

    assert f"UserKnownHostsFile={known_hosts}" in ssh_cmd
    assert "GlobalKnownHostsFile=none" in ssh_cmd
    assert "KnownHostsCommand=none" in ssh_cmd


def test_build_ssh_base_cmd_rejects_missing_explicit_known_hosts(
    tmp_path: Path, monkeypatch
) -> None:
    from nebius_vpngw.cli import _build_ssh_base_cmd

    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="non-empty readable regular file"):
        _build_ssh_base_cmd(None)


def test_ssh_policy_honors_an_explicit_empty_environment(tmp_path: Path, monkeypatch) -> None:
    from nebius_vpngw.deploy.ssh_policy import build_openssh_base_command

    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "missing"))

    ssh_cmd = build_openssh_base_command(environment={})

    assert not any(option.startswith("UserKnownHostsFile=") for option in ssh_cmd)


def test_restart_tunnel_uses_inline_remote_python_and_shows_real_failure_output(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "restart.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway_group": {
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmd: list[str] = []
    recorded_input = ""

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmd[:] = cmd
        nonlocal recorded_input
        recorded_input = input or ""
        return SimpleNamespace(
            returncode=1,
            stdout="[TunnelMonitor] ❌ Failed to restart tunnel tunnel-1",
            stderr="Warning: Permanently added '203.0.113.10' (ED25519) to the list of known hosts.",
        )

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "tunnel-1", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert recorded_cmd[-1] == "sudo /usr/bin/python3 - --restart-tunnel tunnel-1"
    assert "def _restart_tunnel(tunnel_name: str) -> bool:" in recorded_input
    assert 'print(f"[TunnelMonitor] Failed to restart tunnel {tunnel_name}")' in recorded_input
    assert "\nConnecting to nebius-vpn-gw-0 (203.0.113.10)..." in result.stdout
    assert "\\nConnecting to nebius-vpn-gw-0" not in result.stdout
    assert "[TunnelMonitor] ❌ Failed to restart tunnel tunnel-1" in result.stdout


def test_restart_tunnel_full_reset_also_bounces_matching_bgp_neighbor(tmp_path: Path) -> None:
    config_path = tmp_path / "restart.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
        patch("nebius_vpngw.cli.time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "tunnel-1", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == "sudo /usr/bin/python3 - --restart-tunnel tunnel-1"
    assert recorded_cmds[1][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.10.2 shutdown'"
    )
    assert recorded_cmds[2][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.10.2 shutdown'"
    )
    assert "Resetting matching BGP neighbor(s)" in result.stdout
    assert "Successfully reset tunnel 'tunnel-1'" in result.stdout


def test_restart_tunnel_targets_only_owning_gateway_instance(tmp_path: Path) -> None:
    config_path = tmp_path / "restart-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    }
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        recorded_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
        patch("nebius_vpngw.cli.time.sleep", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "site-b-active", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 0
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert all("203.0.113.10" not in part for cmd in recorded_cmds for part in cmd)
    assert "Connecting to nebius-vpn-gw-1 (203.0.113.20)" in result.stdout
    assert "Connecting to nebius-vpn-gw-0" not in result.stdout


def test_restart_tunnel_fails_fast_when_tunnel_name_is_unknown(tmp_path: Path) -> None:
    config_path = tmp_path / "restart-unknown.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "gateway_instance_index": 0,
                    }
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run") as run_mock,
    ):
        result = CliRunner().invoke(
            app,
            ["restart-tunnel", "missing-tunnel", "--local-config-file", str(config_path)],
        )

    assert result.exit_code == 1
    assert "Tunnel 'missing-tunnel' not found." in result.stdout
    run_mock.assert_not_called()


def test_failover_accepts_enum_routing_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "failover.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                        "inner_local_ip": "169.254.10.1",
                    },
                    {
                        "name": "tunnel-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                        "inner_local_ip": "169.254.11.1",
                    },
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.10.2":{"state":"Idle"},"169.254.11.2":{"state":"Established"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(app, ["failover", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.10.2 shutdown'"
    )
    assert "Failover confirmed" in result.stdout


def test_failover_targets_selected_passive_tunnel_instance_in_multivm_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "failover-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "site-a-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "name": "site-b-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.20.2":{"state":"Idle"},"169.254.21.2":{"state":"Established"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            [
                "failover",
                "site-b-passive",
                "--local-config-file",
                str(config_path),
            ],
        )

    assert result.exit_code == 0
    assert "Failing over connection 'site-b'" in result.stdout
    assert "site-b-active" in result.stdout
    assert "site-b-passive" in result.stdout
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'neighbor 169.254.20.2 shutdown'"
    )


def test_failback_accepts_enum_routing_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "failback.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 1,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "tunnel-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "tunnel-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            }
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            )
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.10.2":{"state":"Established"},"169.254.11.2":{"state":"Idle"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(app, ["failback", "--local-config-file", str(config_path)])

    assert result.exit_code == 0
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.10.2 shutdown'"
    )
    assert "Failback confirmed" in result.stdout


def test_failback_targets_selected_active_tunnel_instance_in_multivm_config(tmp_path: Path) -> None:
    config_path = tmp_path / "failback-multivm.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    local_cfg = {
        "gateway": {"local_asn": 65010},
        "defaults": {"routing": {"mode": RoutingMode.BGP}},
        "gateway_group": {
            "name": "nebius-vpn-gw",
            "instance_count": 2,
            "vm_spec": {},
        },
        "connections": [
            {
                "name": "site-a",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-a-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "site-a-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "site-b",
                "routing_mode": RoutingMode.BGP,
                "tunnels": [
                    {
                        "name": "site-b-active",
                        "ha_role": HARole.ACTIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "name": "site-b-passive",
                        "ha_role": HARole.PASSIVE,
                        "gateway_instance_index": 1,
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=2,
            region="eu-west1",
            external_ips=[],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="gateway: {}\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="gateway: {}\n",
            ),
        ],
    )
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        recorded_cmds.append(cmd)
        if "show bgp ipv4 unicast summary json" in cmd[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ipv4Unicast":{"peers":{"169.254.20.2":{"state":"Established"},"169.254.21.2":{"state":"Idle"}}}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("nebius_vpngw.cli._resolve_local_config", return_value=config_path),
        patch("nebius_vpngw.cli.load_local_config", return_value=local_cfg),
        patch("nebius_vpngw.cli.merge_with_peer_configs", return_value=plan),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            app,
            [
                "failback",
                "site-b-active",
                "--local-config-file",
                str(config_path),
            ],
        )

    assert result.exit_code == 0
    assert "restore site-b-active" in result.stdout
    assert all("203.0.113.20" in cmd[-2] for cmd in recorded_cmds)
    assert recorded_cmds[0][-1] == (
        "sudo vtysh -c 'configure terminal' -c 'router bgp 65010' "
        "-c 'no neighbor 169.254.20.2 shutdown'"
    )
