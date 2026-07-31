from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nebius_cxcli import cli
from nebius_cxcli import soperator_migration as migration


def _node_group(*, count: int = 100) -> dict[str, object]:
    return {
        "metadata": {
            "id": "mk8snodegroup-worker-a",
            "name": "worker-a",
            "resource_version": 17,
        },
        "spec": {
            "version": "1.31",
            "fixed_node_count": count,
            "strategy": {
                "max_surge": {"count": "1"},
                "max_unavailable": {"count": "1"},
                "drain_timeout": "1800s",
            },
            "template": {
                "os": "ubuntu22.04",
                "gpu_settings": {"drivers_preset": "cuda12.8"},
            },
        },
        "status": {"ready_node_count": count, "target_node_count": count},
    }


def test_external_upgrade_campaign_journal_and_report_are_v6_only() -> None:
    assert migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA.endswith("/v6")
    assert migration.SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA.endswith("/v6")
    assert migration.SOPERATOR_MIGRATION_REPORT_SCHEMA.endswith("/v6")


def test_managed_upgrade_rejects_abbreviated_bridge_journal(tmp_path: Path) -> None:
    assert cli.SOPERATOR_UPGRADE_CHECKPOINT_SCHEMA.endswith("/v2")
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "schema": cli.SOPERATOR_UPGRADE_CHECKPOINT_SCHEMA,
                "controller_bridge": {
                    "schema": "nebius-cxcli-managed-controller-bridge/v1",
                    "stage": "bridge-source-active",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Incompatible unfinished managed"):
        cli._load_soperator_upgrade_checkpoint(path)  # noqa: SLF001


def test_managed_upgrade_substrate_preflight_records_fixed_live_domains() -> None:
    checkpoint = {
        "managed_bridge_binding": {
            "schema": "nebius-cxcli-managed-bridge-binding/v1",
            "status": "planned",
            "placement_domains": {"controller": {}, "system": {}},
            "storage_fingerprints": {},
        }
    }
    node_groups = {
        role: {
            "node_count": count,
            "jail": True,
            "sfs_filesystem_keys": ["jail", "controller-spool"],
            "node_labels": {"nebius.ai/soperator-bridge-domain": role},
        }
        for role, count in (("controller", 2), ("system", 3))
    }
    nodes = []
    for role, count in (("controller", 2), ("system", 3)):
        for index in range(count):
            nodes.append(
                {
                    "metadata": {
                        "name": f"{role}-{index}",
                        "uid": f"{role}-uid-{index}",
                        "labels": {
                            "nebius.ai/soperator-bridge-domain": role,
                            "nebius.com/node-group-id": f"group-{role}",
                        },
                    },
                    "spec": {"unschedulable": False},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            )
    cli._verify_managed_controller_bridge_substrate(  # noqa: SLF001
        checkpoint=checkpoint,
        source_payload={
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {"node_groups": node_groups},
                    }
                ]
            }
        },
        target_ref="cluster1",
        snapshot={"kubernetes_nodes": nodes},
    )

    binding = checkpoint["managed_bridge_binding"]
    assert binding["status"] == "verified"
    assert binding["placement_domains"]["controller"]["node_group_id"] == "group-controller"
    assert binding["placement_domains"]["system"]["ready_capacity"] == 3


def test_in_place_execution_places_jail_slot_b_before_compute() -> None:
    phase_ids = migration._phase_ids_for_actions(  # noqa: SLF001
        report={},
        onboarding={
            "actions": [migration.ONBOARDING_ACTION_UPGRADE_SOPERATOR],
            "compute_migration": {"mode": migration.COMPUTE_MIGRATION_MODE_IN_PLACE},
        },
    )

    assert phase_ids.index(migration.POPULATE_JAIL_REFRESH_PHASE_ID) < phase_ids.index(
        "rolling-compute-migration"
    )


@pytest.mark.parametrize("legacy_version", range(1, 6))
def test_pre_v6_execution_journal_is_rejected_without_conversion(
    tmp_path: Path,
    legacy_version: int,
) -> None:
    path = tmp_path / "checkpoint.json"
    original = json.dumps(
        {"schema": f"nebius-cxcli-ext-soperator-upgrade-journal/v{legacy_version}"}
    )
    path.write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unsupported external Soperator upgrade checkpoint"):
        migration._load_checkpoint(path)  # noqa: SLF001

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("count", [1, 37, 100])
def test_zero_surge_all_resolves_to_complete_fixed_group(count: int) -> None:
    desired, mask = migration._in_place_node_group_desired_spec(  # noqa: SLF001
        original_node_group=_node_group(count=count),
        target={
            "kubernetes_version": "1.32",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
        },
        max_surge=0,
        max_unavailable=count,
        drain_timeout="10m",
    )

    assert desired["fixed_node_count"] == count
    assert desired["strategy"] == {
        "max_surge": {"count": "0"},
        "max_unavailable": {"count": str(count)},
        "drain_timeout": "600s",
    }
    assert mask == (
        "spec.version",
        "spec.template.os",
        "spec.template.gpu_settings",
        "spec.strategy",
    )


def test_service_role_in_place_uses_unlimited_provider_drain() -> None:
    desired, _mask = migration._in_place_node_group_desired_spec(  # noqa: SLF001
        original_node_group=_node_group(count=1),
        target={
            "kubernetes_version": "1.32",
            "os": "ubuntu24.04",
            "drivers_preset": "",
        },
        max_surge=0,
        max_unavailable=1,
        drain_timeout="none",
    )

    assert "drain_timeout" not in desired["strategy"]


def test_safe_surge_uses_spare_capacity_without_unavailable_workers() -> None:
    assert (
        migration._in_place_worker_max_unavailable(  # noqa: SLF001
            strategy="safe-surge",
            raw_max_unavailable=0,
            resolved_all={},
            group_id="worker-a",
            group_size=100,
        )
        == 0
    )
    desired, _mask = migration._in_place_node_group_desired_spec(  # noqa: SLF001
        original_node_group=_node_group(count=100),
        target={
            "kubernetes_version": "1.32",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
        },
        max_surge=3,
        max_unavailable=0,
        drain_timeout="10m",
    )

    assert desired["strategy"]["max_surge"] == {"count": "3"}
    assert desired["strategy"]["max_unavailable"] == {"count": "0"}


