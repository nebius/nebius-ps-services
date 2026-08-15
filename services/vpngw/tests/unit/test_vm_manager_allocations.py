from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_ha_identity import (
    FormerVMHAProvenance,
    LegacyVMHAIdentity,
    parse_provisioning_marker,
    render_provisioning_marker,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
)
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
    provisioning = VMProvisioningConfig(
        subnet_id="subnet-1",
        num_nics=1,
        platform="cpu-d3",
        preset=None,
        boot_image="ubuntu",
        disk_gb=20,
        disk_type="NETWORK_SSD",
        disk_block_bytes=4096,
        cloud_init="#cloud-config\n",
    )
    active = SimpleNamespace(
        id="compute-a",
        spec=SimpleNamespace(network_interfaces=[SimpleNamespace(name="eth0")]),
    )
    binding = MagicMock()

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_resolve_client_apis", return_value=(None, None, None, object())),
        patch.object(vm_mgr, "_discover_vm_ha_members", return_value={}),
        patch.object(vm_mgr, "verify_vm_ha_existing_identities"),
        patch.object(
            vm_mgr,
            "_prepare_vm_ha_enrollment_cloud_inits",
            return_value={"gateway-0": "cloud-0", "gateway-1": "cloud-1"},
        ),
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


class _Waitable:
    def __init__(self, value) -> None:
        self.value = value

    def wait(self):
        return self.value


class _FormerAllocationService:
    def __init__(self, allocations: list[object], current: object | None = None) -> None:
        self.allocations = allocations
        self.current = current if current is not None else (allocations[0] if allocations else None)
        self.list_requests: list[object] = []
        self.get_requests: list[object] = []

    def list(self, request):
        self.list_requests.append(request)
        return _Waitable(SimpleNamespace(items=self.allocations, next_page_token=""))

    def get(self, request):
        self.get_requests.append(request)
        return _Waitable(self.current)


def _former_allocation(
    *,
    owner_instance_id: str | None = "compute-0",
    owner_nic_name: str = "eth0",
    private: bool = True,
    alloc_id: str = "shared-private",
    name: str = "gateway-cluster-shared-private-ip",
):
    assignment = (
        SimpleNamespace(
            network_interface=SimpleNamespace(
                instance_id=owner_instance_id,
                name=owner_nic_name,
            ),
            load_balancer=None,
        )
        if owner_instance_id is not None
        else None
    )
    return SimpleNamespace(
        id=alloc_id,
        metadata=SimpleNamespace(id=alloc_id, name=name),
        spec=SimpleNamespace(
            ipv4_private=SimpleNamespace(subnet_id="subnet-1") if private else None,
            ipv4_public=None if private else SimpleNamespace(subnet_id="subnet-1"),
        ),
        status=SimpleNamespace(
            state="ASSIGNED" if owner_instance_id is not None else "ALLOCATED",
            assignment=assignment,
        ),
    )


def _former_member(
    *,
    index: int,
    marker: str | None,
    allocation_id: str = "",
    compute_id: str | None = None,
    legacy_signature: bool = False,
):
    name = f"gateway-{index}"
    return SimpleNamespace(
        id=compute_id or f"compute-{index}",
        metadata=SimpleNamespace(id=compute_id or f"compute-{index}", name=name),
        spec=SimpleNamespace(
            cloud_init_user_data=(
                f"#cloud-config\n# nebius-vpngw-vm-ha-provisioning-v1: {marker}\n"
                if marker is not None
                else (
                    "#cloud-config\n"
                    "write_files:\n"
                    "  - path: /etc/ssh/ssh_host_vpngw_key\n"
                    "    content: fixture\n"
                    "            HostKey /etc/ssh/ssh_host_vpngw_key\n"
                    if legacy_signature
                    else "#cloud-config\n"
                )
            ),
            network_interfaces=[
                SimpleNamespace(
                    name="eth0",
                    ip_address=SimpleNamespace(allocation_id=allocation_id),
                )
            ],
        ),
        status=SimpleNamespace(
            network_interfaces=[
                SimpleNamespace(
                    public_ip_address=SimpleNamespace(address=f"203.0.113.{10 + index}/32")
                )
            ]
        ),
    )


