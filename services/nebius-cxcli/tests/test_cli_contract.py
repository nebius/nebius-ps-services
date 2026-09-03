from __future__ import annotations

from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli.cli_contract import cli_contract_snapshot, load_cli_contract


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
