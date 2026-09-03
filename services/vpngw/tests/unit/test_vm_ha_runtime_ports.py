from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat, ReplayState
from nebius_vpngw.agent.vm_ha.mtls import MTLSSnapshot, PeerLeaf
from nebius_vpngw.agent.vm_ha.runtime import (
    BGPExportState,
    BoundCloudRuntime,
    BoundPeerRuntime,
    CommandResult,
    DataPlaneCommandSet,
    LocalDataPlaneObservation,
    RuntimeStateStore,
    SystemDataPlaneRuntime,
    _advertised_bgp_prefixes,
    _bgp_export_diagnostic,
    _bgp_export_state,
    _configured_bgp_sessions,
    _configured_bgp_summary_sessions,
    _expected_bgp_exports,
    _live_peer_binding_from_mtls,
    _observed_import_policy_digest,
    _passive_bgp_policy_matches,
    _policy_digest,
    _routing_hygiene_ready,
    build_runtime_ports,
    runtime_identity_public_status,
    validate_installed_credential_bundle,
)
from nebius_vpngw.agent.vm_ha.store import AtomicGenerationStore
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
    AcceptedRouteOperation,
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    PendingRouteMutation,
    RouteMutation,
    RouteMutationKind,
    RouteMutationPhase,
    RouteReconciliationContext,
    RouteReconciliationReceipt,
    RouteRollbackSnapshot,
    RouteTransitionState,
    VerifiedAllocationOwnership,
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding


@dataclass
class MemoryReplayStore:
    values: dict[str, ReplayState] = field(default_factory=dict)

    def load_replay_state(self, peer_node_id: str) -> ReplayState | None:
        return self.values.get(peer_node_id)

    def save_replay_state(self, peer_node_id: str, state: ReplayState) -> None:
        self.values[peer_node_id] = state


def test_peer_runtime_heals_replay_advanced_before_heartbeat_cache(tmp_path: Path) -> None:
    store = AtomicGenerationStore(tmp_path / "generation-store")
    heartbeat = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=7,
        sent_at="2026-08-24T00:00:00Z",
        configured_role="passive",
        observed_owner_id="node-b",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )
    store.save_replay_state("node-b", ReplayState("boot-b", 7))
    store.save_accepted_peer_heartbeat("node-b", heartbeat)
    store.save_replay_state("node-b", ReplayState("boot-b", 8))

    replacement = replace(heartbeat, sequence=9)

    class Exchange:
        def receive(self, *, timeout_seconds: float) -> tuple[PeerHeartbeat, ReplayState]:
            assert timeout_seconds == 2.0
            replay = ReplayState("boot-b", 9)
            store.save_replay_state("node-b", replay)
            return replacement, replay

        def send(self, _heartbeat: PeerHeartbeat) -> None:
            return None

    runtime = BoundPeerRuntime(  # type: ignore[arg-type]
        Exchange(),
        clock=lambda: 10.0,
        peer_node_id="node-b",
        heartbeat_store=store,
    )

    assert runtime.observe() == (None, None)
    assert runtime.poll(timeout_seconds=2.0) == replacement
    assert runtime.observe() == (replacement, 10.0)
    assert store.load_accepted_peer_heartbeat("node-b") == replacement


class FakeSDK:
    def __init__(self) -> None:
        self.close_calls = 0

    def sync_close(self) -> None:
        self.close_calls += 1


class FakeRouteBackend:
    def __init__(self) -> None:
        self.reconciliation_operation_ids: list[str] = []

    def set_reconciliation_operation_id(self, operation_id: str) -> None:
        self.reconciliation_operation_ids.append(operation_id)

    def verify_target(self, _target: VMHARouteTarget) -> None:
        return None

    def verify_migration_route(self, _binding: object) -> bool:
        return False

    def verify_migration_successor(self, _binding: object, _ownership: object) -> bool:
        return False

    def list_routes(self, *_args: object) -> tuple[object, ...]:
        return ()

    def apply_mutation(self, _mutation: RouteMutation) -> str | None:
        return "route-a"

    def recover_deleted_route(self, _mutation: RouteMutation) -> bool:
        return True

    def recover_created_route(self, _mutation: RouteMutation) -> str | None:
        return None

    def recover_restored_route(self, _mutation: RouteMutation) -> str | None:
        return "route-restored"


def _fake_mtls_snapshot() -> MTLSSnapshot:
    peer = PeerLeaf("node-b", "compute-b", 1, "e" * 64, "f" * 64)
    return MTLSSnapshot(
        cluster_id="cluster-a",
        node_id="node-a",
        compute_id="compute-a",
        epoch=1,
        certificate_fingerprint="d" * 64,
        spki_fingerprint="c" * 64,
        certificate_path=Path("/unused/certificate.pem"),
        private_key_path=Path("/unused/private-key.pem"),
        peers=(peer,),
        peer_certificate_paths=(Path("/unused/peer.pem"),),
        peer_certificate_pems=(b"unused",),
    )


def test_live_peer_binding_uses_highest_managed_mtls_epoch(tmp_path: Path) -> None:
    binding = VMHARuntimeBinding.model_validate(_runtime_config(tmp_path)["runtime_binding"])
    local = next(item for item in binding.nodes if item.node_id == "node-a")
    peer = next(item for item in binding.nodes if item.node_id == "node-b")
    snapshot = replace(
        _fake_mtls_snapshot(),
        peers=(
            PeerLeaf("node-b", "compute-b", 1, "e" * 64, "f" * 64),
            PeerLeaf("node-b", "compute-b-new", 2, "1" * 64, "2" * 64),
        ),
    )

    current = _live_peer_binding_from_mtls(
        snapshot=snapshot,
        binding=binding,
        local=local,
        peer=peer,
    )

    assert current.compute_id == "compute-b-new"
    assert current.model_dump(exclude={"compute_id"}) == peer.model_dump(exclude={"compute_id"})


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
            bgp_export_state=BGPExportState.MATCH,
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


class UnavailableDataPlane(FakeDataPlane):
    def observe(self) -> LocalDataPlaneObservation:
        raise RuntimeError("local FRR is not materialized")


def test_expected_bgp_exports_are_exact_per_connection_and_peer() -> None:
    config = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "name": "site-a",
                "bgp": {"advertise_local_prefixes": True},
                "tunnels": [{"inner_remote_ip": "169.254.10.2"}],
            },
            {
                "name": "site-b",
                "bgp": {"advertise_local_prefixes": False},
                "tunnels": [{"inner_remote_ip": "169.254.11.2"}],
            },
        ],
    }
    records = [
        {"connection": "site-a", "remote_prefixes": ["10.10.0.0/24"]},
        {"connection": "site-b", "remote_prefixes": ["10.20.0.0/24"]},
    ]

    assert _expected_bgp_exports(config, records) == {
        "169.254.10.2": frozenset({"10.96.0.0/13"}),
        "169.254.11.2": frozenset(),
    }


def test_empty_frr_bgp_summary_is_an_exact_empty_peer_set() -> None:
    assert _configured_bgp_summary_sessions({}) == frozenset()
    assert _configured_bgp_summary_sessions({"unexpected": {}}) is None


def test_static_cold_readiness_accepts_empty_frr_bgp_summary(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    static_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    static_digest = hashlib.sha256(static_json.encode()).hexdigest()
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    generation_digests = generation["digests"]
    readiness_digests = readiness["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(generation_digests, dict)
    assert isinstance(readiness_digests, dict)
    manifests["static_routes_json"] = static_json
    generation_digests["static_routes"] = static_digest
    readiness_digests["static_routes"] = static_digest
    runtime_binding["static_routes_digest"] = static_digest

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] in {"show bgp summary json", "show bgp ipv4 unicast json"}:
                return CommandResult(0, "{}")
            if argv[-1] == "show running-config":
                return CommandResult(0, "")
        if argv[0] == DataPlaneCommandSet().ip:
            if tuple(argv[1:]) in {
                ("rule", "show"),
                ("route", "show", "169.254.0.0/16"),
            }:
                return CommandResult(0, "")
            return CommandResult(0, "[]")
        if argv[0] == DataPlaneCommandSet().swanctl:
            return CommandResult(0, "list-sas reply {}\n")
        if argv[0] == DataPlaneCommandSet().sysctl:
            return CommandResult(0, "0\n")
        raise AssertionError(argv)

    def data_plane_factory(**kwargs: object) -> SystemDataPlaneRuntime:
        return SystemDataPlaneRuntime(
            **kwargs,
            routing_lock_path=tmp_path / "routing.lock",
        )

    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=data_plane_factory,
        runner=runner,
    )
    passive = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, static_digest, hashlib.sha256(b"[]").hexdigest()),
    )
    ports.data_plane._write_state(passive, DataPlaneMode.PASSIVE)  # type: ignore[attr-defined]

    local = ports.data_plane.observe()
    observed = ports.routes.readiness()

    assert local.configured_bgp_sessions == frozenset()
    assert observed.candidate_preparation_required is True
    assert observed.cold_standby_ready is True
    assert observed.transfer_ready is True
    assert observed.promotion_ready is False
    ports.close()


