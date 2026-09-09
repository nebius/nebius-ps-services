import copy

import pytest

from nebius_cxcli import soperator_install_docker_recovery as recovery
from test_soperator_install_storage_recovery import storage as storage


@pytest.fixture
def docker_failure(storage, monkeypatch, tmp_path):
    s = storage
    r = s.r
    r.state["acceptance"]["gpuWorkers"] = ["worker-0"]
    s.entry["check"] = recovery.DOCKER_CHECK
    s.control["JobState"] = "FAILED"
    s.job["metadata"]["creationTimestamp"] = "2026-01-01T00:00:00Z"
    s.pod["metadata"]["creationTimestamp"] = "2025-12-31T23:59:00Z"
    s.pod["status"]["containerStatuses"] = [{"name": "slurmd", "ready": True, "restartCount": 0}]
    s.pod["spec"]["volumes"].append(
        {"name": "supervisord-config", "configMap": {"name": "default"}}
    )
    source = tmp_path / "helm/soperator-custom-configmaps/config-files"
    source.mkdir(parents=True)
    files = {
        "supervisord.conf": "[program:dockerd]\n",
        "daemon.json": "{}\n",
        "enroot.conf": "native\n",
    }
    for name, content in files.items():
        (source / name).write_text(content)
    configs = {
        "default": {
            "metadata": {"uid": "default-uid"},
            "data": {"supervisord.conf": "[program:slurmd]\n"},
        },
        "custom-supervisord-config": {
            "metadata": {"uid": "native-supervisor"},
            "data": {"supervisord.conf": files["supervisord.conf"]},
        },
        "image-storage": {
            "metadata": {"uid": "native-storage"},
            "data": {k: v for k, v in files.items() if k != "supervisord.conf"},
        },
    }
    node = {"metadata": {"uid": "nodeset-uid"}, "spec": {}}
    original_get = r._get

    def get(kind, name=""):
        if kind == "configmap":
            return configs[name]
        if kind == "nodeset":
            return node
        return original_get(kind, name)

    r._get = get
    row = {
        "JobId": "11",
        "JobState": "FAILED",
        "ExitCode": "1:0",
        "JobName": s.name,
        "User": "soperatorchecks",
        "Reservation": "reserve",
        "Partition": "hidden",
        "NodeList": "worker-0",
        "NumNodes": "1",
        "NumCPUs": "32",
        "AllocTRES": s.allocation,
        "StartTime": "2026-01-01T00:00:10",
        "EndTime": "2026-01-01T00:00:15",
    }
    monkeypatch.setattr(recovery, "_accounted", lambda *a: copy.deepcopy(row))
    output = [
        "failed to connect to the docker API at unix:///var/run/docker.sock: connect: no such file or directory"
    ]
    original_slurm = r.slurm
    r.slurm = lambda command: (
        output[0] if command.startswith("tail -c ") else original_slurm(command)
    )
    return s, tmp_path, row, configs, node, output


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "job_uid",
        "missing_worker",
        "active",
        "wrong_gpu",
        "success",
        "pod_restart",
        "supervisor_present",
        "foreign_config",
        "other_failure",
    ],
)
def test_docker_admission_requires_native_missing_socket_cause(docker_failure, mutation):
    s, source, row, configs, node, output = docker_failure
    if mutation == "job_uid":
        s.job["metadata"]["uid"] = "replaced"
    if mutation == "missing_worker":
        s.r.state["acceptance"]["gpuWorkers"].append("worker-1")
    if mutation == "active":
        s.control["JobState"] = "RUNNING"
    if mutation == "wrong_gpu":
        row["AllocTRES"] = row["AllocTRES"].replace("gpu=8", "gpu=1")
    if mutation == "success":
        row["JobState"] = "COMPLETED"
    if mutation == "pod_restart":
        s.pod["status"]["containerStatuses"][0]["restartCount"] = 1
    if mutation == "supervisor_present":
        node["spec"]["configMapRefSupervisord"] = "already-configured"
    if mutation == "foreign_config":
        configs["image-storage"]["data"]["daemon.json"] = "other"
    if mutation == "other_failure":
        output[0] = "unrelated error"
    before = copy.deepcopy(s.r.state)
    if mutation:
        with pytest.raises(RuntimeError):
            recovery.capture_docker_failure(s.r, source)
    else:
        proof = recovery.capture_docker_failure(s.r, source)
        assert proof["failures"][0]["slurm"]["JobState"] == "FAILED"
        assert proof["reservation"]["fingerprint"] == "unchanged"
    assert s.writes == [] and s.persisted == []
    assert s.r.state == before


@pytest.mark.parametrize(
    "mutation", [None, "job_uid", "accounting", "active", "reservation", "users"]
)
def test_docker_handoff_rechecks_failure_before_closing_authorization(docker_failure, mutation):
    s, source, row, configs, node, output = docker_failure
    proof = recovery.capture_docker_failure(s.r, source)
    if mutation == "job_uid":
        s.job["metadata"]["uid"] = "replaced"
    if mutation == "accounting":
        row["JobState"] = "COMPLETED"
    if mutation == "active":
        s.control["JobState"] = "RUNNING"
    if mutation == "reservation":
        s.reservation["fingerprint"] = "replaced"
    if mutation == "users":
        s.reservation["users"].append("foreign")
    if mutation:
        with pytest.raises(RuntimeError):
            recovery.close_docker_repair_reservation(s.r, {"dockerFailure": proof})
        assert s.writes == [] and s.persisted == []
    else:
        recovery.close_docker_repair_reservation(s.r, {"dockerFailure": proof})
        assert s.writes == ["scontrol update ReservationName=reserve Users=root"]
        assert s.r.state["dockerRepairQuiescence"]["status"] == "complete"
