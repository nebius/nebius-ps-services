from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time

import pytest

from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw import ordinary_routes as routes
from nebius_vpngw.agent.state_store import StateStore
from nebius_vpngw.agent.strongswan_renderer import StrongSwanRenderer


def config(*prefixes):
    return {
        "gateway": {},
        "connections": [
            {
                "routing_mode": "static",
                "tunnels": [
                    {
                        "name": f"peer-{i}",
                        "remote_public_ip": f"203.0.113.{10 + i}",
                        "static_routes": {"remote_prefixes": [p]},
                    }
                    for i, p in enumerate(prefixes)
                ],
            }
        ],
    }


class Kernel:
    def __init__(self, cfg):
        self.links = [{"ifname": "eth0", "ifindex": 2}]
        self.routes = []
        self.deletes = []
        self.fail_delete = False
        for e in routes.projection(cfg):
            self.links.append(
                {
                    "ifname": e["name"],
                    "ifindex": e["if_id"],
                    "link_index": 2,
                    "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": e["if_id"]}},
                }
            )
            self.routes.extend({"dst": p, "dev": e["name"], "scope": "link"} for p in e["prefixes"])

    def run(self, args, **kwargs):
        if args == ["ip", "-d", "-j", "link", "show"]:
            value = self.links
        elif args == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            value = self.routes
        else:
            assert args[:5] == ["ip", "-4", "route", "del", "unicast"]
            self.deletes.append(args)
            if self.fail_delete:
                return subprocess.CompletedProcess(args, 2, "", "fixture failure")
            self.routes = [
                r
                for r in self.routes
                if not (
                    routes.prefix(r["dst"]) == args[5]
                    and r.get("dev") == args[-1]
                    and routes._route(r)["table"] == 254
                    and routes._route(r)["protocol"] == 3
                    and routes._route(r)["scope"] == 253
                    and routes._route(r)["metric"] == 0
                )
            ]
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")


@pytest.fixture
def guest(tmp_path, ordinary_operation_guest):
    old = config("10.10.0.0/16", "10.20.0.0/16", "10.30.0.0/16")
    kernel = Kernel(old)
    state = tmp_path / "last-applied.json"
    StateStore(state).save_last_applied(old)
    store = routes.RouteStore()
    boot = ops.BOOT.read_text()
    manager = ordinary_operation_guest(time.monotonic() + 60)
    return old, kernel, state, store, boot, manager


def snapshot(guest, desired=None, proof=None):
    old, kernel, state, store, boot, _ = guest
    return store.snapshot(
        old,
        state,
        kernel.run,
        boot,
        proof=proof,
        future=routes.projection(desired) if desired is not None else None,
    )


def begin(guest, plan, identity="a", approved=True, previous=None, config_hash="b" * 64):
    _, _, _, store, _, manager = guest
    binding = {
        "id": identity * 32,
        "config": config_hash,
        "artifact": "c" * 64,
        "predecessor": "d" * 64,
    }
    store.prepare(binding, plan, approved=approved)
    operation = ops.Operation.begin(
        request=binding["id"],
        config=binding["config"],
        artifact=binding["artifact"],
        predecessor=binding["predecessor"],
        manager=manager,
        network=manager.network(),
        previous=previous,
    )
    operation.phase("reconcile")
    return operation


