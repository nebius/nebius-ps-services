from __future__ import annotations

import hashlib
import json
import subprocess
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest
from rich.console import Console

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
    VMHAClusterRecord,
    VMHADigestRecord,
    VMHAGenerationRecord,
    VMHALogicalManifests,
    VMHANodeRecord,
    VMHAReadinessRecord,
)
from nebius_vpngw.deploy.route_manager import (
    BGPAdvertisementState,
    RouteManagementError,
    RouteManager,
    VMHAAdvertisementAuthority,
    VMHAStaticRouteAuthority,
    VMHAStaticRouteConvergence,
)
from nebius_vpngw.deploy.ssh_policy import KNOWN_HOSTS_ENV, require_vm_ha_ssh_policy
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHAMigrationTransaction,
)
from nebius_vpngw.deploy.vm_ha_routes import BGPRouteReadiness
from nebius_vpngw.schema import VMHARole, VMHARouteTarget


def _plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=1,
            region="us-central1",
            external_ips=[["203.0.113.10"]],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.96.0.0/13"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="version: 1\n",
            )
        ],
    )


def test_vm_ha_bgp_optional_prefix_drift_is_informational() -> None:
    readiness = BGPRouteReadiness.normalize(
        configured_sessions=("169.254.10.2",),
        established_sessions=("169.254.10.2",),
        required_prefixes=("10.20.0.0/16",),
        optional_prefixes=("10.21.0.0/16",),
        learned_prefixes=("10.20.0.0/16",),
        usable_xfrm_prefixes=("10.20.0.0/16",),
        observed_import_policy_digest="policy-a",
        committed_import_policy_digest="policy-a",
    )

    assert readiness.promotion_ready
    assert readiness.missing_optional_prefixes == {"10.21.0.0/16"}


def test_expected_advertised_prefixes_follow_gateway_local_prefixes() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "defaults": {"routing": {"mode": "bgp"}},
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "name": "gcp-ha-vpn",
                "routing_mode": "bgp",
                "bgp": {"advertise_local_prefixes": True},
                "tunnels": [
                    {
                        "name": "tunnel-1",
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "name": "tunnel-2",
                        "gateway_instance_index": 0,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            }
        ],
    }

    expected = route_manager._expected_advertised_prefixes(_plan(), local_cfg)

    assert expected == {
        "nebius-vpn-gw-0": {
            "169.254.10.2": {"10.96.0.0/13"},
            "169.254.11.2": {"10.96.0.0/13"},
        }
    }


def test_expected_advertisements_omit_ordinary_static_only_gateway() -> None:
    instances = [
        SimpleNamespace(instance_index=0, hostname="gateway-0"),
        SimpleNamespace(instance_index=1, hostname="gateway-1"),
    ]
    plan = SimpleNamespace(
        vm_ha=None,
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            },
            {
                "routing_mode": "static",
                "tunnels": [{"gateway_instance_index": 1}],
            },
        ],
    }

    assert RouteManager(project_id="project-test")._expected_advertised_prefixes(
        plan,
        local_cfg,
    ) == {"gateway-0": {"169.254.10.2": {"10.96.0.0/13"}}}


def test_disabled_only_bgp_policy_keeps_empty_peer_audit_target() -> None:
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "disable",
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }

    assert RouteManager(project_id="project-test")._expected_advertised_prefixes(
        _plan(),
        local_cfg,
    ) == {"nebius-vpn-gw-0": {}}


def test_mixed_ordinary_modes_do_not_query_static_only_gateway(monkeypatch) -> None:
    instances = [
        SimpleNamespace(
            instance_index=0,
            hostname="gateway-0",
            external_ip="203.0.113.10",
        ),
        SimpleNamespace(
            instance_index=1,
            hostname="gateway-1",
            external_ip="203.0.113.11",
        ),
    ]
    plan = SimpleNamespace(
        vm_ha=None,
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            },
            {
                "routing_mode": "static",
                "tunnels": [{"gateway_instance_index": 1}],
            },
        ],
    }
    route_manager = RouteManager(project_id="project-test")
    queried: list[str] = []
    monkeypatch.setattr(route_manager, "require_agent_capabilities", lambda *_args: None)

    def observe(external_ip: str, _local_cfg: dict):
        queried.append(external_ip)
        if external_ip != "203.0.113.10":
            raise AssertionError("static-only gateway was queried for BGP")
        return (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.96.0.0/13"}},
        )

    monkeypatch.setattr(route_manager, "_collect_observed_bgp_advertisements", observe)

    route_manager.ensure_bgp_advertisements_current(plan, local_cfg)

    assert queried == ["203.0.113.10"]


def test_matching_bgp_advertisements_report_only_completed_audit(
    monkeypatch,
    capsys,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(route_manager, "require_agent_capabilities", lambda *_args: None)
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.96.0.0/13"}},
        ),
    )
    monkeypatch.setattr(
        route_manager,
        "_force_reconcile_runtime_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching advertisements must not be repaired")
        ),
    )

    route_manager.ensure_bgp_advertisements_current(_plan(), local_cfg)

    output = capsys.readouterr().out
    assert "Audit: Querying current BGP advertisements" in output
    assert "Step " not in output
    assert "Repair " not in output
    assert "Skipped" not in output
    assert "already match the current YAML" in output


