import copy

import pytest

from nebius_cxcli import soperator_install_cpu_mask_recovery as recovery
from nebius_cxcli.soperator_worker_topology import h200_static
from test_soperator_install_topology_recovery import native as native


@pytest.fixture
def memory(native, monkeypatch):
    n = native
    n.current.update(CoreCnt="64", TRES="cpu=128")
    n.nodes["worker-0"].update(Sockets="2", ThreadsPerCore="2", CPUTot="128")
    n.nodeset["spec"]["nodeConfig"]["static"] = h200_static(32000)
    n.entry.update(check="mem-perf", status="submit-intent")
    n.r.state.update(
        installReservationHandoff={"operation": "prior"},
        topologyReservationTransition={"status": "complete"},
    )
    n.report["tests"] = [
        {
            "name": name,
            "enable": True,
            "cmd": command,
            "state": {"code": code, "error": "", "stdout": name + ".abc-def.out"},
            "checks": [
                {"name": "mem_perf", "enable": True, "state": {"status": status, "error": ""}}
            ],
        }
        for name, command, code, status in (
            ("mem_bw", "mlc --bandwidth_matrix ", 1, "EMPTY"),
            ("mem_lat", "mlc --latency_matrix ", 0, "PASS"),
        )
    ]
    n.stderr[0] = "Error: unable to bind thread to core 127 with hwid 127"
    prior_slurm, prior_worker = n.r.slurm, n.read_worker
    n.r.slurm = lambda cmd: n.stderr[0] if cmd.startswith("head -c 32769") else prior_slurm(cmd)
    n.parent_status = ["Cpus_allowed_list:\t0-127\nMems_allowed_list:\t0-1\n"]
    n.read_worker = lambda worker, args: (
        n.parent_status[0] if args == ["cat", "/proc/self/status"] else prior_worker(worker, args)
    )
    monkeypatch.setattr(recovery, "slurm_nodes", lambda _: n.nodes)
    monkeypatch.setattr(recovery, "_accounted", lambda *args: copy.deepcopy(n.row))
    return n


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "uid",
        "gpu",
        "active",
        "parent-mask",
        "mask-config",
        "command",
        "latency-failed",
        "latency-error",
        "other-error",
        "extra-test",
    ],
)
def test_admission_requires_exact_native_mlc_cpu_mask_failure(memory, defect):
    n = memory
    if defect == "uid":
        n.job["metadata"]["uid"] = "foreign"
    elif defect == "gpu":
        n.row["AllocTRES"] = "cpu=32,gres/gpu=4"
    elif defect == "active":
        n.active.append({"JobState": "RUNNING"})
    elif defect == "parent-mask":
        n.parent_status[0] = "Cpus_allowed_list:\t0-63\nMems_allowed_list:\t0-1\n"
    elif defect == "mask-config":
        n.nodeset["spec"]["nodeConfig"]["static"] = "foreign"
    elif defect == "command":
        n.report["tests"][0]["cmd"] = "true"
    elif defect == "latency-failed":
        n.report["tests"][1]["checks"][0]["state"]["status"] = "FAIL"
    elif defect == "latency-error":
        n.report["tests"][1]["checks"][0]["state"]["error"] = "incomplete result"
    elif defect == "other-error":
        n.stderr[0] = "bandwidth below threshold"
    elif defect == "extra-test":
        n.report["tests"].append(n.report["tests"][0])
    before = copy.deepcopy(n.r.state)
    if defect:
        with pytest.raises(RuntimeError):
            recovery.capture_cpu_mask_failure(n.r, n.read_worker)
    else:
        proof = recovery.capture_cpu_mask_failure(n.r, n.read_worker)
        assert proof["failures"][0]["parentCpus"] == "0-127"
        assert proof["reservationRecords"]["raw"] == n.current
    assert not n.writes and not n.saves and n.r.state == before


def prepared(n):
    proof = recovery.capture_cpu_mask_failure(n.r, n.read_worker)
    obj = recovery.InstallCpuMaskRecovery(n.r, {"cpuMaskFailure": proof})
    obj.close()
    assert "cpuMaskRepairQuiescence" in n.r.state
    n.nodes["worker-0"]["CPUEfctv"] = "128"
    n.writes.clear()
    n.saves.clear()
    return obj


def test_full_cpu_access_keeps_the_entire_original_reservation(memory):
    n = memory
    obj = prepared(n)
    original = copy.deepcopy(n.current)
    obj.maintenance(True)
    n.users[0] = "root,soperatorchecks"
    n.active.append({"JobState": "RUNNING"})
    obj.maintenance(False)
    assert n.current == original
    assert not n.writes and not n.saves


@pytest.mark.parametrize(
    "field", ["StartTime", "EndTime", "Duration", "Flags", "Nodes", "CoreCnt", "TRES"]
)
def test_no_reservation_drift_is_allowed_for_cpu_mask_repair(memory, field):
    n = memory
    obj = prepared(n)
    n.current[field] = "changed"
    with pytest.raises(RuntimeError, match="original reservation changed"):
        obj.maintenance(True)
    assert not n.writes and not n.saves


def test_missing_full_hardware_cpu_access_blocks_checks(memory):
    n = memory
    obj = prepared(n)
    n.nodes["worker-0"]["CPUEfctv"] = "32"
    with pytest.raises(RuntimeError, match="full hardware CPU access"):
        obj.maintenance(True)
    assert not n.writes and not n.saves