def test_bgp_readiness_rejects_empty_frr_bgp_summary(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    bgp_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    bgp_digest = hashlib.sha256(bgp_json.encode()).hexdigest()
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    generation_digests = generation["digests"]
    readiness_digests = readiness["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(generation_digests, dict)
    assert isinstance(readiness_digests, dict)
    manifests["bgp_policy_json"] = bgp_json
    generation_digests["bgp_policy"] = bgp_digest
    readiness_digests["bgp_policy"] = bgp_digest
    runtime_binding["bgp_policy_digest"] = bgp_digest
    config["gateway"] = {"local_prefixes": ["10.96.0.0/13"]}
    config["connections"] = [
        {
            "name": "site-a",
            "routing_mode": "bgp",
            "bgp": {"advertise_local_prefixes": True},
            "tunnels": [
                {
                    "inner_remote_ip": "169.254.10.2",
                    "ha_role": "active",
                }
            ],
        }
    ]

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] in {"show bgp summary json", "show bgp ipv4 unicast json"}:
                return CommandResult(0, "{}")
            if argv[-1] == "show running-config":
                return CommandResult(0, "")
        if argv[0] == DataPlaneCommandSet().ip:
            if tuple(argv[1:]) in {
                ("rule", "show"),
                ("route", "show", "169.254.0.0/16"),
            }:
                return CommandResult(0, "")
            return CommandResult(0, "[]")
        if argv[0] == DataPlaneCommandSet().swanctl:
            return CommandResult(0, "list-sas reply {}\n")
        if argv[0] == DataPlaneCommandSet().sysctl:
            return CommandResult(0, "0\n")
        raise AssertionError(argv)

    def data_plane_factory(**kwargs: object) -> SystemDataPlaneRuntime:
        return SystemDataPlaneRuntime(
            **kwargs,
            routing_lock_path=tmp_path / "routing.lock",
        )

    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=data_plane_factory,
        runner=runner,
    )
    passive = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, hashlib.sha256(b"[]").hexdigest(), bgp_digest),
    )
    ports.data_plane._write_state(passive, DataPlaneMode.PASSIVE)  # type: ignore[attr-defined]

    local = ports.data_plane.observe()
    observed = ports.routes.readiness()

    assert local.configured_bgp_sessions == frozenset()
    assert observed.candidate_preparation_required is False
    assert observed.cold_standby_ready is False
    assert observed.bgp_ready is False
    assert observed.transfer_ready is False
    assert observed.promotion_ready is False
    assert "bgp-not-ready" in observed.blocked_reasons
    ports.close()


def test_disabled_tunnels_are_absent_from_runtime_bgp_sessions_and_exports() -> None:
    config = {
        "gateway": {"local_prefixes": ["10.96.0.0/13"]},
        "connections": [
            {
                "name": "site-a",
                "bgp": {"advertise_local_prefixes": True},
                "tunnels": [
                    {"inner_remote_ip": "169.254.10.2", "ha_role": "active"},
                    {"inner_remote_ip": "169.254.11.2", "ha_role": "disable"},
                ],
            }
        ],
    }
    records = [{"connection": "site-a", "remote_prefixes": ["10.10.0.0/24"]}]

    assert _configured_bgp_sessions(config, records) == frozenset({"169.254.10.2"})
    assert _expected_bgp_exports(config, records) == {"169.254.10.2": frozenset({"10.96.0.0/13"})}

    config["connections"][0]["tunnels"] = [
        {"inner_remote_ip": "169.254.11.2", "ha_role": "disable"}
    ]
    assert _configured_bgp_sessions(config, records) == frozenset()
    assert _expected_bgp_exports(config, records) == {}


def test_disabled_only_bgp_policy_still_detects_stale_live_peer(
    tmp_path: Path,
) -> None:
    stale_peer = "169.254.99.2"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        if argv[-1] == "show bgp summary json":
            return CommandResult(
                0,
                json.dumps({"ipv4Unicast": {"peers": {stale_peer: {"state": "Established"}}}}),
            )
        if argv[-1].endswith("advertised-routes json"):
            return CommandResult(
                0,
                json.dumps(
                    {
                        "advertisedRoutes": {"10.10.0.0/24": {}},
                        "totalPrefixCounter": 1,
                    }
                ),
            )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={},
        bgp_export_observation_required=True,
        runner=runner,
    )

    evidence = runtime._observe_bgp_exports()
    assert evidence is not None
    configured, advertised = evidence
    assert configured == frozenset({stale_peer})
    assert advertised == {stale_peer: frozenset({"10.10.0.0/24"})}
    assert (
        _bgp_export_state(
            runtime.expected_bgp_exports,
            advertised,
            configured_peers=configured,
        )
        is BGPExportState.DRIFT
    )


def test_bgp_export_state_requires_complete_exact_peer_evidence() -> None:
    expected = {"169.254.10.2": frozenset({"10.96.0.0/13"})}

    assert _bgp_export_state(expected, expected) is BGPExportState.MATCH
    assert (
        _bgp_export_state(expected, {"169.254.10.2": frozenset({"10.10.0.0/24"})})
        is BGPExportState.DRIFT
    )
    assert _bgp_export_state(expected, {}) is BGPExportState.UNKNOWN
    assert (
        _bgp_export_state(
            expected,
            expected,
            configured_peers={"169.254.10.2", "169.254.99.2"},
        )
        is BGPExportState.DRIFT
    )
    assert _advertised_bgp_prefixes(
        {
            "advertisedRoutes": {"10.96.0.1/13": {}},
            "totalPrefixCounter": 1,
        }
    ) == frozenset({"10.96.0.0/13"})
    assert _advertised_bgp_prefixes({"totalPrefixCounter": 0}) == frozenset()
    assert _advertised_bgp_prefixes({}) is None
    assert _advertised_bgp_prefixes({"advertisedRoutes": {}, "totalPrefixCounter": 1}) is None


def test_bgp_export_diagnostic_is_aggregate_and_secret_free() -> None:
    expected_peer = "169.254.10.2"
    unexpected_peer = "169.254.99.2"
    expected_prefix = "10.96.0.0/13"
    unexpected_prefix = "10.10.0.0/24"

    diagnostic = _bgp_export_diagnostic(
        {expected_peer: frozenset({expected_prefix})},
        (
            frozenset({expected_peer, unexpected_peer}),
            {expected_peer: frozenset({unexpected_prefix})},
        ),
    )

    assert diagnostic == (
        "expected_peers=1,configured_peers=2,observed_peers=1,missing_configured=0,"
        "unexpected_configured=1,missing_observations=0,unexpected_observations=0,"
        "mismatched_prefix_sets=1,expected_prefixes=1,observed_prefixes=1"
    )
    assert expected_peer not in diagnostic
    assert unexpected_peer not in diagnostic
    assert expected_prefix not in diagnostic
    assert unexpected_prefix not in diagnostic


@pytest.mark.parametrize(
    "running_config",
    (
        "route-map ADVERTISE-NONE permit 10\n neighbor 169.254.10.2 route-map ADVERTISE-NONE out\n",
        "route-map ADVERTISE-NONE deny 10\n neighbor 169.254.10.2 route-map ADVERTISE-ACTIVE out\n",
        "route-map ADVERTISE-NONE deny 10\n"
        "route-map ADVERTISE-NONE permit 20\n"
        " neighbor 169.254.10.2 route-map ADVERTISE-NONE out\n",
    ),
)
def test_passive_bgp_policy_rejects_nonexclusive_or_non_deny_bindings(
    running_config: str,
) -> None:
    assert not _passive_bgp_policy_matches(running_config, ("169.254.10.2",))


def test_runtime_observes_unexpected_live_frr_peers_as_export_drift(
    tmp_path: Path,
) -> None:
    expected_peer = "169.254.10.2"
    unexpected_peer = "169.254.99.2"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        command = argv[-1]
        if command == "show bgp summary json":
            return CommandResult(
                0,
                json.dumps(
                    {
                        "ipv4Unicast": {
                            "peers": {
                                expected_peer: {"state": "Established"},
                                unexpected_peer: {"state": "Established"},
                            }
                        }
                    }
                ),
            )
        if command.endswith("advertised-routes json"):
            routes = {"10.96.0.0/13": {}} if expected_peer in command else {}
            return CommandResult(
                0,
                json.dumps(
                    {
                        "advertisedRoutes": routes,
                        "totalPrefixCounter": len(routes),
                    }
                ),
            )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(expected_peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={expected_peer: ("10.96.0.0/13",)},
        runner=runner,
    )

    evidence = runtime._observe_bgp_exports()
    assert evidence is not None
    configured, advertised = evidence
    assert configured == frozenset({expected_peer, unexpected_peer})
    assert (
        _bgp_export_state(
            runtime.expected_bgp_exports,
            advertised,
            configured_peers=configured,
        )
        is BGPExportState.DRIFT
    )


def test_active_export_drift_keeps_forwarding_fenced(tmp_path: Path) -> None:
    peer = "169.254.10.2"
    forwarding = "0"
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        calls.append(tuple(argv))
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}}),
                )
            if argv[-1].endswith("advertised-routes json"):
                return CommandResult(
                    0,
                    json.dumps({"advertisedRoutes": {}, "totalPrefixCounter": 0}),
                )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: ("10.96.0.0/13",)},
        runner=runner,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
        active_preparer=lambda: calls.append(("active-preparer",)),
        blocked_preparer=lambda: calls.append(("blocked-preparer",)),
    )

    with pytest.raises(RuntimeError, match="active BGP advertisements are drift"):
        runtime.enable_active(
            ControllerAction(
                ActionKind.ENABLE_ACTIVE,
                "active-op",
                "boot-a",
                "node-a",
                "allocation-a",
                "7",
                "a" * 64,
                DigestSet("a" * 64, "b" * 64, "c" * 64),
            )
        )

    assert forwarding == "0"
    assert not any(command[-1] == "net.ipv4.ip_forward=1" for command in calls)


