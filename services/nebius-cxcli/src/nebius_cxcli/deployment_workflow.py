"""Shared direct-execution workflow with immutable intent and forward recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .deployment_dependencies import PriorDeletion
from .deployment_plan import DeploymentPlan, DeploymentStage, DeploymentStageKind
from .deployment_recovery import execution_checkpoint
from .deployment_state import DeploymentGeneration, DeploymentState, ObjectVersion, digest
from .duration_utils import parse_optional_duration_seconds


@dataclass(frozen=True)
class StageAdmission:
    stage: DeploymentStage
    terraform: Mapping[str, Any]
    application: Mapping[str, Any]
    prerequisite_deletions: tuple[PriorDeletion, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage.as_payload(),
            "terraform": dict(self.terraform),
            "application": dict(self.application),
            "prerequisiteDeletions": [item.as_payload() for item in self.prerequisite_deletions],
        }


class DeploymentExecutor(Protocol):
    def admit(self, plan: DeploymentPlan) -> Sequence[StageAdmission]:
        """Check every intermediate, dependency, quota and capability before mutation."""
        ...

    def restore_admissions(self, admissions: Sequence[StageAdmission]) -> None: ...

    def execute(self, admission: StageAdmission, *, recovering: bool) -> None:
        """Reobserve first, refresh an in-scope plan, and execute the declared stage."""
        ...

    def verify(self, admission: StageAdmission) -> Mapping[str, Any] | None:
        """Independent authoritative postconditions; None means stage still needed."""
        ...

    def verify_final(self, plan: DeploymentPlan) -> Mapping[str, Any]: ...

    def acceptance_generations(self) -> Sequence[DeploymentGeneration]: ...

    def recovery_cache(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DeploymentResult:
    preview: bool
    plan: Mapping[str, Any]
    evidence: Mapping[str, Any]


def _semantic_controls(controls: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(controls)
    for key in ("jobWaitTimeout", "jobRefreshInterval"):
        if key in result:
            value = result[key]
            if not isinstance(value, str):
                raise RuntimeError(f"Deployment duration control {key} must be a string")
            result[key] = parse_optional_duration_seconds(value, option_name=key)
    return result


def run_deployment(
    *,
    generation: DeploymentGeneration,
    plan: DeploymentPlan,
    state: DeploymentState,
    executor: DeploymentExecutor,
    dry_run: bool,
    controls: Mapping[str, Any],
) -> DeploymentResult:
    """Admit all stages, then recover or execute with no user-facing approval protocol."""
    state.assert_publishable(generation.identity)
    prior = state.read()
    active = prior.value.get("active") if prior else None
    from .soperator_acceptance import current_control

    acceptance = current_control()
    frozen_plan = active.get("plan") if isinstance(active, Mapping) else None
    if isinstance(active, Mapping) and frozen_plan is not None:
        acceptance.restore(active.get("acceptance", frozen_plan.get("acceptance", {})))
        recorded = frozen_plan.get("controls")
        if not isinstance(recorded, Mapping):
            raise RuntimeError("Active deployment has incomplete execution controls")
        recorded_semantic = _semantic_controls(recorded)
        requested_semantic = _semantic_controls(controls)
        if recorded_semantic != requested_semantic:
            differing = sorted(
                key
                for key in recorded.keys() | controls.keys()
                if recorded_semantic.get(key) != requested_semantic.get(key)
            )
            raise RuntimeError(
                "Deployment recovery requires the same execution controls; differing fields: "
                + ", ".join(differing)
                + ". Rerun with the original deployment options."
            )
        if frozen_plan.get("semanticPlan") != plan.as_payload():
            raise RuntimeError("Deployment recovery differs from its frozen semantic plan")
    if frozen_plan is None:
        admissions = tuple(executor.admit(plan))
    else:
        raw = frozen_plan.get("admissions")
        if not isinstance(raw, list):
            raise RuntimeError("Active deployment has incomplete stage admissions")
        admissions = tuple(
            StageAdmission(
                DeploymentStage(
                    DeploymentStageKind(item["stage"]["name"]),
                    item["stage"]["config"],
                    tuple(item["stage"]["retiredGroups"]),
                    tuple(item["stage"]["addedGroups"]),
                ),
                item["terraform"],
                item["application"],
                tuple(PriorDeletion(**row) for row in item["prerequisiteDeletions"]),
            )
            for item in raw
        )
        executor.restore_admissions(admissions)
    if [item.stage for item in admissions] != list(plan.stages):
        raise RuntimeError("Deployment executor did not admit every declared stage in order")
    reviewed = {
        "semanticPlan": plan.as_payload(),
        "controls": dict(controls),
        "admissions": [item.as_payload() for item in admissions],
        "acceptance": acceptance.snapshot(),
    }
    if dry_run:
        # Preview does not claim an active generation, write acceptance, or advance
        # checkpoints. Any temporary Terraform binary belongs to this preview only.
        return DeploymentResult(True, reviewed, {})
    record = state.begin(generation, plan=reviewed, recovery=executor.recovery_cache())
    stage_key = "final-verification"
    try:
        for index, admission in enumerate(admissions):
            stage_key = f"{index}:{admission.stage.name.value}"
            prior_stage = _stage_record(record, stage_key)
            evidence = executor.verify(admission)
            if evidence is None:
                record = state.checkpoint(
                    record,
                    stage=stage_key,
                    acceptance=acceptance.snapshot(),
                    evidence={
                        "status": "executing",
                        "admission": digest(admission.as_payload()),
                    },
                )
                state.assert_held()

                def persist_recovery(_stage_key: str = stage_key) -> None:
                    nonlocal record
                    record = state.checkpoint(
                        record,
                        stage=_stage_key,
                        acceptance=acceptance.snapshot(),
                        evidence={
                            "status": "executing",
                            "recovery": executor.recovery_cache(),
                        },
                    )

                with execution_checkpoint(persist_recovery):
                    persist_recovery()
                    executor.execute(admission, recovering=prior_stage is not None)
                    persist_recovery()
                state.assert_held()
                evidence = executor.verify(admission)
                if evidence is None:
                    raise RuntimeError(
                        f"Deployment stage {admission.stage.name} did not reach its postconditions"
                    )
            record = state.checkpoint(
                record,
                stage=stage_key,
                acceptance=acceptance.snapshot(),
                evidence={
                    "status": "complete",
                    "observed": dict(evidence),
                },
            )
        stage_key = "final-verification"
        evidence = {**executor.verify_final(plan), "acceptanceControl": acceptance.snapshot()}
    except (Exception, KeyboardInterrupt) as exc:
        # Failure metadata is mutable evidence, never a replacement for intent.
        # Persist only a type/classification, not possibly secret exception text.
        try:
            state.assert_held()
            record = state.checkpoint(
                record,
                stage=stage_key,
                acceptance=acceptance.snapshot(),
                evidence={
                    **dict(_stage_record(record, stage_key) or {}),
                    "status": "recovery-required",
                    "failureType": type(exc).__name__,
                    "recovery": executor.recovery_cache(),
                },
            )
        except Exception as report_error:
            exc.add_note(f"Recovery checkpoint could not be updated: {type(report_error).__name__}")
        raise
    state.accept(record, evidence=evidence, derived_generations=executor.acceptance_generations())
    return DeploymentResult(False, reviewed, evidence)


def _stage_record(record: ObjectVersion, stage: str) -> Mapping[str, Any] | None:
    active = record.value.get("active")
    stages = active.get("stages") if isinstance(active, Mapping) else None
    value = stages.get(stage) if isinstance(stages, Mapping) else None
    return value if isinstance(value, Mapping) else None
