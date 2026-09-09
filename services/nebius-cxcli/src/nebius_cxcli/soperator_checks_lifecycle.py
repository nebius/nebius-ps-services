"""Phase orchestration shared by install and upgrade; job policy stays with its owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .slurm_jobs import SlurmPartitionPauseRecord
from .soperator_checks_admission import ChecksAdmission
from .soperator_checks_phase import ChecksPhase, ChecksPhaseContext
from .soperator_passive_checks import PassiveDiagnostics

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution


class ChecksLifecycle:
    def __init__(
        self,
        checks: SoperatorChecksExecution,
        apply: Callable[[ChecksPhaseContext], Any],
        *,
        preimages: Callable[[], tuple[SlurmPartitionPauseRecord, ...]] = lambda: (),
        installing: bool = False,
    ) -> None:
        self.checks = checks
        if checks.state.get("freshInstall", installing) != installing:
            raise RuntimeError("diagnostic lifecycle installation identity changed")
        checks.state["freshInstall"] = installing
        self.apply = apply
        self.admission = ChecksAdmission(checks, preimages)
        self.passive = PassiveDiagnostics(checks)

    def context(self, phase: ChecksPhase | None = None) -> ChecksPhaseContext:
        return ChecksPhaseContext(
            phase or ChecksPhase(self.checks.state.get("lifecyclePhase", "maintenance")),
            self.checks.state.get("reservation") or "cxcli_" + self.checks.operation_id[:16],
            self.checks.state.get("passive", {}).get("status") == "enabled-fallback"
            or self.checks.state.get("passive", {}).get("fallbackIntent", False),
            self.checks.state["freshInstall"],
            self.admission.state.get("partitions"),
        )

    def _apply(self, phase: ChecksPhase) -> None:
        self.checks.state["lifecycleIntent"] = phase.value
        self.checks._save()
        self.apply(self.context(phase))
        self.checks.state["lifecyclePhase"] = phase.value
        self.checks._save()

    def maintenance(self) -> Any:
        if self.checks.state.get("scheduleRelease"):
            return self.admission.verify()
        self.checks._verify_isolation()
        self.admission.establish()
        self._apply(ChecksPhase.MAINTENANCE)
        try:
            return self.passive.verify(paused=not self.context().passive_fallback)
        except RuntimeError:
            self.checks._verify_isolation()
            self.admission.verify()
            self.passive.state["fallbackIntent"] = True
            self.checks._save()
            self._apply(ChecksPhase.MAINTENANCE)
            return self.passive.verify(paused=False)

    def before_acceptance(self) -> Any:
        if self.checks.state.get("scheduleRelease"):
            self.admission.verify()
            return self.checks.state.get("passive", {})
        self.checks._verify_isolation()
        self.admission.establish(checks_open=True)
        self._apply(ChecksPhase.ACCEPTANCE)
        # Restore passive execution first; scheduled active checks remain paused.
        self.checks._until(self.passive.accept_ready, "fresh native passive periodic evidence")
        return self.passive.state

    def schedules(self) -> None:
        self.admission.verify()
        self._apply(ChecksPhase.SCHEDULES)
        self.passive.verify(paused=False)
        self.admission.verify()

    def authorize_admission(self) -> None:
        self.admission.authorize()
        self.admission.release_holds()

    def finish(self) -> None:
        if not self.admission.state.get("authorized"):
            raise RuntimeError("final scheduling restoration lacks admission authorization")
        # The existing job-policy owner releases its holds before this callback.
        self.admission.release_holds()
        self.admission.prepare_release_configuration()
        self._apply(ChecksPhase.READY)
        self.admission.restore_partitions()
        self.admission.complete_release_configuration()
        self.admission.verify()
