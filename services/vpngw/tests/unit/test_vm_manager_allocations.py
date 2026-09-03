from __future__ import annotations

import asyncio
import base64
import os
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_ha_cloud import AllocationOwner, wait_vm_ha_operation
from nebius_vpngw.deploy.vm_ha_identity import (
    LEGACY_VM_HA_SSH_HOST_KEY_PATH,
    FormerVMHAProvenance,
    LegacyVMHAIdentity,
    parse_provisioning_marker,
    recover_product_host_key,
    render_provisioning_marker,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    VMHAMigrationTransaction,
    normalize_vm_ha_observation,
    vm_ha_missing_standby_disk_name,
    vm_ha_passive_replacement_binding_key,
)
from nebius_vpngw.deploy.vm_manager import (
    VMManager,
    VMProvisioningConfig,
    VMProvisioningResult,
    validate_vm_ha_shared_allocation,
)
from nebius_vpngw.schema import VMHARole, VMHARouteTarget
from nebius_vpngw.vm_ha_credentials import (
    VMHACredentialIdentity,
    VMHACredentialSet,
)


def _route_targets() -> tuple[VMHARouteTarget, ...]:
    return (
        VMHARouteTarget(
            project_id="project-1",
            network_id="network-1",
            workload_subnet_id="workload-subnet-1",
            route_table_id="route-table-1",
        ),
    )


def _runtime_credentials() -> VMHACredentialSet:
    return VMHACredentialSet(
        nodes=(
            VMHACredentialIdentity(
                node_id="node-a",
                source_path=Path("/operator/nebius-credentials.json"),
                credential_sha256="d" * 64,
                service_account_id="service-account-a",
                authorized_key_id="authorized-key-a",
                project_id="project-1",
                service_account_name="gateway-ha",
            ),
            VMHACredentialIdentity(
                node_id="node-b",
                source_path=Path("/operator/nebius-credentials.json"),
                credential_sha256="d" * 64,
                service_account_id="service-account-a",
                authorized_key_id="authorized-key-a",
                project_id="project-1",
                service_account_name="gateway-ha",
            ),
        )
    )


def test_missing_standby_preflight_uses_fresh_disk_name_and_retained_allocations() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    manager._build_sdk_client = Mock(return_value=object())
    manager._require_ha_compute_absent = Mock()
    manager._get_vm_by_name_for_vm_ha_preflight = Mock(return_value=None)
    manager._get_ha_disk_by_name = Mock(return_value=None)
    allocation_client = object()
    manager._resolve_client_apis = Mock(
        return_value=(object(), object(), object(), allocation_client)
    )
    manager._require_retained_allocation = Mock()

    manager.validate_missing_vm_ha_standby_replacement(
        _ha_spec(),
        ["10.0.0.0/8"],
        target_instance_name="gateway-1",
        retired_compute_id="compute-retired",
        replacement_disk_name="gateway-1-boot-r2-abcdef012345",
        primary_allocation_id="primary-1",
        public_allocation_id="public-1",
    )

    manager._require_ha_compute_absent.assert_called_once_with("compute-retired")
    manager._get_vm_by_name_for_vm_ha_preflight.assert_called_once_with(
        manager._build_sdk_client.return_value,
        "gateway-1",
    )
    manager._get_ha_disk_by_name.assert_called_once_with(
        manager._build_sdk_client.return_value,
        "gateway-1-boot-r2-abcdef012345",
    )
    assert manager._require_retained_allocation.call_args_list == [
        call(
            allocation_client,
            "primary-1",
            require_detached=True,
        ),
        call(
            allocation_client,
            "public-1",
            require_detached=True,
        ),
    ]


def _install_missing_standby_replacement_journal(
    manager: VMManager,
    tmp_path: Path,
) -> tuple[VMHALifecycleJournal, str]:
    members = (
        VMHALifecycleMember(
            0,
            "gateway-0",
            "node-a",
            "active",
            "compute-0",
            "eth0",
            "203.0.113.10",
            "11",
            "disk-0",
            "subnet-1",
            "primary-0",
            "public-0",
            ("shared-private",),
        ),
        VMHALifecycleMember(
            1,
            "gateway-1",
            "node-b",
            "passive",
            "compute-retired",
            "eth0",
            "203.0.113.11",
            "12",
            "disk-retired",
            "subnet-1",
            "primary-1",
            "public-1",
        ),
    )
    initial = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings={
            "compute:gateway-0": "compute-0",
            "compute:gateway-1": "compute-retired",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-retired",
            "primary-allocation:gateway-0:eth0": "primary-0",
            "primary-allocation:gateway-1:eth0": "primary-1",
            "public-allocation:gateway-0:eth0": "public-0",
            "public-allocation:gateway-1:eth0": "public-1",
            "shared-allocation-id": "shared-private",
            "shared-allocation-owner-compute": "compute-0",
            "shared-allocation-owner-nic": "eth0",
        },
    )
    active = replace(
        initial,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="shared-private",
        route_runtime_id="route-runtime",
        route_targets=tuple(target.model_dump_json() for target in _route_targets()),
    )
    observation = {
        "members": [
            {
                "aliases": ["shared-private"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "present": True,
                "state": "running",
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "present": True,
        },
    }
    disk_name = vm_ha_missing_standby_disk_name(
        gateway_name="gateway",
        instance_name="gateway-1",
        predecessor_sha256=active.record_sha256,
        cycle=1,
    )
    replacement = VMHALifecycleState.start_missing_standby_replacement(
        active,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        replacement_disk_name=disk_name,
        operation_id="d" * 64,
        approval_digest="e" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="f" * 64,
        current_observation=observation,
    )
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(replacement)
    journal = VMHALifecycleJournal(store, replacement)
    manager.set_vm_ha_lifecycle_journal(journal)
    return journal, disk_name


def test_missing_standby_executor_creates_only_fresh_disk_and_non_owner_compute(
    tmp_path: Path,
) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    _journal, disk_name = _install_missing_standby_replacement_journal(manager, tmp_path)
    client = object()
    instance_api = object()
    disk_client = Mock()
    allocation_client = object()
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
    disk = SimpleNamespace(id="disk-new")
    compute = SimpleNamespace(id="compute-new")

    manager._build_sdk_client = Mock(return_value=client)
    manager._verify_vm_ha_transaction_preconditions = Mock()
    manager._require_ha_compute_absent = Mock()
    manager._get_vm_by_name_for_vm_ha_preflight = Mock(side_effect=[None, None])
    manager._prepare_gateway_ssh_enrollment_cloud_inits = Mock(
        return_value={"gateway-1": "#cloud-config\nreplacement\n"}
    )
    manager._build_vm_provisioning_config = Mock(return_value=provisioning)
    manager._resolve_client_apis = Mock(
        return_value=(instance_api, object(), object(), allocation_client)
    )
    manager._require_retained_allocation = Mock()
    manager._begin_vm_ha_effect = Mock(side_effect=["disk-operation", "compute-operation"])
    manager._complete_vm_ha_effect = Mock()
    manager._get_ha_disk_by_name = Mock(side_effect=[None, disk])
    manager._get_ha_disk_by_id = Mock()
    manager._resolve_boot_image_id = Mock(return_value="image-1")
    manager._build_boot_disk_create_request = Mock(return_value=object())

    def submit_disk(*_args, **_kwargs) -> str:
        manager._vm_ha_accepted_resource_ids["replace-missing-gateway-1-create-boot-disk"] = (
            "disk-new"
        )
        return "disk-new"

    manager._submit_boot_disk_create = Mock(side_effect=submit_disk)

    def create_compute(*_args, **_kwargs) -> bool:
        manager._vm_ha_accepted_resource_ids["replace-missing-gateway-1-create-compute"] = (
            "compute-new"
        )
        return True

    manager._create_instance_with_fallback = Mock(side_effect=create_compute)
    manager._get_ha_instance_by_name = Mock(return_value=compute)
    manager._vm_public_ip_from_object = Mock(return_value="203.0.113.11")
    manager._wait_for_vm_ha_member_ssh = Mock()
    manager.set_ha_private_alias = Mock()
    runtime_binding = object()
    manager._build_vm_ha_runtime_binding = Mock(return_value=runtime_binding)

    with patch(
        "nebius.api.nebius.compute.v1.DiskServiceClient",
        return_value=disk_client,
    ):
        result = manager.replace_missing_vm_ha_standby(
            _ha_spec(),
            ["10.0.0.0/8"],
            approval_digest="e" * 64,
        )

    disk_request_call = manager._build_boot_disk_create_request.call_args
    assert disk_request_call.args[0] == disk_name
    assert disk_name != "gateway-1-boot"
    create_call = manager._create_instance_with_fallback.call_args
    assert create_call.args[2] == "gateway-1"
    assert create_call.args[4] == "disk-new"
    assert create_call.args[5] == ["public-1"]
    assert manager._private_alloc_ids["gateway-1"] == ["primary-1"]
    manager._require_ha_compute_absent.assert_called_once_with("compute-retired")
    manager._get_ha_disk_by_id.assert_not_called()
    manager.set_ha_private_alias.assert_not_called()
    assert not disk_client.delete.called
    assert isinstance(result, VMProvisioningResult)
    assert result.vm_ha_runtime_binding is runtime_binding


def test_missing_standby_executor_rejects_foreign_disk_after_accepted_create(
    tmp_path: Path,
) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    journal, _disk_name = _install_missing_standby_replacement_journal(manager, tmp_path)
    create_disk_effect = "replace-missing-gateway-1-create-boot-disk"
    journal.begin(create_disk_effect)
    journal.record_cloud_operation(create_disk_effect, "cloud-disk-operation")
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

    manager._build_sdk_client = Mock(return_value=object())
    manager._verify_vm_ha_transaction_preconditions = Mock()
    manager._require_ha_compute_absent = Mock()
    manager._get_vm_by_name_for_vm_ha_preflight = Mock(return_value=None)
    manager._prepare_gateway_ssh_enrollment_cloud_inits = Mock(
        return_value={"gateway-1": "#cloud-config\nreplacement\n"}
    )
    manager._build_vm_provisioning_config = Mock(return_value=provisioning)
    manager._resolve_client_apis = Mock(return_value=(object(), object(), object(), object()))
    manager._require_retained_allocation = Mock()

    def resume_disk_effect(_effect: str) -> str:
        manager._vm_ha_accepted_resource_ids[create_disk_effect] = "disk-accepted"
        return "disk-operation"

    manager._begin_vm_ha_effect = Mock(side_effect=resume_disk_effect)
    manager._get_ha_disk_by_name = Mock(return_value=SimpleNamespace(id="disk-foreign"))
    manager._submit_boot_disk_create = Mock()

    with pytest.raises(RuntimeError, match="fresh standby disk identity is invalid"):
        manager.replace_missing_vm_ha_standby(
            _ha_spec(),
            ["10.0.0.0/8"],
            approval_digest="e" * 64,
        )

    manager._submit_boot_disk_create.assert_not_called()


def test_missing_standby_executor_adopts_accepted_compute_allocation_owner(
    tmp_path: Path,
) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    journal, _disk_name = _install_missing_standby_replacement_journal(manager, tmp_path)
    create_disk_effect = "replace-missing-gateway-1-create-boot-disk"
    create_compute_effect = "replace-missing-gateway-1-create-compute"
    disk_binding = vm_ha_passive_replacement_binding_key("disk", "gateway-1", 1)
    journal.begin(create_disk_effect)
    journal.complete(create_disk_effect, resource_updates={disk_binding: "disk-new"})
    journal.begin(create_compute_effect)
    journal.record_cloud_operation(create_compute_effect, "cloud-compute-operation")
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
    client = object()
    allocation_client = object()
    compute = SimpleNamespace(id="compute-new")

    manager._build_sdk_client = Mock(return_value=client)
    manager._verify_vm_ha_transaction_preconditions = Mock()
    manager._require_ha_compute_absent = Mock()
    manager._get_vm_by_name_for_vm_ha_preflight = Mock(return_value=compute)
    manager._prepare_gateway_ssh_enrollment_cloud_inits = Mock(
        return_value={"gateway-1": "#cloud-config\nreplacement\n"}
    )
    manager._build_vm_provisioning_config = Mock(return_value=provisioning)
    manager._resolve_client_apis = Mock(
        return_value=(object(), object(), object(), allocation_client)
    )
    manager._require_retained_allocation = Mock()

    def resume_compute(_effect: str) -> str:
        manager._vm_ha_accepted_resource_ids[create_compute_effect] = "compute-new"
        return "compute-operation"

    manager._begin_vm_ha_effect = Mock(side_effect=resume_compute)
    manager._get_ha_disk_by_name = Mock(return_value=SimpleNamespace(id="disk-new"))
    manager._create_instance_with_fallback = Mock()
    manager._complete_vm_ha_effect = Mock()
    manager._vm_public_ip_from_object = Mock(return_value="203.0.113.11")
    manager._wait_for_vm_ha_member_ssh = Mock()
    manager._get_ha_instance_by_name = Mock(return_value=compute)
    manager._build_vm_ha_runtime_binding = Mock(return_value=object())

    manager.replace_missing_vm_ha_standby(
        _ha_spec(),
        ["10.0.0.0/8"],
        approval_digest="e" * 64,
    )

    expected_owner = AllocationOwner("compute-new", "eth0")
    assert manager._require_retained_allocation.call_args_list == [
        call(
            allocation_client,
            "primary-1",
            require_detached=False,
            expected_owner=expected_owner,
        ),
        call(
            allocation_client,
            "public-1",
            require_detached=False,
            expected_owner=expected_owner,
        ),
    ]
    manager._begin_vm_ha_effect.assert_called_once_with(create_compute_effect)
    manager._create_instance_with_fallback.assert_not_called()


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
            network_interface=SimpleNamespace(instance_id=assignment_instance_id, name="eth0")
        )

    return SimpleNamespace(
        id=alloc_id,
        metadata=SimpleNamespace(
            id=alloc_id,
            name=name,
            parent_id="project-1",
            resource_version=1,
        ),
        spec=SimpleNamespace(
            ipv4_public=SimpleNamespace(**ipv4_public_fields),
            ipv4_private=None,
        ),
        status=SimpleNamespace(
            state="ASSIGNED" if assignment_instance_id is not None else "ALLOCATED",
            details=SimpleNamespace(**details_fields),
            assignment=assignment,
        ),
    )


def test_allocation_ip_from_obj_accepts_ipv4_public_cidr() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    alloc = _public_allocation("89.169.123.157", use_cidr_only=True)

    assert vm_mgr._allocation_ip_from_obj(alloc) == "89.169.123.157"


def test_allocation_ip_from_obj_skips_placeholder_spec_cidr_and_uses_status_details() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    alloc = _public_allocation("204.12.170.147", spec_cidr_only=True)

    assert vm_mgr._allocation_ip_from_obj(alloc) == "204.12.170.147"


def test_allocation_is_attached_accepts_assignment_network_interface() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    alloc = _public_allocation("204.12.170.147", assignment_instance_id="computeinstance-1")

    assert vm_mgr._allocation_is_attached(alloc)


def test_allocation_state_uses_sdk_enum_name_instead_of_integer_value() -> None:
    from nebius.api.nebius.vpc.v1 import AllocationStatus

    allocation = SimpleNamespace(status=SimpleNamespace(state=AllocationStatus.State.ALLOCATED))

    assert VMManager._allocation_state(allocation) == "ALLOCATED"


def test_prepared_public_allocation_accepts_assigned_intended_gateway_attachment() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    allocation = _public_allocation(
        "203.0.113.20",
        assignment_instance_id="compute-1",
    )
    allocation_client = SimpleNamespace(
        get=Mock(return_value=SimpleNamespace(wait=lambda: allocation))
    )

    observed, address = vm_mgr._validate_prepared_public_allocation(
        allocation_client,
        allocation,
        subnet_id="subnet-1",
        desired_ip="203.0.113.20",
        expected_attachment=("compute-1", "eth0"),
    )

    assert observed is allocation
    assert address == "203.0.113.20"


@pytest.mark.parametrize(
    ("state", "assignment_instance_id"),
    (("ASSIGNED", None), ("ALLOCATED", "compute-1")),
)
def test_prepared_public_allocation_rejects_inconsistent_stable_state_and_assignment(
    state: str,
    assignment_instance_id: str | None,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    allocation = _public_allocation(
        "203.0.113.20",
        assignment_instance_id=assignment_instance_id,
    )
    allocation.status.state = state
    allocation_client = SimpleNamespace(
        get=Mock(return_value=SimpleNamespace(wait=lambda: allocation))
    )

    with pytest.raises(RuntimeError, match="not stably allocated"):
        vm_mgr._validate_prepared_public_allocation(
            allocation_client,
            allocation,
            subnet_id="subnet-1",
            desired_ip="203.0.113.20",
            expected_attachment=("compute-1", "eth0"),
        )


def test_sdk_client_explicit_token_overrides_conflicting_ambient_token(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "ambient-token")
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        auth_token="selected-token",
    )

    with patch("nebius_vpngw.deploy.vm_manager.build_operator_sdk_client") as build_client:
        client = vm_mgr._build_sdk_client("eu-north1")

    assert client is build_client.return_value
    build_client.assert_called_once_with(explicit_token="selected-token")
    assert os.environ["NEBIUS_IAM_TOKEN"] == "ambient-token"


def test_sdk_client_cli_profile_uses_renewable_cli_credentials(monkeypatch) -> None:
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "ambient-token")
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    with patch("nebius_vpngw.deploy.vm_manager.build_operator_sdk_client") as build_client:
        client = vm_mgr._build_sdk_client("eu-north1")
        cached_client = vm_mgr._build_sdk_client("eu-north1")

    assert client is build_client.return_value
    assert cached_client is client
    build_client.assert_called_once_with(explicit_token=None)
    assert os.environ["NEBIUS_IAM_TOKEN"] == "ambient-token"


