#!/usr/bin/env python3
"""Deterministic publication contracts and bounded, fail-closed read adapters.

No command in this helper creates a release or pushes a registry tag. Workflow
owners retain those narrowly scoped effects and service-specific qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

RELEASE_SCHEMA = "publication-release/v1"
IMAGE_SCHEMA = "publication-image/v1"
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class Unverifiable(RuntimeError):
    """Evidence is insufficient; no publication effect is permitted."""


class Conflict(Unverifiable):
    """Published content conflicts with the immutable contract."""


def require(condition, message):
    if not condition:
        raise Unverifiable(message)


def command(args, *, binary=False):
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    require(
        result.returncode == 0, "External observation failed; state is unverifiable"
    )
    return result.stdout if binary else result.stdout.decode().strip()


def version(tag, prefix):
    require(re.fullmatch(re.escape(prefix) + "-v" + SEMVER, tag), "Invalid release tag")
    return tag[len(prefix) + 2 :]


def release_notes(text, tag, release_version):
    heading = re.compile(
        r"^##\s+\[?(?:"
        + re.escape(tag)
        + "|"
        + re.escape(release_version)
        + r")\]?(?:\s+-.*)?$"
    )
    sections, current = [], None
    for line in text.splitlines():
        if re.match(r"^##\s+", line.strip()):
            current = [] if heading.fullmatch(line.strip()) else None
            if current is not None:
                sections.append(current)
        elif current is not None:
            current.append(line)
    require(
        len(sections) == 1 and "\n".join(sections[0]).strip(),
        "Missing, empty or ambiguous changelog section",
    )
    return "\n".join(sections[0]).strip() + "\n"


def fingerprint(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Asset must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def asset_records(paths):
    rows = sorted((fingerprint(p) for p in paths), key=lambda row: row["name"])
    require(
        rows and len({r["name"] for r in rows}) == len(rows),
        "Empty or duplicate asset basenames",
    )
    return rows


def tag_identity(tag):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag), "Invalid tag name")
    ref = f"refs/tags/{tag}"
    identity = {
        "tag": tag,
        "tag_object": command(["git", "rev-parse", "--verify", ref]),
        "sha": command(["git", "rev-parse", "--verify", ref + "^{commit}"]),
    }
    remote = command(["git", "ls-remote", "--refs", "origin", ref]).splitlines()
    require(
        remote == [identity["tag_object"] + "\t" + ref],
        "Remote tag object changed or disappeared",
    )
    return identity


def ancestry(commit, branch):
    require(SHA.fullmatch(commit), "Invalid commit")
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) and ".." not in branch,
        "Invalid release branch",
    )
    command(["git", "fetch", "origin", branch])
    command(["git", "merge-base", "--is-ancestor", commit, f"origin/{branch}"])


def runtime(workflow):
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow), "Invalid workflow filename"
    )
    repo = os.environ["GITHUB_REPOSITORY"]
    require(
        os.environ["GITHUB_WORKFLOW_REF"].startswith(
            f"{repo}/.github/workflows/{workflow}@"
        ),
        "Unexpected workflow identity",
    )
    return {
        "repository": repo,
        "workflow": workflow,
        "workflow_sha": os.environ["GITHUB_SHA"],
        "event": os.environ["GITHUB_EVENT_NAME"],
        "ref": os.environ["GITHUB_REF"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }


class GitHub:
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        require(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo),
            "Invalid repository",
        )

    def args(self, path):
        return [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            f"repos/{self.repo}/{path}",
        ]

    def api(self, path):
        return json.loads(command(self.args(path)))

    def pages(self, path, key=None):
        rows = []
        for page in range(1, 101):
            sep = "&" if "?" in path else "?"
            value = self.api(f"{path}{sep}per_page=100&page={page}")
            batch = value[key] if key else value
            require(isinstance(batch, list), "Invalid listing")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise Unverifiable("Incomplete listing; no effects permitted")

    def artifact(self, artifact):
        require(
            not artifact["expired"] and artifact["size_in_bytes"] <= 1024 * 1024,
            "Missing or oversized publication evidence",
        )
        raw = command(
            self.args(f"actions/artifacts/{int(artifact['id'])}/zip"), binary=True
        )
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(
                len(entries) == 1
                and entries[0].filename == "publication.json"
                and entries[0].file_size <= 1024 * 1024,
                "Invalid publication archive",
            )
            return json.loads(archive.read(entries[0]))

    def download(self, asset, directory):
        name = asset["name"]
        require(
            Path(name).name == name and name not in {".", ".."},
            "Invalid asset basename",
        )
        path = Path(directory) / name
        with path.open("xb") as stream:
            result = subprocess.run(
                self.args(f"releases/assets/{int(asset['id'])}")
                + ["-H", "Accept: application/octet-stream"],
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=300,
                check=False,
            )
        require(result.returncode == 0, "Asset download failed; state is unverifiable")
        return path


def trusted_manifests(api, workflow, schema, source_sha, tag=None):
    manifests = []
    # Run-scoped artifacts avoid untrusted repository-wide lookalike names.
    runs = api.pages(
        f"actions/workflows/{quote(workflow, safe='')}/runs", "workflow_runs"
    )
    for run in runs:
        if (
            run["path"] != f".github/workflows/{workflow}"
            or run["head_repository"]["full_name"] != api.repo
            or run["event"] not in {"push", "workflow_dispatch"}
        ):
            continue
        if tag and (
            run["event"] != "push"
            or run["head_branch"] != tag
            or run["head_sha"] != source_sha
        ):
            continue
        if not tag and (
            run["event"] != "workflow_dispatch"
            or run["head_branch"] != os.environ.get("MAIN_BRANCH", "main")
        ):
            continue
        for artifact in api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts"):
            match = re.fullmatch(
                r"publication-(release|image)-(\d+)-(\d+)", artifact["name"]
            )
            if (
                not match
                or f"publication-{match[1]}/v1" != schema
                or int(match[2]) != run["id"]
            ):
                continue
            if artifact["expired"]:
                continue
            value = api.artifact(artifact)
            require(isinstance(value, dict), "Invalid publication manifest")
            if value.get("sha") != source_sha:
                continue
            attempt = api.api(f"actions/runs/{run['id']}/attempts/{int(match[3])}")
            require(
                value["schema"] == schema
                and value["repository"] == api.repo
                and value["workflow"] == workflow
                and value["run_id"] == run["id"]
                and value["run_attempt"] == int(match[3])
                and value["workflow_sha"] == run["head_sha"]
                and value["event"] == run["event"]
                and attempt["path"] == run["path"]
                and attempt["head_sha"] == run["head_sha"],
                "Publication provenance mismatch",
            )
            # Historical workflow code must belong to the accepted main history.
            command(
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    run["head_sha"],
                    "origin/" + os.environ.get("MAIN_BRANCH", "main"),
                ]
            )
            expected_ref = (
                f"refs/tags/{tag}"
                if tag
                else f"refs/heads/{os.environ.get('MAIN_BRANCH', 'main')}"
            )
            require(value["ref"] == expected_ref, "Publication ref mismatch")
            manifests.append(value)
    return manifests


def verify_assets(expected, actual):
    if sorted(expected, key=lambda row: row["name"]) != sorted(
        actual, key=lambda row: row["name"]
    ):
        raise Conflict(
            "Release asset names, sizes or hashes differ from original evidence"
        )


def release_state(api, workflow, tag):
    identity = tag_identity(tag)
    releases = [r for r in api.pages("releases") if r["tag_name"] == tag]
    require(len(releases) <= 1, "Ambiguous release identity")
    if not releases:
        return "absent"
    release = releases[0]
    require(not release["draft"], "Partial draft release requires reconciliation")
    manifests = trusted_manifests(api, workflow, RELEASE_SCHEMA, identity["sha"], tag)
    require(
        manifests,
        "Existing release lacks trusted original hashes; maintainer reconciliation required",
    )
    for value in manifests:
        require(
            all(value[k] == v for k, v in identity.items()),
            "Release tag object or commit changed",
        )
        require(
            value["assets"] == manifests[0]["assets"],
            "Conflicting original release manifests",
        )
    assets = api.pages(f"releases/{int(release['id'])}/assets")
    with tempfile.TemporaryDirectory(prefix="release-verification-") as directory:
        actual = asset_records([api.download(asset, directory) for asset in assets])
    verify_assets(manifests[0]["assets"], actual)
    return "verified-existing"


def registry_absent(message):
    # Generic HTTP 404/not-found can be auth masking, proxy or DNS errors.
    text = message.lower()
    return bool(re.search(r"\bmanifest[_ ]unknown\b", text)) and not re.search(
        r"unauthorized|denied|forbidden|timeout|tls|certificate|connection|429|500|502|503",
        text,
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unverifiable(
            "Registry redirects require explicit endpoint reconciliation"
        )


def public_open(request):
    return build_opener(NoRedirect()).open(request, timeout=45)


def registry_get(repository, suffix):
    """Anonymous public OCI observation, including anonymous bearer auth.

    No stored credential is read or forwarded. Ambiguous HTTP errors fail closed.
    """
    value = urlsplit(repository.replace("oci://", "https://", 1))
    require(
        value.scheme == "https"
        and value.netloc
        and not value.username
        and not value.query
        and not value.fragment
        and ".." not in value.path,
        "Invalid public OCI repository",
    )
    url = f"https://{value.netloc}/v2/{value.path.strip('/')}/{suffix}"
    headers = {"Accept": "application/json"}
    for attempt in range(2):
        try:
            with public_open(Request(url, headers=headers)) as response:
                require(not response.headers.get("Link"), "Incomplete registry listing")
                raw = response.read(1024 * 1024 + 1)
                require(len(raw) <= 1024 * 1024, "Registry response is too large")
                return response.status, json.loads(raw)
        except HTTPError as error:
            if error.code == 401 and attempt == 0:
                challenge = error.headers.get("WWW-Authenticate", "")
                require(
                    challenge.lower().startswith("bearer "),
                    "Registry authorization unavailable",
                )
                fields = dict(re.findall(r'(\w+)="([^"\r\n]*)"', challenge))
                realm = urlsplit(fields.get("realm", ""))
                require(
                    realm.scheme == "https"
                    and realm.netloc
                    and not realm.username
                    and not realm.fragment,
                    "Invalid registry token endpoint",
                )
                params = {k: fields[k] for k in ("service", "scope") if k in fields}
                token_url = (
                    fields["realm"] + ("&" if realm.query else "?") + urlencode(params)
                )
                with public_open(token_url) as response:
                    token_value = json.loads(response.read(65536))
                require(
                    isinstance(token_value, dict), "Invalid anonymous token response"
                )
                token = token_value.get("token") or token_value.get("access_token")
                require(
                    isinstance(token, str)
                    and token
                    and "\n" not in token
                    and "\r" not in token,
                    "Anonymous registry token unavailable",
                )
                headers["Authorization"] = "Bearer " + token
                continue
            if error.code != 404:
                raise Unverifiable(
                    "Registry transport or authorization failed"
                ) from error
            try:
                body = json.loads(error.read(65536))
            except ValueError as decode_error:
                raise Unverifiable("Unstructured registry error") from decode_error
            return 404, body
    raise Unverifiable("Registry authorization failed")


def empty_repository(status, value):
    if status == 200:
        require(
            isinstance(value, dict)
            and "tags" in value
            and (value["tags"] is None or isinstance(value["tags"], list)),
            "Invalid registry tag listing",
        )
        if value["tags"]:
            raise Conflict("Duplicate nested chart repository contains tags")
        return True
    errors = value.get("errors", []) if isinstance(value, dict) else []
    require(
        status == 404
        and errors
        and all(
            isinstance(e, dict) and e.get("code") == "NAME_UNKNOWN" for e in errors
        ),
        "Registry repository absence is unverifiable",
    )
    return True


def image_digest(tag):
    result = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            tag,
            "--format",
            "{{json .Manifest}}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        if registry_absent(result.stderr):
            return None
        raise Unverifiable("Registry inspection failed; state is unverifiable")
    digest = json.loads(result.stdout)["digest"]
    require(DIGEST.fullmatch(digest), "Registry returned invalid digest")
    return digest


def immutable_state(observed, manifests, tags):
    if all(value is None for value in observed.values()):
        return "absent", ""
    if any(value is None for value in observed.values()):
        raise Conflict("Partial immutable image aliases require reconciliation")
    require(manifests, "Existing image lacks trusted original evidence")
    digests = {m["digest"] for m in manifests if set(tags).issubset(m["tags"])}
    require(len(digests) == 1, "Missing or conflicting original image evidence")
    expected = digests.pop()
    if not DIGEST.fullmatch(expected) or set(observed.values()) != {expected}:
        raise Conflict("Immutable image digest conflicts with original evidence")
    # Emit the registry observation after matching every alias to original evidence.
    return "verified-existing", next(iter(observed.values()))


def output(values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        for key, value in values.items():
            require("\n" not in str(value), "Invalid output value")
            stream.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "version",
            "ancestry",
            "notes",
            "release-state",
            "release-verify",
            "release-manifest",
            "image-state",
            "image-verify",
            "image-manifest",
            "registry-absent",
            "repository-empty",
        ],
    )
    parser.add_argument("--repository")
    parser.add_argument("--tag")
    parser.add_argument("--prefix")
    parser.add_argument("--commit")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--workflow")
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--output", default="publication.json")
    args = parser.parse_args()
    try:
        if args.action == "version":
            output({"version": version(args.tag, args.prefix), "tag": args.tag})
        elif args.action == "ancestry":
            ancestry(args.commit, args.branch)
        elif args.action == "notes":
            Path(args.output).write_text(
                release_notes(
                    Path("CHANGELOG.md").read_text(),
                    args.tag,
                    version(args.tag, args.prefix),
                )
            )
        elif args.action == "repository-empty":
            empty_repository(*registry_get(args.repository, "tags/list?n=100"))
        elif args.action == "registry-absent":
            require(registry_absent(sys.stdin.read()), "Registry state is unverifiable")
        elif args.action in {"release-state", "release-verify"}:
            state = release_state(GitHub(), args.workflow, args.tag)
            if args.action == "release-verify":
                require(
                    state == "verified-existing",
                    "Published release is not observable; reconciliation required",
                )
            output(
                {
                    "state": state,
                    "release_exists": str(state == "verified-existing").lower(),
                }
            )
        elif args.action == "release-manifest":
            value = {
                **runtime(args.workflow),
                **tag_identity(args.tag),
                "schema": RELEASE_SCHEMA,
                "assets": asset_records(args.asset),
            }
            require(
                value["event"] == "push"
                and value["ref"] == f"refs/tags/{args.tag}"
                and value["workflow_sha"] == value["sha"],
                "Release must use its exact tagged workflow",
            )
            previous = trusted_manifests(
                GitHub(), args.workflow, RELEASE_SCHEMA, value["sha"], args.tag
            )
            for original in previous:
                require(
                    all(original[k] == value[k] for k in ("tag", "tag_object", "sha")),
                    "Original tag identity changed",
                )
                verify_assets(original["assets"], value["assets"])
            Path(args.output).write_text(json.dumps(value, indent=2) + "\n")
        else:
            tags = os.environ["TAGS"].splitlines()
            require(
                tags and all(re.fullmatch(r"[A-Za-z0-9._:/-]+", t) for t in tags),
                "Invalid image tags",
            )
            if args.action == "image-state":
                immutable = [t for t in tags if not t.endswith(":latest")]
                require(immutable, "No immutable image aliases")
                observed = {t: image_digest(t) for t in immutable}
                manifests = []
                if any(observed.values()):
                    manifests = trusted_manifests(
                        GitHub(),
                        args.workflow,
                        IMAGE_SCHEMA,
                        args.commit,
                        args.tag or None,
                    )
                if args.tag:
                    identity = tag_identity(args.tag)
                    for manifest in manifests:
                        require(
                            all(manifest[k] == v for k, v in identity.items()),
                            "Original image tag identity changed",
                        )
                state, digest = immutable_state(observed, manifests, immutable)
                latest = [t for t in tags if t.endswith(":latest")]
                require(
                    not latest
                    or (
                        not args.tag
                        and len(latest) == 1
                        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                        and os.environ.get("PUBLISH_LATEST") == "true"
                        and os.environ.get("GITHUB_REF")
                        == "refs/heads/" + os.environ.get("MAIN_BRANCH", "main")
                    ),
                    "Latest requires an explicit manual request on main",
                )
                latest_needed = bool(
                    state == "verified-existing"
                    and latest
                    and image_digest(latest[0]) != digest
                )
                output(
                    {
                        "state": state,
                        "digest": digest,
                        "publish_needed": str(state == "absent").lower(),
                        "latest_needed": str(latest_needed).lower(),
                        "latest_ref": latest[0] if latest else "",
                        "verified_source": immutable[0].rsplit(":", 1)[0] + "@" + digest
                        if digest
                        else "",
                    }
                )
            else:
                digest = os.environ["IMAGE_DIGEST"]
                require(DIGEST.fullmatch(digest), "Missing build digest")
                require(
                    all(image_digest(t) == digest for t in tags),
                    "Published tag digest mismatch",
                )
                if args.action == "image-manifest":
                    value = {
                        **runtime(args.workflow),
                        "schema": IMAGE_SCHEMA,
                        "sha": args.commit,
                        "tags": tags,
                        "digest": digest,
                        "release_tag": args.tag or "",
                    }
                    if args.tag:
                        value.update(tag_identity(args.tag))
                    Path(args.output).write_text(json.dumps(value, indent=2) + "\n")
        return 0
    except (
        Unverifiable,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as error:
        kind = "conflict" if isinstance(error, Conflict) else "unverifiable"
        reason = (
            str(error)
            if isinstance(error, Unverifiable)
            else "Invalid or unavailable publication evidence"
        )
        print(f"{kind}: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
