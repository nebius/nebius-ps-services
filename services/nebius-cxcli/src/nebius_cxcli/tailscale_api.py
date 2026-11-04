"""Bounded Tailscale API adapter. No arbitrary API endpoints, redirects or proxying."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .secretstash import oauth_pair

BASE = "https://api.tailscale.com/api/v2"
MAX_BYTES = 4 * 1024 * 1024


class ApiError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(
            f"Tailscale API returned HTTP {status}; check delegated permissions and retry status before mutation"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class TailscaleAPI:
    def __init__(self, config: dict[str, Any], *, base_dir: Path) -> None:
        self.config, self.base_dir = config, base_dir
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.token = ""
        self.expiry = 0.0
        self._credential_binding: dict[str, str] | None = None

    def bind_credential(self, binding: dict[str, str]) -> None:
        if not binding:
            raise RuntimeError("Original OAuth credential binding is unavailable")
        if binding != self._credential_binding:
            self.token, self.expiry = "", 0.0
            self._credential_binding = dict(binding)

    def credential_binding(self) -> dict[str, str]:
        # New operations select the configured version/primary. Recovery binds
        # an existing operation separately, before any key lookup or deletion.
        _, _, binding = oauth_pair(self.config.get("auth", {}), base=self.base_dir)
        self.bind_credential(binding)
        return dict(binding)

    def _http(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> tuple[bytes, Any]:
        request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise RuntimeError("Tailscale API response exceeds the size bound")
                return raw, response.headers
        except urllib.error.HTTPError as error:
            raise ApiError(error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError(
                "Tailscale API outcome unavailable; reconcile before retrying a write"
            ) from None

    def authenticate(self) -> None:
        if self.token and self.expiry > time.monotonic() + 60:
            return
        client_id, client_secret, binding = oauth_pair(
            self.config.get("auth", {}),
            base=self.base_dir,
            binding=self._credential_binding,
        )
        self._credential_binding = binding
        self.authenticate_pair(client_id, client_secret)

    def authenticate_pair(self, client_id: str, client_secret: str) -> None:
        """Validate an in-memory import pair without rereading its source file."""
        payload = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            }
        ).encode()
        raw, _ = self._http(
            "POST", "/oauth/token", payload, {"Content-Type": "application/x-www-form-urlencoded"}
        )
        data = json.loads(raw)
        self.token = data["access_token"]
        self.expiry = time.monotonic() + min(int(data["expires_in"]), 3600)

    def request(
        self, method: str, path: str, value: Any = None, *, raw: bool = False, etag: str = ""
    ) -> tuple[Any, str]:
        self.authenticate()
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/hujson" if raw else "application/json",
        }
        if etag:
            headers["If-Match"] = '"' + etag.strip('"') + '"'
        body = (
            value.encode()
            if raw and isinstance(value, str)
            else json.dumps(value).encode()
            if value is not None
            else None
        )
        content, response = self._http(method, path, body, headers)
        return (content.decode() if raw else json.loads(content) if content else {}), response.get(
            "ETag", ""
        )

    @property
    def tailnet(self) -> str:
        return "/tailnet/" + urllib.parse.quote(self.config["tailnet"], safe="")

    def key(self, operation_id: str) -> tuple[str, str]:
        data, _ = self.request(
            "POST",
            self.tailnet + "/keys",
            {
                "capabilities": {
                    "devices": {
                        "create": {
                            "reusable": False,
                            "ephemeral": False,
                            "preauthorized": True,
                            "tags": self.config["tags"],
                        }
                    }
                },
                "expirySeconds": 3600,
                "description": "cxcli enrollment " + operation_id,
            },
        )
        return data["id"], data["key"]

    def key_info(self, key_id: str) -> dict[str, Any] | None:
        try:
            return self.request(
                "GET", self.tailnet + "/keys/" + urllib.parse.quote(key_id, safe="")
            )[0]
        except ApiError as error:
            if error.status == 404:
                return None
            raise

    def revoke_key(self, key_id: str) -> None:
        if self.key_info(key_id) is None:
            return
        try:
            self.request("DELETE", self.tailnet + "/keys/" + urllib.parse.quote(key_id, safe=""))
        except RuntimeError:
            # A failed acknowledgement is not a failed deletion. Read back before retry.
            if self.key_info(key_id) is None:
                return
            raise
        if self.key_info(key_id) is not None:
            raise RuntimeError("Enrollment key revocation is not confirmed")

    def device(self, node_id: str) -> dict[str, Any]:
        return self.request("GET", "/device/" + urllib.parse.quote(node_id, safe=""))[0]

    def routes(self, node_id: str) -> dict[str, Any]:
        return self.request("GET", "/device/" + urllib.parse.quote(node_id, safe="") + "/routes")[0]

    def approve(self, node_id: str, routes: list[str]) -> dict[str, Any]:
        before = self.routes(node_id)
        if set(before.get("advertisedRoutes", [])) - set(routes) or set(
            before.get("enabledRoutes", [])
        ) - set(routes):
            raise RuntimeError(
                "Device routes differ from the owned intent; no route approval was changed"
            )
        if set(before.get("advertisedRoutes", [])) != set(routes):
            return before  # Advertisement has not reached the control plane yet.
        if set(before.get("enabledRoutes", [])) != set(routes):
            self.request(
                "POST",
                "/device/" + urllib.parse.quote(node_id, safe="") + "/routes",
                {"routes": routes},
            )
        after = self.routes(node_id)
        return after

    def delete_owned(self, node_id: str, tags: list[str]) -> None:
        try:
            device = self.device(node_id)
        except ApiError as error:
            if error.status == 404:
                return
            raise
        if node_id not in {device.get("id"), device.get("nodeId")} or set(
            device.get("tags", [])
        ) != set(tags):
            raise RuntimeError("Device retirement identity mismatch")
        self.request("DELETE", "/device/" + urllib.parse.quote(node_id, safe=""))
        try:
            self.device(node_id)
        except ApiError as error:
            if error.status == 404:
                return
            raise
        raise RuntimeError("Device retirement is not confirmed; retry status before destruction")
