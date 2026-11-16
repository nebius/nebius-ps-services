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


class SSHBootstrapConflict(DiagnosticFailure):
    """A selected or published SSH prerequisite no longer matches its evidence."""

    first = Failure("ssh-bootstrap", "ssh-bootstrap-conflict")


class PackageReservationConflict(DiagnosticFailure):
    """A package request no longer matches its original command authority."""

    first = Failure("package-admission", "package-reservation-conflict")


class ManagedMTLSActionFailure(DiagnosticFailure):
    """Recognize only fixed guest diagnostics; never retain remote stderr."""

    def __init__(self, stderr: bytes):
        permissions = stderr.strip() in {
            b"managed mTLS action failed: managed mTLS state directory permissions are invalid",
            b"managed mTLS action failed: managed mTLS state permissions are invalid",
            b"managed mTLS action failed: managed mTLS identity directory permissions are invalid",
        }
        code = "managed-mtls-permissions" if permissions else "managed-mtls-action-failed"
        self.first = Failure("prepare-mtls", code)
        super().__init__("Managed mTLS action could not complete: " + code)


class RouteHandoffFailure(DiagnosticFailure):
    """Closed guest categories; never remote exception messages or stderr."""

    CODES = frozenset(
        {
            "route-lock-deadline",
            "route-operation-pending",
            "route-authority-conflict",
            "route-owner-import-failed",
            "route-owner-integrity-failed",
            "route-handoff-unknown",
        }
    )

    def __init__(self, predicate: str):
        if predicate not in self.CODES:
            raise ValueError("unknown route handoff predicate")
        self.first = Failure("ordinary-route-handoff", predicate)
        super().__init__("Ordinary route handoff could not complete: " + predicate)


class PreparationBlocked(DiagnosticFailure):
    """Closed preparation predicates preserved through HA apply."""

    ACTIONS = {
        "desired-state-changed": "run apply --repair for a confirmed rebuild on the retained VMs",
        "artifact-changed": "rerun apply for artifact recovery, or use apply --repair",
        "checkpoint-binding-changed": "verify the configured VM identities and rerun apply",
        "approval-state-changed": "rerun apply to inspect and approve the fresh plan",
        "command-execution-changed": "rerun apply to reconcile retained work, or use apply --repair",
        "package-parent-changed": "rerun apply for package recovery, or use apply --repair",
        "durable-intent-changed": "rerun apply to inspect the actual durable operation",
    }

    def __init__(self, predicate: str, *, repair_command: str | None = None):
        if predicate not in self.ACTIONS:
            raise ValueError("unknown preparation predicate")
        self.first = Failure("prepare-transaction", predicate)
        self.next_action = self.ACTIONS[predicate]
        message = "VM-HA transaction preparation needs recovery: " + predicate + ". "
        if predicate == "desired-state-changed":
            message += "The changed desired settings cannot resume the saved activation. "
        message += self.next_action + "."
        if repair_command is not None:
            message += " Command: " + repair_command + ". Repair has no overall outage limit."
        super().__init__(message)


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


class GuestResponseUnavailable(ObservationUnavailable):
    """An ended guest response is not evidence of an operator interruption."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase, "guest-response-unavailable")
        self.first = Failure(phase, "guest-response-unavailable")


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
