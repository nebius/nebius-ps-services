from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import URLError

import pytest
import yaml
from typer.testing import CliRunner

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli import cli, grafana_cli
from nebius_cxcli.grafana_api import (
    GrafanaAuth,
    GrafanaClient,
    GrafanaError,
    NoRedirects,
    normalize_url,
    token_auth,
)
from nebius_cxcli.grafana_dashboards import (
    DatasourceMappingRequired,
    json_bytes,
    load_dashboards,
    map_datasources,
    normalize_dashboard,
    validate_import_declarations,
)
from nebius_cxcli.grafana_import import execute_imports, prepare_imports, require_complete
from nebius_cxcli.grafana_project import exclude_linked_catalog_dashboards, publish_import


@pytest.fixture
def fake(monkeypatch):
    client = FakeGrafana()
    monkeypatch.setattr(grafana_cli, "GrafanaClient", lambda *_a, **_kw: client)
    monkeypatch.setenv("TEST_GRAFANA_TOKEN", "fixture-token")
    return client


def write_dashboard(path, uid="board", **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(dashboard(uid, **fields)))
    return path


def test_migrated_classic_import_is_noop_on_repeat():
    client = FakeGrafana()
    first = prepare_imports(client, [dashboard()])
    require_complete(execute_imports(client, first, emit=lambda _: None))
    assert client.resources["board"]["spec"]["schemaVersion"] == 42
    second = prepare_imports(client, [dashboard()])
    assert execute_imports(client, second, emit=lambda _: None) == {"board": "unchanged"}
    assert client.writes == 1
    assert not any(uid.startswith("cxcli-preview-") for uid in client.resources)


def test_whole_batch_preflight_refuses_different_existing_dashboard():
    client = FakeGrafana()
    execute_imports(client, prepare_imports(client, [dashboard("z")]), emit=lambda _: None)
    with pytest.raises(GrafanaError, match="--overwrite"):
        prepare_imports(client, [dashboard("a"), dashboard("z", title="Changed")])
    assert set(client.resources) == {"z"}


@pytest.mark.parametrize("annotation", ["grafana.app/managedBy", "grafana.app/managerId"])
def test_provisioned_dashboard_cannot_be_overwritten(annotation):
    client = FakeGrafana()
    execute_imports(client, prepare_imports(client, [dashboard()]), emit=lambda _: None)
    client.resources["board"]["metadata"]["annotations"][annotation] = "file-provider"
    client.calls.clear()
    with pytest.raises(GrafanaError, match="provision"):
        prepare_imports(client, [dashboard(title="Changed")], overwrite=True)
    assert client.writes == 1
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_managed_external_import_rejects_before_datasource_prompt(tmp_path, monkeypatch, fake):
    execute_imports(fake, prepare_imports(fake, [dashboard()]), emit=lambda _: None)
    fake.resources["board"]["metadata"]["annotations"]["grafana.app/managedBy"] = "file"
    fake.calls.clear()
    source = write_dashboard(
        tmp_path / "input.json", panels=[{"datasource": {"type": "prometheus", "uid": "missing"}}]
    )
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)
    monkeypatch.setattr(
        grafana_cli, "_select_datasource", lambda _: pytest.fail("Prompted for managed dashboard")
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(source),
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "provision" in result.output and "new UID" in result.output
    assert not any(method == "POST" or path == "api/datasources" for method, path, _ in fake.calls)


def test_concurrent_change_does_not_refresh_version_and_overwrite():
    client = FakeGrafana()
    execute_imports(client, prepare_imports(client, [dashboard()]), emit=lambda _: None)
    plans = prepare_imports(client, [dashboard(title="Changed")], overwrite=True)
    client.resources["board"]["metadata"]["resourceVersion"] = "concurrent"
    client.resources["board"]["spec"]["title"] = "Another editor"
    assert execute_imports(client, plans, emit=lambda _: None) == {"board": "failed"}
    assert client.resources["board"]["spec"]["title"] == "Another editor"
    assert client.writes == 1


def test_partial_failure_and_lost_response_resume_without_duplicates():
    client = FakeGrafana()
    plans = prepare_imports(client, [dashboard("a"), dashboard("b"), dashboard("c")])
    client.fail_uid = "b"
    client.lose_response = True
    assert execute_imports(client, plans, emit=lambda _: None) == {
        "a": "installed",
        "b": "failed",
        "c": "pending",
    }
    client.fail_uid = None
    require_complete(
        execute_imports(
            client,
            prepare_imports(client, [dashboard("a"), dashboard("b"), dashboard("c")]),
            emit=lambda _: None,
        )
    )
    assert client.writes == 3


@pytest.mark.parametrize(
    "bad",
    [
        "http://external.example.com",
        "https://user:secret@example.com",
        "https://example.com/?token=secret",
        "https://example.com/#secret",
        "https://example.com/../x",
        "https://example.com/\\x",
    ],
)
def test_url_rejects_unsafe_credentials_or_paths(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)


def test_url_preserves_reverse_proxy_prefix():
    assert normalize_url("https://example.com/monitoring") == "https://example.com/monitoring/"
    assert normalize_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000/"


def test_auth_reads_only_selected_env_and_does_not_disclose(monkeypatch):
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "must-not-use")
    monkeypatch.setenv("GRAFANA_TOKEN", "must-not-use-either")
    monkeypatch.delenv("SELECTED", raising=False)
    with pytest.raises(ValueError, match="empty"):
        token_auth("SELECTED")
    monkeypatch.setenv("SELECTED", "selected-fixture")
    auth = token_auth("SELECTED")
    assert auth.header == "Bearer selected-fixture"
    assert "selected-fixture" not in repr(auth)


