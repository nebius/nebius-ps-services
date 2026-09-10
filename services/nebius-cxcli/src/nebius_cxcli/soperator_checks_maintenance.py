"""One restoration contract for initial execution, recovery and verification."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .soperator_checks import SoperatorChecksExecution


@dataclass
class SoperatorChecksMaintenance:
    checks: SoperatorChecksExecution | None
    installing: bool
    partitions: list[Mapping[str, Any]]
    reservation: Callable[[], str]
    restore_infrastructure: Callable[[], object]
    recover_infrastructure: Callable[[], object]
    verify_infrastructure: Callable[[], object]
    verify_checks_restored: Callable[[], object]
    before_checks: Callable[[bool], object] = lambda _mutate: None
    released_checks: Callable[[bool], object] | None = None

    def _checks(self, *, mutate: bool) -> object:
        if self.checks is None:
            return {"status": "delegated"}
        if self.checks.state.get("scheduleRelease"):
            if self.released_checks is None:
                raise RuntimeError("released checks maintenance has no owner verifier")
            return self.released_checks(mutate)
        self.before_checks(mutate)
        if self.checks.state.get("phase") == "restored":
            return self.verify_checks_restored()
        self.checks.verify_deferred_diagnostics(
            allow_acceptance=self.checks.state.get("phase") == "acceptance"
        )
        if mutate:
            return self.checks.prepare_maintenance(
                self.reservation(), installing=self.installing, partitions=self.partitions
            )
        return self.checks.verify_maintenance(
            installing=self.installing, partitions=self.partitions
        )

    def restore(self) -> object:
        checks = self._checks(mutate=True)
        return {"checks": checks, "infrastructure": self.restore_infrastructure()}

    def recover(self) -> object:
        checks = self._checks(mutate=True)
        return {"checks": checks, "infrastructure": self.recover_infrastructure()}

    def verify(self) -> object:
        checks = self._checks(mutate=False)
        return {"checks": checks, "infrastructure": self.verify_infrastructure()}
