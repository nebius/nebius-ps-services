from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import nebius_cxcli.deployment_status as deployment_status_module
from nebius_cxcli.deployment_status import (
    DeploymentStatusReporter,
    Mk8sDeploymentTarget,
    StatusWatcherTarget,
    _enum_field_name,
    _is_transient_node_group_warning,
    _mk8s_deployment_target,
    _transient_node_group_note,
    deployment_status_reporting,
)


def test_mk8s_deployment_target_resolves_enabled_cluster() -> None:
    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-u123",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cluster_name": "clust1",
                    },
                }
            ]
        },
    }

    assert _mk8s_deployment_target(config) == Mk8sDeploymentTarget(
        project_id="project-u123",
        cluster_name="clust1",
    )


def test_deployment_status_reporting_falls_back_to_heartbeat_on_sdk_init_error(
    monkeypatch,
) -> None:
    messages: list[str] = []

    class _BrokenPoller:
        def __init__(self, target):
            raise RuntimeError("auth failed")

    monkeypatch.setattr("nebius_cxcli.deployment_status._Mk8sStatusPoller", _BrokenPoller)

    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-u123",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cluster_name": "clust1",
                    },
                }
            ]
        },
    }

    with deployment_status_reporting(
        config,
        emit=messages.append,
        poll_interval_seconds=0.01,
        repeat_interval_seconds=0.01,
    ):
        pass

    assert any("falling back to Terraform + elapsed heartbeat" in message for message in messages)
    assert any("API unavailable; heartbeat only" in message for message in messages)
    assert any("[bold yellow]TF [/bold yellow]" in message for message in messages)
    assert any("[bold cyan]API[/bold cyan]" in message for message in messages)


def test_deployment_status_reporter_merges_terraform_and_api_status(monkeypatch) -> None:
    messages: list[str] = []

    class _FakePoller:
        def __init__(self, target):
            self.target = target

        def summary(self) -> str:
            return "mk8s clust1 (mk8scluster-123): PROVISIONING; endpoints pending; node groups 0."

        def close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.deployment_status._Mk8sStatusPoller", _FakePoller)

    config = {
        "client_info": {
            "nebius": {
                "project_id": "project-u123",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "cluster_name": "clust1",
                    },
                }
            ]
        },
    }

    reporter = DeploymentStatusReporter(
        config,
        emit=messages.append,
        poll_interval_seconds=999,
        repeat_interval_seconds=999,
    ).start()
    try:
        reporter.handle_terraform_event(
            {
                "type": "change_summary",
                "changes": {"operation": "plan", "add": 1, "change": 0, "remove": 0},
            }
        )
        reporter.handle_terraform_event(
            {
                "type": "apply_start",
                "hook": {
                    "resource": {"addr": "module.mk8s.nebius_mk8s_v1_cluster.this"},
                    "action": "create",
                },
            }
        )
    finally:
        reporter.close()

    assert any("Watching Terraform + Nebius status" in message for message in messages)
    assert any("plan 1 add, 0 change, 0 destroy" in message for message in messages)
    assert any(
        "active module.mk8s.nebius_mk8s_v1_cluster.this (create)" in message
        and "mk8s clust1 (mk8scluster-123): PROVISIONING" in message
        for message in messages
    )
    assert any("[bold yellow]TF [/bold yellow]" in message for message in messages)
    assert any("[bold cyan]API[/bold cyan]" in message for message in messages)


