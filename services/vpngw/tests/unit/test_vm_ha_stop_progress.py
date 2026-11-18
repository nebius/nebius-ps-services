"""Stop-fencing contracts composed from the real policy, runtime and cloud adapter."""

import json
from dataclasses import replace

import pytest
from test_vm_ha_controller import _peer, _snapshot
from test_vm_ha_runtime_ports import _runtime_config
from test_vm_manager_fencing import _allocation, _instance

from nebius_vpngw.agent.main import VMHAControllerRuntime
from nebius_vpngw.agent.vm_ha import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import BoundCloudRuntime
from nebius_vpngw.agent.vm_ha_checkpoint import (
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ComputeState,
    ControllerCheckpoint,
    DataPlaneMode,
    FormerOwnerStopPending,
    HAState,
    RecoverableController,
    VMHAController,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationOwner,
    PendingHACloudOperation,
    VMHACloudAdapter,
)
from nebius_vpngw.schema import VMHARuntimeBinding


class Checkpoints:
    """Round-trip every checkpoint through the production persisted format."""

    def __init__(self):
        self.save(
            ControllerCheckpoint(
                state=HAState.SUSPECT,
                suspect_since=80,
                suspicion_boot_id="local-boot",
            )
        )

    def load(self):
        return controller_checkpoint_from_dict(json.loads(self.raw))

    def save(self, checkpoint):
        self.raw = json.dumps(controller_checkpoint_to_dict(checkpoint))


class StopModel:
    """Only external Compute responses and effect ports are simulated."""

    def __init__(self, tmp_path, initial_state):
        self.binding = VMHARuntimeBinding.model_validate(
            _runtime_config(tmp_path)["runtime_binding"]
        )
        self.local = next(node for node in self.binding.nodes if node.role.value == "passive")
        self.peer = next(node for node in self.binding.nodes if node.node_id != self.local.node_id)
        self.owner = AllocationOwner(self.peer.compute_id, "eth0")
        self.former_state = initial_state
        self.now = 100
        self.stop_requests = []
        self.sleeps = []
        self.allocation_writes = []
        self.actions = []
        self.provider_pending = False
        self.provider_polls = []
        self.mode = DataPlaneMode.PASSIVE
        self.guard_boot = "local-boot"
        self.checkpoints = Checkpoints()
        self.restart_controller()

    def read_instance(self, instance_id):
        former = instance_id == self.peer.compute_id
        assert former or instance_id == self.local.compute_id
        allocation = ""
        if self.owner is not None and self.owner.instance_id == instance_id:
            allocation = self.binding.shared_allocation_id
        return _instance(
            self.former_state if former else "RUNNING",
            resource_version="9" if former else "3",
            attachments=(("eth0", allocation),),
        )

    def request_stop(self, instance_id, operation_id):
        assert instance_id == self.peer.compute_id
        self.stop_requests.append((instance_id, operation_id))
        self.former_state = "STOPPING"

    def set_alias(self, instance_id, interface, allocation_id, present, operation_id):
        assert operation_id
        self.allocation_writes.append((instance_id, interface, allocation_id, present))
        assert self.former_state == "STOPPED"
        assert instance_id == self.peer.compute_id and not present
        self.owner = None

    def observe(self):
        digests = DigestSet(
            self.binding.configuration_digest,
            self.binding.static_routes_digest,
            self.binding.bgp_policy_digest,
        )
        return _snapshot(
            now=self.now,
            cluster_id=self.binding.cluster_id,
            local_generation_id=self.binding.generation_id,
            local_digests=digests,
            peer_heartbeat=replace(_peer(), cluster_id=self.binding.cluster_id, digests=digests),
            peer_received_at=80,
            cloud=self.cloud.observe(),
            data_plane_mode=self.mode,
            guard_boot_id=self.guard_boot,
            route_runtime_id=self.binding.route_runtime_id,
        )

    def apply(self, action):
        self.actions.append(action)
        if action.kind is ActionKind.STOP_FORMER_OWNER:
            self.cloud.stop_former(action)
            return
        assert action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
        self.cloud.detach_former(action)

    def restart_controller(self):
        self.adapter = VMHACloudAdapter(
            instance_reader=self.read_instance,
            instance_stopper=self.request_stop,
            allocation_reader=lambda _allocation_id: _allocation(self.owner),
            alias_allocation_setter=self.set_alias,
            attempts=6,
            poll_interval=1,
            sleeper=self.sleeps.append,
        )
        self.cloud = BoundCloudRuntime(
            self.binding,
            self.local,
            self.peer,
            self.adapter,
            poll_stop_operation=self.poll_stop,
        )
        self.runtime = RecoverableController(
            policy=VMHAController(peer_timeout_seconds=10, suspicion_seconds=5),
            snapshots=self,
            checkpoints=self.checkpoints,
            effects=self,
            before_observe=lambda checkpoint: self.cloud.settle_pending_action(
                checkpoint.pending_action
            ),
        )

    def poll_stop(self, operation_id, instance_id):
        assert instance_id == self.peer.compute_id
        self.provider_polls.append(operation_id)
        if self.provider_pending:
            raise PendingHACloudOperation("accepted Stop is not terminal")


