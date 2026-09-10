"""Retain native Docker failures while recovering omitted worker runtime bindings."""

from __future__ import annotations

import copy
import re
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_runtime_recovery import slurm_jobs
from .soperator_install_storage_recovery import _accounted, _complete, _time

DOCKER_CHECK = "all-reduce-perf-nccl-in-docker"
_ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}


def capture_docker_failure(runner: SoperatorChecksExecution, source_dir: Path) -> dict[str, Any]:
    state = runner.state
    unfinished = [(n, e) for n, e in state.get("jobs", {}).items() if e.get("status") != "complete"]
    workers = state.get("acceptance", {}).get("gpuWorkers", [])
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationIntent")
        or not workers
        or len(unfinished) != len(workers)
        or {e.get("worker") for _, e in unfinished} != set(workers)
        or any(e.get("check") != DOCKER_CHECK for _, e in unfinished)
    ):
        raise RuntimeError(
            "Docker repair requires failed initial native checks on every GPU worker"
        )
    if any(row.get("JobState") in _ACTIVE for row in slurm_jobs(runner)):
        raise RuntimeError("Docker repair cannot overlap active Slurm work")
    jobs = runner._jobs()
    for name, entry in state["jobs"].items():
        live = jobs.get(name, {})
        meta = live.get("metadata", {})
        if (
            not entry.get("submitted")
            or not entry.get("uid")
            or meta.get("uid") != entry["uid"]
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
            or job_execution_digest(live) != entry["execution"]
            or not _complete(live)
        ):
            raise RuntimeError("Docker repair lost an exact native submitter")
    reservation = runner._reservation(state["reservation"])
    if reservation["users"] != ["root", "soperatorchecks"]:
        raise RuntimeError("Docker repair temporary authorization changed")
    if runner.slurm("id -u soperatorchecks").strip() != state.get("principalUid"):
        raise RuntimeError("Docker repair principal identity changed")
    failures = []
    for name, entry in sorted(unfinished):
        runner._verify_execution_authority(DOCKER_CHECK, entry["epoch"], generation=True)
        cron = runner._target_cronjob(DOCKER_CHECK)
        if not cron or checks_digest(cron["spec"]["jobTemplate"]) != entry["epoch"]["template"]:
            raise RuntimeError("Docker repair native template changed")
        job = jobs[name]
        jid = str(job["metadata"].get("annotations", {}).get("slurm-job-id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", jid) or entry.get("slurmIds") not in (None, [jid]):
            raise RuntimeError("Docker repair Slurm submission is ambiguous")
        worker = _identifier(entry["worker"])
        row = _accounted(runner, jid)
        allocation = state["acceptance"]["resources"][worker]
        tres = dict(p.split("=", 1) for p in row["AllocTRES"].split(",") if "=" in p)
        if (
            row["JobState"] != "FAILED"
            or row["ExitCode"] != "1:0"
            or row["JobName"] != name
            or row["User"] != "soperatorchecks"
            or row["Reservation"] != reservation["name"]
            or row["Partition"] != "hidden"
            or row["NodeList"] != worker
            or row["NumNodes"] != "1"
            or row["NumCPUs"] != str(allocation["cores"])
            or tres.get("gres/gpu") != str(allocation["gpus"])
            or _time(row["EndTime"]) <= _time(row["StartTime"])
            or _time(job["metadata"]["creationTimestamp"].removesuffix("Z"))
            > _time(row["StartTime"])
        ):
            raise RuntimeError("Docker repair failed allocation changed")
        pod = runner._get("pod", worker)
        meta, spec, status = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
        owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
        if (
            not meta.get("uid")
            or meta.get("deletionTimestamp")
            or status.get("phase") != "Running"
            or not status.get("containerStatuses")
            or any(
                c.get("restartCount") != 0 or c.get("ready") is not True
                for c in status["containerStatuses"]
            )
            or _time(meta["creationTimestamp"].removesuffix("Z")) > _time(row["StartTime"])
            or len(owners) != 1
            or owners[0].get("kind") != "StatefulSet"
        ):
            raise RuntimeError("Docker repair lost its original ready worker")
        sts = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
        parents = [
            o
            for o in sts.get("metadata", {}).get("ownerReferences", [])
            if o.get("controller") is True
        ]
        if (
            sts.get("metadata", {}).get("uid") != owners[0]["uid"]
            or len(parents) != 1
            or parents[0].get("kind") != "NodeSet"
        ):
            raise RuntimeError("Docker repair lost native worker ownership")
        node = runner._get("nodeset", parents[0]["name"])
        if node.get("metadata", {}).get("uid") != parents[0]["uid"] or node.get("spec", {}).get(
            "configMapRefSupervisord"
        ):
            raise RuntimeError("Docker repair requires the omitted native Supervisor binding")
        refs = [
            v.get("configMap", {}).get("name")
            for v in spec.get("volumes", [])
            if v.get("name") == "supervisord-config"
        ]
        if len(refs) != 1 or not refs[0]:
            raise RuntimeError("Docker repair worker Supervisor source is ambiguous")
        default = runner._get("configmap", refs[0])
        if (
            not default.get("metadata", {}).get("uid")
            or "[program:dockerd]" in default.get("data", {}).get("supervisord.conf", "")
            or "[program:slurmd]" not in default.get("data", {}).get("supervisord.conf", "")
        ):
            raise RuntimeError("Docker repair default Supervisor cause changed")
        output = runner.slurm(
            "tail -c 16384 "
            + shlex.quote(
                f"/opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{jid}.out"
            )
        )
        if (
            "failed to connect to the docker API at unix:///var/run/docker.sock" not in output
            or "connect: no such file or directory" not in output
        ):
            raise RuntimeError("Docker repair has no exact native missing-socket failure")
        failures.append(
            {
                "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
                "slurm": row,
                "worker": {"name": worker, "uid": meta["uid"], "nodeset": parents[0]},
                "defaultSupervisor": {
                    "name": refs[0],
                    "uid": default["metadata"]["uid"],
                    "dataSha256": checks_digest(default["data"]),
                },
                "outputSha256": checks_digest(output),
            }
        )
    configs = []
    for name, files in (
        ("custom-supervisord-config", ["supervisord.conf"]),
        ("image-storage", ["daemon.json", "enroot.conf"]),
    ):
        live = runner._get("configmap", name)
        expected = {
            f: (source_dir / "helm/soperator-custom-configmaps/config-files" / f).read_text()
            for f in files
        }
        if not live.get("metadata", {}).get("uid") or live.get("data") != expected:
            raise RuntimeError("Docker repair lost the exact pinned upstream runtime config")
        configs.append(
            {"name": name, "uid": live["metadata"]["uid"], "dataSha256": checks_digest(expected)}
        )
    return {"failures": failures, "reservation": reservation, "configs": configs}


def close_docker_repair_reservation(
    runner: SoperatorChecksExecution, repair: Mapping[str, Any]
) -> None:
    proof = repair["dockerFailure"]
    identity = checks_digest(repair)
    saved = runner.state.get("dockerRepairQuiescence")
    if saved and saved != {"repair": identity, "status": "complete"}:
        raise RuntimeError("Docker recovery identity changed")
    if saved:
        return
    for failure in proof["failures"]:
        live = runner._get("job", failure["job"]["name"])
        if (
            live.get("metadata", {}).get("uid") != failure["job"]["uid"]
            or not _complete(live)
            or job_execution_digest(live) != failure["job"]["entry"]["execution"]
            or _accounted(runner, failure["slurm"]["JobId"]) != failure["slurm"]
        ):
            raise RuntimeError("Docker recovery native failure changed")
    if any(row.get("JobState") in _ACTIVE for row in slurm_jobs(runner)):
        raise RuntimeError("Docker recovery has active Slurm work")
    observed = runner._reservation(proof["reservation"]["name"])
    if observed["users"] not in (["root"], ["root", "soperatorchecks"]):
        raise RuntimeError("Docker recovery temporary authorization changed")
    if {k: v for k, v in observed.items() if k != "users"} != {
        k: v for k, v in proof["reservation"].items() if k != "users"
    }:
        raise RuntimeError("Docker recovery reservation changed")
    if observed["users"] != ["root"]:
        runner.authority()
        runner.slurm(
            "scontrol update ReservationName=" + _identifier(observed["name"]) + " Users=root"
        )
    if runner._reservation(observed["name"])["users"] != ["root"]:
        raise RuntimeError("Docker recovery authorization did not close")
    runner.state["dockerRepairQuiescence"] = {"repair": identity, "status": "complete"}
    runner._save()
