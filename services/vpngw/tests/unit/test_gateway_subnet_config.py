from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent.main import (
    VMHACheckpointFileStore,
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
    install_vm_ha_cold_start_guard,
    require_vm_ha_current_boot_materialization,
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
    RepairAttempt,
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


def test_gateway_network_auto_discovery_reports_once_without_caching_sdk_reads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nebius.api.nebius.vpc import v1 as vpc_v1

    network = SimpleNamespace(
        metadata=SimpleNamespace(id="network-default", name="default-network")
    )
    get_by_name = Mock(return_value=SimpleNamespace(wait=lambda: network))
    network_client = SimpleNamespace(get_by_name=get_by_name)
    subnet_client = object()
    monkeypatch.setattr(vpc_v1, "NetworkServiceClient", lambda _client: network_client)
    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda _client: subnet_client)
    manager = VMManager(project_id="project-test", zone="eu-west1")
    spec = _gateway_group_spec()

    silent = manager._resolve_gateway_network(object(), spec)
    assert silent[1:] == ("network-default", "default-network", subnet_client)
    assert capsys.readouterr().out == ""

    first = manager._resolve_gateway_network(object(), spec, report_selection=True)
    second = manager._resolve_gateway_network(object(), spec, report_selection=True)

    assert first[1:] == ("network-default", "default-network", subnet_client)
    assert second[1:] == first[1:]
    assert get_by_name.call_count == 3
    output = capsys.readouterr().out
    assert (
        output.count("[VMManager] No gateway_group.network_id in YAML, auto-discovering network...")
        == 1
    )
    assert output.count("[VMManager] Found default-network, using it") == 1


def test_explicit_gateway_network_reports_once_without_caching_sdk_reads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nebius.api.nebius.vpc import v1 as vpc_v1

    network = SimpleNamespace(
        metadata=SimpleNamespace(id="network-explicit", name="custom-network")
    )
    get_network = Mock(return_value=SimpleNamespace(wait=lambda: network))
    network_client = SimpleNamespace(get=get_network)
    subnet_client = object()
    monkeypatch.setattr(vpc_v1, "NetworkServiceClient", lambda _client: network_client)
    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda _client: subnet_client)
    manager = VMManager(project_id="project-test", zone="eu-west1")
    spec = _gateway_group_spec()
    spec.network_id = "network-explicit"

    first = manager._resolve_gateway_network(object(), spec, report_selection=True)
    second = manager._resolve_gateway_network(object(), spec, report_selection=True)

    assert first[1:] == ("network-explicit", "custom-network", subnet_client)
    assert second[1:] == first[1:]
    assert get_network.call_count == 2
    assert (
        capsys.readouterr().out.count("[VMManager] Using network from YAML: network-explicit") == 1
    )


def test_existing_instance_progress_matches_recreate_mode() -> None:
    assert VMManager._existing_instances_message(0, recreate=False) == (
        "[VMManager] No existing VMs found"
    )
    assert VMManager._existing_instances_message(2, recreate=False) == (
        "[VMManager] Found 2 existing VM(s)"
    )
    assert VMManager._existing_instances_message(2, recreate=True) == (
        "[VMManager] Found 2 existing VM(s) for recreation"
    )


def test_network_selection_reporting_is_owned_by_provisioning_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    subnet = SimpleNamespace(metadata=SimpleNamespace(id="subnet-1"))
    subnet_client = SimpleNamespace(
        list_by_network=Mock(return_value=SimpleNamespace(wait=lambda: SimpleNamespace(items=[])))
    )
    resolver = Mock(
        return_value=(
            SimpleNamespace(metadata=SimpleNamespace(id="network-1")),
            "network-1",
            "default-network",
            subnet_client,
        )
    )
    manager = VMManager(project_id="project-test", zone="eu-west1")
    spec = _gateway_group_spec()
    monkeypatch.setattr(manager, "_resolve_gateway_network", resolver)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.route_manager.RouteManager.resolve_vm_ha_route_targets",
        lambda *_args, **_kwargs: (),
    )

    manager._resolve_vm_ha_route_targets(client, spec, ["10.0.0.0/8"])

    resolver.assert_called_once_with(client, spec)

    resolver.reset_mock()
    monkeypatch.setattr(manager, "_find_gateway_subnet", lambda *_args: subnet)
    monkeypatch.setattr(
        manager,
        "_validate_existing_gateway_subnet",
        lambda *_args: ipaddress.ip_network("172.16.30.0/28"),
    )

    assert manager._ensure_vpngw_subnet(client, spec) == "subnet-1"
    resolver.assert_called_once_with(client, spec, report_selection=True)


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
    assert "/usr/sbin/sshd -t" in rendered
    assert "systemctl restart ssh.socket" in rendered
    assert "ss -H -lnt sport = :22 | grep -q ." in rendered
    assert "[ systemctl, reload, ssh ]" not in rendered
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
        takeover_fence_required=True,
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


