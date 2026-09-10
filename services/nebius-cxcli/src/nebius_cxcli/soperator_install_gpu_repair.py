"""Sealed missing-Gres repair before initial check acceptance has started."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import SoperatorChecksPolicy, checks_digest, compile_checks_policy
from .soperator_config_materialization import _soperator_fit_gpu_node_config_to_group
from .soperator_install_checks_repair import _publish_binding_repair, _verify_ancestor_seals
from .soperator_install_render_repair import (
    GPU_MAINTENANCE_REPAIR_REASON,
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
from .soperator_release_reconciler import validate_install_gpu_acceptance_frontier
from .soperator_release_resolver import frozen_soperator_release_from_snapshot


def gpu_maintenance_repair_candidate(previous: Mapping[str, bytes]) -> dict[str, bytes]:
    """Use the canonical missing-count materializer without resizing any resource."""
    cm, outer = _documents(previous[VALUES_FILE]), _documents(previous[OUTER_FILE])
    if len(cm) != 1 or len(outer) != 1:
        raise RuntimeError("GPU repair requires unambiguous compiled inputs")
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values:
        raise RuntimeError("GPU repair requires matching umbrella values")
    changed = False
    for node in values["nodesets"]["overrideValues"]["nodesets"]:
        if node.get("gpu", {}).get("enabled") is not True:
            continue
        static = str(node.get("nodeConfig", {}).get("static") or "")
        if re.search(r"(?:^|\s)Gres=", static, flags=re.IGNORECASE):
            continue
        count = node["slurmd"]["resources"].get("gpu")
        if type(count) is not int or count <= 0 or not static:
            raise RuntimeError("GPU repair requires an approved GPU count and static topology")
        _soperator_fit_gpu_node_config_to_group(
            node, group_cpu_millicores=None, slurmd_cpu_millicores=None, gpu_count=count
        )
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


def gpu_maintenance_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    """Bind both policy identities through the sealed, reversible values delta."""
    current = _files(paths.flux_dir)
    if _file_hashes(current) != repair["replacementFiles"]:
        raise RuntimeError("GPU maintenance handoff lost its replacement values")
    cm, outer = _documents(current[VALUES_FILE]), _documents(current[OUTER_FILE])
    values = yaml.safe_load(cm[0]["data"]["values.yaml"])
    if outer[0]["spec"]["values"] != values or checks_digest(values) != policy.values_sha256:
        raise RuntimeError("GPU maintenance handoff target policy inputs differ")
    previous_values = copy.deepcopy(values)
    names = [row["name"] for row in repair["nodesetsRelease"]["nodes"]]
    recovered = []
    for node in previous_values["nodesets"]["overrideValues"]["nodesets"]:
        if node["name"] not in names:
            continue
        suffix = f" Gres=gpu:{node['slurmd']['resources']['gpu']}"
        static = node["nodeConfig"]["static"]
        if not static.endswith(suffix):
            raise RuntimeError("GPU maintenance handoff lost its exact missing-count delta")
        node["nodeConfig"]["static"] = static.removesuffix(suffix)
        recovered.append(node["name"])
    if not names or sorted(recovered) != sorted(set(names)) or len(names) != len(set(names)):
        raise RuntimeError("GPU maintenance handoff NodeSet identity is ambiguous")
    cm[0]["data"]["values.yaml"] = yaml.safe_dump(previous_values, sort_keys=False)
    outer[0]["spec"]["values"] = previous_values
    previous = {
        **current,
        VALUES_FILE: yaml.safe_dump_all(cm, sort_keys=False).encode(),
        OUTER_FILE: yaml.safe_dump_all(outer, sort_keys=False).encode(),
    }
    if (
        _file_hashes(previous) != repair["previousFiles"]
        or gpu_maintenance_repair_candidate(previous) != current
    ):
        raise RuntimeError("GPU maintenance handoff cannot authenticate predecessor inputs")
    predecessor_policy = replace(policy, values_sha256=checks_digest(previous_values))
    handoff = repair["reservationHandoff"]
    if predecessor_policy.sha256 != handoff["policy"]:
        raise RuntimeError("GPU maintenance handoff changed the check execution contract")
    return {**handoff, "predecessorPolicy": handoff["policy"], "policy": policy.sha256}


def prepare_install_gpu_maintenance_repair(
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
    repair_path = paths.reports_dir / f"soperator-install-gpu-maintenance-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator GPU maintenance repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != GPU_MAINTENANCE_REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("Saved GPU repair lost its exact generated authority")
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
    candidate = gpu_maintenance_repair_candidate(existing)
    if candidate == existing:
        return ancestor
    # Earlier recoveries still own their exact pre-apply frontier.
    if scheduling_journal.get("lastCompletedStage") == "gated":
        return ancestor
    if (
        scheduling_journal != local_scheduling_journal
        or scheduling_journal.get("status") != "recovery-required"
        or scheduling_journal.get("lastCompletedStage") != "infrastructure-restored"
        or scheduling_journal.get("actions") != []
        or scheduling_journal.get("infrastructureRestoreReceipt")
        != {"namespaceCount": 0, "status": "restored"}
        or slurm is None
    ):
        raise RuntimeError("GPU repair requires exact unstarted maintenance recovery")
    previous_sha = str(scheduling_journal.get("operationSpecSha256") or "")
    predecessors = [
        row
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if isinstance(
            row := read_owner_only_json(path, label="Soperator GPU repair predecessor"), Mapping
        )
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("GPU repair requires one exact predecessor")
    predecessor = predecessors[0]
    validate_install_gpu_acceptance_frontier(predecessor)
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
        raise RuntimeError("GPU repair lost unchanged source, storage or ancestry")
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    nodes = values["nodesets"]["overrideValues"]["nodesets"]
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    upstream_defaults = yaml.safe_load(
        (Path(frozen.source.source_dir) / "helm/soperator-fluxcd/values.yaml").read_text()
    )["nodesets"]
    owner = {
        key: values["nodesets"].get(key, upstream_defaults[key])
        for key in ("releaseName", "namespace")
    }
    if any(not isinstance(value, str) or not value for value in owner.values()):
        raise RuntimeError("GPU repair has no upstream Helm owner identity")
    policy = compile_checks_policy(Path(frozen.source.source_dir), values)
    check_path = (
        paths.reports_dir / f"soperator-checks-{previous_sha.removeprefix('sha256:')[:24]}.json"
    )
    checks = SoperatorChecksExecution(
        policy=policy,
        operation_id=previous_sha,
        receipt_path=check_path,
        kubernetes=lambda *_: {},
        slurm=slurm,
        assert_authority=assert_authority,
    )
    state = checks.state
    if (
        not check_path.exists()
        or state.get("phase") != "planned"
        or state.get("jobs") != {}
        or state.get("installReservationIntent") is not True
        or state.get("targetApplyIntent") is not True
        or state.get("reservation") != "cxcli_" + checks.operation_id[:16]
        or any(
            k in state
            for k in ("acceptance", "reservationFingerprint", "installReservationHandoff")
        )
    ):
        raise RuntimeError("GPU repair cannot carry submitted or accepted checks")
    reservation = checks._reservation(state["reservation"])
    if reservation["users"] != ["root"]:
        raise RuntimeError("GPU repair requires the original root-only reservation")
    expected_workers = {
        f"{node['name']}-{index}" for node in nodes for index in range(node["replicas"])
    }
    inventory = checks._node_inventory()
    if set(inventory) != expected_workers:
        raise RuntimeError("GPU repair worker inventory differs from the approved NodeSets")
    cluster_values = values["slurmCluster"]["overrideValues"]
    cluster = _kube_get(
        ["slurmcluster", cluster_values["clusterName"], "-n", "soperator"],
        env=env,
        kube_context=kube_context,
    )
    desired_claims = [
        {"name": row["name"], "persistentVolumeClaim": row["persistentVolumeClaim"]}
        for row in cluster_values["volumeSources"]
        if "persistentVolumeClaim" in row
    ]
    live_claims = [
        row
        for row in (cluster or {}).get("spec", {}).get("volumeSources", [])
        if "persistentVolumeClaim" in row
    ]
    if not cluster or not desired_claims or live_claims != desired_claims:
        raise RuntimeError("GPU repair lost its approved live storage bindings")
    node_evidence = []
    for node in nodes:
        if node.get("gpu", {}).get("enabled") is not True:
            continue
        if re.search(r"(?:^|\s)Gres=", node["nodeConfig"]["static"], flags=re.IGNORECASE):
            raise RuntimeError("GPU repair requires the proven absent count on every GPU NodeSet")
        live = _kube_get(
            ["nodeset", node["name"], "-n", "soperator"], env=env, kube_context=kube_context
        )
        metadata, live_spec = (live or {}).get("metadata", {}), (live or {}).get("spec", {})
        if (
            not metadata.get("uid")
            or metadata.get("deletionTimestamp")
            or metadata.get("annotations", {}).get("meta.helm.sh/release-name")
            != owner["releaseName"]
            or metadata.get("annotations", {}).get("meta.helm.sh/release-namespace")
            != owner["namespace"]
            or live_spec.get("replicas") != node["replicas"]
            or live_spec.get("nodeConfig", {}).get("static") != node["nodeConfig"]["static"]
            or live_spec.get("slurmd", {}).get("resources", {}).get("nvidia.com/gpu")
            != str(node["slurmd"]["resources"]["gpu"])
            or live_spec.get("gpu", {}).get("enabled") is not True
        ):
            raise RuntimeError("GPU repair lost its exact approved native NodeSet")
        node_evidence.append(
            {"name": node["name"], "uid": metadata["uid"], "specSha256": _digest(live_spec)}
        )
    if not node_evidence:
        raise RuntimeError("GPU repair has no native GPU NodeSet")
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
    digest = snapshot.charts["nodesets"].digest
    if (
        not child
        or not child.get("metadata", {}).get("uid")
        or child.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/version")
        != snapshot.release
        or child.get("spec", {}).get("releaseName") != owner["releaseName"]
        or child.get("spec", {}).get("targetNamespace") != owner["namespace"]
        or child.get("spec", {}).get("chartRef")
        != {
            "kind": "OCIRepository",
            "name": "soperator-upstream-nodesets",
            "namespace": "flux-system",
        }
        or child.get("status", {}).get("lastAttemptedRevisionDigest") != digest
        or not source
        or not source.get("metadata", {}).get("uid")
        or source.get("spec", {}).get("ref") != {"digest": digest}
        or source.get("status", {}).get("artifact", {}).get("digest")
        != snapshot.charts["nodesets"].package_sha256
    ):
        raise RuntimeError("GPU repair lost its frozen native chart identity")
    # Recheck mutable admission evidence before publishing the immutable seal.
    if read_owner_only_json(check_path, label="Soperator checks predecessor") != state:
        raise RuntimeError("GPU repair predecessor checks changed during admission")
    if checks._reservation(state["reservation"]) != reservation:
        raise RuntimeError("GPU repair reservation changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=GPU_MAINTENANCE_REPAIR_REASON,
        repair_path=repair_path,
        child_key="nodesetsRelease",
        child_evidence={
            "uid": child["metadata"]["uid"],
            "sourceUid": source["metadata"]["uid"],
            "sourceDigest": digest,
            "nodes": node_evidence,
        },
        hook_evidence=None,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
        additional_evidence={
            "reservationHandoff": {
                "operation": previous_sha,
                "receiptSha256": _digest(state),
                "policy": policy.sha256,
                "reservation": reservation["name"],
                "fingerprint": reservation["fingerprint"],
            }
        },
    )
