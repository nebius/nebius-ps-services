"""Shared Tailscale command I/O; no cloud access during import or config loading."""

from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from .tailscale_config import rows, validate

Config = Annotated[Path, typer.Argument(metavar="CONFIG_YAML", help="Project config.yaml.")]
Component = Annotated[
    str | None,
    typer.Option(help="Instance ID or tailscale-gw@INSTANCE; optional when only one exists."),
]
Json = Annotated[bool, typer.Option("--json", help="Print structured, secret-free evidence.")]


def handled(function: Any) -> Any:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except (RuntimeError, ValueError, OSError, KeyError) as error:
            from . import cli

            cli._exit_with_error(error)

    return run


def load(config_path: Path, component: str | None) -> tuple[Any, Any, Any, dict[str, Any]]:
    from . import cli

    config = cli.load_config(config_path, persist_normalized=False)
    paths = cli.resolve_project_paths(config_path)
    cli.validate_path_alignment(config, paths)
    from .component_instances import component_instance_label

    selected = [
        r
        for r in rows(config)
        if component is None
        or component in {r["instance_id"], component_instance_label(r["id"], r["instance_id"])}
    ]
    if len(selected) != 1:
        raise ValueError("Select exactly one enabled tailscale-gw instance using --component")
    validate(selected[0])
    return cli, config, paths, selected[0]


def emit(value: dict[str, Any], as_json: bool) -> None:
    from . import cli

    if as_json:
        typer.echo(json.dumps(value, indent=2))
        return
    from rich.table import Table

    if "members" in value:
        table = Table("Member", "State")
        for member in value["members"]:
            table.add_row(str(member["member"]), str(member["status"]))
        cli.console.print(table)
        if value.get("next"):
            cli.console.print(value["next"], markup=False)
        for member in value["members"]:
            if "guest" in member:
                cli.console.print(json.dumps(member["guest"], indent=2), markup=False)
    else:
        cli.console.print(json.dumps(value, indent=2), markup=False)


def protected_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink report destination")
    fd, name = tempfile.mkstemp(prefix=".tailscale-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
