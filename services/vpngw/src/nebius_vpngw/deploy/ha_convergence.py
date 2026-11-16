"""Shared HA orchestration; apply owns locking, selectors and terminal output.

Existing private effect owners are injected explicitly by the CLI adapter.
This module never invokes a public command or publishes a terminal report.
"""

from __future__ import annotations

import shlex
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from .apply_plan import Evidence, Outcome, Result
from .ha_composite import ResultReady
from .ha_conversion import resolve as resolve_conversion
from .recovery import (
    CURRENT,
    ApprovalMismatch,
    ArgumentFailure,
    Invocation,
    PackagePreparationFailure,
    advice,
)

REPORT: ContextVar[Any] = ContextVar("ha_apply_report", default=None)


class Interrupted(Exception):
    def __init__(self, result):
        self.result = result


def local_convergence_ready(snapshot: Any) -> bool:
    from .apply_health import ha_convergence

    try:
        return ha_convergence(snapshot).local_runtime is Evidence.READY
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
        return False


def local_result(api: Any, result: Any, snapshot: Any, *, verified: bool = False) -> Any:
    from .apply_health import ha_convergence

    projection = ha_convergence(snapshot)
    report = REPORT.get()
    if report is not None:
        report.result = replace(
            report.result,
            local_runtime=projection.local_runtime,
            connectivity=projection.connectivity,
            authority=Evidence.READY,
        )
    if result.health is api.VMHACommandHealth.MAINTENANCE:
        return result
    if verified:
        result = replace(
            result,
            outcome=api.VMHACommandOutcome.HEALTHY,
            classification=api.VMHACommandClassification.CONVERGED,
            reasons=()
            if projection.connectivity is Evidence.READY
            else ("vpn-connectivity-" + projection.connectivity.value,),
            next_action=None
            if projection.connectivity is Evidence.READY
            else "Check the remote peer and routes; local HA configuration is converged",
        )
    return replace(
        result,
        health=(
            api.VMHACommandHealth.HEALTHY
            if projection.connectivity is Evidence.READY
            else api.VMHACommandHealth.DEGRADED
        ),
    )


def confirm_convergence(
    api: Any, config_path: Path, first: Any, *, region: str | None, progress_sink: Any
) -> Any:
    from .apply_health import ha_convergence

    if first.snapshot.view.overall == "HEALTHY" or not local_convergence_ready(first.snapshot):
        return api._confirm_vm_ha_healthy(
            config_path, first, region=region, progress_sink=progress_sink
        )
    before = ha_convergence(first.snapshot)
    second = api._inspect_vm_ha_status_with_region(config_path, region=region)
    after = ha_convergence(second.snapshot)
    if before.identity != after.identity or after.local_runtime is not Evidence.READY:
        raise RuntimeError("HA convergence observation changed before verification")
    return second


