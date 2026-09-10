from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError

from nebius_vpngw.config_loader import GatewayGroupSpec
from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.fixture(params=[False, True], ids=["ordinary", "ha"])
def disk_inspection(request):
    spec = GatewayGroupSpec(
        name="gateway",
        instance_count=1,
        region="eu-north1",
        external_ips=[],
        vm_spec={},
    )
    if request.param:
        spec.vm_ha = SimpleNamespace()  # type: ignore[assignment]
    manager = VMManager(project_id="project-1", region=spec.region)
    manager._get_client = Mock(return_value=object())
    vm = SimpleNamespace(
        spec=SimpleNamespace(
            boot_disk=SimpleNamespace(existing_disk=SimpleNamespace(id="disk-attached"))
        )
    )
    manager._get_vm_by_name = Mock(return_value=vm)
    disk = SimpleNamespace(
        metadata=SimpleNamespace(
            id="disk-attached", parent_id="project-1", name="renamed-attached-disk"
        ),
        spec=SimpleNamespace(),
    )
    service = Mock()
    service.get.return_value.wait.return_value = disk
    service.get_by_name.return_value.wait.side_effect = RequestError(
        SimpleNamespace(code=StatusCode.NOT_FOUND)  # type: ignore[arg-type]
    )
    with patch("nebius.api.nebius.compute.v1.DiskServiceClient", return_value=service):
        yield manager, spec, vm, disk, service
    manager.close()


def test_existing_gateway_compares_attached_disk_despite_rename(disk_inspection):
    manager, spec, _vm, disk, service = disk_inspection
    diff = manager.check_changes(spec)[0][1]
    assert not diff.has_changes()
    assert service.get.call_args.args[0].id == disk.metadata.id
    service.get_by_name.assert_not_called()
    service.create.assert_not_called()


def test_missing_compute_remains_safe_creation(disk_inspection):
    manager, spec, _vm, _disk, service = disk_inspection
    manager._get_vm_by_name.return_value = None
    diff = manager.check_changes(spec)[0][1]
    assert diff.differences == ["VM does not exist (will create)"]
    assert not diff.requires_recreation()
    service.get.assert_not_called()
    service.get_by_name.assert_not_called()


def test_existing_gateway_without_boot_attachment_blocks_planning(disk_inspection):
    manager, spec, vm, _disk, service = disk_inspection
    vm.spec.boot_disk.existing_disk.id = ""
    with pytest.raises(RuntimeError, match="boot disk attachment"):
        manager.check_changes(spec)
    service.get.assert_not_called()
    service.get_by_name.assert_not_called()


def test_existing_gateway_with_missing_attached_disk_blocks_planning(disk_inspection):
    manager, spec, _vm, _disk, service = disk_inspection
    service.get.return_value.wait.side_effect = RequestError(
        SimpleNamespace(code=StatusCode.NOT_FOUND)  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="attached boot disk is unavailable"):
        manager.check_changes(spec)
    service.create.assert_not_called()


@pytest.mark.parametrize("field", ["id", "parent_id"])
def test_existing_gateway_rejects_inexact_attached_disk(disk_inspection, field):
    manager, spec, _vm, disk, service = disk_inspection
    setattr(disk.metadata, field, "foreign-identity")
    with pytest.raises(RuntimeError, match="inexact identity"):
        manager.check_changes(spec)
    service.create.assert_not_called()


@pytest.mark.parametrize("code", [StatusCode.PERMISSION_DENIED, StatusCode.UNAVAILABLE])
def test_attached_disk_read_failure_cannot_become_creation(disk_inspection, code):
    manager, spec, _vm, _disk, service = disk_inspection
    error = RequestError(SimpleNamespace(code=code))  # type: ignore[arg-type]
    service.get.return_value.wait.side_effect = error
    with pytest.raises(RuntimeError, match="could not be classified") as raised:
        manager.check_changes(spec)
    assert raised.value.__cause__ is error
    service.create.assert_not_called()
