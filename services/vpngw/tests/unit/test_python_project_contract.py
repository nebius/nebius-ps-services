from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_supported_python_packaging_and_cli_contract_remains_stable() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for declaration in (
        'name = "nebius-vpngw"',
        'dynamic = ["version"]',
        'requires-python = ">=3.10,<3.13"',
        '"click>=8.3.0,<8.4.0"',
        '"typer>=0.20.0,<0.26.0"',
        '"nebius>=0.3.18,<0.4.0"',
        'nebius-vpngw = "nebius_vpngw.__main__:main"',
        'nebius-vpngw-agent = "nebius_vpngw.agent.main:main"',
        'build-binary = "nebius_vpngw.build:build_binary"',
        'package-dir = {"" = "src"}',
        'where = ["src"]',
        'include = ["nebius_vpngw*"]',
        'nebius_vpngw = ["systemd/*"]',
        'version_file = "src/nebius_vpngw/_version.py"',
        "write_to_source = true",
        "[tool.setuptools_scm.tag]",
        'regex = "^nebius-vpngw-v(?P<version>\\\\d+\\\\.\\\\d+\\\\.\\\\d+)$"',
        "search_parent_directories = true",
    ):
        assert pyproject.count(declaration) == 1
    assert pyproject.count('"setuptools-scm>=10.2.1,<11.0.0"') == 2
    assert "tag_regex" not in pyproject
    for helper in (
        '"misc/gcp_vpngw_vm_ha.py"',
        '"misc/gcp_vpngw_classic_vm_ha.py"',
    ):
        assert pyproject.count(helper) == 1


def test_makefile_retains_canonical_check_and_build_workflow() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

    assert ".DEFAULT_GOAL := all" in makefile
    assert "all: check build" in makefile
    assert "check: lint typecheck test-unit" in makefile
    for target in ("test-unit:", "test-integration:", "coverage:", "build:"):
        assert target in makefile


def test_standard_local_python_tool_outputs_are_ignored() -> None:
    ignored = set((PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    assert {"htmlcov/", ".tox/", ".nox/"} <= ignored
    assert {
        "*.destroy-lifecycle.json",
        ".*.destroy-lifecycle.json.write.lock",
    } <= ignored


def test_operator_docs_use_resource_scoped_transfer_commands() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for command in (
        "nebius-vpngw failover vm",
        "nebius-vpngw failback vm",
        "nebius-vpngw failover tunnel",
        "nebius-vpngw failback tunnel",
    ):
        assert command in readme
    assert "nebius-vpngw failover tunnel" in design
    assert "nebius-vpngw failback tunnel" in design
    assert "Migrate `vm-ha-failover` to `failover vm`" in changelog


def test_operator_docs_do_not_restore_removed_vm_ha_credential_inputs() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for removed_guidance in (
        "prepare one absolute operator-local Nebius credential JSON path per member",
        "Each member supplies operator-local source paths",
        "asks only for the new HA credential",
        "preflights both mode-`0600` Nebius credential JSON files",
    ):
        assert removed_guidance not in readme
        assert removed_guidance not in changelog
    assert (
        "~/.config/nebius-vpngw/credentials/<project>/<gateway>/nebius-credentials.json"
    ) in readme
