"""One evidence-bound replacement for an initial Docker registry reset."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_docker_drains import _handled_failure
from .soperator_install_docker_storage_recovery import observe_docker_storage
from .soperator_install_runtime_recovery import _LIVE, slurm_jobs, slurm_nodes
from .soperator_install_storage_recovery import _accounted, _complete, _time

CHECK = "all-reduce-perf-nccl-in-docker"
KEY = "imagePullRecovery"
_FLAGS = {"IDLE", "CLOUD", "MAINTENANCE", "RESERVED"}
_LIMIT = 16384


def registry_reset(output: str) -> bool:
    """Classify only Docker's pre-execution layer-download reset, without logging it."""
    errors = [line for line in output.splitlines() if line.startswith("docker:")]
    return bool(
        len(output.encode()) <= _LIMIT
        and len(errors) == 1
        and re.fullmatch(
            r"docker: failed to copy: read tcp [^\s]+->[^\s]+: read: connection reset by peer",
            errors[0],
        )
        and "Unable to find image '" in output
        and "Pulling fs layer" in output
        and "Pulling from " in output
        and "Exited with exit code 125" in output
        and not any(
            marker in output
            for marker in (
                "Collective test",
                "Health checker",
                "Avg bus bandwidth",
                "Out of bounds",
            )
        )
    )


def replacement_name(runner: SoperatorChecksExecution, worker: str) -> str:
    proof = runner.state.get(KEY, {}).get(worker)
    return str(proof["replacement"]) if proof and proof["status"] == "ready" else ""


def _barrier(runner: SoperatorChecksExecution) -> Mapping[str, Any]:
    runner.authority()
    runner._verify_isolation()
    runner.require_lifecycle().admission.verify()
    if any(row.get("JobState") in _LIVE for row in slurm_jobs(runner)):
        raise RuntimeError("Image-pull recovery requires terminal Slurm work")
    barrier = runner._reservation(runner.state["reservation"])
    expected = runner.state["acceptance"]
    if barrier["nodes"] != expected["reservationNodes"] or barrier["flags"] != expected["flags"]:
        raise RuntimeError("Image-pull recovery reservation changed")
    if runner.slurm("id -u soperatorchecks").strip() != runner.state["principalUid"]:
        raise RuntimeError("Image-pull recovery principal changed")
    return barrier


def _runtime(
    runner: SoperatorChecksExecution,
    worker: str,
    read_worker: Callable[[str, list[str]], str],
) -> dict[str, Any]:
    pod = runner._get("pod", worker)
    meta, spec = pod.get("metadata", {}), pod.get("spec", {})
    statuses = pod.get("status", {}).get("containerStatuses", [])
    node = runner._get("node", str(spec.get("nodeName") or ""))
    conditions = node.get("status", {}).get("conditions", [])
    owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
    if (
        not meta.get("uid")
        or meta.get("deletionTimestamp")
        or pod.get("status", {}).get("phase") != "Running"
        or len(owners) != 1
        or owners[0].get("kind") != "StatefulSet"
        or not statuses
        or any(
            c.get("ready") is not True or c.get("restartCount") != 0 or not c.get("containerID")
            for c in statuses
        )
        or not node.get("metadata", {}).get("uid")
        or not any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
        or any(
            c.get("status") == "True"
            and c.get("type")
            in {"DiskPressure", "MemoryPressure", "PIDPressure", "HardwareIssuesSuspected"}
            for c in conditions
        )
    ):
        raise RuntimeError("Image-pull recovery worker is not stably healthy")
    controller = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
    parents = [
        owner
        for owner in controller.get("metadata", {}).get("ownerReferences", [])
        if owner.get("controller") is True
    ]
    if (
        controller.get("metadata", {}).get("uid") != owners[0].get("uid")
        or controller.get("metadata", {}).get("deletionTimestamp")
        or len(parents) != 1
        or parents[0].get("kind") != "NodeSet"
    ):
        raise RuntimeError("Image-pull recovery lost native worker ownership")
    nodeset = runner._get("nodeset", parents[0]["name"])
    if (
        not parents[0].get("uid")
        or nodeset.get("metadata", {}).get("uid") != parents[0]["uid"]
        or nodeset.get("metadata", {}).get("deletionTimestamp")
    ):
        raise RuntimeError("Image-pull recovery lost native NodeSet ownership")
    storage = observe_docker_storage(read_worker, worker)
    if (
        storage["ready"] is not True
        or storage["mount"][0].get("target") != "/mnt/jail/mnt/image-storage"
        or storage["mount"][0].get("fstype") not in {"ext4", "xfs"}
    ):
        raise RuntimeError("Image-pull recovery requires a ready Docker daemon on private storage")
    return {
        "podUid": meta["uid"],
        "nodeUid": node["metadata"]["uid"],
        "owners": owners,
        "nodeset": parents[0],
        "containers": sorted(c["containerID"] for c in statuses),
        "storage": storage,
    }


