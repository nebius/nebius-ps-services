"""Command-owned recovery accounting. This record grants no guest authority.

The caller holds the existing gateway writer and revalidates lifecycle/guest
authority before every effect. Approval inputs stay immutable; attempts live in
an execution-bound protected child record, never in digest-bound intent.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..apply_approval import digest
from .apply_diagnostics import (
    DiagnosticFailure,
    Failure,
    ObservationUnavailable,
    VerificationFailure,
    first_failure,
    latest_failure,
    observation_pending,
    unavailable_observation,
)
from .operator_guest import publish, read_file
from .operator_keys import protected_directory

POLICY: dict[str, Any] = {
    "schema": "nebius-vpngw.activation-recovery-policy.v2",
    "attempts": 3,
    "soft_target_seconds": 120,
    "actions": ["service", "configuration", "packages", "unlock"],
}


def clock_identity() -> str:
    if sys.platform == "linux":
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    elif sys.platform == "darwin":
        value = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "kern.boottime"], timeout=5, text=True
        ).strip()
    else:
        raise RuntimeError("activation recovery clock identity unavailable")
    return digest({"host": os.uname().nodename, "boot": value})


@dataclass(frozen=True)
class Diagnosis:
    action: str
    fingerprint: str

    def __post_init__(self) -> None:
        if (
            self.action not in POLICY["actions"]
            or re.fullmatch(r"[a-f0-9]{64}", self.fingerprint) is None
        ):
            raise ValueError("invalid activation repair diagnosis")


class Recovery:
    """One bounded repair allowance for an existing approved HA execution."""

    def __init__(
        self,
        root: Path,
        *,
        execution: str,
        authority: str,
        clock: Callable[[], float] = time.monotonic,
        boot: Callable[[], str] = clock_identity,
    ) -> None:
        if (
            re.fullmatch(r"[a-f0-9]{32}", execution) is None
            or re.fullmatch(r"[a-f0-9]{64}", authority) is None
        ):
            raise ValueError("invalid activation recovery identity")
        self.path = root / "activation-recovery" / (execution + ".json")
        self.execution, self.authority = execution, authority
        self.clock, self.boot = clock, boot

    def initialize(self) -> None:
        """Called only when beginning a new approved command, before effects."""
        if self.read() is not None:
            return
        protected_directory(self.path.parent)
        self._save(
            None,
            {
                "schema": "nebius-vpngw.activation-recovery.v2",
                "execution": self.execution,
                "authority": self.authority,
                "attempts": [],
                "initial": {},
            },
        )

    def read(self) -> dict | None:
        record = read_file(self.path)
        if record is None:
            return None
        if record["uid"] != os.getuid() or record["mode"] != 0o600:
            raise RuntimeError("activation recovery history is unsafe")
        value = json.loads(record["content"])
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "execution", "authority", "attempts", "initial"}
            or value["schema"] != "nebius-vpngw.activation-recovery.v2"
            or value["execution"] != self.execution
            or value["authority"] != self.authority
            or not isinstance(value["attempts"], list)
            or len(value["attempts"]) > POLICY["attempts"]
        ):
            raise RuntimeError("activation recovery history has changed authority")
        if not isinstance(value["initial"], dict) or any(
            not isinstance(stage, str)
            or not stage
            or len(stage) > 256
            or status not in {"started", "waiting-authority", "verified"}
            for stage, status in value["initial"].items()
        ):
            raise RuntimeError("activation initial-effect history is invalid")
        seen = set()
        for ordinal, attempt in enumerate(value["attempts"], 1):
            if (
                not isinstance(attempt, dict)
                or set(attempt)
                != {
                    "ordinal",
                    "stage",
                    "action",
                    "fingerprint",
                    "boot",
                    "target_at",
                    "state",
                    "cleaned",
                }
                or not isinstance(attempt["cleaned"], bool)
                or attempt["ordinal"] != ordinal
                or not isinstance(attempt["stage"], str)
                or not attempt["stage"]
                or len(attempt["stage"]) > 256
                or attempt["state"]
                not in {"reserved", "dispatched", "verified", "failed", "uncertain"}
                or not isinstance(attempt["target_at"], (int, float))
                or isinstance(attempt["target_at"], bool)
                or not math.isfinite(attempt["target_at"])
                or not isinstance(attempt["boot"], str)
            ):
                raise RuntimeError("activation recovery attempt is invalid")
            Diagnosis(attempt["action"], attempt["fingerprint"])
            key = (attempt["stage"], attempt["action"], attempt["fingerprint"])
            if key in seen or (
                ordinal < len(value["attempts"])
                and attempt["state"] in {"reserved", "dispatched", "uncertain"}
            ):
                raise RuntimeError("activation recovery has unresolved or repeated work")
            seen.add(key)
        return value

    def _save(self, expected: dict | None, value: dict) -> None:
        current = read_file(self.path)
        if (json.loads(current["content"]) if current else None) != expected:
            raise RuntimeError("activation recovery history changed")
        publish(
            self.path,
            current,
            {
                "content": json.dumps(value, sort_keys=True),
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o600,
            },
        )

    def history(self) -> list[dict]:
        value = self.read()
        if value is None:
            raise RuntimeError("approved activation recovery history is missing")
        return deepcopy(value["attempts"])

    def initial(self, stage: str, *, state: str | None = None) -> str | None:
        value = self.read()
        if value is None:
            raise RuntimeError("approved activation recovery history is missing")
        previous = value["initial"].get(stage)
        if state is None:
            return previous
        if (
            not stage
            or len(stage) > 256
            or (previous, state)
            not in {
                (None, "started"),
                ("started", "verified"),
                ("verified", "verified"),
                ("started", "waiting-authority"),
                ("waiting-authority", "started"),
                ("waiting-authority", "verified"),
            }
        ):
            raise RuntimeError("activation initial-effect transition is invalid")
        updated = deepcopy(value)
        updated["initial"][stage] = state
        self._save(value, updated)
        return state

    def reserve(self, stage: str, diagnosis: Diagnosis) -> dict:
        if not isinstance(stage, str) or not stage or len(stage) > 256:
            raise ValueError("invalid activation stage")
        value = self.read()
        if value is None:
            raise RuntimeError("approved activation recovery history is missing")
        attempts = value["attempts"]
        if attempts and attempts[-1]["state"] in {"reserved", "dispatched", "uncertain"}:
            raise RuntimeError("previous activation repair requires observation before replay")
        if len(attempts) >= POLICY["attempts"]:
            raise RuntimeError("activation recovery exhausted three attempts")
        if any(
            (a["stage"], a["action"], a["fingerprint"])
            == (stage, diagnosis.action, diagnosis.fingerprint)
            for a in attempts
        ):
            error = DiagnosticFailure("activation recovery refuses unchanged repair")
            error.first = Failure("activation-recovery", "unchanged-repair-refused")
            raise error
        attempt = {
            "ordinal": len(attempts) + 1,
            "stage": stage,
            "action": diagnosis.action,
            "fingerprint": diagnosis.fingerprint,
            "boot": self.boot(),
            "target_at": self.clock() + POLICY["soft_target_seconds"],
            "state": "reserved",
            "cleaned": False,
        }
        updated = deepcopy(value)
        updated["attempts"].append(attempt)
        self._save(value, updated)
        return deepcopy(attempt)

    def remaining(self, attempt: dict) -> float:
        if attempt["boot"] != self.boot():
            return 0.0
        return max(0.0, attempt["target_at"] - self.clock())

    def cleaned(self, attempt: dict) -> dict:
        value = self.read()
        if (
            value is None
            or value["attempts"][-1] != attempt
            or attempt["state"] in {"reserved", "dispatched", "uncertain"}
        ):
            raise RuntimeError("activation cleanup has no settled attempt")
        updated = deepcopy(value)
        updated["attempts"][-1]["cleaned"] = True
        self._save(value, updated)
        return deepcopy(updated["attempts"][-1])

    def transition(self, attempt: dict, state: str) -> dict:
        value = self.read()
        if value is None or not value["attempts"] or value["attempts"][-1] != attempt:
            raise RuntimeError("activation repair attempt changed")
        allowed = {
            "reserved": {"dispatched", "failed"},
            "dispatched": {"verified", "failed", "uncertain"},
            "uncertain": {"verified", "failed"},
        }
        if state not in allowed.get(attempt["state"], set()):
            raise RuntimeError("invalid activation repair transition")
        updated = deepcopy(value)
        updated["attempts"][-1]["state"] = state
        self._save(value, updated)
        return deepcopy(updated["attempts"][-1])


def execute(
    recovery: Recovery,
    stage: str,
    *,
    effect: Callable[[float | None], None],
    observe: Callable[[float], dict],
    repair: Callable[[dict, dict], None],
    finish: Callable[[dict], None],
    continue_unsubmitted: Callable[[], None] | None = None,
) -> None:
    """Recover one pending lifecycle effect without replaying completed siblings.

    Observation is authoritative only for the exact staged config and fenced
    operation. Unknown replies leave durable uncertainty; no generic retry is
    admitted. The caller's original exception remains the cause on exhaustion.
    """
    history = recovery.history()
    pending = (
        history[-1]
        if history and history[-1]["state"] in {"reserved", "dispatched", "uncertain"}
        else None
    )
    if history and pending is None and not history[-1]["cleaned"]:
        if history[-1]["stage"] != stage:
            raise RuntimeError("previous activation stage requires exact cleanup")
        finish(history[-1])
        recovery.cleaned(history[-1])
    first: Exception | None = None
    if (
        pending is None
        and recovery.initial(stage) is None
        and not any(item["stage"] == stage for item in history)
    ):
        recovery.initial(stage, state="started")
        try:
            effect(None)
            recovery.initial(stage, state="verified")
            return
        except (OSError, RuntimeError) as error:
            if observation_pending(error):
                recovery.initial(stage, state="waiting-authority")
            if unavailable_observation(error) is not None:
                raise
            first = error
    elif pending is not None and pending["stage"] != stage:
        raise RuntimeError("another activation stage has an unresolved repair")

    def bounded_observation() -> dict:
        from .vm_ha_cloud import vm_ha_observation_budget

        try:
            with vm_ha_observation_budget(seconds=20, clock=recovery.clock):
                return observe(recovery.clock() + 20)
        except TimeoutError as error:
            raise ObservationUnavailable(
                "activation-observation", "observation-deadline-expired"
            ) from error

    while True:
        # Each observation is bounded independently. Elapsed time grants no new
        # effect and never cancels an existing owner or consumes an attempt.
        try:
            observation = bounded_observation()
        except Exception as error:
            if unavailable_observation(error) is not None:
                if first is not None:
                    raise VerificationFailure(
                        first_failure(first)
                        or Failure("activation-recovery", "initial-effect-failed"),
                        latest_failure(error)
                        or Failure("activation-observation", "status-observation-unavailable"),
                    ) from error
                raise
            raise RuntimeError("activation recovery cannot prove current local state") from (
                first or error
            )
        if pending is not None:
            if pending["state"] == "reserved":
                # Reservation is recorded before submission. There was no effect
                # if dispatch was never persisted; still consume the reservation.
                pending = recovery.transition(pending, "failed")
            elif observation.get("complete") is True:
                pending = recovery.transition(pending, "verified")
                finish(pending)
                recovery.cleaned(pending)
                return
            elif observation.get("settled") is True:
                pending = recovery.transition(pending, "failed")
            else:
                if pending["state"] == "dispatched":
                    pending = recovery.transition(pending, "uncertain")
                time.sleep(0.2)
                continue
            finish(pending)
            recovery.cleaned(pending)
            pending = None
        if observation.get("complete") is True:
            if recovery.initial(stage) in {"started", "waiting-authority"}:
                recovery.initial(stage, state="verified")
            return
        if observation.get("settled") is not True:
            time.sleep(0.2)
            continue
        if (
            observation.get("unsubmitted") is True
            and continue_unsubmitted is not None
            and not any(item["stage"] == stage for item in recovery.history())
        ):
            # Only the exact original submission is admitted by its existing
            # package owner. Leave the read budget before supervised work starts.
            # An exception returns pending/failure; it never loops into replay.
            if recovery.initial(stage) == "waiting-authority":
                recovery.initial(stage, state="started")
            try:
                continue_unsubmitted()
            except Exception as error:
                if observation_pending(error):
                    recovery.initial(stage, state="waiting-authority")
                raise
            recovery.initial(stage, state="verified")
            return
        diagnosis = observation.get("diagnosis")
        if not isinstance(diagnosis, Diagnosis):
            raise RuntimeError("activation failure has no proven safe local repair") from first
        try:
            attempt = recovery.reserve(stage, diagnosis)
        except RuntimeError as error:
            raise error from first
        attempt = recovery.transition(attempt, "dispatched")
        try:
            repair(attempt, observation)
            effect(None)
            verified = bounded_observation()
            if verified.get("complete") is not True:
                raise RuntimeError("activation repair postconditions remain unproven")
        except Exception as error:
            first = first or error
            pending = recovery.transition(attempt, "uncertain")
            if unavailable_observation(error) is not None:
                raise
        else:
            attempt = recovery.transition(attempt, "verified")
            finish(attempt)
            recovery.cleaned(attempt)
            return