def test_active_export_verification_and_forwarding_share_routing_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = "169.254.10.2"
    forwarding = "0"
    locked = False
    trace: list[str] = []

    def flock(_fd: int, operation: int) -> None:
        nonlocal locked
        if operation == fcntl.LOCK_EX:
            assert not locked
            locked = True
        else:
            assert locked
            locked = False

    monkeypatch.setattr("nebius_vpngw.agent.vm_ha.runtime.fcntl.flock", flock)

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            assert locked
            trace.append("forward")
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            assert locked
            if argv[-1] == "show bgp summary json":
                trace.append("summary")
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}}),
                )
            trace.append("advertised-routes")
            return CommandResult(
                0,
                json.dumps(
                    {
                        "advertisedRoutes": {"10.96.0.0/13": {}},
                        "totalPrefixCounter": 1,
                    }
                ),
            )
        raise AssertionError(argv)

    def prepare_active() -> None:
        assert locked
        trace.append("prepare")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: ("10.96.0.0/13",)},
        runner=runner,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
        active_preparer=prepare_active,
    )

    runtime.enable_active(
        ControllerAction(
            ActionKind.ENABLE_ACTIVE,
            "active-op",
            "boot-a",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )

    assert trace == ["prepare", "summary", "advertised-routes", "forward"]
    assert forwarding == "1"
    assert not locked


def test_active_export_verification_waits_for_bounded_bgp_convergence(
    tmp_path: Path,
) -> None:
    peer = "169.254.10.2"
    expected_prefix = "10.96.0.0/13"
    forwarding = "0"
    advertised_reads = 0
    forwarding_enables = 0

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal advertised_reads, forwarding, forwarding_enables
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            forwarding_enables += forwarding == "1"
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}}),
                )
            if argv[-1].endswith("advertised-routes json"):
                advertised_reads += 1
                routes = {expected_prefix: {}} if advertised_reads == 3 else {}
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "advertisedRoutes": routes,
                            "totalPrefixCounter": len(routes),
                        }
                    ),
                )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: (expected_prefix,)},
        runner=runner,
        bgp_export_attempts=3,
        bgp_export_interval=0,
        active_preparer=lambda: None,
    )

    runtime.enable_active(
        ControllerAction(
            ActionKind.ENABLE_ACTIVE,
            "active-op",
            "boot-a",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )

    assert advertised_reads == 3
    assert forwarding_enables == 1
    assert forwarding == "1"


def test_passive_export_leak_restores_blocked_authority(tmp_path: Path) -> None:
    peer = "169.254.10.2"
    forwarding = "1"
    frr_active = True

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding, frr_active
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().systemctl:
            if argv[1:] == ("stop", "frr"):
                frr_active = False
                return CommandResult(0)
            if argv[1:] == ("is-active", "--quiet", "frr"):
                return CommandResult(0 if frr_active else 3)
            return CommandResult(0)
        if argv[0] in {DataPlaneCommandSet().python, DataPlaneCommandSet().swanctl}:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}}),
                )
            if argv[-1].endswith("advertised-routes json"):
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "advertisedRoutes": {"10.10.0.0/24": {}},
                            "totalPrefixCounter": 1,
                        }
                    ),
                )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: ("10.96.0.0/13",)},
        runner=runner,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
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

    with pytest.raises(RuntimeError, match="passive BGP advertisements are drift"):
        runtime.enter_passive(action)

    assert forwarding == "0"
    assert not frr_active
    assert runtime.mode() is DataPlaneMode.BLOCKED


def test_failed_passive_materialization_reproves_empty_exports_before_blocked(
    tmp_path: Path,
) -> None:
    peer = "169.254.10.2"
    forwarding = "1"
    advertised = {"10.96.0.0/13"}
    deny_installs = 0

    def install_deny_all() -> None:
        nonlocal deny_installs, advertised
        deny_installs += 1
        advertised = set()

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().python:
            return CommandResult(1)
        if argv[0] == DataPlaneCommandSet().swanctl:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}}),
                )
            return CommandResult(
                0,
                json.dumps(
                    {
                        "advertisedRoutes": {prefix: {} for prefix in advertised},
                        "totalPrefixCounter": len(advertised),
                    }
                ),
            )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: ("10.96.0.0/13",)},
        runner=runner,
        blocked_preparer=install_deny_all,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
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

    with pytest.raises(RuntimeError, match="materialization did not converge"):
        runtime.enter_passive(action)

    assert deny_installs == 2
    assert advertised == set()
    assert forwarding == "0"
    assert runtime.mode() is DataPlaneMode.BLOCKED


