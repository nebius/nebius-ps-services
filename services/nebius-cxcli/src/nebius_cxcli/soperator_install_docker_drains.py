"""Recover only drains caused by the sealed missing-Docker failures."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import UTC
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_docker_recovery import DOCKER_CHECK
from .soperator_install_runtime_recovery import (
    _JOB_KEYS,
    _LIVE,
    _SUBMIT,
    PROBE,
    InstallRuntimeRecovery,
    slurm_jobs,
    slurm_nodes,
)
from .soperator_install_storage_recovery import _accounted, _complete, _time
from .soperator_worker_docker import DOCKER_STORAGE_MOUNT, DOCKER_SUPERVISOR_CONFIG

_KEY = "dockerDrainRecovery"
_FLAGS = {"IDLE", "CLOUD", "DRAIN", "MAINTENANCE", "RESERVED"}


def _handled_failure(
    runner: SoperatorChecksExecution, failure: Mapping[str, Any]
) -> dict[str, str]:
    name, uid = failure["job"]["name"], failure["job"]["uid"]
    live = runner._get("job", name)
    meta = live.get("metadata", {})
    row = _accounted(runner, failure["slurm"]["JobId"])
    handled = meta.get("annotations", {}).get("soperator-checks-final-state-time", "")
    ended = int(_time(row["EndTime"]).replace(tzinfo=UTC).timestamp())
    if (
        meta.get("uid") != uid
        or meta.get("deletionTimestamp")
        or not _complete(live)
        or job_execution_digest(live) != failure["job"]["entry"]["execution"]
        or row != failure["slurm"]
        or not re.fullmatch(r"[1-9][0-9]*", handled)
        or int(handled) != ended
    ):
        raise RuntimeError("Docker drain recovery lost the handled native failure")
    return row


def verified_docker_drains(
    runner: SoperatorChecksExecution, repair: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind the old native reaction, including its durable handled timestamp."""
    result = {}
    nodes = slurm_nodes(runner)
    failures = repair["dockerFailure"]["failures"]
    for failure in failures:
        row = _handled_failure(runner, failure)
        worker = _identifier(failure["worker"]["name"])
        node = nodes.get(worker, {})
        reason = node.get("Reason", "")
        prefix = f"[node_problem] {DOCKER_CHECK}: job {row['JobId']} [slurm_job]"
        match = re.fullmatch(re.escape(prefix) + r" \[root@([^\]]+)\]", reason)
        if (
            not match
            or _time(match[1]) < _time(row["EndTime"])
            or set(node.get("State", "").split("+")) != _FLAGS
            or node.get("CPUAlloc") != "0"
            or node.get("AllocMem") != "0"
            or worker in result
        ):
            raise RuntimeError("Docker drain recovery cannot attribute the exact idle node drain")
        result[worker] = {"reason": reason, "jobId": row["JobId"], "jobUid": failure["job"]["uid"]}
    if set(result) != set(repair["dockerFailure"]["reservation"]["nodes"]):
        raise RuntimeError("Docker drain recovery changed reserved worker scope")
    return result


