import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from github_ai_automation import images
from github_ai_automation.contracts import Blocked
from github_ai_automation.sandbox import command


@pytest.mark.parametrize("selector", ["next", "main; exit 0", "1.2", "*", "latest\n"])
def test_version_selector_rejects_ambiguous_or_executable_values(selector, tmp_path):
    with pytest.raises(Blocked, match="CODEX_VERSION_INVALID"):
        images.resolve(tmp_path, selector)


def test_latest_resolves_once_to_integrity_bound_build_key(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "inputs_hash", lambda _: "a" * 64)
    metadata = {
        "name": "@openai/codex",
        "version": "1.2.3",
        "dist": {"integrity": "sha512-" + "A" * 86 + "=="},
    }
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=metadata)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        first = images.resolve(tmp_path, "latest", client)
        assert len(requests) == 1
        assert first["codex_version"] == "1.2.3"
        assert images.resolve(tmp_path, "1.2.3", client) == first
        metadata["dist"]["integrity"] = "sha512-" + "B" * 86 + "=="
        assert images.resolve(tmp_path, "latest", client)["key"] != first["key"]
        metadata["version"] = "2.0.0"
        with pytest.raises(Blocked, match="CODEX_METADATA_INVALID"):
            images.resolve(tmp_path, "1.2.3", client)


def archive(tmp_path, *, corrupt=False, arch="amd64"):
    blobs = {}

    def blob(value):
        data = json.dumps(value).encode()
        sha = hashlib.sha256(data).hexdigest()
        blobs["blobs/sha256/" + sha] = data
        return {"digest": "sha256:" + sha, "size": len(data)}

    config = blob({"architecture": arch, "os": "linux", "config": {"User": "10001:10001"}})
    manifest = blob({"schemaVersion": 2, "config": config, "layers": []})
    layers = [
        blob({"predicateType": predicate, "subject": []})
        for predicate in ("https://spdx.dev/Document", "https://slsa.dev/provenance/v1")
    ]
    attestation = blob({"schemaVersion": 2, "config": blob({}), "layers": layers})
    attestation["platform"] = {"architecture": "unknown", "os": "unknown"}
    attestation["annotations"] = {
        "vnd.docker.reference.type": "attestation-manifest",
        "vnd.docker.reference.digest": manifest["digest"],
    }
    manifest["platform"] = {"architecture": arch, "os": "linux"}
    manifest = blob({"schemaVersion": 2, "manifests": [manifest, attestation]})
    blobs["index.json"] = json.dumps({"schemaVersion": 2, "manifests": [manifest]}).encode()
    if corrupt:
        blobs["blobs/sha256/" + config["digest"][7:]] = b"{}"
    path = tmp_path / "image.oci"
    with tarfile.open(path, "w") as output:
        for name, data in blobs.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return path, config, manifest


def test_oci_verifier_binds_configuration_and_manifest_bytes(tmp_path):
    path, config, manifest = archive(tmp_path)
    result = images.archive_identity(path)
    assert result["config_digest"] == config["digest"]
    assert result["digest"] == manifest["digest"]
    assert result["archive_sha256"] == images.file_hash(path)


@pytest.mark.parametrize(
    "options,code",
    [
        ({"corrupt": True}, "IMAGE_ARCHIVE_INVALID"),
        ({"arch": "arm64"}, "IMAGE_PLATFORM_UNQUALIFIED"),
    ],
)
def test_changed_or_unqualified_archive_fails(tmp_path, options, code):
    path, *_ = archive(tmp_path, **options)
    with pytest.raises(Blocked, match=code):
        images.archive_identity(path)


