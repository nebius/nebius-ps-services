"""Populate-jail refresh helpers for Soperator upgrade flows."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .soperator_adapter import mount_gate_init_container
from .soperator_jail_mounts import JAIL_LEGACY_ACTIVE_SOURCE, sync_jail_volume_sources
from .soperator_release import SOPERATOR_ADAPTER_MOUNT_IMAGE

POPULATE_JAIL_REFRESH_MODES = frozenset({"auto", "force", "manual"})
POPULATE_JAIL_PASSIVE_SLOT_REFRESH = "populatePassiveSlot"
POPULATE_JAIL_STEADY_MAINTENANCE = "none"
POPULATE_JAIL_JOB_SUFFIX = "populate-jail"
JAIL_ROOTFS_STRATEGY_ACTIVE_PASSIVE = "activePassive"
JAIL_ROOTFS_SLOT_A = "slot-a"
JAIL_ROOTFS_SLOT_B = "slot-b"


class PopulateJailCommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


class PopulateJailCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> PopulateJailCommandResult:
        """Run a command for populate-jail inspection."""


@dataclass(frozen=True)
class PopulateJailSnapshot:
    slurmcluster_name: str = ""
    image: str = ""
    job_name: str = ""
    job_uid: str = ""
    job_complete: bool = False
    job_failed: bool = False
    job_image: str = ""
    active_consumer_pods: tuple[str, ...] = ()
    status: str = "unknown"
    detail: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "slurmcluster_name": self.slurmcluster_name,
            "image": self.image,
            "job_name": self.job_name,
            "job_uid": self.job_uid,
            "job_complete": self.job_complete,
            "job_failed": self.job_failed,
            "job_image": self.job_image,
            "active_consumer_pods": list(self.active_consumer_pods),
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PopulateJailProgress:
    """Bounded, checkpoint-safe observation of one exact populate-jail Job."""

    status: str
    observed_at: str
    job_name: str
    job_uid: str = ""
    job_started_at: str = ""
    deadline_at: str = ""
    pod_name: str = ""
    pod_uid: str = ""
    pod_phase: str = ""
    pod_node: str = ""
    container_state: str = ""
    reason: str = ""
    restart_count: int = 0
    exit_code: int | None = None
    warning_event_reason: str = ""
    log_status: str = "not-requested"
    log_line_count: int = 0
    log_sha256: str = ""
    total_files: int | None = None
    files_restored: int | None = None
    total_bytes: int | None = None
    bytes_restored: int | None = None
    summary_seen: bool = False
    summary_complete: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "observed_at": self.observed_at,
            "job_name": self.job_name,
            "job_uid": self.job_uid,
            "job_started_at": self.job_started_at,
            "deadline_at": self.deadline_at,
            "pod_name": self.pod_name,
            "pod_uid": self.pod_uid,
            "pod_phase": self.pod_phase,
            "pod_node": self.pod_node,
            "container_state": self.container_state,
            "reason": self.reason,
            "restart_count": self.restart_count,
            "exit_code": self.exit_code,
            "warning_event_reason": self.warning_event_reason,
            "log": {
                "status": self.log_status,
                "line_count": self.log_line_count,
                "sha256": self.log_sha256,
            },
            "progress": {
                "total_files": self.total_files,
                "files_restored": self.files_restored,
                "total_bytes": self.total_bytes,
                "bytes_restored": self.bytes_restored,
                "summary_seen": self.summary_seen,
                "summary_complete": self.summary_complete,
            },
        }


@dataclass(frozen=True)
class ActivePassiveJailRootfsSlots:
    active_slot: str
    passive_slot: str
    active_volume_source: str
    passive_volume_source: str
    active_pvc: str
    passive_pvc: str

    def as_payload(self) -> dict[str, str]:
        return {
            "active_slot": self.active_slot,
            "passive_slot": self.passive_slot,
            "active_volume_source": self.active_volume_source,
            "passive_volume_source": self.passive_volume_source,
            "active_pvc": self.active_pvc,
            "passive_pvc": self.passive_pvc,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mutable_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if isinstance(value, dict):
        return value
    replacement: dict[str, Any] = {}
    parent[key] = replacement
    return replacement


def _slot_name(value: Any, fallback: str) -> str:
    slot = str(value or "").strip() or fallback
    if slot not in {JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B}:
        raise RuntimeError(f"jailRootfs slot must be slot-a or slot-b; got {slot!r}.")
    return slot


def _other_slot(slot: str) -> str:
    return JAIL_ROOTFS_SLOT_B if slot == JAIL_ROOTFS_SLOT_A else JAIL_ROOTFS_SLOT_A


def _slot_config(values: Mapping[str, Any], slot: str) -> Mapping[str, Any]:
    jail_rootfs = _mapping(values.get("jailRootfs"))
    slots = _mapping(jail_rootfs.get("slots"))
    return _mapping(slots.get(slot))


def _slot_volume_source(values: Mapping[str, Any], slot: str) -> str:
    return str(_slot_config(values, slot).get("volumeSourceName") or f"jail-rootfs-{slot}").strip()


def _slot_pvc(values: Mapping[str, Any], slot: str) -> str:
    return str(_slot_config(values, slot).get("pvcName") or f"jail-rootfs-{slot}-pvc").strip()


def active_passive_jail_rootfs_slots(values: Mapping[str, Any]) -> ActivePassiveJailRootfsSlots:
    jail_rootfs = _mapping(values.get("jailRootfs"))
    strategy = str(jail_rootfs.get("strategy") or JAIL_ROOTFS_STRATEGY_ACTIVE_PASSIVE).strip()
    if strategy != JAIL_ROOTFS_STRATEGY_ACTIVE_PASSIVE:
        raise RuntimeError("jailRootfs.strategy must be activePassive for rootfs refresh.")
    active_slot = _slot_name(jail_rootfs.get("activeSlot"), JAIL_ROOTFS_SLOT_A)
    passive_slot = _slot_name(jail_rootfs.get("passiveSlot"), _other_slot(active_slot))
    if active_slot == passive_slot:
        raise RuntimeError("jailRootfs.activeSlot and jailRootfs.passiveSlot must differ.")
    return ActivePassiveJailRootfsSlots(
        active_slot=active_slot,
        passive_slot=passive_slot,
        active_volume_source=_slot_volume_source(values, active_slot),
        passive_volume_source=_slot_volume_source(values, passive_slot),
        active_pvc=_slot_pvc(values, active_slot),
        passive_pvc=_slot_pvc(values, passive_slot),
    )


def _copy_mapping_field(source: Mapping[str, Any], key: str, target: dict[str, Any]) -> None:
    value = source.get(key)
    if isinstance(value, Mapping) and value:
        target[key] = copy.deepcopy(dict(value))


def _copy_sequence_field(source: Mapping[str, Any], key: str, target: dict[str, Any]) -> None:
    value = source.get(key)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray) and value:
        target[key] = copy.deepcopy(list(value))


def active_passive_pod_scheduling_fields(
    scheduling: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return Kubernetes pod scheduling fields safe to place on a generated Job."""

    if not isinstance(scheduling, Mapping):
        return {}
    fields: dict[str, Any] = {}
    _copy_mapping_field(scheduling, "affinity", fields)
    _copy_mapping_field(scheduling, "nodeSelector", fields)
    _copy_sequence_field(scheduling, "tolerations", fields)
    priority_class_name = str(
        scheduling.get("priorityClassName") or scheduling.get("priorityClass") or ""
    ).strip()
    if priority_class_name:
        fields["priorityClassName"] = priority_class_name
    return fields