def test_passive_materialization_accepts_exact_deny_policy_before_bgp_establishes(
    tmp_path: Path,
) -> None:
    peer = "169.254.10.2"
    forwarding = "1"
    frr_active = True
    commands: list[tuple[str, ...]] = []
    materialization_path = tmp_path / "materialization.json"
    materialization_path.write_text('{"stale":true}\n', encoding="utf-8")

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding, frr_active
        commands.append(argv)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().systemctl:
            if argv[1:] == ("reload-or-restart", "nebius-vpngw-agent"):
                assert not materialization_path.exists()
            if argv[1:] == ("stop", "frr"):
                frr_active = False
            if argv[1:] == ("is-active", "--quiet", "frr"):
                return CommandResult(0 if frr_active else 3)
            return CommandResult(0)
        if argv[0] in {DataPlaneCommandSet().python, DataPlaneCommandSet().swanctl}:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().vtysh:
            if argv[-1] == "show bgp summary json":
                return CommandResult(
                    0,
                    json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Active"}}}}),
                )
            if argv[-1] == "show running-config":
                return CommandResult(
                    0,
                    "\n".join(
                        (
                            "route-map ADVERTISE-NONE deny 10",
                            f" neighbor {peer} route-map ADVERTISE-NONE out",
                        )
                    ),
                )
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        materialization_path=materialization_path,
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest="c" * 64,
        expected_bgp_exports={peer: ("10.96.0.0/13",)},
        runner=runner,
        blocked_preparer=lambda: None,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
    )

    runtime.enter_passive(
        ControllerAction(
            ActionKind.ENTER_PASSIVE,
            "passive-op",
            "boot-a",
            "node-a",
            "allocation-a",
            "7",
            "a" * 64,
            DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )

    assert forwarding == "0"
    assert frr_active
    assert not materialization_path.exists()
    assert runtime.mode() is DataPlaneMode.PASSIVE
    assert (DataPlaneCommandSet().systemctl, "stop", "frr") not in commands


def _repair_action(*, deadline: float, reasons: tuple[str, ...]) -> ControllerAction:
    return ControllerAction(
        kind=ActionKind.REPAIR_LOCAL_DATAPLANE,
        operation_id="boot-a:1:repair-local-dataplane:node-a",
        boot_id="boot-a",
        target_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        repair_deadline_at=deadline,
        repair_reasons=reasons,
    )


def test_local_repair_uses_only_the_budget_before_reserved_fence(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def runner(argv, timeout: float) -> CommandResult:
        calls.append((tuple(argv), timeout))
        return CommandResult(0, "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
        runner=runner,
        monotonic_clock=lambda: 10.0,
    )

    runtime.repair_local(_repair_action(deadline=15.0, reasons=("bgp-not-ready",)))

    assert calls == [
        (
            (DataPlaneCommandSet().systemctl, "reload-or-restart", "frr"),
            4.0,
        )
    ]


def test_xfrm_loss_repairs_the_encrypted_path_before_bgp(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv, _timeout: float) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(0, "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
        runner=runner,
        monotonic_clock=lambda: 10.0,
    )

    runtime.repair_local(
        _repair_action(
            deadline=15.0,
            reasons=("bgp-not-ready", "xfrm-not-ready"),
        )
    )

    assert calls == [
        (
            DataPlaneCommandSet().systemctl,
            "restart",
            "strongswan-starter",
            "frr",
        ),
        (DataPlaneCommandSet().swanctl, "--load-all", "--noprompt"),
    ]


def test_late_successful_local_repair_still_physically_fences(tmp_path: Path) -> None:
    now = [10.0]
    calls: list[tuple[str, ...]] = []

    def runner(argv, _timeout: float) -> CommandResult:
        command = tuple(argv)
        calls.append(command)
        if command[1:] == ("reload-or-restart", "frr"):
            now[0] = 14.1
        if command[1:] == ("-n", "net.ipv4.ip_forward"):
            return CommandResult(0, "0\n")
        return CommandResult(0, "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
        runner=runner,
        monotonic_clock=lambda: now[0],
    )

    runtime.repair_local(_repair_action(deadline=15.0, reasons=("bgp-not-ready",)))

    assert calls == [
        (DataPlaneCommandSet().systemctl, "reload-or-restart", "frr"),
        (DataPlaneCommandSet().sysctl, "-w", "net.ipv4.ip_forward=0"),
        (DataPlaneCommandSet().sysctl, "-n", "net.ipv4.ip_forward"),
    ]
    assert json.loads((tmp_path / "state.json").read_text())["mode"] == "blocked"


def test_expired_local_repair_physically_fences_without_routing_lock(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv, _timeout: float) -> CommandResult:
        command = tuple(argv)
        calls.append(command)
        if command[1:] == ("-n", "net.ipv4.ip_forward"):
            return CommandResult(0, "0\n")
        return CommandResult(0, "")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
        runner=runner,
        routing_lock_path=tmp_path / "never-acquired.lock",
        monotonic_clock=lambda: 14.1,
    )

    runtime.repair_local(_repair_action(deadline=15.0, reasons=("xfrm-not-ready",)))

    assert calls == [
        (DataPlaneCommandSet().sysctl, "-w", "net.ipv4.ip_forward=0"),
        (DataPlaneCommandSet().sysctl, "-n", "net.ipv4.ip_forward"),
    ]
    assert not (tmp_path / "never-acquired.lock").exists()
    assert json.loads((tmp_path / "state.json").read_text())["mode"] == "blocked"


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


def test_readiness_fails_closed_while_passive_services_are_not_materialized(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=UnavailableDataPlane,
    )

    assert ports.routes.readiness() == LocalReadiness(False, False, False, False)
    ports.close()


def test_passive_readiness_fails_closed_on_routing_hygiene_drift(
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
    ports.data_plane.current = DataPlaneMode.PASSIVE  # type: ignore[attr-defined]
    clean = ports.data_plane.observe()
    ports.data_plane.observe = lambda: replace(  # type: ignore[method-assign]
        clean, routing_hygiene_ready=False
    )

    passive = ports.routes.readiness()
    assert passive.routing_hygiene_ready is False
    assert passive.transfer_ready is False
    assert passive.path_degraded is True
    assert "routing-hygiene-not-ready" in passive.blocked_reasons
    assert "routing-hygiene-not-ready" in passive.degraded_reasons

    ports.data_plane.current = DataPlaneMode.ACTIVE  # type: ignore[attr-defined]
    active = ports.routes.readiness()
    assert active.routing_hygiene_ready is True
    assert active.promotion_ready is True
    ports.close()


def test_cold_standby_cannot_bypass_routing_hygiene() -> None:
    readiness = LocalReadiness(
        True,
        False,
        True,
        False,
        candidate_preparation_required=True,
        cold_standby_ready=True,
        routing_hygiene_ready=False,
    )

    assert readiness.transfer_ready is False
    assert readiness.transfer_blocked_reasons == (
        "static-routes-not-ready",
        "xfrm-not-ready",
        "routing-hygiene-not-ready",
    )


@pytest.mark.parametrize(
    ("route_allocation_id", "exact_route"),
    [
        ("primary-allocation", True),
        ("allocation-a", True),
        ("allocation-a", False),
    ],
)
def test_route_plan_adopts_only_an_exact_approval_bound_route(
    tmp_path: Path,
    route_allocation_id: str,
    exact_route: bool,
) -> None:
    config = _runtime_config(tmp_path)
    static_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    digests = generation["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(digests, dict)
    static_digest = hashlib.sha256(static_json.encode()).hexdigest()
    manifests["static_routes_json"] = static_json
    digests["static_routes"] = static_digest
    readiness_digests = readiness["digests"]
    assert isinstance(readiness_digests, dict)
    readiness_digests["static_routes"] = static_digest
    runtime_binding["static_routes_digest"] = static_digest
    target = VMHARouteTarget.model_validate(runtime_binding["route_targets"][0])
    runtime_binding["migration_routes"] = [
        {
            "allocation_id": route_allocation_id,
            "name": "vpngw-10.20.0.0-16",
            "prefix": "10.20.0.0/16",
            "resource_revision": "7",
            "route_id": "route-old",
            "route_target": target.model_dump(mode="json"),
        }
    ]

    class MigrationRouteBackend(FakeRouteBackend):
        def verify_migration_route(self, _binding: object) -> bool:
            return exact_route

        def list_routes(self, _target, ownership):
            owner = ownership.get("route-old")
            if owner is None:
                return ()
            return (
                ManagedRouteSnapshot(
                    route_id="route-old",
                    prefix="10.20.0.0/16",
                    allocation_id=route_allocation_id,
                    ownership=owner,
                ),
            )

    backend = MigrationRouteBackend()
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
        data_plane_factory=FakeDataPlane,
    )
    ownership = VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-a",
        observed_owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
    )

    if not exact_route:
        with pytest.raises(RuntimeError, match="changed before adoption"):
            ports.routes._plan(
                ownership,
                RouteTransitionState(0.0),
                prepare_authority=True,
            )
        assert ports.routes.store.load_ledger() == {}
    else:
        plan = ports.routes._plan(
            ownership,
            RouteTransitionState(0.0),
            prepare_authority=True,
        )
        if route_allocation_id == "primary-allocation":
            assert len(plan.mutations) == 1
            assert plan.mutations[0].kind is RouteMutationKind.REPLACE
            assert plan.mutations[0].route_id == "route-old"
            assert plan.mutations[0].allocation_id == "allocation-a"
        else:
            assert plan.mutations == ()
            assert plan.blocked_reasons == ()
        assert ports.routes.store.load_ledger()["route-old"] == ManagedRouteOwnership(
            "cluster-a",
            ManagedRouteKind.STATIC,
            target,
        )
    ports.close()


def test_route_reconcile_uses_same_cycle_adopted_migration_ledger(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    static_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    generation_digests = generation["digests"]
    readiness_digests = readiness["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(generation_digests, dict)
    assert isinstance(readiness_digests, dict)
    static_digest = hashlib.sha256(static_json.encode()).hexdigest()
    manifests["static_routes_json"] = static_json
    generation_digests["static_routes"] = static_digest
    readiness_digests["static_routes"] = static_digest
    runtime_binding["static_routes_digest"] = static_digest
    target = VMHARouteTarget.model_validate(runtime_binding["route_targets"][0])
    runtime_binding["migration_routes"] = [
        {
            "allocation_id": "ordinary-allocation",
            "name": "vpngw-10.20.0.0-16",
            "prefix": "10.20.0.0/16",
            "resource_revision": "7",
            "route_id": "route-old",
            "route_target": target.model_dump(mode="json"),
        }
    ]

    class MigrationRouteBackend(FakeRouteBackend):
        def __init__(self) -> None:
            super().__init__()
            self.successor = False

        def verify_migration_route(self, _binding: object) -> bool:
            return not self.successor

        def list_routes(self, _target, ownership):
            route_id = "route-new" if self.successor else "route-old"
            owner = ownership.get(route_id)
            if owner is None:
                return ()
            return (
                ManagedRouteSnapshot(
                    route_id=route_id,
                    prefix="10.20.0.0/16",
                    allocation_id=("allocation-a" if self.successor else "ordinary-allocation"),
                    ownership=owner,
                    rollback=(
                        None
                        if self.successor
                        else RouteRollbackSnapshot(
                            route_id="route-old",
                            resource_version="7",
                            name="vpngw-10.20.0.0-16",
                            labels=(),
                            description="",
                            prefix="10.20.0.0/16",
                            allocation_id="ordinary-allocation",
                            route_target=target,
                        )
                    ),
                ),
            )

        def apply_mutation(self, mutation: RouteMutation) -> str | None:
            assert mutation.kind is RouteMutationKind.REPLACE
            assert mutation.route_id == "route-old"
            self.successor = True
            return "route-new"

    backend = MigrationRouteBackend()
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
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
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        "boot-a:9:reconcile-routes:node-a",
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
        takeover_fence_required=False,
    )

    ports.routes.reconcile(action)

    assert backend.successor is True
    assert ports.routes.store.load_ledger() == {
        "route-new": ManagedRouteOwnership(
            "cluster-a",
            ManagedRouteKind.STATIC,
            target,
        )
    }
    assert ports.routes.receipt_context() is not None
    ports.close()


def test_non_owner_observes_no_stale_route_receipt_without_blocking_snapshot(
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
        ownership_incarnation=0,
    )
    ports.routes.store.save_route_reconciliation_receipt(
        RouteReconciliationReceipt(context=context, plan_digest="d" * 64).to_dict()
    )
    ports.cloud.observe = lambda: CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-b",
        former_owner_node_id="node-a",
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=False,
        candidate_attachment_exact=False,
        ownership_re_read_exact=False,
        ownership_epoch="8",
    )

    assert ports.routes.receipt_context() is None
    ports.close()


def test_rejoined_guarded_former_does_not_invalidate_durable_route_receipt(
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
        ownership_incarnation=1,
    )
    ports.routes.store.save_route_reconciliation_receipt(
        RouteReconciliationReceipt(context=context, plan_digest="d" * 64).to_dict()
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
    plan = SimpleNamespace(blocked_reasons=(), mutations=())
    ports.routes._plan = (  # type: ignore[method-assign]
        lambda _ownership, _state, **_kwargs: plan
    )
    ports.routes._plan_digest = lambda _plan: "d" * 64  # type: ignore[method-assign]

    observed = ports.routes.receipt_context()

    assert observed is not None
    assert observed.owner_node_id == "node-a"
    assert observed.ownership_incarnation == 1
    ports.close()


def test_owner_route_receipt_is_unproven_until_local_data_plane_materializes(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=UnavailableDataPlane,
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
        ownership_incarnation=1,
    )
    ports.routes.store.save_route_reconciliation_receipt(
        RouteReconciliationReceipt(context=context, plan_digest="d" * 64).to_dict()
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

    assert ports.routes.receipt_context() is None
    ports.close()


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
                    ((peer.network_interface_name, "former-primary"),),
                    (
                        (
                            peer.network_interface_name,
                            (former_allocation,) if former_allocation else (),
                        ),
                    ),
                ),
                candidate=ComputeObservation(
                    local.compute_id,
                    InstanceCloudState.RUNNING,
                    state["candidate_revision"],
                    ((local.network_interface_name, "candidate-primary"),),
                    (
                        (
                            local.network_interface_name,
                            (candidate_allocation,) if candidate_allocation else (),
                        ),
                    ),
                ),
                former_owner=AllocationOwner(peer.compute_id, peer.network_interface_name),
                candidate_owner=AllocationOwner(local.compute_id, local.network_interface_name),
            )

        def require_stopped(self, _compute_id: str, _operation_id: str) -> None:
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
    assert attached.ownership_re_read_exact is True
    runtime.confirm_candidate(
        replace(
            action(ActionKind.CONFIRM_CANDIDATE_OWNERSHIP),
            ownership_epoch="4",
        )
    )
    assert runtime.observe().ownership_re_read_exact is True


def test_bound_cloud_missing_peer_authority_is_exactly_inhibition_scoped(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])
    local = next(node for node in binding.nodes if node.role.value == "active")
    peer = next(node for node in binding.nodes if node.node_id != local.node_id)
    inhibited = False
    observed_flags: list[bool] = []

    class Adapter:
        def observe_cluster(self, **kwargs: object) -> ClusterCloudObservation:
            observed_flags.append(bool(kwargs["allow_missing_former"]))
            owner = AllocationOwner(local.compute_id, local.network_interface_name)
            return ClusterCloudObservation(
                allocation=AllocationObservation(binding.shared_allocation_id, owner),
                former=ComputeObservation(
                    peer.compute_id,
                    InstanceCloudState.STOPPED,
                    "0",
                    ((peer.network_interface_name, ""),),
                    ((peer.network_interface_name, ()),),
                ),
                candidate=ComputeObservation(
                    local.compute_id,
                    InstanceCloudState.RUNNING,
                    "7",
                    ((local.network_interface_name, "primary-a"),),
                    ((local.network_interface_name, (binding.shared_allocation_id,)),),
                ),
                former_owner=AllocationOwner(peer.compute_id, peer.network_interface_name),
                candidate_owner=owner,
            )

    runtime = BoundCloudRuntime(
        binding,
        local,
        peer,
        Adapter(),  # type: ignore[arg-type]
        standby_replacement_inhibited=lambda: inhibited,
    )

    assert runtime.observe().local_attachment_exact(local.node_id)
    inhibited = True
    assert runtime.observe().local_attachment_exact(local.node_id)
    assert observed_flags == [False, True]


