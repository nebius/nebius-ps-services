"""Identity receipts for a same-MK8s protected Soperator data-plane upgrade."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from .oci_image import is_immutable_oci_image_reference
from .soperator_infrastructure_identity import (
    SoperatorInfrastructureReceipt,
    soperator_infrastructure_receipt_from_payload,
    verify_soperator_infrastructure_identity,
)
from .soperator_rootfs_manifest import RootfsManifest, rootfs_manifest
from .soperator_upgrade_safety import ProtectedCustomerState

SOPERATOR_PROTECTED_DATA_PLANE_SCHEMA = "nebius-cxcli.soperator-protected-data-plane.v3"
SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION = "nebius-cxcli/admitted-workload-sha256"
SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION = "nebius-cxcli/requested-workload-sha256"
_KUBERNETES_LABEL_VALUE = re.compile(r"[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?")
_ROOTFS_INVENTORY_FUNCTION = r"""
inventory_rootfs() {
  find . -xdev -mindepth 1 \
    ! -path './.nebius-cxcli' \
    ! -path './.nebius-cxcli/*' \
    -exec sh -c '
  set -eu
  for path do
    relative=${path#./}
    if [ -L "$path" ]; then
      kind=l
      digest="$(readlink -- "$path" | sha256sum | awk "{print \$1}")"
    elif [ -f "$path" ]; then
      kind=f
      digest="$(sha256sum -- "$path" | awk "{print \$1}")"
    elif [ -d "$path" ]; then
      kind=d
      digest="$(printf directory | sha256sum | awk "{print \$1}")"
    else
      printf "unsupported rootfs path type: %s\n" "$relative" >&2
      exit 1
    fi
    metadata_digest="$(stat -c "%f:%u:%g" -- "$path" | sha256sum | awk "{print \$1}")"
    encoded="$(printf "%s" "$relative" | base64 | tr -d "\r\n")"
    printf "%s\tsha256:%s\tsha256:%s\t%s\n" "$kind" "$digest" "$metadata_digest" "$encoded"
  done
' sh {} +
}
""".strip()
_ROOTFS_INVENTORY_SCRIPT = f"""
set -eu
cd /mnt/jail
{_ROOTFS_INVENTORY_FUNCTION}
inventory_rootfs | LC_ALL=C sort
""".strip()


@dataclass(frozen=True)
class ProtectedObjectIdentity:
    kind: str
    namespace: str
    name: str
    uid: str
    resource_version: str
    content_sha256: str


@dataclass(frozen=True)
class ProtectedVolumeIdentity:
    role: str
    pvc: ProtectedObjectIdentity
    pv: ProtectedObjectIdentity
    phase: str
    volume_name: str
    reclaim_policy: str
    pvc_spec_sha256: str
    pv_spec_sha256: str


@dataclass(frozen=True)
class PassiveRootfsRecycleResumePolicy:
    """Creation authority for an interrupted inactive-slot cleanup Job."""

    pre_clean_manifest_sha256: str
    pre_clean_inventory_sha256: str
    allow_job_create: bool


@dataclass(frozen=True)
class ProtectedDataPlaneReceipt:
    schema: str
    target_ref: str
    ownership: str
    nebius_cluster_id: str
    kubernetes_uid: str
    infrastructure: SoperatorInfrastructureReceipt
    state_sha256: str
    volumes: tuple[ProtectedVolumeIdentity, ...]
    protected_objects: tuple[ProtectedObjectIdentity, ...]
    home_mount_sha256: str

    @property
    def receipt_sha256(self) -> str:
        return _sha256(asdict(self))

    def as_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["infrastructure"] = self.infrastructure.as_payload()
        return {**payload, "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True)
class ProtectedWorkloadIdentity:
    """Complete admitted Job workload identity used for safe recovery adoption."""

    namespace: str
    name: str
    purpose: str
    operation_id: str
    fence_epoch: str
    workload_sha256: str


_GENERATED_METADATA_FIELDS = frozenset(
    {
        "creationTimestamp",
        "deletionGracePeriodSeconds",
        "deletionTimestamp",
        "generation",
        "managedFields",
        "resourceVersion",
        "selfLink",
        "uid",
    }
)

_GENERATED_JOB_SELECTOR_LABELS = frozenset(
    {
        "batch.kubernetes.io/controller-uid",
        "batch.kubernetes.io/job-name",
        "controller-uid",
        "job-name",
    }
)


def _remove_generated_job_identity_fields(canonical: dict[str, object]) -> None:
    """Remove only Job-controller identity fields that differ across API requests."""

    spec = canonical.get("spec")
    if not isinstance(spec, dict):
        return
    if spec.get("manualSelector") is True:
        return
    spec.pop("selector", None)
    if spec.get("manualSelector") is False:
        spec.pop("manualSelector", None)
    template = spec.get("template")
    if not isinstance(template, dict):
        return
    template_metadata = template.get("metadata")
    if not isinstance(template_metadata, dict):
        return
    for key in _GENERATED_METADATA_FIELDS:
        template_metadata.pop(key, None)
    labels = template_metadata.get("labels")
    if isinstance(labels, dict):
        for key in _GENERATED_JOB_SELECTOR_LABELS:
            labels.pop(key, None)


def bind_protected_workload_identity(
    manifest: Mapping[str, object],
    *,
    requested_workload_sha256: str,
    admitted_workload_sha256: str,
) -> dict[str, object]:
    """Bind a created Job to the requested and server-admitted workload identities."""

    for label, value in (
        ("requested workload SHA-256", requested_workload_sha256),
        ("admitted workload SHA-256", admitted_workload_sha256),
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")):
            raise ValueError(f"protected Soperator {label} is invalid")
    bound = copy.deepcopy(dict(manifest))
    metadata = bound.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("protected Soperator workload metadata is incomplete")
    annotations = metadata.setdefault("annotations", {})
    if not isinstance(annotations, dict):
        raise RuntimeError("protected Soperator workload annotations are malformed")
    annotations[SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION] = requested_workload_sha256
    annotations[SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION] = admitted_workload_sha256
    return bound


def protected_workload_identity(manifest: Mapping[str, object]) -> ProtectedWorkloadIdentity:
    """Hash the complete admitted Job/Pod contract, excluding only server-owned metadata."""

    if str(manifest.get("apiVersion") or "") != "batch/v1" or manifest.get("kind") != "Job":
        raise RuntimeError("protected Soperator workload must be a batch/v1 Job")
    canonical = copy.deepcopy(dict(manifest))
    canonical.pop("status", None)
    metadata = canonical.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("protected Soperator workload metadata is incomplete")
    for key in _GENERATED_METADATA_FIELDS:
        metadata.pop(key, None)
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        annotations.pop(SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION, None)
        annotations.pop(SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION, None)
        if not annotations:
            metadata.pop("annotations", None)
    _remove_generated_job_identity_fields(canonical)
    namespace = _required(metadata.get("namespace"), field="Job namespace")
    name = _required(metadata.get("name"), field="Job name")
    labels = metadata.get("labels")
    label_map = labels if isinstance(labels, Mapping) else {}
    purpose = _required(
        label_map.get("soperator.nebius.ai/protected-data-plane"),
        field="Job purpose",
    )
    operation_id = _required(
        label_map.get("nebius-cxcli/operation-id"),
        field="Job operation id",
    )
    fence_epoch = _required(
        label_map.get("nebius-cxcli/fence-epoch"),
        field="Job fencing epoch",
    )
    spec = canonical.get("spec")
    if not isinstance(spec, Mapping):
        raise RuntimeError("protected Soperator workload spec is incomplete")
    template = spec.get("template")
    if not isinstance(template, Mapping):
        raise RuntimeError("protected Soperator workload pod template is incomplete")
    pod_spec = template.get("spec")
    if not isinstance(pod_spec, Mapping):
        raise RuntimeError("protected Soperator workload pod spec is incomplete")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise RuntimeError("protected Soperator workload must contain exactly one container")
    if pod_spec.get("ephemeralContainers"):
        raise RuntimeError("protected Soperator workload must not contain ephemeral containers")
    return ProtectedWorkloadIdentity(
        namespace=namespace,
        name=name,
        purpose=purpose,
        operation_id=operation_id,
        fence_epoch=fence_epoch,
        workload_sha256=_sha256(canonical),
    )


def protected_job_pod_identity(
    *,
    job: Mapping[str, object],
    pod: Mapping[str, object],
) -> str:
    """Require one Pod to preserve the admitted Job template's execution semantics."""

    job_metadata = job.get("metadata")
    job_metadata_map = job_metadata if isinstance(job_metadata, Mapping) else {}
    job_uid = _required(job_metadata_map.get("uid"), field="Job UID")
    job_spec = job.get("spec")
    job_spec_map = job_spec if isinstance(job_spec, Mapping) else {}
    template = job_spec_map.get("template")
    template_map = copy.deepcopy(dict(template)) if isinstance(template, Mapping) else {}
    pod_metadata = pod.get("metadata")
    pod_metadata_map = pod_metadata if isinstance(pod_metadata, Mapping) else {}
    owner_refs = pod_metadata_map.get("ownerReferences")
    owners = owner_refs if isinstance(owner_refs, list) else []
    if not any(
        isinstance(owner, Mapping)
        and owner.get("kind") == "Job"
        and str(owner.get("uid") or "") == job_uid
        and owner.get("controller") is True
        for owner in owners
    ):
        raise RuntimeError("protected Soperator Pod is not owned by the exact Job UID")
    expected_metadata = template_map.get("metadata")
    expected_metadata_map = (
        copy.deepcopy(dict(expected_metadata)) if isinstance(expected_metadata, Mapping) else {}
    )
    if expected_metadata_map.get("creationTimestamp") is None:
        expected_metadata_map.pop("creationTimestamp", None)
    actual_metadata = {
        key: copy.deepcopy(pod_metadata_map.get(key))
        for key in ("labels", "annotations")
        if key in pod_metadata_map
    }
    for generated_label in _GENERATED_JOB_SELECTOR_LABELS:
        expected_labels = expected_metadata_map.get("labels")
        actual_labels = actual_metadata.get("labels")
        if isinstance(expected_labels, dict):
            expected_labels.pop(generated_label, None)
        if isinstance(actual_labels, dict):
            actual_labels.pop(generated_label, None)
    expected_spec = template_map.get("spec")
    actual_spec = copy.deepcopy(pod.get("spec"))
    if not isinstance(expected_spec, Mapping) or not isinstance(actual_spec, dict):
        raise RuntimeError("protected Soperator Job/Pod spec is incomplete")
    actual_spec.pop("nodeName", None)
    for field, default in (
        ("enableServiceLinks", True),
        ("preemptionPolicy", "PreemptLowerPriority"),
        ("priority", 0),
    ):
        if field not in expected_spec and actual_spec.get(field) == default:
            actual_spec.pop(field, None)
    for service_account_field in ("serviceAccount", "serviceAccountName"):
        if (
            service_account_field not in expected_spec
            and actual_spec.get(service_account_field) == "default"
        ):
            actual_spec.pop(service_account_field, None)
    if "tolerations" not in expected_spec:
        raw_tolerations = actual_spec.get("tolerations")
        if isinstance(raw_tolerations, list):
            remaining_tolerations = [
                item
                for item in raw_tolerations
                if not (
                    isinstance(item, Mapping)
                    and item.get("key")
                    in {
                        "node.kubernetes.io/not-ready",
                        "node.kubernetes.io/unreachable",
                    }
                    and item.get("operator") == "Exists"
                    and item.get("effect") == "NoExecute"
                    and isinstance(item.get("tolerationSeconds"), int)
                    and int(item["tolerationSeconds"]) >= 0
                )
            ]
            if remaining_tolerations:
                actual_spec["tolerations"] = remaining_tolerations
            else:
                actual_spec.pop("tolerations", None)
    if _sha256({"metadata": expected_metadata_map, "spec": expected_spec}) != _sha256(
        {"metadata": actual_metadata, "spec": actual_spec}
    ):
        differing_paths = _json_difference_paths(
            {"metadata": expected_metadata_map, "spec": expected_spec},
            {"metadata": actual_metadata, "spec": actual_spec},
        )
        detail = ", ".join(differing_paths[:16]) or "unknown"
        raise RuntimeError(
            "recovery-required: protected Soperator Pod changed workload identity at " + detail
        )
    return _sha256({"jobUid": job_uid, "podUid": pod_metadata_map.get("uid"), "spec": actual_spec})


def _json_difference_paths(
    expected: object,
    actual: object,
    *,
    prefix: str = "$",
) -> tuple[str, ...]:
    """Return bounded-safe structural paths without exposing compared values."""

    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        paths: list[str] = []
        expected_keys = {str(key) for key in expected}
        actual_keys = {str(key) for key in actual}
        paths.extend(f"{prefix}.{key}" for key in sorted(expected_keys ^ actual_keys))
        for key in sorted(expected_keys & actual_keys):
            paths.extend(
                _json_difference_paths(
                    expected[key],
                    actual[key],
                    prefix=f"{prefix}.{key}",
                )
            )
        return tuple(paths)
    if isinstance(expected, list) and isinstance(actual, list):
        paths = []
        if len(expected) != len(actual):
            paths.append(f"{prefix}.length")
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=False)):
            paths.extend(
                _json_difference_paths(
                    expected_item,
                    actual_item,
                    prefix=f"{prefix}[{index}]",
                )
            )
        return tuple(paths)
    return (prefix,) if expected != actual else ()


