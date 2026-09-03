"""Authenticated, bounded peer-state exchange over mutually authenticated TLS."""

from __future__ import annotations

import hashlib
import json
import math
import socket
import ssl
import struct
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .models import (
    PeerHeartbeat,
    PeerReplayGuard,
    ReplayState,
    StalePeerStateError,
    StateValidationError,
    canonical_json,
)
from .mtls import MTLSSnapshot

_FRAME_HEADER = struct.Struct("!I")
_DEFAULT_MAX_FRAME_BYTES = 64 * 1024
_MAX_CONFIGURABLE_FRAME_BYTES = 1024 * 1024
_NODE_ID_SAN_PREFIX = "urn:nebius-vpngw:node:"


class PeerTransportError(RuntimeError):
    """A secret-safe peer transport failure."""


def _require_positive_finite_timeout(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


@dataclass(frozen=True)
class AuthenticatedPeerMessage:
    """One heartbeat plus the peer identity proven by the transport channel."""

    heartbeat: PeerHeartbeat
    authenticated_node_id: str
    authenticated_certificate_fingerprint: str
    authenticated_mtls_epoch: int


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
    """Build fresh fail-closed contexts from one managed identity snapshot."""

    snapshot_provider: Callable[[], MTLSSnapshot]
    server_hostname: str

    def __post_init__(self) -> None:
        if not self.server_hostname.strip():
            raise ValueError("server_hostname must be non-empty")

    @staticmethod
    def _apply_common_context_policy(
        context: ssl.SSLContext,
        snapshot: MTLSSnapshot,
    ) -> None:
        if not snapshot.peer_certificate_pems:
            raise PeerTransportError("managed peer certificate trust is empty")
        peer_bundle = b"\n".join(snapshot.peer_certificate_pems).decode("ascii")
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.options |= ssl.OP_NO_TICKET
        context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        context.load_verify_locations(cadata=peer_bundle)
        context.load_cert_chain(
            str(snapshot.certificate_path),
            str(snapshot.private_key_path),
        )

    def client_context(self) -> tuple[ssl.SSLContext, MTLSSnapshot]:
        try:
            snapshot = self.snapshot_provider()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            self._apply_common_context_policy(context, snapshot)
            return context, snapshot
        except PeerTransportError:
            raise
        except (OSError, UnicodeError, ssl.SSLError, ValueError):
            raise PeerTransportError("unable to load managed peer TLS identity") from None

    def server_context(self) -> tuple[ssl.SSLContext, MTLSSnapshot]:
        try:
            snapshot = self.snapshot_provider()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.check_hostname = False
            self._apply_common_context_policy(context, snapshot)
            context.num_tickets = 0
            return context, snapshot
        except PeerTransportError:
            raise
        except (OSError, UnicodeError, ssl.SSLError, ValueError):
            raise PeerTransportError("unable to load managed peer TLS identity") from None


def _authenticated_node_id(peer_certificate: Mapping[str, Any] | None) -> str:
    """Derive the node identity solely from one verified URI SAN."""

    if not peer_certificate:
        raise PeerTransportError("peer certificate identity is missing")
    subject_alt_names = peer_certificate.get("subjectAltName")
    if not isinstance(subject_alt_names, (list, tuple)):
        raise PeerTransportError("peer certificate identity is missing")
    node_ids: list[str] = []
    for entry in subject_alt_names:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise PeerTransportError("peer certificate identity is malformed")
        kind, value = entry
        if kind == "URI" and isinstance(value, str) and value.startswith(_NODE_ID_SAN_PREFIX):
            node_ids.append(value.removeprefix(_NODE_ID_SAN_PREFIX))
    if len(node_ids) != 1 or not node_ids[0]:
        raise PeerTransportError("peer certificate must contain one node identity")
    return node_ids[0]


def _encode_heartbeat(heartbeat: PeerHeartbeat, *, max_frame_bytes: int) -> bytes:
    payload = canonical_json(heartbeat.to_dict()).encode("ascii")
    if len(payload) > max_frame_bytes:
        raise PeerTransportError("peer heartbeat exceeds the configured size limit")
    return _FRAME_HEADER.pack(len(payload)) + payload


def _decode_heartbeat(payload: bytes) -> PeerHeartbeat:
    try:
        text = payload.decode("ascii")
        value = json.loads(
            text,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, Mapping) or canonical_json(value) != text:
            raise ValueError
        return PeerHeartbeat.from_mapping(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise PeerTransportError("peer heartbeat is malformed or non-canonical") from None


class _Deadline:
    def __init__(self, timeout_seconds: float) -> None:
        _require_positive_finite_timeout("timeout_seconds", timeout_seconds)
        self._end = time.monotonic() + timeout_seconds

    def remaining(self) -> float:
        remaining = self._end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining


def _receive_exact(channel: ssl.SSLSocket, size: int, deadline: _Deadline) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        channel.settimeout(deadline.remaining())
        chunk = channel.recv(size - len(chunks))
        if not chunk:
            raise PeerTransportError("peer closed the heartbeat frame early")
        chunks.extend(chunk)
    return bytes(chunks)


class MutualTLSPeerTransport:
    """One-message-per-connection mTLS transport with strict framing and deadlines."""

    def __init__(
        self,
        tls: MutualTLSConfig,
        *,
        peer_host: str,
        peer_port: int,
        listen_host: str,
        listen_port: int,
        expected_peer_node_id: str,
        connect_timeout_seconds: float = 5.0,
        send_timeout_seconds: float = 5.0,
        max_frame_bytes: int = _DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if not peer_host or not listen_host or not expected_peer_node_id:
            raise ValueError("peer, listener, and expected node identities must be non-empty")
        if not 1 <= peer_port <= 65535 or not 1 <= listen_port <= 65535:
            raise ValueError("peer and listener ports must be between 1 and 65535")
        _require_positive_finite_timeout("connect_timeout_seconds", connect_timeout_seconds)
        _require_positive_finite_timeout("send_timeout_seconds", send_timeout_seconds)
        if not 1 <= max_frame_bytes <= _MAX_CONFIGURABLE_FRAME_BYTES:
            raise ValueError("max_frame_bytes is outside the bounded transport limit")
        self._tls = tls
        self._peer_address = (peer_host, peer_port)
        self._listen_address = (listen_host, listen_port)
        self._expected_peer_node_id = expected_peer_node_id
        self._connect_timeout_seconds = connect_timeout_seconds
        self._send_timeout_seconds = send_timeout_seconds
        self._max_frame_bytes = max_frame_bytes

    def _verify_peer(
        self,
        channel: ssl.SSLSocket,
        snapshot: MTLSSnapshot,
    ) -> tuple[str, str, int]:
        node_id = _authenticated_node_id(channel.getpeercert())
        if node_id != self._expected_peer_node_id:
            raise PeerTransportError("peer certificate belongs to the wrong node")
        certificate_der = channel.getpeercert(binary_form=True)
        if not isinstance(certificate_der, bytes) or not certificate_der:
            raise PeerTransportError("peer certificate identity is missing")
        fingerprint = hashlib.sha256(certificate_der).hexdigest()
        peers = {
            peer.certificate_fingerprint: peer
            for peer in snapshot.peers
            if peer.node_id == self._expected_peer_node_id
        }
        peer = peers.get(fingerprint)
        if peer is None:
            raise PeerTransportError("peer certificate is not an exact managed pin")
        return node_id, fingerprint, peer.epoch

    def send(self, heartbeat: PeerHeartbeat) -> None:
        deadline = _Deadline(self._send_timeout_seconds)
        try:
            context, snapshot = self._tls.client_context()
            if (
                heartbeat.certificate_fingerprint != snapshot.certificate_fingerprint
                or heartbeat.mtls_epoch != snapshot.epoch
            ):
                raise PeerTransportError("outbound heartbeat mTLS identity is stale")
            frame = _encode_heartbeat(
                heartbeat,
                max_frame_bytes=self._max_frame_bytes,
            )
            raw = socket.create_connection(
                self._peer_address,
                timeout=min(self._connect_timeout_seconds, deadline.remaining()),
            )
            with raw:
                raw.settimeout(deadline.remaining())
                with context.wrap_socket(
                    raw,
                    server_hostname=self._tls.server_hostname,
                ) as channel:
                    self._verify_peer(channel, snapshot)
                    channel.settimeout(deadline.remaining())
                    channel.sendall(frame)
        except PeerTransportError:
            raise
        except TimeoutError:
            raise PeerTransportError("peer transport timed out") from None
        except (OSError, ssl.SSLError, ValueError):
            raise PeerTransportError("peer authentication or transport failed") from None

    def receive(self, *, timeout_seconds: float) -> AuthenticatedPeerMessage:
        deadline = _Deadline(timeout_seconds)
        try:
            context, snapshot = self._tls.server_context()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(self._listen_address)
                listener.listen(1)
                listener.settimeout(deadline.remaining())
                raw, _address = listener.accept()
                with raw:
                    raw.settimeout(deadline.remaining())
                    with context.wrap_socket(
                        raw,
                        server_side=True,
                    ) as channel:
                        node_id, fingerprint, epoch = self._verify_peer(channel, snapshot)
                        header = _receive_exact(channel, _FRAME_HEADER.size, deadline)
                        (size,) = _FRAME_HEADER.unpack(header)
                        if size == 0 or size > self._max_frame_bytes:
                            raise PeerTransportError("peer heartbeat frame has an invalid size")
                        payload = _receive_exact(channel, size, deadline)
                        heartbeat = _decode_heartbeat(payload)
                        return AuthenticatedPeerMessage(
                            heartbeat,
                            node_id,
                            fingerprint,
                            epoch,
                        )
        except PeerTransportError:
            raise
        except TimeoutError:
            raise PeerTransportError("peer transport timed out") from None
        except (OSError, ssl.SSLError, struct.error, ValueError):
            raise PeerTransportError("peer authentication or transport failed") from None


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
        max_heartbeat_age_seconds: float | None = None,
        max_clock_skew_seconds: float = 5.0,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not cluster_id or not peer_node_id:
            raise ValueError("cluster_id and peer_node_id must be non-empty")
        _require_positive_finite_timeout("max_timeout_seconds", max_timeout_seconds)
        if max_heartbeat_age_seconds is not None:
            _require_positive_finite_timeout("max_heartbeat_age_seconds", max_heartbeat_age_seconds)
        if not math.isfinite(max_clock_skew_seconds) or max_clock_skew_seconds < 0:
            raise ValueError("max_clock_skew_seconds must be finite and non-negative")
        self.transport = transport
        self.cluster_id = cluster_id
        self.peer_node_id = peer_node_id
        self.replay_store = replay_store
        self.replay_guard = PeerReplayGuard(replay_store.load_replay_state(peer_node_id))
        self.max_timeout_seconds = max_timeout_seconds
        self.max_heartbeat_age_seconds = max_heartbeat_age_seconds
        self.max_clock_skew_seconds = max_clock_skew_seconds
        self.wall_clock = wall_clock

    def _require_fresh_timestamp(self, heartbeat: PeerHeartbeat) -> None:
        if self.max_heartbeat_age_seconds is None:
            return
        try:
            sent_at = datetime.fromisoformat(heartbeat.sent_at.removesuffix("Z") + "+00:00")
            if sent_at.tzinfo is None:
                raise ValueError
            sent_at_seconds = sent_at.astimezone(timezone.utc).timestamp()
        except (OverflowError, ValueError):
            raise StateValidationError("peer heartbeat timestamp is invalid") from None
        now = self.wall_clock()
        if not math.isfinite(now):
            raise StateValidationError("peer heartbeat clock is unavailable")
        age = now - sent_at_seconds
        if age < -self.max_clock_skew_seconds:
            raise StalePeerStateError("peer heartbeat timestamp is in the future")
        if age > self.max_heartbeat_age_seconds:
            raise StalePeerStateError("peer heartbeat timestamp is stale")

    def send(self, heartbeat: PeerHeartbeat) -> None:
        if heartbeat.cluster_id != self.cluster_id:
            raise StateValidationError("outbound heartbeat has the wrong cluster identity")
        self.transport.send(heartbeat)

    def receive(self, *, timeout_seconds: float) -> tuple[PeerHeartbeat, ReplayState]:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if timeout_seconds > self.max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds must be positive and at most {self.max_timeout_seconds}"
            )
        message = self.transport.receive(timeout_seconds=timeout_seconds)
        candidate = PeerReplayGuard(self.replay_guard.state)
        replay_state = candidate.accept(
            message.heartbeat,
            authenticated_node_id=message.authenticated_node_id,
            authenticated_certificate_fingerprint=(message.authenticated_certificate_fingerprint),
            authenticated_mtls_epoch=message.authenticated_mtls_epoch,
            expected_cluster_id=self.cluster_id,
            expected_node_id=self.peer_node_id,
        )
        self._require_fresh_timestamp(message.heartbeat)
        self.replay_store.save_replay_state(self.peer_node_id, replay_state)
        self.replay_guard = candidate
        return message.heartbeat, replay_state
