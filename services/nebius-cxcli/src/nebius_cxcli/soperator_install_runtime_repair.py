"""Sealed initial-install repair for omitted native NodeSet runtime mounts."""

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
from .soperator_config_materialization import (
    _SOPERATOR_NODESET_RUNTIME_MOUNTS,
    _materialize_soperator_nodeset_runtime_mounts,
)
from .soperator_install_checks_repair import _publish_binding_repair, _verify_ancestor_seals
from .soperator_install_render_repair import (
    OUTER_FILE,
    RUNTIME_REPAIR_REASON,
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
    _kube_get,
)
from .soperator_install_runtime_recovery import capture_probe_failure
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_reconciler import validate_install_runtime_frontier
from .soperator_release_resolver import frozen_soperator_release_from_snapshot


def runtime_repair_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("runtime repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("runtime repair requires matching umbrella values")
    nodesets = values["nodesets"]["overrideValues"]
    if not nodesets.get("nodesets"):
        raise RuntimeError("runtime repair has no approved NodeSets")
    if inverse:
        for node in nodesets["nodesets"]:
            mounts = node["slurmd"]["volumes"]["customVolumeMounts"]
            if mounts[:3] != list(_SOPERATOR_NODESET_RUNTIME_MOUNTS):
                raise RuntimeError("runtime repair lost the exact added mount prefix")
            del mounts[:3]
    elif not _materialize_soperator_nodeset_runtime_mounts(nodesets):
        return dict(previous)
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    outer[0]["spec"]["values"] = copy.deepcopy(values)
    return {
        **previous,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }


def runtime_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = runtime_repair_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or runtime_repair_candidate(previous) != current
    ):
        raise RuntimeError("runtime repair lost its exact reversible input delta")
    values = yaml.safe_load(_documents(previous[VALUES_FILE])[0]["data"]["values.yaml"])
    target_values = yaml.safe_load(_documents(current[VALUES_FILE])[0]["data"]["values.yaml"])
    old_policy = replace(policy, values_sha256=checks_digest(values))
    handoff = repair["reservationHandoff"]
    if (
        old_policy.sha256 != handoff["policy"]
        or checks_digest(target_values) != policy.values_sha256
    ):
        raise RuntimeError("runtime repair changed the native check execution contract")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_runtime_repair(
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
    repair_path = paths.reports_dir / f"soperator-install-runtime-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator runtime repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != RUNTIME_REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("saved runtime repair lost generated authority")
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
    candidate = runtime_repair_candidate(existing)
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
        raise RuntimeError("runtime repair requires exact initial scheduling recovery")
    if runtime_repair_candidate(candidate, inverse=True) != existing:
        raise RuntimeError("runtime repair only admits wholly missing operational mounts")
    previous_sha = scheduling_journal["operationSpecSha256"]
    predecessors = [
        row
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if isinstance(
            row := read_owner_only_json(path, label="runtime repair predecessor"), Mapping
        )
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("runtime repair requires one exact predecessor")
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
        raise RuntimeError("runtime repair changed source, storage or ancestry")
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    source_dir = Path(frozen.source.source_dir)
    policy = compile_checks_policy(source_dir, values)

    def read_kube(args: list[str], document: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if args[0] != "get" or document is not None:
            raise RuntimeError("runtime repair admission is read-only")
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
        raise RuntimeError("runtime repair predecessor checks are missing")
    runner = SoperatorChecksExecution(
        policy=policy,
        operation_id=previous_sha,
        receipt_path=check_path,
        kubernetes=read_kube,
        slurm=slurm,
        assert_authority=assert_authority,
    )
    state = copy.deepcopy(runner.state)
    failure = capture_probe_failure(runner)
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
            or live_spec.get("slurmd", {}).get("volumes", {}).get("customVolumeMounts", [])
            != node["slurmd"]["volumes"]["customVolumeMounts"]
        ):
            raise RuntimeError("runtime repair lost its exact native NodeSet")
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
        raise RuntimeError("runtime repair lost approved storage bindings")
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
        raise RuntimeError("runtime repair lost frozen native chart authority")
    cluster_owner = values["slurmCluster"].get(
        "releaseName", defaults["slurmCluster"]["releaseName"]
    )
    rendered = subprocess.run(
        [
            "helm",
            "template",
            cluster_owner,
            str(source_dir / "helm/slurm-cluster"),
            "--namespace",
            "soperator",
            "-f",
            "-",
        ],
        input=yaml.safe_dump(cluster_values),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    script_maps = [
        row
        for row in yaml.safe_load_all(rendered.stdout)
        if isinstance(row, Mapping)
        and row.get("kind") == "ConfigMap"
        and row.get("metadata", {}).get("name") == "slurm-scripts"
    ]
    scripts = runner._get("configmap", "slurm-scripts")
    if (
        len(script_maps) != 1
        or not scripts.get("metadata", {}).get("uid")
        or scripts.get("data") != script_maps[0].get("data")
    ):
        raise RuntimeError("runtime repair scripts differ from pinned upstream render")
    if (
        read_owner_only_json(check_path, label="runtime repair predecessor checks") != state
        or capture_probe_failure(runner) != failure
    ):
        raise RuntimeError("runtime repair evidence changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=RUNTIME_REPAIR_REASON,
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
            "runtimeFailure": failure,
            "scripts": {
                "uid": scripts["metadata"]["uid"],
                "dataSha256": checks_digest(scripts["data"]),
            },
            "reservationHandoff": {
                "operation": previous_sha,
                "receiptSha256": _digest(state),
                "policy": policy.sha256,
                "reservation": failure["reservation"]["name"],
                "fingerprint": failure["reservation"]["fingerprint"],
            },
        },
    )
