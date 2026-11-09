"""Planning must produce the same generated request the resource owner submits."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.replacement_cloud import message_digest
from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.mark.parametrize("groups", [(), ("security-group-a", "security-group-b")])
@pytest.mark.parametrize("allocations", [([], []), (["public-a"], ["private-a"])])
def test_resolved_provisioning_plan_matches_submitted_compute_request(
    monkeypatch, groups, allocations
):
    manager = VMManager(project_id="project-a", region="eu-north1")
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=1,
        region="eu-north1",
        external_ips=[],
        vm_spec={"platform": "cpu-d3", "preset": "4vcpu-16gb", "disk_gb": 40},
        security_group_ids=groups,
    )
    original = deepcopy(spec)
    service = Mock()
    constructor = Mock(return_value=service)
    monkeypatch.setattr("nebius.api.nebius.compute.v1.InstanceServiceClient", constructor)
    manager._get_client = Mock(side_effect=AssertionError("planning acquired a cloud client"))
    manager._ensure_vpngw_subnet = Mock(side_effect=AssertionError("planning changed networking"))
    manager._private_alloc_ids["gateway-0"] = allocations[1]
    provisioning = manager._resolved_vm_provisioning_config(spec, "subnet-a", "#cloud-config\n")
    planned = manager._build_instance_create_request(
        "gateway-0", provisioning, "disk-a", *allocations
    )
    constructor.assert_not_called()
    assert spec == original
    assert planned.metadata.name == "gateway-0"
    assert planned.metadata.parent_id == "project-a"
    assert planned.spec.boot_disk.existing_disk.id == "disk-a"
    assert planned.spec.resources.preset == "4vcpu-16gb"
    nic = planned.spec.network_interfaces[0]
    assert nic.subnet_id == "subnet-a"
    assert tuple(item.id for item in nic.security_groups) == groups
    assert nic.public_ip_address.allocation_id == (allocations[0][0] if allocations[0] else None)
    assert nic.ip_address.allocation_id == (allocations[1][0] if allocations[1] else "")

    monkeypatch.setattr("nebius_vpngw.deploy.vm_manager.time.sleep", lambda _: None)
    monkeypatch.setattr(manager, "get_vm_public_ip", lambda _: "203.0.113.10")
    addresses = {}
    assert manager._create_instance_with_fallback(
        object(), None, "gateway-0", provisioning, "disk-a", allocations[0], addresses
    )
    service.create.assert_called_once()
    submitted = service.create.call_args.args[0]
    assert message_digest(submitted) == message_digest(planned)
    service.create.return_value.wait.return_value.sync_wait.assert_called_once()
    assert addresses == {"gateway-0": "203.0.113.10"}


@pytest.mark.parametrize("disk", ["", " "])
def test_compute_plan_requires_exact_disk_before_sdk_use(monkeypatch, disk):
    manager = VMManager(project_id="project-a", region="eu-north1")
    constructor = Mock(side_effect=AssertionError("invalid intent reached SDK"))
    monkeypatch.setattr("nebius.api.nebius.compute.v1.InstanceServiceClient", constructor)
    with pytest.raises(RuntimeError, match="authoritative boot disk"):
        manager._build_instance_create_request("gateway-0", None, disk, [], [])
    constructor.assert_not_called()


@pytest.mark.parametrize("kind", ["pool", "subnet"])
def test_network_update_changes_only_reviewed_field_and_preserves_unknowns(kind):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import Pool, PoolCidr, PoolSpec, Subnet, SubnetSpec

    from nebius_vpngw.deploy.provisioning_network import attach_table, extend_pool

    metadata = ResourceMetadata(
        id=f"{kind}-a", parent_id="project-a", resource_version=7, labels={"operator": "keep"}
    )
    metadata.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    spec = (
        PoolSpec(cidrs=[PoolCidr(cidr="192.0.2.0/24", max_mask_length=28)])
        if kind == "pool"
        else SubnetSpec(network_id="network-a", route_table_id="old-table")
    )
    spec.__pb2_message__.MergeFromString(b"\xf8\x07\x02")
    resource = (Pool if kind == "pool" else Subnet)(metadata=metadata, spec=spec)
    original = message_digest(resource)
    if kind == "pool":
        request = extend_pool(resource, "project-a", "198.51.100.0/24")
        assert [item.cidr for item in request.spec.cidrs] == ["192.0.2.0/24", "198.51.100.0/24"]
        restored = deepcopy(request.spec.__pb2_message__)
        del restored.cidrs[-1]
    else:
        request = attach_table(resource, "project-a", "new-table")
        assert request.spec.route_table_id == "new-table"
        restored = deepcopy(request.spec.__pb2_message__)
        restored.route_table_id = "old-table"
    assert message_digest(request.metadata) == message_digest(metadata)
    assert restored.SerializeToString(deterministic=True) == spec.__pb2_message__.SerializeToString(
        deterministic=True
    )
    assert message_digest(resource) == original


@pytest.mark.parametrize("project,revision", [("different-project", 7), ("project-a", 0)])
@pytest.mark.parametrize("kind", ["pool", "subnet"])
def test_network_update_rejects_foreign_or_unversioned_resource(project, revision, kind):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import Pool, PoolSpec, Subnet, SubnetSpec

    from nebius_vpngw.deploy.provisioning_network import attach_table, extend_pool

    resource = (
        Pool(spec=PoolSpec()) if kind == "pool" else Subnet(spec=SubnetSpec(network_id="network-a"))
    )
    resource.metadata = ResourceMetadata(
        id=f"{kind}-a", parent_id=project, resource_version=revision
    )
    with pytest.raises(RuntimeError, match="identity and positive revision"):
        if kind == "pool":
            extend_pool(resource, "project-a", "198.51.100.0/24")
        else:
            attach_table(resource, "project-a", "new-table")