def test_worker_drain_rescheduling_guard_is_uid_bound_and_restores_null_affinity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = "security-profiles-operator-system"
    deployment_name = "security-profiles-operator-webhook"
    replica_set_name = deployment_name + "-abc"
    deployment = {
        "metadata": {
            "namespace": namespace,
            "name": deployment_name,
            "uid": "deployment-uid",
            "resourceVersion": "1",
        },
        "spec": {
            "replicas": 3,
            "template": {"spec": {"affinity": None}},
        },
        "status": {"updatedReplicas": 3, "availableReplicas": 3},
    }
    replica_set = {
        "metadata": {
            "namespace": namespace,
            "name": replica_set_name,
            "ownerReferences": [{"kind": "Deployment", "name": deployment_name}],
        }
    }

    def _pod(name: str, node: str) -> dict[str, Any]:
        return {
            "metadata": {
                "namespace": namespace,
                "name": name,
                "uid": name + "-uid",
                "ownerReferences": [{"kind": "ReplicaSet", "name": replica_set_name}],
            },
            "spec": {"nodeName": node},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }

    pods_payload = {
        "items": [_pod("webhook-source", "gpu-source"), _pod("webhook-peer", "system-peer")]
    }

    def _json_command(_runner: Any, args: list[str], **_kwargs: Any) -> Mapping[str, Any]:
        if "replicasets.apps" in args:
            return {"items": [copy.deepcopy(replica_set)]}
        if "deployments.apps" in args:
            return {"items": [copy.deepcopy(deployment)]}
        if "deployment" in args:
            return copy.deepcopy(deployment)
        raise AssertionError(args)

    monkeypatch.setattr(migration, "_json_from_command", _json_command)
    patches: list[list[Mapping[str, Any]]] = []

    def _runner(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "patch" in args:
            patch = json.loads(args[args.index("-p") + 1])
            patches.append(patch)
            assert patch[0] == {
                "op": "test",
                "path": "/metadata/uid",
                "value": "deployment-uid",
            }
            operation = patch[-1]
            if operation["op"] == "remove":
                deployment["spec"]["template"]["spec"].pop("affinity")
            else:
                deployment["spec"]["template"]["spec"]["affinity"] = copy.deepcopy(
                    operation["value"]
                )
            deployment["metadata"]["resourceVersion"] = str(
                int(deployment["metadata"]["resourceVersion"]) + 1
            )
        elif "rollout" not in args:
            raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    group_state: dict[str, Any] = {}
    lines = migration._ensure_in_place_worker_drain_rescheduling_guard(  # noqa: SLF001
        group_state=group_state,
        group_id="worker-group-id",
        group_name="worker-0-0",
        kubernetes_nodes=("gpu-source",),
        pods_payload=pods_payload,
        command_runner=_runner,  # type: ignore[arg-type]
        kube_context="test",
        checkpoint_writer=None,
    )

    guarded = deployment["spec"]["template"]["spec"]["affinity"]
    expression = guarded["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]
    assert expression == {
        "key": "nebius.com/node-group-id",
        "operator": "NotIn",
        "values": ["worker-group-id"],
    }
    assert group_state["kubernetes_drain_rescheduling_guard"]["status"] == "guarded"
    assert "fenced 1 Deployment" in lines[0]

    restore_lines = migration._restore_in_place_worker_drain_rescheduling_guard(  # noqa: SLF001
        group_state=group_state,
        command_runner=_runner,  # type: ignore[arg-type]
        kube_context="test",
        checkpoint_writer=None,
    )

    assert deployment["spec"]["template"]["spec"]["affinity"] is None
    assert patches[-1][-1] == {
        "op": "replace",
        "path": "/spec/template/spec/affinity",
        "value": None,
    }
    assert group_state["kubernetes_drain_rescheduling_guard"]["status"] == "restored"
    assert "Restored 1" in restore_lines[0]

    migration._ensure_in_place_worker_drain_rescheduling_guard(  # noqa: SLF001
        group_state=group_state,
        group_id="worker-group-id",
        group_name="worker-0-0",
        kubernetes_nodes=("gpu-source",),
        pods_payload=pods_payload,
        command_runner=_runner,  # type: ignore[arg-type]
        kube_context="test",
        checkpoint_writer=None,
    )
    assert len(group_state["kubernetes_drain_rescheduling_guard_history"]) == 1
    assert group_state["kubernetes_drain_rescheduling_guard"]["status"] == "guarded"


def test_worker_drain_rescheduling_guard_rejects_single_replica_without_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = "customer"
    deployment_name = "single"
    replica_set_name = "single-rs"
    pod = {
        "metadata": {
            "namespace": namespace,
            "name": "single-pod",
            "uid": "single-pod-uid",
            "ownerReferences": [{"kind": "ReplicaSet", "name": replica_set_name}],
        },
        "spec": {"nodeName": "gpu-source"},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    replica_set = {
        "metadata": {
            "namespace": namespace,
            "name": replica_set_name,
            "ownerReferences": [{"kind": "Deployment", "name": deployment_name}],
        }
    }
    deployment = {
        "metadata": {
            "namespace": namespace,
            "name": deployment_name,
            "uid": "single-uid",
            "resourceVersion": "1",
        },
        "spec": {"replicas": 1, "template": {"spec": {}}},
        "status": {"updatedReplicas": 1, "availableReplicas": 1},
    }

    def _json_command(_runner: Any, args: list[str], **_kwargs: Any) -> Mapping[str, Any]:
        if "replicasets.apps" in args:
            return {"items": [replica_set]}
        if "deployments.apps" in args:
            return {"items": [deployment]}
        raise AssertionError(args)

    monkeypatch.setattr(migration, "_json_from_command", _json_command)

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="no redundant Ready"):
        migration._ensure_in_place_worker_drain_rescheduling_guard(  # noqa: SLF001
            group_state={},
            group_id="worker-group-id",
            group_name="worker-0-0",
            kubernetes_nodes=("gpu-source",),
            pods_payload={"items": [pod]},
            command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            kube_context="test",
            checkpoint_writer=None,
        )


def test_in_place_batch_impact_reports_aggregate_before_dispatch() -> None:
    lines = migration._in_place_batch_impact_lines(  # noqa: SLF001
        (
            ({"id": "worker-a", "name": "worker-a", "fixed_size": 100}, 99),
            ({"id": "worker-b", "name": "worker-b", "fixed_size": 100}, 99),
        ),
        ("worker-c: blocked - active Slurm allocations remain",),
    )

    assert "worker-a: permits 99/100 unavailable" in lines
    assert "worker-b: permits 99/100 unavailable" in lines
    assert "worker-c: blocked - active Slurm allocations remain" in lines
    assert "Batch permits up to 198 worker nodes to be unavailable." in lines
    assert "Every prepared worker group retains at least one provider capacity member." in lines
    assert "Nebius may process fewer nodes concurrently than this permitted bound." in lines


def test_final_group_proof_accepts_only_the_exact_cxcli_owned_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "cxcli-in-place-fingerprint-worker-a"
    monkeypatch.setattr(
        migration,
        "_nodes_for_source_groups",
        lambda **_kwargs: ("k8s-worker-a",),
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_jobs",
        lambda **_kwargs: (),
    )
    observations = iter(
        (
            SimpleNamespace(
                returncode=0,
                stdout="NodeName=slurm-worker-a NodeAddr=k8s-worker-a\n",
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=f"NodeName=slurm-worker-a State=IDLE+DRAIN Reason={reason}\n",
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(migration, "_kubectl_exec_login", lambda **_kwargs: next(observations))

    clear, evidence = migration._source_worker_group_retirement_evidence(  # noqa: SLF001
        group_name="worker-a",
        source_report={},
        command_runner=SimpleNamespace(),
        kube_context="context",
        allowed_node_reason=reason,
    )

    assert clear is True
    assert evidence["node_reasons"] == {"slurm-worker-a": reason}


def test_pause_false_opens_durable_job_control_without_waiting_for_every_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        migration,
        "_nodes_for_worker_groups",
        lambda **_kwargs: ("worker-a-0", "worker-b-0"),
    )
    monkeypatch.setattr(
        migration,
        "_checkpoint_slurm_held_job_operations",
        lambda _checkpoint: {},
    )
    monkeypatch.setattr(
        migration,
        "_checkpoint_slurm_action_journal_for_job_control",
        lambda _checkpoint: {},
    )

    def handle(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["operator action accepted"]

    monkeypatch.setattr(migration, "_handle_external_upgrade_slurm_jobs", handle)

    lines = migration._in_place_pause_false_job_control(  # noqa: SLF001
        checkpoint={},
        source_report={},
        worker_group_names=("worker-a", "worker-b"),
        command_runner=SimpleNamespace(),
        kube_context="context",
        checkpoint_writer=lambda: None,
        job_policy="interactive",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=0,
        job_refresh_interval_seconds=2,
        slurm_decision_recorder=None,
        interactive_prompt_pause=None,
        allow_resolved_interactive_job_policy=True,
    )

    assert lines == ["operator action accepted"]
    assert captured["node_names"] == ("worker-a-0", "worker-b-0")
    assert captured["include_pending"] is True
    assert captured["return_after_operator_action"] is True


def test_pause_true_pauses_partitions_even_without_control_plane_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        migration,
        "_nodes_for_worker_groups",
        lambda **_kwargs: ("worker-a-0",),
    )
    monkeypatch.setattr(
        migration,
        "_checkpoint_slurm_held_job_operations",
        lambda _checkpoint: {},
    )
    monkeypatch.setattr(
        migration,
        "_checkpoint_slurm_action_journal_for_job_control",
        lambda _checkpoint: {},
    )

    def quiet(**kwargs: Any) -> tuple[list[str], tuple[Any, ...]]:
        captured.update(kwargs)
        return ["partitions paused"], ()

    monkeypatch.setattr(migration, "_ensure_slurm_quiet", quiet)
    phase: dict[str, Any] = {}

    lines = migration._in_place_pause_true_job_control(  # noqa: SLF001
        checkpoint={},
        phase=phase,
        source_report={},
        worker_group_names=("worker-a",),
        command_runner=SimpleNamespace(),
        kube_context="context",
        checkpoint_writer=lambda: None,
        job_policy="preserve",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=0,
        job_refresh_interval_seconds=2,
        slurm_decision_recorder=None,
        interactive_prompt_pause=None,
        allow_resolved_interactive_job_policy=True,
    )

    assert captured["node_names"] == ("worker-a-0",)
    assert captured["slurm_scheduling_pause"] is True
    assert captured["all_partitions"] is True
    assert phase["slurm_paused_partitions"] == []
    assert lines[-1].endswith("remain paused through final health gates.")


def test_service_role_replacement_rechecks_slot_b_and_persistent_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(
        migration,
        "active_passive_jail_rootfs_slots",
        lambda _values: SimpleNamespace(
            active_slot="slot-b",
            passive_slot="slot-a",
            active_pvc="slot-b-pvc",
        ),
    )
    monkeypatch.setattr(
        migration,
        "_verify_target_rootfs_handoff_consumers",
        lambda **_kwargs: [{"name": "controller", "status": "verified"}],
    )
    monkeypatch.setattr(
        migration,
        "_wait_for_target_slurmcluster_available",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"metadata": {"name": "external-cluster"}},
    )
    monkeypatch.setattr(
        migration,
        "_verify_target_jail_alias_consumers",
        lambda **_kwargs: [{"name": "controller", "status": "verified"}],
    )
    monkeypatch.setattr(
        migration,
        "_live_home_mount_probe_checks",
        lambda **_kwargs: ({"name": "home", "status": "passed", "detail": "mounted"},),
    )
    checkpoint = {
        "phase_state": {
            migration.POPULATE_JAIL_REFRESH_PHASE_ID: {
                "rootfs_handoff_verification": {
                    "status": "verified",
                    "active_slot": "slot-b",
                }
            }
        }
    }
    phase: dict[str, Any] = {}

    lines = migration._verify_in_place_service_role_replacement_health(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        role="controller",
        payload={},
        source_report={},
        live_snapshot={},
        target_ref="external-cluster",
        kube_context="context",
        command_runner=SimpleNamespace(),
        checkpoint_writer=lambda: None,
    )

    assert lines == [
        "In-place controller replacement verified on Jail slot-b with controller aliases "
        "and persistent mounts."
    ]
    assert phase["in_place_service_role_health"]["controller"]["status"] == "passed"
    assert phase["in_place_service_role_health"]["controller"]["jail_alias_consumer_checks"] == [
        {"name": "controller", "status": "verified"}
    ]


def test_service_role_health_reuses_same_invocation_proof_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"rootfs": 0, "available": 0, "cluster": 0, "aliases": 0, "mounts": 0}
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(
        migration,
        "active_passive_jail_rootfs_slots",
        lambda _values: SimpleNamespace(
            active_slot="slot-b",
            passive_slot="slot-a",
            active_pvc="slot-b-pvc",
        ),
    )

    def _rootfs(**_kwargs: Any) -> list[dict[str, str]]:
        calls["rootfs"] += 1
        return [{"name": "controller", "status": "verified"}]

    def _available(**_kwargs: Any) -> None:
        calls["available"] += 1

    def _cluster(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["cluster"] += 1
        return {"metadata": {"name": "external-cluster"}}

    def _aliases(**_kwargs: Any) -> list[dict[str, str]]:
        calls["aliases"] += 1
        return [{"name": "controller", "status": "verified"}]

    def _mounts(**_kwargs: Any) -> tuple[dict[str, str], ...]:
        calls["mounts"] += 1
        return ({"name": "home", "status": "passed"},)

    monkeypatch.setattr(migration, "_verify_target_rootfs_handoff_consumers", _rootfs)
    monkeypatch.setattr(migration, "_wait_for_target_slurmcluster_available", _available)
    monkeypatch.setattr(migration, "_json_from_command", _cluster)
    monkeypatch.setattr(migration, "_verify_target_jail_alias_consumers", _aliases)
    monkeypatch.setattr(migration, "_live_home_mount_probe_checks", _mounts)
    checkpoint = {
        "phase_state": {
            migration.POPULATE_JAIL_REFRESH_PHASE_ID: {
                "rootfs_handoff_verification": {
                    "status": "verified",
                    "active_slot": "slot-b",
                }
            }
        },
        "controller_bridge": {"stage": migration.BridgeStage.SOURCE_HA_ACTIVE.value},
    }
    phase: dict[str, Any] = {}
    shared: dict[str, Any] = {}

    for role in ("login", "accounting"):
        migration._verify_in_place_service_role_replacement_health(  # noqa: SLF001
            checkpoint=checkpoint,
            phase=phase,
            role=role,
            payload={},
            source_report={},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="context",
            command_runner=SimpleNamespace(),
            checkpoint_writer=lambda: None,
            shared_evidence=shared,
        )

    assert calls == {"rootfs": 1, "available": 1, "cluster": 1, "aliases": 1, "mounts": 1}
    assert phase["in_place_service_role_health"]["login"]["evidence_source"] == "live"
    assert (
        phase["in_place_service_role_health"]["accounting"]["evidence_source"] == "invocation-reuse"
    )
    assert (
        phase["in_place_service_role_health"]["login"]["proof_sha256"]
        == phase["in_place_service_role_health"]["accounting"]["proof_sha256"]
    )


def test_completed_service_health_reuses_proof_after_planned_worker_outage() -> None:
    group_state = {
        "status": "completed",
        "target_update": {
            "operation": {
                "attempt_state": "provider-terminal",
                "reconciled_at": "2026-07-21T06:24:35Z",
            }
        },
    }
    role_health = {
        # A later replay may be checking while replacement workers are intentionally
        # unavailable, but the successful post-provider proof remains authoritative.
        "status": "checking",
        "verified_at": "2026-07-21T07:35:43Z",
    }

    assert migration._in_place_completed_service_health_reusable(  # noqa: SLF001
        group_state=group_state,
        role_health=role_health,
    )
    role_health["verified_at"] = "2026-07-21T06:20:00Z"
    assert not migration._in_place_completed_service_health_reusable(  # noqa: SLF001
        group_state=group_state,
        role_health=role_health,
    )


def test_accepted_worker_provider_operation_is_classified_for_bridge_resume() -> None:
    phase = {
        "in_place_node_groups": {
            "worker": {
                "role": "worker-0",
                "target_update": {
                    "operation": {
                        "attempt_state": "provider-pending",
                        "provider_operation_id": "opmk8snodegroup-worker",
                        "accepted_at": "2026-07-21T07:43:00Z",
                    }
                },
            },
            "login": {
                "role": "login",
                "target_update": {
                    "operation": {
                        "attempt_state": "provider-pending",
                        "provider_operation_id": "opmk8snodegroup-login",
                        "accepted_at": "2026-07-21T07:00:00Z",
                    }
                },
            },
        }
    }

    pending = migration._accepted_in_place_worker_provider_operations_pending(  # noqa: SLF001
        phase
    )
    assert [item["provider_operation_id"] for item in pending] == ["opmk8snodegroup-worker"]


def test_worker_provider_resume_reuses_predispatch_proofs_after_source_retirement() -> None:
    group_state = {
        "target_update": {
            "operation": {
                "attempt_state": "provider-pending",
                "provider_operation_id": "opmk8snodegroup-worker",
                "accepted_at": "2026-07-21T07:43:00Z",
            }
        },
        "slurm_drain": {"status": "applied", "reason": "cxcli-owned"},
        "final_dispatch_evidence": {
            "status": "locked",
            "reason": "cxcli-owned",
            "active_job_ids": [],
            "checked_at": "2026-07-21T07:42:36Z",
        },
        "kubernetes_provider_drain": {"status": "drained"},
    }

    assert migration._in_place_worker_provider_operation_resume_ready(  # noqa: SLF001
        group_state
    )
    group_state["final_dispatch_evidence"]["active_job_ids"] = ["123"]
    assert not migration._in_place_worker_provider_operation_resume_ready(  # noqa: SLF001
        group_state
    )


def test_worker_outage_boundary_requires_an_exact_accepted_provider_operation() -> None:
    phase = {
        "in_place_node_groups": {
            "worker": {
                "role": "worker-0",
                "status": "provider-complete-health-pending",
                "target_update": {
                    "operation": {
                        "attempt_state": "provider-terminal",
                        "provider_operation_id": "opmk8snodegroup-worker",
                        "accepted_at": "2026-07-21T07:43:00Z",
                    }
                },
            }
        }
    }

    assert migration._in_place_worker_outage_boundary_active(phase)  # noqa: SLF001
    phase["in_place_node_groups"]["worker"]["status"] = "completed"
    assert not migration._in_place_worker_outage_boundary_active(phase)  # noqa: SLF001


@pytest.mark.parametrize("field", ["registrations", "catalogs"])
def test_in_place_accounting_continuity_rejects_exact_evidence_drift(field: str) -> None:
    baseline = {
        "writer_identity": {
            "pod": "cluster-acct-db-0",
            "pod_uid": "pod-old",
            "statefulset": "cluster-acct-db",
            "statefulset_uid": "statefulset-uid",
            "mariadb": "cluster-acct-db",
            "mariadb_uid": "mariadb-uid",
            "slurmcluster": "cluster",
            "slurmcluster_uid": "slurmcluster-uid",
            "pvc": "storage-cluster-acct-db-0",
            "pvc_uid": "pvc-uid",
        },
        "history": {"jobs": 10},
        "registrations": {"cluster": {"id": 1}},
        "catalogs": {"qos": {"row_count": 1, "sha256": "a" * 64}},
    }
    observed = json.loads(json.dumps(baseline))
    observed["writer_identity"]["pod_uid"] = "pod-new"
    observed[field] = {"drift": True}

    with pytest.raises(migration.SoperatorMigrationPhasePending, match=field):
        migration._verify_in_place_accounting_continuity(  # noqa: SLF001
            baseline=baseline,
            observed=observed,
        )


def test_in_place_accounting_continuity_allows_monotonic_job_history() -> None:
    baseline = {
        "writer_identity": {
            "pod": "cluster-acct-db-0",
            "pod_uid": "pod-old",
            "statefulset": "cluster-acct-db",
            "statefulset_uid": "statefulset-uid",
            "mariadb": "cluster-acct-db",
            "mariadb_uid": "mariadb-uid",
            "slurmcluster": "cluster",
            "slurmcluster_uid": "slurmcluster-uid",
            "pvc": "storage-cluster-acct-db-0",
            "pvc_uid": "pvc-uid",
        },
        "history": {"jobs": 4, "steps": 2, "completed_jobs": 4, "max_job_id": 5},
        "registrations": {"cluster": {"id": 1}},
        "catalogs": {"qos": {"row_count": 1, "sha256": "a" * 64}},
    }
    observed = json.loads(json.dumps(baseline))
    observed["history"].update({"jobs": 5, "steps": 2, "completed_jobs": 5, "max_job_id": 6})

    migration._verify_in_place_accounting_continuity(  # noqa: SLF001
        baseline=baseline,
        observed=observed,
    )


def test_in_place_accounting_continuity_rejects_history_regression() -> None:
    baseline = {
        "writer_identity": {},
        "history": {"jobs": 5, "max_job_id": 6},
        "registrations": {},
        "catalogs": {},
    }
    observed = json.loads(json.dumps(baseline))
    observed["history"]["jobs"] = 4

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="history.*jobs"):
        migration._verify_in_place_accounting_continuity(  # noqa: SLF001
            baseline=baseline,
            observed=observed,
        )


def test_in_place_accounting_continuity_allows_only_writer_pod_recreation() -> None:
    baseline = {
        "writer_identity": {
            "pod": "cluster-acct-db-0",
            "pod_uid": "pod-old",
            "statefulset": "cluster-acct-db",
            "statefulset_uid": "statefulset-uid",
            "mariadb": "cluster-acct-db",
            "mariadb_uid": "mariadb-uid",
            "slurmcluster": "cluster",
            "slurmcluster_uid": "slurmcluster-uid",
            "pvc": "storage-cluster-acct-db-0",
            "pvc_uid": "pvc-uid",
        },
        "history": {"jobs": 10},
        "registrations": {"cluster": {"id": 1}},
        "catalogs": {"qos": {"row_count": 1, "sha256": "a" * 64}},
    }
    observed = json.loads(json.dumps(baseline))
    observed["writer_identity"]["pod_uid"] = "pod-new"

    migration._verify_in_place_accounting_continuity(  # noqa: SLF001
        baseline=baseline,
        observed=observed,
    )


def test_in_place_accounting_continuity_rejects_unproven_controller_host_refresh() -> None:
    baseline = {
        "writer_identity": {
            "pod": "cluster-acct-db-0",
            "pod_uid": "pod-old",
            "statefulset": "cluster-acct-db",
            "statefulset_uid": "statefulset-uid",
            "mariadb": "cluster-acct-db",
            "mariadb_uid": "mariadb-uid",
            "slurmcluster": "cluster",
            "slurmcluster_uid": "slurmcluster-uid",
            "pvc": "storage-cluster-acct-db-0",
            "pvc_uid": "pvc-uid",
        },
        "history": {"jobs": 10},
        "registrations": {
            "cluster": {"control_host": "10.0.0.1", "control_port": "6817", "rpc": "1"}
        },
        "catalogs": {"qos": {"row_count": 1, "sha256": "a" * 64}},
    }
    observed = json.loads(json.dumps(baseline))
    observed["writer_identity"]["pod_uid"] = "pod-new"
    observed["registrations"]["cluster"]["control_host"] = "10.0.0.2"

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="registrations evidence"):
        migration._verify_in_place_accounting_continuity(  # noqa: SLF001
            baseline=baseline,
            observed=observed,
        )


def test_in_place_controller_roll_requires_system_domain_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = [
        {
            "pod_name": "bridge-0",
            "pod_uid": "controller-pod",
            "node_uid": "controller-node",
            "node_group_id": "controller-group",
            "role": "active",
            "image": "registry/controller@sha256:" + "a" * 64,
        },
        {
            "pod_name": "bridge-1",
            "pod_uid": "system-pod",
            "node_uid": "system-node",
            "node_group_id": "system-group",
            "role": "standby",
            "image": "registry/controller@sha256:" + "a" * 64,
        },
    ]
    checkpoint = {"controller_bridge": {"namespace": "soperator", "controller_roles": roles}}
    phase: dict[str, Any] = {}
    observations = iter(((roles[0], roles[1]), (roles[1], roles[0])))
    monkeypatch.setattr(
        migration,
        "_prove_journaled_controller_bridge_exclusivity",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_target_controller_command_gate",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_bridge_client_configuration",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_primary_role_from_ping",
        lambda **_kwargs: next(observations),
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"spec": {"template": {"spec": {"containers": []}}}},
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_container_name",
        lambda *_args, **_kwargs: "slurmctld",
    )
    commands: list[tuple[str, ...]] = []

    def _runner(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    lines = migration._ensure_in_place_bridge_authority_outside_provider_domain(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        role="controller",
        planned_groups=(
            {"role": "controller", "id": "controller-group"},
            {"role": "system", "id": "system-group"},
        ),
        target_ref="target",
        command_runner=_runner,
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    assert any(command[-3:] == ("scontrol", "takeover", "1") for command in commands)
    assert phase["in_place_authority_transitions"]["controller"]["status"] == "verified"
    assert checkpoint["controller_bridge"]["controller_roles"][1]["role"] == "active"
    assert lines == [
        "Transferred bridge authority outside the controller provider domain before replacement."
    ]


def test_bridge_primary_authority_uses_exact_login_client_not_cached_controller_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = [
        {"pod_name": "bridge-0", "pod_uid": "pod-0"},
        {"pod_name": "bridge-1", "pod_uid": "pod-1"},
    ]
    exact_calls: list[tuple[str, ...]] = []

    def _exact_login(**kwargs: Any) -> SimpleNamespace:
        exact_calls.append(tuple(kwargs["args"]))
        return SimpleNamespace(
            returncode=0,
            stdout=("Slurmctld(primary) at bridge-0 is UP\nSlurmctld(backup) at bridge-1 is UP\n"),
            stderr="",
        )

    monkeypatch.setattr(migration, "_kubectl_exec_login_once", _exact_login)
    monkeypatch.setattr(
        migration,
        "_kubectl_exec_login",
        lambda **_kwargs: pytest.fail("cached read-only controller route must not be used"),
    )

    active, standby = migration._controller_bridge_primary_role_from_ping(  # noqa: SLF001
        journal={"controller_roles": roles},
        command_runner=lambda args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        kube_context="context",
    )

    assert active is roles[0]
    assert standby is roles[1]
    assert exact_calls == [
        (
            "env",
            f"SLURM_CONF={migration._SOPERATOR_LEGACY_SLURM_CONF}",  # noqa: SLF001
            "scontrol",
            "ping",
        )
    ]


def test_in_place_controller_roll_resumes_setup_after_manager_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = {
        "pod_name": "bridge-1",
        "pod_uid": "system-pod",
        "node_uid": "system-node",
        "node_group_id": "system-group",
        "role": "active",
    }
    controller = {
        "pod_name": "bridge-0",
        "pod_uid": "controller-pod",
        "node_uid": "controller-node",
        "node_group_id": "controller-group",
        "role": "standby",
    }
    phase = {"in_place_target_manager_pause": {"status": "verified"}}
    calls: list[str] = []
    monkeypatch.setattr(
        migration,
        "_verify_in_place_target_native_controller_ha",
        lambda **_kwargs: pytest.fail("manager pause alone is not a completed native HA proof"),
    )
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_bridge_client_configuration",
        lambda **_kwargs: calls.append("clients") or [],
    )
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_target_controller_command_gate",
        lambda **_kwargs: calls.append("gate") or [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_journaled_controller_bridge_exclusivity",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_primary_role_from_ping",
        lambda **_kwargs: (system, controller),
    )

    lines = migration._ensure_in_place_bridge_authority_outside_provider_domain(  # noqa: SLF001
        checkpoint={"controller_bridge": {"controller_roles": [controller, system]}},
        phase=phase,
        role="controller",
        planned_groups=(
            {"role": "controller", "id": "controller-group"},
            {"role": "system", "id": "system-group"},
        ),
        target_ref="target",
        command_runner=lambda args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    assert calls == ["clients", "gate"]
    assert lines == [
        "Bridge authority is outside the controller provider domain before replacement."
    ]


def test_dedicated_bridge_authority_proof_is_reused_within_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = {
        "pod_name": "bridge-0",
        "pod_uid": "pod-0",
        "node_uid": "node-0",
        "node_group_id": "dedicated-a",
        "role": "active",
        "image": "controller@sha256:" + "a" * 64,
    }
    standby = {
        "pod_name": "bridge-1",
        "pod_uid": "pod-1",
        "node_uid": "node-1",
        "node_group_id": "dedicated-b",
        "role": "standby",
        "image": "controller@sha256:" + "a" * 64,
    }
    calls: list[str] = []
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_bridge_client_configuration",
        lambda **_kwargs: calls.append("clients") or [],
    )
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_target_controller_command_gate",
        lambda **_kwargs: calls.append("gate") or [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_journaled_controller_bridge_exclusivity",
        lambda **_kwargs: calls.append("exclusivity"),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_primary_role_from_ping",
        lambda **_kwargs: (active, standby),
    )
    checkpoint = {"controller_bridge": {"controller_roles": [active, standby]}}
    phase: dict[str, Any] = {}
    shared: dict[str, Any] = {}
    common = {
        "checkpoint": checkpoint,
        "phase": phase,
        "planned_groups": (
            {"role": "controller", "id": "controller-group"},
            {"role": "system", "id": "system-group"},
        ),
        "target_ref": "target",
        "command_runner": lambda args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
        "kube_context": "context",
        "checkpoint_writer": lambda: None,
        "shared_evidence": shared,
    }

    migration._ensure_in_place_bridge_authority_outside_provider_domain(  # noqa: SLF001
        **common,
        role="controller",
    )
    lines = migration._ensure_in_place_bridge_authority_outside_provider_domain(  # noqa: SLF001
        **common,
        role="system",
    )

    assert calls == ["clients", "gate", "exclusivity"]
    assert "invocation-scoped" in lines[0]
    assert phase["in_place_authority_transitions"]["system"]["evidence_source"] == (
        "invocation-reuse"
    )


def test_post_roll_rebind_accepts_exact_dedicated_bridge_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = {
        "pod_name": "bridge-0",
        "pod_uid": "pod-0",
        "node_uid": "node-0",
        "node_group_id": "dedicated-bridge-a",
    }
    standby = {
        "pod_name": "bridge-1",
        "pod_uid": "pod-1",
        "node_uid": "node-1",
        "node_group_id": "dedicated-bridge-b",
    }
    proofs: list[str] = []
    monkeypatch.setattr(
        migration,
        "_prove_journaled_controller_bridge_exclusivity",
        lambda **kwargs: proofs.append(kwargs["proof_label"]),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_primary_role_from_ping",
        lambda **_kwargs: (active, standby),
    )
    phase: dict[str, Any] = {}

    lines = migration._rebind_in_place_bridge_domain_after_provider_roll(  # noqa: SLF001
        checkpoint={
            "controller_bridge": {
                "controller_roles": [active, standby],
            }
        },
        phase=phase,
        role="controller",
        rolled_group_id="main-controller-group",
        command_runner=lambda args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    assert lines == [
        "Dedicated bridge authority remained outside the rolled controller provider domain."
    ]
    assert proofs == ["post-controller-replacement dedicated bridge authority"]
    assert (
        phase["in_place_service_role_health"]["controller"]["bridge_rebind"]["status"]
        == "verified-dedicated-bridge"
    )


def _target_owned_controller_gate_resources(
    *,
    observed_gate: Mapping[str, Any],
    workload_gate: Mapping[str, Any] | None = None,
    workload_replicas: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = {
        "apiVersion": "slurm.nebius.ai/v1alpha1",
        "kind": "SlurmCluster",
        "metadata": {
            "namespace": "soperator",
            "name": "target",
            "uid": "cluster-uid",
            "resourceVersion": "42",
        },
        "spec": {
            "slurmNodes": {
                "controller": {
                    "slurmctld": copy.deepcopy(dict(observed_gate)),
                }
            }
        },
    }
    workload = {
        "apiVersion": "apps.kruise.io/v1beta1",
        "kind": "StatefulSet",
        "metadata": {
            "namespace": "soperator",
            "name": "controller",
            "uid": "workload-uid",
            "resourceVersion": "84",
            "ownerReferences": [
                {
                    "apiVersion": "slurm.nebius.ai/v1alpha1",
                    "kind": "SlurmCluster",
                    "name": "target",
                    "uid": "cluster-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "replicas": workload_replicas,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "slurmctld",
                            **copy.deepcopy(dict(workload_gate or observed_gate)),
                        }
                    ]
                }
            },
        },
    }
    return target, workload


def test_in_place_controller_roll_reasserts_reconciled_target_command_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {
        "controller_bridge": {
            "stage": migration.BridgeStage.TARGET_HA_ACTIVE.value,
            "authority": {
                "owner": "bridge-target",
                "source_restart_prohibited": True,
            },
            "target_singleton_takeover": {"command_gate_applied": True},
        }
    }
    phase: dict[str, Any] = {}
    resources = iter(
        _target_owned_controller_gate_resources(
            observed_gate={"command": ["slurmctld"], "args": []},
        )
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: next(resources),
    )
    commands: list[tuple[str, ...]] = []

    def _runner(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="reasserted the exact inert target controller command gate",
    ):
        migration._ensure_in_place_target_controller_command_gate(  # noqa: SLF001
            checkpoint=checkpoint,
            phase=phase,
            target_ref="target",
            command_runner=_runner,
            kube_context="context",
            checkpoint_writer=lambda: None,
        )

    patch_command = next(command for command in commands if "patch" in command)
    patch_payload = json.loads(patch_command[-1])
    expected = migration.target_controller_gate_values({})["slurmNodes"]["controller"]["slurmctld"]
    assert patch_payload["metadata"]["resourceVersion"] == "42"
    assert patch_payload["spec"]["slurmNodes"]["controller"]["slurmctld"] == expected
    assert phase["in_place_target_controller_command_gate"]["status"] == "slurmcluster-accepted"


def test_in_place_controller_roll_rearms_gate_for_a_later_bridge_owned_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {
        "controller_bridge": {
            "stage": migration.BridgeStage.SOURCE_HA_ACTIVE.value,
            "authority": {
                "owner": "bridge-source",
                "source_restart_prohibited": True,
            },
            "target_singleton_takeover": {"command_gate_applied": False},
        }
    }
    phase: dict[str, Any] = {}
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    resources = iter(
        _target_owned_controller_gate_resources(
            observed_gate={"command": ["slurmctld"], "args": []},
        )
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: next(resources),
    )
    commands: list[tuple[str, ...]] = []

    def _runner(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="reasserted the exact inert target controller command gate",
    ):
        migration._ensure_in_place_target_controller_command_gate(  # noqa: SLF001
            checkpoint=checkpoint,
            phase=phase,
            target_ref="target",
            command_runner=_runner,
            kube_context="context",
            checkpoint_writer=lambda: None,
        )

    assert any("patch" in command for command in commands)
    assert (
        checkpoint["controller_bridge"]["target_singleton_takeover"]["command_gate_applied"]
        is False
    )
    assert phase["in_place_target_controller_command_gate"]["status"] == "slurmcluster-accepted"


def test_in_place_controller_roll_uses_admission_window_while_manager_is_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {
        "controller_bridge": {
            "stage": migration.BridgeStage.TARGET_HA_ACTIVE.value,
            "authority": {
                "owner": "bridge-target",
                "source_restart_prohibited": True,
            },
            "target_singleton_takeover": {"command_gate_applied": True},
        }
    }
    phase: dict[str, Any] = {
        "in_place_target_manager_pause": {"status": "verified"},
    }
    resources = iter(
        _target_owned_controller_gate_resources(
            observed_gate={"command": ["slurmctld"], "args": []},
        )
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: next(resources),
    )
    admission_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_patch_slurmcluster_during_manager_pause",
        lambda **kwargs: admission_calls.append(kwargs),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="reasserted the exact inert target controller command gate",
    ):
        migration._ensure_in_place_target_controller_command_gate(  # noqa: SLF001
            checkpoint=checkpoint,
            phase=phase,
            target_ref="target",
            command_runner=lambda args, **_kwargs: pytest.fail(
                f"unexpected direct command: {args}"
            ),
            kube_context="context",
            checkpoint_writer=lambda: None,
        )

    assert len(admission_calls) == 1
    assert admission_calls[0]["target_uid"] == "cluster-uid"
    assert admission_calls[0]["target_resource_version"] == "42"
    assert admission_calls[0]["operation_label"] == "target controller gate repair"
    assert admission_calls[0]["window_purpose"] == "target-controller-gate-rearm"
    expected = migration.target_controller_gate_values({})["slurmNodes"]["controller"]["slurmctld"]
    assert admission_calls[0]["spec_patch"]["slurmNodes"]["controller"]["slurmctld"] == expected


