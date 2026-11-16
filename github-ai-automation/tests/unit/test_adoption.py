"""Replacement publication, inherited authority and explicit campaign renewal."""

import base64
import copy
import time
from pathlib import Path

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import adoption, requests
from github_ai_automation.contracts import Blocked, Candidate, Producer, digest
from github_ai_automation.policy import policy_digest
from github_ai_automation.state import acquire


@pytest.fixture
def campaign(monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.setenv("AI_AUTOMATION_MERGE_FORK_CONTRIBUTION", "true")
    parent = Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="contributor/repo",
        head_repository_id=2,
        head_ref="feature",
        pr_id=9,
        pr_number=2,
        head_sha="a" * 40,
        base_sha="b" * 40,
        tree_sha="c" * 40,
    )
    child = parent.model_copy(
        update={
            "head_repository": "owner/repo",
            "head_repository_id": 1,
            "head_ref": "ai-adoption/9-" + REQUEST_ID[:16],
            "pr_id": 10,
            "pr_number": 3,
            "head_sha": "d" * 40,
            "tree_sha": "e" * 40,
        }
    )
    store = MemoryStore()
    op = admitted(
        store,
        parent,
        capabilities=["assess", "comment", "label", "close", "adopt", "qualify", "merge"],
    )
    op.update(attempts=1, rationale_sha256=digest({}), state_events=[])
    _, rev = store.read(parent.key + "/operation")
    store.put(parent.key + "/operation", op, rev)
    binding = {
        "candidate": parent.model_dump(),
        "request_id": REQUEST_ID,
        "record": parent.key + "/requests/" + REQUEST_ID + "/runs/10_1",
        "child": child.model_dump(),
        "proposal": "f" * 64,
        "rationale_sha256": digest({}),
        "state_events": [],
    }
    return parent, child, store, op, binding


def transfer(campaign):
    parent, child, store, op, binding = campaign
    lease = acquire(store, parent.key + "/lock")
    try:
        return adoption.transfer(store, lease, parent, child, op, binding)
    finally:
        lease.release()


def test_transfer_preserves_budget_blocks_parent_and_repairs_only_owned_child(
    campaign, monkeypatch
):
    parent, child, store, original, _ = campaign
    record = transfer(campaign)
    assert record["deadline"] == original["deadline"] and record["attempts"] == 1
    assert "adopt" not in record["capabilities"]
    requests.require_capability(store, child, record["id"], "repair")
    with pytest.raises(Blocked, match="ADOPTION_EXECUTION_TRANSFERRED"):
        requests.require_capability(store, parent, original["id"], "assess")
    with pytest.raises(Blocked):
        requests.require_capability(store, parent, original["id"], "repair")
    monkeypatch.setenv("AI_AUTOMATION_MERGE_FORK_CONTRIBUTION", "false")
    for capability in ("merge", "repair", "qualify"):
        with pytest.raises(Blocked, match="ADOPTION_CAPABILITY_DENIED"):
            requests.require_capability(store, child, record["id"], capability)


def test_fresh_manual_request_reuses_replacement_and_frozen_renewal_deadline(campaign):
    parent, child, store, _, binding = campaign
    first = transfer(campaign)
    op, rev = store.read(child.key + "/operation")
    store.put(child.key + "/operation", {**op, "status": "terminal", "attempts": 2}, rev)

    class API:
        def candidate(self, number):
            return (child if number == 3 else parent), {"state": "open", "draft": False}

        def request(self, path):
            assert path == "pulls/2"
            return {"head": {"sha": parent.head_sha}, "state": "open"}

    env = {
        "GITHUB_RUN_ID": "77",
        "AI_AUTOMATION_PR_REVIEW": "merge",
        "AI_AUTOMATION_MERGE_FORK_CONTRIBUTION": "true",
    }
    now = int(time.time())
    renewed = requests.register(API(), store, parent, {}, "process", "manual", env, now=now)
    assert renewed["candidate"]["pr_id"] == child.pr_id
    assert (
        renewed["id"] != first["id"]
        and renewed["attempts"] == 0
        and renewed["deadline"] == now + 10800
    )
    assert renewed["adoption_parent"]["record"] == binding["record"]
    assert (
        requests.register(API(), store, child, {}, "process", "manual", env, now=now + 50)
        == renewed
    )
    requests.require_capability(store, child, renewed["id"], "repair")
    assert requests.register(API(), store, child, {}, "process", "opened", env, now=now) is None


