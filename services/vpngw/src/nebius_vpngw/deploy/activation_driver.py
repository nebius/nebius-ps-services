"""Connect an existing HA activation effect to evidence-selected local recovery."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from ..apply_approval import digest
from .activation_recovery import Diagnosis, Recovery, execute
from .apply_diagnostics import DiagnosticFailure, Failure, ObservationUnavailable


class Completion(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete-with-evidence"
    UNKNOWN = "unknown-retryable"
    BLOCKED = "blocked"

    def __bool__(self) -> bool:
        raise TypeError("completion evidence requires an explicit outcome comparison")


def completed(value: Completion) -> bool:
    if not isinstance(value, Completion):
        raise TypeError("activation completion probe returned untyped evidence")
    if value is Completion.UNKNOWN:
        raise ObservationUnavailable("verify-completion", "completion-unknown")
    if value is Completion.BLOCKED:
        error = DiagnosticFailure("activation completion evidence is invalid or foreign")
        error.first = Failure("verify-completion", "completion-blocked")
        raise error
    return value is Completion.COMPLETE


def activate(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    recovery: Recovery,
    phase: str | None = None,
    phase_effect: Any = None,
    phase_complete: Any = None,
    phase_resume: Any = None,
    **kwargs: Any,
) -> None:
    receipt = kwargs["staged_receipt"]
    binding = kwargs["runtime_binding"]
    operation = kwargs["apply_operation_id"]
    if receipt is None or binding is None or operation is None:
        raise RuntimeError("activation recovery requires exact staged authority")
    stage = (phase or "activate") + "-" + receipt.node_id
    request = {
        "lock": {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            "cluster_id": binding.cluster_id,
            "node_id": receipt.node_id,
            "generation_id": receipt.generation_id,
            "operation_id": operation,
        },
        "configuration": receipt.staged_file_sha256,
    }

    def control(action: str, *, deadline: float, **values: Any) -> dict:
        return ssh.activation_control(
            target,
            instance,
            local,
            {
                **request,
                "action": action,
                **values,
            },
            deadline=deadline,
        )

    def observe(deadline: float) -> dict:
        if phase_complete is not None and completed(phase_complete(deadline)):
            return {"complete": True, "settled": True}
        if phase_resume is not None and completed(phase_resume(deadline)):
            # Fresh native proof permits only the canonical own-lock release.
            # The guest separately confirms active authority before arming it.
            observed = control("inspect-unlock", deadline=deadline)
            return {
                "complete": False,
                "settled": True,
                "observed": observed,
                "received": recovery.clock(),
                "unlock_only": True,
                "diagnosis": Diagnosis("unlock", digest(request)),
            }
        while True:
            if time.monotonic() >= deadline:
                raise ObservationUnavailable(
                    "activation-observation", "observation-deadline-expired"
                )
            observed = control("inspect", deadline=deadline)
            units = observed["units"]
            settled = all(
                not state["Job"]
                and state["ControlPID"] == "0"
                and state["ActiveState"] not in {"activating", "deactivating"}
                for state in units.values()
            )
            failed = any(state["ActiveState"] in {"failed", "inactive"} for state in units.values())
            if settled and (failed or observed["materialized"] is not None):
                break
            # A busy routing writer can make materialization unknown even
            # after systemd settles. Reobserve under this same read deadline;
            # missing proof cannot select or consume a corrective attempt.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObservationUnavailable(
                    "activation-observation",
                    "materialization-unavailable" if settled else "services-still-transitioning",
                )
            time.sleep(min(0.2, remaining))
        configured = (
            settled
            and not failed
            and observed["materialized"] is True
            and observed["complete"] is True
        )
        if configured and phase_complete is not None and phase_effect is not None:
            # The native receipt proves configuration completion, not the later
            # forwarding phase. Resume that phase without repairing healthy
            # services. A fenced member needs the existing activation permit for
            # controller-owned origination/reconciliation; an already-active
            # member above needs only the narrower own-lock release permit.
            return {
                "complete": False,
                "settled": True,
                "observed": observed,
                "received": recovery.clock(),
                "unlock_only": False,
                "diagnosis": Diagnosis("unlock", digest(request)),
            }
        complete = configured and phase_complete is None
        action = "service" if failed else "configuration"
        return {
            "complete": complete,
            "settled": settled,
            "observed": observed,
            "received": recovery.clock(),
            "diagnosis": Diagnosis(
                action,
                digest(
                    {
                        "boot": observed["boot"],
                        "materialized": observed["materialized"],
                        "units": {
                            unit: {key: value for key, value in state.items() if key != "MainPID"}
                            for unit, state in units.items()
                        },
                    }
                ),
            )
            if settled
            and not complete
            and (
                failed
                or observed["materialized"] is False
                or (
                    observed["materialized"] is True
                    and (observed["complete"] is False or phase_complete is not None)
                )
            )
            else None,
        }

    def identity(attempt: dict) -> str:
        return digest(
            {"execution": recovery.execution, "stage": stage, "ordinal": attempt["ordinal"]}
        )

    def repair(attempt: dict, observation: dict) -> None:
        print(f"HA activation recovery {attempt['ordinal']}/3: {attempt['action']} ({stage})")
        control(
            "arm",
            deadline=recovery.clock() + 30,
            identity=identity(attempt),
            unlock_only=observation["unlock_only"] if attempt["action"] == "unlock" else False,
            started_at=observation["observed"]["clock"],
        )
        if attempt["action"] == "unlock":
            return  # This allowance admits no service/configuration mutation.
        control(
            "service",
            deadline=recovery.clock() + 30,
            identity=identity(attempt),
            fingerprint=digest(
                {key: value for key, value in observation["observed"].items() if key != "clock"}
            ),
            action_kind=attempt["action"],
            resume_phase=phase,
        )

    def finish(attempt: dict) -> None:
        control(
            "finish",
            deadline=time.monotonic() + 10,
            identity=identity(attempt),
            verified=attempt["state"] == "verified",
        )

    def effect(deadline: float | None) -> None:
        from .ssh_commands import soft_operation

        with soft_operation():
            if phase_effect is not None:
                phase_effect(None)
                return
            ssh.push_config_and_reload(
                target, instance, local, deadline=deadline, serialize_activation=True, **kwargs
            )
        control("mark", deadline=deadline if deadline is not None else time.monotonic() + 10)

    execute(
        recovery,
        stage,
        effect=effect,
        observe=observe,
        repair=repair,
        finish=finish,
    )