@pytest.mark.parametrize("initial_state", ["RUNNING", "STOPPING"])
@pytest.mark.parametrize("restart_while_waiting", [False, True])
def test_stopping_spans_controller_passes_without_error_or_duplicate_stop(
    tmp_path, initial_state, restart_while_waiting
):
    model = StopModel(tmp_path, initial_state)
    pending_id = None

    # Twelve separate passes exceed the old six-poll synchronous window.
    for iteration in range(12):
        decision = model.runtime.step()
        assert decision.state is HAState.FENCING
        assert not decision.forwarding_enabled
        assert not model.sleeps, "Waiting must yield to the next pass, not sleep inside an effect"
        assert not model.allocation_writes
        assert model.former_state == "STOPPING"
        pending = model.checkpoints.load().pending_action
        if pending is not None:
            assert pending.kind is ActionKind.STOP_FORMER_OWNER
            if pending_id is None:
                pending_id = pending.operation_id
            assert pending.operation_id == pending_id
        assert len(model.stop_requests) == (1 if initial_state == "RUNNING" else 0)
        if restart_while_waiting and iteration == 5:
            model.restart_controller()
        model.now += 1

    model.former_state = "STOPPED"
    completed = model.runtime.step()

    assert completed.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert not completed.forwarding_enabled
    assert len(model.stop_requests) == (1 if initial_state == "RUNNING" else 0)
    assert len(model.allocation_writes) == 1


def test_already_stopped_owner_proceeds_to_detach_without_a_stop_request(tmp_path):
    model = StopModel(tmp_path, "STOPPED")

    decision = model.runtime.step()

    assert decision.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert not decision.forwarding_enabled
    assert not model.stop_requests
    assert len(model.allocation_writes) == 1


def test_pending_stop_recognizes_stopped_owner_after_controller_restart(tmp_path):
    model = StopModel(tmp_path, "RUNNING")
    pending = model.runtime.policy.decide(model.observe(), model.checkpoints.load())
    assert pending.action.kind is ActionKind.STOP_FORMER_OWNER
    model.checkpoints.save(pending.checkpoint)
    model.former_state = "STOPPED"
    model.restart_controller()

    decision = model.runtime.step()

    assert decision.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert not decision.forwarding_enabled
    assert not model.stop_requests
    assert len(model.allocation_writes) == 1


