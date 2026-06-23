from __future__ import annotations

import ipaddress
import json
import typing as t
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
)
from nebius_vpngw.deploy.route_manager import RouteManager

_LocalConfig = dict[str, t.Any]


def _fake_subnet(
    *,
    name: str,
    network_id: str,
    explicit_cidrs: list[str] | None = None,
    status_cidrs: list[str] | None = None,
    use_network_pools: bool = False,
) -> SimpleNamespace:
    pools = []
    if explicit_cidrs:
        pools.append(
            SimpleNamespace(
                cidrs=[SimpleNamespace(cidr=cidr) for cidr in explicit_cidrs],
            )
        )

    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, id=f"id-{name}"),
        spec=SimpleNamespace(
            network_id=network_id,
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=use_network_pools,
                pools=pools,
            ),
        ),
        status=SimpleNamespace(
            ipv4_private_cidrs=status_cidrs or explicit_cidrs or [],
            route_table=SimpleNamespace(id="", default=True),
        ),
    )


def _fake_route(*, route_id: str, name: str, cidr: str, allocation_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(id=route_id, name=name),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr=cidr),
            next_hop=SimpleNamespace(allocation=SimpleNamespace(id=allocation_id)),
        ),
    )


def _metadata_name(resource: object) -> str:
    return str(vars(vars(resource)["metadata"])["name"])


def _metadata_id(resource: object) -> str:
    return str(vars(vars(resource)["metadata"])["id"])


def _three_vm_plan() -> ResolvedDeploymentPlan:
    return ResolvedDeploymentPlan(
        gateway_group=GatewayGroupSpec(
            name="nebius-vpn-gw",
            instance_count=3,
            region="eu-north1",
            external_ips=[["203.0.113.10"], ["203.0.113.20"], ["203.0.113.30"]],
            vm_spec={},
        ),
        gateway={"local_prefixes": ["10.96.0.0/13"]},
        per_instance=[
            InstanceResolvedConfig(
                instance_index=0,
                hostname="nebius-vpn-gw-0",
                external_ip="203.0.113.10",
                config_yaml="version: 1\n",
            ),
            InstanceResolvedConfig(
                instance_index=1,
                hostname="nebius-vpn-gw-1",
                external_ip="203.0.113.20",
                config_yaml="version: 1\n",
            ),
            InstanceResolvedConfig(
                instance_index=2,
                hostname="nebius-vpn-gw-2",
                external_ip="203.0.113.30",
                config_yaml="version: 1\n",
            ),
        ],
    )


def test_select_local_prefix_subnets_excludes_inherited_status_cidrs_owned_by_other_subnets() -> None:
    route_manager = RouteManager(project_id="project-test")
    subnets = [
        _fake_subnet(
            name="default-subnet",
            network_id="net-1",
            status_cidrs=["10.48.0.0/13", "172.16.10.0/24", "172.16.20.0/24"],
            use_network_pools=True,
        ),
        _fake_subnet(
            name="workload-subnet-1",
            network_id="net-1",
            explicit_cidrs=["172.16.10.0/24"],
        ),
        _fake_subnet(
            name="workload-subnet-2",
            network_id="net-1",
            explicit_cidrs=["172.16.20.0/24"],
        ),
        _fake_subnet(
            name="custom-gateway-subnet",
            network_id="net-1",
            explicit_cidrs=["172.16.30.0/24"],
        ),
        _fake_subnet(
            name="other-network-subnet",
            network_id="net-2",
            explicit_cidrs=["172.16.20.0/24"],
        ),
    ]

    selected, inherited_selected = route_manager._select_local_prefix_subnets(
        subnets,
        [
            ipaddress.IPv4Network("172.16.10.0/24"),
            ipaddress.IPv4Network("172.16.20.0/24"),
            ipaddress.IPv4Network("172.16.30.0/24"),
        ],
        target_network_id="net-1",
        gateway_subnet_name="custom-gateway-subnet",
    )

    assert [_metadata_name(subnet) for subnet, _cidrs in selected] == [
        "workload-subnet-1",
        "workload-subnet-2",
    ]
    assert inherited_selected == [
        "[dim]Ignoring inherited subnet default-subnet for gateway.local_prefixes matching because Nebius status CIDRs overlap explicit CIDRs owned by other subnets.[/dim]"
    ]


