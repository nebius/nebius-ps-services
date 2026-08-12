from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nebius_vpngw.deploy.vm_manager import VMManager


def _public_allocation(
    ip: str,
    *,
    alloc_id: str = "alloc-1",
    name: str = "vpngw-public-ip",
    subnet_id: str = "subnet-1",
    attached: bool = False,
    use_cidr_only: bool = False,
    spec_cidr_only: bool = False,
    assignment_instance_id: str | None = None,
):
    ipv4_public_fields = {"subnet_id": subnet_id}
    if spec_cidr_only:
        ipv4_public_fields["cidr"] = "/32"
    elif use_cidr_only:
        ipv4_public_fields["cidr"] = f"{ip}/32"
    else:
        ipv4_public_fields["address"] = f"{ip}/32"

    details_fields = {"allocated_cidr": f"{ip}/32"}
    if attached:
        details_fields["resource_id"] = "instance-1"

    assignment = None
    if assignment_instance_id is not None:
        assignment = SimpleNamespace(
            network_interface=SimpleNamespace(instance_id=assignment_instance_id)
        )

    return SimpleNamespace(
        id=alloc_id,
        metadata=SimpleNamespace(id=alloc_id, name=name),
        spec=SimpleNamespace(ipv4_public=SimpleNamespace(**ipv4_public_fields)),
        status=SimpleNamespace(details=SimpleNamespace(**details_fields), assignment=assignment),
    )


def test_allocation_ip_from_obj_accepts_ipv4_public_cidr() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")

    alloc = _public_allocation("89.169.123.157", use_cidr_only=True)

    assert vm_mgr._allocation_ip_from_obj(alloc) == "89.169.123.157"


def test_allocation_ip_from_obj_skips_placeholder_spec_cidr_and_uses_status_details() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")

    alloc = _public_allocation("204.12.170.147", spec_cidr_only=True)

    assert vm_mgr._allocation_ip_from_obj(alloc) == "204.12.170.147"


def test_allocation_is_attached_accepts_assignment_network_interface() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")

    alloc = _public_allocation("204.12.170.147", assignment_instance_id="computeinstance-1")

    assert vm_mgr._allocation_is_attached(alloc)


def test_list_allocations_by_ip_waits_for_request_objects() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    alloc = _public_allocation("204.12.170.147", spec_cidr_only=True)

    class FakeRequest:
        def wait(self):
            return SimpleNamespace(items=[alloc])

    alloc_client = SimpleNamespace(list=lambda request: FakeRequest())

    mapping = vm_mgr._list_allocations_by_ip(alloc_client)

    assert mapping == {"204.12.170.147": alloc}


def test_ensure_public_allocation_reuses_existing_requested_ip_from_project_lookup() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    desired_ip = "89.169.123.157"
    existing_alloc = _public_allocation(desired_ip, name="vpngw-public-ip")

    with (
        patch.object(vm_mgr, "_list_allocations_by_ip", return_value={desired_ip: existing_alloc}),
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=existing_alloc),
    ):
        alloc_name, alloc_obj = vm_mgr._ensure_public_allocation(
            alloc_api=None,
            alloc_client=object(),
            inst_name="nebius-vpn-gw-0",
            nic_name="eth0",
            subnet_id="subnet-1",
            desired_ip=desired_ip,
            preserved_alloc_id=None,
        )

    assert alloc_name == "nebius-vpn-gw-0-eth0-ip"
    assert alloc_obj is existing_alloc


def test_ensure_public_allocation_rejects_requested_ip_when_allocation_is_attached() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    desired_ip = "89.169.123.157"
    attached_alloc = _public_allocation(desired_ip, attached=True)

    with (
        patch.object(vm_mgr, "_list_allocations_by_ip", return_value={desired_ip: attached_alloc}),
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=attached_alloc),
        pytest.raises(RuntimeError, match="already attached"),
    ):
        vm_mgr._ensure_public_allocation(
            alloc_api=None,
            alloc_client=object(),
            inst_name="nebius-vpn-gw-0",
            nic_name="eth0",
            subnet_id="subnet-1",
            desired_ip=desired_ip,
            preserved_alloc_id=None,
        )