def protected_data_plane_receipt_from_payload(
    payload: Mapping[str, object],
) -> ProtectedDataPlaneReceipt:
    raw_volumes = payload.get("volumes")
    raw_objects = payload.get("protected_objects")
    if (
        not isinstance(raw_volumes, Sequence)
        or isinstance(raw_volumes, (str, bytes, bytearray))
        or not isinstance(raw_objects, Sequence)
        or isinstance(raw_objects, (str, bytes, bytearray))
    ):
        raise RuntimeError("protected Soperator data-plane receipt is malformed")

    def object_identity(value: object) -> ProtectedObjectIdentity:
        if not isinstance(value, Mapping):
            raise RuntimeError("protected Soperator object identity is malformed")
        return ProtectedObjectIdentity(
            kind=str(value.get("kind") or ""),
            namespace=str(value.get("namespace") or ""),
            name=_required(value.get("name"), field="object name"),
            uid=_required(value.get("uid"), field="object UID"),
            resource_version=_required(
                value.get("resource_version"), field="object resourceVersion"
            ),
            content_sha256=_required_sha256(
                value.get("content_sha256"), field="object content SHA-256"
            ),
        )

    volumes: list[ProtectedVolumeIdentity] = []
    for item in raw_volumes:
        if not isinstance(item, Mapping):
            raise RuntimeError("protected Soperator volume identity is malformed")
        volumes.append(
            ProtectedVolumeIdentity(
                role=str(item.get("role") or ""),
                pvc=object_identity(item.get("pvc")),
                pv=object_identity(item.get("pv")),
                phase=str(item.get("phase") or ""),
                volume_name=str(item.get("volume_name") or ""),
                reclaim_policy=str(item.get("reclaim_policy") or ""),
                pvc_spec_sha256=_required_sha256(
                    item.get("pvc_spec_sha256"), field="PVC spec SHA-256"
                ),
                pv_spec_sha256=_required_sha256(
                    item.get("pv_spec_sha256"), field="PV spec SHA-256"
                ),
            )
        )
    raw_infrastructure = payload.get("infrastructure")
    infrastructure_payload = (
        dict(raw_infrastructure) if isinstance(raw_infrastructure, Mapping) else {}
    )
    if "receipt_sha256" not in infrastructure_payload:
        infrastructure_payload["receipt_sha256"] = _sha256(infrastructure_payload)
    receipt = ProtectedDataPlaneReceipt(
        schema=str(payload.get("schema") or ""),
        target_ref=_required(payload.get("target_ref"), field="target ref"),
        ownership=str(payload.get("ownership") or ""),
        nebius_cluster_id=_required(payload.get("nebius_cluster_id"), field="cluster id"),
        kubernetes_uid=_required(payload.get("kubernetes_uid"), field="Kubernetes UID"),
        infrastructure=soperator_infrastructure_receipt_from_payload(infrastructure_payload),
        state_sha256=_required_sha256(payload.get("state_sha256"), field="state SHA-256"),
        volumes=tuple(volumes),
        protected_objects=tuple(object_identity(item) for item in raw_objects),
        home_mount_sha256=_required_sha256(
            payload.get("home_mount_sha256"), field="home mount SHA-256"
        ),
    )
    if receipt.schema != SOPERATOR_PROTECTED_DATA_PLANE_SCHEMA:
        raise RuntimeError("protected Soperator data-plane receipt has an unsupported schema")
    if payload.get("receipt_sha256") != receipt.receipt_sha256:
        raise RuntimeError("protected Soperator data-plane receipt was modified")
    return receipt