def test_select_local_prefix_subnets_keeps_inherited_status_cidrs_after_sanitizing_explicit_ones() -> None:
    route_manager = RouteManager(project_id="project-test")
    subnets = [
        _fake_subnet(
            name="default-subnet",
            network_id="net-1",
            status_cidrs=["10.48.0.0/13", "172.16.10.0/24"],
            use_network_pools=True,
        ),
        _fake_subnet(
            name="workload-subnet-1",
            network_id="net-1",
            explicit_cidrs=["172.16.10.0/24"],
        ),
    ]

    selected, inherited_selected = route_manager._select_local_prefix_subnets(
        subnets,
        [ipaddress.IPv4Network("10.48.0.0/13")],
        target_network_id="net-1",
        gateway_subnet_name="custom-gateway-subnet",
    )

    assert [_metadata_name(subnet) for subnet, _cidrs in selected] == ["default-subnet"]
    assert [str(cidr) for cidr in selected[0][1]] == ["10.48.0.0/13"]
    assert inherited_selected == [
        "[dim]Subnet default-subnet inherits parent network pools (use_network_pools=true); sanitized status CIDRs to exclude explicit CIDRs owned by other subnets before matching: 10.48.0.0/13[/dim]"
    ]


def test_collect_remote_prefix_targets_uses_connection_vm_allocations() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "defaults": {"routing": {"mode": "bgp"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16", "10.11.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 0, "ha_role": "active"},
                    {"gateway_instance_index": 0, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "static",
                "remote_prefixes": ["10.20.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 1, "ha_role": "active"},
                    {"gateway_instance_index": 1, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-c",
                "routing_mode": "static",
                "remote_prefixes": ["10.30.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 2, "ha_role": "active"},
                    {"gateway_instance_index": 2, "ha_role": "passive"},
                ],
            },
        ],
    }

    targets = route_manager._collect_remote_prefix_targets(
        _three_vm_plan(),
        local_cfg,
        {
            0: "alloc-vm0",
            1: "alloc-vm1",
            2: "alloc-vm2",
        },
    )

    assert targets == {
        "10.10.0.0/16": "alloc-vm0",
        "10.11.0.0/16": "alloc-vm0",
        "10.20.0.0/16": "alloc-vm1",
        "10.30.0.0/16": "alloc-vm2",
    }


def test_collect_remote_prefix_targets_rejects_same_prefix_on_different_vms() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "defaults": {"routing": {"mode": "static"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 0, "ha_role": "active"},
                    {"gateway_instance_index": 0, "ha_role": "passive"},
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "static",
                "remote_prefixes": ["10.10.0.0/16"],
                "tunnels": [
                    {"gateway_instance_index": 1, "ha_role": "active"},
                    {"gateway_instance_index": 1, "ha_role": "passive"},
                ],
            },
        ],
    }

    with pytest.raises(
        ValueError, match="same remote prefix is present on more than one gateway VM"
    ):
        route_manager._collect_remote_prefix_targets(
            _three_vm_plan(),
            local_cfg,
            {
                0: "alloc-vm0",
                1: "alloc-vm1",
                2: "alloc-vm2",
            },
        )


def test_summarize_prefix_targets_collapses_exact_ranges_per_allocation() -> None:
    route_manager = RouteManager(project_id="project-test")

    summarized = route_manager._summarize_prefix_targets(
        {
            "10.10.0.0/24": "alloc-a",
            "10.10.1.0/24": "alloc-a",
            "10.10.2.0/24": "alloc-b",
            "10.10.3.0/24": "alloc-a",
        }
    )

    assert summarized == {
        "10.10.0.0/23": "alloc-a",
        "10.10.2.0/24": "alloc-b",
        "10.10.3.0/24": "alloc-a",
    }


def test_filter_prefix_targets_skips_local_and_network_pool_overlaps() -> None:
    route_manager = RouteManager(project_id="project-test")

    filtered, skipped_local, skipped_network_pools = route_manager._filter_prefix_targets(
        {
            "10.20.0.0/16": "alloc-a",
            "10.96.0.0/13": "alloc-a",
            "172.16.88.0/28": "alloc-a",
        },
        local_networks=[ipaddress.IPv4Network("10.96.0.0/13")],
        network_pool_networks=[ipaddress.IPv4Network("172.16.0.0/12")],
    )

    assert filtered == {"10.20.0.0/16": "alloc-a"}
    assert skipped_local == ["10.96.0.0/13"]
    assert skipped_network_pools == [("172.16.88.0/28", "172.16.0.0/12")]


