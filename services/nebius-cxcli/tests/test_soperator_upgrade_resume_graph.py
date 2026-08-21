from __future__ import annotations

import pytest

from nebius_cxcli import soperator_migration as migration


def test_report_phase_order_uses_locked_campaign_plan() -> None:
    locked_plan = (
        "discovery-and-plan",
        "customer-approval",
        migration._CONTROLLER_HA_BRIDGE_PHASE_ID,  # noqa: SLF001
        migration._TARGET_GPU_STACK_PHASE_ID,  # noqa: SLF001
        migration.POPULATE_JAIL_REFRESH_PHASE_ID,
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    )
    checkpoint = {
        "planned_phases": list(locked_plan),
        "completed_phases": list(locked_plan[:4]),
        "phase_state": {
            migration.POPULATE_JAIL_REFRESH_PHASE_ID: {},
            "rolling-compute-migration": {},
        },
    }

    assert (
        migration._external_upgrade_report_phase_ids(  # noqa: SLF001
            phase_ids=locked_plan,
            checkpoint=checkpoint,
        )
        == locked_plan
    )


def test_resume_frontier_is_earliest_incomplete_locked_phase() -> None:
    plan = ("discovery-and-plan", "customer-approval", "controller-ha-bridge")

    assert migration._derive_external_upgrade_resume_frontier(  # noqa: SLF001
        phase_ids=plan,
        completed_phases={"discovery-and-plan", "customer-approval"},
    ) == {
        "phase_id": "controller-ha-bridge",
        "phase_index": 2,
        "completed_prefix": ["discovery-and-plan", "customer-approval"],
    }


def test_resume_frontier_rejects_completed_phase_after_hole() -> None:
    with pytest.raises(RuntimeError, match="completed phase after the earliest incomplete"):
        migration._derive_external_upgrade_resume_frontier(  # noqa: SLF001
            phase_ids=(
                "discovery-and-plan",
                "customer-approval",
                "controller-ha-bridge",
            ),
            completed_phases={"discovery-and-plan", "controller-ha-bridge"},
        )


def test_resume_frontier_rejects_reordered_completed_prefix() -> None:
    with pytest.raises(RuntimeError, match="exact ordered prefix"):
        migration._derive_external_upgrade_resume_frontier(  # noqa: SLF001
            phase_ids=(
                "discovery-and-plan",
                "customer-approval",
                "controller-ha-bridge",
            ),
            completed_phases=("customer-approval", "discovery-and-plan"),
        )


def test_completed_phase_invalidation_removes_dependent_suffix() -> None:
    completed = {
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
        "populate-jail-refresh",
        "rolling-compute-migration",
        "validation-and-rollback-hold",
    }
    plan = (
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
        "populate-jail-refresh",
        "rolling-compute-migration",
        "validation-and-rollback-hold",
    )

    removed = migration._invalidate_external_upgrade_completed_suffix(  # noqa: SLF001
        completed_phases=completed,
        phase_ids=plan,
        from_phase="populate-jail-refresh",
    )

    assert removed == (
        "populate-jail-refresh",
        "rolling-compute-migration",
        "validation-and-rollback-hold",
    )
    assert completed == {
        "discovery-and-plan",
        "customer-approval",
        "controller-ha-bridge",
    }


def test_active_mutation_boundary_set_includes_bridge_provider_operation() -> None:
    checkpoint = {
        "completed_phases": ["discovery-and-plan", "customer-approval"],
        "pending_phase": "controller-ha-bridge",
        "controller_bridge": {
            "stage": "planned",
            "node_groups": [
                {
                    "operation": {
                        "operation_kind": "mk8s-node-group-create",
                        "requested_at": "2026-08-13T18:30:39Z",
                        "provider_operation_id": "operation-bridge-a",
                    }
                }
            ],
        },
    }

    assert migration._external_upgrade_active_mutation_boundaries(  # noqa: SLF001
        checkpoint
    ) == frozenset({"controller-bridge-provider"})


def test_bridge_provider_boundary_does_not_trust_stale_pending_phase() -> None:
    checkpoint = {
        "completed_phases": ["discovery-and-plan", "customer-approval"],
        "pending_phase": "none",
        "controller_bridge": {
            "stage": "planned",
            "node_groups": [
                {
                    "operation": {
                        "operation_kind": "mk8s-node-group-create",
                        "requested_at": "2026-08-13T18:30:39Z",
                        "provider_operation_id": "operation-bridge-a",
                    }
                }
            ],
        },
    }

    assert migration._external_upgrade_active_mutation_boundaries(  # noqa: SLF001
        checkpoint
    ) == frozenset({"controller-bridge-provider"})


def test_bridge_provider_boundary_protects_only_locked_predecessors() -> None:
    plan = (
        "discovery-and-plan",
        "customer-approval",
        migration._CONTROLLER_HA_BRIDGE_PHASE_ID,  # noqa: SLF001
        migration.POPULATE_JAIL_REFRESH_PHASE_ID,
        "rolling-compute-migration",
    )

    assert migration._external_upgrade_protected_completed_predecessors(  # noqa: SLF001
        phase_ids=plan,
        active_boundaries={"controller-bridge-provider"},
    ) == frozenset({"discovery-and-plan", "customer-approval"})