def test_deployment_status_reporter_includes_node_group_alerts(monkeypatch) -> None:
    messages: list[str] = []

    class _FakePoller:
        def __init__(self, target):
            self.target = target

        def summary(self) -> str:
            return (
                "mk8s clust1 (mk8scluster-123): PROVISIONING; endpoints pending; "
                "node groups workers:PROVISIONING 0/3 ready.; alerts ERROR workers: Quota exhausted."
            )

        def close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.deployment_status._Mk8sStatusPoller", _FakePoller)

    config = {
        "client_info": {"nebius": {"project_id": "project-u123"}},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {"cluster_name": "clust1"},
                }
            ]
        },
    }

    reporter = DeploymentStatusReporter(
        config,
        emit=messages.append,
        poll_interval_seconds=999,
        repeat_interval_seconds=999,
    ).start()
    try:
        reporter.handle_terraform_event(
            {
                "type": "change_summary",
                "changes": {"operation": "plan", "add": 1, "change": 0, "remove": 0},
            }
        )
    finally:
        reporter.close()

    assert any("alerts ERROR workers: Quota exhausted." in message for message in messages)


def test_mk8s_status_poller_exposes_terminal_node_group_error() -> None:
    class _Level(Enum):
        ERROR = 1

    class _StatusCode(Enum):
        RESOURCE_EXHAUSTED = 1

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="clust1", id="mk8scluster-123"),
        status=SimpleNamespace(
            state="RUNNING",
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="1.2.3.4", private_endpoint="")
            ),
        ),
    )
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(name="workers"),
        status=SimpleNamespace(
            state="PROVISIONING",
            ready_node_count=0,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Creation of compute instance is failed for nodes workers-a,workers-b",
                        error=SimpleNamespace(code=_StatusCode.RESOURCE_EXHAUSTED),
                    )
                )
            ],
        ),
    )
    poller = deployment_status_module._Mk8sStatusPoller.__new__(
        deployment_status_module._Mk8sStatusPoller
    )
    poller._target = Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    poller._find_cluster = lambda: cluster
    poller._list_node_groups = lambda cluster_id: [node_group]
    poller._node_group_state_name = lambda raw_state: str(raw_state)

    failure = poller.terminal_failure()

    assert failure is not None
    assert "ComputeInstanceCreationFailed" in failure
    assert "RESOURCE_EXHAUSTED" in failure
    assert "workers" in failure


def test_mk8s_status_poller_ignores_terminal_error_for_healthy_node_group() -> None:
    class _Level(Enum):
        ERROR = 1

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="clust1", id="mk8scluster-123"),
        status=SimpleNamespace(
            state="RUNNING",
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="1.2.3.4", private_endpoint="")
            ),
        ),
    )
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(name="workers"),
        status=SimpleNamespace(
            state="RUNNING",
            ready_node_count=2,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Old error that should not abort a healthy group",
                        error=None,
                    )
                )
            ],
        ),
    )
    poller = deployment_status_module._Mk8sStatusPoller.__new__(
        deployment_status_module._Mk8sStatusPoller
    )
    poller._target = Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    poller._find_cluster = lambda: cluster
    poller._list_node_groups = lambda cluster_id: [node_group]
    poller._node_group_state_name = lambda raw_state: str(raw_state)

    assert poller.terminal_failure() is None


def test_mk8s_status_poller_summary_reads_enum_event_levels_and_error_details() -> None:
    class _Level(Enum):
        ERROR = 1

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="clust1", id="mk8scluster-123"),
        status=SimpleNamespace(
            state="RUNNING",
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="1.2.3.4", private_endpoint="")
            ),
        ),
    )
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(name="workers"),
        status=SimpleNamespace(
            state="PROVISIONING",
            ready_node_count=0,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Creation of compute instance is failed for nodes workers-a,workers-b",
                        error="RESOURCE_EXHAUSTED: VM schedule timeout",
                    )
                )
            ],
        ),
    )
    poller = deployment_status_module._Mk8sStatusPoller.__new__(
        deployment_status_module._Mk8sStatusPoller
    )
    poller._target = Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    poller._find_cluster = lambda: cluster
    poller._list_node_groups = lambda cluster_id: [node_group]
    poller._latest_operation_summary = lambda cluster_id: None
    poller._cluster_state_name = lambda raw_state: str(raw_state)
    poller._node_group_state_name = lambda raw_state: str(raw_state)

    summary = poller.summary()

    assert "alerts ERROR workers:" in summary
    assert "RESOURCE_EXHAUSTED" in summary


