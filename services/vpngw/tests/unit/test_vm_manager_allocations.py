from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_manager import (
    VMManager,
    VMProvisioningConfig,
    VMProvisioningResult,
)
from nebius_vpngw.schema import VMHARole, VMHARouteTarget


def _route_targets() -> tuple[VMHARouteTarget, ...]:
    return (
        VMHARouteTarget(
            project_id="project-1",
            network_id="network-1",
            workload_subnet_id="workload-subnet-1",
            route_table_id="route-table-1",
        ),
    )


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


def _ha_spec() -> GatewayGroupSpec:
    generation = SimpleNamespace(
        generation_id="a" * 64,
        digests=SimpleNamespace(
            configuration="a" * 64,
            static_routes="b" * 64,
            bgp_policy="c" * 64,
        ),
    )
    return GatewayGroupSpec(
        name="gateway",
        instance_count=2,
        region="eu-north1-a",
        external_ips=[[], []],
        vm_spec={},
        vm_ha=SimpleNamespace(
            cluster_id="cluster",
            active_instance_index=0,
            generation=generation,
            members=(
                SimpleNamespace(node_id="node-a", instance_index=0, role=VMHARole.ACTIVE),
                SimpleNamespace(node_id="node-b", instance_index=1, role=VMHARole.PASSIVE),
            ),
        ),
    )


def test_ensure_vm_ha_shared_allocation_reuses_one_deterministic_identity() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    allocation = SimpleNamespace(
        id="shared-private",
        metadata=SimpleNamespace(id="shared-private", name="gateway-cluster-shared-private-ip"),
    )
    request = SimpleNamespace(wait=lambda: SimpleNamespace(items=[allocation]))
    client = SimpleNamespace(list=lambda _: request)

    with patch.object(vm_mgr, "get_ha_allocation", return_value=allocation):
        allocation_id = vm_mgr._ensure_vm_ha_shared_allocation(
            client,
            _ha_spec(),
            "subnet-1",
        )

    assert allocation_id == "shared-private"
    assert vm_mgr._vm_ha_shared_allocation_id == "shared-private"


def test_ensure_vm_ha_shared_allocation_propagates_ambiguous_sdk_failure() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    client = SimpleNamespace(
        list=lambda _: SimpleNamespace(wait=lambda: (_ for _ in ()).throw(OSError("denied")))
    )

    with pytest.raises(OSError, match="denied"):
        vm_mgr._ensure_vm_ha_shared_allocation(client, _ha_spec(), "subnet-1")


def test_vm_ha_operation_sync_propagates_failure() -> None:
    operation = SimpleNamespace(sync_wait=MagicMock(side_effect=OSError("operation failed")))

    with pytest.raises(OSError, match="operation failed"):
        VMManager._sync_vm_ha_operation(operation)


def test_build_vm_ha_runtime_binding_rereads_exact_active_owner() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    vm_mgr._vm_ha_route_targets = _route_targets()
    allocation = SimpleNamespace(
        id="shared-private",
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(instance_id="compute-a", name="eth0"),
                load_balancer=None,
            ),
        ),
    )

    def instance(name: str) -> SimpleNamespace:
        suffix = "a" if name.endswith("-0") else "b"
        return SimpleNamespace(
            id=f"compute-{suffix}",
            spec=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(
                        name="eth0",
                        ip_address=SimpleNamespace(
                            allocation_id="shared-private" if suffix == "a" else ""
                        ),
                    )
                ]
            ),
            status=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(
                        ip_address=SimpleNamespace(
                            address=f"10.0.0.{10 if suffix == 'a' else 11}/32"
                        )
                    )
                ]
            ),
        )

    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr, "_get_ha_instance_by_name", side_effect=lambda _, name: instance(name)
        ),
    ):
        binding = vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())

    assert binding.shared_allocation_id == "shared-private"
    assert [node.compute_id for node in binding.nodes] == ["compute-a", "compute-b"]
    assert [node.peer_endpoint for node in binding.nodes] == ["10.0.0.10:9443", "10.0.0.11:9443"]


def test_build_vm_ha_runtime_binding_rejects_non_active_owner() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    vm_mgr._vm_ha_route_targets = _route_targets()
    allocation = SimpleNamespace(
        id="shared-private",
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(instance_id="compute-b", name="eth0"),
                load_balancer=None,
            ),
        ),
    )

    def instance(name: str) -> SimpleNamespace:
        suffix = "a" if name.endswith("-0") else "b"
        return SimpleNamespace(
            id=f"compute-{suffix}",
            spec=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(
                        name="eth0",
                        ip_address=SimpleNamespace(
                            allocation_id="shared-private" if suffix == "a" else ""
                        ),
                    )
                ]
            ),
            status=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(ip_address=SimpleNamespace(address="10.0.0.10/32"))
                ]
            ),
        )

    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr, "_get_ha_instance_by_name", side_effect=lambda _, name: instance(name)
        ),
        pytest.raises(RuntimeError, match="not exact on configured active"),
    ):
        vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())


def test_build_vm_ha_runtime_binding_rejects_conflicting_passive_compute_owner() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    vm_mgr._vm_ha_route_targets = _route_targets()
    allocation = SimpleNamespace(
        id="shared-private",
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(instance_id="compute-a", name="eth0"),
                load_balancer=None,
            ),
        ),
    )

    def instance(name: str) -> SimpleNamespace:
        suffix = "a" if name.endswith("-0") else "b"
        return SimpleNamespace(
            id=f"compute-{suffix}",
            spec=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(
                        name="eth0",
                        ip_address=SimpleNamespace(allocation_id="shared-private"),
                    )
                ]
            ),
            status=SimpleNamespace(
                network_interfaces=[
                    SimpleNamespace(ip_address=SimpleNamespace(address="10.0.0.10/32"))
                ]
            ),
        )

    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr, "_get_ha_instance_by_name", side_effect=lambda _, name: instance(name)
        ),
        pytest.raises(RuntimeError, match="passive Compute NIC conflicts"),
    ):
        vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())


