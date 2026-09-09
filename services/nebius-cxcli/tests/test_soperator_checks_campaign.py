from __future__ import annotations

import copy
from contextlib import nullcontext
from dataclasses import replace

import pytest

from checks_lifecycle_fakes import lifecycle_transport
from nebius_cxcli import soperator_checks_campaign as campaign_module
from nebius_cxcli.soperator_checks import SoperatorChecksExecution
from nebius_cxcli.soperator_checks_phase import ChecksPhase
from nebius_cxcli.soperator_checks_policy import apply_checks_proposal, freeze_checks_proposal
from nebius_cxcli.soperator_full_stack_upgrade import (
    CampaignSegmentResult,
    load_campaign_receipt,
    run_campaign,
)
from nebius_cxcli.soperator_strategy import SoperatorStrategy, plan_soperator_strategy
from test_soperator_checks_execution import Cluster
from test_soperator_checks_execution import policy as policy
from test_soperator_full_stack_upgrade import _intent


@pytest.fixture
def campaign(tmp_path, policy, monkeypatch):
    cluster = Cluster(policy)
    lifecycle_transport(cluster)
    events = []
    monkeypatch.setattr(campaign_module, "compile_checks_policy", lambda *_args: policy)
    monkeypatch.setattr(
        campaign_module,
        "SoperatorChecksExecution",
        lambda **kwargs: SoperatorChecksExecution(
            **kwargs,
            timeout_seconds=2,
            poll_seconds=1,
            clock=lambda: cluster.now,
            sleep=cluster.sleep,
        ),
    )

    def apply(target, context):
        deferred = context.phase not in {ChecksPhase.SCHEDULES, ChecksPhase.READY}
        events.append("deferred-policy" if deferred else "desired-policy")
        if context.phase == ChecksPhase.ACCEPTANCE:
            events.append("acceptance-policy")
        for rule in policy.rules:
            cluster.checks[rule.name]["spec"].update(
                suspend=True if deferred else rule.suspend,
                runAfterCreation=rule.bootstrap and rule.required if deferred else rule.required,
            )
            cluster.crons[rule.name]["spec"]["suspend"] = True if deferred else rule.suspend

    def release(proof):
        assert proof["reservation"] == "reserve"
        assert cluster.users == "root"
        events.append("reservation-released")
        cluster.reservation_present = False

    def verify_release(proof):
        assert proof["reservation"] == "reserve"
        assert not cluster.reservation_present

    checks = campaign_module.SoperatorCampaignChecks(
        operation_id="campaign",
        reports_dir=tmp_path / "reports",
        source_dir=tmp_path,
        cluster_name="lab",
        kubernetes=cluster.kube,
        slurm=cluster.slurm,
        assert_authority=lambda: events.append("fence"),
        load_target=lambda: (policy, tuple(cluster.nodes), ("gpu-0", "gpu-1")),
        apply_target=apply,
        partition_preimages=lambda: (),
        reservation="reserve",
        emit=lambda _message: None,
        target_writers=lambda: (),
        release_maintenance=release,
        verify_maintenance_released=verify_release,
        recover_target=lambda _target: None,
    )
    return checks, cluster, events


def test_parent_keeps_diagnostics_deferred_through_every_infrastructure_segment(tmp_path, campaign):
    checks, cluster, events = campaign
    intent = _intent()

    def stage(name):
        events.append(name)
        assert all(row["spec"]["suspend"] is True for row in cluster.checks.values())
        assert not cluster.jobs
        return CampaignSegmentResult(evidence={"status": "ready"})

    def final():
        assert "deferred-policy" in events
        assert not cluster.jobs
        events.append("runtime-validated")
        result = checks.finalize(already_released=False)
        events.append("checks-accepted")
        return CampaignSegmentResult(evidence=result)

    def restore(record, evidence):
        checks.verify_handoff()
        assert cluster.users == "root"
        events.append("customer-release")
        return {"restored": True}

    receipt = run_campaign(
        path=tmp_path / "campaign.json",
        intent=intent,
        segment_executors={
            name: (final if name == "final-readiness" else lambda name=name: stage(name))
            for name in intent.segments
        },
        enter_maintenance=lambda record, evidence: (
            checks.enter(record),
            {"reservationName": "reserve"},
        )[1],
        restore_maintenance=restore,
        assert_fence=lambda: None,
        verify_maintenance=checks.before_segment,
    )
    assert receipt.status == "complete"
    assert (
        events.index("deferred-policy")
        < events.index("runtime-validated")
        < events.index("acceptance-policy")
        < events.index("checks-accepted")
        < events.index("customer-release")
    )
    assert events.index("desired-policy") > events.index("acceptance-policy")