def test_transport_has_no_redirect_fallback_or_raw_error_chain():
    assert (
        NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid") is None
    )
    client = GrafanaClient(
        "https://example.invalid/prefix/", GrafanaAuth("Bearer sensitive-fixture")
    )

    def failure(*args, **kwargs):
        raise URLError("sensitive-fixture raw transport error")

    client.opener = SimpleNamespace(open=failure)
    with pytest.raises(GrafanaError) as error:
        client.request("GET", "api/frontend/settings/")
    assert "sensitive-fixture" not in str(error.value)
    assert error.value.__suppress_context__


def test_exact_resource_version_and_strict_writes():
    client = FakeGrafana()
    execute_imports(client, prepare_imports(client, [dashboard()]), emit=lambda _: None)
    client.resources["board"]["metadata"].pop("resourceVersion")
    with pytest.raises(GrafanaError, match="resourceVersion"):
        client.write(dashboard(), "", client.get("board"))


def test_json_envelopes_identity_and_input_validation(tmp_path):
    raw = normalize_dashboard({"dashboard": dashboard(id=5, version=8)})
    assert "id" not in raw and "version" not in raw
    assert (
        normalize_dashboard(
            {
                "apiVersion": "dashboard.grafana.app/v1",
                "metadata": {"name": "board", "uid": "storage-id"},
                "spec": dashboard(),
            }
        )["uid"]
        == "board"
    )
    with pytest.raises(ValueError, match="differs"):
        normalize_dashboard(
            {
                "apiVersion": "dashboard.grafana.app/v1",
                "metadata": {"name": "different"},
                "spec": dashboard(),
            }
        )
    with pytest.raises(ValueError, match="Classic"):
        normalize_dashboard({"apiVersion": "dashboard.grafana.app/v2", "spec": {}})
    write_dashboard(tmp_path / "a.json")
    write_dashboard(tmp_path / "nested" / "b.json", "second")
    assert len(load_dashboards([tmp_path])) == 1
    assert len(load_dashboards([tmp_path], recursive=True)) == 2
    with pytest.raises(ValueError, match="Duplicate"):
        load_dashboards([tmp_path, tmp_path / "a.json"])
    (tmp_path / "link.json").symlink_to(tmp_path / "a.json")
    with pytest.raises(ValueError, match="symlink"):
        load_dashboards([tmp_path])


def test_multi_datasource_mapping_preserves_types_and_variables():
    raw = dashboard(
        panels=[
            {"datasource": {"uid": "${DS_METRICS}", "type": "prometheus"}},
            {"datasource": {"uid": "logs", "type": "loki"}},
            {"datasource": "$dynamic"},
        ],
        __inputs=[{"name": "DS_METRICS", "type": "datasource", "pluginId": "prometheus"}],
        templating={"list": [{"name": "dynamic", "type": "datasource", "query": "prometheus"}]},
    )
    mapped = map_datasources(raw, {"DS_METRICS": "metrics"}, FakeGrafana().inventory)
    assert mapped["panels"][0]["datasource"]["uid"] == "metrics"
    assert mapped["panels"][1]["datasource"]["uid"] == "logs"
    assert mapped["panels"][2]["datasource"] == "$dynamic"
    assert "__inputs" not in mapped
    with pytest.raises(ValueError, match="type mismatch"):
        map_datasources(raw, {"DS_METRICS": "logs"}, FakeGrafana().inventory)


