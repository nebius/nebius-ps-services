import json
from types import SimpleNamespace

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from test_replacement import plan

from nebius_vpngw.deploy.replacement import Journal, digest
from nebius_vpngw.deploy.replacement_cloud import Cloud, CloudOperationRejected, message_digest


@pytest.mark.parametrize("kind", ["stop", "update", "update-opaque"])
def test_lost_acceptance_receipt_replays_exact_request_before_observing_state(
    tmp_path, monkeypatch, kind
):
    desired = plan()
    captured = []
    private_user_data = "fixture-private-host-key-never-written-to-journal"
    original = compute.Instance(
        metadata=ResourceMetadata(id="compute-old", parent_id=desired.project_id),
        spec=compute.InstanceSpec(cloud_init_user_data=private_user_data),
    )
    if kind == "update-opaque":
        original.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    request = (
        compute.StopInstanceRequest(id="compute-old")
        if kind == "stop"
        else compute.UpdateInstanceRequest(
            metadata=ResourceMetadata(
                id="compute-old",
                parent_id=desired.project_id,
                name="gateway-test-0",
                resource_version=7,
            ),
            spec=original.spec,
        )
    )

    def interrupted(req, **kwargs):
        captured.append((message_digest(req), kwargs["metadata"]))
        raise TimeoutError("provider accepted, response lost")

    operation = SimpleNamespace(
        id="operation-accepted",
        resource_id="compute-old",
        sync_wait=lambda **kw: None,
        successful=lambda: True,
    )

    def replay(req, **kwargs):
        captured.append((message_digest(req), kwargs["metadata"]))
        return SimpleNamespace(wait=lambda: operation)

    monkeypatch.setattr(
        compute, "InstanceServiceClient", lambda _: SimpleNamespace(stop=replay, update=replay)
    )
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))
        cloud = Cloud(object(), journal)
        with pytest.raises(TimeoutError):
            cloud.mutate(kind, request, interrupted)
        state = journal.bundle.read_text()
        assert private_user_data not in state
        assert "cloud_init_user_data" not in state
        assert (
            next(iter(json.loads(state)["transaction"]["operations"].values()))["status"]
            == "intent"
        )
        predecessor = tmp_path / "uncertain-receipt.json"
        predecessor.write_text(state)
        predecessor.chmod(0o600)
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        cloud = Cloud(object(), journal)
        monkeypatch.setattr(cloud, "get_instance", lambda _: original)
        cloud.settle()
        assert cloud.completed(kind) == "compute-old"
        completed = journal.path.read_bytes()
        journal.import_bundle(predecessor)
        assert journal.path.read_bytes() == completed
    assert len(captured) == 2
    assert captured[0] == captured[1]


def test_recovery_does_not_replay_update_with_changed_private_user_data(tmp_path, monkeypatch):
    desired = plan()
    request = compute.UpdateInstanceRequest(
        metadata=ResourceMetadata(id="compute-old", resource_version=7),
        spec=compute.InstanceSpec(cloud_init_user_data="fixture-original"),
    )
    submitted = []
    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: SimpleNamespace(update=lambda *args, **kw: submitted.append(args)),
    )
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))
        cloud = Cloud(object(), journal)
        with pytest.raises(TimeoutError):
            cloud.mutate(
                "update", request, lambda *args, **kw: (_ for _ in ()).throw(TimeoutError())
            )
        monkeypatch.setattr(
            cloud,
            "get_instance",
            lambda _: compute.Instance(
                metadata=ResourceMetadata(id="compute-old", resource_version=7),
                spec=compute.InstanceSpec(cloud_init_user_data="fixture-drift"),
            ),
        )
        with pytest.raises(RuntimeError, match="reconstruction changed"):
            cloud.settle()
    assert submitted == []


def request_error(code):
    from nebius.aio.service_error import RequestError, RequestStatusExtended

    return RequestError(RequestStatusExtended(code, "private provider detail", [], "", "", []))


