"""Read-only effective scheduling guards shared by install and upgrade."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .soperator_checks_contract import job_execution_digest, verify_native_template

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution

_AUXILIARY = "run-extensive-check-on-reservations"
_OPERATION = "cxcli.nebius.ai/check-operation"


def _owned(resource: Mapping[str, Any], kind: str, owner: Mapping[str, Any]) -> bool:
    metadata = owner.get("metadata", {})
    return bool(metadata.get("uid")) and any(
        ref.get("kind") == kind
        and ref.get("name") == metadata.get("name")
        and ref.get("uid") == metadata["uid"]
        and ref.get("controller") is True
        for ref in resource.get("metadata", {}).get("ownerReferences", [])
    )


def scheduling_inventory(
    checks: SoperatorChecksExecution, *, deferred: bool
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    active = {
        row["metadata"]["name"]: row
        for row in checks._get("activechecks.slurm.nebius.ai").get("items", [])
    }
    crons = {row["metadata"]["name"]: row for row in checks._get("cronjob").get("items", [])}
    clusters = {}
    for rule in checks.policy.rules:
        expected = checks.policy.execution_specs[rule.name]
        cluster_name = expected["slurmClusterRefName"]
        if cluster_name not in clusters:
            clusters[cluster_name] = checks._get("slurmcluster", cluster_name)
        check, cron = active.get(rule.name, {}), crons.get(rule.name, {})
        spec, schedule = check.get("spec", {}), cron.get("spec", {})
        if (
            not check.get("metadata", {}).get("uid")
            or not cron.get("metadata", {}).get("uid")
            or not _owned(cron, "SlurmCluster", clusters[cluster_name])
            or not expected.get("schedule")
            or spec.get("schedule") != expected["schedule"]
            or schedule.get("schedule") != expected["schedule"]
            or schedule.get("timeZone") is not None
            or spec.get("suspend", False) != (True if deferred else rule.suspend)
            or spec.get("runAfterCreation", False)
            != (rule.required and rule.bootstrap if deferred else rule.required)
            or schedule.get("suspend", False) != (True if deferred else rule.suspend)
        ):
            return None
    if checks.policy.auxiliary_pvc:
        from .soperator_checks_auxiliary_recovery import auxiliary_cluster, auxiliary_references

        cron = crons.get(_AUXILIARY, {})
        expected = checks.policy.auxiliary_spec
        spec = cron.get("spec", {})
        cluster = auxiliary_cluster(checks)
        if (
            not cron.get("metadata", {}).get("uid")
            or not expected.get("schedule")
            or spec.get("schedule") != expected["schedule"]
            or spec.get("timeZone") != expected.get("timeZone")
            or spec.get("suspend", False) != (True if deferred else expected.get("suspend", False))
            or auxiliary_references(cron)
            != {
                "jail": checks.policy.auxiliary_pvc,
                "config": cluster + "-slurm-configs",
                "munge": cluster + "-munge",
            }
        ):
            return None
    return active, crons


def verify_deferred_diagnostics(
    checks: SoperatorChecksExecution, *, allow_acceptance: bool = False
) -> None:
    inventory = scheduling_inventory(checks, deferred=True)
    if inventory is None:
        raise RuntimeError("upstream checks deferral changed during maintenance")
    active, crons = inventory
    rules = {rule.name: rule for rule in checks.policy.rules}
    names = set(rules) | ({_AUXILIARY} if checks.policy.auxiliary_pvc else set())
    for job in checks._get("jobs").get("items", []):
        if any(
            c.get("type") in {"Complete", "Failed"} and c.get("status") == "True"
            for c in job.get("status", {}).get("conditions", [])
        ):
            continue
        metadata = job.get("metadata", {})
        owners = [
            ref
            for ref in metadata.get("ownerReferences", [])
            if ref.get("kind") == "CronJob" and ref.get("name") in names
        ]
        labels = metadata.get("labels", {})
        annotations = (
            job.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})
        )
        if not (
            owners
            or labels.get("component") == "soperatorchecks"
            or labels.get("app.kubernetes.io/component") == "soperatorchecks"
            or labels.get(_OPERATION)
            or annotations.get("slurm.nebius.ai/active-check-name") in names
        ):
            continue
        # Retained recovery evidence can be suspended rather than terminal.
        # Require controller acknowledgement and no live Pod; Slurm submissions
        # are checked separately below because they can outlive their submitter.
        status = job.get("status", {})
        if (
            job.get("spec", {}).get("suspend") is True
            and metadata.get("uid")
            and not status.get("active", 0)
            and not status.get("terminating", 0)
            and any(
                c.get("type") == "Suspended" and c.get("status") == "True"
                for c in status.get("conditions", [])
            )
            and not any(
                pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                or pod.get("metadata", {}).get("deletionTimestamp")
                for pod in checks._get("pods").get("items", [])
                if _owned(pod, "Job", job)
            )
        ):
            continue
        record = checks.state["jobs"].get(metadata.get("name"), {})
        if (
            allow_acceptance
            and checks.state.get("phase") == "acceptance"
            and record
            and metadata.get("uid")
            and labels.get(_OPERATION) == checks.operation_id
            and (not record.get("uid") or metadata.get("uid") == record["uid"])
            and record.get("execution") == job_execution_digest(job)
        ):
            continue
        if len(owners) == 1:
            name = owners[0]["name"]
            rule = rules.get(name)
            if rule and rule.bootstrap and rule.required and _owned(job, "CronJob", crons[name]):
                verify_native_template(
                    checks.policy.execution_specs[name],
                    active[name],
                    job.get("spec", {}).get("template", {}),
                )
                continue
        raise RuntimeError("in-flight diagnostic overlaps maintenance")
    if allow_acceptance and checks.state.get("phase") == "acceptance":
        checks._verify_isolation(include_pending=True)
    elif checks.slurm("squeue -h -u soperatorchecks -o '%i'").strip():
        raise RuntimeError("in-flight Slurm diagnostic overlaps maintenance")