def datasource_import(path, *flags):
    return CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            *flags,
        ],
    )


def stub_datasource_picker(monkeypatch, choose):
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    calls = []
    real_select = grafana_cli.questionary.select
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)

    def select(message, **kwargs):
        calls.append((message, kwargs))
        question = real_select(message, **kwargs, input=DummyInput(), output=DummyOutput())
        question.unsafe_ask = lambda: choose(kwargs)
        return question

    monkeypatch.setattr(grafana_cli.questionary, "select", select)
    return calls


@pytest.mark.parametrize("kind", ["prometheus", "", "$type"])
def test_datasource_candidates_share_validation_rules(kind):
    raw = dashboard(panels=[{"datasource": {"uid": "unresolved", "type": kind}}])
    inventory = FakeGrafana().inventory
    inventory[0]["url"] = "https://not-for-display.invalid"
    with pytest.raises(DatasourceMappingRequired) as caught:
        map_datasources(raw, {}, inventory)
    needed = caught.value
    assert needed.expected_type == (kind if kind == "prometheus" else "")
    assert {item["uid"] for item in needed.candidates} == (
        {"metrics"} if kind == "prometheus" else {"metrics", "logs"}
    )
    assert all(set(item) == {"uid", "name", "type"} for item in needed.candidates)
    for candidate in needed.candidates:
        mapped = map_datasources(raw, {"unresolved": candidate["uid"]}, inventory)
        assert mapped["panels"][0]["datasource"]["uid"] == candidate["uid"]


def test_import_picker_confirms_single_candidate_once_across_batch(tmp_path, monkeypatch, fake):
    fields = {
        "panels": [{"datasource": "${DS_METRICS}", "targets": [{"expr": "up"}]}],
        "__inputs": [{"name": "DS_METRICS", "type": "datasource", "pluginId": "prometheus"}],
    }
    write_dashboard(tmp_path / "a.json", "a", **fields)
    write_dashboard(tmp_path / "b.json", "b", **fields)
    calls = stub_datasource_picker(monkeypatch, lambda _: "metrics")
    result = datasource_import(tmp_path)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1  # A sole candidate still requires the chooser's answer.
    message, options = calls[0]
    assert "DS_METRICS" in message
    assert options["default"] == "metrics"
    assert [(c.title, c.value) for c in options["choices"]] == [
        ("Metrics · prometheus · UID: metrics", "metrics")
    ]
    assert "Required type: prometheus" in result.output
    assert "Datasource mapping: DS_METRICS → metrics" in result.output
    assert sum(path == "api/datasources" for _, path, _ in fake.calls) == 2
    for resource in fake.resources.values():
        assert resource["spec"]["panels"] == [
            {"datasource": "metrics", "targets": [{"expr": "up"}]}
        ]


def test_import_progress_precedes_network_work_and_pauses_for_picker(tmp_path, monkeypatch, fake):
    from io import StringIO

    from rich.console import Console

    from nebius_cxcli.grafana_progress import GrafanaProgress

    progress = GrafanaProgress(
        Console(file=StringIO(), force_terminal=True, _environ={"TERM": "xterm"}, width=100)
    )
    monkeypatch.setattr(grafana_cli, "GrafanaProgress", lambda: progress)
    path = write_dashboard(tmp_path / "a.json", panels=[{"datasource": "missing"}])
    connect = fake.connect

    def connecting():
        assert progress._description == "Checking Grafana authentication and API"
        assert progress._display.live.is_started
        connect()

    monkeypatch.setattr(fake, "connect", connecting)

    def choose(_):
        assert not progress._display.live.is_started
        assert progress._description == "Waiting for datasource selection"
        return "metrics"

    stub_datasource_picker(monkeypatch, choose)
    result = datasource_import(path)
    assert result.exit_code == 0, result.output
    assert "Verified: 1" in result.stdout
    assert "Grafana import:" not in result.stdout
    assert not progress._display.live.is_started


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_import_only_reports_verified_after_session_cleanup(
    tmp_path, monkeypatch, fake, cleanup_fails
):
    path = write_dashboard(tmp_path / "a.json")
    events = []
    monkeypatch.setattr(grafana_cli, "emit", events.append)

    @contextmanager
    def destination(**kwargs):
        yield fake, None
        events.append("cleanup")
        if cleanup_fails:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(grafana_cli, "destination", destination)
    result = datasource_import(path)
    assert result.exit_code == (1 if cleanup_fails else 0), result.output
    verified = [i for i, event in enumerate(events) if event.startswith("Verified:")]
    if cleanup_fails:
        assert not verified
        assert "Error: cleanup failed" in events
    else:
        assert len(verified) == 1
        assert events.index("cleanup") < verified[0]