def test_in_place_controller_roll_activates_one_exact_gated_workload_replica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {
        "controller_bridge": {
            "stage": migration.BridgeStage.TARGET_HA_ACTIVE.value,
            "authority": {
                "owner": "bridge-target",
                "source_restart_prohibited": True,
            },
            "target_singleton_takeover": {"command_gate_applied": True},
        }
    }
    expected = migration.target_controller_gate_values({})["slurmNodes"]["controller"]["slurmctld"]
    phase: dict[str, Any] = {
        "in_place_target_controller_command_gate": {
            "status": "slurmcluster-accepted",
            "slurmcluster_uid": "cluster-uid",
            "resource_version": "42",
            "contract_fingerprint": migration._fingerprint(expected),  # noqa: SLF001
            "accepted_at": "accepted",
            "slurmcluster_admission_window": {
                "schema": "nebius-cxcli/in-place-slurmcluster-admission-window-v1",
                "status": "restored",
                "purpose": "target-controller-gate-rearm",
                "original_failure_policy": "Fail",
                "target_ref": "target",
                "target_uid": "cluster-uid",
                "uid": "webhook-uid",
                "target_patched_at": "patched",
                "restored_at": "restored",
            },
        }
    }
    resources = iter(
        _target_owned_controller_gate_resources(
            observed_gate=expected,
            workload_gate=expected,
            workload_replicas=0,
        )
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: next(resources),
    )
    patches: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_kubectl_patch_namespace_resource",
        lambda **kwargs: patches.append(kwargs),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="activated one exact inert target controller replica",
    ):
        migration._ensure_in_place_target_controller_command_gate(  # noqa: SLF001
            checkpoint=checkpoint,
            phase=phase,
            target_ref="target",
            command_runner=lambda args, **_kwargs: pytest.fail(
                f"unexpected direct command: {args}"
            ),
            kube_context="context",
            checkpoint_writer=lambda: None,
        )

    assert len(patches) == 1
    assert patches[0]["resource"] == "statefulsets.apps.kruise.io/controller"
    assert patches[0]["patch"] == {
        "metadata": {"uid": "workload-uid", "resourceVersion": "84"},
        "spec": {"replicas": 1},
    }
    assert phase["in_place_target_controller_command_gate"]["status"] == ("workload-scale-accepted")


