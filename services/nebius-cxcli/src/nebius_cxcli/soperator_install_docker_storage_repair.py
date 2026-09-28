"""Seal the exact private Docker storage mount lost during jail compilation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest
from .soperator_install_render_repair import (
    DOCKER_STORAGE_REPAIR_REASON,
    OUTER_FILE,
    VALUES_FILE,
    _documents,
    _file_hashes,
    _files,
)
from .soperator_worker_docker import (
    DOCKER_DAEMON_MOUNT,
    DOCKER_STORAGE_MOUNT,
    DOCKER_SUPERVISOR_CONFIG,
    gpu_nodesets,
)


def docker_storage_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("Docker storage repair requires unambiguous compiled values")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Docker storage repair requires matching umbrella values")
    nodes = gpu_nodesets(values["nodesets"]["overrideValues"])
    if not nodes:
        return dict(previous)
    if not inverse and not any(
        n.get("configMapRefSupervisord") == DOCKER_SUPERVISOR_CONFIG for n in nodes
    ):
        return dict(previous)
    changed = False
    for node in nodes:
        volumes = node["slurmd"]["volumes"]
        mounts = volumes.get("jailSubMounts", [])
        if not isinstance(mounts, list) or any(not isinstance(m, Mapping) for m in mounts):
            raise RuntimeError("Docker storage repair requires exact worker mounts")
        selected = [
            m
            for m in mounts
            if m.get("name") == DOCKER_STORAGE_MOUNT["name"]
            or str(m.get("mountPath", "")).rstrip("/") == DOCKER_STORAGE_MOUNT["mountPath"]
        ]
        if not inverse and selected == [DOCKER_STORAGE_MOUNT]:
            continue
        if (
            node.get("configMapRefSupervisord") != DOCKER_SUPERVISOR_CONFIG
            or volumes.get("customVolumeMounts", []).count(DOCKER_DAEMON_MOUNT) != 1
            or not mounts
            or (inverse and (selected != [DOCKER_STORAGE_MOUNT] or mounts[0] != selected[0]))
            or (not inverse and selected)
        ):
            raise RuntimeError("Docker storage repair is outside the omitted private mount defect")
        if inverse:
            mounts.pop(0)
        else:
            # Frozen deployment inputs use canonical mapping order. Emit the
            # same bytes as their renderer, including this newly retained mount.
            mounts.insert(0, copy.deepcopy(dict(sorted(DOCKER_STORAGE_MOUNT.items()))))
        changed = True
    if not changed:
        return dict(previous)
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def docker_storage_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = docker_storage_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or docker_storage_candidate(previous) != current
    ):
        raise RuntimeError("Docker storage repair lost its exact reversible delta")
    before = yaml.safe_load(_documents(previous[VALUES_FILE])[0]["data"]["values.yaml"])
    after = yaml.safe_load(_documents(current[VALUES_FILE])[0]["data"]["values.yaml"])
    old = replace(policy, values_sha256=checks_digest(before))
    handoff = repair["reservationHandoff"]
    if old.sha256 != handoff["policy"] or checks_digest(after) != policy.values_sha256:
        raise RuntimeError("Docker storage repair changed native check policy")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_docker_storage_repair(**kwargs: Any) -> Mapping[str, Any] | None:
    from .soperator_install_docker_storage_recovery import capture_docker_storage_failure
    from .soperator_install_nodeset_binding_repair import (
        NodeSetBindingRepair,
        prepare_install_nodeset_binding_repair,
    )

    if not kwargs.get("scheduling_journal"):
        return None
    own_path = (
        kwargs["paths"].reports_dir
        / f"soperator-install-docker-storage-repair-{kwargs['target_ref']}.json"
    )
    if not own_path.exists() and any(
        kwargs["paths"].reports_dir.glob(f"soperator-install-*-repair-{kwargs['target_ref']}.json")
    ):
        # Existing sealed interventions retain their own validated ancestry path.
        return None
    return prepare_install_nodeset_binding_repair(
        binding=NodeSetBindingRepair(
            reason=DOCKER_STORAGE_REPAIR_REASON,
            slug="docker-storage",
            candidate=docker_storage_candidate,
            capture=capture_docker_storage_failure,
            failure_key="dockerStorageFailure",
            requires_ancestor=False,
        ),
        **kwargs,
    )
