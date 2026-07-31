from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_ADMISSION_CLOSED,
    begin_target_slurm_action_generation,
    enqueue_slurm_action,
    finalize_target_slurm_action_generation,
    new_slurm_action_journal,
    set_slurm_action_broker_mode,
)


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_retained_bridge_slot_zero_keeps_canonical_label_value() -> None:
    assert migration._controller_bridge_slot_label_value({"slot": 0}) == "0"  # noqa: SLF001
    assert migration._controller_bridge_slot_label_value({"slot": 1}) == "1"  # noqa: SLF001
    assert migration._controller_bridge_slot_label_value({}) == ""  # noqa: SLF001


def test_bridge_node_group_cleanup_accepts_slot_zero_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_id = "bridge-group-a"
    group_name = "cxcli-bridge-a"
    live_group = {
        "metadata": {
            "id": group_id,
            "uid": "bridge-group-a-uid",
            "name": group_name,
            "resource_version": "1",
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {
                        migration.CONTROLLER_BRIDGE_LABEL: "true",
                        migration.CONTROLLER_BRIDGE_SLOT_LABEL: "0",
                    }
                }
            }
        },
    }

    class _Api:
        def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
            assert node_group_id == group_id
            return live_group

    monkeypatch.setattr(
        migration,
        "_provider_operation_intent_for_request",
        lambda **_kwargs: {"attempt_state": "intent-recorded"},
    )
    monkeypatch.setattr(migration, "_provider_operation_requested", lambda **_kwargs: None)
    deleted: list[str] = []
    monkeypatch.setattr(
        migration,
        "_delete_node_group",
        lambda **kwargs: deleted.append(str(kwargs["node_group_id"])) or {},
    )
    monkeypatch.setattr(migration, "_provider_operation_terminal", lambda **_kwargs: None)
    journal = {
        "cleanup": {
            "node_group_operations": {},
            "node_groups_deleted": [],
            "node_groups_preserved": [],
        },
        "node_groups": [
            {
                "id": group_id,
                "name": group_name,
                "slot": 0,
                "cleanup_policy": "delete-domain",
            }
        ],
    }

    result = migration._controller_bridge_delete_node_groups(  # noqa: SLF001
        checkpoint={},
        journal=journal,
        nebius_api=_Api(),
        checkpoint_writer=lambda: None,
    )

    assert result == [group_id]
    assert deleted == [group_id]


def test_cleanup_delete_transport_is_narrowly_allowlisted() -> None:
    supported = (
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge",
        "/api/v1/namespaces/cxcli-soperator-upgrade-inspectors",
        "/api/v1/persistentvolumes/cxcli-controller-bridge-state-pv",
        "/api/v1/persistentvolumes/cxcli-controller-bridge-jail-pv",
        "/apis/scheduling.k8s.io/v1/priorityclasses/cxcli-soperator-upgrade-bridge",
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/persistentvolumeclaims/"
        "cxcli-controller-bridge-state",
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/persistentvolumeclaims/"
        "cxcli-controller-bridge-jail",
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/pods/cxcli-controller-bridge-canary-0",
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/pods/cxcli-controller-bridge-canary-1",
        "/apis/rbac.authorization.k8s.io/v1/namespaces/soperator/rolebindings/"
        "cxcli-controller-bridge-power-manager",
    )

    assert all(
        migration._uid_preconditioned_delete_api_path_is_supported(path)  # noqa: SLF001
        for path in supported
    )
    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/api/v1/namespaces/customer"
    )
    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/api/v1/persistentvolumes/customer-state-pv"
    )
    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/apis/scheduling.k8s.io/v1/priorityclasses/customer"
    )
    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/persistentvolumeclaims/customer"
    )
    assert not migration._uid_preconditioned_delete_api_path_is_supported(  # noqa: SLF001
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/pods/customer"
    )


def test_uid_preconditioned_delete_can_dispatch_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deletes: list[str] = []
    waits: list[str] = []
    api_path = (
        "/api/v1/namespaces/cxcli-soperator-upgrade-bridge/persistentvolumeclaims/"
        "cxcli-controller-bridge-jail"
    )
    monkeypatch.setattr(
        migration,
        "_delete_kubernetes_resource_with_uid_precondition",
        lambda **kwargs: deletes.append(str(kwargs["uid"])),
    )
    monkeypatch.setattr(
        migration,
        "_wait_for_kubernetes_resource_uid_absent",
        lambda **kwargs: waits.append(str(kwargs["uid"])),
    )

    migration._command_runner_uid_preconditioned_delete(  # noqa: SLF001
        lambda *_args, **_kwargs: pytest.fail("unexpected resource polling"),
        kube_context="context",
        api_path=api_path,
        uid="bridge-jail-pvc-uid",
        resource_version="12",
        propagation_policy="Background",
        timeout_seconds=300,
        wait_for_absence=False,
    )

    assert deletes == ["bridge-jail-pvc-uid"]
    assert waits == []


def test_exact_pvc_nonblocking_delete_reaches_delete_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "name": "cxcli-controller-bridge-jail",
        "uid": "bridge-jail-pvc-uid",
        "resource_version": "12",
    }
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (
            True,
            {"metadata": {"uid": identity["uid"], "resourceVersion": "12"}},
        ),
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        migration,
        "_command_runner_uid_preconditioned_delete",
        lambda *_args, **kwargs: calls.append(bool(kwargs["wait_for_absence"])),
    )

    cleanup: dict[str, Any] = {"kubernetes_operations": {}}
    migration._controller_bridge_delete_exact_kubernetes_resource(  # noqa: SLF001
        cleanup=cleanup,
        identity=identity,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        checkpoint_writer=lambda: None,
        wait_for_absence=False,
    )

    assert calls == [False]
    operation = next(iter(cleanup["kubernetes_operations"].values()))
    assert operation["state"] == "delete-accepted"