def test_in_place_singleton_gate_removal_uses_exact_admission_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_ref = "target-cluster"
    gated = {
        "metadata": {"uid": "target-uid", "resourceVersion": "41"},
        "spec": {
            "slurmNodes": {
                "controller": {
                    "slurmctld": {
                        "image": "example.invalid/controller:target",
                        "command": ["/bin/sh", "-ec"],
                        "args": ["sleep 30"],
                    }
                }
            }
        },
    }
    ungated = json.loads(json.dumps(gated))
    ungated["metadata"]["resourceVersion"] = "42"
    ungated["spec"]["slurmNodes"]["controller"]["slurmctld"].pop("command")
    ungated["spec"]["slurmNodes"]["controller"]["slurmctld"].pop("args")
    resources = iter((gated, ungated))
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: next(resources),
    )
    policy = {"value": "Fail", "resource_version": 10}

    def _admission(**_kwargs: Any) -> dict[str, Any]:
        return {
            "name": "soperator-validating-webhook-configuration",
            "uid": "webhook-uid",
            "resourceVersion": str(policy["resource_version"]),
            "webhook_name": "vslurmcluster-v1.kb.io",
            "webhook_index": 1,
            "failure_policy": policy["value"],
        }

    def _patch_policy(*, desired_policy: str, **_kwargs: Any) -> None:
        policy["value"] = desired_policy
        policy["resource_version"] += 1

    monkeypatch.setattr(migration, "_soperator_slurmcluster_admission_webhook", _admission)
    monkeypatch.setattr(
        migration,
        "_patch_soperator_slurmcluster_admission_policy",
        _patch_policy,
    )
    patches: list[Mapping[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_kubectl_patch_namespace_resource",
        lambda **kwargs: patches.append(kwargs["patch"]),
    )
    state: dict[str, Any] = {}
    writes: list[str] = []

    migration._remove_in_place_target_controller_command_gate(  # noqa: SLF001
        state=state,
        target_ref=target_ref,
        command_runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
        kube_context="context",
        checkpoint_writer=lambda: writes.append(
            str(state.get("slurmcluster_admission_window", {}).get("status") or "")
        ),
    )

    assert policy["value"] == "Fail"
    assert state["slurmcluster_admission_window"]["status"] == "restored"
    assert state["controller_gate_removed_at"]
    assert patches[0]["metadata"] == {
        "uid": "target-uid",
        "resourceVersion": "41",
    }
    assert writes[:3] == ["intent", "active", "target-patched"]
    assert "restored" in writes


def test_in_place_singleton_gate_removal_recovers_lost_patch_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = {
        "metadata": {"uid": "target-uid", "resourceVersion": "42"},
        "spec": {
            "slurmNodes": {
                "controller": {"slurmctld": {"image": "example.invalid/controller:target"}}
            }
        },
    }
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: target,
    )
    policy = {"value": "Ignore"}

    def _admission(**_kwargs: Any) -> dict[str, Any]:
        return {
            "name": "soperator-validating-webhook-configuration",
            "uid": "webhook-uid",
            "resourceVersion": "12",
            "webhook_name": "vslurmcluster-v1.kb.io",
            "webhook_index": 1,
            "failure_policy": policy["value"],
        }

    def _patch_policy(*, desired_policy: str, **_kwargs: Any) -> None:
        policy["value"] = desired_policy

    monkeypatch.setattr(migration, "_soperator_slurmcluster_admission_webhook", _admission)
    monkeypatch.setattr(
        migration,
        "_patch_soperator_slurmcluster_admission_policy",
        _patch_policy,
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_patch_namespace_resource",
        lambda **_kwargs: pytest.fail("an accepted target patch must not be replayed"),
    )
    state = {
        "slurmcluster_admission_window": {
            "schema": "nebius-cxcli/in-place-slurmcluster-admission-window-v1",
            "status": "active",
            "target_ref": "target-cluster",
            "target_uid": "target-uid",
            "name": "soperator-validating-webhook-configuration",
            "uid": "webhook-uid",
            "webhook_name": "vslurmcluster-v1.kb.io",
            "webhook_index": 1,
            "original_failure_policy": "Fail",
        }
    }

    migration._remove_in_place_target_controller_command_gate(  # noqa: SLF001
        state=state,
        target_ref="target-cluster",
        command_runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    assert policy["value"] == "Fail"
    assert state["slurmcluster_admission_window"]["status"] == "restored"


def test_in_place_singleton_finalizer_resumes_after_backup_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase = {
        "in_place_target_native_controller_ha": {
            "cleanup_status": "singleton-config-accepted",
        },
        "in_place_target_manager_pause": {
            "status": "verified",
            "deployment_uid": "manager-uid",
            "original_replicas": 1,
        },
    }
    checkpoint = {
        "controller_bridge": {"target_singleton_takeover": {"command_gate_applied": True}}
    }
    monkeypatch.setattr(
        migration,
        "_verify_in_place_target_native_controller_ha",
        lambda **_kwargs: pytest.fail("retired backup must not be revalidated"),
    )
    removed: list[str] = []
    monkeypatch.setattr(
        migration,
        "_remove_in_place_target_controller_command_gate",
        lambda **_kwargs: removed.append("gate"),
    )
    monkeypatch.setattr(migration, "_kubectl_rollout_status", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_kubectl_exec_login",
        lambda **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Slurmctld(primary) at controller-0 is UP\n",
            stderr="",
        ),
    )
    commands: list[tuple[str, ...]] = []
    manager = {
        "metadata": {
            "uid": "manager-uid",
            "resourceVersion": "8",
            "generation": 4,
        },
        "spec": {"replicas": 0, "template": {"spec": {"containers": []}}},
        "status": {"observedGeneration": 4},
    }
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: copy.deepcopy(manager),
    )

    def _runner(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(tuple(args))
        if "patch" in args and "deployment/soperator-manager" in args:
            manager["spec"]["replicas"] = 1
            manager["metadata"].update({"resourceVersion": "9", "generation": 5})
            manager["status"] = {
                "observedGeneration": 5,
                "readyReplicas": 1,
                "availableReplicas": 1,
            }
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    lines = migration._finalize_in_place_target_native_controller_ha(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        target_ref="target-cluster",
        command_runner=_runner,
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    state = phase["in_place_target_native_controller_ha"]
    assert state["cleanup_status"] == "verified"
    assert phase["in_place_target_manager_pause"]["status"] == "restored"
    assert removed == ["gate"]
    manager_patch = next(
        command
        for command in commands
        if "patch" in command and "deployment/soperator-manager" in command
    )
    patch = json.loads(manager_patch[manager_patch.index("-p") + 1])
    assert patch[:3] == [
        {"op": "test", "path": "/metadata/uid", "value": "manager-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "8"},
        {"op": "test", "path": "/metadata/generation", "value": 4},
    ]
    assert lines


def test_in_place_completed_service_role_skips_retired_ha_revalidation() -> None:
    assert migration._in_place_service_role_authority_finalized(  # noqa: SLF001
        phase={
            "in_place_target_native_controller_ha": {"cleanup_status": "singleton-config-accepted"}
        },
        group_state={"status": "completed"},
    )
    assert not migration._in_place_service_role_authority_finalized(  # noqa: SLF001
        phase={
            "in_place_target_native_controller_ha": {"cleanup_status": "singleton-config-accepted"}
        },
        group_state={"status": "provider-complete-health-pending"},
    )


def test_service_role_replacement_blocks_when_controller_alias_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(
        migration,
        "active_passive_jail_rootfs_slots",
        lambda _values: SimpleNamespace(
            active_slot="slot-b",
            passive_slot="slot-a",
            active_pvc="slot-b-pvc",
        ),
    )
    monkeypatch.setattr(
        migration,
        "_verify_target_rootfs_handoff_consumers",
        lambda **_kwargs: [{"name": "login", "status": "verified"}],
    )
    monkeypatch.setattr(
        migration,
        "_wait_for_target_slurmcluster_available",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"metadata": {"name": "external-cluster"}},
    )
    monkeypatch.setattr(
        migration,
        "_verify_target_jail_alias_consumers",
        lambda **_kwargs: [
            {
                "kind": "statefulset",
                "name": "controller",
                "status": "pending",
                "reason": "workload rollout is not Ready",
            }
        ],
    )
    monkeypatch.setattr(
        migration,
        "_live_home_mount_probe_checks",
        lambda **_kwargs: ({"name": "home", "status": "passed"},),
    )
    checkpoint = {
        "phase_state": {
            migration.POPULATE_JAIL_REFRESH_PHASE_ID: {
                "rootfs_handoff_verification": {
                    "status": "verified",
                    "active_slot": "slot-b",
                }
            }
        }
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="controller.*workload rollout is not Ready",
    ):
        migration._verify_in_place_service_role_replacement_health(  # noqa: SLF001
            checkpoint=checkpoint,
            phase={},
            role="controller",
            payload={},
            source_report={},
            live_snapshot={},
            target_ref="external-cluster",
            kube_context="context",
            command_runner=SimpleNamespace(),
            checkpoint_writer=None,
        )


def test_in_place_compute_refuses_to_start_without_verified_slot_switch() -> None:
    checkpoint = {
        "phase_state": {
            "populate-jail-refresh": {
                "completed_at": "2026-07-13T00:00:00Z",
                "active_slot": "slot-a",
                "rollback_slot": "slot-b",
                "rootfs_slots": {
                    "active_slot": "slot-a",
                    "passive_slot": "slot-b",
                },
                "rootfs_handoff_verification": {
                    "status": "verified",
                    "active_slot": "slot-a",
                },
            }
        }
    }

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="slot activation"):
        migration._require_in_place_jail_slot_switch(  # noqa: SLF001
            checkpoint=checkpoint,
            planned_groups=(),
        )


@pytest.mark.parametrize(
    ("prior_active", "activated", "rollback"),
    (
        ("slot-a", "slot-b", "slot-a"),
        ("slot-b", "slot-a", "slot-b"),
        ("slot-a", "slot-b", "legacy-rootfs"),
    ),
)
def test_in_place_compute_accepts_verified_slot_switch_and_persistent_mounts(
    prior_active: str,
    activated: str,
    rollback: str,
) -> None:
    checkpoint = {
        "phase_state": {
            "populate-jail-refresh": {
                "completed_at": "2026-07-13T00:00:00Z",
                "active_slot": activated,
                "rollback_slot": rollback,
                "rootfs_slots": {
                    "active_slot": prior_active,
                    "passive_slot": activated,
                },
                "rootfs_handoff_verification": {
                    "status": "verified",
                    "active_slot": activated,
                },
                "persistent_jail_mounts": {"status": "verified"},
            }
        }
    }

    migration._require_in_place_jail_slot_switch(  # noqa: SLF001
        checkpoint=checkpoint,
        planned_groups=(),
    )


def test_in_place_compute_reuses_completed_segment_jail_handoff() -> None:
    populate = {
        "completed_at": "2026-07-13T00:00:00Z",
        "active_slot": "slot-b",
        "rollback_slot": "legacy-rootfs",
        "rootfs_slots": {
            "active_slot": "legacy-rootfs",
            "passive_slot": "slot-b",
        },
        "rootfs_handoff_verification": {
            "status": "verified",
            "active_slot": "slot-b",
        },
        "persistent_jail_mounts": {"status": "verified"},
    }
    checkpoint = {
        "phase_state": {},
        "completed_segment_ids": ["segment-1"],
        "segment_state": {
            "segment-1": {
                "completed_at": "2026-07-14T00:00:00Z",
                "completed_phases": [migration.POPULATE_JAIL_REFRESH_PHASE_ID],
                "operation_evidence": {
                    "phase_state": {
                        migration.POPULATE_JAIL_REFRESH_PHASE_ID: populate,
                    }
                },
            }
        },
    }

    assert migration._accepted_populate_jail_phase_state(checkpoint) == populate  # noqa: SLF001
    migration._require_in_place_jail_slot_switch(  # noqa: SLF001
        checkpoint=checkpoint,
        planned_groups=(),
    )


def test_in_place_runtime_identity_is_journaled_before_slurm_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = {
        "worker-0": {
            "pod_uid": "worker-pod-uid",
            "instance_id": "computeinstance-new",
            "node_addr": "worker-0.cluster-nodeset-svc.soperator.svc.cluster.local",
        }
    }
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_target_worker_pod_runtime_bindings",
        lambda **_kwargs: bindings,
    )

    def reconcile(**kwargs: Any) -> list[str]:
        calls.append(kwargs)
        return ["worker-0 runtime identity aligned"]

    monkeypatch.setattr(migration, "_reconcile_slurm_worker_runtime_identity", reconcile)
    monkeypatch.setattr(
        migration,
        "_slurm_worker_runtime_identity_snapshot",
        lambda **_kwargs: {
            "worker-0": {
                "instance_id": "computeinstance-new",
                "node_addr": "worker-0.cluster-nodeset-svc.soperator.svc.cluster.local",
            }
        },
    )
    phase: dict[str, Any] = {}
    writes: list[bool] = []

    lines = migration._ensure_in_place_slurm_worker_runtime_identity(  # noqa: SLF001
        checkpoint={},
        phase=phase,
        command_runner=SimpleNamespace(),
        kube_context="context",
        target_ref="cluster",
        checkpoint_writer=lambda: writes.append(True),
    )

    journal = phase["pre_rollout_worker_runtime_identity"]
    assert journal["status"] == "verified"
    assert journal["bindings"] == bindings
    assert calls[0]["via_login"] is True
    assert calls[0]["resume_not_responding"] is True
    assert calls[0]["worker_instances"] == {"worker-0": "computeinstance-new"}
    assert len(writes) == 2
    assert "exact current Ready Pod IPs" in lines[0]