def test_find_redundant_managed_routes_only_returns_covered_vpngw_routes() -> None:
    route_manager = RouteManager(project_id="project-test")
    existing_routes = [
        _fake_route(
            route_id="route-1",
            name="vpngw-10.10.0.0-24",
            cidr="10.10.0.0/24",
            allocation_id="alloc-a",
        ),
        _fake_route(
            route_id="route-2",
            name="vpngw-10.10.1.0-24",
            cidr="10.10.1.0/24",
            allocation_id="alloc-a",
        ),
        _fake_route(
            route_id="route-3",
            name="manual-10.10.2.0-24",
            cidr="10.10.2.0/24",
            allocation_id="alloc-a",
        ),
        _fake_route(
            route_id="route-4",
            name="vpngw-10.10.3.0-24",
            cidr="10.10.3.0/24",
            allocation_id="alloc-b",
        ),
    ]

    redundant = route_manager._find_redundant_managed_routes(
        existing_routes,
        {"10.10.0.0/23": "alloc-a"},
    )

    assert [_metadata_id(route) for route in redundant] == ["route-1", "route-2"]


def test_installed_prefix_targets_require_matching_next_hop() -> None:
    route_manager = RouteManager(project_id="project-test")
    existing_routes = [
        _fake_route(
            route_id="route-1",
            name="manual-summary",
            cidr="10.10.0.0/23",
            allocation_id="alloc-b",
        ),
        _fake_route(
            route_id="route-2",
            name="vpngw-summary",
            cidr="10.20.0.0/23",
            allocation_id="alloc-a",
        ),
    ]

    installed = route_manager._installed_prefix_targets(
        existing_routes,
        {
            "10.10.0.0/23": "alloc-a",
            "10.20.0.0/23": "alloc-a",
        },
    )

    assert installed == {"10.20.0.0/23": "alloc-a"}


def test_find_redundant_managed_covering_routes_returns_broader_summaries_once_exact_routes_exist() -> None:
    route_manager = RouteManager(project_id="project-test")
    existing_routes = [
        _fake_route(
            route_id="route-1",
            name="vpngw-10.10.0.0-23",
            cidr="10.10.0.0/23",
            allocation_id="alloc-a",
        ),
        _fake_route(
            route_id="route-2",
            name="manual-10.20.0.0-23",
            cidr="10.20.0.0/23",
            allocation_id="alloc-a",
        ),
        _fake_route(
            route_id="route-3",
            name="vpngw-10.30.0.0-23",
            cidr="10.30.0.0/23",
            allocation_id="alloc-b",
        ),
    ]

    redundant = route_manager._find_redundant_managed_covering_routes(
        existing_routes,
        {
            "10.10.0.0/24": "alloc-a",
            "10.10.1.0/24": "alloc-a",
            "10.20.0.0/24": "alloc-a",
        },
        {
            "10.10.0.0/24": "alloc-a",
            "10.10.1.0/24": "alloc-a",
            "10.20.0.0/24": "alloc-a",
        },
    )

    assert [_metadata_id(route) for route in redundant] == ["route-1"]


def test_find_redundant_managed_covering_routes_keeps_summary_until_all_exact_routes_exist() -> None:
    route_manager = RouteManager(project_id="project-test")
    existing_routes = [
        _fake_route(
            route_id="route-1",
            name="vpngw-10.10.0.0-23",
            cidr="10.10.0.0/23",
            allocation_id="alloc-a",
        ),
    ]

    redundant = route_manager._find_redundant_managed_covering_routes(
        existing_routes,
        {
            "10.10.0.0/24": "alloc-a",
            "10.10.1.0/24": "alloc-a",
        },
        {
            "10.10.0.0/24": "alloc-a",
        },
    )

    assert redundant == []


def test_write_swap_rollback_spec_persists_subnet_restore_payload(tmp_path: Path) -> None:
    route_manager = RouteManager(project_id="project-test")
    subnet = _fake_subnet(
        name="workload-subnet",
        network_id="net-1",
        explicit_cidrs=["10.96.0.0/13"],
    )

    rollback_path = route_manager._write_swap_rollback_spec(
        rollback_dir=tmp_path,
        subnet_obj=subnet,
        previous_route_table_id="rt-old-123",
    )

    assert rollback_path.exists()
    payload = json.loads(rollback_path.read_text(encoding="utf-8"))
    assert payload == {
        "metadata": {
            "id": "id-workload-subnet",
            "parent_id": "project-test",
            "name": "workload-subnet",
        },
        "spec": {
            "network_id": "net-1",
            "route_table_id": "rt-old-123",
            "ipv4_private_pools": {
                "use_network_pools": False,
                "pools": [{"cidrs": [{"cidr": "10.96.0.0/13"}]}],
            },
        },
    }


