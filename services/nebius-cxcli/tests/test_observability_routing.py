from __future__ import annotations

import copy
import itertools

import pytest
import yaml
from typer.testing import CliRunner

from nebius_cxcli import cli, grafana_install
from nebius_cxcli.observability import materialize_observability_app_values
from nebius_cxcli.observability_backends import metrics_destinations
from nebius_cxcli.observability_routing import (
    SIGNALS,
    app_row,
    collector_owners,
    connections,
    required_apps,
    resolve_settings,
    save_settings,
    target_settings,
)


def payload():
    return {
        "client_info": {"nebius": {"project_id": "project-fixture", "region_id": "eu-north1"}},
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "cluster", "enabled": True, "inputs": {}}]
        },
        "apps": {"charts": []},
        "deploy": {"targets": [{"instance_id": "cluster"}]},
    }


@pytest.mark.parametrize("mode", ["local", "remote", "both"])
def test_full_config_save_load_render_keeps_routes_and_unique_apps(tmp_path, mode):
    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.config_model import to_dynamic_payload
    from nebius_cxcli.flux_render import render_flux
    from nebius_cxcli.paths import resolve_project_paths
    from test_render import _mk8s_inputs, _starter_payload

    data = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    next(row for row in data["infra"]["components"] if row["id"] == "mk8s")["inputs"] = (
        _mk8s_inputs()
    )
    data = grafana_install.configure(
        data, "mk8s", overrides={f"{signal}_storage": mode for signal in SIGNALS}
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(to_dynamic_payload(data)))
    config = load_config(path)
    saved = to_dynamic_payload(config)
    assert target_settings(saved, "mk8s")["metrics"]["storage"] == mode
    identities = [(row["id"], row.get("instance_id")) for row in saved["apps"]["charts"]]
    assert len(set(identities)) == len(identities)
    files = render_flux(config, resolve_project_paths(path))
    releases = [
        doc
        for file in files
        if file.suffix == ".yaml"
        for doc in yaml.safe_load_all(file.read_text())
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease"
    ]
    grafana = next(doc for doc in releases if doc["metadata"]["name"] == "grafana")
    sources = grafana["spec"]["values"]["datasources"]["datasources.yaml"]["datasources"]
    assert {row["name"] for row in sources} == {row["name"] for row in connections(saved, "mk8s")}
    if mode != "remote":
        stack = next(
            doc for doc in releases if doc["metadata"]["name"] == "victoria-metrics-k8s-stack"
        )
        destinations = stack["spec"]["values"]["vmagent"]["spec"]["remoteWrite"]
        assert len(destinations) == (2 if mode == "both" else 1)
        assert ".svc:" in destinations[0]["url"]
        if mode == "local":
            assert "nebius.cloud" not in str(destinations)


@pytest.mark.parametrize(
    "routing",
    [
        {"metric": {}},
        {"metrics": []},
        {"metrics": {"remote": {"url": 1}}},
        {"datasources": {}},
        {"signals": [{}]},
    ],
)
def test_invalid_saved_routing_fails_as_configuration_error(routing):
    data = payload()
    data["deploy"]["targets"][0]["observability"] = {"routing": routing}
    with pytest.raises(ValueError):
        resolve_settings(data, "cluster")


def test_local_default_complete_and_idempotent():
    source = payload()
    before = copy.deepcopy(source)
    candidate = grafana_install.configure(source, "cluster")
    assert source == before
    settings = target_settings(candidate, "cluster")
    assert [settings[s]["storage"] for s in SIGNALS] == ["local"] * 3
    assert settings["pushgateway"] is False
    assert app_row(candidate, "cluster", "nebius-observability-agent") is None
    assert grafana_install.configure(candidate, "cluster") == candidate
    materialize_observability_app_values(candidate)
    assert len(metrics_destinations(candidate, "cluster")) == 1
    assert "nebius.cloud" not in str(metrics_destinations(candidate, "cluster"))
    grafana = app_row(candidate, "cluster", "grafana")["values"]
    assert [d["type"] for d in grafana["datasources"]["datasources.yaml"]["datasources"]] == [
        "prometheus",
        "victoriametrics-logs-datasource",
        "jaeger",
    ]
    assert "victoriametrics-logs-datasource" in grafana["plugins"]
    assert all(
        "Authorization" not in str(d)
        for d in grafana["datasources"]["datasources.yaml"]["datasources"]
    )


