import copy
import hashlib
from datetime import UTC
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_install_docker_drains as drains
from nebius_cxcli import soperator_install_docker_recovery as recovery
from test_soperator_install_docker_recovery import docker_failure as docker_failure
from test_soperator_install_runtime_recovery import runtime as runtime
from test_soperator_install_storage_recovery import storage as storage


@pytest.mark.parametrize("mutation", [None, "uid", "handled", "reason", "busy", "accounting"])
def test_docker_drain_requires_handled_exact_software_failure(
    docker_failure, monkeypatch, mutation
):
    s, source, row, configs, node, output = docker_failure
    proof = recovery.capture_docker_failure(s.r, source)
    s.job["metadata"]["annotations"]["soperator-checks-final-state-time"] = str(
        int(drains._time(row["EndTime"]).replace(tzinfo=UTC).timestamp())
    )
    live = {
        "State": "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED",
        "CPUAlloc": "0",
        "AllocMem": "0",
        "Reason": "[node_problem] all-reduce-perf-nccl-in-docker: job 11 [slurm_job] [root@2026-01-01T00:00:15]",
    }
    monkeypatch.setattr(drains, "slurm_nodes", lambda _: {"worker-0": live})
    monkeypatch.setattr(drains, "_accounted", lambda *a: copy.deepcopy(row))
    if mutation == "uid":
        s.job["metadata"]["uid"] = "other"
    if mutation == "handled":
        s.job["metadata"]["annotations"]["soperator-checks-final-state-time"] = "1"
    if mutation == "reason":
        live["Reason"] = live["Reason"].replace("job 11", "job 12")
    if mutation == "busy":
        live["CPUAlloc"] = "1"
    if mutation == "accounting":
        row["ExitCode"] = "2:0"
    if mutation:
        with pytest.raises(RuntimeError):
            drains.verified_docker_drains(s.r, {"dockerFailure": proof})
    else:
        result = drains.verified_docker_drains(s.r, {"dockerFailure": proof})
        assert result["worker-0"]["jobId"] == "11"
    assert s.writes == [] and s.persisted == []


@pytest.fixture
def pending_probe(runtime):
    runtime.runner.state["jobs"]["probe"]["epoch"] = {"uid": "native"}
    runtime.runner._verify_execution_authority = lambda *a, **k: None
    return runtime


@pytest.mark.parametrize("mutation", [None, "uid", "foreign", "running", "command", "user", "pod"])
def test_pending_probe_has_exact_native_submitter(pending_probe, mutation):
    s = pending_probe
    barrier = s.runner._reservation("cxcli_test")
    if mutation == "uid":
        s.job["metadata"]["uid"] = "other"
    if mutation == "foreign":
        s.jobs["3"] = {**s.jobs["2"], "JobId": "3"}
    if mutation == "running":
        s.jobs["2"]["JobState"] = "RUNNING"
    if mutation == "command":
        s.jobs["2"]["SubmitLine"] = "srun other"
    if mutation == "user":
        s.jobs["2"]["UserId"] = "other(1001)"
    if mutation == "pod":
        s.pod["status"]["podIP"] = "192.0.2.2"
    if mutation:
        with pytest.raises(RuntimeError):
            drains.capture_pending_probe(s.runner, barrier)
    else:
        proof = drains.capture_pending_probe(s.runner, barrier)
        assert proof["pendingIds"] == ["2"] and proof["pod"]["uid"] == "pod-uid"
    assert s.writes == []


