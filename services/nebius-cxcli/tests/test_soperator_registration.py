from __future__ import annotations

import copy
import inspect

import pytest

import nebius_cxcli.soperator_config_materialization as soperator_config
import nebius_cxcli.soperator_registration_projection as registration_projection
from nebius_cxcli import cli, flux_render
from nebius_cxcli.runtime_validation import validate_runtime_payload
from nebius_cxcli.soperator_registration import (
    SOPERATOR_REGISTRATION_SCHEMA,
    SOPERATOR_REGISTRATION_STATE_SUPPORTED,
    soperator_registration_fingerprint,
    soperator_registration_is_accepted,
    validate_soperator_registration,
)
from soperator_fixtures import sample_infrastructure_receipt


def _registered_payload() -> dict:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "deploy": {
            "targets": [
                {
                    "instance_id": "existing-a",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "cluster_id": "mk8scluster-a",
                    "access": "external",
                    "project_id": "project-a",
                    "inventory": {
                        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
                        "node_groups": {"worker-a": {"node_count": 1}},
                    },
                    "soperator_registration": {
                        "schema": SOPERATOR_REGISTRATION_SCHEMA,
                        "accepted": True,
                        "state": SOPERATOR_REGISTRATION_STATE_SUPPORTED,
                        "source_version": "4.1.7",
                        "source_repository": "https://github.com/nebius/soperator",
                        "source_tag": "4.1.7",
                        "source_commit": "a" * 40,
                        "source_tree": "b" * 40,
                        "source_archive_sha256": "sha256:" + "c" * 64,
                        "source_manifest_sha256": "sha256:" + "d" * 64,
                        "source_contract": "upstream-flux-v1",
                        "source_capability_sha256": "sha256:" + "e" * 64,
                        "provenance_method": "official-render-equivalence",
                        "live_manifest_sha256": "sha256:" + "f" * 64,
                        "rendered_manifest_sha256": "sha256:" + "f" * 64,
                        "owned_object_graph_sha256": "sha256:" + "1" * 64,
                        "protected_storage_receipt_sha256": "sha256:" + "2" * 64,
                        "collection_errors": [],
                        "analysis_fingerprint": "0" * 64,
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "existing-a",
                    "enabled": True,
                    "namespace": "flux-system",
                    "release-name": "soperator-fluxcd",
                    "version": "4.1.7",
                    "values": {},
                }
            ]
        },
    }
    registration = payload["deploy"]["targets"][0]["soperator_registration"]
    registration["analysis_fingerprint"] = soperator_registration_fingerprint(
        payload,
        target_ref="existing-a",
    )
    return payload


def test_exact_official_registration_is_the_only_registration_authority() -> None:
    payload = _registered_payload()

    assert soperator_registration_is_accepted(payload, target_ref="existing-a")
    validate_soperator_registration(payload, target_ref="existing-a")
    validate_runtime_payload(payload)


def test_unsupported_registration_schema_fails_without_migration_guidance() -> None:
    payload = _registered_payload()
    payload["deploy"]["targets"][0]["soperator_registration"]["schema"] = (
        "nebius-cxcli.soperator-registration.unsupported"
    )

    with pytest.raises(ValueError, match="schema is unsupported") as exc_info:
        validate_runtime_payload(payload)
    assert "onboard" not in str(exc_info.value).lower()

    with pytest.raises(ValueError, match="unsupported or invalid") as exc_info:
        validate_soperator_registration(payload, target_ref="existing-a")
    assert "onboard" not in str(exc_info.value).lower()


def test_registration_has_no_upgrade_target_or_retired_workflow_state() -> None:
    payload = _registered_payload()
    registration = payload["deploy"]["targets"][0]["soperator_registration"]

    assert "target_version" not in registration
    assert "upgrade_path" not in registration
    assert "storage_mode" not in registration
    assert "compute_mode" not in registration
    assert "migration_profile_id" not in registration


