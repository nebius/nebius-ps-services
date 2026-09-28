from __future__ import annotations

import copy
import json

import pytest
import yaml
from typer.testing import CliRunner

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli import cli, grafana_cli
from nebius_cxcli.grafana_api import GrafanaError
from nebius_cxcli.grafana_dashboards import json_bytes, validate_import_declarations
from nebius_cxcli.grafana_import import execute_imports, prepare_imports, require_complete
from nebius_cxcli.grafana_project import (
    declarations,
    declared_dashboards,
    exclude_linked_catalog_dashboards,
    publish_import,
)


def provisioned(client, uid="board", **annotations):
    client.resources[uid] = {
        "metadata": {
            "name": uid,
            "resourceVersion": "1",
            "labels": {"fixture": "preserved"},
            "annotations": {
                "grafana.app/managedBy": "classic-file-provisioning",
                "grafana.app/managerId": "fixture-provider",
                "grafana.app/managerAllowsEdits": "true",
                "grafana.app/folder": "fixture-folder",
                "grafana.app/sourcePath": "fixture.json",
                "grafana.app/sourceChecksum": "fixture-checksum",
                **annotations,
            },
        },
        "spec": dashboard(uid, schemaVersion=42),
    }
    client.writes = 1


def test_managed_overwrite_preserves_metadata_and_repeated_import_is_write_free():
    client = FakeGrafana()
    provisioned(client)
    metadata = copy.deepcopy(client.resources["board"]["metadata"])
    desired = dashboard(title="Updated dashboard")
    first = prepare_imports(client, [desired], overwrite=True)
    require_complete(execute_imports(client, first, emit=lambda _: None))
    saved = copy.deepcopy(client.resources["board"])
    assert saved["metadata"]["annotations"] == metadata["annotations"]
    assert saved["metadata"]["labels"] == metadata["labels"]
    assert client.writes == 2
    again = prepare_imports(client, [desired], overwrite=True)
    assert execute_imports(client, again, emit=lambda _: None) == {"board": "unchanged"}
    assert client.resources["board"] == saved
    assert client.writes == 2


@pytest.mark.parametrize(
    "annotation,value",
    [
        ("grafana.app/managedBy", "repo"),
        ("grafana.app/managedBy", "terraform"),
        ("grafana.app/managerId", ""),
        ("grafana.app/managerAllowsEdits", "false"),
        ("grafana.app/managerAllowsEdits", True),
        ("grafana.app/managerAllowsEdits", None),
    ],
)
def test_ineligible_management_fails_before_schema_conversion(annotation, value):
    client = FakeGrafana()
    provisioned(client, **{annotation: value})
    with pytest.raises(GrafanaError):
        prepare_imports(client, [dashboard(title="Updated")], overwrite=True)
    assert not any(method == "POST" for method, _, _ in client.calls)
    assert client.writes == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("grafana.app/managerId", "replacement-provider"),
        ("grafana.app/managerAllowsEdits", "false"),
        ("grafana.app/folder", "moved-folder"),
    ],
)
def test_ownership_drift_cannot_hide_behind_identical_content(field, value):
    client = FakeGrafana()
    provisioned(client)
    plans = prepare_imports(client, [dashboard()], overwrite=True)
    client.resources["board"]["metadata"]["annotations"][field] = value
    assert execute_imports(client, plans, emit=lambda _: None) == {"board": "failed"}
    assert client.writes == 1


@pytest.mark.parametrize(
    "options", [{"attach": True}, {"folder": "other"}, {"folder_explicit": True}]
)
def test_managed_batch_rejects_attachment_and_folder_moves_before_conversion(options):
    client = FakeGrafana()
    provisioned(client)
    with pytest.raises(GrafanaError):
        prepare_imports(client, [dashboard("new"), dashboard()], overwrite=True, **options)
    assert not any(method == "POST" for method, _, _ in client.calls)
    assert client.writes == 1


def test_managed_lost_response_readback_does_not_repeat_write():
    client = FakeGrafana()
    provisioned(client)
    client.lose_response = True
    desired = dashboard(title="Updated")
    plans = prepare_imports(client, [desired], overwrite=True)
    assert execute_imports(client, plans, emit=lambda _: None) == {"board": "installed"}
    assert execute_imports(
        client, prepare_imports(client, [desired], overwrite=True), emit=lambda _: None
    ) == {"board": "unchanged"}
    assert client.writes == 2


def project_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"apps": {"charts": [{"id": "grafana", "instance_id": "cluster", "enabled": True}]}}
        )
    )
    return path