def test_get_bgp_learned_routes_queries_only_connection_owner_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.delenv("VPNGW_SSH_USER", raising=False)
    monkeypatch.delenv("VPNGW_SSH_KEY", raising=False)
    local_cfg: _LocalConfig = {
        "gateway_group": {"vm_spec": {}},
    }
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
            },
            {
                "gateway_instance_index": 1,
                "ha_role": "passive",
            },
        ],
    }
    calls: list[str] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        calls.append(cmd[5].removeprefix("ubuntu@"))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                        "10.96.0.0/13": [
                            {"nexthops": [{"ip": "0.0.0.0"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert calls == ["203.0.113.20"]
    assert prefixes == ["10.20.0.0/16"]


def test_get_bgp_learned_routes_honors_custom_ssh_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    ssh_key = tmp_path / "vpngw-key"
    monkeypatch.setenv("VPNGW_SSH_USER", "env-user")
    monkeypatch.setenv("VPNGW_SSH_KEY", "/tmp/env-key")
    local_cfg: _LocalConfig = {
        "gateway_group": {
            "vm_spec": {
                "ssh_username": "operator",
                "ssh_private_key_path": str(ssh_key),
            }
        },
    }
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
                "inner_remote_ip": "169.254.20.2",
            },
        ],
    }
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert prefixes == ["10.20.0.0/16"]
    assert calls == [
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(ssh_key),
            "operator@203.0.113.20",
            "sudo vtysh -c 'show bgp ipv4 unicast json'",
        ]
    ]


def test_get_bgp_learned_routes_honors_env_ssh_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setenv("VPNGW_SSH_USER", "env-user")
    monkeypatch.setenv("VPNGW_SSH_KEY", "/tmp/env-key")
    local_cfg: _LocalConfig = {
        "gateway_group": {"vm_spec": {}},
    }
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
                "inner_remote_ip": "169.254.20.2",
            },
        ],
    }
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert prefixes == ["10.20.0.0/16"]
    assert calls == [
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-i",
            "/tmp/env-key",
            "env-user@203.0.113.20",
            "sudo vtysh -c 'show bgp ipv4 unicast json'",
        ]
    ]


def test_get_bgp_learned_routes_filters_to_connection_peer_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "gateway_group": {"vm_spec": {}},
    }
    conn = {
        "name": "gcp-site-a",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 0,
                "ha_role": "active",
                "inner_remote_ip": "169.254.10.2",
            },
            {
                "gateway_instance_index": 0,
                "ha_role": "passive",
                "inner_remote_ip": "169.254.11.2",
            },
        ],
    }

    def fake_run(cmd, capture_output, text, timeout, input=None):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.10.0.0/16": [
                            {"nexthops": [{"ip": "169.254.10.2"}]},
                        ],
                        "10.11.0.0/16": [
                            {"nexthops": [{"ip": "169.254.11.2"}]},
                        ],
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                        "10.96.0.0/13": [
                            {"nexthops": [{"ip": "0.0.0.0"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert prefixes == ["10.10.0.0/16", "10.11.0.0/16"]


def test_get_bgp_learned_routes_reports_connection_scoped_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "gateway_group": {"vm_spec": {}},
    }
    conn = {
        "name": "gcp-site-a",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 0,
                "ha_role": "active",
                "inner_remote_ip": "169.254.10.2",
            },
            {
                "gateway_instance_index": 0,
                "ha_role": "passive",
                "inner_remote_ip": "169.254.11.2",
            },
        ],
    }

    def fake_run(cmd, capture_output, text, timeout, input=None):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "routes": {
                        "10.10.0.0/16": [
                            {"nexthops": [{"ip": "169.254.10.2"}]},
                        ],
                        "10.11.0.0/16": [
                            {"nexthops": [{"ip": "169.254.11.2"}]},
                        ],
                        "10.20.0.0/16": [
                            {"nexthops": [{"ip": "169.254.20.2"}]},
                        ],
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    prefixes = route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert prefixes == ["10.10.0.0/16", "10.11.0.0/16"]
    captured = capsys.readouterr().out
    assert "Selected 2 connection-scoped BGP route(s)" in captured
    assert "FRR table: 3 route(s)" in captured


def test_list_remote_routes_processes_only_connections_owned_by_current_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "defaults": {"routing": {"mode": "bgp"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 1,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "gateway_instance_index": 1,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    calls: list[tuple[str, int]] = []

    def fake_list_bgp_routes(
        local_cfg, hostname, external_ip, conn_name, conn, instance_index, whitelist, console
    ):
        calls.append((conn_name, instance_index))

    monkeypatch.setattr(route_manager, "_list_bgp_routes", fake_list_bgp_routes)

    route_manager.list_remote_routes(_three_vm_plan(), local_cfg)

    assert calls == [("gcp-site-a", 0), ("gcp-site-b", 1)]


def test_list_remote_routes_connection_filter_targets_selected_connection_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "defaults": {"routing": {"mode": "bgp"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.10.2",
                    },
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.11.2",
                    },
                ],
            },
            {
                "name": "gcp-site-b",
                "routing_mode": "bgp",
                "tunnels": [
                    {
                        "gateway_instance_index": 1,
                        "ha_role": "active",
                        "inner_remote_ip": "169.254.20.2",
                    },
                    {
                        "gateway_instance_index": 1,
                        "ha_role": "passive",
                        "inner_remote_ip": "169.254.21.2",
                    },
                ],
            },
        ],
    }
    calls: list[tuple[str, int]] = []

    def fake_list_bgp_routes(
        local_cfg, hostname, external_ip, conn_name, conn, instance_index, whitelist, console
    ):
        calls.append((conn_name, instance_index))

    monkeypatch.setattr(route_manager, "_list_bgp_routes", fake_list_bgp_routes)

    route_manager.list_remote_routes(
        _three_vm_plan(),
        local_cfg,
        connection_filter="gcp-site-b",
    )

    assert calls == [("gcp-site-b", 1)]


