from __future__ import annotations

import copy
from collections import deque
from types import SimpleNamespace

import click
import pytest

from nebius_cxcli import grafana_install, grafana_install_wizard
from nebius_cxcli.observability import materialize_observability_app_values
from nebius_cxcli.observability_routing import (
    SIGNALS,
    connections,
    endpoint_defaults,
    save_settings,
    target_settings,
)
from test_observability_routing import payload

LOCAL = {f"{signal}_storage": "local" for signal in SIGNALS}
DEFAULT = object()


@pytest.fixture
def dialogue(monkeypatch):
    pending = deque()
    calls = []

    def respond(method, message, **kwargs):
        assert pending, f"Unexpected {method} prompt: {message}"
        expected_method, prefix, answer = pending.popleft()
        assert (method, message.startswith(prefix)) == (expected_method, True), message
        calls.append((method, message, kwargs))
        if answer is DEFAULT:
            answer = kwargs.get("default")
        if method == "select" and answer is not None:
            assert answer in [choice.value for choice in kwargs["choices"]], message
        return SimpleNamespace(ask=lambda: answer)

    for method in ("select", "text", "confirm"):
        monkeypatch.setattr(
            grafana_install_wizard.questionary,
            method,
            lambda message, _method=method, **kwargs: respond(_method, message, **kwargs),
        )

    def script(*steps):
        pending.extend(steps)
        return calls

    yield script
    assert not pending, f"Unused prompt answers: {pending}"


def test_local_defaults_preview_matches_render_and_headless(dialogue, capsys):
    calls = dialogue(
        *(("select", f"{signal.capitalize()} storage for", DEFAULT) for signal in SIGNALS),
        ("confirm", "Customize datasource", False),
        ("confirm", "Enable Pushgateway", False),
    )
    source = payload()
    before = copy.deepcopy(source)
    result = grafana_install.configure(source, "cluster", interactive=True)
    assert source == before
    assert result == grafana_install.configure(source, "cluster")
    assert not any(method == "text" for method, _, _ in calls)
    labels = [choice.title for choice in calls[0][2]["choices"]]
    assert all(
        any(phrase in label for label in labels)
        for phrase in ("this cluster", "external storage", "remotely")
    )
    output = capsys.readouterr().out
    assert "automatic; default" in output
    assert "No manual datasource setup" in output
    materialize_observability_app_values(result)
    grafana = next(row for row in result["apps"]["charts"] if row["id"] == "grafana")
    sources = grafana["values"]["datasources"]["datasources.yaml"]["datasources"]
    assert len(sources) == 3
    assert next(item["name"] for item in sources if item["isDefault"]) == "metrics-local"
    for item in sources:
        assert item["name"] in output
        assert item["type"] in output
        assert item["url"] in output


@pytest.mark.parametrize("mode", ["remote", "both"])
def test_nebius_destinations_need_no_endpoint_or_protocol_fields(mode, dialogue):
    calls = dialogue(
        *(("select", f"Where should remote {signal}", "nebius") for signal in SIGNALS),
        ("confirm", "Customize datasource", False),
    )
    result = grafana_install.configure(
        payload(),
        "cluster",
        interactive=True,
        overrides={**{f"{signal}_storage": mode for signal in SIGNALS}, "pushgateway": False},
    )
    settings = target_settings(result, "cluster")
    assert settings["local_stores"] == ([] if mode == "remote" else sorted(SIGNALS))
    assert settings["default_datasource"] == (
        "metrics-remote" if mode == "remote" else "metrics-local"
    )
    assert len(connections(result, "cluster")) == (3 if mode == "remote" else 6)
    assert not any(method == "text" for method, _, _ in calls)
    for signal in ("logs", "traces"):
        assert settings[signal]["remote"]["protocol"] == "otlp-grpc"


