import hashlib
import json
from pathlib import Path

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted, general

from github_ai_automation import live, merge, publication, recovery
from github_ai_automation.contracts import Blocked, Candidate, Scenario
from github_ai_automation.state import acquire


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


def test_live_failure_preserves_cleanup_and_recovery_never_grades_pass(tmp_path, candidate):
    driver = tmp_path / "driver.py"
    driver.write_text("""
class Driver:
    def __init__(self, *, context, store, lease, run_key):
        self.context, self.store, self.lease, self.key = context, store, lease, run_key
    def reconcile(self):
        self.lease.assert_owned()
    def preflight(self, plan):
        pass
    def provision(self, plan):
        self.lease.assert_owned()
        value, etag = self.store.read(self.key)
        self.store.put(self.key, {**value, 'resources': ['owned-fixture']}, etag)
    def verify(self, plan):
        raise RuntimeError('test fixture process failed')
    def cleanup(self):
        if not self.context.get('cleanup_allowed'):
            raise RuntimeError('cleanup transport unavailable')
        self.lease.assert_owned()
        value, etag = self.store.read(self.key)
        self.store.put(self.key, {**value, 'resources': []}, etag)
    def absent(self):
        return self.store.read(self.key)[0]['resources'] == []
""")
    policy = {
        "live_timeout_seconds": 10,
        "live_capabilities": {
            "fixture": {
                "driver": "driver.py",
                "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
                "qualified": True,
            }
        },
    }
    scenario = Scenario(
        schema_version=1,
        capability="fixture",
        objective="One PR regression",
        assertions=[
            {
                "name": "regression",
                "path": "src/a.py",
                "product_action": "Run product",
                "observation": "Read result",
                "expected": "Product result",
            }
        ],
        fixture_requirements="Disposable test fixture",
        workload="Run product",
        observer="Read result",
        negative_control="Reproduce defect on previous version",
    )
    from github_ai_automation.policy import validate_scenario

    validate_scenario(scenario, ["src/a.py"])
    with pytest.raises(Blocked, match="LIVE_SCENARIO_UNRELATED"):
        validate_scenario(scenario, ["src/other.py"])
    store = MemoryStore()
    lease = acquire(store, candidate.key + "/lock")
    result = live.execute_live(
        scenario=scenario,
        candidate=candidate,
        policy=policy,
        trusted_root=tmp_path,
        context={"trial": "1"},
        lease=lease,
        store=store,
    )
    assert result.outcome == "unavailable" and result.cleanup_complete is False
    with pytest.raises(Blocked, match="CLEANUP_PENDING"):
        live.require_cleanup(store, candidate)
    live.recover_cleanup(
        candidate=candidate,
        policy=policy,
        trusted_root=tmp_path,
        context={"cleanup_allowed": True},
        lease=lease,
        store=store,
    )
    live.require_cleanup(store, candidate)
    records = [store.read(k)[0] for k in store.scan(candidate.key + "/live")]
    assert records[0]["status"] == "recovered_cleanup"
    assert records[0]["result"]["outcome"] == "unavailable"


def test_actual_merge_subprocess_rejection_is_retryable_only_before_submission(
    tmp_path, candidate, monkeypatch
):
    helper = tmp_path / "skills/merge-pr/scripts/merge_gate.py"
    helper.parent.mkdir(parents=True)
    monkeypatch.setenv("GH_TOKEN", "test-only")
    monkeypatch.setattr(
        merge, "verify_review", lambda *a, **kw: {"automation": {"operation_id": REQUEST_ID}}
    )

    class API:
        repository = "owner/repo"

        def candidate(self, number):
            return candidate, {}

        def request(self, path):
            return {"merged": False}

    store = MemoryStore()
    admitted(store, candidate)
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    helper.write_text("raise SystemExit(2)\n")
    with pytest.raises(Blocked, match="EFFECT_NOT_SUBMITTED"):
        merge.execute_merge(API(), store, tmp_path, 2, candidate.head_sha, 20, "squash")
    helper.write_text(
        "import os\nfrom pathlib import Path\nPath(os.environ['AI_AUTOMATION_MERGE_DISPATCH_RECEIPT']).write_text('submitted')\nraise SystemExit(2)\n"
    )
    with pytest.raises(Blocked, match="MERGE_PENDING_OR_REJECTED"):
        merge.execute_merge(API(), store, tmp_path, 2, candidate.head_sha, 21, "squash")
    with pytest.raises(Blocked, match="EFFECT_RECONCILIATION_REQUIRED"):
        merge.execute_merge(API(), store, tmp_path, 2, candidate.head_sha, 21, "squash")
    record = store.read(candidate.key + "/requests/" + REQUEST_ID + "/merge/" + candidate.head_sha)[
        0
    ]
    assert record["status"] == "pending"
    assert len(record["previous_attempts"]) == 1