def resolve_admitted_protected_data_plane_baseline(
    *,
    admitted_receipt_sha256: str,
    observed_receipt: ProtectedDataPlaneReceipt,
    admitted_receipt_payload: Mapping[str, object] | None = None,
    journal_receipt_payload: Mapping[str, object] | None = None,
    allow_home_mount_transport_transition: bool = False,
) -> tuple[ProtectedDataPlaneReceipt, bool]:
    """Recover the exact admitted baseline without equating it to a fresh observation.

    The transport-transition option is for a bound first-adoption recovery whose
    target graph has already rebound the admitted persistent ``/home`` path.  It
    never changes the stored baseline and leaves all durable identities under
    the ordinary handoff checks.
    """

    admitted_sha256 = _required_sha256(
        admitted_receipt_sha256,
        field="admitted protected data-plane receipt SHA-256",
    )
    admitted_baseline = (
        protected_data_plane_receipt_from_payload(admitted_receipt_payload)
        if admitted_receipt_payload is not None
        else None
    )
    if admitted_baseline is not None and admitted_baseline.receipt_sha256 != admitted_sha256:
        raise RuntimeError(
            "recovery-required: protected data-plane admission payload differs from its "
            "frozen digest"
        )
    normalize_journal = False
    if journal_receipt_payload is not None:
        baseline = protected_data_plane_receipt_from_payload(journal_receipt_payload)
        if baseline.receipt_sha256 != admitted_sha256:
            raise RuntimeError(
                "recovery-required: protected data-plane journal differs from the admitted "
                "preimage"
            )
        raw_infrastructure = journal_receipt_payload.get("infrastructure")
        normalize_journal = not isinstance(raw_infrastructure, Mapping) or not str(
            raw_infrastructure.get("receipt_sha256") or ""
        ).strip()
    elif admitted_baseline is not None:
        baseline = admitted_baseline
    else:
        baseline = observed_receipt
        if baseline.receipt_sha256 != admitted_sha256:
            raise RuntimeError(
                "recovery-required: the admitted protected data-plane baseline is unavailable"
            )
    verify_protected_data_plane_handoff(
        before=baseline,
        after=observed_receipt,
        require_retained=False,
        allow_home_mount_transport_transition=(
            allow_home_mount_transport_transition
        ),
    )
    return baseline, normalize_journal


def readmit_unbound_protected_data_plane_baseline(
    *,
    admitted_receipt_sha256: str,
    journal_receipt_payload: Mapping[str, object],
    observed_receipt: ProtectedDataPlaneReceipt,
    frozen_infrastructure: SoperatorInfrastructureReceipt,
    operation_spec_sha256: str,
    scheduling_actions: Sequence[object],
) -> ProtectedDataPlaneReceipt:
    """Adopt a pre-bind journal after proving no admitted mutation could have started."""

    _required_sha256(
        admitted_receipt_sha256,
        field="previous admitted protected data-plane receipt SHA-256",
    )
    if str(operation_spec_sha256 or "").strip():
        raise RuntimeError(
            "recovery-required: a bound operation cannot re-admit protected state"
        )
    if scheduling_actions:
        raise RuntimeError(
            "recovery-required: scheduling mutations prevent protected-state re-admission"
        )
    baseline = protected_data_plane_receipt_from_payload(journal_receipt_payload)
    verify_soperator_infrastructure_identity(
        before=frozen_infrastructure,
        after=baseline.infrastructure,
    )
    verify_protected_data_plane_handoff(
        before=baseline,
        after=observed_receipt,
        require_retained=False,
    )
    return baseline