def switch_active_passive_jail_rootfs_values(values: Mapping[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(dict(values))
    slots = active_passive_jail_rootfs_slots(patched)
    jail_rootfs = _mutable_mapping(patched, "jailRootfs")
    jail_rootfs["strategy"] = JAIL_ROOTFS_STRATEGY_ACTIVE_PASSIVE
    jail_rootfs["activeSlot"] = slots.passive_slot
    jail_rootfs["passiveSlot"] = slots.active_slot
    adoption = jail_rootfs.get("adoption")
    if isinstance(adoption, dict) and adoption.get("activeSource") == JAIL_LEGACY_ACTIVE_SOURCE:
        adoption["rollbackSource"] = JAIL_LEGACY_ACTIVE_SOURCE
        adoption["activeSource"] = "slot"
    refresh = _mutable_mapping(jail_rootfs, "refresh")
    refresh.update(
        {
            "mode": POPULATE_JAIL_PASSIVE_SLOT_REFRESH,
            "targetSlot": slots.passive_slot,
            "rollbackSlot": slots.active_slot,
            "status": "switching-consumers",
        }
    )

    slurm_nodes = _mutable_mapping(patched, "slurmNodes")
    # REST and SConfigController follow the chart-owned canonical `jail` alias
    # inside Soperator. Only controller and login expose per-role jail values in
    # the SlurmCluster API; worker NodeSets carry direct PVC references below.
    for role in ("controller", "login"):
        role_values = _mutable_mapping(slurm_nodes, role)
        volumes = _mutable_mapping(role_values, "volumes")
        volumes["jail"] = {"volumeSourceName": slots.passive_volume_source}

    nodesets = patched.get("nodesets")
    if isinstance(nodesets, list):
        for nodeset in nodesets:
            if not isinstance(nodeset, dict):
                continue
            slurmd = _mutable_mapping(nodeset, "slurmd")
            volumes = _mutable_mapping(slurmd, "volumes")
            volumes["jail"] = {"persistentVolumeClaim": {"claimName": slots.passive_pvc}}
    return sync_jail_volume_sources(patched)


def active_passive_populate_jail_job_manifest(
    *,
    namespace: str,
    target_ref: str,
    image: str,
    passive_slot: str,
    passive_pvc: str,
    image_pull_policy: str = "IfNotPresent",
    scheduling: Mapping[str, Any] | None = None,
    active_deadline_seconds: int = 2700,
    operation_purpose: str = "",
    require_mount_receipt: bool = True,
) -> dict[str, Any]:
    target = str(target_ref or "soperator").strip() or "soperator"
    slot_suffix = passive_slot.replace("-", "")
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "populate-jail",
                "image": image,
                "imagePullPolicy": image_pull_policy,
                "env": [{"name": "OVERWRITE", "value": "1"}],
                "securityContext": {
                    "capabilities": {"add": ["SYS_ADMIN", "SETFCAP"]},
                },
                "volumeMounts": [{"name": "jail-rootfs", "mountPath": "/mnt/jail"}],
            }
        ],
        "volumes": [
            {
                "name": "jail-rootfs",
                "persistentVolumeClaim": {"claimName": passive_pvc},
            }
        ],
    }
    if require_mount_receipt:
        pod_spec["initContainers"] = [
            mount_gate_init_container(
                name="populate-jail",
                volume_name="jail-rootfs",
                mount_id="jail",
                filesystem_id="",
                image=SOPERATOR_ADAPTER_MOUNT_IMAGE,
            )
        ]
    pod_spec.update(active_passive_pod_scheduling_fields(scheduling))
    object_labels = {
        "app.kubernetes.io/component": "populate-jail",
        "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
        "slurm.nebius.ai/jail-rootfs-slot": passive_slot,
    }
    pod_labels = {
        "app.kubernetes.io/component": "populate-jail",
        "slurm.nebius.ai/jail-rootfs-slot": passive_slot,
    }
    normalized_purpose = str(operation_purpose or "").strip()
    if normalized_purpose:
        object_labels["soperator.nebius.ai/protected-data-plane"] = normalized_purpose
        pod_labels["soperator.nebius.ai/protected-data-plane"] = normalized_purpose
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": namespace,
            "name": f"{target}-{POPULATE_JAIL_JOB_SUFFIX}-passive-{slot_suffix}",
            "labels": object_labels,
        },
        "spec": {
            "activeDeadlineSeconds": max(int(active_deadline_seconds), 1),
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": pod_spec,
            },
        },
    }


