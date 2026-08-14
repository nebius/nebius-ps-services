from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHAEffectAdapter,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
    _manual_failback_provider,
    _read_vm_ha_config,
    _run_vm_ha_controller,
    _strict_boolean_record,
    build_default_vm_ha_controller_runtime,
    build_vm_ha_controller_runtime,
)
from nebius_vpngw.agent.routing_guard import require_vm_ha_current_boot_ready
from nebius_vpngw.agent.vm_ha.models import (
    DigestSet,
    PeerHeartbeat,
    StalePeerStateError,
    StateValidationError,
)
from nebius_vpngw.agent.vm_ha.transport import PeerTransportError
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
    RouteReconciliationContext,
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding


def _runtime_config() -> dict[str, object]:
    digest = "a" * 64
    credentials = {
        "certificate_authority": "/etc/nebius-vpngw/vm-ha/ca.crt",
        "certificate": "/etc/nebius-vpngw/vm-ha/node.crt",
        "private_key": "/etc/nebius-vpngw/vm-ha/node.key",
    }
    route_targets = (
        VMHARouteTarget(
            project_id="project-a",
            network_id="network-a",
            workload_subnet_id="subnet-a",
            route_table_id="route-table-a",
        ),
    )
    return {
        "cluster_id": "cluster-a",
        "node": {"node_id": "node-a", "role": "active"},
        "generation": {
            "generation_id": digest,
            "digests": {"configuration": digest, "static_routes": "b" * 64, "bgp_policy": "c" * 64},
        },
        "runtime_binding": {
            "cluster_id": "cluster-a",
            "shared_allocation_id": "allocation-a",
            "nodes": [
                {
                    "node_id": "node-a",
                    "role": "active",
                    "compute_id": "compute-a",
                    "network_interface_name": "eth0",
                    "peer_endpoint": "10.0.0.1:9443",
                    "credentials": credentials,
                },
                {
                    "node_id": "node-b",
                    "role": "passive",
                    "compute_id": "compute-b",
                    "network_interface_name": "eth0",
                    "peer_endpoint": "10.0.0.2:9443",
                    "credentials": credentials,
                },
            ],
            "route_targets": [target.model_dump(mode="json") for target in route_targets],
            "route_runtime_id": VMHARuntimeBinding.derive_route_runtime_id(
                "cluster-a", "allocation-a", route_targets
            ),
            "generation_id": digest,
            "configuration_digest": digest,
            "static_routes_digest": "b" * 64,
            "bgp_policy_digest": "c" * 64,
        },
    }


def test_snapshot_adapter_requires_binding_and_observes_current_guard(tmp_path: Path) -> None:
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
    cloud = CloudObservation(
        True, "allocation-a", "node-a", "node-b", ComputeState.STOPPED, True, True, True, "7"
    )
    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
        ),
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )
    snapshot = adapter.observe()
    assert (snapshot.local_node_id, snapshot.peer_node_id) == ("node-a", "node-b")
    assert snapshot.guard_boot_id == "boot-a"
    assert (
        snapshot.route_reconciliation_context.route_runtime_id
        == (
            _runtime_config()["runtime_binding"]["route_runtime_id"]  # type: ignore[index]
        )
    )
    config = _runtime_config()
    config.pop("runtime_binding")
    with pytest.raises(ValueError, match="runtime binding"):
        VMHASnapshotAdapter(config=config, providers=adapter.providers)


def test_runtime_config_includes_resolved_top_level_connections(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "connections": [{"name": "peer-a", "tunnels": []}],
                "vm_ha": _runtime_config(),
            }
        ),
        encoding="utf-8",
    )

    config = _read_vm_ha_config(path)

    assert config is not None
    assert config["connections"] == [{"name": "peer-a", "tunnels": []}]


def test_runtime_config_preserves_omitted_and_explicitly_disabled_vm_ha(tmp_path: Path) -> None:
    omitted = tmp_path / "omitted.yaml"
    disabled = tmp_path / "disabled.yaml"
    omitted.write_text("connections: []\n", encoding="utf-8")
    disabled.write_text("vm_ha:\n  enabled: false\n", encoding="utf-8")

    assert _read_vm_ha_config(omitted) is None
    assert _read_vm_ha_config(disabled) is None


