import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_install_topology_recovery as recovery
from nebius_cxcli.soperator_checks_contract import job_execution_digest
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_worker_topology import H200_QUOTA_DEFAULT


@pytest.fixture
def native(monkeypatch):
    reservation = {
        "ReservationName": "reserve",
        "StartTime": "2026-01-01T00:00:00",
        "EndTime": "2027-01-01T00:00:00",
        "Duration": "365-00:00:00",
        "State": "ACTIVE",
        "Nodes": "worker-0",
        "NodeCnt": "1",
        "CoreCnt": "32",
        "TRES": "cpu=32",
        "Flags": "MAINT,IGNORE_JOBS,SPEC_NODES,ALL_NODES",
    }
    current = copy.deepcopy(reservation)
    users, writes, saves, active = ["root,soperatorchecks"], [], [], []
    job = {
        "metadata": {
            "name": "native-job",
            "uid": "job-uid",
            "labels": {"cxcli.nebius.ai/check-operation": "old"},
            "annotations": {"slurm-job-id": "42"},
        },
        "spec": {"template": {"spec": {"containers": [{"name": "native"}]}}},
        "status": {"conditions": [{"type": "Complete", "status": "True"}]},
    }
    entry = {
        "check": "ib-gpu-perf",
        "worker": "worker-0",
        "uid": "job-uid",
        "epoch": {},
        "execution": job_execution_digest(job),
        "slurmIds": ["42"],
        "status": "complete",
    }
    row = {
        "JobId": "42",
        "JobState": "COMPLETED",
        "ExitCode": "0:0",
        "JobName": "native-job",
        "User": "soperatorchecks",
        "Reservation": "reserve",
        "Partition": "hidden",
        "NodeList": "worker-0",
        "NumNodes": "1",
        "NumCPUs": "32",
        "AllocTRES": "cpu=32,gres/gpu=8",
    }
    pod = {
        "metadata": {
            "uid": "pod-uid",
            "ownerReferences": [
                {"kind": "StatefulSet", "name": "worker", "uid": "sts-uid", "controller": True}
            ],
        },
        "status": {"phase": "Running"},
    }
    sts = {
        "metadata": {
            "uid": "sts-uid",
            "ownerReferences": [
                {"kind": "NodeSet", "name": "worker", "uid": "nodeset-uid", "controller": True}
            ],
        }
    }
    nodeset = {
        "metadata": {"uid": "nodeset-uid"},
        "spec": {"nodeConfig": {"static": H200_QUOTA_DEFAULT + " Gres=gpu:8"}},
    }
    nodes = {
        "worker-0": {
            "NodeName": "worker-0",
            "Sockets": "1",
            "CoresPerSocket": "32",
            "ThreadsPerCore": "1",
            "CPUTot": "32",
            "CPUEfctv": "32",
        }
    }
    tests = [
        {
            "name": f"{name}_gpu_0_{gpu}",
            "enable": True,
            "cmd": "numactl --cpunodebind=1 --membind=1 test",
            "state": {"code": 1, "stderr": f"{name}_gpu_0_{gpu}.abc-def.out"},
        }
        for name in ("ib_write_bw", "ib_send_lat", "ib_read_lat")
        for gpu in range(4, 8)
    ]
    report = {"status": "ERROR", "tests": tests}
    hardware = [
        "CPUs=128 Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2 Gres=gpu:nvidia_h200:8"
    ]
    stderr = [
        "numa_sched_setaffinity_v2_int() failed: Invalid argument\nsched_setaffinity: Invalid argument\n"
    ]
    r = SimpleNamespace(
        operation_id="old",
        state={
            "phase": "acceptance",
            "installReservationIntent": True,
            "jobs": {"native-job": entry},
            "acceptance": {"gpuWorkers": ["worker-0"], "workers": ["worker-0"]},
            "reservation": "reserve",
            "principalUid": "1000",
        },
    )
    r.authority = lambda: None
    r._save = lambda: saves.append(copy.deepcopy(r.state))
    r._jobs = lambda: {"native-job": job}
    r._get = lambda kind, name: {
        "pod": pod,
        "statefulsets.apps.kruise.io": sts,
        "nodeset": nodeset,
        "job": job,
    }[kind]
    r._verify_execution_authority = lambda *args, **kwargs: None
    r._reservation = lambda name: {
        "name": name,
        "fingerprint": checks_digest(current),
        "users": users[0].split(","),
    }

    def slurm(command):
        if "scontrol show reservation " in command:
            return " ".join(f"{k}={v}" for k, v in {**current, "Users": users[0]}.items())
        if command == "id -u soperatorchecks":
            return "1000"
        if command.startswith("head -c 4194305"):
            return (
                "Health checker output:\n" + json.dumps(report) + "\nHealth checker status: ERROR\n"
            )
        if command.startswith("head -c 16385"):
            return stderr[0]
        if command.startswith("scontrol update ReservationName=reserve "):
            writes.append(command)
            if command.endswith("Users=root"):
                users[0] = "root"
            elif command.endswith("Nodes=ALL"):
                current.update(CoreCnt="64", TRES="cpu=128")
            else:
                pytest.fail(command)
            return ""
        pytest.fail(command)

    def read_worker(worker, args):
        assert worker == "worker-0"
        if args == ["slurmd", "-C"]:
            return hardware[0]
        return "0-63" if "node0" in args[1] else "64-127"

    r.slurm = slurm
    monkeypatch.setattr(recovery, "slurm_jobs", lambda _: active)
    monkeypatch.setattr(recovery, "slurm_nodes", lambda _: nodes)
    monkeypatch.setattr(recovery, "_accounted", lambda *args: copy.deepcopy(row))
    return SimpleNamespace(**locals())


