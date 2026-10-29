"""Compose production observation, HA decisions, and repair around SA changes.

Only OS command responses and cloud/peer observations are simulated. This suite
does not execute systemctl, change host routes, or prove packet delivery.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import yaml
from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import (
    _owned_cloud,
    _proven_transfer_checkpoint,
    _reconciled,
    _snapshot,
)
from tests.unit.test_vm_ha_static_selection import (
    PREFIX,
    Kernel,
    action,
    config,
    readiness,
    runtime,
)

from nebius_vpngw.agent import main as agent_main
from nebius_vpngw.agent import xfrm_manager
from nebius_vpngw.agent.vm_ha.runtime import CommandResult
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    DataPlaneMode,
    HAState,
    VMHAController,
)

pytestmark = pytest.mark.integration


def simulated_runtime(tmp_path, kernel, cfg):
    data_plane = runtime(tmp_path, kernel, cfg)
    original_runner = data_plane.runner

    def run(arguments, timeout):
        if arguments[0].endswith("swanctl"):
            # Reloading succeeds, but cannot make a failed remote peer healthy.
            kernel.calls.append(tuple(arguments))
            output = "list-sas reply {}" if "--list-sas" in arguments else ""
            return CommandResult(0, output)
        return original_runner(arguments, timeout)

    data_plane.runner = run
    data_plane.monotonic_clock = lambda: 100.0
    return data_plane


def installed_route(kernel, device):
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(xfrm_manager, "run", kernel.subprocess)
        xfrm_manager.XFRMManager()._add_static_routes(device, [PREFIX])
    assert kernel.routes[PREFIX] == device


def owner_snapshot(data_plane):
    observed = _snapshot(
        cloud=_owned_cloud(),
        data_plane_mode=DataPlaneMode.ACTIVE,
        readiness=readiness(data_plane),
    )
    checkpoint = _proven_transfer_checkpoint(observed, state=HAState.ACTIVE)
    observed = replace(
        observed,
        routes_reconciled_context=_reconciled(
            observed, ownership_incarnation=checkpoint.ownership_incarnation
        ),
    )
    return observed, checkpoint


@pytest.mark.parametrize(
    ("initial_device", "replacement_device"),
    [("xfrm0", "xfrm1"), ("xfrm1", "xfrm0")],
    ids=["neighbor-failover", "neighbor-failback"],
)
def test_active_owner_moves_static_route_after_neighbor_sa_switch(
    tmp_path, initial_device, replacement_device
):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {initial_device}
    installed_route(kernel, initial_device)
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    assert readiness(data_plane).promotion_ready

    # The remote HA pair changes owner. Local ownership and route stay intact.
    kernel.usable = {replacement_device}
    assert kernel.routes[PREFIX] == initial_device
    observed, checkpoint = owner_snapshot(data_plane)
    assert not observed.readiness.static_ready
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    decision = policy.decide(observed, checkpoint)
    assert decision.action is not None
    assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE

    data_plane.repair_local(decision.action)

    assert kernel.routes[PREFIX] == replacement_device, kernel.calls
    assert readiness(data_plane).promotion_ready
    assert kernel.forwarding == "1"
    assert not any("restart" in command for command in kernel.calls)
    acknowledged = policy.decide(
        replace(
            observed,
            now=101.0,
            readiness=readiness(data_plane),
            completed_effect_operation_id=decision.action.operation_id,
        ),
        decision.checkpoint,
    )
    assert acknowledged.action is None
    assert acknowledged.state is HAState.REPAIRING
    verified = policy.decide(
        replace(
            observed,
            now=101.25,
            readiness=readiness(data_plane),
            completed_effect_operation_id=decision.action.operation_id,
        ),
        acknowledged.checkpoint,
    )
    assert verified.state is HAState.ACTIVE
    assert verified.forwarding_enabled


def test_owner_without_a_replacement_path_reaches_forwarding_fence(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0"}
    installed_route(kernel, "xfrm0")
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    kernel.usable.clear()
    observed, checkpoint = owner_snapshot(data_plane)
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    decision = policy.decide(observed, checkpoint)
    assert decision.action is not None
    assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    data_plane.repair_local(decision.action)
    assert not readiness(data_plane).promotion_ready
    assert kernel.routes[PREFIX] == "xfrm0"

    acknowledged = policy.decide(
        replace(observed, now=101.0, completed_effect_operation_id=decision.action.operation_id),
        decision.checkpoint,
    )
    exhausted = policy.decide(
        replace(observed, now=104.1),
        acknowledged.checkpoint,
    )
    assert exhausted.action is not None
    assert exhausted.action.kind is ActionKind.DISABLE_ACTIVE
    data_plane.disable_active(exhausted.action)
    assert kernel.forwarding == "0"


@pytest.mark.xfail(
    strict=True,
    reason="Repairable mixed standby is transfer-ready but its status evidence is rejected",
)
def test_reload_then_backup_sa_can_pass_standby_admission(tmp_path):
    kernel = Kernel()
    installed_route(kernel, "xfrm1")
    # The bootstrap route was installed before UK SAs existed. The Home path
    # and backup UK path are now established, but the UK route has not moved.
    kernel.usable = {"xfrm0", "xfrm2"}
    kernel.routes["192.168.0.0/24"] = "xfrm0"
    data_plane = simulated_runtime(tmp_path, kernel, config(mixed=True))
    data_plane._write_state(action(), DataPlaneMode.PASSIVE)
    local_readiness = readiness(data_plane)
    assert local_readiness.static_route_repair_ready
    assert local_readiness.transfer_ready
    assert not local_readiness.promotion_ready

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}))
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-b",
        ComputeState.RUNNING,
        False,
        False,
        False,
        "7",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"})
    )
    controller = agent_main.build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=agent_main.VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: readiness(data_plane),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
        ),
        handlers={kind: lambda _action: None for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )
    agent_main._run_vm_ha_controller(controller, once=True, config_path=config_path)
    status = agent_main.vm_ha_status(
        state_dir=tmp_path, clock=lambda: 10.0, boot_id=lambda: "boot-a"
    )
    assert status["standby_ready"] is True, status["standby_readiness_reasons"]
