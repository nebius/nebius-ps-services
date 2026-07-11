"""Populate-jail refresh helpers for Soperator upgrade flows."""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .soperator_jail_mounts import JAIL_LEGACY_ACTIVE_SOURCE, sync_jail_volume_sources

POPULATE_JAIL_REFRESH_PHASE_ID = "populate-jail-refresh"
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
class PopulateJailRefreshPlan:
    mode: str
    required: bool
    reason: str
    manual_instruction: str
    before: PopulateJailSnapshot
    after_chart: PopulateJailSnapshot

    def as_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "required": self.required,
            "reason": self.reason,
            "manual_instruction": self.manual_instruction,
            "before": self.before.as_payload(),
            "after_chart": self.after_chart.as_payload(),
        }


@dataclass(frozen=True)
class PopulateJailRefreshResult:
    mode: str
    status: str
    reason: str
    target_image: str
    job_name: str
    job_uid: str
    job_image: str
    maintenance_restored: bool
    detail: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "reason": self.reason,
            "target_image": self.target_image,
            "job_name": self.job_name,
            "job_uid": self.job_uid,
            "job_image": self.job_image,
            "maintenance_restored": self.maintenance_restored,
            "detail": self.detail,
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


def normalize_populate_jail_refresh_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower() or "auto"
    if mode not in POPULATE_JAIL_REFRESH_MODES:
        raise RuntimeError(
            "--populate-jail-refresh must be one of: "
            + ", ".join(sorted(POPULATE_JAIL_REFRESH_MODES))
        )
    return mode


def populate_jail_refresh_values(values: Mapping[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(dict(values))
    patched["maintenance"] = POPULATE_JAIL_STEADY_MAINTENANCE
    populate = patched.get("populateJail")
    if not isinstance(populate, dict):
        populate = {}
        patched["populateJail"] = populate
    populate["overwrite"] = False
    jail_rootfs = patched.get("jailRootfs")
    if not isinstance(jail_rootfs, dict):
        jail_rootfs = {}
        patched["jailRootfs"] = jail_rootfs
    slots = active_passive_jail_rootfs_slots(patched)
    refresh = jail_rootfs.get("refresh")
    if not isinstance(refresh, dict):
        refresh = {}
        jail_rootfs["refresh"] = refresh
    refresh.update(
        {
            "mode": POPULATE_JAIL_PASSIVE_SLOT_REFRESH,
            "targetSlot": slots.passive_slot,
            "rollbackSlot": slots.active_slot,
            "status": "planned",
        }
    )
    return sync_jail_volume_sources(patched)


def populate_jail_steady_state_values(values: Mapping[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(dict(values))
    patched["maintenance"] = POPULATE_JAIL_STEADY_MAINTENANCE
    populate = patched.get("populateJail")
    if not isinstance(populate, dict):
        populate = {}
        patched["populateJail"] = populate
    populate["overwrite"] = False
    return sync_jail_volume_sources(patched)


def populate_jail_manual_instruction(*, namespace: str, slurmcluster_name: str) -> str:
    target = slurmcluster_name or "<slurmcluster>"
    return (
        "Refresh the jail rootfs by populating the passive active/passive slot with "
        "the target populate-jail image, switching the canonical jail volume-source "
        "alias plus controller, SConfigController, login, worker, and REST "
        f"consumers to that slot, and keeping the previous slot available for rollback. Wait for "
        f"job/{target}-{POPULATE_JAIL_JOB_SUFFIX}-passive-<slot> to complete in "
        f"namespace {namespace}, then require every enabled alias consumer to roll out "
        "Ready without letting the login Service reach zero ready endpoints."
    )


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


def _mapping_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


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


def active_passive_populate_jail_job_scheduling(
    values: Mapping[str, Any],
    *,
    target_ref: str = "",
) -> dict[str, Any]:
    populate_jail = _mapping(values.get("populateJail"))
    filter_name = str(populate_jail.get("k8sNodeFilterName") or "").strip()
    scheduling: dict[str, Any] = {}
    if filter_name:
        for item in _mapping_items(values.get("k8sNodeFilters")):
            if str(item.get("name") or "").strip() != filter_name:
                continue
            _copy_mapping_field(item, "affinity", scheduling)
            _copy_mapping_field(item, "nodeSelector", scheduling)
            _copy_sequence_field(item, "tolerations", scheduling)
            break

    priority_class = str(
        populate_jail.get("priorityClass") or populate_jail.get("priorityClassName") or ""
    ).strip()
    if not priority_class:
        # target_ref is a cxcli target selector, not necessarily the Helm clusterName.
        del target_ref
        cluster_name = str(values.get("clusterName") or "soperator").strip()
        if cluster_name:
            priority_class = f"{cluster_name[:63].rstrip('-')}-slurm-populate-jail"
    if priority_class:
        scheduling["priorityClassName"] = priority_class
    return active_passive_pod_scheduling_fields(scheduling)


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
    for role in ("controller", "login", "rest"):
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
    pod_spec.update(active_passive_pod_scheduling_fields(scheduling))
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": namespace,
            "name": f"{target}-{POPULATE_JAIL_JOB_SUFFIX}-passive-{slot_suffix}",
            "labels": {
                "app.kubernetes.io/component": "populate-jail",
                "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
                "slurm.nebius.ai/jail-rootfs-slot": passive_slot,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/component": "populate-jail",
                        "slurm.nebius.ai/jail-rootfs-slot": passive_slot,
                    }
                },
                "spec": pod_spec,
            },
        },
    }


