from __future__ import annotations

import copy
from collections import deque

import pytest
import yaml

from nebius_cxcli.cluster_config import require_cluster_row
from nebius_cxcli.mysterybox_eso import (
    mysterybox_eso_selected_target_refs,
    normalize_mysterybox_eso_project_settings,
)
from nebius_cxcli.mysterybox_wizard import SYNC_QUESTION, MysteryBoxSyncWizard

BACK = object()
QUIT = object()


def project(*targets):
    return {
        "infra": {
            "components": [
                {"id": "mk8s", "instance_id": target, "enabled": True} for target in targets
            ]
        },
        "apps": {"charts": []},
    }


def add_eso(payload, target, *, enabled=True):
    row = {
        "id": "external-secrets",
        "instance_id": target,
        "enabled": enabled,
        "values": {"custom": "preserved"},
    }
    payload["apps"]["charts"].append(row)
    return row


class Script:
    def __init__(self, decisions=(), fields=()):
        self.decisions = deque(decisions)
        self.fields = deque(fields)
        self.confirmed = []
        self.prompted = []
        self.messages = []

    def confirm(self, label, default):
        self.confirmed.append((label, default))
        return self.decisions.popleft()

    def prompt(self, label, current, **kwargs):
        self.prompted.append((label, current, kwargs))
        value = self.fields.popleft()
        return (current, True) if value is QUIT else (value, False)

    def run(self, payload, *, state=None, component=None, catalog=True):
        self.state = state or MysteryBoxSyncWizard()
        result = self.state.configure(
            payload,
            component or {"id": "mysterybox", "instance_id": "secrets", "inputs": {}},
            confirm=self.confirm,
            prompt=self.prompt,
            is_back=lambda value: value is BACK,
            append_eso=add_eso if catalog else None,
            emit=self.messages.append,
        )
        assert not self.decisions
        assert not self.fields
        return result


def policy(payload, target="cluster"):
    return require_cluster_row(payload, target)["secrets"]["mysterybox"]


@pytest.mark.parametrize("selected_eso", [False, True])
def test_no_persists_explicit_opt_out_without_removing_eso(selected_eso):
    payload = project("cluster")
    if selected_eso:
        add_eso(payload, "cluster")
    charts = copy.deepcopy(payload["apps"])
    script = Script(["no"])
    assert script.run(payload) == "continue"
    assert script.confirmed == [(SYNC_QUESTION, selected_eso)]
    assert policy(payload) == {"enabled": False}
    normalize_mysterybox_eso_project_settings(payload)
    assert policy(payload)["enabled"] is False
    assert payload["apps"] == charts


@pytest.mark.parametrize("eso_state", ["absent", "disabled", "enabled"])
def test_yes_completes_target_and_preserves_existing_eso_values(eso_state):
    payload = project("cluster")
    if eso_state != "absent":
        add_eso(payload, "cluster", enabled=eso_state == "enabled")
    script = Script(["yes"] if eso_state == "enabled" else ["yes", "yes"], [["app", "jobs"]])
    assert script.run(payload) == "continue"
    assert policy(payload)["enabled"] is True
    assert policy(payload)["sync_namespaces"] == ["app", "jobs"]
    assert policy(payload)["refresh_interval"] == "15m"
    assert len(payload["apps"]["charts"]) == 1
    assert payload["apps"]["charts"][0]["values"] == {"custom": "preserved"}
    assert payload["apps"]["charts"][0]["enabled"] is True
    assert script.state.activated_targets == (set() if eso_state == "enabled" else {"cluster"})
    assert script.prompted[0][1] == ["default"]


def test_existing_settings_are_used_as_defaults_and_preserved():
    payload = project("cluster")
    add_eso(payload, "cluster")
    require_cluster_row(payload, "cluster")["secrets"] = {
        "mysterybox": {
            "enabled": True,
            "sync_namespaces": ["app"],
            "store_name": "custom-store",
            "refresh_interval": "30s",
            "allow_all_namespaces": False,
        }
    }
    script = Script(["yes"], [["app"]])
    assert script.run(payload) == "continue"
    assert script.confirmed == [(SYNC_QUESTION, True)]
    assert script.prompted[0][1] == ["app"]
    assert policy(payload)["store_name"] == "custom-store"
    assert policy(payload)["refresh_interval"] == "30s"
    assert policy(payload)["allow_all_namespaces"] is False


@pytest.mark.parametrize("answer", ["no", "back"])
def test_declining_eso_returns_to_sync_without_adding_app(answer):
    payload = project("cluster")
    script = Script(["yes", answer, "no"])
    assert script.run(payload) == "continue"
    assert payload["apps"]["charts"] == []
    assert policy(payload) == {"enabled": False}
    assert script.confirmed[-1] == (SYNC_QUESTION, False)


@pytest.mark.parametrize(
    "decisions,fields,outcome",
    [
        (["back"], [], "back"),
        (["quit"], [], "quit"),
        (["yes", "quit"], [], "quit"),
        (["yes", "yes"], [QUIT], "quit"),
    ],
)
def test_navigation_never_commits_an_incomplete_target(decisions, fields, outcome):
    payload = project("cluster")
    before = copy.deepcopy(payload)
    assert Script(decisions, fields).run(payload) == outcome
    assert payload == before


def test_namespace_back_discards_candidate_and_revisits_sync():
    payload = project("cluster")
    script = Script(["yes", "yes", "no"], [BACK])
    assert script.run(payload) == "continue"
    assert payload["apps"]["charts"] == []
    assert policy(payload) == {"enabled": False}


