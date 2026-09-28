from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli import component_sources
from nebius_cxcli.grafana_import import prepare_imports
from nebius_cxcli.grafana_project import prepare_attachment


def _write_grafana_catalog(path: Path, *, gnet_folder: str = "") -> None:
    dashboard_defaults: dict[str, Any] = {}
    if gnet_folder:
        dashboard_defaults[gnet_folder] = {
            "service-dashboard": {
                "gnetId": 23425,
                "revision": 1,
                "uid": "service-dashboard",
                "datasource": "Nebius Services",
            }
        }
    sources = {
        "components": {
            "infra": {},
            "apps": {
                "grafana": {
                    "source": {
                        "portable": {
                            "repo": "https://example.invalid/grafana",
                            "chart": "grafana",
                            "version": "1.0.0",
                        }
                    },
                    "release": {"namespace": "observability", "name": "grafana"},
                    "defaults": {
                        "values.dashboardProviders": {
                            "dashboardproviders.yaml": {"apiVersion": 1, "providers": []}
                        },
                        "values.dashboards": dashboard_defaults,
                    },
                }
            },
        }
    }
    settings = {
        "observability": {
            "endpoints": {
                "read": {
                    "metrics_service_provider_read": {
                        "label": "Services metrics",
                        "template": "https://example.invalid/services/{project_id}",
                    },
                    "metrics_user_read": {
                        "label": "User metrics",
                        "template": "https://example.invalid/metrics/{project_id}",
                    },
                    "logs_loki_read": {
                        "label": "Logs",
                        "template": "https://example.invalid/logs/{project_id}",
                    },
                },
                "write": {},
            }
        },
        "components": {
            "apps": {
                "grafana": {
                    "cli": {
                        "datasources": {
                            "services": {
                                "name": "Nebius Services",
                                "uid": "nebius-service-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_service_provider_read",
                            },
                            "user-metrics": {
                                "name": "Nebius User Metrics",
                                "uid": "nebius-user-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_user_read",
                            },
                            "logs": {
                                "name": "Nebius Logs",
                                "uid": "nebius-logs",
                                "type": "loki",
                                "read_endpoint": "logs_loki_read",
                            },
                        }
                    }
                }
            }
        },
    }
    path.write_text(yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")
    path.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(settings, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "catalog" / "component_sources.yaml"
    path.parent.mkdir()
    _write_grafana_catalog(path)
    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(path))
    component_sources.reset_component_sources_cache()
    yield path
    component_sources.reset_component_sources_cache()


def test_attachment_is_valid_idempotent_and_preserves_distinct_sources(catalog):
    client = FakeGrafana()
    plans = prepare_imports(
        client,
        [
            dashboard(
                panels=[
                    {"datasource": {"type": "prometheus", "uid": "metrics"}},
                    {"datasource": {"type": "loki", "uid": "logs"}},
                ]
            )
        ],
        folder="f" * 40,
    )
    attachment = prepare_attachment(catalog, plans, folder_title="GPU", overwrite=False, org_id=2)
    assert not any(catalog.parent.glob("grafana_dashboards/**/*.json"))
    attachment.publish(lambda: None)
    payload = yaml.safe_load(catalog.read_bytes())
    values = payload["components"]["apps"]["grafana"]["defaults"]
    provider = values["values.dashboardProviders"]["dashboardproviders.yaml"]["providers"][0]
    assert provider["orgId"] == 2
    assert len(provider["name"]) <= 40
    key = attachment.keys["board"].split("/")[0]
    entry = values["values.dashboards"][key]["board"]
    assert "datasource" not in entry
    saved = json.loads((catalog.parent / entry["json_file"]).read_bytes())
    assert [x["datasource"]["uid"] for x in saved["panels"]] == ["metrics", "logs"]
    before = catalog.read_bytes()
    prepare_attachment(catalog, plans, folder_title="GPU", overwrite=False, org_id=2).publish(
        lambda: None
    )
    assert catalog.read_bytes() == before


def test_attachment_refuses_concurrent_catalog_edit(catalog):
    plans = prepare_imports(FakeGrafana(), [dashboard()])
    attachment = prepare_attachment(catalog, plans, folder_title="", overwrite=False)
    catalog.write_bytes(catalog.read_bytes() + b"\n# editor owns this change\n")
    with pytest.raises(RuntimeError, match="changed after"):
        attachment.publish(lambda: None)
    assert b"editor owns" in catalog.read_bytes()


def test_attachment_refuses_folder_move_without_publishing_duplicate_owner(catalog):
    client = FakeGrafana()
    initial = prepare_imports(client, [dashboard()])
    prepare_attachment(catalog, initial, folder_title="", overwrite=False).publish(lambda: None)
    before = {path: path.read_bytes() for path in catalog.parent.rglob("*") if path.is_file()}
    moved = prepare_imports(client, [dashboard()], folder="new-folder", overwrite=True)

    with pytest.raises(ValueError, match="already belongs to catalog entry"):
        prepare_attachment(catalog, moved, folder_title="New folder", overwrite=True)

    assert {
        path: path.read_bytes() for path in catalog.parent.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("source", ["json", "json_file", "gnetId"])
def test_attachment_refuses_uid_owned_by_another_catalog_key(catalog, source):
    payload = yaml.safe_load(catalog.read_bytes())
    if source == "json":
        entry = {"json": json.dumps(dashboard())}
    elif source == "json_file":
        (catalog.parent / "existing.json").write_text(json.dumps(dashboard()))
        entry = {"json_file": "existing.json"}
    else:
        entry = {"gnetId": 23425, "revision": 1, "uid": "board", "datasource": "Nebius Services"}
    payload["components"]["apps"]["grafana"]["defaults"]["values.dashboards.existing.alias"] = entry
    catalog.write_text(yaml.safe_dump(payload))
    before = catalog.read_bytes()

    with pytest.raises(ValueError, match="already belongs to catalog entry existing/alias"):
        prepare_attachment(
            catalog, prepare_imports(FakeGrafana(), [dashboard()]), folder_title="", overwrite=True
        )

    assert catalog.read_bytes() == before
    assert not any(catalog.parent.glob("grafana_dashboards/**/*.json"))


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("attach", [False, True])
def test_cluster_file_and_directory_import_with_optional_attachment(
    tmp_path, monkeypatch, catalog, directory, attach
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from nebius_cxcli import cli, grafana_cli
    from nebius_cxcli.grafana_dashboards import json_bytes

    root = tmp_path / "project"
    root.mkdir()
    config = root / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {"apps": {"charts": [{"id": "grafana", "enabled": True, "instance_id": "cluster"}]}}
        )
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "a.json").write_bytes(json_bytes(dashboard("a")))
    (inputs / "b.json").write_bytes(json_bytes(dashboard("b")))
    selected = inputs if directory else inputs / "a.json"
    client = FakeGrafana()

    @contextmanager
    def session(path, target, *, mutating, progress, pause, discovery):
        source = [path.read_bytes()]
        yield SimpleNamespace(
            config=yaml.safe_load(source[0]),
            paths=SimpleNamespace(project_dir=root, config_path=config),
            target=target,
            client=client,
            identity={"cluster_id": "fixture"},
            source=source,
            fence=lambda: None,
            accept_publication=lambda expected: source.__setitem__(0, expected),
        )

    monkeypatch.setattr(grafana_cli, "cluster_session", session)
    # Catalog ownership has independent render/guard tests; the actual attachment
    # transaction and candidate parser remain real in this command-level test.
    monkeypatch.setattr(grafana_cli, "assert_import_ownership", lambda *_, **__: None)
    argv = ["grafana", "import", str(selected), "--config", str(config), "--target", "cluster"]
    if attach:
        argv += ["--attach", "--component-sources", str(catalog)]
    before_catalog = catalog.read_bytes()
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    expected = {"a", "b"} if directory else {"a"}
    assert set(client.resources) == expected
    entries = yaml.safe_load(config.read_bytes())["apps"]["charts"][0]["dashboard_imports"]
    assert {e["uid"] for e in entries} == expected
    assert all(bool(e.get("catalog_key")) == attach for e in entries)
    assert (catalog.read_bytes() != before_catalog) == attach
    published = [
        config,
        catalog,
        *config.parent.glob("grafana_dashboards/**/*.json"),
        *config.parent.glob(".grafana-imports/*.json"),
        *catalog.parent.glob("grafana_dashboards/**/*.json"),
    ]
    before_repeat = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in published}
    again = CliRunner().invoke(cli.app, argv)
    assert again.exit_code == 0, again.output
    assert client.writes == len(expected)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in published} == before_repeat
    if attach:
        before_config = config.read_bytes()
        before_catalog = catalog.read_bytes()
        moved = CliRunner().invoke(cli.app, [*argv, "--folder-uid", "new-folder", "--overwrite"])
        assert moved.exit_code == 1, moved.output
        assert "already belongs to catalog entry" in moved.output
        assert config.read_bytes() == before_config
        assert catalog.read_bytes() == before_catalog
        assert client.writes == len(expected)