def test_pre_rollout_runtime_identity_resumes_only_not_responding_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []

    def exec_login(**kwargs: Any) -> SimpleNamespace:
        scripts.append(str(kwargs["args"][-1]))
        return SimpleNamespace(returncode=0, stdout="worker-1 State DOWN -> RESUME\n", stderr="")

    monkeypatch.setattr(migration, "_kubectl_exec_login", exec_login)

    lines = migration._reconcile_slurm_worker_runtime_identity(  # noqa: SLF001
        command_runner=SimpleNamespace(),
        kube_context="context",
        worker_instances={"worker-1": "computeinstance-current"},
        worker_addresses={"worker-1": "10.0.0.8"},
        via_login=True,
        resume_not_responding=True,
    )

    assert lines == ["worker-1 State DOWN -> RESUME"]
    assert '"${current_state}" == *NOT_RESPONDING*' in scripts[0]
    assert 'scontrol update NodeName="${node}" State=RESUME' in scripts[0]


def test_later_segment_binds_fresh_provider_resource_version() -> None:
    live = _node_group(count=2)
    live["status"] = {
        "ready_node_count": 2,
        "target_node_count": 2,
        "node_count": 2,
        "outdated_node_count": 0,
    }
    planned_group = {
        "source": {
            "kubernetes_version": "1.31",
            "os": "ubuntu22.04",
            "drivers_preset": "cuda12.8",
        },
        "provider_identity": {
            "resource_uid": "mk8snodegroup-worker-a",
            "resource_version": 1,
            "reservation_policy": "",
            "reservation_ids": [],
            "failure_domains": [],
            "gpu_cluster_id": "",
        },
    }
    observed = {
        **planned_group["provider_identity"],
        "resource_version": 17,
    }
    group_state: dict[str, Any] = {}

    accepted = migration._bind_in_place_segment_provider_identity(  # noqa: SLF001
        checkpoint={"completed_segment_ids": ["segment-1"]},
        group_state=group_state,
        planned_group=planned_group,
        observed_provider_identity=observed,
        live=live,
        group_name="worker-a",
        provider_operation_journaled=False,
    )

    assert accepted["resource_version"] == 17
    assert group_state["segment_start_provider_identity"] == observed


