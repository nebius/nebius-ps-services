"""Upgrade settlement preserves original authority and cannot plan fresh work."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.unit.test_vm_ha_controller import _owned_cloud
from tests.unit.test_vm_ha_runtime_ports import (
    FakeDataPlane,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _fake_credential_bundle,
    _runtime_config,
    build_runtime_ports,
)

from nebius_vpngw import ha_repair
from nebius_vpngw import ha_route_settlement as settlement
from nebius_vpngw.agent.vm_ha.store import atomic_write_json
from nebius_vpngw.deploy.vm_ha_routes import (
    ManagedRouteKind,
    RouteMutation,
    RouteMutationKind,
    RouteReconciliationContext,
)


@pytest.fixture
def predecessor(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    config = _runtime_config(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"vm_ha": config}))
    path.chmod(0o600)
    boot = tmp_path / "boot"
    boot.write_text("boot-a")
    forward = tmp_path / "forwarding"
    forward.write_text("0")
    monkeypatch.setattr(settlement, "CONFIG", path)
    monkeypatch.setattr(ha_repair, "STATE", state)
    monkeypatch.setattr(ha_repair, "BOOT", boot)
    monkeypatch.setattr(ha_repair, "FORWARDING", forward)
    monkeypatch.setattr(settlement, "_require_writers_stopped", lambda: None)
    expected = {
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": "d" * 64,
    }
    atomic_write_json(
        state / "apply.lock",
        {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            **expected,
        },
    )
    atomic_write_json(
        state / "guard.json", {"guard_boot_id": "boot-a", "data_plane_mode": "passive"}
    )
    effects = []

    class Backend(FakeRouteBackend):
        def _accepted_operation(self, action, mutation, submit):
            return submit()

        def apply_mutation(self, mutation):
            return self._accepted_operation("create", mutation, lambda: self.submit(mutation))

        def submit(self, mutation):
            effects.append(mutation)
            return "route-a"

    ports = build_runtime_ports(
        config,
        state_dir=state,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda sdk: Backend(),
        data_plane_factory=FakeDataPlane,
    )
    cloud = replace(
        _owned_cloud(),
        allocation_id="allocation-a",
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        ownership_epoch="7",
    )
    ports.cloud.observe = lambda: cloud
    ports.routes.reconcile = lambda action: pytest.fail("settlement planned fresh work")
    ports.routes.data_plane.destination_protection = None
    monkeypatch.setattr(
        "nebius_vpngw.agent.vm_ha.runtime.build_runtime_ports", lambda *a, **kw: ports
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.1.0.0/16",
        route_kind=ManagedRouteKind.STATIC,
        cluster_id="cluster-a",
        allocation_id="allocation-a",
        route_target=ports.binding.route_targets[0],
    )
    context = RouteReconciliationContext(
        operation_id="original-route-op",
        cluster_id="cluster-a",
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest=ports.binding.static_routes_digest,
        bgp_policy_digest=ports.binding.bgp_policy_digest,
        ownership_incarnation=2,
    )
    ports.routes.store.save_pending_mutation(mutation, context)
    return SimpleNamespace(
        state=state,
        expected=expected,
        ports=ports,
        mutation=mutation,
        effects=effects,
        config=path,
        forward=forward,
    )


def test_upgrade_finishes_only_original_intent_and_records_settlement(predecessor):
    p = predecessor
    settlement.settle_locked(p.expected, artifact="e" * 64)
    assert p.effects == [p.mutation]
    assert p.ports.routes.store.load_pending_mutation() is None
    assert set(p.ports.routes.store.load_ledger()) == {"route-a"}
    receipt = json.loads((p.state / "route-settlement.json").read_bytes())
    assert receipt["context"]["operation_id"] == "original-route-op"
    assert receipt["operation"] == p.expected["operation_id"]
    assert p.forward.read_text() == "0"


@pytest.mark.parametrize("drift", ["owner", "forwarding", "lock", "other-writer"])
def test_upgrade_blocks_before_cloud_effect_on_authority_drift(predecessor, drift):
    from dataclasses import replace

    p = predecessor
    before = (p.state / "route-pending.json").read_bytes()
    if drift == "owner":
        p.ports.cloud.observe = lambda: replace(_owned_cloud(), observed_owner_node_id="node-b")
    elif drift == "forwarding":
        p.forward.write_text("1")
    elif drift == "lock":
        p.expected["operation_id"] = "f" * 64
    else:
        (p.state / "accepted-cloud-operation.json").write_text("{}")
    with pytest.raises(RuntimeError):
        settlement.settle_locked(p.expected, artifact="e" * 64)
    assert not p.effects
    assert (p.state / "route-pending.json").read_bytes() == before
    assert not (p.state / "route-settlement.json").exists()


def test_settlement_rechecks_fencing_immediately_before_submission(predecessor):
    p = predecessor
    original = p.ports.routes.backend._accepted_operation

    def lose_fence(action, mutation, submit):
        p.forward.write_text("1")
        return original(action, mutation, submit)

    p.ports.routes.backend._accepted_operation = lose_fence
    with pytest.raises(RuntimeError, match="fencing"):
        settlement.settle_locked(p.expected, artifact="e" * 64)
    assert not p.effects
    assert p.ports.routes.store.load_pending_mutation() is not None
