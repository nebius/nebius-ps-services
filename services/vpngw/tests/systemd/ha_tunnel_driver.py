"""Native tunnel selector driver, executed only inside a disposable test namespace."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import yaml

from nebius_vpngw.agent.frr_renderer import FRR_CONF, FRRRenderer
from nebius_vpngw.agent.strongswan_renderer import SWANCTL_CONF, StrongSwanRenderer
from nebius_vpngw.agent.vm_ha.tunnel_observation import parse_sas, xfrm_records
from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import Authority, TunnelController, effective_config
from nebius_vpngw.ha_tunnel_policy import connection_policies
from nebius_vpngw.ipsec_observation import command as ipsec_command


def recover_owner(cfg, config_path, state_dir):
    """Freeze only cloud authority; observe and repair the real native path."""
    from nebius_vpngw.agent.main import VMHACheckpointFileStore, VMHAControllerRuntime
    from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
    from nebius_vpngw.agent.vm_ha.tunnels import read_state
    from nebius_vpngw.agent.vm_ha_controller import (
        CloudObservation,
        ComputeState,
        ConfiguredRole,
        ControllerSnapshot,
        DataPlaneMode,
        LocalReadiness,
    )

    digests = DigestSet("a" * 64, "b" * 64, "c" * 64)
    cfg["vm_ha"].update(
        node={"node_id": "owner"},
        runtime_binding={"shared_allocation_id": "allocation"},
        generation={"generation_id": digests.configuration},
    )
    config_path.write_text(yaml.safe_dump(cfg))
    peer = PeerHeartbeat(
        cluster_id="native-fixture",
        node_id="standby",
        boot_id="peer-boot",
        sequence=1,
        sent_at="2026-11-01T00:00:00Z",
        configured_role="passive",
        observed_owner_id="owner",
        generation_id=digests.configuration,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=digests,
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="disabled",
        auto_healing_policy_digest="e" * 64,
        tunnel_capability=True,
    )
    snapshots = SimpleNamespace(
        last_snapshot=None,
        providers=SimpleNamespace(apply_lock_operation_id=lambda: None),
    )
    vm_effects = []

    def observe():
        ready = tunnels.static_ready()
        state = read_state(state_dir / "tunnel-selection.json")
        episode = state["episode"]
        now = time.time()
        snapshot = ControllerSnapshot(
            now=now,
            boot_id="native-boot",
            cluster_id="native-fixture",
            local_node_id="owner",
            peer_node_id="standby",
            configured_role=ConfiguredRole.ACTIVE,
            local_generation_id=digests.configuration,
            local_digests=digests,
            peer_heartbeat=peer,
            peer_received_at=now,
            apply_locked=False,
            emergency_active_only=False,
            readiness=LocalReadiness(True, ready, True, True, protection_ready=True),
            cloud=CloudObservation(
                authoritative=True,
                allocation_id="allocation",
                observed_owner_node_id="owner",
                former_owner_node_id="standby",
                former_owner_compute_state=ComputeState.STOPPED,
                former_attachment_absent=True,
                candidate_attachment_exact=True,
                ownership_re_read_exact=True,
                ownership_epoch="1",
            ),
            guard_boot_id="native-boot",
            data_plane_mode=DataPlaneMode.ACTIVE,
            routes_reconciled_context=None,
            route_runtime_id="native-routes",
            tunnel_recovery_remaining=None if episode is None else episode["deadline"] - now,
        )
        snapshot = replace(
            snapshot,
            routes_reconciled_context=replace(
                snapshot.route_reconciliation_context, operation_id="fixture-initial-routes"
            ),
        )
        snapshots.last_snapshot = snapshot
        return snapshot

    snapshots.observe = observe
    runtime = VMHAControllerRuntime(
        snapshots=snapshots,
        effects=SimpleNamespace(apply=vm_effects.append),
        checkpoints=VMHACheckpointFileStore(state_dir / "controller-checkpoint.json"),
        status_path=state_dir / "status.json",
        guard=lambda: {},
    )
    tunnels = ControllerTunnelRuntime(
        config_path,
        state_dir,
        runtime,
        SimpleNamespace(routes=SimpleNamespace(), data_plane=SimpleNamespace()),
    )
    runtime.tunnel_runtime = tunnels
    runtime.tunnel_step = tunnels.step
    try:
        tunnels.step()
        assert tunnels.static_ready()
        # Inject the loss after the ordinary tunnel check but before the VM
        # readiness snapshot. No observer or selector result is substituted.
        tunnels.effects.run(("ip", "link", "set", "xfrm0", "down"))
        decision = runtime.controller.step()
        assert decision.reasons == ("native-path-recovery",), decision.reasons
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            runtime.tunnel_step()
            runtime.controller.step()
            assert not vm_effects, "generic VM repair preempted native tunnel recovery"
            state = read_state(state_dir / "tunnel-selection.json")
            operation = state["operation"]
            if operation and operation["phase"] in {"succeeded", "failed"}:
                print(
                    json.dumps(
                        {
                            "selected": state["selected"],
                            "phase": operation["phase"],
                            "result": operation["result"],
                        }
                    )
                )
                return
            time.sleep(0.3)
        raise RuntimeError("native owner tunnel recovery did not converge")
    finally:
        tunnels.close()
        tunnels.effects.run(("ip", "link", "set", "xfrm0", "up"))


def main() -> None:
    cfg = yaml.safe_load(Path(sys.argv[1]).read_text())
    command = sys.argv[2]
    authority = Authority(
        "native-fixture", cfg.get("_fixture_owner", "owner"), "allocation", "1", 1, "a" * 64
    )
    state_dir = Path("/var/lib/nebius-vpngw/vm-ha")
    state_dir.mkdir(parents=True, exist_ok=True)
    if command in {"activation-checkpoint", "activation"}:
        activate_retained_static(
            cfg, Path(sys.argv[1]), state_dir, checkpoint=command.endswith("checkpoint")
        )
        return
    if command == "recover-owner":
        recover_owner(cfg, Path(sys.argv[1]), state_dir)
        return

    def revalidate(observed):
        # Model bounded provider observation latency without replacing native
        # SA, XFRM or route effects. Cloud authority remains a fixture input.
        time.sleep(cfg.get("_fixture_authority_delay_seconds", 0))
        return observed == authority

    effects = NativeTunnelEffects(cfg, state_dir, revalidate, active_revalidate=revalidate)
    if command == "prepared-static-health":
        from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
        from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode

        # The fixture has completed owner preparation and closed forwarding.
        # Exercise the production read collectors against real VICI/XFRM/FRR.
        assert effects.run(("sysctl", "-n", "net.ipv4.ip_forward")).strip() == "0"
        plane = SimpleNamespace(mode=lambda: DataPlaneMode.PASSIVE)
        runtime = ControllerTunnelRuntime(
            Path(sys.argv[1]),
            state_dir,
            None,
            SimpleNamespace(data_plane=plane, routes=SimpleNamespace()),
        )
        before = SWANCTL_CONF.read_bytes()
        assert plane.ipsec_configuration_observer() is None
        assert runtime.static_ready()
        try:
            plane.ipsec_configuration_provider()
        except RuntimeError:
            pass
        else:
            raise AssertionError("prepared Static policy granted standby reload authority")
        assert SWANCTL_CONF.read_bytes() == before
        assert effects.run(("sysctl", "-n", "net.ipv4.ip_forward")).strip() == "0"
        print("prepared-static-health-verified")
        return
    if command in {"materialize", "peer"}:
        rendered = {}
        StrongSwanRenderer().render_and_apply(
            cfg, activate=command != "peer", rendered_files=rendered
        )
        content = rendered[SWANCTL_CONF]
        if command == "peer":
            content = content.replace(
                "remote_ts = 0.0.0.0/0", "remote_ts = " + ", ".join(cfg["_fixture_remote_ts"])
            )
        SWANCTL_CONF.write_text(content)
        SWANCTL_CONF.chmod(0o600)
        effects.run(("swanctl", "--load-all", "--noprompt"))
        if cfg.get("gateway", {}).get("local_asn"):
            FRRRenderer().render_and_apply(cfg, rendered_files=rendered)
            FRR_CONF.write_text(rendered[FRR_CONF])
            FRR_CONF.chmod(0o640)
        return
    selector = TunnelController(cfg, state_dir / "tunnel-selection.json", effects)
    policy = next(p for p in connection_policies(cfg) if p.name == "site")
    if command == "diagnose":
        print(
            json.dumps(
                {
                    "sas": parse_sas(effects.run(ipsec_command())),
                    "states": xfrm_records(effects.run(("ip", "xfrm", "state")), policy=False),
                    "policies": effects.run(("ip", "xfrm", "policy")),
                    "routes": json.loads(
                        effects.run(("ip", "-j", "route", "show", "table", "all"))
                    ),
                    "links": json.loads(effects.run(("ip", "-j", "-d", "link", "show"))),
                }
            )
        )
        return
    if command == "health":
        print(effects.verify(policy, selector.state["selected"].get("site")).value)
        return
    if command == "tick":
        selector.tick(authority, time.time())
        print(json.dumps(selector.state["selected"]))
        return
    if command == "reconcile":
        rendered = {}
        StrongSwanRenderer().render_and_apply(
            effective_config(cfg, selector.state, authority), rendered_files=rendered
        )
        SWANCTL_CONF.write_text(rendered[SWANCTL_CONF])
        effects.run(("swanctl", "--load-conns"))
        print(json.dumps(selector.state["selected"]))
        return
    target = sys.argv[3]
    prior_operation = selector.state["operation"]
    prior_id = prior_operation["id"] if prior_operation else None
    if command in {"failover", "failback"}:
        selector.request(command, target, authority, time.time())
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        # A new instance each iteration exercises persisted replay, including
        # every native effect boundary, without mocking observations or routing.
        selector = TunnelController(cfg, state_dir / "tunnel-selection.json", effects)
        selector.tick(authority, time.time())
        operation = selector.state["operation"]
        if (
            operation
            and operation["phase"] in {"succeeded", "failed"}
            and (command != "recover" or operation["id"] != prior_id)
        ):
            print(
                json.dumps(
                    {
                        "selected": selector.state["selected"],
                        "result": operation["result"],
                        "phase": operation["phase"],
                    }
                )
            )
            return
        time.sleep(0.3)
    raise RuntimeError("native tunnel operation did not converge")


def activate_retained_static(cfg, config_path, state_dir, *, checkpoint):
    """Fixture cloud/checkpoint inputs; real product preparation and kernel effects."""
    import hashlib

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.unit.test_vm_ha_controller import _owned_cloud
    from tests.unit.test_vm_ha_runtime_ports import (
        FakeCredentialBundle,
        FakeRouteBackend,
        FakeSDK,
        MemoryReplayStore,
        _runtime_config,
    )

    from nebius_vpngw.agent import main as agent
    from nebius_vpngw.agent.vm_ha.models import DigestSet
    from nebius_vpngw.agent.vm_ha.runtime import build_runtime_ports
    from nebius_vpngw.agent.vm_ha.store import atomic_write_json
    from nebius_vpngw.agent.vm_ha.tunnels import read_state
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import (
        ActionKind,
        ControllerAction,
        ControllerCheckpoint,
        DataPlaneMode,
    )

    if checkpoint:
        vm = _runtime_config(state_dir)
        manifest = json.dumps(
            [
                {"connection": c["name"], "remote_prefixes": c["remote_prefixes"]}
                for c in cfg["connections"]
            ]
        )
        digest = hashlib.sha256(manifest.encode()).hexdigest()
        vm["generation"]["logical_manifests"]["static_routes_json"] = manifest
        vm["generation"]["digests"]["static_routes"] = digest
        vm["readiness"]["digests"]["static_routes"] = digest
        vm["runtime_binding"]["static_routes_digest"] = digest
        vm.update(gateway=cfg["gateway"], connections=cfg["connections"], defaults=cfg["defaults"])
        cfg["vm_ha"] = vm
        config_path.write_text(yaml.safe_dump(cfg))
        config_path.chmod(0o600)
        authority = Authority("cluster-a", "node-a", "allocation-a", "42", 1, "a" * 64)
        selector = TunnelController(cfg, state_dir / "tunnel-selection.json", SimpleNamespace())
        selector.bind(authority)
        selector.state["selected"] = {"site": "backup"}
        selector._save()
        atomic_write_json(
            state_dir / "controller-checkpoint.json",
            controller_checkpoint_to_dict(ControllerCheckpoint(ownership_incarnation=1)),
        )
        print("retained-backup-checkpoint")
        return

    before = (state_dir / "tunnel-selection.json").read_bytes()
    vm = cfg["vm_ha"]
    ports = build_runtime_ports(
        vm,
        state_dir=state_dir,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **kw: FakeSDK(),
        credential_bundle_factory=lambda *a: FakeCredentialBundle(),
        route_backend_factory=lambda sdk: FakeRouteBackend(),
        candidate_preparer=lambda: agent._prepare_vm_ha_static_tunnels(config_path),
    )
    ports.cloud.observe = lambda: replace(
        _owned_cloud(),
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
    )
    plane = ports.data_plane
    action = ControllerAction(
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        "retained-activation",
        "native-boot",
        "node-a",
        "allocation-a",
        "42",
        "a" * 64,
        DigestSet(**vm["generation"]["digests"]),
        ownership_incarnation=1,
    )
    try:
        plane._write_state(action, DataPlaneMode.PASSIVE)
        plane._write_guard(action, DataPlaneMode.PASSIVE)
        assert not plane._forwarding()
        plane.prepare_candidate(action)
        assert not plane._forwarding()
        # Independently inspect exact kernel objects before forwarding admission.
        effects = NativeTunnelEffects(
            cfg, state_dir, lambda a: True, active_revalidate=lambda a: True
        )
        for argv, policy in [
            (("ip", "xfrm", "state", "list", "nokeys"), False),
            (("ip", "-s", "xfrm", "policy"), True),
        ]:
            assert not any(
                int(str(o.get("if_id", 0)), 0) == 100
                for o in xfrm_records(effects.run(argv), policy=policy)
            ), "activation retained unselected kernel objects"
        plane.enable_active(replace(action, kind=ActionKind.ENABLE_ACTIVE))
        assert plane._forwarding()
        plane.destination_protection.verify()
        plane.enable_active(replace(action, kind=ActionKind.ENABLE_ACTIVE))
        assert (state_dir / "tunnel-selection.json").read_bytes() == before
        assert read_state(state_dir / "tunnel-selection.json")["selected"] == {"site": "backup"}
        print(json.dumps({"selected": {"site": "backup"}, "phase": "succeeded"}))
    finally:
        ports.close()


if __name__ == "__main__":
    main()
