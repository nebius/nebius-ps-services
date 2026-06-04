from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

import pytest

import nebius_cxcli.mk8s_destroy_recovery as recovery


def test_find_stuck_node_groups_returns_only_terminal_unfinished_creates(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)

    class _Level(Enum):
        ERROR = 1

    class _StatusCode(Enum):
        RESOURCE_EXHAUSTED = 1

    cluster = SimpleNamespace(metadata=SimpleNamespace(id="mk8scluster-123"))
    stuck_node_group = SimpleNamespace(
        metadata=SimpleNamespace(id="ng-1", name="cluster1-ng-gpu"),
        status=SimpleNamespace(
            state="PROVISIONING",
            ready_node_count=0,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Creation of compute instance is failed for nodes ng-1-a,ng-1-b",
                        error=SimpleNamespace(code=_StatusCode.RESOURCE_EXHAUSTED),
                    )
                )
            ],
        ),
    )
    healthy_node_group = SimpleNamespace(
        metadata=SimpleNamespace(id="ng-2", name="cluster1-ng-cpu"),
        status=SimpleNamespace(
            state="RUNNING",
            ready_node_count=1,
            target_node_count=1,
            events=[],
        ),
    )

    class _FakeClusterClient:
        def __init__(self, current_sdk):
            assert current_sdk is sdk

        def get_by_name(self, request):
            assert request.parent_id == "project-u123"
            assert request.name == "cluster1"
            return SimpleNamespace(wait=lambda: cluster)

    class _FakeOperationService:
        def list(self, request):
            if request.resource_id == "ng-1":
                operations = [
                    SimpleNamespace(
                        id="op-create-ng-1",
                        description="Create node group",
                        created_at=2,
                    )
                ]
            else:
                operations = [
                    SimpleNamespace(
                        id="op-create-ng-2",
                        description="Create node group",
                        created_at=1,
                    )
                ]
            return SimpleNamespace(wait=lambda: SimpleNamespace(operations=operations))

        def get(self, request):
            if request.id == "op-create-ng-1":
                operation = SimpleNamespace(done=lambda: False)
            else:
                operation = SimpleNamespace(done=lambda: True)
            return SimpleNamespace(wait=lambda: operation)

    class _FakeNodeGroupClient:
        def __init__(self, current_sdk):
            assert current_sdk is sdk

        def list(self, request):
            assert request.parent_id == "mk8scluster-123"
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(items=[stuck_node_group, healthy_node_group])
            )

        def operation_service(self):
            return _FakeOperationService()

    monkeypatch.setattr(recovery, "init_nebius_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(recovery, "ClusterServiceClient", _FakeClusterClient)
    monkeypatch.setattr(recovery, "NodeGroupServiceClient", _FakeNodeGroupClient)

    candidates = recovery.find_stuck_node_groups(project_id="project-u123", cluster_name="cluster1")

    assert candidates == (
        recovery.Mk8sNodeGroupDestroyCandidate(
            project_id="project-u123",
            cluster_name="cluster1",
            cluster_id="mk8scluster-123",
            node_group_name="cluster1-ng-gpu",
            node_group_id="ng-1",
            create_operation_id="op-create-ng-1",
            reason=(
                "Creation of compute instance is failed for nodes ng-1-a,ng-1-b "
                "[ComputeInstanceCreationFailed / RESOURCE_EXHAUSTED]"
            ),
        ),
    )


def test_delete_node_group_waits_for_delete_operation(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    operation = SimpleNamespace(
        id="op-delete-ng-1",
        sync_wait=lambda timeout=None: recorded.append(("wait", timeout)),
        successful=lambda: True,
        status=lambda: "OK",
    )
    recorded: list[tuple[str, object]] = []

    class _FakeNodeGroupClient:
        def __init__(self, current_sdk):
            assert current_sdk is sdk

        def delete(self, request):
            recorded.append(("delete", request.id))
            return SimpleNamespace(wait=lambda: operation)

    monkeypatch.setattr(recovery, "init_nebius_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(recovery, "NodeGroupServiceClient", _FakeNodeGroupClient)

    operation_id = recovery.delete_node_group(
        recovery.Mk8sNodeGroupDestroyCandidate(
            project_id="project-u123",
            cluster_name="cluster1",
            cluster_id="mk8scluster-123",
            node_group_name="cluster1-ng-gpu",
            node_group_id="ng-1",
            create_operation_id="op-create-ng-1",
            reason="demo",
        ),
        timeout_seconds=42,
    )

    assert operation_id == "op-delete-ng-1"
    assert recorded == [("delete", "ng-1"), ("wait", 42)]


def test_delete_node_group_rejects_unconfirmable_delete_operation(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    operation = SimpleNamespace(id="op-delete-ng-1")

    class _FakeNodeGroupClient:
        def __init__(self, current_sdk):
            assert current_sdk is sdk

        def delete(self, request):
            assert request.id == "ng-1"
            return SimpleNamespace(wait=lambda: operation)

    monkeypatch.setattr(recovery, "init_nebius_sdk", lambda **_kwargs: sdk)
    monkeypatch.setattr(recovery, "NodeGroupServiceClient", _FakeNodeGroupClient)

    with pytest.raises(RuntimeError, match="cannot be confirmed"):
        recovery.delete_node_group(
            recovery.Mk8sNodeGroupDestroyCandidate(
                project_id="project-u123",
                cluster_name="cluster1",
                cluster_id="mk8scluster-123",
                node_group_name="cluster1-ng-gpu",
                node_group_id="ng-1",
                create_operation_id="op-create-ng-1",
                reason="demo",
            ),
            timeout_seconds=42,
        )