def test_exact_pvc_delete_reconciles_lost_response_from_same_uid_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "name": "cxcli-controller-bridge-state",
        "uid": "bridge-state-pvc-uid",
        "resource_version": "12",
    }
    key = migration._controller_bridge_resource_journal_key(identity)  # noqa: SLF001
    cleanup = {
        "kubernetes_operations": {
            key: {
                "identity": dict(identity),
                "intent_at": "2026-07-12T10:00:00Z",
                "state": "dispatching",
                "dispatching_at": "2026-07-12T10:00:01Z",
                "delete_resource_version": "12",
            }
        }
    }
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (
            True,
            {
                "metadata": {
                    "uid": identity["uid"],
                    "resourceVersion": "13",
                    "deletionTimestamp": "2026-07-12T10:00:02Z",
                }
            },
        ),
    )
    waits: list[str] = []
    monkeypatch.setattr(
        migration,
        "_wait_for_kubernetes_resource_uid_absent",
        lambda **kwargs: waits.append(str(kwargs["uid"])),
    )

    def no_second_delete(
        _args: Sequence[str], **_kwargs: Any
    ) -> migration.SoperatorMigrationCommandResult:
        pytest.fail("same-UID terminating PVC was blindly deleted a second time")

    migration._controller_bridge_delete_exact_kubernetes_resource(  # noqa: SLF001
        cleanup=cleanup,
        identity=identity,
        kube_context="context",
        command_runner=no_second_delete,
        checkpoint_writer=lambda: None,
    )

    operation = cleanup["kubernetes_operations"][key]
    assert operation["state"] == "absent"
    assert operation["terminating_resource_version"] == "13"
    assert waits == [identity["uid"]]


def test_exact_pvc_delete_rejects_changed_resource_version_without_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "name": "cxcli-controller-bridge-jail",
        "uid": "bridge-jail-pvc-uid",
        "resource_version": "14",
    }
    key = migration._controller_bridge_resource_journal_key(identity)  # noqa: SLF001
    cleanup = {
        "kubernetes_operations": {
            key: {
                "identity": dict(identity),
                "intent_at": "2026-07-12T10:00:00Z",
                "state": "dispatching",
                "dispatching_at": "2026-07-12T10:00:01Z",
                "delete_resource_version": "14",
            }
        }
    }
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (
            True,
            {"metadata": {"uid": identity["uid"], "resourceVersion": "15"}},
        ),
    )

    with pytest.raises(RuntimeError, match="without same-UID termination evidence"):
        migration._controller_bridge_delete_exact_kubernetes_resource(  # noqa: SLF001
            cleanup=cleanup,
            identity=identity,
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=lambda: None,
        )


def test_final_singleton_proof_rejects_pending_second_slurmctld_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "a" * 64
    target_pod = {
        "metadata": {
            "namespace": "soperator",
            "name": "controller-0",
            "uid": "target-controller-pod-uid",
        },
        "spec": {"containers": [{"name": "slurmctld"}]},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "slurmctld",
                    "imageID": f"registry.example/slurm@sha256:{digest}",
                }
            ],
        },
    }
    pending_source = {
        "metadata": {
            "namespace": "soperator-source",
            "name": "controller-0",
            "uid": "pending-source-controller-uid",
        },
        "spec": {"containers": [{"name": "slurmctld"}]},
        "status": {"phase": "Pending"},
    }
    workload = {
        "metadata": {"uid": "target-controller-workload-uid"},
        "spec": {"replicas": 1},
    }

    def get_resource(**kwargs: Any) -> tuple[bool, Mapping[str, Any]]:
        resource = str(kwargs["resource"])
        if resource == "pod/controller-0":
            return True, target_pod
        if resource == "statefulset.apps.kruise.io/controller":
            return True, workload
        raise AssertionError(resource)

    def json_command(_runner: Any, args: Sequence[str], **_kwargs: Any) -> Mapping[str, Any]:
        if "configmap" in args:
            return {
                "data": {
                    "slurm.conf": (
                        "ClusterName=cluster\n"
                        "SlurmctldHost=controller-0\n"
                        "StateSaveLocation=/mnt/controller-spool/current\n"
                    )
                }
            }
        if "pods" in args and "--all-namespaces" in args:
            return {"items": [target_pod, pending_source]}
        raise AssertionError(tuple(args))

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    monkeypatch.setattr(migration, "_json_from_command", json_command)

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(
            args,
            stdout=(
                "Slurmctld(primary) at controller-0 is UP\n"
                "StateSaveLocation = /mnt/controller-spool/current\n"
            ),
        )

    journal = {
        "target_singleton_takeover": {
            "controller_pod_uid": "target-controller-pod-uid",
            "controller_workload_uid": "target-controller-workload-uid",
            "final_config_map_name": "slurm-config",
            "target_state_save_location": "/mnt/controller-spool/current",
        },
        "version_transition": {
            "target_image": f"registry.example/slurm@sha256:{digest}",
        },
        "target_image_lock": {
            "immutable_reference": f"registry.example/slurm@sha256:{digest}",
            "repository": "registry.example/slurm",
            "index_digest": f"sha256:{digest}",
            "platform_digest": f"sha256:{digest}",
        },
        "state_manifest": {"stable_path": "/mnt/controller-spool/current"},
        "source_configuration": {"config_key": "slurm.conf"},
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="second nonterminal"):
        migration._prove_controller_bridge_cleanup_target_singleton(  # noqa: SLF001
            journal=journal,
            kube_context="context",
            command_runner=runner,
        )


