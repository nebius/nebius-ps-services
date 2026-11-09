from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.provisioning_discovery import foundation
from nebius_vpngw.deploy.provisioning_journal import requests_from_plan
from nebius_vpngw.deploy.provisioning_network import create_default_route, create_subnet
from nebius_vpngw.deploy.vm_manager import VMManager


def meta(identifier, name=None):
    return ResourceMetadata(
        id=identifier, name=name or identifier, parent_id="project-a", resource_version=3
    )


def fixtures(monkeypatch):
    manager = VMManager(project_id="project-a", region="eu-north1")
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=1,
        region="eu-north1",
        external_ips=[],
        vm_spec={},
        subnet={"name": "gateway-subnet", "cidr": "192.0.2.0/24"},
    )
    network = vpc.Network(metadata=meta("network-a"))
    pool = vpc.Pool(
        metadata=meta("pool-a"), spec=vpc.PoolSpec(cidrs=[vpc.PoolCidr(cidr="192.0.2.0/24")])
    )
    state = {"subnets": [], "table": None, "routes": [], "pool": pool}
    manager._resolve_gateway_network = Mock(
        return_value=(network, "network-a", "network", object())
    )
    manager._get_network_private_pools = Mock(return_value=[("pool-a", pool)])
    manager._find_gateway_subnet = Mock(
        side_effect=lambda *a, **kw: next(
            (s for s in state["subnets"] if s.metadata.name == "gateway-subnet"), None
        )
    )
    manager._strict_route_table_by_id = Mock(side_effect=lambda *a: state["table"])
    manager._strict_route_table_by_name = Mock(side_effect=lambda *a: state["table"])
    manager._get_client = Mock(side_effect=AssertionError("planning created another SDK"))
    manager._ensure_network_pool_contains_cidr = Mock(
        side_effect=AssertionError("planning mutated a pool")
    )
    manager._ensure_default_egress_route = Mock(
        side_effect=AssertionError("planning mutated routes")
    )
    from nebius_vpngw.deploy import provisioning_discovery

    monkeypatch.setattr(
        provisioning_discovery,
        "inventory",
        lambda sdk, kind, parent: tuple(state["subnets" if kind == "subnet" else "routes"]),
    )
    monkeypatch.setattr(
        vpc,
        "RouteTableServiceClient",
        lambda sdk: SimpleNamespace(
            create=Mock(side_effect=AssertionError("planning created table"))
        ),
    )
    return manager, spec, state


def test_fresh_network_plan_has_typed_ordered_references_and_no_mutations(monkeypatch):
    manager, spec, _ = fixtures(monkeypatch)
    result = foundation(manager, object(), spec)
    assert [r.type for r in result.requests] == [
        "CreateRouteTableRequest",
        "CreateRouteRequest",
        "CreateSubnetRequest",
    ]
    assert result.subnet == "@gateway-subnet"
    assert result.requests[-1].references[0].slot == "gateway-table"
    assert result.requests[-1].template().spec.route_table_id == "@gateway-table"
    assert set(result.observed) == {"network-a", "pool-a"}
    assert result.inventories == [
        {"kind": "subnet", "parent": "network-a", "ids": []},
        {"kind": "route", "parent": "@gateway-table", "ids": []},
    ]
    frozen = {
        "project_id": "project-a",
        "gateway_name": "gateway",
        "configuration_sha256": "a" * 64,
        "artifact_sha256": "b" * 64,
        "requests": [r.mapping() for r in result.requests],
        "observed": result.observed,
        "inventories": result.inventories,
        "bootstraps": {},
    }
    assert requests_from_plan(frozen) == result.requests


def test_pool_extension_is_frozen_full_copy_before_any_creation(monkeypatch):
    manager, spec, state = fixtures(monkeypatch)
    state["pool"].spec.cidrs = [vpc.PoolCidr(cidr="198.51.100.0/24")]
    state["pool"].metadata.labels = {"operator": "preserved"}
    result = foundation(manager, object(), spec)
    request = result.requests[0].template()
    assert result.requests[0].type == "UpdatePoolRequest"
    assert request.metadata.resource_version == 3
    assert dict(request.metadata.labels) == {"operator": "preserved"}
    assert [x.cidr for x in request.spec.cidrs] == ["198.51.100.0/24", "192.0.2.0/24"]


