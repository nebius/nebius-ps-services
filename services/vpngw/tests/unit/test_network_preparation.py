from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError
from nebius.api.nebius.vpc.v1 import IPv4PrivateSubnetPools

from nebius_vpngw.deploy.vm_manager import VMManager


def _request(value):
    return SimpleNamespace(wait=lambda: value)


def _operation():
    return SimpleNamespace(
        sync_wait=Mock(),
        successful=Mock(return_value=True),
    )


def _subnet(*, route_table_id: str = "route-table-1", subnet_id: str = "subnet-1"):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id=subnet_id,
            parent_id="project-1",
            name="vpngw-subnet" if subnet_id == "subnet-1" else "other-subnet",
            resource_version=7,
        ),
        spec=SimpleNamespace(
            network_id="network-1",
            route_table_id=route_table_id,
            ipv4_private_pools=IPv4PrivateSubnetPools(use_network_pools=False, pools=[]),
            ipv4_public_pools=None,
        ),
    )


def _route_table():
    return SimpleNamespace(
        metadata=SimpleNamespace(
            id="route-table-1",
            parent_id="project-1",
            name="vpngw-subnet-routing-table",
        ),
        spec=SimpleNamespace(network_id="network-1"),
    )


def _route(*, cidr: str = "0.0.0.0/0", default_egress: bool = True, name: str = "route"):
    return SimpleNamespace(
        metadata=SimpleNamespace(id=f"{name}-id", name=name),
        spec=SimpleNamespace(
            destination=SimpleNamespace(cidr=cidr),
            next_hop=SimpleNamespace(default_egress_gateway=default_egress),
        ),
    )


def _install_clients(monkeypatch, *, subnet_client, route_table_client, route_client) -> None:
    from nebius.api.nebius.vpc import v1 as vpc_v1

    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda _client: subnet_client)
    monkeypatch.setattr(vpc_v1, "RouteTableServiceClient", lambda _client: route_table_client)
    monkeypatch.setattr(vpc_v1, "RouteServiceClient", lambda _client: route_client)


def test_existing_named_subnet_ignores_creation_prefix_when_cidr_is_blank() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=False,
                pools=[
                    SimpleNamespace(
                        cidrs=[SimpleNamespace(cidr="172.16.20.0/28")],
                    )
                ],
            )
        )
    )

    observed = manager._validate_existing_gateway_subnet(
        subnet,
        "vpngw-subnet",
        None,
        24,
    )

    assert observed == ipaddress.ip_network("172.16.20.0/28")


def test_existing_named_subnet_requires_explicit_cidr_exact_match() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    subnet = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                use_network_pools=False,
                pools=[SimpleNamespace(cidrs=[SimpleNamespace(cidr="172.16.20.0/28")])],
            )
        )
    )

    with pytest.raises(ValueError, match="config requires"):
        manager._validate_existing_gateway_subnet(
            subnet,
            "vpngw-subnet",
            ipaddress.ip_network("172.16.21.0/28"),
            24,
        )


def test_strict_subnet_create_rejects_exact_name_race_with_different_cidr(
    monkeypatch,
) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    spec = SimpleNamespace(
        network_id="network-1",
        subnet={"name": "vpngw-subnet", "cidr": None, "prefix_length": 24},
    )
    subnet_client = object()
    conflicting = _subnet()
    conflicting.spec.ipv4_private_pools = SimpleNamespace(
        use_network_pools=False,
        pools=[SimpleNamespace(cidrs=[SimpleNamespace(cidr="172.16.21.0/24")])],
    )
    monkeypatch.setattr(
        manager,
        "_resolve_gateway_network",
        Mock(
            return_value=(
                SimpleNamespace(metadata=SimpleNamespace(id="network-1")),
                "network-1",
                "custom-network",
                subnet_client,
            )
        ),
    )
    monkeypatch.setattr(
        manager,
        "_find_gateway_subnet",
        Mock(side_effect=[None, conflicting]),
    )
    monkeypatch.setattr(
        manager,
        "_select_gateway_subnet_cidr",
        Mock(return_value="172.16.20.0/24"),
    )
    monkeypatch.setattr(manager, "_create_gateway_subnet", Mock(return_value=conflicting))

    with pytest.raises(RuntimeError, match="Failed to create"):
        manager._ensure_vpngw_subnet(object(), spec, strict=True)