def test_registered_source_release_renders_only_the_release_neutral_handoff() -> None:
    payload = _registered_payload()

    assert flux_render._registered_source_release_is_handoff(
        payload,
        target_ref="existing-a",
        release="4.1.7",
    )
    assert not flux_render._registered_source_release_is_handoff(
        payload,
        target_ref="existing-a",
        release="4.1.6",
    )
    assert flux_render._configured_app_release_specs(payload) == []


@pytest.mark.parametrize(
    "field",
    [
        "source_version",
        "source_contract",
        "source_commit",
        "provenance_method",
        "owned_object_graph_sha256",
        "protected_storage_receipt_sha256",
    ],
)
def test_registration_fingerprint_fails_closed_on_source_identity_drift(field: str) -> None:
    payload = _registered_payload()
    changed = copy.deepcopy(payload)
    changed["deploy"]["targets"][0]["soperator_registration"][field] = "changed"

    assert not soperator_registration_is_accepted(changed, target_ref="existing-a")
    with pytest.raises(ValueError, match="unsupported or invalid"):
        validate_soperator_registration(changed, target_ref="existing-a")


def test_registration_fingerprint_allows_desired_app_version_transition() -> None:
    payload = _registered_payload()
    payload["apps"]["charts"][0]["version"] = "4.2.0"

    assert soperator_registration_is_accepted(payload, target_ref="existing-a")
    assert payload["deploy"]["targets"][0]["soperator_registration"]["source_version"] == "4.1.7"


def test_runtime_rejects_historical_workflow_fields() -> None:
    payload = _registered_payload()
    payload["deploy"]["targets"][0]["soperator_registration"]["upgrade_path"] = {}

    with pytest.raises(ValueError, match="unsupported field.*upgrade_path"):
        validate_runtime_payload(payload)


def test_registration_projects_only_live_declarative_topology() -> None:
    snapshot = {
        "node_groups": {
            "system-a": {
                "labels": {"slurm.nebius.ai/nodeset": "system"},
            },
            "worker-a": {
                "labels": {"slurm.nebius.ai/nodeset": "worker-gpu"},
            },
        },
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "customer-cluster"},
                "spec": {
                    "partitionConfiguration": {
                        "configType": "structured",
                        "partitions": [
                            {
                                "name": "gpu",
                                "nodeSetRefs": ["worker-gpu"],
                                "privateNote": "must-not-copy",
                            },
                        ],
                        "privateMetadata": "must-not-copy",
                    },
                },
                "status": {"phase": "Available"},
            },
            {
                "kind": "NodeSet",
                "metadata": {"name": "worker-gpu"},
                "spec": {
                    "gpu": {"enabled": True},
                    "nodeConfig": {"features": ["gpu", "customer-feature"]},
                    "slurmd": {
                        "image": {"repository": "source-only.invalid/worker"},
                        "customEnv": [{"name": "INLINE_SECRET", "value": "must-not-copy"}],
                        "resources": {"gpu": 8},
                        "volumes": {
                            "customVolumeMounts": [
                                {
                                    "name": "source-only",
                                    "mountPath": "/customer/private",
                                }
                            ]
                        },
                    },
                    "customInitContainers": [
                        {
                            "name": "source-only",
                            "env": [{"name": "TOKEN", "value": "must-not-copy"}],
                        }
                    ],
                    "workerAnnotations": {"customer.example/private": "must-not-copy"},
                },
            },
            {
                "kind": "Secret",
                "metadata": {"name": "must-not-enter-config"},
                "data": {"password": "redacted"},
            },
        ],
    }

    values, profile = cli._soperator_registration_adoption_values_from_snapshot(snapshot)

    assert values == {
        "clusterName": "customer-cluster",
        "nodesets": [
            {
                "gpu": {"enabled": True},
                "name": "worker-gpu",
                "nodeConfig": {"features": ["gpu", "customer-feature"]},
                "slurmd": {"resources": {"gpu": 8}},
            }
        ],
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [{"name": "gpu", "nodeSetRefs": ["worker-gpu"]}],
        },
        "placements": {"system": ["system-a"], "worker-gpu": ["worker-a"]},
    }
    assert profile in {"", "nebius-gpu-v1"}
    assert "must-not-enter-config" not in repr(values)
    assert "must-not-copy" not in repr(values)
    assert "source-only" not in repr(values)


