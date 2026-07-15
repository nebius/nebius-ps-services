from __future__ import annotations

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


def test_external_upgrade_campaign_and_execution_journal_are_v5_only() -> None:
    assert migration.SOPERATOR_MIGRATION_EXECUTION_SCHEMA.endswith("/v5")
    assert migration.SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA.endswith("/v5")


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


def test_v4_execution_journal_is_rejected_without_conversion(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({"schema": "nebius-cxcli-ext-soperator-upgrade-journal/v4"}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Unsupported external Soperator upgrade checkpoint"):
        migration._load_checkpoint(path)  # noqa: SLF001


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


def test_in_place_batch_impact_reports_aggregate_before_dispatch() -> None:
    lines = migration._in_place_batch_impact_lines(  # noqa: SLF001
        (
            ({"id": "worker-a", "name": "worker-a", "fixed_size": 100}, 100),
            ({"id": "worker-b", "name": "worker-b", "fixed_size": 100}, 100),
        ),
        ("worker-c: blocked - active Slurm allocations remain",),
    )

    assert "worker-a: permits 100/100 unavailable" in lines
    assert "worker-b: permits 100/100 unavailable" in lines
    assert "worker-c: blocked - active Slurm allocations remain" in lines
    assert "Batch permits up to 200 worker nodes to be unavailable." in lines
    assert "Each full-group dispatch may temporarily have zero Ready nodes." in lines
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


@pytest.mark.parametrize("field", ["history", "registrations", "catalogs"])
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
        command_runner=_runner,
        kube_context="context",
        checkpoint_writer=lambda: None,
    )

    assert any(command[-3:] == ("scontrol", "takeover", "1") for command in commands)
    assert phase["in_place_authority_transitions"]["controller"]["status"] == "verified"
    assert checkpoint["controller_bridge"]["controller_roles"][1]["role"] == "active"
    assert lines == [
        "Transferred bridge authority to the system domain before controller replacement."
    ]


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


def test_replacement_health_proves_slot_b_mounts_and_gpu_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(migration, "_patch_target_values_for_compute", lambda **_kwargs: {})
    monkeypatch.setattr(
        migration,
        "active_passive_jail_rootfs_slots",
        lambda _values: SimpleNamespace(active_slot="slot-b", passive_slot="slot-a"),
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

    assert calls == ["ready", "gpu-release", "gpu-health"]
    assert phase["in_place_worker_replacement_health"]["status"] == "passed"
    assert "slot-b Jail consumers" in lines[0]


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
