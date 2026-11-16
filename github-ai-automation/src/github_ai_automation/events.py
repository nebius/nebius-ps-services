"""Privileged model-free continuation, bound to an active request and candidate."""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from . import requests
from .contracts import Admission, Blocked, Candidate, CIResult, GateAssessment, Producer, digest
from .controller import Heartbeat
from .policy import CONTROL_PATHS, policy_digest, read_policy
from .state import Unconfirmed, acquire, effect


def completed_run(api, run_id: int, *, require_success=True):
    run = api.request(f"actions/runs/{run_id}")
    repo = api.request("")
    if (
        run["repository"]["id"] != repo["id"]
        or run["event"] != "workflow_dispatch"
        or run["head_branch"] != repo["default_branch"]
        or run["status"] != "completed"
        or (require_success and run["conclusion"] != "success")
    ):
        raise Blocked("PRODUCER_NOT_SUCCESSFUL")
    return run


def ci_key(candidate, request_id, workflow):
    return (
        candidate.key
        + "/requests/"
        + request_id
        + "/ci-requests/"
        + digest({"candidate": candidate.model_dump(), "workflow": workflow})
    )


def latest_candidate_run(api, workflow, candidate, request_id):
    title = (
        f"ai-candidate-{candidate.pr_number}-{candidate.head_sha}-{candidate.base_sha}-{request_id}"
    )
    runs = api.pages(
        f"actions/workflows/{workflow}/runs?event=workflow_dispatch", field="workflow_runs"
    )
    matches = [r for r in runs if r["display_title"] == title]
    return max(matches, key=lambda r: r["id"]) if matches else None


def ci_receipt(api, store, candidate, request_id, workflow, run):
    requested, _ = store.read(ci_key(candidate, request_id, workflow))
    if not requested or requested["candidate"] != candidate.model_dump():
        raise Blocked("CI_NOT_REQUESTED")
    run = completed_run(api, run["id"])
    receipt = api.artifact(
        run["id"], f"candidate-ci-{run['id']}-{run['run_attempt']}", "candidate.json"
    )
    if (
        run["path"] != ".github/workflows/" + workflow
        or any(receipt.get(k) != v for k, v in candidate.model_dump().items())
        or receipt.get("schema") != "automation-candidate-ci/v2"
        or receipt.get("request_id") != request_id
        or receipt.get("source") != requested["source"]
        or receipt.get("run_id") != run["id"]
        or receipt.get("attempt") != run["run_attempt"]
        or receipt.get("workflow_sha") != run["head_sha"]
        or receipt.get("workflow") != workflow
        or receipt.get("conclusion") != "success"
        or not receipt.get("jobs")
    ):
        raise Blocked("CI_RECEIPT_INVALID")
    return CIResult(
        workflow=workflow,
        request_id=request_id,
        run_id=run["id"],
        attempt=run["run_attempt"],
        workflow_sha=run["head_sha"],
        tested_tree=candidate.tree_sha,
        conclusion="success",
    )


def refresh_ci(api, store, ci: list[CIResult], candidate: Candidate, request_id):
    requests.require_capability(store, candidate, request_id, "qualify")
    for result in ci:
        latest = latest_candidate_run(api, result.workflow, candidate, request_id)
        if (
            not latest
            or latest["id"] != result.run_id
            or ci_receipt(api, store, candidate, request_id, result.workflow, latest) != result
        ):
            raise Blocked("CI_PRODUCER_CHANGED")


def expected_ci(api, root, candidate):
    inventory = json.loads((root / ".github/merge-policy.json").read_text())["ci_workflows"]
    paths = api.paths(candidate)
    return [
        w["file"]
        for w in inventory
        if any(fnmatch.fnmatchcase(p, q) for p in paths for q in w["paths"])
    ]


def reconcile_ci(api, store, lease, root, candidate, request_id, *, env=os.environ):
    requests.require_capability(store, candidate, request_id, "qualify", env=env)
    results = []
    for workflow in expected_ci(api, root, candidate):
        run = latest_candidate_run(api, workflow, candidate, request_id)
        if not run or run["status"] != "completed":
            continue
        if run["conclusion"] != "success":
            raise Blocked("CANDIDATE_CI_FAILED")
        results.append(ci_receipt(api, store, candidate, request_id, workflow, run))
    return results