def converge(
    api: Any,
    local_config_file: Path,
    *,
    output: Path | None = None,
    force: bool = False,
    standby_auto_healing: Any = None,
    region: str | None = None,
    dry_run: bool = False,
    approve: str | None = None,
    output_format: Any = None,
) -> Any:
    result: Any
    effective_config_file = local_config_file
    observed_health = api.VMHACommandHealth.UNKNOWN
    convergence_effects_may_have_started = False
    interactive = (
        output_format is api._VMHAOutputFormat.TEXT and api._vm_ha_wizard_streams_interactive()
    )
    progress_reporter = api._vm_ha_progress_sink(api.sys.stderr)
    progress_sink: Any = progress_reporter
    try:
        resolve_progress_active = True
        api._emit_vm_ha_progress(
            progress_sink, api._VMHAProgressPhase.RESOLVE_CONFIG, api._VMHAProgressState.STARTED
        )

        def finish_resolve_progress() -> None:
            nonlocal resolve_progress_active
            if not resolve_progress_active:
                return
            resolve_progress_active = False
            api._emit_vm_ha_progress(
                progress_sink,
                api._VMHAProgressPhase.RESOLVE_CONFIG,
                api._VMHAProgressState.COMPLETED,
            )

        try:
            effective = resolve_conversion(
                api=api,
                source_path=local_config_file,
                output=output,
                force=force,
                dry_run=dry_run,
                interactive=interactive,
                region=region,
                before_interactive_wizard=finish_resolve_progress,
                progress_sink=progress_sink,
                ordinary_trust_preflight=api._ordinary_vm_ha_conversion_trust_prerequisite,
            )
        except BaseException:
            if resolve_progress_active:
                resolve_progress_active = False
                api._emit_vm_ha_progress(
                    progress_sink,
                    api._VMHAProgressPhase.RESOLVE_CONFIG,
                    api._VMHAProgressState.FAILED,
                )
            raise
        finish_resolve_progress()
        if isinstance(effective, api.VMHACommandResult):
            effective_config_file = effective.effective_config_file
            if approve is None:
                result = effective
            else:
                result = api.VMHACommandResult(
                    outcome=api.VMHACommandOutcome.BLOCKED,
                    classification=api.VMHACommandClassification.AMBIGUOUS_STATE,
                    health=effective.health,
                    effective_config_file=effective.effective_config_file,
                    actions=effective.actions,
                    reasons=("approval-not-applicable",),
                    next_action="rerun without --approve and use only a digest emitted by apply",
                )
        else:
            effective_config_file = effective.path
            if context := CURRENT.get():
                context.select_config(effective.path)
            with api._vm_ha_progress_step(progress_sink, api._VMHAProgressPhase.INSPECT_STATE):
                inspection = api._inspect_vm_ha_status_with_region(effective.path, region=region)
            context = CURRENT.get()
            if context is not None:
                context.project = inspection.project_id
                if inspection.plan is not None:
                    context.region = context.region or inspection.plan.gateway_group.region
            actions = effective.actions
            policy_result: Any = None
            if standby_auto_healing is not None:
                from . import ha_composite

                if standby_auto_healing is api.StandbyAutoHealing.ENABLED and ha_composite.pending(
                    api, effective.path, "enable-standby-auto-healing", inspection=inspection
                ):
                    raise ResultReady(
                        ha_composite.converge(
                            api,
                            effective.path,
                            "enable-standby-auto-healing",
                            dry_run=dry_run,
                            approve=approve,
                            region=region,
                            interactive=interactive,
                        )
                    )
                try:
                    policy_statuses = api._run_vm_ha_auto_healing_statuses(
                        local_config_file=effective.path,
                        desired=standby_auto_healing,
                        inspection=inspection,
                        require_capability=True,
                    )
                except api.MissingCapability:
                    if standby_auto_healing is not api.StandbyAutoHealing.ENABLED:
                        raise
                    raise ResultReady(
                        ha_composite.converge(
                            api,
                            effective.path,
                            "enable-standby-auto-healing",
                            dry_run=dry_run,
                            approve=approve,
                            region=region,
                            interactive=interactive,
                        )
                    ) from None
                if not dry_run:
                    inspection, policy_statuses = api._observe_vm_ha_auto_healing_recovery(
                        effective.path,
                        inspection,
                        policy_statuses,
                        desired=standby_auto_healing,
                        region=region,
                        progress_sink=progress_sink,
                    )
                cleanup_prerequisite_effects: Any = ()
                cleanup_recovery_digests: Any = ()
                cleanup_plan_statuses: Any = None
                cleanup_confirmed_enabled = False
                completed_recoveries: Any = None
                if (
                    len(policy_statuses) == 2
                    and any(
                        status.get("recovery_phase") == api.AutoHealingRecoveryPhase.COMPLETED.value
                        for status in policy_statuses
                    )
                    and (
                        not api._vm_ha_auto_healing_pending_recovery_policy(
                            policy_statuses,
                            desired=standby_auto_healing,
                            owner_node_id=inspection.snapshot.authority.owner_node_id,
                        )
                    )
                ):
                    try:
                        completed_recoveries = api._exact_completed_vm_ha_auto_healing_recovery(
                            policy_statuses
                        )
                        if completed_recoveries is None:
                            raise RuntimeError("completed recovery cleanup evidence is missing")
                        cleanup_prerequisite_effects = ("clear-completed-recovery",)
                        cleanup_recovery_digests = tuple(
                            sorted(
                                (
                                    api.auto_healing_recovery_digest(recovery)
                                    for _, recovery in completed_recoveries
                                )
                            )
                        )
                        cleanup_plan_statuses = policy_statuses
                        projected_policy_statuses = [
                            {**status, "recovery": None, "recovery_phase": None}
                            if status.get("recovery_phase")
                            == api.AutoHealingRecoveryPhase.COMPLETED.value
                            else status
                            for status in policy_statuses
                        ]
                        if dry_run:
                            policy_statuses = projected_policy_statuses
                        else:
                            if approve is not None:
                                if standby_auto_healing is api.StandbyAutoHealing.ENABLED:
                                    cleanup_plan = api._vm_ha_auto_healing_cleanup_noop_plan_result(
                                        config_path=effective.path,
                                        desired=standby_auto_healing,
                                        inspection=inspection,
                                        statuses=cleanup_plan_statuses,
                                        recoveries=completed_recoveries,
                                    )
                                    if cleanup_plan.approval is None:
                                        raise RuntimeError(
                                            "completed recovery cleanup approval is missing"
                                        )
                                    cleanup_approval_digest = cleanup_plan.approval.digest
                                else:
                                    projected_transaction = (
                                        api._vm_ha_auto_healing_transaction_for_statuses(
                                            desired=standby_auto_healing,
                                            inspection=inspection,
                                            statuses=projected_policy_statuses,
                                        )
                                    )
                                    projected_recovery_required = (
                                        api._vm_ha_auto_healing_recovery_required(
                                            desired=standby_auto_healing,
                                            inspection=inspection,
                                            statuses=projected_policy_statuses,
                                        )
                                    )
                                    cleanup_approval_digest = (
                                        api._vm_ha_auto_healing_approval_digest(
                                            desired=standby_auto_healing,
                                            inspection=inspection,
                                            statuses=projected_policy_statuses,
                                            transaction=projected_transaction,
                                            recovery_required=projected_recovery_required,
                                            prerequisite_effects=cleanup_prerequisite_effects,
                                            prerequisite_recovery_digests=cleanup_recovery_digests,
                                        )
                                    )
                                if approve != cleanup_approval_digest:
                                    policy_result = api.VMHACommandResult(
                                        outcome=api.VMHACommandOutcome.BLOCKED,
                                        classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                                        health=api.VMHACommandHealth.BLOCKED,
                                        effective_config_file=effective.path,
                                        actions=actions,
                                        reasons=("approval-digest-stale-or-incorrect",),
                                        next_action="rerun apply to obtain the current exact approval digest",
                                    )
                            if policy_result is None:
                                if not api._clear_completed_vm_ha_auto_healing_recovery(
                                    config_path=effective.path, statuses=cleanup_plan_statuses
                                ):
                                    raise RuntimeError(
                                        "completed recovery cleanup evidence is missing"
                                    )
                                actions = (*actions, "standby-auto-healing-recovery-cleared")
                                policy_statuses = api._run_vm_ha_auto_healing_statuses(
                                    local_config_file=effective.path,
                                    desired=standby_auto_healing,
                                    inspection=inspection,
                                    require_capability=True,
                                )
                                if any(
                                    status.get("recovery") is not None
                                    or status.get("recovery_phase") is not None
                                    for status in policy_statuses
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery remained after cleanup"
                                    )
                                approve = None
                                cleanup_prerequisite_effects = ()
                                cleanup_recovery_digests = ()
                        if policy_result is None:
                            if not api._vm_ha_auto_healing_matches_cleaned_recovery(
                                policy_statuses, completed_recoveries[0][0]
                            ):
                                raise RuntimeError(
                                    "standby auto-healing policy changed during recovery cleanup"
                                )
                            cleanup_confirmed_enabled = True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        policy_result = api.VMHACommandResult(
                            outcome=api.VMHACommandOutcome.BLOCKED,
                            classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                            health=api.VMHACommandHealth.BLOCKED,
                            effective_config_file=effective.path,
                            actions=actions,
                            reasons=("standby-auto-healing-recovery-cleanup-required",),
                            next_action="inspect VM-HA agent journals for the recovery owner, then rerun the same requested apply command",
                        )
                if policy_result is None and (
                    any(status.get("record") is None for status in policy_statuses)
                    and standby_auto_healing is api.StandbyAutoHealing.DISABLED
                ):
                    policy_result = api.VMHACommandResult(
                        outcome=api.VMHACommandOutcome.BLOCKED,
                        classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                        health=api.VMHACommandHealth.BLOCKED,
                        effective_config_file=effective.path,
                        actions=actions,
                        reasons=("standby-auto-healing-policy-unavailable",),
                        next_action="run apply with this CLI version to initialize the explicit two-member policy, then rerun apply",
                    )
                elif policy_result is None and (
                    api._vm_ha_auto_healing_is_terminal(policy_statuses, standby_auto_healing)
                    or (
                        cleanup_confirmed_enabled
                        and standby_auto_healing is api.StandbyAutoHealing.ENABLED
                    )
                ):
                    if cleanup_prerequisite_effects:
                        if completed_recoveries is None or cleanup_plan_statuses is None:
                            raise RuntimeError("completed recovery cleanup evidence is missing")
                        policy_result = api._vm_ha_auto_healing_cleanup_noop_plan_result(
                            config_path=effective.path,
                            desired=standby_auto_healing,
                            inspection=inspection,
                            statuses=cleanup_plan_statuses,
                            recoveries=completed_recoveries,
                        )
                    elif standby_auto_healing is api.StandbyAutoHealing.DISABLED:
                        policy_result = api.VMHACommandResult(
                            outcome=api.VMHACommandOutcome.MAINTENANCE,
                            classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                            health=api.VMHACommandHealth.MAINTENANCE,
                            effective_config_file=effective.path,
                            actions=(*actions, "standby-auto-healing-already-disabled"),
                            reasons=("maintenance-ready",),
                            next_action="rerun apply with --standby-auto-healing enabled when maintenance is complete",
                        )
                    else:
                        actions = (*actions, "standby-auto-healing-already-enabled")
                elif policy_result is None:
                    transaction = api._vm_ha_auto_healing_transaction_for_statuses(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                    )
                    recovery_required = api._vm_ha_auto_healing_recovery_required(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                    )
                    bootstrap_required = bool(
                        recovery_required
                        and len(policy_statuses) == 1
                        and (
                            policy_statuses[0].get("desired")
                            != api.StandbyAutoHealing.DISABLED.value
                        )
                    )
                    owner_initialization_required = bool(
                        bootstrap_required and policy_statuses[0].get("record") is None
                    )
                    recovery_phase = (
                        api.t.cast(str | None, policy_statuses[0].get("recovery_phase"))
                        if recovery_required
                        else None
                    )
                    recovery_start_required = (
                        api._vm_ha_auto_healing_recovery_start_required(
                            inspection=inspection, statuses=policy_statuses
                        )
                        if recovery_required
                        else False
                    )
                    approval_digest = api._vm_ha_auto_healing_approval_digest(
                        desired=standby_auto_healing,
                        inspection=inspection,
                        statuses=policy_statuses,
                        transaction=transaction,
                        recovery_required=recovery_required,
                        prerequisite_effects=cleanup_prerequisite_effects,
                        prerequisite_recovery_digests=cleanup_recovery_digests,
                    )
                    planned_policy = api._vm_ha_auto_healing_plan_result(
                        config_path=effective.path,
                        desired=standby_auto_healing,
                        transaction=transaction,
                        approval_digest=approval_digest,
                        recovery_required=recovery_required,
                        bootstrap_required=bootstrap_required,
                        owner_initialization_required=owner_initialization_required,
                        recovery_phase=recovery_phase,
                        recovery_start_required=recovery_start_required,
                        dry_run=dry_run,
                        prerequisite_effects=cleanup_prerequisite_effects,
                    )
                    if approve is not None and approve != approval_digest:
                        policy_result = api.VMHACommandResult(
                            outcome=api.VMHACommandOutcome.BLOCKED,
                            classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                            health=api.VMHACommandHealth.BLOCKED,
                            effective_config_file=effective.path,
                            actions=actions,
                            reasons=("approval-digest-stale-or-incorrect",),
                            next_action="rerun apply to obtain the current exact approval digest",
                        )
                    elif dry_run or (
                        approve is None
                        and (not interactive)
                        and api._vm_ha_result_requires_approval(planned_policy)
                    ):
                        policy_result = planned_policy
                    elif (
                        approve is None
                        and api._vm_ha_result_requires_approval(planned_policy)
                        and (not api._confirm_vm_ha_apply_plan(planned_policy))
                    ):
                        policy_result = api._vm_ha_operator_declined(planned_policy)
                    else:
                        with api.contextlib.nullcontext():
                            current_inspection = api._inspect_vm_ha_status_with_region(
                                effective.path, region=region
                            )
                            current_statuses = api._run_vm_ha_auto_healing_statuses(
                                local_config_file=effective.path,
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                require_capability=True,
                            )
                            current_transaction = api._vm_ha_auto_healing_transaction_for_statuses(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                            )
                            current_recovery_required = api._vm_ha_auto_healing_recovery_required(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                            )
                            current_bootstrap_required = bool(
                                current_recovery_required
                                and len(current_statuses) == 1
                                and (
                                    current_statuses[0].get("desired")
                                    != api.StandbyAutoHealing.DISABLED.value
                                )
                            )
                            current_owner_initialization_required = bool(
                                current_bootstrap_required
                                and current_statuses[0].get("record") is None
                            )
                            current_recovery_start_required = (
                                api._vm_ha_auto_healing_recovery_start_required(
                                    inspection=current_inspection, statuses=current_statuses
                                )
                                if current_recovery_required
                                else False
                            )
                            current_approval_digest = api._vm_ha_auto_healing_approval_digest(
                                desired=standby_auto_healing,
                                inspection=current_inspection,
                                statuses=current_statuses,
                                transaction=current_transaction,
                                recovery_required=current_recovery_required,
                            )
                            if not (
                                current_transaction == transaction
                                and current_approval_digest == approval_digest
                                and (current_bootstrap_required == bootstrap_required)
                                and (
                                    current_owner_initialization_required
                                    == owner_initialization_required
                                )
                                and (current_recovery_start_required == recovery_start_required)
                            ):
                                raise api._VMHAApplyConvergenceFailed(
                                    "Standby auto-healing authority changed after policy planning.",
                                    reason="approval-state-changed",
                                )
                            mark_effects_started()
                            recovery_owner_node_id: Any = None
                            if current_recovery_required:
                                owner_status = current_statuses[0]
                                recovery_owner_node_id = str(owner_status["node_id"])
                                if current_owner_initialization_required:
                                    initialized = api._run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="initialize",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if len(initialized) != 1:
                                        raise RuntimeError(
                                            "owner policy initialization did not complete exactly"
                                        )
                                    current_statuses = api._run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if (
                                        api._vm_ha_auto_healing_transaction(
                                            desired=standby_auto_healing, statuses=current_statuses
                                        )
                                        != transaction
                                    ):
                                        raise RuntimeError(
                                            "owner policy initialization changed the approved transaction"
                                        )
                                    api._vm_ha_auto_healing_recovery_required(
                                        desired=standby_auto_healing,
                                        inspection=current_inspection,
                                        statuses=current_statuses,
                                    )
                                    owner_status = current_statuses[0]
                                current_recovery_phase = owner_status.get("recovery_phase")

                                def arm_recovery(
                                    owner_node_id: str, target_node_id: str, stopped_revision: str
                                ) -> None:
                                    if (
                                        current_recovery_phase
                                        == api.AutoHealingRecoveryPhase.COMPLETED.value
                                        and (not current_recovery_start_required)
                                    ):
                                        raise RuntimeError(
                                            "completed standby recovery approval does not authorize another start"
                                        )
                                    api._arm_vm_ha_auto_healing_recovery(
                                        config_path=effective.path,
                                        owner_status=owner_status,
                                        transaction=transaction,
                                        approval_digest=approval_digest,
                                        owner_node_id=owner_node_id,
                                        target_node_id=target_node_id,
                                        stopped_revision=stopped_revision,
                                    )

                                def exact_recovery_progress() -> bool:
                                    recovery_statuses = api._run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        node_ids=frozenset({recovery_owner_node_id}),
                                    )
                                    if len(recovery_statuses) != 1:
                                        return False
                                    recovery_status = recovery_statuses[0]
                                    return api._vm_ha_auto_healing_recovery_progress_is_exact(
                                        recovery_status=recovery_status,
                                        recovery_owner_node_id=recovery_owner_node_id,
                                        transaction=transaction,
                                        approval_digest=approval_digest,
                                    )

                                api._prepare_vm_ha_planned_target(
                                    local_config_file=effective.path,
                                    target_role=None,
                                    region=region,
                                    show_auth_progress=False,
                                    progress_sink=progress_sink,
                                    before_rearm_request=arm_recovery,
                                    on_rearm_authorization_aborted=lambda: (
                                        api._cancel_vm_ha_auto_healing_recovery(
                                            config_path=effective.path,
                                            owner_node_id=recovery_owner_node_id,
                                            transaction=transaction,
                                            approval_digest=approval_digest,
                                        )
                                    ),
                                    rearm_request_progress_is_exact=exact_recovery_progress,
                                )
                                current_statuses = api._run_vm_ha_auto_healing_action(
                                    local_config_file=effective.path,
                                    action="status",
                                    require_capability=True,
                                )
                                if current_bootstrap_required:
                                    peer_node_id = next(
                                        node_id
                                        for node_id in transaction.member_node_ids
                                        if node_id != recovery_owner_node_id
                                    )
                                    api._run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="initialize",
                                        node_ids=frozenset({peer_node_id}),
                                    )
                                    current_statuses = api._run_vm_ha_auto_healing_action(
                                        local_config_file=effective.path,
                                        action="status",
                                        require_capability=True,
                                    )
                                if (
                                    api._vm_ha_auto_healing_transaction(
                                        desired=standby_auto_healing, statuses=current_statuses
                                    )
                                    != transaction
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-transaction-changed-after-recovery"
                                    )
                                transaction_inspection = api._inspect_vm_ha_status_with_region(
                                    effective.path, region=region
                                )
                                if (
                                    transaction_inspection.snapshot.authority.owner_node_id
                                    != current_inspection.snapshot.authority.owner_node_id
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-owner-changed-after-recovery"
                                    )
                            else:
                                transaction_inspection = current_inspection

                            def require_policy_authority() -> None:
                                observed = api._inspect_vm_ha_status_with_region(
                                    effective.path, region=region
                                )
                                if (
                                    observed.snapshot.authority_digest
                                    != transaction_inspection.snapshot.authority_digest
                                ):
                                    raise RuntimeError(
                                        "standby-auto-healing-authority-changed-during-transaction"
                                    )

                            terminal_statuses = api._execute_vm_ha_auto_healing_policy(
                                config_path=effective.path,
                                desired=standby_auto_healing,
                                transaction=transaction,
                                initial_statuses=current_statuses,
                                authority_guard=require_policy_authority,
                            )
                            if recovery_owner_node_id is not None or any(
                                status.get("recovery_phase")
                                == api.AutoHealingRecoveryPhase.COMPLETED.value
                                for status in current_statuses
                            ):
                                exact_recoveries = api._exact_completed_vm_ha_auto_healing_recovery(
                                    terminal_statuses
                                )
                                if exact_recoveries is None or (
                                    recovery_owner_node_id is not None
                                    and (
                                        not any(
                                            (
                                                record.node_id == recovery_owner_node_id
                                                for record, _ in exact_recoveries
                                            )
                                        )
                                    )
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery owner changed before cleanup"
                                    )
                                if not api._clear_completed_vm_ha_auto_healing_recovery(
                                    config_path=effective.path, statuses=terminal_statuses
                                ):
                                    raise RuntimeError(
                                        "completed standby recovery was not safely cleared"
                                    )
                        inspection = api._inspect_vm_ha_status_with_region(
                            effective.path, region=region
                        )
                        inspection = api._observe_vm_ha_auto_healing_projection(
                            effective.path,
                            inspection,
                            desired=standby_auto_healing,
                            expected_owner_node_id=transaction_inspection.snapshot.authority.owner_node_id,
                            expected_observation_digest=transaction_inspection.snapshot.authority.observation_digest,
                            region=region,
                        )
                        actions = (*actions, f"standby-auto-healing-{standby_auto_healing.value}")
                        if standby_auto_healing is api.StandbyAutoHealing.DISABLED:
                            if inspection.snapshot.view.overall != "MAINTENANCE":
                                raise RuntimeError(
                                    "standby auto-healing policy committed but stable maintenance authority was not re-proven"
                                )
                            policy_result = api.VMHACommandResult(
                                outcome=api.VMHACommandOutcome.MAINTENANCE,
                                classification=api.VMHACommandClassification.MAINTENANCE_POLICY,
                                health=api.VMHACommandHealth.MAINTENANCE,
                                effective_config_file=effective.path,
                                actions=actions,
                                reasons=("maintenance-ready",),
                                next_action="rerun apply with --standby-auto-healing enabled when maintenance is complete",
                            )
                        approve = None
            transition_reason: Any = None
            if policy_result is not None:
                result = policy_result
            elif (
                inspection.snapshot.view.overall == "TRANSITIONING"
                or inspection.snapshot.view.action == "wait"
            ) and (not api._vm_ha_snapshot_is_apply_owned_transition(inspection.snapshot)):
                inspection, transition_reason = api._observe_vm_ha_controller_transition(
                    effective.path, inspection, region=region, progress_sink=progress_sink
                )
                actions = (*actions, "controller-observed")
            if policy_result is not None:
                pass
            elif inspection.snapshot.view.overall == "HEALTHY":
                inspection = confirm_convergence(
                    api, effective.path, inspection, region=region, progress_sink=progress_sink
                )
                result = api._vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=inspection.snapshot,
                    actions=actions,
                    dry_run=dry_run,
                )
            else:
                result = api._vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=inspection.snapshot,
                    actions=actions,
                    dry_run=dry_run,
                )
            if policy_result is None and (
                transition_reason is not None
                and result.classification is api.VMHACommandClassification.CONTROLLER_TRANSITION
            ):
                result = api.VMHACommandResult(
                    outcome=api.VMHACommandOutcome.BLOCKED
                    if transition_reason == "controller-observation-contradictory"
                    else result.outcome,
                    classification=result.classification,
                    health=result.health,
                    effective_config_file=result.effective_config_file,
                    actions=result.actions,
                    reasons=api.dedupe_reason_codes([*result.reasons, transition_reason]),
                    impact=result.impact,
                    approval=result.approval,
                    next_action="resume apply to continue observing the same controller operation"
                    if transition_reason == "controller-observation-budget-exhausted"
                    else "inspect VM-HA controller service journals on both members, then rerun apply",
                )
            if policy_result is None and (
                result.classification
                in {
                    api.VMHACommandClassification.APPLY_REQUIRED,
                    api.VMHACommandClassification.HEALTHY,
                }
                or result.outcome is api.VMHACommandOutcome.MAINTENANCE
                or local_convergence_ready(inspection.snapshot)
            ):
                if local_convergence_ready(inspection.snapshot):
                    result = local_result(api, result, inspection.snapshot)
                observed_health = result.health
                with api._vm_ha_progress_step(
                    progress_sink, api._VMHAProgressPhase.PLAN_CONVERGENCE
                ):
                    apply_plan = api._plan_vm_ha_convergence_with_region(
                        effective.path, region=region, inspection=inspection
                    )
                if apply_plan.kind == "unchanged" and REPORT.get() is not None:
                    REPORT.get().verified(
                        changed=False,
                        connectivity=REPORT.get().result.connectivity,
                        inhibition_retained=result.health is api.VMHACommandHealth.MAINTENANCE,
                    )
                planned_result = api._vm_ha_apply_plan_result(
                    config_path=effective.path, prior=result, report=apply_plan, dry_run=dry_run
                )
                if approve is not None and (
                    apply_plan.kind == "unchanged" or approve != apply_plan.digest
                ):
                    result = api.VMHACommandResult(
                        outcome=api.VMHACommandOutcome.BLOCKED,
                        classification=api.VMHACommandClassification.AMBIGUOUS_STATE,
                        health=result.health,
                        effective_config_file=effective.path,
                        actions=effective.actions,
                        reasons=("approval-digest-stale-or-incorrect",),
                        next_action="rerun apply to obtain the current exact approval digest",
                    )
                elif (
                    dry_run
                    or planned_result.approval is None
                    or (
                        approve is None
                        and (not interactive)
                        and api._vm_ha_result_requires_approval(planned_result)
                        and (not apply_plan.authorization_persisted)
                    )
                ):
                    result = planned_result
                elif (
                    approve is None
                    and api._vm_ha_result_requires_approval(planned_result)
                    and (not apply_plan.authorization_persisted)
                    and (not api._confirm_vm_ha_apply_plan(planned_result))
                ):
                    result = api._vm_ha_operator_declined(planned_result)
                else:
                    with api.contextlib.ExitStack() as lock_stack:
                        with api._vm_ha_progress_step(
                            progress_sink, api._VMHAProgressPhase.ACQUIRE_LOCK
                        ):
                            lock_stack.enter_context(api.contextlib.nullcontext())
                        with api._vm_ha_progress_step(
                            progress_sink, api._VMHAProgressPhase.REVALIDATE_APPROVAL
                        ):
                            if apply_plan.kind in {
                                "artifact-standby-recovery",
                                "replacement-artifact-recovery",
                            }:
                                current_inspection = api._inspect_vm_ha_status_with_region(
                                    effective.path, region=region
                                )
                                current_plan = api._plan_vm_ha_convergence_with_region(
                                    effective.path, region=region, inspection=current_inspection
                                )
                            else:
                                current_plan = api._plan_vm_ha_convergence_with_region(
                                    effective.path, region=region
                                )
                            if current_plan != apply_plan:
                                raise RuntimeError("apply-approval-authority-changed")
                            if current_plan.artifact is not None:
                                current_plan.artifact.verify_current()
                        mark_effects_started()
                        convergence_effects_may_have_started = True
                        api._execute_vm_ha_convergence_with_region(
                            effective.path, current_plan, region=region, progress_sink=progress_sink
                        )
                    with api._vm_ha_progress_step(
                        progress_sink, api._VMHAProgressPhase.INSPECT_STATE
                    ):
                        healed = api._inspect_vm_ha_status_with_region(
                            effective.path, region=region
                        )
                    if healed.snapshot.view.overall != "MAINTENANCE":
                        healed = confirm_convergence(
                            api, effective.path, healed, region=region, progress_sink=progress_sink
                        )
                    result = api._vm_ha_result_from_snapshot(
                        config_path=effective.path,
                        snapshot=healed.snapshot,
                        actions=(
                            *actions,
                            "created-missing-non-owner-vm"
                            if apply_plan.kind == "active-standby-replacement"
                            else "apply-converged",
                        ),
                        dry_run=False,
                    )
                    if local_convergence_ready(healed.snapshot):
                        result = local_result(api, result, healed.snapshot, verified=True)
            elif (
                policy_result is None
                and approve is not None
                and (
                    not (
                        result.classification is api.VMHACommandClassification.HEALTHY
                        and result.outcome is api.VMHACommandOutcome.HEALTHY
                    )
                )
            ):
                result = api.VMHACommandResult(
                    outcome=api.VMHACommandOutcome.BLOCKED,
                    classification=api.VMHACommandClassification.AMBIGUOUS_STATE,
                    health=result.health,
                    effective_config_file=effective.path,
                    actions=effective.actions,
                    reasons=("approval-not-applicable",),
                    next_action="rerun without --approve and use only a digest emitted by apply",
                )
            elif policy_result is None and (
                result.classification is api.VMHACommandClassification.STANDBY_REARM
                and (not dry_run)
            ):
                with api.contextlib.ExitStack() as lock_stack:
                    with api._vm_ha_progress_step(
                        progress_sink, api._VMHAProgressPhase.ACQUIRE_LOCK
                    ):
                        lock_stack.enter_context(api.contextlib.nullcontext())
                    with api._vm_ha_progress_step(
                        progress_sink, api._VMHAProgressPhase.INSPECT_STATE
                    ):
                        current = api._inspect_vm_ha_status_with_region(
                            effective.path, region=region
                        )
                    if (
                        not api._vm_ha_snapshot_is_rearmable(current.snapshot)
                        or current.snapshot.authority_digest != inspection.snapshot.authority_digest
                    ):
                        raise RuntimeError("rearm-authority-changed")
                    with (
                        api.contextlib.redirect_stdout(api.io.StringIO()),
                        api.contextlib.redirect_stderr(api.io.StringIO()),
                    ):
                        mark_effects_started()
                        api._prepare_vm_ha_planned_target(
                            local_config_file=effective.path,
                            target_role=None,
                            command="apply",
                            region=region,
                            show_auth_progress=False,
                            progress_sink=progress_sink,
                        )
                with api._vm_ha_progress_step(progress_sink, api._VMHAProgressPhase.INSPECT_STATE):
                    healed = api._inspect_vm_ha_status_with_region(effective.path, region=region)
                healed = confirm_convergence(
                    api, effective.path, healed, region=region, progress_sink=progress_sink
                )
                result = api._vm_ha_result_from_snapshot(
                    config_path=effective.path,
                    snapshot=healed.snapshot,
                    actions=(*actions, "standby-rearmed"),
                    dry_run=False,
                )
    except api.WizardInterrupted:
        result = api.VMHACommandResult(
            outcome=api.VMHACommandOutcome.FAILED,
            classification=api.VMHACommandClassification.FAILED,
            health=api.VMHACommandHealth.UNKNOWN,
            effective_config_file=output or api._default_vm_ha_candidate_path(local_config_file),
            reasons=("input-interrupted",),
            next_action="rerun apply when the required input is available",
        )
        raise Interrupted(result) from None
    except ResultReady as completed:
        result = completed.result
    except (KeyboardInterrupt, EOFError, api.typer.Abort):
        result = api.VMHACommandResult(
            outcome=api.VMHACommandOutcome.FAILED,
            classification=api.VMHACommandClassification.FAILED,
            health=api.VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=("interrupted",),
            next_action="rerun apply",
        )
        raise Interrupted(result) from None
    except api.WizardCancelled:
        result = api._vm_ha_action_required(
            config_path=output or api._default_vm_ha_candidate_path(local_config_file),
            classification=api.VMHACommandClassification.CONVERSION_REQUIRED,
            health=api.VMHACommandHealth.NOT_CONFIGURED,
            reason="conversion-cancelled",
            next_action="rerun apply when ready to publish the candidate",
        )
    except api._VMHAApplyPlanningFailed as error:
        if error.classification is api.VMHACommandClassification.FAILED:
            result = api.VMHACommandResult(
                outcome=api.VMHACommandOutcome.FAILED,
                classification=error.classification,
                health=observed_health,
                effective_config_file=effective_config_file,
                reasons=(error.reason,),
                next_action=error.next_action,
            )
        else:
            result = api._vm_ha_action_required(
                config_path=effective_config_file,
                classification=error.classification,
                health=observed_health,
                reason=error.reason,
                next_action=error.next_action,
            )
    except (api.VMHAAgentArtifactError, api.ReplayWindowCapabilityError) as error:
        problem = (
            api.VMHAAgentArtifactProblem.INCOMPATIBLE
            if isinstance(error, api.ReplayWindowCapabilityError)
            else error.problem
        )
        reason, next_action = api._VM_HA_AGENT_ARTIFACT_PREREQUISITES[problem]
        if convergence_effects_may_have_started:
            reason = f"{reason}-during-convergence"
            next_action = f"{next_action}; apply will inspect durable checkpoints and resume idempotently, but gateway changes may already have started"
        result = api._vm_ha_action_required(
            config_path=effective_config_file,
            classification=api.VMHACommandClassification.EXTERNAL_PREREQUISITE,
            health=observed_health,
            reason=reason,
            next_action=next_action,
            actions=("convergence-effects-may-have-started",)
            if convergence_effects_may_have_started
            else (),
        )
    except api.typer.Exit as error:
        if api._vm_ha_error_chain_has_sdk_code(
            error, "UNAUTHENTICATED"
        ) or api.error_chain_has_cli_authentication_failure(error):
            reason = "authentication-or-provider-unavailable"
            next_action = "restore authentication and rerun apply"
        else:
            reason = "command-preflight-failed"
            next_action = "resolve the reported VM-HA preflight failure and rerun apply"
        result = api.VMHACommandResult(
            outcome=api.VMHACommandOutcome.FAILED,
            classification=api.VMHACommandClassification.FAILED,
            health=api.VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=(reason,),
            next_action=next_action,
        )
    except api._VMHAApplyConvergenceFailed as error:
        context = CURRENT.get() or Invocation("apply", effective_config_file, region=region)
        context.config = effective_config_file
        reason_codes = (error.reason, *error.diagnostics)
        pending = error.pending
        result = api.VMHACommandResult(
            outcome=api.VMHACommandOutcome.PENDING
            if error.pending
            else api.VMHACommandOutcome.FAILED,
            classification=api.VMHACommandClassification.EXTERNAL_PREREQUISITE
            if error.pending
            else api.VMHACommandClassification.FAILED,
            health=api.VMHACommandHealth.UNKNOWN if error.pending else observed_health,
            effective_config_file=effective_config_file,
            actions=("convergence-effects-may-have-started",) if not dry_run else (),
            reasons=(error.reason, *error.diagnostics),
            next_action=error.next_action,
            recovery=advice(
                context,
                error,
                codes=reason_codes,
                pending=pending,
            )
            if isinstance(error, PackagePreparationFailure)
            else None,
        )
    except (OSError, RuntimeError, TypeError, ValueError, api.WizardValidationError) as error:
        context = CURRENT.get() or Invocation("apply", effective_config_file, region=region)
        context.config = effective_config_file
        recovery = advice(context, error)
        result = api.VMHACommandResult(
            outcome=api.VMHACommandOutcome.FAILED,
            classification=api.VMHACommandClassification.FAILED,
            health=api.VMHACommandHealth.UNKNOWN,
            effective_config_file=effective_config_file,
            reasons=("convergence-failed-safely",),
            next_action=recovery.next_action,
            recovery=recovery,
        )
    finally:
        progress_reporter.close_unfinished()
    return result


