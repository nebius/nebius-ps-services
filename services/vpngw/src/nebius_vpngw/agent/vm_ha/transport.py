"""Authenticated peer-state exchange ports and mTLS context construction."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import PeerHeartbeat, PeerReplayGuard, ReplayState, StateValidationError


@dataclass(frozen=True)
class AuthenticatedPeerMessage:
    """One heartbeat plus the peer identity proven by the transport channel."""

    heartbeat: PeerHeartbeat
    authenticated_node_id: str


class PeerTransport(Protocol):
    """Narrow port implemented by a mutually authenticated peer channel.

    ``authenticated_node_id`` must be derived from the verified certificate
    identity, never from heartbeat payload data.
    """

    def send(self, heartbeat: PeerHeartbeat) -> None: ...

    def receive(self, *, timeout_seconds: float) -> AuthenticatedPeerMessage: ...


class ReplayStateStore(Protocol):
    """Durable replay-state port required by the authenticated exchange."""

    def load_replay_state(self, peer_node_id: str) -> ReplayState | None: ...

    def save_replay_state(self, peer_node_id: str, state: ReplayState) -> None: ...


@dataclass(frozen=True)
class MutualTLSConfig:
    """Build fail-closed standard-library TLS contexts for a peer adapter."""

    certificate_authority: Path
    certificate: Path
    private_key: Path
    server_hostname: str

    def __post_init__(self) -> None:
        if not self.server_hostname.strip():
            raise ValueError("server_hostname must be non-empty")

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH, cafile=str(self.certificate_authority)
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(str(self.certificate), str(self.private_key))
        return context

    def server_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(
            ssl.Purpose.CLIENT_AUTH, cafile=str(self.certificate_authority)
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(str(self.certificate), str(self.private_key))
        return context


class PeerStateExchange:
    """Receive and validate advisory state without turning it into ownership proof."""

    def __init__(
        self,
        transport: PeerTransport,
        *,
        cluster_id: str,
        peer_node_id: str,
        replay_store: ReplayStateStore,
        max_timeout_seconds: float = 30.0,
    ) -> None:
        if not cluster_id or not peer_node_id:
            raise ValueError("cluster_id and peer_node_id must be non-empty")
        if max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")
        self.transport = transport
        self.cluster_id = cluster_id
        self.peer_node_id = peer_node_id
        self.replay_store = replay_store
        self.replay_guard = PeerReplayGuard(replay_store.load_replay_state(peer_node_id))
        self.max_timeout_seconds = max_timeout_seconds

    def send(self, heartbeat: PeerHeartbeat) -> None:
        if heartbeat.cluster_id != self.cluster_id:
            raise StateValidationError("outbound heartbeat has the wrong cluster identity")
        self.transport.send(heartbeat)

    def receive(self, *, timeout_seconds: float) -> tuple[PeerHeartbeat, ReplayState]:
        if timeout_seconds <= 0 or timeout_seconds > self.max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds must be positive and at most {self.max_timeout_seconds}"
            )
        message = self.transport.receive(timeout_seconds=timeout_seconds)
        candidate = PeerReplayGuard(self.replay_guard.state)
        replay_state = candidate.accept(
            message.heartbeat,
            authenticated_node_id=message.authenticated_node_id,
            expected_cluster_id=self.cluster_id,
            expected_node_id=self.peer_node_id,
        )
        self.replay_store.save_replay_state(self.peer_node_id, replay_state)
        self.replay_guard = candidate
        return message.heartbeat, replay_state
