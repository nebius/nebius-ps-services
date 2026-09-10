"""Exact pre-main dashboard render repair for an interrupted installation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .project_bundle_transaction import ProjectBundleTransaction
from .soperator_adapter import (
    SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS,
    render_soperator_monitoring_dashboard_documents,
)
from .soperator_operation import (
    SoperatorOperationAnchor,
    SoperatorOperationSpec,
    soperator_stage_plan_sha256,
)
from .soperator_operation_lock import SoperatorLeaseAuthority
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_reconciler import soperator_reconcile_stage_plan_sha256
from .soperator_release_resolver import frozen_soperator_release_from_snapshot

REPAIR_REASON = "install-dashboard-source-delivery-v1"
CHECKS_REPAIR_REASON = "install-checks-jail-binding-v1"
REST_REPAIR_REASON = "install-rest-dependency-v1"
LOGIN_REPAIR_REASON = "install-checks-login-binding-v1"
COLLECTOR_REPAIR_REASON = "install-jail-collector-binding-v1"
GPU_MAINTENANCE_REPAIR_REASON = "install-gpu-maintenance-binding-v1"
RUNTIME_REPAIR_REASON = "install-nodeset-runtime-binding-v1"
STORAGE_REPAIR_REASON = "install-worker-scratch-binding-v1"
USERNS_REPAIR_REASON = "install-enroot-userns-binding-v1"
DOCKER_REPAIR_REASON = "install-worker-docker-binding-v1"
TOPOLOGY_REPAIR_REASON = "install-worker-topology-binding-v1"
CPU_MASK_REPAIR_REASON = "install-worker-cpu-mask-binding-v1"
INPUT_REPAIR_EVIDENCE_KEYS = {
    CPU_MASK_REPAIR_REASON: "installCpuMaskRepair",
    TOPOLOGY_REPAIR_REASON: "installTopologyRepair",
    DOCKER_REPAIR_REASON: "installDockerRepair",
    STORAGE_REPAIR_REASON: "installStorageRepair",
    USERNS_REPAIR_REASON: "installUsernsRepair",
    RUNTIME_REPAIR_REASON: "installRuntimeRepair",
    GPU_MAINTENANCE_REPAIR_REASON: "installGpuMaintenanceRepair",
    CHECKS_REPAIR_REASON: "installChecksRepair",
    REST_REPAIR_REASON: "installRestRepair",
    LOGIN_REPAIR_REASON: "installLoginRepair",
    COLLECTOR_REPAIR_REASON: "installCollectorRepair",
}
INPUT_REPAIR_RELEASE_KEYS = {
    reason: (
        "nodesetsRelease"
        if reason in {DOCKER_REPAIR_REASON, TOPOLOGY_REPAIR_REASON, CPU_MASK_REPAIR_REASON}
        else "profileRelease"
        if reason == USERNS_REPAIR_REASON
        else "collectorRelease"
        if reason == COLLECTOR_REPAIR_REASON
        else "checksRelease"
    )
    for reason in INPUT_REPAIR_EVIDENCE_KEYS
}
DASHBOARD_RELEASE = "cxcli-soperator-fluxcd-monitoring-dashboards"
DASHBOARD_SOURCE = "soperator-upstream-monitoringdashboards"
UPSTREAM_DASHBOARD_RELEASE = "soperator-fluxcd-monitoring-dashboards"
VALUES_FILE = "configmap-terraform-fluxcd-values.yaml"
OUTER_FILE = "helmrelease-slurm-soperator-fluxcd.yaml"
GRAPH_FILE = "soperator-release-graph.yaml"
DASHBOARD_FILE = "post-flux-soperator-monitoring-dashboards.yaml"


def _digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _documents(content: bytes) -> list[dict[str, Any]]:
    documents = list(yaml.safe_load_all(content))
    if not documents or any(not isinstance(item, dict) for item in documents):
        raise RuntimeError("Install render repair requires complete YAML documents")
    return documents


def _disable_dashboard(values: dict[str, Any], release: str) -> None:
    soperator = values.get("soperator")
    if not isinstance(soperator, dict) or soperator.get("monitoringDashboards") != {
        "enabled": True,
        "version": release,
    }:
        raise RuntimeError("Install render repair requires the original enabled dashboard child")
    soperator["monitoringDashboards"] = {"enabled": False}


def validate_dashboard_render_delta(
    previous: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
    *,
    release: str,
    chart_digest: str,
    expected_dashboards: list[dict[str, Any]],
) -> None:
    """Admit only equivalent source dashboards through the existing adapter."""
    changed = {key for key in previous.keys() & candidate.keys() if previous[key] != candidate[key]}
    if (
        previous.keys() - candidate.keys()
        or candidate.keys() - previous.keys() != {DASHBOARD_FILE}
        or changed != {VALUES_FILE, OUTER_FILE, GRAPH_FILE}
    ):
        raise RuntimeError("Install dashboard repair changes unrelated generated files")
    if (
        _documents(candidate[DASHBOARD_FILE]) != expected_dashboards
        or len(expected_dashboards) != 7
    ):
        raise RuntimeError("Install dashboard repair changed the verified upstream dashboards")

    values_docs = _documents(previous[VALUES_FILE])
    if len(values_docs) != 1:
        raise RuntimeError("Install dashboard values ConfigMap is ambiguous")
    values = yaml.safe_load(values_docs[0]["data"]["values.yaml"])
    _disable_dashboard(values, release)
    new_values_docs = _documents(candidate[VALUES_FILE])
    if len(new_values_docs) != 1:
        raise RuntimeError("Install dashboard replacement values ConfigMap is ambiguous")
    new_values = yaml.safe_load(new_values_docs[0]["data"]["values.yaml"])
    values_docs[0]["data"]["values.yaml"] = ""
    new_values_docs[0]["data"]["values.yaml"] = ""
    if values != new_values or values_docs != new_values_docs:
        raise RuntimeError("Install dashboard repair changes other approved chart values")

    outer_docs = _documents(previous[OUTER_FILE])
    if len(outer_docs) != 1:
        raise RuntimeError("Install dashboard outer release is ambiguous")
    outer = outer_docs[0]
    _disable_dashboard(outer["spec"]["values"], release)
    patches = outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    removed = [
        item
        for item in patches
        if item.get("target")
        == {
            "group": "helm.toolkit.fluxcd.io",
            "version": "v2",
            "kind": "HelmRelease",
            "name": UPSTREAM_DASHBOARD_RELEASE,
        }
    ]
    if len(removed) != 1:
        raise RuntimeError("Install dashboard repair lacks its exact original child patch")
    patches.remove(removed[0])
    if outer_docs != _documents(candidate[OUTER_FILE]):
        raise RuntimeError("Install dashboard repair changes unrelated release wiring")

    graph_docs = _documents(previous[GRAPH_FILE])
    sources = [
        item
        for item in graph_docs
        if item.get("kind") == "OCIRepository"
        and item.get("metadata", {}).get("name") == DASHBOARD_SOURCE
        and item.get("metadata", {}).get("namespace") == "flux-system"
    ]
    if len(sources) != 1 or sources[0].get("spec", {}).get("ref") != {"digest": chart_digest}:
        raise RuntimeError("Install dashboard repair changed the frozen chart digest")
    graph_docs.remove(sources[0])
    graph_maps = [
        item
        for item in graph_docs
        if item.get("kind") == "ConfigMap"
        and item.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
    ]
    if len(graph_maps) != 1:
        raise RuntimeError("Install dashboard graph contract is ambiguous")
    contract = json.loads(graph_maps[0]["data"]["graph.json"])
    rows = [item for item in contract["releases"] if item.get("releaseName") == DASHBOARD_RELEASE]
    if (
        len(rows) != 1
        or rows[0].get("revision") != chart_digest
        or rows[0].get("sourceName") != DASHBOARD_SOURCE
        or rows[0].get("isMain") is not False
        or any(DASHBOARD_RELEASE in item.get("dependencies", []) for item in contract["releases"])
    ):
        raise RuntimeError("Install dashboard repair changes a dependent or foreign graph child")
    contract["releases"].remove(rows[0])
    graph_maps[0]["data"]["graph.json"] = json.dumps(
        contract, sort_keys=True, separators=(",", ":")
    )
    if graph_docs != _documents(candidate[GRAPH_FILE]):
        raise RuntimeError("Install dashboard repair changes unrelated graph authority")


def validate_failed_dashboard(
    payload: Mapping[str, Any], *, release: str, source_digest: str
) -> str:
    """Require the exact failed child, with no successful release history."""
    metadata = payload.get("metadata", {})
    spec = payload.get("spec", {})
    status = payload.get("status", {})
    conditions = status.get("conditions", [])
    ready: Mapping[str, Any] = next(
        (item for item in conditions if item.get("type") == "Ready"), {}
    )
    history = status.get("history") or []
    if (
        metadata.get("name") != DASHBOARD_RELEASE
        or metadata.get("namespace") != "flux-system"
        or not metadata.get("uid")
        or metadata.get("deletionTimestamp")
        or metadata.get("labels", {}).get("soperator.nebius.ai/release-graph") != "nebius-cxcli"
        or metadata.get("labels", {}).get("app.kubernetes.io/version") != release
        # Flux's top-level observedGeneration advances only on Ready or Stalled.
        # A retrying install failure has its own current condition generation.
        or not isinstance(metadata.get("generation"), int)
        or metadata.get("generation", 0) < 1
        or metadata.get("generation") != ready.get("observedGeneration")
        or spec.get("chartRef", {}).get("kind") != "OCIRepository"
        or spec.get("chartRef", {}).get("name") != DASHBOARD_SOURCE
        or ready.get("status") != "False"
        or ready.get("reason") != "InstallFailed"
        or "invalid document separator: ---apiVersion: v1" not in ready.get("message", "")
        or any(item.get("status") in {"deployed", "superseded"} for item in history)
        or source_digest != status.get("lastAttemptedRevisionDigest")
    ):
        raise RuntimeError("Install dashboard repair lacks its exact failed child evidence")
    return str(metadata["uid"])


def _kube_get(
    args: list[str], *, env: Mapping[str, str], kube_context: str, optional: bool = False
) -> Mapping[str, Any] | None:
    command = [
        "kubectl",
        "--context",
        kube_context,
        "--request-timeout=20s",
        "get",
        *args,
        "-o",
        "json",
    ]
    if optional:
        command.append("--ignore-not-found=true")
    result = subprocess.run(command, env=dict(env), capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError("Install dashboard repair cannot prove its Kubernetes checkpoint")
    if optional and not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    if not isinstance(payload, Mapping):
        raise RuntimeError("Install dashboard repair received malformed Kubernetes evidence")
    return payload


def _files(directory: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Install render repair cannot follow generated symlinks")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def _file_hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    return {
        name: "sha256:" + hashlib.sha256(content).hexdigest() for name, content in files.items()
    }


def dashboard_repair_candidate(
    previous: Mapping[str, bytes],
    *,
    release: str,
    chart_digest: str,
    dashboards: list[dict[str, Any]],
) -> dict[str, bytes]:
    """Transform frozen compiled inputs; never recompile unrelated configuration."""
    candidate = dict(previous)
    values = _documents(previous[VALUES_FILE])
    compiled = yaml.safe_load(values[0]["data"]["values.yaml"])
    _disable_dashboard(compiled, release)
    values[0]["data"]["values.yaml"] = yaml.safe_dump(compiled, sort_keys=False)
    outer = _documents(previous[OUTER_FILE])
    _disable_dashboard(outer[0]["spec"]["values"], release)
    patches = outer[0]["spec"]["postRenderers"][0]["kustomize"]["patches"]
    patches[:] = [
        item for item in patches if item.get("target", {}).get("name") != UPSTREAM_DASHBOARD_RELEASE
    ]
    graph = [
        doc
        for doc in _documents(previous[GRAPH_FILE])
        if not (
            doc.get("kind") == "OCIRepository"
            and doc.get("metadata", {}).get("name") == DASHBOARD_SOURCE
        )
    ]
    graph_map = next(
        doc
        for doc in graph
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
    )
    contract = json.loads(graph_map["data"]["graph.json"])
    contract["releases"] = [
        row for row in contract["releases"] if row.get("releaseName") != DASHBOARD_RELEASE
    ]
    graph_map["data"]["graph.json"] = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    for name, docs in (
        (VALUES_FILE, values),
        (OUTER_FILE, outer),
        (GRAPH_FILE, graph),
        (DASHBOARD_FILE, dashboards),
    ):
        candidate[name] = yaml.safe_dump_all(docs, sort_keys=False).encode()
    validate_dashboard_render_delta(
        previous,
        candidate,
        release=release,
        chart_digest=chart_digest,
        expected_dashboards=dashboards,
    )
    return candidate


def _bind_repair_admission(
    repair: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    create: bool,
) -> None:
    """Bind the private render receipt to its authenticated predecessor anchor."""
    predecessor = repair.get("predecessorReceipt")
    if (
        not isinstance(predecessor, Mapping)
        or repair.get("predecessorReceiptSha256") != _digest(predecessor)
        or repair.get("previousOperationSpecSha256")
        != _digest(predecessor.get("operation", {}).get("spec"))
        or repair.get("schema") not in {REPAIR_REASON, *INPUT_REPAIR_EVIDENCE_KEYS}
        or repair.get("interventionGeneration")
        != predecessor.get("operation", {}).get("spec", {}).get("intervention_generation", 0) + 1
    ):
        raise RuntimeError("Install dashboard repair predecessor is invalid")
    spec = SoperatorOperationSpec(**predecessor["operation"]["spec"])
    authority = assert_authority()
    if not isinstance(authority, SoperatorLeaseAuthority):
        raise RuntimeError("Install dashboard repair lost fencing authority")
    anchor = SoperatorOperationAnchor(
        kube_context=kube_context,
        cluster_id=spec.nebius_cluster_id,
        operation_spec=spec,
        lease_authority=authority,
        extra_env=env,
    )
    if create:
        anchor.establish()
    payload = _kube_get(
        ["configmap", anchor.name, "-n", "kube-system"], env=env, kube_context=kube_context
    )
    if payload is None:
        raise RuntimeError("Install dashboard predecessor anchor is missing")
    data = payload.get("data", {})
    metadata = payload.get("metadata", {})
    admission_hash = _digest(repair)
    key = {
        REPAIR_REASON: "installDashboardRepairSha256",
        CHECKS_REPAIR_REASON: "installChecksRepairSha256",
        REST_REPAIR_REASON: "installRestRepairSha256",
        LOGIN_REPAIR_REASON: "installLoginRepairSha256",
        COLLECTOR_REPAIR_REASON: "installCollectorRepairSha256",
        GPU_MAINTENANCE_REPAIR_REASON: "installGpuMaintenanceRepairSha256",
        RUNTIME_REPAIR_REASON: "installRuntimeRepairSha256",
        STORAGE_REPAIR_REASON: "installStorageRepairSha256",
        USERNS_REPAIR_REASON: "installUsernsRepairSha256",
        DOCKER_REPAIR_REASON: "installDockerRepairSha256",
        TOPOLOGY_REPAIR_REASON: "installTopologyRepairSha256",
        CPU_MASK_REPAIR_REASON: "installCpuMaskRepairSha256",
    }[repair["schema"]]
    if (
        data.get("operationSpecSha256") != repair["previousOperationSpecSha256"]
        or data.get("clusterId") != spec.nebius_cluster_id
        or data.get("kubernetesUid") != spec.kubernetes_uid
        or data.get("status") not in {"active", "superseded"}
    ):
        raise RuntimeError("Install dashboard repair lost its cluster predecessor authority")
    if key in data:
        if data[key] != admission_hash:
            raise RuntimeError("Install dashboard repair differs from its cluster-bound admission")
        return
    if not create or data.get("status") != "active" or not metadata.get("resourceVersion"):
        raise RuntimeError("Install dashboard repair has no cluster-bound admission")
    patch = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {
            "op": "test",
            "path": "/data/operationSpecSha256",
            "value": repair["previousOperationSpecSha256"],
        },
        {"op": "test", "path": "/data/status", "value": "active"},
        {"op": "add", "path": "/data/" + key, "value": admission_hash},
    ]
    assert_authority()
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            "kube-system",
            "patch",
            "configmap",
            anchor.name,
            "--type=json",
            "-p",
            json.dumps(patch),
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError("Install dashboard repair could not bind its admission atomically")
    confirmed = _kube_get(
        ["configmap", anchor.name, "-n", "kube-system"], env=env, kube_context=kube_context
    )
    if confirmed is None or confirmed.get("data", {}).get(key) != admission_hash:
        raise RuntimeError("Install dashboard repair admission readback failed")


def prepare_install_dashboard_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    local_scheduling_journal: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
) -> Mapping[str, Any] | None:
    """Publish a closed render delta only after proving a pre-main install frontier."""
    assert_authority()
    repair_path = paths.reports_dir / f"soperator-install-render-repair-{target_ref}.json"
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator install render repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(_files(paths.flux_dir))
        ):
            raise RuntimeError("Saved install render repair lost its exact generated authority")
        _bind_repair_admission(
            saved,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )
        return saved
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    chart = snapshot.charts.get("monitoringDashboards")
    if chart is None or chart.digest not in SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS:
        return None
    existing = _files(paths.flux_dir)
    values_docs = _documents(existing[VALUES_FILE])
    values = yaml.safe_load(values_docs[0]["data"]["values.yaml"])
    if values.get("soperator", {}).get("monitoringDashboards", {}).get("enabled") is not True:
        return None
    if (
        local_scheduling_journal != scheduling_journal
        or scheduling_journal.get("lastCompletedStage") != "gated"
        or scheduling_journal.get("actions") != []
    ):
        raise RuntimeError("Install dashboard repair requires the exact unchanged scheduling gate")
    previous_spec_sha256 = str(scheduling_journal.get("operationSpecSha256") or "")
    predecessors = []
    for path in paths.reports_dir.glob("soperator-release-reconcile-*.json"):
        row = read_owner_only_json(path, label="Soperator reconcile predecessor")
        if (
            isinstance(row, Mapping)
            and _digest(row.get("operation", {}).get("spec")) == previous_spec_sha256
        ):
            predecessors.append(row)
    if len(predecessors) != 1:
        raise RuntimeError("Install dashboard repair requires one exact reconcile predecessor")
    predecessor = predecessors[0]
    spec = predecessor["operation"]["spec"]
    transitions = predecessor.get("transitions", [])
    if (
        predecessor.get("status") != "running"
        or spec.get("strategy") != "install"
        or spec.get("current_release") != ""
        or spec.get("target_release") != snapshot.release
        or spec.get("target_ref") != target_ref
        or spec.get("intervention_generation") != 0
        or [(row.get("phase"), row.get("status")) for row in transitions]
        != [
            ("resolve-immutable-sources", "complete"),
            ("establish-boot-storage-barrier", "complete"),
            ("apply-declarative-release", "running"),
        ]
        or spec.get("desired_values_sha256") != _file_hashes(existing)[VALUES_FILE]
        or spec.get("adapter_sha256") != _file_hashes(existing)["soperator-nebius-adapter.yaml"]
        or spec.get("stage_plan_sha256")
        != soperator_reconcile_stage_plan_sha256(
            strategy="install", rendered_graph_sha256=soperator_stage_plan_sha256(paths)
        )
    ):
        raise RuntimeError("Install dashboard repair is outside its original pre-main frontier")
    graph_map = next(
        doc
        for doc in _documents(existing[GRAPH_FILE])
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
    )
    graph = json.loads(graph_map["data"]["graph.json"])
    main_rows = [row for row in graph["releases"] if row.get("isMain") is True]
    if len(main_rows) != 1:
        raise RuntimeError("Install dashboard repair has no exact main release")
    main = _kube_get(
        ["helmrelease", main_rows[0]["releaseName"], "-n", "flux-system"],
        env=env,
        kube_context=kube_context,
    )
    if (
        main is None
        or main.get("metadata", {}).get("name") != main_rows[0]["releaseName"]
        or main.get("metadata", {}).get("namespace") != "flux-system"
        or not main.get("metadata", {}).get("uid")
        or main.get("metadata", {}).get("deletionTimestamp")
        or main.get("metadata", {}).get("labels", {}).get("soperator.nebius.ai/release-graph")
        != "nebius-cxcli"
        or main.get("spec", {}).get("chartRef", {}).get("name") != main_rows[0]["sourceName"]
        or main.get("spec", {}).get("chartRef", {}).get("kind") != main_rows[0]["sourceKind"]
        or main.get("spec", {}).get("suspend") is not True
        or main.get("status", {}).get("lastAttemptedRevision")
        or main.get("status", {}).get("history")
    ):
        raise RuntimeError("Install dashboard repair cannot run after the main release started")
    crd = _kube_get(
        ["crd", "slurmclusters.slurm.nebius.ai"], env=env, kube_context=kube_context, optional=True
    )
    if crd is not None:
        clusters = _kube_get(
            ["slurmclusters.slurm.nebius.ai", "-A"], env=env, kube_context=kube_context
        )
        if clusters is None or clusters.get("items") != []:
            raise RuntimeError("Install dashboard repair requires no existing SlurmCluster")
    failed = _kube_get(
        ["helmrelease", DASHBOARD_RELEASE, "-n", "flux-system"], env=env, kube_context=kube_context
    )
    if failed is None:
        raise RuntimeError("Install dashboard failure evidence is missing")
    uid = validate_failed_dashboard(failed, release=snapshot.release, source_digest=chart.digest)
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    dashboard_docs = render_soperator_monitoring_dashboard_documents(
        {"observability": {"enabled": True}},
        release=snapshot,
        source_root=Path(frozen.source.source_dir),
    )
    candidate = dashboard_repair_candidate(
        existing,
        release=snapshot.release,
        chart_digest=chart.digest,
        dashboards=dashboard_docs,
    )
    repair = {
        "schema": REPAIR_REASON,
        "targetRef": target_ref,
        "previousOperationSpecSha256": previous_spec_sha256,
        "interventionGeneration": 1,
        "predecessorReceipt": predecessor,
        "predecessorReceiptSha256": _digest(predecessor),
        "retiredRelease": {"namespace": "flux-system", "name": DASHBOARD_RELEASE, "uid": uid},
        "previousFiles": _file_hashes(existing),
        "replacementFiles": _file_hashes(candidate),
    }
    transaction = ProjectBundleTransaction(paths.project_dir)
    updates = {paths.flux_dir / name: content for name, content in candidate.items()}
    updates[repair_path] = (json.dumps(repair, sort_keys=True, indent=2) + "\n").encode()
    snapshots = transaction.snapshot_preimages(updates)
    for name, content in existing.items():
        if snapshots[paths.flux_dir / name].content != content:
            raise RuntimeError("Generated install files changed during render repair")
    if snapshots[repair_path].content is not None:
        raise RuntimeError("Install render repair authority appeared concurrently")
    _bind_repair_admission(
        repair,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
        create=True,
    )
    assert_authority()
    transaction.commit(
        updates, expected_preimages={path: snap.sha256 for path, snap in snapshots.items()}
    )
    return repair