def test_vm_ha_controller_checkpoint_round_trip_preserves_repair_budget() -> None:
    digests = DigestSet(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64)
    action = ControllerAction(
        kind=ActionKind.REPAIR_LOCAL_DATAPLANE,
        operation_id="boot-a:4:repair-local-dataplane:node-a",
        boot_id="boot-a",
        target_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="epoch-a",
        generation_id="generation-a",
        digests=digests,
        repair_deadline_at=15.0,
        repair_reasons=("bgp-not-ready",),
    )
    attempt = RepairAttempt(
        operation_id=action.operation_id,
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="epoch-a",
        ownership_incarnation=0,
        generation_id="generation-a",
        boot_id="boot-a",
        failure_fingerprint=("bgp-not-ready",),
        started_at=10.0,
        deadline_at=15.0,
    )
    checkpoint = ControllerCheckpoint(
        sequence=4,
        state=HAState.REPAIRING,
        pending_action=action,
        repair_attempt=attempt,
    )

    assert controller_checkpoint_from_dict(controller_checkpoint_to_dict(checkpoint)) == checkpoint


def test_vm_ha_checkpoint_rejects_partial_pending_action() -> None:
    payload = controller_checkpoint_to_dict(ControllerCheckpoint())
    payload["pending_action"] = {"operation_id": "incomplete"}

    with pytest.raises(ValueError, match="invalid shape"):
        controller_checkpoint_from_dict(payload)


def test_vm_ha_checkpoint_rejects_non_boolean_transfer_confirmation() -> None:
    payload = controller_checkpoint_to_dict(ControllerCheckpoint())
    payload["transfer_continuity"] = {
        "allocation_id": "allocation-a",
        "attach_operation_id": "boot-a:1:attach-candidate:node-b",
        "candidate_node_id": "node-b",
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "former_owner_node_id": "node-a",
        "generation_id": "generation-a",
        "ownership_confirmed": "false",
        "ownership_incarnation": 3,
        "post_attach_revision": None,
        "pre_attach_revision": "41",
    }

    with pytest.raises(ValueError, match="ownership_confirmed"):
        controller_checkpoint_from_dict(payload)


def test_vm_ha_v1_late_transfer_migration_invalidates_a_new_incarnation() -> None:
    digests = DigestSet(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64)
    action = ControllerAction(
        kind=ActionKind.RECONCILE_ROUTES,
        operation_id="boot-a:9:reconcile-routes:node-b",
        boot_id="boot-a",
        target_node_id="node-b",
        allocation_id="allocation-a",
        ownership_epoch="42",
        generation_id="generation-a",
        digests=digests,
        ownership_incarnation=3,
    )
    payload = controller_checkpoint_to_dict(
        ControllerCheckpoint(
            sequence=9,
            state=HAState.PROMOTING,
            pending_action=action,
            ownership_continuity_invalidated=False,
            ownership_incarnation=3,
        )
    )
    payload["schema"] = "nebius-vpngw/vm-ha-controller-checkpoint-v1"
    payload.pop("transfer_continuity")
    payload.pop("repair_attempt")
    assert isinstance(payload["pending_action"], dict)
    payload["pending_action"].pop("takeover_fence_required")
    payload["pending_action"].pop("repair_deadline_at")
    payload["pending_action"].pop("repair_reasons")

    migrated = controller_checkpoint_from_dict(payload)

    assert migrated.state is HAState.BLOCKED
    assert migrated.pending_action is None
    assert migrated.ownership_continuity_invalidated is True
    assert migrated.ownership_incarnation == 4


def test_vm_ha_v2_checkpoint_preserves_strict_legacy_takeover_fence() -> None:
    digests = DigestSet(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64)
    action = ControllerAction(
        kind=ActionKind.RECONCILE_ROUTES,
        operation_id="boot-a:9:reconcile-routes:node-a",
        boot_id="boot-a",
        target_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="42",
        generation_id="generation-a",
        digests=digests,
        ownership_incarnation=3,
        takeover_fence_required=False,
    )
    payload = controller_checkpoint_to_dict(
        ControllerCheckpoint(
            sequence=9,
            state=HAState.PROMOTING,
            pending_action=action,
            ownership_incarnation=3,
        )
    )
    payload["schema"] = "nebius-vpngw/vm-ha-controller-checkpoint-v2"
    payload.pop("repair_attempt")
    assert isinstance(payload["pending_action"], dict)
    payload["pending_action"].pop("takeover_fence_required")
    payload["pending_action"].pop("repair_deadline_at")
    payload["pending_action"].pop("repair_reasons")

    migrated = controller_checkpoint_from_dict(payload)

    assert migrated.pending_action is not None
    assert migrated.pending_action.takeover_fence_required is True


