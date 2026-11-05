"""Real BGP path loss through owner readiness, route receipts and bounded repair.

Cloud ownership/routes and service-unit discovery are fixture inputs.
Native SA, XFRM, FRR, route selection, repair effects and receipts are real.
Only post-repair policy observations inject elapsed reset time; no effects
execute with a synthetic deadline.
Run exclusively inside the disposable integration namespace.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import yaml
from tests.unit.test_vm_ha_runtime_ports import (
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _fake_credential_bundle,
    _runtime_config,
)

from nebius_vpngw.agent.main import VMHACheckpointFileStore, VMHAEffectAdapter
from nebius_vpngw.agent.routing_guard import require_vm_ha_current_boot_ready
from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.runtime import (
    CommandResult,
    SystemDataPlaneRuntime,
    _run_command,
    build_runtime_ports,
)
from nebius_vpngw.agent.vm_ha.tunnel_observation import parse_sas
from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime, NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import Authority, TunnelController
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerCheckpoint,
    ControllerSnapshot,
    DataPlaneMode,
    VMHAController,
)
from nebius_vpngw.deploy.vm_ha_routes import (
    ManagedRouteKind,
    ManagedRouteOwnership,
    ManagedRouteSnapshot,
    RouteReconciliationContext,
    RouteReconciliationReceipt,
    RouteTransitionState,
)

root = Path("/run/bgp-causal-probe")
root.mkdir(exist_ok=True)
cfg = yaml.safe_load(Path(sys.argv[1]).read_text())
config = _runtime_config(root)
config.update(gateway=cfg["gateway"], connections=cfg["connections"], defaults=cfg["defaults"])
for kind, manifest in [
    ("static_routes", [{"remote_prefixes": ["10.201.2.1/32"]}]),
    ("bgp_policy", [{"connection": "site", "remote_prefixes": ["10.200.2.1/32"]}]),
]:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    config["generation"]["logical_manifests"][kind + "_json"] = raw
    config["generation"]["digests"][kind] = digest
    config["readiness"]["digests"][kind] = digest
    config["runtime_binding"][kind + "_digest"] = digest


def runner(argv, timeout):
    if Path(argv[0]).name == "systemctl":
        assert argv[1] == "is-active", "scoped repair restarted an unrelated service"
        # This namespace runs real daemons directly. Verify those processes instead
        # of consulting the outer container's unrelated service units.
        daemon = "charon" if argv[-1].startswith("strongswan") else "bgpd"
        pidfiles = list(Path("/run").glob("**/" + daemon + ".pid"))
        ok = any(
            p.read_text().strip().isdigit() and Path("/proc", p.read_text().strip()).exists()
            for p in pidfiles
        )
        if daemon == "charon":
            ok = Path("/run/charon.vici").exists()
        return CommandResult(0 if ok else 1, "")
    return _run_command(argv, timeout)


class Backend(FakeRouteBackend):
    def list_routes(self, target, ledger):
        return tuple(
            ManagedRouteSnapshot(
                "route-" + k,
                p,
                "allocation-a",
                ManagedRouteOwnership("cluster-a", ManagedRouteKind(k), target),
            )
            for k, p in [("static", "10.201.2.1/32"), ("bgp", "10.200.2.1/32")]
        )


def dataplane(**kwargs):
    kwargs["runner"] = runner
    result = SystemDataPlaneRuntime(**kwargs)
    # Declare the already-active fixture checkpoint, never change live resources.
    result.state_path.parent.mkdir(parents=True, exist_ok=True)
    result.state_path.write_text(
        json.dumps({"schema": "nebius-vpngw/vm-ha-data-plane-v1", "mode": "active"})
    )
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    return result


ports = build_runtime_ports(
    config,
    state_dir=root / "state",
    replay_store=MemoryReplayStore(),
    sdk_factory=lambda **k: FakeSDK(),
    credential_bundle_factory=_fake_credential_bundle,
    route_backend_factory=lambda sdk: Backend(),
    data_plane_factory=dataplane,
    monotonic_clock=time.monotonic,
)
cloud = CloudObservation(
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
ports.cloud.observe = lambda: cloud
tunnels = ControllerTunnelRuntime(Path(sys.argv[1]), root / "state", SimpleNamespace(), ports)
routes = ports.routes
transition = RouteTransitionState(time.time() - 100)
routes.store.save_transition(transition)
plan = routes._plan(routes._current_ownership(require_takeover_fence=False), transition)
assert not plan.blocked_reasons and not plan.mutations, (plan.blocked_reasons, plan.mutations)
b = ports.binding
context = RouteReconciliationContext(
    operation_id="fixture-route",
    cluster_id=b.cluster_id,
    owner_node_id="node-a",
    allocation_id="allocation-a",
    ownership_epoch="7",
    generation_id=b.generation_id,
    configuration_digest=b.configuration_digest,
    static_routes_digest=b.static_routes_digest,
    bgp_policy_digest=b.bgp_policy_digest,
    ownership_incarnation=0,
)
routes.store.save_route_reconciliation_receipt(
    RouteReconciliationReceipt(context=context, plan_digest=routes._plan_digest(plan)).to_dict()
)
digests = DigestSet(b.configuration_digest, b.static_routes_digest, b.bgp_policy_digest)
peer = PeerHeartbeat(
    cluster_id=b.cluster_id,
    node_id="node-b",
    boot_id="peer-boot",
    sequence=1,
    sent_at="2026-11-02T00:00:00Z",
    configured_role="passive",
    observed_owner_id="node-a",
    generation_id=b.generation_id,
    mtls_epoch=1,
    certificate_fingerprint="d" * 64,
    digests=digests,
    service_healthy=True,
    route_ready=True,
    promotion_ready=True,
    tunnel_capability=True,
    auto_healing_policy_state="disabled",
    auto_healing_policy_digest="e" * 64,
)
controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
checkpoint = ControllerCheckpoint()


def forbidden(action):
    raise AssertionError("unexpected VM effect " + action.kind.value)


effects = VMHAEffectAdapter(
    {
        kind: ports.data_plane.repair_local
        if kind is ActionKind.REPAIR_LOCAL_DATAPLANE
        else forbidden
        for kind in ActionKind
    },
    receipt_path=root / "effect-receipt.json",
    event_sink=lambda event: None,
)


def observation(label, *, policy_time=None):
    global checkpoint
    readiness = routes.readiness()
    receipt = routes.receipt_context()
    now = time.monotonic() if policy_time is None else policy_time
    snap = ControllerSnapshot(
        now=now,
        boot_id="native-boot",
        cluster_id=b.cluster_id,
        local_node_id="node-a",
        peer_node_id="node-b",
        configured_role=ConfiguredRole.ACTIVE,
        local_generation_id=b.generation_id,
        local_digests=digests,
        peer_heartbeat=peer,
        peer_received_at=now,
        apply_locked=False,
        emergency_active_only=False,
        readiness=readiness,
        cloud=cloud,
        guard_boot_id="native-boot",
        data_plane_mode=ports.data_plane.mode(),
        completed_effect_operation_id=(effects._completed_operation() or (None, None))[0],
        routes_reconciled_context=receipt,
        route_runtime_id=b.route_runtime_id,
    )
    decision = controller.decide(snap, checkpoint)
    if label == "baseline":
        checkpoint = decision.checkpoint
    current = routes._plan(routes._current_ownership(require_takeover_fence=False), transition)
    print(
        json.dumps(
            {
                "label": label,
                "readiness": list(readiness.blocked_reasons),
                "receipt": receipt is not None,
                "plan_blockers": current.blocked_reasons,
                "mutations": len(current.mutations),
                "digest_same": routes._plan_digest(current) == routes._plan_digest(plan),
                "action": decision.action.kind.value if decision.action else None,
                "reasons": decision.reasons,
            }
        ),
        flush=True,
    )
    return snap, decision


baseline, decision = observation("baseline")
assert decision.action is None and baseline.readiness.promotion_ready
# Freeze only the failed peer's transport and terminate every rekey instance.
blocked = False
try:
    sas = parse_sas(
        subprocess.check_output(
            ["swanctl", "--list-sas", "--raw"], text=True, stderr=subprocess.DEVNULL
        )
    )
    for name, sa in sas:
        if name == "preferred":
            subprocess.run(
                ["swanctl", "--terminate", "--ike-id", sa["uniqueid"], "--timeout", "1"],
                check=True,
                capture_output=True,
            )
    subprocess.run(["iptables", "-I", "OUTPUT", "-d", "192.0.2.2", "-j", "DROP"], check=True)
    blocked = True
    failed, decision = observation("failed-preferred-sa")
    assert failed.routes_reconciled_context is not None, (
        "transient path loss invalidated unchanged cloud route receipt"
    )
    assert decision.action.kind is ActionKind.REPAIR_LOCAL_DATAPLANE, decision.reasons
    checkpoints = VMHACheckpointFileStore(root / "state" / "controller-checkpoint.json")
    checkpoints.save(decision.checkpoint)
    checkpoint = checkpoints.load()
    effects.apply(decision.action)
    assert ports.data_plane.mode() is DataPlaneMode.ACTIVE
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if routes.readiness().promotion_ready:
            break
        time.sleep(0.1)
    first, decision = observation("product-first-recovery")
    assert first.readiness.promotion_ready and first.routes_reconciled_context is not None
    assert decision.action is None and decision.state.value == "repairing"
    assert decision.checkpoint.repair_attempt.healthy_observations == 1
    checkpoint = decision.checkpoint
    recovered, decision = observation("product-scoped-repair")
    assert recovered.readiness.promotion_ready and recovered.routes_reconciled_context is not None
    assert decision.action is None
    assert decision.state.value == "degraded-path" and decision.forwarding_enabled
    # A successful repair triggers ordinary agent reconciliation. Its forwarding
    # gate must consume the controller's ready degraded-path state consistently.
    boot = root / "writer-boot"
    status = root / "writer-status.json"
    guard = root / "writer-guard.json"
    boot.write_text(recovered.boot_id)
    status.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "controller_ready_boot_id": recovered.boot_id,
                "guard_boot_id": recovered.guard_boot_id,
                "promotion_ready": decision.forwarding_enabled,
                "data_plane_mode": recovered.data_plane_mode.value,
                "state": decision.state.value,
            }
        )
    )
    guard.write_text(
        json.dumps(
            {
                "guard_boot_id": recovered.guard_boot_id,
                "data_plane_mode": recovered.data_plane_mode.value,
            }
        )
    )
    require_vm_ha_current_boot_ready(
        {"vm_ha": {}}, status_path=status, boot_id_path=boot, guard_path=guard
    )
    selector = TunnelController(
        cfg,
        root / "selector-health.json",
        NativeTunnelEffects(
            cfg, root, lambda authority: True, active_revalidate=lambda authority: True
        ),
    )
    selector.tick(Authority("native-fixture", "owner", "allocation", "1", 1, "a" * 64), time.time())
    assert selector.state["health"] == {"site": "healthy", "other": "healthy"}
    assert selector.state["selected"] == {}
    checkpoint = decision.checkpoint
    original_deadline = checkpoint.repair_attempt.deadline_at
    _, decision = observation("backup-before-reset", policy_time=first.now + 59.999)
    assert decision.action is None
    assert decision.checkpoint.repair_attempt.deadline_at == original_deadline
    checkpoint = decision.checkpoint
    _, decision = observation("backup-health-reset", policy_time=first.now + 60.0)
    assert decision.action is None and decision.forwarding_enabled
    assert decision.checkpoint.repair_attempt is None
finally:
    if blocked:
        subprocess.run(["iptables", "-D", "OUTPUT", "-d", "192.0.2.2", "-j", "DROP"], check=True)
    tunnels.close()
    ports.close()
