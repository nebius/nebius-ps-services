from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration


def test_in_place_gpu_driver_refresh_cleans_exact_temporary_resources(monkeypatch) -> None:
    phase: dict[str, Any] = {
        "in_place_node_groups": {
            "worker-group": {
                "role": "worker",
                "status": "provider-complete-health-pending",
                "replacement_node_uids": ["replacement-node-uid"],
                "target_update": {
                    "operation": {
                        "attempt_state": "provider-terminal",
                        "provider_operation_id": "provider-operation-id",
                        "intended_postcondition": {"kubernetes_version": "1.32"},
                    }
                },
            }
        }
    }
    applied: dict[str, dict[str, Any]] = {}
    deleted: list[dict[str, Any]] = []
    writes = 0

    monkeypatch.setattr(migration, "_gpu_worker_nodeset_names", lambda values: ("worker",))
    monkeypatch.setattr(
        migration,
        "_target_gpu_node_group_bindings",
        lambda **kwargs: (
            {
                "node_group_id": "worker-group",
                "node_group_name": "worker-group",
                "nodeset": "worker",
                "fixed_node_count": 1,
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_ready_owned_target_gpu_nodes",
        lambda **kwargs: [
            {
                "node_group_id": "worker-group",
                "node": "replacement-node",
                "node_uid": "replacement-node-uid",
            }
        ],
    )
    monkeypatch.setattr(
        migration,
        "_accepted_populate_jail_phase_state",
        lambda checkpoint: {
            "gpu_post_population": {
                "image_lock": {
                    "immutable_reference": "registry.example/controller@sha256:" + "a" * 64
                }
            }
        },
    )
    monkeypatch.setattr(
        migration,
        "_jail_gpu_post_population_scheduling",
        lambda values: {},
    )
    monkeypatch.setattr(migration, "_desired_manifest_contract_matches", lambda *args: True)
    monkeypatch.setattr(migration, "_wait_for_job_complete_or_failed", lambda **kwargs: None)
    monkeypatch.setattr(
        migration,
        "_jail_gpu_post_population_job_pod",
        lambda **kwargs: {"node_name": "replacement-node", "uid": "refresh-pod-uid"},
    )

    def get_resource(**kwargs):
        resource = kwargs["resource"]
        if resource.startswith("persistentvolumeclaim/"):
            return True, {"metadata": {"uid": "active-pvc-uid"}}
        kind, name = resource.split("/", 1)
        manifest = applied.get(kind)
        if (
            manifest is None
            or deleted
            and any(item["api_path"].endswith(f"/{name}") for item in deleted)
        ):
            return False, {}
        payload = {**manifest, "metadata": dict(manifest["metadata"])}
        payload["metadata"].update(
            {
                "uid": f"{kind}-uid",
                "resourceVersion": f"{kind}-resource-version",
            }
        )
        if kind == "job":
            payload["status"] = {"conditions": [{"type": "Complete", "status": "True"}]}
        return True, payload

    def apply_resources(**kwargs):
        for item in kwargs["objects"]:
            applied["configmap" if item["kind"] == "ConfigMap" else "job"] = item

    def delete_exact(command_runner, **kwargs):
        del command_runner
        deleted.append(dict(kwargs))

    def command_runner(
        command: Sequence[str],
        *,
        timeout_seconds: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds, check
        assert command[-1].startswith("job/")
        return subprocess.CompletedProcess(
            list(command),
            0,
            "gpu-jail-post-population: driver=580.159.04 visible_gpus=8 status=passed\n",
            "",
        )

    def write_checkpoint() -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(migration, "_kubectl_get_namespace_resource", get_resource)
    monkeypatch.setattr(migration, "_kubectl_apply_objects", apply_resources)
    monkeypatch.setattr(migration, "_command_runner_uid_preconditioned_delete", delete_exact)

    lines = migration._ensure_in_place_gpu_driver_refresh_after_provider_rollout(  # noqa: SLF001
        checkpoint={},
        phase=phase,
        command_runner=command_runner,
        kube_context="external-context",
        target_ref="target",
        values={},
        active_pvc="active-pvc",
        checkpoint_writer=write_checkpoint,
    )

    refresh = phase["in_place_gpu_driver_refresh"]
    assert refresh["status"] == "verified-clean"
    assert refresh["cleanup"]["status"] == "completed"
    assert deleted == [
        {
            "kube_context": "external-context",
            "api_path": (
                "/apis/batch/v1/namespaces/soperator/jobs/" + applied["job"]["metadata"]["name"]
            ),
            "uid": "job-uid",
            "resource_version": "job-resource-version",
            "propagation_policy": "Foreground",
            "timeout_seconds": 300,
            "allow_not_found": True,
        },
        {
            "kube_context": "external-context",
            "api_path": (
                "/api/v1/namespaces/soperator/configmaps/"
                + applied["configmap"]["metadata"]["name"]
            ),
            "uid": "configmap-uid",
            "resource_version": "configmap-resource-version",
            "propagation_policy": "Background",
            "timeout_seconds": 300,
            "allow_not_found": True,
        },
    ]
    assert writes >= 4
    assert "removed its exact temporary Job and ConfigMap" in lines[0]


def test_in_place_gpu_driver_refresh_rejects_nonterminal_provider_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase = {
        "in_place_node_groups": {
            "worker-group": {
                "status": "provider-complete-health-pending",
                "replacement_node_uids": ["replacement-node-uid"],
                "target_update": {
                    "operation": {
                        "attempt_state": "provider-accepted",
                        "provider_operation_id": "provider-operation-id",
                        "intended_postcondition": {"kubernetes_version": "1.32"},
                    }
                },
            }
        }
    }
    monkeypatch.setattr(migration, "_gpu_worker_nodeset_names", lambda _values: ("worker",))
    monkeypatch.setattr(
        migration,
        "_target_gpu_node_group_bindings",
        lambda **_kwargs: (
            {
                "node_group_id": "worker-group",
                "node_group_name": "worker-group",
                "nodeset": "worker",
                "fixed_node_count": 1,
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_ready_owned_target_gpu_nodes",
        lambda **_kwargs: pytest.fail("nonterminal provider evidence must stop before fleet read"),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_apply_objects",
        lambda **_kwargs: pytest.fail("nonterminal provider evidence must stop before apply"),
    )
    monkeypatch.setattr(
        migration,
        "_command_runner_uid_preconditioned_delete",
        lambda *_args, **_kwargs: pytest.fail(
            "nonterminal provider evidence must stop before cleanup"
        ),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="waits for terminal provider replacement identity",
    ):
        migration._ensure_in_place_gpu_driver_refresh_after_provider_rollout(  # noqa: SLF001
            checkpoint={},
            phase=phase,
            command_runner=lambda *_args, **_kwargs: pytest.fail(
                "nonterminal provider evidence must not run a command"
            ),
            kube_context="external-context",
            target_ref="target",
            values={},
            active_pvc="active-pvc",
            checkpoint_writer=lambda: pytest.fail(
                "nonterminal provider evidence must not write a checkpoint"
            ),
        )
