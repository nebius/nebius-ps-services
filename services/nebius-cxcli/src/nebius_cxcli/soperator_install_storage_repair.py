"""Sealed initial-install repair for the undersized native GPU worker scratch default."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest, compile_checks_policy
from .soperator_config_materialization import _SOPERATOR_WORKER_SCRATCH
from .soperator_install_checks_repair import _publish_binding_repair, _verify_ancestor_seals
from .soperator_install_eviction_journal import read_worker_journal
from .soperator_install_render_repair import (
    OUTER_FILE,
    STORAGE_REPAIR_REASON,
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
    _kube_get,
)
from .soperator_install_storage_cause import capture_storage_cause
from .soperator_install_storage_recovery import capture_storage_failure
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_reconciler import validate_install_runtime_frontier
from .soperator_release_resolver import frozen_soperator_release_from_snapshot


def storage_repair_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("storage repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("storage repair requires matching umbrella values")
    nodesets = values["nodesets"]["overrideValues"]
    if not nodesets.get("nodesets"):
        raise RuntimeError("storage repair has no approved NodeSets")
    if inverse:
        for node in nodesets["nodesets"]:
            resources = node["slurmd"]["resources"]
            if resources.get("ephemeralStorage") != _SOPERATOR_WORKER_SCRATCH:
                raise RuntimeError("storage repair lost the exact scratch allowance")
            resources["ephemeralStorage"] = "10Gi"
    else:
        resources = [node["slurmd"]["resources"] for node in nodesets["nodesets"]]
        if all(row.get("ephemeralStorage") == _SOPERATOR_WORKER_SCRATCH for row in resources):
            return dict(previous)
        if any(row.get("ephemeralStorage") != "10Gi" or row.get("gpu") != 8 for row in resources):
            raise RuntimeError("storage repair requires the exact undersized GPU scratch defaults")
        for row in resources:
            row["ephemeralStorage"] = _SOPERATOR_WORKER_SCRATCH
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def storage_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = storage_repair_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or storage_repair_candidate(previous) != current
    ):
        raise RuntimeError("storage repair lost its exact reversible input delta")
    values = yaml.safe_load(_documents(previous[VALUES_FILE])[0]["data"]["values.yaml"])
    target_values = yaml.safe_load(_documents(current[VALUES_FILE])[0]["data"]["values.yaml"])
    old_policy = replace(policy, values_sha256=checks_digest(values))
    handoff = repair["reservationHandoff"]
    if (
        old_policy.sha256 != handoff["policy"]
        or checks_digest(target_values) != policy.values_sha256
    ):
        raise RuntimeError("storage repair changed the native check execution contract")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_storage_repair(
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
    repair_path = paths.reports_dir / f"soperator-install-storage-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator storage repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != STORAGE_REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("saved storage repair lost generated authority")
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
    candidate = storage_repair_candidate(existing)
    if candidate == existing:
        return ancestor
    if scheduling_journal.get("lastCompletedStage") != "infrastructure-restored":
        return ancestor
    if (
        scheduling_journal != local_scheduling_journal
        or scheduling_journal.get("status") != "recovery-required"
        or scheduling_journal.get("actions") != []
        or slurm is None
    ):
        raise RuntimeError("storage repair requires exact initial scheduling recovery")
    if storage_repair_candidate(candidate, inverse=True) != existing:
        raise RuntimeError("storage repair only admits the exact GPU scratch default correction")
    previous_sha = scheduling_journal["operationSpecSha256"]
    predecessors = [
        row
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if isinstance(
            row := read_owner_only_json(path, label="storage repair predecessor"), Mapping
        )
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("storage repair requires one exact predecessor")
    predecessor = predecessors[0]
    validate_install_runtime_frontier(predecessor)
    spec = predecessor["operation"]["spec"]
    hashes = _file_hashes(existing)
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
        raise RuntimeError("storage repair changed source, storage or ancestry")
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    source_dir = Path(frozen.source.source_dir)
    policy = compile_checks_policy(source_dir, values)

    def read_kube(args: list[str], document: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if args[0] != "get" or document is not None:
            raise RuntimeError("storage repair admission is read-only")
        result = subprocess.run(
            ["kubectl", "--context", kube_context, *args],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    check_path = (
        paths.reports_dir / f"soperator-checks-{previous_sha.removeprefix('sha256:')[:24]}.json"
    )
    if not check_path.exists():
        raise RuntimeError("storage repair predecessor checks are missing")
    runner = SoperatorChecksExecution(
        policy=policy,
        operation_id=previous_sha,
        receipt_path=check_path,
        kubernetes=read_kube,
        slurm=slurm,
        assert_authority=assert_authority,
    )
    state = copy.deepcopy(runner.state)
    journal_reader = partial(read_worker_journal, env=env, kube_context=kube_context)

    def observe_failure() -> dict[str, Any]:
        observed = capture_storage_failure(runner, journal=journal_reader)
        observed["storageCause"] = capture_storage_cause(
            runner,
            observed,
            source_dir=source_dir,
            values=values,
            env=env,
            kube_context=kube_context,
        )
        return observed

    failure = observe_failure()
    defaults = yaml.safe_load((source_dir / "helm/soperator-fluxcd/values.yaml").read_text())
    nodeset_owner = {
        key: values["nodesets"].get(key, defaults["nodesets"][key])
        for key in ("releaseName", "namespace")
    }
    nodes = []
    for node in values["nodesets"]["overrideValues"]["nodesets"]:
        live = runner._get("nodeset", node["name"])
        metadata, live_spec = live.get("metadata", {}), live.get("spec", {})
        if (
            not metadata.get("uid")
            or metadata.get("deletionTimestamp")
            or metadata.get("annotations", {}).get("meta.helm.sh/release-name")
            != nodeset_owner["releaseName"]
            or metadata.get("annotations", {}).get("meta.helm.sh/release-namespace")
            != nodeset_owner["namespace"]
            or live_spec.get("replicas") != node["replicas"]
            or live_spec.get("nodeConfig", {}).get("static") != node["nodeConfig"]["static"]
            or live_spec.get("slurmd", {}).get("resources")
            != {
                "cpu": str(node["slurmd"]["resources"]["cpu"]),
                "memory": str(node["slurmd"]["resources"]["memory"]),
                "nvidia.com/gpu": "8",
                "ephemeral-storage": "10Gi",
            }
            or live_spec.get("slurmd", {}).get("volumes", {}).get("customVolumeMounts", [])
            != node["slurmd"]["volumes"]["customVolumeMounts"]
        ):
            raise RuntimeError("storage repair lost its exact native NodeSet")
        nodes.append(
            {"name": node["name"], "uid": metadata["uid"], "specSha256": _digest(live_spec)}
        )
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
        raise RuntimeError("storage repair lost approved storage bindings")
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
        or child.get("spec", {}).get("releaseName") != nodeset_owner["releaseName"]
        or child.get("spec", {}).get("targetNamespace") != nodeset_owner["namespace"]
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
        raise RuntimeError("storage repair lost frozen native chart authority")
    if failure["worker"]["nodeset"].get("uid") not in {node["uid"] for node in nodes}:
        raise RuntimeError("storage repair eviction is outside the admitted NodeSets")
    if (
        read_owner_only_json(check_path, label="storage repair predecessor checks") != state
        or observe_failure() != failure
    ):
        raise RuntimeError("storage repair evidence changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=STORAGE_REPAIR_REASON,
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
            "storageFailure": failure,
            "reservationHandoff": {
                "operation": previous_sha,
                "receiptSha256": _digest(state),
                "policy": policy.sha256,
                "reservation": failure["reservation"]["name"],
                "fingerprint": failure["reservation"]["fingerprint"],
            },
        },
    )