def test_import_picker_sorts_duplicate_names_and_keeps_distinct_sources(
    tmp_path, monkeypatch, fake
):
    fake.inventory += [
        {"uid": "z", "name": "Metrics", "type": "prometheus"},
        {"uid": "a", "name": "alpha", "type": "prometheus"},
    ]
    path = write_dashboard(
        tmp_path / "a.json",
        panels=[
            {"datasource": {"uid": "unknown-metrics", "type": "prometheus"}},
            {"datasource": {"uid": "unknown-logs", "type": "loki"}},
        ],
    )
    answers = iter(["z", "logs"])
    calls = stub_datasource_picker(monkeypatch, lambda _: next(answers))
    result = datasource_import(path)
    assert result.exit_code == 0, result.output
    assert [c.value for c in calls[0][1]["choices"]] == ["a", "metrics", "z"]
    assert [c.value for c in calls[1][1]["choices"]] == ["logs"]
    assert [p["datasource"]["uid"] for p in fake.resources["board"]["spec"]["panels"]] == [
        "z",
        "logs",
    ]


@pytest.mark.parametrize("inventory", [[], [{"uid": "logs", "name": "Logs", "type": "loki"}]])
def test_import_picker_no_compatible_choices_stops_before_writes(
    tmp_path, monkeypatch, fake, inventory
):
    fake.inventory = inventory
    path = write_dashboard(
        tmp_path / "a.json", panels=[{"datasource": {"uid": "missing", "type": "prometheus"}}]
    )
    calls = stub_datasource_picker(monkeypatch, lambda _: pytest.fail("empty picker"))
    result = datasource_import(path)
    assert result.exit_code == 1
    assert "No compatible datasources of type 'prometheus'" in result.output
    assert "missing" in result.output
    assert not calls
    assert fake.writes == 0
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt(), EOFError()])
def test_import_picker_cancellation_preserves_project_and_session_cleanup(
    tmp_path, monkeypatch, fake, answer
):
    path = write_dashboard(tmp_path / "a.json", panels=[{"datasource": "missing"}])
    config = tmp_path / "config.yaml"
    config.write_text("existing: config\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    events = []

    def choose(_):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    stub_datasource_picker(monkeypatch, choose)
    monkeypatch.setattr(
        grafana_cli, "recover_import_publication", lambda *_: events.append("recover")
    )
    monkeypatch.setattr(grafana_cli, "assert_import_ownership", lambda *_, **__: None)
    monkeypatch.setattr(
        grafana_cli, "publish_import", lambda **_: pytest.fail("published cancelled batch")
    )

    @contextmanager
    def session(*args, **kwargs):
        events.append("open")
        try:
            yield SimpleNamespace(client=fake, config={}, target="cluster", identity={})
        finally:
            events.append("close")

    monkeypatch.setattr(grafana_cli, "cluster_session", session)
    result = CliRunner().invoke(
        cli.app, ["grafana", "import", str(path), "--config", str(config), "--target", "cluster"]
    )
    assert result.exit_code == 1, result.output
    assert "Import cancelled." in result.output
    assert events == ["recover", "open", "close"]
    assert fake.writes == 0
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("change", ["remove", "type"])
def test_import_rechecks_selected_datasource_before_writes(tmp_path, monkeypatch, fake, change):
    path = write_dashboard(
        tmp_path / "a.json", panels=[{"datasource": {"uid": "missing", "type": "prometheus"}}]
    )

    def choose(_):
        if change == "remove":
            fake.inventory = []
        else:
            fake.inventory[0]["type"] = "loki"
        return "metrics"

    calls = stub_datasource_picker(monkeypatch, choose)
    result = datasource_import(path)
    assert result.exit_code == 1
    assert ("unavailable UID" if change == "remove" else "type mismatch") in result.output
    assert len(calls) == 1
    assert fake.writes == 0
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("mapping,code", [(None, 1), ("metrics", 0), ("logs", 1), ("absent", 1)])
def test_noninteractive_import_never_prompts(tmp_path, monkeypatch, fake, mapping, code):
    path = write_dashboard(
        tmp_path / "a.json", panels=[{"datasource": {"uid": "missing", "type": "prometheus"}}]
    )
    monkeypatch.setattr(grafana_cli, "interactive", lambda: False)
    monkeypatch.setattr(
        grafana_cli.questionary, "select", lambda *_a, **_kw: pytest.fail("prompted")
    )
    result = datasource_import(
        path, *(["--datasource-map", f"missing={mapping}"] if mapping else [])
    )
    assert result.exit_code == code, result.output
    if mapping is None:
        assert "--datasource-map missing=UID" in result.output
    if code:
        assert fake.writes == 0


@pytest.mark.parametrize(
    "source,mapping", [("metrics", None), ("Metrics", None), ("missing", "metrics")]
)
def test_resolved_interactive_import_skips_picker(tmp_path, monkeypatch, fake, source, mapping):
    path = write_dashboard(tmp_path / "a.json", panels=[{"datasource": source}])
    calls = stub_datasource_picker(monkeypatch, lambda _: pytest.fail("resolved source prompted"))
    result = datasource_import(
        path, *(["--datasource-map", f"missing={mapping}"] if mapping else [])
    )
    assert result.exit_code == 0, result.output
    assert not calls


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\r", "a"),
        ("\x1b[B\r", "b"),
        ("b-uid\r", "b"),
        ("no-match\r" + "\x7f" * 8 + "b-uid\r", "b"),
        ("no-match\r\x03", None),
    ],
)
def test_datasource_picker_real_keyboard_navigation_and_search(monkeypatch, keys, expected):
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    select = grafana_cli.questionary.select
    needed = DatasourceMappingRequired(
        "missing",
        expected_type="prometheus",
        candidates=[
            {"uid": "a", "name": "Alpha", "type": "prometheus"},
            {"uid": "b", "name": "Beta b-uid", "type": "prometheus"},
        ],
    )
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            grafana_cli.questionary,
            "select",
            lambda *a, **kw: select(*a, **kw, input=pipe, output=DummyOutput()),
        )
        pipe.send_text(keys)
        if expected is None:
            with pytest.raises(grafana_cli.typer.Exit) as caught:
                grafana_cli._select_datasource(needed)
            assert caught.value.exit_code == 1
        else:
            assert grafana_cli._select_datasource(needed) == expected


