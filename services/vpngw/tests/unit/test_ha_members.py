"""Independent receipt negatives for fresh and replacement member lineage."""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.deploy import ha_members
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
)


@pytest.fixture
def members():
    nodes = tuple(
        NS(
            instance_name=f"gateway-{i}",
            node_id=f"node-{i}",
            instance_index=i,
            role="active" if i == 0 else "passive",
            compute_id=f"compute-{i}",
            disk_id=f"disk-{i}",
            binding_complete=True,
        )
        for i in range(2)
    )
    state = NS(
        members=nodes,
        transaction=NS(
            pending_effect=None,
            accepted_cloud_operation_id=None,
            observation_guard=None,
            resource_bindings={
                f"{kind}:gateway-{i}": f"{kind}-{i}"
                for i in range(2)
                for kind in ("compute", "disk")
            },
            completed_effects=("provision-gateway-1-compute",),
        ),
    )
    expected = ha_members.intent(nodes, created={"gateway-1"})
    return state, expected


def test_created_member_requires_original_creation_receipt(members):
    state, expected = members
    assert ha_members.targets(expected) == ("compute-0", "create:node-1")
    assert ha_members.verify_members(state, expected)
    state.transaction.completed_effects = ()
    assert not ha_members.verify_members(state, expected)


@pytest.mark.parametrize(
    "change", ["retained-compute", "disk", "node", "extra", "incomplete", "accepted", "guard"]
)
def test_changed_or_uncertain_member_cannot_complete(members, change):
    state, expected = members
    if change == "retained-compute":
        state.members[0].compute_id = "foreign"
        state.transaction.resource_bindings["compute:gateway-0"] = "foreign"
    elif change == "disk":
        state.members[1].disk_id = "foreign"
    elif change == "node":
        state.members[1].node_id = "foreign"
    elif change == "extra":
        state.members += (NS(instance_name="foreign"),)
    elif change == "incomplete":
        state.members[1].binding_complete = False
    elif change == "accepted":
        state.transaction.accepted_cloud_operation_id = "operation"
    else:
        state.transaction.observation_guard = object()
    assert not ha_members.verify_members(state, expected)


def test_replacement_receipt_cannot_use_a_later_cycle(members):
    state, _expected = members
    replacement = NS(
        target_instance_name="gateway-1",
        replacement_cycle=1,
        approval_digest="a" * 64,
        retired_compute_id="retired-compute",
        retired_disk_id="retired-disk",
    )
    expected = ha_members.intent(state.members, created={"gateway-1"}, replacement=replacement)
    values = {
        "approval": "a" * 64,
        "retired-compute": "retired-compute",
        "retired-disk": "retired-disk",
        "compute": "compute-1",
        "disk": "disk-1",
    }
    state.transaction.resource_bindings.update(
        {
            vm_ha_passive_replacement_binding_key(kind, "gateway-1", 1): value
            for kind, value in values.items()
        }
    )
    state.transaction.completed_effects = (
        vm_ha_missing_standby_replacement_effect("gateway-1", 1, "create-compute"),
    )
    assert ha_members.verify_members(state, expected)
    before = deepcopy(state.transaction.resource_bindings)
    state.transaction.resource_bindings.update(
        {
            vm_ha_passive_replacement_binding_key(kind, "gateway-1", 2): value
            for kind, value in values.items()
        }
    )
    assert not ha_members.verify_members(state, expected)
    state.transaction.resource_bindings = before
    state.transaction.resource_bindings[
        vm_ha_passive_replacement_binding_key("approval", "gateway-1", 1)
    ] = "b" * 64
    assert not ha_members.verify_members(state, expected)