# Admission and reporting remain outside the effect orchestration above.


class ReportedExit(click.exceptions.Exit):
    """A complete outer ApplyReport already describes this terminal outcome."""


def validate_options(options: dict[str, Any]) -> None:
    modes = sum(
        bool(options.get(key))
        for key in ("convert_to_ha", "rotate_mtls", "standby_auto_healing", "repair")
    )
    if modes > 1:
        raise ArgumentFailure("action-conflict")
    if (options.get("output") is not None or options.get("force")) and not options.get(
        "convert_to_ha"
    ):
        raise ArgumentFailure("conversion-options-required")
    approvals = (
        "approve_disruption",
        "approve_vm_ha_migration",
        "recover_vm_ha_migration",
        "replace_failed_vm_ha_passive",
    )
    if options.get("approve") is not None and any(
        options.get(key)
        for key in (*approvals, "recreate_gw", "resume_replacement", "prepare_vm_ha_peer_rotation")
    ):
        raise ArgumentFailure("approval-conflict")
    if modes and any(
        options.get(key)
        for key in (
            "recreate_gw",
            "resume_replacement",
            "prepare_vm_ha_peer_rotation",
            *approvals,
        )
    ):
        raise ArgumentFailure("specialized-conflict")
    if options.get("repair") and (options.get("approve") is not None or options.get("dry_run")):
        raise ArgumentFailure("repair-confirmation")
    if modes and options.get("sa"):
        raise ArgumentFailure("ordinary-service-account")


