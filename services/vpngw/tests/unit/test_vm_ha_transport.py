from __future__ import annotations

import errno
import hashlib
import json
import socket
import ssl
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
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
from nebius_vpngw.agent.vm_ha import store as state_store
from nebius_vpngw.agent.vm_ha.mtls import (
    ManagedMTLSStore,
    MTLSOperationKind,
    MTLSSnapshot,
    PeerLeaf,
)
from nebius_vpngw.agent.vm_ha.runtime import BoundPeerRuntime
from nebius_vpngw.agent.vm_ha.transport import (
    MutualTLSPeerTransport,
    PeerTransportError,
    _authenticated_node_id,
    _decode_heartbeat,
    _encode_heartbeat,
)

_PEER_DER = b"managed-peer-node-b"
_PEER_FINGERPRINT = hashlib.sha256(_PEER_DER).hexdigest()


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
        mtls_epoch=1,
        certificate_fingerprint=_PEER_FINGERPRINT,
        digests=DigestSet(digest, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )


def _message(
    heartbeat: PeerHeartbeat,
    node_id: str = "node-b",
    *,
    fingerprint: str = _PEER_FINGERPRINT,
    epoch: int = 1,
) -> AuthenticatedPeerMessage:
    return AuthenticatedPeerMessage(heartbeat, node_id, fingerprint, epoch)


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

    def save_accepted_peer_heartbeat(
        self, peer_node_id: str, heartbeat: PeerHeartbeat, *, replay_state: ReplayState
    ) -> None:
        assert peer_node_id == "node-b"
        assert heartbeat.sequence == replay_state.highest_sequence
        self.state = replay_state


