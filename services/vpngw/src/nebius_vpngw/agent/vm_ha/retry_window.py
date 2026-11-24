"""Durable elapsed-time budget for one restoration episode."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

WINDOW_SECONDS = 300.0
RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class RetryWindow:
    boot_id: str
    observed_at: float
    monotonic_at: float
    elapsed: float

    @classmethod
    def from_mapping(cls, value: object) -> RetryWindow:
        if not isinstance(value, dict) or set(value) != {
            "boot_id",
            "observed_at",
            "monotonic_at",
            "elapsed",
        }:
            raise ValueError("restoration retry clock is invalid")
        if not isinstance(value["boot_id"], str) or not value["boot_id"]:
            raise ValueError("restoration retry boot is invalid")
        for key in ("observed_at", "monotonic_at", "elapsed"):
            if (
                type(value[key]) not in {int, float}
                or not math.isfinite(value[key])
                or value[key] < 0
            ):
                raise ValueError("restoration retry clock is invalid")
        return cls(**value)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def observe(
        cls, previous: RetryWindow | None, *, now: float, monotonic: float, boot: str
    ) -> RetryWindow:
        elapsed = 0.0
        if previous is not None:
            delta = (
                monotonic - previous.monotonic_at
                if boot == previous.boot_id
                else now - previous.observed_at
            )
            if delta < 0 or not math.isfinite(delta):
                raise ValueError("restoration retry clock moved backwards")
            elapsed = previous.elapsed + delta
        return cls.from_mapping(
            dict(boot_id=boot, observed_at=now, monotonic_at=monotonic, elapsed=elapsed)
        )