def _kubectl_args(
    *,
    kube_context: str | None,
    namespace: str | None,
    args: Sequence[str],
) -> list[str]:
    command = ["kubectl"]
    context = str(kube_context or "").strip()
    if context:
        command.extend(["--context", context])
    ns = str(namespace or "").strip()
    if ns:
        command.extend(["-n", ns])
    command.extend(str(arg) for arg in args)
    return command


def _run_json(
    runner: PopulateJailCommandRunner,
    args: Sequence[str],
    *,
    timeout_seconds: int,
    check: bool = False,
) -> tuple[dict[str, Any], str]:
    result = runner(args, timeout_seconds=timeout_seconds, check=check)
    if result.returncode != 0 or not str(result.stdout or "").strip():
        detail = str(result.stderr or result.stdout or "").strip()
        return {}, detail
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}, "kubectl returned invalid JSON"
    if not isinstance(payload, dict):
        return {}, "kubectl JSON was not an object"
    return payload, ""


def _metadata_name(resource: Mapping[str, Any]) -> str:
    metadata = resource.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("name", "") or "").strip()


def _metadata_uid(resource: Mapping[str, Any]) -> str:
    metadata = resource.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("uid", "") or "").strip()


def _job_complete(resource: Mapping[str, Any]) -> bool:
    status = resource.get("status")
    spec = resource.get("spec")
    if not isinstance(status, Mapping):
        return False
    conditions = status.get("conditions")
    if isinstance(conditions, Sequence) and not isinstance(conditions, (str, bytes, bytearray)):
        for condition in conditions:
            if not isinstance(condition, Mapping):
                continue
            if condition.get("type") == "Complete" and str(condition.get("status")) == "True":
                return True
    completions = 1
    if isinstance(spec, Mapping):
        try:
            completions = max(int(spec.get("completions") or 1), 1)
        except (TypeError, ValueError):
            completions = 1
    try:
        return int(status.get("succeeded") or 0) >= completions
    except (TypeError, ValueError):
        return False


