"""Bind a failed native Enroot submission to its host AppArmor denial."""

from __future__ import annotations

import copy
import json
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_runtime_recovery import slurm_jobs
from .soperator_install_storage_recovery import _accounted, _complete, _time


def userns_denial(
    rows: Sequence[Mapping[str, Any]], *, boot: str, started: datetime, ended: datetime
) -> dict[str, Any]:
    """Require the same process transition and denial in its actual execution window."""
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("_BOOT_ID") != boot or row.get("_TRANSPORT") != "kernel":
            continue
        try:
            observed = datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"]) / 1e6, UTC)
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if (
            not started.replace(tzinfo=UTC)
            <= observed
            <= ended.replace(tzinfo=UTC) + timedelta(seconds=2)
        ):
            continue
        message = str(row.get("MESSAGE", ""))
        fields: dict[str, str] = {}
        for match in re.finditer(r'(\w+)=(?:"([^"]*)"|(\S+))', message):
            fields[match[1]] = match[2] if match[2] is not None else match[3]
        pid = fields.get("pid", "")
        if not pid.isdigit() or fields.get("comm") != "enroot-nsenter":
            continue
        kind = ""
        if (
            fields.get("apparmor") == "AUDIT"
            and fields.get("operation") == "userns_create"
            and fields.get("profile") == "unconfined"
            and fields.get("target") == "unprivileged_userns"
        ):
            kind = "transition"
        if (
            fields.get("apparmor") == "DENIED"
            and fields.get("operation") == "capable"
            and fields.get("profile") == "unprivileged_userns"
            and fields.get("capname") == "sys_admin"
        ):
            kind = "denial"
        if kind:
            found.setdefault(pid, {})[kind] = {
                "timestamp": observed.isoformat(),
                "sha256": checks_digest(message),
            }
    pairs = [
        {"pid": pid, **v}
        for pid, v in found.items()
        if set(v) == {"transition", "denial"}
        and abs(
            (
                datetime.fromisoformat(v["transition"]["timestamp"])
                - datetime.fromisoformat(v["denial"]["timestamp"])
            ).total_seconds()
        )
        < 1
    ]
    if len(pairs) != 1:
        raise RuntimeError("Enroot repair requires one exact boot-bound AppArmor userns denial")
    return {"boot": boot, **pairs[0]}


