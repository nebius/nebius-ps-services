import pytest
from pydantic import ValidationError
from tests.support import REQUEST_ID, MemoryStore, stage_evidence

from github_ai_automation.contracts import (
    Blocked,
    Candidate,
    CIResult,
    Fragment,
    LiveResult,
    Producer,
    Review,
    Waiver,
    digest,
)
from github_ai_automation.live import require_cleanup, wait_for_quota
from github_ai_automation.policy import qualification, register_waiver
from github_ai_automation.releases import affected_projects, next_version, verify_assets
from github_ai_automation.state import acquire, effect


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
def inputs(candidate):
    values = dict(
        candidate=candidate,
        producer=Producer(
            workflow=".github/workflows/github-ai-automation.yml",
            workflow_sha="d" * 40,
            run_id=10,
            attempt=1,
            policy_sha256="e" * 64,
            skills_sha256="f" * 64,
            runtime_image="sha256:" + "f" * 64,
            model="test/model",
        ),
        review=Review(
            verdict="pass",
            findings=[],
            reviewed_paths=["src/a.py"],
            summary="Reviewed",
            compatibility="compatible",
            spec_assessment="No spec drift",
            release_fragments=[],
        ),
        live=LiveResult(
            outcome="passed",
            scenario_sha256="e" * 64,
            source_tree=candidate.tree_sha,
            assertions={"regression": True},
            cleanup_complete=True,
            reason="LIVE_PASSED",
        ),
        ci=[
            CIResult(
                request_id=REQUEST_ID,
                workflow="ci.yml",
                run_id=20,
                attempt=1,
                workflow_sha="d" * 40,
                tested_tree=candidate.tree_sha,
                conclusion="success",
            )
        ],
        waiver=None,
        paths=["src/a.py"],
        expected_ci=["ci.yml"],
        operation_id=REQUEST_ID,
        fence=1,
        expires_at=1000,
        now=100,
        mode="merge",
    )

    values["stages"] = stage_evidence(values["review"])
    return values


def test_full_evidence_authorizes_exact_candidate(inputs):
    admitted = qualification(**inputs)
    assert admitted.candidate == inputs["candidate"]
    assert admitted.review_sha256 == digest(inputs["review"])


@pytest.mark.parametrize(
    "change,code",
    [
        ({"mode": "report"}, "MERGE_NOT_ENABLED"),
        ({"now": 1000}, "OPERATION_EXPIRED"),
        ({"paths": [".github/workflows/x.yml"]}, "CONTROL_PLANE_OR_EMPTY_CHANGE"),
        ({"ci": []}, "CI_INCOMPLETE"),
        ({"expected_ci": []}, "CI_INCOMPLETE"),
        ({"live": None}, "LIVE_TEST_NOT_PASSED"),
    ],
)
def test_missing_or_forbidden_evidence_denies(inputs, change, code):
    with pytest.raises(Blocked, match=code):
        qualification(**{**inputs, **change})


@pytest.mark.parametrize("outcome", ["failed", "unavailable", "intervened"])
def test_nonpassing_live_is_never_success(inputs, outcome):
    inputs["live"] = inputs["live"].model_copy(update={"outcome": outcome})
    with pytest.raises(Blocked, match="LIVE_TEST_NOT_PASSED"):
        qualification(**inputs)


def test_waiver_survives_commits_but_not_another_pr(inputs, candidate):
    waiver = Waiver(
        repository_id=1,
        pr_id=100,
        actor_id=30,
        request_run_id=40,
        reason="Documentation-only trial",
        active=True,
        revision=1,
    )
    changed = candidate.model_copy(update={"head_sha": "f" * 40})
    assert (
        qualification(
            **{**inputs, "candidate": changed, "waiver": waiver, "live": None}
        ).waiver_revision
        == 1
    )
    with pytest.raises(Blocked, match="WAIVER_IDENTITY_MISMATCH"):
        qualification(
            **{**inputs, "candidate": changed.model_copy(update={"pr_id": 101}), "waiver": waiver}
        )


def test_waiver_never_clears_failed_cleanup(inputs):
    inputs["waiver"] = Waiver(
        repository_id=1,
        pr_id=100,
        actor_id=30,
        request_run_id=40,
        reason="Waived",
        active=True,
        revision=1,
    )
    inputs["live"] = inputs["live"].model_copy(update={"cleanup_complete": False})
    with pytest.raises(Blocked, match="CLEANUP_PENDING"):
        qualification(**inputs)


def test_historical_cleanup_blocks_even_without_current_trial(candidate):
    store = MemoryStore()
    store.put(candidate.key + "/cleanup", {"runs": ["old-trial"]}, None)
    with pytest.raises(Blocked, match="CLEANUP_PENDING"):
        require_cleanup(store, candidate)


