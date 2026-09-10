"""Sealed recovery of missing upstream GPU worker Docker bindings."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest, compile_checks_policy
from .soperator_install_checks_repair import _publish_binding_repair, _verify_ancestor_seals
from .soperator_install_docker_recovery import capture_docker_failure
from .soperator_install_render_repair import (
    DOCKER_REPAIR_REASON,
    OUTER_FILE,
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
    _kube_get,
)
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_reconciler import validate_install_runtime_frontier
from .soperator_release_resolver import frozen_soperator_release_from_snapshot
from .soperator_worker_docker import (
    DOCKER_DAEMON_MOUNT,
    DOCKER_STORAGE_MOUNT,
    DOCKER_SUPERVISOR_CONFIG,
    gpu_nodesets,
    materialize_worker_docker,
)


def docker_repair_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("Docker repair requires unambiguous compiled values")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("Docker repair requires matching umbrella values")
    node_values = values["nodesets"]["overrideValues"]
    nodes = gpu_nodesets(node_values)
    if not nodes:
        raise RuntimeError("Docker repair has no GPU workers")
    if inverse:
        for node in nodes:
            if node.get("configMapRefSupervisord") != DOCKER_SUPERVISOR_CONFIG:
                raise RuntimeError("Docker repair Supervisor binding changed")
            del node["configMapRefSupervisord"]
            volumes = node["slurmd"]["volumes"]
            for key, expected in (
                ("customVolumeMounts", DOCKER_DAEMON_MOUNT),
                ("jailSubMounts", DOCKER_STORAGE_MOUNT),
            ):
                mounts = volumes.get(key, [])
                if (
                    mounts[-1:] != [expected]
                    or sum(m.get("name") == expected["name"] for m in mounts) != 1
                ):
                    raise RuntimeError("Docker repair lost its exact additive mounts")
                mounts.pop()
    else:
        complete = copy.deepcopy(node_values)
        if not materialize_worker_docker(complete):
            return dict(previous)
        if any(
            "configMapRefSupervisord" in n or n["slurmd"]["volumes"].get("jailSubMounts") != []
            for n in nodes
        ):
            raise RuntimeError("Docker repair requires exactly omitted default runtime bindings")
        materialize_worker_docker(node_values)
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def docker_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = docker_repair_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or docker_repair_candidate(previous) != current
    ):
        raise RuntimeError("Docker repair lost its exact reversible input delta")
    before = yaml.safe_load(_documents(previous[VALUES_FILE])[0]["data"]["values.yaml"])
    after = yaml.safe_load(_documents(current[VALUES_FILE])[0]["data"]["values.yaml"])
    old = replace(policy, values_sha256=checks_digest(before))
    handoff = repair["reservationHandoff"]
    if old.sha256 != handoff["policy"] or checks_digest(after) != policy.values_sha256:
        raise RuntimeError("Docker repair changed native check policy")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_docker_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    local_scheduling_journal: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    slurm: Callable[[str], str] | None,
    ancestor: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    repair_path = paths.reports_dir / f"soperator-install-docker-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator Docker repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != DOCKER_REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("saved Docker repair lost generated authority")
        _verify_ancestor_seals(
            saved, env=env, kube_context=kube_context, assert_authority=assert_authority
        )
        _bind_repair_admission(
            saved,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )
        return saved
    if ancestor is None or ancestor.get("previousOperationSpecSha256") == scheduling_journal.get(
        "operationSpecSha256"
    ):
        return ancestor
    candidate = docker_repair_candidate(existing)
    if candidate == existing:
        return ancestor
    if (
        scheduling_journal != local_scheduling_journal
        or scheduling_journal.get("lastCompletedStage") != "infrastructure-restored"
        or scheduling_journal.get("status") != "recovery-required"
        or scheduling_journal.get("actions") != []
        or slurm is None
        or docker_repair_candidate(candidate, inverse=True) != existing
    ):
        raise RuntimeError(
            "Docker repair requires exact initial scheduling recovery and runtime delta"
        )
    previous_sha = scheduling_journal["operationSpecSha256"]
    predecessors = [
        row
        for p in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if isinstance(row := read_owner_only_json(p, label="Docker predecessor"), Mapping)
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("Docker repair requires one exact predecessor")
    predecessor = predecessors[0]
    validate_install_runtime_frontier(predecessor)
    spec, hashes = predecessor["operation"]["spec"], _file_hashes(existing)
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    if (
        spec.get("target_ref") != target_ref
        or spec.get("target_release") != snapshot.release
        or spec.get("desired_values_sha256") != hashes[VALUES_FILE]
        or spec.get("adapter_sha256") != hashes["soperator-nebius-adapter.yaml"]
        or ancestor.get("replacementFiles") != hashes
        or ancestor.get("interventionGeneration") != spec.get("intervention_generation")
    ):
        raise RuntimeError("Docker repair changed source, storage or ancestry")
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    source_dir = Path(frozen.source.source_dir)
    policy = compile_checks_policy(source_dir, values)

    def read_kube(args: list[str], document: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if args[0] != "get" or document is not None:
            raise RuntimeError("Docker admission is read-only")
        result = subprocess.run(
            ["kubectl", "--context", kube_context, *args],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(result.stdout or "{}")

    check_path = (
        paths.reports_dir / f"soperator-checks-{previous_sha.removeprefix('sha256:')[:24]}.json"
    )
    if not check_path.exists():
        raise RuntimeError("Docker predecessor checks are missing")
    runner = SoperatorChecksExecution(
        policy=policy,
        operation_id=previous_sha,
        receipt_path=check_path,
        kubernetes=read_kube,
        slurm=slurm,
        assert_authority=assert_authority,
    )
    state = copy.deepcopy(runner.state)
    failure = capture_docker_failure(runner, source_dir)
    defaults = yaml.safe_load((source_dir / "helm/soperator-fluxcd/values.yaml").read_text())
    owner = {
        k: values["nodesets"].get(k, defaults["nodesets"][k]) for k in ("releaseName", "namespace")
    }
    nodes = []
    for node in gpu_nodesets(values["nodesets"]["overrideValues"]):
        live = runner._get("nodeset", node["name"])
        meta, live_spec = live.get("metadata", {}), live.get("spec", {})
        if (
            not meta.get("uid")
            or meta.get("deletionTimestamp")
            or meta.get("annotations", {}).get("meta.helm.sh/release-name") != owner["releaseName"]
            or meta.get("annotations", {}).get("meta.helm.sh/release-namespace")
            != owner["namespace"]
            or live_spec.get("replicas") != node["replicas"]
            or live_spec.get("nodeConfig", {}).get("static") != node["nodeConfig"]["static"]
            or live_spec.get("slurmd", {}).get("resources")
            != {
                "cpu": str(node["slurmd"]["resources"]["cpu"]),
                "memory": str(node["slurmd"]["resources"]["memory"]),
                "nvidia.com/gpu": str(node["slurmd"]["resources"]["gpu"]),
                "ephemeral-storage": str(node["slurmd"]["resources"]["ephemeralStorage"]),
            }
            or live_spec.get("configMapRefSupervisord")
            or live_spec.get("slurmd", {}).get("volumes", {}).get("jailSubMounts")
            or live_spec.get("slurmd", {}).get("volumes", {}).get("customVolumeMounts", [])
            != node["slurmd"]["volumes"]["customVolumeMounts"]
        ):
            raise RuntimeError("Docker repair lost its native NodeSet binding")
        nodes.append({"name": node["name"], "uid": meta["uid"], "specSha256": _digest(live_spec)})
    if any(
        f["worker"]["nodeset"].get("uid") not in {n["uid"] for n in nodes}
        for f in failure["failures"]
    ):
        raise RuntimeError("Docker failure is outside the admitted NodeSets")
    cluster_values = values["slurmCluster"]["overrideValues"]
    cluster = runner._get("slurmcluster", cluster_values["clusterName"])
    expected_claims = [
        {"name": row["name"], "persistentVolumeClaim": row["persistentVolumeClaim"]}
        for row in cluster_values["volumeSources"]
        if "persistentVolumeClaim" in row
    ]
    actual_claims = [
        row
        for row in cluster.get("spec", {}).get("volumeSources", [])
        if "persistentVolumeClaim" in row
    ]
    if not expected_claims or actual_claims != expected_claims:
        raise RuntimeError("Docker repair lost approved storage bindings")
    config_owner = {
        k: values.get("customConfigmaps", {}).get(k, defaults["customConfigmaps"][k])
        for k in ("releaseName", "namespace")
    }
    for config in failure["configs"]:
        live = runner._get("configmap", config["name"])
        meta = live.get("metadata", {})
        if (
            meta.get("uid") != config["uid"]
            or meta.get("deletionTimestamp")
            or meta.get("annotations", {}).get("meta.helm.sh/release-name")
            != config_owner["releaseName"]
            or meta.get("annotations", {}).get("meta.helm.sh/release-namespace")
            != config_owner["namespace"]
        ):
            raise RuntimeError("Docker repair lost native runtime config ownership")
    child = _kube_get(
        ["helmrelease", "cxcli-soperator-fluxcd-nodesets", "-n", "flux-system"],
        env=env,
        kube_context=kube_context,
    )
    source = _kube_get(
        ["ocirepository", "soperator-upstream-nodesets", "-n", "flux-system"],
        env=env,
        kube_context=kube_context,
    )
    chart = snapshot.charts["nodesets"]
    if (
        not child
        or not source
        or child.get("spec", {}).get("releaseName") != owner["releaseName"]
        or child.get("spec", {}).get("targetNamespace") != owner["namespace"]
        or child.get("spec", {}).get("chartRef")
        != {
            "kind": "OCIRepository",
            "name": "soperator-upstream-nodesets",
            "namespace": "flux-system",
        }
        or child.get("status", {}).get("lastAttemptedRevisionDigest") != chart.digest
        or source.get("spec", {}).get("ref") != {"digest": chart.digest}
        or source.get("status", {}).get("artifact", {}).get("digest") != chart.package_sha256
    ):
        raise RuntimeError("Docker repair lost frozen native chart authority")
    if (
        read_owner_only_json(check_path, label="Docker predecessor checks") != state
        or capture_docker_failure(runner, source_dir) != failure
    ):
        raise RuntimeError("Docker failure changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=DOCKER_REPAIR_REASON,
        repair_path=repair_path,
        child_key="nodesetsRelease",
        child_evidence={
            "uid": child["metadata"]["uid"],
            "sourceUid": source["metadata"]["uid"],
            "sourceDigest": chart.digest,
            "nodes": nodes,
        },
        hook_evidence=None,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
        additional_evidence={
            "dockerFailure": failure,
            "reservationHandoff": {
                "operation": previous_sha,
                "receiptSha256": _digest(state),
                "policy": policy.sha256,
                "reservation": failure["reservation"]["name"],
                "fingerprint": failure["reservation"]["fingerprint"],
            },
        },
    )