def test_registration_normalizes_pre_nodeset_worker_topology() -> None:
    snapshot = {
        "node_groups": {
            "worker-group": {
                "labels": {"slurm.nebius.ai/nodeset": "worker-0"},
            },
        },
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "customer-cluster"},
                "spec": {
                    "slurmNodes": {
                        "worker": {
                            "size": 2,
                            "maxUnavailable": "20%",
                            "enableGDRCopy": True,
                            "priorityClass": "worker-priority",
                            "slurmNodeExtra": "must-not-copy",
                            "slurmd": {
                                "image": "source-only.invalid/worker",
                                "resources": {
                                    "cpu": "16",
                                    "memory": "48Gi",
                                    "nvidia.com/gpu": "1",
                                },
                            },
                        },
                    },
                },
                "status": {"phase": "Available"},
            },
        ],
    }

    values, profile = cli._soperator_registration_adoption_values_from_snapshot(snapshot)

    assert values == {
        "clusterName": "customer-cluster",
        "nodesets": [
            {
                "gpu": {
                    "enabled": True,
                    "nvidia": {"gdrCopyEnabled": True},
                },
                "maxUnavailable": "20%",
                "name": "worker-0",
                "priorityClass": "worker-priority",
                "replicas": 2,
                "slurmd": {
                    "resources": {
                        "cpu": "16",
                        "memory": "48Gi",
                        "nvidia.com/gpu": "1",
                    },
                },
            },
        ],
        "placements": {"worker-0": ["worker-group"]},
    }
    assert profile == "nebius-gpu-v1"
    assert "source-only.invalid" not in repr(values)
    assert "must-not-copy" not in repr(values)


def test_registration_preserves_optional_service_topology_without_source_payloads() -> None:
    snapshot = {
        "node_groups": {
            "worker-group": {
                "labels": {"slurm.nebius.ai/nodeset": "worker-0"},
            },
        },
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "customer-cluster"},
                "spec": {
                    "slurmNodes": {
                        "accounting": {"enabled": True, "protectedSecret": True},
                        "exporter": {
                            "enabled": False,
                            "size": 1,
                            "image": "source-only.invalid/exporter",
                        },
                        "rest": {
                            "enabled": True,
                            "size": 2,
                            "k8sNodeFilterName": "system",
                            "threadCount": 3,
                            "maxConnections": 10,
                            "rest": {"image": "source-only.invalid/rest"},
                            "customInitContainers": [{"name": "source-only"}],
                        },
                        "worker": {
                            "size": 2,
                            "slurmd": {"resources": {"nvidia.com/gpu": "1"}},
                        },
                    }
                },
                "status": {"phase": "Available"},
            }
        ],
    }

    values, _profile = cli._soperator_registration_adoption_values_from_snapshot(snapshot)

    assert values["slurmNodes"] == {
        "accounting": {"enabled": True},
        "exporter": {"enabled": False, "size": 1},
        "rest": {
            "enabled": True,
            "size": 2,
            "k8sNodeFilterName": "system",
            "threadCount": 3,
            "maxConnections": 10,
        },
    }
    assert "source-only.invalid" not in repr(values)
    assert "source-only" not in repr(values)


def test_registration_reads_optional_service_topology_from_discovery_section() -> None:
    kubernetes = {
        "slurmclusters": [
            {
                "spec": {
                    "slurmNodes": {
                        "accounting": {"enabled": True, "secretRef": "excluded"},
                        "exporter": {"enabled": False, "size": 1},
                        "rest": {"enabled": True, "size": 2},
                    }
                },
                "status": {"phase": "Available"},
            }
        ]
    }

    assert (
        registration_projection.soperator_registration_optional_service_topology_from_discovery(
            kubernetes
        )
        == {
            "accounting": {"enabled": True},
            "exporter": {"enabled": False, "size": 1},
            "rest": {"enabled": True, "size": 2},
        }
    )