def test_mk8s_status_poller_ignores_terminal_error_while_node_group_is_deleting() -> None:
    class _Level(Enum):
        ERROR = 1

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="clust1", id="mk8scluster-123"),
        status=SimpleNamespace(
            state="RUNNING",
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="1.2.3.4", private_endpoint="")
            ),
        ),
    )
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(name="workers"),
        status=SimpleNamespace(
            state="DELETING",
            ready_node_count=0,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Old create failure",
                        error="RESOURCE_EXHAUSTED",
                    )
                )
            ],
        ),
    )
    poller = deployment_status_module._Mk8sStatusPoller.__new__(
        deployment_status_module._Mk8sStatusPoller
    )
    poller._target = Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    poller._find_cluster = lambda: cluster
    poller._list_node_groups = lambda cluster_id: [node_group]
    poller._node_group_state_name = lambda raw_state: str(raw_state)

    assert poller.terminal_failure() is None


def test_mk8s_status_poller_ignores_stale_terminal_error_from_before_current_run() -> None:
    class _Level(Enum):
        ERROR = 1

    cluster = SimpleNamespace(
        metadata=SimpleNamespace(name="clust1", id="mk8scluster-123"),
        status=SimpleNamespace(
            state="RUNNING",
            control_plane=SimpleNamespace(
                endpoints=SimpleNamespace(public_endpoint="1.2.3.4", private_endpoint="")
            ),
        ),
    )
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(name="workers"),
        status=SimpleNamespace(
            state="PROVISIONING",
            ready_node_count=0,
            target_node_count=2,
            events=[
                SimpleNamespace(
                    first_occurred_at=datetime(2026, 4, 20, 18, 46, 3, tzinfo=UTC),
                    last_occurrence=SimpleNamespace(
                        level=_Level.ERROR,
                        code="ComputeInstanceCreationFailed",
                        message="Old create failure from a previous run",
                        occurred_at=datetime(2026, 4, 20, 18, 46, 4, tzinfo=UTC),
                        error="RESOURCE_EXHAUSTED",
                    ),
                )
            ],
        ),
    )
    poller = deployment_status_module._Mk8sStatusPoller.__new__(
        deployment_status_module._Mk8sStatusPoller
    )
    poller._target = Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    poller._monitor_started_at = datetime(2026, 4, 20, 19, 10, 0, tzinfo=UTC)
    poller._find_cluster = lambda: cluster
    poller._list_node_groups = lambda cluster_id: [node_group]
    poller._latest_operation_summary = lambda cluster_id: None
    poller._cluster_state_name = lambda raw_state: str(raw_state)
    poller._node_group_state_name = lambda raw_state: str(raw_state)

    assert poller.terminal_failure() is None
    summary = poller.summary()
    assert "alerts ERROR workers:" not in summary


def test_deployment_status_reporter_exposes_terminal_api_failure(monkeypatch) -> None:
    messages: list[str] = []

    class _FakePoller:
        def __init__(self, target):
            self.target = target

        def summary(self) -> str:
            return (
                "mk8s clust1 (mk8scluster-123): PROVISIONING; endpoints pending; "
                "node groups workers:PROVISIONING 0/2 ready.; "
                "alerts ERROR workers: Compute instance creation failed."
            )

        def terminal_failure(self) -> str | None:
            return (
                "mk8s node group 'workers' reported a terminal API error "
                "[ComputeInstanceCreationFailed / RESOURCE_EXHAUSTED]: "
                "Creation of compute instance is failed for nodes workers-a,workers-b"
            )

        def close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.deployment_status._Mk8sStatusPoller", _FakePoller)

    config = {
        "client_info": {"nebius": {"project_id": "project-u123"}},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "inputs": {"cluster_name": "clust1"},
                }
            ]
        },
    }

    reporter = DeploymentStatusReporter(
        config,
        emit=messages.append,
        poll_interval_seconds=999,
        repeat_interval_seconds=999,
    ).start()
    try:
        assert reporter.abort_reason() is not None
        assert "RESOURCE_EXHAUSTED" in reporter.abort_reason()
    finally:
        reporter.close()

    assert any("aborting Terraform wait early" in message for message in messages)