def test_drifted_bgp_advertisements_report_two_repair_steps(
    monkeypatch,
    capsys,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "inner_remote_ip": "169.254.10.2",
                    }
                ],
            }
        ],
    }
    observations = iter(
        (
            ({"169.254.10.2"}, {"169.254.10.2": {"10.10.0.0/24"}}),
            ({"169.254.10.2"}, {"169.254.10.2": {"10.96.0.0/13"}}),
        )
    )
    reconciled: list[str] = []
    monkeypatch.setattr(route_manager, "require_agent_capabilities", lambda *_args: None)
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(
        route_manager,
        "_force_reconcile_runtime_config",
        lambda inst, _cfg: reconciled.append(inst.hostname) or True,
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    route_manager.ensure_bgp_advertisements_current(_plan(), local_cfg)

    output = capsys.readouterr().out
    assert reconciled == ["nebius-vpn-gw-0"]
    assert "Audit: Querying current BGP advertisements" in output
    assert "Repair 1/2: Force-reconciling the installed config" in output
    assert "Repair 2/2: Re-checking live BGP advertisements" in output
    assert "Step " not in output
    assert "Skipped" not in output
    assert "Refreshed live BGP advertisements" in output


def test_route_manager_ssh_uses_exact_vm_ha_host_alias(tmp_path) -> None:
    hostname = "gateway-0"
    external_ip = "203.0.113.10"
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        f"{hostname} {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )
    policy = require_vm_ha_ssh_policy(
        ((hostname, external_ip),),
        {KNOWN_HOSTS_ENV: str(known_hosts)},
        enrollment_hosts=(),
    )
    route_manager = RouteManager(project_id="project-test", ssh_policy=policy)

    command = route_manager._ssh_base_command({}, external_ip=external_ip)

    assert f"HostKeyAlias={hostname}" in command
    assert f"UserKnownHostsFile={policy.known_hosts_file}" in command


def test_route_manager_ssh_uses_only_configured_client_identity(monkeypatch) -> None:
    expected_auth = SimpleNamespace(
        openssh_options=lambda: (
            "-i",
            "/private/exact-key",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PasswordAuthentication=no",
        )
    )
    resolved: list[tuple[str, object]] = []

    def resolve(public_key, *, explicit_private_key):
        resolved.append((public_key, explicit_private_key))
        return expected_auth

    monkeypatch.setattr(
        "nebius_vpngw.deploy.route_manager.resolve_ssh_client_auth",
        resolve,
    )
    route_manager = RouteManager(project_id="project-test")
    local_cfg = {
        "gateway_group": {
            "vm_spec": {
                "ssh_public_key": "ssh-ed25519 fixture configured key",
                "ssh_private_key_path": "/private/exact-key",
            }
        }
    }

    command = route_manager._ssh_base_command(local_cfg, external_ip="203.0.113.10")

    assert resolved == [("ssh-ed25519 fixture configured key", Path("/private/exact-key"))]
    assert "IdentitiesOnly=yes" in command
    assert "PasswordAuthentication=no" in command


def test_route_manager_ssh_transport_failure_is_typed(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd="ssh", timeout=15)
        ),
    )

    with pytest.raises(RouteManagementError, match="SSH transport"):
        route_manager._run_ssh({}, "203.0.113.10", "true")


def test_bgp_advertisement_state_reports_prefix_drift() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert (
        route_manager._bgp_advertisement_state(
            expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
            observed_peers={"169.254.10.2"},
            observed_prefixes_by_peer={"169.254.10.2": {"10.0.0.0/16"}},
        )
        is BGPAdvertisementState.DRIFT
    )


def test_bgp_advertisement_state_reports_peer_set_drift() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert (
        route_manager._bgp_advertisement_state(
            expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
            observed_peers={"169.254.10.2", "169.254.20.2"},
            observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        )
        is BGPAdvertisementState.DRIFT
    )


def test_bgp_advertisement_state_matches_exact_live_state() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert (
        route_manager._bgp_advertisement_state(
            expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
            observed_peers={"169.254.10.2"},
            observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        )
        is BGPAdvertisementState.MATCH
    )


def test_bgp_advertisement_state_is_unknown_when_peer_query_is_missing() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert (
        route_manager._bgp_advertisement_state(
            expected_by_peer={"169.254.10.2": set()},
            observed_peers={"169.254.10.2"},
            observed_prefixes_by_peer={},
        )
        is BGPAdvertisementState.UNKNOWN
    )


def test_advertised_prefix_parser_requires_explicit_consistent_counter() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._advertised_bgp_prefixes(
        {
            "advertisedRoutes": {"10.96.0.1/13": {}},
            "totalPrefixCounter": 1,
        }
    ) == {"10.96.0.0/13"}
    assert route_manager._advertised_bgp_prefixes({"totalPrefixCounter": 0}) == set()
    assert route_manager._advertised_bgp_prefixes({}) is None
    assert (
        route_manager._advertised_bgp_prefixes({"advertisedRoutes": {}, "totalPrefixCounter": 1})
        is None
    )


def test_malformed_bgp_summary_is_unknown_without_peer_queries(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_query_bgp_summary",
        lambda *_args: {"ipv4Unicast": {"peers": []}},
    )
    monkeypatch.setattr(
        route_manager,
        "_query_bgp_advertised_routes",
        lambda *_args: (_ for _ in ()).throw(AssertionError("queried malformed peers")),
    )

    assert route_manager._collect_observed_bgp_advertisements("203.0.113.10", {}) is None