def receipt():
    return {
        "schema": "image-publication/v1",
        "repository": "owner/repo",
        "run_id": 10,
        "attempt": 1,
        "checks": {name: True for name in images.CHECKS},
        "platform": "linux/amd64",
        "anonymous_pull": True,
        "digest": "sha256:" + "a" * 64,
        "image_ref": "ghcr.io/owner/repo/runtime@sha256:" + "a" * 64,
        "config_digest": "sha256:" + "b" * 64,
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "image-qualification/v1"),
        ("run_id", 11),
        ("anonymous_pull", False),
        ("platform", "linux/arm64"),
        ("checks", {}),
        ("image_ref", "ghcr.io/owner/repo/runtime:latest"),
    ],
)
def test_unpublished_or_changed_receipt_cannot_authorize_runtime(key, value):
    record = receipt()
    record[key] = value
    with pytest.raises(Blocked, match="IMAGE_RECEIPT_INVALID"):
        images.verify_receipt(record, repository="owner/repo", run_id=10, attempt=1)


def test_runtime_requires_qualification_reference_before_any_api_call(tmp_path):
    with pytest.raises(Blocked, match="IMAGE_QUALIFICATION_REQUIRED"):
        images.selected(None, tmp_path, {})


def test_command_bounds_output_and_drains_input():
    assert (
        command([sys.executable, "-c", "import sys; print(sys.stdin.read())"], stdin=b"bounded")
        == b"bounded\n"
    )
    with pytest.raises(Blocked, match="EXECUTION_OUTPUT_LIMIT"):
        command([sys.executable, "-c", 'import sys; sys.stdout.write("x"*6000000)'])
    with pytest.raises(Blocked, match="EXECUTION_TIMEOUT"):
        command([sys.executable, "-c", "import time; time.sleep(1)"], timeout=0.03)


def test_image_workflow_keeps_publication_and_model_credentials_separate():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "github-ai-automation/deployment/workflows/github-ai-image.yml.template").read_text()
    )
    jobs = workflow["jobs"]
    assert jobs["publish"]["permissions"]["packages"] == "write"
    for job in ("resolve", "build", "qualify", "verify"):
        assert "packages: write" not in json.dumps(jobs[job])
        assert jobs[job].get("permissions", {}).get("packages") != "write"
    for job in ("build", "publish", "verify"):
        assert "TF_API_KEY" not in json.dumps(jobs[job])
    assert all(
        "STATE_SECRET" not in json.dumps(jobs[j])
        for j in ("resolve", "build", "qualify", "publish", "verify")
    )
    assert "NEBIUS_CREDENTIALS" not in json.dumps(jobs)


def test_retained_active_image_survives_expired_actions_artifact(tmp_path, monkeypatch):
    from tests.support import MemoryStore

    from github_ai_automation.contracts import digest

    value = receipt()
    value.update(input={"inputs_sha256": "c" * 64}, models=["model"], source_sha="d" * 40)
    store = MemoryStore()
    key = "repositories/1/images/10"
    store.put(key, {"status": "verified", "receipt": value, "receipt_sha256": digest(value)}, None)

    class API:
        repository = "owner/repo"

        def request(self, path):
            assert path == ""
            return {"id": 1, "default_branch": "main"}

        def artifact(self, *args):
            raise AssertionError("Expired artifact consulted")

    monkeypatch.setattr(images, "inputs_hash", lambda _: "c" * 64)
    env = {
        "AI_AUTOMATION_IMAGE_QUALIFICATION_RUN_ID": "10",
        "AI_AUTOMATION_AGENT_IMAGE_REF": value["image_ref"],
        "AI_AUTOMATION_AGENT_IMAGE": value["digest"],
        "TF_MODEL_REVIEW": "model",
        "TF_MODEL_FIX": "model",
    }
    assert images.selected(API(), tmp_path, env, store=store) == value
    retained, revision = store.read(key)
    retained["receipt"]["config_digest"] = "sha256:" + "e" * 64
    store.put(key, retained, revision)
    with pytest.raises(Blocked, match="IMAGE_RECEIPT_INVALID"):
        images.selected(API(), tmp_path, env, store=store)


