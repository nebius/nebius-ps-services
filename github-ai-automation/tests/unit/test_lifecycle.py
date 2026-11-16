"""Public dispositions and durable completion, including later manual merges."""

import copy
import time

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import issue_resolution, lifecycle, requests, rollout
from github_ai_automation.contracts import Blocked, Candidate, digest
from github_ai_automation.state import acquire


@pytest.fixture
def candidate():
    return Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="owner/repo",
        head_repository_id=1,
        head_ref="feature",
        pr_id=9,
        pr_number=2,
        head_sha="a" * 40,
        base_sha="b" * 40,
        tree_sha="c" * 40,
    )


class API:
    repository = "owner/repo"

    def __init__(self, candidate):
        self.candidate_value = candidate
        self.pr = {
            "id": candidate.pr_id,
            "state": "open",
            "merged": False,
            "head": {"sha": candidate.head_sha},
            "base": {"repo": {"id": 1}},
            "merge_commit_sha": "d" * 40,
        }
        self.issue = {
            "id": 30,
            "title": "A defect",
            "body": "Output must work",
            "state": "open",
            "state_reason": None,
        }
        self.comments, self.events, self.writes = [], [], []
        self.labels, self.catalog = {"bug", "testing"}, {"testing"}
        self.tip, self.tree = "d" * 40, candidate.tree_sha
        self.comment_crash = False

    def graphql(self, *_):
        return {
            "repository": {
                "pullRequest": {
                    "closingIssuesReferences": {
                        "nodes": [
                            {
                                "number": 3,
                                "databaseId": 30,
                                "repository": {"nameWithOwner": self.repository},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            }
        }

    def pages(self, path):
        if path == "issues/3/comments":
            return copy.deepcopy(self.comments)
        if path == "issues/3/events":
            return copy.deepcopy(self.events)
        if path == "issues/2/labels":
            return [{"name": n} for n in self.labels]
        raise AssertionError(path)

    def request(self, path, *, method="GET", body=None, absent=False):
        if method != "GET":
            self.writes.append((path, method, body))
        if path == "":
            return {"id": 1, "default_branch": "main"}
        if path == "pulls/2":
            return copy.deepcopy(self.pr)
        if path == "issues/3":
            if method == "PATCH":
                self.issue.update(body)
            return copy.deepcopy(self.issue)
        if path == "issues/3/comments":
            self.comments.append(
                {
                    "id": len(self.comments) + 1,
                    "body": body["body"],
                    "user": {"id": 41898282, "type": "Bot"},
                }
            )
            if self.comment_crash:
                self.comment_crash = False
                raise Blocked("GITHUB_OUTCOME_UNKNOWN")
            return self.comments[-1]
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.tip}}
        if path.startswith("git/commits/"):
            return {"tree": {"sha": self.tree}}
        if path.startswith("labels/"):
            return {"name": path[7:]} if path[7:] in self.catalog else None
        if path == "labels":
            self.catalog.add(body["name"])
            return body
        if path == "issues/2/labels":
            self.labels.update(body["labels"])
            return [{"name": n} for n in self.labels]
        if path.startswith("issues/2/labels/"):
            self.labels.discard(path.split("/")[-1])
            return None
        raise AssertionError(path)

    def comment(self, number, code, detail=""):
        self.writes.append(("pr-comment", code, detail))


def evidence(api, candidate, *, disposition="resolved", basis="head"):
    snapshots = issue_resolution.collect(api, 2)
    return {
        "candidate": candidate.model_dump(),
        "request_id": REQUEST_ID,
        "snapshots": snapshots,
        "assessment": {
            "reviewed_paths": ["src/a.py"],
            "issues": [
                {
                    "repository": api.repository,
                    "number": 3,
                    "issue_id": 30,
                    "snapshot_sha256": snapshots[0]["snapshot_sha256"],
                    "disposition": disposition,
                    "basis": basis,
                    "rationale": "All criteria checked against the exact source and regression",
                    "criteria": ["Output works"],
                    "evidence": ["src/a.py and regression result"],
                    "duplicate_issue_id": None,
                }
            ],
        },
        "stage_key": "unused",
    }


def complete(api, candidate, proof, store=None):
    store = store or MemoryStore()
    lease = acquire(store, candidate.key + "/lock")
    try:
        return issue_resolution.complete_issues(
            api,
            store,
            lease,
            candidate.key + "/completions/" + REQUEST_ID,
            candidate,
            api.pr,
            proof,
            authorize=lambda *_: lease.assert_owned(),
        )
    finally:
        lease.release()


def test_rejection_does_not_resolve_head_only_issue(candidate):
    api = API(candidate)
    api.pr["state"] = "closed"
    assert complete(api, candidate, evidence(api, candidate))[0]["outcome"] == "open"
    assert api.writes == []


@pytest.mark.parametrize("change", ["reopened", "scope", "result_tree", "default"])
def test_merged_issue_requires_unchanged_scope_events_and_result(candidate, change):
    api = API(candidate)
    proof = evidence(api, candidate)
    api.pr.update(state="closed", merged=True)
    if change == "reopened":
        api.events.append({"id": 4, "event": "reopened", "actor": {"id": 5}, "created_at": "now"})
    elif change == "scope":
        api.issue["body"] += " plus another requirement"
    elif change == "result_tree":
        api.tree = "e" * 40
    else:
        api.tip = "f" * 40
    assert complete(api, candidate, proof)[0]["outcome"] == "reassessment_required"
    assert api.writes == []


def test_premerge_catches_reopen_without_scope_change(candidate):
    api = API(candidate)
    proof = evidence(api, candidate)
    api.events.append({"id": 4, "event": "reopened", "actor": {"id": 5}, "created_at": "now"})
    with pytest.raises(Blocked, match="ISSUE_RESOLUTION_INCOMPLETE"):
        issue_resolution.premerge(api, candidate, proof)


def test_comment_crash_resumes_only_missing_close_and_never_recloses(candidate):
    api = API(candidate)
    proof, store = evidence(api, candidate), MemoryStore()
    api.pr.update(state="closed", merged=True)
    api.comment_crash = True
    with pytest.raises(Blocked, match="GITHUB_OUTCOME_UNKNOWN"):
        complete(api, candidate, proof, store)
    assert api.issue["state"] == "open" and len(api.comments) == 1
    assert complete(api, candidate, proof, store)[0]["outcome"] == "closed"
    assert len(api.comments) == 1
    api.issue.update(state="open", state_reason="reopened")
    api.events.append({"id": 4, "event": "reopened", "actor": {"id": 5}, "created_at": "now"})
    assert complete(api, candidate, proof, store)[0]["outcome"] == "reassessment_required"
    assert sum(w[1] == "PATCH" for w in api.writes) == 1


def test_lost_lease_between_comment_and_close_blocks_issue_write(candidate):
    api = API(candidate)
    proof, store = evidence(api, candidate), MemoryStore()
    api.pr.update(state="closed", merged=True)
    lease = acquire(store, candidate.key + "/lock")
    original = api.request

    def request(path, **kwargs):
        result = original(path, **kwargs)
        if path == "issues/3/comments" and kwargs.get("method") == "POST":
            row, rev = store.read(lease.key)
            store.put(lease.key, {**row, "owner": "successor"}, rev)
        return result

    api.request = request
    with pytest.raises(Blocked, match="LEASE_LOST"):
        issue_resolution.complete_issues(
            api, store, lease, "completion", candidate, api.pr, proof,
            authorize=lambda *_: lease.assert_owned(),
        )
    assert api.issue["state"] == "open"


def test_labels_preserve_unrelated_and_manual_wording(candidate):
    api = API(candidate)
    lifecycle.publish(api, candidate, "MANUAL_MERGE", "Live testing is inapplicable.")
    assert api.labels == {"bug", "manual-merge"}
    assert api.writes[-1][2].startswith("Accepted for manual merge.")
    before = len(api.writes)
    lifecycle.label(api, 2, "manual-merge")
    assert len(api.writes) == before
    assert lifecycle.disposition("LIVE_TEST_NOT_PASSED") == "blocked"
    assert lifecycle.disposition("REVIEW_COMPLETE") != "manual-merge"


def test_completion_observes_later_manual_merge_under_shared_lock(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store = API(candidate), MemoryStore()
    op = admitted(store, candidate)
    op["issue_evidence"] = evidence(api, candidate)
    _, revision = store.read(candidate.key + "/operation")
    store.put(candidate.key + "/operation", op, revision)
    lease = acquire(store, candidate.key + "/lock")
    requests.finish(store, lease, candidate, REQUEST_ID, "MANUAL_MERGE")
    lease.release()
    key = candidate.key + "/completions/" + REQUEST_ID
    original = api.comment

    def comment(*args, **kwargs):
        with pytest.raises(Blocked, match="OPERATION_BUSY"):
            acquire(store, candidate.key + "/lock")
        original(*args, **kwargs)

    api.comment = comment
    assert lifecycle.reconcile(api, store, key)["status"] == "waiting_terminal"
    api.pr.update(state="closed", merged=True)
    assert lifecycle.reconcile(api, store, key)["status"] == "complete"
    assert api.labels == {"bug", "merged"} and api.issue["state"] == "closed"
    before = len(api.writes)
    lifecycle.reconcile(api, store, key)
    assert len(api.writes) == before


def test_successor_supersedes_completion(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store = API(candidate), MemoryStore()
    op = admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")
    lifecycle.record_completion(store, lease, candidate, op, "MANUAL_MERGE")
    lease.release()
    _, version = store.read(candidate.key + "/operation")
    store.put(candidate.key + "/operation", {**op, "id": digest("new")}, version)
    with pytest.raises(Blocked, match="COMPLETION_SUPERSEDED"):
        lifecycle.reconcile(api, store, candidate.key + "/completions/" + REQUEST_ID)
    assert api.writes == []


def test_completion_reports_reassessment_separately_from_successful_merge(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store = API(candidate), MemoryStore()
    op = admitted(store, candidate)
    op["issue_evidence"] = evidence(api, candidate)
    _, rev = store.read(candidate.key + "/operation")
    store.put(candidate.key + "/operation", op, rev)
    lease = acquire(store, candidate.key + "/lock")
    requests.finish(store, lease, candidate, REQUEST_ID, "MERGED")
    lease.release()
    api.pr.update(state="closed", merged=True)
    api.tree = "f" * 40
    result = lifecycle.reconcile(api, store, candidate.key + "/completions/" + REQUEST_ID)
    assert result["status"] == "needs_reassessment"
    assert result["observations"]["label"] == "merged"
    assert api.issue["state"] == "open"


def merged_completion(candidate):
    api, store = API(candidate), MemoryStore()
    op = admitted(store, candidate)
    op["issue_evidence"] = evidence(api, candidate)
    # Completion remains possible after execution authority has expired.
    op["deadline"] = int(time.time()) - 1
    _, revision = store.read(candidate.key + "/operation")
    store.put(candidate.key + "/operation", op, revision)
    lease = acquire(store, candidate.key + "/lock")
    try:
        requests.finish(store, lease, candidate, REQUEST_ID, "MERGED")
    finally:
        lease.release()
    api.pr.update(state="closed", merged=True)
    return api, store, candidate.key + "/completions/" + REQUEST_ID


@pytest.mark.parametrize("restriction", ["expired", "no_close", "request_close", "no_label"])
def test_completion_rechecks_effect_capabilities(candidate, monkeypatch, restriction):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store, key = merged_completion(candidate)
    caps = ["assess", "comment", "label", "close"]
    if restriction in {"no_close", "no_label"}:
        caps.remove("close" if restriction == "no_close" else "label")
    scope, revision = store.read(rollout.key(1))
    store.put(rollout.key(1), {**scope, "phase": "qualification", "prs": [{
        "candidate": candidate.model_dump(), "capabilities": caps,
        "expires_at": int(time.time()) + (-1 if restriction == "expired" else 1000),
    }]}, revision)
    if restriction == "request_close":
        op, revision = store.read(candidate.key + "/operation")
        op["capabilities"].remove("close")
        store.put(candidate.key + "/operation", op, revision)
    with pytest.raises(Blocked, match="ROLLOUT_|COMPLETION_CAPABILITY_DENIED"):
        lifecycle.reconcile(api, store, key)
    assert api.issue["state"] == "open"
    assert not any(path.startswith("issues/3") for path, *_ in api.writes)
    if restriction in {"expired", "no_label"}:
        assert api.writes == []
    assert not requests.unsettled(store, candidate)


def test_completion_rechecks_authority_between_issue_comment_and_close(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store, key = merged_completion(candidate)
    original = api.request

    def request(path, **kwargs):
        result = original(path, **kwargs)
        if path == "issues/3/comments" and kwargs.get("method") == "POST":
            monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "report")
        return result

    api.request = request
    with pytest.raises(Blocked, match="COMPLETION_CAPABILITY_DENIED"):
        lifecycle.reconcile(api, store, key)
    assert len(api.comments) == 1
    assert api.issue["state"] == "open"
    assert not requests.unsettled(store, candidate)
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    assert lifecycle.reconcile(api, store, key)["status"] == "complete"
    assert len(api.comments) == 1
    assert api.issue["state"] == "closed"


@pytest.mark.parametrize("effect_name", ["comment", "close"])
def test_permission_revoked_after_intent_is_known_not_submitted(candidate, monkeypatch, effect_name):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store, key = merged_completion(candidate)
    original = store.put
    effect_key = key + "/issues/30" + ("/comment" if effect_name == "comment" else "")

    def put(path, value, revision):
        result = original(path, value, revision)
        if path == effect_key and value.get("status") == "pending":
            monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "report")
        return result

    monkeypatch.setattr(store, "put", put)
    with pytest.raises(Blocked, match="EFFECT_NOT_SUBMITTED"):
        lifecycle.reconcile(api, store, key)
    assert store.read(effect_key)[0]["status"] == "not_submitted"
    assert not requests.unsettled(store, candidate)
    assert api.issue["state"] == "open"
    monkeypatch.setattr(store, "put", original)
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    assert lifecycle.reconcile(api, store, key)["status"] == "complete"
    assert len(api.comments) == 1
    assert api.issue["state"] == "closed"


def test_completion_with_current_authority_outlives_execution_deadline(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    api, store, key = merged_completion(candidate)
    assert lifecycle.reconcile(api, store, key)["status"] == "complete"
    assert api.issue["state"] == "closed"


def test_lost_close_response_reconciles_receipt_without_second_write(candidate):
    api, store = API(candidate), MemoryStore()
    proof = evidence(api, candidate)
    api.pr.update(state="closed", merged=True)
    original = api.request

    def request(path, **kwargs):
        result = original(path, **kwargs)
        if path == "issues/3" and kwargs.get("method") == "PATCH":
            raise Blocked("GITHUB_OUTCOME_UNKNOWN")
        return result

    api.request = request
    with pytest.raises(Blocked, match="GITHUB_OUTCOME_UNKNOWN"):
        complete(api, candidate, proof, store)
    assert api.issue["state"] == "closed"
    assert requests.unsettled(store, candidate)
    before = len(api.writes)
    complete(api, candidate, proof, store)
    assert len(api.writes) == before
    assert not requests.unsettled(store, candidate)