def plan_populate_jail_refresh(
    *,
    mode: str,
    chart_changed: bool,
    before: PopulateJailSnapshot,
    after_chart: PopulateJailSnapshot,
    namespace: str,
) -> PopulateJailRefreshPlan:
    normalized = normalize_populate_jail_refresh_mode(mode)
    image_changed = bool(before.image and after_chart.image and before.image != after_chart.image)
    if normalized == "force":
        required = True
        reason = "forced by --populate-jail-refresh force"
    elif normalized == "manual":
        required = bool(chart_changed or image_changed)
        reason = (
            "manual refresh requested for target chart/rootfs change"
            if required
            else "manual refresh requested but no chart/rootfs change was detected"
        )
    elif image_changed:
        required = True
        reason = "target populateJail image differs from the pre-upgrade image"
    elif chart_changed:
        required = True
        reason = "target chart changed; existing jail rootfs compatibility is unproven"
    else:
        required = False
        reason = "target chart and populateJail image are unchanged"
    return PopulateJailRefreshPlan(
        mode=normalized,
        required=required,
        reason=reason,
        manual_instruction=populate_jail_manual_instruction(
            namespace=namespace,
            slurmcluster_name=after_chart.slurmcluster_name or before.slurmcluster_name,
        ),
        before=before,
        after_chart=after_chart,
    )


def skipped_populate_jail_refresh_result(
    plan: PopulateJailRefreshPlan,
) -> PopulateJailRefreshResult:
    return PopulateJailRefreshResult(
        mode=plan.mode,
        status="skipped",
        reason=plan.reason,
        target_image=plan.after_chart.image,
        job_name=plan.after_chart.job_name,
        job_uid=plan.after_chart.job_uid,
        job_image=plan.after_chart.job_image,
        maintenance_restored=True,
    )


def manual_populate_jail_refresh_result(plan: PopulateJailRefreshPlan) -> PopulateJailRefreshResult:
    return PopulateJailRefreshResult(
        mode=plan.mode,
        status="manual_required",
        reason=plan.reason,
        target_image=plan.after_chart.image,
        job_name=plan.after_chart.job_name,
        job_uid=plan.after_chart.job_uid,
        job_image=plan.after_chart.job_image,
        maintenance_restored=False,
        detail=plan.manual_instruction,
    )


def completed_populate_jail_refresh_result(
    *,
    mode: str,
    reason: str,
    snapshot: PopulateJailSnapshot,
    maintenance_restored: bool,
) -> PopulateJailRefreshResult:
    return PopulateJailRefreshResult(
        mode=mode,
        status="refreshed",
        reason=reason,
        target_image=snapshot.image,
        job_name=snapshot.job_name,
        job_uid=snapshot.job_uid,
        job_image=snapshot.job_image,
        maintenance_restored=maintenance_restored,
        detail=snapshot.detail,
    )


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


def _slurmcluster_image(resource: Mapping[str, Any]) -> str:
    spec = resource.get("spec")
    if not isinstance(spec, Mapping):
        return ""
    populate = spec.get("populateJail")
    if not isinstance(populate, Mapping):
        return ""
    return str(populate.get("image", "") or "").strip()


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


def _active_consumer_pods(pods_payload: Mapping[str, Any]) -> tuple[str, ...]:
    items = pods_payload.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ()
    active: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = _metadata_name(item)
        if not name:
            continue
        if not (name.startswith("login-") or name.startswith("worker-")):
            continue
        status = item.get("status")
        phase = str(status.get("phase", "") or "").strip() if isinstance(status, Mapping) else ""
        if phase in {"Succeeded", "Failed"}:
            continue
        active.append(name)
    return tuple(sorted(active))