def test_list_bgp_routes_honors_custom_ssh_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    ssh_key = tmp_path / "vpngw-key"
    local_cfg: _LocalConfig = {
        "gateway_group": {
            "vm_spec": {
                "ssh_username": "operator",
                "ssh_private_key_path": str(ssh_key),
            }
        },
    }
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
                "inner_remote_ip": "169.254.20.2",
            },
        ],
    }
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        calls.append(cmd)
        remote_cmd = cmd[-1]
        if remote_cmd == "sudo vtysh -c 'show bgp ipv4 unicast json'":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "routes": {
                            "10.20.0.0/16": [
                                {
                                    "nexthops": [{"ip": "169.254.20.2"}],
                                    "path": "65001",
                                }
                            ],
                        }
                    }
                ),
                stderr="",
            )
        if remote_cmd == "ip route get 169.254.20.2":
            return SimpleNamespace(
                returncode=0,
                stdout="169.254.20.2 dev xfrm1 src 169.254.20.1 uid 1000\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {remote_cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    route_manager._list_bgp_routes(
        local_cfg,
        "nebius-vpn-gw-1",
        "203.0.113.20",
        "gcp-site-b",
        conn,
        1,
        [],
        SimpleNamespace(print=lambda table: None),
    )

    assert calls == [
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(ssh_key),
            "operator@203.0.113.20",
            "sudo vtysh -c 'show bgp ipv4 unicast json'",
        ],
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=5",
            "-i",
            str(ssh_key),
            "operator@203.0.113.20",
            "ip route get 169.254.20.2",
        ],
    ]


def test_list_static_routes_honors_custom_ssh_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    ssh_key = tmp_path / "vpngw-key"
    local_cfg: _LocalConfig = {
        "gateway_group": {
            "vm_spec": {
                "ssh_username": "operator",
                "ssh_private_key_path": str(ssh_key),
            }
        },
    }
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, input=None):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout="10.50.0.0/16 dev xfrm0\n",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    route_manager._list_static_routes(
        local_cfg,
        "nebius-vpn-gw-1",
        "203.0.113.20",
        "static-site",
        ["10.50.0.0/16"],
        SimpleNamespace(print=lambda table: None),
    )

    assert calls == [
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(ssh_key),
            "operator@203.0.113.20",
            "ip route show",
        ]
    ]


def test_find_connection_for_peer_respects_gateway_instance_scope() -> None:
    route_manager = RouteManager(project_id="project-test")
    inst_cfg = SimpleNamespace(instance_index=1)
    connections = [
        {
            "name": "gcp-site-a",
            "tunnels": [
                {
                    "name": "site-a-tunnel-1",
                    "gateway_instance_index": 0,
                    "ha_role": "active",
                    "inner_local_ip": "169.254.10.1",
                    "inner_remote_ip": "169.254.10.2",
                }
            ],
        },
        {
            "name": "gcp-site-b",
            "tunnels": [
                {
                    "name": "site-b-tunnel-1",
                    "gateway_instance_index": 1,
                    "ha_role": "active",
                    "inner_local_ip": "169.254.20.1",
                    "inner_remote_ip": "169.254.10.2",
                }
            ],
        },
    ]

    resolved = route_manager._find_connection_for_peer("169.254.10.2", connections, inst_cfg)

    assert resolved == ("gcp-site-b", "site-b-tunnel-1", "169.254.20.1", "active")
