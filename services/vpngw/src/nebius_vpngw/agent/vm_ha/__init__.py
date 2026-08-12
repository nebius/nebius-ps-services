"""Durable, non-authoritative VM-HA state and peer exchange primitives."""

from .models import (
    DigestSet,
    GenerationRevision,
    PeerHeartbeat,
    PeerReplayGuard,
    ReplayState,
    StalePeerStateError,
    StateValidationError,
    TransitionRecord,
)
from .store import (
    AtomicGenerationStore,
    CorruptStateError,
    GenerationPointers,
    atomic_write_json,
)
from .transport import (
    AuthenticatedPeerMessage,
    MutualTLSConfig,
    PeerStateExchange,
    PeerTransport,
    ReplayStateStore,
)

__all__ = [
    "AtomicGenerationStore",
    "AuthenticatedPeerMessage",
    "CorruptStateError",
    "DigestSet",
    "GenerationPointers",
    "GenerationRevision",
    "MutualTLSConfig",
    "PeerHeartbeat",
    "PeerReplayGuard",
    "PeerStateExchange",
    "PeerTransport",
    "ReplayState",
    "ReplayStateStore",
    "StalePeerStateError",
    "StateValidationError",
    "TransitionRecord",
    "atomic_write_json",
]
