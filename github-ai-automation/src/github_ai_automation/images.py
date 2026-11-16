"""Resolve, qualify and verify immutable runtime images; never auto-promote them."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import Field

from .bundle import package_skills
from .contracts import Blocked, Contract, digest
from .sandbox import command, run_agent

WORKFLOW = "github-ai-image.yml"
CHECKS = {
    "isolation",
    "codex_version",
    "responses_tools",
    "tool_error_recovery",
    "structured_output",
    "sbom_provenance",
}


def inputs_hash(root: Path) -> str:
    """Hash the reviewed image/protocol closure, not incidental Git commit metadata."""
    project = root / "github-ai-automation"
    files = [
        project / "Dockerfile",
        project / ".dockerignore",
        project / "uv.lock",
        root / ".github/workflows" / WORKFLOW,
    ]
    files += sorted((project / "src/github_ai_automation").glob("*.py"))
    files = [p for p in files if p.name != "_version.py"]
    files += sorted((project / "config").glob("*.json"))
    with tempfile.TemporaryDirectory(prefix="ai-image-skills-") as directory:
        skills = package_skills(root, project / "config/skills.json", Path(directory) / "skills")
    return digest(
        {
            "files": {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
            },
            "skills": skills,
        }
    )


def resolve(root, selector, client=None):
    if selector != "latest" and not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", selector):
        raise Blocked("CODEX_VERSION_INVALID")
    if client is None:
        with httpx.Client(timeout=30, follow_redirects=False) as transport:
            return resolve(root, selector, transport)
    response = client.get("https://registry.npmjs.org/@openai%2Fcodex/" + quote(selector, safe=""))
    if response.status_code != 200 or len(response.content) > 1_000_000:
        raise Blocked("CODEX_METADATA_UNAVAILABLE")
    metadata = response.json()
    version = metadata.get("version", "")
    integrity = metadata.get("dist", {}).get("integrity", "")
    if (
        not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version)
        or selector not in {"latest", version}
        or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity)
        or metadata.get("name") != "@openai/codex"
    ):
        raise Blocked("CODEX_METADATA_INVALID")
    value = {
        "schema": "image-input/v1",
        "codex_version": version,
        "codex_integrity": integrity,
        "inputs_sha256": inputs_hash(root),
    }
    return {**value, "key": digest(value)}


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_identity(archive):
    """Inspect OCI descriptors without extracting attacker-controlled paths."""
    with tarfile.open(archive) as source:

        def read(name):
            member = source.getmember(name)
            if not member.isfile() or member.size > 8_000_000:
                raise Blocked("IMAGE_ARCHIVE_INVALID")
            return source.extractfile(member).read()

        def blob(descriptor):
            sha = descriptor.get("digest", "")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", sha):
                raise Blocked("IMAGE_ARCHIVE_INVALID")
            data = read("blobs/sha256/" + sha[7:])
            if hashlib.sha256(data).hexdigest() != sha[7:] or len(data) != descriptor["size"]:
                raise Blocked("IMAGE_ARCHIVE_INVALID")
            return json.loads(data)

        index = json.loads(read("index.json"))
        descriptors = index.get("manifests", [])
        if len(descriptors) != 1:
            raise Blocked("IMAGE_ARCHIVE_INVALID")
        top = descriptors[0]
        manifest = blob(top)
        predicates = set()
        if "manifests" in manifest:
            images = [
                d
                for d in manifest["manifests"]
                if d.get("platform") == {"architecture": "amd64", "os": "linux"}
            ]
            if len(images) != 1:
                raise Blocked("IMAGE_PLATFORM_UNQUALIFIED")
            for descriptor in manifest["manifests"]:
                annotations = descriptor.get("annotations", {})
                if annotations.get("vnd.docker.reference.type") != "attestation-manifest":
                    continue
                if annotations.get("vnd.docker.reference.digest") != images[0]["digest"]:
                    raise Blocked("IMAGE_ATTESTATION_CHANGED")
                for layer in blob(descriptor)["layers"]:
                    statement = blob(layer)
                    subjects = statement.get("subject", [])
                    # BuildKit's OCI exporter leaves subjects empty; the immutable
                    # attestation descriptor above binds them to the image manifest.
                    if not isinstance(subjects, list) or any(
                        s.get("digest", {}).get("sha256") != images[0]["digest"][7:]
                        for s in subjects
                    ):
                        raise Blocked("IMAGE_ATTESTATION_CHANGED")
                    predicates.add(statement.get("predicateType"))
            manifest = blob(images[0])
        if "https://spdx.dev/Document" not in predicates or not predicates & {
            "https://slsa.dev/provenance/v0.2",
            "https://slsa.dev/provenance/v1",
        }:
            raise Blocked("IMAGE_ATTESTATIONS_REQUIRED")
        config = manifest["config"]
        configuration = blob(config)
        if configuration.get("architecture") != "amd64" or configuration.get("os") != "linux":
            raise Blocked("IMAGE_PLATFORM_UNQUALIFIED")
        if configuration.get("config", {}).get("User") != "10001:10001":
            raise Blocked("IMAGE_USER_INVALID")
    return {
        "archive_sha256": file_hash(archive),
        "digest": top["digest"],
        "config_digest": config["digest"],
    }


def hosted(api, env=os.environ, *, completed=None):
    repo = api.request("")
    run = api.request(f"actions/runs/{env['GITHUB_RUN_ID']}/attempts/{env['GITHUB_RUN_ATTEMPT']}")
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or repo["full_name"] != api.repository
        or str(repo["id"]) != env.get("AI_AUTOMATION_REPOSITORY_ID")
        or env.get("GITHUB_REF") != "refs/heads/" + repo["default_branch"]
        or run["path"] != ".github/workflows/" + WORKFLOW
        or run["head_branch"] != repo["default_branch"]
        or run["head_sha"] != env["GITHUB_SHA"]
        or run["repository"]["id"] != repo["id"]
        or run["event"] not in {"push", "schedule", "workflow_dispatch"}
    ):
        raise Blocked("IMAGE_PRODUCER_UNTRUSTED")
    if completed:
        jobs = api.pages(
            f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", field="jobs"
        )
        for name in completed:
            matches = [j for j in jobs if j["name"] == name]
            if (
                len(matches) != 1
                or matches[0]["conclusion"] != "success"
                or matches[0]["status"] != "completed"
            ):
                raise Blocked("IMAGE_QUALIFICATION_INCOMPLETE")
    return repo, run


class Probe(Contract):
    challenge: str = Field(min_length=32, max_length=128)
    recovered: bool


def qualify(api, root, archive, spec, destination, env=os.environ):
    _, run = hosted(api, env, completed={"build"})
    if spec["inputs_sha256"] != inputs_hash(root) or spec["key"] != digest(
        {k: v for k, v in spec.items() if k != "key"}
    ):
        raise Blocked("IMAGE_BUILD_INPUT_CHANGED")
    identity = archive_identity(archive)
    tag = "ai-image-qualification:" + str(run["id"])
    command(
        [
            "skopeo",
            "--override-arch",
            "amd64",
            "copy",
            "oci-archive:" + str(archive),
            "docker-daemon:" + tag,
        ]
    )
    observed = command(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).decode().strip()
    loaded = json.loads(command(["skopeo", "inspect", "--raw", "docker-daemon:" + tag]))
    if loaded.get("config", {}).get("digest") != identity["config_digest"]:
        raise Blocked("IMAGE_BYTES_CHANGED")
    image = observed
    version = (
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                image,
                "codex",
                "--version",
            ]
        )
        .decode()
        .strip()
    )
    if version != "codex-cli " + spec["codex_version"]:
        raise Blocked("CODEX_VERSION_CHANGED")
    probe = """import os,socket,pathlib
