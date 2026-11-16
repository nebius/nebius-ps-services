"""Two durable evaluator slots; unknown or draining executions retain capacity."""

from __future__ import annotations

import time
import uuid

from .contracts import Blocked, Candidate, digest
from .state import acquire

WORKFLOW = "github-ai-automation.yml"


def prefix(repository_id):
    return f"repositories/{repository_id}/scheduler"


def enqueue(store, record, *, retry_completed=False):
    candidate = Candidate.model_validate(record["candidate"])
    identity = digest(
        {
            "request": record["id"],
            "candidate": record["candidate"],
            "stage": record["stage"],
            "attempts": record["attempts"],
            "rollout_generation": record["rollout_generation"],
        }
    )
    path = prefix(candidate.repository_id) + "/queue/" + identity
    previous, revision = store.read(path)
    if previous and not (
        retry_completed
        and previous["status"] == "complete"
        and len(previous.get("attempts", [])) < 3
        and previous["deadline"] > time.time()
    ):
        return previous
    value = {
        "id": identity,
        "nonce": uuid.uuid4().hex,
        "request_id": record["id"],
        "candidate": record["candidate"],
        "operation": record["operation"],
        "deadline": record["deadline"],
        "created_at": time.time_ns(),
        "status": "queued",
    }
    if previous:
        value["attempts"] = previous.get("attempts", [])
    try:
        store.put(path, value, revision)
    except Blocked as exc:
        if exc.code != "STATE_CONFLICT":
            raise
        value, _ = store.read(path)
    return value


def reserve(store, repository_id, entry, *, run_id=None, attempt=None, dispatcher=None, now=None):
    now = int(time.time()) if now is None else now
    base = prefix(repository_id)
    lease = acquire(store, base + "/lock")
    try:
        state, revision = store.read(base + "/slots")
        state = state or {"schema_version": 1, "slots": [None, None]}
        if state.get("schema_version") != 1 or len(state.get("slots", [])) != 2:
            raise Blocked("SCHEDULER_STATE_INVALID")
        for slot in state["slots"]:
            if slot and slot["id"] == entry["id"]:
                return slot
        row, _ = store.read(base + "/queue/" + entry["id"])
        if not row or row["nonce"] != entry["nonce"] or row["status"] != "queued":
            raise Blocked("SCHEDULER_REQUEST_NOT_QUEUED")
        if row["deadline"] <= now:
            raise Blocked("OPERATION_EXPIRED")
        if None not in state["slots"]:
            return None
        index = state["slots"].index(None)
        slot = {
            **row,
            "slot": index,
            "status": "starting",
            "run_id": run_id,
            "attempt": attempt,
            "dispatcher": dispatcher,
            "heartbeat_until": now + 120,
        }
        state["slots"][index] = slot
        lease.assert_owned()
        store.put(base + "/slots", state, revision)
        return slot
    finally:
        lease.release()


def bind(store, repository_id, nonce, run_id, attempt, *, now=None, observed_only=False):
    """Bind a dispatched nonce once; reruns cannot acquire the previous attempt."""
    base = prefix(repository_id)
    lease = acquire(store, base + "/lock")
    try:
        state, revision = store.read(base + "/slots")
        matches = [s for s in (state or {}).get("slots", []) if s and s["nonce"] == nonce]
        if len(matches) != 1:
            raise Blocked("EVALUATION_NOT_RESERVED")
        slot = matches[0]
        if slot["run_id"] not in {None, run_id} or slot["attempt"] not in {None, attempt}:
            raise Blocked("EVALUATION_RUN_CHANGED")
        if slot["status"] == "draining":
            raise Blocked("EVALUATION_DRAINING")
        now = int(time.time()) if now is None else now
        if slot["deadline"] <= now and not observed_only:
            raise Blocked("OPERATION_EXPIRED")
        slot.update(run_id=run_id, attempt=attempt)
        if not observed_only:
            slot.update(status="running", heartbeat_until=now + 120)
        lease.assert_owned()
        store.put(base + "/slots", state, revision)
        return slot
    finally:
        lease.release()


def terminal(store, repository_id, nonce, run_id, attempt, *, retry=False):
    """Caller must first verify the exact evaluator job/run is terminal through GitHub."""
    base = prefix(repository_id)
    lease = acquire(store, base + "/lock")
    try:
        state, revision = store.read(base + "/slots")
        matches = [s for s in (state or {}).get("slots", []) if s and s["nonce"] == nonce]
        if not matches:
            return
        slot = matches[0]
        if (slot["run_id"], slot["attempt"]) != (run_id, attempt):
            raise Blocked("EVALUATION_RUN_CHANGED")
        path = base + "/queue/" + slot["id"]
        row, version = store.read(path)
        if not row:
            raise Blocked("SCHEDULER_STATE_INVALID")
        lease.assert_owned()
        # Marking complete first is safe after proven termination, even if the
        # following slot release is interrupted and recovery must repeat it.
        history = row.get("attempts", [])
        observed = {"nonce": nonce, "run_id": run_id, "attempt": attempt}
        if observed not in history:
            history = [*history, observed]
        retry = retry and len(history) < 3 and row["deadline"] > time.time()
        store.put(
            path,
            {
                **row,
                "status": "queued" if retry else "complete",
                "nonce": uuid.uuid4().hex if retry else nonce,
                "attempts": history,
                "run_id": run_id,
                "attempt": attempt,
            },
            version,
        )
        state["slots"][slot["slot"]] = None
        store.put(base + "/slots", state, revision)
    finally:
        lease.release()


