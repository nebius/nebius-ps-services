"""Read durable native passive-check evidence for an initial worker storage failure."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_policy import checks_digest


def _manager(pod: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = [
        s for s in pod.get("status", {}).get("containerStatuses", []) if s.get("name") == "manager"
    ]
    if len(rows) != 1 or rows[0].get("restartCount") != 0 or not rows[0].get("imageID"):
        raise RuntimeError("storage cause lost the original native controller container")
    return rows[0]


def storage_cause_from_logs(
    text: str, *, worker: str, started: datetime, terminated: datetime
) -> Mapping[str, Any]:
    if len(text.encode()) >= 1024 * 1024:
        raise RuntimeError("storage cause logs exceeded their bounded read")
    matches = []
    for line in text.splitlines():
        stamp, separator, body = line.partition(" ")
        if not separator:
            continue
        try:
            when = datetime.fromisoformat(stamp.removesuffix("Z"))
        except ValueError:
            continue
        if not max(started, terminated - timedelta(seconds=10)) <= when < terminated:
            continue
        try:
            row = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        percentage = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", str(row.get("usagePercent", "")))
        if (
            row.get("msg") == "High ephemeral storage usage detected"
            and row.get("logger") == "soperatorchecks.pod-ephemeral-storage-check"
            and row.get("controller") == "soperatorchecks.pod-ephemeral-storage-check"
            and row.get("Pod") == {"name": worker, "namespace": "soperator"}
            and row.get("pod") == worker
            and row.get("namespace") == "soperator"
            and row.get("reconcileID")
            and percentage
            and float(percentage[1]) > 100
        ):
            matches.append((when, row))
    if not matches:
        raise RuntimeError(
            "storage repair lacks native evidence that the worker exceeded its storage limit"
        )
    when, row = max(matches, key=lambda item: item[0])
    return {
        "time": when.isoformat(),
        "recordSha256": checks_digest(row),
        "usagePercent": row["usagePercent"],
        "reconcileId": row["reconcileID"],
    }


def capture_storage_cause(
    runner: SoperatorChecksExecution,
    failure: Mapping[str, Any],
    *,
    source_dir: Path,
    values: Mapping[str, Any],
    env: Mapping[str, str],
    kube_context: str,
) -> Mapping[str, Any]:
    defaults = yaml.safe_load((source_dir / "helm/soperator-fluxcd/values.yaml").read_text())[
        "soperator"
    ]
    configured = values.get("soperator", {})
    settings = configured.get("soperatorChecks", {})
    namespace = configured.get("namespace", defaults["namespace"])
    release = settings.get("releaseName", defaults["soperatorChecks"]["releaseName"])
    native_image = yaml.safe_load((source_dir / "helm/soperatorchecks/values.yaml").read_text())[
        "checks"
    ]["manager"]["image"]
    image = native_image["repository"] + ":" + str(native_image["tag"])
    deployments = runner._get("deployments", namespace=namespace).get("items", [])
    deployments = [
        d
        for d in deployments
        if d.get("metadata", {}).get("annotations", {}).get("meta.helm.sh/release-name") == release
        and d["metadata"]["annotations"].get("meta.helm.sh/release-namespace") == namespace
    ]
    if len(deployments) != 1 or not deployments[0]["metadata"].get("uid"):
        raise RuntimeError("storage cause requires the exact upstream checks controller")
    deployment = deployments[0]
    managers = [
        c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "manager"
    ]
    if len(managers) != 1 or managers[0].get("image") != image:
        raise RuntimeError("storage cause controller is not the pinned upstream implementation")
    labels = deployment["spec"]["selector"]["matchLabels"]
    pods = runner.kube(
        [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            ",".join(f"{k}={v}" for k, v in sorted(labels.items())),
            "-o",
            "json",
        ],
        None,
    ).get("items", [])
    if len(pods) != 1:
        raise RuntimeError("storage cause controller pod identity is ambiguous")
    pod = pods[0]
    pod_managers = [
        c for c in pod.get("spec", {}).get("containers", []) if c.get("name") == "manager"
    ]
    if len(pod_managers) != 1 or pod_managers[0].get("image") != image:
        raise RuntimeError("storage cause pod is not running the pinned upstream implementation")
    meta = pod.get("metadata", {})
    owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
    if (
        not meta.get("uid")
        or meta.get("deletionTimestamp")
        or len(owners) != 1
        or owners[0].get("kind") != "ReplicaSet"
    ):
        raise RuntimeError("storage cause controller pod lost native ownership")
    replica = runner._get("replicaset", owners[0]["name"], namespace=namespace)
    parents = [
        o
        for o in replica.get("metadata", {}).get("ownerReferences", [])
        if o.get("controller") is True
    ]
    if (
        replica.get("metadata", {}).get("uid") != owners[0]["uid"]
        or len(parents) != 1
        or parents[0].get("uid") != deployment["metadata"]["uid"]
        or parents[0].get("kind") != "Deployment"
    ):
        raise RuntimeError("storage cause controller lost deployment lineage")
    manager = _manager(pod)
    terminated = datetime.fromisoformat(failure["eviction"]["time"])
    started = datetime.fromisoformat(failure["executionInterval"]["start"])
    if datetime.fromisoformat(manager["state"]["running"]["startedAt"].removesuffix("Z")) > started:
        raise RuntimeError("storage cause controller did not exist during the failure")
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "logs",
            _identifier(meta["name"]),
            "-c",
            "manager",
            "--since-time=" + max(started, terminated - timedelta(seconds=10)).isoformat() + "Z",
            "--timestamps",
            "--limit-bytes=1048576",
        ],
        env=dict(env),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    cause = storage_cause_from_logs(
        result.stdout, worker=failure["worker"]["name"], started=started, terminated=terminated
    )
    current = runner._get("pod", meta["name"], namespace=namespace)
    if current.get("metadata", {}).get("uid") != meta["uid"] or _manager(current) != manager:
        raise RuntimeError("storage cause controller changed during observation")
    return {
        **cause,
        "podUid": meta["uid"],
        "deploymentUid": deployment["metadata"]["uid"],
        "imageId": manager["imageID"],
    }