def test_registration_replaces_profile_partition_defaults_with_live_topology() -> None:
    row = {
        "values": {
            "partitionConfiguration": {
                "configType": "structured",
                "partitions": [{"name": "gpu", "nodeSetRefs": ["worker"]}],
            }
        }
    }

    changed = cli._merge_soperator_registration_adoption_values(
        row,
        {"partitionConfiguration": {"configType": "default"}},
    )

    assert changed
    assert row["values"]["partitionConfiguration"] == {"configType": "default"}


def test_registration_materialization_compiles_live_default_partitions_for_static_nodesets() -> None:
    payload = _registered_payload()
    payload["deploy"]["targets"][0]["inventory"]["node_groups"]["worker-a"] = {
        "node_count": 1,
        "allocatable": {
            "cpu": "15950m",
            "memory": "60Gi",
            "nvidia.com/gpu": "1",
        },
        "selector": {
            "key": "nebius.com/node-group-id",
            "operator": "In",
            "values": ["worker-group-id"],
        },
    }
    row = payload["apps"]["charts"][0]
    row["profile"] = "nebius-gpu-v1"
    row["placements"] = {"worker-0": ["worker-a"]}
    row["values"] = {
        "nodesets": [
            {
                "name": "worker-0",
                "replicas": 2,
                "gpu": {"enabled": True},
                "nodeConfig": {"features": ["gpu", "customer-feature"]},
                "nodeSelector": {"slurm.nebius.ai/nodeset-name": "worker"},
                "slurmd": {
                    "resources": {
                        "cpu": "13950m",
                        "memory": "54Gi",
                        "nvidia.com/gpu": "1",
                    }
                },
            }
        ],
        "partitionConfiguration": {"configType": "default"},
        "slurmNodes": {
            "rest": {"enabled": True, "k8sNodeFilterName": "system"}
        },
    }

    soperator_config._materialize_soperator_component_defaults(payload)
    soperator_config._materialize_soperator_render_only_values(payload)

    assert row["values"]["partitionConfiguration"] == {
        "configType": "structured",
        "partitions": [
            {
                "name": "main",
                "isAll": True,
                "policy": {
                    "default": True,
                    "state": "UP",
                    "maxTime": "INFINITE",
                    "priorityTier": 10,
                    "overSubscribe": "YES",
                },
            },
            {
                "name": "hidden",
                "isAll": True,
                "policy": {
                    "default": False,
                    "hidden": True,
                    "state": "UP",
                    "maxTime": "INFINITE",
                    "priorityTier": 10,
                    "preemptMode": "OFF",
                    "overSubscribe": "YES",
                },
            },
        ],
    }
    assert row["values"]["nodesets"][0]["nodeSelector"] == {
        "nebius.com/node-group-id": "worker-group-id"
    }
    worker = row["values"]["nodesets"][0]
    assert worker["slurmd"]["resources"]["gpu"] == 1
    assert "nvidia.com/gpu" not in worker["slurmd"]["resources"]
    assert worker["nodeConfig"]["gresConfig"] == [
        "AutoDetect=off Name=gpu File=/dev/nvidia[0]"
    ]
    assert "Gres=gpu:1" in worker["nodeConfig"]["static"]
    assert worker["workerAnnotations"] == {
        soperator_config._SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION: (
            soperator_config._soperator_registered_static_config_revision(row["values"])
        )
    }
    assert {
        "name": "NVIDIA_DRIVER_CAPABILITIES",
        "value": "compute,graphics,utility,video",
    } in worker["slurmd"]["customEnv"]
    assert {
        "name": soperator_config._SOPERATOR_STATIC_CONFIG_REVISION_ENV,
        "value": soperator_config._soperator_registered_static_config_revision(
            row["values"]
        ),
    } in worker["slurmd"]["customEnv"]
    assert worker["slurmd"]["volumes"]["customVolumeMounts"] == [
        copy.deepcopy(item)
        for item in soperator_config._SOPERATOR_REGISTERED_RUNTIME_MOUNTS
    ]
    assert row["values"]["slurmNodes"]["rest"]["enabled"] is True
    assert row["values"]["slurmNodes"]["rest"]["k8sNodeFilterName"] == "system"
    assert (
        row["values"]["slurmNodes"]["controller"]["openMetrics"]["enabled"] is False
    )


