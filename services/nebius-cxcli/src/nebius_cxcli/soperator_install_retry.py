"""One native retry of an authenticated, corrected initial collector release."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .soperator_jail_logs_binding import JAIL_LOGS_RELEASE

_REQUEST = "reconcile.fluxcd.io/requestedAt"
_FORCE = "reconcile.fluxcd.io/forceAt"


def install_retry_pending(payload: Mapping[str, Any], token: str | None) -> bool:
    """Only a request issued by this staged apply can defer stale failure status."""
    if not token:
        return False
    annotations = payload.get("metadata", {}).get("annotations", {})
    status = payload.get("status", {})
    return (
        annotations.get(_REQUEST) == annotations.get(_FORCE) == token
        and status.get("lastHandledForceAt") != token
    )


def collector_install_retry_patch(
    payload: Mapping[str, Any],
    expected: Mapping[str, str],
    deployment: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]] | None:
    """Build a CAS annotation patch, never changing release values or strategy."""
    metadata, spec, status = (payload.get(key, {}) for key in ("metadata", "spec", "status"))
    conditions = status.get("conditions") or []
    if not any(
        c.get("type") == "Stalled"
        and c.get("status") == "True"
        and c.get("reason") == "MissingRollbackTarget"
        for c in conditions
    ):
        return None
    generation = metadata.get("generation")
    history = status.get("history") or []
    digest = status.get("lastAttemptedConfigDigest")
    if (
        expected.get("sourceKind") != "HelmChart"
        or expected.get("name") != "cxcli-" + JAIL_LOGS_RELEASE
        or any(metadata.get(k) != expected.get(k) for k in ("name", "namespace", "uid"))
        or not expected.get("uid")
        or not metadata.get("resourceVersion")
        or metadata.get("deletionTimestamp")
        or type(generation) is not int
        or status.get("observedGeneration") != generation
        or spec.get("suspend") is not True
        or spec.get("chartRef")
        != {
            "kind": "HelmChart",
            "name": expected.get("sourceName"),
            "namespace": expected.get("sourceNamespace"),
        }
        or status.get("lastAttemptedRevision") != expected.get("sourceVersion")
        or status.get("lastAttemptedReleaseAction") != "upgrade"
        or not isinstance(digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        or not history
        or history[0].get("status") != "failed"
        or history[0].get("action") != "upgrade"
        or history[0].get("configDigest") != digest
        or history[0].get("chartVersion") != expected.get("sourceVersion")
        or any(h.get("status") in {"deployed", "superseded"} for h in history)
        or any(c.get("type") == "Reconciling" and c.get("status") == "True" for c in conditions)
        or not all(
            any(
                c.get("type") == kind
                and c.get("status") == "False"
                and c.get("reason") == "UpgradeFailed"
                for c in conditions
            )
            for kind in ("Ready", "Released")
        )
    ):
        raise RuntimeError("Collector retry lost its exact quiescent failed release")
    dm, ds, ready = (deployment.get(key, {}) for key in ("metadata", "spec", "status"))
    replicas = ds.get("replicas", 1)
    owner = dm.get("annotations", {})
    if (
        dm.get("name") != spec.get("releaseName")
        or dm.get("namespace") != spec.get("targetNamespace")
        or not dm.get("uid")
        or dm.get("deletionTimestamp")
        or owner.get("meta.helm.sh/release-name") != spec.get("releaseName")
        or owner.get("meta.helm.sh/release-namespace") != spec.get("targetNamespace")
        or type(dm.get("generation")) is not int
        or ready.get("observedGeneration") != dm["generation"]
        or type(replicas) is not int
        or replicas < 1
        or any(
            ready.get(k) != replicas
            for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")
        )
        or not all(
            any(
                c.get("type") == kind and c.get("status") == "True"
                for c in ready.get("conditions", [])
            )
            for kind in ("Available", "Progressing")
        )
    ):
        raise RuntimeError("Collector retry requires its current healthy owned Deployment")
    token = (
        "cxcli-install-retry-"
        + hashlib.sha256(
            json.dumps([expected["uid"], expected["sourceDigest"], digest]).encode()
        ).hexdigest()[:32]
    )
    if status.get("lastHandledForceAt") == token:
        return None
    if install_retry_pending(payload, token):
        return token, []
    annotations = dict(metadata.get("annotations") or {})
    for key, handled in ((_REQUEST, "lastHandledReconcileAt"), (_FORCE, "lastHandledForceAt")):
        if annotations.get(key) and annotations[key] != status.get(handled):
            raise RuntimeError("Collector retry cannot replace another pending native request")
    annotations.update({_REQUEST: token, _FORCE: token})
    return token, [
        {"op": "test", "path": "/metadata/uid", "value": expected["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {"op": "test", "path": "/spec", "value": spec},
        {"op": "add", "path": "/metadata/annotations", "value": annotations},
    ]
