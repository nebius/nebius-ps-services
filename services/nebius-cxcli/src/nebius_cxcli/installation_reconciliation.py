"""Fenced, backend-owned reconciliation of stale accepted install bookkeeping.

Current state evidence is deliberately distinct from historical operation success.
No deployment, scheduling, storage or package mutation is performed here.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path

from .deployment_state import digest
from .soperator_operation import SOPERATOR_OPERATION_ANCHOR_SCHEMA

KIND = "accepted-installation-reconciliation"
SCHEMA = "nebius-cxcli.installation-reconciliation.v1"
RECEIPTS = "installationReconciliations"


def pending_reconciliation(record, target_ref):
    active = record.get("active") or {}
    plan = active.get("plan", {})
    accepted = record.get("accepted") or {}
    return (
        plan.get("kind") == KIND
        and plan.get("target") == target_ref
        and active.get("generation") == accepted.get("generation")
        and plan.get("acceptedSha256") == digest(accepted)
    )


def _read(argv, env):
    from .grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV
    from .ordinary_apps import _live_json

    context = env.get(GRAFANA_TARGET_KUBE_CONTEXT_ENV)
    if not context:
        raise RuntimeError("Installation reconciliation requires the verified target context")
    return _live_json(["kubectl", "--context", context, *argv], env)


def anchors(identity, env):
    token = hashlib.sha256(identity["cluster_id"].encode()).hexdigest()[:10]
    payload = _read(["get", "configmaps", "-n", "kube-system", "-o", "json"], env)
    return [
        item
        for item in payload.get("items", [])
        if item.get("metadata", {})
        .get("name", "")
        .startswith(f"nebius-cxcli-soperator-op-{token}-")
    ]


def writable_claim_volume(volume, pod_spec, claims):
    claim = volume.get("persistentVolumeClaim", {})
    if claim.get("claimName") not in claims or claim.get("readOnly") is True:
        return False
    containers = [
        container
        for key in ("containers", "initContainers", "ephemeralContainers")
        for container in pod_spec.get(key, [])
    ]
    mounts = [
        mount
        for container in containers
        for mount in container.get("volumeMounts", [])
        if mount.get("name") == volume.get("name")
    ]
    # A PVC source defaults to writable, but every container can mount it read-only.
    # Missing usage evidence and raw devices remain conservative writer candidates.
    return (
        not mounts
        or any(mount.get("readOnly") is not True for mount in mounts)
        or any(
            device.get("name") == volume.get("name")
            for container in containers
            for device in container.get("volumeDevices", [])
        )
    )


def assert_quiescent(env, storage=None, *, namespace=None, steady_controllers=()):
    """Released leases also require a fresh inventory; unlabeled writers block."""
    evidence = {}
    writer_jobs = set()
    claims = set((storage or {}).get("volumes", {}))
    from .soperator_status_health import owned_by

    for kind in ("jobs", "pods"):
        payload = _read(["get", kind, "-A", "-o", "json"], env)
        if not isinstance(payload.get("items"), list):
            raise RuntimeError("Installation writer inventory is incomplete")
        for item in payload["items"]:
            meta = item.get("metadata", {})
            labels = meta.get("labels", {})
            pod_spec = (
                item.get("spec", {})
                if kind == "pods"
                else item.get("spec", {}).get("template", {}).get("spec", {})
            )
            related = (
                labels.get("app.kubernetes.io/managed-by") == "nebius-cxcli"
                or labels.get("app.kubernetes.io/component") == "populate-jail"
                or "soperator.nebius.ai/protected-data-plane" in labels
                or "slurm.nebius.ai/jail-rootfs-refresh" in labels
                or str(meta.get("name", "")).startswith(("cxcli-", "nebius-cxcli-"))
                or any(owner.get("uid") in writer_jobs for owner in meta.get("ownerReferences", []))
                or (
                    meta.get("namespace") == namespace
                    and any(
                        writable_claim_volume(volume, pod_spec, claims)
                        for volume in pod_spec.get("volumes", [])
                    )
                )
            )
            if not related:
                continue
            if kind == "pods" and any(owned_by(item, owner) for owner in steady_controllers):
                # Only independently observed Slurm controllers may keep the shared root mounted.
                continue
            status = item.get("status", {})
            if not meta.get("uid") or meta.get("deletionTimestamp"):
                raise RuntimeError("Installation writer identity is ambiguous")
            if kind == "jobs":
                writer_jobs.add(meta["uid"])
                terminal = status.get("active", 0) == 0 and any(
                    c.get("type") in {"Complete", "Failed"} and c.get("status") == "True"
                    for c in status.get("conditions", [])
                )
            else:
                terminal = status.get("phase") in {"Succeeded", "Failed"}
                for spec_key, status_key in (
                    ("containers", "containerStatuses"),
                    ("initContainers", "initContainerStatuses"),
                    ("ephemeralContainers", "ephemeralContainerStatuses"),
                ):
                    expected = {c["name"] for c in item.get("spec", {}).get(spec_key, [])}
                    observed = status.get(status_key, [])
                    terminal = (
                        terminal
                        and {c.get("name") for c in observed} == expected
                        and all(
                            c.get("state", {}).get("terminated", {}).get("finishedAt")
                            for c in observed
                        )
                    )
            if not terminal:
                raise RuntimeError(
                    "A previous installation writer has not provably stopped: "
                    + "/".join((kind, str(meta.get("namespace", "")), str(meta.get("name", ""))))
                )
            evidence[meta["uid"]] = digest({"spec": item.get("spec"), "status": status})
    return evidence


def accepted_slurm_release_owners(generation, target_ref, env, storage):
    """Bind generated child releases to the accepted outer chart and exact sources."""
    from .deployment_observation import target_documents
    from .flux_ops import _staged_soperator_outer_release
    from .soperator_status_health import helm_owned

    docs = list(target_documents(generation, target_ref, protected_only=True).values())
    jail_namespaces = {
        doc["metadata"]["namespace"]
        for doc in docs
        if doc.get("kind") == "PersistentVolumeClaim"
        and doc["metadata"]["name"] == storage["activePvcName"]
    }
    if len(jail_namespaces) != 1:
        raise RuntimeError("Accepted Slurm namespace lacks its exact rendered jail claim")
    jail_namespace = next(iter(jail_namespaces))
    graph = next(
        doc
        for doc in docs
        if doc.get("kind") == "ConfigMap"
        and doc["metadata"]["name"] == "nebius-cxcli-soperator-release-graph"
    )
    releases = json.loads(graph["data"]["graph.json"])["releases"]
    outer = _staged_soperator_outer_release(docs, releases)
    spec, meta = outer["spec"], outer["metadata"]
    namespace = spec.get("targetNamespace") or meta["namespace"]
    default_name = ((spec["targetNamespace"] + "-") if spec.get("targetNamespace") else "") + meta[
        "name"
    ]
    name = spec.get("releaseName") or default_name
    if len(name) > 53:
        raise RuntimeError("Accepted outer Helm release requires an explicit unambiguous name")
    live = _read(
        ["get", "helmreleases.helm.toolkit.fluxcd.io", "-n", "flux-system", "-o", "json"], env
    )
    owners, evidence = set(), {}
    for row in releases:
        if row.get("upstreamReleaseName") not in {
            "soperator-fluxcd-slurm-cluster",
            "soperator-fluxcd-nodesets",
        }:
            continue
        matches = [
            item
            for item in live.get("items", [])
            if item.get("metadata", {}).get("name") == row["releaseName"]
        ]
        if len(matches) != 1:
            raise RuntimeError("Accepted Slurm child release identity is unavailable")
        item = matches[0]
        child, child_meta, status = (
            item.get("spec", {}),
            item.get("metadata", {}),
            item.get("status", {}),
        )
        ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
        chart = child.get("chartRef", {})
        if (
            not helm_owned(item, name=name, namespace=namespace)
            or not child_meta.get("uid")
            or child_meta.get("deletionTimestamp")
            or child.get("suspend") is True
            or ready.get("status") != "True"
            or ready.get("observedGeneration", status.get("observedGeneration"))
            != child_meta.get("generation")
            or not child_meta.get("generation")
            or chart.get("name") != row["sourceName"]
            or chart.get("kind") != row["sourceKind"]
            or chart.get("namespace", "flux-system") != "flux-system"
            or child_meta.get("namespace") != row["namespace"]
            or child.get("targetNamespace") != jail_namespace
            or not child.get("releaseName")
        ):
            raise RuntimeError("Accepted Slurm child release ownership or readiness changed")
        owners.add((child["releaseName"], child["targetNamespace"]))
        evidence[child_meta["uid"]] = digest(
            {"metadata": child_meta, "spec": child, "status": status}
        )
    if len(owners) != 2:
        raise RuntimeError("Accepted Slurm release ownership inventory is incomplete")
    return owners, evidence


def steady_slurm_controllers(snapshot, owners, resources):
    """Exempt normal Slurm Pods only through the verified release/controller graph."""
    from .soperator_status_health import helm_owned, owned_by

    clusters = [
        r for r in snapshot.get("soperator_resources", []) if r.get("kind") == "SlurmCluster"
    ]
    if len(clusters) != 1:
        raise RuntimeError("Installation Slurm controller identity is ambiguous")
    cluster = clusters[0]
    if not any(helm_owned(cluster, name=name, namespace=ns) for name, ns in owners if name and ns):
        raise RuntimeError("SlurmCluster ownership differs from the accepted release graph")
    nodesets = [
        item
        for item in snapshot.get("soperator_resources", [])
        if item.get("kind") == "NodeSet"
        and item.get("metadata", {}).get("namespace") == cluster["metadata"]["namespace"]
        and item.get("metadata", {})
        .get("annotations", {})
        .get("slurm.nebius.ai/parental-cluster-ref")
        == cluster["metadata"]["name"]
        and any(helm_owned(item, name=name, namespace=ns) for name, ns in owners if name and ns)
    ]
    verified_uids = {proof["uid"] for proof in resources.values()}
    controllers = [
        item
        for item in snapshot.get("workloads", [])
        if item.get("kind") in {"StatefulSet", "DaemonSet", "Deployment"}
        and (
            owned_by(item, cluster)
            or any(owned_by(item, owner) for owner in nodesets)
            or item.get("metadata", {}).get("uid") in verified_uids
        )
    ]
    controllers.extend(
        item
        for item in snapshot.get("workloads", [])
        if item.get("kind") == "ReplicaSet"
        and any(
            owner.get("kind") == "Deployment" and owned_by(item, owner)
            for owner in list(controllers)
        )
    )
    return controllers


def observe_accepted(cli, state, accepted, target_ref, env, fence):
    from functools import partial

    from .deploy_targets import flux_target_dir
    from .deployment_jail_state import (
        accepted_effective_generation,
        jail_values,
        observe_jail_storage,
    )
    from .deployment_observation import bind_soperator_observation_identity, verify_desired_target
    from .paths import ProjectPaths
    from .soperator_status_collect import collect_status_snapshot
    from .soperator_status_health import maintenance_active, project_status_health

    target = accepted["evidence"]["targets"][target_ref]
    generation = accepted_effective_generation(state, target_ref, target)
    if generation is None:
        raise RuntimeError("Installation reconciliation requires accepted jail evidence")
    fence()
    cli.console.print("Verifying accepted installation ownership and settings.")
    resources = verify_desired_target(
        cli,
        generation=generation,
        target_ref=target_ref,
        kube_env=env,
        protected_only=True,
        documents_projection=partial(
            bind_soperator_observation_identity,
            target_ref=target_ref,
            cluster_id=target["identity"]["cluster_id"],
        ),
    )
    cli.console.print("Verifying accepted shared storage and cluster readiness.")
    context = env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV]
    # Materialize the authenticated generation, never current mutable render files.
    with tempfile.TemporaryDirectory(prefix="cxcli-install-observation-") as directory:
        root = Path(directory)
        paths = ProjectPaths(
            config_path=root / "config.yaml",
            repo_root=root,
            deployments_dir=root,
            project_dir=root,
            generated_dir=root / "generated",
            infra_dir=root / "generated/infra",
            flux_dir=root / "generated/flux",
            reports_dir=root / "generated/reports",
            path_tenant_folder="tenant",
            path_project_folder="project",
        )
        generation.materialize(paths)
        storage = observe_jail_storage(
            cli,
            flux_dir=flux_target_dir(paths, target_ref),
            values=jail_values(generation.manifest["runtime_config"], target_ref),
            kube_context=context,
            kube_env=env,
        )
    if storage != target["jailState"]["storageEvidence"]:
        raise RuntimeError("Accepted installation storage or rootfs identity changed")
    snapshot = collect_status_snapshot(
        kube_context=context,
        identity={"cluster_identity": {"kubernetes_uid": target["identity"]["kubernetes_uid"]}},
        extra_env=env,
    )
    clusters = [
        r for r in snapshot.get("soperator_resources", []) if r.get("kind") == "SlurmCluster"
    ]
    health = project_status_health(snapshot)
    if len(clusters) != 1 or maintenance_active(clusters[0]) or health.overall != "Healthy":
        raise RuntimeError("Accepted installation readiness or restored maintenance is unproven")
    cluster = clusters[0]
    owners, release_proof = accepted_slurm_release_owners(generation, target_ref, env, storage)
    controllers = steady_slurm_controllers(snapshot, owners, resources["resources"])
    fence()
    return {
        "resources": resources,
        "releaseGraph": release_proof,
        "storageSha256": digest(storage),
        "clusterUid": clusters[0]["metadata"]["uid"],
        "ready": True,
        "writers": assert_quiescent(
            env, storage, namespace=cluster["metadata"]["namespace"], steady_controllers=controllers
        ),
    }


def _original(item):
    meta = item.get("metadata", {})
    if not all(meta.get(k) for k in ("name", "uid", "resourceVersion")) or meta.get(
        "deletionTimestamp"
    ):
        raise RuntimeError("Historical installation record identity is incomplete")
    return {
        "name": meta["name"],
        "uid": meta["uid"],
        "resourceVersion": meta["resourceVersion"],
        "data": copy.deepcopy(item.get("data", {})),
    }


def accepted_install_identity(state, accepted, target_ref):
    from .deployment_jail_state import accepted_effective_generation

    target = accepted["evidence"]["targets"][target_ref]
    generation = accepted_effective_generation(state, target_ref, target)
    if generation is None:
        raise RuntimeError("Historical installation lacks accepted generation identity")
    snapshot = json.loads(
        base64.b64decode(
            generation.files[f"reports/soperator-release-snapshot-{target_ref}.json"], validate=True
        )
    )
    rows = [
        row
        for row in generation.manifest["deploy"]["targets"]
        if row.get("target_ref") == target_ref
    ]
    if len(rows) != 1 or snapshot.get("release") != accepted["evidence"].get("release"):
        raise RuntimeError("Historical installation release identity is ambiguous")
    return {
        "ownership": rows[0]["ownership"],
        "sourceContract": "absent",
        "targetContract": snapshot["capability_contract"],
        "targetCapabilitySha256": snapshot["capability_sha256"],
        "releaseSnapshotSha256": snapshot["snapshot_sha256"],
        "targetJailImage": snapshot["populate_jail_image"],
        "targetJailImageSource": "upstream-default",
    }


def _admit(item, accepted, target_ref, bindings):
    original = _original(item)
    data = original["data"]
    target = accepted["evidence"]["targets"][target_ref]
    identity = target["identity"]
    release = accepted["evidence"].get("release")
    cluster_token = hashlib.sha256(identity["cluster_id"].encode()).hexdigest()[:10]
    operation_token = str(data.get("operationId", "")).removeprefix("sha256:")[:10]
    hashes = {
        "operationId",
        "operationSpecSha256",
        "sourceCapabilitySha256",
        "targetCapabilitySha256",
        "stagePlanSha256",
        "releaseSnapshotSha256",
        "admissionSha256",
        "infrastructureReceiptSha256",
        "holderIdentitySha256",
    }
    required = (
        hashes
        | set(bindings)
        | {
            "schema",
            "clusterId",
            "kubernetesUid",
            "targetRef",
            "strategy",
            "currentRelease",
            "targetRelease",
            "leaseName",
            "leaseUid",
            "fencingEpoch",
            "status",
        }
    )
    if (
        original["name"] != f"nebius-cxcli-soperator-op-{cluster_token}-{operation_token}"
        or not required <= set(data)
        or not all(isinstance(value, str) for value in data.values())
        or any(data.get(key) != value for key, value in bindings.items())
        or any(not re.fullmatch(r"sha256:[a-f0-9]{64}", data.get(key, "")) for key in hashes)
        or not data.get("leaseName")
        or not data.get("leaseUid")
        or not re.fullmatch(r"[1-9][0-9]*", data.get("fencingEpoch", ""))
        or data.get("schema") != SOPERATOR_OPERATION_ANCHOR_SCHEMA
        or data.get("status") != "active"
        or data.get("strategy") != "install"
        or data.get("currentRelease")
        or data.get("targetRef") != target_ref
        or data.get("clusterId") != identity["cluster_id"]
        or data.get("kubernetesUid") != identity["kubernetes_uid"]
        or not release
        or data.get("targetRelease") != release
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", data.get("operationId", ""))
        or data.get("operationId") != data.get("operationSpecSha256")
        or item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/managed-by")
        != "nebius-cxcli"
    ):
        raise RuntimeError("Historical operation is outside accepted installation reconciliation")
    return original


def validate_reconciled(item, state, record):
    data, meta = item.get("data", {}), item.get("metadata", {})
    receipt_id = data.get("reconciliationReceipt")
    committed = (record.value.get("accepted") or {}).get("evidence", {}).get(RECEIPTS, {})
    if not isinstance(receipt_id, str) or receipt_id not in committed:
        raise RuntimeError("Installation reconciliation has not committed")
    stored = state.store.read(
        state.prefix + "/installation-reconciliations/" + receipt_id.removeprefix("sha256:")
    )
    if stored is None or digest(stored.value) != receipt_id or committed[receipt_id] != receipt_id:
        raise RuntimeError("Installation reconciliation receipt is unavailable or changed")
    receipt = stored.value
    current_identity = (
        (record.value.get("accepted") or {})
        .get("evidence", {})
        .get("targets", {})
        .get(receipt.get("target"), {})
        .get("identity")
    )
    if (
        receipt.get("schema") != SCHEMA
        or receipt.get("identity") != current_identity
        or receipt.get("mode") != "current-state-attestation"
        or receipt.get("historicalOutcome") != "unknown"
    ):
        raise RuntimeError("Installation reconciliation belongs to another target")
    matches = [
        r
        for r in receipt["originals"]
        if r["uid"] == meta.get("uid") and r["name"] == meta.get("name")
    ]
    if len(matches) != 1 or data != {
        **matches[0]["data"],
        "status": "reconciled",
        "reconciliationReceipt": receipt_id,
    }:
        raise RuntimeError("Reconciled installation record changed")


def reconcile_accepted_installation(cli, state, *, target_ref, env, fence):
    from .ordinary_apps import _validate_live_lifecycle_complete

    fence()
    record = state.read()
    if record is None or not record.value.get("accepted"):
        raise RuntimeError("Installation reconciliation requires accepted deployment authority")
    accepted = record.value["accepted"]
    target = accepted["evidence"]["targets"][target_ref]
    identity = target["identity"]
    active = record.value.get("active")
    if active and not pending_reconciliation(record.value, target_ref):
        raise RuntimeError("Another deployment owns the unfinished installation")
    observed = anchors(identity, env)
    for item in observed:
        if item.get("data", {}).get("status") == "reconciled" and not active:
            validate_reconciled(item, state, record)
    candidates = [i for i in observed if i.get("data", {}).get("status") == "active"]
    if not candidates and not active:
        _validate_live_lifecycle_complete(identity, env, state=state)
        return record
    if not active:
        bindings = accepted_install_identity(state, accepted, target_ref)
        originals = [_admit(item, accepted, target_ref, bindings) for item in candidates]
        proof = observe_accepted(cli, state, accepted, target_ref, env, fence)
        receipt = {
            "schema": SCHEMA,
            "mode": "current-state-attestation",
            "historicalOutcome": "unknown",
            "target": target_ref,
            "identity": identity,
            "acceptedSha256": digest(accepted),
            "generation": accepted["generation"],
            "originals": originals,
            "verification": proof,
        }
        fence()
        record = state.begin(
            state.generation(accepted["generation"]),
            plan={"kind": KIND, "target": target_ref, "acceptedSha256": digest(accepted)},
            recovery={"installationReconciliation": receipt},
        )
    receipt = record.value["active"]["recovery"]["installationReconciliation"]
    if receipt.get("acceptedSha256") != digest(accepted) or receipt.get("identity") != identity:
        raise RuntimeError("Frozen installation reconciliation authority changed")
    # Reprove current state after interruptions, never reuse stale health as admission.
    if active:
        observe_accepted(cli, state, accepted, target_ref, env, fence)
    receipt_id = digest(receipt)
    key = state.prefix + "/installation-reconciliations/" + receipt_id.removeprefix("sha256:")
    existing = state.store.read(key)
    if existing is not None and existing.value != receipt:
        raise RuntimeError("Frozen installation reconciliation receipt changed")
    if existing is None:
        fence()
        state.store.write(key, receipt, etag=None)
    cli.console.print("Reconciling verified installation records.")
    for original in receipt["originals"]:
        fence()
        current = _read(
            ["get", "configmap", original["name"], "-n", "kube-system", "-o", "json"], env
        )
        meta, data = current.get("metadata", {}), current.get("data", {})
        final = {**original["data"], "status": "reconciled", "reconciliationReceipt": receipt_id}
        if meta.get("uid") != original["uid"]:
            raise RuntimeError("Historical installation record was replaced")
        if data == final:
            continue
        if data != original["data"] or meta.get("resourceVersion") != original["resourceVersion"]:
            raise RuntimeError("Historical installation record changed during reconciliation")
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": original["uid"]},
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": original["resourceVersion"],
            },
            {"op": "test", "path": "/data", "value": original["data"]},
            {"op": "replace", "path": "/data", "value": final},
        ]
        fence()
        _read(
            [
                "patch",
                "configmap",
                original["name"],
                "-n",
                "kube-system",
                "--type=json",
                "-p",
                json.dumps(patch),
                "-o",
                "json",
            ],
            env,
        )
    fence()
    observe_accepted(cli, state, accepted, target_ref, env, fence)
    record = state.complete_installation_reconciliation(record, receipt_id=receipt_id)
    for original in receipt["originals"]:
        item = _read(["get", "configmap", original["name"], "-n", "kube-system", "-o", "json"], env)
        validate_reconciled(item, state, record)
    fence()
    _validate_live_lifecycle_complete(identity, env, state=state)
    return record