def test_custom_metrics_guides_read_and_retries_bad_url(dialogue):
    dialogue(
        ("select", "Where should remote metrics", "custom"),
        ("text", "Metrics WRITE URL", "https://metrics.example.com/api/v1/write"),
        ("text", "metrics-remote READ URL", "not-a-url"),
        ("text", "metrics-remote READ URL", "https://queries.example.com/"),
        ("confirm", "Customize datasource", False),
    )
    result = grafana_install.configure(
        payload(),
        "cluster",
        interactive=True,
        overrides={**LOCAL, "metrics_storage": "remote", "pushgateway": False},
    )
    settings = target_settings(result, "cluster")
    assert settings["datasources"] == [
        {"name": "metrics-remote", "type": "prometheus", "url": "https://queries.example.com"}
    ]
    assert settings["metrics"]["remote"]["auth"] == "none"
    assert settings["local_stores"] == ["logs", "traces"]


@pytest.mark.parametrize(
    ("signal", "backend", "protocol", "write"),
    [
        ("logs", "loki", "otlp-http", "https://ingest.example.com/v1/logs"),
        ("traces", "jaeger", "otlp-grpc", "https://ingest.example.com:4317"),
    ],
)
def test_custom_logs_and_traces_select_read_backend_separately(
    signal, backend, protocol, write, dialogue
):
    calls = dialogue(
        ("select", f"Where should remote {signal}", "custom"),
        ("select", f"{signal.capitalize()} write protocol", protocol),
        ("text", f"{signal.capitalize()} WRITE URL", write),
        ("select", "Datasource backend", backend),
        ("text", f"{signal}-remote READ URL", "https://queries.example.com"),
        ("confirm", "Customize datasource", False),
    )
    result = grafana_install.configure(
        payload(),
        "cluster",
        interactive=True,
        overrides={**LOCAL, f"{signal}_storage": "both", "pushgateway": False},
    )
    settings = target_settings(result, "cluster")
    assert settings[signal]["remote"]["url"] == write
    assert settings[signal]["remote"]["protocol"] == protocol
    assert settings["datasources"][0]["type"] == backend
    type_choices = next(
        kwargs["choices"] for _, message, kwargs in calls if message == "Datasource backend"
    )
    assert {choice.value for choice in type_choices} == (
        {"loki", "victoriametrics-logs-datasource"} if signal == "logs" else {"tempo", "jaeger"}
    )


def test_add_edit_default_preserve_uid_and_validate_names(dialogue):
    dialogue(
        ("confirm", "Customize datasource", True),
        ("select", "Datasource customization", "add"),
        ("select", "Datasource backend", "prometheus"),
        ("text", "Datasource name", "metrics-local"),
        ("text", "Datasource name", DEFAULT),
        ("text", "metrics-extra READ URL", "https://first.example.com"),
        ("select", "Datasource customization", "edit"),
        ("select", "Edit datasource", "metrics-extra"),
        ("select", "Datasource backend", "prometheus"),
        ("text", "metrics-extra READ URL", "https://second.example.com"),
        ("select", "Datasource customization", "default"),
        ("select", "Default Grafana datasource", "metrics-extra"),
        ("select", "Datasource customization", "done"),
    )
    result = grafana_install.configure(
        payload(), "cluster", interactive=True, overrides={**LOCAL, "pushgateway": False}
    )
    first = grafana_install.configure(
        payload(),
        "cluster",
        datasources=[("metrics-extra", "prometheus", "https://first.example.com")],
    )
    original_uid = next(
        item["uid"] for item in connections(first, "cluster") if item["name"] == "metrics-extra"
    )
    extra = next(item for item in connections(result, "cluster") if item["name"] == "metrics-extra")
    assert extra["uid"] == original_uid
    assert extra["url"] == "https://second.example.com"
    assert extra["isDefault"] is True


