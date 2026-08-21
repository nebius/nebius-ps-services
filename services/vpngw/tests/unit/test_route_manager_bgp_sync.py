from __future__ import annotations

import subprocess
from types import SimpleNamespace

import paramiko
import pytest

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
)
from nebius_vpngw.deploy.route_manager import (
    BGPAdvertisementState,
    RouteManagementError,
    RouteManager,
    VMHAAdvertisementAuthority,
)
from nebius_vpngw.deploy.ssh_policy import KNOWN_HOSTS_ENV, require_vm_ha_ssh_policy
from nebius_vpngw.deploy.vm_ha_routes import BGPRouteReadiness


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

    assert route_manager._bgp_advertisement_state(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.0.0.0/16"}},
    ) is BGPAdvertisementState.DRIFT


def test_bgp_advertisement_state_reports_peer_set_drift() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._bgp_advertisement_state(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2", "169.254.20.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
    ) is BGPAdvertisementState.DRIFT


def test_bgp_advertisement_state_matches_exact_live_state() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._bgp_advertisement_state(
        expected_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
        observed_peers={"169.254.10.2"},
        observed_prefixes_by_peer={"169.254.10.2": {"10.96.0.0/13"}},
    ) is BGPAdvertisementState.MATCH


def test_bgp_advertisement_state_is_unknown_when_peer_query_is_missing() -> None:
    route_manager = RouteManager(project_id="project-test")

    assert route_manager._bgp_advertisement_state(
        expected_by_peer={"169.254.10.2": set()},
        observed_peers={"169.254.10.2"},
        observed_prefixes_by_peer={},
    ) is BGPAdvertisementState.UNKNOWN


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
    assert route_manager._advertised_bgp_prefixes(
        {"advertisedRoutes": {}, "totalPrefixCounter": 1}
    ) is None


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

    assert route_manager._collect_observed_bgp_advertisements(
        "203.0.113.10", {}
    ) is None


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
    assert commands[0][0] == (
        "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --force-reconcile"
    )
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

    assert commands == [
        "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --agent-capabilities"
    ]


@pytest.mark.parametrize(
    ("result", "message"),
    (
        (SimpleNamespace(returncode=2, stdout="", stderr="usage"), "does not expose"),
        (SimpleNamespace(returncode=0, stdout="not-json", stderr=""), "malformed"),
        (
            SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"features":[],"schema":'
                    '"nebius-vpngw.agent-capabilities.v1"}'
                ),
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


def test_vm_ha_explicit_repair_binds_remote_reconcile_to_exact_authority(
    monkeypatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    commands: list[str] = []
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda _cfg, _ip, command, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
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

    assert route_manager._query_bgp_advertised_routes(
        "203.0.113.10",
        "169.254.10.2'; touch /tmp/unexpected; echo '",
        {},
    ) is None


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
                "tunnels": [
                    {"gateway_instance_index": 0, "inner_remote_ip": "169.254.10.2"}
                ],
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
                "tunnels": [
                    {"gateway_instance_index": 0, "inner_remote_ip": "169.254.10.2"}
                ],
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
        lambda *_args, **_kwargs: {
            "gateway-0": {"169.254.10.2": {"10.96.0.0/13"}}
        },
    )
    monkeypatch.setattr(
        route_manager,
        "_collect_observed_bgp_advertisements",
        lambda *_args: (
            {"169.254.10.2"},
            {"169.254.10.2": {"10.96.0.0/13"}},
        ),
    )

    assert route_manager.audit_bgp_advertisements(
        plan,
        {},
        vm_ha_lifecycle_guard=lambda: True,
    ) == {"gateway-0": BGPAdvertisementState.UNKNOWN}


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

    assert route_manager._vm_ha_advertisement_authority(
        plan, {}
    ) == VMHAAdvertisementAuthority(
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

    statuses["203.0.113.11"]["route_reconciliation"]["digests"][
        "bgp_policy"
    ] = "d" * 64
    assert route_manager._vm_ha_advertisement_authority(plan, {}) is None
    statuses["203.0.113.11"]["route_reconciliation"]["digests"][
        "bgp_policy"
    ] = "c" * 64

    statuses["203.0.113.10"]["candidate_attachment_absent"] = False
    assert route_manager._vm_ha_advertisement_authority(plan, {}) is None