def _job_failed(resource: Mapping[str, Any]) -> bool:
    status = resource.get("status")
    spec = resource.get("spec")
    if not isinstance(status, Mapping):
        return False
    conditions = status.get("conditions")
    if isinstance(conditions, Sequence) and not isinstance(conditions, (str, bytes, bytearray)):
        for condition in conditions:
            if not isinstance(condition, Mapping):
                continue
            if condition.get("type") == "Failed" and str(condition.get("status")) == "True":
                return True
    backoff_limit = 6
    if isinstance(spec, Mapping):
        try:
            backoff_limit = int(
                spec.get("backoffLimit") if spec.get("backoffLimit") is not None else 6
            )
        except (TypeError, ValueError):
            backoff_limit = 6
    try:
        return int(status.get("failed") or 0) > backoff_limit
    except (TypeError, ValueError):
        return False


def _job_container_image(resource: Mapping[str, Any]) -> str:
    spec = resource.get("spec")
    if not isinstance(spec, Mapping):
        return ""
    template = spec.get("template")
    pod_spec = template.get("spec") if isinstance(template, Mapping) else None
    containers = pod_spec.get("containers") if isinstance(pod_spec, Mapping) else None
    if not isinstance(containers, Sequence) or isinstance(containers, (str, bytes, bytearray)):
        return ""
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        image = str(container.get("image", "") or "").strip()
        if image:
            return image
    return ""


def _timestamp_deadline(value: object, timeout_seconds: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        started = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (started + timedelta(seconds=max(timeout_seconds, 0))).astimezone(UTC).isoformat()


def _owned_job_pods(payload: Mapping[str, Any], job_uid: str) -> tuple[Mapping[str, Any], ...]:
    owned: list[Mapping[str, Any]] = []
    for pod in _items(payload):
        metadata = pod.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        owners = metadata.get("ownerReferences")
        if not isinstance(owners, Sequence) or isinstance(owners, (str, bytes, bytearray)):
            continue
        if any(
            isinstance(owner, Mapping)
            and str(owner.get("kind") or "") == "Job"
            and str(owner.get("uid") or "") == job_uid
            and owner.get("controller") is True
            for owner in owners
        ):
            owned.append(pod)
    return tuple(owned)


def _named_container_status(pod: Mapping[str, Any]) -> Mapping[str, Any]:
    status = pod.get("status")
    statuses = status.get("containerStatuses") if isinstance(status, Mapping) else None
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes, bytearray)):
        return {}
    for container in statuses:
        if isinstance(container, Mapping) and str(container.get("name") or "") == "populate-jail":
            return container
    return {}


def _failed_init_container_reason(pod: Mapping[str, Any]) -> str:
    status = pod.get("status")
    statuses = status.get("initContainerStatuses") if isinstance(status, Mapping) else None
    if not isinstance(statuses, Sequence) or isinstance(statuses, (str, bytes, bytearray)):
        return ""
    for container in statuses:
        if not isinstance(container, Mapping):
            continue
        state = container.get("state")
        terminated = state.get("terminated") if isinstance(state, Mapping) else None
        if not isinstance(terminated, Mapping):
            continue
        exit_code = _non_negative_int(terminated.get("exitCode"))
        if exit_code in {None, 0}:
            continue
        name = str(container.get("name") or "<unknown>").strip()
        reason = str(terminated.get("reason") or "Error").strip()
        return f"init container {name} failed: {reason} (exit {exit_code})"
    return ""