def test_first_segment_rejects_unaccepted_provider_resource_version() -> None:
    live = _node_group(count=2)
    live["status"] = {
        "ready_node_count": 2,
        "target_node_count": 2,
        "node_count": 2,
        "outdated_node_count": 0,
    }
    planned_group = {
        "source": {
            "kubernetes_version": "1.31",
            "os": "ubuntu22.04",
            "drivers_preset": "cuda12.8",
        },
        "provider_identity": {
            "resource_uid": "mk8snodegroup-worker-a",
            "resource_version": 1,
            "reservation_policy": "",
            "reservation_ids": [],
            "failure_domains": [],
            "gpu_cluster_id": "",
        },
    }
    observed = {
        **planned_group["provider_identity"],
        "resource_version": 17,
    }

    with pytest.raises(RuntimeError, match="identity drifted"):
        migration._bind_in_place_segment_provider_identity(  # noqa: SLF001
            checkpoint={"completed_segment_ids": []},
            group_state={},
            planned_group=planned_group,
            observed_provider_identity=observed,
            live=live,
            group_name="worker-a",
            provider_operation_journaled=False,
        )


def test_replacement_node_uid_proof_requires_ready_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    node = {
        "metadata": {"name": "worker-a-0", "uid": "new-node-uid"},
        "status": {"conditions": [{"type": "Ready", "status": "False"}]},
    }
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"items": [node]},
    )

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="is not Ready"):
        migration._in_place_live_node_uids(  # noqa: SLF001
            command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            kube_context="test",
            node_group_id="worker-a",
            require_ready=True,
        )

    node["status"]["conditions"][0]["status"] = "True"  # type: ignore[index]
    assert migration._in_place_live_node_uids(  # noqa: SLF001
        command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        kube_context="test",
        node_group_id="worker-a",
        require_ready=True,
    ) == ("new-node-uid",)