def test_target_state_binding_uses_active_target_mount_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_pod = {
        "spec": {
            "containers": [
                {
                    "name": "slurmctld",
                    "volumeMounts": [
                        {"name": "controller-spool", "mountPath": "/var/spool/slurmctld"}
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "controller-spool",
                    "persistentVolumeClaim": {"claimName": "controller-spool-pvc"},
                }
            ],
        }
    }
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (
            True,
            {
                "metadata": {"uid": "target-pvc-uid"},
                "spec": {"volumeName": "target-pv"},
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {
            "metadata": {"uid": "target-pv-uid"},
            "spec": {
                "claimRef": {
                    "namespace": "soperator",
                    "name": "controller-spool-pvc",
                    "uid": "target-pvc-uid",
                }
            },
        },
    )
    journal = {
        "state_manifest": {"stable_path": "/mnt/controller-spool/current"},
        "kubernetes_resources": [
            {"kind": "PersistentVolume", "uid": "bridge-pv-uid"},
            {"kind": "PersistentVolumeClaim", "uid": "bridge-pvc-uid"},
        ],
    }

    binding = migration._controller_bridge_target_state_binding(  # noqa: SLF001
        journal=journal,
        target_pod=target_pod,
        container_name="slurmctld",
        state_path="/var/spool/slurmctld",
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
    )

    assert binding["state_path"] == "/var/spool/slurmctld"
    assert binding["pvc_uid"] == "target-pvc-uid"
    assert binding["pv_uid"] == "target-pv-uid"


def test_final_singleton_proof_accepts_locked_index_digest_and_target_mount_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_digest = "b" * 64
    index_digest = "c" * 64
    target_pod = {
        "metadata": {"uid": "target-pod-uid"},
        "spec": {"containers": [{"name": "slurmctld"}]},
        "status": {
            "containerStatuses": [
                {
                    "name": "slurmctld",
                    "imageID": f"registry.example/slurm@sha256:{index_digest}",
                }
            ]
        },
    }

    def get_resource(**kwargs: Any) -> tuple[bool, Mapping[str, Any]]:
        if kwargs["resource"] == "pod/controller-0":
            return True, target_pod
        if kwargs["resource"] == "statefulset.apps.kruise.io/controller":
            return True, {"metadata": {"uid": "target-workload-uid"}, "spec": {"replicas": 1}}
        raise AssertionError(kwargs["resource"])

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {
            "data": {
                "slurm.conf": ("SlurmctldHost[0] = controller-0(target-cluster-controller-svc)\n")
            }
        },
    )
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_exclusivity",
        lambda **_kwargs: None,
    )
    observed_paths: list[str] = []

    def bind_state(**kwargs: Any) -> dict[str, str]:
        observed_paths.append(str(kwargs["state_path"]))
        return {"state_path": str(kwargs["state_path"])}

    monkeypatch.setattr(migration, "_controller_bridge_target_state_binding", bind_state)
    journal = {
        "campaign_fingerprint": "campaign",
        "target_singleton_takeover": {
            "controller_pod_uid": "target-pod-uid",
            "controller_workload_uid": "target-workload-uid",
            "final_config_map_name": "slurm-config",
            "target_state_save_location": "/var/spool/slurmctld",
        },
        "version_transition": {
            "target_image": f"registry.example/slurm@sha256:{platform_digest}",
        },
        "target_image_lock": {
            "immutable_reference": (f"registry.example/slurm@sha256:{platform_digest}"),
            "repository": "registry.example/slurm",
            "index_digest": f"sha256:{index_digest}",
            "platform_digest": f"sha256:{platform_digest}",
        },
        "state_manifest": {"stable_path": "/mnt/controller-spool/current"},
        "source_configuration": {"config_key": "slurm.conf"},
    }

    result = migration._prove_controller_bridge_cleanup_target_singleton(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(
            args,
            stdout=(
                "Slurmctld(primary) at controller-0 is UP\n"
                "StateSaveLocation = /var/spool/slurmctld\n"
            ),
        ),
    )

    assert result == {"state_path": "/var/spool/slurmctld"}
    assert observed_paths == ["/var/spool/slurmctld"]


def test_cleanup_revalidation_delegates_to_full_target_successor_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal: dict[str, Any] = {"target_singleton_takeover": {"target_ref": "target-cluster"}}
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_revalidate_target_singleton_jwt_material",
        lambda **kwargs: calls.append(kwargs),
    )

    def writer() -> None:
        return None

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(args)

    migration._revalidate_target_singleton_before_controller_bridge_cleanup(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=writer,
    )

    assert calls == [
        {
            "journal": journal,
            "target_ref": "target-cluster",
            "kube_context": "context",
            "command_runner": runner,
            "checkpoint_writer": writer,
        }
    ]


def test_cleanup_revalidation_requires_checkpointed_target_identity() -> None:
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="checkpointed target SlurmCluster identity",
    ):
        migration._revalidate_target_singleton_before_controller_bridge_cleanup(  # noqa: SLF001
            journal={"target_singleton_takeover": {}},
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=None,
        )


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("SlurmctldHost=controller-0\n", ("controller-0",)),
        (
            "SlurmctldHost[0] = controller-0(target-cluster-controller-svc)\n",
            ("controller-0",),
        ),
        (
            "SlurmctldHost=controller-0(service)\nSlurmctldHost=controller-1(service)\n",
            ("controller-0", "controller-1"),
        ),
        ("SlurmctldHost=controller-0(service) unexpected\n", ()),
    ],
)
def test_slurmctld_host_identities_parse_canonical_forms(
    config: str,
    expected: tuple[str, ...],
) -> None:
    assert migration._slurmctld_host_identities(config) == expected  # noqa: SLF001


