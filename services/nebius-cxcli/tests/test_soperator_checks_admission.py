from __future__ import annotations

import copy
from dataclasses import asdict

import pytest

from checks_lifecycle_fakes import lifecycle_transport
from nebius_cxcli.slurm_jobs import (
    SlurmPartitionState,
    canonical_slurm_partition_record,
    slurm_partition_record_fingerprint,
)
from nebius_cxcli.soperator_checks_admission import ChecksAdmission, diagnostic_partition_preimages
from test_soperator_checks_execution import Cluster, execution
from test_soperator_checks_execution import policy as policy


@pytest.fixture
def admission(tmp_path, policy):
    cluster = Cluster(policy)
    lifecycle_transport(cluster)
    checks = execution(tmp_path, policy, cluster)
    return cluster, checks, ChecksAdmission(checks)


def job(job_id="17", *, held=False):
    return f"JobId={job_id} UserId=alice(1002) JobState=PENDING BatchFlag=1 SubmitTime=2026-01-01T00:00:00 JobName=train Priority={0 if held else 1} Reason={'JobHeldAdmin' if held else 'None'}"


def authorize(checks, barrier):
    checks.state["phase"] = "restored"
    checks.state["scheduleRelease"] = {
        "status": "released",
        "binding": {"operation": checks.operation_id},
    }
    checks.verify_acceptance = lambda: {}
    barrier.authorize()


def test_checks_use_hidden_while_ordinary_work_remains_blocked(admission):
    cluster, _, barrier = admission
    cluster.pending = {"17_3": job("17_3"), "18+0": job("18+0", held=True)}
    barrier.establish(checks_open=True)
    assert cluster.partitions["gpu"]["State"] == "DOWN"
    assert cluster.partitions["hidden"]["State"] == "UP"
    assert cluster.partitions["hidden"]["AllowGroups"] == "soperatorchecks"
    assert list(barrier.state["holds"]) == ["17_3"]
    before = copy.deepcopy(cluster.writes)
    barrier.verify()
    assert cluster.writes == before


@pytest.mark.parametrize("state", ["RUNNING", "COMPLETING", "SUSPENDED"])
def test_active_allocation_never_bypassed_by_pending_job_handling(admission, state):
    cluster, checks, barrier = admission
    original = checks.slurm

    def running(command):
        if command.startswith("squeue -h -t ") and state in command:
            return "7|alice|train"
        return original(command)

    checks.slurm = running
    with pytest.raises(RuntimeError, match="overlaps check acceptance"):
        barrier.establish(checks_open=True)
    assert cluster.partitions["hidden"]["State"] == "DOWN"


def test_full_preimage_preserves_original_down_and_acl(admission):
    cluster, checks, _ = admission
    rows = []
    for name, live in cluster.partitions.items():
        previous = {
            **live,
            "State": "INACTIVE" if name == "gpu" else "UP",
            "AllowGroups": "research",
        }
        record = canonical_slurm_partition_record(" ".join(f"{k}={v}" for k, v in previous.items()))
        rows.append(
            asdict(
                SlurmPartitionState(
                    name, previous["State"], record, slurm_partition_record_fingerprint(record)
                )
            )
        )
        live["AllowGroups"] = "research"
    records = diagnostic_partition_preimages(rows)
    barrier = ChecksAdmission(checks, lambda: records)
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    barrier.restore_partitions()
    assert cluster.partitions["gpu"]["State"] == "INACTIVE"
    assert cluster.partitions["hidden"]["AllowGroups"] == "research"
    barrier.verify()


def test_failed_restoration_resumes_only_its_owned_delta(admission):
    cluster, checks, barrier = admission
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    original = checks.slurm

    def interrupted(command):
        result = original(command)
        if "PartitionName=gpu State=UP" in command:
            raise KeyboardInterrupt
        return result

    checks.slurm = interrupted
    with pytest.raises(KeyboardInterrupt):
        barrier.restore_partitions()
    checks.slurm = original
    barrier.verify()
    barrier.restore_partitions()
    assert all(row["State"] == "UP" for row in cluster.partitions.values())


@pytest.mark.parametrize("change", ["AllowGroups", "DefaultTime", "unknown_setting"])
def test_external_partition_changes_are_not_overwritten(admission, change):
    cluster, _, barrier = admission
    barrier.establish()
    cluster.partitions["gpu"][change] = "foreign"
    before = copy.deepcopy(cluster.writes)
    with pytest.raises(RuntimeError, match="independently changed"):
        barrier.establish()
    assert cluster.writes == before


def test_foreign_hold_and_job_identity_are_not_released(admission):
    cluster, checks, barrier = admission
    cluster.pending = {"17": job(), "18": job("18", held=True)}
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    barrier.release_holds()
    assert "Priority=1" in cluster.pending["17"]
    assert "Priority=0" in cluster.pending["18"]
    assert ("release", "18") not in cluster.writes


