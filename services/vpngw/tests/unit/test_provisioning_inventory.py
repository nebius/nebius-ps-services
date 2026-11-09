from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import (
    CreateInstanceRequest,
    Instance,
    InstanceSpec,
    InstanceStatus,
    ResourcesSpec,
)

from nebius_vpngw.deploy.provisioning_inventory import matches, read, snapshot
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


def fixture():
    desired = CreateInstanceRequest(
        metadata=ResourceMetadata(name="gateway-0", parent_id="project-a"),
        spec=InstanceSpec(resources=ResourcesSpec(platform="cpu-d3", preset="4vcpu-16gb")),
    )
    actual = Instance(
        metadata=ResourceMetadata(
            id="compute-a", parent_id="project-a", name="gateway-0", resource_version=2
        ),
        spec=clone_nebius_sdk_message(desired.spec),
        status=InstanceStatus(),
    )
    return desired, actual


def test_empty_server_messages_do_not_hide_nonempty_or_unknown_spec_changes():
    desired, actual = fixture()
    actual.spec.__pb2_message__.gpu_cluster.SetInParent()
    assert matches(actual, desired)
    original = snapshot("compute", actual)
    actual.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    assert not matches(actual, desired)
    assert snapshot("compute", actual) != original


@pytest.mark.parametrize("change", ["name", "parent", "identity", "revision", "labels", "spec"])
def test_readback_requires_exact_intent(change):
    desired, actual = fixture()
    desired.metadata.id = "compute-a"
    if change == "name":
        actual.metadata.name = "another"
    elif change == "parent":
        actual.metadata.parent_id = "project-b"
    elif change == "identity":
        actual.metadata.id = "compute-b"
    elif change == "revision":
        actual.metadata.resource_version = 0
    elif change == "labels":
        actual.metadata.labels = {"unreviewed": "label"}
    else:
        actual.spec.resources.preset = "8vcpu-32gb"
    assert not matches(actual, desired)


def test_execution_read_uses_exact_id_and_bounded_request_options(monkeypatch):
    _, actual = fixture()
    getter = Mock(return_value=SimpleNamespace(wait=lambda: actual))
    monkeypatch.setattr(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        lambda _: SimpleNamespace(get=getter),
    )
    assert read(object(), "compute", "compute-a") is actual
    assert getter.call_args.args[0].id == "compute-a"
    assert 0 < getter.call_args.kwargs["timeout"] <= 30
    with pytest.raises(RuntimeError, match="different resource"):
        read(object(), "compute", "compute-b")


def subnet_fixture():
    from nebius.api.nebius.vpc.v1 import AddressBlockState, IPv4PublicSubnetPools, Subnet

    from nebius_vpngw.deploy.provisioning_network import create_subnet

    desired = create_subnet("project-a", "gateway-subnet", "network-a", "192.0.2.0/28")
    actual = Subnet(
        metadata=ResourceMetadata(
            id="subnet-a", name="gateway-subnet", parent_id="project-a", resource_version=2
        ),
        spec=clone_nebius_sdk_message(desired.spec),
    )
    actual.spec.ipv4_public_pools = IPv4PublicSubnetPools(use_network_pools=True)
    cidr = actual.spec.ipv4_private_pools.pools[0].cidrs[0]
    cidr.max_mask_length = 32
    cidr.state = AddressBlockState.AVAILABLE
    return desired, actual


def test_subnet_readback_accepts_documented_defaults_without_changing_request():
    from nebius_vpngw.deploy.replacement_cloud import message_digest

    desired, actual = subnet_fixture()
    original = message_digest(desired)
    assert matches(actual, desired)
    assert message_digest(desired) == original


@pytest.mark.parametrize(
    "change", ["cidr", "mask", "state", "public-policy", "private-policy", "table", "unknown"]
)
def test_subnet_defaults_do_not_hide_explicit_policy_or_unrecognized_fields(change):
    from nebius.api.nebius.vpc.v1 import AddressBlockState, IPv4PublicSubnetPools

    desired, actual = subnet_fixture()
    cidr = actual.spec.ipv4_private_pools.pools[0].cidrs[0]
    if change == "cidr":
        cidr.cidr = "192.0.2.16/28"
    elif change == "mask":
        cidr.max_mask_length = 30
    elif change == "state":
        cidr.state = AddressBlockState.DISABLED
    elif change == "public-policy":
        desired.spec.ipv4_public_pools = IPv4PublicSubnetPools(use_network_pools=False)
    elif change == "private-policy":
        actual.spec.ipv4_private_pools.use_network_pools = True
    elif change == "table":
        actual.spec.route_table_id = "table-unapproved"
    else:
        actual.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    assert not matches(actual, desired)


def test_observation_snapshot_binds_intent_not_provider_revision_bookkeeping():
    from nebius.api.nebius.vpc.v1 import Network, NetworkSpec

    network = Network(
        metadata=ResourceMetadata(
            id="network-a", name="gateway-network", parent_id="project-a", resource_version=30
        ),
        spec=NetworkSpec(),
    )
    before = snapshot("network", network)
    network.metadata.resource_version = 31
    network.metadata.__pb2_message__.updated_at.seconds = 100
    assert snapshot("network", network) == before
    network.metadata.labels = {"unapproved": "change"}
    assert snapshot("network", network) != before


def test_snapshot_keeps_unknown_metadata_and_positive_revision_requirement():
    _, actual = fixture()
    before = snapshot("compute", actual)
    actual.metadata.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    assert snapshot("compute", actual) != before
    actual.metadata.resource_version = 0
    with pytest.raises(RuntimeError, match="versioned"):
        snapshot("compute", actual)


def test_private_allocation_readback_accepts_default_prefix_but_not_changed_scope():
    from nebius.api.nebius.vpc.v1 import Allocation

    from nebius_vpngw.deploy.provisioning_network import create_allocation
    from nebius_vpngw.deploy.replacement_cloud import message_digest

    desired = create_allocation("project-a", "gateway-ip", "subnet-a", public=False)
    original = message_digest(desired)
    actual = Allocation(
        metadata=ResourceMetadata(
            id="allocation-a", name="gateway-ip", parent_id="project-a", resource_version=1
        ),
        spec=clone_nebius_sdk_message(desired.spec),
    )
    actual.spec.ipv4_private.cidr = "/32"
    assert matches(actual, desired)
    assert message_digest(desired) == original
    actual.spec.ipv4_private.subnet_id = "unapproved-subnet"
    assert not matches(actual, desired)
    actual.spec.ipv4_private.subnet_id = "subnet-a"
    actual.spec.ipv4_private.cidr = "/28"
    assert not matches(actual, desired)