def test_recurring_checks_never_resume_while_workers_are_reserved(campaign):
    checks, cluster, events = campaign
    reserved = True
    original_apply = checks.apply_target

    def release(proof):
        nonlocal reserved
        assert cluster.users == "root"
        assert proof["reservation"] == "reserve"
        events.append("reservation-released")
        reserved = False
        cluster.reservation_present = False

    def verify_release(proof):
        assert proof["reservation"] == "reserve"
        assert not reserved

    def apply(target, context):
        if context.phase == ChecksPhase.SCHEDULES and reserved:
            raise RuntimeError("native catch-up has zero eligible nodes")
        original_apply(target, context)

    checks.release_maintenance = release
    checks.verify_maintenance_released = verify_release
    checks.apply_target = apply
    checks.before_segment("soperator-release")
    checks.before_segment("final-readiness")
    checks.finalize(already_released=False)
    assert events.index("reservation-released") < events.index("desired-policy")


def test_user_admission_stays_closed_until_policy_readiness_and_authorization(campaign):
    from test_soperator_checks_admission import job

    checks, cluster, _ = campaign
    cluster.pending["17"] = job()
    checks.before_segment("soperator-release")
    checks.before_segment("final-readiness")
    checks.finalize(already_released=False)
    assert not cluster.reservation_present
    assert cluster.partitions["gpu"]["State"] == "DOWN"
    assert cluster.partitions["hidden"]["AllowGroups"] == "soperatorchecks"
    assert "Priority=0" in cluster.pending["17"]
    checks.authorize_admission()
    checks.finish_admission()
    assert cluster.partitions["gpu"]["State"] == "UP"
    assert cluster.partitions["hidden"]["AllowGroups"] == "ALL"
    assert "Priority=1" in cluster.pending["17"]
    writes = copy.deepcopy(cluster.writes)
    checks.verify_handoff()
    assert cluster.writes == writes


@pytest.mark.parametrize("graph_ready", [True, False])
def test_interrupted_final_apply_resumes_before_graph_proof_without_bypassing_it(
    tmp_path, campaign, graph_ready
):
    checks, cluster, events = campaign
    apply = checks.apply_target
    remaining_stage_suspended = True

    def resume_policy(policy, context):
        nonlocal remaining_stage_suspended
        apply(policy, context)
        remaining_stage_suspended = False

    checks.apply_target = resume_policy

    def final():
        assert not remaining_stage_suspended
        assert not cluster.jobs
        events.append("graph-proof")
        if not graph_ready:
            raise RuntimeError("graph remains unready")
        events.append("gpu-proof")
        return CampaignSegmentResult(evidence=checks.finalize(already_released=False))

    intent = _intent()
    with (
        nullcontext() if graph_ready else pytest.raises(RuntimeError, match="graph remains unready")
    ):
        run_campaign(
            path=tmp_path / "campaign.json",
            intent=intent,
            segment_executors={
                name: final
                if name == "final-readiness"
                else lambda: CampaignSegmentResult(evidence={"status": "ready"})
                for name in intent.segments
            },
            enter_maintenance=lambda record, evidence: (
                checks.enter(record),
                {"reservationName": "reserve"},
            )[1],
            restore_maintenance=lambda record, evidence: {"restored": True},
            assert_fence=lambda: None,
            verify_maintenance=checks.before_segment,
        )
    receipt = load_campaign_receipt(tmp_path / "campaign.json")
    assert receipt is not None
    assert events.index("deferred-policy") < events.index("graph-proof")
    if graph_ready:
        assert receipt.status == "complete"
        assert events.index("gpu-proof") < events.index("acceptance-policy")
    else:
        assert receipt.status != "complete"
        assert receipt.maintenance == "active"
        assert not cluster.jobs
        assert "reservation-released" not in events