def test_authorization_is_bound_to_policy_and_handoff(admission):
    _, checks, barrier = admission
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    checks.state["scheduleRelease"]["binding"]["operation"] = "other"
    with pytest.raises(RuntimeError, match="authorization changed"):
        barrier.release_holds()


def test_verification_detects_pending_race_without_mutation(admission):
    cluster, _, barrier = admission
    barrier.establish(checks_open=True)
    cluster.pending["17"] = job()
    before = copy.deepcopy(cluster.writes)
    with pytest.raises(RuntimeError, match="reconciliation"):
        barrier.verify()
    assert cluster.writes == before
    barrier.establish(checks_open=True)
    assert ("hold", "17") in cluster.writes


@pytest.mark.parametrize("applied", [False, True])
def test_interrupted_partition_close_commits_intent_before_next_partition(admission, applied):
    cluster, checks, barrier = admission
    cluster.partitions["gpu"]["State"] = "UP"
    original = checks.slurm

    def interrupted(command):
        if "PartitionName=gpu State=DOWN" in command:
            if applied:
                original(command)
            raise KeyboardInterrupt
        return original(command)

    checks.slurm = interrupted
    with pytest.raises(KeyboardInterrupt):
        barrier.establish()
    assert barrier.state["partitionIntent"]["name"] == "gpu"
    checks.slurm = original
    barrier.establish(checks_open=True)
    assert "partitionIntent" not in barrier.state
    assert barrier.state["postimages"]["gpu"]["State"] == "DOWN"
    barrier.verify()
    authorize(checks, barrier)
    barrier.restore_partitions()
    assert cluster.partitions["gpu"]["State"] == "UP"


@pytest.mark.parametrize("failure_point", ["before-apply", "partial-apply", "after-apply"])
def test_final_declarative_handoff_resumes_owned_barrier_reconciliation(admission, failure_point):
    from nebius_cxcli.soperator_checks_lifecycle import ChecksLifecycle

    cluster, checks, barrier = admission
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    calls = []

    def apply(context):
        calls.append(context.phase.value)
        intent = barrier.state["releaseConfiguration"]
        assert intent["status"] == "intent"
        if len(calls) == 1 and failure_point == "before-apply":
            raise KeyboardInterrupt
        names = list(intent["after"])
        for name in names:
            cluster.partitions[name].update(barrier.state["partitions"][name])
            if len(calls) == 1 and failure_point == "partial-apply":
                raise KeyboardInterrupt
        if len(calls) == 1 and failure_point == "after-apply":
            raise KeyboardInterrupt

    lifecycle = ChecksLifecycle(checks, apply)
    lifecycle.admission = barrier
    with pytest.raises(KeyboardInterrupt):
        lifecycle.finish()
    frozen = copy.deepcopy(barrier.state["releaseConfiguration"])
    assert frozen["status"] == "intent"
    assert barrier.state["status"] == "releasing"
    # Canonical resume re-proves prior lifecycle stages before final handoff.
    lifecycle.maintenance()
    lifecycle.before_acceptance()
    # A retained old declarative overlay may reappear before READY converges.
    for name, row in frozen["before"].items():
        cluster.partitions[name].update(row)
    lifecycle.finish()
    assert barrier.state["releaseConfiguration"] == {**frozen, "status": "complete"}
    assert barrier.state["status"] == "released"
    assert checks.state["lifecyclePhase"] == "ready"
    assert calls == ["ready", "ready"]
    barrier.verify()
    cluster.partitions["gpu"]["State"] = "DOWN"
    with pytest.raises(RuntimeError, match="independently changed"):
        barrier.verify()


def test_release_configuration_requires_authorization_and_preserves_foreign_fields(admission):
    cluster, checks, barrier = admission
    barrier.establish(checks_open=True)
    with pytest.raises(RuntimeError, match="lacks admission authorization"):
        barrier.prepare_release_configuration()
    authorize(checks, barrier)
    barrier.prepare_release_configuration()
    cluster.partitions["gpu"]["DefaultTime"] = "foreign"
    with pytest.raises(RuntimeError, match="independently changed"):
        barrier.prepare_release_configuration()
    assert barrier.state["releaseConfiguration"]["status"] == "intent"


def test_interruption_after_ready_apply_before_runtime_completion(admission):
    from nebius_cxcli.soperator_checks_lifecycle import ChecksLifecycle

    cluster, checks, barrier = admission
    barrier.establish(checks_open=True)
    authorize(checks, barrier)
    lifecycle = ChecksLifecycle(checks, lambda _: None)
    lifecycle.admission = barrier
    restore = barrier.restore_partitions
    barrier.restore_partitions = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        lifecycle.finish()
    assert checks.state["lifecyclePhase"] == "ready"
    assert barrier.state["releaseConfiguration"]["status"] == "intent"
    barrier.restore_partitions = restore
    lifecycle.finish()
    assert barrier.state["releaseConfiguration"]["status"] == "complete"
    assert all(row["State"] == "UP" for row in cluster.partitions.values())
