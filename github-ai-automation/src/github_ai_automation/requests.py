"""Explicit PR admission, immutable authority ceilings and terminal requests."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

from .contracts import Blocked, Candidate, digest
from .rollout import pr_scope
from .state import acquire

FORK_FLAG = "AI_AUTOMATION_MERGE_FORK_CONTRIBUTION"
EPOCH = "AI_AUTOMATION_AUTOMATIC_SINCE"


def fork_merging(env=os.environ):
    value = env.get(FORK_FLAG, "") or "false"
    if value not in {"true", "false"}:
        raise Blocked("FORK_MERGE_POLICY_INVALID")
    return value == "true"


def capabilities(candidate, operation, env=os.environ):
    enabled = fork_merging(env)
    mode = env.get("AI_AUTOMATION_PR_REVIEW", "disabled")
    if operation not in {"review", "process"}:
        raise Blocked("REQUEST_OPERATION_INVALID")
    if mode not in {"report", "repair", "merge"}:
        return []
    result = ["assess", "comment", "label"]
    if operation == "process" and mode in {"repair", "merge"}:
        result.append("close")
        if not candidate.fork:
            result.append("repair")
        if not candidate.fork or enabled:
            result.append("adopt")
            result.append("qualify")
            if mode == "merge":
                result.append("merge")
    return result


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return int(parsed.timestamp())
    except (TypeError, ValueError, AttributeError):
        raise Blocked("ADMISSION_EPOCH_INVALID") from None


def active(store, candidate, request_id, *, now=None, operation=None):
    record, _ = store.read(candidate.key + "/operation")
    if (
        not re.fullmatch(r"[a-f0-9]{64}", request_id or "")
        or not record
        or record.get("schema_version") != 2
        or record["id"] != request_id
        or record["status"] != "active"
    ):
        raise Blocked("REQUEST_NOT_ACTIVE")
    if operation is not None and record["operation"] != operation:
        raise Blocked("REQUEST_OPERATION_CHANGED")
    if record["deadline"] <= (int(time.time()) if now is None else now):
        raise Blocked("OPERATION_EXPIRED")
    prior = Candidate.model_validate(record["candidate"])
    if (
        prior.repository,
        prior.repository_id,
        prior.pr_id,
        prior.head_repository,
        prior.head_repository_id,
        prior.head_ref,
    ) != (
        candidate.repository,
        candidate.repository_id,
        candidate.pr_id,
        candidate.head_repository,
        candidate.head_repository_id,
        candidate.head_ref,
    ):
        raise Blocked("REQUEST_SOURCE_CHANGED")
    if "rollout_generation" not in record:
        raise Blocked("ROLLOUT_REQUEST_NOT_ADMITTED")
    pr_scope(store, candidate, generation=record["rollout_generation"], now=now)
    return record


def require_capability(store, candidate, request_id, capability, *, env=os.environ):
    record = active(store, candidate, request_id)
    if record.get("adoption_child") and capability not in {"comment", "label", "close"}:
        raise Blocked("ADOPTION_EXECUTION_TRANSFERRED")
    if record.get("adoption_parent"):
        binding = record["adoption_parent"]
        parent = Candidate.model_validate(binding["candidate"])
        owner, _ = store.read(parent.key + "/operation")
        if (
            not owner
            or owner["id"] != binding["request_id"]
            or owner.get("adoption_child") != binding
            or owner["deadline"] != record["deadline"]
            or owner["rollout_generation"] != record["rollout_generation"]
        ):
            raise Blocked("ADOPTION_AUTHORITY_CHANGED")
        parent_capability = "adopt" if capability == "repair" else capability
        if parent_capability not in set(owner["capabilities"]) & set(
            capabilities(parent, owner["operation"], env)
        ):
            raise Blocked("ADOPTION_CAPABILITY_DENIED")
    allowed = set(record["capabilities"]) & set(capabilities(candidate, record["operation"], env))
    if capability not in allowed:
        raise Blocked("REQUEST_CAPABILITY_DENIED")
    pr_scope(store, candidate, generation=record["rollout_generation"], capability=capability)
    if capability == "repair" and candidate.fork:
        raise Blocked("FORK_CONTRIBUTOR_CHANGES_REQUIRED")
    return record


def unsettled(store, candidate):
    """Historical uncertain writes survive the request-schema transition."""
    for key in store.scan(candidate.key):
        value, _ = store.read(key)
        if value and value.get("status") == "pending" and "intent" in value:
            return True
    return False


def register(api, store, candidate, pr, operation, origin, env, *, draft=False, now=None):
    now = int(time.time()) if now is None else now
    prior, _ = store.read(candidate.key + "/operation")
    if candidate.head_ref.startswith("ai-adoption/") or (prior and prior.get("adoption_child")):
        if origin != "manual":
            return None
        from .adoption import renew

        return renew(api, store, candidate, operation, env, now=now)
    generation = pr_scope(store, candidate, automatic=origin == "opened", now=now)
    request_id = digest(
        {
            "repository_id": candidate.repository_id,
            "pr_id": candidate.pr_id,
            "origin": origin,
            "run_id": int(env["GITHUB_RUN_ID"]),
        }
    )
    lease = acquire(store, candidate.key + "/lock")
    try:
        current, version = store.read(candidate.key + "/operation")
        history, _ = store.read(candidate.key + "/requests/" + request_id)
        if env.get("GITHUB_RUN_ATTEMPT", "1") != "1" and (
            not current or current.get("id") != request_id
        ):
            return None
        if history:
            return None  # An original dispatch rerun never starts a successor.
        if origin == "opened" and current:
            return (
                current
                if current.get("id") == request_id and current.get("status") == "active"
                else None
            )
        if current and current.get("schema_version") == 2:
            if current["id"] == request_id:
                return current if current["status"] == "active" else None
            if current["status"] == "active" and current["deadline"] > now:
                try:
                    active(store, candidate, current["id"], now=now)
                except Blocked as exc:
                    if origin != "manual" or not exc.code.startswith("ROLLOUT_"):
                        raise
                    # A fresh human admission may replace an invalidated canary,
                    # only after the existing effect reconciliation below.
                else:
                    if current["operation"] != operation:
                        raise Blocked("REQUEST_OPERATION_CHANGED")
                    return current
            if current["status"] == "awaiting_ready" and origin != "manual":
                return None
        elif current:
            raise Blocked("LEGACY_OPERATION_RECONCILIATION_REQUIRED")
        reconcile_effects(api, store, lease, candidate)
        if current:
            history_key = candidate.key + "/requests/" + current["id"]
            previous, _ = store.read(history_key)
            if previous is None:
                store.put(history_key, current, None)
            elif previous != current:
                raise Blocked("REQUEST_HISTORY_CHANGED")
        record = {
            "schema_version": 2,
            "rollout_generation": generation,
            "id": request_id,
            "origin": origin,
            "operation": operation,
            "capabilities": capabilities(candidate, operation, env),
            "candidate": candidate.model_dump(),
            "head": candidate.head_sha,
            "base": candidate.base_sha,
            "owned_heads": [],
            "attempts": 0,
            "deadline": 0 if draft else now + 10800,
            "status": "awaiting_ready" if draft else "active",
            "stage": "usefulness",
            "epoch": env.get(EPOCH, "") if origin == "opened" else "",
            "outcome": None,
        }
        if current and current.get("live_failure"):
            record["live_failure"] = current["live_failure"]
        lease.assert_owned()
        store.put(candidate.key + "/operation", record, version)
        return None if draft else record
    finally:
        lease.release()


def admit_manual(api, store, candidate, pr, operation, event, env=os.environ):
    actor = event.get("sender", {})
    if (
        actor.get("type") != "User"
        or str(actor.get("id")) != env.get("GITHUB_ACTOR_ID")
        or api.permission(actor.get("login", "")) not in {"write", "maintain", "admin"}
    ):
        raise Blocked("MANUAL_REQUEST_UNAUTHORIZED")
    if pr["state"] != "open" or pr["draft"]:
        raise Blocked("PR_NOT_READY")
    return register(api, store, candidate, pr, operation, "manual", env)


def intake(api, store, event, env=os.environ):
    repo = api.request("")
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or env.get("GITHUB_EVENT_NAME") != "pull_request_target"
        or env.get("GITHUB_REF") != "refs/heads/" + repo["default_branch"]
        or env.get("GITHUB_REPOSITORY") != api.repository
        or env.get("AI_AUTOMATION_REPOSITORY_ID") != str(repo["id"])
    ):
        raise Blocked("TRUSTED_RUNTIME_REQUIRED")
    if env.get("AI_AUTOMATION_PR_REVIEW", "disabled") == "disabled":
        return None
    candidate, pr = api.candidate(event["number"])
    if event["pull_request"]["id"] != candidate.pr_id or pr["state"] != "open":
        return None
    if candidate.head_ref.startswith("ai-adoption/"):
        return None
    action = event["action"]
    if action == "opened":
        epoch = timestamp(env.get(EPOCH, ""))
        if timestamp(pr["created_at"]) < epoch or epoch > int(time.time()):
            return None
        current, _ = store.read(candidate.key + "/operation")
        if env.get("GITHUB_RUN_ATTEMPT") != "1" and not current:
            return None
        # One CAS operation is the enrollment and request. A crash cannot leave
        # a permanent enrollment with no recoverable work.
        return register(api, store, candidate, pr, "process", "opened", env, draft=pr["draft"])
    record, _ = store.read(candidate.key + "/operation")
    if not record or record.get("schema_version") != 2:
        return None
    if record["status"] == "awaiting_ready" and action == "ready_for_review" and not pr["draft"]:
        if record["epoch"] != env.get(EPOCH):
            return None
        lease = acquire(store, candidate.key + "/lock")
        try:
            record, version = store.read(candidate.key + "/operation")
            if record["status"] != "awaiting_ready":
                return None
            record.update(
                status="active",
                deadline=int(time.time()) + 10800,
                candidate=candidate.model_dump(),
                head=candidate.head_sha,
                base=candidate.base_sha,
            )
            # Restrictions may have tightened while the PR was a draft.
            record["capabilities"] = sorted(
                set(record["capabilities"]) & set(capabilities(candidate, "process", env))
            )
            store.put(candidate.key + "/operation", record, version)
        finally:
            lease.release()
    if record["status"] != "active" or pr["draft"]:
        return None
    return active(store, candidate, record["id"])


def finish(store, lease, candidate, request_id, outcome):
    record, version = store.read(candidate.key + "/operation")
    if not record or record.get("schema_version") != 2 or record["id"] != request_id:
        raise Blocked("REQUEST_NOT_ACTIVE")
    if record["status"] == "terminal" and outcome not in {
        "MERGED",
        "CLOSED",
        "ADOPTION_COMPLETE",
        "EXTERNALLY_CLOSED",
    }:
        return
    from .lifecycle import record_completion

    record_completion(store, lease, candidate, record, outcome)
    lease.assert_owned()
    record.update(status="terminal", stage="complete", outcome=outcome)
    store.put(candidate.key + "/operation", record, version)


def advance(store, lease, candidate, request_id, stage):
    record = active(store, candidate, request_id)
    _, version = store.read(candidate.key + "/operation")
    lease.assert_owned()
    record.update(stage=stage, candidate=candidate.model_dump())
    store.put(candidate.key + "/operation", record, version)


def dispatch(api, store, record):
    from . import scheduler

    candidate, pr = api.candidate(record["candidate"]["pr_number"])
    current = require_capability(store, candidate, record["id"], "assess")
    if pr["state"] != "open" or pr["draft"]:
        return
    owner = current.get("active_execution")
    if owner:
        run = api.request(f"actions/runs/{owner['run_id']}/attempts/{owner['attempt']}")
        if run["status"] != "completed":
            return
    scheduler.reconcile(api, store, candidate.repository_id)
    scheduler.enqueue(
        store, {**current, "candidate": candidate.model_dump()}, retry_completed=bool(owner)
    )
    scheduler.pump(api, store, candidate.repository_id)


def retire_legacy(api, store, candidate, event, env=os.environ):
    """Explicit one-time retirement, after old writers and effects are reconciled."""
    if env.get("AI_AUTOMATION_PR_REVIEW", "disabled") != "disabled":
        raise Blocked("LEGACY_RETIREMENT_REQUIRES_DISABLED_MODE")
    actor = event.get("sender", {})
    if (
        actor.get("type") != "User"
        or str(actor.get("id")) != env.get("GITHUB_ACTOR_ID")
        or api.permission(actor.get("login", "")) not in {"write", "maintain", "admin"}
    ):
        raise Blocked("LEGACY_RETIREMENT_UNAUTHORIZED")
    writers = {
        "github-ai-automation.yml",
        "github-ai-continuation.yml",
        "github-ai-recovery.yml",
        "skills-merge-pr.yml",
    }
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        for run in api.pages("actions/runs?status=" + status, field="workflow_runs"):
            if str(run["id"]) != env["GITHUB_RUN_ID"] and run["path"].split("/")[-1] in writers:
                raise Blocked("LEGACY_WRITERS_NOT_QUIESCENT")
    lease = acquire(store, candidate.key + "/lock")
    try:
        current, version = store.read(candidate.key + "/operation")
        if not current or current.get("schema_version") == 2:
            return
        from .live import require_cleanup

        require_cleanup(store, candidate)
        if unsettled(store, candidate):
            raise Blocked("PR_EFFECT_RECONCILIATION_REQUIRED")
        identity = digest(current)
        history_key = candidate.key + "/legacy/" + identity
        previous, _ = store.read(history_key)
        if not previous:
            store.put(history_key, current, None)
        lease.assert_owned()
        store.put(
            candidate.key + "/operation",
            {
                "schema_version": 2,
                "id": identity,
                "status": "terminal",
                "origin": "retired_legacy",
                "operation": "review",
                "candidate": candidate.model_dump(),
                "capabilities": [],
                "deadline": 0,
                "stage": "complete",
                "outcome": "LEGACY_MANUAL_ONLY",
            },
            version,
        )
    finally:
        lease.release()


def reconcile_effects(api, store, lease, candidate):
    """Observe exact known writes before allowing another candidate or request."""
    for key in store.scan(candidate.key):
        row, revision = store.read(key)
        if not row or row.get("status") != "pending" or "intent" not in row:
            continue
        intent = row["intent"]
        observed = None
        current, pr = api.candidate(candidate.pr_number)
        if key.endswith("/push") and current.head_sha == intent.get("after"):
            observed = {"head": current.head_sha}
        elif "/base-sync/" in key and intent.get("candidate"):
            before = Candidate.model_validate(intent["candidate"])
            commit = api.request(f"git/commits/{current.head_sha}")
            if {p["sha"] for p in commit["parents"]} == {before.head_sha, before.base_sha}:
                observed = {"head": current.head_sha}
        elif "/merge/" in key and intent.get("candidate"):
            before = Candidate.model_validate(intent["candidate"])
            if pr.get("merged") and current.head_sha == before.head_sha:
                observed = {"outcome": "merged", "result_sha": pr["merge_commit_sha"]}
        if observed is None:
            raise Blocked("PR_EFFECT_RECONCILIATION_REQUIRED")
        lease.assert_owned()
        store.put(key, {**row, "status": "observed", "result": observed}, revision)