def _non_negative_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _structured_log_progress(log_text: str) -> dict[str, Any]:
    latest: Mapping[str, Any] = {}
    summary: Mapping[str, Any] = {}
    for line in log_text.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        message_type = str(payload.get("message_type") or "")
        if message_type == "status":
            latest = payload
        elif message_type == "summary":
            summary = payload
    source = summary or latest
    total_files = _non_negative_int(source.get("total_files"))
    files_restored = _non_negative_int(source.get("files_restored"))
    total_bytes = _non_negative_int(source.get("total_bytes"))
    bytes_restored = _non_negative_int(source.get("bytes_restored"))
    summary_complete = bool(
        summary
        and total_files is not None
        and files_restored == total_files
        and total_bytes is not None
        and bytes_restored == total_bytes
    )
    return {
        "total_files": total_files,
        "files_restored": files_restored,
        "total_bytes": total_bytes,
        "bytes_restored": bytes_restored,
        "summary_seen": bool(summary),
        "summary_complete": summary_complete,
    }


def inspect_active_passive_populate_jail_progress(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    job_name: str,
    expected_job_uid: str,
    expected_image: str,
    expected_pod_uid: str = "",
    kube_context: str | None = None,
    timeout_seconds: int = 2700,
    log_tail_lines: int = 200,
) -> PopulateJailProgress:
    """Inspect one exact Job and its controller-owned Pod without mutating either."""

    observed_at = datetime.now(UTC).isoformat()
    job, detail = _run_json(
        runner,
        _kubectl_args(
            kube_context=kube_context,
            namespace=namespace,
            args=("get", "job", job_name, "-o", "json"),
        ),
        timeout_seconds=120,
        check=False,
    )
    if not job:
        return PopulateJailProgress(
            status="unavailable",
            observed_at=observed_at,
            job_name=job_name,
            reason="Job status is temporarily unavailable" if detail else "Job was not found",
        )
    job_uid = _metadata_uid(job)
    job_status = job.get("status")
    job_status = job_status if isinstance(job_status, Mapping) else {}
    started_at = str(
        job_status.get("startTime") or (job.get("metadata") or {}).get("creationTimestamp") or ""
    ).strip()
    deadline_at = _timestamp_deadline(started_at, timeout_seconds)
    if expected_job_uid and job_uid != expected_job_uid:
        return PopulateJailProgress(
            status="identity-drift",
            observed_at=observed_at,
            job_name=job_name,
            job_uid=job_uid,
            job_started_at=started_at,
            deadline_at=deadline_at,
            reason=f"expected Job UID {expected_job_uid}, observed {job_uid or '<missing>'}",
        )
    live_image = _job_container_image(job)
    if expected_image and live_image != expected_image:
        return PopulateJailProgress(
            status="contract-drift",
            observed_at=observed_at,
            job_name=job_name,
            job_uid=job_uid,
            job_started_at=started_at,
            deadline_at=deadline_at,
            reason=f"expected image {expected_image}, observed {live_image or '<missing>'}",
        )

    pods, pods_detail = _run_json(
        runner,
        _kubectl_args(
            kube_context=kube_context,
            namespace=namespace,
            args=("get", "pods", "-l", f"job-name={job_name}", "-o", "json"),
        ),
        timeout_seconds=120,
        check=False,
    )
    observed_pods = _items(pods)
    owned_pods = _owned_job_pods(pods, job_uid)
    if len(observed_pods) != len(owned_pods):
        return PopulateJailProgress(
            status="identity-drift",
            observed_at=observed_at,
            job_name=job_name,
            job_uid=job_uid,
            job_started_at=started_at,
            deadline_at=deadline_at,
            reason=(
                "the Job label selector returned a foreign or replacement Pod that is not "
                "controller-owned by the exact checkpointed Job UID"
            ),
        )
    if len(owned_pods) > 1:
        return PopulateJailProgress(
            status="identity-drift",
            observed_at=observed_at,
            job_name=job_name,
            job_uid=job_uid,
            job_started_at=started_at,
            deadline_at=deadline_at,
            reason=f"expected one controller-owned Pod, observed {len(owned_pods)}",
        )

    pod = owned_pods[0] if owned_pods else {}
    pod_name = _metadata_name(pod)
    pod_uid = _metadata_uid(pod)
    if expected_pod_uid and pod_uid != expected_pod_uid:
        return PopulateJailProgress(
            status="identity-drift",
            observed_at=observed_at,
            job_name=job_name,
            job_uid=job_uid,
            job_started_at=started_at,
            deadline_at=deadline_at,
            pod_name=pod_name,
            pod_uid=pod_uid,
            reason=f"expected Pod UID {expected_pod_uid}, observed {pod_uid or '<missing>'}",
        )
    pod_status = pod.get("status") if isinstance(pod, Mapping) else {}
    pod_status = pod_status if isinstance(pod_status, Mapping) else {}
    pod_spec = pod.get("spec") if isinstance(pod, Mapping) else {}
    pod_spec = pod_spec if isinstance(pod_spec, Mapping) else {}
    pod_phase = str(pod_status.get("phase") or "").strip()
    container_status = _named_container_status(pod)
    state = container_status.get("state")
    state = state if isinstance(state, Mapping) else {}
    container_state = ""
    reason = ""
    exit_code: int | None = None
    for candidate in ("waiting", "running", "terminated"):
        value = state.get(candidate)
        if isinstance(value, Mapping):
            container_state = candidate
            reason = str(value.get("reason") or "").strip()
            if candidate == "terminated":
                exit_code = _non_negative_int(value.get("exitCode"))
            break

    warning_event_reason = ""
    if pod_uid and not _job_complete(job):
        events, _ = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=(
                    "get",
                    "events",
                    "--field-selector",
                    f"involvedObject.uid={pod_uid},type=Warning",
                    "-o",
                    "json",
                ),
            ),
            timeout_seconds=60,
            check=False,
        )
        event_items = _items(events)
        if event_items:
            warning_event_reason = str(event_items[-1].get("reason") or "").strip()

    log_status = "not-requested"
    log_text = ""
    if pod_name and container_state in {"running", "terminated"}:
        result = runner(
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=(
                    "logs",
                    f"pod/{pod_name}",
                    "-c",
                    "populate-jail",
                    f"--tail={max(log_tail_lines, 1)}",
                ),
            ),
            timeout_seconds=60,
            check=False,
        )
        if result.returncode == 0:
            log_text = str(result.stdout or "")
            log_status = "collected" if log_text else "empty"
        else:
            log_status = "unavailable"
            if not reason:
                reason = "populate-jail log tail is temporarily unavailable"
    progress = _structured_log_progress(log_text)

    job_failed = _job_failed(job)
    if job_failed or (container_state == "terminated" and exit_code not in {None, 0}):
        status = "failed"
        if job_failed:
            reason = _failed_init_container_reason(pod) or reason or "Job failed"
    elif _job_complete(job) and progress["summary_seen"] and not progress["summary_complete"]:
        status = "failed"
        reason = "structured completion summary reported incomplete file or byte restoration"
    elif _job_complete(job):
        status = "completed"
    elif not pod_name:
        status = "scheduling"
        reason = reason or (
            "Pod status is temporarily unavailable"
            if pods_detail
            else "waiting for the controller-owned Pod"
        )
    elif container_state == "running":
        status = "running"
    elif container_state == "waiting":
        status = "pending"
    elif container_state == "terminated":
        status = "completing"
    else:
        status = "pending"
        reason = reason or pod_phase or "waiting for container state"
    if warning_event_reason and not reason:
        reason = warning_event_reason

    return PopulateJailProgress(
        status=status,
        observed_at=observed_at,
        job_name=job_name,
        job_uid=job_uid,
        job_started_at=started_at,
        deadline_at=deadline_at,
        pod_name=pod_name,
        pod_uid=pod_uid,
        pod_phase=pod_phase,
        pod_node=str(pod_spec.get("nodeName") or "").strip(),
        container_state=container_state,
        reason=reason,
        restart_count=_non_negative_int(container_status.get("restartCount")) or 0,
        exit_code=exit_code,
        warning_event_reason=warning_event_reason,
        log_status=log_status,
        log_line_count=len(log_text.splitlines()),
        log_sha256=hashlib.sha256(log_text.encode()).hexdigest() if log_text else "",
        **progress,
    )


