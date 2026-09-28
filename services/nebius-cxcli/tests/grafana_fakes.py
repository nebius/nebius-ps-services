"""Stateful Grafana HTTP contract double with schema migration and CAS."""

from __future__ import annotations

import copy
from urllib.parse import parse_qs, urlsplit

from nebius_cxcli.grafana_api import GrafanaAuth, GrafanaClient, GrafanaError


class FakeGrafana(GrafanaClient):
    def __init__(self):
        super().__init__("https://grafana.example.invalid/prefix/", GrafanaAuth("Bearer fixture"))
        self.namespace = "default"
        self.resources = {}
        self.calls = []
        self.writes = 0
        self.fail_uid = None
        self.lose_response = False
        self.inventory = [
            {"uid": "metrics", "name": "Metrics", "type": "prometheus"},
            {"uid": "logs", "name": "Logs", "type": "loki"},
        ]

    def request(self, method, path, payload=None):
        self.assert_authority()
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == "api/frontend/settings/":
            return {"namespace": "default"}
        if path == "api/datasources":
            return copy.deepcopy(self.inventory)
        parts = urlsplit(path)
        if "/folders/" in parts.path:
            return {
                "metadata": {"name": parts.path.split("/")[-1]},
                "spec": {"title": "Selected folder"},
            }
        uid = parts.path.split("/")[-1]
        if method == "GET":
            if uid == "dashboards":
                return {"items": copy.deepcopy(list(self.resources.values())), "metadata": {}}
            if uid not in self.resources:
                raise GrafanaError("not found", status=404)
            return copy.deepcopy(self.resources[uid])
        desired = copy.deepcopy(payload)
        uid = desired["metadata"]["name"]
        desired["spec"]["schemaVersion"] = 42
        desired["spec"].setdefault("panels", [])
        if parse_qs(parts.query).get("dryRun") == ["All"]:
            assert method == "POST", "PUT dry-run emits saved events"
            assert uid not in self.resources
            return desired
        if uid == self.fail_uid:
            raise GrafanaError("fixture denied", status=403)
        existing = self.resources.get(uid)
        if method == "POST" and existing is not None:
            raise GrafanaError("conflict", status=409)
        if method == "PUT" and (
            existing is None
            or desired["metadata"].get("resourceVersion")
            != existing["metadata"].get("resourceVersion")
        ):
            raise GrafanaError("conflict", status=409)
        self.writes += 1
        desired["metadata"]["resourceVersion"] = str(self.writes)
        self.resources[uid] = desired
        if self.lose_response:
            self.lose_response = False
            raise GrafanaError("fixture response lost")
        return copy.deepcopy(desired)


def dashboard(uid="board", **fields):
    return {"uid": uid, "title": "GPU dashboard", "panels": [], "schemaVersion": 30, **fields}
