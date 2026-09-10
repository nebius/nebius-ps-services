import copy
import hashlib
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_contract import job_execution_digest
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_runtime_recovery import (
    PROBE,
    InstallRuntimeRecovery,
    capture_probe_failure,
    slurm_fields,
)


@pytest.fixture
def runtime():
    stamp = "2026-01-01T00:00:00"
    job = {
        "metadata": {
            "name": "probe",
            "uid": "job-uid",
            "labels": {"cxcli.nebius.ai/check-operation": "old"},
        },
        "spec": {"template": {"spec": {"containers": [{"name": PROBE}]}}},
        "status": {"active": 1},
    }
    pod = {
        "metadata": {
            "name": "probe-pod",
            "uid": "pod-uid",
            "ownerReferences": [{"uid": "job-uid", "controller": True}],
        },
        "status": {
            "phase": "Running",
            "podIP": "192.0.2.1",
            "containerStatuses": [
                {"name": PROBE, "state": {"running": {"startedAt": stamp + "Z"}}}
            ],
        },
    }
    reservation = {
        "name": "cxcli_test",
        "fingerprint": "sha256:barrier",
        "users": ["root", "soperatorchecks"],
        "nodes": ["worker-0"],
        "flags": ["MAINT", "IGNORE_JOBS"],
    }
    jobs = {
        "1": {
            "JobId": "1",
            "JobName": "test-controller-is-ready",
            "UserId": "soperatorchecks(1000)",
            "Reservation": "cxcli_test",
            "AllocNode:Sid": "192.0.2.1:1",
            "SubmitTime": stamp,
            "SubmitLine": "srun --mpi=none --job-name=test-controller-is-ready -n1 -t1 --partition=hidden hostname",
            "JobState": "FAILED",
            "ExitCode": "0:54",
            "NodeList": "worker-0",
            "EndTime": stamp,
        },
    }
    jobs["2"] = {
        **jobs["1"],
        "JobId": "2",
        "JobState": "PENDING",
        "NodeList": "",
        "EndTime": "Unknown",
        "ExitCode": "0:0",
    }
    node = {
        "NodeName": "worker-0",
        "State": "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED",
        "Reason": f"Prolog error [root@{stamp}]",
        "CPUAlloc": "0",
        "AllocMem": "0",
    }
    cm = {
        "metadata": {"uid": "scripts"},
        "data": {name: "#!/bin/sh\nexit 0" for name in ("prolog.sh", "epilog.sh", "hc_program.sh")},
    }
    mounts = [
        {"name": name, "mountPath": path, "readOnly": True}
        for name, path in (
            ("slurm-scripts", "/opt/slurm_scripts"),
            ("slurm-scripts-jail", "/mnt/jail.upper/opt/slurm_scripts"),
        )
    ]
    worker = {
        "metadata": {"uid": "worker-new"},
        "spec": {
            "containers": [{"name": "slurmd", "volumeMounts": mounts}],
            "volumes": [
                {"name": row["name"], "configMap": {"name": "slurm-scripts", "defaultMode": 493}}
                for row in mounts
            ],
        },
        "status": {"containerStatuses": [{"name": "slurmd", "ready": True}]},
    }
    writes = []
    state = {
        "phase": "acceptance",
        "installReservationIntent": True,
        "reservation": "cxcli_test",
        "principalUid": "1000",
        "jobs": {
            "probe": {
                "status": "submit-intent",
                "check": PROBE,
                "submitted": True,
                "uid": "job-uid",
                "execution": job_execution_digest(job),
            }
        },
    }

    def get(kind, name=""):
        return {"job": job, "pods": {"items": [pod]}, "pod": worker, "configmap": cm}[kind]

    def patch(*args, **kwargs):
        writes.append("suspend")
        job["spec"]["suspend"] = True
        job["status"]["active"] = 0
        pod["status"]["phase"] = "Failed"

    def slurm(command):
        if command.endswith("scontrol show jobs -o"):
            return "\n".join(
                " ".join(f"{key}={value}" for key, value in row.items()) for row in jobs.values()
            )
        if command.endswith("scontrol show nodes -o"):
            return " ".join(f"{key}={value}" for key, value in node.items())
        writes.append(command)
        if command == "scancel 2":
            jobs["2"]["JobState"] = "CANCELLED"
        elif command == "scontrol update ReservationName=cxcli_test Users=root":
            reservation["users"] = ["root"]
        elif command == "scontrol update NodeName=worker-0 State=UNDRAIN":
            node["State"] = node["State"].replace("+DRAIN", "")
        else:
            raise AssertionError(command)
        return ""

    def until(predicate, _description):
        assert predicate()

    runner = SimpleNamespace(
        state=state,
        operation_id="old",
        _get=get,
        _jobs=lambda: {"probe": job},
        _reservation=lambda _: copy.deepcopy(reservation),
        slurm=slurm,
        authority=lambda: None,
        _save=lambda: None,
        _patch=patch,
        _until=until,
    )

    def read_worker(_node, args):
        if args[0] == "sha256sum":
            return "\n".join(
                f"{hashlib.sha256(cm['data'][path.rsplit('/', 1)[1]].encode()).hexdigest()}  {path}"
                for path in args[1:]
            )
        return ("755\n" if "-L" in args else "777\n") * 3

    return SimpleNamespace(
        runner=runner,
        job=job,
        pod=pod,
        jobs=jobs,
        node=node,
        cm=cm,
        worker=worker,
        writes=writes,
        read_worker=read_worker,
    )