def test_target_state_binding_rejects_unmounted_state_path() -> None:
    target_pod = {
        "spec": {
            "containers": [
                {
                    "name": "slurmctld",
                    "volumeMounts": [
                        {"name": "controller-spool", "mountPath": "/var/spool/slurmctld"}
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "controller-spool",
                    "persistentVolumeClaim": {"claimName": "controller-spool-pvc"},
                }
            ],
        }
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="StateSaveLocation to resolve",
    ):
        migration._controller_bridge_target_state_binding(  # noqa: SLF001
            journal={"kubernetes_resources": []},
            target_pod=target_pod,
            container_name="slurmctld",
            state_path="/unmounted/controller-state",
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
        )


def _cleanup_resume_journal(*, stage: str) -> dict[str, Any]:
    namespace = {
        "api_version": "v1",
        "kind": "Namespace",
        "namespace": "",
        "name": "cxcli-soperator-upgrade-bridge",
        "uid": "bridge-namespace-uid",
        "resource_version": "10",
    }
    pv = {
        "api_version": "v1",
        "kind": "PersistentVolume",
        "namespace": "",
        "name": "cxcli-controller-bridge-state-pv",
        "uid": "bridge-pv-uid",
        "resource_version": "11",
    }
    pvc = {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "name": "cxcli-controller-bridge-state",
        "uid": "bridge-pvc-uid",
        "resource_version": "12",
    }
    jail_pv = {
        "api_version": "v1",
        "kind": "PersistentVolume",
        "namespace": "",
        "name": "cxcli-controller-bridge-jail-pv",
        "uid": "bridge-jail-pv-uid",
        "resource_version": "13",
    }
    jail_pvc = {
        "api_version": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "cxcli-soperator-upgrade-bridge",
        "name": "cxcli-controller-bridge-jail",
        "uid": "bridge-jail-pvc-uid",
        "resource_version": "14",
    }
    namespace_key = migration._controller_bridge_resource_journal_key(namespace)  # noqa: SLF001
    inspector_namespace = {
        "api_version": "v1",
        "kind": "Namespace",
        "namespace": "",
        "name": "cxcli-soperator-upgrade-inspectors",
        "uid": "inspector-namespace-uid",
        "resource_version": "15",
    }
    inspector_key = migration._controller_bridge_resource_journal_key(  # noqa: SLF001
        inspector_namespace
    )
    return {
        "stage": stage,
        "authority": {"owner": "target-singleton"},
        "fencing": {"bridge": {"proven": True, "processes_absent": True}},
        "login_session_handoff": {"state": "complete"},
        "security_contract": {
            "inspector": {
                "namespace": "cxcli-soperator-upgrade-inspectors",
                "namespace_uid": "inspector-namespace-uid",
            }
        },
        "namespace": "cxcli-soperator-upgrade-bridge",
        "kubernetes_resources": [namespace, pv, pvc, jail_pv, jail_pvc],
        "node_groups": [
            {"id": "bridge-a", "cleanup_policy": "delete-domain"},
            {"id": "bridge-b", "cleanup_policy": "delete-domain"},
        ],
        "cleanup": {
            "intent_at": "2026-07-12T10:00:00Z",
            "bridge_storage_verified_at": "2026-07-12T10:01:00Z",
            "bridge_storage_bindings": {
                "controller_spool": {
                    "pv_name": pv["name"],
                    "pv_uid": pv["uid"],
                    "pvc_name": pvc["name"],
                    "pvc_uid": pvc["uid"],
                    "reclaim_policy": "Retain",
                    "local_path": "/mnt/controller-spool",
                },
                "jail": {
                    "pv_name": jail_pv["name"],
                    "pv_uid": jail_pv["uid"],
                    "pvc_name": jail_pvc["name"],
                    "pvc_uid": jail_pvc["uid"],
                    "reclaim_policy": "Retain",
                    "local_path": "/mnt/jail",
                },
            },
            "namespace_deleted": False,
            "inspector_namespace_deleted": True,
            "inspector_namespace_identity": inspector_namespace,
            "kubernetes_operations": {
                namespace_key: {
                    "identity": dict(namespace),
                    "state": "absent",
                },
                migration._controller_bridge_resource_journal_key(pvc): {  # noqa: SLF001
                    "identity": dict(pvc),
                    "state": "absent",
                },
                migration._controller_bridge_resource_journal_key(jail_pvc): {  # noqa: SLF001
                    "identity": dict(jail_pvc),
                    "state": "absent",
                },
                inspector_key: {
                    "identity": dict(inspector_namespace),
                    "state": "absent",
                },
            },
            "node_group_operations": {},
            "node_groups_deleted": [],
            "kubernetes_resources_deleted": [],
            "target_state_binding": {},
        },
    }


def _patch_cleanup_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_state: Mapping[str, str],
) -> None:
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_required_gates",
        lambda _checkpoint: None,
    )

    def action_journal(checkpoint: dict[str, Any]) -> dict[str, Any]:
        slurm = checkpoint.setdefault("slurm", {})
        journal = slurm.setdefault("action_journal", new_slurm_action_journal())
        begin_target_slurm_action_generation(journal, authority_epoch="target-epoch-1")
        if journal["broker_mode"] != SLURM_ACTION_ADMISSION_CLOSED:
            set_slurm_action_broker_mode(journal, "accept-only")
            set_slurm_action_broker_mode(journal, SLURM_ACTION_ADMISSION_CLOSED)
            finalize_target_slurm_action_generation(journal)
        return journal

    monkeypatch.setattr(
        migration,
        "_checkpoint_slurm_action_journal_for_job_control",
        action_journal,
    )
    monkeypatch.setattr(
        migration,
        "_drain_external_upgrade_slurm_action_journal",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        migration,
        "slurm_action_partition_restore_blockers",
        lambda _journal: [],
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_action_binding",
        lambda _checkpoint: {
            "action_journal_generation": 0,
            "authority_epoch": "target-epoch-1",
        },
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_require_preservation_binding",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_require_partition_restore_binding",
        lambda _checkpoint: {},
    )
    monkeypatch.setattr(
        migration,
        "_verify_controller_bridge_preservation_jobs",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_cleanup_target_singleton",
        lambda **_kwargs: dict(target_state),
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_target_singleton_before_controller_bridge_cleanup",
        lambda **_kwargs: None,
    )


