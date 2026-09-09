"""Sealed repair of the exact quota-as-physical-topology install defect."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest
from .soperator_install_render_repair import (
    OUTER_FILE,
    TOPOLOGY_REPAIR_REASON,
    VALUES_FILE,
    _documents,
    _file_hashes,
    _files,
)
from .soperator_install_topology_recovery import capture_topology_failure
from .soperator_worker_docker import gpu_nodesets
from .soperator_worker_topology import H200_QUOTA_DEFAULT, h200_static


def topology_repair_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("Topology repair requires unambiguous compiled values")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Topology repair requires matching umbrella values")
    nodes = gpu_nodesets(values["nodesets"]["overrideValues"])
    if not nodes:
        raise RuntimeError("Topology repair has no GPU workers")
    old = H200_QUOTA_DEFAULT + " Gres=gpu:8"
    new = h200_static(32000)
    expected, replacement = (new, old) if inverse else (old, new)
    if not inverse and all(n["nodeConfig"].get("static") == new for n in nodes):
        return dict(previous)
    for node in nodes:
        if (
            node["nodeConfig"].get("static") != expected
            or node["slurmd"]["resources"].get("cpu") not in (32, "32")
            or node["slurmd"]["resources"].get("gpu") != 8
        ):
            raise RuntimeError("Topology repair requires the exact H200 worker quota projection")
        node["nodeConfig"]["static"] = replacement
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def topology_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = topology_repair_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or topology_repair_candidate(previous) != current
    ):
        raise RuntimeError("Topology repair lost its exact reversible input delta")
    before = yaml.safe_load(_documents(previous[VALUES_FILE])[0]["data"]["values.yaml"])
    after = yaml.safe_load(_documents(current[VALUES_FILE])[0]["data"]["values.yaml"])
    old = replace(policy, values_sha256=checks_digest(before))
    handoff = repair["reservationHandoff"]
    if old.sha256 != handoff["policy"] or checks_digest(after) != policy.values_sha256:
        raise RuntimeError("Topology repair changed native check policy")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_topology_repair(**kwargs: Any) -> Mapping[str, Any] | None:
    from .soperator_install_nodeset_binding_repair import (
        NodeSetBindingRepair,
        prepare_install_nodeset_binding_repair,
    )

    return prepare_install_nodeset_binding_repair(
        binding=NodeSetBindingRepair(
            reason=TOPOLOGY_REPAIR_REASON,
            slug="topology",
            candidate=topology_repair_candidate,
            capture=capture_topology_failure,
            failure_key="topologyFailure",
        ),
        **kwargs,
    )
