"""Project explicit owner evidence without deriving health from command exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from .apply_command import CommandJournal
from .apply_diagnostics import Failure, first_failure, latest_failure, unavailable_observation
from .apply_plan import Evidence, Outcome, Progress, Result


class OutputFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


@dataclass
class ApplyReport:
    dry_run: bool = False
    journal: CommandJournal | None = None
    resumed: bool = False
    started: bool = False
    planned: tuple[str, ...] = ()
    approval: str | None = None
    review_approval: str | None = None
    next_command: str | None = None
    prior_execution: str | None = None
    prior_pending: bool = False
    projection_failed: bool = False
    failure: Failure | None = None
    latest: Failure | None = None
    pending_reason: str | None = None
    result: Result = field(
        default_factory=lambda: Result(
            Outcome.BLOCKED, Evidence.UNKNOWN, Evidence.UNKNOWN, Evidence.UNKNOWN, Evidence.UNKNOWN
        )
    )

    def bind(self, journal: CommandJournal, *, resumed: bool) -> None:
        self.journal, self.resumed = journal, resumed
        snapshot = journal.snapshot()
        transaction = snapshot["transaction"] if snapshot else None
        self.prior_execution = transaction["execution_id"] if transaction else None
        self.prior_pending = bool(transaction and transaction["phase"] == "pending")
        if resumed and self.prior_pending and self.prior_execution:
            try:
                self.failure = journal.failure_for(self.prior_execution)
                self.latest = journal.failure_for(self.prior_execution, latest=True)
            except (OSError, RuntimeError, ValueError, TypeError):
                self.projection_failed = True

    def plan(self, effects: tuple[str, ...], *, approval: str | None = None) -> None:
        self.planned, self.approval = effects, approval

    def verified(
        self,
        *,
        changed: bool,
        configuration: Evidence = Evidence.READY,
        local_runtime: Evidence = Evidence.READY,
        authority: Evidence = Evidence.READY,
        connectivity: Evidence = Evidence.UNKNOWN,
        inhibition_retained: bool | None = False,
    ) -> None:
        """Called at an existing owner's verified terminal boundary, never on exit alone."""
        if self.dry_run:
            if changed:
                return
            # No-op inspection can prove parity without executing any effect.
        outcome = (
            Outcome.UNCHANGED
            if not changed
            else Outcome.RESUMED
            if self.resumed
            else Outcome.APPLIED
        )
        self.result = Result(
            outcome,
            configuration,
            local_runtime,
            authority,
            connectivity,
            inhibition_retained=inhibition_retained,
        )
        self.pending_reason = None

    def failed(self, error: BaseException | None = None) -> None:
        self.failure = self.failure or first_failure(error)
        self.latest = latest_failure(error) or Failure("apply", "operation-failed")
        unavailable = unavailable_observation(error)
        pending = unavailable is not None and getattr(unavailable, "retryable", True)
        self.pending_reason = "waiting-for-authority" if pending else None
        if self.journal is not None and self.started:
            try:
                if self.failure is not None:
                    self.journal.record_failure(self.failure.mapping())
                self.journal.record_failure(self.latest.mapping(), latest=True)
            except (OSError, RuntimeError, ValueError):
                self.projection_failed = True
        # Failure invalidates terminal readiness. A retained journal only proves
        # progress; it cannot prove a remote lock remains held after a disconnect.
        self.result = Result(
            Outcome.PENDING if pending else Outcome.FAILED if self.started else Outcome.BLOCKED,
            Evidence.UNKNOWN,
            Evidence.UNKNOWN,
            Evidence.UNKNOWN,
            Evidence.UNKNOWN,
        )

    def mapping(self) -> dict:
        terminal = self.result.outcome in {Outcome.APPLIED, Outcome.RESUMED, Outcome.UNCHANGED}
        completed = self.planned if terminal and not self.dry_run else ()
        pending = () if terminal else self.planned
        if self.journal is not None:
            try:
                snapshot = self.journal.snapshot()
            except (OSError, RuntimeError, ValueError):
                self.projection_failed = True
                self.failed()
                return {
                    **replace(
                        self.result, pending=self.planned, next_command=self.next_command
                    ).mapping(),
                    "dry_run": self.dry_run,
                    "approval": self.review_approval or self.approval,
                    **({"failure": self.failure.mapping()} if self.failure is not None else {}),
                    **(
                        {"latest_failure": self.latest.mapping()} if self.latest is not None else {}
                    ),
                }
            transaction = snapshot["transaction"] if snapshot else None
            # A historical completed command is not evidence for this invocation.
            if transaction and (
                self.started
                and transaction["execution_id"] != self.prior_execution
                or self.resumed
                and self.prior_pending
                and transaction["execution_id"] == self.prior_execution
            ):
                progress = transaction["progress"]
                completed = tuple(k for k, v in progress.items() if v == Progress.VERIFIED.value)
                pending = tuple(k for k, v in progress.items() if v != Progress.VERIFIED.value)
        result = replace(
            self.result,
            completed=completed,
            pending=pending,
            next_command=self.next_command if not terminal else None,
        )
        return {
            **result.mapping(),
            "dry_run": self.dry_run,
            "approval": self.review_approval or self.approval,
            **({"failure": self.failure.mapping()} if self.failure is not None else {}),
            **({"latest_failure": self.latest.mapping()} if self.latest is not None else {}),
            **({"pending_reason": self.pending_reason} if self.pending_reason is not None else {}),
        }
