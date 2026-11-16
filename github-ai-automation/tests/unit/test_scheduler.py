import time

import pytest
from tests.support import MemoryStore

from github_ai_automation import scheduler
from github_ai_automation.contracts import Blocked


def request(number):
    return {
        "id": str(number) * 64,
        "candidate": {
            "repository": "owner/repo",
            "repository_id": 1,
            "head_repository": "owner/repo",
            "head_repository_id": 1,
            "head_ref": "feature",
            "pr_id": number,
            "pr_number": number,
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "tree_sha": "c" * 40,
        },
        "stage": "usefulness",
        "operation": "process",
        "attempts": 0,
        "rollout_generation": 1,
        "deadline": int(time.time()) + 3600,
    }


def test_three_requests_never_reserve_three_evaluators():
    store = MemoryStore()
    entries = [scheduler.enqueue(store, request(n)) for n in (1, 2, 3)]
    assert scheduler.reserve(store, 1, entries[0], run_id=10, attempt=1)["slot"] == 0
    assert scheduler.reserve(store, 1, entries[1], run_id=11, attempt=1)["slot"] == 1
    assert scheduler.reserve(store, 1, entries[2]) is None
    scheduler.terminal(store, 1, entries[0]["nonce"], 10, 1)
    assert scheduler.reserve(store, 1, entries[2])["slot"] == 0


def test_expired_heartbeat_and_unknown_dispatch_hold_capacity():
    store = MemoryStore()
    for n in (1, 2):
        scheduler.reserve(store, 1, scheduler.enqueue(store, request(n)), now=1)
    assert scheduler.reserve(store, 1, scheduler.enqueue(store, request(3))) is None


def test_duplicate_enqueue_and_dispatch_reservation_are_single_owner():
    store = MemoryStore()
    entry = scheduler.enqueue(store, request(1))
    assert scheduler.enqueue(store, request(1))["nonce"] == entry["nonce"]
    first = scheduler.reserve(store, 1, entry, dispatcher="first")
    second = scheduler.reserve(store, 1, entry, dispatcher="second")
    assert first == second
    assert second["dispatcher"] == "first"


def test_stale_run_attempt_cannot_renew_or_release():
    store = MemoryStore()
    entry = scheduler.enqueue(store, request(1))
    scheduler.reserve(store, 1, entry)
    scheduler.bind(store, 1, entry["nonce"], 10, 1)
    for run, attempt in [(11, 1), (10, 2)]:
        with pytest.raises(Blocked, match="EVALUATION_RUN_CHANGED"):
            scheduler.bind(store, 1, entry["nonce"], run, attempt)
        with pytest.raises(Blocked, match="EVALUATION_RUN_CHANGED"):
            scheduler.terminal(store, 1, entry["nonce"], run, attempt)
    assert store.read(scheduler.prefix(1) + "/slots")[0]["slots"][0] is not None


def test_cancel_request_does_not_free_slot():
    store = MemoryStore()
    entry = scheduler.enqueue(store, request(1))
    scheduler.reserve(store, 1, entry, run_id=10, attempt=1, now=1)

    class API:
        cancelled = False

        def request(self, path, **kwargs):
            if not path:
                return {"id": 1, "default_branch": "main"}
            if path.endswith("/cancel"):
                self.cancelled = True
                return None
            return {
                "repository": {"id": 1},
                "path": ".github/workflows/github-ai-automation.yml",
                "event": "workflow_dispatch",
                "head_branch": "main",
                "run_attempt": 1,
                "status": "in_progress",
            }

    api = API()
    scheduler.reconcile(api, store, 1)
    assert api.cancelled
    assert store.read(scheduler.prefix(1) + "/slots")[0]["slots"][0] is not None


def test_invalid_oldest_request_does_not_starve_next_candidate(monkeypatch):
    from github_ai_automation import requests
    from github_ai_automation.contracts import Candidate

    store = MemoryStore()
    first, second = [scheduler.enqueue(store, request(n)) for n in (1, 2)]
    dispatched = []

    class API:
        def candidate(self, number):
            if number == 1:
                raise Blocked("PR_SOURCE_UNAVAILABLE")
            return Candidate.model_validate(second["candidate"]), {"state": "open", "draft": False}

        def dispatch(self, workflow, inputs):
            dispatched.append(inputs["pr_number"])

    monkeypatch.setattr(requests, "require_capability", lambda *_: None)
    scheduler.pump(API(), store, 1)
    assert dispatched == ["2"]
    assert store.read(scheduler.prefix(1) + "/queue/" + first["id"])[0]["status"] == "complete"
    assert store.read(scheduler.prefix(1) + "/slots")[0]["slots"][0]["id"] == second["id"]