def pump(api, store, repository_id):
    from .requests import require_capability

    base = prefix(repository_id)
    pending = []
    for path in store.scan(base + "/queue"):
        row, _ = store.read(path)
        if row and row["status"] == "queued":
            pending.append(row)
    for row in sorted(pending, key=lambda v: (v["created_at"], v["id"])):
        try:
            candidate, pr = api.candidate(row["candidate"]["pr_number"])
            if candidate.model_dump() != row["candidate"] or pr["state"] != "open" or pr["draft"]:
                retire_queued(store, repository_id, row)
                continue
            require_capability(store, candidate, row["request_id"], "assess")
        except Blocked as exc:
            if exc.code in {"PR_SOURCE_UNAVAILABLE", "PR_TARGET_UNSUPPORTED", "OPERATION_EXPIRED"}:
                retire_queued(store, repository_id, row)
            continue
        prior, _ = store.read(base + "/slots")
        if any(s and s["id"] == row["id"] for s in (prior or {}).get("slots", [])):
            continue  # A submitted/uncertain dispatch is never submitted again.
        dispatcher = uuid.uuid4().hex
        slot = reserve(store, repository_id, row, dispatcher=dispatcher)
        if slot is None:
            return
        if slot["dispatcher"] != dispatcher:
            continue
        try:
            api.dispatch(
                WORKFLOW,
                {
                    "pr_number": str(candidate.pr_number),
                    "operation": row["operation"],
                    "request_id": row["request_id"],
                    "dispatch_nonce": row["nonce"],
                },
            )
        except Blocked as exc:
            if exc.code == "GITHUB_WRITE_NOT_SUBMITTED":
                not_submitted(store, repository_id, slot)
            raise


def retire_queued(store, repository_id, expected):
    """Retire only unreserved work. Never infer that an unknown execution stopped."""
    base = prefix(repository_id)
    lease = acquire(store, base + "/lock")
    try:
        state, _ = store.read(base + "/slots")
        if any(s and s["id"] == expected["id"] for s in (state or {}).get("slots", [])):
            return
        path = base + "/queue/" + expected["id"]
        row, revision = store.read(path)
        if row != expected or row["status"] != "queued":
            return
        lease.assert_owned()
        store.put(path, {**row, "status": "complete"}, revision)
    finally:
        lease.release()


def not_submitted(store, repository_id, rejected):
    """Only an authoritative rejection before submission permits this transition."""
    base = prefix(repository_id)
    lease = acquire(store, base + "/lock")
    try:
        state, revision = store.read(base + "/slots")
        current = state["slots"][rejected["slot"]]
        if current != rejected or current["run_id"] is not None:
            raise Blocked("DISPATCH_RECONCILIATION_REQUIRED")
        path = base + "/queue/" + current["id"]
        row, etag = store.read(path)
        failures = row.get("dispatch_rejections", 0) + 1
        lease.assert_owned()
        store.put(
            path,
            {
                **row,
                "dispatch_rejections": failures,
                "status": "queued" if failures < 3 else "complete",
            },
            etag,
        )
        state["slots"][current["slot"]] = None
        store.put(base + "/slots", state, revision)
    finally:
        lease.release()


def reconcile(api, store, repository_id, *, now=None):
    """Observe/cancel exact executions. No elapsed-time-only slot reclamation."""
    now = int(time.time()) if now is None else now
    base = prefix(repository_id)
    state, _ = store.read(base + "/slots")
    if not state or not any(state["slots"]):
        return
    repo = api.request("")
    for slot in (state or {}).get("slots", []):
        if not slot:
            continue
        run_id, attempt = slot["run_id"], slot["attempt"]
        if run_id is None:
            matches = [
                r
                for r in api.pages(
                    f"actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch",
                    field="workflow_runs",
                )
                if r["display_title"] == "ai-evaluation-" + slot["nonce"]
            ]
            if not matches:
                continue
            if len(matches) != 1:
                raise Blocked("EVALUATION_DISPATCH_AMBIGUOUS")
            run_id, attempt = matches[0]["id"], matches[0]["run_attempt"]
        run = api.request(f"actions/runs/{run_id}/attempts/{attempt}")
        if (
            run["repository"]["id"] != repository_id
            or run["event"] != "workflow_dispatch"
            or run["path"] != ".github/workflows/" + WORKFLOW
            or run["head_branch"] != repo["default_branch"]
            or run["run_attempt"] != attempt
        ):
            raise Blocked("EVALUATION_RUN_UNTRUSTED")
        if slot["run_id"] is None:
            bind(store, repository_id, slot["nonce"], run_id, attempt, now=now, observed_only=True)
        if run["status"] == "completed":
            execution, _ = store.read(f"repositories/{repository_id}/executions/{run_id}_{attempt}")
            terminal(
                store,
                repository_id,
                slot["nonce"],
                run_id,
                attempt,
                retry=not execution or execution.get("status") != "accepted",
            )
        elif slot["heartbeat_until"] <= now or slot["deadline"] <= now:
            api.request(f"actions/runs/{run_id}/cancel", method="POST")
            # Cancellation is a request, not proof. Reservation remains held.
