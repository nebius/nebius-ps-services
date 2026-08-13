from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.models import DigestSet, ReplayState
from nebius_vpngw.agent.vm_ha.runtime import (
    CommandResult,
    DataPlaneCommandSet,
    LocalDataPlaneObservation,
    RuntimeStateStore,
    SystemDataPlaneRuntime,
    build_runtime_ports,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    ManagedRouteKind,
    RouteMutation,
    RouteMutationKind,
    RouteReconciliationContext,
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding


@dataclass
class MemoryReplayStore:
    values: dict[str, ReplayState] = field(default_factory=dict)

    def load_replay_state(self, peer_node_id: str) -> ReplayState | None:
        return self.values.get(peer_node_id)

    def save_replay_state(self, peer_node_id: str, state: ReplayState) -> None:
        self.values[peer_node_id] = state


class FakeSDK:
    def __init__(self) -> None:
        self.close_calls = 0

    def sync_close(self) -> None:
        self.close_calls += 1


class FakeRouteBackend:
    def list_routes(self, *_args: object) -> tuple[object, ...]:
        return ()

    def apply_mutation(self, _mutation: RouteMutation) -> str | None:
        return "route-a"

    def recover_created_route(self, _mutation: RouteMutation) -> str | None:
        return None


class FakeDataPlane:
    def __init__(self, **_kwargs: object) -> None:
        self.current = DataPlaneMode.BLOCKED

    def observe(self) -> LocalDataPlaneObservation:
        return LocalDataPlaneObservation(
            service_healthy=True,
            forwarding_enabled=self.current is DataPlaneMode.ACTIVE,
            static_prefixes=frozenset(),
            configured_bgp_sessions=frozenset(),
            established_bgp_sessions=frozenset(),
            learned_bgp_prefixes=frozenset(),
            usable_xfrm_prefixes=frozenset(),
            observed_bgp_policy_digest="c" * 64,
        )

    def install_guard(self, _action: ControllerAction) -> None:
        self.current = DataPlaneMode.BLOCKED

    def enter_passive(self, _action: ControllerAction) -> None:
        self.current = DataPlaneMode.PASSIVE

    def disable_active(self, _action: ControllerAction) -> None:
        self.current = DataPlaneMode.BLOCKED

    def enable_active(self, _action: ControllerAction) -> None:
        self.current = DataPlaneMode.ACTIVE

    def mode(self) -> DataPlaneMode:
        return self.current


def _private_file(path: Path, value: str = "fixture") -> str:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _runtime_config(tmp_path: Path) -> dict[str, object]:
    static_json = "[]"
    bgp_json = "[]"
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    credentials = {
        "certificate_authority": _private_file(tmp_path / "ca.pem"),
        "certificate": _private_file(tmp_path / "node.pem"),
        "private_key": _private_file(tmp_path / "node.key"),
        "nebius_credentials": _private_file(tmp_path / "credentials.json", "{}"),
    }
    binding = {
        "cluster_id": "cluster-a",
        "shared_allocation_id": "allocation-a",
        "nodes": [
            {
                "node_id": "node-a",
                "role": "active",
                "compute_id": "compute-a",
                "network_interface_name": "eth0",
                "peer_endpoint": "127.0.0.1:9443",
                "credentials": credentials,
            },
            {
                "node_id": "node-b",
                "role": "passive",
                "compute_id": "compute-b",
                "network_interface_name": "eth0",
                "peer_endpoint": "127.0.0.1:9444",
                "credentials": credentials,
            },
        ],
        "route_targets": [target.model_dump(mode="json")],
        "route_runtime_id": VMHARuntimeBinding.derive_route_runtime_id(
            "cluster-a", "allocation-a", (target,)
        ),
        "generation_id": "a" * 64,
        "configuration_digest": "a" * 64,
        "static_routes_digest": hashlib.sha256(static_json.encode()).hexdigest(),
        "bgp_policy_digest": hashlib.sha256(bgp_json.encode()).hexdigest(),
    }
    return {
        "cluster_id": "cluster-a",
        "node": {"node_id": "node-a", "role": "active"},
        "generation": {
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": binding["static_routes_digest"],
                "bgp_policy": binding["bgp_policy_digest"],
            },
            "logical_manifests": {
                "static_routes_json": static_json,
                "bgp_policy_json": bgp_json,
            },
        },
        "readiness": {
            "generation_id": "a" * 64,
            "digests": {
                "configuration": "a" * 64,
                "static_routes": binding["static_routes_digest"],
                "bgp_policy": binding["bgp_policy_digest"],
            },
            "required_node_ids": ["node-a", "node-b"],
        },
        "runtime_binding": binding,
    }


def test_factory_builds_every_inert_port_and_closes_one_sdk(tmp_path: Path) -> None:
    sdk = FakeSDK()
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: sdk,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )

    assert set(ports.providers()) == {"peer", "readiness", "cloud", "data_plane", "routes"}
    assert set(ports.handlers()) == set(ActionKind)
    assert ports.local.node_id == "node-a"
    assert ports.peer.node_id == "node-b"
    ports.close()
    ports.close()
    assert sdk.close_calls == 1


