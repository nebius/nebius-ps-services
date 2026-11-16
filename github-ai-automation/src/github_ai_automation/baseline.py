"""Explicit, independently reverified adoption of a historical release baseline."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from .contracts import Blocked, digest
from .controller import Heartbeat, trusted_runtime
from .publication import (
    download_release_asset,
    release_by_tag,
    verify_completed_release,
    verify_wheel,
)
from .state import acquire


def inspect(api, root, project, tag, run_id):
    registry = json.loads((root / "github-ai-automation/config/release-projects.json").read_text())
    if project not in registry["projects"]:
        raise Blocked("RELEASE_PROJECT_UNSUPPORTED")
    entry = registry["projects"][project]
    if not re.fullmatch(re.escape(entry["tag_prefix"]) + r"\d+\.\d+\.\d+", tag):
        raise Blocked("RELEASE_BASELINE_INVALID")
    release = release_by_tag(api, tag)
    if not release or release["draft"] or release["prerelease"]:
        raise Blocked("RELEASE_BASELINE_INVALID")
    ref = api.request("git/ref/tags/" + quote(tag, safe=""))
    if ref["object"]["type"] != "tag":
        raise Blocked("RELEASE_BASELINE_NOT_ANNOTATED")
    annotated = api.request("git/tags/" + ref["object"]["sha"])
    if annotated["object"]["type"] != "commit":
        raise Blocked("RELEASE_BASELINE_INVALID")
    sha = annotated["object"]["sha"]
    run = api.request(f"actions/runs/{run_id}")
    repo = api.request("")
    if (
        run["path"] != ".github/workflows/" + entry["workflow"]
        or run["event"] != "push"
        or run["head_branch"] != tag
        or run["head_sha"] != sha
        or run["repository"]["id"] != repo["id"]
        or run["head_repository"]["id"] != repo["id"]
        or run["status"] != "completed"
        or run["conclusion"] != "success"
    ):
        raise Blocked("RELEASE_BASELINE_PRODUCER_UNTRUSTED")
    # This is a documented adoption path for the original cxcli publisher,
    # not a fabricated publication-release/v1 attestation for old assets.
    if project != "nebius-cxcli":
        raise Blocked("RELEASE_BASELINE_FORMAT_UNSUPPORTED")
    name = f"nebius-cxcli-release-manifest-{run_id}"
    artifacts = [
        a
        for a in api.pages(f"actions/runs/{run_id}/artifacts", field="artifacts")
        if a["name"] == name
    ]
    if len(artifacts) != 1 or artifacts[0]["expired"]:
        raise Blocked("RELEASE_BASELINE_EVIDENCE_UNAVAILABLE")
    artifact = artifacts[0]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", artifact.get("digest", "")):
        raise Blocked("RELEASE_BASELINE_ARTIFACT_DIGEST_REQUIRED")
    original = api.artifact(run_id, name, "release-manifest.json")
    version = tag[len(entry["tag_prefix"]) :]
    expected = {
        "repository": api.repository,
        "event": "push",
        "ref": "refs/tags/" + tag,
        "tag": tag,
        "version": version,
        "sha": sha,
        "run_id": str(run_id),
        "run_attempt": str(run["run_attempt"]),
        "asset": f"dist/nebius_cxcli-{version}-py3-none-any.whl",
        "component_sources_asset": "release-assets/component_sources.yaml",
    }
    if any(original.get(k) != v for k, v in expected.items()):
        raise Blocked("RELEASE_BASELINE_EVIDENCE_INVALID")
    manifest, identities = [], []
    assets = api.pages(f"releases/{release['id']}/assets")
    names = {Path(original["asset"]).name, Path(original["component_sources_asset"]).name}
    if len(assets) != len(names) or {a["name"] for a in assets} != names:
        raise Blocked("RELEASE_ASSET_UNEXPECTED")
    with tempfile.TemporaryDirectory(prefix="ai-baseline-") as directory:
        for asset in sorted(assets, key=lambda a: a["name"]):
            data = download_release_asset(api, asset["id"])
            sha256 = hashlib.sha256(data).hexdigest()
            if asset.get("digest") != "sha256:" + sha256 or asset["size"] != len(data):
                raise Blocked("RELEASE_ASSET_DIGEST_MISMATCH")
            path = Path(directory) / asset["name"]
            path.write_bytes(data)
            if path.suffix == ".whl":
                verify_wheel(path, project, version)
            manifest.append({"name": asset["name"], "size": len(data), "sha256": sha256})
            identities.append({"name": asset["name"], "id": asset["id"]})
    record = {
        "schema": "release-baseline/v1",
        "provenance": "maintainer_adoption",
        "project": project,
        "status": "complete",
        "tag": tag,
        "version": version,
        "source_sha": sha,
        "tag_object": ref["object"]["sha"],
        "release_id": release["id"],
        "notes": release["body"],
        "manifest": manifest,
        "asset_ids": identities,
        "original_producer": {
            "run_id": run_id,
            "attempt": run["run_attempt"],
            "artifact_id": artifact["id"],
            "artifact_digest": artifact["digest"],
            "manifest": original,
            "manifest_sha256": digest(original),
        },
        "historical_build_digests_available": False,
    }
    verify_completed_release(api, record)
    return record


def adopt(api, store, root, project, tag, run_id, expected_digest, expected_revision, event, env):
    repo = trusted_runtime(api, env)
    actor = event.get("sender", {})
    if (
        actor.get("type") != "User"
        or str(actor.get("id")) != env.get("GITHUB_ACTOR_ID")
        or api.permission(actor.get("login", "")) not in {"maintain", "admin"}
    ):
        raise Blocked("BASELINE_ACTOR_UNAUTHORIZED")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_digest) or expected_revision < 0:
        raise Blocked("BASELINE_REVIEW_REQUIRED")
    key = f"repositories/{repo['id']}/releases/{project}"
    lease = acquire(store, key + "/lock")
    with Heartbeat(lease):
        try:
            current, _ = store.read(key + "/current")
            if current and current["status"] != "complete":
                raise Blocked("RELEASE_PENDING")
            previous, etag = store.read(key + "/baseline")
            if (previous or {}).get("revision", 0) != expected_revision:
                raise Blocked("BASELINE_REVISION_CHANGED")
            value = inspect(api, root, project, tag, run_id)
            if digest(value) != expected_digest:
                raise Blocked("BASELINE_REVIEW_CHANGED")
            record = {
                **value,
                "revision": expected_revision + 1,
                "adopted_by": actor["id"],
                "adoption_run": int(env["GITHUB_RUN_ID"]),
                "review_sha256": expected_digest,
            }
            lease.assert_owned()
            store.put(key + "/baseline", record, etag)
            return record
        finally:
            lease.release()
