"""Recognize retained failed Nsight attempts with a proven successful successor."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .soperator_protected_data_plane import (
    SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION,
    protected_job_pod_identity,
    protected_workload_identity,
)

_PURPOSES = frozenset({"profiling-admit", "profiling-install", "profiling-verify"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _terminal_attempt(pod: Mapping[str, Any], jobs: Mapping[str, Mapping[str, Any]]):
    meta = _mapping(pod.get("metadata"))
    status = _mapping(pod.get("status"))
    phase = status.get("phase")
    owners = meta.get("ownerReferences")
    if (
        phase not in {"Failed", "Succeeded"}
        or meta.get("namespace") != "soperator"
        or not meta.get("uid")
        or meta.get("deletionTimestamp")
        or not isinstance(owners, list)
        or len(owners) != 1
    ):
        return None
    owner = _mapping(owners[0])
    job = jobs.get(str(owner.get("uid")), {})
    job_meta = _mapping(job.get("metadata"))
    labels = _mapping(job_meta.get("labels"))
    pod_labels = _mapping(meta.get("labels"))
    job_status = _mapping(job.get("status"))
    conditions = job_status.get("conditions")
    terminal = {
        c.get("type")
        for c in (conditions if isinstance(conditions, list) else [])
        if isinstance(c, Mapping)
        and c.get("status") == "True"
        and c.get("type") in {"Complete", "Failed"}
    }
    if (
        owner.get("apiVersion") != "batch/v1"
        or owner.get("kind") != "Job"
        or owner.get("controller") is not True
        or owner.get("name") != job_meta.get("name")
        or job_meta.get("namespace") != "soperator"
        or job_meta.get("deletionTimestamp")
        or labels.get("app.kubernetes.io/managed-by") != "nebius-cxcli"
        or labels.get("soperator.nebius.ai/protected-data-plane") not in _PURPOSES
        or not labels.get("nebius-cxcli/pvc-uid")
        or any(
            pod_labels.get(key) != labels.get(key)
            for key in (
                "soperator.nebius.ai/protected-data-plane",
                "nebius-cxcli/operation-id",
                "nebius-cxcli/fence-epoch",
                "nebius-cxcli/pvc-uid",
            )
        )
        or job_status.get("active", 0)
        or job_status.get("terminating", 0)
        or terminal != {"Complete" if phase == "Succeeded" else "Failed"}
    ):
        return None
    spec = _mapping(pod.get("spec"))
    for spec_key, status_key in (
        ("containers", "containerStatuses"),
        ("initContainers", "initContainerStatuses"),
    ):
        containers, states = spec.get(spec_key, []), status.get(status_key, [])
        if not isinstance(containers, list) or not isinstance(states, list):
            return None
        if len(states) != len(containers) or {_mapping(c).get("name") for c in containers} != {
            _mapping(s).get("name") for s in states
        }:
            return None
        for state in states:
            terminated = _mapping(_mapping(state).get("state")).get("terminated")
            if not isinstance(terminated, Mapping) or type(terminated.get("exitCode")) is not int:
                return None
            if phase == "Succeeded" and terminated["exitCode"] != 0:
                return None
    try:
        identity = protected_workload_identity(job)
        epoch = int(identity.fence_epoch)
        if epoch < 1 or identity.workload_sha256 != _mapping(job_meta.get("annotations")).get(
            SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION
        ):
            return None
        protected_job_pod_identity(job=job, pod=pod)
    except (RuntimeError, TypeError, ValueError):
        return None
    return (identity.operation_id, identity.purpose, labels["nebius-cxcli/pvc-uid"]), epoch


def superseded_nsight_attempt_pods(
    pods: Sequence[Mapping[str, Any]],
    *,
    read: Callable[[str], Mapping[str, Any] | None],
) -> set[str]:
    """Keep history out of service readiness, without accepting a profiling result.

    Both attempts must preserve their exact admitted Job and Pod execution. A
    terminal successful successor must share the operation, stage and PVC UID,
    with an equal or later fencing epoch. Unknown or unfinished failures remain
    blocking; the profiling owner still validates installation and tool results.
    """
    candidates = [
        pod
        for pod in pods
        if _mapping(_mapping(pod.get("metadata")).get("labels")).get(
            "soperator.nebius.ai/protected-data-plane"
        )
        in _PURPOSES
    ]
    if not any(_mapping(p.get("status")).get("phase") == "Failed" for p in candidates):
        return set()
    items = _mapping(read("jobs.batch")).get("items")
    if not isinstance(items, list):
        return set()
    jobs = {}
    for item in items:
        if not isinstance(item, Mapping):
            return set()
        uid = _mapping(item.get("metadata")).get("uid")
        if not isinstance(uid, str) or not uid or uid in jobs:
            return set()
        jobs[uid] = item
    attempts = [(pod, _terminal_attempt(pod, jobs)) for pod in candidates]
    successes = [
        bound
        for pod, bound in attempts
        if bound is not None and _mapping(pod.get("status")).get("phase") == "Succeeded"
    ]
    return {
        str(_mapping(pod.get("metadata"))["uid"])
        for pod, bound in attempts
        if bound is not None
        and _mapping(pod.get("status")).get("phase") == "Failed"
        and any(key == bound[0] and epoch >= bound[1] for key, epoch in successes)
    }