def test_vm_manager_close_without_sdk_is_idempotent(capsys) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")

    manager.close()
    manager.close()

    assert capsys.readouterr().err == ""


def test_vm_manager_context_closes_one_reused_sdk_exactly_once() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    sdk = MagicMock()
    manager._sdk_client = sdk

    with manager as entered:
        assert entered is manager
        assert manager._build_sdk_client("eu-north1") is sdk
        assert manager._build_sdk_client("eu-north1") is sdk

    manager.close()
    sdk.sync_close.assert_called_once_with()


def test_vm_manager_rejects_sdk_acquisition_after_close() -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    manager.close()

    with pytest.raises(RuntimeError, match="VMManager is closed"):
        manager._build_sdk_client("eu-north1")
    with pytest.raises(RuntimeError, match="VMManager is closed"):
        manager.__enter__()


def test_vm_manager_cleanup_failure_is_sanitized_and_non_fatal(capsys) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    sdk = MagicMock()
    sdk.sync_close.side_effect = RuntimeError("secret SDK detail")
    manager._sdk_client = sdk

    with manager:
        pass

    captured = capsys.readouterr()
    assert captured.err == "[VMManager] Warning: failed to close Nebius SDK resources.\n"
    assert "secret SDK detail" not in captured.err
    sdk.sync_close.assert_called_once_with()


def test_vm_manager_cleanup_failure_preserves_body_exception(capsys) -> None:
    manager = VMManager(project_id="project-1", region="eu-north1")
    sdk = MagicMock()
    sdk.sync_close.side_effect = RuntimeError("cleanup detail")
    manager._sdk_client = sdk

    with pytest.raises(ValueError, match="body failure"), manager:
        raise ValueError("body failure")

    assert capsys.readouterr().err == (
        "[VMManager] Warning: failed to close Nebius SDK resources.\n"
    )
    sdk.sync_close.assert_called_once_with()


def test_vm_manager_cleanup_failure_ignores_broken_stderr(monkeypatch) -> None:
    class BrokenStderr:
        def write(self, _value: str) -> int:
            raise OSError("stderr unavailable")

    successful = VMManager(project_id="project-1", region="eu-north1")
    successful_sdk = MagicMock()
    successful_sdk.sync_close.side_effect = RuntimeError("cleanup detail")
    successful._sdk_client = successful_sdk

    failing = VMManager(project_id="project-1", region="eu-north1")
    failing_sdk = MagicMock()
    failing_sdk.sync_close.side_effect = RuntimeError("cleanup detail")
    failing._sdk_client = failing_sdk

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stderr", BrokenStderr())
        with successful:
            pass
        with pytest.raises(ValueError, match="body failure"), failing:
            raise ValueError("body failure")

    successful_sdk.sync_close.assert_called_once_with()
    failing_sdk.sync_close.assert_called_once_with()


def test_vm_ha_member_preflight_classifies_typed_unauthenticated() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    provider_error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.UNAUTHENTICATED)
    )
    request = MagicMock()
    request.wait.side_effect = provider_error
    service = MagicMock()
    service.get_by_name.return_value = request

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(RuntimeError, match="cloud authentication failed") as raised,
    ):
        vm_mgr._get_vm_by_name_for_vm_ha_preflight(object(), "gateway-0")

    assert raised.value.__cause__ is provider_error


def test_vm_ha_member_preflight_classifies_typed_not_found_as_absent() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    provider_error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.NOT_FOUND)
    )
    request = MagicMock()
    request.wait.side_effect = provider_error
    service = MagicMock()
    service.get_by_name.return_value = request

    with patch(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        return_value=service,
    ):
        result = vm_mgr._get_vm_by_name_for_vm_ha_preflight(object(), "gateway-0")

    assert result is None


def test_vm_ha_member_preflight_rejects_untyped_not_found_text() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    provider_error = RuntimeError("NOT_FOUND from an untyped provider boundary")
    request = MagicMock()
    request.wait.side_effect = provider_error
    service = MagicMock()
    service.get_by_name.return_value = request

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(RuntimeError, match="could not be classified") as raised,
    ):
        vm_mgr._get_vm_by_name_for_vm_ha_preflight(object(), "gateway-0")

    assert raised.value.__cause__ is provider_error


@pytest.mark.parametrize(
    "provider_error",
    [
        RequestError(SimpleNamespace(code=StatusCode.PERMISSION_DENIED)),  # type: ignore[arg-type]
        RequestError(SimpleNamespace(code=StatusCode.UNAVAILABLE)),  # type: ignore[arg-type]
        RuntimeError("NOT_FOUND from an untyped provider boundary"),
    ],
)
def test_ordinary_instance_existence_fails_closed_on_unclassified_exact_read(
    provider_error: Exception,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    request = MagicMock()
    request.wait.side_effect = provider_error
    service = MagicMock()
    service.get_by_name.return_value = request

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        pytest.raises(RuntimeError, match="could not be classified") as raised,
    ):
        vm_mgr._instance_exists(object(), "gateway-0")

    assert raised.value.__cause__ is provider_error


