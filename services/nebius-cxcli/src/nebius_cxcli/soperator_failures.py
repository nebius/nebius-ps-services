"""Typed, sanitized failure boundaries for Soperator lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SoperatorFailureDisposition(StrEnum):
    """The only post-start outcomes understood by the upgrade supervisor."""

    RETRY = "retrying"
    SAFETY_PAUSE = "safety-paused"
    TERMINAL = "terminal-failed"


@dataclass(frozen=True)
class SoperatorMainWorkloadIdentity:
    """Exact non-secret identity accepted for a terminal workload observation."""

    api_version: str
    kind: str
    namespace: str
    name: str
    source_kind: str
    source_name: str
    source_revision: str
    uid: str
    generation: int
    observed_generation: int

    def __post_init__(self) -> None:
        text_fields = (
            self.api_version,
            self.kind,
            self.namespace,
            self.name,
            self.source_kind,
            self.source_name,
            self.source_revision,
            self.uid,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("main-workload identity fields must be non-empty")
        if self.generation < 1 or self.observed_generation != self.generation:
            raise ValueError("main-workload terminal identity requires one observed generation")

    @property
    def resource(self) -> str:
        return f"{self.namespace}/{self.name}"


class SoperatorSafetyPauseError(RuntimeError):
    """Signal that mutation must pause until authority or identity is re-proved."""

    def __init__(self, message: str, *, code: str = "safety-ambiguity") -> None:
        super().__init__(message)
        self.code = code


class SoperatorMainWorkloadTerminalError(RuntimeError):
    """Authoritative terminal failure of the frozen non-source main workload."""

    def __init__(
        self,
        message: str,
        *,
        identity: SoperatorMainWorkloadIdentity,
        reason: str = "terminal-condition",
    ) -> None:
        super().__init__(message)
        self.identity = identity
        self.reason = reason


class SoperatorInvocationEnvironmentError(RuntimeError):
    """Signal that the local cxcli runtime vanished during a durable operation."""


def soperator_invocation_environment_invalidated(exc: BaseException) -> bool:
    """Recognize only kubectl exec-auth failures caused by a missing cxcli runtime."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        detail = str(current).casefold()
        credential_exec_failed = "getting credentials" in detail and "exec:" in detail
        launcher_missing = "no such file or directory" in detail
        module_missing = "no module named" in detail and "nebius_cxcli" in detail
        if credential_exec_failed and (launcher_missing or module_missing):
            return True
        current = current.__cause__ or current.__context__
    return False


def soperator_failure_disposition(exc: BaseException) -> SoperatorFailureDisposition:
    """Classify only typed failures; ordinary exceptions remain retryable."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SoperatorMainWorkloadTerminalError) and current.identity is not None:
            return SoperatorFailureDisposition.TERMINAL
        if isinstance(current, SoperatorSafetyPauseError):
            return SoperatorFailureDisposition.SAFETY_PAUSE
        current = current.__cause__ or current.__context__
    return SoperatorFailureDisposition.RETRY


__all__ = [
    "SoperatorFailureDisposition",
    "SoperatorInvocationEnvironmentError",
    "SoperatorMainWorkloadIdentity",
    "SoperatorMainWorkloadTerminalError",
    "SoperatorSafetyPauseError",
    "soperator_failure_disposition",
    "soperator_invocation_environment_invalidated",
]