def test_deployment_status_reporter_supports_multiple_manifest_watchers(monkeypatch) -> None:
    messages: list[str] = []

    class _FakePgPoller:
        def __init__(self, target):
            self.target = target

        def summary(self) -> str:
            return "managed-postgresql pgsql1 (pg-123): RUNNING; state FINISHED; private rw ready."

        def close(self) -> None:
            return

    class _FakeSfsPoller:
        def __init__(self, target):
            self.target = target

        def summary(self) -> str:
            return "sfs sharedfs (fs-123): READY; attachments rw:0 ro:0."

        def close(self) -> None:
            return

    monkeypatch.setattr(
        deployment_status_module,
        "_STATUS_POLLER_FACTORIES",
        {
            "nebius.msp.postgresql.cluster": _FakePgPoller,
            "nebius.compute.filesystem": _FakeSfsPoller,
        },
    )

    reporter = DeploymentStatusReporter(
        {},
        emit=messages.append,
        status_watchers=[
            {
                "component_id": "managed-postgresql",
                "kind": "nebius.msp.postgresql.cluster",
                "parent_id": "project-u123",
                "resource_name": "pgsql1",
            },
            {
                "component_id": "sfs",
                "kind": "nebius.compute.filesystem",
                "parent_id": "project-u123",
                "resource_name": "sharedfs",
            },
        ],
        poll_interval_seconds=999,
        repeat_interval_seconds=999,
    ).start()
    try:
        reporter.handle_terraform_event(
            {
                "type": "change_summary",
                "changes": {"operation": "plan", "add": 2, "change": 0, "remove": 0},
            }
        )
    finally:
        reporter.close()

    assert any("managed-postgresql 'pgsql1'" in message for message in messages)
    assert any("sfs 'sharedfs'" in message for message in messages)
    assert any(
        "managed-postgresql pgsql1 (pg-123): RUNNING" in message
        and "sfs sharedfs (fs-123): READY" in message
        for message in messages
    )


def test_object_storage_status_poller_reads_bucket_state(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    captured: dict[str, object] = {}

    class _FakeBucketClient:
        def __init__(self, current_sdk):
            captured["bucket_sdk"] = current_sdk

        def get_by_name(self, request):
            captured["request"] = request
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    metadata=SimpleNamespace(
                        name="artifacts-customer-123",
                        id="bucket-u123",
                    ),
                    status=SimpleNamespace(state="STATE_ACTIVE"),
                )
            )

        def operation_service(self):
            return SimpleNamespace()

    monkeypatch.setattr(
        deployment_status_module,
        "init_nebius_sdk",
        lambda *, parent_id, context: (
            captured.update({"parent_id": parent_id, "context": context}) or sdk
        ),
    )
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_summary",
        lambda operation_service, resource_id: (
            captured.update({"operation_service": operation_service, "resource_id": resource_id})
            or "op Create bucket done 4s"
        ),
    )

    import nebius.api.nebius.storage.v1 as storage_api

    monkeypatch.setattr(storage_api, "BucketServiceClient", _FakeBucketClient)
    monkeypatch.setattr(
        storage_api,
        "GetBucketByNameRequest",
        lambda *, parent_id, name: SimpleNamespace(parent_id=parent_id, name=name),
    )

    poller = deployment_status_module._ObjectStorageBucketStatusPoller(
        StatusWatcherTarget(
            component_id="object-storage",
            kind="nebius.storage.bucket",
            parent_id="project-u123",
            resource_name="artifacts-customer-123",
        )
    )

    summary = poller.summary()

    assert "object-storage artifacts-customer-123 (bucket-u123): ACTIVE" in summary
    assert "op Create bucket done 4s" in summary
    assert captured["parent_id"] == "project-u123"
    assert captured["resource_id"] == "bucket-u123"