def collect_ci(api, store, root: Path, run_id: int):
    run = completed_run(api, run_id, require_success=False)
    match = re.fullmatch(
        r"ai-candidate-(\d+)-([a-f0-9]{40})-([a-f0-9]{40})-([a-f0-9]{64})", run["display_title"]
    )
    if not match:
        return
    candidate, pr = api.candidate(int(match[1]))
    if (candidate.head_sha, candidate.base_sha) != (match[2], match[3]) or pr["state"] != "open":
        return
    request_id = match[4]
    lease = acquire(store, candidate.key + "/lock")
    ready = None
    try:
        record = requests.require_capability(store, candidate, request_id, "qualify")
        workflows = expected_ci(api, root, candidate)
        workflow = run["path"].removeprefix(".github/workflows/")
        requested, _ = store.read(ci_key(candidate, request_id, workflow))
        if workflow not in workflows or not requested:
            raise Blocked("CI_PRODUCER_UNTRUSTED")
        latest = latest_candidate_run(api, workflow, candidate, request_id)
        if not latest or latest["id"] != run_id:
            return
        if run["conclusion"] != "success":
            report(
                api,
                store,
                lease,
                candidate,
                request_id,
                "CANDIDATE_CI_FAILED",
                "Candidate CI failed or was cancelled. Fix the failing checks and submit a new manual request.",
            )
            return
        results = reconcile_ci(api, store, lease, root, candidate, request_id)
        if {r.workflow for r in results} == set(workflows):
            ready = record
    finally:
        lease.release()
    if ready:
        requests.dispatch(api, store, ready)


def publish_patch(
    api, store, lease, record: str, candidate: Candidate, request_id: str, root: Path
):
    requests.require_capability(store, candidate, request_id, "repair")
    proposal, _ = store.read(record + "/patch")
    if not proposal or proposal["candidate"] != candidate.model_dump():
        raise Blocked("PATCH_RECEIPT_INVALID")
    if proposal["operation_id"] != request_id:
        raise Blocked("PATCH_OPERATION_CHANGED")
    verification = proposal.get("verification", {})
    assessment = GateAssessment.model_validate(verification.get("assessment", {}))
    receipts = verification.get("checks", [])
    if (
        assessment.verdict != "pass"
        or not receipts
        or any(r.get("exit_code") != 0 for r in receipts)
        or not set(assessment.required_checks) <= {r["id"] for r in receipts}
        or verification.get("patch_sha256") != digest(proposal["patch"])
        or not {p["path"] for p in proposal["patch"]} <= set(assessment.reviewed_paths)
    ):
        raise Blocked("PATCH_NOT_VERIFIED")
    producer = Producer.model_validate(proposal["producer"])
    if proposal.get("merge_base") not in {None, candidate.base_sha}:
        raise Blocked("CONFLICT_BASE_CHANGED")
    if producer.policy_sha256 != policy_digest(root):
        raise Blocked("PATCH_POLICY_CHANGED")
    api.producer(producer)
    current, pr = api.candidate(candidate.pr_number)
    if current != candidate:
        raise Blocked("PATCH_HEAD_CHANGED")
    patch = proposal["patch"]
    if not patch or len(patch) > 100:
        raise Blocked("PATCH_SIZE_INVALID")
    tree = []
    for item in patch:
        path = item["path"]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in CONTROL_PATHS):
            raise Blocked("PATCH_SCOPE_REJECTED")
        if item["mode"] not in {"100644", "100755"}:
            raise Blocked("PATCH_MODE_INVALID")
        sha = None
        if item["content"] is not None:
            data = base64.b64decode(item["content"], validate=True)
            if re.search(
                rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}|(?:tenant|project)-[a-z0-9]{16,}",
                data,
            ):
                raise Blocked("PATCH_SENSITIVE_CONTENT")
            sha = api.request(
                "git/blobs", method="POST", body={"content": item["content"], "encoding": "base64"}
            )["sha"]
        tree.append({"path": path, "mode": item["mode"], "type": "blob", "sha": sha})
    tree_sha = api.request(
        "git/trees", method="POST", body={"base_tree": candidate.tree_sha, "tree": tree}
    )["sha"]
    run = api.request(f"actions/runs/{producer.run_id}")
    author = {
        "name": "github-actions[bot]",
        "email": "41898282+github-actions[bot]@users.noreply.github.com",
        "date": run["created_at"],
    }
    commit = api.request(
        "git/commits",
        method="POST",
        body={
            "message": "fix: address verified PR findings",
            "tree": tree_sha,
            "parents": [candidate.head_sha]
            + ([candidate.base_sha] if proposal.get("merge_base") else []),
            "author": author,
            "committer": author,
        },
    )["sha"]
    current, _ = api.candidate(candidate.pr_number)
    if current != candidate:
        raise Blocked("PATCH_HEAD_CHANGED")
    operation, etag = store.read(candidate.key + "/operation")
    if operation["id"] != proposal["operation_id"]:
        raise Blocked("PATCH_OPERATION_CHANGED")
    operation["owned_heads"] = sorted(set(operation["owned_heads"] + [commit]))
    store.put(candidate.key + "/operation", operation, etag)
    intent = {"branch": pr["head"]["ref"], "before": candidate.head_sha, "after": commit}

    def observe(value):
        head = api.request(f"pulls/{candidate.pr_number}")["head"]["sha"]
        return {"head": head} if head == value["after"] else None

    def push(value):
        requests.require_capability(store, candidate, request_id, "repair")
        current, current_pr = api.candidate(candidate.pr_number)
        if current != candidate or current_pr["state"] != "open" or current_pr["draft"]:
            raise Blocked("PATCH_HEAD_CHANGED")
        # Source and destination identity are verified before any branch write.
        api.request(
            "git/refs/heads/" + quote(value["branch"], safe=""),
            method="PATCH",
            body={"sha": value["after"], "force": False},
        )
        return {"head": value["after"]}

    effect(store, lease, record + "/push", intent, observe, push)


