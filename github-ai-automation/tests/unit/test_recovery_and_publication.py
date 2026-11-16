import base64
import hashlib
import json

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted, general

from github_ai_automation import publication
from github_ai_automation.bundle import package_skills
from github_ai_automation.contracts import Blocked, Candidate, Fragment, Review
from github_ai_automation.events import request_ci
from github_ai_automation.releases import missing_fragment_patch
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


class DispatchAPI:
    def __init__(self):
        self.runs, self.dispatches = [], []

    def pages(self, _, **kwargs):
        return self.runs

    def dispatch(self, workflow, inputs):
        self.dispatches.append((workflow, inputs))
        raise Blocked("GITHUB_OUTCOME_UNKNOWN")


def test_lost_ci_dispatch_is_reconciled_and_bounded(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    store, api = MemoryStore(), DispatchAPI()
    admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")
    for now in [1000, 1120, 1240]:
        with pytest.raises(Blocked, match="GITHUB_OUTCOME_UNKNOWN"):
            request_ci(api, store, lease, "ci.yml", candidate, REQUEST_ID, source="5:1", now=now)
        request_ci(api, store, lease, "ci.yml", candidate, REQUEST_ID, source="5:1", now=now + 1)
    assert len(api.dispatches) == 3
    with pytest.raises(Blocked, match="CI_DISPATCH_UNAVAILABLE"):
        request_ci(api, store, lease, "ci.yml", candidate, REQUEST_ID, source="5:1", now=1360)


def test_discovered_ci_does_not_dispatch_again(candidate, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    store, api = MemoryStore(), DispatchAPI()
    admitted(store, candidate)
    lease = acquire(store, candidate.key + "/lock")
    api.runs = [
        {
            "id": 99,
            "display_title": f"ai-candidate-2-{candidate.head_sha}-{candidate.base_sha}-{REQUEST_ID}",
            "status": "in_progress",
        }
    ]
    assert request_ci(api, store, lease, "ci.yml", candidate, REQUEST_ID, source="5:1") == 99
    assert not api.dispatches
    api.runs[0].update(status="completed", conclusion="failure")
    with pytest.raises(Blocked, match="CANDIDATE_CI_FAILED"):
        request_ci(api, store, lease, "ci.yml", candidate, REQUEST_ID, source="5:1")


class ReleaseAPI:
    def __init__(self, after_tag=None):
        self.tags, self.refs, self.calls = {}, {}, []
        self.after_tag = after_tag

    def pages(self, path, **_):
        assert path == "pulls/2/files"
        return [{"filename": "src/product.py"}]

    def request(self, path, *, method="GET", body=None, absent=False):
        self.calls.append((method, path))
        if path.startswith("compare/"):
            return {"status": "behind"}
        if path.startswith("git/commits/"):
            return {"committer": {"date": "2026-01-01T00:00:00Z"}}
        if path == "git/tags":
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            self.tags[sha] = body
            if self.after_tag:
                self.after_tag()
            return {"sha": sha}
        if path.startswith("git/ref/tags/"):
            return self.refs.get(path)
        if path == "git/refs":
            self.refs["git/ref/" + body["ref"].removeprefix("refs/")] = {
                "object": {"sha": body["sha"], "type": "tag"}
            }
            return {}
        raise AssertionError(path)


@pytest.fixture
def release_setup(tmp_path, monkeypatch):
    directory = tmp_path / "github-ai-automation/config"
    directory.mkdir(parents=True)
    (directory / "release-projects.json").write_text(
        json.dumps(
            {
                "projects": {
                    "vpngw": {
                        "tag_prefix": "test-v",
                        "include": ["src/*"],
                        "exclude": [],
                        "assets": ["*.whl"],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("AI_AUTOMATION_RELEASE_PUBLISH", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "50")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(publication, "trusted_runtime", lambda _: {"id": 1})
    monkeypatch.setattr(publication, "completion", lambda *_: ({"result_sha": "d" * 40}, {}))
    monkeypatch.setattr(
        publication, "previous_release", lambda *_: {"version": "1.2.3", "source_sha": "e" * 40}
    )
    monkeypatch.setattr(
        publication,
        "read_fragments",
        lambda *_: [Fragment(project="vpngw", bump="minor", summary="Add a capability")],
    )
    return tmp_path


def test_release_reconciles_tag_creation_after_interruption(release_setup):
    store, api = general(MemoryStore()), ReleaseAPI()
    result = publication.prepare_release(
        api, store, release_setup, project="vpngw", number=2, completion_run=40
    )
    assert result["tag"] == "test-v1.3.0"
    # Simulate a lost ref creation: intent/tag object remain durable.
    api.refs.clear()
    before = len(api.tags)
    resumed = publication.prepare_release(
        api, store, release_setup, project="vpngw", number=2, completion_run=40
    )
    assert resumed["tag_object"] == result["tag_object"]
    assert len(api.tags) == before and len(api.refs) == 1


def test_release_does_not_overwrite_after_lease_loss(release_setup):
    store = general(MemoryStore())
    key = "repositories/1/releases/vpngw"

    def lose_lease():
        lock, revision = store.read(key + "/lock")
        store.put(key + "/lock", {**lock, "owner": "replacement"}, revision)
        value, revision = store.read(key + "/current")
        store.put(key + "/current", {**value, "status": "replacement-owned"}, revision)

    with pytest.raises(Blocked, match="LEASE_LOST"):
        publication.prepare_release(
            ReleaseAPI(lose_lease),
            store,
            release_setup,
            project="vpngw",
            number=2,
            completion_run=40,
        )
    assert store.read(key + "/current")[0]["status"] == "replacement-owned"


def test_pending_release_rejects_other_source(release_setup, monkeypatch):
    store, api = general(MemoryStore()), ReleaseAPI()
    publication.prepare_release(
        api, store, release_setup, project="vpngw", number=2, completion_run=40
    )
    monkeypatch.setattr(publication, "completion", lambda *_: ({"result_sha": "f" * 40}, {}))
    with pytest.raises(Blocked, match="RELEASE_PENDING"):
        publication.prepare_release(
            api, store, release_setup, project="vpngw", number=2, completion_run=40
        )


def test_bundle_requires_closure_and_excludes_hooks(tmp_path):
    root = tmp_path / "repo"
    for name in ["align", "linter"]:
        skill = root / "skills" / name
        (skill / "assets/hooks").mkdir(parents=True)
        (skill / "SKILL.md").write_text("Instructions")
        (skill / "check.sh").write_text("#!/bin/sh\nexit 0\n")
        (skill / "check.sh").chmod(0o755)
        (skill / "assets/hooks/secret_hook.py").write_text("not installed")
    manifest = tmp_path / "manifest.json"
    value = {
        "schema_version": 1,
        "skills": ["align"],
        "hooks": [],
        "requires": {"align": ["linter"]},
    }
    manifest.write_text(json.dumps(value))
    with pytest.raises(Blocked, match="SKILL_DEPENDENCY_MISSING"):
        package_skills(root, manifest, tmp_path / "bad")
    value["skills"].append("linter")
    manifest.write_text(json.dumps(value))
    package_skills(root, manifest, tmp_path / "good")
    assert not list((tmp_path / "good").rglob("secret_hook.py"))
    assert (tmp_path / "good/align/check.sh").stat().st_mode & 0o777 == 0o755


def test_fragments_are_prepared_in_original_pr_with_explicit_bump(tmp_path):
    review = Review(
        verdict="pass",
        findings=[],
        reviewed_paths=["src/a.py"],
        summary="Feature",
        compatibility="compatible",
        spec_assessment="Aligned",
        release_fragments=[Fragment(project="vpngw", bump="minor", summary="Feature")],
    )
    registry = {"projects": {"vpngw": {"include": ["src/*"], "exclude": []}}}
    patch = missing_fragment_patch(tmp_path, ["src/a.py"], registry, review, 2)
    assert patch[0]["path"] == "release-notes/pr-2-vpngw.json"
    assert json.loads(base64.b64decode(patch[0]["content"]))["bump"] == "minor"
    with pytest.raises(Blocked, match="RELEASE_BUMP_UNRESOLVED"):
        missing_fragment_patch(
            tmp_path, ["src/a.py"], registry, review.model_copy(update={"release_fragments": []}), 2
        )


@pytest.mark.parametrize(
    "path",
    [
        "services/nebius-cxcli/component_cli_settings.yaml",
        "services/nebius-cxcli/compatibility-matrix.yaml",
        "services/nebius-cxcli/scripts/build_tailscale_helper.py",
    ],
)
def test_packaging_inputs_are_release_inputs(path):
    from pathlib import Path

    from github_ai_automation.releases import affected_projects

    registry = json.loads(
        (Path(__file__).resolve().parents[2] / "config/release-projects.json").read_text()
    )
    assert affected_projects([path], registry) == ["nebius-cxcli"]


def test_invalid_tag_assertion_does_not_poison_pending_releases(release_setup):
    store, api = general(MemoryStore()), ReleaseAPI()
    key = "repositories/1/releases/vpngw/requests/" + "d" * 40
    with pytest.raises(Blocked, match="REQUESTED_VERSION_DIFFERS_FROM_FRAGMENTS"):
        publication.prepare_release(
            api,
            store,
            release_setup,
            project="vpngw",
            number=2,
            completion_run=40,
            requested_tag="test-v9.9.9",
        )
    assert store.read(key)[0] is None
    assert store.read("repositories/1/releases/vpngw/current")[0] is None
    result = publication.prepare_release(
        api,
        store,
        release_setup,
        project="vpngw",
        number=2,
        completion_run=40,
        requested_tag="test-v1.3.0",
    )
    assert result["tag"] == "test-v1.3.0"
    assert store.read(key)[0]["requested_tag"] == "test-v1.3.0"


@pytest.mark.parametrize("requested_tag,queued", [(None, True), ("test-v1.4.0", False)])
def test_pending_release_preserves_only_unconditional_demand(
    release_setup, monkeypatch, requested_tag, queued
):
    store, api = general(MemoryStore()), ReleaseAPI()
    publication.prepare_release(
        api, store, release_setup, project="vpngw", number=2, completion_run=40
    )
    monkeypatch.setattr(publication, "completion", lambda *_: ({"result_sha": "f" * 40}, {}))
    with pytest.raises(Blocked, match="RELEASE_PENDING"):
        publication.prepare_release(
            api,
            store,
            release_setup,
            project="vpngw",
            number=2,
            completion_run=41,
            requested_tag=requested_tag,
        )
    record = store.read("repositories/1/releases/vpngw/requests/" + "f" * 40)[0]
    assert (record is not None) is queued
    if queued:
        assert record["requested_tag"] is None and record["status"] == "pending"
