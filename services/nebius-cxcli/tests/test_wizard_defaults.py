from __future__ import annotations

import copy

import pytest
import yaml
from rich.console import Console

from nebius_cxcli import cli, grafana_install
from nebius_cxcli.cluster_config import cluster_rows
from nebius_cxcli.observability import materialize_observability_app_values
from nebius_cxcli.observability_routing import SIGNALS, app_row, target_settings
from nebius_cxcli.wizard_defaults import (
    prompt_bucket_labels,
    prompt_telemetry_mode,
    prompt_wireguard_input,
    remote_telemetry_candidate,
)
from test_observability_routing import payload as routing_payload
from test_phase3_prompt_introspection import (
    _local_observability_answers,
    _mk8s_observability_payload,
    _mk8s_observability_wizard_entry,
    _observability_wizard_entries,
)


def run_wizard(monkeypatch, *, answers=None, payload=None, edit=True, align=False):
    source = payload or _mk8s_observability_payload()
    responses = {label: list(values) for label, values in (answers or {}).items()}
    prompts = []

    def prompt(label, current, **kwargs):
        prompts.append((label, current))
        pending = responses.get(label)
        return (pending.pop(0) if pending else current), False

    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    monkeypatch.setattr(cli, "helm_chart_default_values", lambda **kwargs: {})
    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda label, **kwargs: edit and label == "Configure 'mk8s' component fields now?",
    )
    monkeypatch.setattr(cli, "_prompt_scalar_override", prompt)
    monkeypatch.setattr(
        cli,
        "_prompt_choice_override",
        lambda path_label, current, **kwargs: prompt(path_label, current, **kwargs),
    )
    monkeypatch.setattr(cli, "console", Console(width=180, record=True))
    result, complete = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(source),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=_observability_wizard_entries(),
        new_mk8s_target_refs=frozenset({"mk8s"}),
        align_infra_resource_names_before_apps=align,
    )
    assert complete
    return yaml.safe_load(result), prompts


@pytest.mark.parametrize("edit", [True, False])
def test_new_target_defaults_to_nebius_collection_without_local_stack(monkeypatch, edit):
    monkeypatch.setattr(
        grafana_install,
        "configure",
        lambda *a, **k: pytest.fail("remote-only called Grafana installer"),
    )
    saved, prompts = run_wizard(monkeypatch, edit=edit)
    obs = cluster_rows(saved)[0]["observability"]
    assert obs["enabled"] is True
    assert [obs["routing"][signal]["storage"] for signal in SIGNALS] == ["remote"] * 3
    assert {row["id"] for row in saved["apps"]["charts"] if row["enabled"]} == {
        "nebius-observability-agent"
    }
    if edit:
        assert ("Observability", "managed") in prompts
    materialize_observability_app_values(saved)
    agent = app_row(saved, "mk8s", "nebius-observability-agent")
    assert agent["version"] == "1.0.5"
    assert all(agent["values"]["config"][signal]["enabled"] for signal in SIGNALS)
    output = cli.console.export_text()
    assert (
        "write.monitoring." in output and "write.logging." in output and "write.tracing." in output
    )
    assert "Applications must be instrumented" in output


def test_disabled_collection_creates_no_observability_apps(monkeypatch):
    saved, prompts = run_wizard(monkeypatch, answers={"Observability": ["disabled"]})
    assert cluster_rows(saved)[0]["observability"]["enabled"] is False
    assert not saved["apps"]["charts"]
    assert sum(label == "Observability" for label, _ in prompts) == 1


def test_local_opt_in_uses_existing_grafana_configuration(monkeypatch):
    _local_observability_answers(monkeypatch)
    saved, _ = run_wizard(monkeypatch, answers={"Observability": ["local"]})
    assert app_row(saved, "mk8s", "grafana")
    assert app_row(saved, "mk8s", "nebius-observability-agent") is None
    assert [target_settings(saved, "mk8s")[signal]["storage"] for signal in SIGNALS] == [
        "local"
    ] * 3