def test_registration_runtime_mount_materialization_rejects_reserved_path_collision() -> None:
    values = {
        "nodesets": [
            {
                "name": "worker-0",
                "slurmd": {
                    "volumes": {
                        "customVolumeMounts": [
                            {
                                "name": "customer-volume",
                                "mountPath": "/opt/slurm_scripts/",
                                "volumeSource": {"emptyDir": {}},
                            }
                        ]
                    }
                },
            }
        ]
    }

    with pytest.raises(ValueError, match="runtime mount slurm-scripts"):
        soperator_config._materialize_soperator_registered_runtime_mounts(values)


def test_activechecks_cleanup_preserves_a_frozen_customer_hidden_partition() -> None:
    frozen = {
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [
                {
                    "name": "hidden",
                    "isAll": True,
                    "config": "Hidden=YES PriorityTier=10 State=DOWN",
                }
            ],
        }
    }

    assert not soperator_config._remove_internal_activechecks_partition(frozen)
    assert frozen["partitionConfiguration"]["partitions"][0]["name"] == "hidden"

    generated = copy.deepcopy(frozen)
    generated["partitionConfiguration"]["partitions"] = [
        soperator_config._soperator_internal_activechecks_partition()
    ]
    assert soperator_config._remove_internal_activechecks_partition(generated)
    assert generated["partitionConfiguration"]["partitions"] == []


def test_registered_all_node_partition_preimage_suppresses_profile_partitions() -> None:
    values = {
        "nodesets": [{"name": "worker-0"}],
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [
                {"name": "main", "isAll": True, "config": "State=DOWN"},
                {"name": "background", "isAll": True, "config": "State=DOWN"},
            ],
        },
    }

    assert soperator_config._soperator_preserve_onboarded_worker_nodesets(
        values=values,
        install_mode=soperator_config._SOPERATOR_TARGET_MODE_REGISTERED,
    )


def test_registration_rejects_conflicting_adopted_gpu_resource_aliases() -> None:
    payload = _registered_payload()
    payload["deploy"]["targets"][0]["inventory"]["node_groups"]["worker-a"] = {
        "node_count": 1,
        "allocatable": {"nvidia.com/gpu": "2"},
        "selector": {
            "key": "nebius.com/node-group-id",
            "operator": "In",
            "values": ["worker-group-id"],
        },
    }
    row = payload["apps"]["charts"][0]
    row["profile"] = "nebius-gpu-v1"
    row["placements"] = {"worker-0": ["worker-a"]}
    row["values"] = {
        "nodesets": [
            {
                "name": "worker-0",
                "gpu": {"enabled": True},
                "slurmd": {"resources": {"gpu": 2, "nvidia.com/gpu": 1}},
            }
        ],
        "partitionConfiguration": {"configType": "default"},
    }

    soperator_config._materialize_soperator_component_defaults(payload)
    with pytest.raises(ValueError, match="conflicting GPU counts"):
        soperator_config._materialize_soperator_render_only_values(payload)


def test_local_sfs_bindings_exclude_dynamic_compute_disks() -> None:
    snapshot = {
        "pvcs": [
            {
                "metadata": {"name": "jail-pvc", "namespace": "soperator"},
                "spec": {"volumeName": "jail-pv"},
            },
            {
                "metadata": {"name": "storage-accounting-0", "namespace": "soperator"},
                "spec": {"volumeName": "dynamic-accounting-pv"},
            },
        ],
        "pvs": [
            {
                "metadata": {"name": "jail-pv"},
                "spec": {
                    "local": {"path": "/mnt/jail/data"},
                    "persistentVolumeReclaimPolicy": "Retain",
                },
            },
            {
                "metadata": {"name": "dynamic-accounting-pv"},
                "spec": {
                    "csi": {"volumeHandle": "computedisk-accounting"},
                    "persistentVolumeReclaimPolicy": "Delete",
                },
            },
        ],
    }

    assert cli._soperator_local_sfs_kubernetes_bindings(snapshot) == {
        "jail": {"pv_names": ("jail-pv",), "pvc_names": ("jail-pvc",)}
    }


