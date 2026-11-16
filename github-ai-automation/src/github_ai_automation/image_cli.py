"""Internal image workflow commands. Publication never changes the selected runtime."""

import json
import os
import tempfile
from pathlib import Path

import typer

from . import images
from .contracts import Blocked, digest
from .hosted_cli import api, output, read
from .sandbox import command

app = typer.Typer(no_args_is_help=True)


@app.command()
def resolve(destination: Path, root: Path = Path(".")):
    gh = api(readonly=True)
    repo, run = images.hosted(gh)
    publish = os.environ.get("AI_AUTOMATION_IMAGE_PUBLISH", "false") == "true"
    if not publish and run.get("event") != "workflow_dispatch":
        destination.write_text('{"schema":"image-disabled/v1"}\n')
        output("changed", "false")
        return
    value = images.resolve(root.resolve(), os.environ.get("AI_AUTOMATION_CODEX_VERSION", "latest"))
    unchanged = False
    recent = gh.request(f"actions/workflows/{images.WORKFLOW}/runs?per_page=100&page=1")[
        "workflow_runs"
    ]
    for run in recent:
        if (
            run["status"] != "completed"
            or run["conclusion"] != "success"
            or run["head_branch"] != repo["default_branch"]
            or run["repository"]["id"] != repo["id"]
        ):
            continue
        kind = "publication" if publish else "qualification"
        name = f"image-{kind}-{run['id']}-{run['run_attempt']}"
        input_name = f"image-input-{run['id']}-{run['run_attempt']}"
        artifacts = gh.pages(f"actions/runs/{run['id']}/artifacts", field="artifacts")
        available = {a["name"] for a in artifacts if not a["expired"]}
        if name in available:
            prior = gh.artifact(run["id"], name, kind + ".json")
        elif input_name in available:
            # Carry the original evidence through successful no-op runs. Otherwise
            # hourly no-ops eventually push an unchanged build out of this window.
            prior = gh.artifact(run["id"], input_name, "input.json").get("reuse", {})
        else:
            continue
        if (
            prior.get("schema") == f"image-{kind}/v1"
            and prior.get("input", {}).get("key") == value["key"]
            and set(prior.get("models", []))
            == {os.environ.get("TF_MODEL_REVIEW"), os.environ.get("TF_MODEL_FIX")}
            and prior.get("checks") == {name: True for name in images.CHECKS}
        ):
            unchanged = True
            value["reuse"] = prior
            break
    destination.write_text(json.dumps(value, sort_keys=True) + "\n")
    output("changed", str(not unchanged).lower())
    output("key", value["key"])
    output("codex_version", value["codex_version"])
    output("codex_integrity", value["codex_integrity"])


@app.command()
def qualify(archive: Path, spec: Path, destination: Path, root: Path = Path(".")):
    images.qualify(api(readonly=True), root.resolve(), archive, read(spec), destination)


@app.command()
def publish(archive: Path, qualification: Path, destination: Path, root: Path = Path(".")):
    if os.environ.get("AI_AUTOMATION_IMAGE_PUBLISH", "false") != "true":
        raise Blocked("IMAGE_PUBLISH_DISABLED")
    gh = api(readonly=True)
    _, run = images.hosted(gh, completed={"build", "qualify"})
    value = read(qualification)
    expected = images.archive_identity(archive)
    if (
        value.get("schema") != "image-qualification/v1"
        or value.get("repository") != gh.repository
        or value.get("run_id") != run["id"]
        or value.get("attempt") != run["run_attempt"]
        or value.get("source_sha") != run["head_sha"]
        or value.get("input", {}).get("inputs_sha256") != images.inputs_hash(root.resolve())
        or value.get("checks") != {name: True for name in images.CHECKS}
        or any(value.get(key) != val for key, val in expected.items())
    ):
        raise Blocked("IMAGE_QUALIFICATION_INVALID")
    # Repository identity, not a caller-supplied URL, owns the destination.
    repository = "ghcr.io/" + gh.repository.lower() + "/github-ai-automation"
    target = repository + ":build-" + value["input"]["key"][:32] + "-" + str(run["id"])
    # The package-write token exists only in this short publisher job and auth file.
    with tempfile.TemporaryDirectory(prefix="ai-registry-") as directory:
        auth = str(Path(directory) / "auth.json")
        command(
            [
                "skopeo",
                "login",
                "--authfile",
                auth,
                "--username",
                os.environ["GITHUB_ACTOR"],
                "--password-stdin",
                "ghcr.io",
            ],
            stdin=os.environ["GH_TOKEN"].encode(),
        )
        command(
            [
                "skopeo",
                "copy",
                "--all",
                "--preserve-digests",
                "--authfile",
                auth,
                "oci-archive:" + str(archive),
                "docker://" + target,
            ],
            timeout=600,
        )
    # Another runner performs the clean anonymous pull. No published receipt yet.
    destination.write_text(
        json.dumps(
            {**value, "schema": "image-pushed/v1", "image_ref": repository + "@" + value["digest"]},
            sort_keys=True,
        )
        + "\n"
    )