def test_external_import_uses_no_project_or_catalog(monkeypatch, tmp_path, fake):
    path = write_dashboard(tmp_path / "a.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("external operation accessed project")

    monkeypatch.setattr(grafana_cli, "cluster_session", forbidden)
    monkeypatch.setattr(grafana_cli, "assert_import_ownership", forbidden)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Verified: 1" in result.output
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize(
    "flags",
    [
        ["--config", "missing.yaml"],
        ["--target", "cluster"],
        ["--attach"],
        ["--component-sources", "missing.yaml"],
    ],
)
def test_external_rejects_project_flags_before_connect(tmp_path, fake, flags):
    path = write_dashboard(tmp_path / "a.json")
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            *flags,
        ],
    )
    assert result.exit_code != 0
    assert "rejects" in result.output
    assert fake.calls == []


def test_sso_preparation_reports_pending_and_never_calls_api(tmp_path, monkeypatch, fake):
    path = write_dashboard(tmp_path / "a.json")
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)
    opened = []
    monkeypatch.setattr(grafana_cli.webbrowser, "open", lambda url: opened.append(url))
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://example.invalid/prefix",
            "--sso",
            "--output-dir",
            str(tmp_path / "prepared"),
        ],
    )
    assert result.exit_code == 3, result.output
    assert "manual import pending" in result.output
    assert "Installed:" not in result.output
    assert (tmp_path / "prepared" / "board.json").is_file()
    assert opened == ["https://example.invalid/prefix/dashboard/import"]
    assert fake.calls == []


