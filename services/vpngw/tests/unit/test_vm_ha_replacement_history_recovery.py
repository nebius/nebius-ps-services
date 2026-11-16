from dataclasses import replace

import pytest
from test_vm_ha_corrective import failed_release
from test_vm_ha_upgrade_recovery import checkpoint

from nebius_vpngw.deploy import vm_ha_corrective, vm_ha_upgrade_recovery
from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_passive_replacement_binding_key


def with_replacement_history(state, cycles=(1,)):
    """A later ordinary apply retains ownership, without replacement execution."""
    target = state.members[1]
    bindings = dict(state.transaction.resource_bindings)
    retired_compute, retired_disk = "retired-vm", "retired-disk"
    for index, cycle in enumerate(cycles):
        compute = target.compute_id if index == len(cycles) - 1 else f"replacement-vm-{cycle}"
        disk = target.disk_id if index == len(cycles) - 1 else f"replacement-disk-{cycle}"
        values = {
            "approval": f"{cycle:064x}",
            "retired-compute": retired_compute,
            "retired-disk": retired_disk,
            "compute": compute,
            "disk": disk,
        }
        bindings.update(
            {vm_ha_passive_replacement_binding_key(k, target.instance_name, cycle): v
             for k, v in values.items()}
        )
        retired_compute, retired_disk = compute, disk
    return replace(
        state,
        transaction=replace(state.transaction, resource_bindings=tuple(sorted(bindings.items()))),
    )


@pytest.mark.parametrize("cycles", [(1,), (2,), (1, 2, 10)])
@pytest.mark.parametrize("kind", ["corrective", "upgrade"])
def test_completed_replacement_history_allows_later_host_recovery(kind, cycles):
    state = with_replacement_history(failed_release() if kind == "corrective" else checkpoint(), cycles)
    validator = vm_ha_corrective if kind == "corrective" else vm_ha_upgrade_recovery
    before = state.to_dict()
    validator.validate_checkpoint(state)
    assert state.to_dict() == before


@pytest.mark.parametrize("kind", ["corrective", "upgrade"])
@pytest.mark.parametrize("mutation", [
    "missing-retired-disk", "missing-replacement-disk", "malformed-approval",
    "foreign-member", "different-compute", "different-disk", "raw-binding-drift",
    "active-approval", "active-effect", "standby-execution", "unknown-history",
    "disconnected-cycle",
])
def test_unsettled_or_ambiguous_replacement_cannot_use_host_recovery(kind, mutation):
    state = with_replacement_history(failed_release() if kind == "corrective" else checkpoint(), (1, 2))
    tx = state.transaction
    name = state.members[1].instance_name
    bindings = dict(tx.resource_bindings)
    def key(field, cycle=2):
        return vm_ha_passive_replacement_binding_key(field, name, cycle)

    if mutation == "missing-retired-disk":
        del bindings[key("retired-disk")]
    elif mutation == "missing-replacement-disk":
        del bindings[key("disk")]
    elif mutation == "malformed-approval":
        bindings[key("approval")] = "untrusted-approval"
    elif mutation == "foreign-member":
        bindings[key("approval").replace(name, "foreign")] = bindings.pop(key("approval"))
    elif mutation == "different-compute":
        bindings[key("compute")] = "foreign-vm"
    elif mutation == "different-disk":
        bindings[key("disk")] = "foreign-disk"
    elif mutation == "raw-binding-drift":
        bindings["compute:" + name] = "foreign-vm"
    elif mutation == "active-approval":
        tx = replace(tx, approval_digest=bindings[key("approval")])
    elif mutation == "active-effect":
        tx = replace(tx, completed_effects=(*tx.completed_effects, "replace-missing-" + name + "-intent"))
    elif mutation == "standby-execution":
        bindings["standby-replacement-disk-name:" + name] = "pending-disk"
    elif mutation == "unknown-history":
        bindings["passive-replacement-unknown:" + name] = "unknown"
    elif mutation == "disconnected-cycle":
        bindings[key("retired-disk")] = "unrelated-disk"
    state = replace(state, transaction=replace(tx, resource_bindings=tuple(sorted(bindings.items()))))
    validator = vm_ha_corrective if kind == "corrective" else vm_ha_upgrade_recovery
    with pytest.raises(ValueError):
        validator.validate_checkpoint(state)
