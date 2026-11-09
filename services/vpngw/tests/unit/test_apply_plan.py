from dataclasses import replace

import pytest

from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect, Evidence, Outcome, Progress, Result


def effect(key="attachments", *, owner="cloud", requires=()):
    return Effect(key, owner, "reconcile", ("compute-1",), "a" * 64, "b" * 64, True, requires)


def plan(*effects):
    return CommandPlan("project-1", "gateway", "c" * 64, "d" * 64, effects)


def test_pending_attachment_cannot_admit_unplanned_guest_work():
    approved = plan(effect())
    current = plan(effect("guest", owner="ordinary"), effect())
    with pytest.raises(RuntimeError, match="newly discovered effects"):
        approved.validate_resume(current, {"attachments": Progress.PLANNED})


def test_exact_unstarted_guest_and_partial_cloud_effect_resume_independently():
    guest, cloud = effect("guest", owner="ordinary"), effect()
    approved = plan(guest, cloud)
    current = plan(guest, replace(cloud, observation_sha256="e" * 64))
    approved.validate_resume(
        current,
        {"guest": Progress.PLANNED, "attachments": Progress.STARTED},
        resumable_children=frozenset({"attachments"}),
    )
    with pytest.raises(RuntimeError, match="child resume evidence"):
        approved.validate_resume(
            current, {"guest": Progress.PLANNED, "attachments": Progress.STARTED}
        )


def test_completed_guest_cannot_be_repaired_again_on_previous_approval():
    guest = effect("guest", owner="ordinary")
    with pytest.raises(RuntimeError, match="another effect"):
        plan(guest).validate_resume(plan(guest), {"guest": Progress.VERIFIED})


def test_matching_readback_never_cancels_uncertain_cloud_operation():
    with pytest.raises(RuntimeError, match="settle every started"):
        plan(effect()).validate_resume(plan(), {"attachments": Progress.STARTED})


@pytest.mark.parametrize("field", ["configuration_sha256", "artifact_sha256", "gateway_name"])
def test_changed_intent_requires_reviewed_successor(field):
    approved = plan(effect())
    current = replace(approved, **{field: "e" * 64})
    with pytest.raises(RuntimeError, match="reviewed successor"):
        approved.validate_resume(current, {"attachments": Progress.PLANNED})


def test_unstarted_effect_rejects_changed_predecessor():
    original = effect()
    with pytest.raises(RuntimeError, match="predecessor changed"):
        plan(original).validate_resume(
            plan(replace(original, observation_sha256="e" * 64)),
            {"attachments": Progress.PLANNED},
        )


@pytest.mark.parametrize("change", [{"owner": "replacement"}, {"disruptive": False}])
def test_child_receipt_cannot_change_effect_authority(change):
    original = effect()
    with pytest.raises(RuntimeError, match="newly discovered effects"):
        plan(original).validate_resume(
            plan(replace(original, **change)),
            {"attachments": Progress.STARTED},
            resumable_children=frozenset({"attachments"}),
        )


def test_effect_dependencies_are_ordered_and_approval_is_stable():
    first = effect("guest", owner="ordinary")
    last = effect(requires=("guest",))
    approved = plan(first, last)
    assert CommandPlan.parse(approved.mapping()).approval == approved.approval
    with pytest.raises(ValueError, match="ordered prerequisites"):
        plan(last, first)
    with pytest.raises(ValueError, match="unique"):
        plan(first, first)


def test_result_preserves_degraded_connectivity_with_converged_local_state():
    result = Result(
        Outcome.UNCHANGED, Evidence.READY, Evidence.READY, Evidence.READY, Evidence.DEGRADED
    )
    assert result.mapping()["connectivity"] == "degraded"
    assert result.mapping()["outcome"] == "unchanged"