def test_validate_local_json_is_offline(tmp_path, monkeypatch):
    path = write_dashboard(tmp_path / "a.json")
    monkeypatch.setattr(
        grafana_cli,
        "destination",
        lambda **_: pytest.fail("offline validation opened a destination"),
    )
    result = CliRunner().invoke(cli.app, ["grafana", "validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "Valid JSON: board" in result.output


def test_export_is_local_output_only_and_preflights_collisions(tmp_path, fake):
    execute_imports(
        fake, prepare_imports(fake, [dashboard("one"), dashboard("two")]), emit=lambda _: None
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "two.json").write_text("different")
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "export",
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code == 1
    assert not (out / "one.json").exists()
    assert fake.writes == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["grafana", "--dashboard-json", "a.json"],
        ["grafana", "--export-dashboard", "https://example.invalid"],
        ["validate-dashboards", "config.yaml"],
        ["grafana", "import", "a.json", "--username", "admin"],
    ],
)
def test_legacy_commands_are_not_aliases(argv):
    assert CliRunner().invoke(cli.app, argv).exit_code == 2


@pytest.mark.parametrize("grafana_id", ["grafana", "dashboards"])
def test_project_publication_preserves_unrelated_config_and_receipt_identity(
    tmp_path, monkeypatch, grafana_id
):
    from nebius_cxcli import observability
    from nebius_cxcli.grafana_project import declarations

    monkeypatch.setattr(observability, "_grafana_app_id", lambda: grafana_id)
    path = tmp_path / "config.yaml"
    source = {
        "version": "v1",
        "infra": {"components": []},
        "deploy": {"targets": []},
        "apps": {
            "charts": [
                {"id": grafana_id, "instance_id": "cluster", "enabled": True},
                {
                    "id": "soperator",
                    "instance_id": "cluster",
                    "enabled": True,
                    "values": {"preserve": 1},
                },
            ]
        },
    }
    path.write_text(yaml.safe_dump(source))
    client = FakeGrafana()
    plans = prepare_imports(client, [dashboard()])
    project = publish_import(
        root=tmp_path,
        config_path=path,
        target="cluster",
        plans=plans,
        identity={"cluster_id": "fixture"},
        namespace="default",
        overwrite=False,
        catalog_keys=None,
        assert_authority=lambda: None,
        expected_config=path.read_bytes(),
    )
    saved = yaml.safe_load(path.read_bytes())
    assert saved["apps"]["charts"][1] == source["apps"]["charts"][1]
    assert saved["apps"]["charts"][0]["dashboard_imports"][0]["sha256"] == plans[0].digest
    assert declarations(saved, "cluster")[0]["uid"] == "board"
    execute_imports(client, plans, checkpoint=project.checkpoint, emit=lambda _: None)
    before = path.read_bytes(), project.receipt_path.read_bytes()
    again = prepare_imports(client, [dashboard()])
    repeated = publish_import(
        root=tmp_path,
        config_path=path,
        target="cluster",
        plans=again,
        identity={"cluster_id": "fixture"},
        namespace="default",
        overwrite=False,
        catalog_keys=None,
        assert_authority=lambda: None,
        expected_config=path.read_bytes(),
    )
    execute_imports(client, again, checkpoint=repeated.checkpoint, emit=lambda _: None)
    assert (path.read_bytes(), project.receipt_path.read_bytes()) == before
    with pytest.raises(RuntimeError, match="different Grafana"):
        publish_import(
            root=tmp_path,
            config_path=path,
            target="cluster",
            plans=again,
            identity={"cluster_id": "other"},
            namespace="default",
            overwrite=False,
            catalog_keys=None,
            assert_authority=lambda: None,
            expected_config=path.read_bytes(),
        )


def test_exclusions_only_apply_to_explicitly_linked_entries():
    values = {"dashboards": {"folder": {"board": {"json": json.dumps(dashboard())}}}}
    row = {"dashboard_imports": [{"uid": "board"}]}
    with pytest.raises(ValueError, match="conflicting"):
        exclude_linked_catalog_dashboards(copy.deepcopy(values), row)
    row["dashboard_imports"][0]["catalog_key"] = "folder/board"
    exclude_linked_catalog_dashboards(values, row)
    assert values == {"dashboards": {}}


