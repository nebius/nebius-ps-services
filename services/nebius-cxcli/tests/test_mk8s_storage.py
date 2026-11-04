from __future__ import annotations

import copy
import sys
from types import SimpleNamespace

import pytest
import yaml
from rich.console import Console

from nebius_cxcli import cli
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.infra_render import (
    _materialize_mk8s_sfs_attachments,
    _sfs_module_name_for_target,
)
from nebius_cxcli.mk8s_storage import node_group_sfs_choices, parse_storage_selection
from nebius_cxcli.provider_options import OptionChoice


def component(kind, instance, inputs=None, *, enabled=True):
    return {"id": kind, "instance_id": instance, "enabled": enabled, "inputs": inputs or {}}


def test_named_choices_preserve_internal_keys_and_disambiguate_names():
    payload = {"infra": {"components": [component("sfs", "storage", {"name": "shared-data"})]}}
    assert node_group_sfs_choices(payload, target_ref="cluster") == [
        OptionChoice(value="default", label="shared-data")
    ]
    payload["infra"]["components"][0]["inputs"] = {}
    assert node_group_sfs_choices(payload, target_ref="cluster")[0].label == "storage"
    payload["infra"]["components"][0]["inputs"] = {
        "filesystems": {
            "one": {"name": "data"},
            "two": {"name": "data"},
            "three": {"name": "data (entry 1)"},
            "scratch": {},
        }
    }
    choices = node_group_sfs_choices(payload, target_ref="cluster")
    assert [(choice.value, choice.label) for choice in choices] == [
        ("one", "1. data"),
        ("two", "2. data"),
        ("three", "3. data (entry 1)"),
        ("scratch", "4. scratch"),
    ]


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([], False),
        ([component("sfs", "storage", enabled=False)], False),
        ([component("sfs", "storage"), component("mk8s", "cluster")], True),
        (
            [component("sfs", "storage"), component("mk8s", "cluster"), component("mk8s", "other")],
            False,
        ),
        (
            [component("sfs", "cluster"), component("sfs", "other"), component("mk8s", "cluster")],
            True,
        ),
        ([component("sfs", "one"), component("sfs", "two"), component("mk8s", "cluster")], False),
    ],
)
def test_choice_eligibility_matches_attachment_renderer(rows, expected):
    payload = {"infra": {"components": rows}}
    choices = node_group_sfs_choices(payload, target_ref="cluster")
    module = _sfs_module_name_for_target(
        payload,
        target_ref="cluster",
        sfs_module_name_by_instance_id={
            row["instance_id"]: row["instance_id"] for row in rows if row["id"] == "sfs"
        },
    )
    assert bool(choices) == bool(module) == expected


@pytest.mark.parametrize(
    "raw, expected", [("", ["a", "b"]), ("none", []), ("NONE", []), ("2,1,2", ["b", "a"])]
)
def test_text_selection(raw, expected):
    assert (
        parse_storage_selection(raw, [OptionChoice("a", "Alpha"), OptionChoice("b", "Beta")])
        == expected
    )


@pytest.mark.parametrize("raw", ["0", "3", "default", "Alpha", "1,none", "1,", "²", "1.5"])
def test_invalid_text_selection(raw):
    with pytest.raises(ValueError):
        parse_storage_selection(raw, [OptionChoice("a", "Alpha"), OptionChoice("b", "Beta")])


@pytest.mark.parametrize("answer", [["default", "scratch"], ["scratch"], [], None, "back", "quit"])
def test_tty_preselects_all_and_respects_empty_and_navigation(monkeypatch, answer):
    captured = {}
    sentinel = {"back": cli._WIZARD_BACK_CHOICE, "quit": cli._WIZARD_QUIT_CHOICE}
    result = sentinel.get(answer, answer) if isinstance(answer, str) else answer

    def checkbox(title, **kwargs):
        captured.update(title=title, **kwargs)
        return SimpleNamespace(ask=lambda: result)

    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(cli, "_configure_questionary_checkbox_symbols", lambda: None)
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        SimpleNamespace(Choice=lambda **kwargs: kwargs, checkbox=checkbox),
    )
    monkeypatch.setattr(cli, "console", Console(width=160, record=True))
    choices = [OptionChoice("default", "shared-data"), OptionChoice("scratch", "scratch-data")]
    selected, stopped = cli._prompt_node_group_shared_storage(
        group_name="core-services", choices=choices
    )
    assert captured["title"] == 'Attach shared storage to node group "core-services":'
    assert all(choice["checked"] for choice in captured["choices"])
    assert [choice["title"] for choice in captured["choices"]] == ["shared-data", "scratch-data"]
    if answer == "back":
        assert selected is cli._WIZARD_BACKTRACK and not stopped
    elif answer is None or answer == "quit":
        assert stopped
    else:
        assert selected == answer and not stopped
    output = cli.console.export_text()
    assert "sfs_filesystem_keys" not in output and "default" not in output
    if answer == []:
        assert 'Shared storage for "core-services": none' in output
    elif answer == ["scratch"]:
        assert 'Shared storage for "core-services": scratch-data' in output
    else:
        assert "PV/PVC setup are separate steps" in output