def test_initial_vm_ha_attachment_rejects_existing_passive_owner_without_mutation() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    allocation = SimpleNamespace(
        id="shared-private",
        status=SimpleNamespace(
            state="ASSIGNED",
            assignment=SimpleNamespace(
                network_interface=SimpleNamespace(instance_id="compute-b", name="eth0"),
                load_balancer=None,
            ),
        ),
    )
    setter = MagicMock()

    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(vm_mgr, "set_ha_private_allocation", setter),
        pytest.raises(RuntimeError, match="outside configured active"),
    ):
        vm_mgr._attach_vm_ha_shared_allocation_initially(
            allocation_id="shared-private",
            active_compute_id="compute-a",
            active_network_interface_name="eth0",
        )

    setter.assert_not_called()


def test_vm_ha_instance_allocations_use_shared_id_only_on_configured_active() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    provisioning = VMProvisioningConfig(
        subnet_id="subnet-1",
        num_nics=1,
        platform="cpu-d3",
        preset=None,
        boot_image="image",
        disk_gb=20,
        disk_type="network-ssd",
        disk_block_bytes=4096,
        cloud_init="",
    )

    with (
        patch.object(vm_mgr, "_ensure_public_allocation", return_value=("public", None)),
        patch.object(vm_mgr, "_ensure_private_allocation") as per_node_private,
    ):
        vm_mgr._ensure_instance_allocations(
            object(), object(), _ha_spec(), "gateway-0", 0, provisioning, [], {}
        )
        vm_mgr._ensure_instance_allocations(
            object(), object(), _ha_spec(), "gateway-1", 1, provisioning, [], {}
        )

    assert vm_mgr._private_alloc_ids == {
        "gateway-0": ["shared-private"],
        "gateway-1": [],
    }
    per_node_private.assert_not_called()


def test_vm_ha_ensure_group_returns_binding_only_after_attachment_and_reread() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    provisioning = SimpleNamespace(subnet_id="subnet-1")
    active = SimpleNamespace(
        id="compute-a",
        spec=SimpleNamespace(network_interfaces=[SimpleNamespace(name="eth0")]),
    )
    binding = MagicMock()

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_resolve_client_apis", return_value=(None, None, None, object())),
        patch.object(vm_mgr, "_discover_existing_instances", return_value=[]),
        patch.object(vm_mgr, "_build_vm_provisioning_config", return_value=provisioning),
        patch.object(
            vm_mgr,
            "_ensure_vm_ha_shared_allocation",
            side_effect=lambda *_: setattr(vm_mgr, "_vm_ha_shared_allocation_id", "shared-private"),
        ) as ensure_shared,
        patch.object(vm_mgr, "_instance_exists", return_value=False),
        patch.object(vm_mgr, "_resolve_vm_ha_route_targets", return_value=_route_targets()),
        patch.object(vm_mgr, "_provision_instance") as provision,
        patch.object(vm_mgr, "_get_ha_instance_by_name", return_value=active),
        patch.object(vm_mgr, "_attach_vm_ha_shared_allocation_initially") as attach,
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding", return_value=binding) as reread,
    ):
        result = vm_mgr.ensure_group(spec, local_prefixes=["10.0.0.0/8"])

    assert isinstance(result, VMProvisioningResult)
    assert result.vm_ha_runtime_binding is binding
    ensure_shared.assert_called_once()
    assert provision.call_count == 2
    attach.assert_called_once_with(
        allocation_id="shared-private",
        active_compute_id="compute-a",
        active_network_interface_name="eth0",
    )
    reread.assert_called_once()


def test_vm_ha_attachment_failure_emits_no_runtime_binding() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    active = SimpleNamespace(
        id="compute-a",
        spec=SimpleNamespace(network_interfaces=[SimpleNamespace(name="eth0")]),
    )

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_resolve_client_apis", return_value=(None, None, None, object())),
        patch.object(vm_mgr, "_discover_existing_instances", return_value=[]),
        patch.object(
            vm_mgr,
            "_build_vm_provisioning_config",
            return_value=SimpleNamespace(subnet_id="subnet-1"),
        ),
        patch.object(
            vm_mgr,
            "_ensure_vm_ha_shared_allocation",
            side_effect=lambda *_: setattr(vm_mgr, "_vm_ha_shared_allocation_id", "shared-private"),
        ),
        patch.object(vm_mgr, "_instance_exists", return_value=False),
        patch.object(vm_mgr, "_provision_instance"),
        patch.object(vm_mgr, "_get_ha_instance_by_name", return_value=active),
        patch.object(
            vm_mgr,
            "_attach_vm_ha_shared_allocation_initially",
            side_effect=OSError("attach failed"),
        ),
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding") as binding,
        pytest.raises(RuntimeError, match="failed closed"),
    ):
        vm_mgr.ensure_group(_ha_spec())

    binding.assert_not_called()
    assert vm_mgr._vm_ha_shared_allocation_id is None


def test_explicit_vm_ha_rejects_scaffold_fallback() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=None),
        pytest.raises(RuntimeError, match="failed closed"),
    ):
        vm_mgr.ensure_group(_ha_spec())


def test_omitted_vm_ha_preserves_scaffold_return_shape() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None

    with patch.object(vm_mgr, "_build_sdk_client", return_value=None):
        result = vm_mgr.ensure_group(spec)

    assert result == {}
    assert type(result) is dict