def test_explicit_repair_reconciles_installed_config_without_remote_overwrite(
    monkeypatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    commands: list[tuple[str, str | None]] = []

    def run_ssh(_cfg, _ip, command, *, stdin_text=None, **_kwargs):
        commands.append((command, stdin_text))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(route_manager, "_run_ssh", run_ssh)
    instance = SimpleNamespace(
        instance_index=0,
        hostname="gateway-0",
        external_ip="203.0.113.10",
        config_yaml="version: 1\n",
    )

    assert route_manager._force_reconcile_runtime_config(instance, {})
    assert len(commands) == 1
    assert commands[0][1] is None
    assert commands[0][0] == ("sudo /usr/bin/python3 -m nebius_vpngw.agent.main --force-reconcile")
    assert "config-resolved.yaml" not in commands[0][0]
    assert "systemctl reload nebius-vpngw-agent" not in commands[0][0]


def test_agent_capability_preflight_accepts_exact_read_only_contract(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    commands: list[str] = []
    local_cfg = {
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [{"gateway_instance_index": 0}],
            }
        ]
    }
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda _cfg, _ip, command, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"features":["force-reconcile-v1",'
                    '"vm-ha-authority-bound-force-reconcile-v1"],'
                    '"schema":"nebius-vpngw.agent-capabilities.v1"}'
                ),
                stderr="",
            )
        ),
    )

    route_manager.require_agent_capabilities(_plan(), local_cfg)
    route_manager.require_agent_capabilities(_plan(), local_cfg)

    assert commands == ["sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities"]


@pytest.mark.parametrize(
    ("result", "message"),
    (
        (SimpleNamespace(returncode=2, stdout="", stderr="usage"), "does not expose"),
        (SimpleNamespace(returncode=0, stdout="not-json", stderr=""), "malformed"),
        (
            SimpleNamespace(
                returncode=0,
                stdout=('{"features":[],"schema":"nebius-vpngw.agent-capabilities.v1"}'),
                stderr="",
            ),
            "missing required",
        ),
    ),
)
def test_agent_capability_preflight_rejects_installed_skew(
    monkeypatch,
    result,
    message: str,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(route_manager, "_run_ssh", lambda *_args, **_kwargs: result)
    local_cfg = {
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [{"gateway_instance_index": 0}],
            }
        ]
    }

    with pytest.raises(RouteManagementError, match=message):
        route_manager.require_agent_capabilities(_plan(), local_cfg)


def _static_vm_ha_plan() -> SimpleNamespace:
    manifest = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.10.0.0/24"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes=hashlib.sha256(manifest.encode()).hexdigest(),
            bgp_policy="c" * 64,
        ),
        logical_manifests=SimpleNamespace(static_routes_json=manifest),
    )
    instances = (
        SimpleNamespace(
            instance_index=0,
            hostname="gateway-0",
            external_ip="203.0.113.10",
        ),
        SimpleNamespace(
            instance_index=1,
            hostname="gateway-1",
            external_ip="203.0.113.11",
        ),
    )
    return SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a", generation=generation),
        iter_instance_configs=lambda: iter(instances),
    )


