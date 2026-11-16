import time

import pytest
from tests.support import MemoryStore, admitted

from github_ai_automation import requests, rollout
from github_ai_automation.contracts import Blocked, Candidate, digest


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


def scope(store, candidate, **updates):
    value = {
        "schema_version": 1,
        "repository_id": 1,
        "generation": 1,
        "phase": "qualification",
        "prs": [
            {
                "candidate": candidate.model_dump(),
                "capabilities": ["assess", "comment", "qualify", "merge"],
                "expires_at": int(time.time()) + 1000,
            }
        ],
        "releases": [],
        **updates,
    }
    _, revision = store.read(rollout.key(1))
    store.put(rollout.key(1), value, revision)
    return value


def test_unconfigured_store_never_admits_production(candidate):
    with pytest.raises(Blocked, match="ROLLOUT_NOT_CONFIGURED"):
        rollout.pr_scope(MemoryStore(), candidate)


def test_canary_blocks_unrelated_old_and_retargeted_candidates(candidate):
    store = MemoryStore()
    scope(store, candidate)
    assert rollout.pr_scope(store, candidate, capability="merge") == 1
    for field, value in [
        ("pr_id", 10),
        ("head_sha", "d" * 40),
        ("base_sha", "e" * 40),
        ("head_repository_id", 11),
        ("head_ref", "other"),
    ]:
        with pytest.raises(Blocked, match="ROLLOUT_CANDIDATE_NOT_ADMITTED"):
            rollout.pr_scope(store, candidate.model_copy(update={field: value}))
    with pytest.raises(Blocked, match="ROLLOUT_AUTOMATIC_INTAKE_DISABLED"):
        rollout.pr_scope(store, candidate, automatic=True)
    with pytest.raises(Blocked, match="ROLLOUT_CAPABILITY_DENIED"):
        rollout.pr_scope(store, candidate, capability="repair")


def test_delayed_callbacks_and_current_controls_cannot_bypass_permit(candidate):
    store = MemoryStore()
    record = admitted(store, candidate)
    scope(store, candidate)
    with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
        requests.require_capability(store, candidate, record["id"], "merge", env={})
    requests.require_capability(
        store, candidate, record["id"], "merge", env={"AI_AUTOMATION_PR_REVIEW": "merge"}
    )
    scope(store, candidate, generation=2)
    with pytest.raises(Blocked, match="ROLLOUT_GENERATION_CHANGED"):
        requests.require_capability(
            store, candidate, record["id"], "merge", env={"AI_AUTOMATION_PR_REVIEW": "merge"}
        )


def test_expired_canary_fails_before_effect(candidate):
    store = MemoryStore()
    value = scope(store, candidate)
    with pytest.raises(Blocked, match="ROLLOUT_CANDIDATE_NOT_ADMITTED"):
        rollout.pr_scope(store, candidate, now=value["prs"][0]["expires_at"])


def test_release_requires_distinct_exact_manifest_permit(candidate):
    store = MemoryStore()
    permit = {
        "project": "vpngw",
        "source_sha": "a" * 40,
        "version": "0.6.5",
        "expires_at": int(time.time()) + 1000,
    }
    scope(store, candidate, releases=[permit])
    assert rollout.release_scope(store, 1, "vpngw", "a" * 40, version="0.6.5") == 1
    manifest = [{"name": "test.whl", "sha256": "f" * 64, "size": 3}]
    with pytest.raises(Blocked, match="ROLLOUT_RELEASE_MANIFEST_NOT_ADMITTED"):
        rollout.release_scope(store, 1, "vpngw", "a" * 40, generation=1, manifest=manifest)
    scope(
        store,
        candidate,
        generation=2,
        releases=[{**permit, "manifest_sha256": digest(manifest), "preparation_generation": 1}],
    )
    assert rollout.release_scope(store, 1, "vpngw", "a" * 40, generation=1, manifest=manifest) == 2
    with pytest.raises(Blocked, match="ROLLOUT_PREPARATION_CHANGED"):
        rollout.release_scope(store, 1, "vpngw", "a" * 40, generation=2, manifest=manifest)
    with pytest.raises(Blocked, match="ROLLOUT_RELEASE_NOT_ADMITTED"):
        rollout.release_scope(store, 1, "nebius-cxcli", "a" * 40)