def test_authenticated_peer_publication_never_exposes_partial_pair(tmp_path, monkeypatch):
    store = AtomicGenerationStore(tmp_path / "ha")
    reader = AtomicGenerationStore(store.root)
    heartbeat = _heartbeat(sequence=1)
    exchange = PeerStateExchange(
        FakeTransport([_message(_heartbeat()), _message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    runtime = BoundPeerRuntime(
        exchange, clock=lambda: 1.0, peer_node_id="node-b", heartbeat_store=store
    )
    runtime.poll(timeout_seconds=1.0)
    observations = []
    locked = store._locked

    @contextmanager
    def observe_each_unlocked_boundary():
        with locked():
            yield
        # Deterministically schedule an independent reader at every boundary
        # where another process can acquire the native publication lock.
        replay = reader.load_replay_state("node-b")
        if replay is not None and replay.highest_sequence == heartbeat.sequence:
            observations.append(reader.load_accepted_peer_heartbeat("node-b"))

    monkeypatch.setattr(store, "_locked", observe_each_unlocked_boundary)
    assert runtime.poll(timeout_seconds=1.0) == heartbeat
    assert observations and all(item == heartbeat for item in observations)


def test_peer_exchange_recovers_later_boot_after_failed_cache_publication(tmp_path, monkeypatch):
    store = AtomicGenerationStore(tmp_path / "ha")
    heartbeats = [replace(_heartbeat(), boot_id=f"boot-{boot}") for boot in ("a", "b", "c")]
    exchange = PeerStateExchange(
        FakeTransport([_message(heartbeat) for heartbeat in heartbeats]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    exchange.receive(timeout_seconds=1.0)
    write = state_store.atomic_write_json

    def fail_cache_write(path, value):
        if path.name == "accepted-heartbeat.json":
            raise OSError("injected cache publication failure")
        write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, "atomic_write_json", fail_cache_write)
        with pytest.raises(OSError, match="injected cache publication failure"):
            exchange.receive(timeout_seconds=1.0)
    assert store.load_replay_state("node-b") == ReplayState("boot-b", 0, ("boot-a",))
    assert store.load_accepted_peer_heartbeat("node-b") is None
    assert exchange.receive(timeout_seconds=1.0) == (
        heartbeats[2],
        ReplayState("boot-c", 0, ("boot-a", "boot-b")),
    )
    assert store.load_accepted_peer_heartbeat("node-b") == heartbeats[2]


def test_running_peer_exchange_retains_replay_floor_if_durable_file_is_lost(tmp_path):
    store = AtomicGenerationStore(tmp_path / "ha")
    heartbeat = _heartbeat(sequence=1)
    exchange = PeerStateExchange(
        FakeTransport([_message(heartbeat), _message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    exchange.receive(timeout_seconds=1.0)
    (store.peers / "node-b" / "replay.json").unlink()
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        exchange.receive(timeout_seconds=1.0)


@pytest.mark.parametrize("failed_file", ["replay.json", "accepted-heartbeat.json"])
def test_failed_publication_consumes_sequence_without_returning_parity(
    tmp_path, monkeypatch, failed_file
):
    store = AtomicGenerationStore(tmp_path / "ha")
    heartbeats = [replace(_heartbeat(), boot_id=f"boot-{boot}") for boot in ("a", "b", "c")]
    exchange = PeerStateExchange(
        FakeTransport([_message(h) for h in [*heartbeats[:2], *heartbeats[1:]]]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    exchange.receive(timeout_seconds=1.0)
    write = state_store.atomic_write_json

    def fail_publication(path, value):
        if path.name == failed_file:
            raise OSError("injected publication failure")
        write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, "atomic_write_json", fail_publication)
        with pytest.raises(OSError, match="injected publication failure"):
            exchange.receive(timeout_seconds=1.0)
    assert store.load_accepted_peer_heartbeat("node-b") != heartbeats[1]
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        exchange.receive(timeout_seconds=1.0)
    assert exchange.receive(timeout_seconds=1.0) == (
        heartbeats[2],
        ReplayState("boot-c", 0, ("boot-a", "boot-b")),
    )
    assert store.load_accepted_peer_heartbeat("node-b") == heartbeats[2]


def test_peer_exchange_accepts_authenticated_monotonic_advisory_state() -> None:
    heartbeat = _heartbeat()
    transport = FakeTransport([_message(heartbeat)])
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
        FakeTransport([_message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )
    first.receive(timeout_seconds=1)

    restarted = PeerStateExchange(
        FakeTransport([_message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=AtomicGenerationStore(tmp_path / "ha"),
    )
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        restarted.receive(timeout_seconds=1)


def test_peer_exchange_accepts_first_observed_sequence_for_authenticated_new_boot() -> None:
    store = MemoryReplayStore(ReplayState("boot-a", 4))
    initial = _heartbeat(sequence=9)
    heartbeat = PeerHeartbeat.from_mapping({**initial.to_dict(), "boot_id": "boot-b"})
    exchange = PeerStateExchange(
        FakeTransport([_message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
    )

    received, replay = exchange.receive(timeout_seconds=1)

    assert received == heartbeat
    assert replay == ReplayState("boot-b", 9, ("boot-a",))
    assert store.state == replay


def test_peer_exchange_rejects_unauthenticated_identity_and_wrong_outbound_cluster() -> None:
    heartbeat = _heartbeat()
    transport = FakeTransport([_message(heartbeat, "node-c")])
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
    ("sent_at", "message"),
    [
        ("2026-08-12T00:00:00Z", "stale"),
        ("2026-08-12T00:01:00Z", "future"),
    ],
)
def test_peer_exchange_rejects_stale_or_future_authenticated_timestamp(
    sent_at: str, message: str
) -> None:
    heartbeat = _heartbeat()
    heartbeat = PeerHeartbeat.from_mapping({**heartbeat.to_dict(), "sent_at": sent_at})
    store = MemoryReplayStore()
    exchange = PeerStateExchange(
        FakeTransport([_message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
        max_heartbeat_age_seconds=10,
        max_clock_skew_seconds=2,
        wall_clock=lambda: 1786492830.0,
    )

    with pytest.raises(StalePeerStateError, match=message):
        exchange.receive(timeout_seconds=1)

    assert store.state is None


def test_peer_exchange_accepts_fresh_timestamp_and_persists_after_validation() -> None:
    heartbeat = _heartbeat()
    heartbeat = PeerHeartbeat.from_mapping(
        {**heartbeat.to_dict(), "sent_at": "2026-08-12T00:00:25Z"}
    )
    store = MemoryReplayStore()
    exchange = PeerStateExchange(
        FakeTransport([_message(heartbeat)]),
        cluster_id="cluster-a",
        peer_node_id="node-b",
        replay_store=store,
        max_heartbeat_age_seconds=10,
        wall_clock=lambda: 1786492830.0,
    )

    received, replay = exchange.receive(timeout_seconds=1)

    assert received == heartbeat
    assert store.state == replay


def _managed_store_pair(tmp_path: Path) -> tuple[ManagedMTLSStore, ManagedMTLSStore]:
    operation_id = hashlib.sha256(b"transport-bootstrap").hexdigest()
    first = ManagedMTLSStore(tmp_path / "node-a")
    second = ManagedMTLSStore(tmp_path / "node-b")
    first_receipt = first.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="cluster-a",
        node_id="node-a",
        compute_id="compute-a",
        target_epoch=1,
    )
    second_receipt = second.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="cluster-a",
        node_id="node-b",
        compute_id="compute-b",
        target_epoch=1,
    )
    first.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="node-b",
        peer_compute_id="compute-b",
        peer_epoch=1,
        certificate_pem=second_receipt.certificate_pem,
    )
    second.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="node-a",
        peer_compute_id="compute-a",
        peer_epoch=1,
        certificate_pem=first_receipt.certificate_pem,
    )
    first.activate_identity(operation_id)
    second.activate_identity(operation_id)
    return first, second


@pytest.mark.parametrize(
    ("factory", "check_hostname"),
    [("client_context", True), ("server_context", False)],
)
def test_mtls_contexts_load_vm_local_identity_and_exact_peer_leaf(
    tmp_path: Path,
    factory: str,
    check_hostname: bool,
) -> None:
    first, _second = _managed_store_pair(tmp_path)
    config = MutualTLSConfig(first.snapshot, server_hostname="node-b")

    context, snapshot = getattr(config, factory)()

    assert snapshot == first.snapshot()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.options & ssl.OP_NO_TICKET


def test_mtls_context_requires_stable_peer_hostname() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        MutualTLSConfig(lambda: Mock(), server_hostname="")


def test_mtls_context_redacts_snapshot_provider_failure() -> None:
    def failed_snapshot() -> MTLSSnapshot:
        raise ValueError("TEST-PRIVATE-KEY-BYTES at /secret/node-key.pem")

    config = MutualTLSConfig(failed_snapshot, server_hostname="node-b")

    with pytest.raises(PeerTransportError) as error:
        config.client_context()

    assert "TEST-PRIVATE-KEY-BYTES" not in str(error.value)
    assert "/secret/node-key.pem" not in str(error.value)


def test_certificate_identity_requires_one_verified_node_uri() -> None:
    assert (
        _authenticated_node_id(
            {"subjectAltName": (("DNS", "peer.internal"), ("URI", "urn:nebius-vpngw:node:node-b"))}
        )
        == "node-b"
    )
    with pytest.raises(PeerTransportError, match="one node identity"):
        _authenticated_node_id(
            {
                "subjectAltName": (
                    ("URI", "urn:nebius-vpngw:node:node-b"),
                    ("URI", "urn:nebius-vpngw:node:node-c"),
                )
            }
        )
    with pytest.raises(PeerTransportError, match="one node identity"):
        _authenticated_node_id({"subjectAltName": (("DNS", "peer.internal"),)})
    with pytest.raises(PeerTransportError, match="malformed"):
        _authenticated_node_id({"subjectAltName": (("URI",),)})


def test_heartbeat_frame_is_canonical_and_size_bounded() -> None:
    heartbeat = _heartbeat(sequence=9)
    frame = _encode_heartbeat(heartbeat, max_frame_bytes=4096)
    (size,) = struct.unpack("!I", frame[:4])

    assert size == len(frame) - 4
    assert _decode_heartbeat(frame[4:]) == heartbeat
    with pytest.raises(PeerTransportError, match="size limit"):
        _encode_heartbeat(heartbeat, max_frame_bytes=1)

    noncanonical = json.dumps(heartbeat.to_dict()).encode("ascii")
    with pytest.raises(PeerTransportError, match="non-canonical"):
        _decode_heartbeat(noncanonical)
    with pytest.raises(PeerTransportError, match="malformed"):
        _decode_heartbeat(b"{not-json")


class _FakeRawSocket:
    def __enter__(self) -> _FakeRawSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None


class _FakeTLSChannel(_FakeRawSocket):
    def __init__(self, peer_node_id: str, inbound: bytes = b"", der: bytes = _PEER_DER) -> None:
        self._certificate = {"subjectAltName": (("URI", f"urn:nebius-vpngw:node:{peer_node_id}"),)}
        self._der = der
        self._inbound = bytearray(inbound)
        self.sent = bytearray()

    def getpeercert(self, binary_form: bool = False) -> dict[str, object] | bytes:
        return self._der if binary_form else self._certificate

    def sendall(self, value: bytes) -> None:
        self.sent.extend(value)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self._inbound[:size])
        del self._inbound[:size]
        return chunk


@dataclass
class _FakeTLSContext:
    channel: _FakeTLSChannel

    def wrap_socket(self, _raw: object, **_kwargs: object) -> _FakeTLSChannel:
        return self.channel


@dataclass
class _FakeTLSConfig:
    client_channel: _FakeTLSChannel
    server_channel: _FakeTLSChannel
    server_hostname: str = "peer.internal"

    @staticmethod
    def _snapshot() -> MTLSSnapshot:
        peer = PeerLeaf(
            node_id="node-b",
            compute_id="compute-b",
            epoch=1,
            certificate_fingerprint=_PEER_FINGERPRINT,
            spki_fingerprint="e" * 64,
        )
        return MTLSSnapshot(
            cluster_id="cluster-a",
            node_id="node-b",
            compute_id="compute-b",
            epoch=1,
            certificate_fingerprint=_PEER_FINGERPRINT,
            spki_fingerprint="f" * 64,
            certificate_path=Path("/unused/certificate.pem"),
            private_key_path=Path("/unused/private-key.pem"),
            peers=(peer,),
            current_peer=peer,
            peer_certificate_paths=(Path("/unused/peer.pem"),),
            peer_certificate_pems=(b"unused",),
        )

    def client_context(self) -> tuple[_FakeTLSContext, MTLSSnapshot]:
        return _FakeTLSContext(self.client_channel), self._snapshot()

    def server_context(self) -> tuple[_FakeTLSContext, MTLSSnapshot]:
        return _FakeTLSContext(self.server_channel), self._snapshot()


class _FakeListener(_FakeRawSocket):
    def __init__(self) -> None:
        self.raw = _FakeRawSocket()

    def setsockopt(self, *_args: object) -> None:
        return None

    def bind(self, _address: object) -> None:
        return None

    def listen(self, _backlog: int) -> None:
        return None

    def accept(self) -> tuple[_FakeRawSocket, tuple[str, int]]:
        return self.raw, ("127.0.0.1", 12345)


def _concrete_transport(tls: _FakeTLSConfig) -> MutualTLSPeerTransport:
    return MutualTLSPeerTransport(
        tls,  # type: ignore[arg-type]
        peer_host="127.0.0.1",
        peer_port=9443,
        listen_host="127.0.0.1",
        listen_port=9444,
        expected_peer_node_id="node-b",
    )


def test_concrete_transport_exchanges_authenticated_heartbeat_between_fake_endpoints() -> None:
    heartbeat = _heartbeat(sequence=11)
    client_channel = _FakeTLSChannel("node-b")
    tls = _FakeTLSConfig(client_channel, _FakeTLSChannel("node-b"))
    transport = _concrete_transport(tls)

    with patch("socket.create_connection", return_value=_FakeRawSocket()):
        transport.send(heartbeat)

    tls.server_channel = _FakeTLSChannel("node-b", bytes(client_channel.sent))
    with patch("socket.socket", return_value=_FakeListener()):
        exchange = PeerStateExchange(
            transport,
            cluster_id="cluster-a",
            peer_node_id="node-b",
            replay_store=MemoryReplayStore(),
        )
        received, replay = exchange.receive(timeout_seconds=1)

    assert received == heartbeat
    assert replay == ReplayState("boot-a", 11)


def test_concrete_transport_rejects_wrong_certificate_before_accepting_state() -> None:
    frame = _encode_heartbeat(_heartbeat(), max_frame_bytes=4096)
    tls = _FakeTLSConfig(_FakeTLSChannel("node-c"), _FakeTLSChannel("node-c", frame))
    transport = _concrete_transport(tls)

    with (
        patch("socket.socket", return_value=_FakeListener()),
        pytest.raises(PeerTransportError, match="wrong node"),
    ):
        transport.receive(timeout_seconds=1)


@pytest.mark.parametrize(
    "frame",
    [struct.pack("!I", 0), struct.pack("!I", 65 * 1024), struct.pack("!I", 8) + b"{}"],
)
def test_concrete_transport_rejects_invalid_or_incomplete_frames(frame: bytes) -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b", frame))
    transport = _concrete_transport(tls)

    with (
        patch("socket.socket", return_value=_FakeListener()),
        pytest.raises(PeerTransportError, match="invalid size|closed"),
    ):
        transport.receive(timeout_seconds=1)


def test_concrete_transport_redacts_socket_and_tls_failures() -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b"))
    transport = _concrete_transport(tls)
    sensitive = "PRIVATE-KEY-BYTES /secret/node-key.pem"

    with (
        patch("socket.create_connection", side_effect=OSError(sensitive)),
        pytest.raises(PeerTransportError) as error,
    ):
        transport.send(_heartbeat())

    assert sensitive not in str(error.value)
    assert "/secret/node-key.pem" not in str(error.value)


def test_concrete_transport_reports_bounded_timeout() -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b"))
    transport = _concrete_transport(tls)

    with (
        patch("socket.socket", side_effect=TimeoutError("sensitive endpoint")),
        pytest.raises(PeerTransportError, match="timed out") as error,
    ):
        transport.receive(timeout_seconds=0.001)

    assert "sensitive endpoint" not in str(error.value)


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf")])
def test_concrete_transport_rejects_non_finite_timeouts(timeout_seconds: float) -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b"))

    with pytest.raises(ValueError, match="finite"):
        MutualTLSPeerTransport(
            tls,  # type: ignore[arg-type]
            peer_host="127.0.0.1",
            peer_port=9443,
            listen_host="127.0.0.1",
            listen_port=9444,
            expected_peer_node_id="node-b",
            connect_timeout_seconds=timeout_seconds,
        )

    transport = _concrete_transport(tls)
    with pytest.raises(ValueError, match="finite"):
        transport.receive(timeout_seconds=timeout_seconds)


def test_concrete_transport_rejects_untrusted_certificate_before_state() -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b"))
    transport = _concrete_transport(tls)
    context = Mock()
    context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
        "certificate verify failed: PRIVATE-KEY-BYTES"
    )

    with (
        patch.object(tls, "server_context", return_value=(context, tls._snapshot())),
        patch("socket.socket", return_value=_FakeListener()),
        pytest.raises(PeerTransportError) as error,
    ):
        transport.receive(timeout_seconds=1)

    assert "PRIVATE-KEY-BYTES" not in str(error.value)


@pytest.mark.parametrize(
    ("side_effect", "kind"),
    [
        (TimeoutError("sensitive endpoint"), "timeout"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "sensitive endpoint"), "refused"),
        (OSError(errno.EHOSTUNREACH, "sensitive endpoint"), "unreachable"),
        (OSError(errno.ENETUNREACH, "sensitive endpoint"), "unreachable"),
        # The OSError constructor maps ETIMEDOUT to TimeoutError, which the
        # timeout clause owns before errno classification is consulted.
        (OSError(errno.ETIMEDOUT, "sensitive endpoint"), "timeout"),
        (socket.gaierror(8, "sensitive endpoint"), "unreachable"),
        (ssl.SSLError(1, "sensitive endpoint"), "tls"),
        (OSError("sensitive endpoint"), "protocol"),
    ],
)
def test_concrete_transport_classifies_send_failures(side_effect: Exception, kind: str) -> None:
    tls = _FakeTLSConfig(_FakeTLSChannel("node-b"), _FakeTLSChannel("node-b"))
    transport = _concrete_transport(tls)

    with (
        patch("socket.create_connection", side_effect=side_effect),
        pytest.raises(PeerTransportError) as error,
    ):
        transport.send(_heartbeat())

    assert error.value.kind == kind
    assert "sensitive endpoint" not in str(error.value)


def test_concrete_transport_marks_certificate_pinning_failures_as_tls() -> None:
    frame = _encode_heartbeat(_heartbeat(), max_frame_bytes=4096)
    tls = _FakeTLSConfig(_FakeTLSChannel("node-c"), _FakeTLSChannel("node-c", frame))
    transport = _concrete_transport(tls)

    with (
        patch("socket.socket", return_value=_FakeListener()),
        pytest.raises(PeerTransportError) as error,
    ):
        transport.receive(timeout_seconds=1)

    assert error.value.kind == "tls"


def test_peer_transport_error_kind_defaults_and_rejects_unknown_values() -> None:
    assert PeerTransportError("plain").kind == "protocol"
    with pytest.raises(ValueError, match="kind"):
        PeerTransportError("plain", kind="novel")