def test_interrupted_quiet_policy_preparation_cannot_reach_acceptance(campaign):
    checks, cluster, events = campaign
    checks.before_segment("soperator-release")
    apply = checks.apply_target

    def interrupted(policy, context):
        apply(policy, context)
        raise RuntimeError("interrupted staged apply")

    checks.apply_target = interrupted
    with pytest.raises(RuntimeError, match="interrupted staged apply"):
        checks.before_segment("final-readiness")
    assert not cluster.jobs
    assert cluster.reservation_present
    checks.apply_target = apply
    checks.before_segment("final-readiness")
    assert not cluster.jobs
    assert events.count("deferred-policy") == 2
    checks.finalize(already_released=False)
    checks.verify_handoff()


@pytest.mark.parametrize("after_apply", ["adopted", "suspended", "replaced"])
def test_source_writer_adoption_follows_policy_replay_and_retains_identity(campaign, after_apply):
    checks, cluster, events = campaign
    checks.before_segment("soperator-release")
    writer = {"kind": "helmrelease", "namespace": "flux-system", "name": "main", "uid": "same"}
    checks.source.state["sourceWriters"] = [writer]
    checks.source._save()
    cluster.releases = [
        {
            "metadata": {"namespace": "flux-system", "name": "main", "uid": "same"},
            "spec": {"suspend": True},
        }
    ]
    checks.target_writers = lambda: (("flux-system", "main"),)
    apply = checks.apply_target

    def resume(policy, context):
        apply(policy, context)
        cluster.releases[0]["spec"]["suspend"] = after_apply == "suspended"
        if after_apply == "replaced":
            cluster.releases[0]["metadata"]["uid"] = "replacement"

    checks.apply_target = resume
    expected = (
        nullcontext()
        if after_apply == "adopted"
        else pytest.raises(RuntimeError, match="source (Helm writer|writer identity)")
    )
    with expected:
        checks.before_segment("final-readiness")
    assert "deferred-policy" in events
    assert not cluster.jobs
    assert cluster.reservation_present
    if after_apply == "adopted":
        assert checks.source.state["sourceHandoff"]["writers"][0]["disposition"] == "adopted-target"
    else:
        assert "sourceHandoff" not in checks.source.state


def test_accepted_resume_restores_graph_before_runtime_reproof(campaign):
    checks, cluster, events = campaign
    checks.before_segment("soperator-release")
    checks.before_segment("final-readiness")

    def interrupted(_target):
        raise RuntimeError("interrupted after acceptance")

    checks.recover_target = interrupted
    with pytest.raises(RuntimeError, match="interrupted after acceptance"):
        checks.finalize(already_released=False)
    submitted = len(cluster.jobs)
    assert submitted and cluster.reservation_present
    checks.recover_target = lambda _target: None
    checks.before_segment("final-readiness")
    checks.verify_handoff()
    assert not cluster.reservation_present
    assert len(cluster.jobs) == submitted
    assert events.count("deferred-policy") == 2
    assert events.count("desired-policy") == 1


def test_completed_campaign_reproof_does_not_recreate_a_released_reservation(campaign):
    checks, cluster, _events = campaign
    checks.before_segment("soperator-release")
    checks.before_segment("final-readiness")
    checks.finalize(already_released=False)
    writes = copy.deepcopy(cluster.writes)
    original = cluster.slurm

    def slurm(command):
        assert not command.startswith("scontrol ") or command.startswith("scontrol show "), command
        return original(command)

    checks.slurm = slurm
    assert checks.finalize(already_released=True)["jobs"] == 4
    assert cluster.writes == writes
    cluster.checks["gpu-fryer"]["spec"]["suspend"] = True
    with pytest.raises(RuntimeError, match="restored check acceptance"):
        checks.finalize(already_released=True)