def report(api, store, lease, candidate, request_id, code, detail="", *, terminal=True):
    requests.require_capability(store, candidate, request_id, "comment")
    current, pr = api.candidate(candidate.pr_number)
    if current != candidate or pr["state"] != "open":
        raise Blocked("PR_SNAPSHOT_CHANGED")
    requests.require_capability(store, candidate, request_id, "label")
    api.mark_fork(candidate)
    from .stages import public_text

    pending = requests.unsettled(store, candidate)
    from .lifecycle import publish

    publish(
        api,
        candidate,
        code,
        guard=lease.assert_owned,
        detail=public_text(detail)
        or (
            "A submitted operation is awaiting confirmation. Recovery will reconcile its outcome "
            "within this request's original deadline."
            if pending
            else "The PR remains open. Address the named gate, then submit a new manual request by PR number."
        ),
    )
    current, pr = api.candidate(candidate.pr_number)
    if current != candidate or pr["state"] != "open" or pr.get("draft"):
        raise Blocked("PR_SNAPSHOT_CHANGED")
    if terminal and not pending:
        op, revision = store.read(candidate.key + "/operation")
        op["detail"] = public_text(detail)
        store.put(candidate.key + "/operation", op, revision)
        requests.finish(store, lease, candidate, request_id, code)


