from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from nebius_vpngw.agent.vm_ha import (
    AtomicGenerationStore,
    AuthenticatedPeerMessage,
    DigestSet,
    MutualTLSConfig,
    PeerHeartbeat,
    PeerStateExchange,
    ReplayState,
    StalePeerStateError,
    StateValidationError,
)


def _heartbeat(*, cluster_id: str = "cluster-a", sequence: int = 0) -> PeerHeartbeat:
    digest = "a" * 64
    return PeerHeartbeat(
        cluster_id=cluster_id,
        node_id="node-b",
        boot_id="boot-a",
        sequence=sequence,
        sent_at="2026-08-12T00:00:00Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id=digest,
        digests=DigestSet(digest, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
    )


@dataclass
class FakeTransport:
    inbound: list[AuthenticatedPeerMessage]
    sent: list[PeerHeartbeat] = field(default_factory=list)
    received_timeout: float | None = None

    def send(self, heartbeat: PeerHeartbeat) -> None:
        self.sent.append(heartbeat)

    def receive(self, *, timeout_seconds: float) -> AuthenticatedPeerMessage:
        self.received_timeout = timeout_seconds
        return self.inbound.pop(0)


@dataclass
class MemoryReplayStore:
    state: ReplayState | None = None

    def load_replay_state(self, peer_node_id: str) -> ReplayState | None:
        assert peer_node_id == "node-b"
        return self.state

    def save_replay_state(self, peer_node_id: str, state: ReplayState) -> None:
        assert peer_node_id == "node-b"
        self.state = state


def test_peer_exchange_accepts_authenticated_monotonic_advisory_state() -> None:
    heartbeat = _heartbeat()
    transport = FakeTransport([AuthenticatedPeerMessage(heartbeat, "node-b")])
    exchange = PeerStateExchange(
        transport,
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=MemoryReplayStore(),
    )

    received, replay = exchange.receive(timeout_seconds=2.5)

    assert received == heartbeat
    assert replay.current_boot_id == "boot-a"
    assert transport.received_timeout == 2.5
    assert received.observed_owner_id == "node-a"  # advisory, not an ownership claim


def test_peer_exchange_persists_replay_boundary_before_restart_acceptance(tmp_path) -> None:
    heartbeat = _heartbeat(sequence=7)
    store = AtomicGenerationStore(tmp_path / "ha")
    first = PeerStateExchange(
        FakeTransport([AuthenticatedPeerMessage(heartbeat, "node-b")]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    first.receive(timeout_seconds=1)

    restarted = PeerStateExchange(
        FakeTransport([AuthenticatedPeerMessage(heartbeat, "node-b")]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=AtomicGenerationStore(tmp_path / "ha"),
    )
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        restarted.receive(timeout_seconds=1)


def test_peer_exchange_rejects_unauthenticated_identity_and_wrong_outbound_cluster() -> None:
    heartbeat = _heartbeat()
    transport = FakeTransport([AuthenticatedPeerMessage(heartbeat, "node-c")])
    exchange = PeerStateExchange(
        transport,
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=MemoryReplayStore(),
    )

    with pytest.raises(StateValidationError, match="authenticated peer"):
        exchange.receive(timeout_seconds=1)
    with pytest.raises(StateValidationError, match="wrong cluster"):
        exchange.send(_heartbeat(cluster_id="cluster-other"))
    assert transport.sent == []


def test_peer_exchange_requires_bounded_positive_receive_timeout() -> None:
    exchange = PeerStateExchange(
        FakeTransport([]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=MemoryReplayStore(),
    )

    with pytest.raises(ValueError, match="positive"):
        exchange.receive(timeout_seconds=0)
    with pytest.raises(ValueError, match="at most"):
        exchange.receive(timeout_seconds=31)


@pytest.mark.parametrize(
    ("factory", "purpose", "check_hostname"),
    [
        ("client_context", ssl.Purpose.SERVER_AUTH, True),
        ("server_context", ssl.Purpose.CLIENT_AUTH, False),
    ],
)
def test_mtls_contexts_require_peer_certificates_and_load_local_identity(
    factory: str, purpose: ssl.Purpose, check_hostname: bool
) -> None:
    context = Mock(spec=ssl.SSLContext)
    config = MutualTLSConfig(
        certificate_authority=Path("ca.pem"),
        certificate=Path("node.pem"),
        private_key=Path("node-key.pem"),
        server_hostname="peer.internal",
    )
    with patch("ssl.create_default_context", return_value=context) as create:
        result = getattr(config, factory)()

    assert result is context
    create.assert_called_once_with(purpose, cafile="ca.pem")
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    context.load_cert_chain.assert_called_once_with("node.pem", "node-key.pem")