def _installed_static_plan_and_state() -> tuple[ResolvedDeploymentPlan, VMHALifecycleState]:
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    manifest = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.10.0.0/24"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    digests = VMHADigestRecord(
        configuration="a" * 64,
        static_routes=hashlib.sha256(manifest.encode()).hexdigest(),
        bgp_policy="c" * 64,
    )
    generation = VMHAGenerationRecord(
        generation_id="a" * 64,
        digests=digests,
        logical_manifests=VMHALogicalManifests(
            static_routes_json=manifest,
            bgp_policy_json="[]",
        ),
    )
    nodes = (
        VMHANodeRecord("node-0", 0, VMHARole.ACTIVE),
        VMHANodeRecord("node-1", 1, VMHARole.PASSIVE),
    )
    readiness = VMHAReadinessRecord(
        required_node_ids=("node-0", "node-1"),
        generation_id=generation.generation_id,
        digests=digests,
    )
    cluster = VMHAClusterRecord(
        cluster_id="cluster-a",
        members=nodes,
        generation=generation,
        readiness=readiness,
    )
    plan = ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="gateway",
            instance_count=2,
            region="region-a",
            external_ips=[["203.0.113.10"], ["203.0.113.11"]],
            vm_spec={},
        ),
        per_instance=[
            InstanceResolvedConfig(
                instance_index=index,
                hostname=f"gateway-{index}",
                external_ip=f"203.0.113.1{index}",
                config_yaml="version: 1\n",
                vm_ha_node=node,
                vm_ha_generation=generation,
                vm_ha_readiness=readiness,
            )
            for index, node in enumerate(nodes)
        ],
        vm_ha=cluster,
    )
    transaction = VMHAMigrationTransaction(
        operation_id="operation-a",
        approval_kind="migration",
        approval_digest="1" * 64,
        desired_state_digest="2" * 64,
        current_state_digest="3" * 64,
        checkpoint="active",
        pending_effect=None,
        completed_effects=(),
        resource_bindings=(),
        revision=1,
        predecessor_sha256=None,
    )
    serialized_target = json.dumps(
        target.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    state = VMHALifecycleState(
        status=VMHALifecycleStatus.ACTIVE,
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-a",
        allocation_id="allocation-a",
        allocation_name="gateway-cluster-a-shared-private-ip",
        members=(
            VMHALifecycleMember(
                instance_index=0,
                instance_name="gateway-0",
                node_id="node-0",
                role="active",
                compute_id="compute-0",
                network_interface_name="eth0",
                public_ip="203.0.113.10",
                compute_revision="11",
                disk_id="disk-0",
                network_interface_subnet_id="gateway-subnet",
                primary_allocation_id="primary-0",
                public_allocation_id="public-0",
                alias_allocation_ids=("allocation-a",),
            ),
            VMHALifecycleMember(
                instance_index=1,
                instance_name="gateway-1",
                node_id="node-1",
                role="passive",
                compute_id="compute-1",
                network_interface_name="eth0",
                public_ip="203.0.113.11",
                compute_revision="12",
                disk_id="disk-1",
                network_interface_subnet_id="gateway-subnet",
                primary_allocation_id="primary-1",
                public_allocation_id="public-1",
            ),
        ),
        route_runtime_id="route-runtime-a",
        route_targets=(serialized_target,),
        transaction=transaction,
    )
    return plan, state


def _installed_static_status(
    plan: ResolvedDeploymentPlan,
    *,
    node_id: str,
) -> dict:
    assert plan.vm_ha is not None
    generation = plan.vm_ha.generation
    owner = node_id == "node-0"
    return {
        "allocation_id": "allocation-a",
        "apply_locked": False,
        "candidate_attachment_absent": not owner,
        "candidate_attachment_exact": owner,
        "cluster_id": "cluster-a",
        "data_plane_mode": "active" if owner else "passive",
        "digests": {
            "bgp_policy": generation.digests.bgp_policy,
            "configuration": generation.digests.configuration,
            "static_routes": generation.digests.static_routes,
        },
        "former_attachment_exact": not owner,
        "generation_id": generation.generation_id,
        "node_id": node_id,
        "observed_owner_node_id": "node-0",
        "ownership_epoch": "11" if owner else "12",
        "ownership_re_read_exact": owner,
        "pending_operation_id": ("boot-a:1:disable-active:node-0" if owner else None),
        "repair": None,
        "route_runtime_id": "route-runtime-a",
        "schema": "nebius-vpngw/vm-ha-status-v1",
    }


def test_static_authority_admits_only_installed_generation_and_route_repair_chain(
    monkeypatch,
) -> None:
    plan, state = _installed_static_plan_and_state()
    route_manager = RouteManager(project_id="project-a")
    statuses = {
        "203.0.113.10": _installed_static_status(plan, node_id="node-0"),
        "203.0.113.11": _installed_static_status(plan, node_id="node-1"),
    }
    monkeypatch.setattr(
        route_manager,
        "_query_vm_ha_status",
        lambda ip, _cfg: statuses[ip],
    )

    authority, _observed = route_manager._observe_vm_ha_static_route_authority(
        plan,
        {},
        state,
    )

    assert authority.owner_hostname == "gateway-0"
    assert authority.lifecycle_record_sha256 == state.record_sha256
    statuses["203.0.113.10"]["generation_id"] = "f" * 64
    with pytest.raises(RouteManagementError, match="installed VM-HA generation"):
        route_manager._observe_vm_ha_static_route_authority(plan, {}, state)
    statuses["203.0.113.10"]["generation_id"] = "a" * 64
    statuses["203.0.113.10"]["pending_operation_id"] = "boot-a:2:stop-former-owner:node-1"
    with pytest.raises(RouteManagementError, match="outside static-route convergence"):
        route_manager._observe_vm_ha_static_route_authority(plan, {}, state)


def _static_authority(plan: SimpleNamespace) -> VMHAStaticRouteAuthority:
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    digests = {
        "bgp_policy": plan.vm_ha.generation.digests.bgp_policy,
        "configuration": plan.vm_ha.generation.digests.configuration,
        "static_routes": plan.vm_ha.generation.digests.static_routes,
    }
    return VMHAStaticRouteAuthority(
        lifecycle_record_sha256="d" * 64,
        cluster_id="cluster-a",
        owner_hostname="gateway-0",
        owner_node_id="node-0",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        route_runtime_id="route-runtime-a",
        digests=tuple(sorted(digests.items())),
        ownership_epochs_by_hostname=(("gateway-0", "11"), ("gateway-1", "12")),
        route_targets=(target,),
    )


def _static_statuses(
    authority: VMHAStaticRouteAuthority,
    *,
    current_receipt: bool,
) -> dict[str, dict]:
    receipt = (
        {
            "allocation_id": authority.allocation_id,
            "digests": dict(authority.digests),
            "generation_id": authority.generation_id,
            "operation_id": "boot:7:reconcile-routes:node-0",
            "owner_node_id": authority.owner_node_id,
            "ownership_epoch": "11",
            "ownership_incarnation": 3,
            "route_runtime_id": authority.route_runtime_id,
        }
        if current_receipt
        else None
    )
    return {
        "gateway-0": {
            "apply_locked": False,
            "data_plane_mode": "active",
            "pending_operation_id": None,
            "promotion_ready": True,
            "repair": None,
            "route_reconciliation": receipt,
        },
        "gateway-1": {
            "apply_locked": False,
            "data_plane_mode": "passive",
            "pending_operation_id": None,
            "repair": None,
        },
    }


def test_static_vm_ha_capability_preflight_queries_both_members(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-a")
    queried: list[str] = []
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda _cfg, ip, _command, **_kwargs: (
            queried.append(ip)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"features":["vm-ha-controller-route-reconcile-v1"],'
                    '"schema":"nebius-vpngw.agent-capabilities.v1"}'
                ),
                stderr="",
            )
        ),
    )

    route_manager.require_vm_ha_static_controller_capability(
        _static_vm_ha_plan(),
        {},
    )

    assert queried == ["203.0.113.10", "203.0.113.11"]


