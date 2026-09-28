"""Preserve native Docker failures and prove worker-local storage before replay."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_docker_drains import _handled_failure
from .soperator_install_docker_recovery import DOCKER_CHECK
from .soperator_install_runtime_recovery import slurm_nodes
from .soperator_install_storage_recovery import _accounted, _complete, _time
from .soperator_install_topology_recovery import (
    InstallTopologyRecovery,
    _idle,
    reservation_record,
)
from .soperator_worker_docker import DOCKER_STORAGE_MOUNT, DOCKER_SUPERVISOR_CONFIG

_STORAGE = """import json, os, pathlib, subprocess
root = '/mnt/jail/mnt/image-storage'
mount = json.loads(subprocess.check_output(
    ['findmnt', '-J', '-T', root, '-o', 'TARGET,SOURCE,FSTYPE'], text=True))['filesystems']
database = os.stat(root + '/docker/containerd/daemon/io.containerd.metadata.v1.bolt/meta.db')
ping = subprocess.run(['curl', '-sS', '--max-time', '3', '--unix-socket',
    '/run/docker.sock', 'http://localhost/_ping'], capture_output=True, text=True)
print(json.dumps({'mount': mount, 'inode': database.st_ino,
    'boot': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'dataRoot': json.loads(pathlib.Path('/etc/docker/daemon.json').read_text())['data-root'],
    'ready': ping.returncode == 0 and ping.stdout.strip() == 'OK'}))
