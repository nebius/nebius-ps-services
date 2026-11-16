"""Trusted capability boundary for generated scenarios; never import model code."""

from __future__ import annotations

import hashlib
import importlib.util
import signal
import time
from contextlib import contextmanager
from pathlib import Path

from .contracts import Blocked, LiveResult, Scenario, digest


@contextmanager
def deadline(seconds):
    """Hosted Linux main-thread deadline; drivers still own accepted remote writes."""

    def expire(*_):
        raise Blocked("LIVE_DEADLINE_EXCEEDED")

    prior = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, max(0.01, seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)


def driver_for(entry, trusted_root, context, store, lease, run_key, expected_hash=None):
    path = trusted_root / entry["driver"]
    if path.is_symlink() or not path.resolve().is_relative_to(trusted_root.resolve()):
        raise Blocked("LIVE_DRIVER_UNTRUSTED")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != entry["sha256"] or (expected_hash and actual != expected_hash):
        raise Blocked("LIVE_DRIVER_CHANGED")
    spec = importlib.util.spec_from_file_location("trusted_live_capability", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Driver(context=context, store=store, lease=lease, run_key=run_key)


def recover_cleanup(*, candidate, policy, trusted_root, context, lease, store):
    key = candidate.key + "/cleanup"
    pending, _ = store.read(key)
    for run_key in (pending or {}).get("runs", []):
        record, _ = store.read(run_key)
        if not record or not run_key.startswith(candidate.key + "/live/"):
            raise Blocked("CLEANUP_IDENTITY_INVALID")
        plan = record["plan"]
        entry = policy["live_capabilities"].get(plan["scenario"]["capability"])
        if not entry:
            raise Blocked("CLEANUP_DRIVER_UNAVAILABLE")
        with deadline(1800):
            driver = driver_for(
                entry, trusted_root, context, store, lease, run_key, plan["driver_sha256"]
            )
            driver.reconcile()
            driver.cleanup()
            if driver.absent() is not True:
                raise Blocked("CLEANUP_PENDING")
        lease.assert_owned()
        current, version = store.read(run_key)
        store.put(run_key, {**current, "status": "recovered_cleanup"}, version)
        pending, version = store.read(key)
        pending["runs"] = [v for v in pending["runs"] if v != run_key]
        store.put(key, pending, version)


def execute_live(
    *, scenario: Scenario, candidate, policy: dict, trusted_root: Path, context: dict, lease, store
) -> LiveResult:
    def unavailable(code):
        return LiveResult(
            outcome="unavailable",
            scenario_sha256=digest(scenario),
            source_tree=candidate.tree_sha,
            assertions={},
            cleanup_complete=True,
            reason=code,
        )

    capabilities = policy["live_capabilities"]
    entry = capabilities.get(scenario.capability)
    if not entry:
        return unavailable("LIVE_CAPABILITY_UNAVAILABLE")
    path = trusted_root / entry["driver"]
    if path.is_symlink() or not path.resolve().is_relative_to(trusted_root.resolve()):
        raise Blocked("LIVE_DRIVER_UNTRUSTED")
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"] or not entry.get(
        "qualified"
    ):
        return unavailable("LIVE_CAPABILITY_UNQUALIFIED")
    if not scenario.assertions or len({a.name for a in scenario.assertions}) != len(
        scenario.assertions
    ):
        raise Blocked("LIVE_ASSERTIONS_INVALID")
    lease.assert_owned()
    run_key = (
        candidate.key
        + "/live/"
        + digest(
            {
                "candidate": candidate.model_dump(),
                "scenario": scenario.model_dump(),
                "trial": context["trial"],
            }
        )
    )
    previous, etag = store.read(run_key)
    plan = {
        "candidate": candidate.model_dump(),
        "scenario": scenario.model_dump(),
        "driver_sha256": entry["sha256"],
    }
    if previous and previous["plan"] != plan:
        raise Blocked("LIVE_PLAN_DRIFT")
    if previous:
        if previous["status"] == "complete" and previous.get("result"):
            return LiveResult.model_validate(previous["result"])
        raise Blocked("LIVE_RECONCILIATION_REQUIRED")
    if not previous:
        store.put(run_key, {"plan": plan, "status": "started", "resources": []}, etag)
    cleanup_key = candidate.key + "/cleanup"
    pending, revision = store.read(cleanup_key)
    pending = pending or {"runs": []}
    pending["runs"] = sorted(set(pending["runs"] + [run_key]))
    store.put(cleanup_key, pending, revision)
    # Drivers are reviewed default-branch code. Their API must record intents
    # through store before writes and check lease before every provider action.
    driver = driver_for(entry, trusted_root, context, store, lease, run_key)
    outcome, reason, assertions, cleaned = "unavailable", "LIVE_EXECUTION_UNAVAILABLE", {}, False
    try:
        with deadline(min(policy["live_timeout_seconds"], context.get("remaining_seconds", 3600))):
            driver.reconcile()
            driver.preflight(plan)
            driver.provision(plan)
            result = driver.verify(plan)
        assertions = result["assertions"]
        if set(assertions) != {a.name for a in scenario.assertions} or any(
            type(v) is not bool for v in assertions.values()
        ):
            raise Blocked("LIVE_ASSERTION_EVIDENCE_INVALID")
        outcome = (
            "intervened"
            if result["intervened"]
            else "passed"
            if all(assertions.values())
            else "failed"
        )
        reason = "LIVE_" + outcome.upper()
    except Blocked as exc:
        reason = exc.code
    except Exception:
        reason = "LIVE_EXECUTION_UNAVAILABLE"
    finally:
        # Cleanup is an independently observed postcondition, including after failure.
        try:
            with deadline(1800):
                driver.reconcile()
                driver.cleanup()
                cleaned = driver.absent() is True
        except Exception:
            cleaned = False
    result = LiveResult(
        outcome=outcome,
        scenario_sha256=digest(scenario),
        source_tree=candidate.tree_sha,
        assertions=assertions,
        cleanup_complete=cleaned,
        reason=reason,
    )
    current, etag = store.read(run_key)
    lease.assert_owned()
    store.put(
        run_key,
        {
            **current,
            "status": "complete" if cleaned else "cleanup_pending",
            "result": result.model_dump(),
        },
        etag,
    )
    if cleaned:
        pending, revision = store.read(cleanup_key)
        pending["runs"] = [key for key in pending["runs"] if key != run_key]
        store.put(cleanup_key, pending, revision)
    return result


def require_cleanup(store, candidate):
    pending, _ = store.read(candidate.key + "/cleanup")
    if pending and pending["runs"]:
        raise Blocked("CLEANUP_PENDING")


def wait_for_quota(available, request=None, *, clock=time.monotonic, sleep=time.sleep):
    if available():
        return
    if request is None:
        raise Blocked("QUOTA_REQUEST_UNAVAILABLE")
    deadline = clock() + 120
    request()  # Only a previously qualified provider request adapter may supply this.
    while clock() < deadline:
        if available():
            return
        sleep(min(5, deadline - clock()))
    raise Blocked("QUOTA_NOT_AVAILABLE")