def capture_pending_probe(
    runner: SoperatorChecksExecution, reservation: Mapping[str, Any]
) -> dict[str, Any] | None:
    unfinished = [
        (n, e) for n, e in runner.state.get("jobs", {}).items() if e["status"] != "complete"
    ]
    active = [r for r in slurm_jobs(runner) if r.get("JobState") in _LIVE]
    if not unfinished:
        if active:
            raise RuntimeError("Docker drain recovery has foreign active Slurm work")
        return None
    if len(unfinished) != 1 or unfinished[0][1]["check"] != PROBE:
        raise RuntimeError("Docker drain recovery requires the exact blocked bootstrap probe")
    name, entry = unfinished[0]
    job = runner._get("job", name)
    meta = job.get("metadata", {})
    if (
        not entry.get("submitted")
        or not entry.get("uid")
        or meta.get("uid") != entry["uid"]
        or meta.get("deletionTimestamp")
        or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
        or job_execution_digest(job) != entry["execution"]
        or job.get("spec", {}).get("suspend") is True
        or job.get("status", {}).get("active") != 1
    ):
        raise RuntimeError("Docker drain recovery lost the original native probe")
    runner._verify_execution_authority(PROBE, entry["epoch"], generation=True)
    pods = [
        p
        for p in runner._get("pods").get("items", [])
        if any(
            o.get("uid") == entry["uid"] and o.get("controller") is True
            for o in p.get("metadata", {}).get("ownerReferences", [])
        )
    ]
    if len(pods) != 1 or pods[0].get("status", {}).get("phase") != "Running":
        raise RuntimeError("Docker drain recovery has no unique probe submitter")
    pod = pods[0]
    started = [
        c["state"]["running"]["startedAt"]
        for c in pod["status"].get("containerStatuses", [])
        if c.get("name") == PROBE and c.get("state", {}).get("running")
    ]
    if len(active) != 1 or len(started) != 1 or not pod["metadata"].get("uid"):
        raise RuntimeError("Docker drain recovery has ambiguous pending submissions")
    row = active[0]
    if (
        row.get("JobState") != "PENDING"
        or row.get("JobName") != "test-controller-is-ready"
        or row.get("UserId") != f"soperatorchecks({runner.state['principalUid']})"
        or row.get("Reservation") != reservation["name"]
        or row.get("AllocNode:Sid") != f"{pod['status'].get('podIP')}:1"
        or row.get("SubmitLine") != _SUBMIT
        or not re.fullmatch(r"[1-9][0-9]*", row.get("JobId", ""))
        or _time(row["SubmitTime"]) < _time(started[0])
    ):
        raise RuntimeError("Docker drain recovery cannot attribute the pending Slurm probe")
    return {
        "job": {
            "name": name,
            "uid": entry["uid"],
            "execution": entry["execution"],
            "operationLabel": runner.operation_id,
        },
        "pod": {"name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"]},
        "slurmJobs": {row["JobId"]: {k: row[k] for k in _JOB_KEYS}},
        "pendingIds": [row["JobId"]],
        "reservation": copy.deepcopy(reservation),
    }


