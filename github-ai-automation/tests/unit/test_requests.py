from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support import MemoryStore, general

from github_ai_automation import requests
from github_ai_automation.contracts import Blocked, Candidate
from github_ai_automation.recovery import recover
from github_ai_automation.state import acquire


def test_recovery_never_admits_the_open_backlog(monkeypatch):
    class API:
        def request(self, path):
            return {"id": 1, "default_branch": "main"}

        def pages(self, path):
            return [{"number": 42, "draft": False, "head": {"repo": {"id": 1}}}]

        def candidate(self, number):
            return SimpleNamespace(key="repositories/1/prs/42"), {}

        def dispatch(self, *args):
            raise AssertionError("Unrequested PR was dispatched")

    for key, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REF": "refs/heads/main",
        "AI_AUTOMATION_REPOSITORY_ID": "1",
        "AI_AUTOMATION_PR_REVIEW": "merge",
        "AI_AUTOMATION_RELEASE_PUBLISH": "false",
    }.items():
        monkeypatch.setenv(key, value)
    recover(API(), MemoryStore(), Path(__file__).resolve().parents[3])


@pytest.fixture
def candidate():
    return Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="owner/repo",
        head_repository_id=1,
        head_ref="feature",
        pr_id=100,
        pr_number=2,
        head_sha="a" * 40,
        base_sha="b" * 40,
        tree_sha="c" * 40,
    )


@pytest.fixture
def env():
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "owner/repo",
        "AI_AUTOMATION_REPOSITORY_ID": "1",
        "AI_AUTOMATION_PR_REVIEW": "merge",
        "AI_AUTOMATION_AUTOMATIC_SINCE": "2026-01-01T00:00:00Z",
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR_ID": "7",
    }


class IntakeAPI:
    repository = "owner/repo"

    def __init__(self, candidate, draft=False):
        self.value = candidate
        self.pr = {
            "id": candidate.pr_id,
            "state": "open",
            "draft": draft,
            "created_at": "2026-02-01T00:00:00Z",
        }

    def request(self, path):
        assert path == ""
        return {"id": 1, "default_branch": "main"}

    def candidate(self, number):
        return self.value, self.pr

    def permission(self, login):
        return "write"

    def event(self, action):
        return {"number": 2, "action": action, "pull_request": self.pr}


def terminal(store, candidate, record):
    lease = acquire(store, candidate.key + "/lock")
    try:
        requests.finish(store, lease, candidate, record["id"], "LEFT_OPEN")
    finally:
        lease.release()


def test_old_open_prs_and_old_events_never_enroll(candidate, env):
    api, store = IntakeAPI(candidate), MemoryStore()
    for event in ("synchronize", "reopened", "ready_for_review"):
        assert requests.intake(api, store, api.event(event), env) is None
    api.pr["created_at"] = "2025-12-31T00:00:00Z"
    assert requests.intake(api, store, api.event("opened"), env) is None
    assert not store.data


