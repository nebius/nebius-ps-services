"""Thread-safe advisory snapshots, independent of cloud mutation authority."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

HEARTBEAT_CAPABILITY = "vm-ha-independent-heartbeat-v1"
HEALTH_MAX_AGE_SECONDS = 10.0
CLOCK_SKEW_SECONDS = 5.0


@dataclass(frozen=True)
class HealthSample:
    started_at: float
    service_healthy: bool | None = None
    transfer_ready: bool | None = None
    local_forwarding_ready: bool | None = None
    data_plane_mode: str | None = None


@dataclass(frozen=True)
class OwnershipSample:
    started_at: float
    owner_id: str | None


class HeartbeatSnapshots:
    """Short-held locks protect complete samples, never external operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._health: HealthSample | None = None
        self._ownership: OwnershipSample | None = None
        self._revision = 0
        self.resample = threading.Event()

    def mutation(self, *, completed: bool = False) -> None:
        with self._lock:
            self._revision += 1
            self._health = None
        if completed:
            self.resample.set()
        # The controller is the only mutation writer. Revisions alternate odd
        # (in progress) and even (completed), without holding a lock over I/O.

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def publish_health(self, sample: HealthSample, revision: int) -> None:
        with self._lock:
            if revision != self._revision or revision % 2:
                sample = replace(
                    sample,
                    transfer_ready=None,
                    local_forwarding_ready=None,
                    data_plane_mode=None,
                )
            self._health = sample

    def publish_ownership(self, sample: OwnershipSample) -> None:
        with self._lock:
            self._ownership = sample

    def read(self) -> tuple[HealthSample | None, OwnershipSample | None]:
        with self._lock:
            return self._health, self._ownership
