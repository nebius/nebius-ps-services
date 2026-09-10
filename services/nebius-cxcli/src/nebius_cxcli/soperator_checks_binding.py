"""Bind upstream check workloads to the authoritative active jail claim."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

import yaml

CHECKS_RELEASE = "soperator-fluxcd-soperator-activechecks"
AUXILIARY_CRONJOB = "run-extensive-check-on-reservations"


def bind_checks_jail(overrides: Mapping[str, Any], active_pvc: str) -> dict[str, Any]:
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
    claim["claimName"] = active_pvc
    return result


def checks_post_renderers(active_pvc: str, *, suspended: bool = False) -> list[dict[str, Any]]:
    if not active_pvc:
        raise ValueError("ActiveChecks auxiliary job requires the active jail PVC")
    path = "/spec/jobTemplate/spec/template/spec/volumes/0"
    patch: list[dict[str, Any]] = [
        {"op": "test", "path": path + "/name", "value": "jail"},
        {"op": "test", "path": path + "/persistentVolumeClaim/claimName", "value": "jail-pvc"},
        {"op": "replace", "path": path + "/persistentVolumeClaim/claimName", "value": active_pvc},
    ]
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
    result = checks_post_renderers(active_checks_pvc(values), suspended=suspended)
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
        if current == checks_post_renderers(active_checks_pvc(values), suspended=suspended):
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