@pytest.mark.parametrize(
    "provider_error",
    [
        RequestError(SimpleNamespace(code=StatusCode.PERMISSION_DENIED)),  # type: ignore[arg-type]
        RequestError(SimpleNamespace(code=StatusCode.UNAVAILABLE)),  # type: ignore[arg-type]
        RuntimeError("NOT_FOUND from an untyped provider boundary"),
    ],
)
def test_ordinary_boot_disk_lookup_failure_cannot_fall_back_to_creation(
    provider_error: Exception,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    request = MagicMock()
    request.wait.side_effect = provider_error
    service = MagicMock()
    service.get_by_name.return_value = request
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

    with (
        patch(
            "nebius.api.nebius.compute.v1.DiskServiceClient",
            return_value=service,
        ),
        pytest.raises(RuntimeError, match="could not be classified") as raised,
    ):
        vm_mgr._ensure_boot_disk(
            object(),
            SimpleNamespace(vm_ha=None, vm_spec={}),
            "gateway-0",
            provisioning,
            recreate=False,
        )

    assert raised.value.__cause__ is provider_error
    service.create.assert_not_called()


def test_list_allocations_by_ip_waits_for_request_objects() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    alloc = _public_allocation("204.12.170.147", spec_cidr_only=True)

    class FakeRequest:
        def wait(self):
            return SimpleNamespace(items=[alloc], next_page_token="")

    alloc_client = SimpleNamespace(list=lambda request: FakeRequest())

    mapping = vm_mgr._list_allocations_by_ip(alloc_client)

    assert mapping == {"204.12.170.147": alloc}


def test_list_allocations_by_ip_reads_every_page() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    first = _public_allocation(
        "204.12.170.147",
        alloc_id="allocation-first",
        spec_cidr_only=True,
    )
    second = _public_allocation(
        "204.12.170.148",
        alloc_id="allocation-second",
        spec_cidr_only=True,
    )

    def list_page(request):
        if not request.page_token:
            page = SimpleNamespace(items=[first], next_page_token="next")
        else:
            page = SimpleNamespace(items=[second], next_page_token="")
        return SimpleNamespace(wait=lambda: page)

    mapping = vm_mgr._list_allocations_by_ip(SimpleNamespace(list=list_page), fail_closed=True)

    assert mapping == {
        "204.12.170.147": first,
        "204.12.170.148": second,
    }


def test_strict_preparation_does_not_create_after_transitional_inventory_read_failure() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    transitional = _public_allocation(
        "203.0.113.10",
        alloc_id="allocation-releasing",
        name="gateway-0-eth0-ip",
    )
    transitional.status.state = "DELETING"
    calls = 0

    def list_allocations(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    items=[transitional],
                    next_page_token="",
                )
            )
        raise OSError("inventory unavailable")

    allocation_client = SimpleNamespace(list=list_allocations)
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=1,
        region="eu-north1",
        external_ips=[["203.0.113.10"]],
        vm_spec={"num_nics": 1},
    )
    create = Mock()

    with (
        patch(
            "nebius.api.nebius.vpc.v1.AllocationServiceClient",
            return_value=allocation_client,
        ),
        patch.object(vm_mgr, "_preparation_instance_ids", return_value={0: None}),
        patch.object(vm_mgr, "_hydrate_allocation", side_effect=lambda _client, item: item),
        patch.object(vm_mgr, "_create_prepared_public_allocation", create),
        patch("nebius_vpngw.deploy.vm_manager.time.sleep") as sleep,
        pytest.raises(RuntimeError, match="could not be listed"),
    ):
        vm_mgr._prepare_public_allocations_for_subnet(
            object(),
            spec,
            "subnet-1",
            instance_indices={0},
            desired_external_ips=[["203.0.113.10"]],
            require_unattached=False,
            strict=True,
        )

    create.assert_not_called()
    sleep.assert_called_once_with(2)
    assert calls == 2


def test_interactive_allocation_inventory_filters_and_binds_exact_gateway_attachments() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    vm_mgr._sdk_client = object()
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=2,
        region="eu-north1",
        external_ips=[],
        vm_spec={"num_nics": 1},
    )
    unattached = _public_allocation(
        "203.0.113.10",
        alloc_id="allocation-unattached",
        name="reserved-a",
    )
    assigned = _public_allocation(
        "203.0.113.20",
        alloc_id="allocation-assigned",
        name="reserved-b",
        assignment_instance_id="compute-1",
    )
    foreign_subnet = _public_allocation(
        "203.0.113.30",
        alloc_id="allocation-foreign-subnet",
        subnet_id="subnet-2",
    )
    foreign_owner = _public_allocation(
        "203.0.113.40",
        alloc_id="allocation-foreign-owner",
        assignment_instance_id="compute-other",
    )
    allocations = {item.id: item for item in (unattached, assigned, foreign_subnet, foreign_owner)}
    allocation_client = SimpleNamespace(
        list=Mock(
            return_value=SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    items=list(allocations.values()),
                    next_page_token="",
                )
            )
        ),
        get=Mock(side_effect=lambda request: SimpleNamespace(wait=lambda: allocations[request.id])),
    )
    instances = {
        name: SimpleNamespace(
            id=f"compute-{index}",
            metadata=SimpleNamespace(
                id=f"compute-{index}",
                name=name,
                parent_id="project-1",
            ),
        )
        for index, name in enumerate(("gateway-0", "gateway-1"))
    }
    instance_client = SimpleNamespace(
        get_by_name=Mock(
            side_effect=lambda request: SimpleNamespace(wait=lambda: instances[request.name])
        )
    )

    with (
        patch(
            "nebius.api.nebius.vpc.v1.AllocationServiceClient",
            return_value=allocation_client,
        ),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=instance_client,
        ),
    ):
        candidates = vm_mgr.list_eligible_public_allocations(
            spec,
            subnet_id="subnet-1",
        )

    assert [candidate.address for candidate in candidates] == [
        "203.0.113.10",
        "203.0.113.20",
    ]
    assert candidates[0].assigned_instance_index is None
    assert candidates[1].assigned_instance_index == 1
    assert candidates[1].assigned_nic_index == 0


def test_interactive_allocation_inventory_fails_closed_on_list_error() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    vm_mgr._sdk_client = object()
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=1,
        region="eu-north1",
        external_ips=[],
        vm_spec={"num_nics": 1},
    )
    allocation_client = SimpleNamespace(
        list=Mock(side_effect=OSError("inventory unavailable")),
    )
    instance_client = SimpleNamespace(
        get_by_name=Mock(
            return_value=SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    id="compute-0",
                    metadata=SimpleNamespace(
                        id="compute-0",
                        name="gateway-0",
                        parent_id="project-1",
                    ),
                )
            )
        )
    )

    with (
        patch(
            "nebius.api.nebius.vpc.v1.AllocationServiceClient",
            return_value=allocation_client,
        ),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=instance_client,
        ),
        pytest.raises(RuntimeError, match="inventory is unavailable"),
    ):
        vm_mgr.list_eligible_public_allocations(spec, subnet_id="subnet-1")


def test_ensure_public_allocation_reuses_existing_requested_ip_from_project_lookup() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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


def test_vm_ha_public_allocation_resolves_typed_already_exists_by_exact_shape(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    desired_ip = "89.169.123.157"
    existing = _public_allocation(
        desired_ip,
        alloc_id="public-1",
        name="nebius-vpn-gw-1-eth0-ip",
    )
    existing.metadata.parent_id = "project-1"
    existing.spec.ipv4_private = None
    existing.status.state = "ALLOCATED"
    error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.ALREADY_EXISTS)
    )
    client = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(wait=lambda: (_ for _ in ()).throw(error))
    )

    with (
        patch.object(
            vm_mgr,
            "_find_ha_allocation_by_name",
            return_value=existing,
        ),
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=existing),
    ):
        alloc_name, alloc_obj = vm_mgr._ensure_public_allocation(
            alloc_api=None,
            alloc_client=client,
            inst_name="nebius-vpn-gw-1",
            nic_name="eth0",
            subnet_id="subnet-1",
            desired_ip=desired_ip,
            preserved_alloc_id=None,
            operation_id="operation-1",
        )

    assert alloc_name == "nebius-vpn-gw-1-eth0-ip"
    assert alloc_obj is existing


def test_vm_ha_public_allocation_reuses_approved_id_without_configured_ip(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings={"public-allocation:gateway-0:eth0": "public-0"},
    )
    existing = _public_allocation(
        "89.169.123.157",
        alloc_id="public-0",
        name="gateway-0-eth0-ip",
    )
    existing.metadata.parent_id = "project-1"
    existing.spec.ipv4_private = None
    existing.status.state = "ALLOCATED"

    with (
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=existing),
        patch.object(
            vm_mgr,
            "_create_public_allocation_via_client",
            side_effect=AssertionError("approved retained allocation must not be recreated"),
        ),
    ):
        alloc_name, alloc_obj = vm_mgr._ensure_public_allocation(
            alloc_api=None,
            alloc_client=object(),
            inst_name="gateway-0",
            nic_name="eth0",
            subnet_id="subnet-1",
            desired_ip=None,
            preserved_alloc_id=None,
            approved_allocation_id="public-0",
            operation_id="operation-1",
        )

    assert alloc_name == "gateway-0-eth0-ip"
    assert alloc_obj is existing


def test_vm_ha_public_allocation_rejects_foreign_shape_after_already_exists(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    desired_ip = "89.169.123.157"
    foreign = _public_allocation(
        desired_ip,
        name="nebius-vpn-gw-1-eth0-ip",
        subnet_id="foreign-subnet",
    )
    foreign.metadata.parent_id = "project-1"
    foreign.spec.ipv4_private = None
    foreign.status.state = "ALLOCATED"
    error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.ALREADY_EXISTS)
    )
    client = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(wait=lambda: (_ for _ in ()).throw(error))
    )

    with (
        patch.object(
            vm_mgr,
            "_find_ha_allocation_by_name",
            return_value=foreign,
        ),
        patch.object(vm_mgr, "_get_allocation_by_id", return_value=foreign),
        pytest.raises(RuntimeError, match="foreign resource shape"),
    ):
        vm_mgr._ensure_public_allocation(
            alloc_api=None,
            alloc_client=client,
            inst_name="nebius-vpn-gw-1",
            nic_name="eth0",
            subnet_id="subnet-1",
            desired_ip=desired_ip,
            preserved_alloc_id=None,
            operation_id="operation-1",
        )


def test_resolve_prepared_public_allocation_prefers_requested_ip_over_stale_name() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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