def inspect_populate_jail(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    kube_context: str | None = None,
    timeout_seconds: int = 120,
) -> PopulateJailSnapshot:
    target = str(target_ref or "").strip()
    slurmcluster, detail = _run_json(
        runner,
        _kubectl_args(
            kube_context=kube_context,
            namespace=namespace,
            args=("get", "slurmcluster", target, "-o", "json"),
        ),
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if not slurmcluster:
        listed, list_detail = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=("get", "slurmclusters", "-o", "json"),
            ),
            timeout_seconds=timeout_seconds,
            check=False,
        )
        items = listed.get("items") if isinstance(listed, Mapping) else None
        item_list = (
            [item for item in items if isinstance(item, Mapping)]
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray))
            else []
        )
        if len(item_list) == 1:
            slurmcluster = dict(item_list[0])
            detail = "resolved the only live SlurmCluster"
        else:
            reason = list_detail or detail or "SlurmCluster was not found"
            return PopulateJailSnapshot(status="not_collected", detail=reason)
    slurmcluster_name = _metadata_name(slurmcluster)
    image = _slurmcluster_image(slurmcluster)
    job_name = f"{slurmcluster_name}-{POPULATE_JAIL_JOB_SUFFIX}" if slurmcluster_name else ""
    job: dict[str, Any] = {}
    job_detail = ""
    if job_name:
        job, job_detail = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=("get", "job", job_name, "-o", "json"),
            ),
            timeout_seconds=timeout_seconds,
            check=False,
        )
    pods_payload, _pods_detail = _run_json(
        runner,
        _kubectl_args(
            kube_context=kube_context,
            namespace=namespace,
            args=("get", "pods", "-o", "json"),
        ),
        timeout_seconds=timeout_seconds,
        check=False,
    )
    return PopulateJailSnapshot(
        slurmcluster_name=slurmcluster_name,
        image=image,
        job_name=job_name,
        job_uid=_metadata_uid(job),
        job_complete=_job_complete(job),
        job_failed=_job_failed(job),
        job_image=_job_container_image(job),
        active_consumer_pods=_active_consumer_pods(pods_payload),
        status="collected",
        detail=job_detail or detail,
    )


def wait_for_populate_jail_refresh(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    previous_job_uid: str,
    expected_image: str,
    kube_context: str | None = None,
    timeout_seconds: int = 2700,
    poll_interval_seconds: int = 10,
) -> PopulateJailSnapshot:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        snapshot = inspect_populate_jail(
            runner,
            namespace=namespace,
            target_ref=target_ref,
            kube_context=kube_context,
            timeout_seconds=120,
        )
        if snapshot.job_failed:
            raise RuntimeError(
                f"populate-jail refresh job {snapshot.job_name or '<unknown>'} failed."
            )
        new_job_seen = not previous_job_uid or (
            bool(snapshot.job_uid) and snapshot.job_uid != previous_job_uid
        )
        image_ok = not expected_image or snapshot.job_image == expected_image
        if snapshot.job_complete and new_job_seen and image_ok:
            return snapshot
        if time.monotonic() >= deadline:
            detail = snapshot.detail or "timed out waiting for refreshed populate-jail Job"
            raise RuntimeError(
                "Timed out waiting for populate-jail refresh to complete"
                + (f": {detail}" if detail else "")
            )
        time.sleep(max(poll_interval_seconds, 1))


def wait_for_active_passive_populate_jail_job(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    job_name: str,
    expected_image: str,
    kube_context: str | None = None,
    timeout_seconds: int = 2700,
    poll_interval_seconds: int = 10,
) -> PopulateJailSnapshot:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    job_name = str(job_name or "").strip()
    while True:
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
        snapshot = PopulateJailSnapshot(
            job_name=job_name,
            job_uid=_metadata_uid(job),
            job_complete=_job_complete(job),
            job_failed=_job_failed(job),
            job_image=_job_container_image(job),
            status="collected" if job else "not_collected",
            detail=detail,
        )
        if snapshot.job_failed:
            raise RuntimeError(f"active/passive populate-jail job {job_name} failed.")
        image_ok = not expected_image or snapshot.job_image == expected_image
        if snapshot.job_complete and image_ok:
            return snapshot
        if time.monotonic() >= deadline:
            reason = snapshot.detail or "timed out waiting for passive-slot populate Job"
            raise RuntimeError(
                f"Timed out waiting for active/passive populate-jail job {job_name}: {reason}"
            )
        time.sleep(max(poll_interval_seconds, 1))