def _runtime_config(tmp_path: Path) -> dict[str, object]:
    static_json = "[]"
    bgp_json = "[]"
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    credentials_path = _private_file(tmp_path / "credentials.json", "{}")
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
                "nebius_credentials_path": credentials_path,
            },
            {
                "node_id": "node-b",
                "role": "passive",
                "compute_id": "compute-b",
                "network_interface_name": "eth0",
                "peer_endpoint": "127.0.0.1:9444",
                "nebius_credentials_path": credentials_path,
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


def test_malformed_production_binding_overwrites_stale_verified_identity(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    identity_path = state_dir / "runtime-identity.json"
    identity_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-runtime-identity-v1",
                "state": "verified",
                "reason": "exact-current-boot",
                "boot_id": "boot-a",
            }
        ),
        encoding="utf-8",
    )
    identity_path.chmod(0o600)
    config = _runtime_config(tmp_path)
    runtime_binding = dict(config["runtime_binding"])  # type: ignore[arg-type]
    runtime_binding["nebius_project_id"] = "project-a"
    config["runtime_binding"] = runtime_binding
    sdk_calls = 0

    def sdk_factory(**_kwargs: object) -> FakeSDK:
        nonlocal sdk_calls
        sdk_calls += 1
        return FakeSDK()

    with pytest.raises(ValueError, match="complete or legacy"):
        build_runtime_ports(
            config,
            state_dir=state_dir,
            replay_store=MemoryReplayStore(),
            sdk_factory=sdk_factory,
            credential_bundle_factory=_fake_credential_bundle,
            route_backend_factory=lambda _sdk: FakeRouteBackend(),
            data_plane_factory=FakeDataPlane,
            boot_id="boot-a",
        )

    assert sdk_calls == 0
    assert runtime_identity_public_status(state_dir=state_dir, current_boot_id="boot-a") == {
        "state": "blocked",
        "reason": "identity-proof-pending",
    }


def test_legacy_production_binding_requires_apply_before_bundle_validation(
    tmp_path: Path,
) -> None:
    bundle_calls = 0

    def credential_bundle_factory(
        _binding: VMHARuntimeBinding, _node: object
    ) -> FakeCredentialBundle:
        nonlocal bundle_calls
        bundle_calls += 1
        return FakeCredentialBundle()

    with pytest.raises(RuntimeError, match="migration-required"):
        build_runtime_ports(
            _runtime_config(tmp_path),
            state_dir=tmp_path / "state",
            replay_store=MemoryReplayStore(),
            sdk_factory=lambda **_kwargs: FakeSDK(),
            credential_bundle_factory=credential_bundle_factory,
            route_backend_factory=lambda _sdk: FakeRouteBackend(),
            data_plane_factory=FakeDataPlane,
            boot_id="boot-a",
        )

    assert bundle_calls == 0
    assert runtime_identity_public_status(
        state_dir=tmp_path / "state", current_boot_id="boot-a"
    ) == {"state": "migration-required", "reason": "apply-required"}


def test_factory_requires_export_observation_for_disabled_only_bgp_policy(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    bgp_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.10.0.0/24"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    bgp_digest = hashlib.sha256(bgp_json.encode()).hexdigest()
    generation = config["generation"]
    readiness = config["readiness"]
    binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(binding, dict)
    generation["logical_manifests"]["bgp_policy_json"] = bgp_json
    generation["digests"]["bgp_policy"] = bgp_digest
    readiness["digests"]["bgp_policy"] = bgp_digest
    binding["bgp_policy_digest"] = bgp_digest
    config["gateway"] = {"local_prefixes": ["10.96.0.0/13"]}
    config["connections"] = [
        {
            "name": "site-a",
            "routing_mode": "bgp",
            "tunnels": [{"inner_remote_ip": "169.254.10.2", "ha_role": "disable"}],
        }
    ]
    captured: dict[str, object] = {}

    def data_plane_factory(**kwargs: object) -> FakeDataPlane:
        captured.update(kwargs)
        return FakeDataPlane()

    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=data_plane_factory,
    )

    assert captured["configured_bgp_sessions"] == frozenset()
    assert captured["expected_bgp_exports"] == {}
    assert captured["bgp_export_observation_required"] is True
    assert captured["materialization_path"] == tmp_path / "state" / "materialization.json"
    ports.close()


def test_route_plan_retypes_exact_owned_migration_route_for_new_generation(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    static_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    digests = generation["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(digests, dict)
    static_digest = hashlib.sha256(static_json.encode()).hexdigest()
    manifests["static_routes_json"] = static_json
    digests["static_routes"] = static_digest
    readiness_digests = readiness["digests"]
    assert isinstance(readiness_digests, dict)
    readiness_digests["static_routes"] = static_digest
    runtime_binding["static_routes_digest"] = static_digest
    target = VMHARouteTarget.model_validate(runtime_binding["route_targets"][0])
    runtime_binding["migration_routes"] = [
        {
            "allocation_id": "allocation-a",
            "name": "vpngw-10.20.0.0-16",
            "prefix": "10.20.0.0/16",
            "resource_revision": "7",
            "route_id": "route-old",
            "route_target": target.model_dump(mode="json"),
        }
    ]

    class ExactMigrationRouteBackend(FakeRouteBackend):
        def __init__(self) -> None:
            super().__init__()
            self.successor = False

        def verify_migration_route(self, _binding: object) -> bool:
            if self.successor:
                raise AssertionError("the superseded migration revision must not be rechecked")
            return True

        def verify_migration_successor(self, _binding: object, _ownership: object) -> bool:
            return self.successor

        def list_routes(self, _target, ownership):
            owner = ownership.get("route-old")
            if owner is None:
                return ()
            return (
                ManagedRouteSnapshot(
                    route_id="route-old",
                    prefix="10.20.0.0/16",
                    allocation_id="allocation-a",
                    ownership=owner,
                ),
            )

    backend = ExactMigrationRouteBackend()
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
        data_plane_factory=FakeDataPlane,
    )
    ports.routes.store.save_ledger(
        {
            "route-old": ManagedRouteOwnership(
                "cluster-a",
                ManagedRouteKind.BGP,
                target,
            )
        }
    )
    ownership = VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-a",
        observed_owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
    )

    plan = ports.routes._plan(
        ownership,
        RouteTransitionState(0.0),
        prepare_authority=True,
    )

    assert plan.mutations == ()
    assert plan.blocked_reasons == ()
    assert ports.routes.store.load_ledger()["route-old"] == ManagedRouteOwnership(
        "cluster-a",
        ManagedRouteKind.STATIC,
        target,
    )
    backend.successor = True
    replay = ports.routes._plan(
        ownership,
        RouteTransitionState(0.0),
        prepare_authority=True,
    )
    assert replay.mutations == ()
    assert replay.blocked_reasons == ()
    ports.close()


def test_route_plan_retires_only_backend_proven_absent_ledger_identity(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    runtime_binding = config["runtime_binding"]
    assert isinstance(runtime_binding, dict)
    target = VMHARouteTarget.model_validate(runtime_binding["route_targets"][0])

    class AbsentLedgerBackend(FakeRouteBackend):
        def __init__(self) -> None:
            super().__init__()
            self.synchronized: dict[str, ManagedRouteOwnership] | None = None

        def stably_absent_ledger_route_ids(
            self,
            ownership: dict[str, ManagedRouteOwnership],
        ) -> frozenset[str]:
            assert set(ownership) == {"route-obsolete"}
            return frozenset({"route-obsolete"})

        def synchronize_authority_labels(
            self,
            ownership: dict[str, ManagedRouteOwnership],
        ) -> None:
            self.synchronized = dict(ownership)

    backend = AbsentLedgerBackend()
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
        data_plane_factory=FakeDataPlane,
    )
    ports.routes.store.save_ledger(
        {
            "route-obsolete": ManagedRouteOwnership(
                "cluster-a",
                ManagedRouteKind.STATIC,
                target,
            )
        }
    )
    ownership = VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-a",
        observed_owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
    )

    ports.routes._plan(
        ownership,
        RouteTransitionState(0.0),
        prepare_authority=True,
    )

    assert ports.routes.store.load_ledger() == {}
    assert backend.synchronized == {}
    ports.close()


