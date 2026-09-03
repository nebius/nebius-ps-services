from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from nebius_cxcli.soperator_spool_migration import (
    SOPERATOR_CONTROLLER_SPOOL_MIGRATION_SCHEMA,
    SoperatorControllerSpoolMigration,
)


class _FakeCluster:
    def __init__(self) -> None:
        self.deployment: dict[str, Any] = {
            "metadata": {
                "uid": "deployment-uid",
                "resourceVersion": "10",
                "generation": 1,
                "labels": {
                    "app.kubernetes.io/version": "4.1.7",
                    "app.kubernetes.io/managed-by": "Helm",
                },
            },
            "spec": {"replicas": 1},
            "status": {
                "observedGeneration": 1,
                "replicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
            },
        }
        self.slurm_cluster: dict[str, Any] = {
            "metadata": {"uid": "cluster-uid", "resourceVersion": "20"},
            "spec": {
                "volumeSources": [
                    {
                        "name": "controller-spool",
                        "persistentVolumeClaim": {
                            "claimName": "controller-spool-pvc",
                            "readOnly": False,
                        },
                    }
                ],
                "slurmNodes": {
                    "controller": {
                        "volumes": {
                            "spool": {"volumeSourceName": "controller-spool"}
                        }
                    }
                },
            },
        }
        self.statefulset: dict[str, Any] = {
            "metadata": {
                "uid": "statefulset-uid",
                "resourceVersion": "30",
                "ownerReferences": [
                    {
                        "apiVersion": "slurm.nebius.ai/v1",
                        "controller": True,
                        "kind": "SlurmCluster",
                        "name": "soperator",
                        "uid": "cluster-uid",
                    }
                ],
            },
            "spec": {
                "replicas": 1,
                "volumeClaimUpdateStrategy": {"type": "OnPodRollingUpdate"},
                "volumeClaimTemplates": [
                    {"metadata": {"name": "controller-spool"}}
                ],
                "template": {
                    "spec": {
                        "volumes": [
                            {
                                "name": "controller-spool",
                                "persistentVolumeClaim": {
                                    "claimName": "controller-spool-pvc"
                                },
                            }
                        ]
                    }
                },
            },
        }
        self.controller_pod: dict[str, Any] = {
            "metadata": {
                "name": "controller-0",
                "uid": "controller-pod-uid",
                "resourceVersion": "35",
                "labels": {},
                "ownerReferences": [
                    {
                        "apiVersion": "apps.kruise.io/v1beta1",
                        "controller": True,
                        "kind": "StatefulSet",
                        "name": "controller",
                        "uid": "statefulset-uid",
                    }
                ],
            },
            "spec": {
                "volumes": [
                    {
                        "name": "controller-spool",
                        "persistentVolumeClaim": {
                            "claimName": "controller-spool-pvc"
                        },
                    }
                ]
            },
            "status": {
                "initContainerStatuses": [
                    {
                        "name": "mount-gate-controller-spool",
                        "state": {"terminated": {"exitCode": 0}},
                    }
                ]
            },
        }
        self.source_pvc: dict[str, Any] = {
            "metadata": {"uid": "source-pvc-uid", "resourceVersion": "40"},
            "spec": {"volumeName": "source-pv"},
            "status": {"phase": "Bound"},
        }
        self.target_pvc: dict[str, Any] = {
            "metadata": {"uid": "target-pvc-uid", "resourceVersion": "50"},
            "spec": {"volumeName": "target-pv"},
            "status": {"phase": "Bound"},
        }
        self.source_pv: dict[str, Any] = {
            "metadata": {"uid": "source-pv-uid", "resourceVersion": "60"},
            "spec": {
                "persistentVolumeReclaimPolicy": "Delete",
                "claimRef": {
                    "namespace": "soperator",
                    "name": "controller-spool-controller-0",
                    "uid": "source-pvc-uid",
                },
            },
        }
        self.statefulset_patches: list[list[dict[str, Any]]] = []
        self.pod_patches: list[list[dict[str, Any]]] = []
        self.statefulset_resource_reads: list[str] = []
        self.pending_statefulset_status_update = False
        self.webhook_endpoint_reads = 0
        self.webhook_endpoints: dict[str, Any] = {
            "metadata": {"uid": "webhook-endpoints-uid", "resourceVersion": "70"},
            "subsets": [{"addresses": [{"ip": "10.0.0.10"}]}],
        }

    @staticmethod
    def _result(payload: dict[str, Any], *, returncode: int = 0):
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="" if returncode == 0 else "rejected",
        )

    def __call__(self, command: tuple[str, ...], **_kwargs: object):
        args = list(command[3:])
        namespace = ""
        if args[:2] == ["--namespace", "soperator"]:
            namespace = "soperator"
            args = args[2:]
        elif args[:2] == ["--namespace", "soperator-system"]:
            namespace = "soperator-system"
            args = args[2:]
        if args[0] == "get":
            resource, name = args[1:3]
            if namespace == "soperator-system" and resource == "deployment":
                return self._result(copy.deepcopy(self.deployment))
            if namespace == "soperator-system" and resource == "endpoints":
                assert name == "soperator-controller-webhook-service"
                self.webhook_endpoint_reads += 1
                return self._result(copy.deepcopy(self.webhook_endpoints))
            if namespace == "soperator" and resource == "statefulsets.apps.kruise.io":
                self.statefulset_resource_reads.append(resource)
                if self.pending_statefulset_status_update:
                    self.statefulset["metadata"]["resourceVersion"] = str(
                        int(self.statefulset["metadata"]["resourceVersion"]) + 1
                    )
                    self.pending_statefulset_status_update = False
                return self._result(copy.deepcopy(self.statefulset))
            if namespace == "soperator" and resource == "slurmcluster.slurm.nebius.ai":
                return self._result(copy.deepcopy(self.slurm_cluster))
            if namespace == "soperator" and resource == "pods":
                assert args[2:] == [
                    "--field-selector",
                    "metadata.name=controller-0",
                    "-o",
                    "json",
                ]
                return self._result(
                    {
                        "apiVersion": "v1",
                        "kind": "PodList",
                        "items": [copy.deepcopy(self.controller_pod)],
                    }
                )
            if namespace == "soperator" and resource == "pod":
                assert name == "controller-0"
                return self._result(copy.deepcopy(self.controller_pod))
            if namespace == "soperator" and resource == "pvc":
                payload = (
                    self.source_pvc
                    if name == "controller-spool-controller-0"
                    else self.target_pvc
                )
                return self._result(copy.deepcopy(payload))
            if not namespace and resource == "pv" and name == "source-pv":
                return self._result(copy.deepcopy(self.source_pv))
            raise AssertionError(command)
        if args[0] != "patch":
            raise AssertionError(command)
        resource, name = args[1:3]
        operations = json.loads(args[args.index("-p") + 1])
        if not namespace and resource == "pv":
            assert name == "source-pv"
            self.source_pv["spec"]["persistentVolumeReclaimPolicy"] = "Retain"
            self.source_pv["metadata"]["resourceVersion"] = "61"
            return self._result(copy.deepcopy(self.source_pv))
        if namespace == "soperator-system" and resource == "deployment":
            replicas = operations[-1]["value"]
            self.deployment["spec"]["replicas"] = replicas
            self.deployment["metadata"]["generation"] += 1
            self.deployment["metadata"]["resourceVersion"] = str(
                int(self.deployment["metadata"]["resourceVersion"]) + 1
            )
            self.deployment["status"] = {
                "observedGeneration": self.deployment["metadata"]["generation"],
                "replicas": replicas,
                "readyReplicas": replicas,
                "availableReplicas": replicas,
            }
            self.webhook_endpoints["subsets"] = (
                [{"addresses": [{"ip": "10.0.0.10"}]}] if replicas else []
            )
            return self._result(copy.deepcopy(self.deployment))
        if namespace == "soperator" and resource == "statefulsets.apps.kruise.io":
            self.statefulset_patches.append(operations)
            resource_version_test = next(
                (
                    item
                    for item in operations
                    if item["op"] == "test"
                    and item["path"] == "/metadata/resourceVersion"
                ),
                None,
            )
            if (
                resource_version_test is not None
                and resource_version_test["value"]
                != self.statefulset["metadata"]["resourceVersion"]
            ):
                return self._result({}, returncode=1)
            if any(item["path"] == "/spec/volumeClaimUpdateStrategy" for item in operations):
                self.statefulset["spec"]["volumeClaimUpdateStrategy"] = {
                    "type": "OnDelete"
                }
                self.pending_statefulset_status_update = True
            elif any(item["path"] == "/spec/volumeClaimTemplates/0" for item in operations):
                if (
                    self.statefulset["spec"]["volumeClaimUpdateStrategy"]["type"]
                    != "OnDelete"
                ):
                    return self._result({}, returncode=1)
                self.statefulset["spec"]["volumeClaimTemplates"] = []
            else:
                raise AssertionError(operations)
            self.statefulset["metadata"]["resourceVersion"] = str(
                int(self.statefulset["metadata"]["resourceVersion"]) + 1
            )
            return self._result(copy.deepcopy(self.statefulset))
        if namespace == "soperator" and resource == "pod":
            assert name == "controller-0"
            self.pod_patches.append(operations)
            response = copy.deepcopy(self.controller_pod)
            response["metadata"]["labels"]["apps.kruise.io/specified-delete"] = "true"
            self.controller_pod["metadata"]["uid"] = "replacement-controller-pod-uid"
            self.controller_pod["metadata"]["resourceVersion"] = "36"
            self.controller_pod["metadata"]["labels"] = {}
            self.controller_pod["spec"]["volumes"] = [
                {
                    "name": "controller-spool",
                    "persistentVolumeClaim": {
                        "claimName": "controller-spool-pvc"
                    },
                }
            ]
            self.controller_pod["status"]["initContainerStatuses"] = [
                {
                    "name": "mount-gate-controller-spool",
                    "state": {"terminated": {"exitCode": 0}},
                }
            ]
            return self._result(response)
        raise AssertionError(command)


