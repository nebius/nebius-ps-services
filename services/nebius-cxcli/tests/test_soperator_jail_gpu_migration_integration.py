from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.oci_image import OCIImageResolution
from nebius_cxcli.soperator_jail_gpu_validation import (
    build_jail_gpu_post_population_resources,
)


def _gpu_values() -> dict[str, Any]:
    return {
        "nodesets": [
            {
                "name": "worker-gpu",
                "replicas": 1,
                "gpu": {"enabled": True},
            }
        ]
    }


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        tuple(args),
        returncode,
        stdout,
        stderr,
    )


def _populate_image_resolution(
    source: str,
    *,
    index_hex: str = "a",
    platform_hex: str = "b",
) -> OCIImageResolution:
    repository, tag = source.rsplit(":", 1)
    return OCIImageResolution(
        source=source,
        repository=repository,
        tag=tag,
        index_digest="sha256:" + index_hex * 64,
        platform_digest="sha256:" + platform_hex * 64,
        os="linux",
        architecture="amd64",
        variant="",
        media_type="application/vnd.oci.image.index.v1+json",
    )


def _pre_activation_gpu_worker(index: int) -> dict[str, Any]:
    nodeset = f"worker-gpu-{'a' if index == 0 else 'b'}"
    return {
        "metadata": {
            "name": f"{nodeset}-0",
            "namespace": "soperator",
            "uid": f"pod-uid-{index}",
            "labels": {"slurm.nebius.ai/nodeset-name": nodeset},
        },
        "spec": {"nodeName": f"gpu-node-{index}"},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _pre_activation_gpu_node(index: int) -> dict[str, Any]:
    nodeset = f"worker-gpu-{'a' if index == 0 else 'b'}"
    return {
        "metadata": {
            "name": f"gpu-node-{index}",
            "uid": f"node-uid-{index}",
            "resourceVersion": "7",
            "labels": {
                "nebius.com/node-group-id": f"gpu-group-id-{index}",
                "nebius.com/node-group": f"training-worker-{index}",
                "slurm.nebius.ai/nodeset-name": nodeset,
            },
        },
        "spec": {},
        "status": {
            "allocatable": {"nvidia.com/gpu": "8"},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _pre_activation_gpu_checkpoint() -> dict[str, Any]:
    target_groups: dict[str, Any] = {}
    for index in range(2):
        nodeset = f"worker-gpu-{'a' if index == 0 else 'b'}"
        group_id = f"gpu-group-id-{index}"
        group_name = f"training-worker-{index}"
        target_groups[f"worker-{index}"] = {
            "id": group_id,
            "name": group_name,
            "compute_role": "worker",
            "nodeset_name": nodeset,
            "gpu": True,
            "fixed_node_count": 1,
            "replacement_binding": {
                "replacement_node_group_id": group_id,
                "replacement_node_group_name": group_name,
            },
        }
    return {"phase_state": {"rolling-compute-migration": {"target_node_groups": target_groups}}}


def _pre_activation_image_lock() -> dict[str, str]:
    digest = "sha256:" + "d" * 64
    repository = "registry.example.invalid/populate-jail"
    return {
        "immutable_reference": f"{repository}@{digest}",
        "platform_digest": digest,
    }


def _install_pre_activation_gpu_fleet_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe_job(**kwargs: Any) -> dict[str, Any]:
        node = dict(kwargs["node"])
        runner = kwargs["command_runner"]
        second = str(node["node"]) == "gpu-node-1"
        driver = getattr(runner, "second_driver", "550.90.07") if second else "550.90.07"
        libcuda = getattr(runner, "second_libcuda_sha256", "a" * 64) if second else "a" * 64
        return {
            **node,
            "status": "passed",
            "probe_kind": "node-probe-job",
            "probe_stage": kwargs["stage"],
            "job": f"probe-{node['node']}-{kwargs['stage']}",
            "job_uid": f"probe-uid-{node['node']}-{kwargs['stage']}",
            "gpu_count": 8,
            "driver_version": driver,
            "source_library_sha256": {
                "libcuda.so.1": libcuda,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }

    monkeypatch.setattr(migration, "_ensure_gpu_fleet_probe_job", probe_job)


def _pre_activation_gpu_fleet_runner(
    *,
    second_driver: str = "550.90.07",
    second_libcuda_sha256: str = "a" * 64,
) -> migration.SoperatorMigrationCommandRunner:
    pods = [_pre_activation_gpu_worker(0), _pre_activation_gpu_worker(1)]
    nodes = {
        str(node["metadata"]["name"]): node
        for node in (_pre_activation_gpu_node(0), _pre_activation_gpu_node(1))
    }

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        if command[5:7] == ("get", "pods"):
            return _result(command, stdout=json.dumps({"items": pods}))
        if command[3:5] == ("get", "nodes"):
            selector = command[command.index("-l") + 1]
            group_id = selector.split("=", 1)[1]
            selected = [
                node
                for node in nodes.values()
                if node["metadata"]["labels"]["nebius.com/node-group-id"] == group_id
            ]
            return _result(command, stdout=json.dumps({"items": selected}))
        if command[3:5] == ("get", "node"):
            return _result(command, stdout=json.dumps(nodes[command[5]]))
        if command[5:7] == ("get", "pod"):
            pod_name = command[7]
            pod = next(item for item in pods if item["metadata"]["name"] == pod_name)
            return _result(command, stdout=json.dumps(pod))
        if "exec" in command and migration._GPU_PRE_ACTIVATION_FLEET_SOURCE_PROBE in command:
            pod_name = command[command.index("exec") + 1]
            second = pod_name.startswith("worker-gpu-b-")
            return _result(
                command,
                stdout=(
                    "gpu_count=8 driver_version="
                    + (second_driver if second else "550.90.07")
                    + " libcuda_sha256="
                    + (second_libcuda_sha256 if second else "a" * 64)
                    + " libnvidia_ml_sha256="
                    + "b" * 64
                    + "\n"
                ),
            )
        raise AssertionError(command)

    runner.second_driver = second_driver  # type: ignore[attr-defined]
    runner.second_libcuda_sha256 = second_libcuda_sha256  # type: ignore[attr-defined]
    return runner


def test_external_jail_gpu_post_population_gate_applies_and_binds_exact_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = "registry.example.invalid/populate-jail:4.0.2-slurm25.11.3-cuda12.9.0"
    runner_source = "registry.example.invalid/controller_slurmctld:4.0.2-slurm25.11.3"
    resolution = _populate_image_resolution(runner_source)
    image = resolution.immutable_reference
    desired = build_jail_gpu_post_population_resources(
        namespace="soperator",
        target_ref="training",
        runner_image=image,
        passive_pvc="jail-rootfs-slot-b-pvc",
    )
    resources: dict[str, dict[str, Any]] = {}

    def get_resource(**kwargs: Any) -> tuple[bool, Mapping[str, Any]]:
        resource = str(kwargs["resource"])
        payload = resources.get(resource)
        return (payload is not None, copy.deepcopy(payload or {}))

    def apply_objects(*, objects: Sequence[Mapping[str, Any]], **_kwargs: Any) -> None:
        for item in objects:
            payload = copy.deepcopy(dict(item))
            metadata = payload.setdefault("metadata", {})
            name = str(metadata["name"])
            kind = str(payload["kind"])
            metadata["uid"] = "cm-uid" if kind == "ConfigMap" else "job-uid"
            metadata["resourceVersion"] = "resource-version"
            if kind == "Job":
                payload["status"] = {
                    "conditions": [{"type": "Complete", "status": "True"}],
                    "succeeded": 1,
                }
            resources[f"{kind.lower()}/{name}"] = payload

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    monkeypatch.setattr(migration, "_kubectl_apply_objects", apply_objects)
    monkeypatch.setattr(migration, "_wait_for_job_complete_or_failed", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_jail_gpu_post_population_job_pod",
        lambda **_kwargs: {
            "pod_uid": "pod-uid",
            "node_name": "gpu-node-0",
            "configured_image": image,
            "resolved_image_digest": resolution.platform_digest,
            "image_id": image,
        },
    )

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if str(args[-1]).startswith("job/cxcli-gpu-jail-ready-"):
            return _result(
                args,
                stdout="gpu-jail-legacy-ready-marker: status=passed driver=550.90.07\n",
            )
        assert tuple(args[-2:]) == ("logs", f"job/{desired.job['metadata']['name']}")
        return _result(
            args,
            stdout=(
                "gpu-jail-post-population: driver=550.90.07 visible_gpus=8 status=passed\n"
                "gpu-jail-source-evidence: driver=550.90.07 libcuda_sha256="
                + "b" * 64
                + " libnvidia_ml_sha256="
                + "c" * 64
                + "\n"
            ),
        )

    phase: dict[str, Any] = {}
    writes: list[dict[str, Any]] = []
    lines = migration._ensure_jail_gpu_post_population_gate(
        phase=phase,
        command_runner=runner,
        kube_context="ctx",
        target_ref="training",
        values=_gpu_values(),
        populate_image=source_image,
        runner_image_lock=resolution.as_payload(),
        passive_pvc="jail-rootfs-slot-b-pvc",
        populate_job={
            "uid": "populate-job-uid",
            "pvc_uid": "passive-pvc-uid",
            "pvc": "jail-rootfs-slot-b-pvc",
            "image": source_image,
        },
        checkpoint_writer=lambda: writes.append(copy.deepcopy(phase)),
    )

    gate = phase["gpu_post_population"]
    assert gate["status"] == "passed"
    assert gate["binding"]["source_populate_job_uid"] == "populate-job-uid"
    assert gate["binding"]["image"] == source_image
    assert gate["binding"]["runner_image"] == image
    assert gate["binding"]["runner_image_index_digest"] == resolution.index_digest
    assert gate["binding"]["runner_image_platform_digest"] == resolution.platform_digest
    assert gate["image_lock"] == resolution.as_payload()
    assert gate["resource_uids"] == {
        "config_map_uid": "cm-uid",
        "job_uid": "job-uid",
    }
    assert gate["driver_version"] == "550.90.07"
    assert gate["visible_gpu_count"] == 8
    assert gate["source_library_sha256"] == {
        "libcuda.so.1": "b" * 64,
        "libnvidia-ml.so.1": "c" * 64,
    }
    assert len(gate["log_sha256"]) == 64
    assert gate["pod"]["pod_uid"] == "pod-uid"
    assert gate["legacy_ready_marker"]["status"] == "verified"
    marker_jobs = [
        item
        for key, item in resources.items()
        if key.startswith("job/cxcli-gpu-jail-ready-")
    ]
    assert len(marker_jobs) == 1
    marker_container = marker_jobs[0]["spec"]["template"]["spec"]["containers"][0]
    assert marker_container["resources"] == {}
    assert "etc/gpu_libs_installed.flag" in marker_container["command"][2]
    assert "gpu-driver-jail.env" in marker_container["command"][2]
    applied_job = resources[f"job/{desired.job['metadata']['name']}"]
    applied_container = applied_job["spec"]["template"]["spec"]["containers"][0]
    assert applied_container["image"] == image
    assert (
        applied_job["metadata"]["annotations"]["nebius.ai/cxcli-runner-image-index-digest"]
        == resolution.index_digest
    )
    assert (
        applied_job["metadata"]["annotations"]["nebius.ai/cxcli-runner-image-platform-digest"]
        == resolution.platform_digest
    )
    assert writes
    assert "passive rootfs" in lines[0]


def test_external_jail_gpu_post_population_gate_never_replaces_missing_uid_bound_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = "registry.example.invalid/populate-jail:4.0.2-slurm25.11.3-cuda12.9.0"
    runner_source = "registry.example.invalid/controller_slurmctld:4.0.2-slurm25.11.3"
    resolution = _populate_image_resolution(runner_source, index_hex="c", platform_hex="d")
    image = resolution.immutable_reference
    gpu_scheduling = migration._jail_gpu_post_population_scheduling(_gpu_values())
    desired = build_jail_gpu_post_population_resources(
        namespace="soperator",
        target_ref="training",
        runner_image=image,
        passive_pvc="jail-rootfs-slot-b-pvc",
        scheduling=gpu_scheduling,
    )
    desired_job = copy.deepcopy(dict(desired.job))
    desired_job["spec"]["template"]["spec"]["containers"][0]["command"] = [
        "/bin/bash",
        "-c",
        migration._JAIL_GPU_POST_POPULATION_EVIDENCE_WRAPPER,
    ]
    desired_config = copy.deepcopy(dict(desired.config_map))
    annotations = {
        "nebius.ai/cxcli-source-populate-job-uid": "source-job",
        "nebius.ai/cxcli-source-populate-pvc-uid": "pvc-uid",
        "nebius.ai/cxcli-passive-pvc": "jail-rootfs-slot-b-pvc",
        "nebius.ai/cxcli-populate-image-sha256": hashlib.sha256(source_image.encode()).hexdigest(),
        "nebius.ai/cxcli-runner-image-index-digest": resolution.index_digest,
        "nebius.ai/cxcli-runner-image-platform-digest": resolution.platform_digest,
    }
    desired_config["metadata"].setdefault("annotations", {}).update(annotations)
    desired_job["metadata"].setdefault("annotations", {}).update(annotations)
    desired_job["spec"]["template"].setdefault("metadata", {}).setdefault("annotations", {}).update(
        annotations
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )
    phase = {
        "gpu_post_population": {
            "image_lock": resolution.as_payload(),
            "binding": {
                "source_populate_job_uid": "source-job",
                "source_populate_pvc_uid": "pvc-uid",
                "passive_pvc": "jail-rootfs-slot-b-pvc",
                "image": source_image,
                "runner_image": image,
                "runner_image_index_digest": resolution.index_digest,
                "runner_image_platform_digest": resolution.platform_digest,
                "script_sha256": desired.script_sha256,
                "scheduling_sha256": migration._fingerprint(gpu_scheduling),
                "config_map": desired.config_map["metadata"]["name"],
                "job": desired.job["metadata"]["name"],
                "config_contract_sha256": migration._fingerprint(
                    migration._jail_gpu_post_population_resource_contract(desired_config)
                ),
                "job_contract_sha256": migration._fingerprint(
                    migration._jail_gpu_post_population_resource_contract(desired_job)
                ),
            },
            "resource_uids": {"config_map_uid": "cm-uid", "job_uid": "job-uid"},
        }
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="refusing to replace its immutable evidence identity",
    ):
        migration._ensure_jail_gpu_post_population_gate(
            phase=phase,
            command_runner=lambda args, **_kwargs: _result(args),
            kube_context="ctx",
            target_ref="training",
            values=_gpu_values(),
            populate_image=source_image,
            runner_image_lock=resolution.as_payload(),
            passive_pvc="jail-rootfs-slot-b-pvc",
            populate_job={
                "uid": "source-job",
                "pvc_uid": "pvc-uid",
                "pvc": "jail-rootfs-slot-b-pvc",
                "image": source_image,
            },
            checkpoint_writer=None,
        )


def test_external_jail_gpu_post_population_recovers_unbound_failed_populate_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = "registry.example.invalid/populate-jail:4.0.2-slurm25.11.3-cuda12.9.0"
    old_resolution = _populate_image_resolution(source_image, index_hex="1", platform_hex="2")
    runner_source = "registry.example.invalid/controller_slurmctld:4.0.2-slurm25.11.3"
    runner_resolution = _populate_image_resolution(runner_source, index_hex="3", platform_hex="4")
    scheduling = migration._failed_populate_runner_gpu_post_population_scheduling(
        _gpu_values(),
        priority_class_name="soperator-worker-high-priority",
    )
    old_resources = build_jail_gpu_post_population_resources(
        namespace="soperator",
        target_ref="training",
        runner_image=old_resolution.immutable_reference,
        passive_pvc="jail-rootfs-slot-b-pvc",
        scheduling=scheduling,
    )
    old_config = copy.deepcopy(dict(old_resources.config_map))
    old_job = copy.deepcopy(dict(old_resources.job))
    migration._restore_failed_populate_runner_gpu_job_contract(
        old_job,
        priority_class_name="soperator-worker-high-priority",
    )
    old_job["spec"]["template"]["spec"]["containers"][0]["command"] = [
        "/bin/bash",
        "-c",
        migration._JAIL_GPU_POST_POPULATION_EVIDENCE_WRAPPER,
    ]
    old_annotations = {
        "nebius.ai/cxcli-source-populate-job-uid": "populate-job-uid",
        "nebius.ai/cxcli-source-populate-pvc-uid": "passive-pvc-uid",
        "nebius.ai/cxcli-passive-pvc": "jail-rootfs-slot-b-pvc",
        "nebius.ai/cxcli-populate-image-sha256": hashlib.sha256(source_image.encode()).hexdigest(),
        "nebius.ai/cxcli-populate-image-index-digest": old_resolution.index_digest,
        "nebius.ai/cxcli-populate-image-platform-digest": old_resolution.platform_digest,
    }
    for manifest in (old_config, old_job):
        manifest["metadata"].setdefault("annotations", {}).update(old_annotations)
    old_job["spec"]["template"].setdefault("metadata", {}).setdefault("annotations", {}).update(
        old_annotations
    )
    old_config_contract_sha256 = migration._fingerprint(
        migration._jail_gpu_post_population_resource_contract(old_config)
    )
    old_job_contract_sha256 = migration._fingerprint(
        migration._jail_gpu_post_population_resource_contract(old_job)
    )
    old_job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"][
        "nvidia.com/gpu"
    ] = "1"
    old_config["metadata"].update({"uid": "old-cm-uid", "resourceVersion": "10"})
    old_job["metadata"].update({"uid": "old-job-uid", "resourceVersion": "11"})
    old_job["status"] = {
        "conditions": [{"type": "Failed", "status": "True"}],
        "failed": 1,
    }
    old_pod_spec = old_job["spec"]["template"]["spec"]
    old_container = old_pod_spec["containers"][0]
    assert old_pod_spec.get("runtimeClassName") is None
    assert old_pod_spec["priorityClassName"] == "soperator-worker-high-priority"
    assert old_container.get("env") is None
    assert old_container["resources"] == {"limits": {"nvidia.com/gpu": "1"}}
    config_name = str(old_config["metadata"]["name"])
    job_name = str(old_job["metadata"]["name"])
    resources: dict[str, dict[str, Any]] = {
        f"configmap/{config_name}": old_config,
        f"job/{job_name}": old_job,
    }

    def get_resource(**kwargs: Any) -> tuple[bool, Mapping[str, Any]]:
        payload = resources.get(str(kwargs["resource"]))
        return (payload is not None, copy.deepcopy(payload or {}))

    def apply_objects(*, objects: Sequence[Mapping[str, Any]], **_kwargs: Any) -> None:
        for item in objects:
            payload = copy.deepcopy(dict(item))
            metadata = payload.setdefault("metadata", {})
            kind = str(payload["kind"])
            metadata["uid"] = "new-cm-uid" if kind == "ConfigMap" else "new-job-uid"
            metadata["resourceVersion"] = "new-resource-version"
            if kind == "Job":
                payload["status"] = {
                    "conditions": [{"type": "Complete", "status": "True"}],
                    "succeeded": 1,
                }
            resources[f"{kind.lower()}/{metadata['name']}"] = payload

    deleted: list[tuple[str, str]] = []

    def delete_resource(*, api_path: str, uid: str, **_kwargs: Any) -> None:
        deleted.append((api_path, uid))
        key = f"job/{job_name}" if "/jobs/" in api_path else f"configmap/{config_name}"
        resources.pop(key)

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    monkeypatch.setattr(migration, "_kubectl_apply_objects", apply_objects)
    monkeypatch.setattr(
        migration, "_delete_kubernetes_resource_with_uid_precondition", delete_resource
    )
    monkeypatch.setattr(migration, "_wait_for_job_complete_or_failed", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_jail_gpu_post_population_job_pod",
        lambda **_kwargs: {
            "pod_uid": "new-pod-uid",
            "node_name": "gpu-node-0",
            "configured_image": runner_resolution.immutable_reference,
            "resolved_image_digest": runner_resolution.platform_digest,
            "image_id": runner_resolution.immutable_reference,
        },
    )
    phase: dict[str, Any] = {
        "gpu_post_population": {
            "status": "intent-recorded",
            "image_lock": old_resolution.as_payload(),
            "binding": {
                "source_populate_job_uid": "populate-job-uid",
                "source_populate_pvc_uid": "passive-pvc-uid",
                "passive_pvc": "jail-rootfs-slot-b-pvc",
                "image": source_image,
                "resolved_image": old_resolution.immutable_reference,
                "image_index_digest": old_resolution.index_digest,
                "image_platform_digest": old_resolution.platform_digest,
                "script_sha256": old_resources.script_sha256,
                "scheduling_sha256": migration._fingerprint(scheduling),
                "config_map": config_name,
                "job": job_name,
                "config_contract_sha256": old_config_contract_sha256,
                "job_contract_sha256": old_job_contract_sha256,
            },
        }
    }

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if str(args[-1]).startswith("job/cxcli-gpu-jail-ready-"):
            return _result(
                args,
                stdout="gpu-jail-legacy-ready-marker: status=passed driver=550.90.07\n",
            )
        return _result(
            args,
            stdout=(
                "gpu-jail-post-population: driver=550.90.07 visible_gpus=8 status=passed\n"
                "gpu-jail-source-evidence: driver=550.90.07 libcuda_sha256="
                + "b" * 64
                + " libnvidia_ml_sha256="
                + "c" * 64
                + "\n"
            ),
        )

    lines = migration._ensure_jail_gpu_post_population_gate(
        phase=phase,
        command_runner=runner,
        kube_context="ctx",
        target_ref="training",
        values=_gpu_values(),
        populate_image=source_image,
        runner_image_lock=runner_resolution.as_payload(),
        passive_pvc="jail-rootfs-slot-b-pvc",
        populate_job={
            "uid": "populate-job-uid",
            "pvc_uid": "passive-pvc-uid",
            "pvc": "jail-rootfs-slot-b-pvc",
            "image": source_image,
        },
        checkpoint_writer=lambda: None,
    )

    assert [uid for _path, uid in deleted] == ["old-job-uid", "old-cm-uid"]
    assert phase["gpu_post_population_failed_intent_recovery"]["status"] == (
        "replaced-before-switch"
    )
    assert phase["gpu_post_population"]["status"] == "passed"
    assert phase["gpu_post_population"]["image_lock"] == runner_resolution.as_payload()
    assert "passive rootfs" in lines[0]


def test_gpu_post_population_image_lock_rejects_checkpoint_drift_without_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = "registry.example.invalid/controller_slurmctld:4.0.2-slurm25.11.3"
    gate: dict[str, Any] = {"image_lock": _populate_image_resolution(source_image).as_payload()}
    gate["image_lock"]["architecture"] = "arm64"
    monkeypatch.setattr(
        migration,
        "resolve_oci_image",
        lambda *_args, **_kwargs: pytest.fail("drifted checkpoint must not be re-resolved"),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="image lock is incomplete or differs",
    ):
        migration._checkpoint_jail_gpu_post_population_image_lock(
            gate=gate,
            runner_image_lock=_populate_image_resolution(source_image).as_payload(),
            checkpoint_writer=None,
        )


def test_gpu_post_population_result_pod_requires_exact_platform_image_id() -> None:
    source_image = "registry.example.invalid/populate-jail:4.0.2"
    resolution = _populate_image_resolution(source_image)
    pod = {
        "metadata": {
            "uid": "pod-uid",
            "ownerReferences": [
                {
                    "kind": "Job",
                    "name": "gpu-job",
                    "uid": "job-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "nodeName": "gpu-node-0",
            "containers": [
                {
                    "name": "gpu-jail-post-population",
                    "image": resolution.immutable_reference,
                }
            ],
        },
        "status": {
            "phase": "Succeeded",
            "containerStatuses": [
                {
                    "name": "gpu-jail-post-population",
                    "imageID": (
                        "docker-pullable://registry.example.invalid/populate-jail@"
                        + resolution.platform_digest
                    ),
                }
            ],
        },
    }

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(args, stdout=json.dumps({"items": [pod]}))

    evidence = migration._jail_gpu_post_population_job_pod(
        command_runner=runner,
        kube_context="ctx",
        namespace="soperator",
        job_name="gpu-job",
        job_uid="job-uid",
        image_lock=resolution.as_payload(),
    )
    assert evidence["configured_image"] == resolution.immutable_reference
    assert evidence["resolved_image_digest"] == resolution.platform_digest

    pod["status"]["containerStatuses"][0]["imageID"] = (
        "docker-pullable://registry.example.invalid/populate-jail@sha256:" + "f" * 64
    )
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="imageID did not equal",
    ):
        migration._jail_gpu_post_population_job_pod(
            command_runner=runner,
            kube_context="ctx",
            namespace="soperator",
            job_name="gpu-job",
            job_uid="job-uid",
            image_lock=resolution.as_payload(),
        )


def test_external_jail_gpu_temporary_partition_is_root_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def exec_login_once(*, args: Sequence[str], **_kwargs: Any) -> Any:
        selected = tuple(args)
        calls.append(selected)
        if selected[:3] == ("scontrol", "show", "partition") and len(calls) == 1:
            return _result(selected, returncode=1, stderr="invalid partition")
        if selected[:2] == ("scontrol", "create"):
            return _result(selected)
        return _result(
            selected,
            stdout=(f"PartitionName={selected[3]} Nodes=worker-gpu-0 RootOnly=YES State=UP\n"),
        )

    monkeypatch.setattr(migration, "_kubectl_exec_login_once", exec_login_once)
    state: dict[str, Any] = {}

    partition = migration._ensure_jail_gpu_temporary_partition(
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="ctx",
        state=state,
        slurm_node="worker-gpu-0",
        checkpoint_writer=None,
    )

    create = next(call for call in calls if call[:2] == ("scontrol", "create"))
    assert partition.startswith("cxcli-gpu-")
    assert "RootOnly=YES" in create
    assert "Default=NO" in create
    assert "State=UP" in create
    assert state["partition_status"] == "created"


def test_external_jail_gpu_post_population_uses_gpu_nodeset_scheduling() -> None:
    values = _gpu_values()
    values["nodesets"][0].update(
        {
            "nodeSelector": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
            "tolerations": [
                {
                    "key": "nvidia.com/gpu",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
            "priorityClass": "soperator-worker-high-priority",
        }
    )

    scheduling = migration._jail_gpu_post_population_scheduling(values)

    assert scheduling == {
        "nodeSelector": {
            "slurm.nebius.ai/nodeset-name": "worker-gpu",
            "kubernetes.io/os": "linux",
            "kubernetes.io/arch": "amd64",
        },
        "tolerations": [
            {
                "key": "nvidia.com/gpu",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ],
    }


def test_gpu_pre_activation_fleet_uses_accepted_in_place_group_identities() -> None:
    checkpoint = {
        "compute_migration": {"mode": migration.COMPUTE_MIGRATION_MODE_IN_PLACE},
        "operation_intent": {
            "intended_postcondition": {
                "mk8s": {
                    "node_groups": [
                        {
                            "id": "gpu-group-id-0",
                            "name": "worker-gpu-a-0",
                            "role": "worker-gpu-a",
                            "fixed_size": 1,
                            "gpu_software_mode": "provider-managed",
                        },
                        {
                            "id": "gpu-group-id-1",
                            "name": "worker-gpu-b-0",
                            "role": "worker-gpu-b",
                            "fixed_size": 1,
                            "gpu_software_mode": "provider-managed",
                        },
                    ]
                }
            }
        },
    }

    bindings = migration._target_gpu_node_group_bindings(
        checkpoint=checkpoint,
        gpu_nodesets=("worker-gpu-a", "worker-gpu-b"),
    )

    assert bindings == (
        {
            "state_key": "in-place:worker-gpu-a-0",
            "node_group_id": "gpu-group-id-0",
            "node_group_name": "worker-gpu-a-0",
            "nodeset": "worker-gpu-a",
            "accepted_nodeset_label": "worker-gpu-a",
            "fixed_node_count": 1,
        },
        {
            "state_key": "in-place:worker-gpu-b-0",
            "node_group_id": "gpu-group-id-1",
            "node_group_name": "worker-gpu-b-0",
            "nodeset": "worker-gpu-b",
            "accepted_nodeset_label": "worker-gpu-b",
            "fixed_node_count": 1,
        },
    )


def test_gpu_pre_activation_in_place_maps_one_legacy_role_to_target_nodeset() -> None:
    checkpoint = {
        "compute_migration": {"mode": migration.COMPUTE_MIGRATION_MODE_IN_PLACE},
        "operation_intent": {
            "intended_postcondition": {
                "mk8s": {
                    "node_groups": [
                        {
                            "id": "gpu-group-id-0",
                            "name": "worker-0-0",
                            "role": "worker-0",
                            "fixed_size": 1,
                            "gpu_software_mode": "provider-managed",
                        }
                    ]
                }
            }
        },
    }
    bindings = migration._target_gpu_node_group_bindings(
        checkpoint=checkpoint,
        gpu_nodesets=("worker",),
    )

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(
            args,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "gpu-node-0",
                                "uid": "node-uid-0",
                                "resourceVersion": "7",
                                "labels": {
                                    "nebius.com/node-group-id": "gpu-group-id-0",
                                    "slurm.nebius.ai/nodeset": "worker-0",
                                },
                            },
                            "spec": {},
                            "status": {
                                "allocatable": {"nvidia.com/gpu": "8"},
                                "conditions": [{"type": "Ready", "status": "True"}],
                            },
                        }
                    ]
                }
            ),
        )

    fleet = migration._ready_owned_target_gpu_nodes(
        command_runner=runner,
        kube_context="ctx",
        node_groups=bindings,
    )

    assert bindings[0]["nodeset"] == "worker"
    assert fleet[0]["nodeset"] == "worker"
    assert fleet[0]["node_uid"] == "node-uid-0"


def test_gpu_pre_activation_fleet_gate_binds_every_ready_node_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pre_activation_gpu_fleet_lineage(monkeypatch)
    phase: dict[str, Any] = {
        "gpu_post_population": {
            "status": "passed",
            "driver_version": "550.90.07",
            "image_lock": _pre_activation_image_lock(),
            "source_library_sha256": {
                "libcuda.so.1": "a" * 64,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }
    }
    values = {
        "nodesets": [
            {"name": "worker-gpu-a", "replicas": 1, "gpu": {"enabled": True}},
            {"name": "worker-gpu-b", "replicas": 1, "gpu": {"enabled": True}},
        ]
    }

    lines = migration._ensure_gpu_worker_jail_release_gate(
        phase=phase,
        checkpoint=_pre_activation_gpu_checkpoint(),
        command_runner=_pre_activation_gpu_fleet_runner(),
        kube_context="ctx",
        target_ref="training",
        values=values,
        checkpoint_writer=None,
        gate_key=migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY,
    )

    gate = phase[migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY]
    assert gate["status"] == "passed"
    assert gate["scope"] == "pre-activation-source-fleet"
    assert gate["target_ready_gpu_node_uids"] == ["node-uid-0", "node-uid-1"]
    assert gate["workers"] == []
    assert {node["node_uid"] for node in gate["target_ready_gpu_nodes"]} == {
        "node-uid-0",
        "node-uid-1",
    }
    assert "including podless spare nodes" in lines[0]


@pytest.mark.parametrize(
    ("second_driver", "second_libcuda_sha256"),
    [
        pytest.param("555.42.02", "a" * 64, id="mixed-driver"),
        pytest.param("550.90.07", "c" * 64, id="mixed-source-library-hash"),
    ],
)
def test_gpu_pre_activation_mixed_fleet_blocks_slot_switch(
    monkeypatch: pytest.MonkeyPatch,
    second_driver: str,
    second_libcuda_sha256: str,
) -> None:
    _install_pre_activation_gpu_fleet_lineage(monkeypatch)
    phase: dict[str, Any] = {
        "gpu_post_population": {
            "status": "passed",
            "driver_version": "550.90.07",
            "image_lock": _pre_activation_image_lock(),
            "source_library_sha256": {
                "libcuda.so.1": "a" * 64,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }
    }
    values = {
        "nodesets": [
            {"name": "worker-gpu-a", "replicas": 1, "gpu": {"enabled": True}},
            {"name": "worker-gpu-b", "replicas": 1, "gpu": {"enabled": True}},
        ]
    }
    slot_switches: list[str] = []

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="driver/library evidence|source probe|mixed NVIDIA driver",
    ):
        migration._ensure_gpu_worker_jail_release_gate(
            phase=phase,
            checkpoint=_pre_activation_gpu_checkpoint(),
            command_runner=_pre_activation_gpu_fleet_runner(
                second_driver=second_driver,
                second_libcuda_sha256=second_libcuda_sha256,
            ),
            kube_context="ctx",
            target_ref="training",
            values=values,
            checkpoint_writer=None,
            gate_key=migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY,
        )
        slot_switches.append("active-slot-switch")

    assert slot_switches == []
    gate = phase[migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY]
    assert gate["status"] == "failed"
    assert gate["target_ready_gpu_node_uids"] == ["node-uid-0", "node-uid-1"]


def test_owned_gpu_fleet_inventory_includes_ready_podless_spare_node() -> None:
    nodes = []
    for index in range(2):
        nodes.append(
            {
                "metadata": {
                    "name": f"gpu-node-{index}",
                    "uid": f"node-uid-{index}",
                    "resourceVersion": str(index + 1),
                    "labels": {
                        "nebius.com/node-group-id": "gpu-group-id",
                        "nebius.com/node-group": "training-worker-gpu",
                        "slurm.nebius.ai/nodeset-name": "worker-gpu",
                    },
                },
                "status": {
                    "allocatable": {"nvidia.com/gpu": "8"},
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        )

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(args, stdout=json.dumps({"items": nodes}))

    fleet = migration._ready_owned_target_gpu_nodes(
        command_runner=runner,
        kube_context="ctx",
        node_groups=(
            {
                "node_group_id": "gpu-group-id",
                "node_group_name": "training-worker-gpu",
                "nodeset": "worker-gpu",
                "fixed_node_count": 2,
            },
        ),
    )

    assert [item["node_uid"] for item in fleet] == ["node-uid-0", "node-uid-1"]
    assert all(item["node_group_name"] == "training-worker-gpu" for item in fleet)


def test_gpu_fleet_probe_uses_worker_pod_for_busy_node_and_job_only_for_spare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = (
        {
            "node_group_id": "gpu-group-id",
            "node_group_name": "training-worker-gpu",
            "nodeset": "worker-gpu",
            "fixed_node_count": 2,
        },
    )
    nodes = (
        {
            **groups[0],
            "node": "gpu-node-busy",
            "node_uid": "node-uid-busy",
            "node_resource_version": "1",
            "node_allocatable_gpu": 8,
        },
        {
            **groups[0],
            "node": "gpu-node-spare",
            "node_uid": "node-uid-spare",
            "node_resource_version": "2",
            "node_allocatable_gpu": 8,
        },
    )
    monkeypatch.setattr(migration, "_target_gpu_node_group_bindings", lambda **_kwargs: groups)
    monkeypatch.setattr(migration, "_ready_owned_target_gpu_nodes", lambda **_kwargs: nodes)
    job_nodes: list[str] = []

    def probe_job(**kwargs: Any) -> dict[str, Any]:
        node = dict(kwargs["node"])
        job_nodes.append(str(node["node"]))
        return {
            **node,
            "status": "passed",
            "probe_kind": "node-probe-job",
            "probe_stage": "inventory",
            "gpu_count": 8,
            "driver_version": "550.90.07",
            "source_library_sha256": {
                "libcuda.so.1": "a" * 64,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }

    monkeypatch.setattr(migration, "_ensure_gpu_fleet_probe_job", probe_job)
    _, probes = migration._probe_ready_owned_target_gpu_fleet(
        checkpoint={},
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="ctx",
        gpu_nodesets=("worker-gpu",),
        image_lock=_pre_activation_image_lock(),
        source_contract=("550.90.07", "a" * 64, "b" * 64),
        stage="inventory",
        occupied_node_probes=(
            {
                "node": "gpu-node-busy",
                "node_uid": "node-uid-busy",
                "pod": "worker-gpu-0",
                "pod_uid": "pod-uid",
                "status": "passed",
                "gpu_count": 8,
                "driver_version": "550.90.07",
                "source_library_sha256": {
                    "libcuda.so.1": "a" * 64,
                    "libnvidia-ml.so.1": "b" * 64,
                },
            },
        ),
    )

    assert job_nodes == ["gpu-node-spare"]
    assert {item["probe_kind"] for item in probes} == {"worker-pod", "node-probe-job"}


def test_gpu_fleet_probe_job_is_node_pinned_and_read_only() -> None:
    manifest = migration._gpu_fleet_probe_job_manifest(
        node={
            "node": "gpu-node-spare",
            "node_uid": "node-uid-spare",
            "node_group_id": "gpu-group-id",
        },
        image=_pre_activation_image_lock()["immutable_reference"],
        source_contract=("550.90.07", "a" * 64, "b" * 64),
        stage="activation",
    )
    pod_spec = manifest["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert pod_spec["nodeName"] == "gpu-node-spare"
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["runtimeClassName"] == "nvidia"
    assert container["image"] == _pre_activation_image_lock()["immutable_reference"]
    assert container["resources"] == {}
    assert container["env"] == [
        {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"},
        {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,utility"},
    ]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {
        "drop": ["ALL"],
        "add": ["SYS_CHROOT"],
    }
    assert container["volumeMounts"] == [
        {
            "name": "nvidia-driver-root",
            "mountPath": "/run/nvidia/driver",
            "readOnly": True,
        }
    ]
    assert pod_spec["volumes"][0]["hostPath"] == {
        "path": "/",
        "type": "Directory",
    }


def test_gpu_activation_boundary_blocks_exact_owned_fleet_membership_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pre_activation_gpu_fleet_lineage(monkeypatch)
    values = {
        "nodesets": [
            {"name": "worker-gpu-a", "replicas": 1, "gpu": {"enabled": True}},
            {"name": "worker-gpu-b", "replicas": 1, "gpu": {"enabled": True}},
        ]
    }
    phase: dict[str, Any] = {
        "gpu_post_population": {
            "status": "passed",
            "driver_version": "550.90.07",
            "image_lock": _pre_activation_image_lock(),
            "source_library_sha256": {
                "libcuda.so.1": "a" * 64,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }
    }
    checkpoint = _pre_activation_gpu_checkpoint()
    migration._ensure_gpu_worker_jail_release_gate(
        phase=phase,
        checkpoint=checkpoint,
        command_runner=_pre_activation_gpu_fleet_runner(),
        kube_context="ctx",
        target_ref="training",
        values=values,
        checkpoint_writer=None,
        gate_key=migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY,
    )
    gate = phase[migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY]
    drifted_fleet = copy.deepcopy(gate["target_ready_gpu_nodes"])
    drifted_fleet[1]["node_uid"] = "replacement-node-uid"
    monkeypatch.setattr(
        migration,
        "_probe_ready_owned_target_gpu_fleet",
        lambda **_kwargs: (
            tuple(gate["owned_gpu_node_groups"]),
            tuple(drifted_fleet),
        ),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="membership changed before the active Jail slot switch",
    ):
        migration._revalidate_gpu_pre_activation_fleet_before_switch(
            checkpoint=checkpoint,
            phase=phase,
            command_runner=_pre_activation_gpu_fleet_runner(),
            kube_context="ctx",
            target_ref="training",
            values=values,
            checkpoint_writer=None,
        )

    assert gate["activation_revalidation"]["status"] == "failed"


def test_gpu_activation_boundary_reprobes_exact_fleet_without_source_workload_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pre_activation_gpu_fleet_lineage(monkeypatch)
    values = {
        "nodesets": [
            {"name": "worker-gpu-a", "replicas": 1, "gpu": {"enabled": True}},
            {"name": "worker-gpu-b", "replicas": 1, "gpu": {"enabled": True}},
        ]
    }
    phase: dict[str, Any] = {
        "gpu_post_population": {
            "status": "passed",
            "driver_version": "550.90.07",
            "image_lock": _pre_activation_image_lock(),
            "source_library_sha256": {
                "libcuda.so.1": "a" * 64,
                "libnvidia-ml.so.1": "b" * 64,
            },
        }
    }
    checkpoint = _pre_activation_gpu_checkpoint()
    runner = _pre_activation_gpu_fleet_runner()
    migration._ensure_gpu_worker_jail_release_gate(
        phase=phase,
        checkpoint=checkpoint,
        command_runner=runner,
        kube_context="ctx",
        target_ref="training",
        values=values,
        checkpoint_writer=None,
        gate_key=migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY,
    )
    gate = phase[migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY]
    stages: list[str] = []

    def reprobe(**kwargs: Any) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        stages.append(str(kwargs["stage"]))
        return (
            tuple(copy.deepcopy(gate["owned_gpu_node_groups"])),
            tuple(copy.deepcopy(gate["target_ready_gpu_nodes"])),
        )

    monkeypatch.setattr(migration, "_probe_ready_owned_target_gpu_fleet", reprobe)
    lines = migration._revalidate_gpu_pre_activation_fleet_before_switch(
        checkpoint=checkpoint,
        phase=phase,
        command_runner=runner,
        kube_context="ctx",
        target_ref="training",
        values=values,
        checkpoint_writer=None,
    )

    validation = gate["activation_revalidation"]
    assert validation["status"] == "passed"
    assert validation["fleet_identity_sha256"] == gate["fleet_identity_sha256"]
    assert validation["workers"] == []
    assert stages == ["activation", "activation"]
    assert "immediately before switch" in lines[0]

    preserved = copy.deepcopy(gate)
    resume_lines = migration._require_checkpointed_gpu_activation_boundary_after_switch(
        phase=phase,
        values=values,
    )
    assert gate == preserved
    assert "was not reconstructed" in resume_lines[0]


def test_post_switch_resume_rejects_missing_historical_gpu_activation_proof() -> None:
    phase = {
        migration._GPU_PRE_ACTIVATION_FLEET_GATE_KEY: {
            "revision": migration._GPU_WORKLOAD_RELEASE_GATE_REVISION,
            "status": "passed",
            "owned_gpu_node_groups": [],
            "target_ready_gpu_nodes": [],
            "workers": [],
        }
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="Retrospective activation approval is prohibited",
    ):
        migration._require_checkpointed_gpu_activation_boundary_after_switch(
            phase=phase,
            values=_gpu_values(),
        )


def test_gpu_post_population_rejects_non_amd64_gpu_nodeset() -> None:
    values = _gpu_values()
    values["nodesets"][0]["nodeSelector"] = {"kubernetes.io/arch": "arm64"}

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="requires a linux/amd64 GPU NodeSet",
    ):
        migration._jail_gpu_post_population_scheduling(values)


def test_external_jail_gpu_release_probe_uses_post_population_evidence_marker() -> None:
    assert (
        'marker="${jail}/etc/nebius-cxcli/gpu-driver-jail.env"'
        in migration._GPU_WORKLOAD_RELEASE_PROBE  # noqa: SLF001
    )
    assert "cxcli_gpu_driver_jail_prep" not in migration._GPU_WORKLOAD_RELEASE_PROBE  # noqa: SLF001


def test_gpu_pre_activation_source_probe_rejects_broken_or_out_of_rootfs_sources() -> None:
    probe = migration._GPU_PRE_ACTIVATION_FLEET_SOURCE_PROBE  # noqa: SLF001

    assert "driver_root=/run/nvidia/driver" in probe
    assert 'source="$(readlink -f -- "${host_lib_dir}/${soname}")"' in probe
    assert "host driver library is missing or broken" in probe
    assert "host driver library resolves outside the read-only host NVIDIA root" in probe
    assert "host driver library hash differs from passive-rootfs evidence" in probe
    assert "host NVIDIA driver version differs from passive-rootfs evidence" in probe


def test_external_jail_gpu_post_activation_validates_every_gate_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase: dict[str, Any] = {
        "post_jail_gpu_workload_release_gate": {
            "status": "passed",
            "workers": [
                {
                    "nodeset": "worker-gpu",
                    "pod": "worker-gpu-0",
                    "pod_uid": "pod-uid",
                    "node": "k8s-node-uid",
                    "gpu_count": 8,
                }
            ],
        }
    }
    health_calls: list[str] = []
    monkeypatch.setattr(
        migration,
        "_jail_gpu_slurm_node_name",
        lambda **_kwargs: "worker-gpu-0",
    )

    def health(**_kwargs: Any) -> dict[str, Any]:
        health_calls.append("health")
        return {
            "status": "passed",
            "node_name": "worker-gpu-0",
            "gpu_count": 8,
            "failed_checks": [],
            "result_sha256": "a" * 64,
        }

    monkeypatch.setattr(migration, "_jail_gpu_health_checker_evidence", health)
    monkeypatch.setattr(
        migration,
        "_jail_gpu_pinned_h100_smoke",
        lambda **_kwargs: {
            "status": "passed",
            "job_name": "cxcli-h100-0123456789ab",
            "job_id": "42",
            "node_name": "worker-gpu-0",
            "pinned": True,
            "gpu_product": "NVIDIA H100 80GB HBM3",
            "gpu_count": 8,
        },
    )
    monkeypatch.setattr(
        migration,
        "_jail_gpu_smoke_terminal_evidence",
        lambda **_kwargs: {
            "status": "passed",
            "job_id": "42",
            "job_name": "cxcli-h100-0123456789ab",
            "state": "COMPLETED",
            "exit_code": "0:0",
            "node_name": "worker-gpu-0",
        },
    )
    monkeypatch.setattr(
        migration,
        "_jail_gpu_slurm_node_evidence",
        lambda **_kwargs: {
            "node_name": "worker-gpu-0",
            "state": "IDLE+DYNAMIC_NORM",
            "reason": "",
        },
    )
    cleaned: list[bool] = []
    monkeypatch.setattr(
        migration,
        "_delete_jail_gpu_temporary_partition",
        lambda **_kwargs: cleaned.append(True),
    )

    lines = migration._ensure_jail_gpu_post_activation_gate(
        phase=phase,
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="ctx",
        values=_gpu_values(),
        checkpoint_writer=None,
    )

    state = phase["gpu_post_activation"]
    assert state["status"] == "passed"
    assert state["validation"]["passed"] is True
    assert state["validation"]["node_name"] == "worker-gpu-0"
    assert state["pinned_h100_accounting"]["exit_code"] == "0:0"
    assert health_calls == ["health", "health"]
    assert cleaned == [True]
    assert "IDLE+DYNAMIC_NORM" in lines[0]


def test_external_jail_gpu_smoke_pins_the_full_worker_gpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_ensure_jail_gpu_temporary_partition",
        lambda **_kwargs: "cxcli-gpu-0123456789ab",
    )
    calls: list[tuple[str, ...]] = []

    def exec_login_once(*, args: Sequence[str], **_kwargs: Any) -> Any:
        selected = tuple(args)
        calls.append(selected)
        return _result(
            selected,
            stdout=(
                "cxcli-h100-smoke job_id=42 node=worker-gpu-0 gpu_count=8 "
                "product=NVIDIA H100 80GB HBM3\n"
            ),
        )

    monkeypatch.setattr(migration, "_kubectl_exec_login_once", exec_login_once)
    state: dict[str, Any] = {}

    evidence = migration._jail_gpu_pinned_h100_smoke(
        command_runner=lambda args, **_kwargs: _result(args),
        kube_context="ctx",
        state=state,
        slurm_node="worker-gpu-0",
        expected_gpu_count=8,
        checkpoint_writer=None,
    )

    assert evidence["status"] == "passed"
    assert evidence["gpu_count"] == 8
    assert "--gres=gpu:8" in calls[0]


def test_external_jail_gpu_smoke_lost_response_is_indeterminate_and_not_retried() -> None:
    state = {
        "pinned_h100_smoke": {
            "status": "dispatching",
            "job_name": "cxcli-h100-0123456789ab",
            "node_name": "worker-gpu-0",
        }
    }
    calls: list[Sequence[str]] = []

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="will not blindly retry"):
        migration._jail_gpu_pinned_h100_smoke(
            command_runner=lambda args, **_kwargs: calls.append(args) or _result(args),
            kube_context="ctx",
            state=state,
            slurm_node="worker-gpu-0",
            expected_gpu_count=8,
            checkpoint_writer=None,
        )

    assert state["pinned_h100_smoke"]["status"] == "Indeterminate"
    assert calls == []