def test_strict_subnet_lookup_distinguishes_absence_from_read_failure() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    not_found = RequestError(SimpleNamespace(code=StatusCode.NOT_FOUND))  # type: ignore[arg-type]
    absent_client = SimpleNamespace(
        get_by_name=Mock(side_effect=not_found),
        list_by_network=Mock(return_value=_request(SimpleNamespace(items=[], next_page_token=""))),
    )

    assert (
        manager._find_gateway_subnet(
            absent_client,
            "network-1",
            "vpngw-subnet",
            strict=True,
        )
        is None
    )

    failing_client = SimpleNamespace(
        get_by_name=Mock(side_effect=OSError("read unavailable")),
        list_by_network=Mock(),
    )
    with pytest.raises(RuntimeError, match="Failed to classify"):
        manager._find_gateway_subnet(
            failing_client,
            "network-1",
            "vpngw-subnet",
            strict=True,
        )
    failing_client.list_by_network.assert_not_called()


def test_strict_network_auto_discovery_reads_all_pages_before_selecting(monkeypatch) -> None:
    from nebius.api.nebius.vpc import v1 as vpc_v1

    not_found = RequestError(SimpleNamespace(code=StatusCode.NOT_FOUND))  # type: ignore[arg-type]
    networks = [
        SimpleNamespace(
            metadata=SimpleNamespace(
                id=f"network-{index}",
                name=f"custom-{index}",
                parent_id="project-1",
            )
        )
        for index in (1, 2)
    ]
    network_client = SimpleNamespace(
        get_by_name=Mock(side_effect=not_found),
        list=Mock(
            side_effect=[
                _request(SimpleNamespace(items=[networks[0]], next_page_token="next")),
                _request(SimpleNamespace(items=[networks[1]], next_page_token="")),
            ]
        ),
    )
    monkeypatch.setattr(vpc_v1, "NetworkServiceClient", lambda _client: network_client)
    monkeypatch.setattr(vpc_v1, "SubnetServiceClient", lambda _client: object())

    with pytest.raises(RuntimeError, match="Multiple networks found"):
        VMManager(project_id="project-1", region="eu-north1")._resolve_gateway_network(
            object(),
            SimpleNamespace(network_id=None),
            strict=True,
        )

    assert network_client.list.call_count == 2


def test_strict_subnet_exact_lookup_rejects_network_mismatch_without_listing() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    other_network = SimpleNamespace(
        metadata=SimpleNamespace(
            id="subnet-other",
            parent_id="project-1",
            name="vpngw-subnet",
        ),
        spec=SimpleNamespace(network_id="network-other"),
    )
    subnet_client = SimpleNamespace(
        get_by_name=Mock(return_value=_request(other_network)),
        list_by_network=Mock(),
    )

    with pytest.raises(RuntimeError, match="inexact identity"):
        manager._find_gateway_subnet(
            subnet_client,
            "network-1",
            "vpngw-subnet",
            strict=True,
        )

    subnet_client.list_by_network.assert_not_called()


def test_subnet_cidr_inventory_reads_every_page() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    target = _subnet()
    target.spec.ipv4_private_pools = SimpleNamespace(
        use_network_pools=False,
        pools=[SimpleNamespace(cidrs=[SimpleNamespace(cidr="172.16.20.0/28")])],
    )
    subnet_client = SimpleNamespace(
        list_by_network=Mock(
            side_effect=[
                _request(SimpleNamespace(items=[], next_page_token="next")),
                _request(SimpleNamespace(items=[target], next_page_token="")),
            ]
        )
    )

    assert manager._list_existing_subnet_networks(
        subnet_client,
        "network-1",
    ) == [ipaddress.ip_network("172.16.20.0/28")]
    assert subnet_client.list_by_network.call_count == 2