@pytest.mark.parametrize("conflict", ["cluster", "target"])
def test_route_plan_never_retypes_foreign_migration_authority(
    tmp_path: Path,
    conflict: str,
) -> None:
    config = _runtime_config(tmp_path)
    static_json = json.dumps(
        [{"connection": "site-a", "remote_prefixes": ["10.20.0.0/16"]}],
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = config["generation"]
    readiness = config["readiness"]
    runtime_binding = config["runtime_binding"]
    assert isinstance(generation, dict)
    assert isinstance(readiness, dict)
    assert isinstance(runtime_binding, dict)
    manifests = generation["logical_manifests"]
    digests = generation["digests"]
    assert isinstance(manifests, dict)
    assert isinstance(digests, dict)
    static_digest = hashlib.sha256(static_json.encode()).hexdigest()
    manifests["static_routes_json"] = static_json
    digests["static_routes"] = static_digest
    readiness_digests = readiness["digests"]
    assert isinstance(readiness_digests, dict)
    readiness_digests["static_routes"] = static_digest
    runtime_binding["static_routes_digest"] = static_digest
    target = VMHARouteTarget.model_validate(runtime_binding["route_targets"][0])
    runtime_binding["migration_routes"] = [
        {
            "allocation_id": "allocation-a",
            "name": "vpngw-10.20.0.0-16",
            "prefix": "10.20.0.0/16",
            "resource_revision": "7",
            "route_id": "route-old",
            "route_target": target.model_dump(mode="json"),
        }
    ]
    foreign_target = (
        target.model_copy(update={"route_table_id": "route-table-foreign"})
        if conflict == "target"
        else target
    )
    ports = build_runtime_ports(
        config,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    original = ManagedRouteOwnership(
        "cluster-foreign" if conflict == "cluster" else "cluster-a",
        ManagedRouteKind.BGP,
        foreign_target,
    )
    ports.routes.store.save_ledger({"route-old": original})
    ownership = VerifiedAllocationOwnership(
        cluster_id="cluster-a",
        candidate_node_id="node-a",
        observed_owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
    )

    with pytest.raises(RuntimeError, match="conflicts with the durable ledger"):
        ports.routes._plan(
            ownership,
            RouteTransitionState(0.0),
            prepare_authority=True,
        )

    assert ports.routes.store.load_ledger()["route-old"] == original
    ports.close()


def test_factory_connects_to_runtime_ip_but_authenticates_stable_node_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, tls: object, **kwargs: object) -> None:
            observed["server_hostname"] = tls.server_hostname  # type: ignore[attr-defined]
            observed.update(kwargs)

        def send(self, _heartbeat: object) -> None:
            return None

        def receive(self, *, timeout_seconds: float) -> object:
            del timeout_seconds
            raise AssertionError("transport receive is not part of this construction test")

    monkeypatch.setattr(
        "nebius_vpngw.agent.vm_ha.runtime.MutualTLSPeerTransport",
        FakeTransport,
    )
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )

    assert observed["peer_host"] == "127.0.0.1"
    assert observed["server_hostname"] == "node-b"
    assert observed["expected_peer_node_id"] == "node-b"
    ports.close()


def test_ports_emit_secret_free_fresh_heartbeat_and_restore_shutdown_guard(
    tmp_path: Path,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
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
    ports.data_plane.current = DataPlaneMode.ACTIVE  # type: ignore[attr-defined]

    heartbeat = ports.heartbeat(boot_id="boot-a", sequence=4, clock=10.0)
    ports.install_shutdown_guard(boot_id="boot-a")

    assert heartbeat.node_id == "node-a"
    assert heartbeat.sequence == 4
    assert heartbeat.observed_owner_id == "node-a"
    assert heartbeat.mtls_epoch == 1
    assert heartbeat.certificate_fingerprint == "d" * 64
    assert heartbeat.promotion_ready is True
    assert "credentials" not in heartbeat.to_dict()
    assert ports.data_plane.mode() is DataPlaneMode.BLOCKED
    ports.close()


@pytest.mark.parametrize(
    ("former_exact", "candidate_absent", "promotion_ready"),
    ((True, True, True), (False, True, False), (True, False, False)),
)
def test_passive_heartbeat_reports_role_neutral_exact_standby_readiness(
    tmp_path: Path,
    former_exact: bool,
    candidate_absent: bool,
    promotion_ready: bool,
) -> None:
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    ports.cloud.observe = lambda: CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-b",
        former_owner_node_id="node-b",
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=False,
        candidate_attachment_exact=False,
        ownership_re_read_exact=False,
        ownership_epoch="7",
        former_attachment_exact=former_exact,
        candidate_attachment_absent=candidate_absent,
    )
    ports.routes.readiness = lambda: LocalReadiness(True, True, True, True)
    ports.data_plane.current = DataPlaneMode.PASSIVE  # type: ignore[attr-defined]

    heartbeat = ports.heartbeat(boot_id="boot-a", sequence=5, clock=11.0)

    assert heartbeat.promotion_ready is promotion_ready
    assert heartbeat.to_dict()["schema"] == "nebius-vpngw/vm-ha-heartbeat-v3"
    assert heartbeat.auto_healing_policy_state == "blocked"
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
    assert not tuple((tmp_path / "state").rglob("private-key.pem"))


