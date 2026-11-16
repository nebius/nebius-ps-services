"""Bounded observational timing. Never scheduling or acceptance authority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import re

from workflow_state import WorkflowError, lock, read, write

SCHEMA = "workflow/timing-v2"
STAGES = {"readiness", "launch", "admission", "validation", "integration", "cleanup", "repair", "preparation", "tools", "coordination_wait"}
_scope = ContextVar("workflow_timing_scope", default=None)
COUNTS = {"count", "failed", "total_ms", "max_ms", "unknown"}
COVERAGE = "Observed owner stages, Git/tool calls and lock waits only; unobserved native-tool intervals remain unknown. Preflight timings live in verifier receipts."


def _clock():
    try:
        from workflow_statistics import safe_clock
        return safe_clock()
    except Exception:
        return None


def _valid_clock(stamp):
    return stamp is None or (isinstance(stamp, dict)
        and set(stamp) == {"utc", "monotonic_ns", "clock_id", "scope"}
        and isinstance(stamp["utc"], str)
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})", stamp["utc"]) is not None
        and type(stamp["monotonic_ns"]) is int and stamp["monotonic_ns"] >= 0
        and re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}", str(stamp["clock_id"])) is not None
        and isinstance(stamp["scope"], str)
        and stamp["scope"] in {"machine-boot", "process-only"})


def _row():
    return {**dict.fromkeys(COUNTS, 0), "first_clock": None, "last_clock": None}


def _load(path: Path) -> dict:
    value = read(path) if path.exists() else {"schema": SCHEMA, "stages": {}}
    if (not isinstance(value, dict) or set(value) != {"schema", "stages"} or value.get("schema") != SCHEMA
            or not isinstance(value.get("stages"), dict) or not set(value["stages"]) <= STAGES):
        raise WorkflowError("TIMING_INVALID", "timing schema changed")
    for row in value["stages"].values():
        if (not isinstance(row, dict) or set(row) != COUNTS | {"first_clock", "last_clock"}
                or any(type(row[key]) is not int or row[key] < 0 for key in COUNTS)
                or row["unknown"] > row["count"] or row["failed"] > row["count"]
                or not all(_valid_clock(row[key]) for key in ("first_clock", "last_clock"))):
            raise WorkflowError("TIMING_INVALID", "timing counters changed")
    return value


def flush():
    current = _scope.get()
    if current is None or not current[1]:
        return
    run_dir, pending = current
    try:
        with lock(run_dir.parent, "timing.lock"):
            value = _load(run_dir / "workflow-timing.json")
            for stage, sample in pending.items():
                row = value["stages"].setdefault(stage, _row())
                for key in COUNTS - {"max_ms"}:
                    row[key] += sample[key]
                row["max_ms"] = max(row["max_ms"], sample["max_ms"])
                row["first_clock"] = row["first_clock"] or sample["first_clock"]
                row["last_clock"] = sample["last_clock"]
            write(run_dir / "workflow-timing.json", value)
    except Exception:
        pass  # Telemetry must never replace a product result or exception.
    finally:
        pending.clear()


@contextmanager
def scope(run_dir):
    """Batch bounded counters in memory, with one write per owner transition."""
    current = _scope.get()
    if current is not None and current[0] == Path(run_dir):
        yield
        return
    token = _scope.set((Path(run_dir), {}))
    try:
        yield
    finally:
        flush()
        _scope.reset(token)


@contextmanager
def observed(stage):
    if stage not in STAGES:
        raise ValueError("unsupported timing stage")
    sample = {"failed": False}
    if _scope.get() is None:
        yield sample
        return
    started = _clock()
    try:
        yield sample
    except BaseException:
        sample["failed"] = True
        raise
    finally:
        try:
            from workflow_statistics_report import elapsed
            ended = _clock()
            duration = elapsed(started, ended)
            row = _scope.get()[1].setdefault(stage, _row())
            row["count"] += 1
            row["failed"] += bool(sample["failed"])
            if duration is None:
                row["unknown"] += 1
            else:
                milliseconds = duration // 1_000_000
                row["total_ms"] += milliseconds
                row["max_ms"] = max(row["max_ms"], milliseconds)
            row["first_clock"] = row["first_clock"] or started
            row["last_clock"] = ended
        except Exception:
            pass


@contextmanager
def measure(run_dir: Path, stage: str):
    with scope(run_dir), observed(stage) as sample:
        yield sample


def summary(run_dir: Path, coordinator: dict, workers: list[dict]) -> dict:
    from workflow_capacity import effective
    path = run_dir / "workflow-timing.json"
    try:
        value = _load(path)
    except (OSError, WorkflowError):
        value = {"schema": SCHEMA, "stages": {}, "status": "unavailable"}
    active = sum(row["status"] in {"assigned", "running"} for row in workers)
    capacity = effective(run_dir, coordinator["capacity"])
    reasons = []
    if coordinator.get("cleanup_retained"):
        reasons.append("cleanup_pending")
    if capacity < coordinator["capacity"]:
        reasons.append("shared_or_native_capacity")
    if active < capacity:
        if coordinator.get("active_wave") is None:
            reasons.append("serial_wave_barrier")
        elif any(row["status"] == "done" for row in workers):
            reasons.append("result_admission_or_refill_pending")
        else:
            reasons.append("dependencies_or_write_claims")
    return {**value, "stage_durations_are_inclusive": True, "coverage": COVERAGE,
            "clock_provenance": "Each sample uses compatible monotonic clocks; first/last stamps bound observations, not a continuous interval.",
            "active_workers": active, "effective_capacity": capacity, "idle_reasons": reasons,
            "performance_improvement_qualified": False,
            "qualification": "Requires completed workflow duration and independently observed native overlap."}