def _live_bridge_storage(
    journal: Mapping[str, Any],
    *,
    jail_reclaim_policy: str = "Retain",
) -> dict[tuple[str, str], Mapping[str, Any]]:
    records = journal["kubernetes_resources"]
    by_name = {(item["kind"], item["name"]): item for item in records}
    live: dict[tuple[str, str], Mapping[str, Any]] = {}
    for claim_name, local_path, reclaim_policy in (
        ("cxcli-controller-bridge-state", "/mnt/controller-spool", "Retain"),
        ("cxcli-controller-bridge-jail", "/mnt/jail", jail_reclaim_policy),
    ):
        pvc_record = by_name[("PersistentVolumeClaim", claim_name)]
        pv_record = by_name[("PersistentVolume", f"{claim_name}-pv")]
        live[("PersistentVolumeClaim", claim_name)] = {
            "metadata": {"uid": pvc_record["uid"]},
            "spec": {"volumeName": pv_record["name"]},
        }
        live[("PersistentVolume", f"{claim_name}-pv")] = {
            "metadata": {"uid": pv_record["uid"]},
            "spec": {
                "persistentVolumeReclaimPolicy": reclaim_policy,
                "local": {"path": local_path},
                "claimRef": {
                    "namespace": pvc_record["namespace"],
                    "name": pvc_record["name"],
                    "uid": pvc_record["uid"],
                },
            },
        }
    return live


def test_cleanup_storage_proof_binds_both_exact_retain_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _cleanup_resume_journal(stage="partitions-restored")
    live = _live_bridge_storage(journal)

    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **kwargs: (
            True,
            live[(str(kwargs["identity"]["kind"]), str(kwargs["identity"]["name"]))],
        ),
    )

    proof = migration._controller_bridge_cleanup_verify_bridge_storage(  # noqa: SLF001
        journal=journal,
        target_state={"pvc_uid": "target-pvc", "pv_uid": "target-pv"},
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
    )

    assert set(proof) == {"controller_spool", "jail"}
    assert proof["controller_spool"]["local_path"] == "/mnt/controller-spool"
    assert proof["jail"]["reclaim_policy"] == "Retain"


def test_cleanup_storage_proof_rejects_non_retain_jail_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _cleanup_resume_journal(stage="partitions-restored")
    live = _live_bridge_storage(journal, jail_reclaim_policy="Delete")
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **kwargs: (
            True,
            live[(str(kwargs["identity"]["kind"]), str(kwargs["identity"]["name"]))],
        ),
    )

    with pytest.raises(RuntimeError, match="state or Jail Retain"):
        migration._controller_bridge_cleanup_verify_bridge_storage(  # noqa: SLF001
            journal=journal,
            target_state={"pvc_uid": "target-pvc", "pv_uid": "target-pv"},
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
        )


def test_cleanup_dispatches_both_pvcs_then_deletes_namespace_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_state = {
        "pvc_name": "controller-spool-pvc",
        "pvc_uid": "target-pvc-uid",
        "pv_name": "controller-spool-pv",
        "pv_uid": "target-pv-uid",
        "state_path": "/mnt/controller-spool/current",
    }
    journal = _cleanup_resume_journal(stage="partitions-restored")
    journal["cleanup"]["intent_at"] = ""
    records = journal["kubernetes_resources"]
    namespace = next(item for item in records if item["kind"] == "Namespace")
    operations = journal["cleanup"]["kubernetes_operations"]
    operations.pop(migration._controller_bridge_resource_journal_key(namespace))  # noqa: SLF001
    for record in records:
        if record["kind"] == "PersistentVolumeClaim":
            operations.pop(
                migration._controller_bridge_resource_journal_key(record),  # noqa: SLF001
                None,
            )
    _patch_cleanup_prerequisites(monkeypatch, target_state=target_state)
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_verify_bridge_storage",
        lambda **_kwargs: copy.deepcopy(journal["cleanup"]["bridge_storage_bindings"]),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, {"spec": {"replicas": 0}}),
    )
    monkeypatch.setattr(migration, "_json_from_command", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (False, {}),
    )
    deletion_order: list[tuple[str, str, bool]] = []

    def delete_exact(**kwargs: Any) -> None:
        identity = kwargs["identity"]
        deletion_order.append(
            (
                str(identity["kind"]),
                str(identity["name"]),
                bool(kwargs.get("wait_for_absence", True)),
            )
        )

    monkeypatch.setattr(
        migration,
        "_controller_bridge_delete_exact_kubernetes_resource",
        delete_exact,
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_delete_node_groups",
        lambda **_kwargs: ["bridge-a", "bridge-b"],
    )
    monkeypatch.setattr(
        migration, "_controller_bridge_cleanup_prove_absent", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        migration,
        "advance_bridge_stage",
        lambda state, stage: state.update({"stage": stage.value}),
    )

    changed, _lines = migration._cleanup_controller_bridge_after_final_health(  # noqa: SLF001
        checkpoint={"controller_bridge": journal},
        kube_context="context",
        nebius_api=object(),
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: None,
    )

    assert changed is True
    assert journal["cleanup"]["intent_at"]
    assert deletion_order[:7] == [
        ("PersistentVolumeClaim", "cxcli-controller-bridge-state", False),
        ("PersistentVolumeClaim", "cxcli-controller-bridge-jail", False),
        ("Namespace", "cxcli-soperator-upgrade-bridge", True),
        ("PersistentVolumeClaim", "cxcli-controller-bridge-state", True),
        ("PersistentVolumeClaim", "cxcli-controller-bridge-jail", True),
        ("PersistentVolume", "cxcli-controller-bridge-state-pv", True),
        ("PersistentVolume", "cxcli-controller-bridge-jail-pv", True),
    ]