def test_factory_failure_closes_sdk_and_never_persists_credentials(tmp_path: Path) -> None:
    sdk = FakeSDK()

    def fail(_sdk: object) -> FakeRouteBackend:
        raise RuntimeError("fixture backend failure")

    with pytest.raises(RuntimeError, match="backend failure"):
        build_runtime_ports(
            _runtime_config(tmp_path),
            state_dir=tmp_path / "state",
            replay_store=MemoryReplayStore(),
            sdk_factory=lambda **_kwargs: sdk,
            route_backend_factory=fail,
            data_plane_factory=FakeDataPlane,
        )

    assert sdk.close_calls == 1
    assert not (tmp_path / "state").exists()


def test_factory_rejects_non_private_credential_before_sdk_creation(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    runtime_binding = config["runtime_binding"]
    assert isinstance(runtime_binding, dict)
    nodes = runtime_binding["nodes"]
    assert isinstance(nodes, list)
    node = nodes[0]
    assert isinstance(node, dict)
    credentials = node["credentials"]
    assert isinstance(credentials, dict)
    Path(credentials["nebius_credentials"]).chmod(0o644)
    calls = 0

    def sdk_factory(**_kwargs: object) -> FakeSDK:
        nonlocal calls
        calls += 1
        return FakeSDK()

    with pytest.raises(ValueError, match="private readable regular file"):
        build_runtime_ports(
            config,
            state_dir=tmp_path / "state",
            replay_store=MemoryReplayStore(),
            sdk_factory=sdk_factory,
            route_backend_factory=lambda _sdk: FakeRouteBackend(),
            data_plane_factory=FakeDataPlane,
        )

    assert calls == 0


def test_enable_accepts_prior_route_operation_only_with_current_exact_authority(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    ports.cloud.observe = lambda: CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        former_owner_compute_state=ComputeState.STOPPED,
        former_attachment_absent=True,
        candidate_attachment_exact=True,
        ownership_re_read_exact=True,
        ownership_epoch="7",
    )
    ports.routes.readiness = lambda: LocalReadiness(True, True, True, True)
    reconcile_context = RouteReconciliationContext(
        operation_id="route-op",
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
    ports.routes.store.save_route_reconciliation_receipt(
        {
            "context": reconcile_context.to_dict(),
            "plan_digest": "d" * 64,
            "schema": "nebius-vpngw/vm-ha-route-reconciliation-receipt-v1",
        }
    )
    enable = ControllerAction(
        ActionKind.ENABLE_ACTIVE,
        "enable-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet(
            "a" * 64,
            ports.binding.static_routes_digest,
            ports.binding.bgp_policy_digest,
        ),
        ownership_incarnation=2,
    )

    ports.handlers()[ActionKind.ENABLE_ACTIVE](enable)

    assert ports.data_plane.mode() is DataPlaneMode.ACTIVE
    ports.close()


def test_local_data_plane_guard_is_absolute_bounded_and_fail_closed(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []
    forwarding = "1"

    def runner(argv: tuple[str, ...], timeout: float) -> CommandResult:
        nonlocal forwarding
        calls.append((tuple(argv), timeout))
        if argv[0] == "/usr/sbin/swanctl":
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        configured_bgp_sessions=(),
        bgp_policy_digest="c" * 64,
        runner=runner,
        command_timeout=2.0,
    )
    action = ControllerAction(
        ActionKind.INSTALL_COLD_START_GUARD,
        "guard-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    runtime.install_guard(action)

    assert runtime.mode() is DataPlaneMode.BLOCKED
    assert all(Path(argv[0]).is_absolute() and timeout == 2.0 for argv, timeout in calls)
    assert calls[0][0][0] == DataPlaneCommandSet().sysctl
    assert any(argv[0] == DataPlaneCommandSet().swanctl for argv, _timeout in calls)
    assert json.loads((tmp_path / "data-plane.json").read_text())["boot_id"] == "boot-a"
    assert os.stat(tmp_path / "data-plane.json").st_mode & 0o077 == 0


def test_local_data_plane_passive_preserves_ready_tunnels_with_forwarding_off(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    forwarding = "1"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        calls.append(tuple(argv))
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        configured_bgp_sessions=(),
        bgp_policy_digest="c" * 64,
        runner=runner,
    )
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    runtime.enter_passive(action)

    assert runtime.mode() is DataPlaneMode.PASSIVE
    assert forwarding == "0"
    assert not any("--terminate" in argv for argv in calls)


def test_route_pending_intent_round_trip_binds_full_authority(tmp_path: Path) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.CREATE,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
    )
    context = RouteReconciliationContext(
        operation_id="route-op",
        cluster_id="cluster-a",
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        ownership_incarnation=2,
    )

    store.save_pending_mutation(mutation, context)

    assert store.load_pending_mutation() == (mutation, context)
    assert os.stat(store.pending_path).st_mode & 0o077 == 0


def test_completed_route_effect_recovers_if_pending_clear_was_interrupted(tmp_path: Path) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    target = ports.binding.route_targets[0]
    mutation = RouteMutation(
        kind=RouteMutationKind.DELETE,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
        route_id="old-route",
    )
    context = RouteReconciliationContext(
        operation_id="route-op",
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

    ports.routes._apply_mutation(mutation, {})

    assert ports.routes.store.load_pending_mutation() is None
    ports.close()
