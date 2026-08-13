from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_vm_ha_runtime_ports import (
    FakeCredentialBundle,
    FakeDataPlane,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _runtime_config,
)

from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import build_runtime_ports
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    LocalReadiness,
    RouteReconciliationContext,
)


def test_two_node_takeover_effects_are_ordered_and_crash_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=lambda _binding, _node: FakeCredentialBundle(),
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    trace: list[str] = []
    completed: set[str] = set()
    crashed: set[str] = set()
    state = {"stopped": False, "detached": False, "attached": False, "routes": False}

    def once(name: str, effect) -> None:
        if name not in completed:
            effect()
            completed.add(name)
            trace.append(name)
        if name not in crashed:
            crashed.add(name)
            raise RuntimeError(f"crash-after-{name}")

    def observation() -> CloudObservation:
        return CloudObservation(
            authoritative=True,
            allocation_id="allocation-a",
            observed_owner_node_id="node-a" if state["attached"] else "node-b",
            former_owner_node_id="node-b",
            former_owner_compute_state=(
                ComputeState.STOPPED if state["stopped"] else ComputeState.RUNNING
            ),
            former_attachment_absent=state["detached"],
            candidate_attachment_exact=state["attached"],
            ownership_re_read_exact=state["attached"],
            ownership_epoch="7",
        )

    ports.cloud.observe = observation
    ports.cloud.stop_former = lambda _action: once(
        "stop", lambda: state.__setitem__("stopped", True)
    )
    ports.cloud.detach_former = lambda _action: once(
        "detach", lambda: state.__setitem__("detached", True)
    )
    ports.cloud.attach_candidate = lambda _action: once(
        "attach", lambda: state.__setitem__("attached", True)
    )
    ports.cloud.confirm_candidate = lambda: once("confirm", lambda: None)
    ports.routes.reconcile = lambda _action: once(
        "routes", lambda: state.__setitem__("routes", True)
    )
    ports.routes.readiness = lambda: LocalReadiness(True, True, True, True)
    ports.routes.receipt_context = lambda: RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=DigestSet(
            "a" * 64,
            ports.binding.static_routes_digest,
            ports.binding.bgp_policy_digest,
        ),
        route_runtime_id=ports.binding.route_runtime_id,
        ownership_incarnation=1,
        operation_id="routes-op",
    )
    original_enable = ports.data_plane.enable_active
    ports.data_plane.enable_active = lambda action: once("forward", lambda: original_enable(action))
    digests = DigestSet(
        "a" * 64,
        ports.binding.static_routes_digest,
        ports.binding.bgp_policy_digest,
    )
    actions = (
        ControllerAction(
            ActionKind.STOP_FORMER_OWNER,
            "stop-op",
            "boot",
            "node-b",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.DETACH_FORMER_ATTACHMENT,
            "detach-op",
            "boot",
            "node-b",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.ATTACH_CANDIDATE,
            "attach-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            "confirm-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.RECONCILE_ROUTES,
            "routes-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
        ControllerAction(
            ActionKind.ENABLE_ACTIVE,
            "enable-op",
            "boot",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            digests,
            1,
        ),
    )

    handlers = ports.handlers()
    for action in actions:
        with pytest.raises(RuntimeError, match="crash-after"):
            handlers[action.kind](action)
        handlers[action.kind](action)

    assert trace == ["stop", "detach", "attach", "confirm", "routes", "forward"]
    assert ports.data_plane.mode().value == "active"
    ports.close()
