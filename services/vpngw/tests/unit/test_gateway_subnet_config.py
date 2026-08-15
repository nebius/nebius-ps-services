from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from nebius_vpngw.agent.main import (
    VMHACheckpointFileStore,
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
    install_vm_ha_cold_start_guard,
    require_vm_ha_runtime_prerequisites,
    vm_ha_status,
)
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ControllerAction,
    ControllerCheckpoint,
    HAState,
    OwnershipContext,
)
from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_manager import VMManager
from nebius_vpngw.schema import validate_config


def _gateway_group_spec(subnet: dict | None = None) -> GatewayGroupSpec:
    return GatewayGroupSpec(
        name="nebius-vpn-gw",
        instance_count=1,
        region="eu-west1",
        external_ips=[[]],
        vm_spec={"num_nics": 1},
        subnet=subnet or {},
    )


def test_validate_config_accepts_explicit_gateway_subnet_cidr(sample_config: dict) -> None:
    sample_config["gateway_group"]["subnet"] = {
        "name": "edge-gateway-subnet",
        "cidr": "172.16.30.7/24",
        "prefix_length": 28,
    }

    validated = validate_config(sample_config)

    assert validated.gateway_group.subnet.name == "edge-gateway-subnet"
    assert validated.gateway_group.subnet.cidr == "172.16.30.0/24"
    assert validated.gateway_group.subnet.prefix_length == 28


def test_validate_config_accepts_gateway_group_network_id(sample_config: dict) -> None:
    sample_config["gateway_group"]["network_id"] = "vpcnetwork-abc123def456"

    validated = validate_config(sample_config)

    assert validated.gateway_group.network_id == "vpcnetwork-abc123def456"


def test_validate_config_rejects_legacy_vm_spec_network_id(sample_config: dict) -> None:
    sample_config["gateway_group"]["vm_spec"]["network_id"] = "vpcnetwork-abc123def456"

    with pytest.raises(ValidationError):
        validate_config(sample_config)


def test_validate_config_rejects_non_private_or_too_small_gateway_subnet(
    sample_config: dict,
) -> None:
    sample_config["gateway_group"]["subnet"] = {"cidr": "192.0.2.0/29"}

    with pytest.raises(ValidationError):
        validate_config(sample_config)


def test_find_first_free_subnet_cidr_uses_next_pool_when_needed() -> None:
    result = VMManager._find_first_free_subnet_cidr(
        [
            ipaddress.IPv4Network("10.0.0.0/24"),
            ipaddress.IPv4Network("172.16.30.0/24"),
        ],
        [ipaddress.IPv4Network("10.0.0.0/24")],
        prefix_length=24,
    )

    assert result == "172.16.30.0/24"


def test_gateway_subnet_settings_apply_defaults() -> None:
    spec = _gateway_group_spec()

    assert VMManager._gateway_subnet_settings(spec) == {
        "name": "vpngw-subnet",
        "cidr": None,
        "prefix_length": 24,
    }
    assert VMManager._gateway_subnet_name(spec) == "vpngw-subnet"


def test_extract_explicit_subnet_networks_skips_invalid_and_inherited_pools() -> None:
    explicit_subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=False,
                pools=[
                    SimpleNamespace(
                        cidrs=[
                            SimpleNamespace(cidr="172.16.10.0/24"),
                            SimpleNamespace(cidr="2001:db8::/64"),
                            SimpleNamespace(cidr="not-a-cidr"),
                        ]
                    )
                ],
            )
        )
    )
    inherited_subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=True,
                pools=[SimpleNamespace(cidrs=[SimpleNamespace(cidr="172.16.20.0/24")])],
            )
        )
    )

    assert VMManager._extract_explicit_subnet_networks(explicit_subnet) == [
        ipaddress.ip_network("172.16.10.0/24")
    ]
    assert VMManager._extract_explicit_subnet_networks(inherited_subnet) == []


def test_build_cloud_init_includes_ssh_key_and_local_prefixes() -> None:
    manager = VMManager(project_id="project-test", zone="eu-west1")

    rendered = manager._build_cloud_init(
        ssh_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey",
        local_prefixes=["10.0.0.0/16", "172.16.0.0/24"],
    )

    assert "ssh_authorized_keys" in rendered
    assert "10.0.0.0/16" in rendered
    assert "172.16.0.0/24" in rendered
    assert "/usr/local/bin/setup-vpngw-firewall.sh" in rendered
    assert "/usr/local/bin/nebius-vpngw-esp4-preflight.sh" in rendered
    assert "/etc/systemd/system/nebius-vpngw-esp4-preflight.service" in rendered
    assert "/var/lib/nebius-vpngw/esp4-reboot-pending" in rendered
    assert "systemctl start nebius-vpngw-esp4-preflight strongswan-starter frr" in rendered
    assert "  - [ systemctl, start, strongswan-starter ]" not in rendered
    assert "net.ipv4.ip_forward = 1" in rendered
    assert yaml.safe_load(rendered)["package_upgrade"] is True


