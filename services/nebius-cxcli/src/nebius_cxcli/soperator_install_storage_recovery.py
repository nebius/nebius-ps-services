"""Retire the exact native import interrupted by a proven worker eviction."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_eviction_journal import JournalReader, capture_worker_eviction
from .soperator_install_runtime_recovery import _LIVE, slurm_jobs

_KEYS = (
    "JobId",
    "JobName",
    "User",
    "Partition",
    "Reservation",
    "SubmitTime",
    "StartTime",
    "NodeList",
    "NumNodes",
    "NumCPUs",
    "AllocTRES",
    "SubmitLine",
)
_FAILED = {"CANCELLED", "FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED"}


def _time(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z)?", value):
        raise RuntimeError("storage recovery timestamp is incomplete")
    try:
        return datetime.fromisoformat(value.removesuffix("Z"))
    except ValueError as exc:
        raise RuntimeError("storage recovery timestamp is incomplete") from exc


def _control_identity(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        key: row.get("UserId", "").split("(")[0] if key == "User" else row.get(key) for key in _KEYS
    }


def _accounted(runner: SoperatorChecksExecution, job_id: str) -> dict[str, str]:
    output = runner.slurm(
        "env SLURM_TIME_FORMAT=standard TZ=UTC sacct -n -P -j "
        + _identifier(job_id)
        + " --format=JobIDRaw,State,ExitCode,NodeList,User,JobName%100,Reservation%100,"
        "AllocTRES%300,Start,Submit,Partition,AllocCPUS,AllocNodes,End,SubmitLine%2000"
    )
    rows = [line.split("|", 14) for line in output.splitlines()]
    rows = [row for row in rows if row[0] == job_id]
    if len(rows) != 1 or len(rows[0]) != 15:
        raise RuntimeError("storage repair has no exact Slurm accounting record")
    return dict(
        zip(
            (
                "JobId",
                "JobState",
                "ExitCode",
                "NodeList",
                "User",
                "JobName",
                "Reservation",
                "AllocTRES",
                "StartTime",
                "SubmitTime",
                "Partition",
                "NumCPUs",
                "NumNodes",
                "EndTime",
                "SubmitLine",
            ),
            rows[0],
            strict=True,
        )
    )


def _complete(job: Mapping[str, Any]) -> bool:
    return not job.get("status", {}).get("active", 0) and any(
        c.get("type") == "Complete" and c.get("status") == "True"
        for c in job.get("status", {}).get("conditions", [])
    )


def capture_storage_failure(
    runner: SoperatorChecksExecution, *, journal: JournalReader
) -> dict[str, Any]:
    """Read-only admission; never infer a successful check from a lost worker."""
    state = runner.state
    unfinished = [(n, e) for n, e in state["jobs"].items() if e["status"] != "complete"]
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationIntent")
        or len(unfinished) != 1
        or unfinished[0][1]["check"] != "prepull-container-image"
    ):
        raise RuntimeError("storage repair requires one interrupted native image import")
    name, entry = unfinished[0]
    jobs = runner._jobs()
    for job_name, record in state["jobs"].items():
        live = jobs.get(job_name, {})
        if (
            not record.get("submitted")
            or not record.get("uid")
            or live.get("metadata", {}).get("uid") != record["uid"]
            or live.get("metadata", {}).get("deletionTimestamp")
            or live.get("metadata", {}).get("labels", {}).get("cxcli.nebius.ai/check-operation")
            != runner.operation_id
            or job_execution_digest(live) != record["execution"]
            or not _complete(live)
        ):
            raise RuntimeError("storage repair lost exact completed native submitter evidence")
    cron = runner._target_cronjob(entry["check"])
    if not cron or checks_digest(cron["spec"]["jobTemplate"]) != entry["epoch"]["template"]:
        raise RuntimeError("storage repair native schedule changed")
    runner._verify_execution_authority(entry["check"], entry["epoch"], generation=True)
    job = jobs[name]
    job_id = job["metadata"].get("annotations", {}).get("slurm-job-id", "")
    if not re.fullmatch(r"[1-9][0-9]*", job_id) or entry.get("slurmIds") != [job_id]:
        raise RuntimeError("storage repair submission identity is ambiguous")
    rows = slurm_jobs(runner)
    if any(row.get("JobState") in _LIVE and row.get("JobId") != job_id for row in rows):
        raise RuntimeError("storage repair has foreign Slurm work")
    row = _accounted(runner, job_id)
    worker = _identifier(entry["worker"])
    reservation = runner._reservation(state["reservation"])
    allocation = state["acceptance"]["resources"][worker]
    tres = dict(part.split("=", 1) for part in row.get("AllocTRES", "").split(",") if "=" in part)
    if (
        row.get("JobName") != name
        or row.get("User") != "soperatorchecks"
        or runner.slurm("id -u soperatorchecks").strip() != state["principalUid"]
        or row.get("Reservation") != reservation["name"]
        or row.get("Partition") != "hidden"
        or row.get("NodeList") != worker
        or row.get("NumNodes") != "1"
        or row.get("NumCPUs") != str(allocation["cores"])
        or tres.get("gres/gpu", "0") != str(allocation["gpus"])
        or row.get("JobState") not in ({"RUNNING"} | _FAILED)
        or reservation["users"] != ["root", "soperatorchecks"]
    ):
        raise RuntimeError("storage repair lost its exact native allocation or reservation")
    if any(
        live.get("JobState") in _LIVE
        and _control_identity(live) != {key: row.get(key) for key in _KEYS}
        for live in rows
    ):
        raise RuntimeError("storage repair live job differs from its accounting identity")
    pod = runner._get("pod", worker)
    meta, spec = pod.get("metadata", {}), pod.get("spec", {})
    owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
    if (
        not meta.get("uid")
        or meta.get("deletionTimestamp")
        or len(owners) != 1
        or owners[0].get("kind") != "StatefulSet"
        or spec.get("hostPID", False)
        or not spec.get("nodeName")
        or pod.get("status", {}).get("phase") != "Running"
    ):
        raise RuntimeError("storage repair replacement worker identity is incomplete")
    controller = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
    controllers = [
        o
        for o in controller.get("metadata", {}).get("ownerReferences", [])
        if o.get("controller") is True
    ]
    if (
        controller.get("metadata", {}).get("uid") != owners[0]["uid"]
        or len(controllers) != 1
        or controllers[0].get("kind") != "NodeSet"
    ):
        raise RuntimeError("storage repair worker lost native NodeSet ownership")
    if row["JobState"] == "RUNNING" and row["EndTime"] != "Unknown":
        raise RuntimeError("storage repair running accounting has an unexpected end time")
    event = capture_worker_eviction(
        pod,
        runner._get("node", spec["nodeName"]),
        started=_time(row["StartTime"]),
        ended=_time(row["EndTime"]) if row["JobState"] in _FAILED else None,
        read=journal,
    )
    return {
        "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
        "slurm": {key: row.get(key) for key in _KEYS},
        "executionInterval": {"start": row["StartTime"], "end": row["EndTime"]},
        "reservation": reservation,
        "worker": {
            "name": worker,
            "uid": meta["uid"],
            "created": meta["creationTimestamp"],
            "node": spec["nodeName"],
            "controller": owners[0],
            "nodeset": controllers[0],
        },
        "eviction": event,
    }


class InstallStorageRecovery:
    def __init__(self, runner: SoperatorChecksExecution, repair: Mapping[str, Any]):
        self.runner, self.repair = runner, repair
        self.proof = repair["storageFailure"]

    def _job(self) -> Mapping[str, Any]:
        expected = self.proof["job"]
        live = self.runner._get("job", expected["name"])
        if (
            live.get("metadata", {}).get("uid") != expected["uid"]
            or live.get("metadata", {}).get("deletionTimestamp")
            or not _complete(live)
            or job_execution_digest(live) != expected["entry"]["execution"]
            or live.get("metadata", {}).get("annotations", {}).get("slurm-job-id")
            != self.proof["slurm"]["JobId"]
        ):
            raise RuntimeError("storage recovery native submitter changed")
        return live

    def _terminal(self) -> Mapping[str, Any] | None:
        expected = self.proof["slurm"]
        rows = [
            line.split("|") for line in self.runner._accounting([expected["JobId"]]).splitlines()
        ]
        parents = [row for row in rows if row[0] == expected["JobId"]]
        if len(parents) != 1 or len(parents[0]) < 8:
            raise RuntimeError("storage recovery terminal accounting is unavailable")
        row = parents[0]
        if (
            row[3] != expected["NodeList"]
            or row[4] != "soperatorchecks"
            or row[5] != expected["JobName"]
            or row[6] != expected["Reservation"]
            or dict(part.split("=", 1) for part in row[7].split(","))
            != dict(part.split("=", 1) for part in expected["AllocTRES"].split(","))
        ):
            raise RuntimeError("storage recovery accounting identity changed")
        status = row[1].split()[0]
        if status in _LIVE:
            return None
        if status not in _FAILED:
            raise RuntimeError("an evicted submission cannot become successful acceptance")
        return {"jobId": row[0], "state": status, "exitCode": row[2], "allocation": row[7]}

    def quiesce(self) -> None:
        runner = self.runner
        key, identity = "storageRepairQuiescence", checks_digest(self.repair)
        saved = runner.state.get(key)
        if saved and saved.get("repair") != identity:
            raise RuntimeError("storage recovery receipt identity changed")
        self._job()
        if saved and saved.get("status") == "complete":
            if self._terminal() != saved["terminal"]:
                raise RuntimeError("storage recovery terminal evidence changed")
            return
        pod = runner._get("pod", self.proof["worker"]["name"])
        if pod.get("metadata", {}).get("uid") != self.proof["worker"]["uid"]:
            raise RuntimeError("storage recovery replacement worker changed before cancellation")
        expected = self.proof["slurm"]
        rows = slurm_jobs(runner)
        for row in rows:
            if row.get("JobState") not in _LIVE:
                continue
            if _control_identity(row) != expected or row["JobState"] != "RUNNING":
                raise RuntimeError("storage recovery cannot cancel foreign or changed work")
        observed = runner._reservation(self.proof["reservation"]["name"])
        if {k: v for k, v in observed.items() if k != "users"} != {
            k: v for k, v in self.proof["reservation"].items() if k != "users"
        }:
            raise RuntimeError("storage recovery reservation changed")
        if not saved:
            runner.state[key] = {"repair": identity, "status": "cancel-intent"}
            runner._save()
        if any(row.get("JobState") in _LIVE for row in rows):
            runner.authority()
            runner.slurm("scancel " + _identifier(expected["JobId"]))
        terminal = runner._until(self._terminal, "evicted native import to become terminal")
        if any(row.get("JobState") in _LIVE for row in slurm_jobs(runner)):
            raise RuntimeError("storage recovery has remaining live Slurm work")
        runner.authority()
        runner.slurm(
            "scontrol update ReservationName=" + _identifier(observed["name"]) + " Users=root"
        )
        if runner._reservation(observed["name"])["users"] != ["root"]:
            raise RuntimeError("storage recovery check authorization did not close")
        runner.state[key] = {"repair": identity, "status": "complete", "terminal": terminal}
        runner._save()