def test_strict_pool_extension_preserves_concurrent_cidrs_and_binds_version(
    monkeypatch,
) -> None:
    from nebius.api.nebius.vpc import v1 as vpc_v1

    def pool(version: int, *cidrs: str):
        return SimpleNamespace(
            id="pool-1",
            metadata=SimpleNamespace(
                id="pool-1",
                parent_id="project-1",
                name="private-pool",
                resource_version=version,
            ),
            spec=SimpleNamespace(
                cidrs=[SimpleNamespace(cidr=cidr) for cidr in cidrs],
                version=None,
                visibility=None,
                source_pool_id="",
            ),
        )

    initial = pool(1, "10.0.0.0/8")
    current = pool(2, "10.0.0.0/8", "172.16.0.0/16")
    verified = pool(3, "10.0.0.0/8", "172.16.0.0/16", "192.168.1.0/24")
    pool_client = SimpleNamespace(
        get=Mock(
            side_effect=[
                _request(initial),
                _request(current),
                _request(verified),
            ]
        ),
        update=Mock(return_value=_request(_operation())),
    )
    monkeypatch.setattr(vpc_v1, "PoolServiceClient", lambda _client: pool_client)
    network = SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                pools=[SimpleNamespace(pool_id="pool-1")],
            )
        )
    )

    VMManager(project_id="project-1", region="eu-north1")._ensure_network_pool_contains_cidr(
        object(),
        network,
        ipaddress.ip_network("192.168.1.0/24"),
        strict=True,
    )

    request = pool_client.update.call_args.args[0]
    assert request.metadata.resource_version == 2
    assert {item.cidr for item in request.spec.cidrs} == {
        "10.0.0.0/8",
        "172.16.0.0/16",
        "192.168.1.0/24",
    }


def test_strict_route_reuses_exact_default_route_without_mutation(monkeypatch) -> None:
    subnet = _subnet()
    route_table = _route_table()
    subnet_client = SimpleNamespace(
        get=Mock(return_value=_request(subnet)),
        list_by_network=Mock(
            return_value=_request(SimpleNamespace(items=[subnet], next_page_token=""))
        ),
    )
    route_table_client = SimpleNamespace(get=Mock(return_value=_request(route_table)))
    route_client = SimpleNamespace(
        list=Mock(return_value=_request(SimpleNamespace(items=[_route()], next_page_token=""))),
        create=Mock(),
    )
    _install_clients(
        monkeypatch,
        subnet_client=subnet_client,
        route_table_client=route_table_client,
        route_client=route_client,
    )

    VMManager(project_id="project-1", region="eu-north1")._ensure_vpngw_route_table(
        object(),
        "subnet-1",
    )

    route_client.create.assert_not_called()
    assert not hasattr(subnet_client, "update")


@pytest.mark.parametrize(
    "provider_error",
    [
        RequestError(SimpleNamespace(code=StatusCode.PERMISSION_DENIED)),  # type: ignore[arg-type]
        RequestError(SimpleNamespace(code=StatusCode.UNAVAILABLE)),  # type: ignore[arg-type]
        RuntimeError("NOT_FOUND from an untyped provider boundary"),
    ],
)
def test_ordinary_route_preparation_cannot_replace_an_unreadable_attached_table(
    monkeypatch,
    provider_error: Exception,
) -> None:
    subnet = _subnet()
    subnet_client = SimpleNamespace(get=Mock(return_value=_request(subnet)), update=Mock())
    route_table_client = SimpleNamespace(
        get=Mock(return_value=_request(provider_error)),
        create=Mock(),
    )
    route_table_client.get.return_value.wait = Mock(side_effect=provider_error)
    route_client = SimpleNamespace(create=Mock())
    _install_clients(
        monkeypatch,
        subnet_client=subnet_client,
        route_table_client=route_table_client,
        route_client=route_client,
    )
    manager = VMManager(project_id="project-1", region="eu-north1")
    with pytest.raises(RuntimeError, match="Attached route table could not be read") as raised:
        manager._ensure_vpngw_route_table(object(), "subnet-1")

    assert raised.value.__cause__ is provider_error
    route_table_client.create.assert_not_called()
    route_client.create.assert_not_called()
    subnet_client.update.assert_not_called()


def test_strict_route_adds_missing_default_only_for_exclusive_table(monkeypatch) -> None:
    subnet = _subnet()
    route_table = _route_table()
    exact_route = _route(name="different-name")
    subnet_client = SimpleNamespace(
        get=Mock(return_value=_request(subnet)),
        list_by_network=Mock(
            return_value=_request(SimpleNamespace(items=[subnet], next_page_token=""))
        ),
    )
    route_table_client = SimpleNamespace(get=Mock(return_value=_request(route_table)))
    route_client = SimpleNamespace(
        list=Mock(
            side_effect=[
                _request(SimpleNamespace(items=[], next_page_token="")),
                _request(SimpleNamespace(items=[exact_route], next_page_token="")),
            ]
        ),
        create=Mock(return_value=_request(_operation())),
    )
    _install_clients(
        monkeypatch,
        subnet_client=subnet_client,
        route_table_client=route_table_client,
        route_client=route_client,
    )

    VMManager(project_id="project-1", region="eu-north1")._ensure_vpngw_route_table(
        object(),
        "subnet-1",
    )

    route_client.create.assert_called_once()