def test_static_vm_ha_noop_requires_receipt_and_cloud_postcondition(monkeypatch) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    route_manager = RouteManager(project_id="project-a")
    monkeypatch.setattr(
        route_manager,
        "require_vm_ha_static_controller_capability",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        route_manager,
        "_observe_vm_ha_static_route_authority",
        lambda *_args: (authority, _static_statuses(authority, current_receipt=True)),
    )
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: object())
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_static_cloud_postcondition",
        lambda *_args: True,
    )
    waited: list[str] = []

    result = route_manager.ensure_vm_ha_static_routes_current(
        plan,
        {},
        lifecycle_state_loader=lambda: SimpleNamespace(),
        on_wait=lambda: waited.append("wait"),
    )

    assert result is VMHAStaticRouteConvergence.ALREADY_CURRENT
    assert waited == []


def test_static_vm_ha_repeated_noop_is_request_free(monkeypatch) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    route_manager = RouteManager(project_id="project-a")
    capability_checks: list[str] = []
    authority_reads: list[str] = []
    cloud_reads: list[str] = []
    waited: list[str] = []
    monkeypatch.setattr(
        route_manager,
        "require_vm_ha_static_controller_capability",
        lambda *_args: capability_checks.append("checked"),
    )
    monkeypatch.setattr(
        route_manager,
        "_observe_vm_ha_static_route_authority",
        lambda *_args: (
            authority_reads.append("read")
            or (authority, _static_statuses(authority, current_receipt=True))
        ),
    )
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: object())
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_static_cloud_postcondition",
        lambda *_args: cloud_reads.append("read") or True,
    )

    results = [
        route_manager.ensure_vm_ha_static_routes_current(
            plan,
            {},
            lifecycle_state_loader=lambda: SimpleNamespace(),
            on_wait=lambda: waited.append("wait"),
        )
        for _ in range(2)
    ]

    assert results == [
        VMHAStaticRouteConvergence.ALREADY_CURRENT,
        VMHAStaticRouteConvergence.ALREADY_CURRENT,
    ]
    assert capability_checks == ["checked", "checked"]
    assert authority_reads == ["read", "read", "read", "read"]
    assert cloud_reads == ["read", "read"]
    assert waited == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("allocation_id", "foreign-allocation"),
        ("generation_id", "f" * 64),
        ("owner_node_id", "node-1"),
        ("ownership_epoch", "12"),
        ("ownership_incarnation", -1),
        ("route_runtime_id", "foreign-route-runtime"),
    ),
)
def test_static_vm_ha_receipt_must_match_exact_authority(field, value) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    statuses = _static_statuses(authority, current_receipt=True)
    statuses[authority.owner_hostname]["route_reconciliation"][field] = value

    assert not RouteManager._vm_ha_static_route_receipt_current(authority, statuses)


def test_static_vm_ha_completion_rejects_authority_change_during_cloud_read(
    monkeypatch,
) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    changed = authority._replace(allocation_id="replacement-allocation")
    route_manager = RouteManager(project_id="project-a")
    observations = iter(
        (
            (authority, _static_statuses(authority, current_receipt=True)),
            (changed, _static_statuses(changed, current_receipt=True)),
        )
    )
    monkeypatch.setattr(
        route_manager,
        "require_vm_ha_static_controller_capability",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        route_manager,
        "_observe_vm_ha_static_route_authority",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: object())
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_static_cloud_postcondition",
        lambda *_args: True,
    )

    with pytest.raises(RouteManagementError, match="authority changed"):
        route_manager.ensure_vm_ha_static_routes_current(
            plan,
            {},
            lifecycle_state_loader=lambda: SimpleNamespace(),
        )


def test_static_vm_ha_waits_for_autonomous_controller_without_request(monkeypatch) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    route_manager = RouteManager(project_id="project-a")
    observations = iter(
        (
            (authority, _static_statuses(authority, current_receipt=False)),
            (authority, _static_statuses(authority, current_receipt=True)),
            (authority, _static_statuses(authority, current_receipt=True)),
        )
    )
    monkeypatch.setattr(
        route_manager,
        "require_vm_ha_static_controller_capability",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        route_manager,
        "_observe_vm_ha_static_route_authority",
        lambda *_args: next(observations),
    )
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: object())
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_static_cloud_postcondition",
        lambda *_args: True,
    )
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    waited: list[str] = []
    result = route_manager.ensure_vm_ha_static_routes_current(
        plan,
        {},
        lifecycle_state_loader=lambda: SimpleNamespace(),
        timeout_seconds=120,
        poll_interval_seconds=2,
        clock=lambda: now[0],
        sleeper=sleep,
        on_wait=lambda: waited.append("wait"),
    )

    assert result is VMHAStaticRouteConvergence.CONVERGED
    assert sleeps == [2]
    assert waited == ["wait"]


