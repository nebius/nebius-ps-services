"""Bind the upstream jail-log reader to the declared placement and active jail."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

JAIL_LOGS_RELEASE = "soperator-fluxcd-opentelemetry-collector-jail-logs"
_SOURCE_AFFINITY = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "slurm.nebius.ai/nodeset",
                            "operator": "In",
                            "values": ["system"],
                        }
                    ]
                }
            ]
        }
    }
}


def jail_logs_binding_operations(
    values: Mapping[str, Any], adapter_documents: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return a source-checked patch for this collector alone, never shared values."""
    observability = values.get("observability", {})
    logs = observability.get("opentelemetry", {}).get("logs", {})
    if logs.get("overrideValues"):
        raise ValueError("Jail-log binding cannot replace shared collector override values")
    slurm = values.get("slurmCluster", {})
    namespace = slurm.get("namespace") or "soperator"
    desired = slurm.get("overrideValues", {})
    filters = [x for x in desired.get("k8sNodeFilters", []) if x.get("name") == "system"]
    if len(filters) != 1:
        raise ValueError("Jail-log binding requires one approved system node filter")
    placement = filters[0]
    affinity = placement.get("affinity")
    if not isinstance(affinity, Mapping) or not affinity.get("nodeAffinity", {}).get(
        "requiredDuringSchedulingIgnoredDuringExecution", {}
    ).get("nodeSelectorTerms"):
        raise ValueError("Jail-log binding requires constrained system node affinity")
    jails = [x for x in desired.get("volumeSources", []) if x.get("name") == "jail"]
    if len(jails) != 1 or not jails[0].get("persistentVolumeClaim", {}).get("claimName"):
        raise ValueError("Jail-log binding requires one approved active jail claim")
    claim_name = jails[0]["persistentVolumeClaim"]["claimName"]
    claims = [
        x
        for x in adapter_documents
        if x.get("kind") == "PersistentVolumeClaim"
        and x.get("metadata", {}).get("name") == claim_name
        and x.get("metadata", {}).get("namespace") == namespace
    ]
    volumes = [
        x
        for x in adapter_documents
        if x.get("kind") == "PersistentVolume"
        and x.get("spec", {}).get("claimRef", {}).get("name") == claim_name
        and x.get("spec", {}).get("claimRef", {}).get("namespace") == namespace
    ]
    if (
        len(claims) != 1
        or len(volumes) != 1
        or claims[0].get("spec", {}).get("volumeName") != volumes[0].get("metadata", {}).get("name")
        or volumes[0].get("metadata", {}).get("labels", {}).get("soperator.nebius.ai/lifecycle")
        not in {"protected", "shared-adopted"}
    ):
        raise ValueError("Jail-log binding requires exact protected adapter PVC and PV authority")
    path = volumes[0].get("spec", {}).get("local", {}).get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("/mnt/")
        or ".." in PurePosixPath(path).parts
        or str(PurePosixPath(path)) != path
    ):
        raise ValueError("Jail-log binding requires the active jail's local backing path")
    storage_terms = (
        volumes[0]["spec"].get("nodeAffinity", {}).get("required", {}).get("nodeSelectorTerms")
    )
    system_terms = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ]
    if (
        not isinstance(storage_terms, list)
        or not storage_terms
        or not isinstance(system_terms, list)
        or any(
            not isinstance(term, Mapping) or not term for term in [*storage_terms, *system_terms]
        )
    ):
        raise ValueError("Jail-log binding requires the protected PV's node placement")
    affinity = copy.deepcopy(dict(affinity))
    affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ] = [
        {
            key: copy.deepcopy([*system.get(key, []), *storage.get(key, [])])
            for key in ("matchExpressions", "matchFields")
            if system.get(key) or storage.get(key)
        }
        for system in system_terms
        for storage in storage_terms
    ]
    public_endpoint = observability.get("publicEndpointEnabled", True)
    if not isinstance(public_endpoint, bool):
        raise ValueError("Jail-log binding requires an explicit boolean endpoint contract")
    jail_index = 1 if public_endpoint else 0
    volume_path = f"/spec/values/extraVolumes/{jail_index}"
    operations: list[dict[str, Any]] = [
        {"op": "test", "path": "/spec/values/affinity", "value": copy.deepcopy(_SOURCE_AFFINITY)},
        {"op": "replace", "path": "/spec/values/affinity", "value": copy.deepcopy(affinity)},
        {"op": "test", "path": volume_path + "/name", "value": "jail"},
        {
            "op": "test",
            "path": volume_path + "/hostPath",
            "value": {"path": "/mnt/jail", "type": "Directory"},
        },
        {"op": "replace", "path": volume_path + "/hostPath/path", "value": path},
    ]
    for key in ("nodeSelector", "tolerations"):
        if key in placement:
            operations.append(
                {"op": "add", "path": "/spec/values/" + key, "value": copy.deepcopy(placement[key])}
            )
    return operations
