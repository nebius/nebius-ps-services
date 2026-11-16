"""PR status projection and narrow, resumable completion obligations."""

from __future__ import annotations

import os
import time
from urllib.parse import quote

from .contracts import Blocked, Candidate, LifecycleAuthority, digest
from .stages import public_text
from .state import acquire

LABELS = {
    "under-review": "Contribution assessment and review are in progress",
    "testing": "The reviewed candidate is undergoing validation",
    "being-fixed": "Authorized repair or feature adoption is in progress",
    "manual-merge": "Reviewed without known correctness blockers; maintainer merge needed",
    "blocked": "A named correctness, authority, cleanup or execution gate remains",
    "needs-information": "A material decision or evidence gap needs clarification",
    "closed": "Closure without merging has been verified",
    "merged": "GitHub confirms this pull request was merged",
}


def disposition(code):
    if code == "MERGED":
        return "merged"
    if code in {"CLOSED", "EXTERNALLY_CLOSED", "ADOPTION_COMPLETE"}:
        return "closed"
    if code in {"QUALIFIED_MERGE_DISABLED", "MANUAL_MERGE"}:
        return "manual-merge"
    if code in {"REVIEW_COMPLETE", "CONTRIBUTION_UNCONFIRMED", "LIVE_APPLICABILITY_UNCERTAIN"}:
        return "needs-information"
    if code in {"WAITING_FOR_CI", "CI_REQUIRED", "ASSESSMENT_READY", "MERGE_ELIGIBLE"}:
        return "testing"
    if code in {
        "BASE_SYNC_REQUESTED",
        "PATCH_PREPARED",
        "ADOPTION_PREPARED",
        "ADOPTION_SLICE_PREPARED",
        "LIVE_REMEDIATION_REQUIRED",
        "ADOPTION_WAITING",
    }:
        return "being-fixed"
    if code == "REVIEW_STARTED":
        return "under-review"
    return "blocked"


def label(api, number, status, *, guard=lambda: None):
    """Add before removing stale statuses; never replace unrelated labels."""
    if status not in LABELS:
        raise Blocked("DISPOSITION_INVALID")
    path = "labels/" + quote(status, safe="")
    if not api.request(path, absent=True):
        try:
            guard()
            api.request(
                "labels",
                method="POST",
                body={"name": status, "color": "5319e7", "description": LABELS[status]},
            )
        except Blocked:
            if not api.request(path, absent=True):
                raise
    names = {row["name"] for row in api.pages(f"issues/{number}/labels")}
    if status not in names:
        try:
            guard()
            api.request(f"issues/{number}/labels", method="POST", body={"labels": [status]})
        except Blocked:
            if status not in {row["name"] for row in api.pages(f"issues/{number}/labels")}:
                raise
    for stale in sorted((names & LABELS.keys()) - {status}):
        guard()
        api.request(f"issues/{number}/labels/" + quote(stale, safe=""), method="DELETE")
    observed = {row["name"] for row in api.pages(f"issues/{number}/labels")}
    if observed & LABELS.keys() != {status}:
        raise Blocked("DISPOSITION_NOT_VERIFIED")


def publish(api, candidate, code, detail="", *, expected_state="open", guard=lambda: None):
    pr = api.request(f"pulls/{candidate.pr_number}")
    if pr["id"] != candidate.pr_id or pr["base"]["repo"]["id"] != candidate.repository_id:
        raise Blocked("DISPOSITION_IDENTITY_CHANGED")
    if pr["state"] != expected_state or (
        expected_state == "open" and pr["head"]["sha"] != candidate.head_sha
    ):
        raise Blocked("PR_SNAPSHOT_CHANGED")
    status = "merged" if pr.get("merged") else disposition(code)
    if status in {"merged", "closed"} and pr["state"] != "closed":
        raise Blocked("DISPOSITION_STATE_MISMATCH")
    guard()
    label(api, candidate.pr_number, status, guard=guard)
    if status == "manual-merge":
        detail = "Accepted for manual merge. " + (
            detail or "Review passed; automatic merging is not authorized or available."
        )
    guard()
    api.comment(candidate.pr_number, code, detail=public_text(detail))
    after = api.request(f"pulls/{candidate.pr_number}")
    if (after["id"], after["state"], after["head"]["sha"], bool(after.get("merged"))) != (
        pr["id"],
        pr["state"],
        pr["head"]["sha"],
        bool(pr.get("merged")),
    ):
        raise Blocked("PR_SNAPSHOT_CHANGED")


def record_completion(store, lease, candidate, operation, outcome):
    if "label" not in operation.get("capabilities", []):
        return
    key = candidate.key + "/completions/" + operation["id"]
    prior, revision = store.read(key)
    if prior:
        if prior["candidate"] != candidate.model_dump():
            raise Blocked("COMPLETION_IDENTITY_CHANGED")
        if prior["outcome"] != outcome:
            if outcome not in {"MERGED", "CLOSED", "ADOPTION_COMPLETE", "EXTERNALLY_CLOSED"}:
                raise Blocked("COMPLETION_IDENTITY_CHANGED")
            lease.assert_owned()
            store.put(
                key,
                {
                    **prior,
                    "previous_outcome": prior["outcome"],
                    "outcome": outcome,
                    "status": "pending",
                },
                revision,
            )
        return
    lease.assert_owned()
    store.put(
        key,
        {
            "schema": "pr-completion/v1",
            "authority": LifecycleAuthority(
                origin=operation["origin"],
                operation=operation["operation"],
                request_id=operation["id"],
                rollout_generation=operation["rollout_generation"],
            ).model_dump(),
            "candidate": candidate.model_dump(),
            "request_id": operation["id"],
            "outcome": outcome,
            "operation": operation["operation"],
            "capabilities": operation["capabilities"],
            "rollout_generation": operation["rollout_generation"],
            "detail": operation.get("detail", ""),
            "status": "pending",
            "created_at": int(time.time()),
            "issues": operation.get("issue_evidence"),
            "adoption": operation.get("adoption_parent"),
            "observations": {},
        },
        revision,
    )


