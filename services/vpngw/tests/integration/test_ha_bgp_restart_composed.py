"""Compose restart selection, native evidence, rendering and VM activation.

Only the command transport and cloud provider are fixtures. Advertisements and
selected routes follow the rendered FRR policy, never the selector's intent.
"""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_ha_tunnel_selection import Effects, config, observation
from tests.unit.test_vm_ha_controller import _owned_cloud, _peer, _snapshot
from tests.unit.test_vm_ha_runtime_ports import (
    FakeCredentialBundle,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _runtime_config,
)

from nebius_vpngw.agent import frr_renderer, main, strongswan_renderer
from nebius_vpngw.agent.ordinary import frr_policy_rows
from nebius_vpngw.agent.vm_ha import tunnel_runtime
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import (
    CommandResult,
    SystemDataPlaneRuntime,
    build_runtime_ports,
)
from nebius_vpngw.agent.vm_ha.tunnels import Authority, TunnelController, read_state
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ConfiguredRole,
    DataPlaneMode,
    LocalReadiness,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
)

pytestmark = pytest.mark.integration


class NativeCommands:
    def __init__(self, path):
        self.path = path
        self.applied = ""
        self.forwarding = False
        self.retain_stale_routes = False
        self.peers = {"169.254.1.2": "Established", "169.254.2.2": "Established"}
        self.trace = []
        self.nft = []
        self.loaded_connections = ["preferred", "backup"]
        first = observation()
        second = observation()
        for key in ("sas", "states", "policies"):
            second[key] = (
                second[key]
                .replace("preferred", "backup")
                .replace("192.0.2.1", "192.0.2.3")
                .replace('"reqid":"1"', '"reqid":"2"')
                .replace("reqid 1", "reqid 2")
                .replace('"uniqueid":"1"', '"uniqueid":"2"')
                .replace("0x64", "0x65")
                .replace('"if-id-in":"00000064"', '"if-id-in":"00000065"')
                .replace('"if-id-out":"00000064"', '"if-id-out":"00000065"')
                .replace("00000001", "00000003")
                .replace("00000002", "00000004")
                .replace("spi 0x1", "spi 0x3")
                .replace("spi 0x2", "spi 0x4")
            )
        self.evidence = {key: first[key] + second[key] for key in ("sas", "states", "policies")}
        sas = json.loads(first["sas"])
        sas["sas"].extend(json.loads(second["sas"])["sas"])
        self.evidence["sas"] = json.dumps(sas)
        self.links = first["links"] + [
            {
                "ifname": "xfrm1",
                "flags": ["UP"],
                "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 101}},
            }
        ]

    def advertised(self, peer):
        match = re.search(rf"neighbor {re.escape(peer)} route-map (\S+) out", self.applied)
        assert match is not None
        prefixes = re.findall(r"ip prefix-list ADVERTISE-LOCAL seq \d+ permit (\S+)", self.applied)
        return [] if match[1] == "ADVERTISE-NONE" else prefixes

    def command(self, argv):
        argv = (Path(argv[0]).name, *argv[1:])
        if argv[0] == "sysctl":
            if argv[1] == "-w":
                self.forwarding = argv[2].endswith("=1")
                self.trace.append(("forward", self.forwarding))
            return str(int(self.forwarding))
        if argv[0] == "systemctl":
            return "active" if "show" in argv else ""
        if argv[:3] == ("python3", "-m", "nebius_vpngw.ipsec_observation"):
            if argv[3:] == ("--configuration",):
                return json.dumps(
                    {
                        "connections": self.loaded_connections,
                        "shared_keys": ["ike-psk-1", "ike-psk-2"],
                    }
                )
            if argv[3:] == ("--connections",):
                return json.dumps(self.loaded_connections)
            return self.evidence["sas"]
        if argv[0] == "nft":
            if "-f" in argv and "--check" not in argv:
                batch = json.loads(Path(argv[-1]).read_text())["nftables"]
                self.nft = [item["add"] for item in batch[2:]]
            return json.dumps({"nftables": self.nft})
        if argv == ("python3", "-m", "nebius_vpngw.agent.main", "--vm-ha-materialized"):
            return ""
        if argv == ("vtysh", "-b"):
            self.applied = self.path.read_text()
            self.trace.append(("policy", self.forwarding))
            return ""
        if argv[0] == "vtysh":
            query = argv[2]
            if query.startswith("clear bgp"):
                return ""
            if query == "show running-config":
                return self.applied
            if query == "show bgp summary json":
                return json.dumps(
                    {
                        "ipv4Unicast": {
                            "peers": {peer: {"state": state} for peer, state in self.peers.items()}
                        }
                    }
                )
            if "advertised-routes" in query:
                peer = query.split()[5]
                prefixes = self.advertised(peer)
                self.trace.append(("exports", self.forwarding, peer, tuple(prefixes)))
                return json.dumps(
                    {
                        "advertisedRoutes": dict.fromkeys(prefixes, {}),
                        "totalPrefixCounter": len(prefixes),
                    }
                )
            return json.dumps({"routes": {"10.1.0.0/16": [{"valid": True}]}})
        if argv[0] == "swanctl":
            return self.evidence["sas"]
        if argv[1:4] == ("-j", "xfrm", "state"):
            return self.evidence["states"]
        if argv[1:4] == ("-j", "xfrm", "policy"):
            return self.evidence["policies"]
        if "link" in argv:
            return json.dumps(self.links)
        if argv[1:3] == ("rule", "show"):
            return "0: from all lookup local\n32766: from all lookup main\n32767: from all lookup default\n"
        if "169.254.0.0/16" in argv:
            return ""
        if "route" in argv:
            usable = [
                peer
                for peer, state in self.peers.items()
                if state == "Established" or self.retain_stale_routes
            ]
            preferred = re.findall(r"neighbor (\S+) route-map SET-LOCAL-PREF-200 in", self.applied)
            peer = next((peer for peer in preferred if peer in usable), usable[0])
            return json.dumps(
                [
                    {
                        "dst": "10.1.0.0/16",
                        "dev": "xfrm0" if peer == "169.254.1.2" else "xfrm1",
                        "gateway": peer,
                        "protocol": "bgp",
                    }
                ]
            )
        raise AssertionError(argv)

    def run(self, argv, timeout):
        return CommandResult(0, self.command(argv))