@pytest.mark.parametrize("action", ["keep", "edit", "automatic"])
def test_changed_write_requires_review_of_saved_read(action, dialogue):
    source = grafana_install.configure(
        payload(),
        "cluster",
        overrides={
            "metrics_storage": "remote",
            "metrics_remote": "https://first.example.com/write",
        },
        datasources=[("metrics-remote", "prometheus", "https://first.example.com/query")],
    )
    write = (
        endpoint_defaults(source)["metrics"]["write"]
        if action == "automatic"
        else "https://second.example.com/write"
    )
    steps = [("select", "Grafana read connection for remote metrics", action)]
    if action == "edit":
        steps.append(("text", "metrics-remote READ URL", "https://second.example.com/query"))
    dialogue(*steps, ("confirm", "Customize datasource", False))
    result = grafana_install.configure(
        source,
        "cluster",
        interactive=True,
        overrides={
            **LOCAL,
            "metrics_storage": "remote",
            "metrics_remote": write,
            "pushgateway": False,
        },
    )
    actual = next(
        item for item in connections(result, "cluster") if item["name"] == "metrics-remote"
    )
    expected = {
        "keep": "https://first.example.com/query",
        "edit": "https://second.example.com/query",
        "automatic": endpoint_defaults(source)["metrics"]["read"],
    }[action]
    assert actual["url"] == expected


@pytest.mark.parametrize("replacement", ["", "logs-local"])
def test_unavailable_saved_default_requires_choice(replacement, dialogue):
    source = grafana_install.configure(
        payload(), "cluster", overrides={"default_datasource": "metrics-local"}
    )
    dialogue(
        ("select", "Where should remote metrics", "nebius"),
        ("select", "Default Grafana datasource", replacement),
        ("confirm", "Customize datasource", False),
    )
    result = grafana_install.configure(
        source,
        "cluster",
        interactive=True,
        overrides={**LOCAL, "metrics_storage": "remote", "pushgateway": False},
    )
    settings = target_settings(result, "cluster")
    assert settings["default_datasource"] == (replacement or "metrics-remote")
    assert settings["default_datasource_explicit"] is bool(replacement)
    assert target_settings(source, "cluster")["default_datasource"] == "metrics-local"


def test_explicit_invalid_default_fails_without_repair_prompt(dialogue):
    with pytest.raises(ValueError, match="Default datasource 'missing'"):
        grafana_install.configure(
            payload(),
            "cluster",
            interactive=True,
            overrides={**LOCAL, "default_datasource": "missing", "pushgateway": False},
        )


def test_flags_lock_connection_and_default_choices(dialogue):
    calls = dialogue(
        ("confirm", "Customize datasource", True),
        ("select", "Datasource customization", "edit"),
        ("select", "Edit datasource", "logs-local"),
        ("select", "Datasource backend", "victoriametrics-logs-datasource"),
        ("text", "logs-local READ URL", DEFAULT),
        ("select", "Datasource customization", "done"),
    )
    result = grafana_install.configure(
        payload(),
        "cluster",
        interactive=True,
        overrides={
            **LOCAL,
            "metrics_storage": "remote",
            "metrics_remote": "https://write.example.com",
            "default_datasource": "metrics-remote",
            "pushgateway": False,
        },
        datasources=[("metrics-remote", "prometheus", "https://read.example.com")],
    )
    assert target_settings(result, "cluster")["default_datasource"] == "metrics-remote"
    for _, message, kwargs in calls:
        values = [choice.value for choice in kwargs.get("choices", [])]
        if message == "Datasource customization":
            assert "default" not in values
        elif message == "Edit datasource":
            assert "metrics-remote" not in values


def authenticated_payload():
    source = grafana_install.configure(
        payload(),
        "cluster",
        overrides={"default_datasource": "private-metrics"},
        datasources=[("private-metrics", "prometheus", "https://metrics.example.com")],
    )
    settings = target_settings(source, "cluster")
    settings["datasources"][0]["auth_secret"] = {
        "name": "metrics-auth",
        "key": "token",
    }
    save_settings(source, "cluster", settings)
    return source


def test_rerun_and_blocked_edit_preserve_auth_default_and_uid(dialogue, capsys):
    source = authenticated_payload()
    before = copy.deepcopy(source)
    dialogue(
        ("confirm", "Customize datasource", True),
        ("select", "Datasource customization", "edit"),
        ("select", "Edit datasource", "private-metrics"),
        ("select", "Datasource customization", "done"),
    )
    result = grafana_install.configure(
        source, "cluster", interactive=True, overrides={**LOCAL, "pushgateway": False}
    )
    assert source == before
    assert connections(result, "cluster") == connections(before, "cluster")
    assert (
        target_settings(result, "cluster")["datasources"]
        == target_settings(before, "cluster")["datasources"]
    )
    output = capsys.readouterr().out
    assert "Edit its URL/type and authentication together" in output
    assert "metrics-auth" not in output