def test_cleanup_resume_rejects_namespace_absence_without_both_pvc_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_state = {
        "pvc_name": "controller-spool-pvc",
        "pvc_uid": "target-pvc-uid",
        "pv_name": "controller-spool-pv",
        "pv_uid": "target-pv-uid",
        "state_path": "/mnt/controller-spool/current",
    }
    journal = _cleanup_resume_journal(stage="partitions-restored")
    jail_pvc = next(
        item
        for item in journal["kubernetes_resources"]
        if item["kind"] == "PersistentVolumeClaim" and item["name"].endswith("jail")
    )
    journal["cleanup"]["kubernetes_operations"].pop(
        migration._controller_bridge_resource_journal_key(jail_pvc)  # noqa: SLF001
    )
    _patch_cleanup_prerequisites(monkeypatch, target_state=target_state)

    with pytest.raises(RuntimeError, match="both exact state and Jail PVC deletions"):
        migration._cleanup_controller_bridge_after_final_health(  # noqa: SLF001
            checkpoint={"controller_bridge": journal},
            kube_context="context",
            nebius_api=object(),
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=lambda: None,
        )


def test_cleanup_resume_after_namespace_absence_skips_predelete_storage_and_workload_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_state = {
        "pvc_name": "controller-spool-pvc",
        "pvc_uid": "target-pvc-uid",
        "pv_name": "controller-spool-pv",
        "pv_uid": "target-pv-uid",
        "state_path": "/mnt/controller-spool/current",
    }
    journal = _cleanup_resume_journal(stage="partitions-restored")
    checkpoint = {"controller_bridge": journal}
    _patch_cleanup_prerequisites(monkeypatch, target_state=target_state)
    monkeypatch.setattr(
        migration,
        "_controller_bridge_cleanup_verify_bridge_storage",
        lambda **_kwargs: pytest.fail("post-namespace resume re-entered predelete storage proof"),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_resource",
        lambda **_kwargs: (False, {}),
    )
    deleted_kinds: list[str] = []

    def delete_exact(**kwargs: Any) -> None:
        deleted_kinds.append(str(kwargs["identity"]["kind"]))

    monkeypatch.setattr(
        migration,
        "_controller_bridge_delete_exact_kubernetes_resource",
        delete_exact,
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_delete_node_groups",
        lambda **_kwargs: ["bridge-a", "bridge-b"],
    )
    absence_proofs = 0

    def prove_absent(**_kwargs: Any) -> None:
        nonlocal absence_proofs
        absence_proofs += 1

    monkeypatch.setattr(migration, "_controller_bridge_cleanup_prove_absent", prove_absent)
    monkeypatch.setattr(
        migration,
        "advance_bridge_stage",
        lambda state, stage: state.update({"stage": stage.value}),
    )

    changed, _lines = migration._cleanup_controller_bridge_after_final_health(  # noqa: SLF001
        checkpoint=checkpoint,
        kube_context="context",
        nebius_api=object(),
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: None,
    )

    assert changed is True
    assert deleted_kinds == [
        "PersistentVolumeClaim",
        "PersistentVolumeClaim",
        "PersistentVolumeClaim",
        "PersistentVolumeClaim",
        "PersistentVolume",
        "PersistentVolume",
        "Namespace",
    ]
    assert journal["cleanup"]["namespace_deleted"] is True
    assert journal["stage"] == "cleaned"
    assert absence_proofs == 1
    assert checkpoint["slurm"]["action_journal"]["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED


def test_cleaned_stage_revalidates_live_absence_before_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_state = {
        "pvc_name": "controller-spool-pvc",
        "pvc_uid": "target-pvc-uid",
        "pv_name": "controller-spool-pv",
        "pv_uid": "target-pv-uid",
        "state_path": "/mnt/controller-spool/current",
    }
    journal = _cleanup_resume_journal(stage="cleaned")
    journal["cleanup"]["target_state_binding"] = dict(target_state)
    action_journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(
        action_journal,
        authority_epoch="target-epoch-1",
    )
    set_slurm_action_broker_mode(action_journal, "accept-only")
    set_slurm_action_broker_mode(action_journal, SLURM_ACTION_ADMISSION_CLOSED)
    finalize_target_slurm_action_generation(action_journal)
    checkpoint = {
        "slurm": {"action_journal": action_journal},
        "controller_bridge": journal,
    }
    _patch_cleanup_prerequisites(monkeypatch, target_state=target_state)
    absence_proofs = 0

    def prove_absent(**_kwargs: Any) -> None:
        nonlocal absence_proofs
        absence_proofs += 1

    monkeypatch.setattr(migration, "_controller_bridge_cleanup_prove_absent", prove_absent)

    changed, _lines = migration._cleanup_controller_bridge_after_final_health(  # noqa: SLF001
        checkpoint=checkpoint,
        kube_context="context",
        nebius_api=object(),
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: None,
    )

    assert changed is False
    assert absence_proofs == 1


def _proof_bound_checkpoint() -> tuple[dict[str, Any], dict[str, Any]]:
    action_journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(
        action_journal,
        authority_epoch="target-epoch-1",
    )
    set_slurm_action_broker_mode(action_journal, "accept-only")
    set_slurm_action_broker_mode(action_journal, SLURM_ACTION_ADMISSION_CLOSED)
    finalize_target_slurm_action_generation(action_journal)
    checkpoint: dict[str, Any] = {
        "slurm": {"action_journal": action_journal},
        "controller_bridge": {
            "authority": {"owner": "target-singleton", "epoch": "target-epoch-1"},
            "preservation_jobs": {"verifications": []},
            "cleanup": {},
        },
    }
    binding = migration._controller_bridge_cleanup_action_binding(checkpoint)  # noqa: SLF001
    checkpoint["final_health"] = {"status": "passed", **binding}
    verification = {
        "stage": "pre-cleanup",
        "status": "verified",
        "jobs": {},
        "verified_at": "2026-07-12T10:00:00Z",
        **binding,
    }
    checkpoint["controller_bridge"]["preservation_jobs"]["verifications"] = [verification]
    checkpoint["controller_bridge"]["cleanup"]["pre_cleanup_preservation_proof"] = {
        "status": "verified",
        "verified_at": "2026-07-12T10:00:00Z",
        "verification_sha256": migration._fingerprint(verification),  # noqa: SLF001
        **binding,
    }
    return checkpoint, action_journal


def test_target_generation_action_preserves_pre_target_proof_but_blocks_cleanup() -> None:
    action_journal = new_slurm_action_journal()
    migration.begin_target_slurm_action_generation(
        action_journal,
        authority_epoch="target-epoch-1",
    )
    frozen = migration.slurm_action_partition_restore_binding(action_journal)
    action = enqueue_slurm_action(
        action_journal,
        kind="wait",
        binding=migration.SlurmJobActionBinding(
            job_id="42",
            user_id="1000",
            submit_time="2026-07-12T09:00:00",
            restart_baseline=0,
            identity_fingerprint="identity-42",
            lineage_fingerprint="lineage-42",
        ),
        intended_postcondition={"controller_observed": True},
    )

    assert migration.slurm_action_partition_restore_binding(action_journal) == frozen
    assert action["authority_generation"] == 1
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="closed target-generation",
    ):
        migration._controller_bridge_cleanup_require_target_action_generation(  # noqa: SLF001
            action_journal
        )


