"""Fault-inject the bounded retry without running any live diagnostic."""

import copy
import json
from datetime import UTC
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_checks_image_pull_recovery as recovery
from nebius_cxcli.soperator_checks_contract import job_execution_digest
from nebius_cxcli.soperator_checks_policy import checks_digest
from test_soperator_install_storage_recovery import storage as storage

OUTPUT = """Unable to find image 'example/check:1' locally
1: Pulling from example/check
abc: Pulling fs layer
docker: failed to copy: read tcp 192.0.2.1:1234->192.0.2.2:443: read: connection reset by peer
srun: task 0: Exited with exit code 125
"""


@pytest.fixture
def pull(storage):
    s = storage
    runner = s.r
    name = recovery.CHECK + "-initial-run"
    runner.state.update(freshInstall=True, jobs={name: s.entry})
    s.entry.update(check=recovery.CHECK, gpuCount=8)
    s.job["metadata"].update(name=name)
    s.job["metadata"]["annotations"]["soperator-checks-final-state-time"] = str(
        int(recovery._time("2026-01-01T00:00:15").replace(tzinfo=UTC).timestamp())
    )
    s.entry["execution"] = job_execution_digest(s.job)
    runner.state["acceptance"].update(reservationNodes=["worker-0"], flags=s.reservation["flags"])
    row = {
        "JobId": "11",
        "JobState": "FAILED",
        "ExitCode": "125:0",
        "NodeList": "worker-0",
        "User": "soperatorchecks",
        "JobName": name,
        "Reservation": "reserve",
        "AllocTRES": "cpu=64,gres/gpu=8",
        "StartTime": "2026-01-01T00:00:10",
        "SubmitTime": "2026-01-01T00:00:09",
        "Partition": "hidden",
        "NumCPUs": "64",
        "NumNodes": "1",
        "EndTime": "2026-01-01T00:00:15",
        "SubmitLine": "sbatch native.sh",
    }
    node = {
        "NodeName": "worker-0",
        "State": "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED",
        "CPUAlloc": "0",
        "AllocMem": "0",
        "ThreadsPerCore": "2",
        "CPUTot": "64",
        "CPUEfctv": "64",
        "Reason": f"[node_problem] {recovery.CHECK}: job 11 [slurm_job] [root@2026-01-01T00:00:16]",
    }
    s.pod["status"]["containerStatuses"] = [
        {"name": "slurmd", "ready": True, "restartCount": 0, "containerID": "containerd://worker"}
    ]
    s.node["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
    native = {"metadata": {"uid": "nodeset-uid"}}
    old_get = runner._get
    runner._get = lambda kind, selected="": native if kind == "nodeset" else old_get(kind, selected)
    original_slurm = runner.slurm
    outputs = [OUTPUT]
    active = []

    def slurm(command):
        if "sacct -n -P" in command:
            return "|".join(row.values())
        if command.endswith("scontrol show nodes -o"):
            return " ".join(f"{k}={v}" for k, v in node.items())
        if command.endswith("scontrol show jobs -o"):
            return "\n".join(active)
        if command.startswith("head -c "):
            return outputs[0]
        if command == "scontrol update NodeName=worker-0 State=UNDRAIN":
            assert s.persisted[-1][recovery.KEY]["worker-0"]["status"] == "undrain-intent"
            assert s.reservation["users"] == ["root"]
            s.writes.append(command)
            node["State"] = node["State"].replace("+DRAIN", "")
            return ""
        return original_slurm(command)

    runner.slurm = slurm
    runner._accounting = lambda ids: "|".join(list(row.values())[:8])
    runner._verify_isolation = lambda: None
    runner.require_lifecycle = lambda: SimpleNamespace(
        admission=SimpleNamespace(verify=lambda: None)
    )
    runner.emit = lambda message: None
    storage_observation = {
        "ready": True,
        "inode": 123,
        "boot": "a" * 8 + "-" + "b" * 27,
        "mount": [
            {"target": "/mnt/jail/mnt/image-storage", "source": "/dev/local", "fstype": "ext4"}
        ],
        "dataRoot": "/mnt/jail/mnt/image-storage/docker",
    }

    def read_worker(worker, args):
        return json.dumps(storage_observation)

    return SimpleNamespace(**locals())


def test_retry_preserves_failure_and_peer_and_is_idempotent(pull):
    p = pull
    peer = {"check": recovery.CHECK, "worker": "worker-1", "status": "complete"}
    p.runner.state["jobs"]["peer"] = peer
    before = copy.deepcopy(p.s.entry)
    assert recovery.recover_initial_image_pull(p.runner, p.read_worker)
    proof = p.runner.state[recovery.KEY]["worker-0"]
    assert proof["job"]["entry"] == before
    assert proof["outputSha256"] == checks_digest(OUTPUT)
    assert "192.0.2" not in json.dumps(p.runner.state)
    assert p.runner.state["jobs"] == {"peer": peer}
    assert p.s.job["metadata"]["uid"] == "job-uid"
    assert p.runner.state["creationGuards"][recovery.CHECK]["uid"] == "job-uid"
    assert recovery.replacement_name(p.runner, "worker-0") == proof["replacement"]
    assert not recovery.recover_initial_image_pull(p.runner, p.read_worker)
    assert len(p.s.writes) == 2  # close temporary user, then exact drain restoration


@pytest.mark.parametrize(
    "mutation",
    [
        "job_uid",
        "job_body",
        "cpu",
        "gpu",
        "reason",
        "allocated",
        "handled",
        "policy",
        "pod_restart",
        "owner",
        "nodeset",
        "disk_pressure",
        "storage",
        "authority",
        "active",
    ],
)
def test_guard_drift_never_clears_drain(pull, mutation):
    p = pull
    if mutation == "job_uid":
        p.s.job["metadata"]["uid"] = "foreign"
    if mutation == "job_body":
        p.s.job["spec"]["template"]["spec"]["containers"][0]["args"] = ["other"]
    if mutation == "cpu":
        p.row["NumCPUs"] = "32"
    if mutation == "gpu":
        p.row["AllocTRES"] = "cpu=64,gres/gpu=1"
    if mutation == "reason":
        p.node["Reason"] = "hardware fault"
    if mutation == "allocated":
        p.node["CPUAlloc"] = "1"
    if mutation == "handled":
        p.s.job["metadata"]["annotations"].pop("soperator-checks-final-state-time")
    if mutation == "policy":
        p.s.cron["spec"]["jobTemplate"]["spec"]["suspend"] = True
    if mutation == "pod_restart":
        p.s.pod["status"]["containerStatuses"][0]["restartCount"] = 1
    if mutation == "owner":
        p.s.node_owner["uid"] = "other"
    if mutation == "nodeset":
        p.native["metadata"]["uid"] = "other"
    if mutation == "disk_pressure":
        p.s.node["status"]["conditions"].append({"type": "DiskPressure", "status": "True"})
    if mutation == "storage":
        p.storage_observation["ready"] = False
    if mutation == "authority":
        p.runner.authority = lambda: (_ for _ in ()).throw(RuntimeError("lost lease"))
    if mutation == "active":
        p.active.append("JobId=22 JobState=RUNNING")
    with pytest.raises(RuntimeError):
        recovery.recover_initial_image_pull(p.runner, p.read_worker)
    assert not any("UNDRAIN" in command for command in p.s.writes)


@pytest.mark.parametrize("state", ["pending-k8s", "RUNNING", "PENDING", "COMPLETED"])
def test_inflight_or_successful_submitters_stay_with_normal_acceptance(pull, state):
    p = pull
    if state == "pending-k8s":
        p.s.job["status"] = {}
    else:
        p.row["JobState"] = state
        p.row["ExitCode"] = "0:0"
        if state != "COMPLETED":
            p.row["EndTime"] = "Unknown"
    assert not recovery.recover_initial_image_pull(p.runner, p.read_worker)
    assert p.s.writes == p.s.persisted == []


@pytest.mark.parametrize("point", ["intent", "undrain-intent", "after-undrain"])
def test_resume_after_lost_checkpoint_ack_or_undrain_ack(pull, point):
    p = pull
    save = p.runner._save
    slurm = p.runner.slurm

    def interrupted_save():
        save()
        if p.runner.state.get(recovery.KEY, {}).get("worker-0", {}).get("status") == point:
            raise RuntimeError("lost acknowledgement")

    def interrupted_slurm(command):
        result = slurm(command)
        if point == "after-undrain" and "State=UNDRAIN" in command:
            raise RuntimeError("lost acknowledgement")
        return result

    p.runner._save, p.runner.slurm = interrupted_save, interrupted_slurm
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        recovery.recover_initial_image_pull(p.runner, p.read_worker)
    p.runner._save, p.runner.slurm = save, slurm
    p.runner.state = copy.deepcopy(p.s.persisted[-1])
    assert recovery.recover_initial_image_pull(p.runner, p.read_worker)
    assert sum("UNDRAIN" in command for command in p.s.writes) == 1


def test_failed_replacement_cannot_retry_twice(pull):
    p = pull
    assert recovery.recover_initial_image_pull(p.runner, p.read_worker)
    name = recovery.replacement_name(p.runner, "worker-0")
    p.s.job["metadata"]["name"] = name
    p.row["JobName"] = name
    p.runner.state["jobs"][name] = p.s.entry
    with pytest.raises(RuntimeError, match="exhausted"):
        recovery.recover_initial_image_pull(p.runner, p.read_worker)
    assert len(p.s.writes) == 2


def test_completed_submitter_waits_for_accounting_visibility(pull):
    pull.runner._accounting = lambda ids: ""
    assert not recovery.recover_initial_image_pull(pull.runner, pull.read_worker)
    assert pull.s.writes == pull.s.persisted == []


@pytest.mark.parametrize(
    "output",
    [
        OUTPUT.replace("connection reset by peer", "unauthorized"),
        OUTPUT.replace("connection reset by peer", "no space left on device"),
        OUTPUT + "Collective test running\n",
        OUTPUT + "docker: another failure\n",
        OUTPUT + "x" * 16384,
        OUTPUT.replace("Pulling fs layer", "benchmark"),
    ],
)
def test_only_bounded_pre_benchmark_registry_resets_qualify(output):
    assert not recovery.registry_reset(output)