def test_static_vm_ha_timeout_is_bounded_and_has_no_false_success(monkeypatch) -> None:
    plan = _static_vm_ha_plan()
    authority = _static_authority(plan)
    route_manager = RouteManager(project_id="project-a")
    monkeypatch.setattr(
        route_manager,
        "require_vm_ha_static_controller_capability",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        route_manager,
        "_observe_vm_ha_static_route_authority",
        lambda *_args: (authority, _static_statuses(authority, current_receipt=False)),
    )
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: object())
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_static_cloud_postcondition",
        lambda *_args: False,
    )
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    with pytest.raises(RouteManagementError, match="Timed out after 4 seconds"):
        route_manager.ensure_vm_ha_static_routes_current(
            plan,
            {},
            lifecycle_state_loader=lambda: SimpleNamespace(),
            timeout_seconds=4,
            poll_interval_seconds=2,
            clock=lambda: now[0],
            sleeper=sleep,
        )

    assert sleeps == [2, 2]


def test_vm_ha_explicit_repair_binds_remote_reconcile_to_exact_authority(
    monkeypatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    commands: list[str] = []
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda _cfg, _ip, command, **_kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    instance = SimpleNamespace(
        hostname="gateway-0",
        external_ip="203.0.113.10",
        config_yaml="version: 1\n",
    )
    authority = VMHAAdvertisementAuthority(
        owner_hostname="gateway-1",
        generation_id="a" * 64,
        owner_node_id="node-1",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(
            ("gateway-0", "7"),
            ("gateway-1", "8"),
        ),
    )

    assert route_manager._force_reconcile_runtime_config(
        instance,
        {},
        vm_ha_authority=authority,
    )

    assert len(commands) == 1
    assert "--expected-vm-ha-owner node-1" in commands[0]
    assert f"--expected-vm-ha-generation {'a' * 64}" in commands[0]
    assert "--expected-vm-ha-epoch 7" in commands[0]
    assert "--expected-vm-ha-allocation allocation-a" in commands[0]

    commands.clear()
    instance.hostname = "gateway-1"
    assert route_manager._force_reconcile_runtime_config(
        instance,
        {},
        vm_ha_authority=authority,
    )
    assert "--expected-vm-ha-epoch 8" in commands[0]


def test_advertised_route_query_rejects_non_ip_peer_before_ssh(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid peer reached the SSH command")
        ),
    )

    assert (
        route_manager._query_bgp_advertised_routes(
            "203.0.113.10",
            "169.254.10.2'; touch /tmp/unexpected; echo '",
            {},
        )
        is None
    )


def test_vm_ha_expected_advertisements_follow_authoritative_owner() -> None:
    instances = [
        SimpleNamespace(instance_index=0, hostname="gateway-0"),
        SimpleNamespace(instance_index=1, hostname="gateway-1"),
    ]
    plan = SimpleNamespace(
        vm_ha=object(),
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "name": "site-a",
                "routing_mode": "bgp",
                "tunnels": [
                    {"gateway_instance_index": 0, "inner_remote_ip": "169.254.10.2"},
                    {"gateway_instance_index": 1, "inner_remote_ip": "169.254.11.2"},
                ],
            }
        ],
    }

    expected = RouteManager(project_id="project-test")._expected_advertised_prefixes(
        plan,
        local_cfg,
        vm_ha_owner_hostname="gateway-1",
    )

    assert expected == {
        "gateway-0": {"169.254.10.2": set()},
        "gateway-1": {"169.254.11.2": {"10.96.0.0/13"}},
    }


def test_unknown_advertisement_evidence_never_reloads(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    reloads: list[str] = []
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: ({"169.254.10.2"}, {}),
    )
    monkeypatch.setattr(
        route_manager,
        "_force_reconcile_runtime_config",
        lambda inst, _cfg: reloads.append(inst.hostname) or True,
    )
    monkeypatch.setattr(route_manager, "require_agent_capabilities", lambda *_args: None)
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [{"gateway_instance_index": 0, "inner_remote_ip": "169.254.10.2"}],
            }
        ],
    }

    with pytest.raises(RouteManagementError, match="UNKNOWN"):
        route_manager.ensure_bgp_advertisements_current(_plan(), local_cfg)

    assert reloads == []


def test_read_only_advertisement_audit_reports_drift_without_reload(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.10.0.0/24"}},
        ),
    )
    monkeypatch.setattr(
        route_manager,
        "_force_reconcile_runtime_config",
        lambda *_args: (_ for _ in ()).throw(AssertionError("audit attempted a reload")),
    )
    local_cfg = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "routing_mode": "bgp",
                "tunnels": [{"gateway_instance_index": 0, "inner_remote_ip": "169.254.10.2"}],
            }
        ],
    }

    assert route_manager.audit_bgp_advertisements(_plan(), local_cfg) == {
        "nebius-vpn-gw-0": BGPAdvertisementState.DRIFT
    }


