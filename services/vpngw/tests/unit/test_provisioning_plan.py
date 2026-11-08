import base64
from copy import deepcopy

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import (
    AttachedDiskSpec,
    CreateDiskRequest,
    CreateInstanceRequest,
    DiskSpec,
    ExistingDisk,
    InstanceSpec,
)

from nebius_vpngw.deploy.provisioning_plan import (
    Reference,
    Request,
    creation_receipt,
    validate_requests,
)
from nebius_vpngw.deploy.replacement_cloud import message_digest


def disk():
    return Request.freeze(
        "disk",
        CreateDiskRequest(
            metadata=ResourceMetadata(name="gateway-0-boot", parent_id="project-a"),
            spec=DiskSpec(source_image_id="frozen-image"),
        ),
    )


def compute():
    return Request.freeze(
        "compute",
        CreateInstanceRequest(
            metadata=ResourceMetadata(name="gateway-0", parent_id="project-a"),
            spec=InstanceSpec(boot_disk=AttachedDiskSpec(existing_disk=ExistingDisk(id="@disk"))),
        ),
        references=(Reference("spec.boot_disk.existing_disk.id", "disk", "disk"),),
        guest="gateway-0",
    )


def test_exact_successful_receipt_is_only_source_of_created_identity():
    requests = (disk(), compute())
    validate_requests(requests)
    resolved_disk = requests[0].resolve({})
    operation = {
        "label": "disk",
        "status": "settled",
        "resource_id": "created-disk",
        "cloud_operation_id": "create-operation",
        "request_sha256": message_digest(resolved_disk),
    }
    binding = creation_receipt(requests[0], operation, resolved_disk)
    resolved_compute = requests[1].resolve({"disk": binding}, bootstrap="#cloud-config\n")
    assert resolved_compute.spec.boot_disk.existing_disk.id == "created-disk"
    assert resolved_compute.spec.cloud_init_user_data == "#cloud-config\n"
    assert requests[1].template().spec.boot_disk.existing_disk.id == "@disk"
    assert not requests[1].template().spec.cloud_init_user_data
    for request in requests:
        assert Request.parse(request.mapping()) == request


@pytest.mark.parametrize(
    "change",
    [
        {"status": "intent"},
        {"status": "accepted"},
        {"status": "failed"},
        {"cloud_operation_id": None},
        {"resource_id": None},
        {"label": "different-request"},
        {"request_sha256": "f" * 64},
    ],
)
def test_matching_resource_cannot_replace_successful_exact_operation(change):
    request = disk()
    resolved = request.resolve({})
    operation = {
        "label": "disk",
        "status": "settled",
        "resource_id": "same-name-resource",
        "cloud_operation_id": "operation-a",
        "request_sha256": message_digest(resolved),
        **change,
    }
    with pytest.raises(RuntimeError, match="exact successful operation"):
        creation_receipt(request, operation, resolved)


def test_plan_cannot_persist_compute_user_data():
    request = compute().template()
    request.spec.cloud_init_user_data = "private-bootstrap-material"
    with pytest.raises(ValueError, match="without retaining user-data"):
        Request.freeze("compute", request, guest="gateway-0")


@pytest.mark.parametrize(
    "field,kind",
    [("metadata.parent_id", "disk"), ("spec.boot_disk.existing_disk.id", "allocation")],
)
def test_reference_cannot_change_unapproved_field_or_resource_type(field, kind):
    value = deepcopy(compute().mapping())
    value["references"] = [{"field": field, "slot": "disk", "kind": kind}]
    with pytest.raises(ValueError, match="typed provisioning reference"):
        Request.parse(value)


def test_missing_and_forward_creation_references_are_rejected():
    with pytest.raises(ValueError, match="earlier typed creation"):
        validate_requests((compute(), disk()))
    with pytest.raises(RuntimeError, match="successful operation receipt"):
        compute().resolve({}, bootstrap="#cloud-config\n")


def test_plan_round_trip_preserves_newer_api_fields():
    request = disk().template()
    request.metadata.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    request.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x02")
    planned = Request.freeze("disk", request)
    assert message_digest(Request.parse(planned.mapping()).resolve({})) == message_digest(request)


@pytest.mark.parametrize(
    "change",
    [
        {"protobuf": base64.b64encode(b"\xff").decode()},
        {"protobuf": 12},
        {"type": []},
        {"references": None},
        {"references": [{"field": [], "slot": "disk", "kind": "disk"}]},
    ],
)
def test_malformed_plan_is_rejected_before_sdk_or_cloud_use(change):
    with pytest.raises(ValueError, match="Invalid|Unknown"):
        Request.parse({**compute().mapping(), **change})


@pytest.mark.parametrize(
    "field,value", [("resource_id", []), ("cloud_operation_id", 1), ("request_sha256", "short")]
)
def test_reference_rejects_malformed_success_receipt(field, value):
    binding = {
        "kind": "disk",
        "resource_id": "created-disk",
        "cloud_operation_id": "operation-a",
        "request_sha256": "a" * 64,
        field: value,
    }
    with pytest.raises(RuntimeError, match="exact successful operation receipt"):
        compute().resolve({"disk": binding}, bootstrap="#cloud-config\n")
