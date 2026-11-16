import copy
from pathlib import Path

import pytest
from tests.support import MemoryStore, admitted, stage_evidence

from github_ai_automation import execution, live_slot, scheduler
from github_ai_automation.contracts import Blocked, Candidate, Producer, Review, digest
from github_ai_automation.policy import policy_digest
from github_ai_automation.state import acquire


@pytest.fixture
def prepared(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    candidate = Candidate(
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
    store = MemoryStore()
    request = admitted(store, candidate)
    env = {
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "d" * 40,
        "AI_AUTOMATION_PR_REVIEW": "merge",
    }
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.setattr(execution, "trusted_runtime", lambda *_: {"id": 1})
    monkeypatch.setattr("github_ai_automation.bundle.package_skills", lambda *_: "e" * 64)
    producer = Producer(
        workflow=".github/workflows/github-ai-automation.yml",
        workflow_sha="d" * 40,
        run_id=10,
        attempt=1,
        policy_sha256=policy_digest(root),
        skills_sha256="e" * 64,
        runtime_image="sha256:" + "f" * 64,
        model="test/model",
    )
    entry = scheduler.enqueue(store, request)
    scheduler.reserve(store, 1, entry, run_id=10, attempt=1)
    value = {
        "issues": [],
        "schema": "evaluation-input/v2",
        "candidate": candidate.model_dump(),
        "request_id": request["id"],
        "operation": "process",
        "nonce": entry["nonce"],
        "run_id": 10,
        "attempt": 1,
        "workflow_sha": "d" * 40,
        "policy_sha256": producer.policy_sha256,
        "state": {candidate.key + "/operation": request},
        "settings": {
            "AI_AUTOMATION_AGENT_IMAGE": producer.runtime_image,
            "TF_MODEL_REVIEW": producer.model,
        },
    }
    key = execution.execution_key(1, 10, 1)
    store.put(key, {"input": value, "input_sha256": digest(value), "status": "prepared"}, None)
    identity = {
        "candidate": candidate.model_dump(),
        "request_id": request["id"],
        "policy": producer.policy_sha256,
        "skills": producer.skills_sha256,
        "image": producer.runtime_image,
        "model": producer.model,
        "workflow_sha": producer.workflow_sha,
        "context_sha256": "1" * 64,
    }
    stage_key = candidate.key + "/requests/" + request["id"] + "/stages/" + digest(identity)
    review = Review(
        verdict="pass",
        findings=[],
        reviewed_paths=["src/a.py"],
        summary="Pass",
        compatibility="compatible",
        spec_assessment="Aligned",
        release_fragments=[],
    )
    checks = stage_evidence(review)
    rows = {
        "value": checks["usefulness"],
        "review": checks["review"],
        "alignment": checks["alignment"]["assessment"],
        "checks_" + "2" * 64: checks["alignment"]["checks"],
    }
    evidence = {
        stage_key + "/" + name: {
            "identity": identity,
            "producer": producer.model_dump(),
            "result": data,
        }
        for name, data in rows.items()
    }
    record_path = candidate.key + "/requests/" + request["id"] + "/runs/10_1"
    result = {
        "candidate": candidate.model_dump(),
        "request_id": request["id"],
        "operation": "process",
        "code": "ASSESSMENT_READY",
        "record": record_path,
        "stage_key": stage_key,
        "producer": producer.model_dump(),
        "checks": checks,
        "scenario": None,
    }
    output = {
        "schema": "evaluation-output/v2",
        "input_sha256": digest(value),
        "nonce": entry["nonce"],
        "result": result,
        "evidence": evidence,
    }

    class API:
        repository = "owner/repo"

        def graphql(self, *args):
            return {
                "repository": {
                    "pullRequest": {
                        "closingIssuesReferences": {"nodes": [], "pageInfo": {"hasNextPage": False}}
                    }
                }
            }

        def request(self, path):
            if path.startswith("pulls/"):
                return {"user": {"id": 2}}
            raise AssertionError(path)

        def candidate(self, _):
            return candidate, {"state": "open", "draft": False}

        def pages(self, path, **kwargs):
            return [{"name": "evaluate", "status": "completed", "conclusion": "success"}]

    return API(), store, root, env, output, candidate, key


def test_accept_rejects_forged_checks_and_canonical_record(prepared):
    api, store, root, env, original, *_ = prepared
    forged = copy.deepcopy(original)
    forged["result"]["checks"]["alignment"]["checks"] = [{"id": "fabricated", "exit_code": 0}]
    with pytest.raises(Blocked, match="WORKER_CHECKS_MISMATCH"):
        execution.accept(api, store, root, forged, env)
    forged = copy.deepcopy(original)
    forged["result"]["record"] = "repositories/2/foreign"
    with pytest.raises(Blocked, match="WORKER_RECORD_INVALID"):
        execution.accept(api, store, root, forged, env)


def test_accept_is_idempotent_and_releases_only_terminal_worker(prepared):
    api, store, root, env, output, _, key = prepared
    first = execution.accept(api, store, root, output, env)
    assert execution.accept(api, store, root, output, env) == first
    assert store.read(key)[0]["status"] == "accepted"
    assert store.read(scheduler.prefix(1) + "/slots")[0]["slots"] == [None, None]


def test_shadow_artifact_cannot_become_worker_evidence(prepared):
    api, store, root, env, output, *_ = prepared
    output["schema"] = "qualification-shadow/v1"
    with pytest.raises(Blocked, match="WORKER_OUTPUT_UNEXPECTED"):
        execution.accept(api, store, root, output, env)


def test_live_reservation_survives_expired_lease_and_cleanup_pending(prepared):
    _, store, _, _, _, candidate, _ = prepared
    lease = acquire(store, "repositories/1/live_lock", clock=lambda: 1)
    reservation = live_slot.reserve(store, 1, candidate, 10, 1, lease)
    replacement = acquire(store, "repositories/1/live_lock", clock=lambda: 500)
    with pytest.raises(Blocked, match="LIVE_TRIAL_RESERVED"):
        live_slot.reserve(store, 1, candidate.model_copy(update={"pr_id": 10}), 11, 1, replacement)
    with pytest.raises(Blocked, match="LEASE_LOST"):
        live_slot.CombinedLease(lease, replacement).assert_owned()
    store.put(candidate.key + "/cleanup", {"runs": ["owned"]}, None)
    with pytest.raises(Blocked, match="CLEANUP_PENDING"):
        live_slot.clean(store, 1, reservation, replacement)
    assert store.read("repositories/1/live_slot")[0]["status"] == "occupied"


@pytest.mark.parametrize("successor", [{"run_id": 11}, {"status": "clean"}, {}])
def test_live_recovery_rechecks_reservation_before_cleanup(prepared, monkeypatch, successor):
    _, store, root, _, _, candidate, _ = prepared
    lease = acquire(store, "repositories/1/live_lock")
    original = live_slot.reserve(store, 1, candidate, 10, 1, lease)
    lease.release()
    cleanup_calls = []
    monkeypatch.setattr(live_slot, "recover_cleanup", lambda **kw: cleanup_calls.append(kw))

    class API:
        def request(self, path):
            assert path == "actions/runs/10/attempts/1"
            # The original owner completed; a competing recovery settled it
            # before this recovery acquired its lease. A successor may now own
            # this same candidate's cleanup records.
            _, revision = store.read("repositories/1/live_slot")
            store.put("repositories/1/live_slot", {**original, **successor}, revision)
            return {
                "repository": {"id": 1},
                "run_attempt": 1,
                "path": ".github/workflows/github-ai-automation.yml",
                "status": "completed",
            }

    if successor:
        with pytest.raises(Blocked, match="LIVE_RESERVATION_CHANGED"):
            live_slot.recover(API(), store, root, {}, 1)
        assert cleanup_calls == []
        assert store.read("repositories/1/live_slot")[0] == {**original, **successor}
    else:
        assert live_slot.recover(API(), store, root, {}, 1) is True
        assert len(cleanup_calls) == 1
        assert store.read("repositories/1/live_slot")[0] == {**original, "status": "clean"}


def test_worker_snapshot_writes_cannot_modify_input(prepared):
    *_, output, _, _ = prepared
    original = copy.deepcopy(output)
    local = execution.SnapshotStore({"input": output})
    value, revision = local.read("input")
    value["nonce"] = "changed"
    local.put("input", value, revision)
    assert output == original


def test_interrupted_patch_acceptance_consumes_repair_budget_once(prepared, monkeypatch):
    api, store, root, env, output, candidate, key = prepared
    result = output["result"]
    result["code"] = "PATCH_PREPARED"
    source_hash = "3" * 64
    result_path = result["record"] + "/patch"
    verification = {
        "assessment": result["checks"]["alignment"]["assessment"],
        "checks": result["checks"]["alignment"]["checks"],
        "source_sha256": source_hash,
        "patch_sha256": digest("patch"),
    }
    output["evidence"][result_path] = {
        "candidate": candidate.model_dump(),
        "producer": result["producer"],
        "operation_id": result["request_id"],
        "patch": "patch",
        "verification": verification,
    }
    check_row = copy.deepcopy(next(iter(output["evidence"].values())))
    check_row["result"] = verification["checks"]
    output["evidence"][result["stage_key"] + "/repair-checks_" + source_hash] = check_row
    original_put = store.put
    failed = False

    def interrupt(path, row, revision):
        nonlocal failed
        if path == key and row["status"] == "accepted" and not failed:
            failed = True
            raise RuntimeError("simulated loss after budget write")
        return original_put(path, row, revision)

    monkeypatch.setattr(store, "put", interrupt)
    with pytest.raises(RuntimeError, match="simulated loss"):
        execution.accept(api, store, root, output, env)
    assert store.read(candidate.key + "/operation")[0]["attempts"] == 1
    assert execution.accept(api, store, root, output, env)["code"] == "PATCH_PREPARED"
    assert store.read(candidate.key + "/operation")[0]["attempts"] == 1


def test_finalization_resumes_original_admission_after_interrupted_stage_write(
    prepared, monkeypatch
):
    from github_ai_automation import events
    from github_ai_automation.contracts import CIResult

    api, store, root, env, output, candidate, _ = prepared
    execution.accept(api, store, root, output, env)
    store.put(
        candidate.key + "/waiver",
        {
            "repository_id": 1,
            "pr_id": candidate.pr_id,
            "actor_id": 7,
            "request_run_id": 10,
            "reason": "Fixture qualification",
            "active": True,
            "revision": 1,
        },
        None,
    )
    ci = CIResult(
        request_id=output["result"]["request_id"],
        workflow="ci.yml",
        run_id=20,
        attempt=1,
        workflow_sha="d" * 40,
        tested_tree=candidate.tree_sha,
        conclusion="success",
    )
    monkeypatch.setattr(events, "reconcile_ci", lambda *_: [ci])
    monkeypatch.setattr(events, "expected_ci", lambda *_: ["ci.yml"])
    monkeypatch.setattr(api, "paths", lambda _: ["src/a.py"], raising=False)
    original_put = store.put
    record = output["result"]["record"]
    failed = False

    def interrupt(path, row, revision):
        nonlocal failed
        if path == record + "/stages" and not failed:
            failed = True
            raise RuntimeError("simulated loss after admission")
        return original_put(path, row, revision)

    monkeypatch.setattr(store, "put", interrupt)
    with pytest.raises(RuntimeError, match="simulated loss"):
        execution.finalize(api, store, root, env)
    original = store.read(record + "/admission")[0]
    resumed = execution.finalize(api, store, root, env)
    assert resumed["code"] == "MERGE_ELIGIBLE"
    assert resumed["admission"] == original
    assert store.read(candidate.key + "/operation")[0]["qualification_fence"] == original["fence"]


@pytest.mark.parametrize("outcome", ["failed", "intervened"])
def test_failed_live_trial_requires_diagnosis_even_with_later_waiver(
    prepared, monkeypatch, outcome
):
    from github_ai_automation import events
    from github_ai_automation.contracts import CIResult, LiveResult

    api, store, root, env, output, candidate, key = prepared
    execution.accept(api, store, root, output, env)
    ci = CIResult(
        request_id=output["result"]["request_id"],
        workflow="ci.yml",
        run_id=20,
        attempt=1,
        workflow_sha="d" * 40,
        tested_tree=candidate.tree_sha,
        conclusion="success",
    )
    monkeypatch.setattr(events, "reconcile_ci", lambda *_: [ci])
    monkeypatch.setattr(events, "expected_ci", lambda *_: ["ci.yml"])
    monkeypatch.setattr(api, "paths", lambda _: ["src/a.py"], raising=False)
    trial = LiveResult(
        outcome=outcome,
        scenario_sha256="e" * 64,
        source_tree=candidate.tree_sha,
        assertions={"behavior": False},
        cleanup_complete=True,
        reason="ASSERTION_FAILED",
    )
    store.put(key + "/live", trial.model_dump(), None)
    store.put(
        candidate.key + "/waiver",
        {
            "repository_id": 1,
            "pr_id": candidate.pr_id,
            "actor_id": 7,
            "request_run_id": 10,
            "reason": "Missing live capability",
            "active": True,
            "revision": 1,
        },
        None,
    )
    result = execution.finalize(api, store, root, env)
    assert result["code"] == "LIVE_REMEDIATION_REQUIRED"
    operation, rev = store.read(candidate.key + "/operation")
    assert operation["live_failure"]["live"] == trial.model_dump()
    assert store.read(output["result"]["record"] + "/admission")[0] is None
    operation["attempts"] = 2
    store.put(candidate.key + "/operation", operation, rev)
    assert execution.finalize(api, store, root, env)["code"] == "LIVE_REPAIR_UNAVAILABLE"


def test_unavailable_live_trial_is_manual_only_after_current_ci(prepared, monkeypatch):
    from github_ai_automation import events
    from github_ai_automation.contracts import CIResult, LiveResult

    api, store, root, env, output, candidate, key = prepared
    execution.accept(api, store, root, output, env)
    ci = CIResult(
        request_id=output["result"]["request_id"],
        workflow="ci.yml",
        run_id=20,
        attempt=1,
        workflow_sha="d" * 40,
        tested_tree=candidate.tree_sha,
        conclusion="success",
    )
    monkeypatch.setattr(events, "reconcile_ci", lambda *_: [])
    monkeypatch.setattr(events, "expected_ci", lambda *_: ["ci.yml"])
    monkeypatch.setattr(api, "paths", lambda _: ["src/a.py"], raising=False)
    trial = LiveResult(
        outcome="unavailable",
        scenario_sha256="e" * 64,
        source_tree=candidate.tree_sha,
        assertions={},
        cleanup_complete=True,
        reason="DRIVER_UNAVAILABLE",
    )
    store.put(key + "/live", trial.model_dump(), None)
    with pytest.raises(Blocked, match="CI_INCOMPLETE"):
        execution.finalize(api, store, root, env)
    monkeypatch.setattr(events, "reconcile_ci", lambda *_: [ci])
    assert execution.finalize(api, store, root, env)["code"] == "MANUAL_MERGE"
    assert store.read(output["result"]["record"] + "/admission")[0] is None


def test_live_failure_survives_loss_before_finalization_receipt(prepared, monkeypatch, tmp_path):
    from github_ai_automation import events
    from github_ai_automation.contracts import CIResult, LiveResult, Scenario

    api, store, root, env, output, candidate, key = prepared
    scenario = Scenario(
        schema_version=1,
        capability="fixture",
        objective="Regression",
        workload="run",
        observer="read",
        negative_control="bad output",
        fixture_requirements="isolated",
        assertions=[
            {
                "name": "behavior",
                "path": "src/a.py",
                "product_action": "Run",
                "observation": "Read",
                "expected": "Works",
            }
        ],
    )
    record, revision = store.read(key)
    result = {**output["result"], "scenario": scenario.model_dump()}
    store.put(key, {**record, "status": "accepted", "result": result}, revision)
    ci = CIResult(
        request_id=result["request_id"],
        workflow="ci.yml",
        run_id=20,
        attempt=1,
        workflow_sha="d" * 40,
        tested_tree=candidate.tree_sha,
        conclusion="success",
    )
    monkeypatch.setattr(events, "reconcile_ci", lambda *args: [ci])
    monkeypatch.setattr(events, "expected_ci", lambda *args: ["ci.yml"])
    monkeypatch.setattr(api, "paths", lambda _: ["src/a.py"], raising=False)
    monkeypatch.setattr(execution, "snapshot", lambda *args: (tmp_path, {}))
    trial = LiveResult(
        outcome="failed",
        scenario_sha256=digest(scenario),
        source_tree=candidate.tree_sha,
        assertions={"behavior": False},
        cleanup_complete=True,
        reason="ASSERTION_FAILED",
    )
    monkeypatch.setattr(execution, "execute_live", lambda **kwargs: trial)
    put = store.put

    def fail_receipt(path, value, revision):
        if path == key + "/live":
            raise RuntimeError("receipt loss")
        return put(path, value, revision)

    monkeypatch.setattr(store, "put", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt loss"):
        execution.live_trial(api, store, root, tmp_path, env)
    assert store.read(candidate.key + "/operation")[0]["live_failure"]["live"] == trial.model_dump()
    assert store.read(key + "/live")[0] is None