def test_vm_ha_read_only_audit_downgrades_mixed_epoch_evidence(monkeypatch) -> None:
    instance = SimpleNamespace(
        hostname="gateway-0",
        external_ip="203.0.113.10",
    )
    plan = SimpleNamespace(
        vm_ha=object(),
        iter_instance_configs=lambda: iter((instance,)),
    )
    authorities = iter(
        (
            VMHAAdvertisementAuthority(
                "gateway-0",
                "a" * 64,
                "node-0",
                "allocation-a",
                (("gateway-0", "7"),),
            ),
            VMHAAdvertisementAuthority(
                "gateway-0",
                "a" * 64,
                "node-0",
                "allocation-a",
                (("gateway-0", "8"),),
            ),
        )
    )
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_advertisement_authority",
        lambda *_args: next(authorities),
    )
    monkeypatch.setattr(
        route_manager,
        "_expected_advertised_prefixes",
        lambda *_args, **_kwargs: {"gateway-0": {"169.254.10.2": {"10.96.0.0/13"}}},
    )
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.96.0.0/13"}},
        ),
    )

    result = route_manager._audit_bgp_advertisements(
        plan,
        {},
        vm_ha_lifecycle_guard=lambda: True,
    )

    assert result.states == {"gateway-0": BGPAdvertisementState.UNKNOWN}
    assert result.authority is None


def test_vm_ha_internal_audit_retains_only_stable_authority(monkeypatch) -> None:
    instance = SimpleNamespace(
        instance_index=0,
        hostname="gateway-0",
        external_ip="203.0.113.10",
    )
    plan = SimpleNamespace(
        vm_ha=object(),
        iter_instance_configs=lambda: iter((instance,)),
    )
    authority = VMHAAdvertisementAuthority(
        owner_hostname="gateway-0",
        generation_id="a" * 64,
        owner_node_id="node-0",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(("gateway-0", "7"),),
    )
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_vm_ha_advertisement_authority",
        lambda *_args: authority,
    )
    monkeypatch.setattr(
        route_manager,
        "_expected_advertised_prefixes",
        lambda *_args, **_kwargs: {"gateway-0": {"169.254.10.2": {"10.96.0.0/13"}}},
    )
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.96.0.0/13"}},
        ),
    )

    result = route_manager._audit_bgp_advertisements(
        plan,
        {},
        vm_ha_lifecycle_guard=lambda: True,
    )

    assert result.states == {"gateway-0": BGPAdvertisementState.MATCH}
    assert result.authority == authority


def test_gateway_heading_uses_current_vm_ha_owner_not_tunnel_role() -> None:
    route_manager = RouteManager(project_id="project-test")
    vm_ha_plan = SimpleNamespace(vm_ha=object())
    authority = VMHAAdvertisementAuthority(
        owner_hostname="gateway-1",
        generation_id="a" * 64,
        owner_node_id="node-1",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(
            ("gateway-0", "7"),
            ("gateway-1", "8"),
        ),
    )

    active = route_manager._gateway_heading_markup(
        vm_ha_plan,
        "gateway-1",
        "203.0.113.11",
        authority,
    )
    standby = route_manager._gateway_heading_markup(
        vm_ha_plan,
        "gateway-0",
        "203.0.113.10",
        authority,
    )
    unknown = route_manager._gateway_heading_markup(
        vm_ha_plan,
        "gateway-0",
        "203.0.113.10",
        None,
    )
    ordinary = route_manager._gateway_heading_markup(
        SimpleNamespace(vm_ha=None),
        "gateway-0",
        "203.0.113.10",
        None,
    )

    assert active == ("[bold green]Gateway VM: gateway-1 (203.0.113.11) — ACTIVE[/bold green]")
    assert standby == (
        "[bold cyan]Gateway VM: gateway-0 (203.0.113.10)[/bold cyan] [yellow]— STANDBY[/yellow]"
    )
    assert unknown == (
        "[bold cyan]Gateway VM: gateway-0 (203.0.113.10)[/bold cyan] [yellow]— UNKNOWN[/yellow]"
    )
    assert ordinary == ("[bold cyan]Gateway VM: gateway-0 (203.0.113.10)[/bold cyan]")

    reversed_authority = authority._replace(
        owner_hostname="gateway-0",
        owner_node_id="node-0",
    )
    assert (
        route_manager._gateway_heading_markup(
            vm_ha_plan,
            "gateway-0",
            "203.0.113.10",
            reversed_authority,
        )
        == "[bold green]Gateway VM: gateway-0 (203.0.113.10) — ACTIVE[/bold green]"
    )
    assert route_manager._gateway_heading_markup(
        vm_ha_plan,
        "gateway-1",
        "203.0.113.11",
        reversed_authority,
    ).endswith("[yellow]— STANDBY[/yellow]")


def test_active_gateway_heading_renders_green_in_a_color_terminal() -> None:
    route_manager = RouteManager(project_id="project-test")
    authority = VMHAAdvertisementAuthority(
        owner_hostname="gateway-1",
        generation_id="a" * 64,
        owner_node_id="node-1",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(("gateway-1", "8"),),
    )
    rendered = StringIO()
    console = Console(
        file=rendered,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        width=120,
    )

    console.print(
        route_manager._gateway_heading_markup(
            SimpleNamespace(vm_ha=object()),
            "gateway-1",
            "203.0.113.11",
            authority,
        )
    )

    rendered_output = rendered.getvalue()
    assert "\x1b[1;32m" in rendered_output
    assert (
        rendered_output.replace("\x1b[1;32m", "").replace("\x1b[0m", "")
        == "Gateway VM: gateway-1 (203.0.113.11) — ACTIVE\n"
    )


