from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
from click.testing import CliRunner as ClickRunner
from typer.main import get_command
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli.cli_contract import cli_contract_snapshot, load_cli_contract


def _public_commands(command: click.Command, path: str = ""):
    if command.hidden:
        return
    yield path, command
    if isinstance(command, click.Group):
        for name, child in command.commands.items():
            yield from _public_commands(child, f"{path} {name}".strip())


def test_every_displayed_example_parses_without_running_product_callbacks(monkeypatch) -> None:
    root = get_command(cli.app)
    commands = list(_public_commands(root))
    for _path, command in commands:
        monkeypatch.setattr(command, "callback", lambda **_params: None)
        for parameter in command.params:
            monkeypatch.setattr(parameter, "callback", None)

    examples = 0
    for path, command in commands:
        sections = (command.help or "", command.epilog or "")
        for section in sections:
            prefix = f"{cli._HELP_EXAMPLE_SEPARATOR_MARKUP} nebius-cxcli "
            # Labels may share a paragraph with the next command (Grafana).
            for block in section.split(prefix)[1:]:
                example = block.split("\n\n", maxsplit=1)[0].strip()
                assert not example.endswith((";", ".")), (path, example)
                result = ClickRunner().invoke(root, shlex.split(example))
                assert result.exit_code == 0, (path, example, result.output)
                examples += 1
    assert examples > len(commands)


def test_every_public_leaf_has_parameter_descriptions_and_examples() -> None:
    for path, command in _public_commands(get_command(cli.app)):
        for parameter in command.params:
            assert parameter.help, (path, parameter.name)
        if not isinstance(command, click.Group):
            assert (
                command.epilog
                and f"{cli._HELP_EXAMPLE_SEPARATOR_MARKUP} nebius-cxcli " in command.epilog
            ), path


def _contract_path() -> Path:
    return Path(__file__).parent / "fixtures" / "cli_contract.json"


def test_complete_cli_tree_matches_canonical_contract() -> None:
    contract = load_cli_contract(_contract_path())

    assert cli_contract_snapshot(cli.app) == {
        "hidden_paths": contract["hidden_paths"],
        "public_paths": contract["public_paths"],
        "surface_sha256": contract["surface_sha256"],
    }
    assert contract["public_paths"][0] == ""
    assert contract["hidden_paths"] == ["mk8s-token"]
    assert len(contract["public_paths"]) == len(set(contract["public_paths"]))


def test_every_public_help_surface_renders() -> None:
    contract = load_cli_contract(_contract_path())
    runner = CliRunner()

    for path in contract["public_paths"]:
        argv = [*path.split(), "--help"] if path else ["--help"]
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == 0, f"{path or '<root>'}: {result.output}"
        if path.startswith("soperator "):
            rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
            rendered = " ".join(re.sub(r"[\u2500-\u257f]", " ", rendered).split())
            clauses = contract["soperator"]["commands"][path.removeprefix("soperator ")][
                "help_clauses"
            ]
            for clause in clauses:
                assert clause in rendered, (path, clause)


def test_hidden_commands_do_not_render_in_root_help() -> None:
    contract = load_cli_contract(_contract_path())
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0, result.output
    for path in contract["hidden_paths"]:
        assert path.split()[0] not in result.output


def test_root_version_callback_is_publicly_reachable() -> None:
    result = CliRunner().invoke(cli.app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("nebius-cxcli ")


def test_day_two_help_examples_use_public_component_selectors_and_output_paths() -> None:
    root = get_command(cli.app)
    ssh_epilog = root.commands["ssh-jumphost"].epilog or ""
    wireguard_epilog = root.commands["wireguard"].epilog or ""

    assert "ssh-jumphost@bastion" in ssh_epilog
    assert "wireguard-gw@vpn" in wireguard_epilog
    assert "wireguard-clients/" in wireguard_epilog
    assert "infra:vm-jumphost" not in ssh_epilog
    assert "infra:vm-vpn-gateway" not in wireguard_epilog
    assert "generated/.../wireguard/" not in wireguard_epilog
