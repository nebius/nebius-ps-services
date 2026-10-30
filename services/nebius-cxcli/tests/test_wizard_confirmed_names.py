"""Confirmed names are invocation-local; populated defaults remain editable."""

import copy

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.component_sources import StatusWatcher
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.runtime_introspection import ModuleVariable


@pytest.mark.parametrize("confirmed", [False, True])
def test_confirmed_nested_name_does_not_requeue_containing_module_object(monkeypatch, confirmed):
    entry = ComponentEntry(
        id="demo",
        scope="infra",
        config_path="infra.components[].inputs",
        description="Demo",
        source="./demo-module",
        status=StatusWatcher(kind="compute", name_input="cluster.cluster_name"),
        wizard_fields={"inputs.cluster.cluster_name": {"type_hint": "string"}},
    )
    specs = (
        ModuleVariable(
            name="cluster", required=True, type_hint="object({ cluster_name = string })"
        ),
    )
    monkeypatch.setattr(cli, "module_variables", lambda _: specs)
    monkeypatch.setattr(cli, "module_required_variables", lambda _: ("cluster",))
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda _: {"cluster"})
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    prompted = []

    def answer(path_label, current, **_kwargs):
        prompted.append(path_label)
        return current, False

    monkeypatch.setattr(cli, "_prompt_scalar_override", answer)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "demo",
                    "instance_id": "chosen",
                    "enabled": True,
                    "inputs": {"cluster": {"cluster_name": "chosen"}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    updated, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"chosen"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        confirmed_infra_resource_names=frozenset({("demo", "chosen")})
        if confirmed
        else frozenset(),
    )
    assert completed
    assert yaml.safe_load(updated)["infra"] == payload["infra"]
    assert prompted == ([] if confirmed else ["infra.components[0].inputs.cluster.cluster_name"])


@pytest.mark.parametrize("name_input", ["name", "cluster.cluster_name"])
@pytest.mark.parametrize("confirmation", ["exact", "other-instance", "other-component", "none"])
def test_field_wizard_skips_only_the_confirmed_scalar_name(monkeypatch, name_input, confirmation):
    inputs = {"description": "original"}
    cli._set_mapping_path_value(inputs, name_input, "chosen")
    existing = {
        "id": "demo",
        "instance_id": "existing",
        "enabled": True,
        "inputs": {"name": "existing", "description": "untouched"},
    }
    payload = {
        "infra": {
            "components": [
                copy.deepcopy(existing),
                {"id": "demo", "instance_id": "chosen", "enabled": True, "inputs": inputs},
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="demo",
        scope="infra",
        config_path="infra.components[].inputs",
        description="Demo",
        status=StatusWatcher(kind="compute", name_input=name_input),
        wizard_fields={
            f"inputs.{name_input}": {"type_hint": "string"},
            "inputs.description": {"type_hint": "string"},
        },
    )
    confirmed = {
        "exact": frozenset({("demo", "chosen")}),
        "other-instance": frozenset({("demo", "existing")}),
        "other-component": frozenset({("different", "chosen")}),
        "none": frozenset(),
    }[confirmation]
    prompted = []

    def answer(path_label, current, **_kwargs):
        prompted.append(path_label)
        return "edited" if path_label.endswith(".description") else "renamed", False

    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_prompt_scalar_override", answer)
    updated, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"chosen"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        confirmed_infra_resource_names=confirmed,
    )

    rows = yaml.safe_load(updated)["infra"]["components"]
    assert completed
    assert rows[0] == existing
    assert rows[1]["inputs"]["description"] == "edited"
    name_path = f"infra.components[1].inputs.{name_input}"
    if confirmation == "exact":
        assert name_path not in prompted
        assert cli._mapping_path_value(rows[1]["inputs"], name_input) == "chosen"
    else:
        assert prompted.count(name_path) == 1
        assert cli._mapping_path_value(rows[1]["inputs"], name_input) == "renamed"


@pytest.mark.parametrize("stop", ["q", "qq"])
def test_initial_name_cancellation_does_not_confirm_or_modify_rows(monkeypatch, stop):
    entry = ComponentEntry(
        id="demo",
        scope="infra",
        config_path="infra.components[].inputs",
        description="Demo",
        status=StatusWatcher(kind="compute"),
    )
    payload = {"infra": {"components": []}}
    confirmed = set()
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: stop)
    result = cli._prompt_infra_add_resource_names(
        payload=payload,
        add_targets=[
            cli._ComponentAddTarget(
                scope="infra", component_id="demo", allocate_new_infra_instance_if_enabled=True
            )
        ],
        infra_entries=(entry,),
        confirmed_resource_names=confirmed,
    )
    assert result is None
    assert confirmed == set()
    assert payload == {"infra": {"components": []}}
