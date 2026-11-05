"""Independent bounded publication; callbacks cannot own cloud effects."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..vm_ha_controller import ControllerSnapshot, LocalReadiness
from .models import HeartbeatEvidence, PeerHeartbeat
from .transport import PeerTransportError


@dataclass(frozen=True)
class LocalSample:
    number: int
    started: float
    completed: float
    identity: object
    service_identity: object
    readiness: LocalReadiness | None


def _unknown_path(readiness: LocalReadiness | None) -> LocalReadiness | None:
    if readiness is None:
        return None
    return LocalReadiness(readiness.service_healthy, False, False, False, sample_complete=False)


class HeartbeatPublisher:
    """Single sender and single collector, with bounded callbacks and no queue."""

    def __init__(
        self,
        *,
        sample: Callable[[float], LocalReadiness],
        identity: Callable[[], object],
        service_identity: Callable[[], object],
        build: Callable[
            [ControllerSnapshot | None, LocalReadiness | None, HeartbeatEvidence], PeerHeartbeat
        ],
        send: Callable[[PeerHeartbeat, float], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sample, self.identity, self.build, self.send, self.clock = (
            sample,
            identity,
            build,
            send,
            clock,
        )
        self.stop = threading.Event()
        self.service_identity = service_identity
        self.lock = threading.Lock()
        # Separate from the record lock: drain at most one bounded transport
        # attempt before a dataplane transition or completed shutdown admission.
        self.publication_gate = threading.Lock()
        self.snapshot: ControllerSnapshot | None = None
        self.observed_at: float | None = None
        self.local: LocalSample | None = None
        self.phase = "starting"
        self.health_transition = False
        self.error: BaseException | None = None
        self.generation = 0
        self.counter = 0
        self.threads: list[threading.Thread] = []

    def progress(self, phase: str, invalidates_health: bool = False) -> None:
        if not self.publication_gate.acquire(timeout=3.0):
            raise RuntimeError("heartbeat publication did not quiesce")
        try:
            with self.lock:
                if not self.stop.is_set():
                    if invalidates_health or self.health_transition:
                        self.generation += 1
                        self.local = None
                    self.health_transition = invalidates_health
                    self.phase = phase
        finally:
            self.publication_gate.release()

    def observed(self, snapshot: ControllerSnapshot) -> None:
        with self.lock:
            if not self.stop.is_set():
                self.snapshot, self.observed_at = snapshot, self.clock()

    def collect(self) -> None:
        started = self.clock()
        with self.lock:
            generation = self.generation
            effect_running = self.health_transition
            self.counter += 1
            number = self.counter
        service_before = self.service_identity()
        before = self.identity()
        try:
            readiness = self.sample(started + 5.0)
        except (RuntimeError, ValueError, OSError, TimeoutError):
            readiness = None
        after = self.identity()
        service_after = self.service_identity()
        completed = self.clock()
        if service_before != service_after or not 0 <= completed - started < 5.0:
            readiness = None
        elif effect_running or before != after:
            readiness = _unknown_path(readiness)
        result = LocalSample(number, started, completed, after, service_after, readiness)
        with self.lock:
            if not self.stop.is_set() and generation == self.generation:
                self.local = result

    def publish(self) -> None:
        now = self.clock()
        identity = self.identity()
        service_identity = self.service_identity()
        with self.lock:
            if self.stop.is_set():
                return
            local, snapshot, observed_at, phase = (
                self.local,
                self.snapshot,
                self.observed_at,
                self.phase,
            )
            generation = self.generation
        readiness = local.readiness if local is not None else None
        age = max(0.0, now - local.started) if local is not None else 0.0
        duration = max(0.0, local.completed - local.started) if local is not None else 0.0
        if (
            local is None
            or service_identity != local.service_identity
            or not 0 <= now - local.started < 5.0
        ):
            readiness = None
        elif identity != local.identity:
            readiness = _unknown_path(readiness)
        state = "unknown"
        if readiness is not None and readiness.sample_complete:
            state = (
                "healthy"
                if readiness.transfer_ready
                else ("unhealthy" if not readiness.service_healthy else "unknown")
            )
        cloud_age = max(0.0, now - observed_at) if observed_at is not None else None
        evidence = HeartbeatEvidence(
            state=state,
            serving=bool(state == "healthy" and readiness is not None and readiness.serving_ready),
            sample_id=local.number if local is not None else 0,
            age_seconds=max(age, duration),
            duration_seconds=duration,
            cloud_status=(
                "in-progress"
                if phase in {"observing", "effect"}
                else "complete"
                if cloud_age is not None and cloud_age < 30
                else "unknown"
            ),
            cloud_age_seconds=cloud_age,
            controller_phase=phase,
        )
        heartbeat = self.build(snapshot, readiness, evidence)
        deadline = now + 2.0
        for _attempt in range(3):
            remaining = deadline - self.clock()
            if remaining <= 0 or self.stop.is_set():
                return
            current_identity = self.identity()
            current_service_identity = self.service_identity()
            if not self.publication_gate.acquire(timeout=max(0.0, remaining)):
                return
            try:
                with self.lock:
                    if self.stop.is_set() or generation != self.generation:
                        return
                    if (
                        readiness is not None
                        and readiness.service_healthy
                        and (
                            local is None
                            or current_service_identity != local.service_identity
                            or not 0 <= self.clock() - local.started < 5.0
                        )
                    ):
                        return
                    if evidence.state == "healthy" and (
                        local is None
                        or current_identity != local.identity
                        or not 0 <= self.clock() - local.started < 5.0
                        or self.health_transition
                    ):
                        return
                # Freeze age after build/echo selection, on every attempt. The
                # causal proof must age the sample through metadata I/O too.
                outgoing = heartbeat
                if local is not None and readiness is not None:
                    send_age = self.clock() - local.started
                    outgoing = replace(
                        heartbeat,
                        evidence=replace(
                            heartbeat.evidence,
                            age_seconds=max(duration, send_age),
                        ),
                    )
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return
                self.send(outgoing, remaining)
                return
            except PeerTransportError:
                pass
            finally:
                self.publication_gate.release()
            self.stop.wait(min(0.05, max(0.0, deadline - self.clock())))

    def _loop(self, callback: Callable[[], None]) -> None:
        try:
            while not self.stop.is_set():
                started = self.clock()
                callback()
                # Do not queue missed slots, including after a five-second sample.
                self.stop.wait(max(0.01, 1.0 - (self.clock() - started)))
        except BaseException as error:
            with self.lock:
                self.error = error
                self.generation += 1
                self.local = None
                self.phase = "failed"
            self.stop.set()

    def start(self) -> None:
        if self.threads:
            return
        for name, callback in (("sampler", self.collect), ("sender", self.publish)):
            thread = threading.Thread(target=self._loop, args=(callback,), name=f"vm-ha-{name}")
            self.threads.append(thread)
            thread.start()

    def check(self) -> None:
        if self.error is not None:
            raise RuntimeError("independent heartbeat worker failed") from self.error

    def invalidate(self) -> None:
        with self.lock:
            self.generation += 1
            self.local = None
            self.phase = "closing"
            self.stop.set()
        if not self.publication_gate.acquire(timeout=3.0):
            raise RuntimeError("heartbeat publication did not stop within its bound")
        self.publication_gate.release()

    def join(self) -> None:
        deadline = time.monotonic() + 6.0
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError("independent heartbeat worker did not stop within its bound")
