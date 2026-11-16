import json
from pathlib import Path

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import closure, events, repairs, requests, sandbox
from github_ai_automation.contracts import Blocked, Candidate, digest
from github_ai_automation.state import acquire


@pytest.fixture
def candidate():
    return Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="contributor/repo",
        head_repository_id=9,
        head_ref="main",
        pr_id=100,
        pr_number=2,
        head_sha="a" * 40,
        base_sha="b" * 40,
        tree_sha="c" * 40,
    )


def test_fork_main_can_never_reach_branch_publication(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.setenv("AI_AUTOMATION_MERGE_FORK_CONTRIBUTION", "true")
    store = MemoryStore()
    admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")

    class API:
        def request(self, *args, **kwargs):
            raise AssertionError("Fork branch write attempted")

    with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
        events.publish_patch(API(), store, lease, "missing", candidate, REQUEST_ID, Path("."))


@pytest.mark.parametrize("human_close", [False, True])
def test_closure_race_compensates_only_its_own_close(candidate, monkeypatch, human_close):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    store = MemoryStore()
    admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")

    class API:
        changed = False
        state = "open"
        writes = []

        def candidate(self, number):
            return candidate, {"state": self.state, "merged": False}

        def comment(self, *args, **kwargs):
            pass

        def pages(self, path):
            if not self.changed:
                return []
            return [
                {
                    "id": 1,
                    "event": "closed",
                    "actor": {"id": 7 if human_close else 41898282},
                    "created_at": "stamp",
                }
            ]

        def request(self, path, *, method="GET", body=None):
            self.writes.append(body["state"])
            self.state = body["state"]
            self.changed = True  # Human explanation changed without a new commit.
            return {"state": self.state, "closed_at": "stamp"}

    api = API()
    store.put("closure-stage/value", {"result": {"explanation": "No concrete benefit"}}, None)
    monkeypatch.setattr(closure, "validate_proof", lambda *_: not api.changed)
    code = (
        "CLOSURE_OUTCOME_RECONCILIATION_REQUIRED"
        if human_close
        else "CLOSURE_HEAD_CHANGED_REOPENED"
    )
    with pytest.raises(Blocked, match=code):
        closure.close(
            api,
            store,
            lease,
            candidate,
            {"kind": "no_benefit", "stage_key": "closure-stage"},
            REQUEST_ID,
        )
    assert api.writes == (["closed"] if human_close else ["closed", "open"])
    from github_ai_automation import requests

    assert requests.unsettled(store, candidate) is human_close
    if not human_close:
        requests.reconcile_effects(api, store, lease, candidate)


def test_no_benefit_proof_invalidated_by_new_explanation(candidate, monkeypatch):
    monkeypatch.setattr(
        "github_ai_automation.stages.human_context",
        lambda *_: {"body": "New benefit", "reviews": []},
    )

    class API:
        def request(self, path):
            return {}

    proof = {
        "kind": "no_benefit",
        "stage_key": candidate.key + "/requests/" + REQUEST_ID + "/stages/a",
        "rationale_sha256": "0" * 64,
    }
    assert not closure.validate_proof(API(), MemoryStore(), candidate, proof, REQUEST_ID)


def test_conflict_scope_ignores_clean_base_changes_but_bounds_agent_delta(tmp_path):
    (tmp_path / "product.py").write_text(
        "<<<<<<< head\nvalue = 1\n=======\nvalue = 2\n>>>>>>> base\n"
    )
    (tmp_path / "base.py").write_text("base = 1\n" * 1000)
    before, conflicts = repairs.conflict_basis(tmp_path)
    (tmp_path / "product.py").write_text("value = 3\n")
    repairs.conflict_scope(tmp_path, before, conflicts)
    (tmp_path / "base.py").write_text("base = 2\n")
    with pytest.raises(Blocked, match="CONFLICT_REPAIR_UNRELATED"):
        repairs.conflict_scope(tmp_path, before, conflicts)


def test_conflict_scope_rejects_large_resolution(tmp_path):
    (tmp_path / "product.py").write_text("<<<<<<< head\na\n=======\nb\n>>>>>>> base\n")
    before, conflicts = repairs.conflict_basis(tmp_path)
    (tmp_path / "product.py").write_text("new\n" * 201)
    with pytest.raises(Blocked, match="CONFLICT_REPAIR_TOO_LARGE"):
        repairs.conflict_scope(tmp_path, before, conflicts)


def test_validation_container_has_no_credentials_or_network(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("GH_TOKEN", "private-test-value")
    monkeypatch.setenv("AI_AUTOMATION_TF_API_KEY", "private-test-value")
    monkeypatch.setattr(
        sandbox, "command", lambda args, **kwargs: calls.append((args, kwargs)) or b"ok"
    )
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *args, **kwargs: None)
    receipts = sandbox.run_checks(
        image="sha256:" + "a" * 64, source=tmp_path, paths=["product.py"], profiles=[], timeout=30
    )
    args, kw = calls[0]
    assert "--network=none" in args and "--read-only" in args and "--cap-drop=ALL" in args
    assert ",readonly" in " ".join(args)
    assert set(kw["env"]) == {"PATH"}
    assert "private-test-value" not in json.dumps(calls)
    assert receipts[0]["exit_code"] == 0


def test_unrequested_controller_artifact_has_no_effects(candidate, monkeypatch, tmp_path):
    monkeypatch.setattr(
        events,
        "completed_run",
        lambda *a, **k: {
            "path": ".github/workflows/github-ai-automation.yml",
            "conclusion": "success",
            "run_attempt": 1,
        },
    )

    class API:
        def artifact(self, *args):
            return {
                "pr_number": 2,
                "request_id": REQUEST_ID,
                "candidate": candidate.model_dump(),
                "operation": "process",
                "code": "MERGE_ELIGIBLE",
            }

        def candidate(self, number):
            return candidate, {"state": "open", "draft": False}

        def request(self, *args, **kwargs):
            raise AssertionError("Unadmitted effect")

    with pytest.raises(Blocked, match="REQUEST_NOT_ACTIVE"):
        events.continue_run(API(), MemoryStore(), tmp_path, 10)


def test_fork_notice_and_label_are_updated_idempotently(candidate, monkeypatch):
    from github_ai_automation.github import GitHub

    monkeypatch.setenv("AI_AUTOMATION_MERGE_FORK_CONTRIBUTION", "false")

    class API(GitHub):
        def __init__(self):
            self.comments, self.labels = [], []
            self.created = False

        def pages(self, path):
            return self.comments if path.endswith("comments") else self.labels

        def request(self, path, *, method="GET", body=None, absent=False):
            if path.startswith("issues/comments/") and method == "GET":
                return self.comments[0]
            if path.startswith("pulls/"):
                return {"head": {"repo": {"id": 9}}, "base": {"repo": {"id": 1}}}
            if path == "labels/source%3Afork":
                return {"name": "source:fork"} if self.created else None
            if path == "labels":
                self.created = True
                return
            if path.endswith("/labels"):
                self.labels = [{"name": value} for value in body["labels"]]
                return
            if method == "POST":
                self.comments.append(
                    {
                        "id": 1,
                        "user": {"login": "github-actions[bot]", "id": 41898282},
                        "body": body["body"],
                    }
                )
            else:
                self.comments[0]["body"] = body["body"]
            return self.comments[0]

    api = API()
    api.mark_fork(candidate)
    api.comment(2, "REVIEW_COMPLETE", detail="Useful change")
    api.mark_fork(candidate)
    api.comment(2, "CONTRIBUTOR_BASE_UPDATE_REQUIRED", detail="Update branch")
    assert len(api.comments) == len(api.labels) == 1
    assert "Fork contribution" in api.comments[0]["body"] and "disabled" in api.comments[0]["body"]
    monkeypatch.setenv("AI_AUTOMATION_MERGE_FORK_CONTRIBUTION", "true")
    api.comment(2, "REVIEW_COMPLETE")
    assert "every required gate passes" in api.comments[0]["body"]


def test_unconfirmed_effect_has_no_transient_success_journal():
    from github_ai_automation.state import Unconfirmed, effect

    class AuditedStore(MemoryStore):
        statuses = []

        def put(self, key, value, expected):
            if key == "pr/merge":
                self.statuses.append(value["status"])
            return super().put(key, value, expected)

    store = AuditedStore()
    lease = acquire(store, "pr/lock")
    assert effect(
        store,
        lease,
        "pr/merge",
        {"head": "a" * 40},
        lambda _: None,
        lambda _: Unconfirmed({"outcome": "requested"}),
    ) == {"outcome": "requested"}
    assert store.statuses == ["pending", "pending"]
    with pytest.raises(Blocked, match="EFFECT_RECONCILIATION_REQUIRED"):
        effect(
            store,
            lease,
            "pr/merge",
            {"head": "a" * 40},
            lambda _: None,
            lambda _: pytest.fail("Repeated submission"),
        )


@pytest.mark.parametrize("expire", [False, True])
def test_accepted_base_sync_remains_pending_until_observed(
    candidate, monkeypatch, tmp_path, expire
):
    candidate = candidate.model_copy(
        update={"head_repository": "owner/repo", "head_repository_id": 1, "head_ref": "feature"}
    )
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.setattr(
        events,
        "completed_run",
        lambda *a, **k: {
            "path": ".github/workflows/github-ai-automation.yml",
            "conclusion": "success",
            "run_attempt": 1,
        },
    )
    monkeypatch.setattr(events, "read_policy", lambda _: {"max_fix_attempts": 2})
    store = MemoryStore()
    monkeypatch.setattr(
        "github_ai_automation.lifecycle.publish",
        lambda api, candidate, code, detail="", **kw: api.comment(
            candidate.pr_number, code, detail=detail
        ),
    )
    original = admitted(store, candidate)

    class API:
        code = "BASE_SYNC_REQUIRED"
        writes = 0

        def __init__(self):
            self.value = candidate

        def artifact(self, *args):
            return {
                "pr_number": 2,
                "request_id": REQUEST_ID,
                "candidate": self.value.model_dump(),
                "operation": "process",
                "code": self.code,
            }

        def candidate(self, number):
            return self.value, {"state": "open", "draft": False}

        def mark_fork(self, _):
            pass

        def comment(self, *args, **kwargs):
            pass

        def dispatch(self, *args):
            pass

        def request(self, path, *, method="GET", body=None):
            if method == "PUT":
                assert path == "pulls/2/update-branch"
                assert body == {"expected_head_sha": candidate.head_sha}
                self.writes += 1
                return {"message": "Updating pull request branch."}
            assert path == "git/commits/" + self.value.head_sha
            parents = (
                [candidate.head_sha, candidate.base_sha] if self.value != candidate else ["0" * 40]
            )
            return {"parents": [{"sha": sha} for sha in parents]}

    api = API()
    events.continue_run(api, store, tmp_path, 10)
    key = candidate.key + "/requests/" + REQUEST_ID + "/base-sync/" + digest(candidate)
    assert store.read(key)[0]["status"] == "pending"
    with pytest.raises(Blocked, match="EFFECT_RECONCILIATION_REQUIRED"):
        events.continue_run(api, store, tmp_path, 10)
    assert api.writes == 1

    # The controller's immediate retry can arrive before GitHub finishes the update.
    api.code = "PR_EFFECT_RECONCILIATION_REQUIRED"
    events.continue_run(api, store, tmp_path, 11)
    record = requests.active(store, candidate, REQUEST_ID)
    assert record["deadline"] == original["deadline"] and record["attempts"] == 1

    lease = acquire(store, candidate.key + "/lock")
    if expire:
        requests.finish(store, lease, candidate, REQUEST_ID, "EXPIRED")
        lease.release()
        env = {"GITHUB_RUN_ID": "12", "AI_AUTOMATION_PR_REVIEW": "merge"}
        with pytest.raises(Blocked, match="PR_EFFECT_RECONCILIATION_REQUIRED"):
            requests.register(api, store, candidate, {}, "process", "manual", env)
        api.value = candidate.model_copy(update={"head_sha": "d" * 40, "tree_sha": "e" * 40})
        successor = requests.register(api, store, api.value, {}, "process", "manual", env)
        assert successor["id"] != REQUEST_ID
    else:
        api.value = candidate.model_copy(update={"head_sha": "d" * 40, "tree_sha": "e" * 40})
        try:
            requests.reconcile_effects(api, store, lease, api.value)
            resumed = requests.active(store, api.value, REQUEST_ID)
            assert resumed["deadline"] == original["deadline"] and resumed["attempts"] == 1
        finally:
            lease.release()
    assert store.read(key)[0]["status"] == "observed"
    assert api.writes == 1


@pytest.mark.parametrize("change", ["head", "base", "draft", "closed", "none"])
def test_terminal_report_rechecks_candidate_after_comment(candidate, monkeypatch, change):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    store = MemoryStore()
    monkeypatch.setattr(
        "github_ai_automation.lifecycle.publish",
        lambda api, candidate, code, detail="", **kw: api.comment(
            candidate.pr_number, code, detail=detail
        ),
    )
    original = admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")

    class API:
        commented = False

        def candidate(self, number):
            value = candidate
            pr = {"state": "open", "draft": False}
            if self.commented:
                if change in {"head", "base"}:
                    value = candidate.model_copy(update={change + "_sha": "d" * 40})
                elif change == "closed":
                    pr["state"] = "closed"
                elif change == "draft":
                    pr["draft"] = True
            return value, pr

        def mark_fork(self, _):
            pass

        def comment(self, *args, **kwargs):
            self.commented = True

    api = API()
    try:
        if change == "none":
            events.report(api, store, lease, candidate, REQUEST_ID, "REVIEW_COMPLETE")
            assert store.read(candidate.key + "/operation")[0]["status"] == "terminal"
        else:
            with pytest.raises(Blocked, match="PR_SNAPSHOT_CHANGED"):
                events.report(api, store, lease, candidate, REQUEST_ID, "REVIEW_COMPLETE")
            current = requests.active(store, candidate, REQUEST_ID)
            assert current["deadline"] == original["deadline"]
            assert current["attempts"] == original["attempts"]
    finally:
        lease.release()
