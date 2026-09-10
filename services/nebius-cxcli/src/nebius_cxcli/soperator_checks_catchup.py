"""Recover native pre-submit catch-up failures without replacing check results."""

from __future__ import annotations

import copy
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest, verify_native_template
from .soperator_checks_policy import checks_digest
from .soperator_install_checks_repair import validate_wait_hook


def expected_wait_hook(source_dir: Path) -> Mapping[str, Any]:
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "soperator-activechecks",
            str(source_dir / "helm/soperator-activechecks"),
            "--namespace",
            "soperator",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    jobs = [
        row
        for row in yaml.safe_load_all(rendered.stdout)
        if isinstance(row, Mapping)
        and row.get("kind") == "Job"
        and row.get("metadata", {}).get("name") == "wait-for-active-checks"
    ]
    if len(jobs) != 1:
        raise RuntimeError("catch-up recovery has no verified upstream wait hook")
    return jobs[0]


class ChecksCatchupRecovery:
    def __init__(
        self,
        parent: SoperatorChecksExecution,
        *,
        read_log: Callable[[str, str], str],
        render_hook: Callable[[], Mapping[str, Any]],
        apply_quiet: Callable[[Mapping[str, Any]], object],
    ) -> None:
        self.parent = parent
        self.read_log = read_log
        self.render_hook = render_hook
        self.apply_quiet = apply_quiet

    def _binding(self) -> dict[str, Any]:
        state = self.parent.state
        return {
            "operation": state["operation"],
            "policy": self.parent.policy.sha256,
            "acceptanceSha256": checks_digest(
                {"acceptance": state["acceptance"], "jobs": state["jobs"]}
            ),
            "reservation": state["reservation"],
            "fingerprint": state["reservationFingerprint"],
        }

    def _failures(self) -> list[Mapping[str, Any]]:
        failures = []
        for rule in self.parent.policy.required:
            check = self.parent._get("activecheck", rule.name)
            status = check.get("status", {}).get("slurmJobsStatus", {})
            if status.get("lastRunStatus") == "Error":
                if rule.check_type != "slurmJob" or status.get("lastRunId") != "No slurm job":
                    raise RuntimeError("check failure is outside pre-submit catch-up recovery")
                failures.append(check)
        return failures

    def _capture(self, checks: list[Mapping[str, Any]]) -> dict[str, Any]:
        parent = self.parent
        if parent.state.get("phase") != "accepted" or parent.state.get("scheduleRelease"):
            raise RuntimeError("catch-up recovery requires accepted, unreleased maintenance")
        parent.verify_acceptance()
        reservation = parent._reservation(parent.state["reservation"])
        if reservation["nodes"] != parent.state["acceptance"]["reservationNodes"]:
            raise RuntimeError("catch-up recovery reservation coverage changed")
        if parent.slurm("squeue -h -o '%i'").strip():
            raise RuntimeError("catch-up recovery requires all prior Slurm writers to finish")
        jobs = parent._jobs()
        accepted_at = max(
            str(jobs[name].get("metadata", {}).get("creationTimestamp", ""))
            for name in parent.state["jobs"]
        )
        if not accepted_at:
            raise RuntimeError("catch-up recovery acceptance timestamps are unavailable")
        records = []
        owners = set()
        for check in checks:
            check_name = check["metadata"]["name"]
            name = _identifier(check["status"]["slurmJobsStatus"]["lastRunName"])
            job = jobs.get(name, {})
            meta = job.get("metadata", {})
            cron = parent._get("cronjob", check_name)
            refs = meta.get("ownerReferences", [])
            created = meta.get("creationTimestamp", "")
            scheduled = meta.get("annotations", {}).get(
                "batch.kubernetes.io/cronjob-scheduled-timestamp", ""
            )
            if (
                not meta.get("uid")
                or meta.get("deletionTimestamp")
                or meta.get("labels", {}).get("app.kubernetes.io/component") != "soperatorchecks"
                or meta.get("annotations", {}).get("slurm-job-id")
                or len(refs) != 1
                or refs[0].get("kind") != "CronJob"
                or refs[0].get("name") != check_name
                or refs[0].get("uid") != cron.get("metadata", {}).get("uid")
                or not scheduled
                or not created
                or not scheduled < created
                or not accepted_at < created
                or job.get("status", {}).get("active", 0)
                or not any(
                    c.get("type") == "Failed" and c.get("status") == "True"
                    for c in job.get("status", {}).get("conditions", [])
                )
            ):
                raise RuntimeError("catch-up recovery cannot prove the late failed native CronJob")
            verify_native_template(
                parent.policy.execution_specs[check_name], check, job["spec"]["template"]
            )
            parent._script_identity(check_name)
            container = job["spec"]["template"]["spec"]["containers"][0]["name"]
            log = self.read_log(name, container)
            if (
                "No nodes to run on (ARRAY_SIZE=0). Failing." not in log
                or "Submitting Slurm job" in log
            ):
                raise RuntimeError("catch-up failure is not the native zero-eligible-node branch")
            annotations = check["metadata"].get("annotations", {})
            owners.add(
                (
                    annotations.get("meta.helm.sh/release-name"),
                    annotations.get("meta.helm.sh/release-namespace"),
                )
            )
            records.append(
                {
                    "name": name,
                    "uid": meta["uid"],
                    "check": check_name,
                    "checkUid": check["metadata"]["uid"],
                    "cronUid": refs[0]["uid"],
                    "created": created,
                    "scheduled": scheduled,
                    "execution": job_execution_digest(job),
                    "logSha256": checks_digest(log),
                }
            )
        if len(owners) != 1 or any(not value for value in next(iter(owners))):
            raise RuntimeError("catch-up checks do not have one exact native Helm owner")
        releases = parent.kube(
            ["get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"], None
        )
        matches = [
            hr
            for hr in releases.get("items", [])
            if (
                hr.get("spec", {}).get("releaseName") or hr["metadata"]["name"],
                hr.get("spec", {}).get("targetNamespace") or hr["metadata"]["namespace"],
            )
            in owners
        ]
        if len(matches) != 1:
            raise RuntimeError("catch-up checks release writer is ambiguous")
        release = matches[0]
        meta, status = release["metadata"], release.get("status", {})
        if (
            not meta.get("uid")
            or not status.get("lastAttemptedRevisionDigest")
            or status.get("lastAttemptedReleaseAction") != "upgrade"
        ):
            raise RuntimeError("catch-up recovery requires the exact steady-policy upgrade writer")
        hook = parent._get("job", "wait-for-active-checks")
        return {
            "binding": self._binding(),
            "failures": records,
            "checksRelease": {
                "name": meta["name"],
                "namespace": meta["namespace"],
                "uid": meta["uid"],
                "sourceDigest": status["lastAttemptedRevisionDigest"],
            },
            "waitHook": validate_wait_hook(hook, self.render_hook()) if hook else None,
            "status": "admitted",
        }

    def _child(self, record: Mapping[str, Any]) -> SoperatorChecksExecution:
        parent = self.parent
        child = SoperatorChecksExecution(
            policy=parent.policy,
            operation_id=parent.state["operation"]
            + ":schedule-catchup:"
            + checks_digest(record["binding"]),
            receipt_path=parent.path.with_name(parent.path.stem + "-schedule-catchup.json"),
            kubernetes=parent.kube,
            slurm=parent.slurm,
            assert_authority=parent.authority,
            emit=parent.emit,
            timeout_seconds=parent.timeout,
            poll_seconds=parent.poll,
            clock=parent.clock,
            sleep=parent.sleep,
        )

        child.lifecycle = parent.lifecycle
        return child

    def _verify_failures(self, record: Mapping[str, Any]) -> None:
        jobs = self.parent._jobs()
        for failure in record["failures"]:
            job = jobs.get(failure["name"], {})
            meta = job.get("metadata", {})
            check = self.parent._get("activecheck", failure["check"])
            cron = self.parent._get("cronjob", failure["check"])
            if (
                meta.get("uid") != failure["uid"]
                or meta.get("deletionTimestamp")
                or meta.get("annotations", {}).get("slurm-job-id")
                or job_execution_digest(job) != failure["execution"]
                or check.get("metadata", {}).get("uid") != failure["checkUid"]
                or cron.get("metadata", {}).get("uid") != failure["cronUid"]
            ):
                raise RuntimeError("admitted native catch-up failure identity changed")

    def recover(self) -> Mapping[str, Any] | None:
        parent = self.parent
        record = parent.state.get("catchupRecovery")
        if record is None:
            if parent.state.get("phase") != "accepted" or parent.state.get("scheduleRelease"):
                return None
            failures = self._failures()
            if not failures:
                return None
            record = self._capture(failures)
            parent.state["catchupRecovery"] = record
            parent._save()
        if not isinstance(record, dict) or record.get("binding") != self._binding():
            raise RuntimeError("catch-up recovery acceptance binding changed")
        child = self._child(record)
        if record.get("status") == "accepted":
            child.verify_acceptance(released_parent=parent)
            if record.get("resultSha256") != checks_digest(child.state):
                raise RuntimeError("catch-up recovery result changed")
            return copy.deepcopy(record)
        if parent.state.get("scheduleRelease"):
            raise RuntimeError("catch-up recovery cannot recreate released maintenance")
        parent.verify_acceptance()
        parent._reservation(record["binding"]["reservation"])
        if record.get("status") == "admitted":
            self._verify_failures(record)
            parent.emit("Recovering failed native catch-up schedules under accepted maintenance")
            self.apply_quiet(record)
            record["status"] = "quiet"
            parent._save()
        if record.get("status") != "quiet":
            raise RuntimeError("catch-up recovery phase is invalid")
        child.prepare_reservation(record["binding"]["reservation"], installing=False)
        allocation = parent.state["acceptance"]
        child.accept(
            reservation=allocation["reservation"],
            workers=tuple(allocation["workers"]),
            gpu_workers=tuple(allocation["gpuWorkers"]),
        )
        child.close_authorization()
        child.verify_acceptance()
        record["resultSha256"] = checks_digest(child.state)
        record["status"] = "accepted"
        parent._save()
        return copy.deepcopy(record)