def _legacy_identity(index: int) -> LegacyVMHAIdentity:
    nodes = (
        ("node-active", "active", "compute-0", "eth0"),
        ("node-passive", "passive", "compute-1", "eth0"),
    )
    return LegacyVMHAIdentity(
        instance_name=f"gateway-{index}",
        cluster_id="cluster",
        allocation_id="shared-private",
        instance_index=index,
        node_id=nodes[index][0],
        role=nodes[index][1],
        nodes=nodes,
    )


def _retained_lifecycle_state() -> VMHALifecycleState:
    return VMHALifecycleState(
        status=VMHALifecycleStatus.ACTIVE,
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_id="shared-private",
        allocation_name="gateway-cluster-shared-private-ip",
        members=(
            VMHALifecycleMember(
                0,
                "gateway-0",
                "node-active",
                "active",
                "compute-0",
                "eth0",
                "203.0.113.10",
            ),
            VMHALifecycleMember(
                1,
                "gateway-1",
                "node-passive",
                "passive",
                "compute-1",
                "eth0",
                "203.0.113.11",
            ),
        ),
    )


def _former_identity_fixture():
    ha_spec = _ha_spec()
    markers = [render_provisioning_marker(ha_spec, index) for index in range(2)]
    ordinary_spec = _ha_spec()
    ordinary_spec.vm_ha = None
    ordinary_spec.instance_count = 1
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=markers[index],
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }
    allocation = _former_allocation()
    return ordinary_spec, members, allocation


def test_vm_ha_enrollment_cloud_init_persists_exact_member_identity() -> None:
    spec = _ha_spec()
    identity = SimpleNamespace(cloud_init_entries=lambda: "  - path: /etc/ssh/ssh_host_vpngw_key\n")
    base = "#cloud-config\nwrite_files:\n  - content: |\n            Port 22\n"
    marker = render_provisioning_marker(spec, 0)

    rendered = VMManager._render_vm_ha_enrollment_cloud_init(base, identity, marker)
    parsed = parse_provisioning_marker(
        SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data=rendered))
    )

    assert parsed is not None
    assert parsed["cluster_id"] == "cluster"
    assert parsed["member"]["instance_name"] == "gateway-0"
    assert [member["instance_name"] for member in parsed["members"]] == [
        "gateway-0",
        "gateway-1",
    ]


def test_retained_ordinary_members_are_adopted_from_exact_lifecycle_and_runtime_proof() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    lifecycle = _retained_lifecycle_state()
    allocation = _former_allocation()
    service = _FormerAllocationService([], current=allocation)
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }
    identities = {f"gateway-{index}": _legacy_identity(index) for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        candidates = vm_mgr.discover_former_vm_ha_candidate_members(spec, lifecycle_state=lifecycle)
        discovered = vm_mgr.discover_former_vm_ha_members(
            spec,
            legacy_identities=identities,
            lifecycle_state=lifecycle,
        )
        vm_mgr.verify_former_vm_ha_member_snapshot(
            spec,
            discovered,
            legacy_identities=identities,
            lifecycle_state=lifecycle,
        )

    assert (
        candidates
        == discovered
        == {
            "gateway-0": "203.0.113.10",
            "gateway-1": "203.0.113.11",
        }
    )
    assert vm_mgr.former_vm_ha_candidate_provenance is FormerVMHAProvenance.LIFECYCLE_STATE
    assert service.list_requests == []
    assert len(service.get_requests) == 3


def test_retained_lifecycle_compute_identity_mismatch_fails_closed() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    members = {f"gateway-{index}": _former_member(index=index, marker=None) for index in range(2)}
    members["gateway-1"] = _former_member(
        index=1,
        marker=None,
        compute_id="replacement-compute",
    )

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        pytest.raises(RuntimeError, match="lifecycle member identity changed"),
    ):
        vm_mgr.discover_former_vm_ha_candidate_members(
            spec, lifecycle_state=_retained_lifecycle_state()
        )


