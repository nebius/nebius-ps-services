import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_contract import job_execution_digest
from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_storage_recovery import (
    InstallStorageRecovery,
    capture_storage_failure,
)


@pytest.fixture
def storage():
    name = "prepull-container-image-initial-run"
    job = {
        "metadata": {
            "name": name,
            "uid": "job-uid",
            "labels": {"cxcli.nebius.ai/check-operation": "old"},
            "annotations": {"slurm-job-id": "11"},
        },
        "spec": {"template": {"spec": {"containers": [{"name": "native"}]}}},
        "status": {"conditions": [{"type": "Complete", "status": "True"}]},
    }
    cron = {"spec": {"jobTemplate": copy.deepcopy(job)}}
    epoch = {"template": checks_digest(cron["spec"]["jobTemplate"])}
    entry = {
        "check": "prepull-container-image",
        "status": "submit-intent",
        "worker": "worker-0",
        "submitted": True,
        "uid": "job-uid",
        "slurmIds": ["11"],
        "epoch": epoch,
        "execution": job_execution_digest(job),
    }
    owner = {"name": "worker", "uid": "sts-uid", "kind": "StatefulSet", "controller": True}
    node_owner = {"name": "worker", "uid": "nodeset-uid", "kind": "NodeSet", "controller": True}
    pod = {
        "metadata": {
            "uid": "replacement",
            "name": "worker-0",
            "creationTimestamp": "2026-01-01T00:02:00Z",
            "ownerReferences": [owner],
        },
        "spec": {
            "nodeName": "node-a",
            "volumes": [{"name": "driver", "hostPath": {"path": "/"}}],
            "containers": [
                {
                    "name": "slurmd",
                    "volumeMounts": [
                        {"name": "driver", "mountPath": "/run/nvidia/driver", "readOnly": True}
                    ],
                }
            ],
        },
        "status": {"phase": "Running"},
    }
    old_uid = "12345678-1234-1234-1234-123456789abc"
    boot = "a" * 32
    node = {
        "metadata": {"name": "node-a", "uid": "node-uid"},
        "status": {"nodeInfo": {"bootID": boot}},
    }
    timestamp = int(datetime(2026, 1, 1, 0, 1, tzinfo=UTC).timestamp() * 1_000_000)
    event = {
        "__CURSOR": "kill-cursor",
        "__REALTIME_TIMESTAMP": str(timestamp),
        "_SYSTEMD_UNIT": "kubelet.service",
        "_BOOT_ID": boot,
        "MESSAGE": f'"Killing container with a grace period" pod="soperator/worker-0" podUID="{old_uid}" containerName="slurmd"',
    }
    evicted = {
        **event,
        "__CURSOR": "eviction-cursor",
        "__REALTIME_TIMESTAMP": str(timestamp + 1_000_000),
        "MESSAGE": '"Eviction manager: pod is evicted successfully" pod="soperator/worker-0"',
    }
    journal_rows = [event, evicted]

    def journal(worker, since, until):
        assert worker == "worker-0" and (until - since).total_seconds() <= 130
        return journal_rows

    allocation = "cpu=32,mem=16G,node=1,billing=32,gres/gpu=8"
    control = {
        "JobId": "11",
        "JobName": name,
        "UserId": "soperatorchecks(1000)",
        "Partition": "hidden",
        "Reservation": "reserve",
        "JobState": "RUNNING",
        "SubmitTime": "2026-01-01T00:00:00",
        "StartTime": "2026-01-01T00:00:00",
        "NodeList": "worker-0",
        "NumNodes": "1",
        "NumCPUs": "32",
        "AllocTRES": allocation,
        "SubmitLine": "sbatch native.sh",
    }
    reservation = {
        "name": "reserve",
        "fingerprint": "unchanged",
        "users": ["root", "soperatorchecks"],
        "nodes": ["worker-0"],
        "flags": ["MAINT", "IGNORE_JOBS"],
    }
    writes, persisted = [], []
    r = SimpleNamespace(
        operation_id="old",
        state={
            "phase": "acceptance",
            "installReservationIntent": True,
            "jobs": {name: entry},
            "reservation": "reserve",
            "principalUid": "1000",
            "acceptance": {"resources": {"worker-0": {"cores": 32, "gpus": 8}}},
        },
    )

    def accounting():
        return "|".join(
            [
                "11",
                control["JobState"],
                "0:0",
                control["NodeList"],
                "soperatorchecks",
                control["JobName"],
                control["Reservation"],
                control["AllocTRES"],
                control["StartTime"],
                control["SubmitTime"],
                control["Partition"],
                control["NumCPUs"],
                control["NumNodes"],
                control.get(
                    "EndTime",
                    "Unknown" if control["JobState"] == "RUNNING" else "2026-01-01T00:03:00",
                ),
                control["SubmitLine"],
            ]
        )

    inventory = [control]

    def slurm(command):
        if command == "id -u soperatorchecks":
            return "1000"
        if "sacct -n -P" in command:
            return accounting()
        if command.endswith("scontrol show jobs -o"):
            return "\n".join(" ".join(f"{k}={v}" for k, v in row.items()) for row in inventory)
        if command == "scancel 11":
            writes.append(command)
            control["JobState"] = "CANCELLED"
            return ""
        if command == "scontrol update ReservationName=reserve Users=root":
            writes.append(command)
            reservation["users"] = ["root"]
            return ""
        raise AssertionError(command)

    def get(kind, selected=""):
        return {
            "job": job,
            "pod": pod,
            "node": node,
            "statefulsets.apps.kruise.io": {
                "metadata": {"uid": "sts-uid", "ownerReferences": [node_owner]},
            },
        }[kind]

    def until(action, description):
        for _ in range(3):
            result = action()
            if result:
                return result
        raise RuntimeError(description)

    r.slurm, r._get = slurm, get
    r._jobs = lambda: {name: copy.deepcopy(job)}
    r._target_cronjob = lambda check: cron
    r._verify_execution_authority = lambda *a, **k: None
    r.kube = lambda args, document: {"items": []}
    r._reservation = lambda name: copy.deepcopy(reservation)
    r._accounting = lambda ids: "|".join(accounting().split("|")[:8])
    r._until = until
    r._save = lambda: persisted.append(copy.deepcopy(r.state))
    r.authority = lambda: None
    return SimpleNamespace(**locals())


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "uid",
        "job-body",
        "ambiguous-id",
        "worker",
        "event-uid",
        "event-time",
        "event-host",
        "node-owner",
        "foreign",
        "gpu",
    ],
)
def test_storage_admission_requires_exact_evicted_native_import(storage, mutation):
    s = storage
    if mutation == "uid":
        s.job["metadata"]["uid"] = "other"
    elif mutation == "job-body":
        s.job["spec"]["template"]["spec"]["containers"][0]["args"] = ["changed"]
    elif mutation == "ambiguous-id":
        s.job["metadata"]["annotations"]["slurm-job-id"] = "11,12"
    elif mutation == "worker":
        s.pod["metadata"]["uid"] = s.old_uid
    elif mutation == "event-uid":
        s.event.pop("__CURSOR")
    elif mutation == "event-time":
        s.event["__REALTIME_TIMESTAMP"] = str(s.timestamp + 120_000_000)
    elif mutation == "event-host":
        s.event["_BOOT_ID"] = "b" * 32
    elif mutation == "node-owner":
        s.node_owner["kind"] = "Other"
    elif mutation == "foreign":
        s.inventory.append({**s.control, "JobId": "99"})
    elif mutation == "gpu":
        s.control["AllocTRES"] = s.allocation.replace("gpu=8", "gpu=1")
    if mutation:
        with pytest.raises(RuntimeError):
            capture_storage_failure(s.r, journal=s.journal)
    else:
        proof = capture_storage_failure(s.r, journal=s.journal)
        assert proof["eviction"]["workerUid"] == s.old_uid
        assert proof["worker"]["uid"] == "replacement"
        assert proof["slurm"]["JobId"] == "11"
    assert not s.writes and not s.persisted


