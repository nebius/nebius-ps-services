"""Bounded diagnostic evidence; never an authorization or readiness receipt."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_CODE = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")


@dataclass(frozen=True)
class Failure:
    phase: str
    predicate: str
    reasons: tuple[str, ...] = ()
    lock_reinstated: bool | None = None

    def __post_init__(self):
        if not all(
            isinstance(value, str) and _CODE.fullmatch(value)
            for value in (self.phase, self.predicate, *self.reasons)
        ):
            raise ValueError("invalid failure reason")
        if (
            len(self.reasons) > 16
            or self.lock_reinstated is not None
            and type(self.lock_reinstated) is not bool
        ):
            raise ValueError("invalid failure evidence")

    def mapping(self) -> dict:
        return asdict(self)


class DiagnosticFailure(RuntimeError):
    """Product-owned, bounded evidence carried through public error wrappers."""

    first: Failure | None = None


class ObservationUnavailable(DiagnosticFailure):
    """An unavailable read leaves the current operation pending, never repairable."""

    def __init__(self, phase: str, reason: str, *, retryable: bool = True) -> None:
        self.retryable = retryable
        self.first = Failure(phase, "status-observation-unavailable", (reason,))
        super().__init__(
            "HA observation pending; resume the same operation for fresh evidence"
            if retryable
            else "HA observation blocked; verify exact identity, access and operation state"
        )


def unavailable_observation(error: BaseException | None) -> ObservationUnavailable | None:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ObservationUnavailable):
            return error
        error = error.__cause__ or error.__context__
    return None


def observation_pending(error: BaseException | None) -> bool:
    unavailable = unavailable_observation(error)
    return unavailable is not None and getattr(unavailable, "retryable", True)


def latest_failure(error: BaseException | None) -> Failure | None:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, DiagnosticFailure):
            value = getattr(error, "latest", None) or error.first
            if isinstance(value, Failure):
                return value
        error = error.__cause__ or error.__context__
    return None


class VerificationFailure(DiagnosticFailure):
    first: Failure

    def __init__(self, first: Failure, latest: Failure):
        self.first, self.latest = first, latest
        super().__init__(
            f"VM-HA status verification failed: first phase={first.phase} "
            f"predicate={first.predicate}; latest predicate={latest.predicate}"
        )


def first_failure(error: BaseException | None) -> Failure | None:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, DiagnosticFailure) and error.first is not None:
            return error.first
        error = error.__cause__ or error.__context__
    return None