def test_strict_route_refuses_to_modify_shared_or_conflicting_table(
    monkeypatch,
) -> None:
    subnet = _subnet()
    other = _subnet(subnet_id="subnet-2")
    route_table = _route_table()
    route_table_client = SimpleNamespace(get=Mock(return_value=_request(route_table)))

    shared_subnet_client = SimpleNamespace(
        get=Mock(return_value=_request(subnet)),
        list_by_network=Mock(
            return_value=_request(SimpleNamespace(items=[subnet, other], next_page_token=""))
        ),
    )
    shared_route_client = SimpleNamespace(
        list=Mock(return_value=_request(SimpleNamespace(items=[_route()], next_page_token=""))),
        create=Mock(),
    )
    _install_clients(
        monkeypatch,
        subnet_client=shared_subnet_client,
        route_table_client=route_table_client,
        route_client=shared_route_client,
    )
    manager = VMManager(project_id="project-1", region="eu-north1")

    with pytest.raises(RuntimeError, match="not exclusively assigned"):
        manager._ensure_vpngw_route_table(object(), "subnet-1")
    shared_route_client.list.assert_not_called()
    shared_route_client.create.assert_not_called()

    exclusive_subnet_client = SimpleNamespace(
        get=Mock(return_value=_request(subnet)),
        list_by_network=Mock(
            return_value=_request(SimpleNamespace(items=[subnet], next_page_token=""))
        ),
    )
    conflicting_route_client = SimpleNamespace(
        list=Mock(
            return_value=_request(
                SimpleNamespace(
                    items=[_route(default_egress=False, name="foreign-default")],
                    next_page_token="",
                )
            )
        ),
        create=Mock(),
    )
    _install_clients(
        monkeypatch,
        subnet_client=exclusive_subnet_client,
        route_table_client=route_table_client,
        route_client=conflicting_route_client,
    )

    with pytest.raises(RuntimeError, match="conflicting"):
        manager._ensure_vpngw_route_table(object(), "subnet-1")
    conflicting_route_client.create.assert_not_called()


def test_strict_route_creates_canonical_table_route_and_attachment_once(monkeypatch) -> None:
    before = _subnet(route_table_id="")
    after = _subnet(route_table_id="route-table-1")
    route_table = _route_table()
    exact_route = _route(name="default-egress")
    subnet_client = SimpleNamespace(
        get=Mock(side_effect=[_request(before), _request(after)]),
        list_by_network=Mock(
            side_effect=[
                _request(SimpleNamespace(items=[before], next_page_token="")),
                _request(SimpleNamespace(items=[after], next_page_token="")),
            ]
        ),
        update=Mock(return_value=_request(_operation())),
    )
    not_found = RequestError(SimpleNamespace(code=StatusCode.NOT_FOUND))  # type: ignore[arg-type]
    route_table_client = SimpleNamespace(
        get_by_name=Mock(side_effect=[not_found, _request(route_table)]),
        create=Mock(return_value=_request(_operation())),
        get=Mock(return_value=_request(route_table)),
    )
    route_client = SimpleNamespace(
        list=Mock(
            side_effect=[
                _request(SimpleNamespace(items=[], next_page_token="")),
                _request(SimpleNamespace(items=[exact_route], next_page_token="")),
                _request(SimpleNamespace(items=[exact_route], next_page_token="")),
            ]
        ),
        create=Mock(return_value=_request(_operation())),
    )
    _install_clients(
        monkeypatch,
        subnet_client=subnet_client,
        route_table_client=route_table_client,
        route_client=route_client,
    )

    VMManager(project_id="project-1", region="eu-north1")._ensure_vpngw_route_table(
        object(),
        "subnet-1",
    )

    route_table_client.create.assert_called_once()
    route_client.create.assert_called_once()
    subnet_client.update.assert_called_once()
