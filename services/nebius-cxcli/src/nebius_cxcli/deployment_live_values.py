"""Recover executable source values from observed compiler outputs.

The result is a candidate until a full render reproduces both live ConfigMaps.
This is deliberately separate from authored desired values and local journals.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .soperator_adapter import compile_upstream_soperator_values
from .soperator_deployment_profile import auxiliary_checks_suspended


def physical_values(seed: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    values = copy.deepcopy(dict(seed))

    def slot(row):
        return {
            out: row[key]
            for key, out in (
                ("volume_name", "volumeSourceName"),
                ("pv_name", "pvName"),
                ("pvc_name", "pvcName"),
                ("local_path", "localPath"),
            )
        }

    root = values.setdefault("jailRootfs", {})
    root.update(
        {
            "strategy": "activePassive",
            "activeSlot": state["activeSlot"],
            "passiveSlot": state["passiveSlot"],
            "store": {"mountPath": state["mountPath"], "rootfsPath": state["rootfsPath"]},
            "slots": {key: slot(row) for key, row in state["slots"].items()},
            "retainedGenerations": [slot(row) for row in state.get("retainedGenerations", [])],
            "adoption": {"activeSource": state["activeSource"]},
        }
    )
    values["jailPersistentMounts"] = [
        {
            "name": row["name"],
            "mountPath": row["mount_path"],
            "localPath": row["local_path"],
            "pvName": row["pv_name"],
            "pvcName": row["pvc_name"],
        }
        for row in state["persistentMounts"]
        if row["local_path"]
    ]
    volume = values.setdefault("volume", {})
    volume.setdefault("jail", {}).update(
        {
            "localPath": state["slots"][state["activeSlot"]]["local_path"],
            "filestoreDeviceName": state["deviceTag"],
        }
    )
    values.setdefault("sfs", {}).setdefault("filesystems", {}).setdefault("jail", {})["id"] = state[
        "filesystemId"
    ]
    for key in ("controllerSpool", "accounting"):
        row = state[key]
        volume[key] = {
            "enabled": row["enabled"],
            "name": row["name"],
            "type": row["type"],
            "size": row["size"],
            "localPath": row["mount_path"],
            "filestoreDeviceName": row["device_tag"],
        }
        if row["adopt_existing"]:
            volume[key].update(
                {
                    "existingPvName": row["pv_name"],
                    "existingPvcName": row["pvc_name"],
                    "existingAccessModes": row["access_modes"],
                    "existingStorageClassName": row["storage_class_name"],
                }
            )
        values.setdefault("storage", {})[key] = {
            "matchExpressions": row["affinity"],
            "tolerations": row["tolerations"],
        }
        values["sfs"]["filesystems"].setdefault(key.replace("Spool", "-spool"), {})["id"] = row[
            "filesystem_id"
        ]
    return values


def authored_values(seed, upstream, state, *, release):
    """Invert supported fields, retaining only exact compiler-generated removals."""
    values = physical_values(seed, state)
    from .soperator_adapter import _PARENT_ONLY_KEYS

    values = {key: value for key, value in values.items() if key in _PARENT_ONLY_KEYS}
    values.update(copy.deepcopy(upstream["slurmCluster"]["overrideValues"]))
    nodes = upstream.get("nodesets", {}).get("overrideValues") or {}
    values["nodesets"] = copy.deepcopy(nodes.get("nodesets", []))
    if "priorityClasses" in nodes:
        values["priorityClasses"] = copy.deepcopy(nodes["priorityClasses"])
    operator = upstream["soperator"]
    for key in (
        "controllerManager",
        "serviceMonitor",
        "customContainer",
        "hostNetwork",
        "rebooter",
    ):
        values.pop(key, None)
    if "priorityClasses" not in nodes:
        values.pop("priorityClasses", None)
    values.update(copy.deepcopy(operator.get("overrideValues") or {}))
    values.update(copy.deepcopy(operator.get("nodeConfigurator", {}).get("overrideValues") or {}))
    for key, branch in (
        ("certManager", upstream["certManager"]),
        ("soperator-checks", operator["soperatorChecks"]),
        ("soperator-activechecks", upstream["soperatorActiveChecks"]),
    ):
        values.pop(key, None)
        if "overrideValues" in branch:
            values[key] = {
                **copy.deepcopy(branch.get("overrideValues") or {}),
                "enabled": branch["enabled"],
            }
    for key, branch in (("soperator-notifier", "notifier"),):
        values.pop(key, None)
        if branch in upstream:
            row = upstream[branch]
            values[key] = {
                **copy.deepcopy(row.get("overrideValues") or {}),
                "enabled": row["enabled"],
            }
    values.pop("mariadb-operator", None)
    if "overrideValues" in upstream["mariadbOperator"]:
        values["mariadb-operator"] = {
            **copy.deepcopy(upstream["mariadbOperator"].get("overrideValues") or {}),
            "installOperator": upstream["mariadbOperator"]["enabled"],
        }
    values["kruise"] = copy.deepcopy(operator["kruise"].get("overrideValues") or {})
    values["observability"] = copy.deepcopy(upstream.get("observability", {}))
    backup = upstream.get("backup", {}).get("config", {})
    values.pop("soperator-backup-config", None)
    if "values" in backup:
        values["soperator-backup-config"] = {
            **copy.deepcopy(backup.get("values") or {}),
            "enabled": backup.get("enabled", False),
        }
    values["deploymentProfile"] = (
        "fast-dev-test" if auxiliary_checks_suspended(upstream) else "standard"
    )
    images = values.get("images", {})
    if images.get("populateJail") != state["targetImage"]:
        raise RuntimeError("Observed jail image differs between source values and adapter state")
    images.pop("populateJail", None)

    # Compile a skeleton to obtain exact injected objects for these physical
    # bindings and the observed release. It is never an executable generation.
    skeleton = copy.deepcopy(values)
    skeleton.pop("volumeSources", None)
    for role in skeleton.get("slurmNodes", {}).values():
        role.pop("customInitContainers", None)
        role.get("volumes", {}).pop("jailSubMounts", None)
    for node in skeleton["nodesets"]:
        node.pop("customInitContainers", None)
        volumes = node.get("slurmd", {}).get("volumes", {})
        volumes.pop("jailSubMounts", None)
        volumes.pop("customVolumeMounts", None)
    compiled, _ = compile_upstream_soperator_values(skeleton, release=release)

    def remove_exact(container, key, generated):
        if key in container:
            container[key] = [item for item in container[key] if item not in generated]
            if not container[key]:
                container.pop(key)

    cluster = compiled["slurmCluster"]["overrideValues"]
    remove_exact(values, "volumeSources", cluster.get("volumeSources", []))
    for key, role in values.get("slurmNodes", {}).items():
        generated = cluster.get("slurmNodes", {}).get(key, {})
        remove_exact(role, "customInitContainers", generated.get("customInitContainers", []))
        remove_exact(
            role.get("volumes", {}),
            "jailSubMounts",
            generated.get("volumes", {}).get("jailSubMounts", []),
        )
    compiled_nodes = {
        row["name"]: row for row in compiled["nodesets"]["overrideValues"]["nodesets"]
    }
    for node in values["nodesets"]:
        generated = compiled_nodes[node["name"]]
        remove_exact(node, "customInitContainers", generated.get("customInitContainers", []))
        volumes = node.get("slurmd", {}).get("volumes", {})
        for key in ("jailSubMounts", "customVolumeMounts"):
            remove_exact(volumes, key, generated.get("slurmd", {}).get("volumes", {}).get(key, []))
    return values
