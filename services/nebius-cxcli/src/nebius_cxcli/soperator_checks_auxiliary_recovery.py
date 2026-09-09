"""Recover a never-started native auxiliary job with wrong cluster references."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_binding import AUXILIARY_CRONJOB
from .soperator_checks_contract import job_execution_digest, normalized
from .soperator_checks_policy import checks_digest


def auxiliary_references(cron: Mapping[str, Any]) -> dict[str, str]:
    volumes = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
    by_name = {row["name"]: row for row in volumes}
    if len(by_name) != len(volumes):
        raise RuntimeError("auxiliary job volume names are ambiguous")
    return {
        "jail": by_name["jail"]["persistentVolumeClaim"]["claimName"],
        "config": by_name["slurm-configs"]["configMap"]["name"],
        "munge": by_name["munge-key"]["secret"]["secretName"],
    }


def auxiliary_cluster(checks: SoperatorChecksExecution) -> str:
    names = {spec.get("slurmClusterRefName") for spec in checks.policy.execution_specs.values()}
    if len(names) != 1 or not next(iter(names)):
        raise RuntimeError("auxiliary checks have no unique approved cluster")
    return str(next(iter(names)))


def _native_pod_spec(value: Any) -> Any:
    if isinstance(value, Mapping):
        defaults = {
            "imagePullPolicy": "IfNotPresent",
            "terminationMessagePath": "/dev/termination-log",
            "terminationMessagePolicy": "File",
        }
        return {
            key: _native_pod_spec(item)
            for key, item in normalized(value).items()
            if not (key in defaults and item == defaults[key])
        }
    if isinstance(value, list):
        return [_native_pod_spec(item) for item in value]
    return value


class AuxiliaryBindingRecovery:
    def __init__(
        self,
        checks: SoperatorChecksExecution,
        *,
        render: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        apply_quiet: Callable[[Mapping[str, Any], Callable[[], None]], object],
    ) -> None:
        self.checks = checks
        self.render = render
        self.apply_quiet = apply_quiet

    def _binding(self) -> dict[str, Any]:
        state = self.checks.state
        recovery = state.get("catchupRecovery", {})
        if (
            state.get("phase") not in {"accepted", "restored"}
            or recovery.get("status") != "accepted"
        ):
            raise RuntimeError("auxiliary recovery requires accepted native catch-up results")
        return {
            "operation": state["operation"],
            "policy": self.checks.policy.sha256,
            "reservation": state["reservation"],
            "fingerprint": state["reservationFingerprint"],
            "catchupSha256": checks_digest(recovery),
        }

    def _expected(self) -> dict[str, str]:
        cluster = auxiliary_cluster(self.checks)
        return {
            "jail": self.checks.policy.auxiliary_pvc,
            "config": cluster + "-slurm-configs",
            "munge": cluster + "-munge",
        }

    def _pods(self, job: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        meta = job["metadata"]
        payload = self.checks.kube(
            ["get", "pods", "-n", "soperator", "-l", "job-name=" + meta["name"], "-o", "json"], None
        )
        pods = payload.get("items")
        if not isinstance(pods, list) or not pods:
            raise RuntimeError("auxiliary recovery Pod evidence is unavailable")
        for pod in pods:
            refs = pod.get("metadata", {}).get("ownerReferences", [])
            if (
                len(refs) != 1
                or refs[0].get("kind") != "Job"
                or refs[0].get("uid") != meta["uid"]
                or refs[0].get("name") != meta["name"]
                or refs[0].get("controller") is not True
                or refs[0].get("apiVersion") != "batch/v1"
            ):
                raise RuntimeError("auxiliary recovery Pod ownership changed")
        return pods

    @staticmethod
    def _never_started(pods: list[Mapping[str, Any]]) -> None:
        for pod in pods:
            meta, status = pod.get("metadata", {}), pod.get("status", {})
            states = [
                *status.get("containerStatuses", []),
                *status.get("initContainerStatuses", []),
            ]
            expected = len(pod.get("spec", {}).get("containers", [])) + len(
                pod.get("spec", {}).get("initContainers", [])
            )
            if (
                not meta.get("uid")
                or meta.get("deletionTimestamp")
                or status.get("phase") != "Pending"
                or not states
                or len(states) != expected
                or any(
                    row.get("containerID")
                    or row.get("imageID")
                    or row.get("restartCount", 0)
                    or row.get("lastState")
                    or row.get("started") is True
                    or set(row.get("state", {})) != {"waiting"}
                    for row in states
                )
            ):
                raise RuntimeError("auxiliary recovery cannot prove the Pod never executed")

    def _capture(self, cron: Mapping[str, Any]) -> dict[str, Any]:
        parent = self.checks
        binding = self._binding()
        if parent.state.get("scheduleRelease"):
            raise RuntimeError("auxiliary recovery cannot reopen released maintenance")
        parent.verify_acceptance()
        reservation = parent._reservation(binding["reservation"])
        if reservation["users"] != ["root"]:
            raise RuntimeError("auxiliary recovery requires closed temporary authorization")
        owner = parent.state["catchupRecovery"]["checksRelease"]
        hr = parent._get("helmrelease", owner["name"], owner["namespace"])
        hm, hs = hr.get("metadata", {}), hr.get("status", {})
        annotations = cron["metadata"].get("annotations", {})
        if (
            hm.get("uid") != owner["uid"]
            or hm.get("deletionTimestamp")
            or not cron.get("metadata", {}).get("uid")
            or cron["metadata"].get("deletionTimestamp")
            or hs.get("lastAttemptedRevisionDigest") != owner["sourceDigest"]
            or hs.get("observedGeneration") != hm.get("generation")
            or not any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in hs.get("conditions", [])
            )
            or annotations.get("meta.helm.sh/release-name") != hr["spec"]["releaseName"]
            or annotations.get("meta.helm.sh/release-namespace") != hr["spec"]["targetNamespace"]
            or cron["spec"].get("suspend") is not True
        ):
            raise RuntimeError("auxiliary recovery lost its frozen checks Helm owner")
        expected = copy.deepcopy(dict(self.render(hr["spec"].get("values", {}))))
        volumes = expected["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
        if volumes[0] != {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}}:
            raise RuntimeError("upstream auxiliary jail template changed")
        volumes[0]["persistentVolumeClaim"]["claimName"] = parent.policy.auxiliary_pvc
        actual_spec = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        expected_spec = expected["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        if _native_pod_spec(actual_spec) != _native_pod_spec(expected_spec):
            raise RuntimeError(
                "auxiliary recovery executable differs from the pinned upstream chart"
            )
        jobs = [
            job
            for job in parent._jobs().values()
            if any(
                ref.get("kind") == "CronJob" and ref.get("uid") == cron["metadata"]["uid"]
                for ref in job.get("metadata", {}).get("ownerReferences", [])
            )
            and not any(
                c.get("type") in {"Complete", "Failed"} and c.get("status") == "True"
                for c in job.get("status", {}).get("conditions", [])
            )
        ]
        if len(jobs) != 1:
            raise RuntimeError("auxiliary recovery requires one exact never-started Job")
        job = jobs[0]
        refs = job.get("metadata", {}).get("ownerReferences", [])
        if (
            len(refs) != 1
            or refs[0].get("controller") is not True
            or refs[0].get("name") != AUXILIARY_CRONJOB
            or refs[0].get("apiVersion") != "batch/v1"
            or not job.get("metadata", {}).get("uid")
            or job["metadata"].get("deletionTimestamp")
            or job.get("spec", {}).get("suspend") is True
            or any(
                job.get("spec", {}).get(key) != value
                for key, value in (("parallelism", 1), ("completions", 1), ("backoffLimit", 0))
            )
        ):
            raise RuntimeError("auxiliary recovery Job ownership or execution limits changed")
        if job.get("metadata", {}).get("annotations", {}).get(
            "slurm-job-id"
        ) or job_execution_digest(job) != job_execution_digest(cron["spec"]["jobTemplate"]):
            raise RuntimeError("auxiliary recovery Job has changed or submitted Slurm work")
        pods = self._pods(job)
        if len(pods) != 1:
            raise RuntimeError("auxiliary recovery requires one never-started Pod")
        self._never_started(pods)
        for pod in pods:
            events = parent.kube(
                [
                    "get",
                    "events",
                    "-n",
                    "soperator",
                    "--field-selector",
                    "involvedObject.uid=" + pod["metadata"]["uid"],
                    "-o",
                    "json",
                ],
                None,
            )
            messages = [
                str(event.get("message", ""))
                for event in events.get("items", [])
                if event.get("reason") == "FailedMount"
            ]
            if not all(
                any('"' + name + '" not found' in message for message in messages)
                for name in ("soperator-slurm-configs", "soperator-munge")
            ):
                raise RuntimeError("auxiliary recovery lacks both exact missing-reference failures")
        return {
            "binding": binding,
            "status": "admitted",
            "checksRelease": copy.deepcopy(owner),
            "cronUid": cron["metadata"]["uid"],
            "job": job["metadata"]["name"],
            "jobUid": job["metadata"]["uid"],
            "execution": job_execution_digest(job),
            "pods": sorted(pod["metadata"]["uid"] for pod in pods),
            "expected": self._expected(),
        }

    def _job(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        job = self.checks._get("job", record["job"])
        if (
            job.get("metadata", {}).get("uid") != record["jobUid"]
            or job.get("metadata", {}).get("deletionTimestamp")
            or job_execution_digest(job) != record["execution"]
            or job.get("metadata", {}).get("annotations", {}).get("slurm-job-id")
        ):
            raise RuntimeError("admitted auxiliary Job identity or execution changed")
        return job

    def _terminal(self, record: Mapping[str, Any]) -> bool:
        job = self._job(record)
        return (
            record.get("terminationIntent") is True
            and job.get("spec", {}).get("activeDeadlineSeconds") == 1
            and not job.get("status", {}).get("active", 0)
            and not job.get("status", {}).get("terminating", 0)
            and any(
                c.get("type") == "Failed"
                and c.get("status") == "True"
                and c.get("reason") == "DeadlineExceeded"
                for c in job.get("status", {}).get("conditions", [])
            )
        )

    def stop_admitted_job(self, record: dict[str, Any]) -> None:
        owner = record["checksRelease"]
        hr = self.checks._get("helmrelease", owner["name"], owner["namespace"])
        cron = self.checks._get("cronjob", AUXILIARY_CRONJOB)
        if (
            hr.get("metadata", {}).get("uid") != owner["uid"]
            or hr.get("status", {}).get("lastAttemptedRevisionDigest") != owner["sourceDigest"]
            or hr.get("spec", {}).get("suspend") is not True
            or cron.get("metadata", {}).get("uid") != record["cronUid"]
            or cron.get("spec", {}).get("suspend") is not True
        ):
            raise RuntimeError("auxiliary recovery requires its exact writers suspended")
        job = self._job(record)
        if not record.get("terminationIntent"):
            self.checks._reservation(record["binding"]["reservation"])
            pods = self._pods(job)
            self._never_started(pods)
            if sorted(pod["metadata"]["uid"] for pod in pods) != record["pods"]:
                raise RuntimeError("auxiliary recovery Pod identity changed")
            record["terminationIntent"] = True
            self.checks._save()
        if not self._terminal(record) and job.get("spec", {}).get("activeDeadlineSeconds") != 1:
            self.checks._reservation(record["binding"]["reservation"])
            pods = self._pods(job)
            self._never_started(pods)
            if sorted(pod["metadata"]["uid"] for pod in pods) != record["pods"]:
                raise RuntimeError("auxiliary recovery Pod identity changed before termination")
            self.checks._patch(
                "job",
                record["job"],
                "soperator",
                {"spec": {"activeDeadlineSeconds": 1}},
                uid=record["jobUid"],
            )
        self.checks._until(lambda: self._terminal(record), "admitted auxiliary Job termination")

    def recover(self) -> Mapping[str, Any] | None:
        parent = self.checks
        if not parent.policy.auxiliary_pvc or parent.state.get("phase") not in {
            "accepted",
            "restored",
        }:
            return None
        record = parent.state.get("auxiliaryRecovery")
        cron = parent._get("cronjob", AUXILIARY_CRONJOB)
        refs = auxiliary_references(cron)
        if record is None:
            if refs == self._expected():
                return None
            if refs != {
                "jail": parent.policy.auxiliary_pvc,
                "config": "soperator-slurm-configs",
                "munge": "soperator-munge",
            }:
                raise RuntimeError("auxiliary recovery cannot replace custom resource references")
            record = self._capture(cron)
            parent.state["auxiliaryRecovery"] = record
            parent._save()
        if (
            record.get("binding") != self._binding()
            or cron.get("metadata", {}).get("uid") != record["cronUid"]
        ):
            raise RuntimeError("auxiliary recovery binding changed")
        if record.get("status") == "admitted":
            parent.emit(
                "Recovering the exact never-started auxiliary check Job and cluster references"
            )
            self.apply_quiet(record, lambda: self.stop_admitted_job(record))
            cron = parent._get("cronjob", AUXILIARY_CRONJOB)
            if (
                auxiliary_references(cron) != record["expected"]
                or cron["spec"].get("suspend") is not True
                or not self._terminal(record)
            ):
                raise RuntimeError("auxiliary recovery has not converged")
            record["status"] = "complete"
            parent._save()
        elif record.get("status") != "complete":
            raise RuntimeError("auxiliary recovery phase is invalid")
        if auxiliary_references(cron) != record["expected"] or not self._terminal(record):
            raise RuntimeError("completed auxiliary recovery changed")
        return copy.deepcopy(record)
