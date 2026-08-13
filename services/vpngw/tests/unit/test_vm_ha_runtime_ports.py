from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.models import DigestSet, ReplayState
from nebius_vpngw.agent.vm_ha.runtime import (
    BoundCloudRuntime,
    CommandResult,
    DataPlaneCommandSet,
    LocalDataPlaneObservation,
    RuntimeStateStore,
    SystemDataPlaneRuntime,
    _observed_import_policy_digest,
    _policy_digest,
    build_runtime_ports,
    validate_installed_credential_bundle,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AllocationObservation,
    AllocationOwner,
    ClusterCloudObservation,
    ComputeObservation,
    InstanceCloudState,
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
    def verify_target(self, _target: VMHARouteTarget) -> None:
        return None

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
            observed_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
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


class FakeCredentialBundle:
    def __init__(self) -> None:
        self.checks = 0

    def revalidate(self) -> None:
        self.checks += 1


def _fake_credential_bundle(_binding: VMHARuntimeBinding, _node: object) -> FakeCredentialBundle:
    return FakeCredentialBundle()


def _private_file(path: Path, value: str = "fixture") -> str:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def test_bound_cloud_transfer_uses_one_candidate_revision_boundary(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])
    local = next(node for node in binding.nodes if node.role.value == "passive")
    peer = next(node for node in binding.nodes if node.node_id != local.node_id)
    state = {
        "owner": AllocationOwner(peer.compute_id, peer.network_interface_name),
        "former_state": InstanceCloudState.RUNNING,
        "candidate_revision": "3",
    }

    class Adapter:
        def observe_cluster(self, **_kwargs: object) -> ClusterCloudObservation:
            owner = state["owner"]
            candidate_allocation = (
                binding.shared_allocation_id
                if owner == AllocationOwner(local.compute_id, local.network_interface_name)
                else ""
            )
            former_allocation = (
                binding.shared_allocation_id
                if owner == AllocationOwner(peer.compute_id, peer.network_interface_name)
                else ""
            )
            return ClusterCloudObservation(
                allocation=AllocationObservation(binding.shared_allocation_id, owner),
                former=ComputeObservation(
                    peer.compute_id,
                    state["former_state"],
                    "9",
                    ((peer.network_interface_name, former_allocation),),
                ),
                candidate=ComputeObservation(
                    local.compute_id,
                    InstanceCloudState.RUNNING,
                    state["candidate_revision"],
                    ((local.network_interface_name, candidate_allocation),),
                ),
                former_owner=AllocationOwner(peer.compute_id, peer.network_interface_name),
                candidate_owner=AllocationOwner(local.compute_id, local.network_interface_name),
            )

        def require_stopped(self, _compute_id: str) -> None:
            state["former_state"] = InstanceCloudState.STOPPED

        def require_former_attachment_absent(self, *_args: object) -> None:
            state["owner"] = None

        def require_candidate_attachment(self, *_args: object) -> None:
            state["owner"] = AllocationOwner(local.compute_id, local.network_interface_name)
            state["candidate_revision"] = "4"

        def require_compute_attachment(self, *_args: object, **_kwargs: object) -> None:
            return None

    runtime = BoundCloudRuntime(binding, local, peer, Adapter())  # type: ignore[arg-type]
    action = lambda kind: ControllerAction(  # noqa: E731
        kind,
        f"op-{kind.value}",
        "boot-a",
        peer.node_id
        if kind in {ActionKind.STOP_FORMER_OWNER, ActionKind.DETACH_FORMER_ATTACHMENT}
        else local.node_id,
        binding.shared_allocation_id,
        "3",
        binding.generation_id,
        DigestSet(
            binding.configuration_digest,
            binding.static_routes_digest,
            binding.bgp_policy_digest,
        ),
    )

    assert runtime.observe().ownership_epoch == "3"
    runtime.stop_former(action(ActionKind.STOP_FORMER_OWNER))
    runtime.detach_former(action(ActionKind.DETACH_FORMER_ATTACHMENT))
    assert runtime.observe().ownership_epoch == "3"
    runtime.attach_candidate(action(ActionKind.ATTACH_CANDIDATE))
    attached = runtime.observe()
    assert attached.ownership_epoch == "4"
    assert attached.ownership_re_read_exact is False
    runtime.confirm_candidate()
    assert runtime.observe().ownership_re_read_exact is True


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


