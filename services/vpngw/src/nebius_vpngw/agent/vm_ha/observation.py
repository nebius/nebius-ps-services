"""Per-source observation grace, independent of ownership or mutation authority."""

from __future__ import annotations

from dataclasses import dataclass

OBSERVATION_GRACE_SECONDS = 10.0
OBSERVATION_FAILURE_LIMIT = 3


@dataclass
class ObservationGrace:
    """A single observer owns each instance and supplies its own clock."""

    consecutive_failures: int = 0
    first_failure_at: float | None = None

    def failed(self, now: float) -> None:
        if self.first_failure_at is None:
            self.first_failure_at = now
        self.consecutive_failures += 1

    def succeeded(self) -> None:
        self.consecutive_failures = 0
        self.first_failure_at = None

    def elapsed(self, now: float) -> float:
        if self.first_failure_at is None:
            return 0.0
        return max(0.0, now - self.first_failure_at)

    def expired(self, now: float) -> bool:
        return bool(
            self.consecutive_failures >= OBSERVATION_FAILURE_LIMIT
            and self.elapsed(now) >= OBSERVATION_GRACE_SECONDS
        )

    def status(self, now: float) -> dict[str, object]:
        state = "available"
        if self.consecutive_failures:
            state = "unavailable" if self.expired(now) else "grace"
        return {
            "state": state,
            "consecutive_failures": self.consecutive_failures,
            "failure_age_seconds": self.elapsed(now),
        }
