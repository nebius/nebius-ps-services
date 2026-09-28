import copy
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import yaml

from nebius_cxcli import soperator_install_docker_drains as drains
from nebius_cxcli import soperator_install_docker_storage_recovery as recovery
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_docker_storage_repair import (
    docker_storage_candidate,
    docker_storage_handoff,
)
from nebius_cxcli.soperator_install_render_repair import (
    OUTER_FILE,
    VALUES_FILE,
    _file_hashes,
    _files,
)
from nebius_cxcli.soperator_worker_docker import DOCKER_STORAGE_MOUNT, materialize_worker_docker
from test_soperator_checks_execution import policy as policy
from test_soperator_install_runtime_repair import compiled as runtime_compiled
from test_soperator_install_storage_recovery import storage as storage
from test_soperator_release_reconciler import _paths


def compiled():
    files, values = runtime_compiled()
    node_values = values["nodesets"]["overrideValues"]
    materialize_worker_docker(node_values)
    node_values["nodesets"][0]["slurmd"]["volumes"]["jailSubMounts"] = [
        {
            "name": "home",
            "mountPath": "/home",
            "volumeSource": {"persistentVolumeClaim": {"claimName": "home"}},
        }
    ]
    for name in (VALUES_FILE, OUTER_FILE):
        doc = yaml.safe_load(files[name])
        if name == VALUES_FILE:
            doc["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
        else:
            doc["spec"]["values"] = values
        files[name] = yaml.safe_dump(doc, sort_keys=False).encode()
    return files, values


def test_storage_delta_preserves_every_other_byte_and_is_reversible():
    before, values = compiled()
    after = docker_storage_candidate(before)
    assert {k for k in after if after[k] != before[k]} == {VALUES_FILE, OUTER_FILE}
    assert docker_storage_candidate(after) == after
    assert docker_storage_candidate(after, inverse=True) == before
    expected = copy.deepcopy(values)
    expected["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["volumes"][
        "jailSubMounts"
    ].insert(0, DOCKER_STORAGE_MOUNT)
    assert yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"]) == expected


@pytest.mark.parametrize("mutation", [None, "previous", "replacement", "policy", "other_bytes"])
def test_storage_handoff_binds_exact_delta_and_policy(tmp_path, policy, mutation):
    before, values = compiled()
    after = docker_storage_candidate(before)
    paths = _paths(tmp_path)
    for name, data in after.items():
        (paths.flux_dir / name).write_bytes(data)
    after = _files(paths.flux_dir)
    before = docker_storage_candidate(after, inverse=True)
    old = replace(policy, values_sha256=checks_digest(values))
    target = replace(
        policy,
        values_sha256=checks_digest(
            yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"])
        ),
    )
    repair = {
        "previousFiles": _file_hashes(before),
        "replacementFiles": _file_hashes(after),
        "reservationHandoff": {"policy": old.sha256, "fingerprint": "retained"},
    }
    if mutation == "previous":
        repair["previousFiles"][VALUES_FILE] = "changed"
    elif mutation == "replacement":
        repair["replacementFiles"][OUTER_FILE] = "changed"
    elif mutation == "policy":
        repair["reservationHandoff"]["policy"] = "changed"
    elif mutation == "other_bytes":
        (paths.flux_dir / "soperator-nebius-adapter.yaml").write_text("changed")
    if mutation:
        with pytest.raises(RuntimeError):
            docker_storage_handoff(repair, paths=paths, policy=target)
    else:
        result = docker_storage_handoff(repair, paths=paths, policy=target)
        assert result["policy"] == target.sha256 and result["predecessorPolicy"] == old.sha256


@pytest.fixture
def shared_storage(storage, monkeypatch):
    s = storage
    s.r.state["jobs"] = {}
    jobs, pods, accounting, observations, nodes = {}, {}, {}, {}, {}
    workers = ["worker-0", "worker-1"]
    s.r.state["acceptance"].update(
        workers=workers,
        gpuWorkers=workers,
        resources={w: {"cores": 32, "gpus": 8} for w in workers},
    )
    s.reservation["nodes"] = workers
    for index, worker in enumerate(workers):
        name, jid = "docker-check-" + str(index), str(index + 11)
        entry, job, pod = copy.deepcopy(s.entry), copy.deepcopy(s.job), copy.deepcopy(s.pod)
        entry.update(check=recovery.DOCKER_CHECK, worker=worker, uid=name, slurmIds=None)
        job["metadata"].update(name=name, uid=name, annotations={"slurm-job-id": jid})
        pod["metadata"].update(name=worker, uid=worker, creationTimestamp="2025-12-31T23:59:00Z")
        pod["spec"]["nodeName"] = "node-" + str(index)
        pod["spec"]["volumes"].append(
            {"name": "jail", "persistentVolumeClaim": {"claimName": "jail"}}
        )
        pod["status"].update(
            phase="Running",
            conditions=[{"type": "Ready", "status": "True"}],
            containerStatuses=[
                {"name": "slurmd", "containerID": worker, "ready": True, "restartCount": 0}
            ],
        )
        s.r.state["jobs"][name], jobs[name], pods[worker] = entry, job, pod
        accounting[jid] = {
            "JobId": jid,
            "JobName": name,
            "JobState": "COMPLETED" if index == 0 else "FAILED",
            "ExitCode": "0:0" if index == 0 else "1:0",
            "User": "soperatorchecks",
            "Reservation": "reserve",
            "Partition": "hidden",
            "NodeList": worker,
            "NumNodes": "1",
            "NumCPUs": "32",
            "AllocTRES": s.allocation,
            "StartTime": "2026-01-01T00:00:10",
            "EndTime": "2026-01-01T00:00:15",
        }
        job["metadata"]["annotations"]["soperator-checks-final-state-time"] = str(
            int(datetime.fromisoformat(accounting[jid]["EndTime"]).replace(tzinfo=UTC).timestamp())
        )
        observations[worker] = {
            "mount": [{"target": "/mnt/jail", "source": "jail[/slot-a]", "fstype": "virtiofs"}],
            "inode": 42,
            "boot": "12345678-1234-1234-1234-123456789abc",
            "dataRoot": "/mnt/jail/mnt/image-storage/docker",
            "ready": index == 0,
        }
        nodes[worker] = {
            "ThreadsPerCore": "1",
            "CPUTot": "32",
            "CPUEfctv": "32",
            "NodeName": worker,
            "State": "IDLE+CLOUD+MAINTENANCE+RESERVED" + ("+DRAIN" if index else ""),
            "Reason": f"[node_problem] {recovery.DOCKER_CHECK}: job {jid} [slurm_job]"
            if index
            else "None",
            "CPUAlloc": "0",
            "AllocMem": "0",
        }
    original_get = s.r._get

    def get(kind, name=""):
        if kind == "pod":
            return pods[name]
        if kind == "job":
            return jobs[name]
        if kind == "node":
            return {
                "metadata": {"uid": name},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        if kind == "nodeset":
            return {
                "metadata": {"uid": "nodeset-uid"},
                "spec": {
                    "configMapRefSupervisord": "custom-supervisord-config",
                    "slurmd": {"volumes": {"jailSubMounts": []}},
                },
            }
        return original_get(kind, name)

    original_slurm = s.r.slurm

    def slurm(command):
        if command.startswith("head -c"):
            return (
                "completed"
                if "worker-0" in command
                else "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
            )
        if "State=UNDRAIN" in command:
            worker = command.split("NodeName=")[1].split()[0]
            nodes[worker]["State"] = nodes[worker]["State"].replace("+DRAIN", "")
            s.writes.append(command)
            return ""
        return original_slurm(command)

    s.r._get, s.r.slurm = get, slurm
    s.r._jobs = lambda: copy.deepcopy(jobs)
    monkeypatch.setattr(recovery, "_accounted", lambda r, jid: copy.deepcopy(accounting[jid]))
    monkeypatch.setattr(drains, "_accounted", lambda r, jid: copy.deepcopy(accounting[jid]))
    monkeypatch.setattr(recovery, "_idle", lambda r: None)
    monkeypatch.setattr(recovery, "slurm_nodes", lambda r: copy.deepcopy(nodes))
    monkeypatch.setattr(
        recovery, "reservation_record", lambda *a, **k: {"ReservationName": "reserve"}
    )

    def read_worker(worker, args):
        return json.dumps(observations[worker])

    return s, jobs, pods, accounting, observations, nodes, read_worker


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "identity",
        "outcome",
        "gpus",
        "inode",
        "mount",
        "ready",
        "drain",
        "allocation",
        "no_failure",
    ],
)
def test_shared_storage_admission_preserves_mixed_native_outcomes(shared_storage, mutation):
    s, jobs, pods, rows, observations, nodes, read = shared_storage
    if mutation == "identity":
        jobs["docker-check-1"]["metadata"]["uid"] = "changed"
    if mutation == "outcome":
        rows["12"]["JobState"] = "CANCELLED"
    if mutation == "gpus":
        rows["12"]["AllocTRES"] = "gres/gpu=1"
    if mutation == "inode":
        observations["worker-1"]["inode"] = 43
    if mutation == "mount":
        observations["worker-1"]["mount"][0]["target"] = "/mnt/jail/mnt/image-storage"
    if mutation == "ready":
        observations["worker-1"]["ready"] = True
    if mutation == "drain":
        nodes["worker-1"]["Reason"] = "unrelated"
    if mutation == "allocation":
        nodes["worker-1"]["CPUAlloc"] = "1"
    if mutation == "no_failure":
        rows["12"].update(JobState="COMPLETED", ExitCode="0:0")
    before = copy.deepcopy(s.r.state)
    if mutation:
        with pytest.raises(RuntimeError):
            recovery.capture_docker_storage_failure(s.r, read)
    else:
        proof = recovery.capture_docker_storage_failure(s.r, read)
        assert [f["slurm"]["JobState"] for f in proof["failures"]] == ["COMPLETED", "FAILED"]
    assert s.r.state == before and not s.writes and not s.persisted