def test_registered_storage_discovery_uses_node_group_sfs_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "collection_errors": [],
        "node_groups": {"mk8snodegroup-a": {}, "mk8snodegroup-b": {}},
        "pvcs": [
            {
                "metadata": {"name": "jail-pvc", "namespace": "soperator"},
                "spec": {"volumeName": "jail-pv"},
            }
        ],
        "pvs": [
            {
                "metadata": {"name": "jail-pv"},
                "spec": {
                    "local": {"path": "/mnt/jail/data"},
                    "persistentVolumeReclaimPolicy": "Retain",
                },
            }
        ],
    }
    monkeypatch.setattr(
        cli,
        "collect_kubectl_soperator_snapshot",
        lambda **_kwargs: copy.deepcopy(snapshot),
    )

    result = cli._soperator_protected_storage_discovery_inputs(
        payload=_registered_payload(),
        target_ref="existing-a",
        chart_values={},
        kube_context="ctx",
        extra_env={},
    )

    assert result == {
        "storage_kind": "sfs",
        "sfs_node_group_ids": ("mk8snodegroup-a", "mk8snodegroup-b"),
        "sfs_kubernetes_bindings": {"jail": {"pv_names": ("jail-pv",), "pvc_names": ("jail-pvc",)}},
    }


def test_recovery_storage_discovery_reuses_admitted_identity_without_fresh_snapshot() -> None:
    fresh_calls: list[str] = []
    infrastructure = sample_infrastructure_receipt()

    result = cli._soperator_storage_discovery_inputs_for_admission(
        infrastructure=infrastructure,
        project_id=infrastructure.project_id,
        cluster_id=infrastructure.nebius_cluster_id,
        kubernetes_uid=infrastructure.kubernetes_uid,
        discover_fresh=lambda: fresh_calls.append("fresh") or {},
    )

    assert fresh_calls == []
    assert result == {
        "storage_kind": "sfs",
        "sfs_filesystems": (
            {
                "role": "jail",
                "filesystem_id": "filesystem-jail",
                "mount_tag": "cluster-a-jail",
                "node_group_ids": ("nodes-controller", "nodes-worker"),
                "pv_names": ("pv-jail",),
                "pvc_names": ("jail-rootfs",),
            },
        ),
    }


def test_recovery_storage_discovery_rejects_admitted_target_drift() -> None:
    infrastructure = sample_infrastructure_receipt()

    with pytest.raises(RuntimeError, match="admitted infrastructure identity differs"):
        cli._soperator_storage_discovery_inputs_for_admission(
            infrastructure=infrastructure,
            project_id=infrastructure.project_id,
            cluster_id="different-cluster",
            kubernetes_uid=infrastructure.kubernetes_uid,
            discover_fresh=lambda: {},
        )


def test_new_admission_storage_discovery_still_requires_fresh_inventory() -> None:
    fresh_result = {"storage_kind": "sfs", "sfs_node_group_ids": ("nodes-a",)}

    assert cli._soperator_storage_discovery_inputs_for_admission(
        infrastructure=None,
        project_id="project-a",
        cluster_id="cluster-a",
        kubernetes_uid="kubernetes-a",
        discover_fresh=lambda: fresh_result,
    ) is fresh_result