@pytest.mark.parametrize("local", [False, True])
def test_backtracking_off_removes_only_session_additions(monkeypatch, local):
    _local_observability_answers(monkeypatch)
    saved, _ = run_wizard(
        monkeypatch,
        answers={
            "Observability": ["local" if local else "managed", "disabled"],
            "infra.components[0].observability.kubernetes.logs.enabled": [cli._WIZARD_BACKTRACK],
        },
    )
    obs = cluster_rows(saved)[0]["observability"]
    assert obs["enabled"] is False and "routing" not in obs
    assert not saved["apps"]["charts"]


@pytest.mark.parametrize("first_local", [False, True])
def test_backtracking_between_remote_and_local_replaces_session_owned_pipeline(
    monkeypatch, first_local
):
    _local_observability_answers(monkeypatch)
    saved, _ = run_wizard(
        monkeypatch,
        answers={
            "Observability": [
                "local" if first_local else "managed",
                "managed" if first_local else "local",
            ],
            "infra.components[0].observability.kubernetes.logs.enabled": [cli._WIZARD_BACKTRACK],
        },
    )
    assert bool(app_row(saved, "mk8s", "grafana")) is not first_local
    assert bool(app_row(saved, "mk8s", "nebius-observability-agent")) is first_local


def test_existing_other_target_configuration_is_preserved(monkeypatch):
    existing = grafana_install.configure(routing_payload(), "cluster")
    source = _mk8s_observability_payload()
    source["client_info"] = copy.deepcopy(existing["client_info"])
    source["infra"]["components"].extend(copy.deepcopy(existing["infra"]["components"]))
    source["apps"]["charts"] = copy.deepcopy(existing["apps"]["charts"])
    saved, _ = run_wizard(monkeypatch, payload=source)
    assert cluster_rows(saved)[1] == cluster_rows(existing)[0]
    assert [
        row for row in saved["apps"]["charts"] if row.get("target_ref") == "cluster"
    ] == existing["apps"]["charts"]


def test_remote_candidate_is_atomic_idempotent_and_project_scoped():
    source = routing_payload()
    before = copy.deepcopy(source)
    candidate = remote_telemetry_candidate(source, "cluster")
    assert source == before
    assert remote_telemetry_candidate(candidate, "cluster") == candidate
    routing = target_settings(candidate, "cluster")
    assert (
        routing["metrics"]["remote"]["url"]
        == "https://write.monitoring.eu-north1.nebius.cloud/projects/project-fixture/prometheus/api/v1/write"
    )
    assert routing["logs"]["remote"]["url"] == "https://write.logging.eu-north1.nebius.cloud:443"
    assert routing["traces"]["remote"]["url"] == "https://write.tracing.eu-north1.nebius.cloud:443"
    assert all(routing[signal]["remote"]["auth"] == "nebius" for signal in SIGNALS)


@pytest.mark.parametrize("signal", SIGNALS)
def test_individual_signal_disable_is_honored_in_rendered_collector(monkeypatch, signal):
    saved, _ = run_wizard(
        monkeypatch,
        answers={f"infra.components[0].observability.kubernetes.{signal}.enabled": [False]},
    )
    materialize_observability_app_values(saved)
    agent = app_row(saved, "mk8s", "nebius-observability-agent")
    assert agent["values"]["config"][signal]["enabled"] is False


def prompt_answers(monkeypatch, answers):
    values = iter(answers)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: next(values))


def test_bucket_labels_default_to_empty(monkeypatch):
    prompt_answers(monkeypatch, ["false"])
    assert prompt_bucket_labels(None) == ({}, False)


def test_bucket_labels_preserve_literal_strings(monkeypatch):
    prompt_answers(
        monkeypatch,
        ["true", "environment", "dev", "serial", "001", "flag", "false", "nil", "null", ""],
    )
    assert prompt_bucket_labels(None) == (
        {"environment": "dev", "serial": "001", "flag": "false", "nil": "null"},
        False,
    )