def test_default_durable_gates_validate_exact_records(tmp_path: Path) -> None:
    apply_path = tmp_path / "apply.lock.json"
    assert (
        _strict_boolean_record(
            apply_path,
            schema="nebius-vpngw/vm-ha-apply-lock-v1",
            field="apply_locked",
        )
        is False
    )
    apply_path.write_text(
        json.dumps({"schema": "nebius-vpngw/vm-ha-apply-lock-v1", "apply_locked": True}),
        encoding="utf-8",
    )
    assert _strict_boolean_record(
        apply_path,
        schema="nebius-vpngw/vm-ha-apply-lock-v1",
        field="apply_locked",
    )
    apply_path.write_text('{"apply_locked":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="apply-lock"):
        _strict_boolean_record(
            apply_path,
            schema="nebius-vpngw/vm-ha-apply-lock-v1",
            field="apply_locked",
        )

    failback_path = tmp_path / "manual-failback.json"
    failback_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    requested = _manual_failback_provider(state_dir=tmp_path, config=_runtime_config())
    assert requested()
    failback_path.write_text(
        failback_path.read_text(encoding="utf-8").replace("cluster-a", "stale-cluster"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid or stale"):
        requested()


def test_effect_adapter_replays_completed_exact_operation_after_guard_restore(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    handlers = {kind: (lambda action: calls.append(action.operation_id)) for kind in ActionKind}
    adapter = VMHAEffectAdapter(handlers, receipt_path=tmp_path / "effect.json")
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "boot-a:1:enter-passive:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    adapter.apply(action)
    adapter.apply(action)
    assert calls == [action.operation_id, action.operation_id]
    assert json.loads((tmp_path / "effect.json").read_text())["operation_id"] == action.operation_id


def test_current_boot_gate_rejects_stale_status_and_preserves_non_ha(tmp_path: Path) -> None:
    boot = tmp_path / "boot-id"
    status = tmp_path / "status.json"
    guard = tmp_path / "guard.json"
    boot.write_text("boot-a\n", encoding="utf-8")
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "controller_ready_boot_id": "boot-old",
        "guard_boot_id": "boot-a",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "state": "active",
    }
    status.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="current boot"):
        require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    payload["controller_ready_boot_id"] = "boot-a"
    status.write_text(json.dumps(payload), encoding="utf-8")
    require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="current boot"):
        require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    require_vm_ha_current_boot_ready({}, status_path=tmp_path / "missing", boot_id_path=boot)


def test_service_executes_verified_controller_from_authoritative_binding(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-a",
        "node-b",
        ComputeState.STOPPED,
        True,
        True,
        True,
        "7",
    )
    calls: list[str] = []
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.BLOCKED,
            routes=lambda: None,
        ),
        handlers={kind: (lambda action: calls.append(action.operation_id)) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "normal"
    assert status["reasons"] == ["owner-must-materialize-passive-dataplane"]
    assert len(calls) == 1
    assert calls[0].endswith(":enter-passive:node-a")


def test_service_fails_closed_without_bound_runtime(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")

    def blocked(**_kwargs: object) -> None:
        raise RuntimeError("VM-HA activation BLOCKED: runtime adapters unavailable")

    monkeypatch.setattr("nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites", blocked)
    with pytest.raises(RuntimeError, match="activation BLOCKED"):
        _run_vm_ha_controller(config_path=config_path)


@pytest.mark.parametrize(
    "crash_after",
    [
        ActionKind.ENTER_PASSIVE,
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
def test_default_factory_two_node_transfer_restarts_after_every_effect(
    tmp_path: Path, monkeypatch, crash_after: ActionKind
) -> None:
    digest = "a" * 64
    clock = [0.0]
    shared: dict[str, object] = {
        "owner": "node-a",
        "former_state": ComputeState.RUNNING,
        "candidate_revision": 3,
        "confirmed": {"node-a"},
        "modes": {"node-a": DataPlaneMode.BLOCKED, "node-b": DataPlaneMode.BLOCKED},
        "receipts": {},
        "trace": [],
    }
    for node_id, role in (("node-a", "active"), ("node-b", "passive")):
        config = json.loads(json.dumps(_runtime_config()))
        config["node"] = {"node_id": node_id, "role": role}
        (tmp_path / f"{node_id}.yaml").write_text(
            yaml.safe_dump({"vm_ha": config}), encoding="utf-8"
        )

    class Cloud:
        def __init__(self, local_id: str, peer_id: str) -> None:
            self.local_id = local_id
            self.peer_id = peer_id

        def observe(self) -> CloudObservation:
            owner = shared["owner"]
            owner_is_local = owner == self.local_id
            confirmed = shared["confirmed"]
            return CloudObservation(
                authoritative=True,
                allocation_id="allocation-a",
                observed_owner_node_id=owner if isinstance(owner, str) else None,
                former_owner_node_id=self.peer_id,
                former_owner_compute_state=(
                    shared["former_state"] if self.peer_id == "node-a" else ComputeState.STOPPED
                ),
                former_attachment_absent=owner != self.peer_id,
                candidate_attachment_exact=owner_is_local,
                ownership_re_read_exact=bool(
                    owner_is_local and isinstance(confirmed, set) and self.local_id in confirmed
                ),
                ownership_epoch=str(
                    shared["candidate_revision"] if self.local_id == "node-b" else 3
                ),
            )

    class Routes:
        def __init__(self, local_id: str, binding: VMHARuntimeBinding) -> None:
            self.local_id = local_id
            self.binding = binding

        def readiness(self) -> LocalReadiness:
            return LocalReadiness(True, True, True, True)

        def receipt_context(self) -> RouteReconciliationContext | None:
            receipts = shared["receipts"]
            assert isinstance(receipts, dict)
            value = receipts.get(self.local_id)
            return value if isinstance(value, RouteReconciliationContext) else None

    class Peer:
        def observe(self) -> tuple[PeerHeartbeat | None, float | None]:
            return (
                PeerHeartbeat(
                    cluster_id="cluster-a",
                    node_id="node-a",
                    boot_id="boot-a",
                    sequence=1,
                    sent_at="2026-08-13T00:00:00Z",
                    configured_role="active",
                    observed_owner_id="node-a",
                    generation_id=digest,
                    digests=DigestSet(digest, "b" * 64, "c" * 64),
                    service_healthy=True,
                    route_ready=True,
                    promotion_ready=True,
                ),
                clock[0] - 100.0,
            )

        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"offline peer fixture {timeout_seconds}")

        def send(self, _heartbeat: PeerHeartbeat) -> None:
            return None

    class Ports:
        def __init__(self, config: dict[str, object], state_dir: Path) -> None:
            self.binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])
            node = config["node"]
            assert isinstance(node, dict)
            local_id = str(node["node_id"])
            nodes = {item.node_id: item for item in self.binding.nodes}
            self.local = nodes[local_id]
            self.peer = next(item for item in self.binding.nodes if item.node_id != local_id)
            self.cloud = Cloud(local_id, self.peer.node_id)
            self.routes = Routes(local_id, self.binding)
            self.peer_runtime = Peer()
            self.state_dir = state_dir
            self.data_plane = type("DataPlane", (), {"mode": self.data_mode})()

        def data_mode(self) -> DataPlaneMode:
            modes = shared["modes"]
            assert isinstance(modes, dict)
            value = modes[self.local.node_id]
            assert isinstance(value, DataPlaneMode)
            return value

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            modes = shared["modes"]
            trace = shared["trace"]
            assert isinstance(modes, dict) and isinstance(trace, list)
            modes[self.local.node_id] = DataPlaneMode.BLOCKED
            self.state_dir.mkdir(parents=True, exist_ok=True)
            (self.state_dir / "guard.json").write_text(
                json.dumps(
                    {
                        "schema": "nebius-vpngw/vm-ha-status-v1",
                        "guard_boot_id": boot_id,
                        "data_plane_mode": "blocked",
                    }
                ),
                encoding="utf-8",
            )
            trace.append("guard")

        def handlers(self) -> dict[ActionKind, object]:
            def effect(action: ControllerAction) -> None:
                trace = shared["trace"]
                modes = shared["modes"]
                confirmed = shared["confirmed"]
                receipts = shared["receipts"]
                assert isinstance(trace, list) and isinstance(modes, dict)
                assert isinstance(confirmed, set) and isinstance(receipts, dict)
                trace.append(action.kind)
                if action.kind is ActionKind.ENTER_PASSIVE:
                    modes[self.local.node_id] = DataPlaneMode.PASSIVE
                elif action.kind is ActionKind.DISABLE_ACTIVE:
                    modes[self.local.node_id] = DataPlaneMode.BLOCKED
                elif action.kind is ActionKind.STOP_FORMER_OWNER:
                    shared["former_state"] = ComputeState.STOPPED
                elif action.kind is ActionKind.DETACH_FORMER_ATTACHMENT:
                    shared["owner"] = None
                elif action.kind is ActionKind.ATTACH_CANDIDATE:
                    shared["owner"] = self.local.node_id
                    shared["candidate_revision"] = 4
                    confirmed.discard(self.local.node_id)
                elif action.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP:
                    confirmed.add(self.local.node_id)
                elif action.kind is ActionKind.RECONCILE_ROUTES:
                    receipts[self.local.node_id] = RouteReconciliationContext(
                        owner_node_id=self.local.node_id,
                        allocation_id=action.allocation_id,
                        ownership_epoch=action.ownership_epoch,
                        generation_id=action.generation_id,
                        digests=action.digests,
                        route_runtime_id=self.binding.route_runtime_id,
                        ownership_incarnation=action.ownership_incarnation,
                        operation_id=action.operation_id,
                    )
                elif action.kind is ActionKind.ENABLE_ACTIVE:
                    modes[self.local.node_id] = DataPlaneMode.ACTIVE

            return {kind: effect for kind in ActionKind}

        def heartbeat(self, *, boot_id: str, sequence: int, clock: float) -> PeerHeartbeat:
            owner = shared["owner"]
            return PeerHeartbeat(
                cluster_id="cluster-a",
                node_id=self.local.node_id,
                boot_id=boot_id,
                sequence=sequence,
                sent_at="2026-08-13T00:00:00Z",
                configured_role=self.local.role.value,
                observed_owner_id=owner if isinstance(owner, str) else None,
                generation_id=digest,
                digests=DigestSet(digest, "b" * 64, "c" * 64),
                service_healthy=True,
                route_ready=True,
                promotion_ready=False,
            )

        def close(self) -> None:
            return None

    def fake_ports(config: dict[str, object], *, state_dir: Path, **_kwargs: object) -> Ports:
        return Ports(config, state_dir)

    monkeypatch.setattr("nebius_vpngw.agent.main.build_runtime_ports", fake_ports)
    active = build_default_vm_ha_controller_runtime(
        config_path=tmp_path / "node-a.yaml",
        state_dir=tmp_path / "active-state",
        clock=lambda: clock[0],
        boot_id=lambda: "boot-a",
    )
    try:
        active.step()
    finally:
        active.close()

    passive_state = tmp_path / f"passive-{crash_after.value}"

    def build_passive() -> DefaultVMHAControllerRuntime:
        return build_default_vm_ha_controller_runtime(
            config_path=tmp_path / "node-b.yaml",
            state_dir=passive_state,
            clock=lambda: clock[0],
            boot_id=lambda: "boot-b",
        )

    passive = build_passive()
    crashed = False
    try:
        for _attempt in range(30):
            trace = shared["trace"]
            assert isinstance(trace, list)
            previous = len(trace)
            status = passive.step()
            clock[0] += 10.0
            if not crashed and crash_after in trace[previous:]:
                passive.close()
                passive = build_passive()
                crashed = True
            elif status.get("promotion_ready") is True:
                break
        else:
            pytest.fail(f"default VM-HA runtime did not converge after crash replay: {status}")

        trace = shared["trace"]
        modes = shared["modes"]
        assert isinstance(trace, list) and isinstance(modes, dict)
        effects = [item for item in trace if isinstance(item, ActionKind)]
        expected = [
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
            ActionKind.ATTACH_CANDIDATE,
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            ActionKind.RECONCILE_ROUTES,
            ActionKind.ENABLE_ACTIVE,
        ]
        positions = [effects.index(kind) for kind in expected]
        assert positions == sorted(positions)
        assert trace[0] == "guard"
        assert shared["former_state"] is ComputeState.STOPPED
        assert shared["owner"] == "node-b"
        assert modes["node-b"] is DataPlaneMode.ACTIVE
        assert crashed is True
    finally:
        # This may be the replacement runtime created after the simulated crash.
        passive.close()


def test_default_service_runtime_exchanges_peer_state_and_closes_guarded(tmp_path: Path) -> None:
    calls: list[str] = []

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            calls.append(f"receive:{timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            calls.append("send")

    class Ports:
        local = type("Local", (), {"role": type("Role", (), {"value": "active"})()})()
        peer_runtime = Peer()

        def heartbeat(self, **kwargs: object) -> object:
            calls.append(f"heartbeat:{kwargs['sequence']}")
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            calls.append(f"guard:{boot_id}")

        def close(self) -> None:
            calls.append("close")

    class Controller:
        def step(self) -> dict[str, object]:
            calls.append("step")
            return {}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        clock=lambda: 10.0,
    )
    runtime.step()
    runtime.close()
    runtime.close()

    assert calls[0] == "guard:boot-a"
    assert calls.count("heartbeat:0") == 1
    assert calls.count("send") == 1
    assert calls.count("step") == 1
    assert calls[-2:] == ["guard:boot-a", "close"]
    assert "receive:2.0" in calls

    persisted = json.loads((tmp_path / "sequence.json").read_text(encoding="utf-8"))
    assert persisted["next_sequence"] == 1

    calls.clear()
    restarted = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        clock=lambda: 11.0,
    )
    restarted.step()
    restarted.close(restore_guard=False)
    assert "heartbeat:1" in calls