def publish(path, plans, **kwargs):
    return publish_import(
        root=path.parent,
        config_path=path,
        target="cluster",
        plans=plans,
        identity={"cluster_id": "fixture"},
        namespace="default",
        overwrite=True,
        catalog_keys=None,
        assert_authority=lambda: None,
        expected_config=path.read_bytes(),
        **kwargs,
    )


def snapshot(project, path):
    paths = [path, project.receipt_path, *path.parent.glob("grafana_dashboards/cluster/*.json")]
    return {item: (item.read_bytes(), item.stat().st_mtime_ns) for item in paths}


@pytest.mark.parametrize("managed", [False, True])
def test_repeated_project_import_preserves_all_bytes_and_mtimes(tmp_path, managed):
    path = project_config(tmp_path)
    client = FakeGrafana()
    if managed:
        provisioned(client)
    desired = dashboard(title="Updated")
    plans = prepare_imports(client, [desired], overwrite=True)
    project = publish(path, plans)
    require_complete(
        execute_imports(client, plans, checkpoint=project.checkpoint, emit=lambda _: None)
    )
    before = snapshot(project, path)
    writes = client.writes
    # Server bookkeeping changes alone do not change import intent.
    client.resources["board"]["metadata"]["resourceVersion"] = "99"
    for _ in range(2):
        plans = prepare_imports(client, [desired], overwrite=True)
        again = publish(path, plans)
        assert execute_imports(client, plans, checkpoint=again.checkpoint, emit=lambda _: None) == {
            "board": "unchanged"
        }
        assert snapshot(again, path) == before
    assert client.writes == writes
    payload = yaml.safe_load(path.read_bytes())
    entries = declarations(payload, "cluster")
    assert len(entries) == 1
    if managed:
        assert entries[0]["replay"] is False
        assert (
            entries[0]["management_sha256"]
            == project.receipt["dashboards"]["board"]["management_sha256"]
        )
        assert "catalog_key" not in entries[0]
        assert declarations(payload, "cluster", replay_only=True) == []
        assert declared_dashboards(payload, tmp_path, "cluster") == [
            (plans[0].dashboard, "fixture-folder")
        ]


@pytest.mark.parametrize("managed", [False, True])
def test_completed_import_with_remote_changes_starts_fresh_receipt(tmp_path, managed):
    path = project_config(tmp_path)
    client = FakeGrafana()
    if managed:
        provisioned(client)
    desired = dashboard(title="Desired")
    plans = prepare_imports(client, [desired], overwrite=True)
    project = publish(path, plans)
    require_complete(
        execute_imports(client, plans, checkpoint=project.checkpoint, emit=lambda _: None)
    )
    client.resources["board"]["spec"]["title"] = "External editor"
    client.resources["board"]["metadata"]["resourceVersion"] = "99"
    plans = prepare_imports(client, [desired], overwrite=True)
    again = publish(path, plans)
    assert again.receipt["dashboards"]["board"]["status"] == "pending"
    assert again.receipt["dashboards"]["board"]["previous_version"] == "99"
    require_complete(
        execute_imports(client, plans, checkpoint=again.checkpoint, emit=lambda _: None)
    )
    assert client.resources["board"]["spec"]["title"] == "Desired"


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize(
    "drift", ["owner", "missing", "unmanaged", "receipt", "entry", "fingerprint"]
)
def test_saved_managed_provenance_rejects_drift(tmp_path, completed, drift):
    path = project_config(tmp_path)
    client = FakeGrafana()
    provisioned(client)
    plans = prepare_imports(client, [dashboard(title="Desired")], overwrite=True)
    project = publish(path, plans)
    if completed:
        require_complete(
            execute_imports(client, plans, checkpoint=project.checkpoint, emit=lambda _: None)
        )
    if drift == "owner":
        client.resources["board"]["metadata"]["annotations"]["grafana.app/managerId"] = (
            "replacement"
        )
    elif drift == "missing":
        del client.resources["board"]
    elif drift == "unmanaged":
        client.resources["board"]["metadata"]["annotations"] = {}
    elif drift == "receipt":
        project.receipt_path.unlink()
    else:
        receipt = json.loads(project.receipt_path.read_bytes())
        if drift == "entry":
            del receipt["dashboards"]["board"]
        else:
            receipt["dashboards"]["board"]["management_sha256"] = "a" * 64
        project.receipt_path.write_bytes(json_bytes(receipt))
    plans = prepare_imports(client, [dashboard(title="Desired")], overwrite=True)
    before = path.read_bytes()
    with pytest.raises(GrafanaError, match="ownership|provenance"):
        publish(path, plans)
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_replay_requires_a_strict_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        validate_import_declarations(
            {
                "id": "grafana",
                "dashboard_imports": [
                    {"uid": "board", "json_file": "board.json", "sha256": "a" * 64, "replay": value}
                ],
            },
            grafana_id="grafana",
        )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"management_sha256": "bad"},
        {"management_sha256": "a" * 64, "catalog_key": "folder/board"},
    ],
)
def test_manual_declaration_requires_provenance_and_forbids_catalog_link(fields):
    with pytest.raises(ValueError):
        validate_import_declarations(
            {
                "id": "grafana",
                "dashboard_imports": [
                    {
                        "uid": "board",
                        "json_file": "board.json",
                        "sha256": "a" * 64,
                        "replay": False,
                        **fields,
                    }
                ],
            },
            grafana_id="grafana",
        )