def test_declaration_rejects_traversal_and_unbound_content():
    row = {
        "id": "grafana",
        "dashboard_imports": [{"uid": "board", "json_file": "../outside.json", "sha256": "a" * 64}],
    }
    with pytest.raises(ValueError, match="project-relative"):
        validate_import_declarations(row, grafana_id="grafana")
    row["dashboard_imports"][0]["json_file"] = "inside.json"
    row["dashboard_imports"][0].pop("sha256")
    with pytest.raises(ValueError, match="bind"):
        validate_import_declarations(row, grafana_id="grafana")


def test_conversion_failure_is_not_success():
    with pytest.raises(GrafanaError, match="convert"):
        GrafanaClient.check_conversion({"status": {"conversion": {"failed": True}}})


def test_output_concurrent_creation_never_gets_overwritten(tmp_path, monkeypatch):
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    original = ProjectBundleTransaction.commit

    def race(self, updates, **kwargs):
        next(iter(updates)).write_text("editor content")
        return original(self, updates, **kwargs)

    monkeypatch.setattr(ProjectBundleTransaction, "commit", race)
    with pytest.raises(RuntimeError, match="changed after"):
        grafana_cli.write_outputs([dashboard()], tmp_path, overwrite=False)
    assert (tmp_path / "board.json").read_text() == "editor content"


def test_interrupted_publication_recovers_only_same_destination(tmp_path):
    from nebius_cxcli.grafana_project import publication_identity, recover_import_publication
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    path = tmp_path / "config.yaml"
    path.write_text("before")

    def crash(stage):
        if stage == "after-commit":
            raise RuntimeError("interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        ProjectBundleTransaction(tmp_path, failpoint=crash).commit(
            {path: b"after"}, generation_sha256=publication_identity("import", path, "cluster")
        )
    with pytest.raises(RuntimeError, match="differs"):
        recover_import_publication(path, "other")
    assert path.read_text() == "before"
    recover_import_publication(path, "cluster")
    assert path.read_text() == "after"


@pytest.mark.parametrize(
    "field,value", [("templating", None), ("templating", {"list": 5}), ("__inputs", ["bad"])]
)
def test_malformed_mapping_input_is_rejected(field, value):
    with pytest.raises(ValueError):
        normalize_dashboard(dashboard(**{field: value}))


def test_api_listing_paginates_and_rejects_repeated_token(monkeypatch):
    client = FakeGrafana()
    calls = []

    def request(method, path):
        calls.append(path)
        return {"items": [], "metadata": {"continue": "next"}}

    monkeypatch.setattr(client, "request", request)
    with pytest.raises(GrafanaError, match="repeated"):
        list(client.dashboards())
    assert len(calls) == 2 and "continue=next" in calls[1]


def test_loopback_clients_disable_environment_proxy(monkeypatch):
    from urllib.request import ProxyHandler

    monkeypatch.setenv("http_proxy", "http://proxy.example.invalid:8080")
    monkeypatch.setenv("https_proxy", "http://proxy.example.invalid:8080")
    local = GrafanaClient("http://127.0.0.1:3000", GrafanaAuth("Basic fixture"))
    assert not any(isinstance(handler, ProxyHandler) for handler in local.opener.handlers)
    external = GrafanaClient("https://example.invalid", GrafanaAuth("Bearer fixture"))
    assert any(isinstance(handler, ProxyHandler) for handler in external.opener.handlers)


def test_same_root_interrupted_attachment_recovers_before_import(tmp_path):
    from nebius_cxcli.grafana_project import publication_identity, recover_import_publication
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    config = tmp_path / "config.yaml"
    catalog = tmp_path / "component_sources.yaml"
    config.write_text("config")
    catalog.write_text("before")

    def crash(stage):
        if stage == "after-commit":
            raise RuntimeError("interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        ProjectBundleTransaction(tmp_path, failpoint=crash).commit(
            {catalog: b"after"}, generation_sha256=publication_identity("attach", catalog)
        )
    with pytest.raises(RuntimeError, match="differs"):
        recover_import_publication(config, "cluster")
    recover_import_publication(config, "cluster", catalog)
    assert catalog.read_text() == "after"
    assert config.read_text() == "config"


