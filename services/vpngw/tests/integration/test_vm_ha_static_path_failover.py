"""Compose production observation, HA decisions, and repair around SA changes.

Only OS command responses and cloud/peer observations are simulated. This suite
does not execute systemctl, change host routes, or prove packet delivery.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import (
    _owned_cloud,
    _peer,
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
from nebius_vpngw.agent.vm_ha.heartbeat import HeartbeatSnapshots
from nebius_vpngw.agent.vm_ha.runtime import BoundRouteRuntime, CommandResult, VMHARuntimePorts
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerCheckpoint,
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
    assert acknowledged.state is HAState.ACTIVE
    assert acknowledged.checkpoint.repair_attempt is None
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


def test_preferred_tunnel_recovery_repairs_route_while_forwarding_is_healthy(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0", "xfrm1"}
    kernel.routes = {PREFIX: "xfrm1"}
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observed, checkpoint = owner_snapshot(data_plane)
    assert observed.readiness.promotion_ready
    assert observed.readiness.blocked_reasons == ()
    assert observed.readiness.static_route_repair_required
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)

    decision = policy.decide(observed, checkpoint)

    assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert "static-route-repair-required" in decision.action.repair_reasons
    kernel.calls.clear()
    data_plane.repair_local(decision.action)
    assert kernel.routes == {PREFIX: "xfrm0"}
    assert kernel.forwarding == "1"
    assert data_plane.mode() is DataPlaneMode.ACTIVE
    assert not any("restart" in command or "reload" in command for command in kernel.calls)
    completed = policy.decide(
        replace(
            observed,
            now=101.0,
            readiness=readiness(data_plane),
            completed_effect_operation_id=decision.action.operation_id,
        ),
        decision.checkpoint,
    )
    assert completed.state is HAState.ACTIVE
    assert completed.forwarding_enabled
    assert completed.action is None
    assert completed.checkpoint.repair_attempt is None


def test_successive_remote_tunnel_switches_do_not_consume_a_gateway_failure_budget(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0"}
    kernel.routes = {PREFIX: "xfrm0"}
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observed, checkpoint = owner_snapshot(data_plane)
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    now = 100.0
    for device in ("xfrm1", "xfrm0", "xfrm1"):
        kernel.usable = {device}
        observed = replace(observed, now=now, readiness=readiness(data_plane))
        decision = policy.decide(observed, checkpoint)
        assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
        data_plane.repair_local(decision.action)
        completed = policy.decide(
            replace(
                observed,
                now=now + 0.25,
                readiness=readiness(data_plane),
                completed_effect_operation_id=decision.action.operation_id,
            ),
            decision.checkpoint,
        )
        assert completed.action is None
        assert completed.state is HAState.ACTIVE
        assert completed.checkpoint.repair_attempt is None
        assert kernel.routes[PREFIX] == device
        assert kernel.forwarding == "1"
        checkpoint = completed.checkpoint
        now += 1.0
    assert not any("restart" in command or "reload" in command for command in kernel.calls)


def test_new_route_target_gets_its_own_attempt_before_previous_repair_is_observed(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm1"}
    kernel.routes = {PREFIX: "xfrm0"}
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observed, checkpoint = owner_snapshot(data_plane)
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    first = policy.decide(observed, checkpoint)
    data_plane.repair_local(first.action)
    # The neighbor changes back before the controller observes the first repair.
    kernel.usable = {"xfrm0"}
    second = policy.decide(
        replace(
            observed,
            now=101.0,
            readiness=readiness(data_plane),
            completed_effect_operation_id=first.action.operation_id,
        ),
        first.checkpoint,
    )
    assert second.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert second.action.operation_id != first.action.operation_id
    assert second.checkpoint.repair_attempt.failure_fingerprint != (
        first.checkpoint.repair_attempt.failure_fingerprint
    )
    data_plane.repair_local(second.action)
    assert kernel.routes[PREFIX] == "xfrm0"
    assert kernel.forwarding == "1"


def test_failed_preference_update_retries_without_stopping_working_backup(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0", "xfrm1"}
    kernel.routes = {PREFIX: "xfrm1"}
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observed, checkpoint = owner_snapshot(data_plane)
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    kernel.fail_write = True
    decision = policy.decide(observed, checkpoint)
    for now in (101.0, 106.0, 111.0):
        assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
        data_plane.repair_local(decision.action)
        acknowledged = policy.decide(
            replace(
                observed,
                now=now,
                readiness=readiness(data_plane),
                completed_effect_operation_id=decision.action.operation_id,
            ),
            decision.checkpoint,
        )
        assert kernel.forwarding == "1"
        assert kernel.routes[PREFIX] == "xfrm1"
        assert data_plane.mode() is DataPlaneMode.ACTIVE
        # A successful route write is still required before retiring this goal.
        assert acknowledged.checkpoint.repair_attempt is not None
        if acknowledged.action is None:
            decision = policy.decide(
                replace(observed, now=now + 5.0, readiness=readiness(data_plane)),
                acknowledged.checkpoint,
            )
        else:
            decision = acknowledged
    kernel.fail_write = False
    data_plane.repair_local(decision.action)
    assert kernel.routes[PREFIX] == "xfrm0"
    assert kernel.forwarding == "1"
    assert not any("restart" in command or "reload" in command for command in kernel.calls)


def test_remote_tunnel_switch_recovers_before_standby_suspicion_expires(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm1"}
    kernel.routes = {PREFIX: "xfrm0"}
    data_plane = simulated_runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observed, checkpoint = owner_snapshot(data_plane)
    owner = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    standby = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    suspicion = standby.decide(
        _snapshot(peer_heartbeat=replace(_peer(), local_forwarding_ready=False)),
        ControllerCheckpoint(),
    )
    assert suspicion.state is HAState.SUSPECT
    decision = owner.decide(observed, checkpoint)
    data_plane.repair_local(decision.action)
    ports = SimpleNamespace(
        data_plane=data_plane,
        routes=connection_routes(data_plane),
        monotonic_clock=lambda: 101.0,
        heartbeat_snapshots=HeartbeatSnapshots(),
    )
    # This fixture has only the one configured static destination.
    ports.routes.static_manifest = SimpleNamespace(prefixes=frozenset({PREFIX}))
    ports.routes.connection_prefixes = {"uk-us": {PREFIX}}
    VMHARuntimePorts.sample_local_health(ports, cancelled=lambda: False)
    health, _ownership = ports.heartbeat_snapshots.read()
    assert health.local_forwarding_ready is True
    recovered = standby.decide(
        _snapshot(
            now=101.0,
            peer_received_at=101.0,
            peer_heartbeat=replace(_peer(), sequence=2, local_forwarding_ready=True),
        ),
        suspicion.checkpoint,
    )
    assert recovered.state is HAState.NORMAL
    assert recovered.checkpoint.suspect_since is None
    assert recovered.action is None
    assert kernel.forwarding == "1"


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


def connection_routes(data_plane):
    routes = object.__new__(BoundRouteRuntime)
    routes.data_plane = data_plane
    routes.static_manifest = SimpleNamespace(prefixes=frozenset({PREFIX, "192.168.0.0/24"}))
    routes.connection_prefixes = {"uk-us": {PREFIX}, "other": {"192.168.0.0/24"}}
    routes.connection_sessions = {}
    routes.bgp_policies = []
    routes.expected_import_policy_digest = hashlib.sha256(b"{}").hexdigest()
    return routes


def connection_readiness(data_plane):
    return connection_routes(data_plane).readiness()


def multi_connection_runtime(tmp_path, kernel):
    cfg = config(mixed=True)
    cfg["connections"][0]["routing_mode"] = "static"
    cfg["connections"][0]["remote_prefixes"] = ["192.168.0.0/24"]
    data_plane = simulated_runtime(tmp_path, kernel, cfg)
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    return data_plane


def test_one_failed_connection_keeps_other_connection_forwarding_without_takeover(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0"}
    kernel.routes = {"192.168.0.0/24": "xfrm0", PREFIX: "xfrm1"}
    data_plane = multi_connection_runtime(tmp_path, kernel)
    observed, checkpoint = owner_snapshot(data_plane)
    local = connection_readiness(data_plane)
    assert not local.promotion_ready
    assert local.forwarding_ready
    assert local.degraded_connections == ("uk-us",)
    observed = replace(observed, readiness=local)
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    for now in (100.0, 110.0, 1000.0):
        decision = policy.decide(replace(observed, now=now), checkpoint)
        assert decision.state is HAState.DEGRADED_PATH
        assert decision.action is None
        assert decision.forwarding_enabled
        checkpoint = decision.checkpoint
    assert kernel.forwarding == "1"
    assert kernel.routes["192.168.0.0/24"] == "xfrm0"
    assert not any("restart" in command for command in kernel.calls)


def test_sampler_advertises_partial_forwarding_so_standby_does_not_take_over(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0"}
    kernel.routes = {"192.168.0.0/24": "xfrm0", PREFIX: "xfrm1"}
    data_plane = multi_connection_runtime(tmp_path, kernel)
    ports = SimpleNamespace(
        data_plane=data_plane,
        routes=connection_routes(data_plane),
        monotonic_clock=lambda: 100.0,
        heartbeat_snapshots=HeartbeatSnapshots(),
    )
    VMHARuntimePorts.sample_local_health(ports, cancelled=lambda: False)
    sample, _ownership = ports.heartbeat_snapshots.read()
    assert sample.local_forwarding_ready is True
    assert sample.transfer_ready is False
    template = _peer()
    ports.binding = SimpleNamespace(
        cluster_id=template.cluster_id,
        generation_id=template.generation_id,
        configuration_digest=template.digests.configuration,
        static_routes_digest=template.digests.static_routes,
        bgp_policy_digest=template.digests.bgp_policy,
    )
    ports.local = SimpleNamespace(node_id=template.node_id, role=ConfiguredRole.ACTIVE)
    ports.mtls_snapshot_provider = lambda: SimpleNamespace(
        epoch=template.mtls_epoch, certificate_fingerprint=template.certificate_fingerprint
    )
    ports.auto_healing_policy_provider = lambda: ("enabled", template.auto_healing_policy_digest)
    heartbeat = VMHARuntimePorts.heartbeat(
        ports, boot_id="owner-boot", sequence=1, clock=lambda: 100.0
    )
    standby = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    decision = standby.decide(
        _snapshot(now=100.0, peer_heartbeat=heartbeat, peer_received_at=100.0),
        ControllerCheckpoint(),
    )
    assert decision.state is HAState.NORMAL
    assert decision.action is None


def test_static_route_repair_changes_only_the_affected_connection(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0", "xfrm2"}
    kernel.routes = {"192.168.0.0/24": "xfrm0", PREFIX: "xfrm1"}
    data_plane = multi_connection_runtime(tmp_path, kernel)
    observed, checkpoint = owner_snapshot(data_plane)
    observed = replace(observed, readiness=connection_readiness(data_plane))
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    decision = policy.decide(observed, checkpoint)
    assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    kernel.calls.clear()
    data_plane.repair_local(decision.action)
    writes = []
    for command in kernel.calls:
        if command[1:3] == ("route", "replace"):
            writes.append(command)
    assert len(writes) == 1
    assert writes[0][3:] == (PREFIX, "dev", "xfrm2")
    assert kernel.routes == {"192.168.0.0/24": "xfrm0", PREFIX: "xfrm2"}
    assert kernel.forwarding == "1"
    assert not any("restart" in command for command in kernel.calls)


def test_failed_scoped_route_update_does_not_fence_healthy_connection(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm0", "xfrm2"}
    kernel.routes = {"192.168.0.0/24": "xfrm0", PREFIX: "xfrm1"}
    data_plane = multi_connection_runtime(tmp_path, kernel)
    observed, checkpoint = owner_snapshot(data_plane)
    observed = replace(observed, readiness=connection_readiness(data_plane))
    policy = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    decision = policy.decide(observed, checkpoint)
    kernel.fail_write = True
    data_plane.repair_local(decision.action)
    assert kernel.forwarding == "1"
    acknowledged = policy.decide(
        replace(observed, now=101.0, completed_effect_operation_id=decision.action.operation_id),
        decision.checkpoint,
    )
    assert acknowledged.action is None
    assert acknowledged.forwarding_enabled
    retried = policy.decide(replace(observed, now=105.1), acknowledged.checkpoint)
    assert retried.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE
    assert retried.action.operation_id != decision.action.operation_id


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
    assert status["standby_tunnel_state"] == "repairable"
    assert kernel.routes[PREFIX] == "xfrm1"
    assert kernel.forwarding == "0"

    evidence_path = tmp_path / "standby-ready.json"
    evidence = json.loads(evidence_path.read_text())
    assert evidence["static_route_repair_ready"] is True
    evidence["static_route_repair_ready"] = False
    evidence_path.write_text(json.dumps(evidence))
    invalid = agent_main.vm_ha_status(
        state_dir=tmp_path, clock=lambda: 10.0, boot_id=lambda: "boot-a"
    )
    assert invalid["standby_ready"] is False
    assert "standby-ready-evidence-invalid" in invalid["standby_readiness_reasons"]

    # The existing fenced preparation selects the usable backup before forwarding.
    data_plane.prepare_candidate(action(ActionKind.PREPARE_CANDIDATE_DATAPLANE))
    assert kernel.routes[PREFIX] == "xfrm2"
    assert readiness(data_plane).promotion_ready
    assert kernel.forwarding == "0"
