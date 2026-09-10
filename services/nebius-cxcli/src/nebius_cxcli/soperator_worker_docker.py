"""Bind GPU NodeSets to the Docker runtime already supplied by upstream."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

DOCKER_SUPERVISOR_CONFIG = "custom-supervisord-config"
DOCKER_DAEMON_MOUNT: dict[str, Any] = {
    "name": "docker-daemon-config",
    "mountPath": "/etc/docker/daemon.json",
    "subPath": "daemon.json",
    "readOnly": True,
    "volumeSource": {"configMap": {"name": "image-storage", "defaultMode": 292}},
}
DOCKER_STORAGE_MOUNT: dict[str, Any] = {
    "name": "docker-image-storage",
    "mountPath": "/mnt/image-storage",
    "volumeSource": {"emptyDir": {}},
}


def gpu_nodesets(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return []
    selected = []
    for node in nodesets:
        if not isinstance(node, dict):
            raise ValueError("Soperator NodeSets must be mappings")
        gpu, slurmd = node.get("gpu", {}), node.get("slurmd", {})
        if not isinstance(gpu, Mapping) or not isinstance(slurmd, Mapping):
            raise ValueError("Soperator GPU and slurmd settings must be mappings")
        resources = slurmd.get("resources", {})
        if not isinstance(resources, Mapping):
            raise ValueError("Soperator worker resources must be a mapping")
        if gpu.get("enabled") is True or resources.get("gpu") not in (None, "", 0, "0"):
            selected.append(node)
    return selected


def materialize_worker_docker(values: dict[str, Any]) -> bool:
    """Use the native Supervisor and socket; isolate each pod's disposable images.

    Docker's native data root is inside this jail sub-mount. The native jail
    initializer already shares /run, so no host Docker socket or extra service
    is introduced. Cache usage remains under the declared pod storage allowance.
    """
    changed = False
    for node in gpu_nodesets(values):
        candidate = copy.deepcopy(node)
        selected = candidate.get("configMapRefSupervisord")
        if selected not in (None, "", DOCKER_SUPERVISOR_CONFIG):
            raise ValueError("GPU worker Docker runtime conflicts with a custom Supervisor config")
        candidate["configMapRefSupervisord"] = DOCKER_SUPERVISOR_CONFIG
        volumes = candidate.setdefault("slurmd", {}).setdefault("volumes", {})
        if not isinstance(volumes, dict):
            raise ValueError("GPU worker volumes must be a mapping")
        for key in ("customVolumeMounts", "jailSubMounts"):
            mounts = volumes.get(key, [])
            if not isinstance(mounts, list) or any(not isinstance(m, Mapping) for m in mounts):
                raise ValueError(f"GPU worker {key} must be a list of mappings")
        for key, expected in (
            ("customVolumeMounts", DOCKER_DAEMON_MOUNT),
            ("jailSubMounts", DOCKER_STORAGE_MOUNT),
        ):
            mounts = volumes.setdefault(key, [])
            if not isinstance(mounts, list):
                raise ValueError(f"GPU worker {key} must be a list")
            other_key = "jailSubMounts" if key == "customVolumeMounts" else "customVolumeMounts"
            if any(m.get("name") == expected["name"] for m in volumes.get(other_key, [])):
                raise ValueError("GPU worker Docker volume name collides across mount scopes")
            matches = [
                m
                for m in mounts
                if m.get("name") == expected["name"]
                or str(m.get("mountPath", "")).rstrip("/") == expected["mountPath"]
            ]
            if matches and matches != [expected]:
                raise ValueError("GPU worker Docker runtime collides with an existing mount")
            if not matches:
                mounts.append(copy.deepcopy(expected))
        if candidate != node:
            node.clear()
            node.update(candidate)
            changed = True
    return changed