@pytest.mark.parametrize("mutation", [None, "foreign_drain", "unowned_clear", "runtime"])
def test_docker_recovery_orders_quiescence_runtime_proof_and_native_probe_resume(
    pending_probe, monkeypatch, mutation
):
    s = pending_probe
    barrier = s.runner._reservation("cxcli_test")
    old_reason = "[node_problem] all-reduce-perf-nccl-in-docker: job 11 [slurm_job] [root@2026-01-01T00:00:15]"
    s.node["Reason"] = old_reason
    repair = {
        "dockerFailure": {
            "reservation": barrier,
            "failures": [
                {
                    "worker": {"name": "worker-0"},
                    "job": {"uid": "old-native"},
                    "slurm": {"JobId": "11", "EndTime": "2026-01-01T00:00:15"},
                }
            ],
        }
    }
    monkeypatch.setattr(
        drains,
        "verified_docker_drains",
        lambda *a: {"worker-0": {"reason": old_reason, "jobId": "11", "jobUid": "old-native"}},
    )
    monkeypatch.setattr(
        drains,
        "_accounted",
        lambda *a: {
            "JobState": "CANCELLED by 0",
            "StartTime": "None",
            "NodeList": "None assigned",
            "AllocTRES": "",
        },
    )
    obj = drains.InstallDockerDrainRecovery(s.runner, repair, s.read_worker)
    monkeypatch.setattr(drains, "_handled_failure", lambda runner, failure: failure["slurm"])

    def verified(worker):
        assert "scontrol update NodeName=worker-0 State=UNDRAIN" not in s.writes
        if mutation == "runtime":
            raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(obj, "_verify_runtime", verified)
    old_patch = s.runner._patch

    def patch(kind, name, namespace, document, **kwargs):
        if document["spec"]["suspend"]:
            old_patch(kind, name, namespace, document, **kwargs)
        else:
            s.writes.append("resume-native-probe")
            s.job["spec"]["suspend"] = False
            s.pod["metadata"]["uid"] = "fresh-probe-pod"
            s.pod["status"]["phase"] = "Running"

    s.runner._patch = patch
    obj.quiesce()
    assert s.writes[:3] == [
        "suspend",
        "scancel 2",
        "scontrol update ReservationName=cxcli_test Users=root",
    ]
    if mutation == "foreign_drain":
        s.node["Reason"] = "another fault"
    if mutation == "unowned_clear":
        s.node["State"] = s.node["State"].replace("+DRAIN", "")
    if mutation:
        with pytest.raises(RuntimeError):
            obj.restore_drains()
        assert "scontrol update NodeName=worker-0 State=UNDRAIN" not in s.writes
        return
    obj.restore_drains()
    assert s.writes[-1] == "scontrol update NodeName=worker-0 State=UNDRAIN"
    with pytest.raises(RuntimeError, match="authorization"):
        obj.resume_probe()
    original_reservation = s.runner._reservation
    s.runner._reservation = lambda name: {
        **original_reservation(name),
        "users": ["root", "soperatorchecks"],
    }
    obj.resume_probe()
    assert s.writes[-1] == "resume-native-probe"
    saved = list(s.writes)
    obj.quiesce()
    obj.restore_drains()
    obj.resume_probe()
    assert s.writes == saved


@pytest.mark.parametrize(
    "mutation",
    [None, "ownership", "supervisor", "config", "mounted", "hardware", "daemon", "replaced"],
)
def test_drain_restoration_requires_the_repaired_native_runtime(mutation):
    pod = {
        "metadata": {
            "uid": "pod-new",
            "ownerReferences": [
                {"controller": True, "kind": "StatefulSet", "name": "worker", "uid": "sts"}
            ],
        },
        "spec": {"nodeName": "compute-node"},
        "status": {"containerStatuses": [{"ready": True, "restartCount": 0}]},
    }
    sts = {
        "metadata": {
            "uid": "sts",
            "ownerReferences": [
                {"controller": True, "kind": "NodeSet", "name": "worker", "uid": "nodeset"}
            ],
        }
    }
    ns = {
        "metadata": {"uid": "nodeset"},
        "spec": {
            "configMapRefSupervisord": drains.DOCKER_SUPERVISOR_CONFIG,
            "slurmd": {"volumes": {"jailSubMounts": [drains.DOCKER_STORAGE_MOUNT]}},
        },
    }
    health = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    config = {"metadata": {"uid": "config"}, "data": {"daemon.json": "{}\n"}}
    repair = {
        "nodesetsRelease": {"nodes": [{"uid": "nodeset"}]},
        "dockerFailure": {
            "configs": [
                {
                    "name": "image-storage",
                    "uid": "config",
                    "dataSha256": drains.checks_digest(config["data"]),
                }
            ]
        },
    }
    if mutation == "ownership":
        sts["metadata"]["uid"] = "foreign"
    if mutation == "supervisor":
        ns["spec"]["configMapRefSupervisord"] = "foreign"
    if mutation == "config":
        config["metadata"]["uid"] = "foreign"
    if mutation == "hardware":
        health["status"]["conditions"].append({"type": "HardwareIssuesSuspected", "status": "True"})
    objects = {
        "pod": pod,
        "node": health,
        "statefulsets.apps.kruise.io": sts,
        "nodeset": ns,
        "configmap": config,
    }
    runner = SimpleNamespace(_get=lambda kind, name: objects[kind])

    def read_worker(worker, args):
        assert worker == "worker-0"
        if args[0] == "sha256sum":
            value = hashlib.sha256(config["data"]["daemon.json"].encode()).hexdigest()
            return ("bad" if mutation == "mounted" else value) + "  /etc/docker/daemon.json"
        if mutation == "replaced":
            pod["metadata"] = {"uid": "replaced"}
        return "1 0 supervisord\n7 " + ("2" if mutation == "daemon" else "1") + " dockerd\n"

    obj = drains.InstallDockerDrainRecovery(runner, repair, read_worker)
    if mutation:
        with pytest.raises(RuntimeError):
            obj._verify_runtime("worker-0")
    else:
        obj._verify_runtime("worker-0")