def test_factory_rejects_non_private_credential_before_sdk_creation(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    runtime_binding = config["runtime_binding"]
    assert isinstance(runtime_binding, dict)
    nodes = runtime_binding["nodes"]
    assert isinstance(nodes, list)
    node = nodes[0]
    assert isinstance(node, dict)
    credentials_path = node["nebius_credentials_path"]
    assert isinstance(credentials_path, str)
    Path(credentials_path).chmod(0o644)
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
            if "--list-sas" in argv:
                return CommandResult(0, "list-sas reply {}\n")
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
    assert any(
        argv
        == (
            DataPlaneCommandSet().swanctl,
            "--load-conns",
            "--file",
            DataPlaneCommandSet().empty_swanctl_config,
        )
        for argv, _timeout in calls
    )
    assert json.loads((tmp_path / "data-plane.json").read_text())["boot_id"] == "boot-a"
    assert os.stat(tmp_path / "data-plane.json").st_mode & 0o077 == 0


def test_local_data_plane_guard_terminates_each_exact_ike_sa_before_proving_empty(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    forwarding = "1"
    list_count = 0

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding, list_count
        calls.append(tuple(argv))
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().swanctl:
            if "--list-sas" in argv:
                list_count += 1
                if list_count == 1:
                    return CommandResult(
                        0,
                        "list-sa event {tunnel-a {uniqueid=15 version=2 "
                        "child-sas {tunnel-a-1 {uniqueid=34}}}}\n"
                        "list-sa event {tunnel-a {uniqueid=13 version=2}}\n"
                        "list-sas reply {}\n",
                    )
                return CommandResult(0, "list-sas reply {}\n")
            if "--terminate" in argv:
                return CommandResult(1)
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

    swanctl_calls = [argv for argv in calls if argv[0] == DataPlaneCommandSet().swanctl]
    assert swanctl_calls == [
        (
            DataPlaneCommandSet().swanctl,
            "--load-conns",
            "--file",
            DataPlaneCommandSet().empty_swanctl_config,
        ),
        (DataPlaneCommandSet().swanctl, "--list-sas", "--raw"),
        (
            DataPlaneCommandSet().swanctl,
            "--terminate",
            "--ike-id",
            "15",
            "--timeout",
            "5",
        ),
        (
            DataPlaneCommandSet().swanctl,
            "--terminate",
            "--ike-id",
            "13",
            "--timeout",
            "5",
        ),
        (DataPlaneCommandSet().swanctl, "--list-sas", "--raw"),
    ]
    assert runtime.mode() is DataPlaneMode.BLOCKED


def test_local_data_plane_guard_stops_booting_strongswan_when_vici_is_not_ready(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    forwarding = "1"
    strongswan_active = True

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding, strongswan_active
        calls.append(tuple(argv))
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().systemctl:
            if argv[1:3] == ("is-active", "--quiet"):
                service = argv[3]
                return CommandResult(
                    0 if service == "strongswan-starter" and strongswan_active else 3
                )
            if argv[1:3] == ("stop", "strongswan-starter"):
                strongswan_active = False
                return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().swanctl and "--load-conns" in argv:
            return CommandResult(1, "VICI is not ready")
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
    assert (DataPlaneCommandSet().systemctl, "stop", "strongswan-starter") in calls
    assert not strongswan_active


@pytest.mark.parametrize(
    "observed",
    [
        "not a raw VICI response",
        "list-sa event {tunnel-a {version=2}}\nlist-sas reply {}\n",
        "list-sa event {tunnel-a {uniqueid=7 version=2}}\n"
        "list-sa event {tunnel-b {uniqueid=7 version=2}}\nlist-sas reply {}\n",
    ],
)
def test_local_data_plane_guard_rejects_malformed_ike_sa_observation(
    tmp_path: Path, observed: str
) -> None:
    forwarding = "1"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().swanctl:
            if "--list-sas" in argv:
                return CommandResult(0, observed)
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
        ActionKind.INSTALL_COLD_START_GUARD,
        "guard-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    with pytest.raises(RuntimeError, match="IKE SA observation is malformed"):
        runtime.install_guard(action)


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
        if argv[0] == DataPlaneCommandSet().python:
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
    assert (
        DataPlaneCommandSet().python,
        "-m",
        "nebius_vpngw.agent.main",
        "--vm-ha-materialized",
    ) in calls
    assert not any("--terminate" in argv for argv in calls)
    assert (
        DataPlaneCommandSet().systemctl,
        "reload-or-restart",
        "nebius-vpngw-agent",
    ) in calls


def test_static_passive_rearm_finishes_with_no_loaded_connection_or_ike_sa(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    forwarding = "1"
    ike_ids = [7]

    def raw_sas() -> str:
        events = "".join(
            f"list-sa event {{static-tunnel {{uniqueid={ike_id} version=2}}}}\n"
            for ike_id in ike_ids
        )
        return f"{events}list-sas reply {{}}\n"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        command = tuple(argv)
        calls.append(command)
        if argv[0] == DataPlaneCommandSet().systemctl:
            if argv[1:3] == ("reload-or-restart", "nebius-vpngw-agent"):
                ike_ids[:] = [8]
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().python:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().swanctl:
            if "--list-sas" in argv:
                return CommandResult(0, raw_sas())
            if "--terminate" in argv:
                ike_ids.remove(int(argv[argv.index("--ike-id") + 1]))
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        tunnel_cold_passive=True,
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
    assert ike_ids == []
    assert sum("--terminate" in argv for argv in calls) == 2
    assert not any("--load-all" in argv for argv in calls)


def test_static_candidate_tunnel_activation_keeps_forwarding_fenced(
    tmp_path: Path,
) -> None:
    forwarding = "0"
    ike_ids: list[int] = []
    calls: list[tuple[str, ...]] = []

    def raw_sas() -> str:
        events = "".join(
            f"list-sa event {{static-tunnel {{uniqueid={ike_id} version=2}}}}\n"
            for ike_id in ike_ids
        )
        return f"{events}list-sas reply {{}}\n"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding
        command = tuple(argv)
        calls.append(command)
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().swanctl:
            if "--load-all" in argv:
                ike_ids[:] = [9]
                return CommandResult(0)
            if "--list-sas" in argv:
                return CommandResult(0, raw_sas())
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        tunnel_cold_passive=True,
    )
    passive = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive-op",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    runtime._write_state(passive, DataPlaneMode.PASSIVE)
    runtime._write_guard(passive, DataPlaneMode.PASSIVE)
    prepare = replace(
        passive,
        kind=ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        operation_id="prepare-op",
    )

    runtime.prepare_candidate(prepare)

    assert forwarding == "0"
    assert runtime.mode() is DataPlaneMode.PASSIVE
    assert ike_ids == [9]
    assert (DataPlaneCommandSet().swanctl, "--load-all", "--noprompt") in calls


def test_local_data_plane_failed_passive_hygiene_replays_from_blocked(
    tmp_path: Path,
) -> None:
    forwarding = "1"
    materialization_attempts = 0

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        nonlocal forwarding, materialization_attempts
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding = argv[-1].rsplit("=", 1)[-1]
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().python:
            materialization_attempts += 1
            return CommandResult(1 if materialization_attempts == 1 else 0)
        if argv[0] == DataPlaneCommandSet().swanctl:
            return CommandResult(0)
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        passive_materialization_attempts=1,
        bgp_export_attempts=1,
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

    with pytest.raises(RuntimeError, match="materialization did not converge"):
        runtime.enter_passive(action)

    assert runtime.mode() is DataPlaneMode.BLOCKED
    assert json.loads((tmp_path / "guard.json").read_text())["data_plane_mode"] == "blocked"

    runtime.enter_passive(action)

    assert materialization_attempts == 2
    assert runtime.mode() is DataPlaneMode.PASSIVE
    assert forwarding == "0"


def test_local_data_plane_observes_text_only_iproute2_xfrm_output(tmp_path: Path) -> None:
    peer = "169.254.0.2"
    prefix = "10.10.0.0/24"
    policy_digest = _policy_digest({peer: (prefix,)})

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        if argv[0] == DataPlaneCommandSet().systemctl:
            return CommandResult(0)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, "0\n")
        if argv[0] == DataPlaneCommandSet().vtysh:
            command = argv[-1]
            if command == "show bgp summary json":
                return CommandResult(
                    0, json.dumps({"ipv4Unicast": {"peers": {peer: {"state": "Established"}}}})
                )
            if command == "show bgp ipv4 unicast json":
                return CommandResult(0, json.dumps({"routes": {prefix: [{}]}}))
            if command == (f"show bgp ipv4 unicast neighbors {peer} advertised-routes json"):
                return CommandResult(
                    0,
                    json.dumps({"advertisedRoutes": {}, "totalPrefixCounter": 0}),
                )
            if command == "show running-config":
                return CommandResult(
                    0,
                    f"ip prefix-list peer-in seq 10 permit {prefix}\n"
                    f"router bgp 65000\n neighbor {peer} prefix-list peer-in in\n",
                )
        if argv[0] == DataPlaneCommandSet().ip:
            command = tuple(argv[1:])
            if command in {
                ("rule", "show"),
                ("route", "show", "169.254.0.0/16"),
            }:
                return CommandResult(0, "")
            if command == ("-j", "-4", "route", "show", "table", "all"):
                return CommandResult(
                    0,
                    json.dumps(
                        [
                            {"dst": prefix, "dev": "xfrm0"},
                            {"dst": "fe80::/64", "dev": "xfrm0"},
                        ]
                    ),
                )
            if command == ("-j", "xfrm", "state"):
                return CommandResult(0, "src 192.0.2.10 dst 192.0.2.20\n\tif_id 0x1\n")
            if command == ("-j", "xfrm", "policy"):
                return CommandResult(0, "src 0.0.0.0/0 dst 0.0.0.0/0\n\tif_id 1\n")
            if command == ("-d", "-j", "link", "show", "type", "xfrm"):
                return CommandResult(
                    0,
                    json.dumps(
                        [
                            {
                                "ifname": "xfrm0",
                                "linkinfo": {"info_data": {"if_id": "0x1"}},
                            }
                        ]
                    ),
                )
        raise AssertionError(argv)

    observed = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(peer,),
        expected_bgp_policy_digest=policy_digest,
        runner=runner,
    ).observe()

    assert observed.service_healthy
    assert observed.established_bgp_sessions == frozenset({peer})
    assert observed.learned_bgp_prefixes == frozenset({prefix})
    assert observed.usable_xfrm_prefixes == frozenset({prefix})
    assert observed.observed_bgp_policy_digest == policy_digest
    assert observed.routing_hygiene_ready is True


@pytest.mark.parametrize(
    ("rules", "all_routes", "broad_apipa", "expected"),
    (
        (CommandResult(0, ""), CommandResult(0, "[]"), CommandResult(0, ""), True),
        (
            CommandResult(0, "220: from all lookup 220\n"),
            CommandResult(0, "[]"),
            CommandResult(0, ""),
            False,
        ),
        (
            CommandResult(0, "220: from all lookup main\n100: from all lookup 2200\n"),
            CommandResult(0, "[]"),
            CommandResult(0, ""),
            True,
        ),
        (
            CommandResult(0, ""),
            CommandResult(0, '[{"table":220,"dst":"10.10.0.0/24"}]'),
            CommandResult(0, ""),
            False,
        ),
        (
            CommandResult(0, ""),
            CommandResult(0, "[]"),
            CommandResult(0, "169.254.0.0/16 dev eth0\n"),
            False,
        ),
        (CommandResult(1, ""), CommandResult(0, "[]"), CommandResult(0, ""), False),
        (CommandResult(0, ""), CommandResult(1, ""), CommandResult(0, ""), False),
        (CommandResult(0, ""), CommandResult(0, "not-json"), CommandResult(0, ""), False),
    ),
)
def test_routing_hygiene_observation_is_exact_and_fail_closed(
    rules: CommandResult,
    all_routes: CommandResult,
    broad_apipa: CommandResult,
    expected: bool,
) -> None:
    assert _routing_hygiene_ready(rules, all_routes, broad_apipa) is expected


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


def test_active_preparation_failure_keeps_forwarding_fenced(tmp_path: Path) -> None:
    forwarding = "0"

    def runner(argv: tuple[str, ...], _timeout: float) -> CommandResult:
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding)
        raise AssertionError(f"forwarding changed before active preparation: {argv}")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        active_preparer=lambda: (_ for _ in ()).throw(RuntimeError("firewall failed")),
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

    with pytest.raises(RuntimeError, match="firewall failed"):
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


def test_historical_incarnation_route_reconcile_allows_running_healthy_standby(
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
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=True,
        candidate_attachment_exact=True,
        ownership_re_read_exact=True,
        ownership_epoch="7",
    )
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        "boot-a:9:reconcile-routes:node-a",
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
        ownership_incarnation=3,
        takeover_fence_required=False,
    )

    ports.routes.reconcile(action)

    receipt = ports.routes.receipt_context()
    assert receipt is not None
    assert receipt.ownership_incarnation == 3
    ports.close()


def test_credential_bundle_rejects_noncanonical_runtime_identity(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])

    with pytest.raises(ValueError, match="non-canonical path"):
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

    assert store.load_pending_mutation() == PendingRouteMutation(mutation, context)
    assert os.stat(store.pending_path).st_mode & 0o077 == 0


def test_route_pending_v1_is_read_without_rewrite(tmp_path: Path) -> None:
    store = RuntimeStateStore(tmp_path / "state")
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    context = RouteReconciliationContext(
        operation_id="legacy-route-op",
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
    payload = {
        "context": context.to_dict(),
        "mutation": {
            "allocation_id": "allocation-a",
            "cluster_id": "cluster-a",
            "kind": "replace",
            "prefix": "10.0.0.0/24",
            "route_id": "route-old",
            "route_kind": "static",
            "route_target": target.model_dump(mode="json"),
        },
        "schema": "nebius-vpngw/vm-ha-route-mutation-intent-v1",
    }
    store.root.mkdir(mode=0o700, parents=True)
    original = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    store.pending_path.write_bytes(original)

    pending = store.load_pending_mutation()

    assert pending is not None
    assert pending.record_version == 1
    assert pending.phase is RouteMutationPhase.INTENT
    assert pending.mutation.rollback is None
    assert store.pending_path.read_bytes() == original


def test_route_pending_v2_checkpoints_accepted_operation(tmp_path: Path) -> None:
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
    accepted = AcceptedRouteOperation("action-op", "create", "cloud-op")
    store.save_pending_mutation(mutation, context)
    initial = store.load_pending_mutation()
    assert initial is not None

    successor = store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.CREATE_ACCEPTED,
        rollback=None,
        accepted_operation=accepted,
    )

    assert successor.accepted_operation == accepted
    assert store.load_pending_mutation() == successor


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
    owner = ManagedRouteOwnership("cluster-a", ManagedRouteKind.STATIC, target)
    ledger = {"old-route": owner}
    ports.routes.store.save_ledger(ledger)

    ports.routes._apply_mutation(mutation, ledger)

    assert ports.routes.store.load_pending_mutation() is None
    ports.close()