def test_transient_node_group_warnings_are_classified_as_notes() -> None:
    assert _is_transient_node_group_warning(
        level="WARN",
        message="Node condition Ready is False for node mk8snodegroup-123",
        state_name="PROVISIONING",
    )
    assert _is_transient_node_group_warning(
        level="WARN",
        message="Waiting for a node with matching ProviderID to exist for node mk8snodegroup-123",
        state_name="PROVISIONING",
    )
    assert (
        _transient_node_group_note("Node condition Ready is False for node mk8snodegroup-123")
        == "waiting for node readiness"
    )
    assert (
        _transient_node_group_note(
            "Waiting for a node with matching ProviderID to exist for node mk8snodegroup-123"
        )
        == "waiting for node registration"
    )


def test_mk8s_status_poller_uses_shared_sdk_auth(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sdk = object()

    class _FakeClusterClient:
        def __init__(self, current_sdk):
            captured["cluster_sdk"] = current_sdk

    class _FakeNodeGroupClient:
        def __init__(self, current_sdk):
            captured["node_group_sdk"] = current_sdk

    monkeypatch.setattr(
        deployment_status_module,
        "init_nebius_sdk",
        lambda *, parent_id, context: (
            captured.update({"parent_id": parent_id, "context": context}) or sdk
        ),
    )
    monkeypatch.setattr(deployment_status_module, "ClusterServiceClient", _FakeClusterClient)
    monkeypatch.setattr(deployment_status_module, "NodeGroupServiceClient", _FakeNodeGroupClient)

    poller = deployment_status_module._Mk8sStatusPoller(
        Mk8sDeploymentTarget(project_id="project-u123", cluster_name="clust1")
    )

    assert captured == {
        "parent_id": "project-u123",
        "context": "deployment status polling",
        "cluster_sdk": sdk,
        "node_group_sdk": sdk,
    }
    assert poller._sdk is sdk


def test_postgresql_status_poller_reads_clusters_response_field(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    captured: dict[str, object] = {}

    class _FakeOperationService:
        pass

    class _FakeClusterClient:
        def __init__(self, current_sdk):
            captured["cluster_sdk"] = current_sdk

        def list(self, request):
            captured["request"] = request
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    clusters=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(
                                name="pgsql1",
                                id="postgresql-u123",
                            ),
                            status=SimpleNamespace(
                                phase="PHASE_PROVISIONING",
                                state="STATE_IN_PROGRESS",
                                connection_endpoints=SimpleNamespace(
                                    private_read_write="private-rw.example.invalid",
                                    public_read_write="",
                                ),
                            ),
                        )
                    ]
                )
            )

        def operation_service(self):
            return _FakeOperationService()

    monkeypatch.setattr(
        deployment_status_module,
        "init_nebius_sdk",
        lambda *, parent_id, context: (
            captured.update({"parent_id": parent_id, "context": context}) or sdk
        ),
    )
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_summary",
        lambda operation_service, resource_id: (
            captured.update({"operation_service": operation_service, "resource_id": resource_id})
            or "op Create PostgreSQL cluster running 2m10s"
        ),
    )

    import nebius.api.nebius.msp.postgresql.v1alpha1 as postgresql_api

    monkeypatch.setattr(postgresql_api, "ClusterServiceClient", _FakeClusterClient)
    monkeypatch.setattr(
        postgresql_api,
        "ListClustersRequest",
        lambda *, parent_id: SimpleNamespace(parent_id=parent_id),
    )

    poller = deployment_status_module._PostgreSQLStatusPoller(
        StatusWatcherTarget(
            component_id="managed-postgresql",
            kind="nebius.msp.postgresql.cluster",
            parent_id="project-u123",
            resource_name="pgsql1",
        )
    )

    summary = poller.summary()

    assert "managed-postgresql pgsql1 (postgresql-u123): PROVISIONING" in summary
    assert "state IN_PROGRESS" in summary
    assert "private rw ready" in summary
    assert "op Create PostgreSQL cluster running 2m10s" in summary
    assert captured["parent_id"] == "project-u123"
    assert captured["context"] == "deployment status polling"
    assert captured["resource_id"] == "postgresql-u123"