def recovery(runtime):
    proof = capture_probe_failure(runtime.runner)
    repair = {
        "runtimeFailure": proof,
        "scripts": {"uid": "scripts", "dataSha256": checks_digest(runtime.cm["data"])},
    }
    runtime.runner.state = {"phase": "planned", "jobs": {}}
    return InstallRuntimeRecovery(runtime.runner, repair, runtime.read_worker)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "job-uid",
        "executable",
        "pod-owner",
        "submitter",
        "job-user",
        "command",
        "reservation",
        "drain-reason",
        "drain-time",
        "node-state",
        "another-job",
        "another-check",
    ],
)
def test_admission_proves_exact_native_probe_and_its_drains(runtime, mutation):
    if mutation == "job-uid":
        runtime.job["metadata"]["uid"] = "foreign"
    elif mutation == "executable":
        runtime.job["spec"]["template"]["spec"]["containers"][0]["image"] = "foreign"
    elif mutation == "pod-owner":
        runtime.pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    elif mutation == "submitter":
        runtime.jobs["2"]["AllocNode:Sid"] = "192.0.2.2:1"
    elif mutation == "job-user":
        runtime.jobs["1"]["UserId"] = "customer(1001)"
    elif mutation == "command":
        runtime.jobs["1"]["SubmitLine"] = "srun customer-task"
    elif mutation == "reservation":
        runtime.jobs["1"]["Reservation"] = "foreign"
    elif mutation == "drain-reason":
        runtime.node["Reason"] = "admin maintenance"
    elif mutation == "drain-time":
        runtime.node["Reason"] = "Prolog error [root@2026-01-01T00:00:01]"
    elif mutation == "node-state":
        runtime.node["State"] += "+DOWN"
    elif mutation == "another-job":
        runtime.jobs["3"] = {**runtime.jobs["2"], "JobId": "3", "Reservation": "foreign"}
    elif mutation == "another-check":
        runtime.runner.state["jobs"]["probe"]["check"] = "other"
    if mutation:
        with pytest.raises(RuntimeError):
            capture_probe_failure(runtime.runner)
    else:
        proof = capture_probe_failure(runtime.runner)
        assert proof["pendingIds"] == ["2"]
        assert proof["drains"]["worker-0"]["jobId"] == "1"
    assert runtime.writes == []


def test_product_quiesces_then_verifies_scripts_and_restores_only_owned_drain(runtime):
    workflow = recovery(runtime)
    workflow.quiesce()
    workflow.restore_drains()
    assert runtime.writes == [
        "suspend",
        "scancel 2",
        "scontrol update ReservationName=cxcli_test Users=root",
        "scontrol update NodeName=worker-0 State=UNDRAIN",
    ]
    before = list(runtime.writes)
    workflow.quiesce()
    workflow.restore_drains()
    assert runtime.writes == before


@pytest.mark.parametrize(
    "mutation",
    ["job-uid", "slurm-submit", "scripts", "mount", "mode", "drain", "already-undrained"],
)
def test_recovery_rejects_drift_without_clearing_drains(runtime, mutation):
    workflow = recovery(runtime)
    if mutation == "job-uid":
        runtime.job["metadata"]["uid"] = "foreign"
    elif mutation == "slurm-submit":
        runtime.jobs["2"]["SubmitTime"] = "2026-01-02T00:00:00"
    if mutation in {"job-uid", "slurm-submit"}:
        with pytest.raises(RuntimeError):
            workflow.quiesce()
    else:
        workflow.quiesce()
        if mutation == "scripts":
            runtime.cm["data"]["prolog.sh"] = "foreign"
        elif mutation == "mount":
            runtime.worker["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
        elif mutation == "mode":
            workflow.read_worker = lambda *_: "600\n" * 3
        elif mutation == "drain":
            runtime.node["Reason"] = "new hardware failure"
        elif mutation == "already-undrained":
            runtime.node["State"] = "IDLE+CLOUD+MAINTENANCE+RESERVED"
        with pytest.raises(RuntimeError):
            workflow.restore_drains()
    assert not any("UNDRAIN" in command for command in runtime.writes)


def test_interrupted_undrain_reconciles_saved_intent(runtime):
    workflow = recovery(runtime)
    workflow.quiesce()
    original = runtime.runner.slurm

    def interrupt(command):
        result = original(command)
        if "UNDRAIN" in command:
            raise KeyboardInterrupt
        return result

    runtime.runner.slurm = interrupt
    with pytest.raises(KeyboardInterrupt):
        workflow.restore_drains()
    assert runtime.runner.state["runtimeRepairDrains"]["worker-0"]["status"] == "intent"
    runtime.runner.slurm = original
    workflow.restore_drains()
    assert runtime.runner.state["runtimeRepairDrains"]["worker-0"]["status"] == "complete"
    assert sum("UNDRAIN" in command for command in runtime.writes) == 1


def test_native_field_parser_preserves_reason_and_complete_timestamp():
    assert slurm_fields(
        "NodeName=worker-0 Reason=Prolog error [root@2026-01-01T00:00:01] State=IDLE+DRAIN"
    ) == {
        "NodeName": "worker-0",
        "Reason": "Prolog error [root@2026-01-01T00:00:01]",
        "State": "IDLE+DRAIN",
    }