def _migration(
    cluster: _FakeCluster,
    journal: dict[str, Any],
) -> SoperatorControllerSpoolMigration:
    def _read():
        return copy.deepcopy(journal.get("value"))

    def _write(payload):
        journal["value"] = copy.deepcopy(dict(payload))

    return SoperatorControllerSpoolMigration(
        runner=cluster,
        kube_context="fixture",
        operation_id="sha256:" + "a" * 64,
        target_release="4.1.7",
        target_pvc_name="controller-spool-pvc",
        assert_authority=lambda: object(),
        read_state=_read,
        write_state=_write,
        timeout_seconds=2,
        poll_interval_seconds=0.001,
    )


def test_controller_spool_migration_is_two_step_checkpointed_and_non_deleting() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    migration = _migration(cluster, journal)

    prepared = migration.prepare()

    assert prepared["status"] == "prepared"
    assert cluster.statefulset_resource_reads
    assert set(cluster.statefulset_resource_reads) == {"statefulsets.apps.kruise.io"}
    assert cluster.source_pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
    assert cluster.deployment["spec"]["replicas"] == 1
    assert cluster.webhook_endpoint_reads >= 1
    assert cluster.statefulset["spec"]["volumeClaimTemplates"] == []
    assert len(cluster.statefulset_patches) == 2
    assert cluster.statefulset_patches[0][-1] == {
        "op": "add",
        "path": "/spec/volumeClaimUpdateStrategy",
        "value": {"type": "OnDelete"},
    }
    assert cluster.statefulset_patches[1][-1] == {
        "op": "remove",
        "path": "/spec/volumeClaimTemplates/0",
    }
    assert {
        "op": "test",
        "path": "/spec/volumeClaimUpdateStrategy/type",
        "value": "OnDelete",
    } in cluster.statefulset_patches[1]
    assert not any(
        item["path"] == "/metadata/resourceVersion"
        for item in cluster.statefulset_patches[1]
    )
    assert journal["value"]["schema"] == SOPERATOR_CONTROLLER_SPOOL_MIGRATION_SCHEMA
    assert journal["value"]["status"] == "prepared"
    assert journal["value"]["lastCompletedStep"] == (
        "controller-restored-before-release-open"
    )

    migration.finish(prepared, release_opened=True)

    assert cluster.deployment["spec"]["replicas"] == 1
    assert journal["value"]["status"] == "complete"
    assert journal["value"]["releaseOpened"] is True
    assert cluster.source_pvc["metadata"]["uid"] == "source-pvc-uid"
    assert cluster.target_pvc["metadata"]["uid"] == "target-pvc-uid"


