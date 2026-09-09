"""Release verified maintenance before native recurring checks can select nodes."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .soperator_checks_admission import ChecksAdmission
from .soperator_checks_policy import checks_digest

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution


def _acceptance_binding(checks: SoperatorChecksExecution, owner: str) -> dict[str, Any]:
    state = checks.state
    if state.get("phase") not in {"accepted", "restored"} or not state.get("jobs"):
        raise RuntimeError("schedule handoff requires complete fresh acceptance")
    binding = {
        "owner": owner,
        "operation": state["operation"],
        "policy": checks.policy.sha256,
        "reservation": state["acceptance"]["reservation"],
        "fingerprint": state["reservationFingerprint"],
        "principalUid": state["principalUid"],
        "acceptanceSha256": checks_digest(
            {"acceptance": state["acceptance"], "jobs": state["jobs"]}
        ),
    }
    if checks.lifecycle is not None:
        binding["passiveSha256"] = checks_digest(state["passive"]["acceptance"])
        binding["admissionSha256"] = checks_digest(
            {
                "partitions": state["admission"]["partitions"],
                "transfer": state["admission"]["transfer"],
            }
        )
    recovery = state.get("catchupRecovery")
    if recovery is not None:
        if recovery.get("status") != "accepted":
            raise RuntimeError("native catch-up recovery is incomplete")
        binding["catchupSha256"] = checks_digest(recovery)
    auxiliary = state.get("auxiliaryRecovery")
    if auxiliary is not None:
        if auxiliary.get("status") != "complete":
            raise RuntimeError("auxiliary checks recovery is incomplete")
        binding["auxiliarySha256"] = checks_digest(auxiliary)
    return binding


def _release_record(checks: SoperatorChecksExecution, owner: str) -> dict[str, Any] | None:
    record = checks.state.get("scheduleRelease")
    if record is None:
        return None
    if (
        not isinstance(record, dict)
        or record.get("binding") != _acceptance_binding(checks, owner)
        or record.get("status") not in {"intent", "released"}
        or record.get("reservationPreimage", {}).get("users") != ["root"]
        or record["reservationPreimage"].get("fingerprint") != record["binding"]["fingerprint"]
    ):
        raise RuntimeError("schedule release evidence changed")
    return record


def _reservation_absent(checks: SoperatorChecksExecution) -> bool:
    names = re.findall(r"ReservationName=(\S+)", checks.slurm("scontrol show reservations -o"))
    return checks.state["acceptance"]["reservation"] not in names


def released_diagnostic_evidence(
    checks: SoperatorChecksExecution, *, parent: SoperatorChecksExecution | None = None
) -> bool:
    """Authenticate the existing release seal before upstream local logs expire."""
    authority = parent or checks
    release = authority.state.get("scheduleRelease")
    if release is None:
        return False
    owner = authority.handoff_owner
    if not isinstance(owner, str) or not owner:
        raise RuntimeError("released diagnostic evidence has no handoff owner")
    record = _release_record(authority, owner)
    if record is None or record["status"] != "released":
        return False
    if not _reservation_absent(authority):
        raise RuntimeError("released diagnostic maintenance was recreated")
    if parent is not None:
        recovery = parent.state.get("catchupRecovery", {})
        if (
            recovery.get("status") != "accepted"
            or recovery.get("resultSha256") != checks_digest(checks.state)
            or checks.policy.sha256 != parent.policy.sha256
            or checks.state["operation"]
            != parent.state["operation"] + ":schedule-catchup:" + checks_digest(recovery["binding"])
            or checks.path != parent.path.with_name(parent.path.stem + "-schedule-catchup.json")
        ):
            raise RuntimeError("released diagnostic child acceptance changed")
    return True


class ChecksScheduleHandoff:
    def __init__(
        self,
        checks: SoperatorChecksExecution,
        *,
        owner: str,
        release: Callable[[Mapping[str, Any]], None],
        verify_released: Callable[[Mapping[str, Any]], None],
        admission: ChecksAdmission,
    ) -> None:
        self.checks = checks
        self.owner = owner
        self.release_owned = release
        self.verify_owned = verify_released
        self.admission = admission

    def _binding(self) -> dict[str, Any]:
        return _acceptance_binding(self.checks, self.owner)

    def _record(self) -> dict[str, Any] | None:
        return _release_record(self.checks, self.owner)

    def _absent(self) -> bool:
        return _reservation_absent(self.checks)

    def verify(self) -> dict[str, Any]:
        self.admission.verify()
        record = self._record()
        if record is None or record["status"] != "released":
            raise RuntimeError("schedule maintenance release is incomplete")
        if not self._absent():
            raise RuntimeError("released maintenance reservation was recreated")
        self.verify_owned(record["binding"])
        return copy.deepcopy(record)

    def release(self) -> dict[str, Any]:
        self.admission.verify()
        record = self._record()
        if record is not None and record["status"] == "released":
            return self.verify()
        self.checks.verify_acceptance()
        if record is None:
            self.checks.close_authorization()
            binding = self._binding()
            observed = self.checks._reservation(binding["reservation"])
            if observed["users"] != ["root"]:
                raise RuntimeError("schedule release temporary authorization remains open")
            record = {
                "binding": binding,
                "reservationPreimage": observed,
                "status": "intent",
            }
            self.checks.state["scheduleRelease"] = record
            self.checks._save()
        elif not self._absent():
            if (
                self.checks._reservation(record["binding"]["reservation"])
                != record["reservationPreimage"]
            ):
                raise RuntimeError("schedule release reservation changed after intent")
        self.checks.authority()
        self.release_owned(record["binding"])
        self.verify_owned(record["binding"])
        if not self._absent():
            raise RuntimeError("schedule maintenance reservation remains after release")
        record["status"] = "released"
        self.checks._save()
        return copy.deepcopy(record)

    def restore(
        self,
        *,
        apply_policy: Callable[[], object],
        policy_restored: Callable[[], bool],
        complete_source_handoff: Callable[[], object],
    ) -> dict[str, Any]:
        release = self.release()
        if self.checks.state["phase"] != "restored":
            apply_policy()
            self.checks._until(policy_restored, "steady upstream check policy")
            complete_source_handoff()
            self.checks.state["phase"] = "restored"
            self.checks._save()
        elif not policy_restored():
            apply_policy()
            self.checks._until(policy_restored, "restored upstream check policy reconciliation")
        self.admission.verify()
        return {"status": "restored", "policy": self.checks.policy.sha256, "release": release}
