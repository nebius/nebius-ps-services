from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy.ordinary_route_discovery import Discovery
from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal
from nebius_vpngw.deploy.route_manager import RouteManager


@pytest.fixture
def scope(tmp_path, monkeypatch):
    def metadata(identifier, name=None):
        return ResourceMetadata(
            id=identifier, parent_id="project", name=name or identifier, resource_version=1
        )

    def subnet(identifier, cidr, table):
        return NS(
            metadata=metadata(identifier),
            spec=vpc.SubnetSpec(network_id="network", route_table_id=table),
            status=NS(state=NS(name="READY"), route_table=NS(id=table), ipv4_private_cidrs=[cidr]),
        )

    gateway = subnet("gateway-subnet", "10.0.0.0/24", "gateway-table")
    workload = subnet("workload-subnet", "10.0.1.0/24", "workload-table")
    network = NS(
        metadata=metadata("network"),
        spec=vpc.NetworkSpec(),
        status=NS(state=NS(name="READY"), default_route_table_id="default-table"),
    )
    table = NS(
        metadata=metadata("workload-table"),
        spec=vpc.RouteTableSpec(network_id="network"),
        status=NS(
            state=NS(name="READY"), default=False, assignment=NS(subnets=["workload-subnet"])
        ),
    )
    compute = NS(
        metadata=metadata("compute"),
        spec=NS(network_interfaces=[NS(name="eth0", subnet_id="gateway-subnet")]),
        status=NS(
            state=NS(name="RUNNING"),
            network_interfaces=[
                NS(name="eth0", ip_address=NS(allocation_id="allocation", address="10.0.0.2/24"))
            ],
        ),
    )
    allocation = NS(
        metadata=metadata("allocation"),
        status=NS(
            state=NS(name="ASSIGNED"),
            assignment=NS(
                network_interface=NS(instance_id="compute", name="eth0"), load_balancer=None
            ),
            details=NS(subnet_id="gateway-subnet", allocated_cidr="10.0.0.2/32"),
        ),
    )
    state = NS(
        subnets=[gateway, workload],
        network=network,
        table=table,
        compute=compute,
        allocation=allocation,
        calls=[],
    )

    def read(value, **kwargs):
        assert 0 < kwargs["timeout"] <= 30
        state.calls.append("read")
        return NS(wait=lambda: value)

    monkeypatch.setattr(
        vpc,
        "NetworkServiceClient",
        lambda sdk: NS(get=lambda request, **kwargs: read(state.network, **kwargs)),
    )
    monkeypatch.setattr(
        vpc,
        "SubnetServiceClient",
        lambda sdk: NS(
            list=lambda request, **kwargs: read(
                NS(items=state.subnets, next_page_token=""), **kwargs
            )
        ),
    )
    monkeypatch.setattr(
        vpc,
        "RouteTableServiceClient",
        lambda sdk: NS(get=lambda request, **kwargs: read(state.table, **kwargs)),
    )
    monkeypatch.setattr(
        vpc,
        "RouteServiceClient",
        lambda sdk: NS(
            list=lambda request, **kwargs: read(NS(items=[], next_page_token=""), **kwargs)
        ),
    )
    monkeypatch.setattr(
        vpc,
        "AllocationServiceClient",
        lambda sdk: NS(get=lambda request, **kwargs: read(state.allocation, **kwargs)),
    )
    manager = NS(
        project_id="project",
        _get_client=lambda: object(),
        inspect_ordinary_targets=lambda names: {name: ("compute", "transport") for name in names},
        get_ha_instance=lambda identifier: state.compute,
    )
    deployment = NS(iter_instance_configs=lambda: iter([NS(hostname="gateway", instance_index=0)]))
    local = {
        "gateway_group": {"network_id": "network", "subnet": {"name": "gateway-subnet"}},
        "gateway": {"local_prefixes": ["10.0.1.0/24"]},
        "defaults": {"routing": {"mode": "static"}},
        "connections": [
            {
                "name": "remote",
                "remote_prefixes": ["192.168.1.0/24"],
                "tunnels": [{"gateway_instance_index": 0}],
            }
        ],
    }
    journal = RouteJournal("project", "gateway", root=tmp_path / "routes")
    return state, Discovery(manager, deployment, local, journal, RouteManager("project"))


def test_discovery_binds_workload_tables_exact_compute_and_primary_allocation_without_writes(scope):
    state, discovery = scope
    result = discovery.observe()
    assert result["active_tables"] == ("workload-table",)
    assert result["allocations"] == {0: "allocation"}
    assert result["conversions"] == []
    assert result["policy"].select({}) == {"192.168.1.0/24": 0}
    assert result["infrastructure"]["gateways"]["0"]["compute_id"] == "compute"
    assert result["policy"].excluded("10.0.0.0/24")
    assert not discovery.journal.root.exists()
    assert state.calls


@pytest.mark.parametrize("change", ["default", "shared"])
def test_default_or_shared_tables_require_reviewed_private_copy(scope, change):
    state, discovery = scope
    if change == "default":
        state.table.status.default = True
    else:
        state.subnets[0].status.route_table.id = "workload-table"
        state.table.status.assignment.subnets.append("gateway-subnet")
    result = discovery.observe()
    assert [item["subnet_id"] for item in result["conversions"]] == ["workload-subnet"]
    assert not discovery.journal.root.exists()


@pytest.mark.parametrize(
    "change",
    [
        "foreign-project",
        "foreign-network",
        "assignment",
        "allocation-owner",
        "allocation-address",
        "compute-state",
        "network-state",
        "subnet-state",
    ],
)
def test_ambiguous_authority_fails_closed_before_route_selection(scope, change):
    state, discovery = scope
    if change == "foreign-project":
        state.table.metadata.parent_id = "foreign"
    elif change == "foreign-network":
        state.table.spec.network_id = "foreign"
    elif change == "assignment":
        state.table.status.assignment.subnets = []
    elif change == "allocation-owner":
        state.allocation.status.assignment.network_interface.instance_id = "other"
    elif change == "allocation-address":
        state.allocation.status.details.allocated_cidr = "10.0.0.3/32"
    elif change == "compute-state":
        state.compute.status.state.name = "STOPPED"
    elif change == "network-state":
        state.network.status.state.name = "CREATING"
    else:
        state.subnets[1].status.state.name = "CREATING"
    with pytest.raises(RuntimeError):
        discovery.observe()
    assert not discovery.journal.root.exists()


def test_completed_historical_tables_are_read_only_for_terminal_receipt_verification(
    scope, monkeypatch
):
    _state, discovery = scope
    monkeypatch.setattr(
        discovery.journal,
        "transaction",
        lambda: {
            "phase": "complete",
            "intent": {"infrastructure": {"table_ids": ["retired-table"]}},
            "operations": {},
        },
    )
    assert discovery.observe()["active_tables"] == ("workload-table",)
    with pytest.raises(RuntimeError, match="approved project or VPC"):
        discovery.observe(completed=True)
