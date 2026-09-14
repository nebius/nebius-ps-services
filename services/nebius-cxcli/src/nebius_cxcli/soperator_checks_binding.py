"""Bind upstream check workloads to the authoritative active jail claim."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

import yaml

CHECKS_RELEASE = "soperator-fluxcd-soperator-activechecks"
AUXILIARY_CRONJOB = "run-extensive-check-on-reservations"


def _retained_volume(mount: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": mount["name"], "persistentVolumeClaim": {"claimName": mount["pvc_name"]}}


def _retained_mount(mount: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": mount["name"], "mountPath": "/mnt/jail" + mount["mount_path"]}


def _merge_mounts(container: dict[str, Any], mounts: Sequence[Mapping[str, Any]]) -> None:
    volumes = container["volumes"]
    bindings = container.setdefault("volumeMounts", [{"name": "jail", "mountPath": "/mnt/jail"}])
    for row in bindings:
        path = row.get("mountPath")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or "\0" in path
            or ".." in PurePosixPath(path).parts
            or str(PurePosixPath(path)) != path
        ):
            raise ValueError("ActiveChecks mount paths must be canonical absolute paths")
    root = [row for row in bindings if row.get("name") == "jail"]
    if root != [{"name": "jail", "mountPath": "/mnt/jail"}]:
        raise ValueError("ActiveChecks require the writable jail mount at /mnt/jail")
    for mount in mounts:
        volume, binding = _retained_volume(mount), _retained_mount(mount)
        named = [row for row in volumes if row.get("name") == volume["name"]]
        if named and named != [volume]:
            raise ValueError("ActiveChecks retained volume conflicts with authoritative storage")
        overlapping = [
            row
            for row in bindings
            if row.get("name") != "jail"
            and (
                row.get("name") == binding["name"]
                or PurePosixPath(row["mountPath"]).is_relative_to(binding["mountPath"])
                or PurePosixPath(binding["mountPath"]).is_relative_to(row["mountPath"])
            )
        ]
        if overlapping and overlapping != [binding]:
            raise ValueError("ActiveChecks retained mount conflicts with authoritative storage")
        if not named:
            volumes.append(volume)
        if not overlapping:
            bindings.append(binding)


def validate_rendered_check_storage(
    spec: Mapping[str, Any], active_pvc: str, mounts: Sequence[Mapping[str, Any]]
) -> None:
    kind = spec["checkType"]
    job = spec[kind + "Spec"]
    container = copy.deepcopy(dict(job["jobContainer"]))
    original = copy.deepcopy(container)
    bound = bind_checks_jail({"jobContainer": container}, active_pvc, mounts)["jobContainer"]
    if bound != original:
        raise ValueError("Rendered ActiveCheck is missing authoritative retained jail bindings")


def retained_check_mounts(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recover authoritative retained bindings from compiled main workload values."""
    cluster = values["slurmCluster"]["overrideValues"]
    sources = {row["name"]: row for row in cluster.get("volumeSources", [])}
    mounts = (
        cluster.get("slurmNodes", {}).get("login", {}).get("volumes", {}).get("jailSubMounts", [])
    )
    return [
        {
            "name": row["name"],
            "mount_path": row["mountPath"],
            "pvc_name": sources[row["volumeSourceName"]]["persistentVolumeClaim"]["claimName"],
        }
        for row in mounts
    ]


