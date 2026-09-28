"""Prove a failed initial PostgreSQL install without changing Helm or credentials."""

from __future__ import annotations

import base64
import gzip
import io
import json
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from .grafana_database import OWNER_ANNOTATION

_MAX_RELEASE_BYTES = 8 * 1024 * 1024
_MAX_STORAGE_BYTES = 2 * 1024 * 1024


def failed_initial_documents(
    releases: Sequence[Mapping[str, Any]],
    storage: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
    name: str,
    owner: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return only the owned workload and consumed config from revision one."""
    message = "Cannot prove Grafana's failed initial PostgreSQL install; no automatic migration"

    def require(condition: bool) -> None:
        if not condition:
            raise ValueError

    try:
        require(len(releases) == len(storage) == 1)
        hr, secret = releases[0], storage[0]
        meta, spec, status = hr["metadata"], hr["spec"], hr["status"]
        require(
            meta.get("name") == name
            and meta.get("namespace") == namespace
            and bool(meta.get("uid"))
            and not meta.get("deletionTimestamp")
            and meta.get("annotations", {}).get(OWNER_ANNOTATION) == owner
            and status.get("observedGeneration") == meta.get("generation")
            and bool(meta.get("generation"))
            and not spec.get("suspend")
            and not spec.get("valuesFrom")
            and spec.get("releaseName", name) == name
            and spec.get("targetNamespace", namespace) == namespace
            and spec.get("storageNamespace", namespace) == namespace
            and status.get("lastAttemptedReleaseAction") == "install"
        )
        conditions = {item["type"]: item for item in status["conditions"]}
        require(len(conditions) == len(status["conditions"]))
        for kind, truth, reason in (
            ("Stalled", "True", "RetriesExceeded"),
            ("Ready", "False", "InstallFailed"),
            ("Released", "False", "InstallFailed"),
        ):
            require(
                conditions.get(kind, {}).get("status") == truth
                and conditions[kind].get("reason") == reason
                and conditions[kind].get("observedGeneration") == meta["generation"]
            )
        require(conditions.get("Reconciling", {}).get("status") != "True")
        history = status["history"]
        require(len(history) == 1)
        row = history[0]
        require(
            all(
                row.get(k) == v
                for k, v in {
                    "name": name,
                    "namespace": namespace,
                    "version": 1,
                    "status": "failed",
                    "action": "install",
                    "chartName": "grafana",
                }.items()
            )
            and bool(row.get("chartVersion"))
            and row["chartVersion"] == status.get("lastAttemptedRevision")
            and bool(row.get("configDigest"))
            and row["configDigest"] == status.get("lastAttemptedConfigDigest")
        )
        stored_meta = secret["metadata"]
        require(
            secret.get("type") == "helm.sh/release.v1"
            and stored_meta.get("name") == f"sh.helm.release.v1.{name}.v1"
            and stored_meta.get("namespace") == namespace
            and bool(stored_meta.get("uid"))
            and not stored_meta.get("deletionTimestamp")
            and all(
                stored_meta.get("labels", {}).get(k) == v
                for k, v in {
                    "owner": "helm",
                    "name": name,
                    "version": "1",
                    "status": "failed",
                }.items()
            )
        )
        encoded = secret["data"]["release"]
        require(isinstance(encoded, str) and len(encoded) <= _MAX_STORAGE_BYTES)
        compressed = base64.b64decode(base64.b64decode(encoded, validate=True), validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(_MAX_RELEASE_BYTES + 1)
        require(len(raw) <= _MAX_RELEASE_BYTES)
        release = json.loads(raw)
        require(
            release.get("name") == name
            and release.get("namespace") == namespace
            and release.get("version") == 1
            and release.get("info", {}).get("status") == "failed"
            and release.get("chart", {}).get("metadata", {}).get("name") == "grafana"
            and release["chart"]["metadata"].get("version") == row["chartVersion"]
            and release.get("config") == spec.get("values")
        )
        documents = []
        for index, document in enumerate(yaml.safe_load_all(release["manifest"])):
            require(index < 200)
            if document is not None:
                require(isinstance(document, dict))
                documents.append(document)
        workloads = [
            d
            for d in documents
            if d.get("kind") in {"Deployment", "StatefulSet", "Pod", "DaemonSet", "Job", "CronJob"}
        ]
        configs = [
            d
            for d in documents
            if d.get("kind") == "ConfigMap" and d.get("metadata", {}).get("name") == name
        ]
        require(len(workloads) == len(configs) == 1 and workloads[0].get("kind") == "Deployment")
        require(
            workloads[0].get("apiVersion") == "apps/v1" and configs[0].get("apiVersion") == "v1"
        )
        for document in (workloads[0], configs[0]):
            identity = document["metadata"]
            require(
                identity.get("name") == name
                and identity.get("namespace") == namespace
                and identity.get("annotations", {}).get(OWNER_ANNOTATION) == owner
                and identity.get("labels", {}).get("app.kubernetes.io/instance") == name
            )
        return workloads[0], configs[0]
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        OSError,
        EOFError,
        zlib.error,
        yaml.YAMLError,
    ):
        raise RuntimeError(message) from None