class InstallDockerDrainRecovery:
    def __init__(
        self,
        runner: SoperatorChecksExecution,
        repair: Mapping[str, Any],
        read_worker: Callable[[str, list[str]], str],
    ):
        self.runner, self.repair, self.read_worker = runner, repair, read_worker

    def _state(self) -> dict[str, Any]:
        saved = self.runner.state[_KEY]
        if saved["repair"] != checks_digest(self.repair):
            raise RuntimeError("Docker drain recovery changed repair identity")
        failures = self.repair["dockerFailure"]["failures"]
        if set(saved["drains"]) != {f["worker"]["name"] for f in failures}:
            raise RuntimeError("Docker drain recovery changed worker scope")
        for failure in failures:
            proof = saved["drains"][failure["worker"]["name"]]
            prefix = f"[node_problem] {DOCKER_CHECK}: job {failure['slurm']['JobId']} [slurm_job]"
            match = re.fullmatch(re.escape(prefix) + r" \[root@([^\]]+)\]", proof["reason"])
            if (
                proof["jobId"] != failure["slurm"]["JobId"]
                or proof["jobUid"] != failure["job"]["uid"]
                or not match
                or _time(match[1]) < _time(failure["slurm"]["EndTime"])
            ):
                raise RuntimeError("Docker drain recovery lost its sealed failure attribution")
        if saved["probe"]:
            expected = saved["probe"]["job"]
            entry = self.runner.state.get("jobs", {}).get(expected["name"], {})
            if (
                entry.get("check") != PROBE
                or entry.get("uid") != expected["uid"]
                or entry.get("execution") != expected["execution"]
                or expected["operationLabel"] != self.runner.operation_id
            ):
                raise RuntimeError("Docker drain recovery probe left the current operation")
        return saved

    def _barrier(self) -> Mapping[str, Any]:
        expected = self.repair["dockerFailure"]["reservation"]
        live = self.runner._reservation(expected["name"])
        if {k: v for k, v in live.items() if k != "users"} != {
            k: v for k, v in expected.items() if k != "users"
        } or live["users"] not in (["root"], ["root", "soperatorchecks"]):
            raise RuntimeError("Docker drain recovery lost its original maintenance reservation")
        return live

    def _probe(self) -> InstallRuntimeRecovery:
        return InstallRuntimeRecovery(
            self.runner,
            {"runtimeFailure": self._state()["probe"], "dockerRepair": checks_digest(self.repair)},
            self.read_worker,
            quiescence_key="dockerProbeQuiescence",
        )

    def quiesce(self) -> None:
        runner = self.runner
        if _KEY not in runner.state:
            barrier = self._barrier()
            drains = verified_docker_drains(runner, self.repair)
            probe = capture_pending_probe(runner, barrier)
            runner.state[_KEY] = {
                "repair": checks_digest(self.repair),
                "drains": drains,
                "probe": probe,
                "status": "intent",
                "restored": {},
            }
            runner._save()
        saved = self._state()
        if saved["status"] in {"drains-restored", "resume-intent", "complete"}:
            return
        if saved["probe"]:
            self._probe().quiesce()
            for jid in saved["probe"]["pendingIds"]:
                row = _accounted(runner, jid)
                if (
                    not row["JobState"].startswith("CANCELLED")
                    or row["StartTime"] != "None"
                    or row["NodeList"] != "None assigned"
                    or row["AllocTRES"] != ""
                ):
                    raise RuntimeError(
                        "Docker drain recovery probe did not retire before execution"
                    )
        if any(r.get("JobState") in _LIVE for r in slurm_jobs(runner)):
            raise RuntimeError("Docker drain recovery has active Slurm work")
        barrier = self._barrier()
        if barrier["users"] != ["root"]:
            runner.authority()
            runner.slurm(
                f"scontrol update ReservationName={_identifier(barrier['name'])} Users=root"
            )
        if self._barrier()["users"] != ["root"]:
            raise RuntimeError("Docker drain recovery could not close temporary access")
        saved["status"] = "quiescent"
        runner._save()

    def _verify_runtime(self, worker: str) -> None:
        runner = self.runner
        pod = runner._get("pod", worker)
        meta, spec = pod.get("metadata", {}), pod.get("spec", {})
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if (
            not meta.get("uid")
            or meta.get("deletionTimestamp")
            or not statuses
            or any(c.get("ready") is not True or c.get("restartCount") != 0 for c in statuses)
        ):
            raise RuntimeError("Docker drain recovery worker is not stably ready")
        owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
        if len(owners) != 1 or owners[0].get("kind") != "StatefulSet":
            raise RuntimeError("Docker drain recovery lost worker ownership")
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
            raise RuntimeError("Docker drain recovery lost native NodeSet ownership")
        node = runner._get("nodeset", parents[0]["name"])
        approved = {n["uid"] for n in self.repair["nodesetsRelease"]["nodes"]}
        if (
            node.get("metadata", {}).get("uid") not in approved
            or node["metadata"]["uid"] != parents[0]["uid"]
            or node.get("spec", {}).get("configMapRefSupervisord") != DOCKER_SUPERVISOR_CONFIG
            or DOCKER_STORAGE_MOUNT
            not in node["spec"]["slurmd"]["volumes"].get("jailSubMounts", [])
        ):
            raise RuntimeError("Docker drain recovery lost corrected native runtime bindings")
        health = runner._get("node", spec["nodeName"]).get("status", {}).get("conditions", [])
        if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in health) or any(
            c.get("status") == "True"
            and c.get("type")
            in {"HardwareIssuesSuspected", "DiskPressure", "MemoryPressure", "PIDPressure"}
            for c in health
        ):
            raise RuntimeError(
                "Docker drain recovery cannot clear a current hardware or node fault"
            )
        config = runner._get("configmap", "image-storage")
        expected = next(
            c for c in self.repair["dockerFailure"]["configs"] if c["name"] == "image-storage"
        )
        if (
            config.get("metadata", {}).get("uid") != expected["uid"]
            or checks_digest(config.get("data")) != expected["dataSha256"]
        ):
            raise RuntimeError("Docker drain recovery native daemon configuration changed")
        actual = self.read_worker(worker, ["sha256sum", "/etc/docker/daemon.json"]).split()
        if actual != [
            hashlib.sha256(config["data"]["daemon.json"].encode()).hexdigest(),
            "/etc/docker/daemon.json",
        ]:
            raise RuntimeError("Docker drain recovery mounted daemon configuration differs")
        processes = self.read_worker(
            worker,
            [
                "/bin/sh",
                "-ec",
                "test -S /run/docker.sock; test -S /mnt/jail/run/docker.sock; ps -eo pid,ppid,comm",
            ],
        )
        rows = [line.split() for line in processes.splitlines()]
        if (
            rows.count(["1", "0", "supervisord"]) != 1
            or len([r for r in rows if len(r) == 3 and r[1:] == ["1", "dockerd"]]) != 1
            or runner._get("pod", worker).get("metadata", {}).get("uid") != meta["uid"]
        ):
            raise RuntimeError("Docker drain recovery has no stable native Supervisor-owned daemon")

    def restore_drains(self) -> None:
        runner, saved = self.runner, self._state()
        if saved["status"] in {"drains-restored", "resume-intent", "complete"}:
            return
        if saved["status"] != "quiescent" or self._barrier()["users"] != ["root"]:
            raise RuntimeError("Docker drain recovery is not quiescent under maintenance")
        if any(r.get("JobState") in _LIVE for r in slurm_jobs(runner)):
            raise RuntimeError("Docker drain recovery has active Slurm work")
        for failure in self.repair["dockerFailure"]["failures"]:
            _handled_failure(runner, failure)
        for worker in saved["drains"]:
            self._verify_runtime(worker)
        for worker, proof in saved["drains"].items():
            live = slurm_nodes(runner)[worker]
            flags = set(live["State"].split("+"))
            restored = saved["restored"]
            if "DRAIN" in flags:
                if (
                    flags != _FLAGS
                    or live.get("Reason") != proof["reason"]
                    or live.get("CPUAlloc") != "0"
                    or live.get("AllocMem") != "0"
                ):
                    raise RuntimeError("Docker drain recovery will not clear another drain")
                restored[worker] = "intent"
                runner._save()
                self._barrier()
                runner.authority()
                runner.slurm(f"scontrol update NodeName={_identifier(worker)} State=UNDRAIN")
            elif worker not in restored or flags != _FLAGS - {"DRAIN"}:
                raise RuntimeError("Docker drain changed outside its recorded recovery intent")
            if set(slurm_nodes(runner)[worker]["State"].split("+")) != _FLAGS - {"DRAIN"}:
                raise RuntimeError("Docker drain recovery did not restore the idle worker")
            restored[worker] = "complete"
            runner._save()
        saved["status"] = "drains-restored"
        runner._save()

    def resume_probe(self) -> None:
        runner, saved = self.runner, self._state()
        if saved["status"] == "complete":
            return
        if saved["status"] not in {"drains-restored", "resume-intent"}:
            raise RuntimeError("Docker probe cannot resume before verified drain restoration")
        if self._barrier()["users"] != ["root", "soperatorchecks"]:
            raise RuntimeError("Docker probe acceptance authorization is unavailable")
        if saved["status"] == "drains-restored":
            nodes = slurm_nodes(runner)
            if any(
                set(nodes[w]["State"].split("+")) != _FLAGS - {"DRAIN"}
                or nodes[w].get("CPUAlloc") != "0"
                for w in saved["drains"]
            ):
                raise RuntimeError("Docker probe workers changed before resume")
        if saved["probe"]:
            probe = self._probe()
            job = probe._job()
            if saved["status"] != "resume-intent":
                if job.get("spec", {}).get("suspend") is not True:
                    raise RuntimeError("Docker probe resumed outside its recovery intent")
                saved["status"] = "resume-intent"
                runner._save()
            if job.get("spec", {}).get("suspend") is True:
                runner._patch(
                    "job",
                    saved["probe"]["job"]["name"],
                    "soperator",
                    {"spec": {"suspend": False}},
                    uid=saved["probe"]["job"]["uid"],
                )
            if probe._job().get("spec", {}).get("suspend") is not False:
                raise RuntimeError("Docker probe did not resume")
            runner._until(
                lambda: any(
                    p.get("metadata", {}).get("uid") != saved["probe"]["pod"]["uid"]
                    and p.get("status", {}).get("phase") in {"Running", "Succeeded"}
                    and any(
                        o.get("uid") == saved["probe"]["job"]["uid"] and o.get("controller") is True
                        for o in p.get("metadata", {}).get("ownerReferences", [])
                    )
                    for p in runner._get("pods").get("items", [])
                ),
                "replacement native bootstrap probe Pod",
            )
        saved["status"] = "complete"
        runner._save()