def bind_checks_jail(
    overrides: Mapping[str, Any], active_pvc: str, mounts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if not active_pvc:
        raise ValueError("ActiveChecks require the authoritative active jail PVC")
    result = copy.deepcopy(dict(overrides))
    container = result.setdefault("jobContainer", {})
    volumes = container.setdefault(
        "volumes", [{"name": "jail", "persistentVolumeClaim": {"claimName": active_pvc}}]
    )
    jail = [volume for volume in volumes if volume.get("name") == "jail"]
    if len(jail) != 1 or set(jail[0]) != {"name", "persistentVolumeClaim"}:
        raise ValueError("ActiveChecks jail volume must be one persistent claim")
    claim = jail[0]["persistentVolumeClaim"]
    if claim.get("claimName") not in {"jail-pvc", active_pvc}:
        raise ValueError("ActiveChecks jail claim conflicts with active storage")
    if set(claim) != {"claimName"}:
        raise ValueError("ActiveChecks jail claim must be writable without extra settings")
    claim["claimName"] = active_pvc
    _merge_mounts(container, mounts)
    return result


def checks_post_renderers(
    active_pvc: str, mounts: Sequence[Mapping[str, Any]], *, suspended: bool = False
) -> list[dict[str, Any]]:
    if not active_pvc:
        raise ValueError("ActiveChecks auxiliary job requires the active jail PVC")
    path = "/spec/jobTemplate/spec/template/spec/volumes/0"
    patch: list[dict[str, Any]] = [
        {"op": "test", "path": path + "/name", "value": "jail"},
        {"op": "test", "path": path + "/persistentVolumeClaim/claimName", "value": "jail-pvc"},
        {"op": "replace", "path": path + "/persistentVolumeClaim/claimName", "value": active_pvc},
    ]
    if mounts:
        container_path = "/spec/jobTemplate/spec/template/spec/containers/0"
        patch.append(
            {
                "op": "test",
                "path": container_path + "/volumeMounts/3",
                "value": {"name": "jail", "mountPath": "/mnt/jail"},
            }
        )
        for mount in mounts:
            patch.extend(
                [
                    {
                        "op": "add",
                        "path": "/spec/jobTemplate/spec/template/spec/volumes/-",
                        "value": _retained_volume(mount),
                    },
                    {
                        "op": "add",
                        "path": container_path + "/volumeMounts/-",
                        "value": _retained_mount(mount),
                    },
                ]
            )
    if suspended:
        patch.extend(
            [
                {"op": "test", "path": "/spec/suspend", "value": False},
                {"op": "replace", "path": "/spec/suspend", "value": True},
            ]
        )
    return [
        {
            "kustomize": {
                "patches": [
                    {
                        "target": {
                            "group": "batch",
                            "version": "v1",
                            "kind": "CronJob",
                            "name": AUXILIARY_CRONJOB,
                        },
                        "patch": yaml.safe_dump(patch, sort_keys=False),
                    }
                ]
            }
        }
    ]


def bind_auxiliary_spec(
    spec: Mapping[str, Any],
    active_pvc: str,
    mounts: Sequence[Mapping[str, Any]],
    *,
    cluster: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(spec))
    pod = result["jobTemplate"]["spec"]["template"]["spec"]
    if pod["volumes"][0] != {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}}:
        raise ValueError("upstream auxiliary jail template changed")
    pod["volumes"][0]["persistentVolumeClaim"]["claimName"] = active_pvc
    container = pod["containers"][0]
    binding = {"volumes": pod["volumes"], "volumeMounts": container["volumeMounts"]}
    _merge_mounts(binding, mounts)
    if cluster is not None:
        pod["volumes"][1]["configMap"]["name"] = cluster + "-slurm-configs"
        pod["volumes"][2]["secret"]["secretName"] = cluster + "-munge"
    return result