def test_removal_lifecycle_transition_rejects_stale_active_reversal() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    active = _retained_lifecycle_state()
    removing = active.with_status(VMHALifecycleStatus.REMOVAL_IN_PROGRESS)
    allocation = _former_allocation()
    service = _FormerAllocationService([], current=allocation)
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }
    identities = {f"gateway-{index}": _legacy_identity(index) for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        candidates = vm_mgr.discover_former_vm_ha_candidate_members(
            spec,
            lifecycle_state=active,
        )
        discovered = vm_mgr.discover_former_vm_ha_members(
            spec,
            legacy_identities=identities,
            lifecycle_state=active,
        )
        vm_mgr.verify_former_vm_ha_member_snapshot(
            spec,
            discovered,
            lifecycle_state=removing,
        )
        with pytest.raises(RuntimeError, match="unavailable or stale"):
            vm_mgr.verify_former_vm_ha_member_snapshot(
                spec,
                discovered,
                legacy_identities=identities,
                lifecycle_state=active,
            )

    assert candidates == discovered
    assert service.list_requests == []
    assert len(service.get_requests) == 3


def test_ordinary_compute_without_ha_provenance_does_not_list_allocations() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    service = _FormerAllocationService([])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            return_value=None,
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}
    assert service.list_requests == []


def test_pre_marker_vm_ha_uses_exact_runtime_allocation_without_list_authority() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    allocation = _former_allocation()
    service = _FormerAllocationService([], current=allocation)
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            legacy_signature=True,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }
    identities = {f"gateway-{index}": _legacy_identity(index) for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        candidates = vm_mgr.discover_former_vm_ha_candidate_members(spec)
        result = vm_mgr.discover_former_vm_ha_members(spec, legacy_identities=identities)
        vm_mgr.verify_former_vm_ha_member_snapshot(spec, result, legacy_identities=identities)

    assert candidates == {
        "gateway-0": "203.0.113.10",
        "gateway-1": "203.0.113.11",
    }
    assert result == candidates
    assert vm_mgr.former_vm_ha_candidate_provenance is FormerVMHAProvenance.LEGACY_RUNTIME
    assert service.list_requests == []
    assert len(service.get_requests) == 3


def test_unmarked_retained_vm_ha_uses_runtime_first_then_exact_allocation_get() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    allocation = _former_allocation()
    service = _FormerAllocationService([], current=allocation)
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }
    identities = {f"gateway-{index}": _legacy_identity(index) for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        candidates = vm_mgr.discover_former_vm_ha_candidate_members(
            spec,
            allow_unmarked_runtime_probe=True,
        )
        discovered = vm_mgr.discover_former_vm_ha_members(
            spec,
            legacy_identities=identities,
        )
        vm_mgr.verify_former_vm_ha_member_snapshot(
            spec,
            discovered,
            legacy_identities=identities,
        )

    assert (
        candidates
        == discovered
        == {
            "gateway-0": "203.0.113.10",
            "gateway-1": "203.0.113.11",
        }
    )
    assert vm_mgr.former_vm_ha_candidate_provenance is None
    assert service.list_requests == []
    assert len(service.get_requests) == 3


def test_unmarked_two_ordinary_runtimes_need_no_vpc_read_or_teardown_evidence() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    service = _FormerAllocationService([])
    members = {f"gateway-{index}": _former_member(index=index, marker=None) for index in range(2)}
    identities = {f"gateway-{index}": None for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        candidates = vm_mgr.discover_former_vm_ha_candidate_members(
            spec,
            allow_unmarked_runtime_probe=True,
        )
        discovered = vm_mgr.discover_former_vm_ha_members(
            spec,
            legacy_identities=identities,
        )

    assert candidates == {
        "gateway-0": "203.0.113.10",
        "gateway-1": "203.0.113.11",
    }
    assert discovered == {}
    assert vm_mgr._former_vm_ha_snapshot is None
    assert vm_mgr._former_vm_ha_evidence is None
    assert service.list_requests == []
    assert service.get_requests == []


def test_unmarked_one_sided_runtime_ha_fails_closed_before_vpc_read() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    service = _FormerAllocationService([])
    members = {f"gateway-{index}": _former_member(index=index, marker=None) for index in range(2)}

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        vm_mgr.discover_former_vm_ha_candidate_members(
            spec,
            allow_unmarked_runtime_probe=True,
        )
        with pytest.raises(RuntimeError, match="partial or one-sided"):
            vm_mgr.discover_former_vm_ha_members(
                spec,
                legacy_identities={"gateway-0": _legacy_identity(0), "gateway-1": None},
            )

    assert service.list_requests == []
    assert service.get_requests == []


def test_pre_marker_vm_ha_requires_complete_exact_pinned_runtime_identity() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            legacy_signature=True,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
    ):
        vm_mgr.discover_former_vm_ha_candidate_members(spec)
        with pytest.raises(RuntimeError, match="exact-pinned runtime inspection"):
            vm_mgr.discover_former_vm_ha_members(spec)


def test_partial_pre_marker_vm_ha_signature_fails_closed() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    members = {
        "gateway-0": _former_member(
            index=0,
            marker=None,
            legacy_signature=True,
            allocation_id="shared-private",
        ),
        "gateway-1": _former_member(index=1, marker=None),
    }

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        pytest.raises(RuntimeError, match="incomplete or mixed"),
    ):
        vm_mgr.discover_former_vm_ha_candidate_members(spec)