def test_vm_ha_v3_checkpoint_preserves_explicit_takeover_fence() -> None:
    digests = DigestSet(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64)
    action = ControllerAction(
        kind=ActionKind.RECONCILE_ROUTES,
        operation_id="boot-a:9:reconcile-routes:node-a",
        boot_id="boot-a",
        target_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="42",
        generation_id="generation-a",
        digests=digests,
        ownership_incarnation=3,
        takeover_fence_required=False,
    )
    expected = ControllerCheckpoint(
        sequence=9,
        state=HAState.PROMOTING,
        pending_action=action,
        ownership_incarnation=3,
    )
    payload = controller_checkpoint_to_dict(expected)
    payload["schema"] = "nebius-vpngw/vm-ha-controller-checkpoint-v3"
    payload.pop("repair_attempt")
    assert isinstance(payload["pending_action"], dict)
    payload["pending_action"].pop("repair_deadline_at")
    payload["pending_action"].pop("repair_reasons")

    assert controller_checkpoint_from_dict(payload) == expected


def test_vm_ha_guard_and_default_status_are_fail_closed(tmp_path: Path, monkeypatch) -> None:
    completed = SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("nebius_vpngw.agent.main.subprocess.run", lambda *a, **k: completed)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.acquire_routing_lock",
        lambda blocking=True: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )

    guard = install_vm_ha_cold_start_guard(state_dir=tmp_path, boot_id="boot-a")
    status = vm_ha_status(state_dir=tmp_path, boot_id=lambda: "boot-a")

    assert guard["guard_boot_id"] == "boot-a"
    assert status["guard_boot_id"] == "boot-a"
    assert status["data_plane_mode"] == "blocked"
    assert status["promotion_ready"] is False
    assert status["observed_owner_node_id"] is None
    assert status["state"] == "blocked"
    assert status["reasons"] == ["authoritative-cloud-observation-not-yet-recorded"]
    assert status["recovery_action"] is None
    assert json.loads((tmp_path / "guard.json").read_text())["data_plane_mode"] == "blocked"
    assert not (tmp_path / "standby-ready.json").exists()

    require_vm_ha_runtime_prerequisites(state_dir=tmp_path)
    persisted = status

    persisted.update(state="active", data_plane_mode="active", promotion_ready=True)
    (tmp_path / "status.json").write_text(json.dumps(persisted), encoding="utf-8")
    stale_status = vm_ha_status(state_dir=tmp_path, boot_id=lambda: "boot-a")
    assert stale_status["state"] == "blocked"
    assert stale_status["data_plane_mode"] == "blocked"
    assert stale_status["promotion_ready"] is False


def test_vm_ha_materialization_waits_for_agent_routing_lock_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    generation_id = "a" * 64
    monkeypatch.setattr(
        "nebius_vpngw.agent.main._vm_ha_materialization_authorized",
        lambda **_kwargs: {
            "generation": {"generation_id": generation_id},
            "node": {"node_id": "node-a"},
        },
    )
    monkeypatch.setattr("nebius_vpngw.agent.main._boot_id", lambda: "boot-a")
    (tmp_path / "materialization.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-materialization-v1",
                "boot_id": "boot-a",
                "generation_id": generation_id,
                "node_id": "node-a",
            }
        ),
        encoding="utf-8",
    )
    attempts = iter((None, None, None))
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.acquire_routing_lock",
        lambda blocking=False: next(attempts),
    )
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="handoff is incomplete"):
        require_vm_ha_current_boot_materialization(
            state_dir=tmp_path,
            handoff_attempts=3,
            handoff_interval_seconds=0.25,
            sleeper=sleeps.append,
        )
    assert sleeps == [0.25, 0.25]

    lock_path = tmp_path / "routing.lock"
    attempts = iter(
        (
            None,
            os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600),
        )
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.acquire_routing_lock",
        lambda blocking=False: next(attempts),
    )
    sleeps = []

    require_vm_ha_current_boot_materialization(
        state_dir=tmp_path,
        handoff_attempts=3,
        handoff_interval_seconds=0.25,
        sleeper=sleeps.append,
    )
    assert sleeps == [0.25]


