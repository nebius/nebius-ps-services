"""Immutable operation identity and cluster-visible anchor for Soperator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_cache import locked_cache_entry
from .soperator_failures import SoperatorMainWorkloadIdentity, SoperatorSafetyPauseError
from .soperator_operation_lock import SoperatorLeaseAuthority
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_release import (
    SoperatorReleaseSnapshot,
    load_soperator_release_snapshot,
    normalize_soperator_release_selector,
    soperator_release_snapshot_path,
    write_soperator_release_snapshot,
)

SOPERATOR_OPERATION_ANCHOR_SCHEMA = "nebius-cxcli.soperator-operation-anchor.v7"
SOPERATOR_RELEASE_INTENT_SCHEMA = "nebius-cxcli.soperator-release-intent.v3"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_IMMUTABLE_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class SoperatorOperationSpec:
    """Immutable, non-secret identity for one release operation."""

    target_ref: str
    ownership: str
    strategy: str
    current_release: str
    target_release: str
    source_contract: str
    target_contract: str
    source_capability_sha256: str
    target_capability_sha256: str
    stage_plan_sha256: str
    release_snapshot_sha256: str
    target_jail_image: str
    target_jail_image_source: str
    nebius_cluster_id: str
    kubernetes_uid: str
    infrastructure_plan_sha256: str
    desired_values_sha256: str
    adapter_sha256: str
    protected_state_sha256: str
    scheduling_sha256: str
    admission_sha256: str
    intervention_generation: int = 0


@dataclass(frozen=True)
class SoperatorReleaseIntent:
    """Durable local authority for one unresolved or interrupted release operation."""

    schema: str
    status: str
    target_ref: str
    requested_selector: str
    ownership: str
    strategy: str
    source_release: str
    target_release: str
    source_contract: str
    target_contract: str
    source_capability_sha256: str
    target_capability_sha256: str
    release_snapshot_sha256: str
    target_jail_image: str
    target_jail_image_source: str
    nebius_cluster_id: str
    kubernetes_uid: str
    infrastructure_receipt_sha256: str
    operation_spec_sha256: str = ""


def soperator_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _yaml_documents(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"required Soperator operation artifact is missing: {path.name}")
    return [
        item
        for item in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]


def _release_graph_contract(paths: ProjectPaths) -> Mapping[str, Any]:
    graph_path = paths.flux_dir / "soperator-release-graph.yaml"
    graph_documents = _yaml_documents(graph_path)
    graph_configmap = next(
        (
            item
            for item in graph_documents
            if item.get("kind") == "ConfigMap"
            and item.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
        ),
        None,
    )
    if not isinstance(graph_configmap, Mapping):
        raise RuntimeError("Soperator release graph ConfigMap is missing")
    graph_data = graph_configmap.get("data")
    raw_graph = graph_data.get("graph.json") if isinstance(graph_data, Mapping) else None
    try:
        graph_contract = json.loads(str(raw_graph or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Soperator release graph contract is invalid") from exc
    if not isinstance(graph_contract, Mapping):
        raise RuntimeError("Soperator release graph contract must be an object")
    return graph_contract


def soperator_stage_plan_sha256(paths: ProjectPaths) -> str:
    """Hash the exact ordered rendered source and HelmRelease execution plan."""

    graph = _release_graph_contract(paths)
    releases = graph.get("releases")
    if not isinstance(releases, list) or not releases:
        raise RuntimeError("Soperator release graph has no rendered stages")
    required = {
        "releaseName",
        "namespace",
        "owner",
        "stage",
        "sourceKind",
        "sourceName",
        "revision",
        "dependencies",
    }
    if any(not isinstance(item, Mapping) or not required.issubset(item) for item in releases):
        raise RuntimeError("Soperator release graph contains an incomplete stage")
    return soperator_sha256(
        {
            "schema": graph.get("schema"),
            "release": graph.get("release"),
            "sourceCommit": graph.get("sourceCommit"),
            "sourceTree": graph.get("sourceTree"),
            "sourceManifestSha256": graph.get("sourceManifestSha256"),
            "releases": releases,
        }
    )


def _operation_stage_plan_sha256(paths: ProjectPaths, *, strategy: str) -> str:
    # Local import avoids a module cycle: the reconciler consumes the operation
    # spec, while this builder also binds the reconciler's exact phase plan.
    from .soperator_release_reconciler import soperator_reconcile_stage_plan_sha256

    return soperator_reconcile_stage_plan_sha256(
        strategy=strategy,
        rendered_graph_sha256=soperator_stage_plan_sha256(paths),
    )


def build_soperator_operation_spec(
    *,
    paths: ProjectPaths,
    target_ref: str,
    ownership: str,
    strategy: str,
    current_release: str,
    target_release: str,
    source_contract: str,
    target_contract: str,
    source_capability_sha256: str,
    target_capability_sha256: str,
    release_snapshot_sha256: str,
    target_jail_image: str,
    target_jail_image_source: str,
    nebius_cluster_id: str,
    kubernetes_uid: str,
    infrastructure_plan_sha256: str,
    scheduling_evidence: object,
    protected_state_evidence: object | None = None,
    admission_evidence: object | None = None,
    intervention_generation: int = 0,
) -> SoperatorOperationSpec:
    """Bind one operation to exact generated, protected, and scheduling inputs."""

    values_path = paths.flux_dir / "configmap-terraform-fluxcd-values.yaml"
    adapter_path = paths.flux_dir / "soperator-nebius-adapter.yaml"
    values_documents = _yaml_documents(values_path)
    if len(values_documents) != 1:
        raise RuntimeError("Soperator values ConfigMap must contain exactly one YAML document")
    graph_contract = _release_graph_contract(paths)
    readiness = graph_contract.get("readiness") if isinstance(graph_contract, Mapping) else None
    protected = readiness.get("storage") if isinstance(readiness, Mapping) else None
    if not isinstance(protected, list) or not protected:
        raise RuntimeError("Soperator operation has no protected storage contract")
    if not _SHA256.fullmatch(str(infrastructure_plan_sha256 or "")):
        raise ValueError("Soperator operation requires an exact infrastructure plan SHA-256")
    for label, digest in (
        ("source capability", source_capability_sha256),
        ("target capability", target_capability_sha256),
    ):
        if not _SHA256.fullmatch(str(digest or "")):
            raise ValueError(f"Soperator operation requires an exact {label} SHA-256")
    if not _IMMUTABLE_IMAGE.fullmatch(str(target_jail_image or "").strip()):
        raise ValueError("Soperator operation requires a digest-addressed target Jail image")
    if target_jail_image_source != "upstream-default":
        raise ValueError("Soperator operation target Jail image source is invalid")
    return SoperatorOperationSpec(
        target_ref=str(target_ref or "").strip(),
        ownership=str(ownership or "").strip().lower(),
        strategy=str(strategy or "").strip(),
        current_release=str(current_release or "").strip(),
        target_release=str(target_release or "").strip(),
        source_contract=str(source_contract or "").strip(),
        target_contract=str(target_contract or "").strip(),
        source_capability_sha256=str(source_capability_sha256),
        target_capability_sha256=str(target_capability_sha256),
        stage_plan_sha256=_operation_stage_plan_sha256(
            paths,
            strategy=str(strategy or "").strip(),
        ),
        release_snapshot_sha256=str(release_snapshot_sha256 or "").strip(),
        target_jail_image=str(target_jail_image or "").strip(),
        target_jail_image_source=target_jail_image_source,
        nebius_cluster_id=str(nebius_cluster_id or "").strip(),
        kubernetes_uid=str(kubernetes_uid or "").strip(),
        infrastructure_plan_sha256=str(infrastructure_plan_sha256),
        desired_values_sha256=_sha256_file(values_path),
        adapter_sha256=_sha256_file(adapter_path),
        protected_state_sha256=soperator_sha256(
            protected if protected_state_evidence is None else protected_state_evidence
        ),
        scheduling_sha256=soperator_sha256(scheduling_evidence),
        admission_sha256=soperator_sha256(
            {"mode": "not-required"} if admission_evidence is None else admission_evidence
        ),
        intervention_generation=intervention_generation,
    )


def _release_intent_path(paths: ProjectPaths, target_ref: str) -> Path:
    token = re.sub(r"[^A-Za-z0-9.-]+", "-", str(target_ref or "default")).strip("-.")
    if not token or token in {".", ".."}:
        raise ValueError("Soperator target does not produce a safe release-intent path")
    return paths.reports_dir / f"soperator-release-intent-{token}.json"


def _release_intent_lock_key(target_ref: str) -> str:
    digest = hashlib.sha256(str(target_ref or "default").encode("utf-8")).hexdigest()
    return f"release-intent-{digest}"


def _write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    write_owner_only_json(path, payload)


def _validate_release_intent(intent: SoperatorReleaseIntent) -> None:
    if intent.schema != SOPERATOR_RELEASE_INTENT_SCHEMA:
        raise RuntimeError("Soperator release intent has an unsupported schema")
    if intent.status not in {"active", "complete"}:
        raise RuntimeError("Soperator release intent has an invalid status")
    for field_name in (
        "target_ref",
        "requested_selector",
        "ownership",
        "strategy",
        "target_release",
        "source_contract",
        "target_contract",
        "nebius_cluster_id",
        "kubernetes_uid",
    ):
        if not str(getattr(intent, field_name) or "").strip():
            raise RuntimeError(f"Soperator release intent field {field_name} is required")
    for field_name in (
        "source_capability_sha256",
        "target_capability_sha256",
        "release_snapshot_sha256",
        "infrastructure_receipt_sha256",
    ):
        if not _SHA256.fullmatch(str(getattr(intent, field_name) or "")):
            raise RuntimeError(f"Soperator release intent field {field_name} is invalid")
    if not _IMMUTABLE_IMAGE.fullmatch(intent.target_jail_image):
        raise RuntimeError("Soperator release intent target Jail image is invalid")
    if intent.target_jail_image_source != "upstream-default":
        raise RuntimeError("Soperator release intent target Jail image source is invalid")
    if intent.operation_spec_sha256 and not _SHA256.fullmatch(intent.operation_spec_sha256):
        raise RuntimeError("Soperator release intent operation spec identity is invalid")


def begin_soperator_release_intent(
    *,
    paths: ProjectPaths,
    target_ref: str,
    requested_selector: str,
    ownership: str,
    strategy: str,
    source_release: str,
    target_release: str,
    source_contract: str,
    target_contract: str,
    source_capability_sha256: str,
    target_capability_sha256: str,
    target_jail_image: str,
    target_jail_image_source: str,
    snapshot: SoperatorReleaseSnapshot,
    nebius_cluster_id: str,
    kubernetes_uid: str,
    infrastructure_receipt_sha256: str,
) -> SoperatorReleaseIntent:
    """Persist the frozen target before canonical config or customer-state mutation."""

    intent = SoperatorReleaseIntent(
        schema=SOPERATOR_RELEASE_INTENT_SCHEMA,
        status="active",
        target_ref=str(target_ref or "").strip(),
        requested_selector=normalize_soperator_release_selector(requested_selector),
        ownership=str(ownership or "").strip().lower(),
        strategy=str(strategy or "").strip(),
        source_release=str(source_release or "").strip(),
        target_release=str(target_release or "").strip(),
        source_contract=str(source_contract or "").strip(),
        target_contract=str(target_contract or "").strip(),
        source_capability_sha256=str(source_capability_sha256 or "").strip(),
        target_capability_sha256=str(target_capability_sha256 or "").strip(),
        release_snapshot_sha256=snapshot.snapshot_sha256,
        target_jail_image=str(target_jail_image or "").strip(),
        target_jail_image_source=str(target_jail_image_source or "").strip(),
        nebius_cluster_id=str(nebius_cluster_id or "").strip(),
        kubernetes_uid=str(kubernetes_uid or "").strip(),
        infrastructure_receipt_sha256=str(infrastructure_receipt_sha256 or "").strip(),
    )
    _validate_release_intent(intent)
    if snapshot.release != intent.target_release or (
        snapshot.capability_sha256 != intent.target_capability_sha256
    ):
        raise RuntimeError("Soperator release intent does not match its frozen snapshot")
    if (
        intent.target_jail_image_source == "upstream-default"
        and intent.target_jail_image != snapshot.populate_jail_image
    ):
        raise RuntimeError("Soperator release intent changed the frozen upstream Jail image")
    path = _release_intent_path(paths, target_ref)
    with locked_cache_entry(paths.reports_dir, _release_intent_lock_key(target_ref)):
        if path.is_file():
            current = _load_release_intent(path)
            if current.status == "active" and current != intent:
                raise RuntimeError(
                    "recovery-required: an unfinished Soperator release intent has different "
                    "immutable inputs"
                )
        write_soperator_release_snapshot(
            soperator_release_snapshot_path(paths.reports_dir, target_ref),
            snapshot,
        )
        _write_private_json(path, asdict(intent))
    return intent


def _load_release_intent(path: Path) -> SoperatorReleaseIntent:
    try:
        payload = read_owner_only_json(path, label="Soperator release intent")
        if not isinstance(payload, Mapping):
            raise TypeError("release intent must be a JSON object")
        intent = SoperatorReleaseIntent(**payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Soperator release intent is invalid") from exc
    _validate_release_intent(intent)
    return intent


def load_local_active_soperator_release_intent(
    *,
    paths: ProjectPaths,
    target_ref: str,
) -> tuple[SoperatorReleaseIntent, SoperatorReleaseSnapshot] | None:
    """Load self-consistent local recovery authority without granting mutation."""

    path = _release_intent_path(paths, target_ref)
    if not path.is_file():
        return None
    intent = _load_release_intent(path)
    if intent.status == "complete":
        return None
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    if (
        snapshot.release != intent.target_release
        or snapshot.snapshot_sha256 != intent.release_snapshot_sha256
        or snapshot.capability_contract != intent.target_contract
        or snapshot.capability_sha256 != intent.target_capability_sha256
    ):
        raise RuntimeError(
            "recovery-required: the unfinished Soperator release intent lost its exact snapshot"
        )
    return intent, snapshot


def load_active_soperator_release_intent(
    *,
    paths: ProjectPaths,
    target_ref: str,
    requested_selector: str,
    ownership: str,
    live_release: str,
    nebius_cluster_id: str,
    kubernetes_uid: str,
) -> tuple[SoperatorReleaseIntent, SoperatorReleaseSnapshot] | None:
    """Return the exact unfinished target; never reinterpret a mutable selector."""

    local_authority = load_local_active_soperator_release_intent(
        paths=paths,
        target_ref=target_ref,
    )
    if local_authority is None:
        return None
    intent, snapshot = local_authority
    expected = (
        intent.target_ref == str(target_ref or "").strip()
        and intent.requested_selector == normalize_soperator_release_selector(requested_selector)
        and intent.ownership == str(ownership or "").strip().lower()
        and intent.nebius_cluster_id == str(nebius_cluster_id or "").strip()
        and intent.kubernetes_uid == str(kubernetes_uid or "").strip()
        and str(live_release or "").strip() in {intent.source_release, intent.target_release}
    )
    if not expected:
        raise RuntimeError(
            "recovery-required: the unfinished Soperator release intent belongs to a "
            "different selector, owner, cluster, or live release"
        )
    return intent, snapshot


def bind_soperator_release_intent_operation(
    *, paths: ProjectPaths, target_ref: str, operation_spec_sha256: str
) -> None:
    """Bind the active frozen intent to the later rendered operation identity."""

    path = _release_intent_path(paths, target_ref)
    intent = _load_release_intent(path)
    if intent.status != "active":
        raise RuntimeError("Soperator release intent is not active")
    if not _SHA256.fullmatch(str(operation_spec_sha256 or "")):
        raise ValueError("Soperator operation spec identity must be an exact SHA-256")
    if intent.operation_spec_sha256 and intent.operation_spec_sha256 != operation_spec_sha256:
        raise RuntimeError("Soperator release intent is bound to a different operation spec")
    _write_private_json(
        path,
        asdict(replace(intent, operation_spec_sha256=operation_spec_sha256)),
    )


def rebind_soperator_release_intent_operation(
    *,
    paths: ProjectPaths,
    target_ref: str,
    previous_operation_spec_sha256: str,
    replacement_operation_spec_sha256: str,
) -> None:
    """CAS-rebind an active intent after an admitted pre-handoff repair epoch."""

    for label, value in (
        ("previous", previous_operation_spec_sha256),
        ("replacement", replacement_operation_spec_sha256),
    ):
        if not _SHA256.fullmatch(str(value or "")):
            raise ValueError(f"Soperator {label} operation spec identity must be an exact SHA-256")
    path = _release_intent_path(paths, target_ref)
    intent = _load_release_intent(path)
    if intent.status != "active":
        raise RuntimeError("Soperator release intent is not active")
    if intent.operation_spec_sha256 == replacement_operation_spec_sha256:
        return
    if intent.operation_spec_sha256 != previous_operation_spec_sha256:
        raise RuntimeError(
            "Soperator release intent repair does not match its previous operation spec"
        )
    _write_private_json(
        path,
        asdict(replace(intent, operation_spec_sha256=replacement_operation_spec_sha256)),
    )


def supersede_soperator_operation_anchor(
    *,
    kube_context: str,
    cluster_id: str,
    previous_operation_spec_sha256: str,
    replacement_operation_spec_sha256: str,
    replacement_spec: SoperatorOperationSpec,
    extra_env: Mapping[str, str] | None = None,
) -> None:
    """CAS-seal the prior cluster anchor before a pre-handoff repair replacement."""

    for label, value in (
        ("previous", previous_operation_spec_sha256),
        ("replacement", replacement_operation_spec_sha256),
    ):
        if not _SHA256.fullmatch(str(value or "")):
            raise ValueError(f"Soperator {label} operation spec identity must be an exact SHA-256")
    cluster_digest = hashlib.sha256(cluster_id.encode("utf-8")).hexdigest()[:10]
    operation_digest = previous_operation_spec_sha256.removeprefix("sha256:")[:10]
    name = f"nebius-cxcli-soperator-op-{cluster_digest}-{operation_digest}"
    env = os.environ.copy()
    env.update(extra_env or {})
    get_result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "--namespace",
            "kube-system",
            "get",
            "configmap",
            name,
            "-o",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if get_result.returncode != 0:
        raise RuntimeError("could not inspect the previous Soperator operation anchor")
    try:
        payload = json.loads(get_result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("previous Soperator operation anchor returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
    resource_version = (
        str(metadata.get("resourceVersion") or "").strip()
        if isinstance(metadata, Mapping)
        else ""
    )
    expected_core = {
        "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
        "operationId": previous_operation_spec_sha256,
        "operationSpecSha256": previous_operation_spec_sha256,
        "clusterId": replacement_spec.nebius_cluster_id,
        "kubernetesUid": replacement_spec.kubernetes_uid,
        "targetRef": replacement_spec.target_ref,
        "ownership": replacement_spec.ownership,
        "strategy": replacement_spec.strategy,
        "currentRelease": replacement_spec.current_release,
        "sourceContract": replacement_spec.source_contract,
        "targetContract": replacement_spec.target_contract,
        "sourceCapabilitySha256": replacement_spec.source_capability_sha256,
        "targetCapabilitySha256": replacement_spec.target_capability_sha256,
        "releaseSnapshotSha256": replacement_spec.release_snapshot_sha256,
        "infrastructureReceiptSha256": replacement_spec.infrastructure_plan_sha256,
        "targetJailImage": replacement_spec.target_jail_image,
        "targetJailImageSource": replacement_spec.target_jail_image_source,
        "targetRelease": replacement_spec.target_release,
    }
    if (
        not isinstance(data, Mapping)
        or not resource_version
        or any(str(data.get(key) or "").strip() != value for key, value in expected_core.items())
    ):
        raise RuntimeError("previous Soperator operation anchor has different immutable inputs")
    status = str(data.get("status") or "").strip()
    if status == "superseded":
        if str(data.get("supersededBy") or "").strip() != replacement_operation_spec_sha256:
            raise RuntimeError("previous Soperator operation anchor has a different replacement")
        return
    if status != "active":
        raise RuntimeError("previous Soperator operation anchor is not active")
    patch = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": resource_version},
        {
            "op": "test",
            "path": "/data/operationId",
            "value": previous_operation_spec_sha256,
        },
        {"op": "test", "path": "/data/status", "value": "active"},
        {"op": "replace", "path": "/data/status", "value": "superseded"},
        {
            "op": "add",
            "path": "/data/supersededBy",
            "value": replacement_operation_spec_sha256,
        },
    ]
    patched = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "--namespace",
            "kube-system",
            "patch",
            "configmap",
            name,
            "--type=json",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if patched.returncode != 0:
        raise RuntimeError("could not supersede the previous Soperator operation anchor")


def complete_soperator_release_intent(*, paths: ProjectPaths, target_ref: str) -> None:
    """Seal the local release intent after the anchored reconcile completes."""

    path = _release_intent_path(paths, target_ref)
    intent = _load_release_intent(path)
    if intent.status != "active" or not intent.operation_spec_sha256:
        raise RuntimeError("Soperator release intent cannot complete before operation binding")
    _write_private_json(path, asdict(replace(intent, status="complete")))


def soperator_operation_anchor_status(
    *,
    kube_context: str,
    cluster_id: str,
    intent: SoperatorReleaseIntent,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    """Read the cluster anchor status for a locally bound release intent."""

    if not intent.operation_spec_sha256:
        return "unbound"
    cluster_digest = hashlib.sha256(cluster_id.encode("utf-8")).hexdigest()[:10]
    operation_digest = intent.operation_spec_sha256.removeprefix("sha256:")[:10]
    name = f"nebius-cxcli-soperator-op-{cluster_digest}-{operation_digest}"
    env = os.environ.copy()
    env.update(extra_env or {})
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "--namespace",
            "kube-system",
            "get",
            "configmap",
            name,
            "-o",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = f"{result.stdout}\n{result.stderr}".lower()
        if "notfound" in detail or "not found" in detail:
            return "missing"
        raise RuntimeError("could not inspect the frozen Soperator operation anchor")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Soperator operation anchor returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    expected = {
        "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
        "operationId": intent.operation_spec_sha256,
        "operationSpecSha256": intent.operation_spec_sha256,
        "clusterId": intent.nebius_cluster_id,
        "kubernetesUid": intent.kubernetes_uid,
        "targetRef": intent.target_ref,
        "ownership": intent.ownership,
        "strategy": intent.strategy,
        "currentRelease": intent.source_release,
        "sourceContract": intent.source_contract,
        "targetContract": intent.target_contract,
        "sourceCapabilitySha256": intent.source_capability_sha256,
        "targetCapabilitySha256": intent.target_capability_sha256,
        "releaseSnapshotSha256": intent.release_snapshot_sha256,
        "infrastructureReceiptSha256": intent.infrastructure_receipt_sha256,
        "targetJailImage": intent.target_jail_image,
        "targetJailImageSource": intent.target_jail_image_source,
        "targetRelease": intent.target_release,
    }
    if not isinstance(data, Mapping) or any(
        str(data.get(key) or "").strip() != value for key, value in expected.items()
    ):
        raise RuntimeError("Soperator operation anchor differs from its frozen release intent")
    status = str(data.get("status") or "").strip()
    if status not in {"active", "complete"}:
        raise RuntimeError("Soperator operation anchor has an invalid status")
    return status


class SoperatorOperationAnchor:
    """ConfigMap binding an active operation to its exact immutable specification."""

    def __init__(
        self,
        *,
        kube_context: str,
        cluster_id: str,
        operation_spec: SoperatorOperationSpec,
        lease_authority: SoperatorLeaseAuthority,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        if not str(kube_context or "").strip():
            raise ValueError("Soperator operation anchor requires an explicit Kubernetes context")
        if str(cluster_id or "").strip() != operation_spec.nebius_cluster_id:
            raise ValueError("Soperator operation anchor cluster identity does not match its spec")
        self.kube_context = str(kube_context).strip()
        self.operation_spec = operation_spec
        self.lease_authority = lease_authority
        self.operation_id = soperator_sha256(asdict(operation_spec))
        self.spec_sha256 = self.operation_id
        cluster_digest = hashlib.sha256(cluster_id.encode("utf-8")).hexdigest()[:10]
        operation_digest = self.operation_id.removeprefix("sha256:")[:10]
        self.name = f"nebius-cxcli-soperator-op-{cluster_digest}-{operation_digest}"
        self._extra_env = dict(extra_env or {})

    def _kubectl(
        self, *args: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(self._extra_env)
        return subprocess.run(
            [
                "kubectl",
                "--context",
                self.kube_context,
                "--namespace",
                "kube-system",
                *args,
            ],
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _expected_data(self, *, status: str = "active") -> dict[str, str]:
        return {
            "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
            "operationId": self.operation_id,
            "operationSpecSha256": self.spec_sha256,
            "clusterId": self.operation_spec.nebius_cluster_id,
            "kubernetesUid": self.operation_spec.kubernetes_uid,
            "targetRef": self.operation_spec.target_ref,
            "ownership": self.operation_spec.ownership,
            "strategy": self.operation_spec.strategy,
            "currentRelease": self.operation_spec.current_release,
            "sourceContract": self.operation_spec.source_contract,
            "targetContract": self.operation_spec.target_contract,
            "sourceCapabilitySha256": self.operation_spec.source_capability_sha256,
            "targetCapabilitySha256": self.operation_spec.target_capability_sha256,
            "stagePlanSha256": self.operation_spec.stage_plan_sha256,
            "releaseSnapshotSha256": self.operation_spec.release_snapshot_sha256,
            "admissionSha256": self.operation_spec.admission_sha256,
            "infrastructureReceiptSha256": self.operation_spec.infrastructure_plan_sha256,
            "targetJailImage": self.operation_spec.target_jail_image,
            "targetJailImageSource": self.operation_spec.target_jail_image_source,
            "targetRelease": self.operation_spec.target_release,
            "leaseName": self.lease_authority.lease_name,
            "leaseUid": self.lease_authority.lease_uid,
            "holderIdentitySha256": self.lease_authority.holder_identity_sha256,
            "fencingEpoch": str(self.lease_authority.fencing_epoch),
            "status": status,
        }

    @staticmethod
    def _resource_version(payload: Mapping[str, object]) -> str:
        metadata = payload.get("metadata")
        value = metadata if isinstance(metadata, Mapping) else {}
        resource_version = str(value.get("resourceVersion") or "").strip()
        if not resource_version:
            raise RuntimeError("Soperator operation anchor has no CAS resourceVersion")
        return resource_version

    @staticmethod
    def _fencing_epoch(data: Mapping[str, object]) -> int:
        raw_epoch = str(data.get("fencingEpoch") or "").strip()
        try:
            epoch = int(raw_epoch)
        except ValueError as exc:
            raise RuntimeError("Soperator operation anchor has invalid fencing authority") from exc
        if epoch < 1 or raw_epoch != str(epoch):
            raise RuntimeError("Soperator operation anchor has invalid fencing authority")
        return epoch

    @staticmethod
    def _authority_tests(
        *,
        data: Mapping[str, object],
        resource_version: str,
        status: str,
    ) -> list[dict[str, str]]:
        tests = [
            {"op": "test", "path": "/metadata/resourceVersion", "value": resource_version},
            {
                "op": "test",
                "path": "/data/operationId",
                "value": str(data.get("operationId") or ""),
            },
        ]
        for key in ("leaseName", "leaseUid", "holderIdentitySha256", "fencingEpoch"):
            tests.append(
                {
                    "op": "test",
                    "path": f"/data/{key}",
                    "value": str(data.get(key) or ""),
                }
            )
        tests.append({"op": "test", "path": "/data/status", "value": status})
        return tests

    def establish(self) -> None:
        existing = self._kubectl("get", "configmap", self.name, "-o", "json")
        if existing.returncode == 0:
            try:
                payload = json.loads(existing.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("Soperator operation anchor returned invalid JSON") from exc
            data = payload.get("data") if isinstance(payload, Mapping) else None
            expected = self._expected_data()
            authority_fields = {
                "leaseUid",
                "holderIdentitySha256",
                "fencingEpoch",
                "status",
            }
            if not isinstance(data, Mapping) or any(
                str(data.get(key) or "").strip() != value
                for key, value in expected.items()
                if key not in authority_fields
            ):
                raise RuntimeError(
                    "target cluster has a foreign or drifted Soperator operation anchor"
                )
            resource_version = self._resource_version(payload)
            status = str(data.get("status") or "")
            if status not in {"active", "complete"}:
                raise RuntimeError("Soperator operation anchor has an invalid status")
            current_epoch = self._fencing_epoch(data)
            current_authority = tuple(
                str(data.get(key) or "").strip()
                for key in ("leaseUid", "holderIdentitySha256", "fencingEpoch")
            )
            expected_authority = tuple(
                expected[key] for key in ("leaseUid", "holderIdentitySha256", "fencingEpoch")
            )
            authority_changed = current_authority != expected_authority
            if authority_changed and self.lease_authority.fencing_epoch <= current_epoch:
                raise RuntimeError(
                    "target cluster has a foreign or newer Soperator operation anchor authority"
                )
            if authority_changed or status == "complete":
                patch = self._authority_tests(
                    data=data,
                    resource_version=resource_version,
                    status=status,
                )
                if authority_changed:
                    patch.extend(
                        (
                            {
                                "op": "replace",
                                "path": "/data/leaseUid",
                                "value": expected["leaseUid"],
                            },
                            {
                                "op": "replace",
                                "path": "/data/holderIdentitySha256",
                                "value": expected["holderIdentitySha256"],
                            },
                            {
                                "op": "replace",
                                "path": "/data/fencingEpoch",
                                "value": expected["fencingEpoch"],
                            },
                        )
                    )
                if status == "complete":
                    patch.append({"op": "replace", "path": "/data/status", "value": "active"})
                reopened = self._kubectl(
                    "patch",
                    "configmap",
                    self.name,
                    "--type=json",
                    "-p",
                    json.dumps(patch, separators=(",", ":")),
                )
                if reopened.returncode != 0:
                    raise RuntimeError("could not reactivate the Soperator operation anchor")
            return
        detail = f"{existing.stdout}\n{existing.stderr}".lower()
        if "notfound" not in detail and "not found" not in detail:
            raise RuntimeError("could not inspect the Soperator operation anchor")
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": self.name,
                "namespace": "kube-system",
                "labels": {
                    "app.kubernetes.io/managed-by": "nebius-cxcli",
                    "app.kubernetes.io/part-of": "soperator",
                },
            },
            "data": self._expected_data(),
        }
        created = self._kubectl(
            "create", "-f", "-", input_text=json.dumps(manifest, sort_keys=True)
        )
        if created.returncode != 0:
            raise RuntimeError("could not create the Soperator operation anchor")

    def assert_held(self) -> None:
        current = self._kubectl("get", "configmap", self.name, "-o", "json")
        if current.returncode != 0:
            raise RuntimeError("Soperator operation anchor is unavailable")
        try:
            payload = json.loads(current.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator operation anchor returned invalid JSON") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        expected = self._expected_data()
        if not isinstance(data, Mapping) or any(
            str(data.get(key) or "").strip() != value
            for key, value in expected.items()
            if key != "status"
        ):
            raise RuntimeError("Soperator operation anchor authority was lost")
        if str(data.get("status") or "") != "active":
            raise RuntimeError("Soperator operation anchor is not active")

    def _active_anchor_payload(self) -> tuple[Mapping[str, object], Mapping[str, object]]:
        current = self._kubectl("get", "configmap", self.name, "-o", "json")
        if current.returncode != 0:
            raise SoperatorSafetyPauseError(
                "Soperator operation anchor is unavailable",
                code="operation-anchor-unavailable",
            )
        try:
            payload = json.loads(current.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SoperatorSafetyPauseError(
                "Soperator operation anchor returned invalid JSON",
                code="operation-anchor-invalid",
            ) from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        expected = self._expected_data()
        if not isinstance(data, Mapping) or any(
            str(data.get(key) or "").strip() != value
            for key, value in expected.items()
            if key != "status"
        ):
            raise SoperatorSafetyPauseError(
                "Soperator operation anchor authority was lost",
                code="operation-anchor-authority-lost",
            )
        if str(data.get("status") or "") != "active":
            raise SoperatorSafetyPauseError(
                "Soperator operation anchor is not active",
                code="operation-anchor-inactive",
            )
        return payload, data

    @staticmethod
    def _main_workload_authority_payload(
        identity: SoperatorMainWorkloadIdentity,
    ) -> dict[str, object]:
        return {
            "apiVersion": identity.api_version,
            "kind": identity.kind,
            "namespace": identity.namespace,
            "name": identity.name,
            "sourceKind": identity.source_kind,
            "sourceName": identity.source_name,
            "sourceRevision": identity.source_revision,
            "uid": identity.uid,
            "generation": identity.generation,
            "observedGeneration": identity.observed_generation,
        }

    @staticmethod
    def _parse_main_workload_authority(raw: str) -> SoperatorMainWorkloadIdentity:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, Mapping):
                raise TypeError
            return SoperatorMainWorkloadIdentity(
                api_version=str(payload["apiVersion"]),
                kind=str(payload["kind"]),
                namespace=str(payload["namespace"]),
                name=str(payload["name"]),
                source_kind=str(payload["sourceKind"]),
                source_name=str(payload["sourceName"]),
                source_revision=str(payload["sourceRevision"]),
                uid=str(payload["uid"]),
                generation=int(payload["generation"]),
                observed_generation=int(payload["observedGeneration"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SoperatorSafetyPauseError(
                "Soperator main-workload authority is invalid",
                code="main-workload-authority-invalid",
            ) from exc

    def freeze_main_workload_authority(
        self,
        identity: SoperatorMainWorkloadIdentity,
    ) -> SoperatorMainWorkloadIdentity:
        """Persist the one exact graph-declared main workload accepted as terminal."""

        payload, data = self._active_anchor_payload()
        authority_payload = self._main_workload_authority_payload(identity)
        authority_json = json.dumps(authority_payload, sort_keys=True, separators=(",", ":"))
        authority_sha256 = soperator_sha256(authority_payload)
        existing_json = str(data.get("mainWorkloadAuthority") or "").strip()
        existing_sha256 = str(data.get("mainWorkloadAuthoritySha256") or "").strip()
        if existing_json or existing_sha256:
            if not existing_json or not existing_sha256:
                raise SoperatorSafetyPauseError(
                    "Soperator main-workload authority conflicts with its frozen identity",
                    code="main-workload-authority-conflict",
                )
            existing = self._parse_main_workload_authority(existing_json)
            if (
                soperator_sha256(self._main_workload_authority_payload(existing))
                != existing_sha256
            ):
                raise SoperatorSafetyPauseError(
                    "Soperator main-workload authority digest does not match its payload",
                    code="main-workload-authority-digest-mismatch",
                )
            if existing == identity:
                return existing
            immutable_existing = replace(existing, generation=1, observed_generation=1)
            immutable_observed = replace(identity, generation=1, observed_generation=1)
            if immutable_existing != immutable_observed or identity.generation <= existing.generation:
                raise SoperatorSafetyPauseError(
                    "Soperator main-workload authority conflicts with its frozen identity",
                    code="main-workload-authority-conflict",
                )
            patch = self._authority_tests(
                data=data,
                resource_version=self._resource_version(payload),
                status="active",
            )
            patch.extend(
                (
                    {
                        "op": "test",
                        "path": "/data/mainWorkloadAuthority",
                        "value": existing_json,
                    },
                    {
                        "op": "test",
                        "path": "/data/mainWorkloadAuthoritySha256",
                        "value": existing_sha256,
                    },
                    {
                        "op": "replace",
                        "path": "/data/mainWorkloadAuthority",
                        "value": authority_json,
                    },
                    {
                        "op": "replace",
                        "path": "/data/mainWorkloadAuthoritySha256",
                        "value": authority_sha256,
                    },
                )
            )
            refined = self._kubectl(
                "patch",
                "configmap",
                self.name,
                "--type=json",
                "-p",
                json.dumps(patch, separators=(",", ":")),
            )
            if refined.returncode != 0:
                raise SoperatorSafetyPauseError(
                    "Soperator main-workload authority generation could not be refined",
                    code="main-workload-authority-cas-failed",
                )
            return identity
        patch = self._authority_tests(
            data=data,
            resource_version=self._resource_version(payload),
            status="active",
        )
        patch.extend(
            (
                {
                    "op": "add",
                    "path": "/data/mainWorkloadAuthority",
                    "value": authority_json,
                },
                {
                    "op": "add",
                    "path": "/data/mainWorkloadAuthoritySha256",
                    "value": authority_sha256,
                },
            )
        )
        frozen = self._kubectl(
            "patch",
            "configmap",
            self.name,
            "--type=json",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        )
        if frozen.returncode != 0:
            raise SoperatorSafetyPauseError(
                "Soperator main-workload authority could not be frozen",
                code="main-workload-authority-cas-failed",
            )
        return identity

    def load_main_workload_authority(self) -> SoperatorMainWorkloadIdentity:
        """Load and authenticate the terminal-failure authority for this operation."""

        _payload, data = self._active_anchor_payload()
        authority_json = str(data.get("mainWorkloadAuthority") or "").strip()
        authority_sha256 = str(data.get("mainWorkloadAuthoritySha256") or "").strip()
        if not authority_json or not authority_sha256:
            raise SoperatorSafetyPauseError(
                "Soperator main-workload authority has not been frozen",
                code="main-workload-authority-missing",
            )
        identity = self._parse_main_workload_authority(authority_json)
        if soperator_sha256(self._main_workload_authority_payload(identity)) != authority_sha256:
            raise SoperatorSafetyPauseError(
                "Soperator main-workload authority digest does not match its payload",
                code="main-workload-authority-digest-mismatch",
            )
        return identity

    def complete(self) -> None:
        current = self._kubectl("get", "configmap", self.name, "-o", "json")
        if current.returncode != 0:
            raise RuntimeError("Soperator operation anchor is unavailable")
        try:
            payload = json.loads(current.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator operation anchor returned invalid JSON") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            raise RuntimeError("Soperator operation anchor authority was lost")
        expected = self._expected_data()
        if any(
            str(data.get(key) or "").strip() != value
            for key, value in expected.items()
            if key != "status"
        ):
            raise RuntimeError("Soperator operation anchor authority was lost")
        patch = self._authority_tests(
            data=data,
            resource_version=self._resource_version(payload),
            status="active",
        )
        patch.append({"op": "replace", "path": "/data/status", "value": "complete"})
        completed = self._kubectl(
            "patch",
            "configmap",
            self.name,
            "--type=json",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        )
        if completed.returncode != 0:
            raise RuntimeError("Soperator operation converged but its anchor could not be sealed")


__all__ = [
    "SOPERATOR_OPERATION_ANCHOR_SCHEMA",
    "SOPERATOR_RELEASE_INTENT_SCHEMA",
    "SoperatorOperationAnchor",
    "SoperatorOperationSpec",
    "SoperatorReleaseIntent",
    "begin_soperator_release_intent",
    "bind_soperator_release_intent_operation",
    "build_soperator_operation_spec",
    "complete_soperator_release_intent",
    "load_active_soperator_release_intent",
    "rebind_soperator_release_intent_operation",
    "soperator_operation_anchor_status",
    "soperator_sha256",
    "soperator_stage_plan_sha256",
    "supersede_soperator_operation_anchor",
]