def test_worker_drain_is_not_released_before_slurmd_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_state = {
        "slurm_drain": {
            "status": "applied",
            "nodes": ["worker-a-0"],
            "reason": "cxcli-in-place-test",
        }
    }
    monkeypatch.setattr(
        migration,
        "_in_place_slurm_nodes_after_drain",
        lambda **_kwargs: {
            "status": "blocked",
            "node_reasons": {"worker-a-0": "cxcli-in-place-test"},
        },
    )

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="will not release"):
        migration._restore_in_place_worker_group_lock(  # noqa: SLF001
            group_state=group_state,
            command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            kube_context="test",
            checkpoint_writer=None,
        )

    assert group_state["slurm_drain"]["status"] == "applied"


def test_tui_dispatched_cancel_is_counted_as_an_operator_cancellation() -> None:
    checkpoint = {
        "slurm": {
            "action_journal": {
                "actions": [
                    {
                        "action_id": "cancel-42",
                        "kind": "cancel",
                        "origin": "operator",
                        "state": "Dispatching",
                    },
                    {
                        "action_id": "queued-43",
                        "kind": "cancel",
                        "origin": "operator",
                        "state": "Queued",
                    },
                ]
            }
        }
    }

    assert (
        migration._in_place_operator_cancel_count(  # noqa: SLF001
            checkpoint,
            explicit_cancel_job_ids=(),
        )
        == 1
    )