@pytest.mark.parametrize(
    "transition", ["first", "middle", "last", "all", "disable", "reorder", "prefix", "bgp"]
)
def test_retirement_uses_real_positional_projection_and_preserves_unrelated_routes(
    guest, transition
):
    old, kernel, _, store, boot, _ = guest
    desired = copy.deepcopy(old)
    tunnels = desired["connections"][0]["tunnels"]
    if transition in {"first", "middle", "last"}:
        tunnels.pop({"first": 0, "middle": 1, "last": 2}[transition])
    elif transition == "all":
        tunnels.clear()
    elif transition == "disable":
        tunnels[0]["ha_role"] = "disable"
    elif transition == "reorder":
        tunnels.reverse()
    elif transition == "prefix":
        tunnels[0]["static_routes"]["remote_prefixes"] = []
    else:
        desired["connections"][0]["routing_mode"] = "bgp"
    foreign = [
        {"dst": "default", "dev": "eth0", "gateway": "192.0.2.1"},
        {"dst": "10.10.0.0/16", "dev": "xfrm0", "table": 200, "scope": "link"},
        {"dst": "10.10.0.0/16", "dev": "xfrm0", "protocol": "bgp"},
        {"dst": "169.254.1.0/30", "dev": "xfrm0", "protocol": "kernel", "scope": "link"},
    ]
    kernel.routes.extend(copy.deepcopy(foreign))
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), routes.projection(desired))
    expected = routes.keys(routes.projection(old), static_only=True) - routes.keys(
        routes.projection(desired)
    )
    assert {tuple(k) for k in plan.payload["retired"]} == expected
    original_links = copy.deepcopy(kernel.links)
    operation = begin(guest, plan)
    obligations = store.cleanup(operation, desired, kernel.run, boot)
    assert {(r[5], r[-1]) for r in kernel.deletes} == {(p, n) for n, p in expected}
    # The fake delete must model the exact selectors used by the product.
    assert all(
        r[-1] != "eth0"
        and r[6:14] == ["table", "254", "proto", "3", "scope", "link", "metric", "0"]
        for r in kernel.deletes
    )
    assert all(row in kernel.routes for row in foreign)
    assert kernel.links == original_links
    store.commit(operation.check(), obligations)
    assert store.verify(desired, kernel.run, boot) == obligations


@pytest.mark.parametrize(
    "field,value", [("metric", 20), ("gateway", "192.0.2.1"), ("protocol", "static"), ("nhid", 7)]
)
def test_ambiguous_route_shape_blocks_before_any_delete(guest, field, value):
    _, kernel, _, _, _, _ = guest
    kernel.routes[0][field] = value
    with pytest.raises(RuntimeError, match="shape_conflict"):
        routes.RouteRetirementPlan.build(snapshot(guest, config()), routes.projection(config()))
    assert kernel.deletes == []


@pytest.mark.parametrize("fault", ["ifindex", "parent", "if_id", "rename", "route", "boot", "lost"])
def test_mutable_identity_and_lost_ack_never_produce_success(guest, fault):
    _, kernel, _, store, boot, _ = guest
    desired = config()
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), [])
    operation = begin(guest, plan)
    link = kernel.links[1]
    if fault == "ifindex":
        link["ifindex"] += 1
    elif fault == "parent":
        link["link_index"] += 1
    elif fault == "if_id":
        link["linkinfo"]["info_data"]["if_id"] += 1
    elif fault == "rename":
        link["ifname"] = "renamed"
    elif fault == "route":
        kernel.routes[0]["metric"] = 1
    elif fault == "boot":
        boot = "00000000-0000-0000-0000-000000000000"
    else:
        kernel.fail_delete = True
    with pytest.raises(RuntimeError):
        store.cleanup(operation, desired, kernel.run, boot)
    assert not store.history.exists()
    if fault != "lost":
        assert not kernel.deletes


def test_history_survives_state_replacement_and_does_not_authorize_future_deletion(guest):
    _, kernel, state, store, boot, _ = guest
    desired = config()
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), [])
    operation = begin(guest, plan)
    obligations = store.cleanup(operation, desired, kernel.run, boot)
    store.commit(operation.check(), obligations)
    StateStore(state).save_last_applied(desired)
    operation.complete()
    kernel.routes.append({"dst": "10.10.0.0/16", "dev": "xfrm0", "scope": "link"})
    before = len(kernel.deletes)
    with pytest.raises(RuntimeError, match="retired_route_present"):
        store.verify(desired, kernel.run, boot)
    with pytest.raises(RuntimeError, match="reappeared"):
        routes.RouteRetirementPlan.build(store.snapshot(desired, state, kernel.run, boot), [])
    assert len(kernel.deletes) == before
    # An explicit successor owning this exact route legitimately supersedes its absence.
    reinstated = config("10.10.0.0/16")
    assert all(o["prefix"] != "10.10.0.0/16" for o in store.verify(reinstated, kernel.run, boot))