def test_storage_admission_retains_peer_timeout_without_treating_it_as_success(shared_storage):
    s, _jobs, _pods, rows, _observations, _nodes, read = shared_storage
    rows["11"]["JobState"] = "TIMEOUT"
    proof = recovery.capture_docker_storage_failure(s.r, read)
    assert proof["failures"][0]["slurm"]["JobState"] == "TIMEOUT"
    assert s.r.state["jobs"]["docker-check-0"]["status"] == "submit-intent"


@pytest.mark.parametrize(
    "state,old_exit,new_exit,changed_field,allowed",
    [
        ("TIMEOUT", "0:0", "0:15", None, True),
        ("TIMEOUT", "0:0", "0:9", None, True),
        ("TIMEOUT", "0:15", "0:0", None, False),
        ("TIMEOUT", "0:0", "1:15", None, False),
        ("TIMEOUT", "0:0", "0:11", None, False),
        ("COMPLETED", "0:0", "0:15", None, False),
        ("FAILED", "1:0", "0:15", None, False),
        ("TIMEOUT", "0:0", "0:15", "EndTime", False),
        ("TIMEOUT", "0:0", "0:15", "NodeList", False),
    ],
)
def test_handled_timeout_retains_identity_when_termination_signal_is_finalized(
    shared_storage, state, old_exit, new_exit, changed_field, allowed
):
    s, _jobs, _pods, rows, _observations, _nodes, read = shared_storage
    rows["11"].update(JobState=state, ExitCode=old_exit)
    if state == "FAILED":
        # The capture requires one attributed Docker failure, already on worker-1.
        rows["11"].update(JobState="COMPLETED", ExitCode="0:0")
    proof = recovery.capture_docker_storage_failure(s.r, read)
    failure = proof["failures"][0]
    failure["slurm"].update(JobState=state, ExitCode=old_exit)
    rows["11"].update(JobState=state, ExitCode=new_exit)
    if changed_field:
        rows["11"][changed_field] += "-changed"
    before = copy.deepcopy(failure)
    if allowed:
        assert drains._handled_failure(s.r, failure) == rows["11"]
    else:
        with pytest.raises(RuntimeError):
            drains._handled_failure(s.r, failure)
    assert failure == before and not s.writes and not s.persisted


