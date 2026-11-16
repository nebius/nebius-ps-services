from types import SimpleNamespace

import pytest
from grpc import StatusCode
from nebius.api.nebius.compute import v1 as compute

from nebius_vpngw.deploy.repair_cloud import CloudJournal
from nebius_vpngw.deploy.repair_cloud_retry import RepairCloud
from nebius_vpngw.deploy.replacement_cloud import CloudOperationRejected


def owner(tmp_path):
    journal = CloudJournal(
        tmp_path / "repair",
        operation="a" * 32,
        intent={"project": "project-fixture", "targets": [{"compute": "compute-retained"}]},
    )
    return RepairCloud(object(), journal, member_index=0, operation_scope=journal.operation)


def operation(identifier, *, success):
    return SimpleNamespace(
        id=identifier,
        resource_id="compute-retained",
        sync_wait=lambda **kwargs: None,
        successful=lambda: success,
        done=lambda: True,
        status=lambda: SimpleNamespace(code=StatusCode.OK if success else StatusCode.ABORTED),
    )


def test_confirmed_repair_retries_terminal_failure_with_new_key_and_preserves_evidence(tmp_path):
    cloud = owner(tmp_path)
    keys = []
    request = compute.StopInstanceRequest(id="compute-retained")

    def submit(_request, **kwargs):
        keys.append(kwargs["metadata"])
        return SimpleNamespace(
            wait=lambda: operation("operation-" + str(len(keys)), success=len(keys) > 1)
        )

    with pytest.raises(CloudOperationRejected):
        cloud.mutate("stop", request, submit)
    failed = next(iter(cloud.records.values())).copy()
    assert cloud.mutate("stop", request, submit) == "compute-retained"
    assert keys[0] != keys[1]
    assert len(cloud.records) == 2
    assert next(iter(cloud.records.values())) == failed
    assert failed["status"] == "failed" and failed["cloud_operation_id"] == "operation-1"
    assert cloud.completed("stop") == "compute-retained"
    assert cloud.mutate("stop", request, submit) == "compute-retained"
    assert len(keys) == 2


def test_settlement_accepts_newly_observed_terminal_failure_without_faking_success(
    tmp_path, monkeypatch
):
    cloud = owner(tmp_path)
    request = compute.StopInstanceRequest(id="compute-retained")
    accepted = operation("operation-accepted", success=False)
    monkeypatch.setattr(
        accepted, "sync_wait", lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(TimeoutError):
        cloud.mutate("stop", request, lambda *_a, **_k: SimpleNamespace(wait=lambda: accepted))
    assert next(iter(cloud.records.values()))["status"] == "accepted"
    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: SimpleNamespace(
            operation_service=lambda: SimpleNamespace(
                get=lambda *_a, **_k: SimpleNamespace(
                    wait=lambda: operation("operation-accepted", success=False)
                )
            )
        ),
    )
    cloud.settle()
    assert next(iter(cloud.records.values()))["status"] == "failed"
    assert cloud.completed("stop") is None


def test_uncertain_receipt_does_not_gain_new_retry_identity(tmp_path, monkeypatch):
    cloud = owner(tmp_path)
    keys = []

    def unavailable(_request, **kwargs):
        keys.append(kwargs["metadata"])
        raise TimeoutError("provider response unavailable")

    request = compute.StopInstanceRequest(id="compute-retained")
    with pytest.raises(TimeoutError):
        cloud.mutate("stop", request, unavailable)
    monkeypatch.setattr(
        compute, "InstanceServiceClient", lambda _: SimpleNamespace(stop=unavailable)
    )
    with pytest.raises(TimeoutError):
        cloud.mutate("stop", request, unavailable)
    assert keys[0] == keys[1]
    assert len(cloud.records) == 1
    assert next(iter(cloud.records.values()))["status"] == "intent"
