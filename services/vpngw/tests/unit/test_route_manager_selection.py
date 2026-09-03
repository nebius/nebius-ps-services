from __future__ import annotations

import ipaddress
import json
import subprocess
import typing as t
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from nebius_vpngw.config_loader import (
    GatewayGroupSpec,
    InstanceResolvedConfig,
    ResolvedDeploymentPlan,
    connection_static_remote_prefixes,
)
from nebius_vpngw.deploy.route_manager import RouteManagementError, RouteManager
from nebius_vpngw.deploy.vm_ha_routes import ManagedRouteKind, ManagedRouteOwnership
from nebius_vpngw.schema import VMHARouteTarget

_LocalConfig = dict[str, t.Any]


def test_add_routes_normalizes_unexpected_sdk_failure(monkeypatch) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_add_routes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )

    with pytest.raises(RouteManagementError, match="postcondition") as caught:
        route_manager.add_routes(SimpleNamespace(), {})

    assert "sensitive detail" not in str(caught.value)


def test_mutating_route_inventory_reads_every_page() -> None:
    class Request:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    requests: list[Request] = []

    def list_page(request: Request) -> SimpleNamespace:
        requests.append(request)
        return SimpleNamespace(
            items=[SimpleNamespace(metadata=SimpleNamespace(id=f"route-{len(requests)}"))],
            next_page_token="next" if not request.page_token else "",
        )

    routes = RouteManager(project_id="project-test")._list_route_table_routes(
        SimpleNamespace(List=list_page),
        SimpleNamespace(ListRoutesRequest=Request),
        route_table_id="route-table-1",
    )

    assert [route.metadata.id for route in routes] == ["route-1", "route-2"]
    assert [request.page_token for request in requests] == ["", "next"]
    assert all(request.parent_id == "route-table-1" for request in requests)
    assert all(request.page_size == 1000 for request in requests)


def test_route_refresh_failure_after_first_write_stops_later_writes() -> None:
    from nebius.api.nebius.common.v1 import metadata_pb2
    from nebius.api.nebius.vpc.v1 import route_pb2, route_service_pb2

    route_stub = SimpleNamespace(
        Create=Mock(return_value=SimpleNamespace()),
        List=Mock(side_effect=OSError("inventory unavailable")),
    )

    with pytest.raises(RuntimeError, match="Route table route inventory is unavailable"):
        RouteManager(project_id="project-test")._reconcile_route_table(
            route_stub,
            route_service_pb2,
            route_pb2,
            metadata_pb2,
            route_table_id="route-table-1",
            prefix_targets={
                "10.10.0.0/16": "allocation-1",
                "10.20.0.0/16": "allocation-1",
            },
            summarize=False,
            initial_routes=(),
        )

    route_stub.Create.assert_called_once()


def test_route_next_hops_use_exact_configured_instance_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebius.api.nebius.compute.v1 import instance_service_pb2_grpc

    requests: list[object] = []

    class Stub:
        def __init__(self, _channel: object) -> None:
            pass

        def GetByName(self, request: object) -> SimpleNamespace:
            requests.append(request)
            name = str(request.name)  # type: ignore[attr-defined]
            index = int(name.rsplit("-", 1)[-1])
            return SimpleNamespace(
                metadata=SimpleNamespace(
                    id=f"compute-{index}",
                    name=name,
                    parent_id="project-test",
                ),
                status=SimpleNamespace(
                    network_interfaces=[
                        SimpleNamespace(
                            ip_address=SimpleNamespace(
                                address=f"10.0.0.{index + 1}/24",
                                allocation_id=f"allocation-{index}",
                            )
                        )
                    ]
                ),
            )

    monkeypatch.setattr(instance_service_pb2_grpc, "InstanceServiceStub", Stub)
    plan = SimpleNamespace(
        iter_instance_configs=lambda: (
            SimpleNamespace(hostname="gateway-0", instance_index=0),
            SimpleNamespace(hostname="gateway-1", instance_index=1),
        )
    )

    result = RouteManager(project_id="project-test")._find_gateway_private_allocations_by_index(
        object(), plan
    )

    assert result == {0: "allocation-0", 1: "allocation-1"}
    assert [(request.parent_id, request.name) for request in requests] == [  # type: ignore[attr-defined]
        ("project-test", "gateway-0"),
        ("project-test", "gateway-1"),
    ]


