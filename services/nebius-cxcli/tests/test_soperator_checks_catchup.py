"""Admit the exact reservation catch-up defect and execute fresh native checks."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_catchup import ChecksCatchupRecovery
from nebius_cxcli.soperator_checks_handoff import ChecksScheduleHandoff
from test_soperator_checks_execution import Cluster, accept, execution
from test_soperator_checks_execution import policy as policy


def setup(tmp_path, policy):
    cluster = Cluster(policy)
    parent = execution(tmp_path, policy, cluster)
    accept(parent)
    parent.state["installReservationIntent"] = True
    parent._save()
    for job in cluster.jobs.values():
        job["metadata"]["creationTimestamp"] = "2026-01-01T01:00:00Z"
    name = "gpu-fryer"
    check = cluster.checks[name]
    check["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "checks",
        "meta.helm.sh/release-namespace": "soperator",
    }
    check["status"]["slurmJobsStatus"] = {
        "lastRunName": "gpu-fryer-catchup",
        "lastRunId": "No slurm job",
        "lastRunStatus": "Error",
    }
    cron = cluster.crons[name]
    check["spec"]["suspend"] = False
    cron["spec"]["suspend"] = False
    cluster.jobs["gpu-fryer-catchup"] = {
        "metadata": {
            "name": "gpu-fryer-catchup",
            "uid": "late-job",
            "namespace": "soperator",
            "creationTimestamp": "2026-01-01T02:00:00Z",
            "labels": {"app.kubernetes.io/component": "soperatorchecks"},
            "annotations": {
                "batch.kubernetes.io/cronjob-scheduled-timestamp": "2026-01-01T01:30:00Z"
            },
            "ownerReferences": [{"name": name, "kind": "CronJob", "uid": cron["metadata"]["uid"]}],
        },
        "spec": copy.deepcopy(cron["spec"]["jobTemplate"]["spec"]),
        "status": {"conditions": [{"type": "Failed", "status": "True"}]},
    }
    hook = {
        "metadata": {
            "name": "wait-for-active-checks",
            "namespace": "soperator",
            "uid": "hook",
            "annotations": {"helm.sh/hook": "post-install,post-upgrade"},
        },
        "spec": {
            "template": {
                "spec": {
                    "serviceAccountName": "waiter",
                    "restartPolicy": "Never",
                    "containers": [{"name": "wait", "image": "native", "command": ["wait"]}],
                }
            }
        },
    }
    cluster.jobs["wait-for-active-checks"] = hook
    cluster.releases = [
        {
            "metadata": {"name": "checks", "namespace": "flux-system", "uid": "hr"},
            "spec": {"releaseName": "checks", "targetNamespace": "soperator"},
            "status": {
                "lastAttemptedRevisionDigest": "sha256:chart",
                "lastAttemptedReleaseAction": "upgrade",
            },
        }
    ]
    slurm = parent.slurm
    parent.slurm = lambda command: "" if command == "squeue -h -o '%i'" else slurm(command)
    calls = []

    def apply(proof):
        assert cluster.reservation_present
        assert proof["waitHook"]["uid"] == "hook"
        calls.append("quiet")
        for check in cluster.checks.values():
            check["spec"]["suspend"] = True
        for cron in cluster.crons.values():
            cron["spec"]["suspend"] = True

    recovery = ChecksCatchupRecovery(
        parent,
        read_log=lambda *_: "No nodes to run on (ARRAY_SIZE=0). Failing.",
        render_hook=lambda: hook,
        apply_quiet=apply,
    )
    return cluster, parent, recovery, calls


def test_catchup_runs_new_native_jobs_and_preserves_original_evidence(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    original = copy.deepcopy(parent.state["jobs"])
    old_jobs = copy.deepcopy(cluster.jobs)
    result = recovery.recover()
    assert result["status"] == "accepted"
    assert calls == ["quiet"]
    assert parent.state["jobs"] == original
    assert all(cluster.jobs[name] == job for name, job in old_jobs.items())
    assert len(cluster.jobs) == len(old_jobs) + len(original)
    assert cluster.checks["gpu-fryer"]["status"]["slurmJobsStatus"]["lastRunStatus"] == "Complete"
    assert cluster.users == "root" and cluster.reservation_present
    writes = copy.deepcopy(cluster.writes)
    assert recovery.recover() == result
    assert cluster.writes == writes
    ChecksScheduleHandoff(
        parent,
        owner=parent.operation_id,
        release=parent.release_install_reservation,
        verify_released=parent.verify_install_reservation_released,
        admission=SimpleNamespace(verify=lambda: {"status": "closed"}),
    ).release()
    assert recovery.recover() == result
    assert not cluster.reservation_present


@pytest.mark.parametrize(
    "change", ["slurm-id", "owner", "script", "timestamp", "not-failed", "other-error", "submitted"]
)
def test_catchup_rejects_unproven_or_foreign_failure_before_mutation(tmp_path, policy, change):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    job = cluster.jobs["gpu-fryer-catchup"]
    if change == "slurm-id":
        job["metadata"]["annotations"]["slurm-job-id"] = "99"
    elif change == "owner":
        job["metadata"]["ownerReferences"][0]["uid"] = "another-cron"
    elif change == "script":
        job["spec"]["template"]["spec"]["containers"][0]["command"] = ["true"]
    elif change == "timestamp":
        job["metadata"]["creationTimestamp"] = "2025-12-01T00:00:00Z"
    elif change == "not-failed":
        job["status"] = {"active": 1}
    elif change == "other-error":
        cluster.checks["gpu-fryer"]["status"]["slurmJobsStatus"]["lastRunId"] = "98"
    else:
        recovery.read_log = lambda *_: (
            "Submitting Slurm job for node x... No nodes to run on (ARRAY_SIZE=0). Failing."
        )
    writes = copy.deepcopy(cluster.writes)
    with pytest.raises(RuntimeError):
        recovery.recover()
    assert calls == [] and cluster.writes == writes
    assert "catchupRecovery" not in parent.state


def test_resume_after_quiet_checkpoint_does_not_apply_again(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    child = recovery._child

    def interrupted(record):
        runner = child(record)
        runner.accept = lambda **_: (_ for _ in ()).throw(KeyboardInterrupt())
        return runner

    recovery._child = interrupted
    with pytest.raises(KeyboardInterrupt):
        recovery.recover()
    assert parent.state["catchupRecovery"]["status"] == "quiet"
    recovery._child = child
    assert recovery.recover()["status"] == "accepted"
    assert calls == ["quiet"]


def test_recovery_rejects_changed_parent_acceptance(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    recovery.recover()
    parent.state["jobs"][next(iter(parent.state["jobs"]))]["uid"] = "different"
    with pytest.raises(RuntimeError, match="binding changed"):
        recovery.recover()
    assert calls == ["quiet"]


def test_admitted_recovery_rechecks_failed_job_before_resuming_apply(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    parent.state["catchupRecovery"] = recovery._capture(recovery._failures())
    parent._save()
    cluster.jobs["gpu-fryer-catchup"]["metadata"]["uid"] = "replacement"
    with pytest.raises(RuntimeError, match="failure identity changed"):
        recovery.recover()
    assert calls == []


def test_acceptance_completed_before_parent_receipt_resumes_without_new_jobs(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    original_child = recovery._child

    def interrupted(record):
        child = original_child(record)
        close = child.close_authorization

        def stop():
            close()
            raise KeyboardInterrupt

        child.close_authorization = stop
        return child

    recovery._child = interrupted
    with pytest.raises(KeyboardInterrupt):
        recovery.recover()
    jobs = copy.deepcopy(cluster.jobs)
    recovery._child = original_child
    assert recovery.recover()["status"] == "accepted"
    assert cluster.jobs == jobs and calls == ["quiet"]


def test_healthy_acceptance_has_no_recovery_mutations(tmp_path, policy):
    cluster = Cluster(policy)
    parent = execution(tmp_path, policy, cluster)
    accept(parent)
    writes = copy.deepcopy(cluster.writes)
    recovery = ChecksCatchupRecovery(
        parent,
        read_log=lambda *_: pytest.fail("unexpected log"),
        render_hook=lambda: pytest.fail("unexpected render"),
        apply_quiet=lambda _: pytest.fail("unexpected apply"),
    )
    assert recovery.recover() is None
    assert cluster.writes == writes


def test_released_catchup_retains_sealed_child_after_log_cleanup(tmp_path, policy):
    cluster, parent, recovery, calls = setup(tmp_path, policy)
    result = recovery.recover()
    ChecksScheduleHandoff(
        parent,
        owner=parent.operation_id,
        release=parent.release_install_reservation,
        verify_released=parent.verify_install_reservation_released,
        admission=SimpleNamespace(verify=lambda: {"status": "closed"}),
    ).release()
    original = parent.slurm

    def cleaned(command):
        if command.startswith("head -c "):
            raise RuntimeError("upstream output expired")
        return original(command)

    parent.slurm = cleaned
    writes = copy.deepcopy(cluster.writes)
    assert recovery.recover() == result
    assert cluster.writes == writes and calls == ["quiet"]
    child = recovery._child(result)
    next(e for e in child.state["jobs"].values() if e.get("slurmIds"))["diagnosticResult"][
        "status"
    ] = "FAIL"
    child._save()
    with pytest.raises(RuntimeError, match="child acceptance changed"):
        recovery.recover()