def build_protected_data_plane_receipt(
    *,
    state: ProtectedCustomerState,
    target_ref: str,
    ownership: str,
    nebius_cluster_id: str,
    kubernetes_uid: str,
    infrastructure: SoperatorInfrastructureReceipt,
    expected_volumes: Sequence[ProtectedVolumeIdentity] | None = None,
) -> ProtectedDataPlaneReceipt:
    """Bind one complete live capture to exact protected Kubernetes objects."""

    if not state.complete:
        unavailable = sorted(
            str(name)
            for name, value in state.sections.items()
            if isinstance(value, Mapping) and value.get("available") is False
        )
        detail = ", ".join(unavailable) if unavailable else "unknown read-only probe"
        raise RuntimeError(
            "protected Soperator state capture is incomplete; unavailable sections: " + detail
        )
    normalized_ownership = str(ownership or "").strip().lower()
    if normalized_ownership not in {"managed", "onboarded"}:
        raise ValueError("protected Soperator ownership must be managed or onboarded")
    cluster_id = _required(nebius_cluster_id, field="Nebius cluster id")
    cluster_uid = _required(kubernetes_uid, field="kube-system namespace UID")
    if (
        infrastructure.nebius_cluster_id != cluster_id
        or infrastructure.kubernetes_uid != cluster_uid
    ):
        raise RuntimeError("protected infrastructure receipt belongs to another MK8s cluster")

    pvcs = _items(state.sections.get("pvcs"))
    pvs = _items(state.sections.get("pvs"))
    sfs_by_role = (
        {item.role: item for item in infrastructure.storage.sfs.filesystems}
        if infrastructure.storage.sfs is not None
        else {}
    )
    expected_by_role = {item.role: item for item in expected_volumes or ()}
    volumes = tuple(
        _resolve_volume(
            role,
            pvcs=pvcs,
            pvs=pvs,
            expected_pvc_names=(
                (expected_by_role[role].pvc.name,)
                if role in expected_by_role
                else ((sfs_by_role[role].pvc_names or None) if role in sfs_by_role else None)
            ),
            expected_pv_names=(
                (expected_by_role[role].pv.name,)
                if role in expected_by_role
                else ((sfs_by_role[role].pv_names or None) if role in sfs_by_role else None)
            ),
        )
        for role in ("jail", "controller-spool", "accounting")
    )
    protected_objects = tuple(
        sorted(
            (
                _object_identity(item, expected_kind="Secret")
                for item in _items(state.sections.get("secrets"))
                if _is_protected_secret(item)
            ),
            key=lambda item: (item.namespace, item.name),
        )
    )
    if not protected_objects:
        raise RuntimeError("no protected Soperator Secret identities were discovered")
    runtime = state.sections.get("slurm_runtime")
    runtime_map = runtime if isinstance(runtime, Mapping) else {}
    home = runtime_map.get("home_mount")
    home_map = home if isinstance(home, Mapping) else {}
    home_hash = str(home_map.get("stdout_sha256") or "").strip()
    if not home_map.get("available") or not re.fullmatch(r"sha256:[0-9a-f]{64}", home_hash):
        raise RuntimeError("VM-based NFS /home identity could not be proved from the live jail")

    return ProtectedDataPlaneReceipt(
        schema=SOPERATOR_PROTECTED_DATA_PLANE_SCHEMA,
        target_ref=_required(target_ref, field="target ref"),
        ownership=normalized_ownership,
        nebius_cluster_id=cluster_id,
        kubernetes_uid=cluster_uid,
        infrastructure=infrastructure,
        state_sha256=state.content_hash,
        volumes=volumes,
        protected_objects=protected_objects,
        home_mount_sha256=home_hash,
    )


def verify_protected_data_plane_handoff(
    *,
    before: ProtectedDataPlaneReceipt,
    after: ProtectedDataPlaneReceipt,
    require_retained: bool = True,
    allow_home_mount_transport_transition: bool = False,
) -> dict[str, object]:
    """Require infrastructure and customer-owned object identities to be unchanged.

    A first rootfs-slot adoption can intentionally replace the legacy way that
    ``/home`` is mounted with the admitted persistent-path PVC.  In that one
    workflow the caller may defer the transport-level ``findmnt`` digest check
    to the exact persistent path-to-PVC consumer proof.  The protected storage,
    PV/PVC, Secret, cluster, and infrastructure identities remain immutable.
    """

    fixed_fields = [
        "schema",
        "target_ref",
        "ownership",
        "nebius_cluster_id",
        "kubernetes_uid",
    ]
    if not allow_home_mount_transport_transition:
        fixed_fields.append("home_mount_sha256")
    drift = [field for field in fixed_fields if getattr(before, field) != getattr(after, field)]
    infrastructure = verify_soperator_infrastructure_identity(
        before=before.infrastructure,
        after=after.infrastructure,
    )
    before_volumes = {item.role: item for item in before.volumes}
    after_volumes = {item.role: item for item in after.volumes}
    if before_volumes.keys() != after_volumes.keys():
        drift.append("volume roles")
    for role in sorted(before_volumes.keys() & after_volumes.keys()):
        left = before_volumes[role]
        right = after_volumes[role]
        if (
            left.pvc.uid,
            left.pv.uid,
            left.volume_name,
            left.pvc_spec_sha256,
            left.pv_spec_sha256,
        ) != (
            right.pvc.uid,
            right.pv.uid,
            right.volume_name,
            right.pvc_spec_sha256,
            right.pv_spec_sha256,
        ):
            drift.append(f"{role} PV/PVC identity")
        if right.phase != "Bound" or (require_retained and right.reclaim_policy != "Retain"):
            drift.append(f"{role} binding/retention")
    before_objects = {
        (item.kind, item.namespace, item.name): (item.uid, item.content_sha256)
        for item in before.protected_objects
        if item.kind != "Secret" or _is_protected_secret_name(item.name)
    }
    after_objects = {
        (item.kind, item.namespace, item.name): (item.uid, item.content_sha256)
        for item in after.protected_objects
        if item.kind != "Secret" or _is_protected_secret_name(item.name)
    }
    if any(after_objects.get(key) != identity for key, identity in before_objects.items()):
        drift.append("protected Secret identities")
    if drift:
        raise RuntimeError(
            "protected Soperator data-plane handoff changed immutable state: "
            + ", ".join(sorted(set(drift)))
        )
    return {
        "status": "verified",
        "beforeReceiptSha256": before.receipt_sha256,
        "afterReceiptSha256": after.receipt_sha256,
        "clusterIdentitySha256": _sha256(
            {
                "nebiusClusterId": after.nebius_cluster_id,
                "kubernetesUid": after.kubernetes_uid,
            }
        ),
        "protectedVolumeCount": len(after.volumes),
        "protectedObjectCount": len(after.protected_objects),
        "infrastructureReceiptSha256": after.infrastructure.receipt_sha256,
        "homeMountTransport": (
            "persistent-pvc-consumer-proof-required"
            if allow_home_mount_transport_transition
            and before.home_mount_sha256 != after.home_mount_sha256
            else "unchanged"
        ),
        "advisories": infrastructure["advisories"],
    }