def _failure(
    runner: SoperatorChecksExecution, name: str, entry: Mapping[str, Any]
) -> dict[str, Any]:
    live = runner._get("job", name)
    meta = live.get("metadata", {})
    jid = str(meta.get("annotations", {}).get("slurm-job-id", ""))
    if (
        not entry.get("submitted")
        or not entry.get("uid")
        or meta.get("uid") != entry["uid"]
        or meta.get("deletionTimestamp")
        or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
        or job_execution_digest(live) != entry.get("execution")
        or not _complete(live)
        or not re.fullmatch(r"[1-9][0-9]*", jid)
        or entry.get("slurmIds") not in (None, [jid])
    ):
        raise RuntimeError("Image-pull recovery lost its exact terminal native submitter")
    runner._verify_execution_authority(CHECK, entry["epoch"], generation=True)
    cron = runner._target_cronjob(CHECK)
    if not cron or checks_digest(cron["spec"]["jobTemplate"]) != entry["epoch"]["template"]:
        raise RuntimeError("Image-pull recovery native policy changed")
    row = _accounted(runner, jid)
    worker = _identifier(entry["worker"])
    allocation = runner.state["acceptance"]["resources"][worker]
    node = slurm_nodes(runner)[worker]
    try:
        cpus = int(allocation["cores"]) * int(node["ThreadsPerCore"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Image-pull recovery CPU topology is incomplete") from exc
    tres = dict(item.split("=", 1) for item in row["AllocTRES"].split(",") if "=" in item)
    if (
        row["JobName"] != name
        or row["User"] != "soperatorchecks"
        or row["Reservation"] != runner.state["reservation"]
        or row["Partition"] != "hidden"
        or row["NodeList"] != worker
        or row["NumNodes"] != "1"
        or cpus <= 0
        or row["NumCPUs"] != str(cpus)
        or node.get("CPUTot") != str(cpus)
        or node.get("CPUEfctv") != str(cpus)
        or tres.get("cpu") != str(cpus)
        or tres.get("gres/gpu") != str(allocation["gpus"])
        or entry.get("gpuCount") != allocation["gpus"]
        or _time(row["EndTime"]) <= _time(row["StartTime"])
    ):
        raise RuntimeError("Image-pull recovery Slurm allocation changed")
    return {"job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)}, "slurm": row}


def recover_initial_image_pull(
    runner: SoperatorChecksExecution,
    read_worker: Callable[[str, list[str]], str],
) -> bool:
    """Resume a proven failure once; preserve old Jobs and all unaffected acceptance."""
    state = runner.state
    if state.get("phase") != "acceptance" or not state.get("freshInstall"):
        return False
    if not state.get("installReservationIntent") or state.get("scheduleRelease"):
        return False
    unfinished = [(n, e) for n, e in state["jobs"].items() if e.get("status") != "complete"]
    if any(e.get("check") != CHECK for _, e in unfinished):
        return False
    saved = state.get(KEY, {})
    pending = {w: p for w, p in saved.items() if p["status"] != "ready"}
    proposals = {}
    for name, entry in unfinished:
        worker = _identifier(entry["worker"])
        if worker in pending:
            continue
        # An interrupted submission or still-running replacement belongs to the
        # ordinary acceptance poller. Only terminal failures enter recovery.
        live = runner._get("job", name)
        jid = str(live.get("metadata", {}).get("annotations", {}).get("slurm-job-id", ""))
        if not _complete(live) or not re.fullmatch(r"[1-9][0-9]*", jid):
            return False
        rows = [line.split("|") for line in runner._accounting([jid]).splitlines()]
        rows = [row for row in rows if row[0] == jid]
        if not rows:
            return False
        if len(rows) != 1 or len(rows[0]) != 8:
            raise RuntimeError("Image-pull recovery accounting is ambiguous")
        outcome = tuple(rows[0][1:3])
        if outcome == ("COMPLETED", "0:0"):
            continue
        if outcome != ("FAILED", "125:0"):
            return False
        proof = _failure(runner, name, entry)
        row = proof["slurm"]
        if (row["JobState"], row["ExitCode"]) != ("FAILED", "125:0"):
            return False
        if worker in saved:
            raise RuntimeError(
                f"Image-pull recovery exhausted for {worker}; failed replacement retained"
            )
        output = runner.slurm(
            f"head -c {_LIMIT + 1} -- /opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{row['JobId']}.out"
        )
        if not registry_reset(output):
            return False
        _handled_failure(runner, proof)
        node = slurm_nodes(runner)[worker]
        prefix = f"[node_problem] {CHECK}: job {row['JobId']} [slurm_job]"
        match = re.fullmatch(re.escape(prefix) + r" \[root@([^\]]+)\]", node.get("Reason", ""))
        if not match or _time(match[1]) < _time(row["EndTime"]):
            raise RuntimeError("Image-pull recovery cannot attribute the worker drain")
        proof.update(
            status="intent",
            reason=node["Reason"],
            outputSha256=checks_digest(output),
            runtime=_runtime(runner, worker, read_worker),
            replacement="cxcli-check-"
            + checks_digest([runner.operation_id, name, entry["uid"], "registry-reset"]).split(":")[
                1
            ][:24],
        )
        proposals[worker] = proof
    if not proposals and not pending:
        return False
    _barrier(runner)
    state.setdefault(KEY, {}).update(proposals)
    runner._save()
    pending.update(proposals)
    for worker, proof in pending.items():
        name, entry = proof["job"]["name"], proof["job"]["entry"]
        if state["jobs"].get(name) != entry or _failure(runner, name, entry) != {
            "job": proof["job"],
            "slurm": proof["slurm"],
        }:
            raise RuntimeError("Image-pull recovery predecessor changed")
        _handled_failure(runner, proof)
        if _runtime(runner, worker, read_worker) != proof["runtime"]:
            raise RuntimeError("Image-pull recovery runtime identity changed")
        barrier = _barrier(runner)
        if barrier["users"] != ["root"]:
            runner.authority()
            runner.slurm(
                f"scontrol update ReservationName={_identifier(barrier['name'])} Users=root"
            )
        if _barrier(runner)["users"] != ["root"]:
            raise RuntimeError("Image-pull recovery could not close temporary authorization")
        node = slurm_nodes(runner)[worker]
        flags = set(node.get("State", "").split("+"))
        if node.get("CPUAlloc") != "0" or node.get("AllocMem") != "0":
            raise RuntimeError("Image-pull recovery worker is allocated")
        if flags == _FLAGS | {"DRAIN"} and node.get("Reason") == proof["reason"]:
            proof["status"] = "undrain-intent"
            runner._save()
            runner.authority()
            runner.slurm(f"scontrol update NodeName={worker} State=UNDRAIN")
        elif flags != _FLAGS or proof["status"] != "undrain-intent":
            raise RuntimeError("Image-pull recovery will not clear an unrelated drain")
        if set(slurm_nodes(runner)[worker]["State"].split("+")) != _FLAGS:
            raise RuntimeError("Image-pull recovery drain restoration did not converge")
        if name.endswith("-initial-run"):
            state.setdefault("creationGuards", {})[CHECK] = {"name": name, "uid": entry["uid"]}
        del state["jobs"][name]
        proof["status"] = "ready"
        runner._save()
        runner.emit(f"Preserved failed Docker pull on {worker}; admitted one fresh native check")
    return True