def test_renamed_live_interface_is_not_mistaken_for_a_retired_incarnation(guest):
    _, kernel, _, store, boot, _ = guest
    plan = routes.RouteRetirementPlan.build(snapshot(guest, config()), [])
    operation = begin(guest, plan)
    store.commit(operation.check(), store.cleanup(operation, config(), kernel.run, boot))
    kernel.links[1]["ifname"] = "renamed"
    with pytest.raises(RuntimeError, match="interface_changed"):
        store.verify(config(), kernel.run, boot)


def test_missing_pending_sidecar_never_discards_prospective_successor_ownership(guest):
    old, kernel, state, store, boot, _ = guest
    desired = config("10.40.0.0/16")
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), routes.projection(desired))
    operation = begin(guest, plan)
    (store.root / (operation.value["id"] + ".json")).unlink()
    with pytest.raises(RuntimeError, match="plan_missing"):
        store.snapshot(old, state, kernel.run, boot)


def test_missing_history_preserves_unchanged_upgrade_but_never_licenses_retirement(guest):
    old, _, state, _, _, _ = guest
    state.unlink()
    assert not routes.RouteRetirementPlan.build(snapshot(guest), routes.projection(old)).retirement
    with pytest.raises(RuntimeError, match="history_required"):
        routes.RouteRetirementPlan.build(snapshot(guest, config()), [])


def test_hash_uses_saved_render_version_and_corrupt_state_is_not_absence(guest):
    _, _, state, _, _, _ = guest
    original = state.read_bytes()
    data = json.loads(original)
    data["resolved_config"]["connections"] = []
    state.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="state_invalid"):
        snapshot(guest)
    assert state.read_bytes() != original


def test_pure_projection_matches_renderer_and_streams_without_installed_product(tmp_path):
    cfg = config("10.10.0.0/16")
    endpoint = StrongSwanRenderer().build_interface_endpoints(cfg)[0]
    assert routes.projection(cfg)[0]["if_id"] == endpoint["if_id"]
    source = ops.streamed_bootstrap() + routes.streamed_bootstrap()
    code = (
        "import sys,types; p=types.ModuleType('nebius_vpngw'); p.__path__=[]; sys.modules['nebius_vpngw']=p;"
        + source
    )
    code += f"assert sys.modules['_vpngw_ordinary_routes'].projection({cfg!r}) == {routes.projection(cfg)!r}"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("save_state", [False, True])
def test_commit_then_proof_failure_survives_fresh_journal_and_real_quiescent_inspection(
    guest, monkeypatch, tmp_path, save_state
):
    import yaml

    from nebius_vpngw.deploy import ordinary_remote

    old, kernel, state, store, boot, _ = guest
    initial = routes.RouteRetirementPlan.build(snapshot(guest), routes.projection(old))
    first = begin(guest, initial)
    proof = tmp_path / "ordinary-verification.json"
    proof.write_text(json.dumps({routes.PROOF_KEY: store.commit(first.check(), [])}))
    first.complete()
    desired = config()
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired, proof), [])
    failed = begin(guest, plan, "e")
    store.commit(failed.check(), store.cleanup(failed, desired, kernel.run, boot))
    if save_state:
        StateStore(state).save_last_applied(desired)
    failed.fail()
    value = ops.Journal().read()
    value["owner"]["pid"] = 999999
    ops.Journal().write(value)
    monkeypatch.setattr(ops, "alive", lambda identity: identity["pid"] == os.getpid())
    cfg_path = tmp_path / "config-resolved.yaml"
    cfg_path.write_text(yaml.safe_dump(desired))
    monkeypatch.setattr(ordinary_remote, "CONFIG", cfg_path)
    monkeypatch.setattr(ordinary_remote, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary_remote, "ROUTING_LOCK", ops.ROUTING_LOCK)
    monkeypatch.setattr(ordinary_remote, "DEADLINE", time.monotonic() + 600)

    def command(args, **kwargs):
        if args[:1] == ["ip"]:
            result = kernel.run(args)
            return result.returncode, result.stdout
        return 0, "active"

    monkeypatch.setattr(ordinary_remote, "command", command)
    manifest = {"files": {}, "assets": {}, "route_projection": []}
    before = ordinary_remote.inspect(manifest, verify_runtime=False)
    retry = routes.RouteRetirementPlan.build(before["route_ownership"], [])
    recovered = begin(guest, retry, "f", previous=routes.digest(value))
    after = ordinary_remote.inspect(manifest, verify_runtime=False)
    assert ordinary_remote.stable_predecessor(after) == ordinary_remote.stable_predecessor(before)
    store.commit(recovered.check(), store.cleanup(recovered, desired, kernel.run, boot))
    recovered.complete()
    assert store.verify(desired, kernel.run, boot) != []  # retirement history survives


