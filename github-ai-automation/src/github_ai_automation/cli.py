"""Command line interface."""

import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from .contracts import Blocked
from .hosted_cli import app as execution_app
from .image_cli import app as image_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(execution_app, name="execution", help="Internal trusted hosted job boundaries.")
app.add_typer(
    image_app, name="image", help="Internal immutable image qualification and publication."
)


def show_version(value: bool) -> None:
    if value:
        typer.echo(version("github-ai-automation"))
        raise typer.Exit()


@app.callback()
def options(
    version_flag: Annotated[
        bool, typer.Option("--version", callback=show_version, is_eager=True)
    ] = False,
) -> None:
    """github-ai-automation commands."""


@app.command()
def health() -> None:
    """Report local CLI health; this does not qualify external integrations."""
    typer.echo("ok")


@app.command()
def rollout_inspect():
    """Read the trusted admission phase and generation; no GitHub writes."""
    from .controller import configured_store, trusted_runtime
    from .rollout import current

    api = github()
    repo = trusted_runtime(api)
    typer.echo(current(configured_store(), repo["id"]).model_dump_json())


@app.command()
def rollout_configure(manifest: Path, expected_generation: int):
    """Replace exact canary permits or explicitly activate general admission."""
    from .controller import configured_store
    from .rollout import configure

    if manifest.stat().st_size > 100_000 or expected_generation < 0:
        raise typer.BadParameter("Invalid rollout manifest or generation")
    value = configure(
        github(),
        configured_store(),
        json.loads(manifest.read_text()),
        expected_generation,
        json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        os.environ,
    )
    typer.echo(f"{value.phase}: generation {value.generation}")


@app.command()
def qualify_pr(pr_number: int, output: Path, image_receipt: Path, root: Path = Path(".")):
    """Rehearse assessment without GitHub writes, cloud credentials or production state."""
    from .qualification import ReadOnlyGitHub, shadow

    if pr_number <= 0:
        raise typer.BadParameter("Expected a positive PR number")
    api = ReadOnlyGitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
    result = {
        "schema": "qualification-shadow/v1",
        "github_writes": False,
        "live_qualified": False,
        "protected_effects_qualified": False,
    }
    try:
        from .hosted_cli import read

        result = shadow(
            api,
            root.resolve(),
            output.parent / "qualification",
            pr_number,
            os.environ,
            read(image_receipt, 100_000),
        )
    except Blocked as exc:
        result["code"] = exc.code
    output.write_text(json.dumps(result, sort_keys=True) + "\n")
    typer.echo(result["code"])
    if result["code"] != "REVIEW_COMPLETE":
        raise typer.Exit(2)


def github():
    from .github import GitHub

    return GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))


def state_qualification_run(api):
    from .controller import trusted_runtime

    repo = trusted_runtime(api)
    run = api.request(
        f"actions/runs/{os.environ['GITHUB_RUN_ID']}/attempts/{os.environ['GITHUB_RUN_ATTEMPT']}"
    )
    if (
        run["path"] != ".github/workflows/github-ai-state-qualification.yml"
        or run["head_sha"] != os.environ["GITHUB_SHA"]
    ):
        raise Blocked("STATE_QUALIFICATION_WORKFLOW_REQUIRED")
    return repo, run


@app.command()
def state_sentinel(output: Path):
    """Observe the harmless production sentinel; this command cannot create it."""
    from .controller import configured_store
    from .state_qualification import observe_sentinel

    repo, run = state_qualification_run(github())
    store = configured_store()
    try:
        proof = observe_sentinel(store, repo["id"])
        proof.update(run_id=run["id"], attempt=run["run_attempt"], workflow_sha=run["head_sha"])
        output.write_text(json.dumps(proof, sort_keys=True) + "\n")
    finally:
        store.client.close()


