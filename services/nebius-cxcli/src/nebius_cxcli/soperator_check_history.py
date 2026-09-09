"""Identify terminal native scheduled-check history without accepting its results."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .soperator_checks_binding import AUXILIARY_CRONJOB


def _section(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _section(payload, "metadata")


def _owner(payload: Mapping[str, Any], kind: str, api: str) -> Mapping[str, Any]:
    refs = _metadata(payload).get("ownerReferences")
    if not isinstance(refs, list) or len(refs) != 1:
        return {}
    ref = refs[0]
    if (
        not isinstance(ref, Mapping)
        or ref.get("kind") != kind
        or ref.get("apiVersion") != api
        or ref.get("controller") is not True
        or not ref.get("name")
        or not ref.get("uid")
    ):
        return {}
    return ref


def _index(payload: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items:
        if not isinstance(item, Mapping):
            return {}
        meta = _metadata(item)
        name = meta.get("name")
        if not isinstance(name, str) or not name or name in result or not meta.get("uid"):
            return {}
        if meta.get("namespace") != "soperator":
            return {}
        result[name] = item
    return result


def terminal_native_check_pods(
    pods: Sequence[Mapping[str, Any]],
    *,
    cluster: Mapping[str, Any],
    releases: Sequence[Mapping[str, Any]],
    read: Callable[[str], Mapping[str, Any] | None],
) -> set[str]:
    """Recognize Pod -> terminal Job -> CronJob -> exact SlurmCluster ownership.

    The same-name ActiveCheck must belong to a validated Helm release and refer
    to that cluster. These Pods are history, not service readiness or successful
    checks; the caller must still evaluate its required ActiveCheck results.
    """
    failed = [pod for pod in pods if _section(pod, "status").get("phase") == "Failed"]
    if not failed:
        return set()
    cluster_meta = _metadata(cluster)
    if (
        not cluster_meta.get("uid")
        or cluster_meta.get("namespace") != "soperator"
        or cluster_meta.get("deletionTimestamp")
    ):
        return set()
    release_pairs = {
        (
            _section(hr, "spec").get("releaseName") or _metadata(hr).get("name"),
            _section(hr, "spec").get("targetNamespace") or _metadata(hr).get("namespace"),
        )
        for hr in releases
    }
    jobs = _index(read("jobs.batch"))
    crons = _index(read("cronjobs.batch"))
    checks = _index(read("activechecks.slurm.nebius.ai"))
    history = set()
    for pod in failed:
        meta = _metadata(pod)
        job_ref = _owner(pod, "Job", "batch/v1")
        job = jobs.get(str(job_ref.get("name")), {})
        cron_ref = _owner(job, "CronJob", "batch/v1")
        cron = crons.get(str(cron_ref.get("name")), {})
        cluster_ref = _owner(cron, "SlurmCluster", "slurm.nebius.ai/v1")
        cron_meta = _metadata(cron)
        cron_annotations = _section(cron_meta, "annotations")
        cron_owner = (
            cron_annotations.get("meta.helm.sh/release-name"),
            cron_annotations.get("meta.helm.sh/release-namespace"),
        )
        template = _section(_section(_section(cron, "spec"), "jobTemplate"), "spec")
        pod_spec = _section(_section(template, "template"), "spec")
        containers = pod_spec.get("containers", [])
        auxiliary = (
            cron_meta.get("name") == AUXILIARY_CRONJOB
            and not cron_meta.get("ownerReferences")
            and cron_owner in release_pairs
            and isinstance(containers, list)
            and len(containers) == 1
            and isinstance(containers[0], Mapping)
            and containers[0].get("name") == AUXILIARY_CRONJOB
            and [
                row.get("value")
                for row in containers[0].get("env", [])
                if isinstance(row, Mapping) and row.get("name") == "TARGET_ACTIVE_CHECK_NAME"
            ]
            == ["extensive-check"]
        )
        check = checks.get("extensive-check" if auxiliary else str(cron_meta.get("name")), {})
        check_meta = _metadata(check)
        annotations = _section(check_meta, "annotations")
        job_status = _section(job, "status")
        conditions = job_status.get("conditions")
        terminal = {
            condition.get("type")
            for condition in (conditions if isinstance(conditions, list) else [])
            if isinstance(condition, Mapping)
            and condition.get("type") in {"Complete", "Failed"}
            and condition.get("status") == "True"
        }
        if (
            not meta.get("uid")
            or meta.get("namespace") != "soperator"
            or any(_metadata(row).get("deletionTimestamp") for row in (pod, job, cron, check))
            or any(
                _section(_metadata(row), "labels").get("app.kubernetes.io/component")
                != "soperatorchecks"
                for row in (pod, job, cron)
            )
            or not job_ref
            or job_ref.get("uid") != _metadata(job).get("uid")
            or not cron_ref
            or cron_ref.get("uid") != _metadata(cron).get("uid")
            or not (
                auxiliary
                or (
                    cluster_ref
                    and cluster_ref.get("name") == cluster_meta.get("name")
                    and cluster_ref.get("uid") == cluster_meta.get("uid")
                )
            )
            or len(terminal) != 1
            or job_status.get("active", 0)
            or job_status.get("terminating", 0)
            or _section(check, "spec").get("slurmClusterRefName") != cluster_meta.get("name")
            or _section(check, "spec").get("checkType") not in {"slurmJob", "k8sJob"}
            or _section(check_meta, "labels").get("app.kubernetes.io/managed-by") != "Helm"
            or (
                annotations.get("meta.helm.sh/release-name"),
                annotations.get("meta.helm.sh/release-namespace"),
            )
            not in release_pairs
            or (
                auxiliary
                and (
                    annotations.get("meta.helm.sh/release-name"),
                    annotations.get("meta.helm.sh/release-namespace"),
                )
                != cron_owner
            )
        ):
            continue
        history.add(str(meta["uid"]))
    return history