@pytest.mark.parametrize(
    (
        "mutation_kind",
        "terminal_phase",
        "successor_route_id",
        "successor_kind",
        "deleted_route_absent",
        "expected_error",
    ),
    [
        (
            RouteMutationKind.DELETE,
            RouteMutationPhase.ORIGINAL_ABSENT,
            None,
            ManagedRouteKind.STATIC,
            True,
            None,
        ),
        (
            RouteMutationKind.DELETE,
            RouteMutationPhase.ORIGINAL_ABSENT,
            None,
            ManagedRouteKind.STATIC,
            False,
            "completed route deletion changed",
        ),
        (
            RouteMutationKind.REPLACE,
            RouteMutationPhase.DESIRED_PRESENT,
            "route-new",
            ManagedRouteKind.STATIC,
            True,
            None,
        ),
        (
            RouteMutationKind.REPLACE,
            RouteMutationPhase.DESIRED_PRESENT,
            "route-new",
            ManagedRouteKind.BGP,
            True,
            "exact successor management authority",
        ),
    ],
)
def test_terminal_route_effect_recovers_after_ledger_write_before_pending_clear(
    tmp_path: Path,
    mutation_kind: RouteMutationKind,
    terminal_phase: RouteMutationPhase,
    successor_route_id: str | None,
    successor_kind: ManagedRouteKind,
    deleted_route_absent: bool,
    expected_error: str | None,
) -> None:
    state_dir = tmp_path / "state"
    first = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=state_dir,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: FakeRouteBackend(),
        data_plane_factory=FakeDataPlane,
    )
    target = first.binding.route_targets[0]
    rollback = (
        RouteRollbackSnapshot(
            route_id="route-old",
            resource_version="7",
            name="customer-route",
            labels=(),
            description="",
            prefix="10.0.0.0/24",
            allocation_id="old-allocation",
            route_target=target,
        )
        if mutation_kind is RouteMutationKind.REPLACE
        else None
    )
    mutation = RouteMutation(
        kind=mutation_kind,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
        route_id="route-old",
        rollback=rollback,
    )
    context = RouteReconciliationContext(
        operation_id="route-op-before-restart",
        cluster_id="cluster-a",
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest=first.binding.static_routes_digest,
        bgp_policy_digest=first.binding.bgp_policy_digest,
        ownership_incarnation=2,
    )
    first.routes.store.save_pending_mutation(mutation, context)
    pending = first.routes.store.load_pending_mutation()
    assert pending is not None
    terminal = first.routes.store.checkpoint_pending_mutation(
        pending,
        phase=terminal_phase,
        rollback=rollback,
        accepted_operation=None,
    )
    owner = ManagedRouteOwnership("cluster-a", successor_kind, target)
    first.routes.store.save_ledger(
        {} if successor_route_id is None else {successor_route_id: owner}
    )
    first.close()

    class TerminalBackend(FakeRouteBackend):
        def __init__(self) -> None:
            super().__init__()
            self.applied: list[RouteMutation] = []

        def apply_mutation(self, candidate: RouteMutation) -> str | None:
            self.applied.append(candidate)
            return successor_route_id

        def recover_deleted_route(self, candidate: RouteMutation) -> bool:
            assert candidate == mutation
            return deleted_route_absent

    backend = TerminalBackend()
    restarted = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=state_dir,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
        data_plane_factory=FakeDataPlane,
    )
    assert restarted.routes.store.load_pending_mutation() == terminal
    ledger = restarted.routes.store.load_ledger()

    if expected_error is not None:
        with pytest.raises(RuntimeError, match=expected_error):
            restarted.routes._apply_mutation(mutation, ledger)
        if mutation_kind is RouteMutationKind.DELETE:
            assert backend.applied == []
        assert restarted.routes.store.load_pending_mutation() == terminal
        assert restarted.routes.store.load_ledger() == (
            {} if successor_route_id is None else {successor_route_id: owner}
        )
        restarted.close()
        return

    restarted.routes._apply_mutation(mutation, ledger)

    assert backend.applied == ([mutation] if mutation_kind is RouteMutationKind.REPLACE else [])
    assert restarted.routes.store.load_pending_mutation() is None
    assert restarted.routes.store.load_ledger() == (
        {} if successor_route_id is None else {successor_route_id: owner}
    )
    restarted.close()


@pytest.mark.parametrize(
    ("mutation_kind", "route_id"),
    [
        (RouteMutationKind.CREATE, None),
        (RouteMutationKind.DELETE, "old-route"),
        (RouteMutationKind.REPLACE, "old-route"),
    ],
)
def test_pending_route_mutation_recovers_across_new_controller_operation(
    tmp_path: Path,
    mutation_kind: RouteMutationKind,
    route_id: str | None,
) -> None:
    backend = FakeRouteBackend()
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
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
    target = ports.binding.route_targets[0]
    mutation = RouteMutation(
        kind=mutation_kind,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
        route_id=route_id,
        rollback=(
            RouteRollbackSnapshot(
                route_id="old-route",
                resource_version="7",
                name="customer-route",
                labels=(),
                description="",
                prefix="10.0.0.0/24",
                allocation_id="old-allocation",
                route_target=target,
            )
            if mutation_kind is RouteMutationKind.REPLACE
            else None
        ),
    )
    old_context = RouteReconciliationContext(
        operation_id="route-op-before-reboot",
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
    if route_id is not None:
        ports.routes.store.save_ledger(
            {
                route_id: ManagedRouteOwnership(
                    "cluster-a",
                    ManagedRouteKind.STATIC,
                    target,
                )
            }
        )
    ports.routes.store.save_pending_mutation(mutation, old_context)
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        "route-op-after-reboot",
        "boot-after-reboot",
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

    ports.routes.reconcile(action)

    assert ports.routes.store.load_pending_mutation() is None
    receipt = ports.routes.store.load_route_reconciliation_receipt()
    assert receipt is not None
    assert receipt["context"]["operation_id"] == "route-op-after-reboot"
    assert backend.reconciliation_operation_ids == [
        "route-op-after-reboot",
        "route-op-before-reboot",
        "route-op-after-reboot",
    ]
    ports.close()


def test_pending_route_mutation_rejects_changed_reboot_authority(tmp_path: Path) -> None:
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
    mutation = RouteMutation(
        kind=RouteMutationKind.DELETE,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=ports.binding.route_targets[0],
        route_id="old-route",
    )
    ports.routes.store.save_pending_mutation(
        mutation,
        RouteReconciliationContext(
            operation_id="route-op-before-reboot",
            cluster_id="cluster-a",
            owner_node_id="node-a",
            allocation_id="allocation-a",
            ownership_epoch="7",
            generation_id="b" * 64,
            configuration_digest="a" * 64,
            static_routes_digest=ports.binding.static_routes_digest,
            bgp_policy_digest=ports.binding.bgp_policy_digest,
            ownership_incarnation=2,
        ),
    )
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        "route-op-after-reboot",
        "boot-after-reboot",
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

    with pytest.raises(RuntimeError, match="does not match the current controller authority"):
        ports.routes.reconcile(action)

    assert ports.routes.store.load_pending_mutation() is not None
    ports.close()


def test_compensated_route_replacement_requires_a_new_controller_operation(
    tmp_path: Path,
) -> None:
    backend = FakeRouteBackend()
    ports = build_runtime_ports(
        _runtime_config(tmp_path),
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **_kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        route_backend_factory=lambda _sdk: backend,
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
    target = ports.binding.route_targets[0]
    rollback = RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="7",
        name="customer-route",
        labels=(),
        description="",
        prefix="10.0.0.0/24",
        allocation_id="old-allocation",
        route_target=target,
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
        route_id="route-old",
        rollback=rollback,
    )
    context = RouteReconciliationContext(
        operation_id="route-op-before-reboot",
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
    owner = ManagedRouteOwnership("cluster-a", ManagedRouteKind.STATIC, target)
    ports.routes.store.save_ledger({"route-old": owner})
    initial = ports.routes.store.load_pending_mutation()
    assert initial is not None
    compensated = ports.routes.store.checkpoint_pending_mutation(
        initial,
        phase=RouteMutationPhase.RESTORED,
        rollback=rollback,
        accepted_operation=None,
    )
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        context.operation_id,
        "boot-before-reboot",
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

    with pytest.raises(RuntimeError, match="terminally compensated"):
        ports.routes.reconcile(action)

    assert ports.routes.store.load_pending_mutation() == compensated

    ports.routes.reconcile(replace(action, operation_id="route-op-after-reboot"))

    assert ports.routes.store.load_pending_mutation() is None
    assert ports.routes.store.load_ledger() == {"route-restored": owner}
    assert backend.reconciliation_operation_ids == [
        "route-op-before-reboot",
        "route-op-before-reboot",
        "route-op-after-reboot",
        "route-op-before-reboot",
        "route-op-after-reboot",
    ]
    ports.close()


def test_replacement_recovery_rejects_a_different_ledger_kind(tmp_path: Path) -> None:
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
    rollback = RouteRollbackSnapshot(
        route_id="route-old",
        resource_version="7",
        name="customer-route",
        labels=(),
        description="",
        prefix="10.0.0.0/24",
        allocation_id="old-allocation",
        route_target=target,
    )
    mutation = RouteMutation(
        kind=RouteMutationKind.REPLACE,
        prefix="10.0.0.0/24",
        route_kind=ManagedRouteKind.STATIC,
        allocation_id="allocation-a",
        cluster_id="cluster-a",
        route_target=target,
        route_id="route-old",
        rollback=rollback,
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
    ledger = {
        "route-old": ManagedRouteOwnership(
            "cluster-a",
            ManagedRouteKind.BGP,
            target,
        )
    }
    ports.routes.store.save_ledger(ledger)

    with pytest.raises(RuntimeError, match="exact durable management authority"):
        ports.routes._apply_mutation(mutation, ledger)

    assert ports.routes.store.load_pending_mutation() is not None
    ports.close()
