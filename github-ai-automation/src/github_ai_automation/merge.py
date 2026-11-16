"""Admission adapter for the existing protected merge effect owner."""

import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import requests
from .contracts import Admission, Blocked, Waiver, digest
from .controller import Heartbeat, configured_store
from .events import refresh_ci
from .github import GitHub
from .live import require_cleanup
from .policy import policy_digest
from .state import Lease, NotSubmitted, Unconfirmed, acquire, effect


def assert_execution_lease(pr):
    """The legacy effect helper cannot run outside the serialized controller."""
    owner = os.environ.get("AI_AUTOMATION_LEASE_OWNER", "")
    fence = os.environ.get("AI_AUTOMATION_LEASE_FENCE", "")
    if not owner or not fence.isdecimal():
        raise Blocked("MERGE_CONTROLLER_REQUIRED")
    key = f"repositories/{pr['base']['repo']['id']}/prs/{pr['id']}/lock"
    store = configured_store()
    Lease(store, key, owner, int(fence), time.time).assert_owned()
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    candidate, current = api.candidate(pr["number"])
    record = requests.require_capability(
        store, candidate, os.environ.get("AI_AUTOMATION_REQUEST_ID", ""), "merge"
    )
    if (
        candidate.model_dump() != record["candidate"]
        or current["state"] != "open"
        or current["draft"]
    ):
        raise Blocked("PR_SNAPSHOT_CHANGED")
    from .issue_resolution import premerge

    premerge(
        api,
        candidate,
        record.get("issue_evidence"),
        inherited_from=record.get("adoption_parent", {}).get("candidate", {}).get("pr_number"),
    )


def verify_review(repository: str, token: str, review: dict, pr: dict, *, store=None):
    if os.environ.get("AI_AUTOMATION_PR_REVIEW") != "merge":
        raise Blocked("MERGE_NOT_ENABLED")
    api = GitHub(repository, token)
    if (
        review["user"]["id"] != 41898282
        or review["state"] != "COMMENTED"
        or review["commit_id"] != pr["head"]["sha"]
    ):
        raise Blocked("AUTOMATION_REVIEW_REQUIRED")
    pointer = json.loads(review["body"])
    if (
        set(pointer) != {"schema", "run_id", "attempt", "request_id"}
        or pointer["schema"] != "github-ai-automation/review-v2"
    ):
        raise Blocked("AUTOMATION_REVIEW_REQUIRED")
    run_id, attempt = pointer["run_id"], pointer["attempt"]
    if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) <= 0:
        raise Blocked("AUTOMATION_POINTER_INVALID")
    result = api.artifact(run_id, f"automation-result-{run_id}-{attempt}", "result.json")
    admission = Admission.model_validate(result["admission"])
    if (
        admission.producer.workflow != ".github/workflows/github-ai-automation.yml"
        or admission.producer.policy_sha256 != policy_digest(Path("."))
    ):
        raise Blocked("ADMISSION_POLICY_CHANGED")
    candidate, _ = api.candidate(pr["number"])
    if admission.candidate != candidate or admission.expires_at <= time.time():
        raise Blocked("ADMISSION_STALE")
    if admission.producer.run_id != run_id or admission.producer.attempt != attempt:
        raise Blocked("ADMISSION_PRODUCER_MISMATCH")
    api.producer(admission.producer)
    store = store or configured_store()
    requests.require_capability(store, candidate, pointer["request_id"], "merge")
    if pointer["request_id"] != admission.operation_id:
        raise Blocked("REQUEST_IDENTITY_INVALID")
    require_cleanup(store, candidate)
    key = (
        candidate.key
        + "/requests/"
        + admission.operation_id
        + f"/runs/{run_id}_{attempt}/admission"
    )
    retained, _ = store.read(key)
    if retained != admission.model_dump():
        raise Blocked("ADMISSION_STATE_MISMATCH")
    stages, _ = store.read(key.removesuffix("/admission") + "/stages")
    if not stages or digest(stages) != admission.stages_sha256:
        raise Blocked("STAGE_EVIDENCE_CHANGED")
    operation, _ = store.read(candidate.key + "/operation")
    if (
        not operation
        or operation["id"] != admission.operation_id
        or operation["deadline"] != admission.expires_at
        or operation.get("qualification_fence") != admission.fence
    ):
        raise Blocked("OPERATION_CHANGED")
    waiver_data, _ = store.read(candidate.key + "/waiver")
    waiver = Waiver.model_validate(waiver_data) if waiver_data else None
    if (waiver.revision if waiver else 0) != admission.waiver_revision:
        raise Blocked("WAIVER_CHANGED")
    from .issue_resolution import premerge

    premerge(
        api,
        candidate,
        operation.get("issue_evidence"),
        inherited_from=operation.get("adoption_parent", {}).get("candidate", {}).get("pr_number"),
    )
    refresh_ci(api, store, admission.ci, candidate, admission.operation_id)
    inventory = json.loads(Path(".github/merge-policy.json").read_text())["ci_workflows"]
    paths = api.paths(candidate)
    required = {
        w["file"]
        for w in inventory
        if any(fnmatch.fnmatchcase(p, q) for p in paths for q in w["paths"])
    }
    if not required or {r.workflow for r in admission.ci} != required:
        raise Blocked("CI_POLICY_CHANGED")
    return {
        "automation": admission.model_dump(),
        "head": candidate.head_sha,
        "base_sha": candidate.base_sha,
    }