assert os.getuid()==10001
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not any(k.startswith(('GH_','AWS_','NEBIUS_','AI_AUTOMATION_')) for k in os.environ)
assert int(next(l.split(':')[1].strip() for l in pathlib.Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:')),16)==0
try: pathlib.Path('/root-write-test').write_text('x')
except OSError: pass
else: raise RuntimeError('writable root')
for target in ('1.1.1.1','169.254.169.254'):
 try: socket.create_connection((target,80),timeout=2).close()
 except OSError: pass
 else: raise RuntimeError('network escaped')
"""
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            image,
            "python3",
            "-I",
            "-c",
            probe,
        ]
    )
    models = sorted({env.get("TF_MODEL_REVIEW", ""), env.get("TF_MODEL_FIX", "")})
    if "" in models:
        raise Blocked("MODEL_NOT_CONFIGURED")
    with tempfile.TemporaryDirectory(prefix="ai-image-probe-") as directory:
        scratch = Path(directory)
        source = scratch / "source"
        source.mkdir(mode=0o755)
        challenge = secrets.token_hex(32)
        (source / "challenge.txt").write_text(challenge)
        bundle = scratch / "skills"
        skills = package_skills(root, root / "github-ai-automation/config/skills.json", bundle)
        for index, model in enumerate(models):
            telemetry = {}
            result = run_agent(
                image=image,
                source=source,
                bundle=bundle,
                scratch=scratch / str(index),
                model=model,
                api_key=env.get("AI_AUTOMATION_TF_API_KEY", ""),
                output_type=Probe,
                prompt="Qualification: run a shell command that exits 7, observe its failure, then use a shell tool to read /source/challenge.txt. Return that exact content as challenge and recovered=true. You must actually execute both tools.",
                timeout=300,
                telemetry=telemetry,
            )
            if (
                result.challenge != challenge
                or not result.recovered
                or not telemetry.get("tool_success")
                or not telemetry.get("tool_failure")
            ):
                raise Blocked("IMAGE_PROTOCOL_UNQUALIFIED")
    value = {
        "schema": "image-qualification/v1",
        "repository": api.repository,
        "source_sha": run["head_sha"],
        "run_id": run["id"],
        "attempt": run["run_attempt"],
        "input": spec,
        **identity,
        "skills_sha256": skills,
        "models": models,
        "checks": {name: True for name in sorted(CHECKS)},
        "platform": "linux/amd64",
    }
    destination.write_text(json.dumps(value, sort_keys=True) + "\n")
    return value


def verify_receipt(value, *, repository, run_id, attempt, image_ref=None, runtime_digest=None):
    if (
        value.get("schema") != "image-publication/v1"
        or value.get("repository") != repository
        or value.get("run_id") != run_id
        or value.get("attempt") != attempt
        or value.get("checks") != {name: True for name in CHECKS}
        or value.get("platform") != "linux/amd64"
        or value.get("anonymous_pull") is not True
        or not re.fullmatch(
            r"ghcr.io/[a-z0-9./_-]+@sha256:[a-f0-9]{64}", value.get("image_ref", "")
        )
        or value["image_ref"].split("@")[-1] != value.get("digest")
        or (image_ref is not None and value["image_ref"] != image_ref)
        or (runtime_digest is not None and value.get("digest") != runtime_digest)
    ):
        raise Blocked("IMAGE_RECEIPT_INVALID")
    return value


def selected(api, root, env=os.environ, *, store=None):
    """Require provenance from a completed trusted publisher, never a candidate artifact."""
    run_id = env.get("AI_AUTOMATION_IMAGE_QUALIFICATION_RUN_ID", "")
    if not re.fullmatch(r"[1-9]\d*", run_id):
        raise Blocked("IMAGE_QUALIFICATION_REQUIRED")
    repo = api.request("")
    key = f"repositories/{repo['id']}/images/{run_id}"
    retained, revision = store.read(key) if store else (None, None)
    if retained:
        value = retained["receipt"]
        attempt = value["attempt"]
        if retained.get("receipt_sha256") != digest(value) or retained.get("status") not in {
            "verified",
            "producer_pending",
        }:
            raise Blocked("IMAGE_RECEIPT_INVALID")
    else:
        value = None
    if not retained or retained["status"] != "verified":
        run = api.request("actions/runs/" + run_id)
        if (
            run["path"] != ".github/workflows/" + WORKFLOW
            or run["head_branch"] != repo["default_branch"]
            or run["repository"]["id"] != repo["id"]
            or run["event"] not in {"push", "schedule", "workflow_dispatch"}
            or run["status"] != "completed"
            or run["conclusion"] != "success"
        ):
            raise Blocked("IMAGE_PRODUCER_UNTRUSTED")
        if value is None:
            value = api.artifact(
                int(run_id), f"image-publication-{run_id}-{run['run_attempt']}", "publication.json"
            )
        attempt = run["run_attempt"]
        if value["source_sha"] != run["head_sha"]:
            raise Blocked("IMAGE_PRODUCER_UNTRUSTED")
    verify_receipt(
        value,
        repository=api.repository,
        run_id=int(run_id),
        attempt=attempt,
        image_ref=env.get("AI_AUTOMATION_AGENT_IMAGE_REF", ""),
        runtime_digest=env.get("AI_AUTOMATION_AGENT_IMAGE", ""),
    )
    if value["input"]["inputs_sha256"] != inputs_hash(root) or not {
        env.get("TF_MODEL_REVIEW", ""),
        env.get("TF_MODEL_FIX", env.get("TF_MODEL_REVIEW", "")),
    } <= set(value["models"]):
        raise Blocked("IMAGE_QUALIFICATION_STALE")
    if store and (not retained or retained["status"] != "verified"):
        verified = {"status": "verified", "receipt": value, "receipt_sha256": digest(value)}
        try:
            store.put(key, verified, revision)
        except Blocked as exc:
            if exc.code != "STATE_CONFLICT" or store.read(key)[0] != verified:
                raise
    return value
