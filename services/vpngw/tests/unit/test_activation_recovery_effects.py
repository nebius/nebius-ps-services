"""An unexecuted repair must remain replayable under its original authority."""

import fcntl
import json

from tests.unit.test_vm_ha_runtime_ports import _repair_action

from nebius_vpngw.agent.main import VMHAEffectAdapter
from nebius_vpngw.agent.vm_ha.models import RepairDisposition
from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
from nebius_vpngw.agent.vm_ha_controller import ActionKind


def test_contended_repair_writes_no_receipt_and_retries_same_action(tmp_path):
    calls = []

    def command(argv, timeout):
        calls.append(argv)
        if "net.ipv4.ip_forward" in argv:
            return CommandResult(0, "1")
        return CommandResult(0, "failed" if "show" in argv else "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="a" * 64,
        runner=command,
        monotonic_clock=lambda: 10.0,
        routing_lock_path=tmp_path / "routing.lock",
    )
    runtime.state_path.write_text(
        json.dumps({"schema": "nebius-vpngw/vm-ha-data-plane-v1", "mode": "active"})
    )
    runtime.ipsec_configuration_provider = lambda: ("expected",)
    action = _repair_action(deadline=15.0, reasons=("daemon-failed:strongswan",))
    receipt = tmp_path / "receipt.json"
    events = []
    handlers = dict.fromkeys(ActionKind, lambda _action: None)
    handlers[ActionKind.REPAIR_LOCAL_DATAPLANE] = runtime.repair_local
    adapter = VMHAEffectAdapter(handlers, receipt_path=receipt, event_sink=events.append)
    with runtime.routing_lock_path.open("w") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        adapter.apply(action)
        assert not receipt.exists()
        assert not calls
    adapter.apply(action)
    assert json.loads(receipt.read_text())["operation_id"] == action.operation_id
    assert sum("restart" in call for call in calls) == 1
    assert action.repair_deadline_at == 15.0


def test_deferred_disposition_is_not_success_or_failure(tmp_path):
    failures = []
    adapter = VMHAEffectAdapter(
        dict.fromkeys(ActionKind, lambda _action: RepairDisposition.DEFERRED),
        receipt_path=tmp_path / "receipt.json",
        effect_failed=failures.append,
        event_sink=lambda _event: None,
    )
    adapter.apply(_repair_action(deadline=15.0, reasons=("daemon-failed:strongswan",)))
    assert not failures
    assert not adapter.receipt_path.exists()


def test_deadline_after_forwarding_enable_restores_fence(tmp_path):
    import pytest

    from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget
    from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode

    state = {"clock": 10.0, "forwarding": "0"}

    def runner(argv, _timeout):
        if "net.ipv4.ip_forward=1" in argv:
            state.update(clock=12.0, forwarding="1")
        elif "net.ipv4.ip_forward=0" in argv:
            state["forwarding"] = "0"
        return CommandResult(0, state["forwarding"] if "-n" in argv else "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="a" * 64,
        runner=runner,
        monotonic_clock=lambda: state["clock"],
        routing_lock_path=tmp_path / "routing.lock",
    )
    token = CURRENT.set(CommandBudget(11.0))
    try:
        with pytest.raises(RuntimeError, match="bounded local"):
            runtime._set_mode(
                _repair_action(deadline=15.0, reasons=("daemon-failed:strongswan",)),
                DataPlaneMode.ACTIVE,
            )
    finally:
        CURRENT.reset(token)
    assert state["forwarding"] == "0"
    assert json.loads(runtime.guard_path.read_text())["data_plane_mode"] == "blocked"


def test_native_handler_shares_repair_deadline_with_cloud_requests(tmp_path, monkeypatch):
    import time
    from dataclasses import replace
    from unittest.mock import Mock

    import pytest
    from tests.unit.test_vm_ha_runtime_ports import (
        FakeDataPlane,
        FakeRouteBackend,
        FakeSDK,
        MemoryReplayStore,
        _fake_credential_bundle,
        _runtime_config,
    )

    from nebius_vpngw import ha_activation
    from nebius_vpngw.agent.vm_ha.models import DigestSet
    from nebius_vpngw.agent.vm_ha.runtime import build_runtime_ports
    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_request_kwargs, wait_vm_ha_operation

    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    ports.data_plane.state_path = tmp_path / "state" / "data-plane.json"
    observed = []
    operation = Mock()

    def handler(_action):
        observed.append(vm_ha_request_kwargs())
        wait_vm_ha_operation(operation)

    ports.data_plane.enter_passive = handler
    deadline = time.monotonic() + 10
    monkeypatch.setattr(ha_activation, "command_deadline", lambda **_: deadline)
    action = replace(
        _repair_action(deadline=15.0, reasons=("daemon-failed:strongswan",)),
        kind=ActionKind.ENTER_PASSIVE,
        repair_deadline_at=None,
        repair_reasons=(),
        target_node_id=ports.local.node_id,
        allocation_id=ports.binding.shared_allocation_id,
        generation_id=ports.binding.generation_id,
        digests=DigestSet(
            ports.binding.configuration_digest,
            ports.binding.static_routes_digest,
            ports.binding.bgp_policy_digest,
        ),
    )
    try:
        ports.handlers()[action.kind](action)
        assert 0 < observed[0]["timeout"] <= 10
        assert 0 < operation.sync_wait.call_args.kwargs["timeout"] <= 10
        deadline = time.monotonic() - 1
        with pytest.raises(TimeoutError):
            ports.handlers()[action.kind](action)
        assert len(observed) == 1
    finally:
        ports.close()