@pytest.mark.parametrize("changed", [False, True])
def test_concurrent_first_selection_requires_identical_verified_evidence(
    tmp_path, monkeypatch, changed
):
    from tests.support import MemoryStore

    from github_ai_automation.contracts import digest

    value = receipt()
    value.update(input={"inputs_sha256": "c" * 64}, models=["model"], source_sha="d" * 40)
    key = "repositories/1/images/10"

    class RacingStore(MemoryStore):
        def put(self, path, row, expected):
            if path == key and row["status"] == "verified":
                winner = {**row, "receipt_sha256": "e" * 64} if changed else row
                super().put(path, winner, expected)
                raise Blocked("STATE_CONFLICT")
            return super().put(path, row, expected)

    store = RacingStore()
    store.put(
        key, {"status": "producer_pending", "receipt": value, "receipt_sha256": digest(value)}, None
    )

    class API:
        repository = "owner/repo"

        def request(self, path):
            if path == "":
                return {"id": 1, "default_branch": "main"}
            return {
                "path": ".github/workflows/github-ai-image.yml",
                "head_branch": "main",
                "repository": {"id": 1},
                "event": "schedule",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "d" * 40,
                "run_attempt": 1,
            }

    monkeypatch.setattr(images, "inputs_hash", lambda _: "c" * 64)
    env = {
        "AI_AUTOMATION_IMAGE_QUALIFICATION_RUN_ID": "10",
        "AI_AUTOMATION_AGENT_IMAGE_REF": value["image_ref"],
        "AI_AUTOMATION_AGENT_IMAGE": value["digest"],
        "TF_MODEL_REVIEW": "model",
        "TF_MODEL_FIX": "model",
    }
    if changed:
        with pytest.raises(Blocked, match="STATE_CONFLICT"):
            images.selected(API(), tmp_path, env, store=store)
    else:
        assert images.selected(API(), tmp_path, env, store=store) == value


def test_hourly_noops_carry_original_qualification_beyond_history_window(tmp_path, monkeypatch):
    from github_ai_automation import image_cli

    value = receipt()
    value.update(input={"key": "key"}, models=["model"])

    class API:
        def request(self, path):
            assert "per_page=100" in path
            return {
                "workflow_runs": [
                    {
                        "id": 200,
                        "run_attempt": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "main",
                        "repository": {"id": 1},
                    }
                ]
            }

        def pages(self, *args, **kwargs):
            return [{"name": "image-input-200-1", "expired": False}]

        def artifact(self, run, name, file):
            assert (run, name, file) == (200, "image-input-200-1", "input.json")
            return {"key": "key", "reuse": value}

    outputs = {}
    monkeypatch.setattr(image_cli, "api", lambda **_: API())
    monkeypatch.setattr(images, "hosted", lambda _: ({"id": 1, "default_branch": "main"}, {}))
    monkeypatch.setattr(
        images,
        "resolve",
        lambda *_: {"key": "key", "codex_version": "1.2.3", "codex_integrity": "integrity"},
    )
    monkeypatch.setattr(image_cli, "output", lambda k, v: outputs.update({k: v}))
    monkeypatch.setenv("AI_AUTOMATION_IMAGE_PUBLISH", "true")
    monkeypatch.setenv("TF_MODEL_REVIEW", "model")
    monkeypatch.setenv("TF_MODEL_FIX", "model")
    destination = tmp_path / "input.json"
    image_cli.resolve(destination, tmp_path)
    assert outputs["changed"] == "false"
    assert json.loads(destination.read_text())["reuse"] == value


@pytest.mark.parametrize("event", ["push", "schedule"])
def test_default_off_automatic_image_run_does_not_start_build_or_inference(
    tmp_path, monkeypatch, event
):
    from github_ai_automation import image_cli

    outputs = {}
    monkeypatch.setattr(image_cli, "api", lambda **_: object())
    monkeypatch.setattr(images, "hosted", lambda _: ({"id": 1}, {"event": event}))
    monkeypatch.setattr(image_cli, "output", lambda k, v: outputs.update({k: v}))
    monkeypatch.delenv("AI_AUTOMATION_IMAGE_PUBLISH", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled automatic image build reached registry")

    monkeypatch.setattr(images, "resolve", forbidden)
    image_cli.resolve(tmp_path / "input.json", tmp_path)
    assert outputs == {"changed": "false"}