@pytest.mark.parametrize("failure", ["frr", "proof", None])
def test_composed_reconciliation_cleans_before_reuse_and_retains_partial_successor(
    guest, monkeypatch, tmp_path, failure
):
    import yaml

    from nebius_vpngw.agent import ordinary
    from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget

    _, kernel, state, store, boot, manager = guest
    desired = config("10.40.0.0/16")
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), routes.projection(desired))
    operation = begin(guest, plan)
    path = tmp_path / "resolved.yaml"
    raw = yaml.safe_dump(desired).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(ordinary, "CONFIG", path)
    monkeypatch.setattr(ordinary, "STATE", state)
    monkeypatch.setattr(ordinary, "PROOF", tmp_path / "proof.json")
    monkeypatch.setattr(ordinary, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary, "run", kernel.run)
    monkeypatch.setattr(ordinary, "verify_unchanged", lambda *a: False)

    def render(cfg):
        assert not any(
            routes.prefix(r["dst"]) in {"10.10.0.0/16", "10.20.0.0/16", "10.30.0.0/16"}
            for r in kernel.routes
        )
        return StrongSwanRenderer().build_interface_endpoints(cfg)

    class Swan:
        render_and_apply = staticmethod(render)

    class Xfrm:
        def setup_interfaces(self, endpoints):
            for endpoint in endpoints:
                kernel.routes.extend(
                    {"dst": p, "dev": endpoint["name"], "scope": "link"}
                    for p in endpoint["remote_prefixes"]
                )

    class FRR:
        def render_and_apply(self, *args, **kwargs):
            if failure == "frr":
                raise RuntimeError("injected FRR failure")

    monkeypatch.setattr(ordinary, "StrongSwanRenderer", Swan)
    monkeypatch.setattr(ordinary, "XFRMManager", Xfrm)
    monkeypatch.setattr(ordinary, "FRRRenderer", FRR)
    monkeypatch.setattr(ordinary.firewall, "update_firewall_from_config", lambda *a, **kw: None)
    monkeypatch.setattr(ordinary, "enforce_routing_invariants_locked", lambda *a: None)
    monkeypatch.setattr(ordinary, "observe_local", lambda cfg: "runtime")
    if failure == "proof":
        monkeypatch.setattr(
            ordinary,
            "atomic_write_json",
            lambda *a: (_ for _ in ()).throw(OSError("injected publication failure")),
        )
    token = CURRENT.set(CommandBudget(time.monotonic() + 300, operation=operation))
    try:
        if failure:
            with pytest.raises((RuntimeError, OSError)):
                ordinary.reconcile_locked(desired, raw, verify_only=False)
            assert ops.Journal().read()["state"] == "unresolved"
        else:
            assert ordinary.reconcile_locked(desired, raw, verify_only=False) == "applied"
            assert json.loads(ordinary.PROOF.read_text())[routes.PROOF_KEY] == routes.digest(
                store.ledger()
            )
    finally:
        CURRENT.reset(token)
    if failure:
        replacement = config("10.50.0.0/16")
        observed = store.snapshot(
            desired, state, kernel.run, boot, future=routes.projection(replacement)
        )
        next_plan = routes.RouteRetirementPlan.build(observed, routes.projection(replacement))
        assert any(row["route"]["dst"] == "10.40.0.0/16" for row in next_plan.payload["deletions"])