@pytest.mark.parametrize("forbid_deletion", [False, True])
def test_configured_sfs_discovery_accepts_optional_provider_choice(
    monkeypatch: pytest.MonkeyPatch,
    forbid_deletion: bool,
) -> None:
    payload = _registered_payload()
    payload["infra"]["components"] = [
        {
            "id": "sfs",
            "instance_id": "existing-a",
            "enabled": True,
            "inputs": {
                "filesystems": {
                    "jail": {
                        "name": "existing-a-jail",
                        "mount_tag": "jail",
                        "forbid_deletion": forbid_deletion,
                    }
                }
            },
        }
    ]
    monkeypatch.setattr(
        cli,
        "collect_kubectl_soperator_snapshot",
        lambda **_kwargs: {
            "collection_errors": [],
            "node_groups": {"mk8snodegroup-a": {}},
            "pvcs": [
                {
                    "metadata": {"name": "jail-pvc", "namespace": "soperator"},
                    "spec": {"volumeName": "jail-pv"},
                }
            ],
            "pvs": [
                {
                    "metadata": {"name": "jail-pv"},
                    "spec": {"csi": {"volumeHandle": "filesystem-jail"}},
                }
            ],
        },
    )

    result = cli._soperator_protected_storage_discovery_inputs(
        payload=payload,
        target_ref="existing-a",
        chart_values={},
        kube_context="ctx",
        extra_env={},
    )

    assert result == {
        "storage_kind": "sfs",
        "sfs_filesystems": (
            {
                "role": "jail",
                "filesystem_id": "filesystem-jail",
                "mount_tag": "jail",
                "node_group_ids": ("mk8snodegroup-a",),
                "pv_names": ["jail-pv"],
                "pvc_names": ["jail-pvc"],
            },
        ),
    }


def test_registration_rejects_ambiguous_pre_nodeset_worker_placement() -> None:
    snapshot = {
        "node_groups": {
            "worker-a": {"labels": {"slurm.nebius.ai/nodeset": "worker-a"}},
            "worker-b": {"labels": {"slurm.nebius.ai/nodeset": "worker-b"}},
        },
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "customer-cluster"},
                "spec": {"slurmNodes": {"worker": {"size": 2}}},
                "status": {"phase": "Available"},
            },
        ],
    }

    with pytest.raises(RuntimeError, match="exactly one live worker placement"):
        cli._soperator_registration_adoption_values_from_snapshot(snapshot)


def test_registered_nodeset_render_uses_target_profile_without_source_payload() -> None:
    values = {
        "nodesets": [
            {
                "gpu": {"enabled": True},
                "name": "worker-customer",
                "nodeConfig": {"features": ["gpu", "customer-feature"]},
                "slurmd": {"resources": {"gpu": 8}},
            }
        ]
    }

    soperator_config._soperator_hydrate_registered_nodesets_from_profile(
        values,
        profile=soperator_config._soperator_nodesets_profiles()[1]["nebius-gpu-v1"],
    )

    nodeset = values["nodesets"][0]
    assert nodeset["name"] == "worker-customer"
    assert nodeset["nodeConfig"]["features"] == ["gpu", "customer-feature"]
    assert nodeset["slurmd"]["resources"]["gpu"] == 8
    assert nodeset["slurmd"]["volumes"]["jail"]["persistentVolumeClaim"]["claimName"]
    assert nodeset["munge"]["resources"]
    assert "source-only.invalid" not in repr(nodeset)
    assert "must-not-copy" not in repr(nodeset)


def test_registration_rejects_ambiguous_live_slurmclusters() -> None:
    snapshot = {
        "soperator_resources": [
            {"kind": "SlurmCluster", "metadata": {"name": "one"}},
            {"kind": "SlurmCluster", "metadata": {"name": "two"}},
        ]
    }

    with pytest.raises(RuntimeError, match="exactly one authoritative"):
        cli._soperator_registration_adoption_values_from_snapshot(snapshot)


def test_onboard_persists_registration_before_rendering_upgrade_handoff() -> None:
    source = inspect.getsource(cli._register_existing_soperator_target)

    assert "with SoperatorOperationLocalLock(config_lock_path):" in source
    assert source.index("ensure_mk8s_gpu_app_rows(") < source.index(
        "_selection_change_issues(next_payload)"
    )
    assert source.index("materialize_mk8s_gpu_app_values(") < source.index(
        "_selection_change_issues(next_payload)"
    )
    assert source.index("_write_runtime_payload_config(") < source.index(
        "_run_internal_render_command(config_path, force=True)"
    )
