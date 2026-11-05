"""Bounded, process-local causal proof for advisory VM-HA health.

Only authenticated/replay-checked messages may enter ``accept``. This module
cannot authorize cloud effects and never compares clocks between hosts.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace

from .models import PeerHeartbeat, StateValidationError

HEARTBEAT_CAPABILITY = "independent-heartbeat-v5"
SAMPLE_MAX_AGE = 5.0
CHALLENGE_MAX_AGE = 10.0
PEER_HEALTH_MAX_AGE = 20.0


class PeerEvidenceTracker:
    def __init__(
        self,
        *,
        local_node_id: str,
        peer_node_id: str,
        generation_id: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.local_node_id = local_node_id
        self.peer_node_id = peer_node_id
        self.generation_id = generation_id
        self.clock = clock
        self.process_id = secrets.token_hex(16)
        self._lock = threading.Lock()
        self._challenges: OrderedDict[str, float] = OrderedDict()
        self._echo: str | None = None
        self._peer_process: str | None = None
        self._sample_id = -1
        self._expiry: float | None = None
        self._service_expiry: float | None = None
        self._sample_age = 0.0
        self._closed = False

    def _expire(self, now: float) -> None:
        for nonce, sent in tuple(self._challenges.items()):
            if not 0 <= now - sent < CHALLENGE_MAX_AGE:
                del self._challenges[nonce]

    def issue(self, heartbeat: PeerHeartbeat) -> PeerHeartbeat:
        if heartbeat.node_id != self.local_node_id or heartbeat.generation_id != self.generation_id:
            raise StateValidationError("challenge does not match local binding")
        with self._lock:
            if self._closed:
                raise RuntimeError("peer evidence is closed")
            now = self.clock()
            if not math.isfinite(now):
                raise RuntimeError("local monotonic clock unavailable")
            self._expire(now)
            nonce = secrets.token_hex(16)
            self._challenges[nonce] = now
            while len(self._challenges) > 16:
                self._challenges.popitem(last=False)
            return replace(
                heartbeat,
                health_valid_until=None,
                service_valid_until=None,
                echo_valid_until=None,
                evidence=replace(
                    heartbeat.evidence, process_id=self.process_id, challenge=nonce, echo=self._echo
                ),
            )

    def accept(self, heartbeat: PeerHeartbeat) -> PeerHeartbeat:
        if heartbeat.node_id != self.peer_node_id:
            raise StateValidationError("health evidence does not match authenticated peer")
        with self._lock:
            now = self.clock()
            self._expire(now)
            evidence = heartbeat.evidence
            self._echo = evidence.challenge
            if evidence.process_id != self._peer_process:
                self._peer_process = evidence.process_id
                self._sample_id, self._expiry, self._service_expiry, self._sample_age = (
                    -1,
                    None,
                    None,
                    0.0,
                )
            if evidence.sample_id < self._sample_id:
                return replace(
                    heartbeat,
                    health_valid_until=None,
                    service_valid_until=None,
                    echo_valid_until=None,
                )
            same_sample = evidence.sample_id == self._sample_id
            previous_expiry = self._expiry
            previous_service_expiry = self._service_expiry
            decreasing_age = same_sample and evidence.age_seconds < self._sample_age
            self._sample_id = evidence.sample_id
            self._sample_age = evidence.age_seconds
            sent = self._challenges.get(evidence.echo or "")
            echo_expiry = (
                sent + CHALLENGE_MAX_AGE
                if sent is not None
                and not self._closed
                and heartbeat.generation_id == self.generation_id
                else None
            )
            expiry = None
            if (
                not self._closed
                and math.isfinite(now)
                and not decreasing_age
                and heartbeat.generation_id == self.generation_id
                and evidence.sample_id > 0
                and evidence.age_seconds < SAMPLE_MAX_AGE
                and sent is not None
                and 0 <= now - sent < CHALLENGE_MAX_AGE
            ):
                upper_age = evidence.age_seconds + now - sent
                if upper_age < PEER_HEALTH_MAX_AGE:
                    expiry = now + PEER_HEALTH_MAX_AGE - upper_age
            service_expiry = expiry if heartbeat.service_healthy else None
            expiry = expiry if evidence.state == "healthy" else None
            if same_sample:
                # Each evidence channel expires independently and cannot resurrect.
                expiry = (
                    min(previous_expiry, expiry)
                    if previous_expiry is not None and expiry is not None
                    else None
                )
                service_expiry = (
                    min(previous_service_expiry, service_expiry)
                    if previous_service_expiry is not None and service_expiry is not None
                    else None
                )
            self._expiry = expiry
            self._service_expiry = service_expiry
            return replace(
                heartbeat,
                health_valid_until=expiry,
                service_valid_until=service_expiry,
                echo_valid_until=echo_expiry,
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._expiry = None
            self._service_expiry = None
            self._challenges.clear()
