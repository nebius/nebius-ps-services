"""Command boundary with lazy ML imports."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from nebius_train.config import load_config

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def show_version(value: bool) -> None:
    if value:
        typer.echo(version("nebius-train-script"))
        raise typer.Exit()


@app.callback()
def options(
    version_flag: Annotated[
        bool, typer.Option("--version", callback=show_version, is_eager=True)
    ] = False,
) -> None:
    """Reproducible classification and seq2seq training on local assets."""


@app.command()
def validate(config: Annotated[Path, typer.Option(exists=True, dir_okay=False)]) -> None:
    """Validate configuration and paths without importing the ML runtime."""
    load_config(config)
    typer.echo("Configuration valid")


@app.command()
def train(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    resume: Annotated[bool, typer.Option(help="Resume the last complete epoch.")] = False,
) -> None:
    """Train locally or under torchrun; failures return a nonzero status."""
    plan = load_config(config)
    from nebius_train.ml.engine import train as run_training

    run_training(plan, resume=resume)


@app.command()
def download(
    model: Annotated[str, typer.Option()],
    model_revision: Annotated[str, typer.Option(help="Full 40-character model commit.")],
    model_license: Annotated[str, typer.Option(help="Reviewed model license identifier.")],
    dataset: Annotated[str, typer.Option()],
    dataset_revision: Annotated[str, typer.Option(help="Full 40-character dataset commit.")],
    dataset_license: Annotated[str, typer.Option(help="Reviewed dataset license identifier.")],
    output: Annotated[Path, typer.Option(help="New bundle directory; never overwritten.")],
    dataset_config: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Download pinned assets. HF_TOKEN is read from the environment, never stored."""
    from nebius_train.download import download_bundle

    download_bundle(
        model=model,
        model_revision=model_revision,
        model_license=model_license,
        dataset=dataset,
        dataset_revision=dataset_revision,
        dataset_license=dataset_license,
        output=output,
        dataset_config=dataset_config,
    )
    typer.echo("Asset bundle complete")


def main() -> None:
    try:
        app()
    except (ValueError, OSError) as exc:
        # Pydantic errors can include input data; keep errors redacted.
        typer.echo(
            f"Error: {type(exc).__name__}; check configuration, paths and permissions.", err=True
        )
        raise SystemExit(2) from None