def test_newly_selected_grafana_row_stays_private():
    from test_render import _starter_payload

    data = grafana_install.configure(
        _starter_payload(selected_infra={"mk8s"}, selected_apps={"grafana"}), "mk8s"
    )
    materialize_observability_app_values(data)
    assert target_settings(data, "mk8s")["grafana_access"] == "private"
    values = app_row(data, "mk8s", "grafana")["values"]
    assert not values["route"]["main"]["enabled"]
    assert not any(item.get("kind") == "Gateway" for item in values.get("extraObjects", []))


def test_preexisting_grafana_access_is_preserved_explicitly():
    from test_render import _starter_payload

    source = _starter_payload(selected_infra={"mk8s"}, selected_apps={"grafana"})
    data = grafana_install.configure(source, "mk8s", preserve_existing_access=True)
    materialize_observability_app_values(data)
    assert target_settings(data, "mk8s")["grafana_access"] == "existing"
    values = app_row(data, "mk8s", "grafana")["values"]
    assert values["route"]["main"]["enabled"] is True


def test_headless_grafana_completes_standalone_backend_routing():
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.observability_routing import enable_app, ensure_routing_apps

    data = payload()
    entries = {entry.id: entry for entry in component_entries("apps")}
    enable_app(data, "cluster", entries["victoria-logs-single"])
    ensure_routing_apps(data)
    assert target_settings(data, "cluster")["signals"] == ["logs"]
    enable_app(data, "cluster", entries["grafana"])
    assert grafana_install.initialize_selected_grafana(data)
    settings = target_settings(data, "cluster")
    assert set(settings["signals"]) == {"metrics", "logs", "traces"}
    assert settings["grafana_access"] == "private"
    assert app_row(data, "cluster", "victoria-metrics-k8s-stack")
    assert app_row(data, "cluster", "victoria-traces-single")
    assert grafana_install.configure(data, "cluster") == data


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, "lab-results-prometheus-pushgateway.observability.svc:9091"),
        ({"fullnameOverride": "results"}, "results.observability.svc:9091"),
        (
            {"nameOverride": "pg", "namespaceOverride": "labs", "service": {"port": 9092}},
            "lab-results-pg.labs.svc:9092",
        ),
    ],
)
def test_pushgateway_custom_service_identity_matches_scrape_and_publication(overrides, expected):
    from nebius_cxcli.observability_backends import pushgateway_scrape

    data = grafana_install.configure(payload(), "cluster", overrides={"pushgateway": True})
    row = app_row(data, "cluster", "prometheus-pushgateway")
    row["release-name"] = "lab-results"
    row["values"].update(overrides)
    assert pushgateway_scrape(data, "cluster")["static_configs"][0]["targets"] == [expected]
    assert any("http://" + expected in line for line in grafana_install.summary(data, "cluster"))


def test_local_logs_remove_stale_external_node_pipeline():
    data = grafana_install.configure(payload(), "cluster")
    node = app_row(data, "cluster", "opentelemetry-logs")
    node["values"]["config"] = {
        "exporters": {"otlp_grpc/old": {"endpoint": "external.example:443"}},
        "service": {
            "pipelines": {"logs/old": {"receivers": ["filelog"], "exporters": ["otlp_grpc/old"]}}
        },
        "processors": {"batch": {"timeout": "3s"}},
    }
    materialize_observability_app_values(data)
    config = node["values"]["config"]
    assert "otlp_grpc/old" not in config["exporters"]
    assert "logs/old" not in config["service"]["pipelines"]
    assert config["processors"]["batch"]["timeout"] == "3s"


@pytest.mark.parametrize("modes", itertools.product(("local", "remote", "both"), repeat=3))
def test_all_signal_routes_have_one_collector_and_matching_queries(modes):
    data = grafana_install.configure(
        payload(),
        "cluster",
        overrides={f"{signal}_storage": mode for signal, mode in zip(SIGNALS, modes, strict=True)},
    )
    owners = collector_owners(data, "cluster")
    for signal, mode in zip(SIGNALS, modes, strict=True):
        if mode == "remote":
            assert owners[signal] == "nebius-observability-agent"
        else:
            assert owners[signal] != "nebius-observability-agent"
    assert len(connections(data, "cluster")) == sum(2 if mode == "both" else 1 for mode in modes)


def test_pushgateway_is_optional_for_any_storage():
    for mode in ("local", "remote", "both"):
        data = grafana_install.configure(
            payload(), "cluster", overrides={"metrics_storage": mode, "pushgateway": True}
        )
        assert "prometheus-pushgateway" in required_apps(data, "cluster")
        materialize_observability_app_values(data)
        owner = app_row(data, "cluster", collector_owners(data, "cluster")["metrics"])
        assert "cxcli-pushgateway" in str(owner["values"])
        assert "5s" in str(owner["values"])