@pytest.mark.parametrize("action, expected", [("keep", {"env": "dev"}), ("clear", {})])
def test_existing_bucket_labels_keep_or_clear(monkeypatch, action, expected):
    monkeypatch.setattr(cli, "_prompt_choice_override", lambda **k: (action, False))
    assert prompt_bucket_labels({"env": "dev"}) == (expected, False)


def test_bucket_label_edit_requires_duplicate_replacement(monkeypatch):
    monkeypatch.setattr(cli, "_prompt_choice_override", lambda **k: ("edit", False))
    prompt_answers(monkeypatch, ["env", "false", "env", "true", "prod", ""])
    original = {"env": "dev"}
    assert prompt_bucket_labels(original) == ({"env": "prod"}, False)
    assert original == {"env": "dev"}


@pytest.mark.parametrize("token, stop", [("q", False), ("qq", True)])
def test_bucket_label_navigation_does_not_commit_partial_edits(monkeypatch, token, stop):
    monkeypatch.setattr(cli, "_prompt_choice_override", lambda **k: ("edit", False))
    original = {"env": "dev"}
    prompt_answers(monkeypatch, ["new", "value", "next", token])
    result, stopped = prompt_bucket_labels(original)
    assert stopped is stop
    assert result == (original if stop else cli._WIZARD_BACKTRACK)
    assert original == {"env": "dev"}


@pytest.mark.parametrize(
    "field, current, answer, expected",
    [
        ("create_public_ip_allocation", True, "false", False),
        ("nat_mode", True, "false", False),
        ("client_default_persistent_keepalive", 25, "0", 0),
    ],
)
def test_wireguard_preserves_explicit_false_and_zero(monkeypatch, field, current, answer, expected):
    prompt_answers(monkeypatch, [answer])
    assert prompt_wireguard_input(field, current, {}, "vpn") == (expected, False)


def test_public_ip_name_stays_derived_until_overridden(monkeypatch):
    monkeypatch.setattr(cli, "console", Console(record=True))
    prompt_answers(monkeypatch, ["", "", "custom-name"])
    assert prompt_wireguard_input("public_ip_allocation_name", None, {"name": "vpn"}, "vpn") == (
        None,
        False,
    )
    assert prompt_wireguard_input(
        "public_ip_allocation_name", None, {"name": "renamed"}, "vpn"
    ) == (None, False)
    assert prompt_wireguard_input("public_ip_allocation_name", None, {}, "vpn") == (
        "custom-name",
        False,
    )
    output = cli.console.export_text()
    assert "vpn-public-ip" in output and "renamed-public-ip" in output


def test_telemetry_named_choice_disables_collection(monkeypatch):
    prompt_answers(monkeypatch, ["3"])
    assert prompt_telemetry_mode(target="cluster", enabled=True, local=False) == (
        {"enabled": False, "local": False},
        False,
    )


def test_telemetry_named_choice_quit_cancels_selection(monkeypatch):
    prompt_answers(monkeypatch, ["qq"])
    assert prompt_telemetry_mode(target="cluster", enabled=True, local=False) == ("managed", True)


def run_wireguard_wizard(monkeypatch, *, initial=None, answers=None):
    from pathlib import Path

    from nebius_cxcli.components import ComponentEntry
    from nebius_cxcli.wizard_profiles import resolve_builtin_wizard_profile

    source = str(Path(__file__).resolve().parents[3] / "platform-infra/modules/wireguard-gw")
    fields = {
        "create_public_ip_allocation",
        "public_ip_allocation_id",
        "public_ip_allocation_name",
        "client_default_persistent_keepalive",
        "nat_mode",
    }
    specs = tuple(spec for spec in cli.module_variables(source) if spec.name in fields)
    assert {spec.name for spec in specs} == fields
    entry = ComponentEntry(
        id="wireguard-gw",
        scope="infra",
        config_path="infra.wireguard-gw",
        description="VPN",
        source=source,
        wizard_fields={
            key: value
            for key, value in resolve_builtin_wizard_profile("wireguard-gw").items()
            if key.removeprefix("inputs.") in fields
        },
    )
    monkeypatch.setattr(cli, "module_variables", lambda source: specs)
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda entry: set())
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    prompts = []
    responses = {key: list(values) for key, values in (answers or {}).items()}

    def prompt(label, **kwargs):
        prompts.append(label)
        for key, values in responses.items():
            if key in label and values:
                return values.pop(0)
        if "public_ip_allocation_id" in label:
            return "allocation-fixture"
        return kwargs.get("default", "")

    monkeypatch.setattr(cli.typer, "prompt", prompt)
    payload = {
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"name": "vpn", **(initial or {})},
                }
            ]
        },
        "apps": {"charts": []},
        "version": "v2",
    }
    result, complete = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"vpn"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
    )
    assert complete
    return yaml.safe_load(result), prompts