def test_name_collision_and_two_ordinary_vms_do_not_infer_former_vm_ha() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    allocation = _former_allocation()
    service = _FormerAllocationService([allocation])
    ordinary_members = {
        f"gateway-{index}": _former_member(
            index=index,
            marker=None,
            allocation_id="shared-private" if index == 0 else "",
        )
        for index in range(2)
    }

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: ordinary_members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}
    assert vm_mgr._former_vm_ha_snapshot is None
    assert vm_mgr._former_vm_ha_evidence is None
    assert service.list_requests == []


def test_mismatched_member_marker_does_not_infer_former_vm_ha() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    service = _FormerAllocationService([allocation])
    mismatched = render_provisioning_marker(_ha_spec(), 1).replace(
        '"cluster_id":"cluster"', '"cluster_id":"other-cluster"'
    )
    members["gateway-1"] = _former_member(index=1, marker=mismatched)

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}


def test_ambiguous_allocation_names_do_not_infer_former_vm_ha() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    other = _former_allocation(
        alloc_id="other-private",
        name="gateway-other-cluster-shared-private-ip",
    )
    service = _FormerAllocationService([allocation, other])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}


def test_incomplete_member_set_does_not_infer_former_vm_ha() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    service = _FormerAllocationService([allocation])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members.get(name) if name == "gateway-0" else None,
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}


@pytest.mark.parametrize(
    "allocation",
    [
        _former_allocation(owner_instance_id=None),
        _former_allocation(private=False),
        _former_allocation(owner_instance_id="unrelated-compute"),
    ],
    ids=("orphaned", "public", "unrelated-owner"),
)
def test_non_authoritative_allocation_evidence_remains_ordinary_non_ha(allocation) -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, _ = _former_identity_fixture()
    service = _FormerAllocationService([allocation])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}


def test_unattached_private_allocation_does_not_infer_former_vm_ha() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    members["gateway-0"] = _former_member(
        index=0,
        marker=render_provisioning_marker(_ha_spec(), 0),
    )
    service = _FormerAllocationService([allocation])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {}


def test_former_vm_ha_discovery_requires_coherent_allocation_and_member_records() -> None:
    from nebius.api.nebius.vpc.v1 import ListAllocationsRequest

    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    service = _FormerAllocationService([allocation])

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        result = vm_mgr.discover_former_vm_ha_members(spec)

    assert result == {
        "gateway-0": "203.0.113.10",
        "gateway-1": "203.0.113.11",
    }
    assert vm_mgr._former_vm_ha_snapshot is not None
    assert vm_mgr._former_vm_ha_evidence is not None
    assert vm_mgr._former_vm_ha_evidence.cluster_id == "cluster"
    assert vm_mgr._former_vm_ha_evidence.owner_instance_id == "compute-0"
    assert len(service.list_requests) == 1
    request = service.list_requests[0]
    assert isinstance(request, ListAllocationsRequest)
    assert request.parent_id == "project-1"
    assert request.page_size == 1000
    assert request.page_token == ""
    assert not hasattr(request, "metadata_filter")


def test_former_vm_ha_identity_drift_blocks_teardown_after_classification() -> None:
    vm_mgr = VMManager(project_id="project-1", zone="eu-north1-a")
    spec, members, allocation = _former_identity_fixture()
    service = _FormerAllocationService([allocation])
    current_members = dict(members)

    with (
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_get_vm_by_name_for_vm_ha_preflight",
            side_effect=lambda _, name: current_members[name],
        ),
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=service),
    ):
        expected = vm_mgr.discover_former_vm_ha_members(spec)
        current_members["gateway-1"] = _former_member(
            index=1,
            marker=render_provisioning_marker(_ha_spec(), 1),
            compute_id="replacement-compute",
        )
        with pytest.raises(RuntimeError, match="evidence changed"):
            vm_mgr.verify_former_vm_ha_member_snapshot(spec, expected)