def test_remote_url_alone_does_not_enable_export():
    data = grafana_install.configure(
        payload(), "cluster", overrides={"metrics_remote": "https://metrics.example.com/write"}
    )
    assert target_settings(data, "cluster")["metrics"]["storage"] == "local"
    assert len(metrics_destinations(data, "cluster")) == 1


def test_custom_write_requires_explicit_typed_read_and_never_inherits_auth():
    with pytest.raises(ValueError, match="requires --datasource"):
        grafana_install.configure(
            payload(),
            "cluster",
            overrides={
                "logs_storage": "remote",
                "logs_remote": "https://logs.example.com/write",
                "logs_remote_protocol": "otlp-http",
            },
        )
    data = grafana_install.configure(
        payload(),
        "cluster",
        overrides={
            "logs_storage": "remote",
            "logs_remote": "https://logs.example.com/write",
            "logs_remote_protocol": "otlp-http",
        },
        datasources=[("logs-remote", "loki", "https://logs.example.com/read")],
    )
    ds = next(d for d in connections(data, "cluster") if d["name"] == "logs-remote")
    assert ds.get("auth", "none") == "none"
    assert collector_owners(data, "cluster")["logs"] == "opentelemetry-collector"


def test_flags_preserve_saved_choices_and_default_selects_by_name():
    data = grafana_install.configure(payload(), "cluster", overrides={"pushgateway": True})
    next_data = grafana_install.configure(
        data,
        "cluster",
        overrides={"logs_storage": "both"},
        datasources=[("production-logs", "loki", "http://loki.example.com:3100")],
    )
    assert target_settings(next_data, "cluster")["pushgateway"] is True
    assert len(connections(next_data, "cluster")) == 5
    with pytest.raises(ValueError, match="Default datasource"):
        grafana_install.configure(data, "cluster", overrides={"default_datasource": "missing"})


@pytest.mark.parametrize("auth", ["none", "secret", "nebius"])
def test_repeated_datasource_flag_preserves_auth_order_and_identity(auth):
    from nebius_cxcli.observability_routing import endpoint_defaults

    url = endpoint_defaults(payload())["metrics"]["read"]
    argument = ("custom-metrics", "prometheus", url)
    source = grafana_install.configure(
        payload(),
        "cluster",
        datasources=[argument, ("custom-logs", "loki", "https://logs.example.invalid")],
        overrides={"default_datasource": "custom-metrics", "pushgateway": True},
    )
    saved = target_settings(source, "cluster")
    if auth == "secret":
        saved["datasources"][0]["auth_secret"] = {"name": "metrics-auth", "key": "token"}
    else:
        saved["datasources"][0]["auth"] = auth
    save_settings(source, "cluster", saved)
    before = copy.deepcopy(source)
    for _ in range(2):
        source = grafana_install.configure(source, "cluster", datasources=[argument])
        assert source == before
        assert connections(source, "cluster") == connections(before, "cluster")


@pytest.mark.parametrize("field", ["url", "type"])
def test_authenticated_datasource_flag_change_requires_config_review(field):
    source = grafana_install.configure(
        payload(),
        "cluster",
        datasources=[("private", "prometheus", "https://metrics.example.invalid")],
    )
    saved = target_settings(source, "cluster")
    saved["datasources"][0]["auth_secret"] = {"name": "metrics-auth", "key": "token"}
    save_settings(source, "cluster", saved)
    before = copy.deepcopy(source)
    changed = (
        "private",
        "loki" if field == "type" else "prometheus",
        "https://changed.example.invalid" if field == "url" else "https://metrics.example.invalid",
    )
    with pytest.raises(ValueError, match="authentication"):
        grafana_install.configure(source, "cluster", datasources=[changed])
    assert source == before


