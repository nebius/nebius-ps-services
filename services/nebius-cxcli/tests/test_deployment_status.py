from __future__ import annotations

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
    assert _transient_node_group_note(
        "Node condition Ready is False for node mk8snodegroup-123"
    ) == "waiting for node readiness"
    assert _transient_node_group_note(
        "Waiting for a node with matching ProviderID to exist for node mk8snodegroup-123"
    ) == "waiting for node registration"


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


def test_enum_field_name_supports_python_enum_values() -> None:
    class _Phase(Enum):
        PHASE_PROVISIONING = 2

    message = SimpleNamespace(phase=_Phase.PHASE_PROVISIONING)

    assert _enum_field_name(message, "phase", prefixes=("PHASE_",)) == "PROVISIONING"