def test_bgp_route_listing_labels_owner_and_standby_from_audit_snapshot(
    monkeypatch,
    capsys,
) -> None:
    instances = (
        SimpleNamespace(
            instance_index=0,
            hostname="gateway-0",
            external_ip="203.0.113.10",
        ),
        SimpleNamespace(
            instance_index=1,
            hostname="gateway-1",
            external_ip="203.0.113.11",
        ),
    )
    plan = SimpleNamespace(
        vm_ha=object(),
        iter_instance_configs=lambda: iter(instances),
    )
    local_cfg = {
        "connections": [
            {
                "name": "site-a",
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "name": "configured-active-on-standby",
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                    },
                    {
                        "name": "configured-passive-on-owner",
                        "gateway_instance_index": 1,
                        "ha_role": "passive",
                    },
                ],
            }
        ]
    }
    authority = VMHAAdvertisementAuthority(
        owner_hostname="gateway-1",
        generation_id="a" * 64,
        owner_node_id="node-1",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(
            ("gateway-0", "7"),
            ("gateway-1", "8"),
        ),
    )
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_audit_bgp_advertisements",
        lambda *_args, **_kwargs: SimpleNamespace(states={}, authority=authority),
    )
    monkeypatch.setattr(
        route_manager,
        "_query_bgp_summary",
        lambda *_args: {"ipv4Unicast": {"peers": {}}},
    )

    route_manager._list_bgp_advertised_routes(plan, local_cfg, None)

    output = capsys.readouterr().out
    assert "Gateway VM: gateway-0 (203.0.113.10) — STANDBY" in output
    assert "Gateway VM: gateway-1 (203.0.113.11) — ACTIVE" in output


def test_vm_ha_advertisement_authority_requires_owner_and_standby_parity(
    monkeypatch,
) -> None:
    digests = SimpleNamespace(
        configuration="a" * 64,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    generation = SimpleNamespace(generation_id="a" * 64, digests=digests)
    instances = [
        SimpleNamespace(
            hostname="gateway-0",
            external_ip="203.0.113.10",
            vm_ha_node=SimpleNamespace(node_id="node-0"),
            vm_ha_generation=generation,
        ),
        SimpleNamespace(
            hostname="gateway-1",
            external_ip="203.0.113.11",
            vm_ha_node=SimpleNamespace(node_id="node-1"),
            vm_ha_generation=generation,
        ),
    ]
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-a"),
        iter_instance_configs=lambda: iter(instances),
    )
    common = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "allocation_id": "allocation-a",
        "observed_owner_node_id": "node-1",
        "apply_locked": False,
        "pending_operation_id": None,
    }
    statuses = {
        "203.0.113.10": {
            **common,
            "node_id": "node-0",
            "ownership_epoch": "7",
            "data_plane_mode": "passive",
            "promotion_ready": False,
            "standby_ready": True,
            "former_attachment_exact": True,
            "candidate_attachment_absent": True,
        },
        "203.0.113.11": {
            **common,
            "node_id": "node-1",
            "ownership_epoch": "8",
            "data_plane_mode": "active",
            "promotion_ready": True,
            "standby_ready": False,
            "candidate_attachment_exact": True,
            "ownership_re_read_exact": True,
            "route_reconciliation": {
                "owner_node_id": "node-1",
                "allocation_id": "allocation-a",
                "ownership_epoch": "8",
                "generation_id": "a" * 64,
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "b" * 64,
                    "bgp_policy": "c" * 64,
                },
            },
        },
    }
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_query_vm_ha_status",
        lambda external_ip, _cfg: statuses[external_ip],
    )

    assert route_manager._vm_ha_advertisement_authority(plan, {}) == VMHAAdvertisementAuthority(
        owner_hostname="gateway-1",
        generation_id="a" * 64,
        owner_node_id="node-1",
        allocation_id="allocation-a",
        ownership_epochs_by_hostname=(("gateway-0", "7"), ("gateway-1", "8")),
    )

    statuses["203.0.113.10"]["ownership_epoch"] = "9"
    assert route_manager._vm_ha_advertisement_authority(plan, {}) == (
        "gateway-1",
        "a" * 64,
        "node-1",
        "allocation-a",
        (("gateway-0", "9"), ("gateway-1", "8")),
    )
    statuses["203.0.113.10"]["ownership_epoch"] = "7"

    statuses["203.0.113.11"]["route_reconciliation"]["ownership_epoch"] = "9"
    assert route_manager._vm_ha_advertisement_authority(plan, {}) is None
    statuses["203.0.113.11"]["route_reconciliation"]["ownership_epoch"] = "8"

    statuses["203.0.113.11"]["route_reconciliation"]["digests"]["bgp_policy"] = "d" * 64
    assert route_manager._vm_ha_advertisement_authority(plan, {}) is None
    statuses["203.0.113.11"]["route_reconciliation"]["digests"]["bgp_policy"] = "c" * 64

    statuses["203.0.113.10"]["candidate_attachment_absent"] = False
    assert route_manager._vm_ha_advertisement_authority(plan, {}) is None