def test_prepare_public_allocations_selects_only_passive_and_reuses_on_retry() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    spec = GatewayGroupSpec(
        name="nebius-vpn-gw",
        instance_count=2,
        region="eu-north1",
        external_ips=[],
        vm_spec={"num_nics": 1},
    )
    allocation = _public_allocation(
        "203.0.113.20",
        name="nebius-vpn-gw-1-eth0-ip",
        subnet_id="subnet-1",
    )
    allocation.metadata.parent_id = "project-1"
    allocation.spec.ipv4_private = None
    allocation.status.state = "ALLOCATED"
    resolved_names: list[str] = []

    def resolve(_client, _subnet_id, alloc_name, _desired_ip, allocations_by_ip):
        resolved_names.append(alloc_name)
        if "-0-" in alloc_name:
            raise AssertionError("the active allocation must not be looked up")
        return allocation, allocations_by_ip

    with (
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=object()),
        patch.object(
            vm_mgr,
            "_resolve_prepared_public_allocation",
            side_effect=resolve,
        ),
        patch.object(
            vm_mgr,
            "_create_prepared_public_allocation",
            side_effect=AssertionError("retry must reuse the canonical passive allocation"),
        ),
        patch.object(vm_mgr, "_resolve_prepared_public_ip", return_value="203.0.113.20"),
        patch.object(vm_mgr, "_hydrate_allocation", return_value=allocation),
        patch.object(vm_mgr, "_validate_requested_public_allocation", return_value=allocation),
    ):
        first = vm_mgr._prepare_public_allocations_for_subnet(
            object(),
            spec,
            "subnet-1",
            instance_indices={1},
            desired_external_ips=[],
            require_unattached=True,
        )
        second = vm_mgr._prepare_public_allocations_for_subnet(
            object(),
            spec,
            "subnet-1",
            instance_indices={1},
            desired_external_ips=[],
            require_unattached=True,
        )

    assert first == second == {1: ["203.0.113.20"]}
    assert resolved_names == [
        "nebius-vpn-gw-1-eth0-ip",
        "nebius-vpn-gw-1-eth0-ip",
    ]


def test_passive_allocation_retry_reuses_create_accepted_before_resolution_failure() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    spec = GatewayGroupSpec(
        name="nebius-vpn-gw",
        instance_count=2,
        region="eu-north1",
        external_ips=[],
        vm_spec={"num_nics": 1},
    )
    allocation = _public_allocation(
        "203.0.113.20",
        name="nebius-vpn-gw-1-eth0-ip",
        subnet_id="subnet-1",
    )
    allocation.metadata.parent_id = "project-1"
    allocation.spec.ipv4_private = None
    allocation.status.state = "ALLOCATED"
    resolve_calls = 0

    def resolve(_client, _subnet_id, alloc_name, _desired_ip, allocations_by_ip):
        nonlocal resolve_calls
        resolve_calls += 1
        assert alloc_name == "nebius-vpn-gw-1-eth0-ip"
        return (None if resolve_calls == 1 else allocation), allocations_by_ip

    with (
        patch("nebius.api.nebius.vpc.v1.AllocationServiceClient", return_value=object()),
        patch.object(vm_mgr, "_resolve_prepared_public_allocation", side_effect=resolve),
        patch.object(
            vm_mgr,
            "_create_prepared_public_allocation",
            return_value=allocation,
        ) as create,
        patch.object(
            vm_mgr,
            "_resolve_prepared_public_ip",
            side_effect=[RuntimeError("operation status unavailable"), "203.0.113.20"],
        ),
        patch.object(vm_mgr, "_hydrate_allocation", return_value=allocation),
        patch.object(vm_mgr, "_validate_requested_public_allocation", return_value=allocation),
    ):
        with pytest.raises(RuntimeError, match="operation status unavailable"):
            vm_mgr._prepare_public_allocations_for_subnet(
                object(),
                spec,
                "subnet-1",
                instance_indices={1},
                desired_external_ips=[],
                require_unattached=True,
            )
        retry = vm_mgr._prepare_public_allocations_for_subnet(
            object(),
            spec,
            "subnet-1",
            instance_indices={1},
            desired_external_ips=[],
            require_unattached=True,
        )

    assert retry == {1: ["203.0.113.20"]}
    create.assert_called_once()


def test_require_public_allocation_in_gateway_subnet_rejects_different_subnet() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    alloc = _public_allocation(
        "204.12.163.64",
        name="manual-migrate-ip",
        subnet_id="subnet-old",
    )

    with pytest.raises(RuntimeError, match="cannot be moved to gateway subnet subnet-new"):
        vm_mgr._require_public_allocation_in_gateway_subnet(
            SimpleNamespace(get=lambda request: SimpleNamespace(wait=lambda: alloc)),
            alloc,
            "subnet-new",
            "204.12.163.64",
        )


def test_set_ha_private_alias_updates_only_exact_nic_and_preserves_primary() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        Instance,
        InstanceSpec,
        IPAddress,
        IPAlias,
        NetworkInterfaceSpec,
    )

    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    instance = Instance(
        metadata=ResourceMetadata(
            id="instance-new", parent_id="project-1", name="new", resource_version=7
        ),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    subnet_id="subnet-1",
                    name="eth0",
                    ip_address=IPAddress(allocation_id="old-private"),
                    aliases=[IPAlias(allocation_id="existing-alias")],
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
        vm_mgr.set_ha_private_alias("instance-new", "eth0", "shared-private", True)

    request = service.update.call_args.args[0]
    assert request.metadata.id == "instance-new"
    assert request.spec.network_interfaces[0].ip_address.allocation_id == "old-private"
    assert [alias.allocation_id for alias in request.spec.network_interfaces[0].aliases] == [
        "existing-alias",
        "shared-private",
    ]
    assert request.spec.network_interfaces[1].ip_address.allocation_id == "other-private"
    assert [alias.allocation_id for alias in instance.spec.network_interfaces[0].aliases] == [
        "existing-alias"
    ]


def test_set_ha_private_alias_replay_skips_update() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        Instance,
        InstanceSpec,
        IPAddress,
        IPAlias,
        NetworkInterfaceSpec,
    )

    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    instance = Instance(
        metadata=ResourceMetadata(
            id="instance-new", parent_id="project-1", name="new", resource_version=7
        ),
        spec=InstanceSpec(
            network_interfaces=[
                NetworkInterfaceSpec(
                    subnet_id="subnet-1",
                    name="eth0",
                    ip_address=IPAddress(allocation_id="member-primary"),
                    aliases=[IPAlias(allocation_id="shared-private")],
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
        vm_mgr.set_ha_private_alias("instance-new", "eth0", "shared-private", True)

    service.update.assert_not_called()


def test_set_ha_private_alias_requires_exact_nic() -> None:
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import Instance, InstanceSpec, NetworkInterfaceSpec

    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    instance = Instance(
        metadata=ResourceMetadata(
            id="instance-new", parent_id="project-1", name="new", resource_version=7
        ),
        spec=InstanceSpec(
            network_interfaces=[NetworkInterfaceSpec(subnet_id="subnet-1", name="eth1")]
        ),
    )

    with (
        patch.object(vm_mgr, "_get_client", return_value=object()),
        patch.object(vm_mgr, "get_ha_instance", return_value=instance),
        pytest.raises(RuntimeError, match="exactly one NIC named eth0"),
    ):
        vm_mgr.set_ha_private_alias("instance-new", "eth0", "shared-private", True)


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
        region="eu-north1",
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


def _install_vm_ha_journal(
    vm_mgr: VMManager,
    tmp_path,
    *,
    bindings: dict[str, str] | None = None,
) -> VMHALifecycleJournal:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    state = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=(
            VMHALifecycleMember(0, "gateway-0", "node-a", "active", "", "", ""),
            VMHALifecycleMember(1, "gateway-1", "node-b", "passive", "", "", ""),
        ),
        operation_id="fixture-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        initial_resource_bindings=bindings,
    )
    store = VMHALifecycleStore(config_path)
    store.write_verified(state)
    journal = VMHALifecycleJournal(store, state)
    vm_mgr.set_vm_ha_lifecycle_journal(journal)
    return journal


def _install_vm_ha_activating_journal(
    vm_mgr: VMManager,
    tmp_path,
    *,
    credential_bindings: dict[str, str] | None = None,
    pending_effect: str | None = None,
    route_effect_completed: bool = False,
) -> tuple[VMHALifecycleJournal, dict[str, object], SimpleNamespace]:
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    target = _route_targets()[0]
    members = (
        VMHALifecycleMember(
            0,
            "gateway-0",
            "node-a",
            "active",
            "compute-0",
            "eth0",
            "203.0.113.10",
            "11",
            "disk-0",
            "subnet-1",
            "primary-0",
            "public-0",
            ("shared-private",),
        ),
        VMHALifecycleMember(
            1,
            "gateway-1",
            "node-b",
            "passive",
            "compute-1",
            "eth0",
            "203.0.113.11",
            "12",
            "disk-1",
            "subnet-1",
            "primary-1",
            "public-1",
        ),
    )
    observation: dict[str, object] = {
        "members": [
            {
                "aliases": ["shared-private"],
                "boot_disk_id": "disk-0",
                "compute_id": "compute-0",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "parent_id": "project-1",
                "present": True,
                "primary_allocation_id": "primary-0",
                "public_allocation_id": "public-0",
                "public_ip": "203.0.113.10",
                "subnet_id": "subnet-1",
            },
            {
                "aliases": [],
                "boot_disk_id": "disk-1",
                "compute_id": "compute-1",
                "compute_revision": "12",
                "instance_name": "gateway-1",
                "network_interface_name": "eth0",
                "parent_id": "project-1",
                "present": True,
                "primary_allocation_id": "primary-1",
                "public_allocation_id": "public-1",
                "public_ip": "203.0.113.11",
                "subnet_id": "subnet-1",
            },
        ],
        "project_id": "project-1",
        "route_targets": [target.model_dump(mode="json")],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "allocation_name": "gateway-cluster-shared-private-ip",
            "owner": {
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            "parent_id": "project-1",
            "present": True,
            "private_subnet_id": "subnet-1",
            "public_shape_present": False,
            "resource_revision": "7",
        },
    }
    route_runtime_id = "route-runtime-a"
    bindings = vm_mgr._observation_resource_bindings(observation)
    bindings.update(credential_bindings or {})
    bindings["route-runtime-id"] = route_runtime_id
    assert journal.state.transaction is not None
    completed_effects = ("verify-active-forwarding-and-routes",) if route_effect_completed else ()
    transaction = replace(
        journal.state.transaction,
        pending_effect=pending_effect,
        completed_effects=completed_effects,
        resource_bindings=tuple(sorted(bindings.items())),
        observation=normalize_vm_ha_observation(observation),
        observation_guard=None,
    )
    state = replace(
        journal.state,
        status=VMHALifecycleStatus.ACTIVATING,
        allocation_id="shared-private",
        members=members,
        route_runtime_id=route_runtime_id,
        route_targets=vm_mgr._serialized_route_targets((target,)),
        transaction=transaction,
    )
    journal.state = state
    binding = SimpleNamespace(
        cluster_id="cluster",
        shared_allocation_id="shared-private",
        route_runtime_id=route_runtime_id,
        route_targets=(target,),
        nodes=(
            SimpleNamespace(
                node_id="node-a",
                compute_id="compute-0",
                network_interface_name="eth0",
                role=SimpleNamespace(value="active"),
            ),
            SimpleNamespace(
                node_id="node-b",
                compute_id="compute-1",
                network_interface_name="eth0",
                role=SimpleNamespace(value="passive"),
            ),
        ),
    )
    return journal, observation, binding


def test_runtime_binding_selects_exact_approved_primary_and_shared_routes(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal, observation, _binding = _install_vm_ha_activating_journal(vm_mgr, tmp_path)
    shared_route_name = next(
        iter(
            vm_mgr._vm_ha_managed_route_names(
                cluster_id="cluster",
                route_target=_route_targets()[0],
                prefix="10.35.0.0/16",
                allocation_id="shared-private",
            )
        )
    )
    observation["routes"] = [
        {
            "allocation_id": "primary-0",
            "name": "vpngw-10.20.0.0-16",
            "prefix": "10.20.0.0/16",
            "route_id": "route-approved",
            "route_table_id": "route-table-1",
            "revision": "7",
        },
        {
            "allocation_id": "primary-1",
            "name": "vpngw-10.30.0.0-16",
            "prefix": "10.30.0.0/16",
            "route_id": "route-passive-primary",
            "route_table_id": "route-table-1",
            "revision": "8",
        },
        {
            "allocation_id": "shared-private",
            "name": shared_route_name,
            "prefix": "10.35.0.0/16",
            "route_id": "route-shared",
            "route_table_id": "route-table-1",
            "revision": "10",
        },
        {
            "allocation_id": "primary-0",
            "name": "customer-route",
            "prefix": "10.40.0.0/16",
            "route_id": "route-foreign",
            "route_table_id": "route-table-1",
            "revision": "9",
        },
    ]
    assert journal.state.transaction is not None
    journal.state = replace(
        journal.state,
        transaction=replace(
            journal.state.transaction,
            observation=normalize_vm_ha_observation(observation),
        ),
    )

    result = vm_mgr._vm_ha_migration_route_bindings(_ha_spec(), _route_targets(), "shared-private")

    assert [(route.route_id, route.allocation_id, route.resource_revision) for route in result] == [
        ("route-approved", "primary-0", "7"),
        ("route-shared", "shared-private", "10"),
    ]


def test_vm_ha_activation_resume_rebuilds_binding_without_provisioning(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _journal, observation, binding = _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        pending_effect="activate-node-b",
    )

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            side_effect=[deepcopy(observation), deepcopy(observation)],
        ) as observe,
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(
            vm_mgr,
            "_build_vm_ha_runtime_binding",
            return_value=binding,
        ) as build_binding,
        patch.object(
            vm_mgr,
            "ensure_group",
            side_effect=AssertionError("activation resume must not provision"),
        ),
    ):
        result = vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])

    assert result == {
        "gateway-0": "203.0.113.10",
        "gateway-1": "203.0.113.11",
    }
    assert result.vm_ha_runtime_binding is binding
    assert observe.call_count == 2
    build_binding.assert_called_once()