@pytest.mark.parametrize("reuse", [False, True])
def test_wireguard_wizard_persists_module_defaults_and_reuse(monkeypatch, reuse):
    saved, prompts = run_wireguard_wizard(
        monkeypatch, answers={"Create a new public IP": ["false" if reuse else "true"]}
    )
    inputs = saved["infra"]["components"][0]["inputs"]
    assert inputs["create_public_ip_allocation"] is not reuse
    assert inputs["client_default_persistent_keepalive"] == 25
    assert inputs["nat_mode"] is True
    assert "public_ip_allocation_name" not in inputs
    assert ("public_ip_allocation_id" in inputs) is reuse
    assert any("name override" in label for label in prompts) is not reuse


def test_guided_bucket_labels_are_persisted_through_field_wizard(monkeypatch):
    from nebius_cxcli.components import ComponentEntry

    entry = ComponentEntry(
        id="object-storage",
        scope="infra",
        config_path="infra.object-storage",
        description="Bucket",
        wizard_fields={
            "inputs.labels": {"type_hint": "map(string)", "default": {}, "prompt_complex": True}
        },
    )
    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda entry: set())
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    prompt_answers(monkeypatch, ["true", "environment", "dev", ""])
    payload = {
        "infra": {
            "components": [
                {"id": "object-storage", "instance_id": "bucket", "enabled": True, "inputs": {}}
            ]
        },
        "apps": {"charts": []},
        "version": "v2",
    }
    result, complete = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"bucket"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
    )
    assert complete
    assert yaml.safe_load(result)["infra"]["components"][0]["inputs"]["labels"] == {
        "environment": "dev"
    }


def test_renamed_new_cluster_keeps_remote_only_selection(monkeypatch):
    payload = _mk8s_observability_payload()
    payload["infra"]["components"][0]["inputs"]["cluster"] = {"cluster_name": "renamed-cluster"}
    saved, _ = run_wizard(monkeypatch, payload=payload, align=True)
    assert app_row(saved, "renamed-cluster", "nebius-observability-agent")
    assert app_row(saved, "renamed-cluster", "grafana") is None
    assert target_settings(saved, "renamed-cluster")["metrics"]["storage"] == "remote"


def test_disabling_all_signals_disables_collection_and_session_apps(monkeypatch):
    saved, _ = run_wizard(
        monkeypatch,
        answers={
            f"infra.components[0].observability.kubernetes.{signal}.enabled": [False]
            for signal in SIGNALS
        },
    )
    assert cluster_rows(saved)[0]["observability"]["enabled"] is False
    assert saved["apps"]["charts"] == []