def test_simultaneous_owners_and_expired_fence():
    store, now = MemoryStore(), [100]
    first = acquire(store, "pr/lock", clock=lambda: now[0])
    with pytest.raises(Blocked, match="OPERATION_BUSY"):
        acquire(store, "pr/lock", clock=lambda: now[0])
    now[0] = 221
    second = acquire(store, "pr/lock", clock=lambda: now[0])
    assert second.fence == first.fence + 1
    with pytest.raises(Blocked, match="LEASE_LOST"):
        first.assert_owned()
    with pytest.raises(Blocked, match="LEASE_LOST"):
        first.release()
    second.assert_owned()


def test_lost_response_reconciles_without_second_effect():
    store = MemoryStore()
    lease = acquire(store, "pr/lock")
    calls = []

    def execute(intent):
        calls.append(intent)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        effect(store, lease, "pr/merge", {"head": "a"}, lambda _: None, execute)
    assert effect(store, lease, "pr/merge", {"head": "a"}, lambda _: {"merged": True}, execute) == {
        "merged": True
    }
    assert len(calls) == 1


def test_uncertain_absence_does_not_repeat_effect():
    store = MemoryStore()
    lease = acquire(store, "pr/lock")
    store.put("pr/merge", {"intent": {"head": "a"}, "status": "pending"}, None)
    with pytest.raises(Blocked, match="EFFECT_RECONCILIATION_REQUIRED"):
        effect(
            store,
            lease,
            "pr/merge",
            {"head": "a"},
            lambda _: None,
            lambda _: pytest.fail("duplicate"),
        )


def test_bot_cannot_grant_waiver(candidate):
    store = MemoryStore()
    lease = acquire(store, candidate.key + "/lock")
    with pytest.raises(Blocked, match="WAIVER_ACTOR_UNAUTHORIZED"):
        register_waiver(
            store,
            lease,
            candidate,
            actor_id=1,
            run_id=2,
            reason="skip",
            active=True,
            permission="admin",
            actor_type="Bot",
        )


def test_revocation_waits_for_merge_owner(candidate):
    store = MemoryStore()
    lease = acquire(store, candidate.key + "/lock")
    with pytest.raises(Blocked, match="OPERATION_BUSY"):
        acquire(store, candidate.key + "/lock")
    lease.release()
    revoke = acquire(store, candidate.key + "/lock")
    assert not register_waiver(
        store,
        revoke,
        candidate,
        actor_id=1,
        run_id=2,
        reason="restore testing",
        active=False,
        permission="write",
        actor_type="User",
    ).active


def test_quota_wait_is_bounded_and_no_unsupported_request():
    with pytest.raises(Blocked, match="QUOTA_REQUEST_UNAVAILABLE"):
        wait_for_quota(lambda: False)
    now, calls = [0], []
    with pytest.raises(Blocked, match="QUOTA_NOT_AVAILABLE"):
        wait_for_quota(
            lambda: False,
            lambda: calls.append(1),
            clock=lambda: now[0],
            sleep=lambda n: now.__setitem__(0, now[0] + n),
        )
    assert now[0] == 120 and calls == [1]


@pytest.mark.parametrize(
    "bump,expected", [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]
)
def test_deterministic_version(bump, expected):
    assert next_version("1.2.3", [Fragment(project="p", bump=bump, summary="Change")]) == expected


def test_release_assets_cannot_be_redefined_from_remote():
    expected = [{"name": "p.whl", "size": 10, "sha256": "a"}]
    with pytest.raises(Blocked, match="RELEASE_ASSET_MISMATCH"):
        verify_assets(expected, [{"name": "p.whl", "size": 10, "sha256": "b"}])


def test_documentation_changes_do_not_release():
    registry = {
        "projects": {"p": {"include": ["p/*"], "exclude": ["p/docs/*", "p/tests/*", "**/*.md"]}}
    }
    assert affected_projects(["p/docs/guide.md", "p/tests/test_x.py"], registry) == []
    assert affected_projects(["p/src/x.py"], registry) == ["p"]


@pytest.mark.parametrize("value", [True, 0, -1, "1"])
def test_candidate_identity_requires_positive_integer(candidate, value):
    with pytest.raises(ValidationError):
        Candidate.model_validate({**candidate.model_dump(), "pr_id": value})


@pytest.mark.parametrize(
    "outcome,assertions",
    [
        ("failed", {"regression": False}),
        ("intervened", {"regression": True}),
        ("passed", {"regression": False}),
        ("passed", {}),
    ],
)
def test_waiver_cannot_override_observed_failure_or_inconsistent_pass(inputs, outcome, assertions):
    inputs["waiver"] = Waiver(
        repository_id=1,
        pr_id=100,
        actor_id=30,
        request_run_id=40,
        reason="Missing validation",
        active=True,
        revision=1,
    )
    inputs["live"] = inputs["live"].model_copy(
        update={"outcome": outcome, "assertions": assertions}
    )
    with pytest.raises(Blocked, match="LIVE_TEST_NOT_PASSED"):
        qualification(**inputs)