@pytest.mark.parametrize("explicit", [True, False])
def test_write_secret_is_not_dropped_by_destination_change(explicit, dialogue):
    source = grafana_install.configure(
        payload(),
        "cluster",
        overrides={"metrics_storage": "remote", "metrics_remote": "https://old.example.com/write"},
        datasources=[("metrics-remote", "prometheus", "https://old.example.com/query")],
    )
    settings = target_settings(source, "cluster")
    settings["metrics"]["remote"]["auth_secret"] = {
        "name": "write-auth",
        "key": "token",
    }
    save_settings(source, "cluster", settings)
    before = copy.deepcopy(source)
    flags = {**LOCAL, "metrics_storage": "remote", "pushgateway": False}
    if explicit:
        flags["metrics_remote"] = "https://new.example.com/write"
    else:
        dialogue(
            ("select", "Where should remote metrics", "custom"),
            ("text", "Metrics WRITE URL", "https://new.example.com/write"),
        )
    with pytest.raises(ValueError, match="will not drop or transfer"):
        grafana_install.configure(source, "cluster", interactive=True, overrides=flags)
    assert source == before


@pytest.mark.parametrize(
    "stage", ["storage", "customize", "backend", "name", "url", "default", "pushgateway"]
)
def test_cancellation_is_atomic_at_each_prompt_family(stage, dialogue):
    source = payload()
    before = copy.deepcopy(source)
    flags = {**LOCAL, "pushgateway": False}
    steps = []
    if stage == "storage":
        flags.pop("metrics_storage")
        steps = [("select", "Metrics storage for", None)]
    elif stage == "customize":
        steps = [("confirm", "Customize datasource", None)]
    elif stage == "pushgateway":
        flags.pop("pushgateway")
        steps = [
            ("confirm", "Customize datasource", False),
            ("confirm", "Enable Pushgateway", None),
        ]
    else:
        steps = [("confirm", "Customize datasource", True)]
        if stage == "default":
            steps += [
                ("select", "Datasource customization", "default"),
                ("select", "Default Grafana datasource", None),
            ]
        else:
            steps += [
                ("select", "Datasource customization", "add"),
                ("select", "Datasource backend", None if stage == "backend" else "prometheus"),
            ]
            if stage in {"name", "url"}:
                steps.append(("text", "Datasource name", None if stage == "name" else "extra"))
            if stage == "url":
                steps.append(("text", "extra READ URL", None))
    dialogue(*steps)
    with pytest.raises(click.Abort):
        grafana_install.configure(source, "cluster", interactive=True, overrides=flags)
    assert source == before


def test_install_cancellation_preserves_config_and_never_deploys(tmp_path, monkeypatch, dialogue):
    from nebius_cxcli import config_loader, config_model, observability_installation

    source = payload()
    config = tmp_path / "config.yaml"
    original = b"unchanged source configuration\n"
    config.write_bytes(original)
    monkeypatch.setattr(config_loader, "load_config", lambda _path, **_kwargs: source)
    monkeypatch.setattr(config_model, "to_dynamic_payload", lambda data: data)
    deployed = []
    monkeypatch.setattr(
        observability_installation, "install_observability", lambda *a, **k: deployed.append(a)
    )
    dialogue(("confirm", "Customize datasource", False), ("confirm", "Save these settings", False))
    with pytest.raises(click.Abort):
        grafana_install.install(
            config,
            "cluster",
            interactive=True,
            overrides={**LOCAL, "pushgateway": False},
            datasources=(),
            emit=lambda _: None,
        )
    assert config.read_bytes() == original
    assert not deployed


