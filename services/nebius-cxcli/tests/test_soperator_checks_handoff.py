"""Check scheduling eligibility and durable release across interruption boundaries."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_handoff import ChecksScheduleHandoff
from test_soperator_checks_execution import Cluster, accept, execution
from test_soperator_checks_execution import policy as policy


def handoff(runner, *, owner=None):
    return ChecksScheduleHandoff(
        runner,
        owner=owner or runner.operation_id,
        release=runner.release_install_reservation,
        verify_released=runner.verify_install_reservation_released,
        admission=SimpleNamespace(verify=lambda: {"status": "closed"}),
    )


def accepted(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    accept(runner)
    runner.state["installReservationIntent"] = True
    runner._save()
    return cluster, runner


def test_policy_only_resumes_after_root_only_owned_release(tmp_path, policy):
    cluster, runner = accepted(tmp_path, policy)
    calls = []

    def apply():
        assert not cluster.reservation_present
        assert cluster.users == "root"
        assert runner.state["phase"] == "accepted"
        calls.append("apply")

    result = handoff(runner).restore(
        apply_policy=apply,
        policy_restored=lambda: bool(calls),
        complete_source_handoff=lambda: calls.append("source"),
    )
    assert result["status"] == "restored"
    assert runner.state["phase"] == "restored"
    assert calls == ["apply", "source"]
    assert [x[0] for x in cluster.writes].count("delete-reservation") == 1


@pytest.mark.parametrize("boundary", ["intent", "deleted", "released"])
def test_release_resumes_each_durable_boundary_without_recreating_maintenance(
    tmp_path, policy, boundary
):
    cluster, runner = accepted(tmp_path, policy)
    original_save = runner._save
    original_verify = runner.verify_install_reservation_released

    def save():
        original_save()
        if runner.state.get("scheduleRelease", {}).get("status") == boundary:
            raise KeyboardInterrupt

    def verify(proof):
        original_verify(proof)
        if boundary == "deleted":
            raise KeyboardInterrupt

    runner._save = save
    runner.verify_install_reservation_released = verify
    with pytest.raises(KeyboardInterrupt):
        handoff(runner).release()
    resumed = execution(tmp_path, policy, cluster)
    assert handoff(resumed).release()["status"] == "released"
    assert not cluster.reservation_present
    assert resumed.state["phase"] == "accepted"
    assert [x[0] for x in cluster.writes].count("delete-reservation") == 1
    with pytest.raises(RuntimeError, match="cannot be entered again"):
        resumed.prepare_reservation("reserve", installing=True)


@pytest.mark.parametrize("change", ["owner", "accepted-job", "recreated", "lost-intent"])
def test_release_rejects_changed_authority_and_unproven_absence(tmp_path, policy, change):
    cluster, runner = accepted(tmp_path, policy)
    handoff(runner).release()
    writes = copy.deepcopy(cluster.writes)
    owner = None
    if change == "owner":
        owner = "another-operation"
    elif change == "accepted-job":
        next(iter(runner.state["jobs"].values()))["uid"] = "replacement"
    elif change == "recreated":
        cluster.reservation_present = True
    else:
        runner.state.pop("scheduleRelease")
    with pytest.raises(RuntimeError):
        handoff(runner, owner=owner).release()
    assert cluster.writes == writes


def test_policy_failure_retains_accepted_evidence_without_false_restored_phase(tmp_path, policy):
    cluster, runner = accepted(tmp_path, policy)
    jobs = copy.deepcopy(cluster.jobs)

    def fail():
        raise RuntimeError("policy unavailable")

    with pytest.raises(RuntimeError, match="policy unavailable"):
        handoff(runner).restore(
            apply_policy=fail, policy_restored=lambda: False, complete_source_handoff=lambda: None
        )
    assert runner.state["phase"] == "accepted"
    assert runner.state["scheduleRelease"]["status"] == "released"
    resumed = execution(tmp_path, policy, cluster)
    handoff(resumed).restore(
        apply_policy=lambda: None,
        policy_restored=lambda: True,
        complete_source_handoff=lambda: None,
    )
    assert resumed.state["phase"] == "restored"
    assert cluster.jobs == jobs


def test_no_release_or_authorization_change_before_acceptance(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    with pytest.raises(RuntimeError, match="acceptance"):
        handoff(runner).release()
    assert cluster.writes == []


def test_released_acceptance_survives_upstream_output_retention(tmp_path, policy):
    cluster, runner = accepted(tmp_path, policy)
    handoff(runner).release()
    original = runner.slurm

    def cleaned(command):
        if command.startswith("head -c "):
            raise RuntimeError("upstream output expired")
        return original(command)

    runner.slurm = cleaned
    writes = copy.deepcopy(cluster.writes)
    assert runner.verify_acceptance()["status"] == "accepted"
    assert cluster.writes == writes


@pytest.mark.parametrize(
    "change",
    ["no-release", "intent", "owner", "digest", "verdict", "recreated", "job", "accounting"],
)
def test_released_acceptance_retains_identity_and_evidence_guards(tmp_path, policy, change):
    cluster, runner = accepted(tmp_path, policy)
    handoff(runner).release()
    job_name, entry = next((n, e) for n, e in runner.state["jobs"].items() if e.get("slurmIds"))
    if change == "no-release":
        runner.state.pop("scheduleRelease")
    elif change == "intent":
        runner.state["scheduleRelease"]["status"] = "intent"
    elif change == "owner":
        runner.state["scheduleRelease"]["binding"]["owner"] = "foreign"
    elif change == "digest":
        runner.state["scheduleRelease"]["binding"]["acceptanceSha256"] = "changed"
    elif change == "verdict":
        entry["diagnosticResult"]["status"] = "FAIL"
    elif change == "recreated":
        cluster.reservation_present = True
    elif change == "job":
        cluster.jobs[job_name]["metadata"]["uid"] = "foreign"
    else:
        cluster.accounting[entry["slurmIds"][0]] = ""
    original = runner.slurm

    def cleaned(command):
        if command.startswith("head -c "):
            raise RuntimeError("upstream output expired")
        return original(command)

    runner.slurm = cleaned
    writes = copy.deepcopy(cluster.writes)
    with pytest.raises(RuntimeError):
        runner.verify_acceptance()
    assert cluster.writes == writes