@pytest.mark.parametrize("code_name", ["ABORTED", "FAILED_PRECONDITION", "INVALID_ARGUMENT"])
def test_definitive_request_rejection_is_not_replayed_forever(tmp_path, code_name):
    from grpc import StatusCode

    desired = plan()
    request = compute.UpdateInstanceRequest(
        metadata=ResourceMetadata(id="compute-old", resource_version=7),
        spec=compute.InstanceSpec(),
    )
    submitted = []

    def reject(*args, **kwargs):
        assert kwargs["retries"] == 0
        submitted.append(args)
        raise request_error(getattr(StatusCode, code_name))

    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))
        with pytest.raises(CloudOperationRejected, match="request was rejected"):
            Cloud(object(), journal).mutate("update", request, reject)
        record = next(iter(journal.state["transaction"]["operations"].values()))
        assert record["status"] == "rejected"
        assert record["failure"] == {"kind": "request", "code": code_name}
        assert record["submission_count"] == 1
        assert record["single_attempt"] is True
        assert "private provider detail" not in journal.path.read_text()
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        cloud = Cloud(object(), journal)
        cloud.settle(require_success=False)
        with pytest.raises(CloudOperationRejected, match="review a successor"):
            cloud.mutate("update", request, reject)
    assert len(submitted) == 1


def test_rejection_after_uncertain_submission_does_not_prove_no_effect(tmp_path):
    from grpc import StatusCode

    desired = plan()
    request = compute.StopInstanceRequest(id="compute-old")
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))
        cloud = Cloud(object(), journal)
        with pytest.raises(TimeoutError):
            cloud.mutate("stop", request, lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
        record = next(iter(cloud.records.values()))
        assert record["submission_count"] == 1
        with pytest.raises(CloudOperationRejected):
            cloud._submit(
                record,
                next(iter(cloud.records)),
                request,
                lambda *a, **k: (_ for _ in ()).throw(request_error(StatusCode.ABORTED)),
            )
        assert record["submission_count"] == 2
        assert record["status"] == "rejected"


@pytest.mark.parametrize(
    "code_name", ["DEADLINE_EXCEEDED", "UNAVAILABLE", "UNKNOWN", "CANCELLED", "INTERNAL"]
)
def test_transport_and_cancellation_errors_remain_uncertain(tmp_path, code_name):
    from grpc import StatusCode
    from nebius.aio.service_error import RequestError

    desired = plan()
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))

        def uncertain(*args, **kwargs):
            raise request_error(getattr(StatusCode, code_name))

        with pytest.raises(RequestError):
            Cloud(object(), journal).mutate(
                "stop", compute.StopInstanceRequest(id="compute-old"), uncertain
            )
        record = next(iter(journal.state["transaction"]["operations"].values()))
        assert record["status"] == "intent"
        assert "failure" not in record


def test_terminal_operation_failure_is_durable_but_never_success(tmp_path):
    from grpc import StatusCode

    desired = plan()
    operation = SimpleNamespace(
        id="operation-failed",
        sync_wait=lambda **kwargs: None,
        successful=lambda: False,
        done=lambda: True,
        status=lambda: SimpleNamespace(code=StatusCode.ABORTED),
    )
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        journal.start(desired, digest(desired.mapping()))
        cloud = Cloud(object(), journal)
        with pytest.raises(CloudOperationRejected, match="operation failed"):
            cloud.mutate(
                "stop",
                compute.StopInstanceRequest(id="compute-old"),
                lambda *args, **kwargs: SimpleNamespace(wait=lambda: operation),
            )
        record = next(iter(journal.state["transaction"]["operations"].values()))
        assert record["status"] == "failed"
        assert record["cloud_operation_id"] == "operation-failed"
        cloud.settle(require_success=False)
        with pytest.raises(CloudOperationRejected):
            cloud.completed("stop")