def test_daemon_has_no_implicit_retirement_authority(guest, monkeypatch, tmp_path):
    from nebius_vpngw.agent import ordinary
    from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget

    _, kernel, state, _, _, _ = guest
    monkeypatch.setattr(ordinary, "STATE", state)
    monkeypatch.setattr(ordinary, "PROOF", tmp_path / "proof")
    monkeypatch.setattr(ordinary, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary, "run", kernel.run)
    monkeypatch.setattr(ordinary, "verify_unchanged", lambda *a: False)
    token = CURRENT.set(CommandBudget(time.monotonic() + 30))
    before = state.read_bytes()
    try:
        with pytest.raises(RuntimeError):
            ordinary.reconcile_locked(config(), b"configuration", verify_only=False)
    finally:
        CURRENT.reset(token)
    assert state.read_bytes() == before and not kernel.deletes
    assert ops.Journal().read() is None


def test_existing_duplicate_claim_envelope_survives_interrupted_resume(guest, monkeypatch):
    old, kernel, state, store, boot, _ = guest
    old.clear()
    old.update(config("10.10.0.0/16", "10.10.0.0/16"))
    StateStore(state).save_last_applied(old)
    kernel.links = Kernel(old).links
    kernel.routes = [{"dst": "10.10.0.0/16", "dev": "xfrm1", "scope": "link"}]
    # Freeze the previous v1 representation explicitly. Both claims must survive
    # even though only the final tunnel owns a route in the actual kernel.
    claims = [
        {
            "name": "xfrm0",
            "if_id": 100,
            "mode": "static",
            "prefixes": ["10.10.0.0/16"],
            "peer": None,
            "inner": None,
        },
        {
            "name": "xfrm1",
            "if_id": 101,
            "mode": "static",
            "prefixes": ["10.10.0.0/16"],
            "peer": None,
            "inner": None,
        },
    ]
    payload = {
        "schema": "nebius-vpngw.ordinary-route-plan.v1",
        "snapshot": "e" * 64,
        "sources": claims,
        "desired": claims,
        "accepted_history": None,
        "retired": [],
        "deletions": [],
        "obligations": [],
    }
    first = begin(guest, routes.RouteRetirementPlan(payload))
    routes._write(
        store.history,
        {
            "schema": "nebius-vpngw.ordinary-route-store.v1",
            "binding": store.binding(first.check()),
            "obligations": [],
        },
    )
    envelope = store.root / (first.check()["id"] + ".json")
    before = (envelope.read_bytes(), store.history.read_bytes())
    first.fail()
    previous = ops.Journal().read()
    previous["owner"]["pid"] = 999999
    ops.Journal().write(previous)
    monkeypatch.setattr(ops, "alive", lambda identity: identity["pid"] == os.getpid())
    assert store.envelope(previous)["plan"] == payload
    observed = snapshot(guest, old)
    assert routes.keys(observed["pending_sources"]) == {
        ("xfrm0", "10.10.0.0/16"),
        ("xfrm1", "10.10.0.0/16"),
    }
    assert (envelope.read_bytes(), store.history.read_bytes()) == before
    retry_plan = routes.RouteRetirementPlan.build(observed, routes.projection(old))
    retry = begin(guest, retry_plan, "f", previous=routes.digest(previous))
    obligations = store.cleanup(retry, old, kernel.run, boot)
    store.commit(retry.check(), obligations)
    retry.complete()
    assert ops.Journal().read()["state"] == "complete"
    assert store.ledger()["binding"] == store.binding(ops.Journal().read())
    assert store.verify(old, kernel.run, boot) == []
    assert routes.projection(old) == claims
    assert not kernel.deletes