def test_interrupted_policy_restoration_reuses_acceptance_without_resubmission(campaign):
    checks, cluster, _events = campaign
    checks.before_segment("soperator-release")
    apply = checks.apply_target

    def fail_restore(policy, context):
        if context.phase == ChecksPhase.SCHEDULES:
            raise RuntimeError("interrupted restoration")
        apply(policy, context)

    checks.apply_target = fail_restore
    checks.before_segment("final-readiness")
    with pytest.raises(RuntimeError, match="interrupted restoration"):
        checks.finalize(already_released=False)
    submitted = len(cluster.jobs)
    checks.apply_target = apply
    checks.before_segment("final-readiness")
    checks.verify_handoff()
    assert checks.finalize(already_released=False)["jobs"] == submitted
    assert len(cluster.jobs) == submitted
    checks.verify_handoff()


@pytest.mark.parametrize("drift", ["cron", "job", "slurm"])
def test_effective_diagnostics_must_be_quiet_before_next_segment(campaign, drift):
    checks, cluster, events = campaign
    checks.before_segment("soperator-release")
    if drift == "cron":
        cluster.crons["gpu-fryer"]["spec"]["suspend"] = False
    elif drift == "job":
        cluster.jobs["unexpected-diagnostic"] = {
            "metadata": {
                "name": "unexpected-diagnostic",
                "uid": "unexpected-job",
                "ownerReferences": [
                    {"kind": "CronJob", "name": "gpu-fryer", "uid": "cron-gpu-fryer"}
                ],
            },
            "status": {},
        }
    else:
        cluster.running = "123"
    before = list(events)
    with pytest.raises(RuntimeError, match="deferral|diagnostic"):
        checks.before_segment("mk8s-hop:1.35")
        events.append("infrastructure-mutated")
    assert "infrastructure-mutated" not in events[len(before) :]
    assert not cluster.writes


@pytest.mark.parametrize(
    "resource,field", [("check", "schedule"), ("cron", "schedule"), ("cron", "timeZone")]
)
def test_completed_handoff_rejects_exact_schedule_drift(campaign, resource, field):
    checks, cluster, _events = campaign
    checks.before_segment("soperator-release")
    checks.before_segment("final-readiness")
    checks.finalize(already_released=False)
    rows = cluster.checks if resource == "check" else cluster.crons
    rows["gpu-fryer"]["spec"][field] = "0 0 * * *" if field == "schedule" else "Europe/Paris"
    writes = copy.deepcopy(cluster.writes)
    count = len(cluster.jobs)
    with pytest.raises(RuntimeError, match="restored check acceptance"):
        checks.finalize(already_released=True)
    assert len(cluster.jobs) == count and cluster.writes == writes
    assert not cluster.reservation_present


def test_between_segment_policy_or_barrier_drift_blocks_next_mutation(campaign):
    checks, cluster, _events = campaign
    checks.before_segment("soperator-release")
    cluster.checks["gpu-fryer"]["spec"]["runAfterCreation"] = True
    with pytest.raises(RuntimeError, match="deferral changed"):
        checks.before_segment("node-templates:1.34")
    cluster.checks["gpu-fryer"]["spec"]["runAfterCreation"] = False
    cluster.reservation_fields["CoreCnt"] = "1"
    with pytest.raises(RuntimeError, match="every worker core"):
        checks.before_segment("node-templates:1.34")


def test_reviewed_proposal_is_bound_to_campaign_and_recoverable_from_either_preimage():
    before = {
        "clusterType": "cpu",
        "soperator-checks": {"enabled": False},
        "soperator-activechecks": {
            "enabled": False,
            "waitForChecks": {"enabled": False},
            "srunReadyPartition": "cpu",
            "checks": {
                "wait-for-topology": {"runAfterCreation": False},
                "gpu-fryer": {"enabled": False},
            },
        },
    }
    frozen = freeze_checks_proposal(before)
    after, changes = apply_checks_proposal(before, frozen)
    assert changes
    assert "waitForChecks" not in after["soperator-activechecks"]
    assert "srunReadyPartition" not in after["soperator-activechecks"]
    assert (
        after["soperator-activechecks"]["checks"]["wait-for-topology"]["runAfterCreation"] is False
    )
    assert after["soperator-activechecks"]["checks"]["gpu-fryer"]["enabled"] is False
    assert apply_checks_proposal(after, frozen)[0] == after
    changed = copy.deepcopy(after)
    changed["soperator-activechecks"]["checks"]["gpu-fryer"]["enabled"] = True
    with pytest.raises(ValueError, match="differs from the frozen"):
        apply_checks_proposal(changed, frozen)
    original = _intent()
    assert replace(original, checks_policy_proposal=frozen).digest != original.digest