def capture_userns_failure(
    runner: SoperatorChecksExecution, *, env: Mapping[str, str], kube_context: str
) -> dict[str, Any]:
    state = runner.state
    unfinished = [(n, e) for n, e in state.get("jobs", {}).items() if e.get("status") != "complete"]
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationIntent")
        or len(unfinished) != 1
        or unfinished[0][1].get("check") != "prepull-container-image"
    ):
        raise RuntimeError("Enroot repair requires one failed initial native container preparation")
    name, entry = unfinished[0]
    jobs = runner._jobs()
    for job_name, record in state["jobs"].items():
        live = jobs.get(job_name, {})
        if (
            not record.get("submitted")
            or not record.get("uid")
            or live.get("metadata", {}).get("uid") != record["uid"]
            or live["metadata"].get("deletionTimestamp")
            or live["metadata"].get("labels", {}).get("cxcli.nebius.ai/check-operation")
            != runner.operation_id
            or job_execution_digest(live) != record["execution"]
            or not _complete(live)
        ):
            raise RuntimeError("Enroot repair lost its exact native submitter")
    runner._verify_execution_authority(entry["check"], entry["epoch"], generation=True)
    cron = runner._target_cronjob(entry["check"])
    if not cron or checks_digest(cron["spec"]["jobTemplate"]) != entry["epoch"]["template"]:
        raise RuntimeError("Enroot repair native schedule changed")
    job_id = jobs[name]["metadata"].get("annotations", {}).get("slurm-job-id", "")
    if not re.fullmatch(r"[1-9][0-9]*", job_id) or entry.get("slurmIds") != [job_id]:
        raise RuntimeError("Enroot repair Slurm submission is ambiguous")
    if any(
        r.get("JobState") in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}
        for r in slurm_jobs(runner)
    ):
        raise RuntimeError("Enroot repair requires all Slurm writers to be terminal")
    row = _accounted(runner, job_id)
    worker = _identifier(entry["worker"])
    reservation = runner._reservation(state["reservation"])
    allocation = state["acceptance"]["resources"][worker]
    tres = dict(p.split("=", 1) for p in row["AllocTRES"].split(","))
    if (
        row["JobState"] != "FAILED"
        or row["ExitCode"] != "1:0"
        or row["JobName"] != name
        or row["User"] != "soperatorchecks"
        or runner.slurm("id -u soperatorchecks").strip() != state["principalUid"]
        or row["Reservation"] != reservation["name"]
        or row["Partition"] != "hidden"
        or row["NodeList"] != worker
        or row["NumNodes"] != "1"
        or row["NumCPUs"] != str(allocation["cores"])
        or tres.get("gres/gpu") != str(allocation["gpus"])
        or reservation["users"] != ["root", "soperatorchecks"]
    ):
        raise RuntimeError("Enroot repair lost its failed native allocation or reservation")
    start, end = _time(row["StartTime"]), _time(row["EndTime"])
    if end <= start:
        raise RuntimeError("Enroot repair execution interval is invalid")
    pod = runner._get("pod", worker)
    meta, spec = pod.get("metadata", {}), pod.get("spec", {})
    owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
    if (
        not meta.get("uid")
        or meta.get("deletionTimestamp")
        or len(owners) != 1
        or owners[0].get("kind") != "StatefulSet"
        or pod.get("status", {}).get("phase") != "Running"
        or _time(meta["creationTimestamp"].removesuffix("Z")) > start
        or any(
            c.get("restartCount") != 0 for c in pod.get("status", {}).get("containerStatuses", [])
        )
    ):
        raise RuntimeError("Enroot repair lost the original running worker")
    controller = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
    node_owners = [
        o
        for o in controller.get("metadata", {}).get("ownerReferences", [])
        if o.get("controller") is True
    ]
    if (
        controller.get("metadata", {}).get("uid") != owners[0]["uid"]
        or len(node_owners) != 1
        or node_owners[0].get("kind") != "NodeSet"
    ):
        raise RuntimeError("Enroot repair lost native NodeSet ownership")
    node = runner._get("node", spec["nodeName"])
    boot = str(node["status"]["nodeInfo"]["bootID"]).replace("-", "")
    native = [c for c in spec.get("containers", []) if c.get("name") == "slurmd"]
    roots = [v for v in spec.get("volumes", []) if v.get("hostPath", {}).get("path") == "/"]
    mounts = [
        m
        for c in native
        for m in c.get("volumeMounts", [])
        if m.get("mountPath") == "/run/nvidia/driver"
    ]
    if (
        not re.fullmatch(r"[0-9a-f]{32}", boot)
        or not node["metadata"].get("uid")
        or node["metadata"].get("name") != spec["nodeName"]
        or len(native) != 1
        or len(roots) != 1
        or mounts
        != [{"name": roots[0]["name"], "mountPath": "/run/nvidia/driver", "readOnly": True}]
        or native[0].get("resources", {}).get("limits", {}).get("ephemeral-storage") != "55Gi"
    ):
        raise RuntimeError("Enroot repair lost worker boot identity")
    output = runner.slurm(
        "tail -c 16384 "
        + shlex.quote(
            f"/opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{job_id}.out"
        )
    )
    if (
        "pyxis: imported docker image:" not in output
        or "enroot-nsenter: failed to create user namespace: Permission denied" not in output
    ):
        raise RuntimeError("Enroot repair has no native imported-image namespace failure")
    times = re.findall(
        r"\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)\] error: pyxis: container start failed", output
    )
    if len(times) != 1:
        raise RuntimeError("Enroot repair has no exact native failure timestamp")
    failed_at = datetime.fromisoformat(times[0])
    if not start <= failed_at <= end + timedelta(seconds=1):
        raise RuntimeError("Enroot native failure is outside its Slurm execution interval")
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            "soperator",
            "exec",
            worker,
            "-c",
            "slurmd",
            "--",
            "env",
            "TZ=UTC",
            "journalctl",
            "--directory=/run/nvidia/driver/var/log/journal",
            "--since=" + (end - timedelta(seconds=10)).isoformat(),
            "--until=" + (end + timedelta(seconds=2)).isoformat(),
            "--grep=enroot-nsenter",
            "--lines=101",
            "--no-pager",
            "--output=json",
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    if len(result.stdout.encode()) > 1024 * 1024:
        raise RuntimeError("Enroot denial journal exceeded its bounded read")
    rows = [json.loads(s) for s in result.stdout.splitlines() if s.strip()]
    if len(rows) > 100:
        raise RuntimeError("Enroot denial journal is truncated")
    denial = userns_denial(
        rows, boot=boot, started=failed_at - timedelta(seconds=1), ended=failed_at
    )
    return {
        "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
        "slurm": row,
        "reservation": reservation,
        "worker": {
            "name": worker,
            "uid": meta["uid"],
            "node": spec["nodeName"],
            "nodeUid": node["metadata"]["uid"],
            "nodeset": node_owners[0],
        },
        "denial": denial,
        "outputSha256": checks_digest(output),
    }


def close_userns_repair_reservation(
    runner: SoperatorChecksExecution, repair: Mapping[str, Any]
) -> None:
    proof = repair["usernsFailure"]
    key, identity = "usernsRepairQuiescence", checks_digest(repair)
    saved = runner.state.get(key)
    if saved and saved != {"repair": identity, "status": "complete"}:
        raise RuntimeError("Enroot recovery identity changed")
    live = runner._get("job", proof["job"]["name"])
    if (
        live.get("metadata", {}).get("uid") != proof["job"]["uid"]
        or not _complete(live)
        or job_execution_digest(live) != proof["job"]["entry"]["execution"]
        or _accounted(runner, proof["slurm"]["JobId"]) != proof["slurm"]
    ):
        raise RuntimeError("Enroot recovery native failure changed")
    if any(
        r.get("JobState") in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}
        for r in slurm_jobs(runner)
    ):
        raise RuntimeError("Enroot recovery has active Slurm work")
    observed = runner._reservation(proof["reservation"]["name"])
    if {k: v for k, v in observed.items() if k != "users"} != {
        k: v for k, v in proof["reservation"].items() if k != "users"
    }:
        raise RuntimeError("Enroot recovery reservation changed")
    if observed["users"] != ["root"]:
        runner.authority()
        runner.slurm(
            "scontrol update ReservationName=" + _identifier(observed["name"]) + " Users=root"
        )
    if runner._reservation(observed["name"])["users"] != ["root"]:
        raise RuntimeError("Enroot recovery check authorization did not close")
    runner.state[key] = {"repair": identity, "status": "complete"}
    runner._save()
