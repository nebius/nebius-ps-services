"""Support-safe timing receipts: names only, never command arguments or output."""

from __future__ import annotations

import functools
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from .soperator_receipt_io import _write_owner_only_json, write_owner_only_json

_RECORDER: ContextVar[TimingRecorder | None] = ContextVar("deployment_timing", default=None)
_PARENT: ContextVar[str | None] = ContextVar("deployment_timing_parent", default=None)


def category_for(phase: str) -> str:
    if any(word in phase for word in ("bootstrap", "jail", "populate", "storage-barrier")):
        return "setup"
    if any(word in phase for word in ("terraform", "infrastructure")):
        return "infrastructure"
    if any(word in phase for word in ("active-check", "passive", "acceptance")):
        return "diagnostics"
    if any(word in phase for word in ("readiness", "smoke", "verify-", "wait-required")):
        return "readiness"
    return "orchestration"


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class TimingRecorder:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self.checkpoint_reports = True
        self.clock = clock
        self.started = clock()
        self.payload: dict[str, Any] = {
            "schema": "nebius-cxcli/deploy-timing/v1",
            "startedAt": _utc(),
            "status": "running",
            "phases": [],
            "freshTwoWorkerTargetSeconds": 900,
            "targetIsDeadline": False,
        }

    def save(self) -> None:
        self.payload["elapsedSeconds"] = round(self.clock() - self.started, 3)
        write = write_owner_only_json if self.checkpoint_reports else _write_owner_only_json
        write(self.path, self.payload)

    def finish(self, *, console: Console) -> None:
        self.payload["endedAt"] = _utc()
        elapsed = self.clock() - self.started
        self.payload["withinTargetDuration"] = elapsed <= 900
        totals: dict[str, float] = {}
        for row in self.payload["phases"]:
            category = row["category"]
            totals[category] = totals.get(category, 0) + row.get("exclusiveSeconds", 0)
        self.payload["exclusiveCategorySeconds"] = {
            category: round(duration, 3) for category, duration in sorted(totals.items())
        }
        self.save()
        console.print(
            f"Deployment elapsed: {elapsed / 60:.1f} minutes ({self.payload['status']}).",
            markup=False,
        )
        console.print(f"Deployment timing report: {self.path}", markup=False)

    @contextmanager
    def phase(self, name: str, category: str) -> Iterator[None]:
        rows = self.payload["phases"]
        parent = _PARENT.get()
        row = {
            "id": str(len(rows) + 1),
            "parent": parent,
            "name": name,
            "category": category,
            "startedAt": _utc(),
            "startedSeconds": self.clock() - self.started,
            "attempt": 1 + sum(item["name"] == name and item["parent"] == parent for item in rows),
            "outcome": "running",
        }
        rows.append(row)
        self.save()
        token = _PARENT.set(row["id"])
        failure: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            failure = exc
            row["outcome"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            raise
        else:
            row["outcome"] = "succeeded"
        finally:
            _PARENT.reset(token)
            row["endedAt"] = _utc()
            row["endedSeconds"] = self.clock() - self.started
            row["durationSeconds"] = round(row["endedSeconds"] - row["startedSeconds"], 3)
            # Child intervals can overlap; subtract their union, not their sum.
            spans = sorted(
                (r["startedSeconds"], r.get("endedSeconds", row["endedSeconds"]))
                for r in rows
                if r["parent"] == row["id"]
            )
            covered, end = 0.0, row["startedSeconds"]
            for start, stop in spans:
                covered += max(0.0, stop - max(start, end))
                end = max(end, stop)
            row["exclusiveSeconds"] = round(max(0, row["durationSeconds"] - covered), 3)
            try:
                self.save()
            except Exception:
                if failure is None:
                    raise
                failure.add_note("Deployment phase timing could not be published.")


def _fast_only_target(config: Mapping[str, Any], selected_targets: Sequence[str]) -> bool:
    from .deployment_plan import soperator_target
    from .soperator_deployment_profile import FAST_DEV_TEST, deployment_profile

    try:
        target = soperator_target(config)
        if target is None or tuple(selected_targets) != (target[0],):
            return False
        values = target[1].get("values", {})
        return isinstance(values, Mapping) and deployment_profile(values) == FAST_DEV_TEST
    except ValueError:
        # Reporting policy never grants admission to an invalid configuration.
        return False


@contextmanager
def selected_deployment_timing(
    config: Mapping[str, Any], selected_targets: Sequence[str]
) -> Iterator[None]:
    """Use local-only timing writes only for the admitted frozen fast target."""
    recorder = _RECORDER.get()
    if recorder is None:
        yield
        return
    previous = recorder.checkpoint_reports
    recorder.checkpoint_reports = not _fast_only_target(config, selected_targets)
    try:
        yield
    finally:
        recorder.checkpoint_reports = previous


@contextmanager
def timed_phase(name: str, *, category: str | None = None) -> Iterator[None]:
    recorder = _RECORDER.get()
    if recorder is None:
        yield
    else:
        with recorder.phase(name, category or category_for(name)):
            yield


def timed(name: str, *, category: str | None = None) -> Callable:
    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def run(*args: Any, **kwargs: Any) -> Any:
            with timed_phase(name, category=category):
                return function(*args, **kwargs)

        return run

    return decorate


def deployment_timing(function: Callable) -> Callable:
    @functools.wraps(function)
    def run(config: Any, paths: Any, *args: Any, **kwargs: Any) -> Any:
        # Root report lives beside the authored deployment, outside temporary execution.
        if _RECORDER.get() is not None or kwargs["options"].dry_run:
            return function(config, paths, *args, **kwargs)
        recorder = TimingRecorder(paths.reports_dir / f"deploy-timing-{uuid.uuid4().hex}.json")
        token = _RECORDER.set(recorder)
        failure: BaseException | None = None
        try:
            with recorder.phase("deploy", "orchestration"):
                result = function(config, paths, *args, **kwargs)
            recorder.payload["status"] = "succeeded"
            return result
        except BaseException as exc:
            failure = exc
            recorder.payload["status"] = (
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            )
            raise
        finally:
            _RECORDER.reset(token)
            try:
                recorder.finish(console=Console(stderr=True))
            except Exception:
                if failure is None:
                    raise
                failure.add_note("Deployment timing summary could not be published.")

    return run
