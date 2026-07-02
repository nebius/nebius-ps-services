"""Populate-jail refresh helpers for Soperator upgrade flows."""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

POPULATE_JAIL_REFRESH_PHASE_ID = "populate-jail-refresh"
POPULATE_JAIL_REFRESH_MODES = frozenset({"auto", "force", "manual"})
POPULATE_JAIL_OVERWRITE_MAINTENANCE = "downscaleAndOverwritePopulateJail"
POPULATE_JAIL_STEADY_MAINTENANCE = "none"
POPULATE_JAIL_JOB_SUFFIX = "populate-jail"


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
    patched["maintenance"] = POPULATE_JAIL_OVERWRITE_MAINTENANCE
    populate = patched.get("populateJail")
    if not isinstance(populate, dict):
        populate = {}
        patched["populateJail"] = populate
    populate["overwrite"] = True
    return patched


def populate_jail_steady_state_values(values: Mapping[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(dict(values))
    patched["maintenance"] = POPULATE_JAIL_STEADY_MAINTENANCE
    populate = patched.get("populateJail")
    if not isinstance(populate, dict):
        populate = {}
        patched["populateJail"] = populate
    populate["overwrite"] = False
    return patched


def populate_jail_manual_instruction(*, namespace: str, slurmcluster_name: str) -> str:
    target = slurmcluster_name or "<slurmcluster>"
    return (
        "Refresh the shared jail rootfs by applying the target chart with "
        "values.maintenance=downscaleAndOverwritePopulateJail and "
        "values.populateJail.overwrite=true, wait for "
        f"job/{target}-{POPULATE_JAIL_JOB_SUFFIX} to complete in namespace {namespace}, "
        "then reapply the target chart with maintenance=none and "
        "populateJail.overwrite=false before resuming login/worker pods."
    )


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


def skipped_populate_jail_refresh_result(plan: PopulateJailRefreshPlan) -> PopulateJailRefreshResult:
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
            backoff_limit = int(spec.get("backoffLimit") if spec.get("backoffLimit") is not None else 6)
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
