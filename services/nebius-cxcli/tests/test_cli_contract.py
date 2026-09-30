from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import pytest
import typer.rich_utils
from click.testing import CliRunner as ClickRunner
from rich.console import Console
from rich.text import Text
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
            prefix = "| nebius-cxcli "
            section = Text.from_markup(section).plain
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
            assert command.epilog and "| nebius-cxcli " in Text.from_markup(command.epilog).plain, (
                path
            )


def _contract_path() -> Path:
    return Path(__file__).parent / "fixtures" / "cli_contract.json"


def test_readme_index_lists_every_public_leaf_once() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    index = readme.split("<!-- command-index:start -->", maxsplit=1)[1].split(
        "<!-- command-index:end -->", maxsplit=1
    )[0]
    documented = re.findall(r"^\| `([^`]+)` \|", index, re.MULTILINE)
    leaves = {
        path
        for path, command in _public_commands(get_command(cli.app))
        if not isinstance(command, click.Group)
    }
    assert set(documented) == leaves
    assert len(documented) == len(set(documented)), "Duplicate command-index entries"


def test_operator_guide_examples_parse_without_product_callbacks(monkeypatch) -> None:
    project = Path(__file__).resolve().parents[1]
    documents = [
        project / "README.md",
        *sorted(
            path
            for path in (project / "docs").glob("*.md")
            if path.name not in {"design.md", "requirements.md", "development.md"}
        ),
    ]
    root = get_command(cli.app)

    def disable_callbacks(command: click.Command) -> None:
        monkeypatch.setattr(command, "callback", lambda **_params: None)
        for parameter in command.params:
            monkeypatch.setattr(parameter, "callback", None)
        if isinstance(command, click.Group):
            for child in command.commands.values():
                disable_callbacks(child)

    disable_callbacks(root)
    examples = 0
    for path in documents:
        contents = path.read_text(encoding="utf-8")
        for block in re.findall(r"```(?:bash|sh|shell)\n(.*?)```", contents, re.DOTALL):
            for line in block.replace("\\\n", " ").splitlines():
                line = line.strip()
                if not line.startswith("nebius-cxcli "):
                    continue
                # Parse shell words only: never expand variables or run substitutions.
                # These examples use string/path placeholders, not typed enum defaults.
                argv = shlex.split(line, comments=True)[1:]
                if argv == ["--version"]:
                    # The separate version test retains and exercises its eager callback.
                    continue
                result = ClickRunner().invoke(root, argv)
                assert result.exit_code == 0, (path.name, line, result.output)
                examples += 1
    assert examples > 0


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


@pytest.mark.parametrize("width", [80, 160])
@pytest.mark.parametrize("no_color", [False, True])
def test_help_command_background_at_supported_widths(monkeypatch, width, no_color):
    from io import StringIO

    stream = StringIO()
    console = Console(
        file=stream, force_terminal=True, no_color=no_color, color_system="truecolor", width=width
    )
    monkeypatch.setattr(typer.rich_utils, "_get_rich_console", lambda **kwargs: console)
    result = CliRunner().invoke(cli.app, ["grafana", "show", "--help"])
    assert result.exit_code == 0, result.output
    text = Text.from_ansi(stream.getvalue())
    command = "nebius-cxcli grafana show --config ./config.yaml --target CLUSTER_TARGET"
    start = text.plain.index(command)
    for offset in range(start, start + len(command)):
        style = text.get_style_at_offset(console, offset)
        assert (style.bgcolor is not None) == (not no_color)
    assert text.get_style_at_offset(console, start - 2).bgcolor is None


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