def test_storage_recovery_preserves_failed_job_and_requires_terminal_accounting(storage):
    s = storage
    proof = capture_storage_failure(s.r, journal=s.journal)
    original = copy.deepcopy(s.job)
    s.r.state = {}
    recovery = InstallStorageRecovery(s.r, {"storageFailure": proof})
    recovery.quiesce()
    assert s.writes == ["scancel 11", "scontrol update ReservationName=reserve Users=root"]
    assert s.persisted[0]["storageRepairQuiescence"]["status"] == "cancel-intent"
    assert s.r.state["storageRepairQuiescence"]["terminal"]["state"] == "CANCELLED"
    assert s.job == original
    assert "jobs" not in s.r.state
    recovery.quiesce()
    assert len(s.writes) == 2


def test_timed_out_import_can_be_retired_after_controller_record_is_purged(storage):
    s = storage
    s.control["JobState"] = "TIMEOUT"
    s.inventory.clear()
    proof = capture_storage_failure(s.r, journal=s.journal)
    s.r.state = {}
    InstallStorageRecovery(s.r, {"storageFailure": proof}).quiesce()
    assert "scancel 11" not in s.writes
    assert s.r.state["storageRepairQuiescence"]["terminal"]["state"] == "TIMEOUT"


def test_cancel_interruption_resumes_without_duplicate_cancellation(storage):
    s = storage
    proof = capture_storage_failure(s.r, journal=s.journal)
    s.r.state = {}
    original = s.r.slurm

    def interrupted(command):
        result = original(command)
        if command == "scancel 11":
            raise KeyboardInterrupt
        return result

    s.r.slurm = interrupted
    recovery = InstallStorageRecovery(s.r, {"storageFailure": proof})
    with pytest.raises(KeyboardInterrupt):
        recovery.quiesce()
    s.r.slurm = original
    recovery.quiesce()
    assert s.writes.count("scancel 11") == 1


