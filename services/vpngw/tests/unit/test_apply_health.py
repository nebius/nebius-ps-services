import copy
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.agent.main import _project_current_vm_ha_status
from nebius_vpngw.deploy.apply_health import ha_convergence
from nebius_vpngw.deploy.apply_plan import Evidence


def snapshot(*, coverage="ready", policy="enabled"):
    members = []
    for node, mode in (("node-a", "active"), ("node-b", "passive")):
        projection = {
            "boot_id": "boot-" + node,
            "observed_at": 10.0,
            "data_plane_mode": mode,
            "local_runtime": "ready",
            "authority_ready": True,
            "quiescent": True,
            "coverage": coverage,
            "fresh": True,
        }
        record = {
            "guard_boot_id": projection["boot_id"],
            "data_plane_mode": mode,
            "observed_owner_node_id": "node-a",
            "pending_operation_id": None,
            "apply_locked": False,
            "transfer_inhibition_operation_id": None,
            "apply_convergence": projection,
            "generation_id": "g",
            "digests": {"configuration": "g"},
            "auto_healing": {"state": policy, "peer_agrees": True, "accepted_start": False},
            "mtls": {
                "state": "healthy",
                "operation_id": None,
                "inhibited": False,
                "epoch": 1,
                "certificate_fingerprint": node,
            },
            "heartbeat": {"peer_v6_fresh": True, "peer_health_fresh": True},
            "promotion_ready": coverage == "ready",
            "standby_ready": coverage == "ready",
        }
        members.append(NS(condition="exact", record=record, node_id=node))
    return NS(
        authority=NS(
            lifecycle="active",
            condition="exact" if coverage == "ready" else "blocked",
            ownership_condition="exact",
            ownership_digest="cloud-identity",
            operation_id=None,
            owner_node_id="node-a",
            member_compute_states=(("node-a", "running"), ("node-b", "running")),
        ),
        lifecycle_state=NS(record_sha256="lifecycle-identity"),
        members=tuple(members),
        view=NS(overall="HEALTHY" if coverage == "ready" else "BLOCKED"),
    )


@pytest.mark.parametrize("coverage", ["ready", "degraded", "unknown"])
@pytest.mark.parametrize("policy", ["enabled", "disabled"])
def test_remote_loss_or_maintenance_never_selects_local_redeployment(coverage, policy):
    evidence = ha_convergence(snapshot(coverage=coverage, policy=policy))
    assert evidence.local_runtime is Evidence.READY
    assert evidence.connectivity.value == coverage


def test_path_churn_does_not_change_safety_comparison():
    first = snapshot()
    second = snapshot(coverage="degraded")
    second.members[0].record["apply_convergence"]["observed_at"] += 1
    second.members[0].record["heartbeat"]["peer_sequence"] = 999
    assert ha_convergence(first).identity == ha_convergence(second).identity


@pytest.mark.parametrize(
    "field,value",
    [
        ("fresh", False),
        ("authority_ready", False),
        ("quiescent", False),
        ("boot_id", "another-boot"),
        ("data_plane_mode", "passive"),
        ("local_runtime", "unknown"),
    ],
)
def test_unknown_or_stale_projection_blocks_instead_of_redeploying(field, value):
    inspected = snapshot(coverage="degraded")
    inspected.members[0].record["apply_convergence"][field] = value
    with pytest.raises(RuntimeError):
        ha_convergence(inspected)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ownership_condition", "unknown"),
        ("ownership_condition", "blocked"),
        ("operation_id", "accepted-operation"),
        ("owner_node_id", "foreign-node"),
        ("member_compute_states", (("node-a", "running"), ("node-b", "stopped"))),
    ],
)
def test_unknown_cloud_or_unsettled_effects_cannot_authorize_noop(field, value):
    inspected = snapshot()
    setattr(inspected.authority, field, value)
    with pytest.raises(RuntimeError):
        ha_convergence(inspected)


def test_confirmed_local_defect_can_select_bounded_existing_apply_repair():
    inspected = snapshot()
    inspected.members[0].record["apply_convergence"]["local_runtime"] = "degraded"
    assert ha_convergence(inspected).local_runtime is Evidence.DEGRADED


@pytest.mark.parametrize("change", ["policy", "mtls", "heartbeat", "accepted-start"])
def test_peer_administration_must_be_committed_and_quiescent(change):
    inspected = snapshot()
    member = inspected.members[1].record
    if change == "policy":
        member["auto_healing"]["state"] = "disabled"
    elif change == "accepted-start":
        member["auto_healing"]["accepted_start"] = True
    elif change == "mtls":
        member["mtls"]["inhibited"] = True
    else:
        member["heartbeat"]["peer_v6_fresh"] = False
    with pytest.raises(RuntimeError):
        ha_convergence(inspected)


@pytest.mark.parametrize(
    "now,boot,mode,fresh",
    [
        (10.0, "boot-node-a", "active", True),
        (20.0, "boot-node-a", "active", False),
        (9.0, "boot-node-a", "active", False),
        (10.0, "boot-node-a", "passive", False),
        (10.0, "another-boot", "active", False),
    ],
)
def test_guest_projection_requires_current_guard_boot_and_fresh_controller(
    tmp_path, now, boot, mode, fresh
):
    record = copy.deepcopy(snapshot().members[0].record)
    record["promotion_ready"] = record["standby_ready"] = False
    record["controller_ready_boot_id"] = None
    projected = _project_current_vm_ha_status(
        record,
        state_dir=tmp_path,
        guard={"guard_boot_id": boot, "data_plane_mode": mode},
        current_boot_id=boot,
        clock=lambda: now,
    )
    assert bool((projected.get("apply_convergence") or {}).get("fresh")) is fresh
    # Observational convergence never grants the existing agent writer gate.
    assert projected["controller_ready_boot_id"] is None