def test_static_remote_prefixes_union_connection_and_enabled_member_tunnels() -> None:
    connection = {
        "remote_prefixes": ["10.10.0.0/24"],
        "tunnels": [
            {
                "gateway_instance_index": 0,
                "static_routes": {"remote_prefixes": ["10.20.0.0/24"]},
            },
            {
                "gateway_instance_index": 1,
                "static_routes": {"remote_prefixes": ["10.30.0.0/24"]},
            },
            {
                "gateway_instance_index": 0,
                "ha_role": "disable",
                "static_routes": {"remote_prefixes": ["10.40.0.0/24"]},
            },
        ],
    }

    assert connection_static_remote_prefixes(connection) == [
        "10.10.0.0/24",
        "10.20.0.0/24",
        "10.30.0.0/24",
    ]
    assert connection_static_remote_prefixes(connection, instance_index=0) == [
        "10.10.0.0/24",
        "10.20.0.0/24",
    ]
    assert connection_static_remote_prefixes(connection, instance_index=1) == [
        "10.10.0.0/24",
        "10.30.0.0/24",
    ]


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
        metadata=SimpleNamespace(
            name=name,
            id=f"id-{name}",
            parent_id="project-test",
        ),
        spec=SimpleNamespace(
            network_id=network_id,
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=use_network_pools,
                pools=pools,
            ),
        ),
        status=SimpleNamespace(
            ipv4_private_pools=[SimpleNamespace(cidrs=status_cidrs or explicit_cidrs or [])],
            ipv4_private_cidrs=status_cidrs or explicit_cidrs or [],
            route_table=SimpleNamespace(id="", default=True),
        ),
    )


def test_status_subnet_cidrs_prefer_current_pool_shape_without_legacy_access() -> None:
    class CurrentStatus:
        ipv4_private_pools = [SimpleNamespace(cidrs=["10.20.0.0/16"])]

        @property
        def ipv4_private_cidrs(self) -> list[str]:
            raise AssertionError("deprecated status field must not be read")

    subnet = SimpleNamespace(status=CurrentStatus())

    assert RouteManager._extract_status_subnet_cidrs(subnet) == [
        ipaddress.ip_network("10.20.0.0/16")
    ]


def test_status_subnet_cidrs_support_legacy_sdk_shape() -> None:
    subnet = SimpleNamespace(status=SimpleNamespace(ipv4_private_cidrs=["10.30.0.0/16"]))

    assert RouteManager._extract_status_subnet_cidrs(subnet) == [
        ipaddress.ip_network("10.30.0.0/16")
    ]


def _fake_route(*, route_id: str, name: str, cidr: str, allocation_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(id=route_id, name=name),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr=cidr),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=allocation_id),
                default_egress_gateway=False,
            ),
        ),
    )