@pytest.mark.parametrize(
    "defect", [None, "uid", "gpu", "active", "hardware", "health", "other_error", "missing_test"]
)
def test_capture_requires_exact_native_numa_cause_without_writes(native, defect):
    n = native
    if defect == "uid":
        n.job["metadata"]["uid"] = "foreign"
    if defect == "gpu":
        n.row["AllocTRES"] = "cpu=32,gres/gpu=4"
    if defect == "active":
        n.active.append({"JobState": "RUNNING"})
    if defect == "hardware":
        n.hardware[0] = n.hardware[0].replace("CPUs=128", "CPUs=64")
    if defect == "health":
        n.report["status"] = "PASS"
    if defect == "other_error":
        n.stderr[0] = "network device unavailable"
    if defect == "missing_test":
        n.report["tests"].pop()
    before = copy.deepcopy(n.r.state)
    if defect:
        with pytest.raises(RuntimeError):
            recovery.capture_topology_failure(n.r, n.read_worker)
    else:
        proof = recovery.capture_topology_failure(n.r, n.read_worker)
        assert len(proof["failures"][0]["errors"]) == 12
    assert not n.writes and not n.saves and n.r.state == before


def prepared(n):
    proof = recovery.capture_topology_failure(n.r, n.read_worker)
    obj = recovery.InstallTopologyRecovery(n.r, {"topologyFailure": proof})
    obj.close()
    n.r.state["reservationFingerprint"] = checks_digest(n.reservation)
    n.nodes["worker-0"].update(Sockets="2", ThreadsPerCore="2", CPUTot="128")
    n.writes.clear()
    n.saves.clear()
    return obj


@pytest.mark.parametrize("already_recomputed", [False, True])
def test_transition_preserves_identity_and_is_recoverable(native, already_recomputed):
    n = native
    obj = prepared(n)
    if already_recomputed:
        n.current.update(CoreCnt="64", TRES="cpu=128")
    obj.maintenance(True)
    assert n.r.state["reservationFingerprint"] == checks_digest(n.current)
    assert n.current == {**n.reservation, "CoreCnt": "64", "TRES": "cpu=128"}
    assert n.writes == (
        [] if already_recomputed else ["scontrol update ReservationName=reserve Nodes=ALL"]
    )
    n.writes.clear()
    obj.maintenance(False)
    obj.maintenance(True)
    n.users[0] = "root,soperatorchecks"
    n.active.append({"JobState": "RUNNING"})
    obj.maintenance(False)
    assert n.writes == [] and len(n.saves) == 1


@pytest.mark.parametrize(
    "field", ["StartTime", "EndTime", "Duration", "Flags", "Nodes", "CoreCnt", "TRES"]
)
def test_transition_rejects_unrelated_drift_without_write(native, field):
    obj = prepared(native)
    native.current[field] = "changed"
    with pytest.raises(RuntimeError):
        obj.maintenance(True)
    assert not native.writes and not native.saves


def test_observer_cannot_adopt_unrecorded_transition(native):
    obj = prepared(native)
    native.current.update(CoreCnt="64", TRES="cpu=128")
    with pytest.raises(RuntimeError, match="durably owned"):
        obj.maintenance(False)
    assert not native.writes and not native.saves


@pytest.mark.parametrize("defect", ["active", "cpu_budget", "users"])
def test_initial_transition_requires_quiescent_root_only_exact_budget(native, defect):
    obj = prepared(native)
    if defect == "active":
        native.active.append({"JobState": "RUNNING"})
    elif defect == "cpu_budget":
        native.nodes["worker-0"]["CPUEfctv"] = "128"
    else:
        native.users[0] = "root,soperatorchecks"
    with pytest.raises(RuntimeError):
        obj.maintenance(True)
    assert not native.saves
    assert not native.writes


def test_submission_racing_authorization_close_cannot_pass(native):
    proof = recovery.capture_topology_failure(native.r, native.read_worker)
    original = native.r.slurm

    def raced(command):
        result = original(command)
        if command.endswith("Users=root"):
            native.active.append({"JobState": "PENDING"})
        return result

    native.r.slurm = raced
    obj = recovery.InstallTopologyRecovery(native.r, {"topologyFailure": proof})
    with pytest.raises(RuntimeError, match="active Slurm work"):
        obj.close()
    assert not native.saves