def test_factory_builds_every_runtime_port_and_closes_one_sdk(tmp_path: Path) -> None:
    sdk = FakeSDK()
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: sdk,
        credential_bundle_factory=_fake_credential_bundle,
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


def test_ports_emit_secret_free_fresh_heartbeat_and_restore_shutdown_guard(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
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

    heartbeat = ports.heartbeat(boot_id="boot-a", sequence=4, clock=10.0)
    ports.install_shutdown_guard(boot_id="boot-a")

    assert heartbeat.node_id == "node-a"
    assert heartbeat.sequence == 4
    assert heartbeat.observed_owner_id == "node-a"
    assert heartbeat.promotion_ready is True
    assert "credentials" not in heartbeat.to_dict()
    assert ports.data_plane.mode() is DataPlaneMode.BLOCKED
    ports.close()


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
            credential_bundle_factory=_fake_credential_bundle,
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
            credential_bundle_factory=_fake_credential_bundle,
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
        credential_bundle_factory=_fake_credential_bundle,
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
    current_plan = ports.routes._plan(
        ports.routes._ownership(require_takeover_fence=True),
        ports.routes.store.load_transition(now=0.0),
    )
    ports.routes.store.save_transition(current_plan.next_state)
    ports.routes.store.save_route_reconciliation_receipt(
        {
            "context": reconcile_context.to_dict(),
            "plan_digest": ports.routes._plan_digest(current_plan),
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
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
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
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
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
    assert any("--unload-conns" in argv for argv, _timeout in calls)
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
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == "/usr/sbin/swanctl":
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
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


def test_current_frr_import_policy_digest_detects_live_drift() -> None:
    sessions = frozenset({"169.254.0.2"})
    expected = _policy_digest({"169.254.0.2": ("10.0.0.0/24",)})
    current = """
ip prefix-list ALLOW-FROM-169-254-0-2 seq 10 permit 10.0.0.0/24
router bgp 65000
 neighbor 169.254.0.2 prefix-list ALLOW-FROM-169-254-0-2 in
"""

    assert _observed_import_policy_digest(current, sessions) == expected
    assert (
        _observed_import_policy_digest(current.replace("10.0.0.0/24", "10.1.0.0/24"), sessions)
        != expected
    )


def test_active_persistence_failure_rolls_forwarding_back_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebius_vpngw.agent.vm_ha import runtime as runtime_module

    forwarding = "0"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        raise AssertionError(argv)

    real_write = runtime_module._atomic_write_json
    writes = 0

    def interrupted(path: Path, value: dict[str, object]) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected durable write failure")
        real_write(path, value)

    monkeypatch.setattr(runtime_module, "_atomic_write_json", interrupted)
    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
    )
    action = ControllerAction(
        ActionKind.ENABLE_ACTIVE,
        "enable-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    with pytest.raises(OSError, match="injected"):
        runtime.enable_active(action)

    assert forwarding == "0"


def test_runtime_distinguishes_initial_owner_from_takeover_fencing(tmp_path: Path) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    ports.cloud.observe = lambda: CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=True,
        candidate_attachment_exact=True,
        ownership_re_read_exact=True,
        ownership_epoch="7",
    )

    assert ports.routes._ownership(require_takeover_fence=False).candidate_node_id == "node-a"
    with pytest.raises(RuntimeError, match="ownership authority"):
        ports.routes._ownership(require_takeover_fence=True)
    ports.close()


def test_credential_bundle_rejects_noncanonical_runtime_identity(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])

    with pytest.raises(ValueError, match="non-canonical bundle path"):
        validate_installed_credential_bundle(binding, binding.nodes[0])


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
        credential_bundle_factory=_fake_credential_bundle,
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