def continue_run(api, store, root: Path, run_id: int):
    run = completed_run(api, run_id, require_success=False)
    if run["path"] != ".github/workflows/github-ai-automation.yml":
        return collect_ci(api, store, root, run_id)
    if run["conclusion"] != "success":
        return  # Active-request recovery owns interrupted runs; no backlog comments.
    result = api.artifact(run_id, f"automation-result-{run_id}-{run['run_attempt']}", "result.json")
    if not result.get("candidate") or not result.get("request_id"):
        return
    candidate, pr = api.candidate(result["pr_number"])
    if result["candidate"] != candidate.model_dump() or pr["state"] != "open" or pr["draft"]:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    request_id, code = result["request_id"], result["code"]
    record_key = candidate.key + "/requests/" + request_id + f"/runs/{run_id}_{run['run_attempt']}"
    lease = acquire(store, candidate.key + "/lock")
    followup = None
    with Heartbeat(lease):
        try:
            record = requests.active(store, candidate, request_id, operation=result["operation"])
            if code == "CLOSURE_PREPARED":
                from .closure import close
                from .live import require_cleanup

                requests.require_capability(store, candidate, request_id, "close")
                require_cleanup(store, candidate)
                api.mark_fork(candidate)
                close(api, store, lease, candidate, result["proof"], request_id)
                requests.finish(store, lease, candidate, request_id, "CLOSED")
                return
            if code == "ADOPTION_PREPARED":
                from .adoption import publish

                if result.get("record") != record_key:
                    raise Blocked("CONTINUATION_IDENTITY_INVALID")
                report(
                    api,
                    store,
                    lease,
                    candidate,
                    request_id,
                    "ADOPTION_WAITING",
                    "Adopting accepted features into one replacement PR, with independent coverage verification.",
                    terminal=False,
                )
                child_record = publish(api, store, lease, record_key, candidate, request_id, root)
                followup = ("adoption", child_record)
            elif code == "LIVE_REMEDIATION_REQUIRED":
                report(
                    api,
                    store,
                    lease,
                    candidate,
                    request_id,
                    code,
                    "Live testing failed. Diagnosing the failure before a bounded repair and fresh trial.",
                    terminal=False,
                )
                followup = (
                    "github-ai-automation.yml",
                    {
                        "pr_number": str(candidate.pr_number),
                        "operation": record["operation"],
                        "request_id": request_id,
                    },
                )
            elif code == "BASE_SYNC_REQUIRED":
                requests.require_capability(store, candidate, request_id, "repair")
                if (
                    record["attempts"]
                    >= read_policy(root / "github-ai-automation/config/policy.json")[
                        "max_fix_attempts"
                    ]
                ):
                    raise Blocked("REPAIR_BUDGET_EXHAUSTED")
                key = candidate.key + "/requests/" + request_id + "/base-sync/" + digest(candidate)
                intent = {"candidate": candidate.model_dump()}

                def observe(_):
                    current, _pr = api.candidate(candidate.pr_number)
                    if current.head_sha == candidate.head_sha:
                        return None
                    commit = api.request(f"git/commits/{current.head_sha}")
                    if {p["sha"] for p in commit["parents"]} == {
                        candidate.head_sha,
                        candidate.base_sha,
                    }:
                        return {"head": current.head_sha}
                    return None

                def synchronize(_):
                    requests.require_capability(store, candidate, request_id, "repair")
                    current, _pr = api.candidate(candidate.pr_number)
                    if current != candidate:
                        raise Blocked("PR_SNAPSHOT_CHANGED")
                    op, revision = store.read(candidate.key + "/operation")
                    op["attempts"] += 1
                    store.put(candidate.key + "/operation", op, revision)
                    api.request(
                        f"pulls/{candidate.pr_number}/update-branch",
                        method="PUT",
                        body={"expected_head_sha": candidate.head_sha},
                    )
                    return Unconfirmed({"submitted": True})

                report(
                    api,
                    store,
                    lease,
                    candidate,
                    request_id,
                    "BASE_SYNC_REQUESTED",
                    "Updating the branch against the current base; the new candidate will be reassessed.",
                    terminal=False,
                )
                effect(store, lease, key, intent, observe, synchronize)
                followup = (
                    "github-ai-automation.yml",
                    {
                        "pr_number": str(candidate.pr_number),
                        "operation": record["operation"],
                        "request_id": request_id,
                    },
                )
            elif code in {"PATCH_PREPARED", "CI_REQUIRED", "MERGE_ELIGIBLE"}:
                if result.get("record") != record_key:
                    raise Blocked("CONTINUATION_IDENTITY_INVALID")
                if code == "PATCH_PREPARED":
                    requests.require_capability(store, candidate, request_id, "repair")
                    report(
                        api,
                        store,
                        lease,
                        candidate,
                        request_id,
                        code,
                        "Publishing an independently verified repair; the new candidate will be reviewed and tested again.",
                        terminal=False,
                    )
                    publish_patch(api, store, lease, record_key, candidate, request_id, root)
                    followup = (
                        "github-ai-automation.yml",
                        {
                            "pr_number": str(candidate.pr_number),
                            "operation": record["operation"],
                            "request_id": request_id,
                        },
                    )
                elif code == "CI_REQUIRED":
                    requests.require_capability(store, candidate, request_id, "qualify")
                    workflows = expected_ci(api, root, candidate)
                    if set(result["expected_ci"]) != set(workflows) or not workflows:
                        raise Blocked("CI_POLICY_CHANGED")
                    report(
                        api,
                        store,
                        lease,
                        candidate,
                        request_id,
                        "WAITING_FOR_CI",
                        "Usefulness, review and alignment passed. Waiting for required candidate CI.",
                        terminal=False,
                    )
                    for workflow in workflows:
                        request_ci(
                            api,
                            store,
                            lease,
                            workflow,
                            candidate,
                            request_id,
                            source=f"{run_id}:{run['run_attempt']}",
                        )
                else:
                    requests.require_capability(store, candidate, request_id, "merge")
                    admission = Admission.model_validate(result["admission"])
                    retained, _ = store.read(record_key + "/admission")
                    if retained != admission.model_dump() or admission.operation_id != request_id:
                        raise Blocked("ADMISSION_STATE_MISMATCH")
                    refresh_ci(api, store, admission.ci, candidate, request_id)
                    api.request(
                        f"statuses/{candidate.head_sha}",
                        method="POST",
                        body={
                            "state": "success",
                            "context": "Required CI",
                            "description": "Trusted exact-candidate CI verified",
                            "target_url": f"https://github.com/{api.repository}/actions/runs/{run_id}",
                        },
                    )
                    pointer = {
                        "schema": "github-ai-automation/review-v2",
                        "run_id": run_id,
                        "attempt": run["run_attempt"],
                        "request_id": request_id,
                    }
                    reviews = api.pages(f"pulls/{candidate.pr_number}/reviews")
                    matching = [
                        r
                        for r in reviews
                        if r["user"]["id"] == 41898282
                        and r["commit_id"] == candidate.head_sha
                        and r["body"] == json.dumps(pointer, sort_keys=True)
                    ]
                    review = (
                        matching[-1]
                        if matching
                        else api.request(
                            f"pulls/{candidate.pr_number}/reviews",
                            method="POST",
                            body={
                                "event": "COMMENT",
                                "commit_id": candidate.head_sha,
                                "body": json.dumps(pointer, sort_keys=True),
                            },
                        )
                    )
                    report(
                        api,
                        store,
                        lease,
                        candidate,
                        request_id,
                        "MERGE_ELIGIBLE",
                        "Every required gate passed. The protected merge broker will revalidate before acting.",
                        terminal=False,
                    )
                    followup = (
                        "skills-merge-pr.yml",
                        {
                            "pr": str(candidate.pr_number),
                            "head": candidate.head_sha,
                            "review_id": str(review["id"]),
                            "method": "squash",
                        },
                    )
            else:
                report(api, store, lease, candidate, request_id, code, result.get("detail", ""))
        finally:
            lease.release()
    if followup:
        if followup[0] == "adoption":
            requests.dispatch(api, store, followup[1])
        elif followup[0] == "github-ai-automation.yml":
            requests.dispatch(api, store, record)
        else:
            requests.require_capability(store, candidate, request_id, "merge")
            api.dispatch(*followup)