def proposal(parent, root):
    proof = {
        "assessment": {
            "verdict": "pass",
            "findings": [],
            "reviewed_paths": ["src/a.py", "src/b.py"],
            "summary": "Both features preserved",
            "required_checks": ["unit"],
        },
        "checks": [{"id": "unit", "exit_code": 0}],
        "source_sha256": "1" * 64,
    }
    parts = []
    features = []
    for index, name in enumerate(("src/a.py", "src/b.py"), 1):
        patch = [
            {
                "path": name,
                "mode": "100644",
                "content": base64.b64encode(f"feature = {index}\n".encode()).decode(),
            }
        ]
        parts.append(
            {
                "feature": f"feature-{index}",
                "patch": patch,
                "verification": {**proof, "patch_sha256": digest(patch)},
            }
        )
        features.append(
            {
                "id": f"feature-{index}",
                "summary": f"Feature {index}",
                "paths": [name],
                "depends_on": [],
                "disposition": "adopt",
                "criteria": ["Behavior preserved"],
                "evidence": name,
            }
        )
    patch = [p for part in parts for p in part["patch"]]
    producer = Producer(
        workflow=".github/workflows/github-ai-automation.yml",
        workflow_sha="4" * 40,
        run_id=10,
        attempt=1,
        policy_sha256=policy_digest(root),
        skills_sha256="5" * 64,
        runtime_image="sha256:" + "6" * 64,
        model="test/model",
    )
    return {
        "candidate": parent.model_dump(),
        "operation_id": REQUEST_ID,
        "producer": producer.model_dump(),
        "original_context": "Full original diff",
        "plan": {"reviewed_paths": ["src/a.py", "src/b.py"], "features": features},
        "plan_review": proof["assessment"],
        "slices": parts,
        "patch": patch,
        "verification": {**proof, "patch_sha256": digest(patch)},
        "stage_key": "stages",
    }


class Publisher:
    repository = "owner/repo"

    def __init__(self, parent):
        self.parent = parent
        self.branch, self.pr = None, None
        self.trees, self.commits = {"0" * 40: {}}, {parent.base_sha: {"tree": {"sha": "0" * 40}}}
        self.pr_creates = 0
        self.crash = True

    def candidate(self, number):
        if number == 2:
            return self.parent, {"state": "open"}
        return self.parent.model_copy(
            update={
                "pr_number": 3,
                "pr_id": 10,
                "head_repository": self.repository,
                "head_repository_id": 1,
                "head_ref": self.branch["ref"].removeprefix("refs/heads/"),
                "head_sha": self.branch["sha"],
                "tree_sha": self.commits[self.branch["sha"]]["tree"]["sha"],
            }
        ), {"state": "open", "draft": False}

    def producer(self, _):
        pass

    def paths(self, _):
        return ["src/a.py", "src/b.py"]

    def pages(self, path):
        return [self.pr] if self.pr else []

    def request(self, path, *, method="GET", body=None, absent=False):
        if path == "":
            return {"default_branch": "main"}
        if path == "actions/runs/10":
            return {"created_at": "2026-01-01T00:00:00Z"}
        if path.startswith("git/commits/"):
            return self.commits[path.split("/")[-1]]
        if path == "git/blobs":
            return {"sha": digest(body)[0:40]}
        if path == "git/trees":
            tree = copy.deepcopy(self.trees[body["base_tree"]])
            tree.update({r["path"]: r["sha"] for r in body["tree"]})
            sha = digest(tree)[:40]
            self.trees[sha] = tree
            return {"sha": sha}
        if path == "git/commits":
            sha = digest(body)[:40]
            self.commits[sha] = {"tree": {"sha": body["tree"]}, "parents": body["parents"]}
            return {"sha": sha}
        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.branch["sha"]}} if self.branch else None
        if path == "git/refs":
            self.branch = body
            return {"object": {"sha": body["sha"]}}
        if path == "pulls":
            self.pr_creates += 1
            self.pr = {
                "id": 10,
                "number": 3,
                "head": {"sha": self.branch["sha"]},
                "base": {"ref": body["base"]},
                "user": {"id": 41898282},
            }
            if self.crash:
                self.crash = False
                raise Blocked("GITHUB_OUTCOME_UNKNOWN")
            return self.pr
        raise AssertionError(path)