def rotate(
    api: Any,
    config_path: Path,
    *,
    dry_run: bool,
    approve: str | None,
    region: str | None,
    interactive: bool,
) -> Any:
    """Plan and execute rotation under apply's already-held canonical lock."""
    from . import ha_composite

    pending = ha_composite.pending(api, config_path, "rotate-mtls")
    try:
        inspected = None if pending else api._inspect_vm_ha_mtls_rotation(config_path)
    except api.MissingCapability:
        pending = True
    if pending:
        return ha_composite.converge(
            api,
            config_path,
            "rotate-mtls",
            dry_run=dry_run,
            approve=approve,
            region=region,
            interactive=interactive,
        )
    assert inspected is not None
    approval = api.VMHACommandApproval(
        kind="rotate-mtls",
        digest=inspected.digest,
        effects=("rotate-mtls",),
    )
    planned = api.VMHACommandResult(
        outcome=api.VMHACommandOutcome.PLANNED
        if dry_run
        else api.VMHACommandOutcome.ACTION_REQUIRED,
        classification=api.VMHACommandClassification.VM_HA_REQUIRED,
        health=api.VMHACommandHealth.UNKNOWN,
        effective_config_file=config_path,
        approval=approval,
        impact=api.VMHACommandImpact(
            summary="Rotate both mTLS identities; failover and rearm pause until verified completion",
            destructive=False,
            vpn_traffic_interruption=False,
            resource_creation=False,
        ),
        next_action="rerun apply --rotate-mtls with --approve " + inspected.digest,
    )
    if approve is not None and approve != inspected.digest:
        raise ApprovalMismatch("managed mTLS rotation approval digest does not match")
    if dry_run or (approve is None and not interactive):
        return planned
    if approve is None:
        for line in api._render_vm_ha_mtls_rotation_plan(inspected):
            api.typer.echo(line, err=True)
        if not api.typer.confirm(
            "Execute this exact VM-HA mTLS rotation?", default=False, err=True
        ):
            return api._vm_ha_operator_declined(planned)
    current = api._inspect_vm_ha_mtls_rotation(config_path)
    if current.digest != inspected.digest:
        raise api.PreparationBlocked("approval-state-changed")
    progress = api._vm_ha_progress_sink(api.sys.stderr)
    try:
        with api._vm_ha_progress_step(progress, api._VMHAProgressPhase.ROTATE_MTLS):
            mark_effects_started()
            api._execute_vm_ha_mtls_rotation(current)
    finally:
        progress.close_unfinished()
    return replace(
        planned,
        outcome=api.VMHACommandOutcome.HEALTHY,
        actions=("mtls-rotated",),
        approval=None,
        next_action=None,
    )