def test_same_release_policy_change_or_later_infrastructure_change_is_not_a_noop():
    args = dict(
        source_release="4.1.7",
        target_release="4.1.7",
        source_contract="upstream-flux-v1",
        target_contract="upstream-flux-v1",
    )
    assert plan_soperator_strategy(**args).strategy is SoperatorStrategy.NOOP
    assert (
        plan_soperator_strategy(**args, desired_state_changed=True).strategy
        is SoperatorStrategy.IN_PLACE
    )
    intent = replace(_intent(), source_release="4.1.7", target_release="4.1.7")
    assert (
        intent.requires_fresh_checks
    )  # Kubernetes/provider segments still need the diagnostic boundary.


@pytest.mark.parametrize(
    "suspend,claim,expected",
    [(False, "active-jail", True), (True, "active-jail", False), (False, "foreign", False)],
)
def test_policy_restoration_requires_auxiliary_schedule_and_active_claim(
    tmp_path, policy, suspend, claim, expected
):
    from test_soperator_checks_execution import execution

    policy = replace(
        policy, auxiliary_pvc="active-jail", auxiliary_spec={"schedule": "*/5 * * * *"}
    )
    cluster = Cluster(policy)
    for rule in policy.rules:
        cluster.checks[rule.name]["spec"].update(
            suspend=rule.suspend, runAfterCreation=rule.required
        )
        cluster.crons[rule.name]["spec"]["suspend"] = rule.suspend
    cluster.crons["run-extensive-check-on-reservations"] = {
        "metadata": {"name": "run-extensive-check-on-reservations", "uid": "auxiliary-uid"},
        "spec": {
            "schedule": "*/5 * * * *",
            "suspend": suspend,
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "volumes": [
                                {"name": "jail", "persistentVolumeClaim": {"claimName": claim}},
                                {
                                    "name": "slurm-configs",
                                    "configMap": {"name": "cluster-slurm-configs"},
                                },
                                {"name": "munge-key", "secret": {"secretName": "cluster-munge"}},
                            ]
                        }
                    }
                }
            },
        },
    }
    assert (
        campaign_module.SoperatorCampaignChecks._policy_restored(
            execution(tmp_path, policy, cluster)
        )
        is expected
    )


@pytest.mark.parametrize("drift", [None, "schedule", "timeZone", "suspend"])
def test_auxiliary_effective_deferral_checks_exact_scheduling(tmp_path, policy, drift):
    from nebius_cxcli.soperator_checks_scheduling import scheduling_inventory
    from test_soperator_checks_execution import execution

    policy = replace(
        policy,
        auxiliary_pvc="active-jail",
        auxiliary_spec={"schedule": "*/5 * * * *", "timeZone": "Etc/UTC"},
    )
    cluster = Cluster(policy)
    spec = {
        "schedule": "*/5 * * * *",
        "timeZone": "Etc/UTC",
        "suspend": True,
        "jobTemplate": {
            "spec": {
                "template": {
                    "spec": {
                        "volumes": [
                            {"name": "jail", "persistentVolumeClaim": {"claimName": "active-jail"}},
                            {
                                "name": "slurm-configs",
                                "configMap": {"name": "cluster-slurm-configs"},
                            },
                            {"name": "munge-key", "secret": {"secretName": "cluster-munge"}},
                        ]
                    }
                }
            }
        },
    }
    if drift:
        spec[drift] = (
            False if drift == "suspend" else "0 1 * * *" if drift == "schedule" else "Europe/Paris"
        )
    cluster.crons["run-extensive-check-on-reservations"] = {
        "metadata": {"name": "run-extensive-check-on-reservations", "uid": "aux-uid"},
        "spec": spec,
    }
    runner = execution(tmp_path, policy, cluster)
    assert (scheduling_inventory(runner, deferred=True) is not None) is (drift is None)
    assert not cluster.writes