def test_list_routes_uses_public_sdk_clients_and_reads_all_pages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import nebius.api.nebius.vpc.v1 as vpc_v1

    class FakeRequest:
        def __init__(self, response: SimpleNamespace) -> None:
            self.response = response

        def wait(self) -> SimpleNamespace:
            return self.response

    class FakePagedClient:
        def __init__(
            self,
            pages: dict[str, SimpleNamespace],
            *,
            by_name: SimpleNamespace | None = None,
        ) -> None:
            self.pages = pages
            self.by_name = by_name
            self.page_tokens: list[str] = []
            self.requested_names: list[str] = []

        def list(self, request: object) -> FakeRequest:
            page_token = str(getattr(request, "page_token", "") or "")
            self.page_tokens.append(page_token)
            return FakeRequest(self.pages[page_token])

        def get_by_name(self, request: object) -> FakeRequest:
            self.requested_names.append(str(getattr(request, "name", "") or ""))
            if self.by_name is None:
                raise RuntimeError("resource not found")
            return FakeRequest(self.by_name)

    class FakeSDK:
        def __init__(self) -> None:
            self.closed = False

        def sync_close(self) -> None:
            self.closed = True

    allocation = SimpleNamespace(
        metadata=SimpleNamespace(id="allocation-gateway-0"),
        status=SimpleNamespace(details=SimpleNamespace(allocated_cidr="172.16.30.10/32")),
    )
    subnet = _fake_subnet(
        name="workload-subnet",
        network_id="network-1",
        explicit_cidrs=["10.20.0.0/16"],
    )
    subnet.status.route_table = SimpleNamespace(id="route-table-1", default=False)
    gateway_subnet = _fake_subnet(
        name="vpngw-subnet",
        network_id="network-1",
        explicit_cidrs=["172.16.30.0/28"],
    )
    allocation_route = _fake_route(
        route_id="route-1",
        name="vpngw-10-50-0-0-16",
        cidr="10.50.0.0/16",
        allocation_id="allocation-gateway-0",
    )
    default_route = SimpleNamespace(
        metadata=SimpleNamespace(id="route-2", name="default-egress"),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr="0.0.0.0/0"),
            next_hop=SimpleNamespace(
                allocation=SimpleNamespace(id=""),
                default_egress_gateway=True,
            ),
        ),
    )
    allocation_client = FakePagedClient(
        {
            "": SimpleNamespace(items=[], next_page_token="allocations-2"),
            "allocations-2": SimpleNamespace(items=[allocation], next_page_token=""),
        }
    )
    subnet_client = FakePagedClient(
        {
            "": SimpleNamespace(items=[], next_page_token="subnets-2"),
            "subnets-2": SimpleNamespace(items=[subnet], next_page_token=""),
        },
        by_name=gateway_subnet,
    )
    route_client = FakePagedClient(
        {
            "": SimpleNamespace(items=[allocation_route], next_page_token="routes-2"),
            "routes-2": SimpleNamespace(items=[default_route], next_page_token=""),
        }
    )
    sdk = FakeSDK()
    route_manager = RouteManager(project_id="project-test", auth_token="token-test")
    bgp_calls: list[tuple[ResolvedDeploymentPlan, _LocalConfig]] = []

    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: sdk, raising=False)
    monkeypatch.setattr(
        route_manager,
        "_list_bgp_advertised_routes",
        lambda plan, local_cfg, _console, **_kwargs: bgp_calls.append((plan, local_cfg)),
    )
    monkeypatch.setattr(
        vpc_v1,
        "AllocationServiceClient",
        lambda client: allocation_client,
    )
    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda client: subnet_client)
    monkeypatch.setattr(vpc_v1, "RouteServiceClient", lambda client: route_client)

    plan = _three_vm_plan()
    local_cfg: _LocalConfig = {
        "gateway": {"local_prefixes": ["10.20.0.0/16"]},
        "gateway_group": {
            "subnet": {"name": "vpngw-subnet"},
        },
    }

    route_manager.list_routes(plan, local_cfg)

    output = capsys.readouterr().out
    assert "Subnet: workload-subnet" in output
    assert "Route Table ID: route-table-1" in output
    assert "10.50.0.0/16" in output
    assert "172.16.30.10" in output
    assert "0.0.0.0/0" in output
    assert "default-egress" in output
    assert allocation_client.page_tokens == ["", "allocations-2"]
    assert subnet_client.page_tokens == ["", "subnets-2"]
    assert subnet_client.requested_names == ["vpngw-subnet"]
    assert route_client.page_tokens == ["", "routes-2"]
    assert bgp_calls == [(plan, local_cfg)]
    assert sdk.closed is True