def retention_patch_contract(
    receipt: ProtectedDataPlaneReceipt,
) -> tuple[dict[str, object], ...]:
    """Return UID/resourceVersion-guarded JSON patches for non-Retain PVs."""

    patches: list[dict[str, object]] = []
    for volume in receipt.volumes:
        if volume.reclaim_policy == "Retain":
            continue
        patches.append(
            {
                "role": volume.role,
                "pvName": volume.pv.name,
                "pvUid": volume.pv.uid,
                "pvResourceVersion": volume.pv.resource_version,
                "operations": [
                    {"op": "test", "path": "/metadata/uid", "value": volume.pv.uid},
                    {
                        "op": "test",
                        "path": "/metadata/resourceVersion",
                        "value": volume.pv.resource_version,
                    },
                    {
                        "op": "replace",
                        "path": "/spec/persistentVolumeReclaimPolicy",
                        "value": "Retain",
                    },
                ],
            }
        )
    return tuple(patches)


def rootfs_inventory_job_manifest(
    *,
    namespace: str,
    name: str,
    image: str,
    pvc_name: str,
) -> dict[str, object]:
    """Build a read-only Job that emits only path, kind, and SHA-256 evidence."""

    return _rootfs_job_manifest(
        namespace=namespace,
        name=name,
        image=image,
        pvc_name=pvc_name,
        script=_ROOTFS_INVENTORY_SCRIPT,
        read_only=True,
        purpose="inventory",
    )


def rootfs_cleanup_job_manifest(
    *,
    namespace: str,
    name: str,
    image: str,
    pvc_name: str,
    expected_inventory_sha256: str | None = None,
) -> dict[str, object]:
    """Build a cleanup Job for an operation-owned, non-active passive slot."""

    if expected_inventory_sha256 is None:
        script = """
set -eu
cd /mnt/jail
find . -xdev -mindepth 1 -depth \
  ! -path './.nebius-cxcli' \
  ! -path './.nebius-cxcli/*' \
  -delete
test -z "$(find . -xdev -mindepth 1 \
  ! -path './.nebius-cxcli' \
  ! -path './.nebius-cxcli/*' \
  -print -quit)"
""".strip()
    else:
        expected = _required_sha256(
            expected_inventory_sha256,
            field="passive rootfs expected inventory digest",
        )
        script = f"""
set -eu
cd /mnt/jail
{_ROOTFS_INVENTORY_FUNCTION}
actual="$(inventory_rootfs | LC_ALL=C sort | sha256sum | awk '{{print $1}}')"
if [ "sha256:$actual" != "{expected}" ]; then
  printf '%s\n' 'passive rootfs inventory changed before cleanup' >&2
  exit 42
fi
find . -xdev -mindepth 1 -depth \
  ! -path './.nebius-cxcli' \
  ! -path './.nebius-cxcli/*' \
  -delete
test -z "$(find . -xdev -mindepth 1 \
  ! -path './.nebius-cxcli' \
  ! -path './.nebius-cxcli/*' \
  -print -quit)"
""".strip()

    return _rootfs_job_manifest(
        namespace=namespace,
        name=name,
        image=image,
        pvc_name=pvc_name,
        script=script,
        read_only=False,
        purpose="cleanup-passive",
    )


def passive_rootfs_recycle_resume_policy(
    *,
    stage: Mapping[str, object] | None,
    current_manifest_sha256: str,
    current_inventory_sha256: str,
) -> PassiveRootfsRecycleResumePolicy:
    """Never create a cleanup Job after its sealed pre-clean inventory drifts."""

    current = _required_sha256(
        current_manifest_sha256,
        field="passive rootfs current manifest digest",
    )
    current_inventory = _required_sha256(
        current_inventory_sha256,
        field="passive rootfs current inventory digest",
    )
    if stage is None:
        return PassiveRootfsRecycleResumePolicy(
            pre_clean_manifest_sha256=current,
            pre_clean_inventory_sha256=current_inventory,
            allow_job_create=True,
        )
    status = str(stage.get("status") or "").strip()
    intent = stage.get("intent")
    if status not in {"intent", "complete"} or not isinstance(intent, Mapping):
        raise RuntimeError("recovery-required: passive rootfs recycle stage is invalid")
    pre_clean = _required_sha256(
        intent.get("preCleanManifestSha256"),
        field="passive rootfs pre-clean manifest digest",
    )
    pre_clean_inventory = _required_sha256(
        intent.get("preCleanInventorySha256"),
        field="passive rootfs pre-clean inventory digest",
    )
    return PassiveRootfsRecycleResumePolicy(
        pre_clean_manifest_sha256=pre_clean,
        pre_clean_inventory_sha256=pre_clean_inventory,
        allow_job_create=(
            status == "intent" and current == pre_clean and current_inventory == pre_clean_inventory
        ),
    )


def rootfs_inventory_evidence_sha256(manifest: RootfsManifest) -> str:
    """Hash the exact sorted, content-free line stream emitted by inventory Jobs."""

    kind_map = {"file": "f", "symlink": "l", "directory": "d"}
    lines = sorted(
        "\t".join(
            (
                kind_map[entry.kind],
                entry.digest,
                entry.metadata_digest,
                base64.b64encode(entry.path.removeprefix("/").encode()).decode("ascii"),
            )
        )
        for entry in manifest.entries
    )
    payload = "".join(f"{line}\n" for line in lines).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def bind_protected_job_authority(
    manifest: Mapping[str, object],
    *,
    operation_id: str,
    fence_epoch: int,
    pvc_uid: str,
) -> dict[str, object]:
    """Bind a protected Job and its Pod template to one fenced operation."""

    if fence_epoch < 1:
        raise ValueError("protected Soperator Job requires a positive fencing epoch")
    bound = copy.deepcopy(dict(manifest))
    metadata = bound.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("protected Soperator Job metadata is malformed")
    template = bound.setdefault("spec", {}).setdefault("template", {})  # type: ignore[union-attr]
    if not isinstance(template, dict):
        raise RuntimeError("protected Soperator Job template is malformed")
    template_metadata = template.setdefault("metadata", {})
    if not isinstance(template_metadata, dict):
        raise RuntimeError("protected Soperator Job template metadata is malformed")
    authority_labels = {
        "nebius-cxcli/operation-id": _kubernetes_operation_label(operation_id),
        "nebius-cxcli/fence-epoch": str(fence_epoch),
        "nebius-cxcli/pvc-uid": _required(pvc_uid, field="PVC UID")[:63],
    }
    for target in (metadata, template_metadata):
        labels = target.setdefault("labels", {})
        if not isinstance(labels, dict):
            raise RuntimeError("protected Soperator Job labels are malformed")
        labels.update(authority_labels)
    return bound