def test_postgresql_status_poller_exposes_terminal_operation_failure(monkeypatch) -> None:
    poller = deployment_status_module._PostgreSQLStatusPoller.__new__(
        deployment_status_module._PostgreSQLStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="managed-postgresql",
        kind="nebius.msp.postgresql.cluster",
        parent_id="project-u123",
        resource_name="pgsql1",
    )
    poller._find_cluster = lambda: SimpleNamespace(
        metadata=SimpleNamespace(name="pgsql1", id="postgresql-u123"),
        status=SimpleNamespace(
            phase="PHASE_PROVISIONING",
            state="STATE_IN_PROGRESS",
        ),
    )
    poller._cluster_client = SimpleNamespace(operation_service=lambda: object())
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_failure",
        lambda operation_service, resource_id: (
            "Create PostgreSQL cluster [RESOURCE_EXHAUSTED]: not enough resources"
        ),
    )

    failure = poller.terminal_failure()

    assert failure is not None
    assert "managed-postgresql 'pgsql1' reported a terminal API error" in failure
    assert "RESOURCE_EXHAUSTED" in failure


def test_filesystem_status_poller_exposes_terminal_operation_failure(monkeypatch) -> None:
    poller = deployment_status_module._FilesystemStatusPoller.__new__(
        deployment_status_module._FilesystemStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="sfs",
        kind="nebius.compute.filesystem",
        parent_id="project-u123",
        resource_name="sharedfs",
    )
    poller._find_filesystem = lambda: SimpleNamespace(
        metadata=SimpleNamespace(name="sharedfs", id="filesystem-u123"),
        status=SimpleNamespace(
            state="CREATING",
            state_description="Waiting for allocation",
        ),
    )
    poller._filesystem_client = SimpleNamespace(operation_service=lambda: object())
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_failure",
        lambda operation_service, resource_id: (
            "Create filesystem [RESOURCE_EXHAUSTED]: insufficient storage capacity"
        ),
    )

    failure = poller.terminal_failure()

    assert failure is not None
    assert "sfs 'sharedfs' reported a terminal API error" in failure
    assert "RESOURCE_EXHAUSTED" in failure


def test_object_storage_status_poller_exposes_terminal_operation_failure(monkeypatch) -> None:
    poller = deployment_status_module._ObjectStorageBucketStatusPoller.__new__(
        deployment_status_module._ObjectStorageBucketStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="object-storage",
        kind="nebius.storage.bucket",
        parent_id="project-u123",
        resource_name="artifacts-customer-123",
    )
    poller._find_bucket = lambda: SimpleNamespace(
        metadata=SimpleNamespace(name="artifacts-customer-123", id="bucket-u123"),
        status=SimpleNamespace(state="STATE_CREATING"),
    )
    poller._bucket_client = SimpleNamespace(operation_service=lambda: object())
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_failure",
        lambda operation_service, resource_id: "Create bucket [RESOURCE_EXHAUSTED]: quota exceeded",
    )

    failure = poller.terminal_failure()

    assert failure is not None
    assert "object-storage 'artifacts-customer-123' reported a terminal API error" in failure
    assert "RESOURCE_EXHAUSTED" in failure