def test_replacement_health_releases_owned_drain_before_gpu_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(
        migration,
        "active_passive_jail_rootfs_slots",
        lambda _values: SimpleNamespace(
            active_slot="slot-b",
            passive_slot="slot-a",
            active_pvc="jail-rootfs-slot-b",
        ),
    )
    monkeypatch.setattr(
        migration,
        "_wait_for_target_worker_nodesets_ready",
        lambda **_kwargs: calls.append("ready"),
    )
    monkeypatch.setattr(
        migration,
        "_verify_target_rootfs_handoff_consumers",
        lambda **_kwargs: [{"status": "verified"}],
    )
    monkeypatch.setattr(
        migration,
        "_live_home_mount_probe_checks",
        lambda **_kwargs: ({"status": "passed"},),
    )
    monkeypatch.setattr(
        migration,
        "_ensure_gpu_worker_jail_release_gate",
        lambda **_kwargs: calls.append("gpu-release") or ["gpu release passed"],
    )
    monkeypatch.setattr(migration, "_gpu_worker_nodeset_names", lambda _values: ("worker",))
    monkeypatch.setattr(
        migration,
        "_ensure_in_place_gpu_driver_refresh_after_provider_rollout",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_release_in_place_worker_locks_for_gpu_smoke",
        lambda **_kwargs: calls.append("drain-release") or ["drain release passed"],
    )
    monkeypatch.setattr(
        migration,
        "_ensure_jail_gpu_post_activation_gate",
        lambda **_kwargs: calls.append("gpu-health") or ["gpu health passed"],
    )
    checkpoint = {
        "phase_state": {
            "populate-jail-refresh": {"rootfs_handoff_verification": {"active_slot": "slot-b"}}
        }
    }
    phase: dict[str, Any] = {}

    lines = migration._verify_in_place_worker_replacement_health(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        payload={},
        source_report={},
        live_snapshot={},
        target_ref="cluster",
        kube_context="test",
        command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        checkpoint_writer=None,
    )

    assert calls == ["ready", "gpu-release", "drain-release", "gpu-health"]
    assert phase["in_place_worker_replacement_health"]["status"] == "passed"
    assert "slot-b Jail consumers" in lines[0]


def test_gpu_smoke_drain_release_requires_continuous_partition_pause() -> None:
    phase: dict[str, Any] = {"slurm_scheduling_pause": False}

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="continuous Slurm scheduling pause",
    ):
        migration._release_in_place_worker_locks_for_gpu_smoke(  # noqa: SLF001
            checkpoint={},
            phase=phase,
            command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            kube_context="test",
            values={"partitionConfiguration": {"partitions": [{"name": "gpu"}]}},
            checkpoint_writer=None,
        )

    assert phase["in_place_gpu_smoke_scheduling_gate"]["status"] == "failed"


def test_gpu_smoke_drain_release_accepts_only_target_retired_missing_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = (
        SimpleNamespace(
            partition="background",
            applied_state="DOWN",
            applied_record="PartitionName=background State=DOWN",
            applied_record_fingerprint="background-down",
        ),
        SimpleNamespace(
            partition="gpu",
            applied_state="DOWN",
            applied_record="PartitionName=gpu State=DOWN",
            applied_record_fingerprint="gpu-down",
        ),
    )
    monkeypatch.setattr(
        migration,
        "_rolling_compute_checkpoint_pause_records",
        lambda _phase: records,
    )
    monkeypatch.setattr(
        migration,
        "_external_upgrade_partition_state_snapshot",
        lambda **_kwargs: (SimpleNamespace(name="gpu", record="PartitionName=gpu State=DOWN"),),
    )
    monkeypatch.setattr(
        migration,
        "_slurm_partition_observation_matches",
        lambda observation, **_kwargs: observation.name == "gpu",
    )
    restored: list[str] = []
    monkeypatch.setattr(
        migration,
        "_restore_in_place_worker_group_lock",
        lambda **_kwargs: restored.append("worker") or ["worker drain released"],
    )
    phase = {
        "slurm_scheduling_pause": True,
        "in_place_node_groups": {"worker": {"status": "provider-complete-health-pending"}},
    }

    lines = migration._release_in_place_worker_locks_for_gpu_smoke(  # noqa: SLF001
        checkpoint={},
        phase=phase,
        command_runner=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        kube_context="test",
        values={"partitionConfiguration": {"partitions": [{"name": "gpu"}]}},
        checkpoint_writer=None,
    )

    gate = phase["in_place_gpu_smoke_scheduling_gate"]
    assert gate["status"] == "passed"
    assert gate["retired_partitions"] == ["background"]
    assert gate["partitions"][0]["status"] == "retired-from-target"
    assert gate["partitions"][1]["status"] == "down"
    assert restored == ["worker"]
    assert "source-era partitions" in lines[0]


def test_final_restore_filters_only_checkpointed_target_retired_partitions() -> None:
    target_partitions = ["gpu"]
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "in_place_gpu_smoke_scheduling_gate": {
                    "status": "passed",
                    "target_partitions": target_partitions,
                    "target_partitions_sha256": migration._fingerprint(target_partitions),  # noqa: SLF001
                    "retired_partitions": ["background"],
                    "partitions": [
                        {"partition": "background", "status": "retired-from-target"},
                        {"partition": "gpu", "status": "down"},
                    ],
                }
            }
        }
    }
    records = (SimpleNamespace(partition="background"), SimpleNamespace(partition="gpu"))

    retired = migration._checkpoint_target_retired_slurm_partition_names(  # noqa: SLF001
        checkpoint,
        records=records,  # type: ignore[arg-type]
    )

    assert retired == frozenset({"background"})


def test_final_restore_classifies_live_down_non_target_partition_as_retired() -> None:
    target_partitions = ["gpu"]
    checkpoint = {
        "phase_state": {
            "rolling-compute-migration": {
                "in_place_gpu_smoke_scheduling_gate": {
                    "status": "passed",
                    "target_partitions": target_partitions,
                    "target_partitions_sha256": migration._fingerprint(target_partitions),  # noqa: SLF001
                    "retired_partitions": [],
                    "partitions": [
                        {
                            "partition": "background",
                            "status": "down",
                            "target_configured": False,
                        },
                        {
                            "partition": "gpu",
                            "status": "down",
                            "target_configured": True,
                        },
                    ],
                }
            }
        }
    }
    records = (SimpleNamespace(partition="background"), SimpleNamespace(partition="gpu"))

    retired = migration._checkpoint_target_retired_slurm_partition_names(  # noqa: SLF001
        checkpoint,
        records=records,  # type: ignore[arg-type]
    )

    assert retired == frozenset({"background"})


def test_terminal_worker_provider_operation_resumes_at_health_boundary() -> None:
    state = {
        "status": "provider-complete-health-pending",
        "replacement_node_uids": ["node-a", "node-b"],
        "target_update": {"operation": {"attempt_state": "provider-terminal"}},
    }

    assert migration._in_place_worker_health_resume_ready(  # noqa: SLF001
        state,
        accepted_size=2,
    )
    assert not migration._in_place_worker_health_resume_ready(  # noqa: SLF001
        {**state, "replacement_node_uids": ["node-a"]},
        accepted_size=2,
    )
    assert not migration._in_place_worker_health_resume_ready(  # noqa: SLF001
        {
            **state,
            "target_update": {"operation": {"attempt_state": "provider-accepted"}},
        },
        accepted_size=2,
    )


class _ScaleApi:
    def __init__(self) -> None:
        self.groups = {
            "source": _scale_group("source", 5),
            "target": _scale_group("target", 2),
        }
        self.scales: list[tuple[str, int]] = []

    def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
        return self.groups[node_group_id]

    def scale_node_group(
        self,
        *,
        node_group_id: str,
        count: int,
        timeout_seconds: int,
        operation_accepted: migration.ProviderOperationAccepted | None = None,
    ) -> Mapping[str, Any]:
        assert timeout_seconds == 3000
        self.scales.append((node_group_id, count))
        if operation_accepted is not None:
            operation_accepted(f"operation-{node_group_id}-{count}")
        group = self.groups[node_group_id]
        group["spec"]["fixed_node_count"] = count
        group["metadata"]["resource_version"] += 1
        return {
            **group,
            "_cxcli_provider_operation_id": f"operation-{node_group_id}-{count}",
        }


def _scale_group(node_group_id: str, count: int) -> dict[str, Any]:
    return {
        "metadata": {
            "id": node_group_id,
            "uid": node_group_id,
            "resource_version": 1,
        },
        "spec": {"fixed_node_count": count},
    }


def test_blue_green_bootstrap_exchanges_capacity_incrementally() -> None:
    api = _ScaleApi()
    checkpoint = {
        "campaign_fingerprint": "a" * 64,
        "current_segment_id": "segment-1",
        "operation_intent": migration._new_operation_intent(  # noqa: SLF001
            campaign_fingerprint="a" * 64,
            segment_id="segment-1",
        ),
    }
    phase: dict[str, Any] = {}
    rolling = {
        "target_node_groups": {
            "worker:worker-a": {
                "id": "target",
                "compute_role": "worker",
                "source_fixed_node_count": 5,
                "bootstrap_node_count": 2,
                "fixed_node_count": 2,
            }
        }
    }
    old_groups = {
        "worker-a": {
            "id": "source",
            "source_fixed_node_count": 5,
            "replacement_keys": ["worker:worker-a"],
        }
    }

    lines = migration._exchange_blue_green_worker_capacity(  # noqa: SLF001
        checkpoint=checkpoint,
        phase=phase,
        rolling=rolling,
        old_groups=old_groups,
        worker_group_names=("worker-a",),
        nebius_api=api,  # type: ignore[arg-type]
        checkpoint_writer=lambda: None,
    )

    assert api.scales == [("source", 3), ("target", 4), ("source", 2), ("target", 5)]
    assert api.groups["source"]["spec"]["fixed_node_count"] == 2
    assert api.groups["target"]["spec"]["fixed_node_count"] == 5
    assert rolling["target_node_groups"]["worker:worker-a"]["fixed_node_count"] == 5
    exchange = phase["worker_capacity_exchanges"]["worker-a"]
    assert exchange["remaining_source_overlap"] == 2
    assert len(exchange["steps"]) == 2
    assert all(step.get("completed_at") for step in exchange["steps"])
    assert "target 5/5" in lines[0]