def auxiliary_retained_mounts(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    sources = {row["name"]: row for row in pod["volumes"]}
    return [
        {
            "name": row["name"],
            "mount_path": row["mountPath"][len("/mnt/jail") :],
            "pvc_name": sources[row["name"]]["persistentVolumeClaim"]["claimName"],
        }
        for row in pod["containers"][0]["volumeMounts"]
        if row["mountPath"].startswith("/mnt/jail/")
        and "persistentVolumeClaim" in sources[row["name"]]
    ]


def active_checks_pvc(values: Mapping[str, Any]) -> str:
    volumes = values["soperatorActiveChecks"]["overrideValues"]["jobContainer"]["volumes"]
    jail = [volume for volume in volumes if volume.get("name") == "jail"]
    if len(jail) != 1:
        raise ValueError("ActiveChecks require exactly one active jail binding")
    return str(jail[0]["persistentVolumeClaim"]["claimName"])


def auxiliary_post_renderers(
    values: Mapping[str, Any], *, suspended: bool = False
) -> list[dict[str, Any]]:
    """Bind all hardcoded auxiliary resource names to the approved cluster."""
    cluster = values["slurmCluster"]["overrideValues"].get("clusterName")
    if not isinstance(cluster, str) or re.fullmatch(r"[a-z0-9][a-z0-9.-]*", cluster) is None:
        raise ValueError("Auxiliary checks require the exact approved cluster name")
    result = checks_post_renderers(
        active_checks_pvc(values), retained_check_mounts(values), suspended=suspended
    )
    renderer = result[0]["kustomize"]["patches"][0]
    operations = yaml.safe_load(renderer["patch"])
    for index, volume, field, source, target in (
        (
            1,
            "slurm-configs",
            "configMap/name",
            "soperator-slurm-configs",
            cluster + "-slurm-configs",
        ),
        (2, "munge-key", "secret/secretName", "soperator-munge", cluster + "-munge"),
    ):
        path = f"/spec/jobTemplate/spec/template/spec/volumes/{index}"
        operations.extend(
            [
                {"op": "test", "path": path + "/name", "value": volume},
                {"op": "test", "path": path + "/" + field, "value": source},
                {"op": "replace", "path": path + "/" + field, "value": target},
            ]
        )
    renderer["patch"] = yaml.safe_dump(operations, sort_keys=False)
    return result


def bind_auxiliary_cluster(outer: dict[str, Any], values: Mapping[str, Any]) -> None:
    """Normalize the common staged/stable copy; do not rewrite frozen inputs."""
    if values.get("soperatorActiveChecks", {}).get("enabled") is not True:
        return
    rows = outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    matches = [row for row in rows if row.get("target", {}).get("name") == CHECKS_RELEASE]
    if len(matches) != 1:
        raise ValueError("Auxiliary checks require one exact upstream child patch")
    operations = yaml.safe_load(matches[0]["patch"])
    renderers = [op for op in operations if op.get("path") == "/spec/postRenderers"]
    if len(renderers) != 1:
        raise ValueError("Auxiliary checks require one exact postrenderer")
    current = renderers[0].get("value")
    for suspended in (False, True):
        desired = auxiliary_post_renderers(values, suspended=suspended)
        if current == desired:
            return
        if current == checks_post_renderers(
            active_checks_pvc(values), retained_check_mounts(values), suspended=suspended
        ):
            renderers[0]["value"] = desired
            matches[0]["patch"] = yaml.safe_dump(operations, sort_keys=False)
            return
    raise ValueError("Auxiliary checks binding differs from the approved adapter")


def suspend_auxiliary_checks(outer: dict[str, Any], values: Mapping[str, Any]) -> None:
    """Change only the canonical child postrenderer in an operation copy."""
    bind_auxiliary_cluster(outer, values)
    rows = outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    matches = [row for row in rows if row.get("target", {}).get("name") == CHECKS_RELEASE]
    if len(matches) != 1:
        raise ValueError("Operation checks require one canonical ActiveChecks child patch")
    operations = yaml.safe_load(matches[0]["patch"])
    renderers = [op for op in operations if op.get("path") == "/spec/postRenderers"]
    if len(renderers) != 1 or renderers[0].get("value") != auxiliary_post_renderers(values):
        raise ValueError("Operation checks auxiliary binding differs from the approved adapter")
    renderers[0]["value"] = auxiliary_post_renderers(values, suspended=True)
    matches[0]["patch"] = yaml.safe_dump(operations, sort_keys=False)
