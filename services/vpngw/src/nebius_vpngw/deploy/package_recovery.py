"""Command-thread accounting for a package phase shared by fenced members."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..apply_approval import digest
from .activation_recovery import Diagnosis, Recovery, execute
from .ordinary_handoff import REPAIR, observe_guarded

COORDINATED: ContextVar[bool] = ContextVar("package_pair_coordinated", default=False)


def lifecycle_root(state: Any) -> Path:
    from .apply_command import CommandJournal

    return CommandJournal(state.project_id, state.gateway_name).root


@contextmanager
def owner_scope(root: Path, operation: str, authority: dict):
    """Explicit adapter for a persisted specialized lifecycle/replacement owner."""
    from .package_reservation import SCOPE

    parent = SCOPE.get()
    if parent is not None:
        root, execution, binding = parent
        yield Recovery(root, execution=execution, authority=binding)
        return
    binding = digest(authority)
    execution = digest({"operation": operation, "authority": binding})[:32]
    recovery = Recovery(root, execution=execution, authority=binding)
    recovery.initialize()
    token = SCOPE.set((root, execution, binding))
    try:
        yield recovery
    finally:
        SCOPE.reset(token)


def prepare(
    recovery: Recovery,
    *,
    effect: Callable[[], None],
    members: Callable[[], list[tuple[Any, Any, dict, dict | None]]],
    stage: str = "packages",
    handoffs: dict | None = None,
) -> None:
    repairing = False

    def run(_deadline: float | None) -> None:
        token = REPAIR.set(repairing)
        coordinated = COORDINATED.set(True)
        try:
            effect()
        finally:
            COORDINATED.reset(coordinated)
            REPAIR.reset(token)

    def observe(_deadline: float) -> dict:
        from .vm_ha_package import verify_prepared

        observations = []
        for ssh, plan, local, expected in members():
            holder = (handoffs or {}).get(plan.instance.hostname)
            if holder is not None:
                from ..package_worker import digest as phase_digest

                peer = holder._peer_request()  # Validate fresh peer authority independently.
                request = holder.job.submitted.get("prepare-package") or dict(
                    action="prepare-package", plan_digest=plan.digest, **peer
                )
                observed = holder.job.control(
                    {
                        "action": "worker-status",
                        "identity": holder.job.identity,
                        "phase": phase_digest(request),
                    },
                )
                if observed["state"] == "PREPARED":
                    holder.package_receipt = observed["result"]
                    holder.verify_prepared()
            else:
                observed = observe_guarded(ssh, plan, local, expected)
                if observed["state"] == "PREPARED":
                    verify_prepared(
                        ssh,
                        plan,
                        local,
                        checkpoint_conversion=expected,
                        convert=False,
                        allow_new_boot=True,
                    )
            observations.append(observed)
        states = [item["state"] for item in observations]
        if not states or "BLOCKED" in states:
            raise RuntimeError("package recovery authority is unavailable")
        complete = all(state == "PREPARED" for state in states)
        settled = all(
            state in {"PREPARED", "READY_TO_CONTINUE", "NOT_SUBMITTED"} for state in states
        )
        return {
            "complete": complete,
            "settled": settled,
            "unsubmitted": (
                recovery.initial(stage) == "waiting-authority"
                and "NOT_SUBMITTED" in states
                and all(state in {"PREPARED", "NOT_SUBMITTED"} for state in states)
            ),
            "diagnosis": Diagnosis("packages", digest(observations))
            # Absence of the exact phase also permits its approved submission.
            # Submission still revalidates authority and excludes pending work;
            # NOT_SUBMITTED does not establish global guest quiescence.
            if settled and any(state in {"READY_TO_CONTINUE", "NOT_SUBMITTED"} for state in states)
            else None,
        }

    def repair(_attempt: dict, _observation: dict) -> None:
        nonlocal repairing
        repairing = True

    execute(
        recovery,
        stage,
        effect=run,
        observe=observe,
        repair=repair,
        finish=lambda _: None,
        continue_unsubmitted=lambda: run(None),
    )


def reserve(job: Any, request: dict) -> dict:
    """Account for a settled failure even before a conversion Holder is ready."""
    from .package_reservation import SCOPE

    scope = SCOPE.get()
    if scope is None:
        return job.start(request)
    job.bind(request)
    root, execution, authority = scope
    recovery = Recovery(root, execution=execution, authority=authority)
    repairing = False
    result = None

    def run(_deadline):
        nonlocal result
        token = REPAIR.set(repairing)
        try:
            result = job.start(request)
        finally:
            REPAIR.reset(token)

    def observe(_deadline):
        nonlocal result
        value = job.control({"action": "worker-reservation", "reservation": job.reservation})
        if value["state"] == "NOT_SUBMITTED":
            return {"complete": False, "settled": True, "unsubmitted": True, "diagnosis": None}
        complete = value["state"] == "PREPARED"
        if complete:
            result = value["result"]
        return {
            "complete": complete,
            "settled": value["state"] in {"READY_TO_CONTINUE", "BLOCKED"},
            "diagnosis": Diagnosis("packages", digest(value))
            if value["state"] == "READY_TO_CONTINUE"
            else None,
        }

    def repair(_attempt, _observation):
        nonlocal repairing
        repairing = True

    execute(
        recovery,
        "packages-reserve-" + job.instance.hostname,
        effect=run,
        observe=observe,
        repair=repair,
        finish=lambda _: None,
        continue_unsubmitted=lambda: run(None),
    )
    if not isinstance(result, dict):
        raise RuntimeError("HA reservation has no verified receipt")
    return result
