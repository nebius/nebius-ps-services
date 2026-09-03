#!/usr/bin/env python3
"""Verify an isolated installed wheel against the canonical complete CLI contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

import click
import typer
from click.testing import CliRunner as ClickCliRunner
from typer.testing import CliRunner as TyperCliRunner

import nebius_cxcli
from nebius_cxcli import cli
from nebius_cxcli.cli_contract import cli_contract_snapshot, load_cli_contract


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--console-script", type=Path, required=True)
    return parser


def _normalized_help(value: str) -> str:
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    without_boxes = re.sub(r"[\u2500-\u257f]", " ", without_ansi)
    return " ".join(without_boxes.split())


def _retired_soperator_root_command() -> str:
    return "-".join(("ext", "soperator"))


def _verify_removed_package_entries(package_root: Path) -> None:
    package_dir = package_root / "nebius_cxcli"
    prefix = "soperator_"
    removed_entries = (
        prefix + "migration.py",
        prefix + "migration_profiles.yaml",
        prefix + "onboarding.py",
        prefix + "upgrade_campaign.py",
        prefix + "controller_bridge.py",
        prefix + "controller_fencing.py",
        prefix + "scaling.py",
        prefix + "jail_capacity.py",
        prefix + "jail_gpu_validation.py",
    )
    present = [entry for entry in removed_entries if (package_dir / entry).exists()]
    if present:
        raise RuntimeError(
            "installed wheel contains removed Soperator entries: " + ", ".join(present)
        )


def _command_at_path(root: click.Command, path: str) -> click.Command:
    command = root
    for part in path.split():
        if not isinstance(command, click.Group) or part not in command.commands:
            raise RuntimeError(f"CLI contract path is not registered: {path}")
        command = command.commands[part]
    return command


def _parameter_value(parameter: click.Parameter) -> str:
    choices = getattr(parameter.type, "choices", None)
    if choices:
        return str(next(iter(choices)))
    name = str(getattr(parameter.type, "name", ""))
    if name in {"float", "integer", "integer range"}:
        return "1"
    if name == "boolean":
        return "true"
    return "probe"


def _callback_probe_argv(command: click.Command) -> list[str]:
    argv: list[str] = []
    for parameter in command.params:
        if not parameter.required:
            continue
        count = max(1, parameter.nargs)
        values = [_parameter_value(parameter)] * count
        if isinstance(parameter, click.Option):
            option = next(iter(parameter.opts or parameter.secondary_opts), None)
            if option is None:
                raise RuntimeError(f"required option has no spelling: {parameter.name}")
            argv.append(option)
            if not parameter.is_flag:
                argv.extend(values)
        else:
            argv.extend(values)
    return argv


def _verify_registered_callbacks(public_paths: list[str]) -> None:
    root = typer.main.get_command(cli.app)
    runner = ClickCliRunner()
    for path in public_paths:
        command = _command_at_path(root, path)
        if isinstance(command, click.Group):
            continue
        reached = False

        def callback(**_kwargs: Any) -> None:
            nonlocal reached
            reached = True

        original_callback = command.callback
        original_no_args_is_help = command.no_args_is_help
        command.callback = callback
        command.no_args_is_help = False
        try:
            result = runner.invoke(command, _callback_probe_argv(command))
        finally:
            command.callback = original_callback
            command.no_args_is_help = original_no_args_is_help
        if result.exit_code != 0 or not reached:
            raise RuntimeError(
                f"installed callback probe failed for {path}: "
                + json.dumps(
                    {"returncode": result.exit_code, "output": _normalized_help(result.output)},
                    sort_keys=True,
                )
            )


def _assert_soperator_callback(
    runner: TyperCliRunner,
    *,
    command: str,
    argv: list[str],
    expected_error: str,
) -> None:
    result = runner.invoke(cli.app, ["soperator", command, *argv])
    rendered = _normalized_help(result.output)
    if result.exit_code != 1 or expected_error not in rendered:
        raise RuntimeError(
            f"installed Soperator {command} callback smoke failed: "
            + json.dumps(
                {"returncode": result.exit_code, "output": rendered},
                sort_keys=True,
            )
        )


def _verify_soperator_semantics(package_root: Path) -> None:
    runner = TyperCliRunner()
    missing_config = package_root / "__soperator-wheel-smoke-missing-config__.yaml"
    if missing_config.exists():
        raise RuntimeError(f"installed-wheel smoke target unexpectedly exists: {missing_config}")
    _assert_soperator_callback(
        runner,
        command="install",
        argv=[str(missing_config), "--profile", "cpu", "--no-interactive", "--dry-run"],
        expected_error="requires --release latest or exact X.Y.Z",
    )
    with patch.object(
        cli,
        "_register_existing_soperator_target",
        side_effect=RuntimeError("installed-wheel-onboard-callback"),
    ):
        _assert_soperator_callback(
            runner,
            command="onboard",
            argv=[str(missing_config), "--no-interactive"],
            expected_error="installed-wheel-onboard-callback",
        )
    _assert_soperator_callback(
        runner,
        command="upgrade",
        argv=[str(missing_config), "--no-interactive"],
        expected_error="requires an explicit execution mode",
    )
    with (
        patch.object(cli, "SoperatorOperationLocalLock", side_effect=lambda _path: nullcontext()),
        patch.object(
            cli,
            "_load_source_payload",
            side_effect=RuntimeError("installed-wheel-destroy-callback"),
        ),
    ):
        _assert_soperator_callback(
            runner,
            command="destroy",
            argv=[str(missing_config), "--target", "cluster-a", "--dry-run"],
            expected_error="installed-wheel-destroy-callback",
        )
    with patch.object(
        cli,
        "_run_soperator_public_discovery_command",
        side_effect=RuntimeError("installed-wheel-discover-callback"),
    ):
        _assert_soperator_callback(
            runner,
            command="discover",
            argv=[
                str(package_root),
                "--tenant-id",
                "tenant-a",
                "--project-id",
                "project-a",
                "--cluster-id",
                "mk8scluster-a",
            ],
            expected_error="installed-wheel-discover-callback",
        )
    with patch.object(
        cli,
        "_load_source_payload",
        side_effect=RuntimeError("installed-wheel-status-callback"),
    ):
        _assert_soperator_callback(
            runner,
            command="status",
            argv=[str(missing_config), "--no-live", "--no-interactive"],
            expected_error="installed-wheel-status-callback",
        )


def _help_clauses(contract: dict[str, Any], path: str) -> list[str]:
    soperator = contract["soperator"]
    if path == "soperator":
        return [str(item) for item in soperator.get("group_help_clauses", ())]
    if path.startswith("soperator "):
        command = path.split(maxsplit=1)[1]
        return [str(item) for item in soperator["commands"][command].get("help_clauses", ())]
    return []


def _run_console(console_script: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(console_script), *argv],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )


def main() -> int:
    args = _parser().parse_args()
    package_root = args.package_root.resolve(strict=True)
    imported = Path(nebius_cxcli.__file__).resolve(strict=True)
    if not imported.is_relative_to(package_root):
        raise RuntimeError(
            f"nebius_cxcli imported from {imported}, outside installed wheel root {package_root}"
        )
    _verify_removed_package_entries(package_root)
    contract = load_cli_contract(args.contract)
    actual = cli_contract_snapshot(cli.app)
    expected = {key: contract[key] for key in ("hidden_paths", "public_paths", "surface_sha256")}
    if actual != expected:
        raise RuntimeError(
            "installed wheel CLI differs from its canonical contract: "
            + json.dumps({"expected": expected, "actual": actual}, sort_keys=True)
        )

    console_script = args.console_script.resolve(strict=True)
    for path in contract["public_paths"]:
        argv = [*path.split(), "--help"] if path else ["--help"]
        completed = _run_console(console_script, argv)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"installed help failed for {path or '<root>'}: {detail}")
        rendered = _normalized_help(completed.stdout)
        missing = [clause for clause in _help_clauses(contract, path) if clause not in rendered]
        if missing:
            raise RuntimeError(
                f"installed help for {path} is missing canonical clauses: " + json.dumps(missing)
            )

    version = _run_console(console_script, ["--version"])
    if version.returncode != 0 or not _normalized_help(version.stdout).startswith("nebius-cxcli "):
        raise RuntimeError(
            "installed --version failed: "
            + json.dumps(
                {"returncode": version.returncode, "output": _normalized_help(version.stdout)},
                sort_keys=True,
            )
        )

    retired = _retired_soperator_root_command()
    for suffix in ([], ["--help"]):
        completed = _run_console(console_script, [retired, *suffix])
        rendered = _normalized_help(completed.stderr or completed.stdout)
        if completed.returncode != 2 or "No such command" not in rendered:
            raise RuntimeError(
                "installed wheel did not reject the removed Soperator root command: "
                + json.dumps(
                    {"returncode": completed.returncode, "output": rendered},
                    sort_keys=True,
                )
            )

    _verify_registered_callbacks(contract["public_paths"])
    _verify_soperator_semantics(package_root)
    print(
        f"Installed wheel CLI contract verified: {len(contract['public_paths'])} public "
        f"surface(s), {len(contract['hidden_paths'])} hidden surface(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
