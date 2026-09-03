#!/usr/bin/env python3
"""Refresh the canonical whole-tree CLI contract from the Typer application."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import typer

from nebius_cxcli import cli
from nebius_cxcli.cli_contract import CLI_CONTRACT_SCHEMA, cli_contract_snapshot

SOPERATOR_SCHEMA = "nebius-cxcli.soperator-cli-contract.v4"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _normalized(value: object) -> str:
    return " ".join(str(value or "").split())


def _soperator_command_metadata(command: Any) -> dict[str, Any]:
    arguments: list[dict[str, Any]] = []
    options: list[str] = []
    required: list[str] = []
    repeatable: list[str] = []
    flags: list[str] = []
    paired: dict[str, list[str]] = {}
    defaults: dict[str, Any] = {}
    option_help: dict[str, str] = {}
    for parameter in command.params:
        primary = next(
            (option for option in getattr(parameter, "opts", ()) if option.startswith("--")),
            None,
        )
        if primary is None:
            arguments.append(
                {
                    "name": parameter.name,
                    "metavar": parameter.metavar,
                    "required": parameter.required,
                    "help": _normalized(parameter.help),
                }
            )
            continue
        secondary = sorted(
            option for option in getattr(parameter, "secondary_opts", ()) if option.startswith("--")
        )
        options.extend((primary, *secondary))
        option_help[primary] = _normalized(parameter.help)
        if parameter.required:
            required.append(primary)
        if parameter.multiple:
            repeatable.append(primary)
        if parameter.is_flag:
            flags.append(primary)
        if secondary:
            paired[primary] = secondary
        if parameter.default is not None:
            defaults[primary] = parameter.default
    if len(arguments) != 1:
        raise RuntimeError("each Soperator command must have one canonical path argument")
    return {
        "short_help": _normalized(command.short_help),
        "argument": arguments[0],
        "option_help": dict(sorted(option_help.items())),
        "options": sorted(options),
        "option_order": options,
        "required": sorted(required),
        "repeatable": sorted(repeatable),
        "flags": sorted(flags),
        "paired": dict(sorted(paired.items())),
        "defaults": dict(sorted(defaults.items())),
    }


def _soperator_contract(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("soperator")
    if not isinstance(nested, dict) or not str(nested.get("schema", "")).startswith(
        "nebius-cxcli.soperator-cli-contract.v"
    ):
        raise RuntimeError("existing CLI contract has no canonical Soperator subtree")
    click_group = typer.main.get_command(cli.soperator_app)
    commands = {
        name: {
            **_soperator_command_metadata(command),
            "conditional_requirements": list(cli._SOPERATOR_CONDITIONAL_REQUIREMENTS.get(name, ())),
            "help_clauses": (
                [
                    "Target Kubernetes endpoint: latest or exact major.minor",
                    "ownership-selected Terraform or provider-API backend",
                ]
                if name == "upgrade"
                else [
                    "The command derives the live region when --region-id is omitted",
                    "Discovery is information-only",
                    "it does not read or create config.yaml",
                ]
                if name == "discover"
                else nested.get("commands", {}).get(name, {}).get("help_clauses", [])
            ),
        }
        for name, command in click_group.commands.items()
    }
    return {
        "schema": SOPERATOR_SCHEMA,
        "command_order": list(click_group.commands),
        "group_help": _normalized(click_group.help),
        "group_help_clauses": nested.get("group_help_clauses", []),
        "commands": commands,
    }


def main() -> int:
    args = _parser().parse_args()
    output = args.output.resolve(strict=True)
    existing = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise RuntimeError("existing CLI contract must be a JSON object")
    snapshot = cli_contract_snapshot(cli.app)
    payload = {
        **snapshot,
        "schema": CLI_CONTRACT_SCHEMA,
        "soperator": _soperator_contract(existing),
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