def parse_rootfs_inventory_log(*, image: str, output: str) -> RootfsManifest:
    entries: list[dict[str, str]] = []
    kind_map = {"f": "file", "l": "symlink", "d": "directory"}
    for line_number, line in enumerate(str(output or "").splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 4 or fields[0] not in kind_map:
            raise ValueError(f"rootfs inventory line {line_number} is malformed or unsupported")
        try:
            relative = base64.b64decode(fields[3], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"rootfs inventory line {line_number} has an invalid path") from exc
        if not relative or relative.startswith("/") or relative.startswith("../"):
            raise ValueError(f"rootfs inventory line {line_number} escaped the rootfs")
        entries.append(
            {
                "path": "/" + relative,
                "kind": kind_map[fields[0]],
                "digest": fields[1],
                "metadata_digest": fields[2],
            }
        )
    return rootfs_manifest(image=image, entries=entries)


def verify_passive_rootfs_precondition(
    *,
    pvc_name: str,
    manifest: RootfsManifest,
    pods: Mapping[str, object],
    soperator_resources: Mapping[str, object],
    allowed_operation_id: str = "",
) -> dict[str, object]:
    """Require a fresh, logically empty, unconsumed passive slot before writing."""

    consumer_evidence = verify_passive_rootfs_consumers(
        pvc_name=pvc_name,
        pods=pods,
        soperator_resources=soperator_resources,
        allowed_operation_id=allowed_operation_id,
    )

    filesystem_bootstrap = "none"
    if manifest.entries:
        only_empty_filesystem_lost_found = len(manifest.entries) == 1 and (
            manifest.entries[0].path,
            manifest.entries[0].kind,
        ) == ("/lost+found", "directory")
        if not only_empty_filesystem_lost_found:
            raise RuntimeError(
                "protected passive rootfs PVC is not empty before operation-owned population; "
                "refusing destructive cleanup without the matching operation Job journal "
                f"({len(manifest.entries)} unexpected entries)"
            )
        filesystem_bootstrap = "empty-lost-found-directory"
    return {
        "status": "empty-and-unconsumed",
        "manifestSha256": manifest.manifest_sha256,
        "filesystemBootstrap": filesystem_bootstrap,
        "controlMetadataPolicy": "reserved-nebius-cxcli-subtree",
        **consumer_evidence,
    }


def validate_passive_rootfs_preflight_evidence(
    evidence: Mapping[str, object],
) -> dict[str, object]:
    """Validate the exact completed evidence required before passive-slot population."""

    if (
        evidence.get("status") != "empty-and-unconsumed"
        or evidence.get("consumerStatus") != "unconsumed"
        or evidence.get("consumerCount") != 0
        or evidence.get("filesystemBootstrap") not in {"none", "empty-lost-found-directory"}
        or evidence.get("controlMetadataPolicy") != "reserved-nebius-cxcli-subtree"
    ):
        raise RuntimeError(
            "recovery-required: passive target preflight does not prove an empty, unconsumed PVC"
        )
    _required_sha256(evidence.get("manifestSha256"), field="preflight manifest digest")
    _required(evidence.get("jobUid"), field="preflight Job UID")
    _required_sha256(
        evidence.get("admittedWorkloadSha256"),
        field="preflight admitted workload digest",
    )
    return dict(evidence)


def verify_passive_rootfs_consumers(
    *,
    pvc_name: str,
    pods: Mapping[str, object],
    soperator_resources: Mapping[str, object],
    allowed_operation_id: str = "",
) -> dict[str, object]:
    """Require a fresh passive-slot consumer census immediately before a write."""

    normalized_pvc = _required(pvc_name, field="passive rootfs PVC name")
    consumers: list[str] = []
    for item in _kubernetes_items(pods, field="Pod inventory"):
        status = item.get("status")
        phase = str(status.get("phase") or "") if isinstance(status, Mapping) else ""
        if phase in {"Succeeded", "Failed"}:
            continue
        spec = item.get("spec")
        spec_map = spec if isinstance(spec, Mapping) else {}
        volumes = spec_map.get("volumes")
        for volume in volumes if isinstance(volumes, list) else []:
            if not isinstance(volume, Mapping):
                continue
            claim = volume.get("persistentVolumeClaim")
            claim_map = claim if isinstance(claim, Mapping) else {}
            if str(claim_map.get("claimName") or "") == normalized_pvc:
                metadata = item.get("metadata")
                labels = metadata.get("labels") if isinstance(metadata, Mapping) else None
                if (
                    allowed_operation_id
                    and isinstance(labels, Mapping)
                    and labels.get("nebius-cxcli/operation-id")
                    == _kubernetes_operation_label(allowed_operation_id)
                ):
                    break
                consumers.append(_kubernetes_identity(item, fallback_kind="Pod"))
                break
    for item in _kubernetes_items(
        soperator_resources,
        field="Soperator rootfs consumer inventory",
    ):
        spec = item.get("spec")
        if isinstance(spec, Mapping) and _mapping_contains_claim(spec, normalized_pvc):
            consumers.append(_kubernetes_identity(item, fallback_kind="SoperatorResource"))
    if consumers:
        raise RuntimeError(
            "protected passive rootfs PVC is already referenced by live consumers: "
            + ", ".join(sorted(set(consumers)))
        )
    return {
        "consumerStatus": "unconsumed",
        "consumerCount": 0,
    }


def verify_rootfs_persistent_consumers(
    *,
    soperator_resources: Mapping[str, object],
    expected_mounts: Mapping[str, str],
) -> dict[str, object]:
    """Require every jail consumer to use the exact protected path-to-PVC mapping."""

    expected = {
        _required(path, field="persistent mount path"): _required(
            pvc_name,
            field=f"persistent mount PVC for {path}",
        )
        for path, pvc_name in expected_mounts.items()
    }
    if not expected:
        raise RuntimeError("protected rootfs handoff has no persistent mount identities")
    consumers: list[str] = []

    def _require_exact(owner: str, observed: Mapping[str, str]) -> None:
        if dict(observed) != expected:
            raise RuntimeError(
                f"target Soperator {owner} persistent jail submounts do not match the "
                "admitted protected path-to-PVC mapping"
            )
        consumers.append(owner)

    for item in _kubernetes_items(
        soperator_resources,
        field="Soperator persistent rootfs consumer inventory",
    ):
        kind = str(item.get("kind") or "")
        metadata = item.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        owner_name = str(metadata_map.get("name") or "unresolved")
        spec = item.get("spec")
        spec_map = spec if isinstance(spec, Mapping) else {}
        if kind == "SlurmCluster":
            raw_sources = spec_map.get("volumeSources")
            sources: dict[str, str] = {}
            for source in raw_sources if isinstance(raw_sources, list) else []:
                if not isinstance(source, Mapping):
                    continue
                source_name = str(source.get("name") or "").strip()
                claim = source.get("persistentVolumeClaim")
                claim_map = claim if isinstance(claim, Mapping) else {}
                claim_name = str(claim_map.get("claimName") or "").strip()
                if source_name in sources:
                    raise RuntimeError(
                        f"target Soperator SlurmCluster/{owner_name} has duplicate volume "
                        f"source {source_name!r}"
                    )
                if source_name:
                    sources[source_name] = claim_name
            slurm_nodes = spec_map.get("slurmNodes")
            slurm_nodes_map = slurm_nodes if isinstance(slurm_nodes, Mapping) else {}
            role_name = "login"
            role = slurm_nodes_map.get(role_name)
            role_map = role if isinstance(role, Mapping) else {}
            volumes = role_map.get("volumes")
            volumes_map = volumes if isinstance(volumes, Mapping) else {}
            if "jail" in volumes_map or "jailSubMounts" in volumes_map:
                raw_mounts = volumes_map.get("jailSubMounts")
                observed: dict[str, str] = {}
                for mount in raw_mounts if isinstance(raw_mounts, list) else []:
                    if not isinstance(mount, Mapping):
                        continue
                    path = str(mount.get("mountPath") or "").strip()
                    source_name = str(mount.get("volumeSourceName") or "").strip()
                    if not path or path in observed:
                        raise RuntimeError(
                            f"target Soperator SlurmCluster/{owner_name}:{role_name} has "
                            "invalid or duplicate persistent jail submount paths"
                        )
                    observed[path] = sources.get(source_name, "")
                _require_exact(f"SlurmCluster/{owner_name}:{role_name}", observed)
        elif kind == "NodeSet":
            slurmd = spec_map.get("slurmd")
            slurmd_map = slurmd if isinstance(slurmd, Mapping) else {}
            volumes = slurmd_map.get("volumes")
            volumes_map = volumes if isinstance(volumes, Mapping) else {}
            if "jail" not in volumes_map and "jailSubMounts" not in volumes_map:
                continue
            raw_mounts = volumes_map.get("jailSubMounts")
            observed = {}
            for mount in raw_mounts if isinstance(raw_mounts, list) else []:
                if not isinstance(mount, Mapping):
                    continue
                path = str(mount.get("mountPath") or "").strip()
                source = mount.get("volumeSource")
                source_map = source if isinstance(source, Mapping) else {}
                claim = source_map.get("persistentVolumeClaim")
                claim_map = claim if isinstance(claim, Mapping) else {}
                if not path or path in observed:
                    raise RuntimeError(
                        f"target Soperator NodeSet/{owner_name} has invalid or duplicate "
                        "persistent jail submount paths"
                    )
                observed[path] = str(claim_map.get("claimName") or "").strip()
            _require_exact(f"NodeSet/{owner_name}", observed)
    if not consumers:
        raise RuntimeError("target Soperator has no jail consumers with protected submounts")
    return {
        "persistentConsumerStatus": "exact",
        "persistentConsumerCount": len(consumers),
        "persistentMountCount": len(expected),
    }


def _rootfs_job_manifest(
    *,
    namespace: str,
    name: str,
    image: str,
    pvc_name: str,
    script: str,
    read_only: bool,
    purpose: str,
) -> dict[str, object]:
    for label, value in (
        ("namespace", namespace),
        ("name", name),
        ("image", image),
        ("PVC name", pvc_name),
    ):
        _required(value, field=label)
    if not is_immutable_oci_image_reference(image):
        raise ValueError("protected rootfs jobs require digest-addressed images")
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": namespace,
            "name": name,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "soperator.nebius.ai/protected-data-plane": purpose,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 2700,
            "template": {
                "metadata": {"labels": {"soperator.nebius.ai/protected-data-plane": purpose}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "rootfs",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "-c", script],
                            "volumeMounts": [
                                {
                                    "name": "rootfs",
                                    "mountPath": "/mnt/jail",
                                    "readOnly": read_only,
                                }
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": False,
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "rootfs",
                            "persistentVolumeClaim": {
                                "claimName": pvc_name,
                                "readOnly": read_only,
                            },
                        }
                    ],
                },
            },
        },
    }


