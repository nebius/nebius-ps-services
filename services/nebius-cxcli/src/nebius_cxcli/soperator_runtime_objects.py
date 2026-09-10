"""Create-only, lease-guarded runtime objects; never persist sensitive manifests."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence

import yaml

from .soperator_values import validate_object_name


class RuntimeObjects:
    def __init__(
        self, *, extra_env: Mapping[str, str] | None, assert_authority: Callable[[], object]
    ) -> None:
        self.env = {**os.environ, **(extra_env or {})}
        self.context = str((extra_env or {}).get("NEBIUS_CXCLI_TARGET_KUBE_CONTEXT") or "")
        if not self.context:
            raise RuntimeError(
                "Soperator runtime objects require an explicitly bound target context"
            )
        self.assert_authority = assert_authority

    def _run(self, args: Sequence[str], *, manifest: Mapping | None = None) -> str:
        command = ["kubectl", "--context", self.context, *args]
        try:
            rendered = yaml.safe_dump(dict(manifest), sort_keys=False) if manifest else None
            if manifest is not None:
                self.assert_authority()
            result = subprocess.run(
                command, env=self.env, input=rendered, capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("Soperator runtime object command failed") from None
        if result.returncode:
            # Admission errors and timeout output can echo the entire Secret.
            raise RuntimeError(
                "Soperator runtime object command failed; no existing object was overwritten"
            )
        return result.stdout

    def complete(self, kind: str, namespace: str, name: str, keys: Sequence[str]) -> bool:
        validate_object_name(namespace, "runtime namespace")
        validate_object_name(name, "runtime object name")
        raw = self._run(["-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"])
        if not raw.strip():
            return False
        try:
            obj = json.loads(raw)
        except ValueError:
            raise RuntimeError("Soperator runtime object lookup returned invalid JSON") from None
        data = obj.get("data") if isinstance(obj, Mapping) else None
        if not isinstance(data, Mapping) or any(not data.get(key) for key in keys):
            raise RuntimeError(
                f"Existing Soperator {kind} {namespace}/{name} is incomplete; repair it explicitly"
            )
        return True

    def create(self, kind: str, namespace: str, name: str, data: Mapping[str, str]) -> None:
        # Namespace creation is also a mutation and uses the same lease assertion.
        raw = self._run(["get", "namespace", namespace, "--ignore-not-found", "-o", "name"])
        if not raw.strip():
            self._run(
                ["create", "-f", "-"],
                manifest={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
            )
        manifest = {
            "apiVersion": "v1",
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
            "stringData" if kind == "Secret" else "data": dict(data),
        }
        if kind == "Secret":
            manifest["type"] = "Opaque"
        # Create fails on a race; apply would silently rotate credentials.
        self._run(["create", "-f", "-"], manifest=manifest)
        if not self.complete(kind, namespace, name, tuple(data)):
            raise RuntimeError("Soperator runtime object was not present after creation")
