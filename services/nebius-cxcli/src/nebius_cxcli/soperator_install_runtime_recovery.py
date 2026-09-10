"""Retire an exact failed native probe and restore only its proven prolog drains."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest

PROBE = "wait-for-soperatorchecks-srun-ready"
_SUBMIT = "srun --mpi=none --job-name=test-controller-is-ready -n1 -t1 --partition=hidden hostname"
_LIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}
_JOB_KEYS = (
    "JobId",
    "JobName",
    "UserId",
    "Reservation",
    "AllocNode:Sid",
    "SubmitTime",
    "SubmitLine",
)


def slurm_fields(line: str) -> dict[str, str]:
    """Retain spaces and full timestamps in native one-line fields."""
    return dict(
        re.findall(
            r"(?:^| +)([A-Za-z][A-Za-z0-9_:]*)=(.*?)(?= +[A-Za-z][A-Za-z0-9_:]*=|$)", line.strip()
        )
    )


def slurm_jobs(runner: SoperatorChecksExecution) -> list[dict[str, str]]:
    return [
        slurm_fields(line)
        for line in runner.slurm(
            "env SLURM_TIME_FORMAT=standard TZ=UTC scontrol show jobs -o"
        ).splitlines()
        if line.startswith("JobId=")
    ]


def slurm_nodes(runner: SoperatorChecksExecution) -> dict[str, dict[str, str]]:
    rows = [
        slurm_fields(line)
        for line in runner.slurm(
            "env SLURM_TIME_FORMAT=standard TZ=UTC scontrol show nodes -o"
        ).splitlines()
        if line.startswith("NodeName=")
    ]
    result = {row["NodeName"]: row for row in rows}
    if not result or len(result) != len(rows):
        raise RuntimeError("runtime repair has ambiguous worker inventory")
    return result


def capture_probe_failure(runner: SoperatorChecksExecution) -> dict[str, Any]:
    """Admission is read-only; bind the actual Job, Pod and Slurm submitter."""
    state = runner.state
    if state.get("phase") != "acceptance" or not state.get("installReservationIntent"):
        raise RuntimeError("runtime repair requires unfinished initial acceptance")
    unfinished = [(name, row) for name, row in state["jobs"].items() if row["status"] != "complete"]
    if len(unfinished) != 1 or unfinished[0][1]["check"] != PROBE:
        raise RuntimeError("runtime repair requires one exact unfinished native probe")
    name, entry = unfinished[0]
    if not entry.get("submitted") or not entry.get("uid"):
        raise RuntimeError("runtime repair probe was not durably submitted")
    jobs = runner._jobs()
    for job_name, record in state["jobs"].items():
        live = jobs.get(job_name, {})
        if (
            live.get("metadata", {}).get("uid") != record.get("uid")
            or live.get("metadata", {}).get("labels", {}).get("cxcli.nebius.ai/check-operation")
            != runner.operation_id
            or job_execution_digest(live) != record["execution"]
            or live.get("metadata", {}).get("deletionTimestamp")
            or (
                job_name != name
                and not any(
                    c.get("type") == "Complete" and c.get("status") == "True"
                    for c in live.get("status", {}).get("conditions", [])
                )
            )
        ):
            raise RuntimeError("runtime repair lost predecessor Job evidence")
    pod_rows = runner._get("pods").get("items", [])
    pods = [
        pod
        for pod in pod_rows
        if any(
            owner.get("uid") == entry["uid"] and owner.get("controller") is True
            for owner in pod.get("metadata", {}).get("ownerReferences", [])
        )
    ]
    if len(pods) != 1 or pods[0].get("status", {}).get("phase") != "Running":
        raise RuntimeError("runtime repair requires one live native probe Pod")
    pod = pods[0]
    pod_ip = pod["status"].get("podIP")
    started = [
        row["state"]["running"]["startedAt"]
        for row in pod["status"].get("containerStatuses", [])
        if row["name"] == PROBE and row.get("state", {}).get("running")
    ]
    if not pod_ip or len(started) != 1 or not pod["metadata"].get("uid"):
        raise RuntimeError("runtime repair has no exact probe submitter")
    reservation = runner._reservation(state["reservation"])
    if reservation["users"] != ["root", "soperatorchecks"]:
        raise RuntimeError("runtime repair lost check reservation authorization")
    selected = []
    for row in slurm_jobs(runner):
        if row.get("Reservation") != reservation["name"]:
            if row.get("JobState") in _LIVE:
                raise RuntimeError("runtime repair encountered another live workload")
            continue
        if (
            row.get("JobName") != "test-controller-is-ready"
            or row.get("UserId") != f"soperatorchecks({state['principalUid']})"
            or row.get("AllocNode:Sid") != f"{pod_ip}:1"
            or row.get("SubmitLine") != _SUBMIT
            or row.get("SubmitTime", "") < started[0].removesuffix("Z")
            or not re.fullmatch(r"[1-9][0-9]*", row.get("JobId", ""))
            or row.get("JobState") not in {"FAILED", "PENDING"}
        ):
            raise RuntimeError("runtime repair cannot attribute a Slurm job to the probe")
        selected.append(row)
    nodes = slurm_nodes(runner)
    if set(nodes) != set(reservation["nodes"]):
        raise RuntimeError("runtime repair changed reserved worker scope")
    drains = {}
    for node, row in nodes.items():
        failures = [
            job
            for job in selected
            if job.get("NodeList") == node
            and job.get("JobState") == "FAILED"
            and job.get("ExitCode") == "0:54"
        ]
        reason = row.get("Reason", "")
        if (
            len(failures) != 1
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", failures[0].get("EndTime", "")
            )
            or reason != f"Prolog error [root@{failures[0]['EndTime']}]"
            or set(row.get("State", "").split("+"))
            != {"IDLE", "CLOUD", "DRAIN", "MAINTENANCE", "RESERVED"}
            or row.get("CPUAlloc") != "0"
            or row.get("AllocMem") != "0"
        ):
            raise RuntimeError("runtime repair cannot attribute the exact worker drain")
        drains[node] = {"reason": reason, "jobId": failures[0]["JobId"]}
    pending = [row["JobId"] for row in selected if row["JobState"] == "PENDING"]
    if len(pending) != 1 or len(selected) != len(nodes) + 1:
        raise RuntimeError("runtime repair probe submissions are ambiguous")
    return {
        "job": {
            "name": name,
            "uid": entry["uid"],
            "execution": entry["execution"],
            "operationLabel": runner.operation_id,
        },
        "pod": {"name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"], "address": pod_ip},
        "slurmJobs": {row["JobId"]: {key: row[key] for key in _JOB_KEYS} for row in selected},
        "pendingIds": pending,
        "drains": drains,
        "reservation": reservation,
    }


class InstallRuntimeRecovery:
    """Product-owned forward recovery under the successor's existing lease."""

    def __init__(
        self,
        runner: SoperatorChecksExecution,
        repair: Mapping[str, Any],
        read_worker: Callable[[str, list[str]], str],
        *,
        quiescence_key: str = "runtimeRepairQuiescence",
    ):
        self.runner, self.repair, self.read_worker = runner, repair, read_worker
        self.proof = repair["runtimeFailure"]
        self.quiescence_key = quiescence_key

    def _job(self) -> Mapping[str, Any]:
        expected = self.proof["job"]
        live = self.runner._get("job", expected["name"])
        meta = live.get("metadata", {})
        if (
            meta.get("uid") != expected["uid"]
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation")
            != expected["operationLabel"]
            or job_execution_digest(live) != expected["execution"]
        ):
            raise RuntimeError("runtime recovery lost its exact predecessor probe")
        return live

    def quiesce(self) -> None:
        runner = self.runner
        key = self.quiescence_key
        identity = checks_digest(self.repair)
        saved = runner.state.get(key)
        if saved and saved.get("repair") != identity:
            raise RuntimeError("runtime recovery receipt changed")
        job = self._job()
        if saved and saved.get("status") == "complete":
            if job.get("spec", {}).get("suspend") is not True:
                raise RuntimeError("runtime recovery predecessor probe resumed")
            return
        if not saved:
            if job.get("spec", {}).get("suspend") is True:
                raise RuntimeError("runtime recovery found unowned probe suspension")
            runner.state[key] = {"repair": identity, "status": "intent"}
            runner._save()
        if job.get("spec", {}).get("suspend") is not True:
            runner._patch(
                "job",
                self.proof["job"]["name"],
                "soperator",
                {"spec": {"suspend": True}},
                uid=self.proof["job"]["uid"],
            )

        def stopped() -> bool:
            current = self._job()
            if current.get("spec", {}).get("suspend") is not True:
                raise RuntimeError("runtime recovery probe suspension drifted")
            pods = runner._get("pods").get("items", [])
            owned = [
                pod
                for pod in pods
                if any(
                    owner.get("uid") == self.proof["job"]["uid"]
                    for owner in pod.get("metadata", {}).get("ownerReferences", [])
                )
            ]
            return not current.get("status", {}).get("active", 0) and all(
                pod.get("status", {}).get("phase") in {"Succeeded", "Failed"} for pod in owned
            )

        runner._until(stopped, "failed native probe to stop")
        rows = slurm_jobs(runner)
        for row in rows:
            if row.get("JobState") not in _LIVE:
                continue
            job_id = row.get("JobId", "")
            if (
                job_id not in self.proof["pendingIds"]
                or {k: row.get(k) for k in _JOB_KEYS} != self.proof["slurmJobs"][job_id]
                or row["JobState"] != "PENDING"
            ):
                raise RuntimeError("runtime recovery found a foreign or changed live Slurm job")
            runner.authority()
            runner.slurm(f"scancel {job_id}")
        runner._until(
            lambda: not any(row.get("JobState") in _LIVE for row in slurm_jobs(runner)),
            "exact failed probe Slurm submission to stop",
        )
        reservation = runner._reservation(self.proof["reservation"]["name"])
        if {k: v for k, v in reservation.items() if k != "users"} != {
            k: v for k, v in self.proof["reservation"].items() if k != "users"
        }:
            raise RuntimeError("runtime recovery reservation changed")
        if reservation["users"] != ["root"]:
            runner.authority()
            runner.slurm(
                f"scontrol update ReservationName={_identifier(reservation['name'])} Users=root"
            )
        if runner._reservation(reservation["name"])["users"] != ["root"]:
            raise RuntimeError("runtime recovery could not restore reservation authorization")
        runner.state[key]["status"] = "complete"
        runner._save()

    def restore_drains(self) -> None:
        runner = self.runner
        if runner.state.get("runtimeRepairQuiescence", {}).get("status") != "complete":
            raise RuntimeError("runtime recovery probe is not quiescent")
        if self._job().get("spec", {}).get("suspend") is not True:
            raise RuntimeError("runtime recovery predecessor probe resumed")
        restored = runner.state.get("runtimeRepairDrains", {})
        if set(restored) == set(self.proof["drains"]) and all(
            row.get("status") == "complete" for row in restored.values()
        ):
            return
        if any(row.get("JobState") in _LIVE for row in slurm_jobs(runner)):
            raise RuntimeError("runtime recovery has live Slurm work")
        cm = runner._get("configmap", "slurm-scripts")
        expected = self.repair["scripts"]
        if (
            cm.get("metadata", {}).get("uid") != expected["uid"]
            or checks_digest(cm.get("data")) != expected["dataSha256"]
        ):
            raise RuntimeError("runtime recovery upstream scripts changed")
        for node, proof in self.proof["drains"].items():
            _identifier(node)
            pod = runner._get("pod", node)
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if not any(
                row.get("name") == "slurmd" and row.get("ready") is True for row in statuses
            ):
                raise RuntimeError("runtime recovery worker is not ready")
            slurmd: Mapping[str, Any] = next(
                (
                    row
                    for row in pod.get("spec", {}).get("containers", [])
                    if row.get("name") == "slurmd"
                ),
                {},
            )
            mounts = slurmd.get("volumeMounts", [])
            volumes = {row["name"]: row for row in pod.get("spec", {}).get("volumes", [])}
            # Upstream RenderVolumeMount uses path.Join, removing trailing slashes.
            for name, path in (
                ("slurm-scripts", "/opt/slurm_scripts"),
                ("slurm-scripts-jail", "/mnt/jail.upper/opt/slurm_scripts"),
            ):
                if not any(
                    row.get("name") == name
                    and row.get("mountPath") == path
                    and row.get("readOnly") is True
                    for row in mounts
                ) or volumes.get(name, {}).get("configMap") != {
                    "name": "slurm-scripts",
                    "defaultMode": 493,
                }:
                    raise RuntimeError("runtime recovery worker lost upstream script mount")
            names = ("prolog.sh", "epilog.sh", "hc_program.sh")
            hashes = self.read_worker(
                node, ["sha256sum", *(f"/opt/slurm_scripts/{name}" for name in names)]
            )
            hash_rows = [line.split() for line in hashes.splitlines()]
            if len(hash_rows) != len(names) or any(len(row) != 2 for row in hash_rows):
                raise RuntimeError("runtime recovery worker script hashes are incomplete")
            if {row[1]: row[0] for row in hash_rows} != {
                f"/opt/slurm_scripts/{name}": hashlib.sha256(cm["data"][name].encode()).hexdigest()
                for name in names
            }:
                raise RuntimeError("runtime recovery worker script contents differ")
            if self.read_worker(
                node, ["stat", "-L", "-c", "%a", *(f"/opt/slurm_scripts/{name}" for name in names)]
            ).splitlines() != ["755"] * len(names):
                raise RuntimeError("runtime recovery scripts are not executable")
            if (
                not pod.get("metadata", {}).get("uid")
                or runner._get("pod", node).get("metadata", {}).get("uid") != pod["metadata"]["uid"]
            ):
                raise RuntimeError("runtime recovery worker changed during script verification")
            live = slurm_nodes(runner)[node]
            saved = runner.state.setdefault("runtimeRepairDrains", {})
            flags = set(live.get("State", "").split("+"))
            if "DRAIN" not in flags:
                if node not in saved or flags - {"IDLE", "CLOUD", "MAINTENANCE", "RESERVED"}:
                    raise RuntimeError("runtime recovery drain changed without its intent")
            else:
                if (
                    live.get("Reason") != proof["reason"]
                    or flags != {"IDLE", "CLOUD", "DRAIN", "MAINTENANCE", "RESERVED"}
                    or live.get("CPUAlloc") != "0"
                ):
                    raise RuntimeError("runtime recovery will not clear another drain")
                saved[node] = {"status": "intent", "reason": proof["reason"]}
                runner._save()
                runner._reservation(self.proof["reservation"]["name"])
                runner.authority()
                runner.slurm(f"scontrol update NodeName={node} State=UNDRAIN")
                if "DRAIN" in slurm_nodes(runner)[node]["State"].split("+"):
                    raise RuntimeError("runtime recovery worker remains drained")
            saved[node]["status"] = "complete"
            runner._save()
