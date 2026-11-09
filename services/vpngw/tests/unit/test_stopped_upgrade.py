from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from test_checkpoint_upgrade import conversion  # noqa: F401
from test_vm_ha_upgrade_recovery import checkpoint

from nebius_vpngw import checkpoint_upgrade as upgrade
from nebius_vpngw import ha_repair as repair
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
from nebius_vpngw.agent.vm_ha_controller import ActionKind, ControllerAction, ControllerCheckpoint
from nebius_vpngw.deploy.stopped_upgrade import admitted, observe


def stopped_state():
    state = checkpoint()
    return replace(
        state,
        transaction=replace(
            state.transaction,
            pending_effect=None,
            completed_effects=(
                *state.transaction.completed_effects,
                "stop-upgrade-writers-node-0",
                "stop-upgrade-writers-node-1",
            ),
        ),
    )


def test_only_completed_same_operation_stop_selects_readback():
    state = stopped_state()
    artifact = dict(state.transaction.resource_bindings)["heartbeat-upgrade-artifact"]
    assert admitted(state, "node-0", artifact, resumed=True)
    assert not admitted(state, "node-0", artifact, resumed=False)
    assert not admitted(state, "node-0", "f" * 64, resumed=True)
    assert not admitted(state, "foreign", artifact, resumed=True)
    for effect in ("stop-upgrade-writers-node-0", "install-apply-lock-node-1"):
        changed = replace(
            state,
            transaction=replace(
                state.transaction,
                pending_effect=effect,
                completed_effects=tuple(
                    x for x in state.transaction.completed_effects if x != effect
                ),
            ),
        )
        assert not admitted(changed, "node-0", artifact, resumed=True)
    changed = replace(
        state,
        transaction=replace(state.transaction, pending_effect="release-node-1"),
    )
    assert not admitted(changed, "node-0", artifact, resumed=True)


@pytest.fixture
def stopped_guest(conversion, monkeypatch):  # noqa: F811
    import json

    root = conversion["state_dir"]
    monkeypatch.setattr(repair, "STATE", root)
    expected = {key: value for key, value in conversion["expected"].items() if key != "host_id"}
    digests = {"configuration": "1" * 64, "static_routes": "2" * 64, "bgp_policy": "3" * 64}
    installed = {
        "cluster_id": "cluster",
        "node": {"node_id": "node"},
        "generation": {"generation_id": "1" * 64, "digests": digests},
        "runtime_binding": {
            "shared_allocation_id": "allocation",
            "nodes": [{"node_id": "node", "compute_id": "compute"}],
        },
    }
    staged = deepcopy(installed)
    staged["generation"]["generation_id"] = expected["generation_id"]
    request = {
        "expected": expected,
        "boot_id": "boot-a",
        "allocation_id": "allocation",
        "members": {"node": "compute"},
        "units": ["controller", "timer"],
        "required_units": ["controller"],
    }
    for key, value in (("config", installed), ("staged", staged)):
        raw = yaml.safe_dump({"vm_ha": value}).encode()
        path = root / (key + ".yaml")
        repair.publish(path, raw)
        request[key + "_path"] = str(path)
        request[key + "_sha256"] = repair.sha(raw)
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "boot-a:7:enter-passive:node",
        "boot-a",
        "node",
        "allocation",
        "epoch",
        "1" * 64,
        DigestSet(**digests),
    )
    record = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=7, pending_action=action))
    repair.publish(root / "controller-checkpoint.json", json.dumps(record).encode())
    # The incident's controller projection is stale. It must neither authorize
    # continuation nor veto the independently proved stopped native state.
    repair.publish(root / "status.json", b'{"pending_operation_id":"old-disable-active"}')
    manager = SimpleNamespace(unit=lambda _: {"missing": False}, stopped=lambda _: True)
    return request, manager, record


def test_stopped_readback_preserves_exact_pending_passive_intent(stopped_guest):
    request, manager, before = stopped_guest
    result = observe(request, repair, upgrade, manager)
    assert result["state"] == "stopped" and result["boot_id"] == request["boot_id"]
    assert repair.read_json(repair.STATE / "controller-checkpoint.json") == before
    assert (
        repair.read_json(repair.STATE / "status.json")["pending_operation_id"]
        == "old-disable-active"
    )


@pytest.mark.parametrize(
    "change",
    [
        "boot",
        "config",
        "staged",
        "lock",
        "forwarding",
        "accepted",
        "active",
        "missing",
        "foreign-action",
    ],
)
def test_stopped_readback_never_substitutes_for_missing_authority(stopped_guest, change):
    import json

    request, manager, record = stopped_guest
    if change == "boot":
        request["boot_id"] = "old-boot"
    elif change in {"config", "staged"}:
        request[change + "_sha256"] = "f" * 64
    elif change == "lock":
        request["expected"]["operation_id"] = "f" * 64
    elif change == "forwarding":
        repair.FORWARDING.write_text("1")
    elif change == "accepted":
        repair.publish(repair.STATE / "accepted-cloud-operation.json", b"{}")
    elif change == "active":
        manager.stopped = lambda _: False
    elif change == "missing":
        manager.unit = lambda _: {"missing": True}
    else:
        record["pending_action"]["kind"] = "attach-candidate"
        repair.publish(repair.STATE / "controller-checkpoint.json", json.dumps(record).encode())
    with pytest.raises((RuntimeError, ValueError)):
        observe(request, repair, upgrade, manager)