@app.command()
def qualify_state(output: Path, sentinel_proof: Path):
    """Run finite CAS/isolation trials in the dedicated qualification bucket."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    from .contracts import digest
    from .controller import configured_store
    from .hosted_cli import read
    from .state_qualification import qualify

    api = github()
    repo, run = state_qualification_run(api)
    guards = [
        j
        for j in api.pages(
            f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", field="jobs"
        )
        if j["name"] == "sentinel"
    ]
    sentinel = read(sentinel_proof, 10_000)
    if (
        len(guards) != 1
        or guards[0]["status"] != "completed"
        or guards[0]["conclusion"] != "success"
        or sentinel.get("repository_id") != repo["id"]
        or sentinel.get("run_id") != run["id"]
        or sentinel.get("attempt") != run["run_attempt"]
        or sentinel.get("workflow_sha") != run["head_sha"]
        or sentinel
        != api.artifact(
            run["id"], f"state-sentinel-{run['id']}-{run['run_attempt']}", "sentinel.json"
        )
    ):
        raise Blocked("STATE_SENTINEL_PRODUCER_UNTRUSTED")
    trial = digest({"repository": repo["id"], "run": run["id"], "attempt": run["run_attempt"]})[:32]
    result = {
        "schema": "state-qualification/v1",
        "trial_id": trial,
        "checks": {},
        "code": "STARTED",
    }
    output.write_text(json.dumps(result) + "\n")
    store = configured_store()
    anonymous = boto3.client(
        "s3",
        endpoint_url=os.environ["AI_AUTOMATION_STATE_ENDPOINT"],
        region_name=store.client.meta.region_name,
        config=Config(
            signature_version=UNSIGNED,
            connect_timeout=5,
            read_timeout=20,
            retries={"total_max_attempts": 1},
        ),
    )
    try:
        proof = qualify(
            store.client,
            anonymous,
            store.bucket,
            os.environ.get("AI_AUTOMATION_PRODUCTION_STATE_BUCKET", ""),
            trial,
            sentinel,
        )
        result.update(proof, code="STATE_QUALIFIED")
    except Blocked as exc:
        result["code"] = exc.code
    except Exception:
        result["code"] = "STATE_QUALIFICATION_UNAVAILABLE"
    finally:
        store.client.close()
        anonymous.close()
        output.write_text(json.dumps(result, sort_keys=True) + "\n")
    typer.echo(result["code"])
    if result["code"] != "STATE_QUALIFIED":
        raise typer.Exit(2)


@app.command()
def intake():
    """Admit new PRs or resume only already-active requests on trusted metadata events."""
    from .controller import configured_store
    from .requests import dispatch
    from .requests import intake as admit

    api = github()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    store = configured_store()
    record = admit(api, store, event)
    if record:
        dispatch(api, store, record)


@app.command()
def continue_run(run_id: int, root: Path = Path(".")):
    """Reconcile a completed producer; this process receives no inference key."""
    from .controller import configured_store
    from .events import continue_run as continuation

    if os.environ.get("AI_AUTOMATION_PR_REVIEW", "disabled") == "disabled":
        typer.echo("AUTOMATION_DISABLED")
        return
    try:
        api, store = github(), configured_store()
        continuation(api, store, root.resolve(), run_id)
        from .lifecycle import reconcile_all

        reconcile_all(api, store)
    except Blocked as exc:
        typer.echo(exc.code)
        raise typer.Exit(2) from None


@app.command()
def reconcile_completion(key: str):
    """Internal reporting/issue reconciliation; never admits or executes a PR."""
    import re

    from .controller import configured_store, trusted_runtime
    from .lifecycle import reconcile

    api = github()
    repo = trusted_runtime(api)
    if not re.fullmatch(
        r"repositories/" + str(repo["id"]) + r"/prs/[1-9][0-9]*/completions/[a-f0-9]{64}", key
    ):
        raise typer.BadParameter("Invalid completion identity")
    try:
        value = reconcile(api, configured_store(), key)
        typer.echo(value["status"])
    except Blocked as exc:
        typer.echo(exc.code)
        raise typer.Exit(2) from None


@app.command()
def waiver(pr_number: int, reason: str, revoke: bool = False):
    """Register/revoke a lifetime waiver from an authenticated human dispatch."""
    from .controller import configured_store, trusted_runtime
    from .policy import register_waiver
    from .state import acquire

    api = github()
    trusted_runtime(api)
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    actor = event["sender"]
    if actor["id"] != int(os.environ["GITHUB_ACTOR_ID"]):
        raise Blocked("WAIVER_ACTOR_MISMATCH")
    candidate, _ = api.candidate(pr_number)
    store = configured_store()
    lease = acquire(store, candidate.key + "/lock")
    try:
        register_waiver(
            store,
            lease,
            candidate,
            actor_id=actor["id"],
            run_id=int(os.environ["GITHUB_RUN_ID"]),
            reason=reason,
            active=not revoke,
            permission=api.permission(actor["login"]),
            actor_type=actor["type"],
        )
        api.comment(
            pr_number,
            "LIVE_WAIVER_REVOKED" if revoke else "LIVE_TEST_WAIVED",
            detail="This PR's live-test policy is recorded independently of labels and commits.",
        )
    finally:
        lease.release()


@app.command()
def merge(
    pr_number: int, head: str, review_id: int, method: str = "squash", root: Path = Path(".")
):
    """Serialize and invoke the existing protected merge effect owner."""
    from .controller import configured_store
    from .merge import execute_merge

    try:
        value = execute_merge(
            github(), configured_store(), root.resolve(), pr_number, head, review_id, method
        )
        typer.echo(json.dumps(value))
    except Blocked as exc:
        typer.echo(exc.code)
        raise typer.Exit(2) from None


@app.command()
def recover(root: Path = Path(".")):
    """Recover cleanup and pending hosted operations; preserve original budgets."""
    from .controller import configured_store
    from .recovery import recover as reconcile

    typer.echo(json.dumps(reconcile(github(), configured_store(), root.resolve())))


@app.command()
def release_intake(run_id: int, root: Path = Path(".")):
    """Route a protected merged-result completion to registered releases."""
    from .controller import configured_store
    from .publication import release_intake as route

    route(github(), configured_store(), root.resolve(), run_id)


@app.command()
def release_prepare(
    project: str,
    pr_number: int,
    completion_run: int,
    output: Path,
    tag: str = "",
    root: Path = Path("."),
):
    """Reserve a version and annotated tag after exact merged-result verification."""
    from .controller import configured_store
    from .publication import prepare_release

    value = prepare_release(
        github(),
        configured_store(),
        root.resolve(),
        project=project,
        number=pr_number,
        completion_run=completion_run,
        requested_tag=tag or None,
    )
    output.write_text(json.dumps(value, sort_keys=True) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            for name in ("tag", "source_sha", "needs_build"):
                stream.write(f"{name}={value[name]}\n")


@app.command()
def release_publish(project: str, source_sha: str, tag: str, root: Path = Path(".")):
    """Publish immutable build artifacts and independently verify downloaded bytes."""
    from .controller import configured_store
    from .publication import publish_release

    typer.echo(
        json.dumps(
            publish_release(github(), configured_store(), root.resolve(), project, source_sha, tag)
        )
    )


def main() -> None:
    try:
        app()
    except Blocked as exc:
        typer.echo(exc.code, err=True)
        raise SystemExit(2) from None
    except Exception:
        typer.echo("AUTOMATION_EXECUTION_UNAVAILABLE", err=True)
        raise SystemExit(2) from None


@app.command()
def baseline_inspect(
    project: str, tag: str, release_run: int, destination: Path, root: Path = Path(".")
):
    """Verify a historical producer and independently hash the current release assets."""
    from .baseline import inspect
    from .contracts import digest
    from .controller import trusted_runtime

    gh = github()
    trusted_runtime(gh)
    value = inspect(gh, root.resolve(), project, tag, release_run)
    destination.write_text(json.dumps(value, sort_keys=True) + "\n")
    typer.echo("Review SHA256: " + digest(value))


@app.command()
def baseline_adopt(
    project: str,
    tag: str,
    release_run: int,
    expected_digest: str,
    expected_revision: int,
    root: Path = Path("."),
):
    """Adopt only the exact human-reviewed baseline; never change tags or release assets."""
    from .baseline import adopt
    from .controller import configured_store

    record = adopt(
        github(),
        configured_store(),
        root.resolve(),
        project,
        tag,
        release_run,
        expected_digest,
        expected_revision,
        json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        os.environ,
    )
    typer.echo(f"Adopted {record['tag']} at baseline revision {record['revision']}")