def composed(tmp_path, monkeypatch, *, advertise=True, protected_checkpoint=True):
    cfg = config("bgp")
    cfg["gateway"]["local_prefixes"] = ["10.9.0.0/16"]
    cfg["connections"][0]["bgp"] = {"advertise_local_prefixes": advertise}
    cfg["connections"][0]["tunnels"][1]["remote_public_ip"] = "192.0.2.3"
    vm = _runtime_config(tmp_path)
    manifest = json.dumps([{"connection": "site", "remote_prefixes": ["10.1.0.0/16"]}])
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    vm["runtime_binding"]["bgp_policy_digest"] = digest
    vm["generation"]["logical_manifests"]["bgp_policy_json"] = manifest
    vm["generation"]["digests"]["bgp_policy"] = digest
    vm["readiness"]["digests"]["bgp_policy"] = digest
    cfg["vm_ha"] = vm
    vm.update(connections=cfg["connections"], gateway=cfg["gateway"])
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    swanctl_path = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", swanctl_path)
    monkeypatch.setattr(tunnel_runtime, "SWANCTL_CONF", swanctl_path)
    rendered = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(cfg, rendered_files=rendered)
    strongswan_renderer._write_secret_file(swanctl_path, rendered[swanctl_path])
    frr_path = tmp_path / "frr.conf"
    native = NativeCommands(frr_path)
    now = [100.0]
    monkeypatch.setattr(frr_renderer, "FRR_CONF", frr_path)
    monkeypatch.setattr(frr_renderer, "BGPD_CONF", tmp_path / "bgpd.conf")
    monkeypatch.setattr(tunnel_runtime, "FRR_CONF", frr_path)
    monkeypatch.setattr(frr_renderer.FRRRenderer, "_ensure_bgpd_enabled", lambda *a, **kw: False)
    monkeypatch.setattr(frr_renderer.FRRRenderer, "ensure_local_prefix_routes", lambda *a: None)
    monkeypatch.setattr(
        frr_renderer.FRRRenderer, "_enable_vm_ha_controller_writes", lambda *a: None
    )
    monkeypatch.setattr(frr_renderer, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(main, "VM_HA_STATE_DIR", state_dir)
    monkeypatch.setattr(main, "update_firewall_from_config", lambda *a, **kw: None)

    def prepare_active():
        assert not native.forwarding
        main._prepare_vm_ha_active_data_plane(config_path)
        native.command(("vtysh", "-b"))

    def prepare_blocked():
        main._prepare_vm_ha_blocked_bgp(config_path)
        native.command(("vtysh", "-b"))

    def data_plane(**kwargs):
        return SystemDataPlaneRuntime(
            **kwargs,
            routing_lock_path=state_dir / "routing.lock",
            bgp_export_attempts=1,
            origination_verifier=lambda: (
                frr_policy_rows(native.applied) == frr_policy_rows(frr_path.read_text())
            ),
        )

    class ExistingRoute(FakeRouteBackend):
        def list_routes(self, target, ownership):
            return (
                ManagedRouteSnapshot(
                    "route-a",
                    "10.1.0.0/16",
                    "allocation-a",
                    ManagedRouteOwnership("cluster-a", ManagedRouteKind.BGP, target),
                ),
            )

        def apply_mutation(self, mutation):
            raise AssertionError("same-owner restart must preserve the existing route")

    ports = build_runtime_ports(
        vm,
        state_dir=state_dir,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **kw: FakeSDK(),
        credential_bundle_factory=lambda *a: FakeCredentialBundle(),
        route_backend_factory=lambda sdk: ExistingRoute(),
        data_plane_factory=data_plane,
        runner=native.run,
        active_preparer=prepare_active,
        blocked_preparer=prepare_blocked,
        clock=lambda: now[0],
    )
    cloud = replace(
        _owned_cloud(),
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
    )
    ports.cloud.observe = lambda: cloud
    digests = DigestSet("a" * 64, vm["runtime_binding"]["static_routes_digest"], digest)
    snapshots = SimpleNamespace(
        last_snapshot=None,
        providers=SimpleNamespace(apply_lock_operation_id=lambda: None),
    )

    def observe():
        snapshot = _snapshot(
            now=now[0],
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            configured_role=ConfiguredRole.ACTIVE,
            local_digests=digests,
            peer_heartbeat=replace(
                _peer(node_id="node-b", configured_role="passive"),
                cluster_id="cluster-a",
                digests=digests,
                tunnel_capability=True,
            ),
            peer_received_at=now[0],
            cloud=cloud,
            readiness=ports.routes.readiness(),
            data_plane_mode=ports.data_plane.mode(),
            routes_reconciled_context=ports.routes.receipt_context(),
            route_runtime_id=ports.binding.route_runtime_id,
        )
        snapshots.last_snapshot = snapshot
        return snapshot

    snapshots.observe = observe
    checkpoints = main.VMHACheckpointFileStore(state_dir / "controller-checkpoint.json")
    checkpoints.save(checkpoints.load())

    def apply(action):
        native.trace.append(("vm", action.kind))
        ports.handlers()[action.kind](action)

    runtime = main.VMHAControllerRuntime(
        snapshots=snapshots,
        effects=SimpleNamespace(apply=apply),
        checkpoints=checkpoints,
        status_path=state_dir / "status.json",
        guard=lambda: {},
    )
    monkeypatch.setattr(runtime, "_write_status", lambda result: result)
    tunnels = tunnel_runtime.ControllerTunnelRuntime(
        config_path, state_dir, runtime, ports, clock=lambda: now[0]
    )
    tunnels.effects.routing_lock = state_dir / "routing.lock"
    tunnels.effects.run = native.command
    tunnels.effects.observer.run = native.command
    runtime.tunnel_step = tunnels.step
    runtime.tunnel_runtime = tunnels
    authority = Authority("cluster-a", "node-a", "allocation-a", "42", 0, "a" * 64)
    effects = Effects()
    effects.current = authority
    selector = TunnelController(cfg, state_dir / "tunnel-selection.json", effects)
    selector.request("failover", "backup", authority, 99)
    selector.state["selected"] = {"site": "backup"}
    selector._save()
    # Known-good checkpoint after the product's passive materialization. Real
    # restart/Agent.reload and kernel effects are additionally covered natively.
    ports.data_plane.state_path.write_text(
        json.dumps({"schema": "nebius-vpngw/vm-ha-data-plane-v1", "mode": "passive"})
    )
    main._prepare_vm_ha_blocked_bgp(config_path)
    native.command(("vtysh", "-b"))
    if protected_checkpoint:
        ports.data_plane.destination_protection.install()
    native.trace.clear()
    return runtime, tunnels, native, selector, now, ports


@pytest.mark.parametrize("phase", ["activate", "verify", "succeeded"])
@pytest.mark.parametrize("down", [False, True])
@pytest.mark.parametrize("advertise", [False, True])
def test_fenced_restart_activates_before_tunnel_completion(
    tmp_path, monkeypatch, phase, down, advertise
):
    runtime, tunnels, native, selector, now, ports = composed(
        tmp_path, monkeypatch, advertise=advertise
    )
    selector.state["operation"].update(phase=phase, successes=1)
    selector._save()
    if down:
        native.peers["169.254.2.2"] = "Idle"
    for _ in range(8):
        runtime.step()
        state = read_state(selector.path)
        if not native.forwarding and phase != "succeeded":
            assert state["operation"]["phase"] not in {"succeeded", "failed"}
            if ports.data_plane.mode() is DataPlaneMode.ORIGINATING:
                assert ports.data_plane.origination_policy_ready()
            else:
                assert all(not native.advertised(peer) for peer in native.peers)
        now[0] += 1
    assert native.forwarding and ports.routes.readiness().promotion_ready
    state = read_state(selector.path)
    assert state["selected"] == ({} if down else {"site": "backup"})
    assert state["operation"]["phase"] == (
        "failed" if down and phase != "succeeded" else "succeeded"
    )
    forwarding_index = native.trace.index(("forward", True))
    assert any(
        event[0] == "exports" and not event[1] and (bool(event[3]) == advertise)
        for event in native.trace[:forwarding_index]
    )
    assert native.trace.index(("vm", ActionKind.ENABLE_ACTIVE)) < forwarding_index
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("advertise", [False, True])
def test_fenced_bgp_startup_installs_protection_before_origination(
    tmp_path, monkeypatch, advertise
):
    runtime, tunnels, native, selector, now, ports = composed(
        tmp_path, monkeypatch, advertise=advertise, protected_checkpoint=False
    )
    protection = ports.data_plane.destination_protection
    assert not protection.path.exists() and not protection.ready()
    assert ports.data_plane.mode() is DataPlaneMode.PASSIVE and not native.forwarding
    try:
        for _ in range(8):
            runtime.step()
            if any(native.advertised(peer) for peer in native.peers) or native.forwarding:
                protection.verify()
            now[0] += 1
        assert native.forwarding and ports.routes.readiness().promotion_ready
        assert protection.path.exists() and protection.ready()
        assert read_state(selector.path)["operation"]["phase"] == "succeeded"
    finally:
        tunnels.close()
        ports.close()


@pytest.mark.parametrize("routes_retained", [True, False])
@pytest.mark.parametrize("loss_timing", ["before-readiness", "after-readiness"])
def test_production_factory_retains_route_authority_for_missing_daemon_configuration(
    tmp_path, monkeypatch, routes_retained, loss_timing
):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    selector.state["operation"].update(phase="succeeded", successes=2)
    selector._save()
    for _ in range(8):
        runtime.step()
        now[0] += 1
    assert native.forwarding and ports.routes.readiness().promotion_ready
    assert ports.routes.receipt_context() is not None
    checkpoint = runtime.controller.checkpoints.load()
    peer = runtime.snapshots.observe().peer_heartbeat
    tunnels.close()

    # Exercise production provider wiring with real route receipts/ledger and
    # the same complete empty VICI inventory as an auto-restarted daemon.
    monkeypatch.setattr(main, "build_runtime_ports", lambda *a, **kw: ports)
    ports.peer_runtime.observe = lambda: (peer, now[0])
    production = main.build_default_vm_ha_controller_runtime(
        config_path=tmp_path / "config.yaml",
        state_dir=tmp_path / "state",
        clock=lambda: now[0],
        boot_id=lambda: "local-boot",
    )
    try:
        if loss_timing == "after-readiness":
            providers = production.controller.snapshots.providers

            def crash_after_readiness():
                readiness = providers.readiness()
                native.loaded_connections = []
                return readiness

            production.controller.snapshots.providers = replace(
                providers, readiness=crash_after_readiness
            )
        else:
            native.loaded_connections = []
        if not routes_retained:
            ports.routes.backend.list_routes = lambda *a: ()
        snapshot = production.controller.snapshots.observe()
        if loss_timing == "after-readiness":
            assert snapshot.readiness.promotion_ready
            assert snapshot.routes_reconciled_context is None
            mixed = production.controller.controller.policy.decide(snapshot, checkpoint)
            if routes_retained:
                assert mixed.action is None, mixed.reasons
                assert mixed.forwarding_enabled and mixed.state.value == "degraded"
                assert mixed.reasons == ("local-evidence-unknown",)
                assert mixed.checkpoint.repair_attempt is None
                production.controller.snapshots.last_snapshot = snapshot
                assert not production.controller._write_status(mixed)["promotion_ready"]
            else:
                assert mixed.action.kind is ActionKind.DISABLE_ACTIVE
            production.controller.snapshots.providers = providers
            snapshot = production.controller.snapshots.observe()
        assert snapshot.readiness.local_defects == ("daemon-configuration-missing:strongswan",)
        assert snapshot.routes_reconciled_context is None
        result = production.controller.controller.policy.decide(snapshot, checkpoint)
        assert result.action is not None
        assert result.action.kind is (
            ActionKind.REPAIR_LOCAL_DATAPLANE if routes_retained else ActionKind.DISABLE_ACTIVE
        ), result.reasons
        assert (snapshot.established_routes_context is not None) is routes_retained
        assert native.forwarding
        if routes_retained:
            # Writer contention may defer dispatch. Replay uses the same exact
            # retained route authority that admitted the original repair.
            replay = production.controller.controller.policy.decide(snapshot, result.checkpoint)
            assert replay.action == result.action
            for readiness in (
                LocalReadiness(False, False, False, False, sample_complete=False),
                LocalReadiness(True, False, False, False),
                LocalReadiness(True, True, True, True),
            ):
                no_defect = replace(snapshot, readiness=replace(readiness, protection_ready=True))
                retired = production.controller.controller.policy.decide(
                    no_defect, result.checkpoint
                )
                assert retired.action is None and retired.forwarding_enabled
                assert retired.checkpoint.pending_action is None
                assert retired.checkpoint.repair_attempt == result.checkpoint.repair_attempt

            # After the bounded reload, one healthy sample retains forwarding
            # while verification waits for the second. It must not dispatch an
            # Agent reload that the current-boot writer gate would reject/fence.
            native.loaded_connections = ["preferred", "backup"]
            healed = replace(
                production.controller.snapshots.observe(),
                completed_effect_operation_id=result.action.operation_id,
            )
            first = production.controller.controller.policy.decide(healed, result.checkpoint)
            assert first.state.value == "repairing" and first.forwarding_enabled
            production.controller.snapshots.last_snapshot = healed
            status = production.controller._write_status(first)
            assert status["promotion_ready"] is False
            assert status["controller_ready_boot_id"] is None
            assert native.forwarding

            now[0] += 1
            healed = production.controller.snapshots.observe()
            second = production.controller.controller.policy.decide(healed, first.checkpoint)
            assert second.forwarding_enabled and second.state.value == "active"
            status = production.controller._write_status(second)
            assert status["promotion_ready"] is True
            boot = tmp_path / "boot-id"
            boot.write_text("local-boot")
            main.require_vm_ha_current_boot_ready(
                {"vm_ha": {}},
                status_path=production.controller.status_path,
                boot_id_path=boot,
                guard_path=ports.data_plane.guard_path,
            )
    finally:
        production.close(restore_guard=False)


def test_unknown_selected_peer_stays_fenced_despite_healthy_backup_coverage(tmp_path, monkeypatch):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    native.peers["169.254.2.2"] = "unavailable"
    for _ in range(3):
        runtime.step()
        now[0] += 1
    assert not native.forwarding
    assert read_state(selector.path)["selected"] == {"site": "backup"}
    assert not ports.routes.readiness().promotion_ready
    assert not any(event == ("vm", ActionKind.ENABLE_ACTIVE) for event in native.trace)
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("state", ["Idle", "unavailable"])
def test_checkpointed_activation_retry_prepares_failed_override_while_blocked(
    tmp_path, monkeypatch, state
):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    result = runtime.step()
    assert result.action.kind is ActionKind.PREPARE_OWNER_ORIGINATION
    now[0] += 1
    result = runtime.step()  # Real route reconciliation and receipt.
    assert result.action.kind is ActionKind.RECONCILE_ROUTES
    now[0] += 1
    prepare = ports.data_plane.active_preparer

    def fail_during_activation():
        raise RuntimeError("activation interrupted")

    ports.data_plane.active_preparer = fail_during_activation
    with pytest.raises(RuntimeError, match="activation interrupted"):
        runtime.step()
    assert ports.data_plane.mode() is DataPlaneMode.BLOCKED
    pending = runtime.controller.checkpoints.load().pending_action
    assert pending.kind is ActionKind.ENABLE_ACTIVE
    native.peers["169.254.2.2"] = state
    ports.data_plane.active_preparer = prepare
    now[0] += 1
    if state == "unavailable":
        result = runtime.step()
        assert result.action is None
        assert not native.forwarding
        assert read_state(selector.path)["selected"] == {"site": "backup"}
    else:
        for _ in range(5):
            runtime.step()
            now[0] += 1
        assert native.forwarding
        assert read_state(selector.path)["selected"] == {}
        assert read_state(selector.path)["operation"]["result"] == "normal-policy-restored"
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("verb", ["release", "failback", "expired"])
def test_restart_cleanup_and_failback_require_active_verification(tmp_path, monkeypatch, verb):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    operation = selector.state["operation"]
    if verb == "expired":
        now[0] = operation["deadline"] + 1
    else:
        operation["verb"] = verb
        if verb == "release":
            operation["result"] = "target-failed-restoring-policy"
        if verb == "failback":
            operation.update(
                target="preferred", requested_verb="failback", requested_target="preferred"
            )
    selector._save()
    deadline = operation["deadline"]
    # Origination and route proof are separate fenced transitions. The original
    # recovery deadline remains unchanged through both additional observations.
    for _ in range(10):
        runtime.step()
        state = read_state(selector.path)
        assert state["operation"]["deadline"] == deadline
        if not native.forwarding:
            assert state["operation"]["phase"] not in {"succeeded", "failed"}
        now[0] += 1
    assert native.forwarding and read_state(selector.path)["selected"] == {}
    assert read_state(selector.path)["operation"]["phase"] == (
        "succeeded" if verb == "failback" else "failed"
    )
    tunnels.close()
    ports.close()


def test_stale_failed_backup_route_requires_canonical_passive_policy_refresh(tmp_path, monkeypatch):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    # Actual passive policy retained the override before the peer failed. FRR
    # graceful restart may retain its route even after the session goes down.
    main._prepare_vm_ha_blocked_bgp(tunnels.config_path)
    native.command(("vtysh", "-b"))
    native.peers["169.254.2.2"] = "Idle"
    native.retain_stale_routes = True
    native.links = native.links[:1]
    assert not ports.routes.readiness().promotion_ready
    operation = deepcopy(selector.state["operation"])
    for _ in range(10):
        runtime.step()
        now[0] += 1
    state = read_state(selector.path)
    assert state["selected"] == {}
    assert state["operation"]["id"] == operation["id"]
    assert state["operation"]["deadline"] == operation["deadline"]
    assert state["operation"]["result"] == "normal-policy-restored"
    assert native.forwarding and ports.routes.readiness().promotion_ready
    refresh = native.trace.index(("vm", ActionKind.ENTER_PASSIVE))
    activation = native.trace.index(("vm", ActionKind.ENABLE_ACTIVE))
    assert refresh < activation < native.trace.index(("forward", True))
    assert all(not event[1] for event in native.trace[:activation] if event[0] == "forward")
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("evidence", ["missing", "duplicate", "unavailable"])
def test_unknown_passive_import_binding_never_admits_refresh_or_activation(
    tmp_path, monkeypatch, evidence
):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    command = native.command

    def observed(argv):
        result = command(argv)
        if tuple(argv) == ("vtysh", "-c", "show running-config"):
            if evidence == "unavailable":
                raise RuntimeError("observation unavailable")
            binding = "neighbor 169.254.2.2 route-map SET-LOCAL-PREF-200 in"
            return result.replace(binding, "") if evidence == "missing" else result + "\n" + binding
        return result

    tunnels.effects.run = observed
    for _ in range(2):
        result = runtime.step()
        assert result.action is None
        assert not native.forwarding
        assert not ports.routes.readiness().passive_policy_refresh_required
        now[0] += 1
    assert read_state(selector.path)["selected"] == {"site": "backup"}
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("policy", [None, False, True])
def test_mixed_cold_static_exception_requires_complete_bgp_policy_evidence(
    tmp_path, monkeypatch, policy
):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    ports.routes.static_manifest = replace(
        ports.routes.static_manifest, prefixes=frozenset({"10.2.0.0/16"})
    )
    ports.routes.passive_bgp_policy_provider = lambda: policy
    readiness = ports.routes.readiness()
    assert not readiness.promotion_ready
    assert readiness.candidate_preparation_required
    assert readiness.cold_standby_ready is (policy is True)
    assert readiness.transfer_ready is (policy is True)
    tunnels.close()
    ports.close()


@pytest.mark.parametrize("after_effect", [False, True])
def test_passive_policy_refresh_checkpoint_replays_without_renewing_tunnel_request(
    tmp_path, monkeypatch, after_effect
):
    runtime, tunnels, native, selector, now, ports = composed(tmp_path, monkeypatch)
    native.peers["169.254.2.2"] = "Idle"
    native.retain_stale_routes = True
    native.links = native.links[:1]
    original = deepcopy(selector.state["operation"])
    enter_passive = ports.data_plane.enter_passive

    def interrupted(action):
        if after_effect:
            enter_passive(action)
        raise RuntimeError("refresh interrupted")

    monkeypatch.setattr(ports.data_plane, "enter_passive", interrupted)
    with pytest.raises(RuntimeError, match="refresh interrupted"):
        runtime.step()
    assert runtime.controller.checkpoints.load().pending_action.kind is ActionKind.ENTER_PASSIVE
    assert not native.forwarding
    tunnels.close()
    resumed = tunnel_runtime.ControllerTunnelRuntime(
        tunnels.config_path, tunnels.state_dir, runtime, ports, clock=lambda: now[0]
    )
    resumed.effects.run = resumed.effects.observer.run = native.command
    resumed.effects.routing_lock = tunnels.effects.routing_lock
    runtime.tunnel_step = resumed.step
    monkeypatch.setattr(ports.data_plane, "enter_passive", enter_passive)
    for _ in range(10):
        now[0] += 1
        runtime.step()
    operation = read_state(selector.path)["operation"]
    assert operation["id"] == original["id"] and operation["deadline"] == original["deadline"]
    assert operation["result"] == "normal-policy-restored"
    assert native.forwarding and ports.routes.readiness().promotion_ready
    resumed.close()
    ports.close()