@pytest.mark.parametrize(
    "mutation", [None, "old_pod", "shared_storage", "daemon", "drain", "allocation", "pod_race"]
)
def test_storage_recovery_requires_new_private_ready_workers_before_undrain(
    shared_storage, mutation
):
    s, jobs, pods, rows, observations, nodes, read = shared_storage
    proof = recovery.capture_docker_storage_failure(s.r, read)
    s.reservation["users"] = ["root"]
    for worker, pod in pods.items():
        pod["metadata"]["uid"] += "-new"
        pod["spec"]["volumes"].append({"name": "docker-image-storage", "emptyDir": {}})
        observations[worker].update(
            ready=True,
            mount=[
                {"target": "/mnt/jail/mnt/image-storage", "source": "/dev/disk", "fstype": "ext4"}
            ],
        )
    if mutation == "old_pod":
        pods["worker-1"]["metadata"]["uid"] = "worker-1"
    if mutation == "shared_storage":
        observations["worker-1"]["mount"][0]["fstype"] = "virtiofs"
    if mutation == "daemon":
        observations["worker-1"]["ready"] = False
    if mutation == "drain":
        nodes["worker-1"]["Reason"] = "unrelated"
    if mutation == "allocation":
        nodes["worker-1"]["CPUAlloc"] = "1"
    original_get = s.r._get
    seen = 0

    def raced_get(kind, name=""):
        nonlocal seen
        if mutation == "pod_race" and kind == "pod" and name == "worker-1":
            seen += 1
            if seen > 1:
                pods[name]["metadata"]["uid"] += "-raced"
        return original_get(kind, name)

    s.r._get = raced_get
    obj = recovery.InstallDockerStorageRecovery(s.r, {"dockerStorageFailure": proof}, read)
    if mutation:
        with pytest.raises(RuntimeError):
            obj.restore_drains()
        assert not s.writes
    else:
        obj.restore_drains()
        obj.restore_drains()
        assert s.writes == ["scontrol update NodeName=worker-1 State=UNDRAIN"]
        assert s.r.state["dockerStorageRestore"]["status"] == "complete"


def test_storage_admission_accounts_for_native_cpu_threads(shared_storage):
    s, _jobs, _pods, rows, _observations, nodes, read = shared_storage
    for node in nodes.values():
        node.update(ThreadsPerCore="2", CPUTot="64", CPUEfctv="64")
    for row in rows.values():
        row.update(NumCPUs="64", AllocTRES=row["AllocTRES"].replace("cpu=32", "cpu=64"))
    assert recovery.capture_docker_storage_failure(s.r, read)


def test_root_storage_dispatch_preserves_an_existing_repair_chain(tmp_path, monkeypatch):
    from nebius_cxcli import soperator_install_nodeset_binding_repair as binding
    from nebius_cxcli.soperator_install_docker_storage_repair import (
        prepare_install_docker_storage_repair,
    )

    paths = _paths(tmp_path)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    (paths.reports_dir / "soperator-install-topology-repair-cluster.json").write_text("{}")
    monkeypatch.setattr(
        binding,
        "prepare_install_nodeset_binding_repair",
        lambda **kw: pytest.fail("must keep saved ancestry"),
    )
    assert (
        prepare_install_docker_storage_repair(
            paths=paths, target_ref="cluster", scheduling_journal={"status": "recovery-required"}
        )
        is None
    )