def test_remote_collection_round_trips_and_renders_only_the_pinned_agent(tmp_path):
    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.config_model import to_dynamic_payload
    from nebius_cxcli.flux_render import render_flux
    from nebius_cxcli.paths import resolve_project_paths
    from test_render import _mk8s_inputs, _starter_payload

    data = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    next(row for row in data["infra"]["components"] if row["id"] == "mk8s")["inputs"] = (
        _mk8s_inputs()
    )
    candidate = remote_telemetry_candidate(data, "mk8s")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(to_dynamic_payload(candidate)))
    config = load_config(path)
    saved = to_dynamic_payload(config)
    assert all(target_settings(saved, "mk8s")[signal]["storage"] == "remote" for signal in SIGNALS)
    files = render_flux(config, resolve_project_paths(path))
    releases = [
        doc
        for file in files
        if file.suffix == ".yaml"
        for doc in yaml.safe_load_all(file.read_text())
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease"
    ]
    assert [doc["metadata"]["name"] for doc in releases] == ["nebius-observability-agent"]
    agent = releases[0]["spec"]
    assert agent["chart"]["spec"]["version"] == "1.0.5"
    assert all(agent["values"]["config"][signal]["enabled"] for signal in SIGNALS)


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("finish", ["off", "local"])
def test_backtracking_restores_preexisting_disabled_collector(monkeypatch, bound, finish):
    _local_observability_answers(monkeypatch)
    source = _mk8s_observability_payload()
    original = {"id": "nebius-observability-agent", "enabled": False, "values": {"custom": "keep"}}
    if bound:
        original.update(instance_id="mk8s", target_ref="mk8s")
    source["apps"]["charts"] = [copy.deepcopy(original)]
    saved, _ = run_wizard(
        monkeypatch,
        payload=source,
        answers={
            "Observability": ["managed", "disabled" if finish == "off" else "local"],
            "infra.components[0].observability.kubernetes.logs.enabled": [cli._WIZARD_BACKTRACK],
        },
    )
    assert original in saved["apps"]["charts"]
    assert not app_row(saved, "mk8s", "nebius-observability-agent")


@pytest.mark.parametrize("backtrack", [False, True])
def test_wireguard_reuse_to_create_discards_conflicting_id_after_mode_choice(
    monkeypatch, backtrack
):
    answers = {"Create a new public IP": ["false", "true"] if backtrack else ["true"]}
    if backtrack:
        answers["public_ip_allocation_id"] = ["q"]
    saved, prompts = run_wireguard_wizard(
        monkeypatch,
        initial={
            "create_public_ip_allocation": False,
            "public_ip_allocation_id": "existing-allocation",
        },
        answers=answers,
    )
    inputs = saved["infra"]["components"][0]["inputs"]
    assert inputs["create_public_ip_allocation"] is True
    assert "public_ip_allocation_id" not in inputs
    assert not cli._jump_host_public_ip_allocation_issues(saved)
    assert "Create a new public IP" in prompts[0]


@pytest.mark.parametrize("current, invalid", [(25, "-1"), (25, "65536"), (25.0, "1.5")])
def test_wireguard_keepalive_reprompts_invalid_values(monkeypatch, current, invalid):
    prompt_answers(monkeypatch, [invalid, "25"])
    assert prompt_wireguard_input("client_default_persistent_keepalive", current, {}, "vpn") == (
        25,
        False,
    )


def test_existing_public_ip_name_can_reset_to_automatic(monkeypatch):
    prompt_answers(monkeypatch, ["automatic"])
    assert prompt_wireguard_input(
        "public_ip_allocation_name", "custom", {"name": "vpn"}, "vpn"
    ) == (None, False)


@pytest.mark.parametrize("current, value", [(25, "0"), (25.0, "65535")])
def test_wireguard_keepalive_accepts_boundaries_as_integers(monkeypatch, current, value):
    prompt_answers(monkeypatch, [value])
    selected, stop = prompt_wireguard_input(
        "client_default_persistent_keepalive", current, {}, "vpn"
    )
    assert stop is False and selected == int(value) and type(selected) is int


@pytest.mark.parametrize("token", ["q", "qq"])
def test_wireguard_keepalive_navigation_after_invalid_input(monkeypatch, token):
    prompt_answers(monkeypatch, ["-1", token])
    selected, stop = prompt_wireguard_input("client_default_persistent_keepalive", 25, {}, "vpn")
    assert stop is (token == "qq")
    if not stop:
        assert selected is cli._WIZARD_BACKTRACK


