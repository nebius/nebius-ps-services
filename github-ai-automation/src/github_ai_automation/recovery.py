"""Scheduled reconciliation of interrupted operations without resetting budgets."""

import json
import os
import time

from .contracts import Blocked, Candidate
from .policy import read_policy
from .state import acquire


def recover(api, store, root):
    repo = api.request("")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
        or os.environ.get("GITHUB_REF") != "refs/heads/" + repo["default_branch"]
        or str(repo["id"]) != os.environ.get("AI_AUTOMATION_REPOSITORY_ID")
    ):
        raise Blocked("TRUSTED_RUNTIME_REQUIRED")
    policy = read_policy(root / "github-ai-automation/config/policy.json")
    counts = {"cleanup_recovered": 0, "pending": 0, "requests": 0}
    prefix = f"repositories/{repo['id']}"
    from . import live_slot, scheduler

    scheduler.reconcile(api, store, repo["id"])
    if os.environ.get("AI_AUTOMATION_PR_REVIEW", "disabled") != "disabled":
        from .lifecycle import reconcile_all

        reconcile_all(api, store, dispatch=True)
    try:
        if live_slot.recover(api, store, root, policy, repo["id"]):
            counts["cleanup_recovered"] += 1
    except Blocked:
        counts["pending"] += 1
    for key in store.scan(prefix):
        if not key.endswith("/cleanup"):
            continue
        pending, _ = store.read(key)
        if not pending or not pending["runs"]:
            continue
        plan, _ = store.read(pending["runs"][0])
        candidate = Candidate.model_validate(plan["plan"]["candidate"])
        if key != candidate.key + "/cleanup":
            raise Blocked("CLEANUP_IDENTITY_INVALID")
        # Only the global live owner can establish writer quiescence and clean.
        # Orphaned records without that authority require explicit reconciliation.
        counts["pending"] += 1
    if os.environ.get("AI_AUTOMATION_PR_REVIEW", "disabled") != "disabled":
        from .requests import active, dispatch, finish

        for key in store.scan(prefix):
            if not key.endswith("/operation"):
                continue
            operation, _ = store.read(key)
            if (
                not operation
                or operation.get("schema_version") != 2
                or operation.get("status") != "active"
            ):
                continue
            candidate = Candidate.model_validate(operation["candidate"])
            if key != candidate.key + "/operation":
                raise Blocked("REQUEST_IDENTITY_INVALID")
            try:
                pr = api.request(f"pulls/{candidate.pr_number}")
                if (
                    pr["id"] != candidate.pr_id
                    or pr["base"]["repo"]["id"] != candidate.repository_id
                ):
                    raise Blocked("REQUEST_IDENTITY_CHANGED")
                # Preserve the reviewed base/tree on an external merge and tolerate
                # a deleted fork; completion will independently check head/result.
                current = candidate
                if pr["state"] == "open":
                    current, pr = api.candidate(candidate.pr_number)
                if operation["deadline"] <= time.time() or pr["state"] != "open":
                    lease = acquire(store, candidate.key + "/lock")
                    try:
                        finish(
                            store,
                            lease,
                            current,
                            operation["id"],
                            "MERGED"
                            if pr.get("merged")
                            else "EXTERNALLY_CLOSED"
                            if pr["state"] != "open"
                            else "OPERATION_EXPIRED",
                        )
                    finally:
                        lease.release()
                    continue
                if pr["draft"]:
                    continue
                if operation.get("adoption_child"):
                    binding = operation["adoption_child"]
                    child = Candidate.model_validate(binding["child"])
                    child_record, _ = store.read(child.key + "/operation")
                    if not child_record:
                        from .adoption import transfer

                        parent_lease = acquire(store, candidate.key + "/lock")
                        try:
                            child_record = transfer(
                                store, parent_lease, candidate, child, operation, binding
                            )
                        finally:
                            parent_lease.release()
                    if child_record["status"] == "active":
                        dispatch(api, store, child_record)
                    continue
                dispatch(api, store, active(store, current, operation["id"]))
                counts["requests"] += 1
            except Blocked:
                counts["pending"] += 1
        scheduler.pump(api, store, repo["id"])
    if os.environ.get("AI_AUTOMATION_RELEASE_PUBLISH") == "true":
        registry = json.loads(
            (root / "github-ai-automation/config/release-projects.json").read_text()
        )
        for project in registry["projects"]:
            release_key = prefix + f"/releases/{project}"
            record, _ = store.read(release_key + "/current")
            if record and record["status"] != "complete":
                from .rollout import release_scope

                try:
                    release_scope(
                        store, repo["id"], project, record["source_sha"], version=record["version"]
                    )
                except Blocked:
                    counts["pending"] += 1
                    continue
                api.dispatch(
                    "github-ai-release.yml",
                    {
                        "project": project,
                        "pr_number": str(record["pr_number"]),
                        "completion_run": str(record["completion_run"]),
                        "tag": record["tag"],
                    },
                )
                continue
            pending = []
            for key in store.scan(release_key + "/requests"):
                request, _ = store.read(key)
                if request and request["status"] == "pending":
                    pending.append(request)
            if pending:
                request = min(pending, key=lambda r: (r["requested_at"], r["source_sha"]))
                from .rollout import release_scope

                try:
                    release_scope(store, repo["id"], project, request["source_sha"])
                except Blocked:
                    counts["pending"] += 1
                    continue
                api.dispatch(
                    "github-ai-release.yml",
                    {
                        "project": project,
                        "pr_number": str(request["pr_number"]),
                        "completion_run": str(request["completion_run"]),
                        "tag": request.get("requested_tag") or "",
                    },
                )
    return counts