def test_existing_exact_network_is_observation_only_and_has_no_creation_ownership(monkeypatch):
    manager, spec, state = fixtures(monkeypatch)
    request = create_subnet("project-a", "gateway-subnet", "network-a", "192.0.2.0/24")
    request.spec.route_table_id = "table-a"
    state["subnets"] = [vpc.Subnet(metadata=meta("subnet-a", "gateway-subnet"), spec=request.spec)]
    state["table"] = vpc.RouteTable(
        metadata=meta("table-a"), spec=vpc.RouteTableSpec(network_id="network-a")
    )
    route = create_default_route("table-a")
    route.metadata.id = "route-a"
    route.metadata.resource_version = 4
    state["routes"] = [vpc.Route(metadata=route.metadata, spec=route.spec)]
    result = foundation(manager, object(), spec)
    assert result.requests == () and result.subnet == "subnet-a"
    assert set(result.observed) == {"network-a", "subnet-a", "table-a", "route-a"}


def test_network_overlap_stops_before_any_plan_can_be_executed(monkeypatch):
    manager, spec, state = fixtures(monkeypatch)
    request = create_subnet("project-a", "other-subnet", "network-a", "192.0.2.0/25")
    state["subnets"] = [
        vpc.Subnet(metadata=meta("subnet-other", "other-subnet"), spec=request.spec)
    ]
    with pytest.raises(RuntimeError, match="overlaps"):
        foundation(manager, object(), spec)


@pytest.mark.parametrize("count,groups", [(1, ()), (2, ("group-a", "group-b"))])
def test_member_plan_binds_image_allocations_disk_and_multiple_groups_without_private_data(
    monkeypatch, count, groups
):
    from dataclasses import replace

    from grpc import StatusCode
    from nebius.aio.service_error import RequestError
    from nebius.api.nebius.compute import v1 as compute

    from nebius_vpngw.deploy import provisioning_discovery, security_groups

    manager, spec, _ = fixtures(monkeypatch)
    spec = replace(spec, instance_count=count, security_group_ids=groups)
    manager._get_vm_by_name = Mock(return_value=None)
    manager._resolve_boot_image_id = Mock(return_value="frozen-image")
    manager._get_allocation_by_name = Mock(return_value=None)
    disk = SimpleNamespace(
        get_by_name=Mock(side_effect=RequestError(SimpleNamespace(code=StatusCode.NOT_FOUND)))
    )
    monkeypatch.setattr(compute, "DiskServiceClient", lambda _: disk)
    monkeypatch.setattr(vpc, "AllocationServiceClient", lambda _: object())
    monkeypatch.setattr(
        security_groups, "effective_ids", lambda sdk, network, ids: ids or ("default-group",)
    )
    image = compute.Image(metadata=meta("frozen-image"))

    def read(sdk, kind, identifier):
        if kind == "image":
            return image
        group = vpc.SecurityGroup(
            metadata=meta(identifier), spec=vpc.SecurityGroupSpec(network_id="network-a")
        )
        group.status.state = "READY"
        group.status.default = not groups
        return group

    monkeypatch.setattr(provisioning_discovery, "read", read)
    result = provisioning_discovery.resources(
        manager,
        object(),
        spec,
        tuple(range(count)),
        configuration_sha256="a" * 64,
        artifact_sha256="b" * 64,
        bootstraps={
            f"gateway-{index}": {"template_sha256": "c" * 64, "trust_sha256": "d" * 64}
            for index in range(count)
        },
    )
    planned = requests_from_plan(result)
    assert len(planned) == 3 + 4 * count
    for index in range(count):
        guest = f"gateway-{index}"
        candidate = next(r for r in planned if r.guest == guest)
        request = candidate.template()
        nic = request.spec.network_interfaces[0]
        assert tuple(group.id for group in nic.security_groups) == groups
        assert request.spec.boot_disk.existing_disk.id == "@disk:" + guest
        assert nic.public_ip_address.allocation_id == "@public:" + guest
        assert nic.ip_address.allocation_id == "@private:" + guest
        assert not request.spec.cloud_init_user_data
        boot = next(r for r in planned if r.key == "disk:" + guest).template()
        assert boot.spec.source_image_id == "frozen-image"
    manager._resolve_boot_image_id.assert_called_once()
    assert disk.get_by_name.call_count == count