def test_lost_pr_create_response_reconciles_one_pr_and_feature_commits(campaign):
    parent, _, store, _, binding = campaign
    root = Path(__file__).resolve().parents[3]
    proposed = proposal(parent, root)
    store.put(binding["record"] + "/adoption", proposed, None)
    api = Publisher(parent)
    lease = acquire(store, parent.key + "/lock")
    try:
        with pytest.raises(Blocked, match="GITHUB_OUTCOME_UNKNOWN"):
            adoption.publish(api, store, lease, binding["record"], parent, REQUEST_ID, root)
        child = adoption.publish(api, store, lease, binding["record"], parent, REQUEST_ID, root)
        assert api.pr_creates == 1
        assert child["attempts"] == 1 and child["candidate"]["pr_number"] == 3
        assert len(api.commits) == 3  # base + two deterministic feature commits
    finally:
        lease.release()


def test_omitted_slice_or_divergent_aggregate_cannot_publish(campaign):
    parent, _, store, _, binding = campaign
    root = Path(__file__).resolve().parents[3]
    proposed = proposal(parent, root)
    omitted = copy.deepcopy(proposed)
    omitted["slices"].pop()
    with pytest.raises(Blocked, match="ADOPTION_COVERAGE_INCOMPLETE"):
        adoption.validate(omitted, ["src/a.py", "src/b.py"])
    proposed["patch"] = proposed["patch"][:1]
    proposed["verification"]["patch_sha256"] = digest(proposed["patch"])
    store.put(binding["record"] + "/adoption", proposed, None)
    api = Publisher(parent)
    lease = acquire(store, parent.key + "/lock")
    try:
        with pytest.raises(Blocked, match="ADOPTION_SLICE_TREE_MISMATCH"):
            adoption.publish(api, store, lease, binding["record"], parent, REQUEST_ID, root)
        assert api.branch is None and api.pr_creates == 0
    finally:
        lease.release()