def test_failed_post_proof_final_health_blocks_cleanup() -> None:
    checkpoint, _action_journal = _proof_bound_checkpoint()
    checkpoint["final_health"]["status"] = "failed"

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="fresh final",
    ):
        migration._controller_bridge_cleanup_require_preservation_binding(  # noqa: SLF001
            checkpoint,
            cleanup=checkpoint["controller_bridge"]["cleanup"],
        )


def test_authority_epoch_change_invalidates_cleanup_proofs() -> None:
    checkpoint, _action_journal = _proof_bound_checkpoint()
    checkpoint["controller_bridge"]["authority"]["epoch"] = "target-epoch-2"

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="live target authority epoch",
    ):
        migration._controller_bridge_cleanup_require_preservation_binding(  # noqa: SLF001
            checkpoint,
            cleanup=checkpoint["controller_bridge"]["cleanup"],
        )


def test_pre_cleanup_preservation_proof_is_reverified_not_reused() -> None:
    checkpoint, _action_journal = _proof_bound_checkpoint()
    journal = checkpoint["controller_bridge"]
    journal["preservation_jobs"] = {
        "schema": "nebius-cxcli-slurm-preservation-jobs/v1",
        "capture_state": "complete",
        "captured_at": "2026-07-12T09:00:00Z",
        "jobs": {},
        "verifications": [
            {
                "stage": "pre-cleanup",
                "status": "verified",
                "jobs": {},
                "verified_at": "2026-07-12T09:30:00Z",
                "action_journal_generation": 0,
                "authority_epoch": "target-epoch-1",
            }
        ],
    }
    binding = migration._controller_bridge_cleanup_action_binding(checkpoint)  # noqa: SLF001

    lines = migration._verify_controller_bridge_preservation_jobs(  # noqa: SLF001
        checkpoint=checkpoint,
        journal=journal,
        proof_stage="pre-cleanup",
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: None,
        reuse_existing=False,
        proof_binding=binding,
    )

    proof = journal["preservation_jobs"]["verifications"][0]
    assert "reused" not in lines[0]
    assert proof["verified_at"] != "2026-07-12T09:30:00Z"
    assert proof["action_journal_generation"] == 0
    assert proof["authority_epoch"] == "target-epoch-1"
    assert proof["target_authority_generation"] == 1
    assert proof["target_action_journal_generation"] == 0