def test_controller_spool_migration_recovers_prepared_quiesced_state() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    first = _migration(cluster, journal)
    first.prepare()
    webhook_reads_after_first_prepare = cluster.webhook_endpoint_reads

    resumed = _migration(cluster, journal)
    prepared = resumed.prepare()
    resumed.finish(prepared, release_opened=True)

    assert len(cluster.statefulset_patches) == 2
    assert cluster.deployment["spec"]["replicas"] == 1
    assert cluster.webhook_endpoint_reads > webhook_reads_after_first_prepare
    assert journal["value"]["status"] == "complete"


def test_controller_spool_migration_removes_template_reintroduced_before_release_open() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    migration = _migration(cluster, journal)
    prepared = migration.prepare()
    cluster.statefulset["spec"]["volumeClaimTemplates"] = [
        {"metadata": {"name": "controller-spool"}}
    ]
    cluster.controller_pod["spec"]["volumes"] = [
        {
            "name": "controller-spool",
            "persistentVolumeClaim": {
                "claimName": "controller-spool-controller-0"
            },
        }
    ]
    cluster.controller_pod["status"]["initContainerStatuses"] = [
        {
            "name": "mount-gate-controller-spool",
            "state": {"terminated": {"exitCode": 1}},
        }
    ]

    migration.finish(prepared, release_opened=True)

    assert cluster.statefulset["spec"]["volumeClaimTemplates"] == []
    assert cluster.controller_pod["spec"]["volumes"][0][
        "persistentVolumeClaim"
    ]["claimName"] == "controller-spool-pvc"
    assert cluster.controller_pod["status"]["initContainerStatuses"][0]["state"] == {
        "terminated": {"exitCode": 0}
    }
    assert cluster.pod_patches == [
        [
            {
                "op": "test",
                "path": "/metadata/uid",
                "value": "controller-pod-uid",
            },
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": "35",
            },
            {
                "op": "add",
                "path": "/metadata/labels/apps.kruise.io~1specified-delete",
                "value": "true",
            },
        ]
    ]
    assert journal["value"]["status"] == "complete"
    assert journal["value"]["lastCompletedStep"] == (
        "target-release-opened-and-controller-restored"
    )