def test_interrupted_import_retains_original_remote_preimage(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"apps": {"charts": [{"id": "grafana", "instance_id": "cluster", "enabled": True}]}}
        )
    )
    client = FakeGrafana()
    first = prepare_imports(client, [dashboard()])
    project = publish_import(
        root=tmp_path,
        config_path=path,
        target="cluster",
        plans=first,
        identity={"cluster_id": "fixture"},
        namespace="default",
        overwrite=False,
        catalog_keys=None,
        assert_authority=lambda: None,
        expected_config=path.read_bytes(),
    )
    project.checkpoint(first[0], "writing", None)
    # A different editor creates a different dashboard while the importer is stopped.
    execute_imports(
        client, prepare_imports(client, [dashboard(title="Editor")]), emit=lambda _: None
    )
    fresh = prepare_imports(client, [dashboard()], overwrite=True)
    with pytest.raises(RuntimeError, match="since the interrupted import"):
        publish_import(
            root=tmp_path,
            config_path=path,
            target="cluster",
            plans=fresh,
            identity={"cluster_id": "fixture"},
            namespace="default",
            overwrite=True,
            catalog_keys=None,
            assert_authority=lambda: None,
            expected_config=path.read_bytes(),
        )
    assert client.resources["board"]["spec"]["title"] == "Editor"


def test_live_validation_maps_inputs_and_only_uses_dry_run(tmp_path, fake):
    path = write_dashboard(
        tmp_path / "input.json",
        panels=[{"datasource": {"type": "prometheus", "uid": "${DS_METRICS}"}}],
        __inputs=[{"name": "DS_METRICS", "type": "datasource", "pluginId": "prometheus"}],
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "validate",
            str(path),
            "--url",
            "https://example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            "--datasource-map",
            "DS_METRICS=metrics",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fake.writes == 0 and not fake.resources
    calls = [(path, payload) for method, path, payload in fake.calls if method == "POST"]
    assert len(calls) == 1 and "dryRun=All" in calls[0][0]
    assert calls[0][1]["spec"]["panels"][0]["datasource"]["uid"] == "metrics"


@pytest.mark.parametrize("drift", [None, "datasource", "identity", "config", "owner"])
def test_cluster_selection_precedes_leases_and_locked_preflight_rejects_drift(
    tmp_path, monkeypatch, drift
):
    from nebius_cxcli.grafana_cluster import ClusterSession
    from nebius_cxcli.paths import resolve_project_paths

    paths = resolve_project_paths(tmp_path / "config.yaml")
    payload = {"apps": {"charts": [{"id": "grafana", "instance_id": "cluster", "enabled": True}]}}
    paths.config_path.write_text(yaml.safe_dump(payload))
    original = paths.config_path.read_bytes()
    source = write_dashboard(
        tmp_path / "input.json",
        panels=[{"datasource": {"type": "prometheus", "uid": "missing"}}],
    )
    client = FakeGrafana()
    phases = []
    locked = []

    @contextmanager
    def session(config_path, target, *, mutating, **kwargs):
        phases.append(mutating)
        identity = {"cluster_id": "fixture"}
        config_bytes = original
        if mutating:
            locked.append(True)
            if drift == "identity":
                identity["cluster_id"] = "changed"
            elif drift == "config":
                config_bytes += b"# concurrent edit\n"
            elif drift == "datasource":
                client.inventory = []
            elif drift == "owner":
                client.resources["board"] = {
                    "metadata": {"name": "board", "annotations": {"grafana.app/managedBy": "file"}},
                    "spec": dashboard(),
                }
        try:
            yield ClusterSession(
                config=payload,
                paths=paths,
                client=client,
                identity=identity,
                target=target,
                env={},
                source=[config_bytes],
                fence=lambda: None,
            )
        finally:
            if mutating:
                locked.pop()

    def select(exc):
        assert not locked and phases == [False]
        return "metrics"

    monkeypatch.setattr(grafana_cli, "cluster_session", session)
    monkeypatch.setattr(grafana_cli, "selected_target", lambda *_: "cluster")
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)
    monkeypatch.setattr(grafana_cli, "_select_datasource", select)
    before = set(tmp_path.rglob("*"))
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(source),
            "--config",
            str(paths.config_path),
            "--target",
            "cluster",
        ],
    )
    assert phases == [False, True]
    assert not locked
    if drift is None:
        assert result.exit_code == 0, result.output
        assert client.writes == 1
    else:
        assert result.exit_code != 0, result.output
        assert client.writes == 0
        assert paths.config_path.read_bytes() == original
        assert set(tmp_path.rglob("*")) - before == {
            tmp_path / ".nebius-cxcli",
            tmp_path / ".nebius-cxcli" / "config.lock",
        }