def request_ci(api, store, lease, workflow, candidate, request_id, *, source, now=None):
    """Dispatch loss may repeat credentialless CI; authority stays request-scoped."""
    requests.require_capability(store, candidate, request_id, "qualify")
    now = int(time.time()) if now is None else now
    key = ci_key(candidate, request_id, workflow)
    requested, revision = store.read(key)
    latest = latest_candidate_run(api, workflow, candidate, request_id)
    if latest:
        if latest["status"] == "completed" and latest["conclusion"] != "success":
            raise Blocked("CANDIDATE_CI_FAILED")
        return latest["id"]
    if requested and now - requested["time"] < 120:
        return None
    attempts = (requested or {}).get("attempts", 0)
    if attempts >= 3:
        raise Blocked("CI_DISPATCH_UNAVAILABLE")
    source = requested["source"] if requested else source
    lease.assert_owned()
    store.put(
        key,
        {
            "status": "requested",
            "time": now,
            "attempts": attempts + 1,
            "candidate": candidate.model_dump(),
            "request_id": request_id,
            "workflow": workflow,
            "source": source,
        },
        revision,
    )
    api.dispatch(
        workflow,
        {
            "candidate_pr": str(candidate.pr_number),
            "candidate_head": candidate.head_sha,
            "candidate_base": candidate.base_sha,
            "candidate_request_id": request_id,
            "candidate_source": source,
        },
    )
