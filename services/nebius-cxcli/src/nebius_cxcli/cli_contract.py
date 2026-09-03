"""Stable recursive metadata for the complete Typer/Click command tree."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click
import typer

CLI_CONTRACT_SCHEMA = "nebius-cxcli.cli-contract.v1"


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _json_value(enum_value)
    return str(value)


def _parameter_metadata(parameter: click.Parameter) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "default": _json_value(parameter.default),
        "metavar": _normalized_text(parameter.metavar),
        "multiple": bool(parameter.multiple),
        "name": str(parameter.name or ""),
        "nargs": int(parameter.nargs),
        "required": bool(parameter.required),
        "type": str(getattr(parameter.type, "name", type(parameter.type).__name__)),
    }
    choices = getattr(parameter.type, "choices", None)
    if choices is not None:
        metadata["choices"] = [_json_value(choice) for choice in choices]
    if isinstance(parameter, click.Option):
        metadata.update(
            {
                "flag_value": _json_value(parameter.flag_value),
                "help": _normalized_text(parameter.help),
                "is_flag": bool(parameter.is_flag),
                "kind": "option",
                "opts": list(parameter.opts),
                "secondary_opts": list(parameter.secondary_opts),
            }
        )
    else:
        metadata.update(
            {"help": _normalized_text(getattr(parameter, "help", "")), "kind": "argument"}
        )
    return metadata


def command_metadata(command: click.Command) -> dict[str, Any]:
    children = list(command.commands) if isinstance(command, click.Group) else []
    return {
        "children": children,
        "epilog": _normalized_text(command.epilog),
        "help": _normalized_text(command.help),
        "hidden": bool(command.hidden),
        "kind": "group" if isinstance(command, click.Group) else "command",
        "name": str(command.name or ""),
        "no_args_is_help": bool(command.no_args_is_help),
        "params": [_parameter_metadata(parameter) for parameter in command.params],
        "short_help": _normalized_text(command.short_help),
    }


def _metadata_sha256(metadata: dict[str, Any]) -> str:
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cli_contract_snapshot(app: typer.Typer) -> dict[str, Any]:
    root = typer.main.get_command(app)
    public_paths: list[str] = []
    hidden_paths: list[str] = []
    surface_sha256: dict[str, str] = {}

    def visit(command: click.Command, path: tuple[str, ...], *, ancestor_hidden: bool) -> None:
        rendered_path = " ".join(path)
        hidden = ancestor_hidden or bool(command.hidden)
        (hidden_paths if hidden else public_paths).append(rendered_path)
        surface_sha256[rendered_path] = _metadata_sha256(command_metadata(command))
        if isinstance(command, click.Group):
            for name, child in command.commands.items():
                visit(child, (*path, name), ancestor_hidden=hidden)

    visit(root, (), ancestor_hidden=False)
    return {
        "hidden_paths": hidden_paths,
        "public_paths": public_paths,
        "surface_sha256": dict(sorted(surface_sha256.items())),
    }


def load_cli_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != CLI_CONTRACT_SCHEMA:
        raise RuntimeError(f"CLI contract must use schema {CLI_CONTRACT_SCHEMA}")
    for key in ("public_paths", "hidden_paths"):
        value = payload.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise RuntimeError(f"CLI contract has invalid {key}")
    hashes = payload.get("surface_sha256")
    if not isinstance(hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in hashes.items()
    ):
        raise RuntimeError("CLI contract has invalid surface_sha256")
    if set(hashes) != set(payload["public_paths"]) | set(payload["hidden_paths"]):
        raise RuntimeError("CLI contract surface hashes do not cover the exact command tree")
    return payload