def test_completed_spool_migration_reopens_reintroduced_template_for_finish() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    first = _migration(cluster, journal)
    prepared = first.prepare()
    first.finish(prepared, release_opened=True)
    cluster.statefulset["spec"]["volumeClaimTemplates"] = [
        {"metadata": {"name": "controller-spool"}}
    ]

    resumed = _migration(cluster, journal)
    resumed_prepared = resumed.prepare()

    assert resumed_prepared["status"] == "prepared"
    assert journal["value"]["status"] == "prepared"
    assert journal["value"]["lastCompletedStep"] == (
        "post-open-legacy-template-reintroduced"
    )

    resumed.finish(resumed_prepared, release_opened=True)
    assert cluster.statefulset["spec"]["volumeClaimTemplates"] == []
    assert journal["value"]["status"] == "complete"


def test_completed_spool_migration_rechecks_after_main_release_reopens() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    first = _migration(cluster, journal)
    prepared = first.prepare()
    first.finish(prepared, release_opened=True)

    resumed = _migration(cluster, journal)
    resumed_prepared = resumed.prepare()
    assert resumed_prepared["status"] == "already-complete"

    cluster.statefulset["spec"]["volumeClaimTemplates"] = [
        {"metadata": {"name": "controller-spool"}}
    ]
    cluster.controller_pod["spec"]["volumes"] = [
        {
            "name": "controller-spool",
            "persistentVolumeClaim": {
                "claimName": "controller-spool-controller-0"
            },
        }
    ]
    cluster.controller_pod["status"]["initContainerStatuses"] = [
        {
            "name": "mount-gate-controller-spool",
            "state": {"terminated": {"exitCode": 1}},
        }
    ]

    resumed.finish(resumed_prepared, release_opened=True)

    assert cluster.statefulset["spec"]["volumeClaimTemplates"] == []
    assert cluster.controller_pod["spec"]["volumes"][0][
        "persistentVolumeClaim"
    ]["claimName"] == "controller-spool-pvc"
    assert cluster.controller_pod["status"]["initContainerStatuses"][0]["state"] == {
        "terminated": {"exitCode": 0}
    }
    assert len(cluster.pod_patches) == 1
    assert journal["value"]["status"] == "complete"


def test_controller_spool_migration_refuses_recreation_for_wrong_pod_owner() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    migration = _migration(cluster, journal)
    prepared = migration.prepare()
    cluster.statefulset["spec"]["volumeClaimTemplates"] = [
        {"metadata": {"name": "controller-spool"}}
    ]
    cluster.controller_pod["metadata"]["ownerReferences"][0]["uid"] = "other-owner"
    cluster.controller_pod["spec"]["volumes"][0]["persistentVolumeClaim"][
        "claimName"
    ] = "controller-spool-controller-0"

    with pytest.raises(RuntimeError, match="owner is not the exact"):
        migration.finish(prepared, release_opened=True)

    assert cluster.pod_patches == []


def test_controller_spool_migration_fails_closed_on_retained_pvc_identity_drift() -> None:
    cluster = _FakeCluster()
    journal: dict[str, Any] = {}
    migration = _migration(cluster, journal)
    migration.prepare()
    cluster.source_pvc["metadata"]["uid"] = "replacement-pvc-uid"

    with pytest.raises(RuntimeError, match="bind|identity changed"):
        _migration(cluster, journal).prepare()