def test_list_routes_buffers_all_nebius_route_tables_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import nebius.api.nebius.vpc.v1 as vpc_v1

    class Request:
        def __init__(self, response: object) -> None:
            self.response = response

        def wait(self) -> object:
            return self.response

    first_subnet = _fake_subnet(
        name="workload-one",
        network_id="network-1",
        explicit_cidrs=["10.20.0.0/16"],
    )
    first_subnet.status.route_table = SimpleNamespace(id="route-table-1", default=False)
    second_subnet = _fake_subnet(
        name="workload-two",
        network_id="network-1",
        explicit_cidrs=["10.30.0.0/16"],
    )
    second_subnet.status.route_table = SimpleNamespace(id="route-table-2", default=False)

    class AllocationClient:
        def list(self, _request: object) -> Request:
            return Request(SimpleNamespace(items=[], next_page_token=""))

    class SubnetClient:
        def list(self, _request: object) -> Request:
            return Request(
                SimpleNamespace(
                    items=[first_subnet, second_subnet],
                    next_page_token="",
                )
            )

    class RouteClient:
        def list(self, request: object) -> Request:
            parent_id = str(getattr(request, "parent_id", "") or "")
            page_token = str(getattr(request, "page_token", "") or "")
            if parent_id == "route-table-1":
                return Request(
                    SimpleNamespace(
                        items=[
                            _fake_route(
                                route_id="route-one",
                                name="route-one",
                                cidr="10.50.0.0/16",
                                allocation_id="allocation-one",
                            )
                        ],
                        next_page_token="",
                    )
                )
            if not page_token:
                return Request(
                    SimpleNamespace(
                        items=[
                            _fake_route(
                                route_id="route-two",
                                name="route-two",
                                cidr="10.60.0.0/16",
                                allocation_id="allocation-two",
                            )
                        ],
                        next_page_token="next",
                    )
                )
            raise OSError("later route page unavailable")

    sdk = SimpleNamespace(sync_close=Mock())
    route_manager = RouteManager(project_id="project-test", auth_token="token-test")
    monkeypatch.setattr(route_manager, "_create_read_sdk", lambda: sdk)
    monkeypatch.setattr(
        route_manager,
        "_resolve_target_network_id_with_sdk",
        lambda *_args, **_kwargs: "network-1",
    )
    bgp = Mock()
    monkeypatch.setattr(route_manager, "_list_bgp_advertised_routes", bgp)
    monkeypatch.setattr(vpc_v1, "AllocationServiceClient", lambda _sdk: AllocationClient())
    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda _sdk: SubnetClient())
    monkeypatch.setattr(vpc_v1, "RouteServiceClient", lambda _sdk: RouteClient())

    with pytest.raises(RuntimeError, match="VPC route resource inventory is unavailable"):
        route_manager.list_routes(
            _three_vm_plan(),
            {
                "gateway": {"local_prefixes": ["10.20.0.0/16", "10.30.0.0/16"]},
                "gateway_group": {"subnet": {"name": "vpngw-subnet"}},
            },
        )

    assert "Subnet:" not in capsys.readouterr().out
    bgp.assert_not_called()
    sdk.sync_close.assert_called_once_with()


def _metadata_name(resource: object) -> str:
    return str(vars(vars(resource)["metadata"])["name"])


def _metadata_id(resource: object) -> str:
    return str(vars(vars(resource)["metadata"])["id"])


def test_vm_ha_route_selection_requires_explicit_route_id_ownership() -> None:
    route_manager = RouteManager(project_id="project-test")
    ledger_route = _fake_route(
        route_id="route-owned",
        name="ordinary-name",
        cidr="10.10.0.0/16",
        allocation_id="shared-allocation",
    )
    name_only_route = _fake_route(
        route_id="route-unowned",
        name="vpngw-name-is-not-authority",
        cidr="10.20.0.0/16",
        allocation_id="shared-allocation",
    )

    snapshots = route_manager._vm_ha_owned_route_snapshots(
        (ledger_route, name_only_route),
        ownership_by_route_id={
            "route-owned": ManagedRouteOwnership(
                cluster_id="cluster-a",
                kind=ManagedRouteKind.STATIC,
                route_target=VMHARouteTarget(
                    project_id="project-test",
                    network_id="network-test",
                    workload_subnet_id="subnet-test",
                    route_table_id="route-table-test",
                ),
            )
        },
    )

    assert [(snapshot.route_id, snapshot.prefix) for snapshot in snapshots] == [
        ("route-owned", "10.10.0.0/16")
    ]


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


def test_select_local_prefix_subnets_excludes_inherited_status_cidrs_owned_by_other_subnets() -> (
    None
):
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


def test_select_local_prefix_subnets_keeps_inherited_status_cidrs_after_sanitizing_explicit_ones() -> (
    None
):
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


