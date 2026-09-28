"""Opt-in API qualification against the disposable runner's pinned Grafana."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from nebius_cxcli.grafana_api import GrafanaClient, GrafanaError, basic_auth
from nebius_cxcli.grafana_dashboards import content_digest
from nebius_cxcli.grafana_import import execute_imports, prepare_imports, require_complete

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    url = os.environ.get("CXCLI_GRAFANA_TEST_URL")
    if not url:
        pytest.skip("Run scripts/verify_grafana_api.py for the pinned disposable API lane")
    if not url.startswith("http://127.0.0.1:"):
        pytest.fail("Integration fixture must be the disposable loopback server")
    result = GrafanaClient(url, basic_auth("admin", os.environ["CXCLI_GRAFANA_TEST_PASSWORD"]))
    result.connect()
    return result


def test_real_post_dry_run_migration_and_create_readback(client):
    original = {
        "uid": "cxcli-real-import",
        "title": "API qualification",
        "editable": False,
        "schemaVersion": 30,
        "panels": [],
    }
    before = {item["metadata"]["name"] for item in client.dashboards()}
    canonical = client.canonical({**original, "editable": True}, "")
    after = {item["metadata"]["name"] for item in client.dashboards()}
    assert before == after
    assert canonical["schemaVersion"] >= original["schemaVersion"]
    require_complete(
        execute_imports(client, prepare_imports(client, [original]), emit=lambda _: None)
    )
    resource = client.get(original["uid"])
    assert content_digest(client.portable(resource)) == content_digest(canonical)
    version = resource["metadata"]["resourceVersion"]
    assert execute_imports(client, prepare_imports(client, [original]), emit=lambda _: None) == {
        original["uid"]: "unchanged"
    }
    assert client.get(original["uid"])["metadata"]["resourceVersion"] == version


def test_real_stale_version_rejected(client):
    original = {
        "uid": "cxcli-real-cas",
        "title": "CAS qualification",
        "schemaVersion": 42,
        "panels": [],
    }
    canonical = client.canonical(original, "")
    created = client.write(canonical, "", None)
    client.write({**canonical, "title": "Concurrent editor"}, "", created)
    with pytest.raises(GrafanaError) as error:
        client.write({**canonical, "title": "Must not win"}, "", created)
    assert error.value.status in {409, 412}
    assert client.get(original["uid"])["spec"]["title"] == "Concurrent editor"


def test_real_editable_provisioned_update_is_idempotent_and_source_remains_authoritative(client):
    resource = client.get("cxcli-provisioned-fixture")
    assert resource is not None
    source_path = Path(os.environ["CXCLI_GRAFANA_TEST_SOURCE"])
    original = source_path.read_bytes()
    with pytest.raises(GrafanaError, match="provisioned"):
        client.assert_unmanaged(resource)
    with pytest.raises(GrafanaError, match="provisioned"):
        prepare_imports(client, [client.portable(resource)])
    desired = {**client.portable(resource), "title": "Explicit temporary update"}
    require_complete(execute_imports(client, prepare_imports(client, [desired], overwrite=True)))
    saved = client.get(desired["uid"])
    assert saved is not None
    for key, value in resource["metadata"]["annotations"].items():
        if key.startswith(("grafana.app/manager", "grafana.app/source")) or key in {
            "grafana.app/managedBy",
            "grafana.app/folder",
        }:
            assert saved["metadata"]["annotations"][key] == value
    assert source_path.read_bytes() == original
    assert execute_imports(client, prepare_imports(client, [desired], overwrite=True)) == {
        desired["uid"]: "unchanged"
    }
    assert (
        client.get(desired["uid"])["metadata"]["resourceVersion"]
        == saved["metadata"]["resourceVersion"]
    )
    # External fixture edit starts the provisioning-reconciliation scenario.
    source = json.loads(original)
    source["title"] = "Updated provisioning source"
    source_path.write_text(json.dumps(source))
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if client.get(desired["uid"])["spec"]["title"] == source["title"]:
            break
        time.sleep(0.25)
    else:
        pytest.fail("Provisioning did not replace the temporary API edit")


def test_real_locked_file_provisioning_is_rejected_before_write(client):
    resource = client.get("cxcli-provisioned-locked")
    assert resource is not None
    with pytest.raises(GrafanaError, match="allowUiUpdates"):
        prepare_imports(
            client, [{**client.portable(resource), "title": "Must not change"}], overwrite=True
        )
    assert client.get("cxcli-provisioned-locked") == resource


def test_real_nondefault_organization_uses_authenticated_namespace(client):
    organization = client.request("POST", "api/orgs", {"name": "cxcli-organization-fixture"})
    scoped = GrafanaClient(client.url, client.auth, org_id=organization["orgId"])
    scoped.connect()
    assert scoped.namespace != client.namespace
    value = {
        "uid": "cxcli-org-scoped",
        "title": "Organization scope",
        "schemaVersion": 42,
        "panels": [],
    }
    require_complete(execute_imports(scoped, prepare_imports(scoped, [value]), emit=lambda _: None))
    assert scoped.get(value["uid"]) is not None
    assert client.get(value["uid"]) is None