"""


def observe_docker_storage(
    read_worker: Callable[[str, list[str]], str], worker: str
) -> dict[str, Any]:
    value = json.loads(read_worker(_identifier(worker), ["python3", "-c", _STORAGE]))
    if (
        not isinstance(value, dict)
        or set(value) != {"mount", "inode", "boot", "dataRoot", "ready"}
        or value["dataRoot"] != "/mnt/jail/mnt/image-storage/docker"
        or not isinstance(value["mount"], list)
        or len(value["mount"]) != 1
        or type(value["inode"]) is not int
        or value["inode"] <= 0
        or not re.fullmatch(r"[0-9a-f-]{36}", str(value["boot"]))
        or type(value["ready"]) is not bool
    ):
        raise RuntimeError("Docker storage observation is incomplete")
    return value


def capture_docker_storage_failure(
    runner: SoperatorChecksExecution, read_worker: Callable[[str, list[str]], str]
) -> dict[str, Any]:
    state = runner.state
    workers = state.get("acceptance", {}).get("gpuWorkers", [])
    selected = [(n, e) for n, e in state.get("jobs", {}).items() if e.get("check") == DOCKER_CHECK]
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationIntent")
        or len(workers) < 2
        or sorted(workers) != sorted(state["acceptance"]["workers"])
        or len(selected) != len(workers)
        or {e.get("worker") for _, e in selected} != set(workers)
        or any(
            e.get("status") != "complete" and e.get("check") != DOCKER_CHECK
            for e in state["jobs"].values()
        )
    ):
        raise RuntimeError("Docker storage repair requires the exact initial native worker checks")
    _idle(runner)
    reservation = runner._reservation(state["reservation"])
    if reservation["users"] != ["root", "soperatorchecks"] or runner.slurm(
        "id -u soperatorchecks"
    ).strip() != state.get("principalUid"):
        raise RuntimeError("Docker storage repair lost temporary check authorization")
    records = {"standard": reservation_record(runner, reservation["name"], standard=True)}
    jobs, nodes = runner._jobs(), slurm_nodes(runner)
    failures = []
    for name, entry in sorted(selected):
        job = jobs.get(name, {})
        meta = job.get("metadata", {})
        runner._verify_execution_authority(DOCKER_CHECK, entry["epoch"], generation=True)
        if (
            not entry.get("submitted")
            or not entry.get("uid")
            or meta.get("uid") != entry["uid"]
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
            or not _complete(job)
            or job_execution_digest(job) != entry["execution"]
        ):
            raise RuntimeError("Docker storage repair lost its exact native submitter")
        jid = str(meta.get("annotations", {}).get("slurm-job-id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", jid) or entry.get("slurmIds") not in (None, [jid]):
            raise RuntimeError("Docker storage repair has ambiguous native accounting")
        worker = _identifier(entry["worker"])
        row = _accounted(runner, jid)
        allocation = state["acceptance"]["resources"][worker]
        try:
            cpus = int(allocation["cores"]) * int(nodes[worker]["ThreadsPerCore"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Docker storage repair has incomplete CPU topology") from exc
        expected = {
            "JobName": name,
            "User": "soperatorchecks",
            "Reservation": reservation["name"],
            "Partition": "hidden",
            "NodeList": worker,
            "NumNodes": "1",
            "NumCPUs": str(cpus),
        }
        tres = dict(p.split("=", 1) for p in row["AllocTRES"].split(",") if "=" in p)
        if (
            any(row.get(k) != v for k, v in expected.items())
            or cpus <= 0
            or nodes[worker].get("CPUTot") != str(cpus)
            or nodes[worker].get("CPUEfctv") != str(cpus)
            or tres.get("cpu") != str(cpus)
            or tres.get("gres/gpu") != str(allocation["gpus"])
            or (row["JobState"], row["ExitCode"])
            not in {
                ("FAILED", "1:0"),
                ("COMPLETED", "0:0"),
                ("TIMEOUT", "0:0"),
                ("TIMEOUT", "0:15"),
                ("TIMEOUT", "0:9"),
            }
            or _time(row["EndTime"]) <= _time(row["StartTime"])
        ):
            raise RuntimeError("Docker storage repair native allocation or outcome changed")
        pod = runner._get("pod", worker)
        pm, ps = pod.get("metadata", {}), pod.get("spec", {})
        owners = [o for o in pm.get("ownerReferences", []) if o.get("controller") is True]
        containers = pod.get("status", {}).get("containerStatuses", [])
        if (
            not pm.get("uid")
            or pm.get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
            or not ps.get("nodeName")
            or len(owners) != 1
            or owners[0].get("kind") != "StatefulSet"
            or not containers
            or any(
                c.get("ready") is not True or c.get("restartCount") != 0 or not c.get("containerID")
                for c in containers
            )
            or _time(pm["creationTimestamp"].removesuffix("Z")) > _time(row["StartTime"])
        ):
            raise RuntimeError("Docker storage repair worker identity changed")
        sts = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
        parents = [
            o
            for o in sts.get("metadata", {}).get("ownerReferences", [])
            if o.get("controller") is True
        ]
        if (
            sts.get("metadata", {}).get("uid") != owners[0]["uid"]
            or len(parents) != 1
            or (parents[0].get("kind") != "NodeSet")
        ):
            raise RuntimeError("Docker storage repair lost worker ownership")
        node = runner._get("nodeset", parents[0]["name"])
        volumes = node.get("spec", {}).get("slurmd", {}).get("volumes", {})
        jail = [
            v.get("persistentVolumeClaim") for v in ps.get("volumes", []) if v.get("name") == "jail"
        ]
        if (
            node.get("metadata", {}).get("uid") != parents[0]["uid"]
            or node.get("spec", {}).get("configMapRefSupervisord") != DOCKER_SUPERVISOR_CONFIG
            or len(jail) != 1
            or not jail[0]
            or any(v.get("name") == DOCKER_STORAGE_MOUNT["name"] for v in ps.get("volumes", []))
            or any(
                m.get("name") == DOCKER_STORAGE_MOUNT["name"]
                or m.get("mountPath") == DOCKER_STORAGE_MOUNT["mountPath"]
                for m in volumes.get("jailSubMounts", [])
            )
        ):
            raise RuntimeError("Docker storage repair lost the omitted private mount")
        observation = observe_docker_storage(read_worker, worker)
        mount = observation["mount"][0]
        if mount.get("target") != "/mnt/jail" or mount.get("fstype") != "virtiofs":
            raise RuntimeError("Docker metadata is not on the shared jail rootfs")
        output = runner.slurm(
            f"head -c 16385 -- /opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{jid}.out"
        )
        drain = nodes.get(worker, {})
        flags = set(drain.get("State", "").split("+"))
        if drain.get("CPUAlloc") != "0" or drain.get("AllocMem") != "0":
            raise RuntimeError("Docker storage repair worker is still allocated")
        reason = drain.get("Reason", "")
        if row["JobState"] == "FAILED":
            if (
                len(output.encode()) > 16384
                or observation["ready"]
                or "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
                not in output
                or flags != {"IDLE", "CLOUD", "DRAIN", "MAINTENANCE", "RESERVED"}
                or not reason.startswith(f"[node_problem] {DOCKER_CHECK}: job {jid} [slurm_job]")
            ):
                raise RuntimeError(
                    "Docker storage repair cannot attribute the native failure and drain"
                )
        elif flags != {"IDLE", "CLOUD", "MAINTENANCE", "RESERVED"} or not observation["ready"]:
            raise RuntimeError("Docker storage repair found an unrelated worker condition")
        failures.append(
            {
                "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
                "slurm": row,
                "worker": {
                    "name": worker,
                    "uid": pm["uid"],
                    "nodeset": parents[0],
                    "node": ps["nodeName"],
                    "statefulSet": owners[0],
                    "containers": [c["containerID"] for c in containers],
                },
                "jail": jail[0],
                "storage": observation,
                "drain": reason if "DRAIN" in flags else None,
                "outputPrefixSha256": checks_digest(output),
            }
        )
    if (
        not any(f["slurm"]["JobState"] == "FAILED" for f in failures)
        or len(
            {
                checks_digest(
                    {
                        "mount": f["storage"]["mount"],
                        "inode": f["storage"]["inode"],
                        "jail": f["jail"],
                    }
                )
                for f in failures
            }
        )
        != 1
    ):
        raise RuntimeError("Docker storage repair requires one proven shared metadata directory")
    for failure in failures:
        _handled_failure(runner, failure)
    return {"failures": failures, "reservation": reservation, "reservationRecords": records}


def _ready_private_worker(
    runner: SoperatorChecksExecution,
    read_worker: Callable[[str, list[str]], str],
    failure: Mapping[str, Any],
) -> dict[str, Any]:
    worker = failure["worker"]["name"]
    pod = runner._get("pod", worker)
    meta, spec = pod.get("metadata", {}), pod.get("spec", {})
    mounts = [v for v in spec.get("volumes", []) if v.get("name") == DOCKER_STORAGE_MOUNT["name"]]
    owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
    kube_node = runner._get("node", str(spec.get("nodeName") or ""))
    conditions = kube_node.get("status", {}).get("conditions", [])
    if (
        not meta.get("uid")
        or not kube_node.get("metadata", {}).get("uid")
        or pod.get("status", {}).get("phase") != "Running"
        or not pod.get("status", {}).get("containerStatuses")
        or any(
            c.get("ready") is not True or not c.get("containerID")
            for c in pod["status"]["containerStatuses"]
        )
        or meta["uid"] == failure["worker"]["uid"]
        or meta.get("deletionTimestamp")
        or mounts != [{"name": DOCKER_STORAGE_MOUNT["name"], "emptyDir": {}}]
        or owners != [failure["worker"]["statefulSet"]]
        or not any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
        or any(
            c.get("status") == "True"
            and c.get("type")
            in {"DiskPressure", "MemoryPressure", "PIDPressure", "HardwareIssuesSuspected"}
            for c in conditions
        )
        or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in pod.get("status", {}).get("conditions", [])
        )
    ):
        raise RuntimeError("Docker storage recovery needs newly reconciled private worker storage")
    observation = observe_docker_storage(read_worker, worker)
    if observation["mount"][0].get("target") != "/mnt/jail/mnt/image-storage" or (
        observation["mount"][0].get("fstype") == "virtiofs" or not observation["ready"]
    ):
        raise RuntimeError("Docker storage recovery has no ready daemon on private storage")
    return {
        "podUid": meta["uid"],
        "node": spec["nodeName"],
        "nodeUid": kube_node["metadata"]["uid"],
        "owners": copy.deepcopy(owners),
        "containers": sorted(c["containerID"] for c in pod["status"]["containerStatuses"]),
        "storage": observation,
    }


class InstallDockerStorageRecovery(InstallTopologyRecovery):
    def __init__(
        self,
        runner: SoperatorChecksExecution,
        repair: Mapping[str, Any],
        read_worker: Callable[[str, list[str]], str],
    ) -> None:
        super().__init__(
            runner,
            repair,
            failure_key="dockerStorageFailure",
            quiescence_key="dockerStorageQuiescence",
        )
        self.read_worker = read_worker

    def restore_drains(self) -> None:
        runner = self.runner
        complete = {"repair": self.identity, "status": "complete"}
        if runner.state.get("dockerStorageRestore") == complete:
            return
        _idle(runner)
        reservation = runner._reservation(self.proof["reservation"]["name"])
        if (
            reservation["users"] != ["root"]
            or reservation["fingerprint"] != self.proof["reservation"]["fingerprint"]
        ):
            raise RuntimeError("Docker storage recovery lost closed maintenance")
        nodes = slurm_nodes(runner)
        verified = []
        terminal_accounting = {}
        for failure in self.proof["failures"]:
            terminal_accounting[failure["slurm"]["JobId"]] = _handled_failure(runner, failure)
            worker = failure["worker"]["name"]
            runtime = _ready_private_worker(runner, self.read_worker, failure)
            node = nodes[worker]
            flags = set(node.get("State", "").split("+"))
            expected = {"IDLE", "CLOUD", "MAINTENANCE", "RESERVED"}
            if (
                node.get("CPUAlloc") != "0"
                or node.get("AllocMem") != "0"
                or flags - {"DRAIN"} != expected
            ):
                raise RuntimeError("Docker storage recovery found an unrelated worker condition")
            if "DRAIN" in flags and (
                not failure["drain"] or node.get("Reason") != failure["drain"]
            ):
                raise RuntimeError("Docker storage recovery cannot attribute this drain")
            verified.append((worker, node, "DRAIN" in flags, failure, runtime))
        runner.state["dockerStorageRestore"] = {"repair": self.identity, "status": "intent"}
        runner._save()
        for worker, before, drained, failure, runtime in verified:
            if not drained:
                continue
            observed = slurm_nodes(runner).get(worker, {})
            if any(
                observed.get(k) != before.get(k)
                for k in (
                    "NodeName",
                    "State",
                    "Reason",
                    "CPUAlloc",
                    "AllocMem",
                    "NodeAddr",
                    "NodeHostName",
                    "BootTime",
                    "SlurmdStartTime",
                )
            ):
                raise RuntimeError(
                    "Docker storage recovery worker changed before drain restoration"
                )
            _idle(runner)
            _handled_failure(runner, failure)
            if runner._reservation(reservation["name"]) != reservation or (
                _ready_private_worker(runner, self.read_worker, failure) != runtime
            ):
                raise RuntimeError(
                    "Docker storage recovery identity changed before drain restoration"
                )
            runner.authority()
            runner.slurm(f"scontrol update NodeName={_identifier(worker)} State=UNDRAIN")
            if "DRAIN" in slurm_nodes(runner)[worker].get("State", "").split("+"):
                raise RuntimeError("Docker storage recovery drain did not clear")
        runner.state["dockerStorageVerified"] = {
            "repair": self.identity,
            "workers": [w for w, *_ in verified],
            "terminalAccounting": terminal_accounting,
        }
        runner.state["dockerStorageRestore"] = complete
        runner._save()
