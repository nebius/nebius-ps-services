"""Bounded, read-only native kubelet evidence for an evicted GPU worker."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .soperator_checks import _identifier

JournalReader = Callable[[str, datetime, datetime], Sequence[Mapping[str, Any]]]


def read_worker_journal(
    worker: str,
    since: datetime,
    until: datetime,
    *,
    env: Mapping[str, str],
    kube_context: str,
) -> list[Mapping[str, Any]]:
    worker = _identifier(worker)
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            "soperator",
            "exec",
            worker,
            "-c",
            "slurmd",
            "--",
            "env",
            "TZ=UTC",
            "journalctl",
            "--directory=/run/nvidia/driver/var/log/journal",
            "--unit=kubelet",
            "--since=" + since.isoformat(),
            "--until=" + until.isoformat(),
            "--grep=" + re.escape('pod="soperator/' + worker + '"'),
            "--lines=201",
            "--no-pager",
            "--output=json",
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    if len(result.stdout.encode()) > 1024 * 1024:
        raise RuntimeError("worker eviction journal exceeded its bounded read")
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(rows) > 200 or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("worker eviction journal is incomplete or truncated")
    return rows


def capture_worker_eviction(
    pod: Mapping[str, Any],
    node: Mapping[str, Any],
    *,
    started: datetime,
    ended: datetime | None,
    read: JournalReader,
) -> dict[str, Any]:
    metadata, spec = pod["metadata"], pod["spec"]
    worker = _identifier(metadata["name"])
    created = datetime.fromisoformat(metadata["creationTimestamp"].removesuffix("Z"))
    boot = str(node.get("status", {}).get("nodeInfo", {}).get("bootID", "")).replace("-", "")
    native = [c for c in spec.get("containers", []) if c.get("name") == "slurmd"]
    roots = [v for v in spec.get("volumes", []) if v.get("hostPath", {}).get("path") == "/"]
    mounts = [
        m
        for c in native
        for m in c.get("volumeMounts", [])
        if m.get("mountPath", "").startswith("/run/nvidia/driver")
    ]
    if (
        node.get("metadata", {}).get("name") != spec["nodeName"]
        or not node.get("metadata", {}).get("uid")
        or not re.fullmatch(r"[0-9a-f]{32}", boot)
        or len(native) != 1
        or len(roots) != 1
        or mounts
        != [{"name": roots[0]["name"], "mountPath": "/run/nvidia/driver", "readOnly": True}]
    ):
        raise RuntimeError("worker eviction journal lost its native read-only host authority")
    since, until = max(started, created - timedelta(minutes=2)), created + timedelta(seconds=10)
    if since >= until:
        raise RuntimeError("worker eviction journal has no valid execution window")
    rows = read(worker, since, until)
    candidates = []
    for row in rows:
        message = row.get("MESSAGE")
        if (
            not isinstance(message, str)
            or 'pod="soperator/' + worker + '"' not in message
            or row.get("_SYSTEMD_UNIT") != "kubelet.service"
            or row.get("_BOOT_ID") != boot
            or not isinstance(row.get("__CURSOR"), str)
            or not row["__CURSOR"]
            or not re.fullmatch(r"[0-9]{1,20}", str(row.get("__REALTIME_TIMESTAMP", "")))
        ):
            continue
        stamp = datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, UTC).replace(
            tzinfo=None
        )
        if since <= stamp <= until:
            candidates.append((row, stamp))
    kills = [
        (row, stamp)
        for row, stamp in candidates
        if '"Killing container with a grace period"' in row["MESSAGE"]
        and 'containerName="slurmd"' in row["MESSAGE"]
        and started < stamp < created
        and (ended is None or stamp <= ended)
    ]
    evictions = [
        (row, stamp)
        for row, stamp in candidates
        if '"Eviction manager: pod is evicted successfully"' in row["MESSAGE"]
    ]
    if len(kills) != 1 or len(evictions) != 1:
        raise RuntimeError("storage repair requires one authoritative worker eviction journal")
    killed, killed_at = kills[0]
    evicted, evicted_at = evictions[0]
    uid = re.search(r'\bpodUID="([0-9a-f-]{36})"', killed["MESSAGE"])
    if (
        uid is None
        or uid[1] == metadata["uid"]
        or not killed_at <= evicted_at <= killed_at + timedelta(seconds=10)
        or (ended is not None and evicted_at > ended)
        or killed["__CURSOR"] == evicted["__CURSOR"]
    ):
        raise RuntimeError("storage repair eviction journal lost the exact old worker")
    return {
        "source": "native-kubelet-journal",
        "nodeUid": node["metadata"]["uid"],
        "bootId": boot,
        "workerUid": uid[1],
        "time": killed_at.isoformat(),
        "terminationCursor": killed["__CURSOR"],
        "evictionCursor": evicted["__CURSOR"],
        "evictedAt": evicted_at.isoformat(),
    }