@pytest.mark.parametrize("token", ["q", "qq"])
def test_existing_public_ip_name_navigation_preserves_override(monkeypatch, token):
    prompt_answers(monkeypatch, [token])
    inputs = {"name": "vpn", "public_ip_allocation_name": "custom"}
    selected, stop = prompt_wireguard_input("public_ip_allocation_name", "custom", inputs, "vpn")
    assert inputs["public_ip_allocation_name"] == "custom"
    assert stop is (token == "qq")
    if not stop:
        assert selected is cli._WIZARD_BACKTRACK


def test_automatic_ip_name_reset_round_trips_without_stale_override(monkeypatch):
    saved, _ = run_wireguard_wizard(
        monkeypatch,
        initial={"public_ip_allocation_name": "custom"},
        answers={"Public IP allocation name": ["automatic"]},
    )
    inputs = saved["infra"]["components"][0]["inputs"]
    assert inputs.get("public_ip_allocation_name") is None
    assert not cli._jump_host_public_ip_allocation_issues(saved)
    monkeypatch.setattr(cli, "console", Console(record=True))
    prompt_answers(monkeypatch, [""])
    assert prompt_wireguard_input(
        "public_ip_allocation_name",
        inputs.get("public_ip_allocation_name"),
        {"name": "renamed"},
        "vpn",
    ) == (None, False)
    assert "renamed-public-ip" in cli.console.export_text()


def test_wireguard_create_to_reuse_requires_an_allocation_id(monkeypatch):
    saved, prompts = run_wireguard_wizard(
        monkeypatch,
        initial={"create_public_ip_allocation": True},
        answers={"Create a new public IP": ["false"]},
    )
    inputs = saved["infra"]["components"][0]["inputs"]
    assert inputs["create_public_ip_allocation"] is False
    assert inputs["public_ip_allocation_id"] == "allocation-fixture"
    assert "Create a new public IP" in prompts[0]
    assert any("public_ip_allocation_id" in label for label in prompts)


def test_explicit_managed_selection_replaces_custom_routes_but_keeps_named_queries():
    from nebius_cxcli.observability_routing import collector_owners, endpoint_defaults

    source = grafana_install.configure(
        routing_payload(),
        "cluster",
        overrides={
            "metrics_storage": "remote",
            "metrics_remote": "https://metrics.example.com/write",
        },
        datasources=[
            ("metrics-remote", "prometheus", "https://metrics.example.com/read"),
            ("independent-query", "prometheus", "https://history.example.com/read"),
        ],
    )
    routing = cluster_rows(source)[0]["observability"]["routing"]
    routing["metrics"]["remote"]["auth_secret"] = {"name": "custom-write", "key": "token"}
    routing["datasources"][0]["auth_secret"] = {"name": "custom-read", "key": "token"}
    before = copy.deepcopy(source)
    result = remote_telemetry_candidate(source, "cluster")
    selected = target_settings(result, "cluster")
    defaults = endpoint_defaults(result)
    for signal in SIGNALS:
        assert selected[signal]["remote"]["url"] == defaults[signal]["write"]
        assert selected[signal]["remote"]["auth"] == "nebius"
        assert not selected[signal]["remote"].get("auth_secret")
    assert set(collector_owners(result, "cluster").values()) == {"nebius-observability-agent"}
    assert [item["name"] for item in selected["datasources"]] == ["independent-query"]
    assert source == before


@pytest.mark.parametrize(
    "default, expected",
    [
        ("metrics-local", "metrics-remote"),
        ("logs-local", "metrics-remote"),
        ("traces-local", "metrics-remote"),
        ("independent-query", "independent-query"),
    ],
)
def test_managed_selection_preserves_only_available_explicit_default(default, expected):
    from nebius_cxcli.observability_routing import connections

    source = grafana_install.configure(
        routing_payload(),
        "cluster",
        overrides={"default_datasource": default},
        datasources=[("independent-query", "prometheus", "https://history.example.com/read")],
    )
    result = remote_telemetry_candidate(source, "cluster")
    queries = connections(result, "cluster")
    assert [item["name"] for item in queries if item["isDefault"]] == [expected]