def test_vm_ha_activation_resume_accepts_exact_verified_credential_bindings(
    tmp_path,
) -> None:
    credentials = _runtime_credentials()
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=credentials,
    )
    _journal, observation, binding = _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        credential_bindings=credentials.resource_bindings(),
    )

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            side_effect=[deepcopy(observation), deepcopy(observation)],
        ),
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding", return_value=binding),
    ):
        result = vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])

    assert result.vm_ha_runtime_binding is binding


def test_vm_ha_activation_resume_rejects_verified_credential_drift(tmp_path) -> None:
    prior = _runtime_credentials()
    current = replace(
        prior,
        nodes=tuple(replace(node, authorized_key_id="authorized-key-b") for node in prior.nodes),
    )
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=current,
    )
    _journal, observation, _binding = _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        credential_bindings=prior.resource_bindings(),
    )

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            return_value=deepcopy(observation),
        ),
        pytest.raises(RuntimeError, match="credential-authorized-key:node-a"),
    ):
        vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])


@pytest.mark.parametrize("route_effect_completed", [False, True])
def test_vm_ha_activation_resume_authorizes_only_post_route_effect_route_drift(
    tmp_path,
    route_effect_completed: bool,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _journal, observation, binding = _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        route_effect_completed=route_effect_completed,
    )
    changed = deepcopy(observation)
    changed["routes"] = [
        {
            "allocation_id": "shared-private",
            "name": "vpngw-ha-route",
            "prefix": "10.0.0.0/8",
            "route_id": "route-a",
            "route_table_id": "route-table-1",
            "revision": "9",
        }
    ]
    changed["shared_allocation"]["resource_revision"] = "8"

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            side_effect=[deepcopy(changed), deepcopy(changed)],
        ),
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding", return_value=binding),
    ):
        if route_effect_completed:
            result = vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])
            assert result.vm_ha_runtime_binding is binding
        else:
            with pytest.raises(RuntimeError, match="before route activation"):
                vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_activation_resume_accepts_only_route_revision_drift_before_effect(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal, observation, binding = _install_vm_ha_activating_journal(vm_mgr, tmp_path)
    observation["routes"] = [
        {
            "allocation_id": "shared-private",
            "name": "vpngw-ha-route",
            "prefix": "10.0.0.0/8",
            "route_id": "route-a",
            "route_table_id": "route-table-1",
            "revision": "7",
        }
    ]
    assert journal.state.transaction is not None
    journal.state = replace(
        journal.state,
        transaction=replace(
            journal.state.transaction,
            observation=normalize_vm_ha_observation(observation),
        ),
    )
    changed = deepcopy(observation)
    changed["routes"][0]["revision"] = "8"

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            side_effect=[deepcopy(changed), deepcopy(changed)],
        ),
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding", return_value=binding),
    ):
        result = vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])

    assert result.vm_ha_runtime_binding is binding


def test_vm_ha_activation_resume_rejects_non_route_drift_after_route_effect(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _journal, observation, binding = _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        route_effect_completed=True,
    )
    changed = deepcopy(observation)
    changed["members"][0]["compute_revision"] = "99"

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            return_value=changed,
        ),
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_build_vm_ha_runtime_binding", return_value=binding),
        pytest.raises(RuntimeError, match="non-route cloud state changed"),
    ):
        vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_activation_resume_rejects_unknown_pending_effect(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_activating_journal(
        vm_mgr,
        tmp_path,
        pending_effect="provision-gateway-0-compute",
    )

    with pytest.raises(RuntimeError, match="unknown pending effect"):
        vm_mgr.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])


def test_ensure_vm_ha_shared_allocation_reuses_one_approved_identity(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings={"shared-allocation-id": "shared-private"},
    )
    allocation = SimpleNamespace(
        id="shared-private",
        metadata=SimpleNamespace(
            id="shared-private",
            name="gateway-cluster-shared-private-ip",
            parent_id="project-1",
        ),
        spec=SimpleNamespace(
            ipv4_private=SimpleNamespace(subnet_id="subnet-1"),
            ipv4_public=None,
        ),
        status=SimpleNamespace(state="ALLOCATED", assignment=None),
    )
    client = SimpleNamespace()

    stable_observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr,
            "_stable_vm_ha_effect_observation",
            return_value=stable_observation,
        ),
    ):
        allocation_id = vm_mgr._ensure_vm_ha_shared_allocation(
            client,
            _ha_spec(),
            "subnet-1",
        )

    assert allocation_id == "shared-private"
    assert vm_mgr._vm_ha_shared_allocation_id == "shared-private"


def test_ensure_vm_ha_shared_allocation_propagates_ambiguous_sdk_failure(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    client = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(
            wait=lambda: (_ for _ in ()).throw(OSError("denied"))
        )
    )

    with (
        patch.object(
            vm_mgr,
            "_stable_vm_ha_effect_observation",
            return_value={
                "members": [],
                "route_targets": [],
                "routes": [],
                "shared_allocation": {"present": False},
            },
        ),
        pytest.raises(RuntimeError, match="could not be created"),
    ):
        vm_mgr._ensure_vm_ha_shared_allocation(client, _ha_spec(), "subnet-1")


def test_ensure_vm_ha_shared_allocation_resolves_typed_already_exists(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    error = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.ALREADY_EXISTS)
    )
    client = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(wait=lambda: (_ for _ in ()).throw(error))
    )
    allocation = SimpleNamespace(
        id="shared-private",
        metadata=SimpleNamespace(
            id="shared-private",
            name="gateway-cluster-shared-private-ip",
            parent_id="project-1",
        ),
        spec=SimpleNamespace(
            ipv4_private=SimpleNamespace(subnet_id="subnet-1"),
            ipv4_public=None,
        ),
        status=SimpleNamespace(state="ALLOCATED", assignment=None),
    )
    before = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_name": "gateway-cluster-shared-private-ip",
            "present": False,
        },
    }
    after = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "allocation_name": "gateway-cluster-shared-private-ip",
            "owner": None,
            "parent_id": "project-1",
            "present": True,
            "private_subnet_id": "subnet-1",
            "public_shape_present": False,
            "resource_revision": "1",
        },
    }

    with (
        patch.object(
            vm_mgr,
            "_stable_vm_ha_effect_observation",
            side_effect=(before, after),
        ),
        patch.object(
            vm_mgr,
            "_find_ha_allocation_by_name",
            return_value=allocation,
        ),
    ):
        allocation_id = vm_mgr._ensure_vm_ha_shared_allocation(client, _ha_spec(), "subnet-1")

    assert allocation_id == "shared-private"


@pytest.mark.parametrize(
    ("parent_id", "private_subnet", "public_shape", "message"),
    (
        ("foreign-project", "subnet-1", None, "parent project"),
        ("project-1", "foreign-subnet", None, "subnet"),
        ("project-1", "subnet-1", SimpleNamespace(), "private and not public"),
    ),
)
def test_shared_allocation_validator_rejects_foreign_shape(
    parent_id,
    private_subnet,
    public_shape,
    message,
) -> None:
    allocation = SimpleNamespace(
        id="shared-private",
        metadata=SimpleNamespace(
            id="shared-private",
            name="gateway-cluster-shared-private-ip",
            parent_id=parent_id,
        ),
        spec=SimpleNamespace(
            ipv4_private=SimpleNamespace(subnet_id=private_subnet),
            ipv4_public=public_shape,
        ),
        status=SimpleNamespace(state="ALLOCATED", assignment=None),
    )

    with pytest.raises(RuntimeError, match=message):
        validate_vm_ha_shared_allocation(
            allocation,
            expected_allocation_id="shared-private",
            expected_name="gateway-cluster-shared-private-ip",
            expected_project_id="project-1",
            expected_subnet_id="subnet-1",
            expected_owner=None,
        )