def reconcile(api, store, key, *, env=os.environ):
    value, _ = store.read(key)
    if not value or value.get("schema") != "pr-completion/v1":
        raise Blocked("COMPLETION_INVALID")
    candidate = Candidate.model_validate(value["candidate"])
    authority = LifecycleAuthority.model_validate(value["authority"])
    if (authority.request_id, authority.operation, authority.rollout_generation) != (
        value["request_id"],
        value["operation"],
        value["rollout_generation"],
    ):
        raise Blocked("COMPLETION_AUTHORITY_CHANGED")
    if key != candidate.key + "/completions/" + value["request_id"]:
        raise Blocked("COMPLETION_IDENTITY_CHANGED")
    if value["status"] in {"complete", "superseded", "needs_reassessment"}:
        return value
    if env.get("AI_AUTOMATION_PR_REVIEW", "disabled") == "disabled":
        raise Blocked("AUTOMATION_DISABLED")
    from .rollout import current

    if current(store, candidate.repository_id).generation != value["rollout_generation"]:
        raise Blocked("COMPLETION_POLICY_CHANGED")
    from .controller import Heartbeat

    lease = acquire(store, candidate.key + "/lock")
    heartbeat = Heartbeat(lease)
    heartbeat.__enter__()
    try:
        value, revision = store.read(key)
        pr = api.request(f"pulls/{candidate.pr_number}")
        if pr["id"] != candidate.pr_id or pr["base"]["repo"]["id"] != candidate.repository_id:
            raise Blocked("COMPLETION_IDENTITY_CHANGED")
        if value["status"] == "waiting_terminal" and pr["state"] == "open":
            return value
        terminal = value["outcome"] in {
            "MERGED",
            "CLOSED",
            "EXTERNALLY_CLOSED",
            "ADOPTION_COMPLETE",
        }
        if (terminal and pr["state"] != "closed") or pr["head"]["sha"] != candidate.head_sha:
            value.update(status="superseded", reason="PR_SNAPSHOT_CHANGED")
            store.put(key, value, revision)
            return value
        operation, _ = store.read(candidate.key + "/operation")
        if operation and operation["id"] != value["request_id"]:
            raise Blocked("COMPLETION_SUPERSEDED")

        def authorize(*capabilities):
            # Completion outlives execution, but never its retained authority,
            # current policy or the exact canary permit for the effect.
            from .requests import capabilities as current_capabilities
            from .rollout import pr_scope

            lease.assert_owned()
            operation, _ = store.read(candidate.key + "/operation")
            if not operation or operation["id"] != value["request_id"]:
                raise Blocked("COMPLETION_SUPERSEDED")
            if operation["operation"] != value["operation"]:
                raise Blocked("COMPLETION_AUTHORITY_CHANGED")
            allowed = (
                set(value["capabilities"])
                & set(operation["capabilities"])
                & set(current_capabilities(candidate, value["operation"], env))
            )
            for capability in capabilities:
                if capability not in allowed:
                    raise Blocked("COMPLETION_CAPABILITY_DENIED")
                pr_scope(
                    store, candidate, generation=value["rollout_generation"], capability=capability
                )
            lease.assert_owned()

        code = (
            "MERGED"
            if pr.get("merged")
            else "EXTERNALLY_CLOSED"
            if pr["state"] == "closed"
            else value["outcome"]
        )
        publish(
            api,
            candidate,
            code,
            value["detail"],
            expected_state=pr["state"],
            guard=lambda: authorize("label", "comment"),
        )
        value["observations"]["label"] = disposition(code)
        if value["issues"] and value["operation"] == "process":
            if env.get("AI_AUTOMATION_PR_REVIEW") not in {"repair", "merge"}:
                raise Blocked("COMPLETION_ISSUE_AUTHORITY_RESTRICTED")
            from .issue_resolution import complete_issues

            value["observations"]["issues"] = complete_issues(
                api, store, lease, key, candidate, pr, value["issues"], authorize=authorize
            )
        if value.get("adoption") and pr.get("merged"):
            from .adoption import merged_replacement

            merged_replacement(api, store, value["adoption"], candidate, pr)
        unresolved_completion = any(
            row.get("outcome") == "reassessment_required"
            for row in value["observations"].get("issues", [])
        )
        value.update(
            status="needs_reassessment"
            if unresolved_completion
            else "complete"
            if pr["state"] == "closed"
            else "waiting_terminal",
            observation_sha256=digest(value["observations"]),
        )
        lease.assert_owned()
        store.put(key, value, revision)
        return value
    finally:
        heartbeat.__exit__(None, None, None)
        lease.release()


def reconcile_all(api, store, *, dispatch=False):
    repo = api.request("")
    count = 0
    for key in store.scan(f"repositories/{repo['id']}/prs"):
        if "/completions/" not in key or "/issues/" in key:
            continue
        value, _ = store.read(key)
        if (
            not value
            or value.get("schema") != "pr-completion/v1"
            or value["status"] not in {"pending", "waiting_terminal"}
        ):
            continue
        if (
            value["status"] == "waiting_terminal"
            and api.request(f"pulls/{value['candidate']['pr_number']}")["state"] == "open"
        ):
            continue
        if dispatch:
            api.dispatch("github-ai-continuation.yml", {"completion": key})
        else:
            try:
                reconcile(api, store, key)
            except Blocked:
                continue
        count += 1
    return count
