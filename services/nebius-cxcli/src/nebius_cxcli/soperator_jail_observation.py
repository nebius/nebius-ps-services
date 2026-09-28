"""Observe protected jail directories through the injected Kubernetes adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .soperator_jail_mounts import JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS


def observe_protected_directories(
    cli: Any, values: Mapping[str, Any], *, kube_context: str, extra_env: Mapping[str, str] | None
) -> list[dict[str, str]]:
    from .soperator_jail_protection import directory_probe_script, observed_directory_bindings

    if not any(
        row["mountPath"] not in JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS
        for row in values.get("jailPersistentMounts", [])
    ):
        return []
    namespaces = cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env=extra_env)
    if len(namespaces) != 1:
        raise RuntimeError("protected folder discovery requires one exact Soperator namespace")
    namespace = namespaces[0]

    def read(kind: str, name: str) -> Mapping[str, Any]:
        result = cli._run_soperator_upgrade_kubectl(
            namespace,
            ["get", kind, name, "-o", "json"],
            kube_context=kube_context,
            extra_env=extra_env,
            check=True,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, Mapping):
            raise RuntimeError("protected folder discovery returned an invalid object")
        return payload

    container, bindings = observed_directory_bindings(
        values,
        pod=read("pod", cli._SOPERATOR_UPGRADE_LOGIN_POD),
        read_pvc=lambda name: read("pvc", name),
        read_pv=lambda name: read("pv", name),
    )
    result = cli._run_soperator_upgrade_kubectl(
        namespace,
        [
            "exec",
            cli._SOPERATOR_UPGRADE_LOGIN_POD,
            "-c",
            container,
            "--",
            "sh",
            "-c",
            directory_probe_script([row["mountPath"] for row in bindings]),
        ],
        kube_context=kube_context,
        extra_env=extra_env,
        check=False,
    )
    inodes = result.stdout.splitlines()
    if result.returncode or len(inodes) != len(bindings) or not all(v.isdecimal() for v in inodes):
        raise ValueError(
            "Protected folders must exist as real directories on the jail filesystem without symlink traversal"
        )
    return [{**row, "inode": inode} for row, inode in zip(bindings, inodes, strict=True)]