def test_default_service_runtime_retries_bounded_peer_send(tmp_path: Path) -> None:
    sends = 0
    sleeps: list[float] = []

    class Peer:
        def send(self, _heartbeat: object) -> None:
            nonlocal sends
            sends += 1
            if sends < 3:
                raise PeerTransportError("fixture unavailable")

        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

    class Ports:
        local = type("Local", (), {"role": type("Role", (), {"value": "passive"})()})()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return {}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        peer_retry_seconds=0.05,
        sleeper=sleeps.append,
    )

    runtime.step()
    runtime.close(restore_guard=False)

    assert sends == 3
    assert sleeps == [0.05, 0.05, 0.05]


@pytest.mark.parametrize(
    "invalid_state", [StateValidationError("invalid"), StalePeerStateError("stale")]
)
def test_default_service_runtime_treats_authenticated_invalid_peer_state_as_advisory(
    tmp_path: Path, invalid_state: Exception
) -> None:
    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 2.0
            runtime.listener_stop.set()
            raise invalid_state

    class Ports:
        peer_runtime = Peer()

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        object(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
    )

    runtime._listen_for_peer_state()

    assert runtime.listener_error is None


def test_default_service_runtime_consumes_validated_manual_failback_after_restore(
    tmp_path: Path,
) -> None:
    failback_path = tmp_path / "manual-failback.json"
    failback_path.write_text("{}", encoding="utf-8")

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            return None

    class Ports:
        local = type(
            "Local",
            (),
            {"node_id": "node-a", "role": type("Role", (), {"value": "active"})()},
        )()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return {"promotion_ready": True, "observed_owner_node_id": "node-a"}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        manual_failback_path=failback_path,
    )

    runtime.step()
    runtime.close(restore_guard=False)

    assert not failback_path.exists()
    guard = json.loads((tmp_path / "guard.json").read_text(encoding="utf-8"))
    assert guard["data_plane_mode"] == "active"