def test_vm_ha_transaction_preflight_rejects_unapproved_current_state_drift(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }

    with (
        patch.object(vm_mgr, "observe_vm_ha_migration_state", return_value=observation),
        pytest.raises(RuntimeError, match="approved current state changed"),
    ):
        vm_mgr._verify_vm_ha_transaction_preconditions(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_transaction_preflight_uses_v4_trusted_observation_for_recovery(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    assert journal.state.transaction is not None
    journal.state = replace(
        journal.state,
        transaction=replace(
            journal.state.transaction,
            approval_kind="recovery",
            current_state_digest="e" * 64,
            observation=normalize_vm_ha_observation(observation),
        ),
    )

    with patch.object(
        vm_mgr,
        "observe_vm_ha_migration_state",
        return_value=observation,
    ):
        vm_mgr._verify_vm_ha_transaction_preconditions(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_transaction_preflight_matches_verified_credential_bindings(
    tmp_path,
) -> None:
    credentials = _runtime_credentials()
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=credentials,
    )
    journal = _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings=credentials.resource_bindings(),
    )
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    assert journal.state.transaction is not None
    journal.state = replace(
        journal.state,
        transaction=replace(
            journal.state.transaction,
            approval_kind="recovery",
            current_state_digest="e" * 64,
            observation=normalize_vm_ha_observation(observation),
        ),
    )

    with patch.object(
        vm_mgr,
        "observe_vm_ha_migration_state",
        return_value=observation,
    ):
        vm_mgr._verify_vm_ha_transaction_preconditions(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_transaction_preflight_rejects_verified_credential_drift(
    tmp_path,
) -> None:
    prior = _runtime_credentials()
    current = replace(
        prior,
        nodes=tuple(replace(node, authorized_key_id="authorized-key-b") for node in prior.nodes),
    )
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=current,
    )
    journal = _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings=prior.resource_bindings(),
    )
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    assert journal.state.transaction is not None
    journal.state = replace(
        journal.state,
        transaction=replace(
            journal.state.transaction,
            approval_kind="recovery",
            current_state_digest="e" * 64,
            observation=normalize_vm_ha_observation(observation),
        ),
    )

    with (
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            return_value=observation,
        ),
        pytest.raises(RuntimeError, match="credential-authorized-key:node-a"),
    ):
        vm_mgr._verify_vm_ha_transaction_preconditions(_ha_spec(), ["10.0.0.0/8"])


def test_vm_ha_transaction_preflight_allows_pending_effect_outcome_resolution(
    tmp_path,
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    journal.begin(
        "provision-shared-allocation",
        observation=observation,
        permitted_paths=("/shared_allocation/allocation_id",),
    )

    with patch.object(
        vm_mgr,
        "observe_vm_ha_migration_state",
        return_value=observation,
    ):
        vm_mgr._verify_vm_ha_transaction_preconditions(
            _ha_spec(),
            ["10.0.0.0/8"],
        )


def test_shared_allocation_effect_allows_new_unattached_owner_shape(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    before = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_name": "gateway-cluster-shared-private-ip",
            "present": False,
        },
    }
    after = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "allocation_name": "gateway-cluster-shared-private-ip",
            "owner": None,
            "parent_id": "project-1",
            "present": True,
            "private_subnet_id": "subnet-1",
            "public_shape_present": False,
            "resource_revision": "1",
        },
    }
    permitted = vm_mgr._vm_ha_effect_permitted_paths("provision-shared-allocation", before)

    assert "/shared_allocation/owner" in permitted
    journal.begin(
        "provision-shared-allocation",
        observation=before,
        permitted_paths=permitted,
    )
    journal.complete(
        "provision-shared-allocation",
        resource_updates={"shared-allocation-id": "shared-private"},
        observation=after,
    )


def test_failed_passive_replacement_effects_are_scoped_to_one_compute() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    vm_mgr._vm_ha_effect_spec = _ha_spec()
    observation = {
        "members": [
            {"instance_name": "gateway-0", "present": True},
            {
                "aliases": [],
                "boot_disk_id": "disk-1",
                "compute_id": "compute-1",
                "compute_revision": "11",
                "instance_name": "gateway-1",
                "network_interface_name": "eth0",
                "parent_id": "project-1",
                "present": True,
                "primary_allocation_id": "primary-1",
                "public_allocation_id": "public-1",
                "public_ip": "203.0.113.11",
                "subnet_id": "subnet-1",
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": True},
    }

    delete_paths = vm_mgr._vm_ha_effect_permitted_paths(
        "replace-failed-gateway-1-delete-compute",
        observation,
    )
    create_paths = vm_mgr._vm_ha_effect_permitted_paths(
        "replace-failed-gateway-1-create-compute",
        observation,
    )

    assert delete_paths == (
        "/members/1",
        "/members/1/*",
        "/members/1/aliases/*",
    )
    assert all(path.startswith("/members/1") for path in create_paths)
    assert all(
        path.startswith("/members/1")
        for path in vm_mgr._vm_ha_effect_permitted_paths(
            "replace-failed-2-gateway-1-create-compute",
            observation,
        )
    )
    assert (
        vm_mgr._vm_ha_effect_permitted_paths(
            "replace-failed-gateway-1-delete-boot-disk",
            observation,
        )
        == ()
    )


def test_failed_passive_replacement_waits_for_pinned_ssh_readiness() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    progress = Mock()

    with (
        patch.object(
            vm_mgr,
            "verify_vm_ha_existing_identities",
            side_effect=[RuntimeError("unreachable"), None],
        ) as verify,
        patch("nebius_vpngw.deploy.vm_manager.time.sleep") as sleep,
        patch(
            "nebius_vpngw.deploy.vm_manager.time.monotonic",
            side_effect=[0.0, 1.0, 2.0, 3.0],
        ),
    ):
        vm_mgr._wait_for_vm_ha_member_ssh(
            "gateway-1",
            "203.0.113.11",
            username="ubuntu",
            timeout=300,
            progress_callback=progress,
        )

    assert verify.call_count == 2
    sleep.assert_called_once_with(5.0)
    progress.assert_called_once_with()


def test_failed_passive_replacement_bounds_probe_and_sleep_to_one_deadline() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    with (
        patch.object(
            vm_mgr,
            "verify_vm_ha_existing_identities",
            side_effect=RuntimeError("unreachable"),
        ) as verify,
        patch("nebius_vpngw.deploy.vm_manager.time.sleep") as sleep,
        patch(
            "nebius_vpngw.deploy.vm_manager.time.monotonic",
            side_effect=[0.0, 0.0, 0.25, 0.5],
        ),
        pytest.raises(RuntimeError, match="did not reach pinned SSH readiness"),
    ):
        vm_mgr._wait_for_vm_ha_member_ssh(
            "gateway-1",
            "203.0.113.11",
            username="ubuntu",
            timeout=0.5,
        )

    assert verify.call_args.kwargs["probe_timeout"] == 0.5
    sleep.assert_called_once_with(0.25)


def test_failed_passive_replacement_never_retries_host_identity_failure() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")

    with (
        patch.object(
            vm_mgr,
            "verify_vm_ha_existing_identities",
            side_effect=RuntimeError("SSH host identity verification failed"),
        ) as verify,
        patch("nebius_vpngw.deploy.vm_manager.time.sleep") as sleep,
        pytest.raises(RuntimeError, match="identity verification failed"),
    ):
        vm_mgr._wait_for_vm_ha_member_ssh(
            "gateway-1",
            "203.0.113.11",
            username="ubuntu",
        )

    verify.assert_called_once()
    sleep.assert_not_called()


def test_completed_effect_revalidation_does_not_require_a_removed_guard(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    observation = {
        "members": [],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    journal.begin(
        "resolve-authoritative-route-targets",
        observation=observation,
        permitted_paths=(),
    )
    journal.complete(
        "resolve-authoritative-route-targets",
        observation=observation,
    )

    with patch.object(
        vm_mgr,
        "_stable_vm_ha_effect_observation",
        side_effect=AssertionError("completed effect must not reopen its guard"),
    ):
        vm_mgr._complete_vm_ha_effect("resolve-authoritative-route-targets")


def test_effect_guard_reports_only_unapproved_path_names(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    before = {"shared_allocation": {"present": False}, "members": []}
    journal.begin(
        "provision-shared-allocation",
        observation=before,
        permitted_paths=("/shared_allocation/present",),
    )

    with pytest.raises(ValueError, match=r"/members/0/present"):
        journal.complete(
            "provision-shared-allocation",
            observation={
                "shared_allocation": {"present": True},
                "members": [{"present": True}],
            },
        )


def test_vm_ha_attach_effect_rejects_an_extra_unrelated_alias(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings={"shared-allocation-id": "shared-private"},
    )
    vm_mgr._vm_ha_effect_spec = _ha_spec()
    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    before = {
        "members": [
            {
                "aliases": [],
                "compute_id": "compute-a",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "present": True,
            },
            {
                "aliases": [],
                "compute_id": "compute-b",
                "compute_revision": "12",
                "instance_name": "gateway-1",
                "network_interface_name": "eth0",
                "present": True,
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "owner": None,
            "present": True,
            "resource_revision": "5",
        },
    }
    after = deepcopy(before)
    after["members"][0]["aliases"] = ["shared-private", "foreign-alias"]
    after["members"][0]["compute_revision"] = "13"
    after["shared_allocation"]["owner"] = {
        "compute_id": "compute-a",
        "network_interface_name": "eth0",
    }
    after["shared_allocation"]["resource_revision"] = "6"

    with patch.object(
        vm_mgr,
        "_stable_vm_ha_effect_observation",
        side_effect=[before, after],
    ):
        vm_mgr._begin_vm_ha_effect("attach-shared-allocation-active")
        with pytest.raises(RuntimeError, match="unrelated alias"):
            vm_mgr._complete_vm_ha_effect("attach-shared-allocation-active")


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    (
        (None, None),
        ("aliases", ["foreign-alias"]),
        ("boot_disk_id", "foreign-disk"),
        ("primary_allocation_id", "foreign-primary"),
        ("public_allocation_id", "foreign-public"),
        ("subnet_id", "foreign-subnet"),
    ),
)
def test_vm_ha_compute_effect_validates_exact_create_footprint(
    tmp_path, changed_field: str | None, changed_value: object
) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(
        vm_mgr,
        tmp_path,
        bindings={
            "disk:gateway-0": "disk-0",
            "primary-allocation:gateway-0:eth0": "primary-0",
            "public-allocation:gateway-0:eth0": "public-0",
            "shared-allocation-id": "shared-private",
        },
    )
    vm_mgr._vm_ha_effect_spec = _ha_spec()
    before = {
        "members": [
            {"instance_name": "gateway-0", "present": False},
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "present": True,
            "private_subnet_id": "subnet-1",
        },
    }
    after = deepcopy(before)
    after["members"][0] = {
        "aliases": [],
        "boot_disk_id": "disk-0",
        "compute_id": "compute-0",
        "compute_revision": "11",
        "instance_name": "gateway-0",
        "network_interface_name": "eth0",
        "parent_id": "project-1",
        "present": True,
        "primary_allocation_id": "primary-0",
        "public_allocation_id": "public-0",
        "public_ip": "203.0.113.10",
        "state": "RUNNING",
        "subnet_id": "subnet-1",
    }
    if changed_field is not None:
        after["members"][0][changed_field] = changed_value

    with patch.object(
        vm_mgr,
        "_stable_vm_ha_effect_observation",
        side_effect=[before, after],
    ):
        vm_mgr._begin_vm_ha_effect("provision-gateway-0-compute")
        if changed_field is None:
            vm_mgr._complete_vm_ha_effect("provision-gateway-0-compute")
        else:
            with pytest.raises(RuntimeError, match="Compute create footprint"):
                vm_mgr._complete_vm_ha_effect("provision-gateway-0-compute")


def test_vm_ha_accepted_compute_operation_uses_instance_service_lookup(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    journal = _install_vm_ha_journal(vm_mgr, tmp_path)
    vm_mgr._vm_ha_effect_spec = _ha_spec()
    effect = "provision-gateway-0-compute"
    observation = {
        "members": [
            {"instance_name": "gateway-0", "present": False},
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {"present": False},
    }
    journal.begin(
        effect,
        observation=observation,
        permitted_paths=vm_mgr._vm_ha_effect_permitted_paths(effect, observation),
    )
    journal.record_cloud_operation(effect, "cloud-operation")
    accepted = SimpleNamespace(
        resource_id="compute-0",
        successful=Mock(return_value=True),
    )
    request = Mock(wait=Mock(return_value=accepted))
    operation_service = Mock(get=Mock(return_value=request))
    instance_service = Mock(operation_service=Mock(return_value=operation_service))
    vm_mgr._get_client = Mock(return_value=object())

    with (
        patch.object(vm_mgr, "_stable_vm_ha_effect_observation", return_value=observation),
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=instance_service,
        ),
        patch("nebius_vpngw.deploy.vm_manager.wait_vm_ha_operation") as wait_operation,
    ):
        vm_mgr._begin_vm_ha_effect(effect)

    instance_service.operation_service.assert_called_once_with()
    operation_service.get.assert_called_once()
    wait_operation.assert_called_once_with(accepted)
    assert vm_mgr._vm_ha_accepted_resource_ids[effect] == "compute-0"


def test_wait_vm_ha_operation_uses_sdk_poll_kwargs_without_duplicates() -> None:
    from nebius.aio.operation import Operation
    from nebius.api.nebius.common.v1 import Operation as OperationMessage

    observed: dict[str, object] = {}

    async def update_internal(**kwargs) -> None:
        kwargs.pop("request_deadline", None)
        kwargs.pop("authorization_deadline", None)
        observed.update(kwargs)

    class SynchronousChannel:
        @staticmethod
        def parent_id():
            return None

        @staticmethod
        def run_sync(coroutine, _timeout):
            return asyncio.run(coroutine)

    operation = Operation(
        "nebius.compute.v1.InstanceService.Update",
        SynchronousChannel(),  # type: ignore[arg-type]
        OperationMessage(id="operation-test"),
    )
    operation.done = MagicMock(side_effect=[False, False, False, True])  # type: ignore[method-assign]
    operation.update = update_internal  # type: ignore[method-assign]

    with patch(
        "nebius_vpngw.deploy.vm_ha_cloud.VM_HA_OPERATION_POLL_INTERVAL_SECONDS",
        1e-6,
    ):
        wait_vm_ha_operation(operation)

    assert observed == {
        "auth_timeout": 30.0,
        "per_retry_timeout": 10.0,
        "retries": 3,
        "timeout": 30.0,
    }


def test_vm_ha_observation_requests_use_the_bounded_policy() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    instance = SimpleNamespace(
        id="compute-0",
        metadata=SimpleNamespace(
            id="compute-0",
            name="gateway-0",
            parent_id="project-1",
        ),
    )
    instance_request = MagicMock(wait=MagicMock(return_value=instance))
    instance_service = MagicMock(get_by_name=MagicMock(return_value=instance_request))
    route_request = MagicMock(
        wait=MagicMock(return_value=SimpleNamespace(items=[], next_page_token=""))
    )
    subnet_service = MagicMock(list_by_network=MagicMock(return_value=route_request))
    allocation_service = MagicMock()
    allocation_service.get_by_name.side_effect = RequestError(  # type: ignore[arg-type]
        SimpleNamespace(code=StatusCode.NOT_FOUND)
    )

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=instance_service,
        ),
        patch.object(
            vm_mgr,
            "get_ha_instance",
            return_value=instance,
        ),
    ):
        vm_mgr._get_ha_instance_by_name(object(), "gateway-0")

    with (
        patch.object(
            vm_mgr,
            "_resolve_gateway_network",
            return_value=(object(), "network-1", object(), subnet_service),
        ),
        patch(
            "nebius_vpngw.deploy.route_manager.RouteManager.resolve_vm_ha_route_targets",
            return_value=(),
        ),
    ):
        vm_mgr._resolve_vm_ha_route_targets(object(), _ha_spec(), ["10.0.0.0/8"])

    assert vm_mgr._find_ha_allocation_by_name(allocation_service, "missing") is None

    expected = {
        "auth_timeout": 30.0,
        "per_retry_timeout": 10.0,
        "retries": 3,
        "timeout": 30.0,
    }
    assert instance_service.get_by_name.call_args.kwargs == expected
    assert subnet_service.list_by_network.call_args.kwargs == expected
    assert allocation_service.get_by_name.call_args.kwargs == expected


def test_vm_ha_operation_sync_propagates_failure() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    operation = SimpleNamespace(sync_wait=MagicMock(side_effect=OSError("operation failed")))

    with pytest.raises(OSError, match="operation failed"):
        vm_mgr._sync_vm_ha_operation(operation)


def test_build_vm_ha_runtime_binding_rereads_exact_active_owner() -> None:
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=_runtime_credentials(),
    )
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
                        ip_address=SimpleNamespace(allocation_id=f"primary-{suffix}"),
                        aliases=(
                            [SimpleNamespace(allocation_id="shared-private")]
                            if suffix == "a"
                            else []
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
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=_runtime_credentials(),
    )
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
                        ip_address=SimpleNamespace(allocation_id=f"primary-{suffix}"),
                        aliases=(
                            [SimpleNamespace(allocation_id="shared-private")]
                            if suffix == "a"
                            else []
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
        pytest.raises(RuntimeError, match="owner does not match the exact member NIC"),
    ):
        vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())


def test_build_vm_ha_runtime_binding_rejects_conflicting_passive_compute_owner() -> None:
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=_runtime_credentials(),
    )
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
                        ip_address=SimpleNamespace(allocation_id=f"primary-{suffix}"),
                        aliases=[SimpleNamespace(allocation_id="shared-private")],
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
        patch.object(vm_mgr, "set_ha_private_alias", setter),
        pytest.raises(RuntimeError, match="outside configured active"),
    ):
        vm_mgr._attach_vm_ha_shared_allocation_initially(
            allocation_id="shared-private",
            active_compute_id="compute-a",
            active_network_interface_name="eth0",
        )

    setter.assert_not_called()


