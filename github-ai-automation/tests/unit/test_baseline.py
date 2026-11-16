import hashlib
from pathlib import Path

import pytest
from tests.support import MemoryStore

from github_ai_automation import baseline, publication
from github_ai_automation.contracts import Blocked, digest


@pytest.fixture
def release(monkeypatch):
    sha = "a" * 40
    tag = "nebius-cxcli-v0.1.9"
    key = "b" * 40
    assets = [
        {
            "id": 1,
            "name": "nebius_cxcli-0.1.9-py3-none-any.whl",
            "size": 5,
            "digest": "sha256:" + hashlib.sha256(b"wheel").hexdigest(),
        },
        {
            "id": 2,
            "name": "component_sources.yaml",
            "size": 7,
            "digest": "sha256:" + hashlib.sha256(b"catalog").hexdigest(),
        },
    ]

    class API:
        repository = "owner/repo"

        def request(self, path, **kwargs):
            assert kwargs.get("method", "GET") == "GET"
            if path == "":
                return {"id": 1}
            if path.startswith("git/ref/"):
                return {"object": {"type": "tag", "sha": key}}
            if path.startswith("git/tags/"):
                return {"object": {"type": "commit", "sha": sha}}
            if path == "actions/runs/10":
                return {
                    "id": 10,
                    "run_attempt": 1,
                    "path": ".github/workflows/nebius-cxcli-release.yml",
                    "event": "push",
                    "head_branch": tag,
                    "head_sha": sha,
                    "repository": {"id": 1},
                    "head_repository": {"id": 1},
                    "status": "completed",
                    "conclusion": "success",
                }
            raise AssertionError(path)

        def pages(self, path, **kwargs):
            if path == "releases":
                return [
                    {
                        "id": 4,
                        "tag_name": tag,
                        "draft": False,
                        "prerelease": False,
                        "body": "notes",
                        "html_url": "https://example.test/release",
                    }
                ]
            if path == "releases/4/assets":
                return assets
            if path.endswith("/artifacts"):
                return [
                    {
                        "id": 5,
                        "name": "nebius-cxcli-release-manifest-10",
                        "expired": False,
                        "digest": "sha256:" + "f" * 64,
                    }
                ]
            raise AssertionError(path)

        def artifact(self, *_):
            return {
                "repository": self.repository,
                "event": "push",
                "ref": "refs/tags/" + tag,
                "tag": tag,
                "version": "0.1.9",
                "sha": sha,
                "run_id": "10",
                "run_attempt": "1",
                "asset": "dist/nebius_cxcli-0.1.9-py3-none-any.whl",
                "component_sources_asset": "release-assets/component_sources.yaml",
            }

        def permission(self, _):
            return "maintain"

    monkeypatch.setattr(
        baseline, "download_release_asset", lambda _, i: b"wheel" if i == 1 else b"catalog"
    )
    monkeypatch.setattr(
        publication, "download_release_asset", lambda _, i: b"wheel" if i == 1 else b"catalog"
    )
    monkeypatch.setattr(baseline, "verify_wheel", lambda *_: None)
    return API(), assets, Path(__file__).resolve().parents[3]


def test_inspection_preserves_original_provenance_without_mutations(release):
    api, _, root = release
    value = baseline.inspect(api, root, "nebius-cxcli", "nebius-cxcli-v0.1.9", 10)
    assert value["provenance"] == "maintainer_adoption"
    assert value["historical_build_digests_available"] is False
    assert value["original_producer"]["artifact_id"] == 5
    assert len(value["asset_ids"]) == 2


def test_baseline_rejects_replaced_asset_even_with_identical_bytes(release):
    api, assets, root = release
    value = baseline.inspect(api, root, "nebius-cxcli", "nebius-cxcli-v0.1.9", 10)
    assets[0]["id"] = 99
    with pytest.raises(Blocked, match="RELEASE_ASSET_IDENTITY_CHANGED"):
        publication.verify_completed_release(api, value)


def test_adoption_is_compare_and_set_and_requires_exact_review(release, monkeypatch):
    api, _, root = release
    store = MemoryStore()
    event = {"sender": {"type": "User", "id": 7, "login": "maintainer"}}
    env = {"GITHUB_ACTOR_ID": "7", "GITHUB_RUN_ID": "11"}
    monkeypatch.setattr(baseline, "trusted_runtime", lambda *_: {"id": 1})
    inspected = baseline.inspect(api, root, "nebius-cxcli", "nebius-cxcli-v0.1.9", 10)
    with pytest.raises(Blocked, match="BASELINE_REVIEW_CHANGED"):
        baseline.adopt(
            api, store, root, "nebius-cxcli", "nebius-cxcli-v0.1.9", 10, "0" * 64, 0, event, env
        )
    adopted = baseline.adopt(
        api,
        store,
        root,
        "nebius-cxcli",
        "nebius-cxcli-v0.1.9",
        10,
        digest(inspected),
        0,
        event,
        env,
    )
    assert adopted["revision"] == 1
    with pytest.raises(Blocked, match="BASELINE_REVISION_CHANGED"):
        baseline.adopt(
            api,
            store,
            root,
            "nebius-cxcli",
            "nebius-cxcli-v0.1.9",
            10,
            digest(inspected),
            0,
            event,
            env,
        )


def test_original_manifest_cannot_stand_in_for_current_asset_hashes(release):
    api, assets, root = release
    assets[0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(Blocked, match="RELEASE_ASSET_DIGEST_MISMATCH"):
        baseline.inspect(api, root, "nebius-cxcli", "nebius-cxcli-v0.1.9", 10)