def test_compute_instance_status_poller_reads_instance_state(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    captured: dict[str, object] = {}

    class _FakeOperationService:
        pass

    class _FakeInstanceClient:
        def __init__(self, current_sdk):
            captured["instance_sdk"] = current_sdk

        def list(self, request):
            captured["request"] = request
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    instances=[
                        SimpleNamespace(
                            metadata=SimpleNamespace(
                                name="bastion1",
                                id="instance-u123",
                            ),
                            status=SimpleNamespace(
                                state="RUNNING",
                                reconciling=False,
                                network_interfaces=[
                                    SimpleNamespace(
                                        public_ip_address=SimpleNamespace(address="203.0.113.10")
                                    )
                                ],
                            ),
                        )
                    ]
                )
            )

        def operation_service(self):
            return _FakeOperationService()

    monkeypatch.setattr(
        deployment_status_module,
        "init_nebius_sdk",
        lambda *, parent_id, context: (
            captured.update({"parent_id": parent_id, "context": context}) or sdk
        ),
    )
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_summary",
        lambda operation_service, resource_id: (
            captured.update({"operation_service": operation_service, "resource_id": resource_id})
            or "op Create instance running 25s"
        ),
    )

    import nebius.api.nebius.compute.v1 as compute_api

    monkeypatch.setattr(compute_api, "InstanceServiceClient", _FakeInstanceClient)
    monkeypatch.setattr(
        compute_api,
        "ListInstancesRequest",
        lambda *, parent_id: SimpleNamespace(parent_id=parent_id),
    )

    poller = deployment_status_module._ComputeInstanceStatusPoller(
        StatusWatcherTarget(
            component_id="ssh-jumphost",
            kind="nebius.compute.instance",
            parent_id="project-u123",
            resource_name="bastion1",
        )
    )

    summary = poller.summary()

    assert "compute instance bastion1 (instance-u123): RUNNING" in summary
    assert "public IP ready" in summary
    assert "op Create instance running 25s" in summary
    assert captured["parent_id"] == "project-u123"
    assert captured["resource_id"] == "instance-u123"


def test_compute_instance_status_poller_exposes_terminal_operation_failure(
    monkeypatch,
) -> None:
    poller = deployment_status_module._ComputeInstanceStatusPoller.__new__(
        deployment_status_module._ComputeInstanceStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="ssh-jumphost",
        kind="nebius.compute.instance",
        parent_id="project-u123",
        resource_name="bastion1",
    )
    poller._find_instance = lambda: SimpleNamespace(
        metadata=SimpleNamespace(name="bastion1", id="instance-u123"),
        status=SimpleNamespace(
            state="STARTING",
            reconciling=True,
            network_interfaces=[],
        ),
    )
    poller._instance_client = SimpleNamespace(operation_service=lambda: object())
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_failure",
        lambda operation_service, resource_id: (
            "Create instance [RESOURCE_EXHAUSTED]: VM schedule timeout"
        ),
    )

    failure = poller.terminal_failure()

    assert failure is not None
    assert "compute instance 'bastion1' reported a terminal API error" in failure
    assert "RESOURCE_EXHAUSTED" in failure


def test_compute_instance_status_watcher_kind_is_registered() -> None:
    assert (
        deployment_status_module._STATUS_POLLER_FACTORIES["nebius.compute.instance"]
        is deployment_status_module._ComputeInstanceStatusPoller
    )


