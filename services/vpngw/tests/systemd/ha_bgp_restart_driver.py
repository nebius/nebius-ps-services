"""Native fenced restart replay; provider ownership and unit dispatch are fixtures.

SA/XFRM evidence, FRR rendering/imports/exports, Agent passive materialization,
controller checkpoints, readiness and forwarding effects use production code.
Executed only in the disposable native test namespace.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import yaml
from tests.unit.test_vm_ha_controller import _owned_cloud, _peer, _snapshot
from tests.unit.test_vm_ha_runtime_ports import (
    FakeCredentialBundle,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _runtime_config,
)

from nebius_vpngw.agent import frr_renderer, main, strongswan_renderer
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import (
    CommandResult,
    SystemDataPlaneRuntime,
    _run_command,
    build_runtime_ports,
)
from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
from nebius_vpngw.agent.vm_ha.tunnels import Authority, Health, TunnelController, read_state
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ConfiguredRole,
    ControllerAction,
    DataPlaneMode,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
)


def run():
    assert sys.platform == "linux" and Path("/.dockerenv").is_file()
    assert os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") == "1"
    config_path = Path(sys.argv[1])
    scenario = sys.argv[2]
    cfg = yaml.safe_load(config_path.read_text())
    cfg["connections"] = [c for c in cfg["connections"] if c["routing_mode"] == "bgp"]
    root = Path("/var/lib/nebius-vpngw/vm-ha")
    vm = _runtime_config(root)
    vm.update(gateway=cfg["gateway"], connections=cfg["connections"], defaults=cfg["defaults"])
    manifest = json.dumps([{"connection": "site", "remote_prefixes": ["10.200.2.1/32"]}])
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    vm["generation"]["logical_manifests"]["bgp_policy_json"] = manifest
    vm["generation"]["digests"]["bgp_policy"] = digest
    vm["readiness"]["digests"]["bgp_policy"] = digest
    vm["runtime_binding"]["bgp_policy_digest"] = digest
    cfg["vm_ha"] = vm
    config_path.write_text(yaml.safe_dump(cfg))
    config_path.chmod(0o600)
    main.CONFIG_PATH = config_path
    original_reader = main._read_vm_ha_config
    main._read_vm_ha_config = lambda path=None: original_reader(path or config_path)
    # Namespace LAN setup and host firewall are fixture boundaries. The relevant
    # kernel forwarding, routing, XFRM and FRR transitions are never substituted.
    main.update_firewall_from_config = lambda *a, **kw: None
    original_run = frr_renderer.run

    def renderer_run(argv, **kwargs):
        if argv[0] == "netplan" or (argv[0] == "systemctl" and argv[-1].startswith("strongswan")):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "systemctl" and argv[-1] == "frr":
            # Match the distro service reload owner: additive vtysh -b leaves
            # removed route-map clauses behind and cannot model exact policy.
            return subprocess.run(
                ["/usr/lib/frr/frr-reload.py", "--reload", str(frr_renderer.FRR_CONF)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        return original_run(argv, **kwargs)

    frr_renderer.run = strongswan_renderer.run = renderer_run
    frr_renderer.FRRRenderer._ensure_bgpd_enabled = lambda *a, **kw: False
    events = []

    def runner(argv, timeout):
        command = Path(argv[0]).name
        if command == "systemctl":
            query = argv[1] == "show" and argv[3:] == ("--property=ActiveState", "--value")
            if query or argv[1] == "is-active":
                service = argv[2] if query else argv[-1]
                daemon = "charon" if service == "strongswan" else "bgpd"
                healthy = (
                    Path("/run/charon.vici").exists()
                    if daemon == "charon"
                    else any(
                        path.read_text().strip().isdigit()
                        and Path("/proc", path.read_text().strip()).exists()
                        for path in Path("/run").glob("**/bgpd.pid")
                    )
                )
                return (
                    CommandResult(0, "active\n" if healthy else "failed\n")
                    if query
                    else CommandResult(0 if healthy else 1)
                )
            assert argv[1:] == ("reload-or-restart", "nebius-vpngw-agent"), argv
            main.Agent().reload()
            return CommandResult(0)
        if command.startswith("python") and argv[-1] == "--vm-ha-materialized":
            main.require_vm_ha_current_boot_materialization()
            return CommandResult(0)
        result = _run_command(argv, timeout)
        if command == "sysctl" and "-w" in argv:
            events.append(argv[-1])
        return result

    class Backend(FakeRouteBackend):
        def list_routes(self, target, ledger):
            return (
                ManagedRouteSnapshot(
                    "route-bgp",
                    "10.200.2.1/32",
                    "allocation-a",
                    ManagedRouteOwnership("cluster-a", ManagedRouteKind.BGP, target),
                ),
            )

        def apply_mutation(self, mutation):
            raise AssertionError("same-owner replay changed cloud routes")

    ports = build_runtime_ports(
        vm,
        state_dir=root,
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **kw: FakeSDK(),
        credential_bundle_factory=lambda *a: FakeCredentialBundle(),
        route_backend_factory=lambda sdk: Backend(),
        runner=runner,
        active_preparer=lambda: main._prepare_vm_ha_active_data_plane(config_path),
        origination_verifier=lambda: main._vm_ha_origination_policy_ready(config_path),
        blocked_preparer=lambda: main._prepare_vm_ha_blocked_bgp(config_path),
        data_plane_factory=lambda **kw: SystemDataPlaneRuntime(
            **kw, bgp_export_attempts=10, bgp_export_interval=0.2
        ),
    )
    cloud = replace(
        _owned_cloud(),
        observed_owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
    )
    ports.cloud.observe = lambda: cloud
    digests = DigestSet("a" * 64, vm["runtime_binding"]["static_routes_digest"], digest)
    boot = main._boot_id()
    snapshots = SimpleNamespace(
        last_snapshot=None,
        providers=SimpleNamespace(apply_lock_operation_id=lambda: None),
        last_apply_operation_id=None,
        last_transfer_inhibition_operation_id=None,
        config=vm,
    )

    def observe():
        now = time.time()
        snapshot = _snapshot(
            now=now,
            boot_id=boot,
            guard_boot_id=boot,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            local_digests=digests,
            configured_role=ConfiguredRole.ACTIVE,
            peer_heartbeat=replace(
                _peer(node_id="node-b", configured_role="passive"),
                cluster_id="cluster-a",
                digests=digests,
                tunnel_capability=True,
            ),
            peer_received_at=now,
            cloud=cloud,
            readiness=ports.routes.readiness(),
            data_plane_mode=ports.data_plane.mode(),
            routes_reconciled_context=ports.routes.receipt_context(),
            route_runtime_id=ports.binding.route_runtime_id,
        )
        snapshots.last_snapshot = snapshot
        return snapshot

    snapshots.observe = observe
    checkpoints = main.VMHACheckpointFileStore(root / "controller-checkpoint.json")
    checkpoints.save(checkpoints.load())
    runtime = main.VMHAControllerRuntime(
        snapshots=snapshots,
        effects=main.VMHAEffectAdapter(
            ports.handlers(),
            receipt_path=root / "effect-receipt.json",
            event_sink=lambda event: None,
        ),
        checkpoints=checkpoints,
        status_path=root / "status.json",
        guard=lambda: {},
    )
    tunnels = ControllerTunnelRuntime(config_path, root, runtime, ports)
    runtime.tunnel_step = tunnels.step
    runtime.tunnel_runtime = tunnels
    authority = Authority("cluster-a", "node-a", "allocation-a", "42", 0, "a" * 64)
    # Declare a healthy active fixture checkpoint, then submit real selector
    # intent. The trial starts at the persisted crash boundary below.
    # This driver narrows the shared fixture to BGP above. Materialize that exact
    # policy before claiming a healthy checkpoint; the original swanctl file
    # still includes the unrelated Static connection from the outer fixture.
    files = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(cfg, rendered_files=files)
    strongswan_renderer._write_secret_file(
        strongswan_renderer.SWANCTL_CONF, files[strongswan_renderer.SWANCTL_CONF]
    )
    subprocess.run(
        ["swanctl", "--load-all", "--noprompt"], check=True, capture_output=True, timeout=5
    )
    # Establish the same durable destination protection as a real active owner
    # before restart withdraws its learned BGP routes. Use actual kernel route
    # provenance; a synthetic cloud route alone cannot authorize forwarding.
    ports.data_plane.destination_protection.admit(frozenset({"10.200.2.1/32"}))
    action = ControllerAction(
        ActionKind.ENABLE_ACTIVE,
        "fixture-active",
        boot,
        "node-a",
        "allocation-a",
        "42",
        "a" * 64,
        digests,
    )
    ports.data_plane._write_state(action, DataPlaneMode.ACTIVE)
    ports.data_plane._write_guard(action, DataPlaneMode.ACTIVE)
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    selector = TunnelController(cfg, root / "tunnel-selection.json", tunnels.effects)
    selector.bind(authority)
    assert ports.routes.readiness().promotion_ready, "fixture baseline is not healthy"
    selector.request("failover", "backup", authority, time.time())
    selector.tick(authority, time.time())
    selector.state["operation"]["phase"] = "verify" if scenario == "verify" else "activate"
    selector.state["operation"]["successes"] = 1
    if scenario == "release":
        selector.state["operation"].update(verb="release", result="target-failed-restoring-policy")
    selector._save()
    operation_id = selector.state["operation"]["id"]
    original_deadline = selector.state["operation"]["deadline"]
    # Product restart workflow: install guard, passive Agent materialization,
    # controller route reconciliation/activation, then selector verification.
    ports.data_plane.install_guard(replace(action, kind=ActionKind.INSTALL_COLD_START_GUARD))
    if scenario == "missing-protection":
        # Declared fixture fault: model an upgrade that has never installed the
        # new destination guard. The product is already forwarding-fenced and
        # must recreate protection through its own origination action.
        assert not ports.data_plane._forwarding()
        subprocess.run(
            ["nft", "delete", "table", "inet", "nebius_vpngw_forward"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        ports.data_plane.destination_protection.path.unlink()
        assert not ports.data_plane.destination_protection.ready()
    if scenario == "failed-selected":
        # A peer failure during downtime survives passive materialization. The
        # product must release the persisted override; the fixture never edits it.
        backup = next(t for t in cfg["connections"][0]["tunnels"] if t["name"] == "backup")
        subprocess.run(
            ["iptables", "-I", "OUTPUT", "-d", backup["remote_public_ip"], "-j", "DROP"],
            check=True,
            capture_output=True,
        )
    started = time.monotonic()
    limit = started + 35
    steps = []
    while time.monotonic() < limit:
        status = runtime.step()
        state = read_state(selector.path)
        # Retain existing observations for a failed trial without adding native
        # polls or changing its deadline, state or recovery inputs.
        steps.append(
            {
                "state": status["state"],
                "elapsed_seconds": time.monotonic() - started,
                "reasons": status["reasons"],
                "pending": status["pending_operation_id"],
                "tunnel_phase": state["operation"]["phase"],
                "tunnel_result": state["operation"].get("result"),
            }
        )
        print(json.dumps({"restart_step": steps[-1]}), file=sys.stderr, flush=True)
        assert state["operation"]["id"] == operation_id
        assert state["operation"]["deadline"] == original_deadline
        if ports.data_plane.mode() is not DataPlaneMode.ACTIVE:
            assert state["operation"]["phase"] not in {"succeeded", "failed"}
        elif state["operation"]["phase"] in {"succeeded", "failed"}:
            assert ports.routes.readiness().promotion_ready
            ports.data_plane.destination_protection.verify()
            expected = {} if scenario in {"release", "failed-selected"} else {"site": "backup"}
            assert state["selected"] == expected, state["selected"]
            assert (
                tunnels.effects.verify(
                    selector.policies["site"], None if not expected else "backup"
                )
                is Health.HEALTHY
            )
            assert "net.ipv4.ip_forward=0" in events and "net.ipv4.ip_forward=1" in events
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "phase": state["operation"]["phase"],
                        "selected": state["selected"],
                        "active": True,
                    }
                )
            )
            tunnels.close()
            ports.close()
            return
        time.sleep(0.2)
    raise RuntimeError(
        "native fenced BGP restart did not converge: "
        + repr(
            {
                "steps": steps,
                "mode": ports.data_plane.mode(),
                "readiness": ports.routes.readiness(),
                "selector": read_state(selector.path),
                "observed": ports.data_plane.observe(),
                "routes": _run_command(("ip", "-j", "-4", "route", "show"), 5),
            }
        )
    )


if __name__ == "__main__":
    run()
