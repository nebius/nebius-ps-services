"""Operation counts through the real HTTP authority and Kubernetes lease fences."""

from __future__ import annotations

import io
import json
from collections import Counter
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli.grafana_api import GrafanaClient, GrafanaError
from nebius_cxcli.grafana_import import execute_imports, prepare_imports, require_complete
from nebius_cxcli.soperator_operation_lock import SoperatorLeaseAuthority, SoperatorOperationLease


@pytest.mark.parametrize(
    ("scenario", "http_calls", "kubernetes_calls", "writes"),
    [("new", 62, 200, 12), ("unchanged", 38, 152, 0), ("changed", 74, 296, 12)],
)
def test_batch_request_budget_preserves_live_authority_fences(
    monkeypatch, scenario, http_calls, kubernetes_calls, writes
):
    server = FakeGrafana()
    dashboards = [dashboard(f"board-{index}") for index in range(12)]
    if scenario != "new":
        require_complete(
            execute_imports(
                server, prepare_imports(server, dashboards, folder="folder"), emit=lambda _: None
            )
        )
    if scenario == "changed":
        dashboards = [{**item, "title": "Changed"} for item in dashboards]
    previous_writes = server.writes
    server.calls.clear()
    lease = SoperatorOperationLease(
        kube_context="fixture", cluster_id="fixture-cluster", operation_fingerprint="fixture"
    )
    authority = SoperatorLeaseAuthority(
        lease.name, "fixture-lease-uid", "fixture-holder-hash", 7, "fixture"
    )
    lease._authority = authority
    commands = Counter()

    def kubectl(*args, **kwargs):
        commands[args[0]] += 1
        if args[0] == "patch":
            operations = json.loads(args[-1])
            assert operations[:2] == [
                {"op": "test", "path": "/metadata/uid", "value": authority.lease_uid},
                {"op": "test", "path": "/spec/holderIdentity", "value": lease.holder_identity},
            ]
            payload = {}
        else:
            assert args[:3] == ("get", "configmap", lease.fence_name)
            payload = {
                "data": {
                    "epoch": "7",
                    "leaseUid": authority.lease_uid,
                    "holderIdentitySha256": authority.holder_identity_sha256,
                    "operationFingerprint": authority.operation_fingerprint,
                }
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(lease, "_kubectl", kubectl)
    client = GrafanaClient(server.url, server.auth, assert_authority=lease.assert_held)
    client.namespace = server.namespace

    def open_request(request, **kwargs):
        try:
            payload = server.request(
                request.get_method(),
                request.full_url.removeprefix(server.url),
                json.loads(request.data) if request.data is not None else None,
            )
        except GrafanaError as exc:
            raise HTTPError(request.full_url, exc.status, "fixture", {}, io.BytesIO()) from None
        return io.BytesIO(json.dumps(payload).encode())

    client.opener = SimpleNamespace(open=open_request)
    plans = prepare_imports(client, dashboards, folder="folder", overwrite=True)
    results = execute_imports(client, plans, emit=lambda _: None)

    require_complete(results)
    assert len(server.calls) == http_calls
    assert sum(commands.values()) == kubernetes_calls
    assert commands["patch"] == commands["get"] == kubernetes_calls // 2
    assert server.writes - previous_writes == writes
