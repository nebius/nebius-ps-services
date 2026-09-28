from __future__ import annotations

import copy

import pytest
import typer
import yaml
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.soperator_values import seed_soperator_values


@pytest.mark.parametrize("answers", ["\n\n", "n\nn\n", "y\nn\n", "n\nq\nn\nn\n", "qq\n"])
@pytest.mark.parametrize("first_component", ["nvidia-gpu-operator", "soperator"])
def test_install_app_settings_customize_only_after_explicit_yes(
    monkeypatch, answers, first_component
):
    entries = tuple(
        ComponentEntry(
            id=name,
            scope="apps",
            config_path=f"apps.platform.{name.replace('-', '_')}",
            description="Required integration",
            wizard_fields={"values.operator.replicas": {}},
        )
        for name in (first_component, "nvidia-network-operator")
    )
    payload = {
        # Synthetic chart versions are intentional custom selections; the
        # matrix-owned defaults have separate contract tests.
        "compatibility": {"targets": {"cluster": {"version_set": None}}},
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "cluster", "enabled": True, "inputs": {}}]
        },
        "apps": {
            "charts": [
                {
                    "id": entry.id,
                    "instance_id": "cluster",
                    "enabled": True,
                    "namespace": entry.id,
                    "version": "1.0.0",
                    "values": {"operator": {"replicas": 1}},
                }
                for entry in entries
            ]
        },
    }
    # Required SSH input is already supplied; this test isolates optional settings.
    if first_component == "soperator":
        seed_soperator_values(payload, {"slurmNodes": {"login": {"sshRootPublicKeys": []}}})
    before = copy.deepcopy(payload)
    fields = []

    def customize(label, current, **kwargs):
        fields.append(label)
        return (2 if label.endswith(".values.operator.replicas") else current), False

    monkeypatch.setattr(cli, "_prompt_scalar_override", customize)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    results = []
    app = typer.Typer()

    @app.command()
    def run():
        results.append(
            cli._run_component_field_wizard(
                config_yaml=yaml.safe_dump(payload),
                selected_infra=set(),
                selected_apps={entry.id for entry in entries},
                infra_entries=(),
                app_entries=entries,
                soperator_install=True,
            )
        )

    result = CliRunner().invoke(
        app, [], input=answers + ("\n" if first_component == "soperator" else "")
    )
    assert result.exit_code == 0, result.output
    updated, completed = results[0]
    assert completed == (answers != "qq\n")
    rows = yaml.safe_load(updated)["apps"]["charts"]
    assert all(row["enabled"] for row in rows)
    phase_label = (
        "Configure upstream Soperator settings now?"
        if first_component == "soperator"
        else "Customize 'nvidia-gpu-operator"
    )
    phase_lines = [line for line in result.output.splitlines() if phase_label in line]
    assert len(phase_lines) == (2 if "\nq\n" in answers else 1)
    assert all("[n]:" in line for line in phase_lines)
    assert rows[0]["version"] == before["apps"]["charts"][0]["version"]
    if first_component != "soperator" or answers != "qq\n":
        assert "Required component; the settings shown above will be kept" in result.output
    if answers.startswith("y"):
        assert fields
        assert rows[0]["values"]["operator"]["replicas"] == 2
    else:
        assert not fields
        if completed and first_component == "soperator":
            before["apps"]["charts"][0]["values"]["deploymentProfile"] = "fast-dev-test"
            before["apps"]["charts"][0]["values-explicit-paths"].append("/deploymentProfile")
            before["apps"]["charts"][0]["values-explicit-paths"].sort()
        assert rows == before["apps"]["charts"]


def test_generic_app_prompt_still_requires_an_explicit_choice(monkeypatch):
    entry = ComponentEntry(
        id="example-app", scope="apps", config_path="apps.platform.example_app", description="App"
    )
    seen = []

    def prompt(label, **kwargs):
        seen.append((label, kwargs["default"]))
        return "n"

    monkeypatch.setattr(cli.typer, "prompt", prompt)
    _, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(
            {
                "apps": {
                    "charts": [
                        {"id": entry.id, "instance_id": "cluster", "enabled": True, "values": {}}
                    ]
                }
            }
        ),
        selected_infra=set(),
        selected_apps={entry.id},
        infra_entries=(),
        app_entries=(entry,),
    )
    assert completed
    assert len(seen) == 1 and seen[0][1] is None
    assert seen[0][0].startswith("Configure '")