def test_vm_ha_controller_checkpoint_round_trip_preserves_complete_action_identity(
    tmp_path: Path,
) -> None:
    digests = DigestSet(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64)
    action = ControllerAction(
        kind=ActionKind.RECONCILE_ROUTES,
        operation_id="boot-a:9:reconcile-routes:node-a",
        boot_id="boot-a",
        target_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="epoch-a",
        generation_id="generation-a",
        digests=digests,
        ownership_incarnation=4,
    )
    checkpoint = ControllerCheckpoint(
        sequence=9,
        state=HAState.PROMOTING,
        suspect_since=12.5,
        pending_action=action,
        established_ownership_context=OwnershipContext(
            owner_node_id="node-a",
            allocation_id="allocation-a",
            ownership_epoch="epoch-a",
        ),
        ownership_incarnation=4,
    )

    payload = controller_checkpoint_to_dict(checkpoint)
    assert controller_checkpoint_from_dict(payload) == checkpoint

    store = VMHACheckpointFileStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)
    assert store.load() == checkpoint
    assert (tmp_path / "checkpoint.json").stat().st_mode & 0o777 == 0o600


def test_vm_ha_checkpoint_rejects_partial_pending_action() -> None:
    payload = controller_checkpoint_to_dict(ControllerCheckpoint())
    payload["pending_action"] = {"operation_id": "incomplete"}

    with pytest.raises(ValueError, match="invalid shape"):
        controller_checkpoint_from_dict(payload)


def test_vm_ha_guard_and_default_status_are_fail_closed(tmp_path: Path, monkeypatch) -> None:
    completed = SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("nebius_vpngw.agent.main.subprocess.run", lambda *a, **k: completed)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.acquire_routing_lock",
        lambda blocking=True: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )

    guard = install_vm_ha_cold_start_guard(state_dir=tmp_path, boot_id="boot-a")
    status = vm_ha_status(state_dir=tmp_path)

    assert guard["guard_boot_id"] == "boot-a"
    assert status["guard_boot_id"] == "boot-a"
    assert status["data_plane_mode"] == "blocked"
    assert status["promotion_ready"] is False
    assert status["observed_owner_node_id"] is None
    assert status["state"] == "blocked"
    assert status["reasons"] == ["authoritative-cloud-observation-not-yet-recorded"]
    assert "vm-ha-recover" in status["recovery_action"]
    assert json.loads((tmp_path / "guard.json").read_text())["data_plane_mode"] == "blocked"

    require_vm_ha_runtime_prerequisites(state_dir=tmp_path)
    persisted = status

    persisted.update(state="active", data_plane_mode="active", promotion_ready=True)
    (tmp_path / "status.json").write_text(json.dumps(persisted), encoding="utf-8")
    stale_status = vm_ha_status(state_dir=tmp_path)
    assert stale_status["state"] == "blocked"
    assert stale_status["data_plane_mode"] == "blocked"
    assert stale_status["promotion_ready"] is False


def test_vm_ha_systemd_units_guard_vpn_services_before_controller() -> None:
    systemd = Path(__file__).parents[2] / "src" / "nebius_vpngw" / "systemd"
    guard = (systemd / "nebius-vpngw-vm-ha-guard.service").read_text(encoding="utf-8")
    controller = (systemd / "nebius-vpngw-vm-ha.service").read_text(encoding="utf-8")
    ordering = (systemd / "nebius-vpngw-vm-ha-ordering.conf").read_text(encoding="utf-8")
    fix_routes = (systemd / "nebius-vpngw-fix-routes.service").read_text(encoding="utf-8")

    assert "Before=network-online.target strongswan-starter.service" in guard
    assert "ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in guard
    assert "Requires=nebius-vpngw-vm-ha-guard.service" in controller
    assert (
        "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-preflight" in controller
    )
    assert "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in controller
    assert "Type=notify" in controller
    assert "After=network-online.target nebius-vpngw-vm-ha-guard.service" in controller
    assert "Before=strongswan-starter.service strongswan.service frr.service" in controller
    assert "After=nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha.service" in ordering
    assert "Requires=nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha.service" in ordering
    assert "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-ready" in fix_routes
    assert "ExecStopPost=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in controller
    assert "Restart=on-failure" in controller
    assert "TimeoutStopSec=30" in controller