def test_required_cli_flags_and_repeatable_datasource_tuples(tmp_path, monkeypatch):
    runner = CliRunner()
    assert runner.invoke(cli.app, ["grafana", "install"]).exit_code == 2
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    captured = {}
    monkeypatch.setattr(grafana_install, "install", lambda *args, **kwargs: captured.update(kwargs))
    result = runner.invoke(
        cli.app,
        [
            "grafana",
            "install",
            "--config",
            str(path),
            "--target",
            "cluster",
            "--datasource",
            "production-logs",
            "loki",
            "http://loki.example.com:3100",
            "--datasource",
            "other",
            "prometheus",
            "https://prom.example.com",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["datasources"] == [
        ("production-logs", "loki", "http://loki.example.com:3100"),
        ("other", "prometheus", "https://prom.example.com"),
    ]
    assert captured["overrides"]["pushgateway"] is None


def test_cancel_leaves_payload_unchanged(monkeypatch):
    data = payload()
    before = copy.deepcopy(data)
    monkeypatch.setattr(
        grafana_install.questionary,
        "select",
        lambda *a, **kw: type("Question", (), {"ask": lambda self: None})(),
    )
    import click

    with pytest.raises(click.Abort):
        grafana_install.configure(data, "cluster", interactive=True)
    assert data == before


def test_custom_read_cannot_inherit_nebius_auth():
    data = grafana_install.configure(payload(), "cluster")
    data["deploy"]["targets"][0]["observability"]["routing"]["datasources"] = [
        {
            "name": "metrics-remote",
            "type": "prometheus",
            "url": "https://customer.example",
            "auth": "nebius",
        }
    ]
    with pytest.raises(ValueError, match="custom read endpoint"):
        grafana_install.configure(data, "cluster")


def test_custom_grpc_path_is_never_silently_discarded():
    with pytest.raises(ValueError, match="must not contain a path"):
        grafana_install.configure(
            payload(), "cluster", overrides={"traces_remote": "https://traces.example/invalid"}
        )


def test_local_store_is_retained_when_export_becomes_remote():
    data = grafana_install.configure(payload(), "cluster")
    remote = grafana_install.configure(data, "cluster", overrides={"metrics_storage": "remote"})
    materialize_observability_app_values(remote)
    assert (
        app_row(remote, "cluster", "victoria-metrics-k8s-stack")["values"]["vmsingle"]["enabled"]
        is True
    )
    assert len(metrics_destinations(remote, "cluster")) == 1
    assert "nebius.cloud" in metrics_destinations(remote, "cluster")[0]["url"]


@pytest.mark.parametrize(
    "url",
    ["http://user:password@example.com", "https://example.com?token=secret", "file:///tmp/path"],
)
def test_credentials_and_non_http_urls_rejected(url):
    with pytest.raises(ValueError, match="without credentials"):
        resolve_settings(payload(), "cluster", overrides={"metrics_remote": url})


def test_local_to_remote_retains_vmagent_and_updates_implicit_default():
    data = grafana_install.configure(payload(), "cluster")
    remote = grafana_install.configure(data, "cluster", overrides={"metrics_storage": "remote"})
    assert collector_owners(remote, "cluster")["metrics"] == "victoria-metrics-k8s-stack"
    assert target_settings(remote, "cluster")["default_datasource"] == "metrics-remote"
    assert len(metrics_destinations(remote, "cluster")) == 1
    assert "write.monitoring" in metrics_destinations(remote, "cluster")[0]["url"]


def test_saved_dormant_nebius_auth_cannot_leak_to_custom_write_url():
    data = grafana_install.configure(payload(), "cluster")
    changed = grafana_install.configure(
        data,
        "cluster",
        overrides={
            "metrics_storage": "both",
            "metrics_remote": "https://metrics.example.com/api/v1/write",
        },
        datasources=[("metrics-remote", "prometheus", "https://metrics.example.com")],
    )
    destinations = metrics_destinations(changed, "cluster")
    assert len(destinations) == 2
    assert all("bearerTokenSecret" not in item for item in destinations)


def test_materialization_does_not_duplicate_pushgateway_scrape():
    data = grafana_install.configure(
        payload(),
        "cluster",
        overrides={**{f"{s}_storage": "remote" for s in SIGNALS}, "pushgateway": True},
    )
    materialize_observability_app_values(data)
    before = copy.deepcopy(data)
    materialize_observability_app_values(data)
    assert data == before


def test_standalone_logs_app_gets_only_logs_pipeline_without_grafana():
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.observability import _new_observability_app_row, ensure_observability_app_rows

    data = payload()
    row = _new_observability_app_row(
        next(e for e in component_entries("apps") if e.id == "victoria-logs-single")
    )
    row.update(instance_id="cluster", target_ref="cluster")
    data["apps"]["charts"].append(row)
    ensure_observability_app_rows(data)
    assert target_settings(data, "cluster")["signals"] == ["logs"]
    ids = {row["id"] for row in data["apps"]["charts"]}
    assert ids == {"victoria-logs-single", "opentelemetry-collector", "opentelemetry-logs"}
    materialize_observability_app_values(data)