@pytest.mark.parametrize("transition", ["same", "one", "none", "bgp"])
def test_duplicate_retirement_keeps_superseded_history(guest, transition):
    old, kernel, state, store, boot, _ = guest
    old.clear()
    old.update(config("10.10.0.0/16", "10.10.0.0/16"))
    StateStore(state).save_last_applied(old)
    kernel.links = Kernel(old).links
    kernel.routes = [{"dst": "10.10.0.0/16", "dev": "xfrm1", "scope": "link"}]
    foreign = [
        {"dst": "10.10.0.0/16", "dev": "xfrm0", "table": 200},
        {"dst": "10.20.0.0/16", "dev": "xfrm0", "protocol": "bgp"},
        {"dst": "169.254.1.0/30", "dev": "xfrm0", "protocol": "kernel", "scope": "link"},
    ]
    kernel.routes.extend(copy.deepcopy(foreign))
    desired = copy.deepcopy(old)
    if transition == "one":
        desired["connections"][0]["tunnels"].pop()
    elif transition == "none":
        desired["connections"] = []
    elif transition == "bgp":
        desired["connections"][0]["routing_mode"] = "bgp"
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), routes.projection(desired))
    expected = (
        []
        if transition == "same"
        else [["xfrm1", "10.10.0.0/16"]]
        if transition == "one"
        else [["xfrm0", "10.10.0.0/16"], ["xfrm1", "10.10.0.0/16"]]
    )
    assert plan.payload["retired"] == expected
    operation = begin(guest, plan)
    obligations = store.cleanup(operation, desired, kernel.run, boot)
    store.commit(operation.check(), obligations)
    assert len(kernel.deletes) == (transition != "same")
    assert all(row in kernel.routes for row in foreign)
    assert len(obligations) == len(expected)
    if transition in {"none", "bgp"}:
        before = list(kernel.deletes)
        kernel.routes.append({"dst": "10.10.0.0/16", "dev": "xfrm0", "scope": "link"})
        with pytest.raises(RuntimeError, match="ordinary_retired_route_present"):
            store.verify(desired, kernel.run, boot)
        assert kernel.deletes == before