@pytest.mark.parametrize(
    "answers, expected",
    [
        ([""], ["default", "scratch"]),
        (["none"], []),
        (["bad", "0", "2"], ["scratch"]),
        (["q"], "back"),
        (["qq"], "quit"),
    ],
)
def test_text_prompt_and_retry(monkeypatch, answers, expected):
    pending = iter(answers)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: next(pending))
    monkeypatch.setattr(cli, "console", Console(width=160, record=True))
    selected, stopped = cli._prompt_node_group_shared_storage(
        group_name="core",
        choices=[OptionChoice("default", "data"), OptionChoice("scratch", "scratch-data")],
    )
    if expected == "back":
        assert selected is cli._WIZARD_BACKTRACK and not stopped
    elif expected == "quit":
        assert stopped
    else:
        assert selected == expected and not stopped
    assert next(pending, None) is None
    assert "[x] 1. data" in cli.console.export_text()


@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError])
@pytest.mark.parametrize("tty", [True, False])
def test_interrupted_storage_prompt_stops(monkeypatch, error, tty):
    def interrupt(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "_is_tty_session", lambda: tty)
    monkeypatch.setattr(cli, "_configure_questionary_checkbox_symbols", lambda: None)
    monkeypatch.setattr(cli.typer, "prompt", interrupt)
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        SimpleNamespace(Choice=lambda **kwargs: kwargs, checkbox=interrupt),
    )
    assert cli._prompt_node_group_shared_storage(
        group_name="core", choices=[OptionChoice("default", "data")]
    ) == (None, True)


@pytest.mark.parametrize("existing_sfs", [False, True])
@pytest.mark.parametrize("align_names", [False, True])
def test_wizard_configures_sfs_first_and_persists_independent_group_choices(
    monkeypatch, existing_sfs, align_names
):
    cluster_id = "mk8s" if align_names else "cluster"
    rows = [
        component("mk8s", cluster_id, {"cluster": {"cluster_name": "named-cluster"}}),
        component("sfs", "storage", {"name": "initial-name"}),
    ]
    original_sfs = copy.deepcopy(rows[1])
    existing_group = {"platform": "cpu-d3", "preset": "4vcpu-16gb", "sfs_filesystem_keys": []}
    if existing_sfs:
        rows[0]["inputs"]["node_groups"] = {"existing": copy.deepcopy(existing_group)}
    payload = {"version": "v2", "infra": {"components": rows}, "apps": {"charts": []}}
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="Cluster",
            wizard_fields={"inputs.node_groups": {"type_hint": "map(any)"}},
        ),
        ComponentEntry(
            id="sfs",
            scope="infra",
            config_path="infra.sfs",
            description="Storage",
            wizard_fields={"inputs.name": {"type_hint": "string"}},
        ),
    )
    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda entry: set())
    monkeypatch.setattr(cli, "_resolve_dynamic_field_choices", lambda **kwargs: [])
    monkeypatch.setattr(cli, "_provider_allowed_values_for_field", lambda **kwargs: (set(), ()))
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *args, **kwargs: True)
    groups = iter(["core", "batch"])
    add = iter([True, True, False] if existing_sfs else [True, False])
    events = []

    def prompt(path, current, **kwargs):
        if path.endswith(".inputs.name"):
            events.append("name")
            return "configured-storage", False
        if path.endswith(".node_groups.<new>.name"):
            events.append("group")
            return next(groups), False
        if path.endswith(".node_groups.add_another"):
            return next(add), False
        if path.endswith((".ssh.enabled", ".preemptible", ".autoscaling.enabled")):
            return False, False
        if path.endswith((".placement.rule", ".service_account.mode")):
            return "none", False
        if path.endswith(".platform"):
            return "cpu-d3", False
        if path.endswith(".preset"):
            return "4vcpu-16gb", False
        return current, False

    monkeypatch.setattr(cli, "_prompt_scalar_override", prompt)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    answers = iter(["", "none"])
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(cli, "console", Console(width=160, record=True))
    result, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={cluster_id} if existing_sfs else {cluster_id, "storage"},
        selected_apps=set(),
        infra_entries=entries,
        app_entries=(),
        align_infra_resource_names_before_apps=align_names,
    )
    assert completed
    saved = yaml.safe_load(result)
    groups = saved["infra"]["components"][0]["inputs"]["node_groups"]
    assert groups["core"]["sfs_filesystem_keys"] == ["default"]
    assert groups["batch"]["sfs_filesystem_keys"] == []
    assert events == (["group", "group"] if existing_sfs else ["name", "group", "group"])
    if existing_sfs:
        assert saved["infra"]["components"][1] == original_sfs
        assert groups["existing"] == existing_group
    output = cli.console.export_text()
    assert ("initial-name" if existing_sfs else "configured-storage") in output
    assert 'Shared storage for "batch": none' in output
    assert "sfs_filesystem_keys" not in output
    inputs = copy.deepcopy(saved["infra"]["components"][0]["inputs"])
    _materialize_mk8s_sfs_attachments(
        payload=saved,
        component_id="mk8s",
        instance_id=saved["infra"]["components"][0]["instance_id"],
        module_inputs=inputs,
        sfs_module_name_by_instance_id={"storage": "storage"},
    )
    assert (
        "module.storage.filesystems[key].id"
        in inputs["node_groups"]["core"]["filesystems"].expression
    )
    assert "filesystems" not in inputs["node_groups"]["batch"]