@pytest.mark.parametrize("invalid", [[], ["Bad_Namespace"], [123], "default"])
def test_invalid_namespaces_retry_before_committing(invalid):
    payload = project("cluster")
    script = Script(["yes", "yes"], [invalid, ["valid"]])
    assert script.run(payload) == "continue"
    assert len(script.prompted) == 2
    assert policy(payload)["sync_namespaces"] == ["valid"]


@pytest.mark.parametrize("enable", [False, True])
def test_multitarget_edit_preserves_other_target_and_reports_remaining_sync(enable):
    payload = project("one", "two")
    add_eso(payload, "one")
    add_eso(payload, "two")
    other = copy.deepcopy(require_cluster_row(payload, "two"))
    charts = copy.deepcopy(payload["apps"])
    script = Script(["yes" if enable else "no"], [["one"], *([["app"]] if enable else [])])
    assert script.run(payload) == "continue"
    assert require_cluster_row(payload, "two") == other
    assert payload["apps"] == charts
    active = ("one", "two") if enable else ("two",)
    assert mysterybox_eso_selected_target_refs(payload) == active
    assert script.messages[-1] == "MysteryBox synchronization enabled for: " + ", ".join(active)


def test_completed_target_survives_quit_on_later_target():
    payload = project("one", "two")
    script = Script(["yes", "yes", "quit"], [["one", "two"], ["default"]])
    assert script.run(payload) == "quit"
    assert policy(payload, "one")["enabled"] is True
    assert "secrets" not in require_cluster_row(payload, "two")
    assert script.state.activated_targets == {"one"}


def test_external_target_is_configurable_without_new_managed_cluster():
    payload = project()
    payload["infra"]["external_clusters"] = [
        {
            "instance_id": "outside",
            "kind": "external-mk8s",
            "ownership": "external",
        }
    ]
    script = Script(["yes", "yes"], [["default"]])
    assert script.run(payload) == "continue"
    assert policy(payload, "outside")["enabled"] is True
    assert payload["infra"]["components"] == []


def test_no_target_explains_and_retries_without_fabricating_configuration():
    payload = project()
    before = copy.deepcopy(payload)
    script = Script(["yes", "no"])
    assert script.run(payload) == "continue"
    assert payload == before
    assert "Configure a Kubernetes target first" in script.messages[1]


def test_missing_catalog_explains_and_retries():
    payload = project("cluster")
    script = Script(["yes", "no"])
    assert script.run(payload, catalog=False) == "continue"
    assert payload["apps"]["charts"] == []
    assert policy(payload)["enabled"] is False


def test_second_component_reuses_choices_but_revisiting_can_change_them():
    payload = project("cluster")
    state = MysteryBoxSyncWizard()
    assert Script(["no"]).run(payload, state=state) == "continue"
    assert (
        Script().run(
            payload,
            state=state,
            component={
                "id": "mysterybox",
                "instance_id": "another",
                "inputs": {},
            },
        )
        == "continue"
    )
    assert Script(["yes", "yes"], [["default"]]).run(payload, state=state) == "continue"
    assert policy(payload)["enabled"] is True


def test_wireguard_metadata_only_component_never_prompts_or_marks_setup_done():
    payload = project("cluster")
    before = copy.deepcopy(payload)
    script = Script()
    assert (
        script.run(
            payload,
            component={
                "id": "mysterybox",
                "instance_id": "vpn-access",
                "inputs": {"create_initial_versions": False},
            },
        )
        == "continue"
    )
    assert script.messages == []
    assert payload == before
    assert not script.state.visited_components


@pytest.mark.parametrize("enable", [False, True])
def test_shared_wizard_does_not_repeat_or_overwrite_completed_target_choices(monkeypatch, enable):
    import nebius_cxcli.cli as cli
    from nebius_cxcli.components import ComponentEntry

    payload = project("cluster")
    payload["version"] = "v2"
    payload["infra"]["components"].insert(
        0,
        {
            "id": "mysterybox",
            "instance_id": "secrets",
            "enabled": True,
            "inputs": {"secrets": [{"name": "app", "payload": {"KEY": {"type": "text"}}}]},
        },
    )
    add_eso(payload, "cluster")
    entries = (
        ComponentEntry(
            id="mysterybox",
            scope="infra",
            config_path="infra.components[].inputs",
            description="Secrets",
        ),
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.components[].inputs",
            description="Cluster",
            wizard_fields={
                "infra.components[].secrets.mysterybox.enabled": {
                    "default": True,
                    "write_default_to_config": True,
                },
                "infra.components[].secrets.mysterybox.sync_namespaces": {
                    "default": ["default"],
                    "type_hint": "list(string)",
                    "prompt_complex": True,
                    "write_default_to_config": True,
                    "required": True,
                },
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda label, **kwargs: enable if label == SYNC_QUESTION else True,
    )
    prompted = []

    def prompt(label, current, **kwargs):
        prompted.append(label)
        if label.startswith("Synchronization namespaces"):
            return ["chosen"], False
        assert ".secrets.mysterybox." not in label
        return current, False

    monkeypatch.setattr(cli, "_prompt_scalar_override", prompt)
    output, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"secrets", "cluster"},
        selected_apps=set(),
        infra_entries=entries,
        app_entries=(),
    )
    assert completed
    result = yaml.safe_load(output)
    assert policy(result)["enabled"] is enable
    if enable:
        assert policy(result)["sync_namespaces"] == ["chosen"]
        assert prompted.count("Synchronization namespaces for cluster") == 1