def test_partition_restore_keeps_action_admission_open_until_final_gates_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_journal = new_slurm_action_journal()
    checkpoint: dict[str, Any] = {
        "slurm": {"action_journal": action_journal},
        "controller_bridge": {
            "stage": "handoff-validated",
            "cleanup": {},
            "authority": {"owner": "target-singleton", "epoch": "target-epoch-1"},
            "fencing": {
                "bridge": {"proven": True, "processes_absent": True},
            },
        },
    }
    order: list[str] = []
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)

    def drain(**kwargs: Any) -> tuple[Mapping[str, Any], ...]:
        assert kwargs["finalize_unresolved"] is True
        order.append("terminalize-actions")
        return ()

    def gates(_checkpoint: Mapping[str, Any]) -> None:
        assert action_journal["broker_mode"] == "dispatch-enabled"
        assert action_journal["active_authority_generation"] == 1
        order.append("health-gates")
        raise migration.SoperatorMigrationPhasePending("stop after ordering proof")

    monkeypatch.setattr(migration, "_drain_external_upgrade_slurm_action_journal", drain)
    monkeypatch.setattr(migration, "_controller_bridge_cleanup_required_gates", gates)

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="ordering proof"):
        migration._restore_slurm_partitions_before_controller_bridge_cleanup(  # noqa: SLF001
            checkpoint=checkpoint,
            nebius_api=object(),
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=lambda: None,
            slurm_decision_recorder=None,
            final_health_revalidator=lambda _binding: pytest.fail(
                "final health revalidation ran before the initial gates passed"
            ),
        )

    assert order == ["terminalize-actions", "health-gates"]


def test_target_action_finalization_closes_admission_then_drains_accepted_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(action_journal, authority_epoch="target-epoch-1")
    action = enqueue_slurm_action(
        action_journal,
        kind="wait",
        binding=migration.SlurmJobActionBinding(
            job_id="42",
            user_id="1000",
            submit_time="2026-07-12T09:00:00",
            restart_baseline=0,
            identity_fingerprint="identity-42",
            lineage_fingerprint="lineage-42",
        ),
        intended_postcondition={"controller_observed": True},
    )
    checkpoint: dict[str, Any] = {
        "slurm": {"action_journal": action_journal},
        "controller_bridge": {
            "authority": {"owner": "target-singleton", "epoch": "target-epoch-1"},
        },
    }
    drain_modes: list[tuple[str, bool]] = []
    checkpointed_modes: list[str] = []

    def drain(**kwargs: Any) -> tuple[Mapping[str, Any], ...]:
        journal = kwargs["journal"]
        closed_drain = kwargs.get("drain_closed_admission") is True
        drain_modes.append((str(journal["broker_mode"]), closed_drain))
        if closed_drain:
            migration.transition_slurm_action(
                journal,
                action_id=str(action["action_id"]),
                state=migration.SLURM_ACTION_REJECTED,
                result="job completed before final dispatch",
            )
        return ()

    monkeypatch.setattr(migration, "_drain_external_upgrade_slurm_action_journal", drain)

    binding = migration._finalize_controller_bridge_target_actions(  # noqa: SLF001
        checkpoint=checkpoint,
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: checkpointed_modes.append(str(action_journal["broker_mode"])),
    )

    assert drain_modes == [
        ("dispatch-enabled", False),
        (SLURM_ACTION_ADMISSION_CLOSED, True),
    ]
    assert checkpointed_modes[:2] == ["accept-only", SLURM_ACTION_ADMISSION_CLOSED]
    assert action["state"] == migration.SLURM_ACTION_REJECTED
    assert action_journal["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED
    assert binding["action_journal_generation"] == 0
    assert binding["target_authority_generation"] == 1
    assert binding["target_action_journal_generation"] == 2
    migration._controller_bridge_cleanup_require_target_action_generation(  # noqa: SLF001
        action_journal
    )


def test_checkpoint_merge_keeps_intent_accepted_before_final_admission_closure() -> None:
    open_journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(open_journal, authority_epoch="target-epoch-1")
    closing_journal = copy.deepcopy(open_journal)
    accepted_journal = copy.deepcopy(open_journal)
    action = enqueue_slurm_action(
        accepted_journal,
        kind="wait",
        binding=migration.SlurmJobActionBinding(
            job_id="42",
            user_id="1000",
            submit_time="2026-07-12T09:00:00",
            restart_baseline=0,
            identity_fingerprint="identity-42",
            lineage_fingerprint="lineage-42",
        ),
        intended_postcondition={"controller_observed": True},
    )
    set_slurm_action_broker_mode(closing_journal, "accept-only")
    set_slurm_action_broker_mode(closing_journal, SLURM_ACTION_ADMISSION_CLOSED)

    merged = migration._merge_slurm_action_journals(  # noqa: SLF001
        closing_journal,
        accepted_journal,
    )

    assert merged["broker_mode"] == SLURM_ACTION_ADMISSION_CLOSED
    assert [item["action_id"] for item in merged["actions"]] == [action["action_id"]]
    assert merged["actions"][0]["state"] == migration.SLURM_ACTION_QUEUED


def test_checkpoint_merge_rejects_intent_that_loses_to_durable_admission_closure() -> None:
    open_journal = new_slurm_action_journal()
    begin_target_slurm_action_generation(open_journal, authority_epoch="target-epoch-1")
    late_tui_journal = copy.deepcopy(open_journal)
    closed_journal = copy.deepcopy(open_journal)
    set_slurm_action_broker_mode(closed_journal, "accept-only")
    set_slurm_action_broker_mode(closed_journal, SLURM_ACTION_ADMISSION_CLOSED)
    enqueue_slurm_action(
        late_tui_journal,
        kind="wait",
        binding=migration.SlurmJobActionBinding(
            job_id="42",
            user_id="1000",
            submit_time="2026-07-12T09:00:00",
            restart_baseline=0,
            identity_fingerprint="identity-42",
            lineage_fingerprint="lineage-42",
        ),
        intended_postcondition={"controller_observed": True},
    )

    with pytest.raises(RuntimeError, match="closed before the concurrent TUI intent"):
        migration._merge_slurm_action_journals(  # noqa: SLF001
            late_tui_journal,
            closed_journal,
        )