@pytest.mark.parametrize(
    "change",
    ["none", "original_head", "objection", "reopened", "missing_coverage", "report_mode", "lease"],
)
def test_original_closes_only_after_current_complete_merged_coverage(campaign, monkeypatch, change):
    from github_ai_automation import lifecycle
    from github_ai_automation.stages import human_context

    parent, child, store, op, binding = campaign
    root = Path(__file__).resolve().parents[3]
    proposed = proposal(parent, root)
    store.put(binding["record"] + "/adoption", proposed, None)

    class API:
        def __init__(self):
            self.original = {
                "id": parent.pr_id,
                "head": {"sha": parent.head_sha},
                "state": "open",
                "title": "Feature",
                "body": "Useful",
            }
            self.events, self.humans, self.closes = [], [], 0
            self.tip_reads = 0

        def request(self, path, *, method="GET", body=None):
            if path == "":
                return {"default_branch": "main"}
            if path == "git/ref/heads/main":
                self.tip_reads += 1
                if change == "lease" and self.tip_reads == 4:
                    lock, version = store.read(parent.key + "/lock")
                    store.put(parent.key + "/lock", {**lock, "owner": "successor"}, version)
                return {"object": {"sha": "f" * 40}}
            if path == "git/commits/" + "f" * 40:
                return {"tree": {"sha": child.tree_sha}}
            if path == "pulls/2":
                if method == "PATCH":
                    self.closes += 1
                    self.original.update(body)
                return copy.deepcopy(self.original)
            raise AssertionError(path)

        def pages(self, path):
            if path.endswith("/events"):
                return self.events
            if path == "issues/2/comments":
                return self.humans
            return []

        def comment(self, *args, **kwargs):
            if change == "objection":
                self.humans.append(
                    {
                        "id": 3,
                        "user": {"id": 7, "type": "User"},
                        "body": "Do not close; a feature is missing",
                    }
                )

    api = API()
    binding.update(
        proposal=digest(proposed), rationale_sha256=digest(human_context(api, 2, api.original))
    )
    record = transfer((parent, child, store, op, binding))
    current, rev = store.read(child.key + "/operation")
    current.update(adoption_verified=child.model_dump())
    if change == "missing_coverage":
        current.pop("adoption_verified")
    store.put(child.key + "/operation", current, rev)
    if change == "original_head":
        api.original["head"]["sha"] = "0" * 40
    if change == "reopened":
        api.events = [{"id": 4, "event": "reopened"}]
    if change == "report_mode":
        monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "report")
    monkeypatch.setattr(lifecycle, "publish", lambda *args, **kwargs: None)
    merged = {"merged": True, "merge_commit_sha": "f" * 40}
    if change != "none":
        with pytest.raises(Blocked, match="ADOPTION_|LEASE_LOST"):
            adoption.merged_replacement(api, store, binding, child, merged)
        assert api.closes == 0
    else:
        # A parent may have expired while the manually reviewed child was pending.
        current, rev = store.read(parent.key + "/operation")
        current.update(status="terminal", outcome="OPERATION_EXPIRED")
        store.put(parent.key + "/operation", current, rev)
        adoption.merged_replacement(api, store, binding, child, merged)
        assert api.closes == 1
        assert store.read(parent.key + "/operation")[0]["outcome"] == "ADOPTION_COMPLETE"
        adoption.merged_replacement(api, store, binding, child, merged)
        assert api.closes == 1
        assert record["deadline"] == op["deadline"]


def test_live_scenario_must_exercise_each_adopted_feature(campaign):
    from github_ai_automation.contracts import Scenario

    parent, *_ = campaign
    proposed = proposal(parent, Path(__file__).resolve().parents[3])
    scenario = Scenario(
        schema_version=1,
        capability="fixture",
        objective="Both features",
        workload="run",
        observer="read",
        negative_control="bad output",
        fixture_requirements="isolated",
        assertions=[
            {
                "name": "behavior",
                "path": "src/a.py",
                "product_action": "Exercise both features",
                "observation": "Read output",
                "expected": "Both features work",
            }
        ],
    )
    coverage = {
        "verdict": "pass",
        "reviewed_paths": ["src/a.py", "src/b.py"],
        "feature_assertions": {"feature-1": ["behavior"]},
        "summary": "Verified",
    }
    with pytest.raises(Blocked, match="ADOPTION_LIVE_COVERAGE_INCOMPLETE"):
        adoption.validate_live_coverage(proposed, scenario, coverage)
    coverage["feature_assertions"]["feature-2"] = ["missing-assertion"]
    with pytest.raises(Blocked, match="ADOPTION_LIVE_COVERAGE_INCOMPLETE"):
        adoption.validate_live_coverage(proposed, scenario, coverage)
    coverage["feature_assertions"]["feature-2"] = ["behavior"]
    adoption.validate_live_coverage(proposed, scenario, coverage)