@app.command()
def verify(source: Path, destination: Path):
    gh = api(readonly=True)
    _, run = images.hosted(gh, completed={"build", "qualify", "publish"})
    value = read(source)
    if (
        value.get("schema") != "image-pushed/v1"
        or value.get("run_id") != run["id"]
        or value.get("attempt") != run["run_attempt"]
        or value.get("source_sha") != run["head_sha"]
    ):
        raise Blocked("IMAGE_PUBLICATION_INVALID")
    reference = value["image_ref"]
    images.verify_receipt(
        {**value, "schema": "image-publication/v1", "anonymous_pull": True},
        repository=gh.repository,
        run_id=run["id"],
        attempt=run["run_attempt"],
    )
    with tempfile.TemporaryDirectory(prefix="ai-anonymous-pull-") as directory:
        auth = Path(directory) / "auth.json"
        auth.write_text('{"auths": {}}')
        archive = Path(directory) / "anonymous.oci"
        command(
            [
                "skopeo",
                "copy",
                "--all",
                "--preserve-digests",
                "--src-no-creds",
                "--authfile",
                str(auth),
                "docker://" + reference,
                "oci-archive:" + str(archive),
            ],
            timeout=600,
        )
        actual = images.archive_identity(archive)
        if actual["digest"] != value["digest"] or actual["config_digest"] != value["config_digest"]:
            raise Blocked("IMAGE_ANONYMOUS_PULL_CHANGED")
    destination.write_text(
        json.dumps(
            {**value, "schema": "image-publication/v1", "anonymous_pull": True}, sort_keys=True
        )
        + "\n"
    )
    output("image_ref", reference)
    typer.echo(reference)
    typer.echo(
        "Promotion is manual: select this digest and this qualification run ID after reviewing evidence."
    )


@app.command()
def selected(destination: Path, root: Path = Path(".")):
    from .controller import configured_store

    value = images.selected(api(readonly=True), root.resolve(), store=configured_store())
    destination.write_text(json.dumps(value, sort_keys=True) + "\n")
    output("image_ref", value["image_ref"])
    typer.echo(value["image_ref"])


@app.command()
def retain(source: Path):
    """Retain the completed qualification independently from Actions artifact expiry."""
    from .controller import configured_store

    gh = api(readonly=True)
    repo, run = images.hosted(gh, completed={"build", "qualify", "publish", "verify"})
    value = read(source)
    images.verify_receipt(
        value, repository=gh.repository, run_id=run["id"], attempt=run["run_attempt"]
    )
    if value["source_sha"] != run["head_sha"]:
        raise Blocked("IMAGE_PRODUCER_UNTRUSTED")
    store = configured_store()
    key = f"repositories/{repo['id']}/images/{run['id']}"
    previous, revision = store.read(key)
    if previous:
        if previous["receipt"] != value or previous["receipt_sha256"] != digest(value):
            raise Blocked("IMAGE_RECEIPT_CONFLICT")
        return
    store.put(
        key,
        {"status": "producer_pending", "receipt": value, "receipt_sha256": digest(value)},
        revision,
    )