def test_saved_proof_lost_reply_retry_rebinds_ledger_before_final_confirmation(
    guest, monkeypatch, tmp_path
):
    import hashlib

    import yaml

    from nebius_vpngw.agent import ordinary
    from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget
    from nebius_vpngw.deploy import ordinary_remote

    _, kernel, state, store, boot, manager = guest
    desired = config()
    raw = yaml.safe_dump(desired).encode()
    cfg_path, proof = tmp_path / "config-resolved.yaml", tmp_path / "ordinary-verification.json"
    cfg_path.write_bytes(raw)
    for module in (ordinary, ordinary_remote):
        monkeypatch.setattr(module, "CONFIG", cfg_path)
        monkeypatch.setattr(module, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary, "STATE", state)
    monkeypatch.setattr(ordinary, "PROOF", proof)
    monkeypatch.setattr(ordinary, "package_identity", lambda: "c" * 64)
    monkeypatch.setattr(ordinary, "run", kernel.run)
    monkeypatch.setattr(ordinary, "observe_local", lambda cfg: "verified-runtime")
    renders = []

    class Swan:
        def render_and_apply(self, cfg):
            renders.append(cfg)
            return []

    class FRR:
        def render_and_apply(self, cfg, **kwargs):
            pass

    monkeypatch.setattr(ordinary, "StrongSwanRenderer", Swan)
    monkeypatch.setattr(ordinary, "FRRRenderer", FRR)
    monkeypatch.setattr(ordinary.firewall, "update_firewall_from_config", lambda *a, **kw: None)
    monkeypatch.setattr(ordinary, "enforce_routing_invariants_locked", lambda cfg: None)
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), [])
    config_hash = hashlib.sha256(raw).hexdigest()
    first = begin(guest, plan, config_hash=config_hash)
    token = CURRENT.set(CommandBudget(time.monotonic() + 300, operation=first))
    try:
        assert ordinary.reconcile_locked(desired, raw, verify_only=False) == "applied"
    finally:
        CURRENT.reset(token)
    assert ordinary.verify_unchanged(desired, raw)
    first.fail()  # reply lost after saving proof, before the parent completes its journal
    previous = ops.Journal().read()
    previous["owner"]["pid"] = 999999
    ops.Journal().write(previous)
    monkeypatch.setattr(ops, "alive", lambda identity: identity["pid"] == os.getpid())
    observed = store.snapshot(desired, state, kernel.run, boot, proof=proof)
    retry_plan = routes.RouteRetirementPlan.build(observed, [])
    retry = begin(guest, retry_plan, "f", previous=routes.digest(previous), config_hash=config_hash)
    deletes_before = list(kernel.deletes)
    token = CURRENT.set(CommandBudget(time.monotonic() + 300, operation=retry))
    try:
        assert ordinary.reconcile_locked(desired, raw, verify_only=False) == "applied"
        assert store.ledger()["binding"] == store.binding(retry.check())
        assert ordinary.verify_unchanged(desired, raw)
        assert kernel.deletes == deletes_before
        assert len(renders) == 2  # each approved mutating operation follows the canonical path
        manifest = {
            "config_sha256": hashlib.sha256(raw).hexdigest(),
            "files": {},
            "assets": {},
            "expected_dependencies": {},
            "package_identity": "c" * 64,
            "render_version": 4,
            "route_projection": [],
        }
        final = {
            "files": {},
            "assets": {},
            "asset_modes": {},
            "agent_state": "active",
            "boot_id": boot,
            "config_sha256": manifest["config_sha256"],
            "dependencies": {},
        }
        monkeypatch.setattr(ordinary_remote, "inspect", lambda *a, **kw: final)
        monkeypatch.setattr(ordinary_remote, "ROUTING_LOCK", ops.ROUTING_LOCK)
        monkeypatch.setattr(ordinary_remote, "command_result", kernel.run)

        def receipt(*args, **kwargs):
            before = proof.read_bytes()
            assert ordinary.reconcile_locked(desired, raw, verify_only=True) == "unchanged"
            assert proof.read_bytes() == before
            return {
                "package_identity": "c" * 64,
                "render_version": 4,
                "proof_sha256": hashlib.sha256(before).hexdigest(),
            }

        monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
        ordinary_remote.final_confirmation(manifest, final, "f" * 32, retry)
        assert ops.Journal().read()["state"] == "complete"
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize("fault", ["boot", "prefix", "ifindex", "parent", "binding"])
def test_malformed_nested_ledger_cannot_be_pruned_as_success(guest, fault):
    _, kernel, _, store, boot, _ = guest
    desired = config()
    plan = routes.RouteRetirementPlan.build(snapshot(guest, desired), [])
    operation = begin(guest, plan)
    obligations = store.cleanup(operation, desired, kernel.run, boot)
    store.commit(operation.check(), obligations)
    value = json.loads(store.history.read_text())
    if fault == "binding":
        value["binding"]["id"] = "invalid"
    elif fault == "prefix":
        value["obligations"][0]["prefix"] = "not-a-cidr"
    else:
        value["obligations"][0]["link"][fault] = "corrupt"
    routes._write(store.history, value)
    with pytest.raises((RuntimeError, ValueError)):
        store.verify(desired, kernel.run, boot)


def test_sidecar_compaction_retains_pending_operation_and_discards_orphans(guest):
    _, _, _, store, _, _ = guest
    plan = routes.RouteRetirementPlan.build(snapshot(guest, config()), [])
    operation = begin(guest, plan)
    orphan = {**operation.check(), "id": "e" * 32}
    store.prepare(orphan, plan, approved=True)
    assert (store.root / (operation.check()["id"] + ".json")).exists()
    successor = {**orphan, "id": "f" * 32}
    store.prepare(successor, plan, approved=True)
    assert (store.root / (operation.check()["id"] + ".json")).exists()
    assert not (store.root / (orphan["id"] + ".json")).exists()
    assert (store.root / (successor["id"] + ".json")).exists()
