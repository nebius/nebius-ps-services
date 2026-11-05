import json
from types import SimpleNamespace

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from test_replacement import plan

from nebius_vpngw.deploy.replacement import Journal, digest
from nebius_vpngw.deploy.replacement_cloud import Cloud, message_digest


@pytest.mark.parametrize("kind", ["stop", "update"])
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
    with Journal(desired.project_id, desired.gateway_name, root=tmp_path / "state") as journal:
        cloud = Cloud(object(), journal)
        monkeypatch.setattr(cloud, "get_instance", lambda _: original)
        cloud.settle()
        assert cloud.completed(kind) == "compute-old"
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
            lambda _: SimpleNamespace(spec=SimpleNamespace(cloud_init_user_data="fixture-drift")),
        )
        with pytest.raises(RuntimeError, match="reconstruction changed"):
            cloud.settle()
    assert submitted == []