@pytest.mark.parametrize("action", ["edit", "automatic"])
def test_remote_review_cannot_replace_authenticated_read(action, dialogue):
    source = grafana_install.configure(
        payload(),
        "cluster",
        overrides={"metrics_storage": "remote", "metrics_remote": "https://old.example.com/write"},
        datasources=[("metrics-remote", "prometheus", "https://old.example.com/query")],
    )
    settings = target_settings(source, "cluster")
    settings["datasources"][0]["auth_secret"] = {"name": "read-auth", "key": "token"}
    save_settings(source, "cluster", settings)
    before = copy.deepcopy(source)
    dialogue(("select", "Grafana read connection for remote metrics", action))
    with pytest.raises(ValueError, match="uses configured authentication"):
        grafana_install.configure(
            source,
            "cluster",
            interactive=True,
            overrides={
                **LOCAL,
                "metrics_storage": "remote",
                "metrics_remote": endpoint_defaults(source)["metrics"]["write"],
            },
        )
    assert source == before


@pytest.mark.parametrize("default", ["local", ""])
def test_real_selector_keeps_canonical_values_and_empty_automatic_choice(default, monkeypatch):
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    real_select = grafana_install_wizard.questionary.select
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            grafana_install_wizard.questionary,
            "select",
            lambda message, **kwargs: real_select(
                message, **kwargs, input=pipe, output=DummyOutput()
            ),
        )
        pipe.send_text("\r")
        assert (
            grafana_install_wizard._select(
                "Select",
                {"local": "Local — store in this cluster", "": "Automatic metrics default"},
                default,
            )
            == default
        )


def test_soperator_interactive_preview_uses_frozen_native_query_url(
    tmp_path, monkeypatch, dialogue, capsys
):
    from nebius_cxcli import soperator_release_resolver
    from test_soperator_configuration_render import frozen_charts_for

    snapshot, receipt, _source = frozen_charts_for(tmp_path, "4.1.8")
    frozen = SimpleNamespace(
        snapshot=snapshot,
        source=receipt,
        source_context=SimpleNamespace(
            source=receipt, umbrella=snapshot.umbrella, charts=snapshot.charts
        ),
    )
    monkeypatch.setattr(
        soperator_release_resolver,
        "current_frozen_soperator_release",
        lambda release, **kwargs: frozen if release == "4.1.8" else None,
    )
    monkeypatch.setattr(
        soperator_release_resolver,
        "freeze_soperator_release",
        lambda *_args, **_kwargs: pytest.fail("The verified frozen source must be reused"),
    )
    source = payload()
    source["apps"]["charts"].append(
        {
            "id": "soperator",
            "instance_id": "cluster",
            "enabled": True,
            "version": "4.1.8",
            "namespace": "flux-system",
            "release-name": "soperator-fluxcd",
            "values": {},
        }
    )
    dialogue(("confirm", "Customize datasource", False))
    result = grafana_install.configure(
        source, "cluster", interactive=True, overrides={**LOCAL, "pushgateway": False}
    )
    settings = target_settings(result, "cluster")
    expected = settings["native_backends"]["vmStack"]["queryUrl"]
    assert expected.endswith(":8429")
    actual = next(
        item for item in connections(result, "cluster") if item["name"] == "metrics-local"
    )
    assert actual["url"] == expected
    assert f"Query URL: {expected}" in capsys.readouterr().out
    assert result == grafana_install.configure(
        source, "cluster", overrides={**LOCAL, "pushgateway": False}
    )


