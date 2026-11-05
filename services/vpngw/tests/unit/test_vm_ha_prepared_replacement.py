from dataclasses import replace

import pytest

from nebius_vpngw.deploy import vm_ha_prepared_replacement as prepared
from nebius_vpngw.deploy.replacement import Member, Plan
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    VMHAMigrationTransaction,
    vm_ha_effective_resource_bindings,
)


def active_state():
    members = tuple(
        VMHALifecycleMember(
            instance_index=index,
            instance_name=f"gateway-{index}",
            node_id=f"node-{index}",
            role="active" if index == 0 else "passive",
            compute_id=f"compute-{index}",
            network_interface_name="eth0",
            public_ip=f"192.0.2.{index + 10}",
            compute_revision="10",
            disk_id=f"disk-{index}",
            network_interface_subnet_id="subnet-test",
            primary_allocation_id=f"private-{index}",
            public_allocation_id=f"public-{index}",
            alias_allocation_ids=("shared",) if index == 0 else (),
        )
        for index in range(2)
    )
    transaction = VMHAMigrationTransaction(
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest="a" * 64,
        desired_state_digest="b" * 64,
        current_state_digest="c" * 64,
        checkpoint="active",
        pending_effect=None,
        completed_effects=(),
        resource_bindings=(),
        revision=1,
        predecessor_sha256=None,
    )
    state = VMHALifecycleState(
        status=VMHALifecycleStatus.ACTIVE,
        project_id="project-test",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_id="shared",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        route_runtime_id="route-runtime",
        route_targets=("table:10.0.0.0/8",),
        transaction=transaction,
    )
    return replace(
        state,
        transaction=replace(
            transaction, resource_bindings=tuple(sorted(prepared.bindings(state, members).items()))
        ),
    )


def plan(state):
    return Plan(
        project_id=state.project_id,
        gateway_name=state.gateway_name,
        operation_id="d" * 64,
        image_id="image-frozen",
        desired={"platform": "cpu-d3", "preset": "8vcpu-32gb"},
        configuration_sha256="e" * 64,
        artifact_sha256="f" * 64,
        members=tuple(
            Member(
                logical_name=member.instance_name,
                compute_id=member.compute_id,
                physical_name=member.instance_name,
                revision=int(member.compute_revision),
                disk_id=member.disk_id,
                owned_disk=False,
                compute_sha256="1" * 64,
                authorization_sha256="2" * 64,
                configured_policy_sha256="3" * 64,
                security_groups=(),
                public_allocations=(member.public_allocation_id,),
                primary_allocations=(member.primary_allocation_id,),
                candidate_name=member.instance_name + "-next",
                candidate_disk_name=member.instance_name + "-next-boot",
            )
            for member in reversed(state.members)
        ),
        routes=(),
        vm_ha=True,
        observed_owner="gateway-0",
    ).mapping()


def replacement_member(old):
    return replace(
        old,
        compute_id=old.compute_id + "-next",
        disk_id=old.disk_id + "-next",
        primary_allocation_id=old.primary_allocation_id + "-next",
        compute_revision="11",
    )


def membership_evidence(old, new, owner):
    return {
        "kind": "membership",
        "retired_compute_id": old.compute_id,
        "retired_state": "stopped",
        "candidate_compute_id": new.compute_id,
        "candidate_disk_id": new.disk_id,
        "owner_compute_id": owner.compute_id,
    }


def test_existing_stopped_nonowner_commits_then_promotes_before_second_member(tmp_path):
    previous = active_state()
    config = tmp_path / "gateway.yaml"
    config.write_text("version: 1\n")
    store = VMHALifecycleStore(config)
    store.write_verified(previous)
    state = prepared.start(
        previous, plan(previous), current_members=previous.members, desired_digest="9" * 64
    )
    store.write_verified(state, predecessor_sha256=previous.record_sha256)
    owner, old = state.members
    candidate = replacement_member(old)
    successor = prepared.advance(
        state, members=(owner, candidate), evidence=membership_evidence(old, candidate, owner)
    )
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    state = successor
    # Canonical promotion retains both member identities and transfers only the shared alias.
    former = replace(owner, alias_allocation_ids=(), compute_revision="15")
    candidate = replace(candidate, alias_allocation_ids=("shared",), compute_revision="16")
    successor = prepared.advance(
        state,
        members=(former, candidate),
        evidence={
            "kind": "promotion",
            "owner_compute_id": candidate.compute_id,
            "canonical_promotion_sha256": "8" * 64,
        },
    )
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    state = successor
    replacement = replacement_member(former)
    successor = prepared.advance(
        state,
        members=(replacement, candidate),
        evidence=membership_evidence(former, replacement, candidate),
    )
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    state = successor
    successor = prepared.advance(
        state,
        members=state.members,
        evidence={
            "kind": "complete",
            "verified_member_ids": sorted(member.compute_id for member in state.members),
        },
    )
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    assert successor.status is VMHALifecycleStatus.ACTIVE
    effective = vm_ha_effective_resource_bindings(dict(successor.transaction.resource_bindings))
    assert effective["compute:gateway-0"] == "compute-0-next"
    assert effective["compute:gateway-1"] == "compute-1-next"
    assert effective["shared-allocation-owner-compute"] == "compute-1-next"
    assert not any(key.startswith(prepared.PREFIX) for key in effective)


@pytest.mark.parametrize(
    "drift", ["running-old", "replace-owner", "changed-public", "different-disk-proof"]
)
def test_membership_rejects_unfenced_or_unapproved_rebinding(drift):
    previous = active_state()
    state = prepared.start(
        previous, plan(previous), current_members=previous.members, desired_digest="9" * 64
    )
    owner, old = state.members
    new = replacement_member(old)
    evidence = membership_evidence(old, new, owner)
    members = (owner, new)
    if drift == "running-old":
        evidence["retired_state"] = "running"
    elif drift == "replace-owner":
        members = (replacement_member(owner), old)
    elif drift == "changed-public":
        members = (owner, replace(new, public_allocation_id="foreign-public"))
    else:
        evidence["candidate_disk_id"] = "foreign-disk"
    with pytest.raises(ValueError):
        prepared.advance(state, members=members, evidence=evidence)


def test_pending_staged_replacement_cannot_be_superseded_by_an_unrelated_apply():
    previous = active_state()
    state = prepared.start(
        previous, plan(previous), current_members=previous.members, desired_digest="9" * 64
    )
    foreign = replace(
        state,
        transaction=replace(
            state.transaction, operation_id="foreign-operation", approval_kind="migration"
        ),
    )
    with pytest.raises(ValueError, match="Resume"):
        prepared.validate_successor(state, foreign)