def test_vm_ha_ready_expected_skip_has_no_traceback(monkeypatch, capsys) -> None:
    def not_ready() -> None:
        raise RuntimeError("standby is intentionally not ready")

    monkeypatch.setattr(agent_main, "require_vm_ha_current_boot_readiness", not_ready)
    monkeypatch.setattr(sys, "argv", ["nebius-vpngw-agent", "--vm-ha-ready"])

    with pytest.raises(SystemExit) as exit_info:
        agent_main.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_vm_ha_route_maintenance_expected_skip_has_no_traceback(
    monkeypatch, capsys
) -> None:
    def not_ready() -> None:
        raise RuntimeError("member is intentionally blocked")

    monkeypatch.setattr(
        agent_main, "require_vm_ha_route_maintenance_readiness", not_ready
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["nebius-vpngw-agent", "--vm-ha-route-maintenance-ready"],
    )

    with pytest.raises(SystemExit) as exit_info:
        agent_main.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_agent_keeps_only_the_canonical_vm_ha_status_read(monkeypatch, capsys) -> None:
    calls: list[str] = []

    def current_status() -> dict[str, str]:
        calls.append("status")
        return {"schema": "nebius-vpngw/vm-ha-status-v1"}

    monkeypatch.setattr(agent_main, "vm_ha_status", current_status)
    monkeypatch.setattr(sys, "argv", ["nebius-vpngw-agent", "--vm-ha-status"])

    agent_main.main()

    assert calls == ["status"]
    assert json.loads(capsys.readouterr().out)["schema"] == "nebius-vpngw/vm-ha-status-v1"

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["nebius-vpngw-agent", "--vm-ha-recover"])
    with pytest.raises(SystemExit) as exit_info:
        agent_main.main()

    assert exit_info.value.code == 2
    assert calls == []
    assert "unrecognized arguments: --vm-ha-recover" in capsys.readouterr().err


def test_agent_capability_document_is_read_only_and_machine_readable(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "argv", ["nebius-vpngw-agent", "--agent-capabilities"])

    agent_main.main()

    assert json.loads(capsys.readouterr().out) == {
        "features": [
            "force-reconcile-v1",
            "vm-ha-authority-bound-force-reconcile-v1",
        ],
        "schema": "nebius-vpngw.agent-capabilities.v1",
    }


def test_vm_ha_systemd_units_guard_vpn_services_before_controller() -> None:
    systemd = Path(__file__).parents[2] / "src" / "nebius_vpngw" / "systemd"
    guard = (systemd / "nebius-vpngw-vm-ha-guard.service").read_text(encoding="utf-8")
    controller = (systemd / "nebius-vpngw-vm-ha.service").read_text(encoding="utf-8")
    rearm = (systemd / "nebius-vpngw-vm-ha-rearm.service").read_text(encoding="utf-8")
    ordering = (systemd / "nebius-vpngw-vm-ha-ordering.conf").read_text(encoding="utf-8")
    ufw_lock = (systemd / "nebius-vpngw-ufw-lock.conf").read_text(encoding="utf-8")
    fix_routes = (systemd / "nebius-vpngw-fix-routes.service").read_text(encoding="utf-8")

    assert "Before=network-online.target strongswan-starter.service" in guard
    assert "After=local-fs.target systemd-sysctl.service systemd-tmpfiles-setup.service" in guard
    assert "f /run/ufw.lock 0600 root root -" in ufw_lock
    assert "ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in guard
    assert "RuntimeDirectory=nebius-vpngw" in guard
    assert (
        "ReadWritePaths=/var/lib/nebius-vpngw /run/nebius-vpngw "
        "/proc/sys/net/ipv4/ip_forward" in guard
    )
    assert "Requires=nebius-vpngw-vm-ha-guard.service" in controller
    assert "nebius-vpngw-vm-ha-rearm.service" not in controller
    assert (
        "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-preflight" in controller
    )
    assert "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in controller
    assert "Type=notify" in controller
    assert "After=network-online.target nebius-vpngw-vm-ha-guard.service" in controller
    assert "Before=strongswan-starter.service strongswan.service frr.service" in controller
    assert "After=nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha.service" in ordering
    assert "Requires=nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha.service" in ordering
    assert (
        "ExecCondition=/usr/bin/python3 -m nebius_vpngw.agent.main "
        "--vm-ha-route-maintenance-ready" in fix_routes
    )
    assert "ExecCondition=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-ready" not in fix_routes
    assert "ExecStartPre=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-ready" not in fix_routes
    assert "ExecStopPost=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-guard" in controller
    assert "Restart=on-failure" in controller
    assert "TimeoutStopSec=30" in controller
    assert "SupplementaryGroups=frr frrvty" in controller
    assert "ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-rearm-service" in rearm
    assert "After=network-online.target nebius-vpngw-vm-ha.service" in rearm
    assert "ConditionPathExists=/etc/nebius-vpngw/vm-ha-enabled" in rearm
    assert "Requires=" not in rearm
    assert "CapabilityBoundingSet=" not in rearm
    assert "ReadWritePaths=/var/lib/nebius-vpngw" in rearm
    assert (
        "ReadWritePaths=/var/lib/nebius-vpngw /run/nebius-vpngw /run/ufw.lock "
        "/proc/sys/net/ipv4/ip_forward /etc/vpngw_peer_ips "
        "/etc/vpngw_mgmt_cidrs /etc/vpngw_local_prefixes /etc/ufw "
        "/etc/default/ufw /etc/frr" in controller
    )