def wait_for_active_passive_populate_jail_job(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    job_name: str,
    expected_image: str,
    expected_job_uid: str = "",
    expected_pod_uid: str = "",
    kube_context: str | None = None,
    timeout_seconds: int = 2700,
    poll_interval_seconds: int = 10,
    on_progress: Callable[[PopulateJailProgress], None] | None = None,
    progress_emit_interval_seconds: int = 30,
    stall_timeout_seconds: int = 300,
    absolute_deadline_at: str = "",
) -> PopulateJailSnapshot:
    invocation_deadline = time.monotonic() + max(timeout_seconds, 0)
    job_name = str(job_name or "").strip()
    last_emitted_status = ""
    last_emitted_at = 0.0
    bound_pod_uid = str(expected_pod_uid or "").strip()
    last_progress_digest = ""
    last_progress_at = time.monotonic()
    while True:
        progress = inspect_active_passive_populate_jail_progress(
            runner,
            namespace=namespace,
            job_name=job_name,
            expected_job_uid=expected_job_uid,
            expected_image=expected_image,
            expected_pod_uid=bound_pod_uid,
            kube_context=kube_context,
            timeout_seconds=timeout_seconds,
        )
        now = time.monotonic()
        if not bound_pod_uid and progress.pod_uid:
            bound_pod_uid = progress.pod_uid
        progress_digest = hashlib.sha256(
            json.dumps(
                {
                    "pod_uid": progress.pod_uid,
                    "pod_phase": progress.pod_phase,
                    "container_state": progress.container_state,
                    "restart_count": progress.restart_count,
                    "total_files": progress.total_files,
                    "files_restored": progress.files_restored,
                    "total_bytes": progress.total_bytes,
                    "bytes_restored": progress.bytes_restored,
                    "summary_seen": progress.summary_seen,
                    "log_sha256": progress.log_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if progress_digest != last_progress_digest:
            last_progress_digest = progress_digest
            last_progress_at = now
        must_emit = progress.status != last_emitted_status or progress.status in {
            "completed",
            "failed",
            "identity-drift",
            "contract-drift",
            "stalled",
        }
        periodic_emit = now - last_emitted_at >= max(progress_emit_interval_seconds, 1)
        if on_progress is not None and (must_emit or periodic_emit):
            on_progress(progress)
            last_emitted_status = progress.status
            last_emitted_at = now
        snapshot = PopulateJailSnapshot(
            job_name=job_name,
            job_uid=progress.job_uid,
            job_complete=progress.status == "completed",
            job_failed=progress.status == "failed",
            job_image=expected_image,
            status="collected" if progress.job_uid else "not_collected",
            detail=progress.reason,
        )
        if progress.status in {"identity-drift", "contract-drift"}:
            raise RuntimeError(
                f"active/passive populate-jail job {job_name} {progress.status}: {progress.reason}"
            )
        if progress.status == "failed":
            detail = f": {progress.reason}" if progress.reason else ""
            raise RuntimeError(f"active/passive populate-jail job {job_name} failed{detail}.")
        if progress.status == "completed":
            return snapshot
        if (
            progress.status in {"running", "pending", "scheduling", "completing"}
            and stall_timeout_seconds > 0
            and now - last_progress_at >= stall_timeout_seconds
        ):
            stalled = replace(
                progress,
                status="stalled",
                reason=(
                    "no Job, Pod, container, restart, structured-copy, or bounded-log "
                    f"progress was observed for {stall_timeout_seconds} seconds; the exact "
                    "Job was left untouched for operator inspection and same-UID resume"
                ),
            )
            if on_progress is not None:
                on_progress(stalled)
            raise RuntimeError(
                f"active/passive populate-jail job {job_name} stalled: {stalled.reason}."
            )
        absolute_deadline_reached = False
        effective_deadline_at = progress.deadline_at or str(absolute_deadline_at or "").strip()
        if effective_deadline_at:
            try:
                absolute_deadline_reached = datetime.now(UTC) >= datetime.fromisoformat(
                    effective_deadline_at
                )
            except ValueError:
                absolute_deadline_reached = False
        if absolute_deadline_reached or time.monotonic() >= invocation_deadline:
            reason = progress.reason or "no additional diagnostic was reported"
            if on_progress is not None:
                on_progress(
                    replace(
                        progress,
                        status="timed-out",
                        reason=(f"{reason}. The exact Job was left untouched for same-UID resume."),
                    )
                )
            raise RuntimeError(
                f"Timed out waiting for active/passive populate-jail job {job_name}; "
                f"last status={progress.status}: {reason}. The exact Job was left untouched."
            )
        time.sleep(max(poll_interval_seconds, 1))


def _items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    items = payload.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def _ready_endpoint_count(payload: Mapping[str, Any]) -> int:
    ready = 0
    for item in _items(payload):
        endpoints = item.get("endpoints")
        if not isinstance(endpoints, Sequence) or isinstance(endpoints, (str, bytes, bytearray)):
            continue
        for endpoint in endpoints:
            if not isinstance(endpoint, Mapping):
                continue
            conditions = endpoint.get("conditions")
            if isinstance(conditions, Mapping) and conditions.get("ready") is True:
                ready += 1
    return ready


def login_service_ready_endpoint_count(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    service_names: Sequence[str],
    kube_context: str | None = None,
) -> int:
    ready = 0
    for service_name in service_names:
        endpoint_slices, _detail = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=(
                    "get",
                    "endpointslices.discovery.k8s.io",
                    "-l",
                    f"kubernetes.io/service-name={service_name}",
                    "-o",
                    "json",
                ),
            ),
            timeout_seconds=120,
            check=False,
        )
        ready += _ready_endpoint_count(endpoint_slices)
    return ready


def observe_login_service_continuity(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    kube_context: str | None = None,
    tcp_probe: Callable[[str, int], bool] | None = None,
) -> dict[str, Any]:
    """Return non-blocking login Service evidence for upgrade progress reporting."""

    try:
        services, detail = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=("get", "services", "-o", "json"),
            ),
            timeout_seconds=120,
            check=False,
        )
        observations: list[dict[str, Any]] = []
        ready_endpoints = 0
        for item in _items(services):
            name = _metadata_name(item)
            if not name or "login" not in name:
                continue
            metadata = _mapping(item.get("metadata"))
            spec = _mapping(item.get("spec"))
            status = _mapping(item.get("status"))
            load_balancer = _mapping(status.get("loadBalancer"))
            ingress = load_balancer.get("ingress")
            ingress_rows = (
                ingress
                if isinstance(ingress, Sequence)
                and not isinstance(ingress, (str, bytes, bytearray))
                else ()
            )
            addresses = sorted(
                {
                    str(row.get("ip") or row.get("hostname") or "").strip()
                    for row in ingress_rows
                    if isinstance(row, Mapping)
                    and str(row.get("ip") or row.get("hostname") or "").strip()
                }
            )
            service_ready = login_service_ready_endpoint_count(
                runner,
                namespace=namespace,
                service_names=(name,),
                kube_context=kube_context,
            )
            ready_endpoints += service_ready
            tcp_reachable: bool | None = None
            if tcp_probe is not None and addresses:
                try:
                    tcp_reachable = any(tcp_probe(address, 22) for address in addresses)
                except Exception:
                    tcp_reachable = None
            observations.append(
                {
                    "name": name,
                    "uid": str(metadata.get("uid") or "").strip(),
                    "type": str(spec.get("type") or "").strip(),
                    "clusterIP": str(spec.get("clusterIP") or "").strip(),
                    "clusterIPs": sorted(
                        {
                            str(value).strip()
                            for value in (
                                spec.get("clusterIPs")
                                if isinstance(spec.get("clusterIPs"), Sequence)
                                and not isinstance(spec.get("clusterIPs"), (str, bytes, bytearray))
                                else ()
                            )
                            if str(value).strip()
                        }
                    ),
                    "ports": sorted(
                        (
                            {
                                "name": str(port.get("name") or "").strip(),
                                "port": int(port.get("port") or 0),
                                "targetPort": str(port.get("targetPort") or "").strip(),
                                "nodePort": int(port.get("nodePort") or 0),
                                "protocol": str(port.get("protocol") or "TCP").strip(),
                            }
                            for port in (
                                spec.get("ports") if isinstance(spec.get("ports"), list) else []
                            )
                            if isinstance(port, Mapping)
                        ),
                        key=lambda item: (
                            str(item["name"]),
                            int(item["port"]),
                            str(item["protocol"]),
                        ),
                    ),
                    "selector": {
                        str(key): str(value)
                        for key, value in sorted(
                            _mapping(spec.get("selector")).items(),
                            key=lambda item: str(item[0]),
                        )
                    },
                    "allocationIds": sorted(
                        {
                            str(value).strip()
                            for key, value in _mapping(metadata.get("annotations")).items()
                            if "allocation" in str(key).lower() and str(value).strip()
                        }
                    ),
                    "ingress": addresses,
                    "readyEndpoints": service_ready,
                    "tcp22Reachable": tcp_reachable,
                }
            )
        if not observations:
            return {
                "status": "unknown",
                "serviceCount": 0,
                "readyEndpoints": 0,
                "reason": "login-service-not-observed",
                "inspectionDetailPresent": bool(detail),
            }
        reachable = any(item["tcp22Reachable"] is True for item in observations)
        status_value = "available" if ready_endpoints > 0 else "degraded"
        return {
            "status": status_value,
            "serviceCount": len(observations),
            "readyEndpoints": ready_endpoints,
            "tcp22Reachable": reachable if any(item["ingress"] for item in observations) else None,
            "services": observations,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": "observer-unavailable",
            "failureType": type(exc).__name__,
        }
