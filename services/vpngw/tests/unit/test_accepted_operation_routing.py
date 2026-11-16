"""Accepted receipts must reach their originating service without another mutation."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nebius.api.nebius.common import v1 as common
from nebius.api.nebius.compute import v1 as compute
from nebius.api.nebius.iam import v1 as iam
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy.replacement_cloud import Cloud, effect_key, message_digest
from nebius_vpngw.deploy.route_manager import NebiusSDKRouteBackend
from nebius_vpngw.deploy.vm_ha_cloud import (
    AcceptedCloudOperation,
    AmbiguousHACloudError,
    NebiusSDKCloudClient,
    VMHACloudOperationJournal,
)

ROUTES = [
    (compute, "StopInstanceRequest", "InstanceServiceClient"),
    (compute, "UpdateInstanceRequest", "InstanceServiceClient"),
    (compute, "DeleteInstanceRequest", "InstanceServiceClient"),
    (compute, "CreateDiskRequest", "DiskServiceClient"),
    (compute, "DeleteDiskRequest", "DiskServiceClient"),
    (vpc, "CreateAllocationRequest", "AllocationServiceClient"),
    (vpc, "DeleteAllocationRequest", "AllocationServiceClient"),
    (vpc, "UpdateRouteRequest", "RouteServiceClient"),
    (vpc, "CreateRouteRequest", "RouteServiceClient"),
    (vpc, "DeleteRouteRequest", "RouteServiceClient"),
    (vpc, "CreateRouteTableRequest", "RouteTableServiceClient"),
    (vpc, "UpdateSubnetRequest", "SubnetServiceClient"),
    (vpc, "CreateSubnetRequest", "SubnetServiceClient"),
    (vpc, "UpdatePoolRequest", "PoolServiceClient"),
    (iam, "CreateServiceAccountRequest", "ServiceAccountServiceClient"),
    (iam, "CreateGroupRequest", "GroupServiceClient"),
    (iam, "CreateGroupMembershipRequest", "GroupMembershipServiceClient"),
    (iam, "CreateAccessPermitRequest", "AccessPermitServiceClient"),
    (iam, "CreateAuthPublicKeyRequest", "AuthPublicKeyServiceClient"),
]


def accepted_journal(request_type, *, label="effect"):
    record = {
        "label": label,
        "status": "accepted",
        "cloud_operation_id": "opaque-operation",
        "resource_id": None,
        "request_sha256": "b" * 64,
        "submission_count": 1,
        "single_attempt": True,
    }
    if request_type is not None:
        record.update(request_type=request_type, request_metadata={})
    key = effect_key("a" * 64, 0, label)
    journal = SimpleNamespace(
        state={"transaction": {"member": 0, "approval": "a" * 64, "operations": {key: record}}},
        save=Mock(),
    )
    return journal, record


def bound_service(monkeypatch, module, name, *, identifier="opaque-operation"):
    operation = SimpleNamespace(
        id=identifier, resource_id="exact-resource", sync_wait=Mock(), successful=lambda: True
    )
    service = Mock()
    service.operation_service.return_value.get.return_value.wait.return_value = operation
    factory = Mock(return_value=service)
    monkeypatch.setattr(module, name, factory)
    generic = Mock(side_effect=AssertionError("unbound operation lookup"))
    monkeypatch.setattr(common, "OperationServiceClient", generic)
    return service, factory, generic


@pytest.mark.parametrize("module,request_type,service_name", ROUTES)
def test_accepted_receipt_routes_to_origin_without_resubmission(
    monkeypatch, module, request_type, service_name
):
    service, factory, generic = bound_service(monkeypatch, module, service_name)
    journal, record = accepted_journal(request_type)
    sdk = object()
    Cloud(sdk, journal).settle()
    factory.assert_called_once_with(sdk)
    generic.assert_not_called()
    assert [call[0] for call in service.mock_calls] == [
        "operation_service",
        "operation_service().get",
        "operation_service().get().wait",
    ]
    assert service.operation_service.return_value.get.call_args.args[0].id == "opaque-operation"
    assert record["status"] == "settled" and record["resource_id"] == "exact-resource"
    assert record["submission_count"] == 1


@pytest.mark.parametrize("owner", ["provisioning", "replacement"])
def test_secret_free_compute_creation_receipt_uses_its_owner(monkeypatch, owner):
    service, _, generic = bound_service(monkeypatch, compute, "InstanceServiceClient")
    label = "compute:gateway-0" if owner == "provisioning" else "create-candidate"
    journal, record = accepted_journal(None, label=label)
    request = compute.CreateInstanceRequest()
    request.spec.cloud_init_user_data = "private-bootstrap-must-stay-out-of-receipt"
    record["request_sha256"] = message_digest(request)
    restore = Mock(return_value=request) if owner == "provisioning" else None
    Cloud(object(), journal, restore_request=restore).settle()
    generic.assert_not_called()
    service.operation_service.assert_called_once_with()
    assert record["status"] == "settled" and record["submission_count"] == 1
    assert "request_metadata" not in record and "request_type" not in record
    assert request.spec.cloud_init_user_data not in str(journal.state)
    if restore is not None:
        restore.assert_called_once_with(record)


@pytest.mark.parametrize("request_type", [None, "UnknownRequest"])
def test_missing_or_unknown_operation_origin_grants_no_lookup(monkeypatch, request_type):
    _, factory, generic = bound_service(monkeypatch, compute, "InstanceServiceClient")
    journal, _ = accepted_journal(request_type)
    before = deepcopy(journal.state)
    with pytest.raises(RuntimeError, match="operation service binding"):
        Cloud(object(), journal).settle()
    assert journal.state == before
    factory.assert_not_called()
    generic.assert_not_called()
    journal.save.assert_not_called()


def test_changed_secret_free_creation_request_cannot_select_operation_service(monkeypatch):
    _, factory, generic = bound_service(monkeypatch, compute, "InstanceServiceClient")
    journal, _ = accepted_journal(None)
    before = deepcopy(journal.state)
    with pytest.raises(RuntimeError, match="request reconstruction changed"):
        Cloud(object(), journal, restore_request=lambda _: compute.CreateInstanceRequest()).settle()
    assert journal.state == before
    factory.assert_not_called()
    generic.assert_not_called()


def test_replacement_creation_label_without_exact_member_key_grants_no_lookup(monkeypatch):
    _, factory, generic = bound_service(monkeypatch, compute, "InstanceServiceClient")
    journal, _ = accepted_journal(None, label="create-candidate")
    before = deepcopy(journal.state)
    with pytest.raises(RuntimeError, match="operation service binding"):
        Cloud(object(), journal, member_index=1).settle()
    assert journal.state == before
    factory.assert_not_called()
    generic.assert_not_called()


def test_unavailable_accepted_lookup_preserves_receipt_and_submission_count(monkeypatch):
    service, _, generic = bound_service(monkeypatch, compute, "DiskServiceClient")
    service.operation_service.return_value.get.return_value.wait.side_effect = TimeoutError()
    journal, _ = accepted_journal("CreateDiskRequest")
    before = deepcopy(journal.state)
    with pytest.raises(TimeoutError):
        Cloud(object(), journal).settle()
    assert journal.state == before
    journal.save.assert_not_called()
    generic.assert_not_called()
    service.create.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["stop-instance", "start-instance", "set-alias-present", "set-alias-absent"]
)
def test_ha_accepted_compute_operation_uses_bound_read_only_lookup(tmp_path, monkeypatch, kind):
    service, _, generic = bound_service(monkeypatch, compute, "InstanceServiceClient")
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = AcceptedCloudOperation("action-original", kind, "opaque-operation")
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    submit = Mock(side_effect=AssertionError("accepted mutation was resubmitted"))
    client._mutate(action_operation_id=accepted.action_operation_id, kind=kind, submit=submit)
    assert journal.load() is None
    generic.assert_not_called()
    submit.assert_not_called()
    service.operation_service.assert_called_once_with()


def test_ha_wrong_operation_response_retains_receipt(tmp_path, monkeypatch):
    bound_service(monkeypatch, compute, "InstanceServiceClient", identifier="foreign-operation")
    journal = VMHACloudOperationJournal(tmp_path / "accepted.json")
    accepted = AcceptedCloudOperation("action-original", "stop-instance", "opaque-operation")
    journal.save(accepted)
    client = NebiusSDKCloudClient(object(), operation_journal=journal)
    with pytest.raises(AmbiguousHACloudError, match="operation identity"):
        client.finalize_accepted_operation(accepted)
    assert journal.load() == accepted


@pytest.mark.parametrize("identifier", ["opaque-operation", "foreign-operation"])
def test_ha_route_receipt_uses_origin_and_exact_operation_identity(monkeypatch, identifier):
    service, _, generic = bound_service(
        monkeypatch, vpc, "RouteServiceClient", identifier=identifier
    )
    backend = NebiusSDKRouteBackend(object())
    if identifier == "foreign-operation":
        with pytest.raises(RuntimeError, match="operation identity"):
            backend._resume_operation("opaque-operation")
    else:
        assert backend._resume_operation("opaque-operation").id == identifier
    generic.assert_not_called()
    service.operation_service.assert_called_once_with()
    service.create.assert_not_called()
    service.delete.assert_not_called()