def test_terminal_import_must_overlap_worker_eviction(storage):
    s = storage
    s.control["JobState"] = "TIMEOUT"
    s.control["EndTime"] = "2026-01-01T00:00:30"
    s.inventory.clear()
    with pytest.raises(RuntimeError, match="authoritative worker eviction"):
        capture_storage_failure(s.r, journal=s.journal)
    assert not s.writes and not s.persisted


@pytest.mark.parametrize("mutation", ["mount", "node", "unit", "duplicate", "unrelated", "late"])
def test_journal_admission_rejects_lost_host_and_eviction_authority(storage, mutation):
    s = storage
    if mutation == "mount":
        s.pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif mutation == "node":
        s.node["metadata"]["name"] = "another-node"
    elif mutation == "unit":
        s.event["_SYSTEMD_UNIT"] = "untrusted.service"
    elif mutation == "duplicate":
        s.journal_rows.append({**s.evicted, "__CURSOR": "another-eviction"})
    elif mutation == "unrelated":
        s.evicted["MESSAGE"] = s.evicted["MESSAGE"].replace("worker-0", "worker-1")
    elif mutation == "late":
        s.evicted["__REALTIME_TIMESTAMP"] = str(s.timestamp + 11_000_000)
    with pytest.raises(RuntimeError):
        capture_storage_failure(s.r, journal=s.journal)
    assert not s.writes and not s.persisted


@pytest.mark.parametrize(
    "mutation", ["job", "worker", "reservation", "foreign", "success", "accounting"]
)
def test_storage_recovery_fails_closed_on_changed_authority(storage, mutation):
    s = storage
    proof = capture_storage_failure(s.r, journal=s.journal)
    s.r.state = {}
    if mutation == "job":
        s.job["metadata"]["uid"] = "other"
    elif mutation == "worker":
        s.pod["metadata"]["uid"] = "other"
    elif mutation == "reservation":
        s.reservation["fingerprint"] = "drift"
    elif mutation == "foreign":
        s.inventory.append({**s.control, "JobId": "99"})
    elif mutation == "success":
        s.control["JobState"] = "COMPLETED"
    elif mutation == "accounting":
        s.control["JobState"] = "TIMEOUT"
        s.r._accounting = lambda ids: ""
    with pytest.raises(RuntimeError):
        InstallStorageRecovery(s.r, {"storageFailure": proof}).quiesce()
    assert not s.writes