def test_managed_reapply_retains_exact_promoted_owner_without_mutation(tmp_path) -> None:
    vm_mgr = VMManager(
        project_id="project-1",
        region="eu-north1",
        vm_ha_credentials=_runtime_credentials(),
    )
    members = (
        VMHALifecycleMember(
            0,
            "gateway-0",
            "node-a",
            "active",
            "compute-a",
            "eth0",
            "203.0.113.10",
            "11",
            "disk-a",
            "subnet-1",
            "primary-a",
            "public-a",
        ),
        VMHALifecycleMember(
            1,
            "gateway-1",
            "node-b",
            "passive",
            "compute-b",
            "eth0",
            "203.0.113.11",
            "12",
            "disk-b",
            "subnet-1",
            "primary-b",
            "public-b",
            ("shared-private",),
        ),
    )
    observation: dict[str, object] = {
        "members": [
            {
                "aliases": [],
                "boot_disk_id": "disk-a",
                "compute_id": "compute-a",
                "compute_revision": "11",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "parent_id": "project-1",
                "present": True,
                "primary_allocation_id": "primary-a",
                "public_allocation_id": "public-a",
                "public_ip": "203.0.113.10",
                "subnet_id": "subnet-1",
            },
            {
                "aliases": ["shared-private"],
                "boot_disk_id": "disk-b",
                "compute_id": "compute-b",
                "compute_revision": "13",
                "instance_name": "gateway-1",
                "network_interface_name": "eth0",
                "parent_id": "project-1",
                "present": True,
                "primary_allocation_id": "primary-b",
                "public_allocation_id": "public-b",
                "public_ip": "203.0.113.11",
                "subnet_id": "subnet-1",
            },
        ],
        "project_id": "project-1",
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "shared-private",
            "allocation_name": "gateway-cluster-shared-private-ip",
            "owner": {
                "compute_id": "compute-b",
                "network_interface_name": "eth0",
            },
            "parent_id": "project-1",
            "present": True,
            "private_subnet_id": "subnet-1",
            "public_shape_present": False,
            "resource_revision": "8",
        },
    }
    bindings = vm_mgr._observation_resource_bindings(observation)
    state = VMHALifecycleState(
        status=VMHALifecycleStatus.PROVISIONING,
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_id="shared-private",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        route_runtime_id="route-runtime",
        route_targets=vm_mgr._serialized_route_targets(_route_targets()),
        transaction=VMHAMigrationTransaction(
            operation_id="managed-reapply",
            approval_kind="migration",
            approval_digest="a" * 64,
            desired_state_digest="b" * 64,
            current_state_digest="c" * 64,
            checkpoint="approved-intent",
            pending_effect=None,
            completed_effects=(),
            resource_bindings=tuple(sorted(bindings.items())),
            revision=1,
            predecessor_sha256="d" * 64,
            observation=normalize_vm_ha_observation(observation),
        ),
    )
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(state)
    journal = VMHALifecycleJournal(store, state)
    vm_mgr.set_vm_ha_lifecycle_journal(journal)
    vm_mgr._vm_ha_effect_spec = _ha_spec()
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
        patch.object(
            vm_mgr,
            "observe_vm_ha_migration_state",
            return_value=deepcopy(observation),
        ),
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(vm_mgr, "set_ha_private_alias", setter),
    ):
        vm_mgr._attach_vm_ha_shared_allocation_initially(
            allocation_id="shared-private",
            active_compute_id="compute-a",
            active_network_interface_name="eth0",
        )

    setter.assert_not_called()
    assert journal.state.transaction is not None
    assert "attach-shared-allocation-active" in journal.state.transaction.completed_effects
    assert (
        dict(journal.state.transaction.resource_bindings)["shared-allocation-owner-compute"]
        == "compute-b"
    )

    vm_mgr._vm_ha_shared_allocation_id = "shared-private"
    vm_mgr._vm_ha_route_targets = _route_targets()

    def instance(name: str) -> SimpleNamespace:
        suffix = "a" if name.endswith("-0") else "b"
        aliases = [SimpleNamespace(allocation_id="shared-private")] if suffix == "b" else []
        return SimpleNamespace(
            id=f"compute-{suffix}",
            spec=SimpleNamespace(
                network_interfaces=[SimpleNamespace(name="eth0", aliases=aliases)]
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
            vm_mgr,
            "_get_ha_instance_by_name",
            side_effect=lambda _, name: instance(name),
        ),
    ):
        binding = vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())

    assert [node.compute_id for node in binding.nodes] == ["compute-a", "compute-b"]

    journal.state = replace(journal.state, status=VMHALifecycleStatus.ACTIVATING)
    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr,
            "_get_ha_instance_by_name",
            side_effect=lambda _, name: instance(name),
        ),
    ):
        resumed_binding = vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())

    assert [node.compute_id for node in resumed_binding.nodes] == ["compute-a", "compute-b"]

    journal.state = replace(journal.state, status=VMHALifecycleStatus.ACTIVE)
    with (
        patch.object(vm_mgr, "get_ha_allocation", return_value=allocation),
        patch.object(
            vm_mgr,
            "_get_ha_instance_by_name",
            side_effect=lambda _, name: instance(name),
        ),
        pytest.raises(RuntimeError, match="not exact on configured active"),
    ):
        vm_mgr._build_vm_ha_runtime_binding(object(), _ha_spec())