def mark_effects_started() -> None:
    """Record approved HA effects in the single outer apply result."""
    if report := REPORT.get():
        report.started = True


def _project(api: Any, report: Any, result: Any, *, specialized: bool = False) -> int:
    context = CURRENT.get() or Invocation("apply", result.effective_config_file)
    report.ha = {
        "outcome": result.outcome.value,
        "verification_scope": result.verification_scope,
        "failover_tested": result.failover_tested,
        "effective_config_file": str(result.effective_config_file),
        "health": result.health.value,
        "classification": result.classification.value,
        "actions": list(result.actions),
        "reasons": list(result.reasons),
        "impact": result.impact.to_dict() if result.impact else None,
        "approval": result.approval.to_dict() if result.approval else None,
        "next_action": result.next_action,
    }
    if result.approval:
        report.review_approval = result.approval.digest
        report.plan(result.approval.effects, approval=result.approval.digest)
        report.next_command = (
            replace(
                context,
                resume_options=tuple(
                    value for value in context.resume_options if value != "--dry-run"
                ),
            ).command_line("apply", resume=True, fresh_approval=True)
            + " --approve "
            + shlex.quote(result.approval.digest)
        )
    if {"operator-declined-approval", "conversion-cancelled"} & set(result.reasons):
        report.cancelled = True
        return 0
    if result.outcome in {api.VMHACommandOutcome.HEALTHY, api.VMHACommandOutcome.MAINTENANCE}:
        changed = report.started or any(
            action
            not in {
                "candidate-reused",
                "controller-observed",
                "standby-auto-healing-already-enabled",
                "standby-auto-healing-already-disabled",
                "apply-converged",
            }
            for action in result.actions
        )
        # Keep canonical configuration and connectivity evidence from the engine;
        # a specialized action or a controller snapshot cannot prove YAML parity.
        report.verified(
            changed=changed or report.result.outcome in {Outcome.APPLIED, Outcome.RESUMED},
            configuration=report.result.configuration,
            authority=Evidence.READY,
            local_runtime=report.result.local_runtime if specialized else Evidence.READY,
            connectivity=report.result.connectivity,
            inhibition_retained=result.outcome is api.VMHACommandOutcome.MAINTENANCE,
        )
        return 0
    if result.outcome is api.VMHACommandOutcome.PLANNED:
        return 0
    if result.outcome is api.VMHACommandOutcome.ACTION_REQUIRED and (
        result.approval is not None or result.reasons == ("approval-required",)
    ):
        return 1
    pending = result.outcome is api.VMHACommandOutcome.PENDING
    report.recovery = result.recovery or advice(
        context, codes=result.reasons or ("approval-required",), pending=pending
    )
    if result.next_action:
        report.recovery = replace(report.recovery, next_action=result.next_action)
    report.next_command = report.next_command or report.recovery.resume_command
    report.recovery = replace(report.recovery, resume_command=report.next_command)
    report.result = Result(
        Outcome.PENDING if pending else Outcome.FAILED if report.started else Outcome.BLOCKED,
        Evidence.UNKNOWN,
        Evidence.UNKNOWN,
        Evidence.UNKNOWN,
        Evidence.UNKNOWN,
    )
    if pending:
        report.pending_reason = result.reasons[0] if result.reasons else "waiting-for-authority"
    return 1