def test_mysterybox_secret_status_poller_reads_secret_state(monkeypatch) -> None:
    sdk = SimpleNamespace(sync_close=lambda: None)
    captured: dict[str, object] = {}

    class _FakeOperationService:
        pass

    class _FakeSecretClient:
        def __init__(self, current_sdk):
            captured["secret_sdk"] = current_sdk

        def get_by_name(self, request):
            captured["request"] = request
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    metadata=SimpleNamespace(
                        name="app-runtime",
                        id="secret-u123",
                    ),
                    status=SimpleNamespace(
                        state="ACTIVE",
                        effective_kms_key_id="kmskey-u123",
                    ),
                )
            )

        def operation_service(self):
            return _FakeOperationService()

    monkeypatch.setattr(
        deployment_status_module,
        "init_nebius_sdk",
        lambda *, parent_id, context: (
            captured.update({"parent_id": parent_id, "context": context}) or sdk
        ),
    )
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_summary",
        lambda operation_service, resource_id: (
            captured.update({"operation_service": operation_service, "resource_id": resource_id})
            or "op Create secret done 6s"
        ),
    )

    import nebius.api.nebius.mysterybox.v1 as mysterybox_api

    monkeypatch.setattr(mysterybox_api, "SecretServiceClient", _FakeSecretClient)
    monkeypatch.setattr(
        mysterybox_api,
        "GetSecretByNameRequest",
        lambda *, parent_id, name: SimpleNamespace(parent_id=parent_id, name=name),
    )

    poller = deployment_status_module._MysteryBoxSecretStatusPoller(
        StatusWatcherTarget(
            component_id="mysterybox",
            kind="nebius.mysterybox.secret",
            parent_id="project-u123",
            resource_name="app-runtime",
        )
    )

    summary = poller.summary()

    assert "mysterybox secret app-runtime (secret-u123): ACTIVE" in summary
    assert "kms key kmskey-u123" in summary
    assert "op Create secret done 6s" in summary
    assert captured["parent_id"] == "project-u123"
    assert captured["resource_id"] == "secret-u123"


def test_mysterybox_secret_status_poller_exposes_terminal_operation_failure(
    monkeypatch,
) -> None:
    poller = deployment_status_module._MysteryBoxSecretStatusPoller.__new__(
        deployment_status_module._MysteryBoxSecretStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="mysterybox",
        kind="nebius.mysterybox.secret",
        parent_id="project-u123",
        resource_name="app-runtime",
    )
    poller._find_secret = lambda: SimpleNamespace(
        metadata=SimpleNamespace(name="app-runtime", id="secret-u123"),
        status=SimpleNamespace(
            state="STATE_UNSPECIFIED",
            effective_kms_key_id="",
        ),
    )
    poller._secret_client = SimpleNamespace(operation_service=lambda: object())
    monkeypatch.setattr(
        deployment_status_module,
        "_latest_operation_failure",
        lambda operation_service, resource_id: (
            "Create secret [RESOURCE_EXHAUSTED]: backend unavailable"
        ),
    )

    failure = poller.terminal_failure()

    assert failure is not None
    assert "mysterybox secret 'app-runtime' reported a terminal API error" in failure
    assert "RESOURCE_EXHAUSTED" in failure


def test_mysterybox_secret_status_poller_reports_absent_resource_for_destroy() -> None:
    poller = deployment_status_module._MysteryBoxSecretStatusPoller.__new__(
        deployment_status_module._MysteryBoxSecretStatusPoller
    )
    poller._target = StatusWatcherTarget(
        component_id="mysterybox",
        kind="nebius.mysterybox.secret",
        parent_id="project-u123",
        resource_name="app-runtime",
        operation="destroy",
    )
    poller._find_secret = lambda: None

    assert (
        poller.summary()
        == "mysterybox secret 'app-runtime' is already absent in project project-u123."
    )


def test_mysterybox_secret_status_watcher_kind_is_registered() -> None:
    assert (
        deployment_status_module._STATUS_POLLER_FACTORIES["nebius.mysterybox.secret"]
        is deployment_status_module._MysteryBoxSecretStatusPoller
    )


def test_enum_field_name_supports_python_enum_values() -> None:
    class _Phase(Enum):
        PHASE_PROVISIONING = 2

    message = SimpleNamespace(phase=_Phase.PHASE_PROVISIONING)

    assert _enum_field_name(message, "phase", prefixes=("PHASE_",)) == "PROVISIONING"