def test_collect_remote_prefix_targets_includes_tunnel_only_static_prefixes() -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {
        "defaults": {"routing": {"mode": "static"}},
        "connections": [
            {
                "name": "gcp-site-a",
                "routing_mode": "static",
                "tunnels": [
                    {
                        "gateway_instance_index": 0,
                        "ha_role": "active",
                        "static_routes": {"remote_prefixes": ["10.40.0.0/16"]},
                    }
                ],
            }
        ],
    }

    assert route_manager._collect_remote_prefix_targets(
        _three_vm_plan(),
        local_cfg,
        {0: "alloc-vm0"},
    ) == {"10.40.0.0/16": "alloc-vm0"}


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


def test_find_redundant_managed_covering_routes_returns_broader_summaries_once_exact_routes_exist() -> (
    None
):
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


def test_find_redundant_managed_covering_routes_keeps_summary_until_all_exact_routes_exist() -> (
    None
):
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
        calls.append(cmd[-2].removeprefix("ubuntu@"))
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


@pytest.mark.parametrize(
    ("ssh_outcome", "message"),
    (
        (
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="private remote diagnostic",
            ),
            "failed",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout="{private remote diagnostic",
                stderr="",
            ),
            "invalid JSON",
        ),
        (
            subprocess.TimeoutExpired(cmd="ssh", timeout=15),
            "timed out",
        ),
        (
            RuntimeError("private remote diagnostic"),
            "failed",
        ),
    ),
)
def test_get_bgp_learned_routes_fails_closed_on_query_error(
    monkeypatch: pytest.MonkeyPatch,
    ssh_outcome: object,
    message: str,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    local_cfg: _LocalConfig = {"gateway_group": {"vm_spec": {}}}
    conn = {
        "name": "gcp-site-b",
        "routing_mode": "bgp",
        "tunnels": [
            {
                "gateway_instance_index": 1,
                "ha_role": "active",
                "inner_remote_ip": "169.254.20.2",
            }
        ],
    }

    def fake_run_ssh(*_args, **_kwargs):
        if isinstance(ssh_outcome, BaseException):
            raise ssh_outcome
        return ssh_outcome

    monkeypatch.setattr(route_manager, "_run_ssh", fake_run_ssh)

    with pytest.raises(RouteManagementError, match=message) as caught:
        route_manager._get_bgp_learned_routes(_three_vm_plan(), conn, local_cfg)

    assert "private remote diagnostic" not in str(caught.value)


def test_get_bgp_learned_routes_honors_custom_ssh_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    ssh_key = tmp_path / "vpngw-key"
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
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
            "-i",
            str(ssh_key),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
            "operator@203.0.113.20",
            "sudo vtysh -c 'show bgp ipv4 unicast json'",
        ]
    ]


def test_get_bgp_learned_routes_honors_env_ssh_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
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
            "-i",
            "/tmp/env-key",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
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
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
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
            "-i",
            str(ssh_key),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
            "operator@203.0.113.20",
            "sudo vtysh -c 'show bgp ipv4 unicast json'",
        ],
        [
            "ssh",
            "-i",
            str(ssh_key),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "LogLevel=ERROR",
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
    monkeypatch.delenv("VPNGW_SSH_KNOWN_HOSTS_FILE", raising=False)
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
            "-i",
            str(ssh_key),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
            "operator@203.0.113.20",
            "ip route show",
        ]
    ]


def test_list_static_routes_normalizes_iproute2_host_route_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_manager = RouteManager(project_id="project-test")
    monkeypatch.setattr(
        route_manager,
        "_run_ssh",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="10.10.0.2 dev xfrm1\n",
            stderr="",
        ),
    )
    console = Console(record=True, color_system=None, width=160)

    route_manager._list_static_routes(
        {},
        "nebius-vpn-gw-1",
        "203.0.113.20",
        "static-site",
        ["10.10.0.2/32", "10.60.0.0/16"],
        console,
    )

    rendered = " ".join(console.export_text().replace("│", " ").split())
    assert "10.10.0.2/32 installed - xfrm1" in rendered
    assert "10.60.0.0/16 missing - -" in rendered


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