def test_terminal_request_requires_new_manual_dispatch(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    record = requests.intake(api, store, api.event("opened"), env)
    terminal(store, candidate, record)
    for action in ("opened", "synchronize", "reopened", "ready_for_review"):
        assert requests.intake(api, store, api.event(action), env) is None
    event = {"sender": {"id": 7, "type": "User", "login": "maintainer"}}
    new = requests.admit_manual(
        api, store, candidate, api.pr, "process", event, {**env, "GITHUB_RUN_ID": "11"}
    )
    assert new["id"] != record["id"]
    assert new["attempts"] == 0


def test_active_head_changes_preserve_deadline_budget_and_authority(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    record = requests.intake(api, store, api.event("opened"), env)
    current, version = store.read(candidate.key + "/operation")
    current["attempts"] = 2
    store.put(candidate.key + "/operation", current, version)
    api.value = candidate.model_copy(update={"head_sha": "d" * 40, "base_sha": "e" * 40})
    updated = requests.intake(api, store, api.event("synchronize"), {**env, "GITHUB_RUN_ID": "11"})
    assert updated["id"] == record["id"] and updated["deadline"] == record["deadline"]
    assert updated["attempts"] == 2


def test_draft_deadline_starts_only_when_ready(candidate, env):
    api, store = IntakeAPI(candidate, draft=True), general(MemoryStore())
    assert requests.intake(api, store, api.event("opened"), env) is None
    draft, _ = store.read(candidate.key + "/operation")
    assert draft["status"] == "awaiting_ready" and draft["deadline"] == 0
    api.pr["draft"] = False
    ready = requests.intake(api, store, api.event("ready_for_review"), env)
    assert ready["id"] == draft["id"] and ready["deadline"] > 0


def test_old_epoch_draft_needs_manual_request(candidate, env):
    api, store = IntakeAPI(candidate, draft=True), general(MemoryStore())
    requests.intake(api, store, api.event("opened"), env)
    api.pr["draft"] = False
    env[requests.EPOCH] = "2026-03-01T00:00:00Z"
    assert requests.intake(api, store, api.event("ready_for_review"), env) is None
    record = requests.register(
        api, store, candidate, api.pr, "process", "manual", {**env, "GITHUB_RUN_ID": "11"}
    )
    assert record["origin"] == "manual"


def test_reruns_never_create_successors_even_for_coalesced_dispatch(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    first = requests.register(api, store, candidate, api.pr, "process", "manual", env)
    other = {**env, "GITHUB_RUN_ID": "11"}
    assert requests.register(api, store, candidate, api.pr, "process", "manual", other) == first
    terminal(store, candidate, first)
    for run in (env, other):
        assert (
            requests.register(
                api,
                store,
                candidate,
                api.pr,
                "process",
                "manual",
                {**run, "GITHUB_RUN_ATTEMPT": "2"},
            )
            is None
        )


def test_review_cannot_escalate_to_process(candidate, env):
    store = general(MemoryStore())
    requests.register(None, store, candidate, {}, "review", "manual", env)
    with pytest.raises(Blocked, match="REQUEST_OPERATION_CHANGED"):
        requests.register(
            None, store, candidate, {}, "process", "manual", {**env, "GITHUB_RUN_ID": "11"}
        )


@pytest.mark.parametrize(
    ("mode", "allowed"),
    [
        (None, set()),
        ("disabled", set()),
        ("true", set()),
        ("report", {"assess", "comment", "label"}),
        ("repair", {"adopt", "assess", "comment", "label", "close", "repair", "qualify"}),
        ("merge", {"adopt", "assess", "comment", "label", "close", "repair", "qualify", "merge"}),
    ],
)
def test_pr_control_preserves_mode_authority(candidate, mode, allowed):
    env = {"AI_AUTOMATION_RELEASE_PUBLISH": "true"}
    if mode is not None:
        env["AI_AUTOMATION_PR_REVIEW"] = mode
    assert set(requests.capabilities(candidate, "process", env)) == allowed


@pytest.mark.parametrize("value", ["True", "FALSE", "1", "yes", " false", "false "])
def test_fork_flag_is_strict(value):
    with pytest.raises(Blocked, match="FORK_MERGE_POLICY_INVALID"):
        requests.fork_merging({requests.FORK_FLAG: value})


def test_fork_capability_ceiling_and_revocation(candidate, env):
    fork = candidate.model_copy(
        update={"head_repository": "contributor/repo", "head_repository_id": 9, "head_ref": "main"}
    )
    store = general(MemoryStore())
    record = requests.register(None, store, fork, {}, "process", "manual", env)
    assert "close" in record["capabilities"]
    for cap in ("merge", "qualify", "repair"):
        with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
            requests.require_capability(
                store, fork, record["id"], cap, env={**env, requests.FORK_FLAG: "true"}
            )
    terminal(store, fork, record)
    enabled = {**env, "GITHUB_RUN_ID": "11", requests.FORK_FLAG: "true"}
    record = requests.register(None, store, fork, {}, "process", "manual", enabled)
    requests.require_capability(store, fork, record["id"], "merge", env=enabled)
    with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
        requests.require_capability(store, fork, record["id"], "merge", env=env)
    with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
        requests.require_capability(store, fork, record["id"], "repair", env=enabled)


def test_legacy_state_never_becomes_active_implicitly(candidate, env):
    store = general(MemoryStore())
    store.put(candidate.key + "/operation", {"id": "old", "deadline": 1}, None)
    with pytest.raises(Blocked, match="LEGACY_OPERATION_RECONCILIATION_REQUIRED"):
        requests.register(None, store, candidate, {}, "process", "manual", env)


def test_interrupted_history_archival_is_idempotent(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    record = requests.register(api, store, candidate, api.pr, "process", "manual", env)
    terminal(store, candidate, record)
    current, _ = store.read(candidate.key + "/operation")
    # Emulate a crash after archive publication, before replacing current.
    store.put(candidate.key + "/requests/" + record["id"], current, None)
    successor = requests.register(
        api, store, candidate, api.pr, "process", "manual", {**env, "GITHUB_RUN_ID": "11"}
    )
    assert successor["id"] != record["id"]


def test_uncertain_effect_blocks_successor_until_exact_observation(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    record = requests.register(api, store, candidate, api.pr, "process", "manual", env)
    terminal(store, candidate, record)
    key = candidate.key + "/requests/" + record["id"] + "/runs/10_1/push"
    store.put(key, {"status": "pending", "intent": {"after": "d" * 40}}, None)
    with pytest.raises(Blocked, match="PR_EFFECT_RECONCILIATION_REQUIRED"):
        requests.register(
            api, store, candidate, api.pr, "process", "manual", {**env, "GITHUB_RUN_ID": "11"}
        )
    api.value = candidate.model_copy(update={"head_sha": "d" * 40})
    successor = requests.register(
        api, store, api.value, api.pr, "process", "manual", {**env, "GITHUB_RUN_ID": "11"}
    )
    assert successor["id"] != record["id"]
    assert store.read(key)[0]["status"] == "observed"


def test_legacy_retirement_requires_disabled_mode_and_keeps_history(candidate, env):
    api, store = IntakeAPI(candidate), general(MemoryStore())
    api.pages = lambda *a, **kw: []
    legacy = {"id": "old", "deadline": 1, "attempts": 2}
    store.put(candidate.key + "/operation", legacy, None)
    event = {"sender": {"id": 7, "type": "User", "login": "maintainer"}}
    with pytest.raises(Blocked, match="LEGACY_RETIREMENT_REQUIRES_DISABLED_MODE"):
        requests.retire_legacy(api, store, candidate, event, env)
    requests.retire_legacy(
        api, store, candidate, event, {**env, "AI_AUTOMATION_PR_REVIEW": "disabled"}
    )
    current, _ = store.read(candidate.key + "/operation")
    assert current["status"] == "terminal" and current["capabilities"] == []
    assert store.read(candidate.key + "/legacy/" + requests.digest(legacy))[0] == legacy