def test_vm_ha_instance_allocations_use_independent_primary_ids() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
        patch.object(vm_mgr, "get_allocation_ip", return_value=None) as get_allocation_ip,
        patch.object(
            vm_mgr,
            "_ensure_public_allocation",
            return_value=("public", SimpleNamespace(id="public-allocation")),
        ),
        patch.object(
            vm_mgr,
            "_ensure_private_allocation",
            side_effect=[
                ("gateway-0-eth0-private-ip", SimpleNamespace(id="private-0")),
                ("gateway-1-eth0-private-ip", SimpleNamespace(id="private-1")),
            ],
        ) as per_node_private,
    ):
        vm_mgr._ensure_instance_allocations(
            object(), object(), _ha_spec(), "gateway-0", 0, provisioning, [], {}
        )
        vm_mgr._ensure_instance_allocations(
            object(), object(), _ha_spec(), "gateway-1", 1, provisioning, [], {}
        )

    assert vm_mgr._private_alloc_ids == {
        "gateway-0": ["private-0"],
        "gateway-1": ["private-1"],
    }
    assert per_node_private.call_count == 2
    assert get_allocation_ip.call_count == 2


def test_vm_ha_ensure_group_returns_binding_only_after_attachment_and_reread(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
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
    binding = SimpleNamespace(
        route_runtime_id="route-runtime",
        nebius_service_account_id="service-account-a",
        nebius_authorized_key_id="authorized-key-a",
        nodes=(
            SimpleNamespace(node_id="node-a", nebius_credentials_sha256="d" * 64),
            SimpleNamespace(node_id="node-b", nebius_credentials_sha256="d" * 64),
        ),
    )

    with (
        patch.object(vm_mgr, "_verify_vm_ha_transaction_preconditions"),
        patch.object(vm_mgr, "_begin_vm_ha_effect"),
        patch.object(vm_mgr, "_complete_vm_ha_effect"),
        patch.object(vm_mgr, "_build_sdk_client", return_value=object()),
        patch.object(vm_mgr, "_resolve_client_apis", return_value=(None, None, None, object())),
        patch.object(vm_mgr, "_discover_vm_ha_members", return_value={}),
        patch.object(vm_mgr, "verify_vm_ha_existing_identities"),
        patch.object(
            vm_mgr,
            "_prepare_gateway_ssh_enrollment_cloud_inits",
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


def test_vm_ha_attachment_failure_emits_no_runtime_binding(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)
    active = SimpleNamespace(
        id="compute-a",
        spec=SimpleNamespace(network_interfaces=[SimpleNamespace(name="eth0")]),
    )

    with (
        patch.object(vm_mgr, "_verify_vm_ha_transaction_preconditions"),
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


def test_explicit_vm_ha_rejects_scaffold_fallback(tmp_path) -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    _install_vm_ha_journal(vm_mgr, tmp_path)

    with (
        patch.object(vm_mgr, "_verify_vm_ha_transaction_preconditions"),
        patch.object(vm_mgr, "_build_sdk_client", return_value=None),
        pytest.raises(RuntimeError, match="failed closed"),
    ):
        vm_mgr.ensure_group(_ha_spec())


def test_omitted_vm_ha_preserves_scaffold_return_shape() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    alias_allocation_ids: tuple[str, ...] = (),
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
                    aliases=[
                        SimpleNamespace(allocation_id=alias_id) for alias_id in alias_allocation_ids
                    ],
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
                "11",
                "disk-0",
                "subnet-1",
                "primary-0",
                "public-0",
                ("shared-private",),
            ),
            VMHALifecycleMember(
                1,
                "gateway-1",
                "node-passive",
                "passive",
                "compute-1",
                "eth0",
                "203.0.113.11",
                "12",
                "disk-1",
                "subnet-1",
                "primary-1",
                "public-1",
            ),
        ),
        route_runtime_id="route-runtime",
        route_targets=("route-table-1:10.0.0.0/8",),
        transaction=VMHAMigrationTransaction(
            operation_id="fixture-operation",
            approval_kind="migration",
            approval_digest="a" * 64,
            desired_state_digest="b" * 64,
            current_state_digest="c" * 64,
            checkpoint="fixture",
            pending_effect=None,
            completed_effects=(),
            resource_bindings=(("shared-allocation-id", "shared-private"),),
            revision=1,
            predecessor_sha256=None,
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
            alias_allocation_ids=("shared-private",) if index == 0 else (),
        )
        for index in range(2)
    }
    allocation = _former_allocation()
    return ordinary_spec, members, allocation


def test_vm_ha_enrollment_cloud_init_persists_exact_member_identity() -> None:
    spec = _ha_spec()
    identity = SimpleNamespace(cloud_init_entries=lambda: "  - path: /etc/ssh/vpngw_host_key\n")
    base = "#cloud-config\nwrite_files:\n  - content: |\n            Port 22\n"
    marker = render_provisioning_marker(spec, 0)

    rendered = VMManager._render_gateway_ssh_enrollment_cloud_init(base, identity, marker)
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


def test_ordinary_enrollment_cloud_init_persists_the_prepinned_identity() -> None:
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    identity = SimpleNamespace(cloud_init_entries=lambda: "  - path: /etc/ssh/vpngw_host_key\n")
    policy = SimpleNamespace(identity_for=lambda hostname: identity)
    base = "#cloud-config\nwrite_files:\n  - content: |\n            Port 22\n"
    manager = VMManager(
        project_id="project-test",
        region="eu-west1",
        ssh_policy=policy,
    )

    with patch.object(manager, "_build_cloud_init", return_value=base):
        rendered = manager._prepare_gateway_ssh_enrollment_cloud_inits(
            spec,
            ["10.0.0.0/8"],
            existing_names=set(),
            recreate=False,
        )["gateway-0"]

    assert "  - path: /etc/ssh/vpngw_host_key\n" in rendered
    assert "            HostKey /etc/ssh/vpngw_host_key\n" in rendered
    assert "nebius-vpngw-vm-ha-provisioning" not in rendered


def test_product_cloud_init_recovers_exact_legacy_host_key_entry() -> None:
    private_key = b"legacy-private-host-key\n"
    encoded = base64.b64encode(private_key).decode("ascii")
    cloud_init = (
        "#cloud-config\n"
        "write_files:\n"
        f"  - path: {LEGACY_VM_HA_SSH_HOST_KEY_PATH}\n"
        '    permissions: "0600"\n'
        "    owner: root:root\n"
        "    encoding: b64\n"
        f"    content: {encoded}\n"
        "runcmd:\n"
        f"  - echo 'HostKey {LEGACY_VM_HA_SSH_HOST_KEY_PATH}'\n"
    )
    instance = SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data=cloud_init))

    assert recover_product_host_key(instance, path=LEGACY_VM_HA_SSH_HOST_KEY_PATH) == private_key


def test_product_cloud_init_recovery_rejects_duplicate_yaml_keys() -> None:
    path = LEGACY_VM_HA_SSH_HOST_KEY_PATH
    cloud_init = (
        "#cloud-config\n"
        "write_files: []\n"
        "write_files:\n"
        f"  - path: {path}\n"
        '    permissions: "0600"\n'
        "    owner: root:root\n"
        "    encoding: b64\n"
        "    content: Zml4dHVyZQ==\n"
        "runcmd:\n"
        f"  - echo 'HostKey {path}'\n"
    )
    instance = SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data=cloud_init))

    with pytest.raises(RuntimeError, match="duplicate key"):
        recover_product_host_key(instance, path=path)


def test_current_product_marker_binds_exact_compute_member_for_ssh_recovery() -> None:
    spec = _ha_spec()
    marker = render_provisioning_marker(spec, 0)
    cloud_init = f"#cloud-config\n# nebius-vpngw-vm-ha-provisioning-v1: {marker}\n"
    instance = SimpleNamespace(
        id="compute-0",
        metadata=SimpleNamespace(
            id="compute-0",
            name="gateway-0",
            parent_id="project-1",
            resource_version="7",
        ),
        spec=SimpleNamespace(cloud_init_user_data=cloud_init),
        status=SimpleNamespace(
            network_interfaces=[
                SimpleNamespace(public_ip_address=SimpleNamespace(address="203.0.113.10/32"))
            ]
        ),
    )

    provenance = VMManager(
        project_id="project-1",
        region="eu-north1",
    )._validate_vm_ha_ssh_member_binding(
        name="gateway-0",
        vm_obj=instance,
        public_ip="203.0.113.10",
        spec=spec,
        lifecycle_snapshot=None,
    )

    assert provenance is FormerVMHAProvenance.CURRENT_MARKER


def test_vm_ha_ssh_compute_reread_uses_bounded_request_policy() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    response = object()
    operation = SimpleNamespace(wait=MagicMock(return_value=response))
    service = SimpleNamespace(get=MagicMock(return_value=operation))
    request_kwargs = {"timeout": 17.0}

    with (
        patch(
            "nebius.api.nebius.compute.v1.InstanceServiceClient",
            return_value=service,
        ),
        patch(
            "nebius_vpngw.deploy.vm_manager.vm_ha_request_kwargs",
            return_value=request_kwargs,
        ),
    ):
        result = vm_mgr._get_vm_by_id_for_vm_ha_preflight(object(), "compute-0")

    assert result is response
    assert service.get.call_args.kwargs == request_kwargs


def test_retained_ordinary_members_are_adopted_from_exact_lifecycle_and_runtime_proof() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
            alias_allocation_ids=("shared-private",) if index == 0 else (),
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


def test_retained_lifecycle_rejects_shared_allocation_as_primary_and_alias() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    spec = _ha_spec()
    spec.vm_ha = None
    spec.instance_count = 1
    lifecycle = _retained_lifecycle_state()
    service = _FormerAllocationService([], current=_former_allocation())
    members = {
        "gateway-0": _former_member(
            index=0,
            marker=None,
            allocation_id="shared-private",
            alias_allocation_ids=("shared-private",),
        ),
        "gateway-1": _former_member(index=1, marker=None),
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
        vm_mgr.discover_former_vm_ha_candidate_members(spec, lifecycle_state=lifecycle)
        with pytest.raises(RuntimeError, match="both primary and alias"):
            vm_mgr.discover_former_vm_ha_members(
                spec,
                legacy_identities=identities,
                lifecycle_state=lifecycle,
            )


def test_retained_lifecycle_compute_identity_mismatch_fails_closed() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
            alias_allocation_ids=("shared-private",) if index == 0 else (),
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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

    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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


def test_former_vm_ha_discovery_reads_later_allocation_pages() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
    spec, members, allocation = _former_identity_fixture()

    class PagedService(_FormerAllocationService):
        def list(self, request):
            self.list_requests.append(request)
            if not request.page_token:
                return _Waitable(SimpleNamespace(items=[], next_page_token="allocations-next"))
            return _Waitable(SimpleNamespace(items=[allocation], next_page_token=""))

    service = PagedService([allocation])

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

    assert sorted(result) == ["gateway-0", "gateway-1"]
    assert [request.page_token for request in service.list_requests] == [
        "",
        "allocations-next",
    ]


def test_former_vm_ha_identity_drift_blocks_teardown_after_classification() -> None:
    vm_mgr = VMManager(project_id="project-1", region="eu-north1")
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