def _items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    items = payload.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def _login_service_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
    names = []
    for item in _items(payload):
        name = _metadata_name(item)
        if name and "login" in name:
            names.append(name)
    return tuple(sorted(dict.fromkeys(names)))


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


def _statefulset_rollout_ready(payload: Mapping[str, Any]) -> bool:
    metadata = _mapping(payload.get("metadata"))
    spec = _mapping(payload.get("spec"))
    status = _mapping(payload.get("status"))
    try:
        generation = int(metadata.get("generation") or 0)
        observed_generation = int(status.get("observedGeneration") or 0)
        replicas = int(spec.get("replicas") if spec.get("replicas") is not None else 1)
        ready_replicas = int(status.get("readyReplicas") or 0)
        updated_replicas = int(status.get("updatedReplicas") or 0)
    except (TypeError, ValueError):
        return False
    return (
        observed_generation >= generation
        and ready_replicas >= replicas
        and updated_replicas >= replicas
    )


def _discover_login_service_names(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    kube_context: str | None,
) -> tuple[str, ...]:
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
    names = _login_service_names(services)
    if not names:
        target = str(target_ref or "").strip() or "<unknown>"
        raise RuntimeError(
            f"Could not find a login Service for SlurmCluster {target} in namespace {namespace}"
            + (f": {detail}" if detail else "")
        )
    return names


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


def wait_for_login_service_ready_endpoints(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    kube_context: str | None = None,
    min_ready_endpoints: int = 1,
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 5,
) -> dict[str, Any]:
    service_names = _discover_login_service_names(
        runner,
        namespace=namespace,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        ready = login_service_ready_endpoint_count(
            runner,
            namespace=namespace,
            service_names=service_names,
            kube_context=kube_context,
        )
        if ready >= min_ready_endpoints:
            return {"service_names": list(service_names), "ready_endpoints": ready}
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Timed out waiting for login Service ready endpoints"
                f": services={', '.join(service_names)}, ready={ready}, required={min_ready_endpoints}"
            )
        time.sleep(max(poll_interval_seconds, 1))


def wait_for_login_statefulset_rollout_with_ready_endpoint_guard(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    kube_context: str | None = None,
    statefulset_name: str = "login",
    min_ready_endpoints: int = 1,
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 5,
) -> dict[str, Any]:
    service_names = _discover_login_service_names(
        runner,
        namespace=namespace,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    deadline = time.monotonic() + max(timeout_seconds, 0)
    last_ready = 0
    while True:
        last_ready = login_service_ready_endpoint_count(
            runner,
            namespace=namespace,
            service_names=service_names,
            kube_context=kube_context,
        )
        if last_ready < min_ready_endpoints:
            raise RuntimeError(
                "Login Service ready endpoints dropped below the active/passive guard"
                f": services={', '.join(service_names)}, ready={last_ready}, "
                f"required={min_ready_endpoints}"
            )
        statefulset, detail = _run_json(
            runner,
            _kubectl_args(
                kube_context=kube_context,
                namespace=namespace,
                args=("get", "statefulsets.apps.kruise.io", statefulset_name, "-o", "json"),
            ),
            timeout_seconds=120,
            check=False,
        )
        if _statefulset_rollout_ready(statefulset):
            return {"service_names": list(service_names), "ready_endpoints": last_ready}
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for StatefulSet/{statefulset_name} while guarding "
                "login Service ready endpoints"
                + (f": {detail}" if detail else f": ready={last_ready}")
            )
        time.sleep(max(poll_interval_seconds, 1))


def wait_for_populate_jail_consumers_down(
    runner: PopulateJailCommandRunner,
    *,
    namespace: str,
    target_ref: str,
    kube_context: str | None = None,
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 10,
) -> PopulateJailSnapshot:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    last_snapshot = PopulateJailSnapshot(status="not_collected")
    while True:
        snapshot = inspect_populate_jail(
            runner,
            namespace=namespace,
            target_ref=target_ref,
            kube_context=kube_context,
            timeout_seconds=120,
        )
        last_snapshot = snapshot
        if snapshot.job_failed:
            raise RuntimeError(
                f"populate-jail refresh job {snapshot.job_name or '<unknown>'} failed."
            )
        if snapshot.status == "collected" and not snapshot.active_consumer_pods:
            return snapshot
        if time.monotonic() >= deadline:
            pods = ", ".join(last_snapshot.active_consumer_pods) or "<unknown>"
            detail = last_snapshot.detail or "timed out waiting for login/worker pods to stop"
            raise RuntimeError(
                "Timed out waiting for login/worker pods to stop before populate-jail refresh"
                f": {pods}; {detail}"
            )
        time.sleep(max(poll_interval_seconds, 1))