def execute_merge(api, store, root, number, head, review_id, method):
    candidate, pr = api.candidate(number)
    if candidate.head_sha != head:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    lease = acquire(store, candidate.key + "/lock")
    with Heartbeat(lease):
        try:
            review = api.request(f"pulls/{number}/reviews/{review_id}")
            evidence = verify_review(
                api.repository, os.environ["GH_TOKEN"], review, pr, store=store
            )
            intent = {
                "candidate": candidate.model_dump(),
                "review_id": review_id,
                "method": method,
                "operation_id": evidence["automation"]["operation_id"],
            }

            def observe(_):
                current = api.request(f"pulls/{number}")
                return (
                    {"outcome": "merged", "result_sha": current["merge_commit_sha"]}
                    if current["merged"]
                    else None
                )

            def execute(_):
                with tempfile.TemporaryDirectory(prefix="ai-merge-effect-") as directory:
                    receipt = Path(directory) / "submitted.json"
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(root / "skills/merge-pr/scripts/merge_gate.py"),
                            "execute",
                            "--pr",
                            str(number),
                            "--head",
                            head,
                            "--review-id",
                            str(review_id),
                            "--method",
                            method,
                        ],
                        cwd=root,
                        env={
                            **os.environ,
                            "AI_AUTOMATION_LEASE_OWNER": lease.owner,
                            "AI_AUTOMATION_REQUEST_ID": evidence["automation"]["operation_id"],
                            "AI_AUTOMATION_LEASE_FENCE": str(lease.fence),
                            "AI_AUTOMATION_MERGE_DISPATCH_RECEIPT": str(receipt),
                        },
                        capture_output=True,
                        timeout=300,
                        check=False,
                    )
                    if result.returncode:
                        if not receipt.exists():
                            raise NotSubmitted()
                        raise Blocked("MERGE_PENDING_OR_REJECTED")
                    outcome = json.loads(result.stdout)
                    return outcome if outcome.get("outcome") == "merged" else Unconfirmed(outcome)

            request_id = evidence["automation"]["operation_id"]
            requests.require_capability(store, candidate, request_id, "merge")
            result = effect(
                store,
                lease,
                candidate.key + "/requests/" + request_id + "/merge/" + head,
                intent,
                observe,
                execute,
            )
            if observe(intent):
                requests.finish(store, lease, candidate, request_id, "MERGED")
            return result
        finally:
            lease.release()
