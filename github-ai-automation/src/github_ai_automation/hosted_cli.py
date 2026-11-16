"""Internal hosted job commands; each command owns one credential boundary."""

import json
import os
from pathlib import Path

import typer

from . import execution
from .contracts import Blocked
from .controller import configured_store
from .github import GitHub
from .qualification import ReadOnlyGitHub

app = typer.Typer(no_args_is_help=True)


def api(readonly=False):
    cls = ReadOnlyGitHub if readonly else GitHub
    return cls(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))


def read(path, limit=4_194_304):
    if path.is_symlink() or path.stat().st_size > limit:
        raise Blocked("EXECUTION_ARTIFACT_INVALID")
    return json.loads(path.read_text())


def output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        if "\n" in str(value) or "\r" in str(value):
            raise Blocked("EXECUTION_OUTPUT_INVALID")
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"{name}={value}\n")


@app.command()
def prepare(
    pr_number: int,
    destination: Path,
    operation: str = "process",
    request_id: str = "",
    dispatch_nonce: str = "",
    root: Path = Path("."),
):
    """Admit and reserve work; write bounded non-secret worker inputs."""
    if pr_number <= 0 or operation not in {"process", "review", "retire-legacy"}:
        raise typer.BadParameter("Invalid PR or operation")
    if operation == "retire-legacy":
        from .controller import trusted_runtime
        from .requests import retire_legacy

        gh = api()
        trusted_runtime(gh)
        retire_legacy(
            gh,
            configured_store(),
            gh.candidate(pr_number)[0],
            json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        )
        value = {"ready": False, "code": "LEGACY_RETIRED"}
    elif os.environ.get("AI_AUTOMATION_PR_REVIEW", "disabled") == "disabled":
        value = {"ready": False, "code": "AUTOMATION_DISABLED"}
    else:
        try:
            value = execution.prepare(
                api(),
                configured_store(),
                root.resolve(),
                pr_number,
                operation,
                request_id,
                dispatch_nonce,
            )
        except Blocked as exc:
            value = {"ready": False, "code": exc.code}
    if value["ready"]:
        encoded = json.dumps(value["input"], sort_keys=True)
        if len(encoded.encode()) > 700_000:
            raise Blocked("EVALUATION_INPUT_TOO_LARGE")
        destination.write_text(encoded + "\n")
    output("ready", str(value["ready"]).lower())
    typer.echo(value["code"])


@app.command()
def evaluate(source: Path, destination: Path, root: Path = Path(".")):
    """Run on a disposable worker with local state and a read-only GitHub token."""
    value = execution.evaluate(
        api(readonly=True), root.resolve(), destination.parent / "worker", read(source, 700_001)
    )
    encoded = json.dumps(value, sort_keys=True)
    if len(encoded.encode()) > 4_194_304:
        raise Blocked("EVALUATION_OUTPUT_TOO_LARGE")
    destination.write_text(encoded + "\n")
    typer.echo(value["result"]["code"])


@app.command()
def supervise():
    """Renew exact worker ownership and cancel on revoked or lost authority."""
    execution.supervise(api(), configured_store())


@app.command()
def accept(source: Path, root: Path = Path(".")):
    """Validate hosted producer and typed evidence before trusted persistence."""
    result = execution.accept(api(), configured_store(), root.resolve(), read(source))
    output(
        "live",
        str(result["code"] == "ASSESSMENT_READY" and result.get("scenario") is not None).lower(),
    )
    typer.echo(result["code"])


@app.command()
def live(root: Path = Path(".")):
    """Execute the admitted scenario using the isolated live-test environment."""
    execution.live_trial(
        api(), configured_store(), root.resolve(), Path(os.environ["RUNNER_TEMP"]) / "live"
    )


@app.command()
def finalize(destination: Path, root: Path = Path(".")):
    """Revalidate gates and issue a protected admission or a fixed failure code."""
    gh, store = api(), configured_store()
    try:
        result = execution.finalize(gh, store, root.resolve())
    except Blocked as exc:
        repo = gh.request("")
        record, _ = store.read(
            execution.execution_key(
                repo["id"], int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
            )
        )
        if not record or record.get("status") != "accepted":
            raise
        result = {**record["result"], "code": exc.code}
    public = {
        k: v
        for k, v in result.items()
        if k
        in {
            "candidate",
            "request_id",
            "operation",
            "record",
            "producer",
            "admission",
            "proof",
            "expected_ci",
            "code",
            "detail",
        }
    }
    public["pr_number"] = result["candidate"]["pr_number"]
    destination.write_text(json.dumps(public, sort_keys=True) + "\n")
    typer.echo(public["code"])
