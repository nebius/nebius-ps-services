"""Explicit, destination-bound Grafana dashboard v1 API transport."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import uuid
from base64 import b64encode
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .grafana_dashboards import MAX_JSON_BYTES, dashboard_uid, json_bytes, normalize_dashboard


class GrafanaError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class DashboardOwnership:
    """Admitted management identity, separate from content and mutable versions."""

    manager_kind: str = ""
    manager_id: str = ""
    allows_edits: str = ""
    folder: str = ""

    @property
    def managed(self) -> bool:
        return bool(self.manager_kind)

    def assert_current(self, resource: Mapping[str, Any] | None) -> None:
        if self != GrafanaClient.admit_update(resource, overwrite=self.managed):
            raise GrafanaError("Dashboard ownership or folder changed during import")

    def fingerprint(self, identity: Mapping[str, Any], namespace: str, uid: str) -> str:
        return hashlib.sha256(
            json_bytes(
                {
                    "destination": dict(identity),
                    "namespace": namespace,
                    "uid": uid,
                    "manager_kind": self.manager_kind,
                    "manager_id": self.manager_id,
                    "allows_edits": self.allows_edits,
                    "folder": self.folder,
                }
            )
        ).hexdigest()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_host(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return hostname.lower() == "localhost"


@dataclass(frozen=True)
class GrafanaAuth:
    header: str = field(repr=False)


def token_auth(name: str) -> GrafanaAuth:
    if not name or not name.replace("_", "a").isalnum():
        raise ValueError("Supply --token-env with an environment variable name")
    token = os.environ.get(name, "").strip()
    if not token or any(c in token for c in "\r\n"):
        raise ValueError("Selected Grafana token environment variable is empty or invalid")
    return GrafanaAuth(f"Bearer {token}")


def basic_auth(username: str, password: str) -> GrafanaAuth:
    if not username or not password or ":" in username:
        raise ValueError("Grafana Secret requires valid username and password keys")
    return GrafanaAuth("Basic " + b64encode(f"{username}:{password}".encode()).decode())


def normalize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise ValueError("Invalid Grafana URL") from None
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use a Grafana base URL without credentials, query or fragment")
    loopback = loopback_host(hostname)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("Grafana requires HTTPS (HTTP is permitted only for loopback)")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid Grafana URL port")
    if (
        any(segment in {".", ".."} for segment in parsed.path.split("/"))
        or "\\" in value
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Invalid Grafana URL path")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") + "/", "", ""))


class GrafanaClient:
    def __init__(
        self,
        url: str,
        auth: GrafanaAuth,
        *,
        org_id: int | None = None,
        assert_authority: Callable[[], object] = lambda: None,
    ) -> None:
        self.url = normalize_url(url)
        self.auth = auth
        self.org_id = org_id
        self.namespace = ""
        self.assert_authority = assert_authority
        # A cluster Secret must never leave the identity-bound local tunnel,
        # even on a laptop with system/environment proxy configuration.
        proxies = (
            ProxyHandler({}) if loopback_host(urlsplit(self.url).hostname or "") else ProxyHandler()
        )
        self.opener = build_opener(NoRedirects(), proxies)

    def request(
        self,
        method: str,
        path: str,
        payload: object = None,
        *,
        ndjson: bool = False,
        timeout_seconds: float = 30,
    ) -> Any:
        if path.startswith(("/", "http:", "https:")) or ".." in path.split("/"):
            raise ValueError("Grafana API paths must be relative to the selected destination")
        self.assert_authority()
        headers = {"Accept": "application/json", "Authorization": self.auth.header}
        if self.org_id is not None:
            headers["X-Grafana-Org-Id"] = str(self.org_id)
        data = None
        if payload is not None:
            data = json_bytes(payload)
            headers["Content-Type"] = "application/json"
        req = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=min(30, max(0.1, timeout_seconds))) as response:
                data = response.read(MAX_JSON_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            detail = {
                401: "authentication rejected",
                403: "permission denied",
                409: "concurrent dashboard change",
                412: "dashboard version conflict",
            }.get(status, "request rejected")
            raise GrafanaError(f"Grafana API {detail} (HTTP {status})", status=status) from None
        except (URLError, OSError, TimeoutError):
            raise GrafanaError(
                "Grafana API transport failed; a write may need readback before retry"
            ) from None
        self.assert_authority()
        if len(data) > MAX_JSON_BYTES:
            raise GrafanaError("Grafana API response exceeds the 20 MiB limit")
        try:
            result = (
                [json.loads(line) for line in data.splitlines() if line.strip()]
                if ndjson
                else json.loads(data)
            )
        except (ValueError, UnicodeError):
            raise GrafanaError(
                "Grafana API did not return JSON; browser SSO is not API authentication"
            ) from None
        self.check_conversion(result)
        return result

    @staticmethod
    def check_conversion(value: object) -> None:
        status = value.get("status") if isinstance(value, dict) else None
        conversion = status.get("conversion") if isinstance(status, dict) else None
        if isinstance(conversion, dict) and conversion.get("failed"):
            raise GrafanaError("Grafana could not convert the dashboard schema")

    def connect(self) -> None:
        settings = self.request("GET", "api/frontend/settings/")
        namespace = settings.get("namespace") if isinstance(settings, dict) else None
        if (
            not isinstance(namespace, str)
            or not namespace
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in namespace
            )
        ):
            raise GrafanaError("Grafana did not expose its authenticated API namespace")
        self.namespace = namespace
        try:
            response = self.request("GET", self.dashboard_path() + "?limit=1")
        except GrafanaError as exc:
            if exc.status == 404:
                raise GrafanaError(
                    "Grafana dashboard.grafana.app/v1 is required (qualified with Grafana 13.0.1)"
                ) from None
            raise
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise GrafanaError("Grafana does not provide the required dashboard v1 API")

    def dashboard_path(self, uid: str = "") -> str:
        if not self.namespace:
            raise GrafanaError("Connect to Grafana before accessing dashboards")
        suffix = "/" + quote(dashboard_uid(uid), safe="") if uid else ""
        return f"apis/dashboard.grafana.app/v1/namespaces/{self.namespace}/dashboards{suffix}"

    def get(self, uid: str) -> dict[str, Any] | None:
        try:
            result = self.request("GET", self.dashboard_path(uid))
        except GrafanaError as exc:
            if exc.status == 404:
                return None
            raise
        self.validate_resource(result, uid)
        return result

    @staticmethod
    def validate_resource(value: Any, uid: str | None = None) -> None:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("metadata"), dict)
            or not isinstance(value.get("spec"), dict)
        ):
            raise GrafanaError("Invalid Grafana dashboard resource")
        if uid and value["metadata"].get("name") != uid:
            raise GrafanaError("Grafana returned a different dashboard identity")
        GrafanaClient.check_conversion(value)

    @staticmethod
    def assert_unmanaged(resource: Mapping[str, Any]) -> None:
        GrafanaClient.admit_update(resource, overwrite=False)

    @staticmethod
    def admit_update(resource: Mapping[str, Any] | None, *, overwrite: bool) -> DashboardOwnership:
        if resource is None:
            return DashboardOwnership()
        annotations = resource.get("metadata", {}).get("annotations", {})
        if not isinstance(annotations, Mapping):
            raise GrafanaError("Invalid Grafana dashboard management metadata")
        kind = annotations.get("grafana.app/managedBy")
        manager = annotations.get("grafana.app/managerId")
        if kind in (None, "") and manager in (None, ""):
            return DashboardOwnership()
        if not overwrite:
            raise GrafanaError(
                "Dashboard is provisioned or managed; use --overwrite for editable file "
                "provisioning, update its provisioning source, or import a separate copy with a new UID."
            )
        if (
            kind != "classic-file-provisioning"
            or not isinstance(manager, str)
            or not manager.strip()
        ):
            raise GrafanaError(
                "Dashboard is provisioned or managed by an unsupported owner; "
                "only identified classic file provisioning supports --overwrite"
            )
        if annotations.get("grafana.app/managerAllowsEdits") != "true":
            raise GrafanaError(
                "Provisioned dashboard does not explicitly allow edits; "
                "its provisioning source must enable allowUiUpdates"
            )
        return DashboardOwnership(kind, manager, "true", GrafanaClient.folder(resource))

    @staticmethod
    def portable(resource: dict[str, Any]) -> dict[str, Any]:
        GrafanaClient.validate_resource(resource)
        return normalize_dashboard({"apiVersion": "dashboard.grafana.app/v1", **resource})

    @staticmethod
    def folder(resource: Mapping[str, Any]) -> str:
        return str(
            resource.get("metadata", {}).get("annotations", {}).get("grafana.app/folder") or ""
        )

    def folder_title(self, uid: str) -> str:
        if not uid:
            return ""
        dashboard_uid(uid)
        result = self.request(
            "GET",
            f"apis/folder.grafana.app/v1/namespaces/{self.namespace}/folders/{quote(uid, safe='')}",
        )
        if not isinstance(result, dict) or result.get("metadata", {}).get("name") != uid:
            raise GrafanaError("Grafana returned a different folder identity")
        return str(result.get("spec", {}).get("title") or uid)

    def datasources(self) -> list[dict[str, Any]]:
        result = self.request("GET", "api/datasources")
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise GrafanaError("Grafana datasource inventory is invalid")
        return result

    def dashboards(self) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        token = ""
        while True:
            result = self.request(
                "GET",
                self.dashboard_path()
                + "?"
                + urlencode({"limit": 100, **({"continue": token} if token else {})}),
            )
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                raise GrafanaError("Grafana dashboard listing is invalid")
            for item in result["items"]:
                self.validate_resource(item)
                yield item
            token = str(result.get("metadata", {}).get("continue") or "")
            if not token:
                return
            if token in seen:
                raise GrafanaError("Grafana dashboard pagination repeated a continuation token")
            seen.add(token)

    def canonical(self, dashboard: dict[str, Any], folder: str) -> dict[str, Any]:
        """POST dry-run performs schema migration without storing a dashboard.

        Do not use PUT dry-run: Grafana 13.0.1 emits a saved event on that path.
        A disposable name also avoids collisions with an existing dashboard UID.
        """
        candidate = copy.deepcopy(dashboard)
        uid = candidate.pop("uid")
        result = self.request(
            "POST",
            self.dashboard_path() + "?dryRun=All&fieldValidation=Strict",
            {
                "apiVersion": "dashboard.grafana.app/v1",
                "kind": "Dashboard",
                "metadata": {
                    "name": "cxcli-preview-" + uuid.uuid4().hex[:24],
                    "annotations": {"grafana.app/folder": folder},
                },
                "spec": candidate,
            },
        )
        self.validate_resource(result)
        result["metadata"]["name"] = uid
        return self.portable(result)

    def write(
        self,
        dashboard: dict[str, Any],
        folder: str,
        previous: dict[str, Any] | None,
        *,
        ownership: DashboardOwnership | None = None,
    ) -> dict[str, Any]:
        ownership = ownership or self.admit_update(previous, overwrite=False)
        ownership.assert_current(previous)
        if ownership.managed and folder != ownership.folder:
            raise GrafanaError("A managed dashboard must keep its provisioned folder")
        uid = dashboard_uid(dashboard.get("uid"))
        spec = copy.deepcopy(dashboard)
        for key in ("uid", "id", "version"):
            spec.pop(key, None)
        metadata: dict[str, Any] = {"name": uid, "annotations": {"grafana.app/folder": folder}}
        if previous is not None:
            version = previous["metadata"].get("resourceVersion")
            if not isinstance(version, str) or not version:
                raise GrafanaError("Grafana did not supply a resourceVersion for a guarded update")
            metadata = copy.deepcopy(previous["metadata"])
            metadata["resourceVersion"] = version
            metadata["annotations"] = {
                **previous["metadata"].get("annotations", {}),
                "grafana.app/folder": folder,
            }
        params = {"fieldValidation": "Strict"}
        result = self.request(
            "PUT" if previous else "POST",
            self.dashboard_path(uid if previous else "") + "?" + urlencode(params),
            {
                "apiVersion": "dashboard.grafana.app/v1",
                "kind": "Dashboard",
                "metadata": metadata,
                "spec": spec,
            },
        )
        self.validate_resource(result, uid)
        return result
