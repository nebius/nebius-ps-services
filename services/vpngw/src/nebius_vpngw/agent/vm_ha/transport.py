"""Authenticated, bounded peer-state exchange over mutually authenticated TLS."""

from __future__ import annotations

import json
import math
import socket
import ssl
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import (
    PeerHeartbeat,
    PeerReplayGuard,
    ReplayState,
    StateValidationError,
    canonical_json,
)

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

    @staticmethod
    def _require_credential_file(path: Path, field_name: str) -> None:
        if not path.is_absolute():
            raise ValueError(f"{field_name} must be an absolute file path")
        try:
            if not path.is_file():
                raise ValueError(f"{field_name} must be a readable regular file")
            with path.open("rb"):
                pass
        except OSError:
            raise ValueError(f"{field_name} must be a readable regular file") from None

    def _validate_credential_files(self) -> None:
        self._require_credential_file(self.certificate_authority, "certificate_authority")
        self._require_credential_file(self.certificate, "certificate")
        self._require_credential_file(self.private_key, "private_key")

    def client_context(self) -> ssl.SSLContext:
        self._validate_credential_files()
        try:
            context = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH, cafile=str(self.certificate_authority)
            )
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_cert_chain(str(self.certificate), str(self.private_key))
            return context
        except (OSError, ssl.SSLError, ValueError):
            raise PeerTransportError("unable to load peer TLS credentials") from None

    def server_context(self) -> ssl.SSLContext:
        self._validate_credential_files()
        try:
            context = ssl.create_default_context(
                ssl.Purpose.CLIENT_AUTH, cafile=str(self.certificate_authority)
            )
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_cert_chain(str(self.certificate), str(self.private_key))
            return context
        except (OSError, ssl.SSLError, ValueError):
            raise PeerTransportError("unable to load peer TLS credentials") from None


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

    def _verify_peer(self, channel: ssl.SSLSocket) -> str:
        node_id = _authenticated_node_id(channel.getpeercert())
        if node_id != self._expected_peer_node_id:
            raise PeerTransportError("peer certificate belongs to the wrong node")
        return node_id

    def send(self, heartbeat: PeerHeartbeat) -> None:
        deadline = _Deadline(self._send_timeout_seconds)
        try:
            context = self._tls.client_context()
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
                    self._verify_peer(channel)
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
            context = self._tls.server_context()
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
                        node_id = self._verify_peer(channel)
                        header = _receive_exact(channel, _FRAME_HEADER.size, deadline)
                        (size,) = _FRAME_HEADER.unpack(header)
                        if size == 0 or size > self._max_frame_bytes:
                            raise PeerTransportError("peer heartbeat frame has an invalid size")
                        payload = _receive_exact(channel, size, deadline)
                        heartbeat = _decode_heartbeat(payload)
                        return AuthenticatedPeerMessage(heartbeat, node_id)
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
    ) -> None:
        if not cluster_id or not peer_node_id:
            raise ValueError("cluster_id and peer_node_id must be non-empty")
        _require_positive_finite_timeout("max_timeout_seconds", max_timeout_seconds)
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
            expected_cluster_id=self.cluster_id,
            expected_node_id=self.peer_node_id,
        )
        self.replay_store.save_replay_state(self.peer_node_id, replay_state)
        self.replay_guard = candidate
        return message.heartbeat, replay_state