def test_later_release_survives_interrupted_reservation(tmp_path, monkeypatch):
    config = tmp_path / "github-ai-automation/config"
    config.mkdir(parents=True)
    (config / "policy.json").write_text(
        (Path(__file__).resolve().parents[2] / "config/policy.json").read_text()
    )
    (config / "release-projects.json").write_text(json.dumps({"projects": {"vpngw": {}}}))
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REF": "refs/heads/main",
        "AI_AUTOMATION_REPOSITORY_ID": "1",
        "AI_AUTOMATION_RELEASE_PUBLISH": "true",
        "AI_AUTOMATION_PR_REVIEW": "disabled",
    }.items():
        monkeypatch.setenv(name, value)

    class API:
        def __init__(self):
            self.calls = []

        def request(self, path):
            assert path == ""
            return {"id": 1, "default_branch": "main"}

        def dispatch(self, workflow, inputs):
            self.calls.append(inputs)

    key = "repositories/1/releases/vpngw"
    store, api = general(MemoryStore()), API()
    publication.remember_request(store, key, source="a" * 40, number=2, completion_run=20)
    publication.remember_request(store, key, source="b" * 40, number=3, completion_run=30)
    record = {
        "status": "reserved",
        "source_sha": "a" * 40,
        "pr_number": 2,
        "completion_run": 20,
        "tag": "v1.0.1",
        "version": "1.0.1",
    }
    revision = store.put(key + "/current", record, None)
    recovery.recover(api, store, tmp_path)
    assert api.calls[-1]["pr_number"] == "2"
    store.put(key + "/current", {**record, "status": "complete"}, revision)
    lease = acquire(store, key + "/lock")
    publication.finish_request(store, lease, key, "a" * 40)
    lease.release()
    recovery.recover(api, store, tmp_path)
    assert api.calls[-1]["pr_number"] == "3"
    assert store.read(key + "/requests/" + "b" * 40)[0]["status"] == "pending"


def test_completion_status_lane_has_trusted_runtime_and_state_scope():
    root = Path(__file__).resolve().parents[3]
    text = (root / "github-ai-automation/deployment/workflows/merge-completion.yml.template").read_text()
    lane = text.split("  repair-ci-status:", 1)[1].split("  candidates:", 1)[0]
    assert "environment: github-ai-automation" in lane
    assert "uv run --locked --project github-ai-automation" in lane
    assert "AI_AUTOMATION_STATE_SECRET_ACCESS_KEY" in lane
    assert "TF_API_KEY" not in lane


@pytest.mark.parametrize("value", [None, "false", "True", "1"])
def test_release_control_defaults_off_independently_of_pr_mode(tmp_path, monkeypatch, value):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.delenv("AI_AUTOMATION_RELEASE_PUBLISH", raising=False)
    if value is not None:
        monkeypatch.setenv("AI_AUTOMATION_RELEASE_PUBLISH", value)
    with pytest.raises(Blocked, match="RELEASE_NOT_ENABLED"):
        publication.release_intake(None, None, tmp_path, 10)


def test_intake_persists_all_projects_before_a_dispatch_failure(tmp_path, monkeypatch):
    config = tmp_path / "github-ai-automation/config"
    config.mkdir(parents=True)
    (config / "release-projects.json").write_text(
        json.dumps(
            {
                "projects": {
                    name: {"include": ["src/*"], "exclude": []}
                    for name in ["vpngw", "nebius-cxcli"]
                }
            }
        )
    )
    monkeypatch.setenv("AI_AUTOMATION_RELEASE_PUBLISH", "true")
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "disabled")
    monkeypatch.setattr(publication, "completion", lambda *a: ({"result_sha": "a" * 40}, {}))

    class API:
        def __init__(self):
            self.dispatched = []

        def request(self, path):
            if not path:
                return {"id": 1, "default_branch": "main"}
            return {
                "path": ".github/workflows/merge-completion.yml",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "repository": {"id": 1},
                "run_attempt": 1,
            }

        def pages(self, path, **kwargs):
            if path.endswith("/artifacts"):
                return [{"name": "merge-complete-2-" + "b" * 40 + "-10-1"}]
            return [{"filename": "src/product.py"}]

        def dispatch(self, workflow, inputs):
            self.dispatched.append(inputs)
            raise Blocked("GITHUB_OUTCOME_UNKNOWN")

    store, api = general(MemoryStore()), API()
    with pytest.raises(Blocked, match="RELEASE_DISPATCH_PENDING"):
        publication.release_intake(api, store, tmp_path, 10)
    for project in ["vpngw", "nebius-cxcli"]:
        assert (
            store.read(f"repositories/1/releases/{project}/requests/" + "a" * 40)[0]["status"]
            == "pending"
        )
    assert len(api.dispatched) == 2