def _resolve_volume(
    role: str,
    *,
    pvcs: Sequence[Mapping[str, object]],
    pvs: Sequence[Mapping[str, object]],
    expected_pvc_names: Sequence[str] | None = None,
    expected_pv_names: Sequence[str] | None = None,
) -> ProtectedVolumeIdentity:
    candidates = (
        [item for item in pvcs if str(item.get("name") or "").strip() in set(expected_pvc_names)]
        if expected_pvc_names is not None
        else [item for item in pvcs if _matches_volume_role(role, item)]
    )
    if len(candidates) != 1:
        names = sorted(str(item.get("name") or "") for item in candidates)
        raise RuntimeError(
            f"protected {role} PVC identity is ambiguous; expected one Bound PVC, found "
            f"{len(candidates)} ({', '.join(names) or 'none'})"
        )
    pvc_item = candidates[0]
    pvc = _object_identity(pvc_item, expected_kind="PersistentVolumeClaim")
    phase = str(pvc_item.get("phase") or "").strip()
    volume_name = _required(pvc_item.get("volume_name"), field=f"{role} PVC volume")
    if phase != "Bound":
        raise RuntimeError(f"protected {role} PVC {pvc.name} is not Bound")
    if expected_pv_names is not None and volume_name not in set(expected_pv_names):
        raise RuntimeError(
            f"protected {role} PVC {pvc.name} is bound to {volume_name}, which differs "
            "from the infrastructure receipt"
        )
    matching_pvs = [item for item in pvs if str(item.get("name") or "") == volume_name]
    if len(matching_pvs) != 1:
        raise RuntimeError(f"protected {role} PV {volume_name} is missing or ambiguous")
    pv_item = matching_pvs[0]
    claim = pv_item.get("claim")
    claim_map = claim if isinstance(claim, Mapping) else {}
    if (
        str(claim_map.get("namespace") or "") != pvc.namespace
        or str(claim_map.get("name") or "") != pvc.name
    ):
        raise RuntimeError(f"protected {role} PV/PVC claimRef identity does not match")
    return ProtectedVolumeIdentity(
        role=role,
        pvc=pvc,
        pv=_object_identity(pv_item, expected_kind="PersistentVolume"),
        phase=phase,
        volume_name=volume_name,
        reclaim_policy=str(pv_item.get("persistent_volume_reclaim_policy") or "").strip(),
        pvc_spec_sha256=_sha256(
            {
                "storageClass": pvc_item.get("storage_class"),
                "accessModes": pvc_item.get("access_modes"),
                "requestStorage": pvc_item.get("request_storage"),
                "capacityStorage": pvc_item.get("capacity_storage"),
                "selectorSha256": pvc_item.get("selector_hash"),
            }
        ),
        pv_spec_sha256=_sha256(
            {
                "claim": claim_map,
                "storageClass": pv_item.get("storage_class"),
                "capacityStorage": pv_item.get("capacity_storage"),
            }
        ),
    )