@pytest.mark.parametrize("entrypoint", ["configure", "repeat-install"])
def test_cold_native_preview_does_not_acquire_chart_graph(
    tmp_path, monkeypatch, dialogue, capsys, entrypoint
):
    from nebius_cxcli import soperator_release_resolver as resolver
    from nebius_cxcli.soperator_release_identity import SoperatorReleaseIdentityLedger
    from test_soperator_configuration_render import frozen_charts_for

    snapshot, receipt, _source = frozen_charts_for(tmp_path / "source", "4.1.8")
    metadata = resolver.SoperatorReleaseMetadata(
        selector=snapshot.release,
        release=snapshot.release,
        repository=snapshot.repository,
        tag=snapshot.tag,
        commit=snapshot.commit,
        tree=snapshot.tree,
        archive_url=snapshot.archive_url,
        archive_root=snapshot.archive_root,
        published_at="",
        tree_entries=(),
    )
    acquired = []
    output_at_acquisition = []

    def acquire(*_args, **_kwargs):
        acquired.append(metadata.release)
        output_at_acquisition.append(capsys.readouterr().out)
        return receipt

    monkeypatch.setattr(resolver, "current_frozen_soperator_release", lambda *a, **k: None)
    monkeypatch.setattr(resolver, "resolve_soperator_release", lambda *a, **k: metadata)
    monkeypatch.setattr(resolver, "acquire_soperator_release_source", acquire)
    monkeypatch.setattr(
        resolver, "classify_soperator_release_capabilities", lambda _: ("upstream-flux-v1", "")
    )
    monkeypatch.setattr(
        resolver,
        "SoperatorReleaseIdentityLedger",
        lambda *_: SoperatorReleaseIdentityLedger(tmp_path / "identities"),
    )
    monkeypatch.setattr(
        resolver,
        "freeze_soperator_release",
        lambda *a, **k: pytest.fail("Datasource preview must not acquire the release chart graph"),
    )
    monkeypatch.setattr(
        resolver,
        "_run",
        lambda *a, **k: pytest.fail("Datasource preview must not run Helm"),
    )
    source = payload()
    source["apps"]["charts"].append(
        {
            "id": "soperator",
            "instance_id": "cluster",
            "enabled": True,
            "version": "4.1.8",
            "namespace": "flux-system",
            "release-name": "soperator-fluxcd",
            "values": {},
        }
    )
    if entrypoint == "repeat-install":
        from nebius_cxcli.observability_routing import resolve_settings

        save_settings(source, "cluster", resolve_settings(source, "cluster"))
    before = copy.deepcopy(source)
    dialogue(
        *(("select", f"{signal.capitalize()} storage for", DEFAULT) for signal in SIGNALS),
        ("confirm", "Customize datasource", True),
        ("select", "Datasource customization", "default"),
        ("select", "Default Grafana", "logs-local"),
        ("select", "Datasource customization", "done"),
        ("confirm", "Enable Pushgateway", False),
    )
    if entrypoint == "repeat-install":
        from nebius_cxcli import config_loader, observability_installation
        from nebius_cxcli.observability import ensure_observability_app_rows

        config = tmp_path / "config.yaml"
        original = b"unchanged saved native routing\n"
        config.write_bytes(original)

        def load(_path, *, observability_target_refs):
            loaded = copy.deepcopy(source)
            # The config loader's normalization reconciles existing routing.
            ensure_observability_app_rows(
                loaded, observability_target_refs=observability_target_refs
            )
            return loaded

        monkeypatch.setattr(config_loader, "load_config", load)
        monkeypatch.setattr(
            observability_installation,
            "install_observability",
            lambda *a, **k: pytest.fail("Declining confirmation must not deploy"),
        )
        candidates = []
        original_summary = grafana_install.summary

        def summary(candidate, target):
            candidates.append(candidate)
            return original_summary(candidate, target)

        monkeypatch.setattr(grafana_install, "summary", summary)
        dialogue(("confirm", "Save these settings", False))
        with pytest.raises(click.Abort):
            grafana_install.install(
                config, "cluster", interactive=True, overrides={}, datasources=(), emit=click.echo
            )
        assert config.read_bytes() == original
        result = candidates[0]
    else:
        result = grafana_install.configure(source, "cluster", interactive=True)
    assert acquired == ["4.1.8"]
    assert output_at_acquisition[0].count("Target cluster is configured with Soperator 4.1.8") == 1
    assert "Its metrics and logs collectors will be reused" in output_at_acquisition[0]
    assert "Verifying Soperator 4.1.8 source for datasource connections" in output_at_acquisition[0]
    assert "Grafana will use these datasources" in capsys.readouterr().out
    assert target_settings(result, "cluster")["native_backends"]["vmStack"]["queryUrl"].endswith(
        ":8429"
    )
    assert source == before
    # A new operation revalidates; no process-wide stale source cache is introduced.
    grafana_install.configure(source, "cluster", overrides={**LOCAL, "pushgateway": False})
    assert acquired == ["4.1.8", "4.1.8"]
