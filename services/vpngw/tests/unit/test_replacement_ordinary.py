from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import (
    Instance,
    InstanceSpec,
    InstanceStatus,
    IPAddressStatus,
    NetworkInterfaceSpec,
    NetworkInterfaceStatus,
    PublicIPAddress,
    PublicIPAddressStatus,
    ResourcesSpec,
)

from nebius_vpngw.deploy.replacement_cloud import single_nic_status, source_spec_digest
from nebius_vpngw.deploy.replacement_ordinary import OrdinaryReplacement, projection_digest


def instance():
    return Instance(
        metadata=ResourceMetadata(id="compute-source", parent_id="project-test"),
        spec=InstanceSpec(
            resources=ResourcesSpec(platform="cpu-d3", preset="4vcpu-16gb"),
            network_interfaces=[
                NetworkInterfaceSpec(
                    name="eth0", subnet_id="subnet-test", public_ip_address=PublicIPAddress()
                )
            ],
        ),
        status=InstanceStatus(
            network_interfaces=[
                NetworkInterfaceStatus(
                    name="eth0",
                    index=0,
                    ip_address=IPAddressStatus(allocation_id="allocation-private"),
                    public_ip_address=PublicIPAddressStatus(
                        allocation_id="allocation-public", static=False
                    ),
                )
            ]
        ),
    )


def test_live_nic_status_is_authoritative_for_automatically_allocated_addresses():
    source = instance()
    assert not source.spec.network_interfaces[0].public_ip_address.allocation_id
    assert single_nic_status(source).public_ip_address.allocation_id == "allocation-public"
    source.status.network_interfaces[0].name = "unexpected"
    with pytest.raises(RuntimeError, match="authoritative NIC"):
        single_nic_status(source)


def test_public_retention_pin_is_the_only_source_spec_change_excluded_from_digest():
    source = instance()
    before = source_spec_digest(source)
    source.spec.network_interfaces[0].public_ip_address = PublicIPAddress(
        allocation_id="allocation-public", static=True
    )
    assert source_spec_digest(source) == before
    source.spec.resources.preset = "8vcpu-32gb"
    assert source_spec_digest(source) != before


def test_authorization_comparison_changes_only_the_physical_compute_identity():
    old = {
        "compute_id": "compute-source",
        "files": {"/home/operator/.ssh/authorized_keys": {"content": "customer key"}},
        "operators": {},
    }
    new = dict(old, compute_id="compute-candidate")
    assert projection_digest(new, "compute-source") == projection_digest(old, "compute-source")
    changed = dict(new, files={})
    assert projection_digest(changed, "compute-source") != projection_digest(old, "compute-source")


def journal(step="stop-old"):
    return SimpleNamespace(
        state={
            "transaction": {
                "member": 0,
                "plan": {
                    "operation_id": "a" * 64,
                    "members": [{"logical_name": "gateway-0", "compute_id": "compute-source"}],
                },
                "results": {"0:prepare": {"compute_id": "compute-candidate"}},
            }
        }
    )


def test_pre_cutover_preparation_failure_never_stops_source(monkeypatch):
    from nebius_vpngw.deploy import replacement_ordinary as module

    ports = OrdinaryReplacement.__new__(OrdinaryReplacement)
    ports.sdk = object()
    cloud = Mock()
    monkeypatch.setattr(module, "Cloud", lambda *_args: cloud)
    ports._prepared = Mock(side_effect=RuntimeError("candidate became active or drifted"))
    with pytest.raises(RuntimeError, match="candidate"):
        ports.execute(journal(), step="stop-old", effect_id="effect")
    cloud.stop.assert_not_called()


def test_group_admission_waits_for_every_member_service_proof():
    ports = OrdinaryReplacement.__new__(OrdinaryReplacement)
    state = journal()
    state.state["transaction"]["plan"]["members"].append({"logical_name": "gateway-1"})
    ports._serving = Mock(side_effect=[{}, RuntimeError("second member drifted")])
    ports._verify_access = Mock()
    with pytest.raises(RuntimeError, match="second member"):
        ports.finish(state)
    ports._verify_access.assert_not_called()


def test_route_drift_cannot_be_overwritten_at_cutover(monkeypatch):
    from nebius.api.nebius.vpc import v1

    from nebius_vpngw.deploy import replacement_routes as routes
    from nebius_vpngw.deploy.replacement_cloud import message_digest

    route = v1.Route(
        metadata=ResourceMetadata(
            id="route-exact", parent_id="table-exact", name="vpngw-10.20.0.0-24", resource_version=2
        ),
        spec=v1.RouteSpec(
            description="customer-maintained description",
            destination=v1.DestinationMatch(cidr="10.20.0.0/24"),
            next_hop=v1.NextHop(allocation=v1.AllocationNextHop(id="allocation-old")),
        ),
    )
    approved = {
        "id": "route-exact",
        "parent_id": "table-exact",
        "name": route.metadata.name,
        "revision": 1,
        "labels": {},
        "spec_sha256": message_digest(route.spec),
        "allocation_id": "allocation-old",
        "managed": True,
    }
    service = SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(wait=lambda: route))
    monkeypatch.setattr(v1, "RouteServiceClient", lambda _: service)
    cloud = SimpleNamespace(sdk=object(), completed=lambda _label: None, mutate=Mock())
    with pytest.raises(RuntimeError, match="changed before"):
        routes.rebind(cloud, (approved,), "allocation-old", "allocation-new")
    cloud.mutate.assert_not_called()
    assert route.spec.next_hop.allocation.id == "allocation-old"


def test_retirement_requires_service_proof_before_any_delete(monkeypatch):
    from nebius_vpngw.deploy.replacement_cloud import Cloud

    cloud = Cloud.__new__(Cloud)
    cloud.settle = Mock()
    cloud.get_instance = Mock()
    cloud.mutate = Mock()
    proof = Mock(side_effect=RuntimeError("fresh candidate proof failed"))
    with pytest.raises(RuntimeError, match="fresh candidate"):
        cloud.retire("compute-source", "disk-source", owned_disk=True, candidate_verified=proof)
    cloud.get_instance.assert_not_called()
    cloud.mutate.assert_not_called()
