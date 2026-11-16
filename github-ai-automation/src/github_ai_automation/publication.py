"""Single version/tag/publication authority for hosted and laptop requests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from email import message_from_bytes
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .contracts import Blocked, Fragment, digest
from .controller import Heartbeat, trusted_runtime
from .github import changed_paths
from .releases import affected_projects, asset_manifest, next_version, verify_assets
from .rollout import release_scope
from .state import acquire


def remember_request(store, key, *, source, number, completion_run, requested_tag=None):
    """Persist validated demand before dispatch or lock acquisition can be lost."""
    request_key = key + "/requests/" + source
    value, revision = store.read(request_key)
    if not value:
        value = {
            "source_sha": source,
            "pr_number": number,
            "completion_run": completion_run,
            "status": "pending",
            "requested_at": time.time_ns(),
            "requested_tag": requested_tag,
        }
        try:
            store.put(request_key, value, revision)
        except Blocked as exc:
            if exc.code != "STATE_CONFLICT":
                raise
            value, _ = store.read(request_key)
    if not value or value["source_sha"] != source or value["pr_number"] != number:
        raise Blocked("RELEASE_REQUEST_CONFLICT")


def finish_request(store, lease, key, source, *, status="complete"):
    request_key = key + "/requests/" + source
    value, revision = store.read(request_key)
    if value and value["status"] == "pending":
        lease.assert_owned()
        store.put(request_key, {**value, "status": status}, revision)


def release_intake(api, store, root, run_id):
    """Durable receipt routing. prepare_release repeats all admission checks."""
    if os.environ.get("AI_AUTOMATION_RELEASE_PUBLISH") != "true":
        raise Blocked("RELEASE_NOT_ENABLED")
    run = api.request(f"actions/runs/{run_id}")
    repo = api.request("")
    if (
        run["path"] != ".github/workflows/merge-completion.yml"
        or run["head_branch"] != repo["default_branch"]
        or run["conclusion"] != "success"
        or run["status"] != "completed"
        or run["repository"]["id"] != repo["id"]
    ):
        raise Blocked("MERGE_COMPLETION_UNTRUSTED")
    registry = json.loads((root / "github-ai-automation/config/release-projects.json").read_text())
    requests = []
    for artifact in api.pages(f"actions/runs/{run_id}/artifacts", field="artifacts"):
        match = re.fullmatch(
            r"merge-complete-(\d+)-[a-f0-9]{40}-" + str(run_id) + "-" + str(run["run_attempt"]),
            artifact["name"],
        )
        if not match:
            continue
        number = int(match[1])
        verified, _ = completion(api, root, number, run_id)
        paths = changed_paths(api.pages(f"pulls/{number}/files"))
        for project in affected_projects(paths, registry):
            release_scope(store, repo["id"], project, verified["result_sha"])
            remember_request(
                store,
                f"repositories/{repo['id']}/releases/{project}",
                source=verified["result_sha"],
                number=number,
                completion_run=run_id,
            )
            requests.append((project, number))
    # Persist the entire qualified batch before any dispatch can interrupt it.
    unavailable = False
    for project, number in requests:
        try:
            api.dispatch(
                "github-ai-release.yml",
                {"project": project, "pr_number": str(number), "completion_run": str(run_id)},
            )
        except Blocked:
            unavailable = True
    if unavailable:
        raise Blocked("RELEASE_DISPATCH_PENDING")


def completion(api, root: Path, number: int, run_id: int):
    run = api.request(f"actions/runs/{run_id}")
    repo = api.request("")
    if (
        run["path"] != ".github/workflows/merge-completion.yml"
        or run["head_branch"] != repo["default_branch"]
        or run["repository"]["id"] != repo["id"]
        or run["status"] != "completed"
        or run["conclusion"] != "success"
    ):
        raise Blocked("MERGE_COMPLETION_UNTRUSTED")
    pr = api.request(f"pulls/{number}")
    if not pr["merged"]:
        raise Blocked("RELEASE_SOURCE_NOT_MERGED")
    name = f"merge-complete-{number}-{pr['head']['sha']}-{run_id}-{run['run_attempt']}"
    value = api.artifact(run_id, name, "completion.json")
    if (
        value["schema"] != "merge-completion/v1"
        or value["outcome"] != "complete"
        or value["repository"] != api.repository
        or value["pr"] != number
        or value["head"] != pr["head"]["sha"]
        or value["result_sha"] != pr["merge_commit_sha"]
        or value["run_id"] != run_id
        or value["run_attempt"] != run["run_attempt"]
        or value["source_sha"] != run["head_sha"]
    ):
        raise Blocked("MERGE_COMPLETION_MISMATCH")
    # Reuse the exact-result verifier, including original protected merge intent.
    sys.path.insert(0, str(root / "skills/merge-pr/scripts"))
    import merge_completion

    old_api = merge_completion.GitHub(api.repository)
    policy = json.loads((root / ".github/merge-policy.json").read_text())
    files = api.pages(f"pulls/{number}/files")
    import fnmatch

    expected = {
        w["file"]
        for w in policy["ci_workflows"]
        if any(fnmatch.fnmatchcase(path, q) for path in changed_paths(files) for q in w["paths"])
    }
    if {v["workflow"] for v in value["ci"]} != expected:
        raise Blocked("RESULT_CI_POLICY_CHANGED")
    intent = {"pr": number, "head": value["head"], "base": repo["default_branch"]}
    for ci in value["ci"]:
        observed = old_api.api(f"actions/runs/{ci['run_id']}")
        if observed["run_attempt"] != ci["run_attempt"]:
            raise Blocked("RESULT_CI_RERUN_PENDING")
        merge_completion.validate_ci_run(
            old_api, observed, intent, value["result_sha"], ci["workflow"], policy
        )
    return value, pr


def previous_release(api, entry, retained=None):
    prefix = entry["tag_prefix"]
    releases = [
        r
        for r in api.pages("releases")
        if not r["draft"]
        and not r["prerelease"]
        and re.fullmatch(re.escape(prefix) + r"\d+\.\d+\.\d+", r["tag_name"])
    ]
    if not releases:
        raise Blocked("RELEASE_BASELINE_REQUIRED")
    latest = max(releases, key=lambda r: tuple(map(int, r["tag_name"][len(prefix) :].split("."))))
    tag = api.request("git/ref/tags/" + quote(latest["tag_name"], safe=""))
    if tag["object"]["type"] != "tag":
        raise Blocked("RELEASE_BASELINE_NOT_ANNOTATED")
    obj = api.request("git/tags/" + tag["object"]["sha"])
    if obj["object"]["type"] != "commit":
        raise Blocked("RELEASE_BASELINE_INVALID")
    identity = {
        "tag": latest["tag_name"],
        "tag_object": tag["object"]["sha"],
        "sha": obj["object"]["sha"],
    }
    if retained and retained["status"] == "complete" and retained["tag"] == latest["tag_name"]:
        if (
            retained["source_sha"] != identity["sha"]
            or retained["tag_object"] != identity["tag_object"]
        ):
            raise Blocked("RELEASE_BASELINE_CHANGED")
        verify_completed_release(api, retained)
        manifest = retained["manifest"]
    else:
        manifests = []
        for run in api.pages(
            f"actions/workflows/{entry['workflow']}/runs?event=push", field="workflow_runs"
        ):
            if (
                run["path"] != ".github/workflows/" + entry["workflow"]
                or run["head_branch"] != identity["tag"]
                or run["head_sha"] != identity["sha"]
                or run["head_repository"]["full_name"] != api.repository
                or run["status"] != "completed"
                or run["conclusion"] != "success"
            ):
                continue
            name = f"publication-release-{run['id']}-{run['run_attempt']}"
            artifacts = api.pages(f"actions/runs/{run['id']}/artifacts", field="artifacts")
            if not any(a["name"] == name and not a["expired"] for a in artifacts):
                continue
            value = api.artifact(run["id"], name, "publication.json")
            if (
                any(value.get(k) != v for k, v in identity.items())
                or value.get("schema") != "publication-release/v1"
                or value.get("repository") != api.repository
                or value.get("workflow") != entry["workflow"]
                or value.get("event") != "push"
                or value.get("ref") != "refs/tags/" + identity["tag"]
                or value.get("workflow_sha") != identity["sha"]
                or value.get("run_id") != run["id"]
                or value.get("run_attempt") != run["run_attempt"]
            ):
                raise Blocked("RELEASE_BASELINE_EVIDENCE_INVALID")
            manifests.append(value["assets"])
        if not manifests:
            raise Blocked("RELEASE_BASELINE_EVIDENCE_UNAVAILABLE")
        manifest = manifests[0]
        for other in manifests[1:]:
            verify_assets(manifest, other)
    actual = []
    for asset in api.pages(f"releases/{latest['id']}/assets"):
        data = download_release_asset(api, asset["id"])
        actual.append(
            {"name": asset["name"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    verify_assets(manifest, actual)
    return {
        "version": latest["tag_name"][len(prefix) :],
        "tag": latest["tag_name"],
        "source_sha": obj["object"]["sha"],
        "release_id": latest["id"],
    }


def read_fragments(api, previous: str, source: str, project: str):
    comparison = api.request(f"compare/{previous}...{source}")
    if (
        comparison["status"] not in {"ahead", "identical"}
        or len(comparison.get("files", [])) >= 300
    ):
        raise Blocked("RELEASE_HISTORY_OR_COVERAGE_UNAVAILABLE")
    fragments = []
    for file in comparison["files"]:
        if any(
            path.startswith("release-notes/") and path.endswith(".json")
            for path in changed_paths([file])
        ):
            if file["status"] != "added":
                raise Blocked("RELEASE_FRAGMENT_MUST_BE_IMMUTABLE")
            blob = api.request("git/blobs/" + file["sha"])
            import base64

            if blob["size"] > 8192 or blob["encoding"] != "base64":
                raise Blocked("RELEASE_FRAGMENT_INVALID")
            fragment = Fragment.model_validate_json(base64.b64decode(blob["content"]))
            if fragment.project == project:
                fragments.append(fragment)
    if not fragments:
        raise Blocked("RELEASE_FRAGMENT_MISSING")
    return fragments


def prepare_release(api, store, root, *, project, number, completion_run, requested_tag=None):
    repo = trusted_runtime(api)
    registry = json.loads((root / "github-ai-automation/config/release-projects.json").read_text())
    if project not in registry["projects"]:
        raise Blocked("RELEASE_PROJECT_UNSUPPORTED")
    if os.environ.get("AI_AUTOMATION_RELEASE_PUBLISH") != "true":
        raise Blocked("RELEASE_NOT_ENABLED")
    verified, pr = completion(api, root, number, completion_run)
    files = changed_paths(api.pages(f"pulls/{number}/files"))
    if project not in affected_projects(files, registry):
        raise Blocked("NO_PACKAGE_CHANGE")
    source = verified["result_sha"]
    generation = release_scope(store, repo["id"], project, source)
    entry = registry["projects"][project]
    key = f"repositories/{repo['id']}/releases/{project}"
    if not requested_tag:
        remember_request(store, key, source=source, number=number, completion_run=completion_run)
    lease = acquire(store, key + "/lock")
    with Heartbeat(lease):
        try:
            retained, etag = store.read(key + "/current")
            if retained and retained["status"] != "complete" and retained["source_sha"] != source:
                raise Blocked("RELEASE_PENDING")
            if retained and retained["source_sha"] == source:
                if requested_tag and retained["tag"] != requested_tag:
                    raise Blocked("RELEASE_TAG_CONFLICT")
                value = retained
            else:
                baseline, _ = store.read(key + "/baseline")
                previous = previous_release(api, entry, retained or baseline)
                comparison = api.request(f"compare/{source}...{previous['source_sha']}")
                if comparison["status"] in {"ahead", "identical"}:
                    finish_request(store, lease, key, source, status="covered")
                    raise Blocked("RELEASE_SOURCE_ALREADY_PUBLISHED")
                fragments = read_fragments(api, previous["source_sha"], source, project)
                version = next_version(previous["version"], fragments)
                tag = entry["tag_prefix"] + version
                if requested_tag and requested_tag != tag:
                    raise Blocked("REQUESTED_VERSION_DIFFERS_FROM_FRAGMENTS")
                notes = "\n".join("- " + f.summary for f in fragments) + "\n"
                value = {
                    "rollout_generation": generation,
                    "project": project,
                    "status": "reserved",
                    "source_sha": source,
                    "version": version,
                    "tag": tag,
                    "notes": notes,
                    "notes_sha256": digest(notes),
                    "previous": previous,
                    "pr_number": number,
                    "completion_run": completion_run,
                    "run_id": int(os.environ["GITHUB_RUN_ID"]),
                    "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                }
                lease.assert_owned()
                store.put(key + "/current", value, etag)
            if requested_tag:
                # Only a validated assertion may enter durable recovery. If a
                # different source is pending, its future version is not known.
                remember_request(
                    store,
                    key,
                    source=source,
                    number=number,
                    completion_run=completion_run,
                    requested_tag=requested_tag,
                )
            tag, notes = value["tag"], value["notes"]

            def require_scope():
                if "rollout_generation" not in value:
                    raise Blocked("ROLLOUT_RELEASE_NOT_ADMITTED")
                release_scope(
                    store,
                    repo["id"],
                    project,
                    source,
                    version=value["version"],
                    generation=value["rollout_generation"],
                )

            require_scope()
            lease.assert_owned()
            if not value.get("tag_object"):
                commit = api.request(f"git/commits/{source}")
                require_scope()
                obj = api.request(
                    "git/tags",
                    method="POST",
                    body={
                        "tag": tag,
                        "message": f"Release {tag}\n\n{notes}",
                        "object": source,
                        "type": "commit",
                        "tagger": {
                            "name": "github-actions[bot]",
                            "email": "41898282+github-actions[bot]@users.noreply.github.com",
                            "date": commit["committer"]["date"],
                        },
                    },
                )
                value["tag_object"] = obj["sha"]
                _, etag = store.read(key + "/current")
                lease.assert_owned()
                store.put(key + "/current", value, etag)
            existing = api.request("git/ref/tags/" + quote(tag, safe=""), absent=True)
            if existing and (
                existing["object"]["sha"] != value["tag_object"]
                or existing["object"]["type"] != "tag"
            ):
                raise Blocked("RELEASE_TAG_CONFLICT")
            if not existing:
                lease.assert_owned()
                require_scope()
                api.request(
                    "git/refs",
                    method="POST",
                    body={"ref": "refs/tags/" + tag, "sha": value["tag_object"]},
                )
            return {**value, "needs_build": "false" if value.get("manifest") else "true"}
        finally:
            lease.release()


def download_assets(api, run_id: int, attempt: int, project: str, destination: Path):
    name = f"release-assets-{project}-{run_id}-{attempt}"
    rows = [
        a
        for a in api.pages(f"actions/runs/{run_id}/artifacts", field="artifacts")
        if a["name"] == name
    ]
    if len(rows) != 1 or rows[0]["expired"] or rows[0]["size_in_bytes"] > 512_000_000:
        raise Blocked("RELEASE_BUILD_ARTIFACT_UNAVAILABLE")
    response = api.client.get(
        f"/repos/{api.repository}/actions/artifacts/{rows[0]['id']}/zip", follow_redirects=False
    )
    if response.status_code == 302 and response.headers["location"].startswith("https://"):
        response = httpx.get(response.headers["location"], timeout=120, follow_redirects=False)
    if response.status_code != 200 or len(response.content) > 512_000_000:
        raise Blocked("RELEASE_BUILD_DOWNLOAD_FAILED")
    import io

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        if (
            len(names) > 10
            or len(set(names)) != len(names)
            or sum(i.file_size for i in archive.infolist()) > 512_000_000
        ):
            raise Blocked("RELEASE_ARCHIVE_INVALID")
        for item in archive.infolist():
            if (
                PurePosixPath(item.filename).name != item.filename
                or item.external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise Blocked("RELEASE_ARCHIVE_INVALID")
            (destination / item.filename).write_bytes(archive.read(item))


def verify_wheel(path: Path, project: str, version: str):
    expected = {"vpngw": "nebius-vpngw", "nebius-cxcli": "nebius-cxcli"}[project]
    with zipfile.ZipFile(path) as archive:
        files = archive.infolist()
        if sum(i.file_size for i in files) > 512_000_000 or any(
            i.filename.startswith("/") or ".." in PurePosixPath(i.filename).parts for i in files
        ):
            raise Blocked("WHEEL_UNSAFE")
        names = [i.filename for i in files if i.filename.endswith(".dist-info/METADATA")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > 1_048_576:
            raise Blocked("WHEEL_METADATA_INVALID")
        meta = message_from_bytes(archive.read(names[0]))
        if meta["Version"] != version or meta["Name"].replace("_", "-") != expected:
            raise Blocked("WHEEL_IDENTITY_MISMATCH")


def download_release_asset(api, asset_id):
    response = api.client.get(
        f"/repos/{api.repository}/releases/assets/{asset_id}",
        headers={"Accept": "application/octet-stream"},
        follow_redirects=False,
    )
    if response.status_code == 302 and response.headers["location"].startswith("https://"):
        response = httpx.get(response.headers["location"], timeout=120, follow_redirects=False)
    if response.status_code != 200 or len(response.content) > 512_000_000:
        raise Blocked("RELEASE_DOWNLOAD_UNAVAILABLE")
    return response.content


def release_by_tag(api, tag):
    matches = [r for r in api.pages("releases") if r["tag_name"] == tag]
    if len(matches) > 1:
        raise Blocked("RELEASE_IDENTITY_AMBIGUOUS")
    return matches[0] if matches else None


def verify_completed_release(api, record):
    ref = api.request("git/ref/tags/" + quote(record["tag"], safe=""))
    if ref["object"]["type"] != "tag" or ref["object"]["sha"] != record["tag_object"]:
        raise Blocked("RELEASE_TAG_CONFLICT")
    tag = api.request("git/tags/" + record["tag_object"])
    if tag["object"]["type"] != "commit" or tag["object"]["sha"] != record["source_sha"]:
        raise Blocked("RELEASE_TAG_CONFLICT")
    release = release_by_tag(api, record["tag"])
    if (
        not release
        or release["id"] != record["release_id"]
        or release["draft"]
        or release["prerelease"]
        or release["body"] != record["notes"]
    ):
        raise Blocked("RELEASE_METADATA_CONFLICT")
    actual = []
    assets = api.pages(f"releases/{release['id']}/assets")
    if sorted(record["asset_ids"], key=lambda a: a["name"]) != sorted(
        [{"name": a["name"], "id": a["id"]} for a in assets], key=lambda a: a["name"]
    ):
        raise Blocked("RELEASE_ASSET_IDENTITY_CHANGED")
    for asset in assets:
        data = download_release_asset(api, asset["id"])
        actual.append(
            {"name": asset["name"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    verify_assets(record["manifest"], actual)
    return {"tag": record["tag"], "url": release["html_url"], "source_sha": record["source_sha"]}


def publish_release(api, store, root, project, source_sha, tag):
    repo = trusted_runtime(api)
    if os.environ.get("AI_AUTOMATION_RELEASE_PUBLISH") != "true":
        raise Blocked("RELEASE_NOT_ENABLED")
    registry = json.loads((root / "github-ai-automation/config/release-projects.json").read_text())
    entry = registry["projects"][project]
    key = f"repositories/{repo['id']}/releases/{project}"
    lease = acquire(store, key + "/lock")
    with Heartbeat(lease), tempfile.TemporaryDirectory(prefix="ai-release-") as directory:
        try:
            record, etag = store.read(key + "/current")
            if (
                not record
                or record["project"] != project
                or record["source_sha"] != source_sha
                or record["tag"] != tag
            ):
                raise Blocked("RELEASE_INTENT_MISSING")
            ref = api.request("git/ref/tags/" + quote(record["tag"], safe=""))
            if ref["object"]["sha"] != record["tag_object"] or ref["object"]["type"] != "tag":
                raise Blocked("RELEASE_TAG_CONFLICT")
            if record["status"] == "complete":
                verified = verify_completed_release(api, record)
                finish_request(store, lease, key, source_sha)
                return verified
            completion(api, root, record["pr_number"], record["completion_run"])
            assets = Path(directory)
            source_run, source_attempt = (
                record.get("asset_run", int(os.environ["GITHUB_RUN_ID"])),
                record.get("asset_attempt", int(os.environ["GITHUB_RUN_ATTEMPT"])),
            )
            prior = api.request(f"actions/runs/{source_run}/attempts/{source_attempt}")
            if (
                prior["path"] != ".github/workflows/github-ai-release.yml"
                or prior["head_branch"] != repo["default_branch"]
                or prior["event"] != "workflow_dispatch"
                or prior["repository"]["id"] != repo["id"]
                or prior["run_attempt"] != source_attempt
            ):
                raise Blocked("RELEASE_ASSET_PRODUCER_UNTRUSTED")
            if not record.get("manifest") and (
                source_run != int(os.environ["GITHUB_RUN_ID"])
                or source_attempt != int(os.environ["GITHUB_RUN_ATTEMPT"])
                or os.environ.get("RELEASE_BUILD_RESULT") != "success"
            ):
                raise Blocked("RELEASE_BUILD_NOT_SUCCESSFUL")
            download_assets(api, source_run, source_attempt, project, assets)
            manifest = asset_manifest(assets, entry["assets"])
            for asset in assets.glob("*.whl"):
                verify_wheel(asset, project, record["version"])
            if record.get("manifest"):
                verify_assets(record["manifest"], manifest)
            else:
                record.update(manifest=manifest, asset_run=source_run, asset_attempt=source_attempt)
                lease.assert_owned()
                store.put(key + "/current", record, etag)

            # Remote existence never supplies the expected hashes.
            def require_scope():
                if "rollout_generation" not in record:
                    raise Blocked("ROLLOUT_RELEASE_NOT_ADMITTED")
                release_scope(
                    store,
                    repo["id"],
                    project,
                    source_sha,
                    version=record["version"],
                    generation=record["rollout_generation"],
                    manifest=manifest,
                )

            require_scope()
            release = release_by_tag(api, record["tag"])
            if not release:
                lease.assert_owned()
                require_scope()
                release = api.request(
                    "releases",
                    method="POST",
                    body={
                        "tag_name": record["tag"],
                        "target_commitish": record["source_sha"],
                        "name": record["tag"],
                        "body": record["notes"],
                        "draft": True,
                        "prerelease": False,
                    },
                )
            if (
                release["prerelease"]
                or release["body"] != record["notes"]
                or release["author"]["id"] != 41898282
                or release["tag_name"] != record["tag"]
            ):
                raise Blocked("RELEASE_METADATA_CONFLICT")
            remote = {a["name"]: a for a in api.pages(f"releases/{release['id']}/assets")}
            if set(remote) - {a["name"] for a in manifest}:
                raise Blocked("RELEASE_ASSET_UNEXPECTED")
            for expected in manifest:
                name = expected["name"]
                if name not in remote:
                    lease.assert_owned()
                    require_scope()
                    response = api.client.post(
                        f"https://uploads.github.com/repos/{api.repository}/releases/{release['id']}/assets",
                        params={"name": name},
                        content=(assets / name).read_bytes(),
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=120,
                    )
                    if response.status_code != 201:
                        raise Blocked("RELEASE_UPLOAD_UNCERTAIN")
                    remote[name] = response.json()
                response = api.client.get(
                    f"/repos/{api.repository}/releases/assets/{remote[name]['id']}",
                    headers={"Accept": "application/octet-stream"},
                    follow_redirects=False,
                )
                if response.status_code == 302 and response.headers["location"].startswith(
                    "https://"
                ):
                    response = httpx.get(
                        response.headers["location"], timeout=120, follow_redirects=False
                    )
                if response.status_code != 200:
                    raise Blocked("RELEASE_DOWNLOAD_UNAVAILABLE")
                verify_assets(
                    [expected],
                    [
                        {
                            "name": name,
                            "size": len(response.content),
                            "sha256": hashlib.sha256(response.content).hexdigest(),
                        }
                    ],
                )
            if release["draft"]:
                lease.assert_owned()
                require_scope()
                release = api.request(
                    f"releases/{release['id']}", method="PATCH", body={"draft": False}
                )
            record.update(
                status="complete",
                release_id=release["id"],
                url=release["html_url"],
                asset_ids=sorted(
                    [{"name": name, "id": asset["id"]} for name, asset in remote.items()],
                    key=lambda a: a["name"],
                ),
            )
            _, etag = store.read(key + "/current")
            lease.assert_owned()
            store.put(key + "/current", record, etag)
            finish_request(store, lease, key, source_sha)
            return {"tag": record["tag"], "url": record["url"], "source_sha": record["source_sha"]}
        finally:
            lease.release()