@pytest.mark.parametrize("mode", [DataPlaneMode.PASSIVE, DataPlaneMode.BLOCKED])
def test_accepted_stop_must_settle_even_if_compute_already_reports_stopped(tmp_path, mode):
    model = StopModel(tmp_path, "RUNNING")
    admitted = model.runtime.policy.decide(model.observe(), model.checkpoints.load())
    model.checkpoints.save(admitted.checkpoint)
    model.former_state = "STOPPED"
    model.mode = mode
    model.provider_pending = True

    for _ in range(12):
        model.restart_controller()
        decision = model.runtime.step()
        assert decision.state is HAState.FENCING
        assert decision.action is None
        assert not decision.forwarding_enabled
        assert model.checkpoints.load().pending_action == admitted.action
        assert not model.stop_requests
        assert not model.allocation_writes
        model.now += 1

    assert model.provider_polls == [admitted.action.operation_id] * 12
    model.provider_pending = False
    # A restarted blocked dataplane may need normal passive materialization
    # before transfer. This model isolates acceptance settlement from that port.
    model.mode = DataPlaneMode.PASSIVE
    completed = model.runtime.step()
    assert completed.action.kind is ActionKind.DETACH_FORMER_ATTACHMENT
    assert not completed.forwarding_enabled
    assert len(model.allocation_writes) == 1


@pytest.mark.parametrize("unsafe", ["active", "stale-guard"])
def test_pending_provider_operation_cannot_hide_a_missing_local_fence(tmp_path, unsafe):
    model = StopModel(tmp_path, "RUNNING")
    admitted = model.runtime.policy.decide(model.observe(), model.checkpoints.load())
    model.checkpoints.save(admitted.checkpoint)
    model.provider_pending = True
    if unsafe == "active":
        model.mode = DataPlaneMode.ACTIVE
    else:
        model.guard_boot = "previous-boot"

    with pytest.raises(RuntimeError, match="locally fenced"):
        model.runtime.step()
    assert not model.stop_requests
    assert not model.allocation_writes


def test_pending_stop_signal_does_not_suppress_other_effect_failures(tmp_path):
    model = StopModel(tmp_path, "STOPPED")

    def invalid_pending_signal(_action):
        raise FormerOwnerStopPending("wrong effect attempted to yield")

    model.runtime.effects = type("Effects", (), {"apply": staticmethod(invalid_pending_signal)})()
    with pytest.raises(RuntimeError, match="locally fenced"):
        model.runtime.step()


def test_service_runtime_does_not_enter_failure_guard_while_owner_is_stopping(
    tmp_path, monkeypatch
):
    model = StopModel(tmp_path, "RUNNING")
    failure_guards = []
    service = VMHAControllerRuntime(
        snapshots=model,
        effects=model,
        checkpoints=model.checkpoints,
        status_path=tmp_path / "status.json",
        guard=lambda: failure_guards.append("guard"),
    )
    service.controller.before_observe = lambda checkpoint: model.cloud.settle_pending_action(
        checkpoint.pending_action
    )
    # Status formatting has its own suite. Keep the real service step and its
    # exception/fail-closed boundary, which caused the S12 controller restart.
    monkeypatch.setattr(service, "_write_status", lambda result: result)

    for _ in range(12):
        result = service.step()
        assert result.state is HAState.FENCING
        assert not result.forwarding_enabled
        model.now += 1

    assert not failure_guards
    assert not model.sleeps
    assert not model.allocation_writes
    assert len(model.stop_requests) == 1


@pytest.mark.parametrize("state", ["ERROR", "UNKNOWN"])
def test_uncertain_former_owner_never_authorizes_transfer(tmp_path, state):
    model = StopModel(tmp_path, state)

    decision = model.runtime.step()

    assert decision.state is HAState.BLOCKED
    assert not decision.forwarding_enabled
    assert not model.stop_requests
    assert not model.allocation_writes


@pytest.mark.parametrize("state", [ComputeState.STOPPING, ComputeState.STOPPED])
def test_stopped_or_stopping_peer_is_not_healthy_passive_readiness_or_local_ownership(
    tmp_path, state
):
    model = StopModel(tmp_path, "STOPPED")
    observation = replace(model.cloud.observe(), former_owner_compute_state=state)

    # Local cleanup needs its own policy. Do not weaken these broader predicates
    # merely to work around the S12 inactive-cleanup authorization failure.
    assert not observation.passive_attachment_exact(model.local.node_id, model.peer.node_id)
    assert not observation.local_attachment_exact(model.local.node_id)
    assert not observation.transfer_complete(model.local.node_id)