def test_manual_copy_does_not_suppress_its_catalog_owner():
    values = {"dashboards": {"folder": {"board": {"json": json.dumps(dashboard())}}}}
    before = copy.deepcopy(values)
    exclude_linked_catalog_dashboards(
        values, {"dashboard_imports": [{"uid": "board", "replay": False}]}
    )
    assert values == before


def test_external_managed_import_warns_once_and_creates_no_project_files(tmp_path, monkeypatch):
    client = FakeGrafana()
    provisioned(client)
    monkeypatch.setattr(grafana_cli, "GrafanaClient", lambda *_a, **_kw: client)
    monkeypatch.setenv("TEST_GRAFANA_TOKEN", "fixture")
    path = tmp_path / "input.json"
    path.write_bytes(json_bytes(dashboard(title="Updated")))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://grafana.example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("remains owned by file provisioning") == 1
    assert list(tmp_path.iterdir()) == [path]


def test_external_prompt_cannot_refresh_managed_ownership(tmp_path, monkeypatch):
    client = FakeGrafana()
    provisioned(client)
    monkeypatch.setattr(grafana_cli, "GrafanaClient", lambda *_a, **_kw: client)
    monkeypatch.setenv("TEST_GRAFANA_TOKEN", "fixture")
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)

    def choose(_):
        client.resources["board"]["metadata"]["annotations"]["grafana.app/managerId"] = (
            "replacement"
        )
        return "metrics"

    monkeypatch.setattr(grafana_cli, "_select_datasource", choose)
    path = tmp_path / "input.json"
    path.write_bytes(
        json_bytes(dashboard(panels=[{"datasource": {"type": "prometheus", "uid": "missing"}}]))
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(path),
            "--url",
            "https://grafana.example.invalid",
            "--token-env",
            "TEST_GRAFANA_TOKEN",
            "--overwrite",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ownership or folder changed" in result.output
    assert client.writes == 1
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_mixed_batch_retry_preserves_completed_managed_work(tmp_path):
    path = project_config(tmp_path)
    client = FakeGrafana()
    provisioned(client)
    client.fail_uid = "second"
    desired = [dashboard(title="Desired"), dashboard("second")]
    plans = prepare_imports(client, desired, overwrite=True)
    project = publish(path, plans)
    assert execute_imports(client, plans, checkpoint=project.checkpoint, emit=lambda _: None) == {
        "board": "installed",
        "second": "failed",
    }
    completed = copy.deepcopy(project.receipt["dashboards"]["board"])
    version = client.resources["board"]["metadata"]["resourceVersion"]
    client.fail_uid = None
    plans = prepare_imports(client, desired, overwrite=True)
    again = publish(path, plans)
    assert execute_imports(client, plans, checkpoint=again.checkpoint, emit=lambda _: None) == {
        "board": "unchanged",
        "second": "installed",
    }
    assert again.receipt["dashboards"]["board"] == completed
    assert client.resources["board"]["metadata"]["resourceVersion"] == version
    assert client.writes == 3


def test_interrupted_managed_import_cannot_refresh_its_version(tmp_path):
    path = project_config(tmp_path)
    client = FakeGrafana()
    provisioned(client)
    desired = [dashboard(title="Desired")]
    plans = prepare_imports(client, desired, overwrite=True)
    project = publish(path, plans)
    project.checkpoint(plans[0], "writing", plans[0].previous)
    client.resources["board"]["spec"]["title"] = "Concurrent edit"
    client.resources["board"]["metadata"]["resourceVersion"] = "99"
    before = snapshot(project, path)
    with pytest.raises(RuntimeError, match="since the interrupted import"):
        publish(path, prepare_imports(client, desired, overwrite=True))
    assert snapshot(project, path) == before
    assert client.writes == 1