def _matches_volume_role(role: str, item: Mapping[str, object]) -> bool:
    name = str(item.get("name") or "").lower()
    labels = item.get("labels")
    label_map = labels if isinstance(labels, Mapping) else {}
    text = " ".join(
        (name, *(str(key) for key in label_map), *(str(value) for value in label_map.values()))
    ).lower()
    if role == "jail":
        return "jail" in text and "slot-" not in text and "home" not in text
    if role == "controller-spool":
        return "controller" in text and "spool" in text
    if role == "accounting":
        return "accounting" in text or "acct-db" in text
    raise ValueError(f"unknown protected volume role: {role}")


def _is_protected_secret(item: Mapping[str, object]) -> bool:
    name = str(item.get("name") or "").lower()
    secret_type = str(item.get("type") or "")
    if secret_type in {"helm.sh/release.v1", "kubernetes.io/service-account-token"}:
        return False
    return _is_protected_secret_name(name)


def _is_protected_secret_name(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if normalized.endswith("-slurmdbd-configs"):
        return False
    return any(
        token in normalized
        for token in ("mariadb", "munge", "password", "ssh", "slurm", "acct")
    )


def _kubernetes_items(
    value: Mapping[str, object],
    *,
    field: str,
) -> tuple[Mapping[str, object], ...]:
    items = value.get("items")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise RuntimeError(f"{field} is malformed")
    return tuple(item for item in items if isinstance(item, Mapping))


def _kubernetes_identity(item: Mapping[str, object], *, fallback_kind: str) -> str:
    metadata = item.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    kind = str(item.get("kind") or fallback_kind).strip() or fallback_kind
    namespace = str(metadata_map.get("namespace") or "").strip()
    name = _required(metadata_map.get("name"), field=f"{kind} name")
    return f"{kind}:{namespace + '/' if namespace else ''}{name}"


def _mapping_contains_claim(value: Mapping[str, object], pvc_name: str) -> bool:
    for key, item in value.items():
        if key == "claimName" and str(item or "") == pvc_name:
            return True
        if isinstance(item, Mapping) and _mapping_contains_claim(item, pvc_name):
            return True
        if isinstance(item, list) and any(
            isinstance(child, Mapping) and _mapping_contains_claim(child, pvc_name)
            for child in item
        ):
            return True
    return False


def _object_identity(item: Mapping[str, object], *, expected_kind: str) -> ProtectedObjectIdentity:
    kind = str(item.get("kind") or "").strip()
    if kind != expected_kind:
        raise RuntimeError(f"expected {expected_kind} identity, found {kind or 'unknown'}")
    return ProtectedObjectIdentity(
        kind=kind,
        namespace=str(item.get("namespace") or "").strip(),
        name=_required(item.get("name"), field=f"{expected_kind} name"),
        uid=_required(item.get("uid"), field=f"{expected_kind} UID"),
        resource_version=_required(
            item.get("resource_version"), field=f"{expected_kind} resourceVersion"
        ),
        content_sha256=_sha256(
            {
                key: item.get(key)
                for key in (
                    "type",
                    "data_keys",
                    "string_data_keys",
                    "data_sha256_by_key",
                    "string_data_sha256_by_key",
                )
                if key in item
            }
        ),
    )


def _items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Mapping) or value.get("available") is not True:
        raise RuntimeError("protected Soperator resource inventory is unavailable")
    items = value.get("items")
    if not isinstance(items, list):
        raise RuntimeError("protected Soperator resource inventory is malformed")
    return tuple(item for item in items if isinstance(item, Mapping))


def _required(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise RuntimeError(f"protected Soperator {field} is missing or invalid")
    return text


def _required_sha256(value: object, *, field: str) -> str:
    text = _required(value, field=field)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        raise RuntimeError(f"protected Soperator {field} must be an exact SHA-256")
    return text


def _kubernetes_operation_label(value: object) -> str:
    operation_id = _required(value, field="operation id")
    digest = re.fullmatch(r"sha256:([0-9a-f]{64})", operation_id)
    label = digest.group(1)[:63] if digest is not None else operation_id
    if _KUBERNETES_LABEL_VALUE.fullmatch(label) is None:
        raise RuntimeError(
            "protected Soperator operation id cannot be represented as a Kubernetes label"
        )
    return label


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ProtectedWorkloadIdentity",
    "ProtectedDataPlaneReceipt",
    "ProtectedObjectIdentity",
    "PassiveRootfsRecycleResumePolicy",
    "ProtectedVolumeIdentity",
    "SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION",
    "SOPERATOR_PROTECTED_DATA_PLANE_SCHEMA",
    "SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION",
    "bind_protected_job_authority",
    "bind_protected_workload_identity",
    "build_protected_data_plane_receipt",
    "protected_job_pod_identity",
    "protected_workload_identity",
    "readmit_unbound_protected_data_plane_baseline",
    "retention_patch_contract",
    "parse_rootfs_inventory_log",
    "passive_rootfs_recycle_resume_policy",
    "protected_data_plane_receipt_from_payload",
    "resolve_admitted_protected_data_plane_baseline",
    "rootfs_cleanup_job_manifest",
    "rootfs_inventory_job_manifest",
    "rootfs_inventory_evidence_sha256",
    "validate_passive_rootfs_preflight_evidence",
    "verify_passive_rootfs_consumers",
    "verify_passive_rootfs_precondition",
    "verify_rootfs_persistent_consumers",
    "verify_protected_data_plane_handoff",
]