@pytest.mark.parametrize("soperator_install", [False, True])
def test_component_order_is_stable_with_vpc_first_and_sfs_before_consumers(
    monkeypatch, soperator_install
):
    order = [
        ("mk8s", "cluster"),
        ("sfs", "data"),
        ("vm", "worker"),
        ("vpc", "network"),
        ("sfs", "scratch"),
    ]
    payload = {
        "infra": {"components": [component(kind, name) for kind, name in order]},
        "apps": {"charts": []},
        "version": "v2",
    }
    if soperator_install:
        for row in payload["infra"]["components"]:
            if row["id"] == "sfs":
                row["inputs"] = {
                    "filesystems": {
                        role: {"name": role, "mount_tag": role, "size_gib": 1}
                        for role in ("accounting", "controller-spool", "jail")
                    }
                }
    entries = tuple(
        ComponentEntry(id=kind, scope="infra", config_path=f"infra.{kind}", description=kind)
        for kind in ("mk8s", "sfs", "vm", "vpc")
    )
    phases = []

    def phase(message, **kwargs):
        phases.append(message)
        return False

    monkeypatch.setattr(cli, "_wizard_continue_phase", phase)
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda entry: set())
    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    _, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={name for _, name in order},
        selected_apps=set(),
        infra_entries=entries,
        app_entries=(),
        soperator_install=soperator_install,
    )
    assert completed
    expected = (
        ["vpc@network", "mk8s@cluster", "sfs@data", "vm@worker", "sfs@scratch"]
        if soperator_install
        else ["vpc@network", "sfs@data", "sfs@scratch", "mk8s@cluster", "vm@worker"]
    )
    assert phases == [f"Configure '{label}' component fields now?" for label in expected]


def test_name_alignment_preserves_component_backtracking_targets(monkeypatch):
    payload = {
        "infra": {
            "components": [
                component("mk8s", "mk8s", {"cluster": {"cluster_name": "named-cluster"}}),
                component("sfs", "sfs", {"name": "named-storage"}),
            ]
        },
        "apps": {"charts": []},
        "version": "v2",
    }
    entries = tuple(
        ComponentEntry(id=kind, scope="infra", config_path=f"infra.{kind}", description=kind)
        for kind in ("mk8s", "sfs")
    )
    phases = []

    def phase(message, **kwargs):
        phases.append(message)
        return cli._WizardPhaseDecision(proceed=False, back=len(phases) == 2)

    monkeypatch.setattr(cli, "_wizard_continue_phase", phase)
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda entry: set())
    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    _, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"mk8s", "sfs"},
        selected_apps=set(),
        infra_entries=entries,
        app_entries=(),
        align_infra_resource_names_before_apps=True,
    )
    assert completed
    assert phases == [
        "Configure 'sfs' component fields now?",
        "Configure 'mk8s@named-cluster' component fields now?",
        "Configure 'sfs@named-storage' component fields now?",
        "Configure 'mk8s@named-cluster' component fields now?",
    ]