def run(api: Any, report: Any, **options: Any) -> None:
    """Select the canonical engine or HA orchestration without command recursion."""
    validate_options(options)
    convert = options.pop("convert_to_ha", False)
    output = options.pop("output", None)
    force = options.pop("force", False)
    rotation = options.pop("rotate_mtls", False)
    policy = options.pop("standby_auto_healing", None)
    approve = options.pop("approve", None)
    output_format = options.pop("output_format", api.ApplyOutputFormat.TEXT)
    if options.get("repair"):
        api._apply_impl(**options, result_report=report)
        return
    path = options["local_config_file"]
    config = (
        api.load_local_config(path, allow_missing_tunnel_psk_placeholders=True)
        if convert
        else api.load_local_config(path)
    )
    is_ha = bool(((config.get("gateway_group") or {}).get("vm_ha") or {}).get("enabled"))
    if not is_ha and (rotation or policy is not None or approve is not None and not convert):
        raise ArgumentFailure("vm-ha-required")
    if (
        not is_ha
        and not convert
        or any(
            options.get(key)
            for key in (
                "recreate_gw",
                "resume_replacement",
                "prepare_vm_ha_peer_rotation",
                "approve_disruption",
                "approve_vm_ha_migration",
                "recover_vm_ha_migration",
                "replace_failed_vm_ha_passive",
            )
        )
    ):
        api._apply_impl(**options, result_report=report)
        return
    if options.get("sa"):
        raise ArgumentFailure("ordinary-service-account")
    dry_run = options.get("dry_run", False)
    region = options.get("region")
    ha_format = api._VMHAOutputFormat(output_format.value)
    token = REPORT.set(report)
    try:
        if rotation:
            result = rotate(
                api,
                path,
                dry_run=dry_run,
                approve=approve,
                region=region,
                interactive=ha_format is api._VMHAOutputFormat.TEXT
                and api._vm_ha_wizard_streams_interactive(),
            )
        else:
            result = converge(
                api,
                path,
                output=output,
                force=force,
                standby_auto_healing=policy,
                region=region,
                dry_run=dry_run,
                approve=approve,
                output_format=ha_format,
            )
            if (
                "standby-rearmed" in result.actions
                and result.outcome is api.VMHACommandOutcome.HEALTHY
            ):
                # Rearm proves restored redundancy, then the same planner proves
                # desired configuration/software parity before terminal success.
                prior_actions = result.actions
                result = converge(
                    api,
                    result.effective_config_file,
                    region=region,
                    dry_run=dry_run,
                    output_format=ha_format,
                )
                result = replace(result, actions=(*prior_actions, *result.actions))
        code = _project(api, report, result, specialized=rotation or policy is not None)
    except Interrupted as error:
        _project(api, report, error.result)
        raise ReportedExit(130) from None
    finally:
        REPORT.reset(token)
    if code:
        raise ReportedExit(code)


def render(report: Any) -> str:
    details = report.ha or {}
    lines = [
        "Apply cancelled."
        if report.cancelled
        else "Apply plan."
        if report.dry_run
        else "Apply needs approval."
        if details.get("outcome") == "action-required"
        else "Apply completed."
    ]
    if details.get("effective_config_file"):
        lines.append("Configuration: " + details["effective_config_file"])
    if details.get("health"):
        lines.append("HA health: " + details["health"])
    if details.get("impact"):
        lines.append("Impact: " + details["impact"]["summary"])
    if details.get("next_action"):
        lines.append("Next action: " + details["next_action"])
    return "\n".join(lines)