def test_resolve_prepared_public_allocation_prefers_requested_ip_over_stale_name() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    desired_ip = "89.169.123.157"
    desired_alloc = _public_allocation(desired_ip, name="vpngw-public-ip")
    stale_named_alloc = _public_allocation(
        "89.169.123.158",
        alloc_id="alloc-2",
        name="nebius-vpn-gw-0-eth0-ip",
    )

    with (
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=desired_alloc),
        patch.object(
            vm_mgr, "_get_allocation_by_name", return_value=stale_named_alloc
        ) as by_name_mock,
    ):
        alloc_obj, _ = vm_mgr._resolve_prepared_public_allocation(
            alloc_client=object(),
            subnet_id="subnet-1",
            alloc_name="nebius-vpn-gw-0-eth0-ip",
            desired_ip=desired_ip,
            allocations_by_ip={desired_ip: desired_alloc},
        )

    assert alloc_obj is desired_alloc
    by_name_mock.assert_not_called()


def test_resolve_prepared_public_allocation_rejects_named_allocation_with_different_ip() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    desired_ip = "89.169.123.157"
    stale_named_alloc = _public_allocation(
        "89.169.123.158",
        name="nebius-vpn-gw-0-eth0-ip",
    )

    with (
        patch.object(vm_mgr, "_list_allocations_by_ip", return_value={}),
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=stale_named_alloc),
        patch.object(vm_mgr, "_get_allocation_by_name", return_value=stale_named_alloc),
        pytest.raises(RuntimeError, match="has IP 89.169.123.158"),
    ):
        vm_mgr._resolve_prepared_public_allocation(
            alloc_client=object(),
            subnet_id="subnet-1",
            alloc_name="nebius-vpn-gw-0-eth0-ip",
            desired_ip=desired_ip,
            allocations_by_ip={},
        )


def test_require_public_allocation_in_gateway_subnet_rejects_different_subnet() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    alloc = _public_allocation(
        "204.12.163.64",
        name="manual-migrate-ip",
        subnet_id="subnet-old",
    )

    with pytest.raises(RuntimeError, match="cannot be moved to gateway subnet subnet-new"):
        vm_mgr._require_public_allocation_in_gateway_subnet(
            object(),
            alloc,
            "subnet-new",
            "204.12.163.64",
        )


def test_set_ha_private_allocation_updates_only_exact_nic() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        Instance,
        InstanceSpec,
        IPAddress,
        NetworkInterfaceSpec,
    )

    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    instance = Instance(
        metadata=ResourceMetadata(id="instance-new", parent_id="project-1", name="new"),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    subnet_id="subnet-1",
                    name="eth0",
                    ip_address=IPAddress(allocation_id="old-private"),
                ),
                NetworkInterfaceSpec(
                    subnet_id="subnet-2",
                    name="eth1",
                    ip_address=IPAddress(allocation_id="other-private"),
                ),
            ]
        ),
    )
    operation = MagicMock()
    service = MagicMock()
    service.update.return_value.wait.return_value = operation

    with (
        patch.object(vm_mgr, "_get_client", return_value=object()),
        patch.object(vm_mgr, "get_ha_instance", return_value=instance),
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
    ):
        vm_mgr.set_ha_private_allocation("instance-new", "eth0", "shared-private")

    request = service.update.call_args.args[0]
    assert request.metadata.id == "instance-new"
    assert request.spec.network_interfaces[0].ip_address.allocation_id == "shared-private"
    assert request.spec.network_interfaces[1].ip_address.allocation_id == "other-private"


def test_set_ha_private_allocation_replay_skips_update() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import Instance, InstanceSpec, IPAddress, NetworkInterfaceSpec

    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    instance = Instance(
        metadata=ResourceMetadata(id="instance-new", parent_id="project-1", name="new"),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    subnet_id="subnet-1",
                    name="eth0",
                    ip_address=IPAddress(allocation_id="shared-private"),
                )
            ]
        ),
    )
    service = MagicMock()

    with (
        patch.object(vm_mgr, "_get_client", return_value=object()),
        patch.object(vm_mgr, "get_ha_instance", return_value=instance),
        patch("nebius.api.nebius.compute.v1.InstanceServiceClient", return_value=service),
    ):
        vm_mgr.set_ha_private_allocation("instance-new", "eth0", "shared-private")

    service.update.assert_not_called()


def test_set_ha_private_allocation_requires_exact_nic() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import Instance, InstanceSpec, NetworkInterfaceSpec

    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    instance = Instance(
        metadata=ResourceMetadata(id="instance-new", parent_id="project-1", name="new"),
        spec=InstanceSpec(
            network_interfaces=[NetworkInterfaceSpec(subnet_id="subnet-1", name="eth1")]
        ),
    )

    with (
        patch.object(vm_mgr, "_get_client", return_value=object()),
        patch.object(vm_mgr, "get_ha_instance", return_value=instance),
        pytest.raises(RuntimeError, match="exactly one NIC named eth0"),
    ):
        vm_mgr.set_ha_private_allocation("instance-new", "eth0", "shared-private")
