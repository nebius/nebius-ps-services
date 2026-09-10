from __future__ import annotations

import copy
import ipaddress
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw import ordinary_routes as routes
from nebius_vpngw.agent import ordinary, xfrm_manager
from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget
from nebius_vpngw.tunnel_state import collect_tunnel_state


def shared_config():
    return yaml.safe_load(
        (Path(__file__).parents[2] / "examples/static-example.config.yaml").read_text()
    )


def write_routes(monkeypatch, cfg):
    """Model replacement, not an impossible route per configured claim."""
    final = {}

    def replace(args, **kwargs):
        assert args[:3] == ["ip", "route", "replace"] and args[4] == "dev"
        final[routes.prefix(args[3])] = {
            "dst": routes.prefix(args[3]),
            "dev": args[5],
            "protocol": "boot",
            "scope": "link",
        }
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(xfrm_manager, "run", replace)
    for endpoint in collect_tunnel_state(cfg, log=lambda _: None)[2]:
        if endpoint["mode"] == "static":
            xfrm_manager.XFRMManager()._add_static_routes(
                endpoint["name"], endpoint["remote_prefixes"]
            )
    return list(final.values())


def test_shipped_shared_static_example_passes_real_verifier(monkeypatch, ordinary_static_observer):
    cfg = shared_config()
    state = ordinary_static_observer(cfg)
    state.routes = write_routes(monkeypatch, cfg)
    assert state.routes == [
        {"dst": "192.168.0.0/16", "dev": "xfrm1", "protocol": "boot", "scope": "link"}
    ]
    assert len(ordinary.observe_local(cfg)) == 64


@pytest.mark.parametrize("fault", ["missing", "wrong", "loser", "multipath"])
def test_shared_destination_requires_only_the_selected_owner(
    monkeypatch, ordinary_static_observer, fault
):
    cfg = shared_config()
    state = ordinary_static_observer(cfg)
    state.routes = write_routes(monkeypatch, cfg)
    if fault == "missing":
        state.routes.clear()
    elif fault == "wrong":
        state.routes[0]["dev"] = "xfrm0"
    elif fault == "loser":
        state.routes.append(dict(state.routes[0], dev="xfrm0", metric=50))
    else:
        state.routes.append(
            {"dst": "192.168.0.0/16", "nexthops": [{"dev": "xfrm0"}, {"dev": "xfrm1"}]}
        )
    with pytest.raises(RuntimeError, match="ordinary local invariant"):
        ordinary.observe_local(cfg)


@pytest.mark.parametrize("kind", ["kernel", "numeric-kernel", "boot", "gateway", "multipath"])
def test_connected_subnet_cannot_disguise_a_losing_static_route(
    monkeypatch, ordinary_static_observer, kind
):
    cfg = shared_config()
    cfg["connections"][0]["remote_prefixes"] = ["169.254.10.0/30"]
    tunnel = cfg["connections"][0]["tunnels"][0]
    tunnel.update(inner_cidr="169.254.10.0/30", inner_local_ip="169.254.10.1")
    state = ordinary_static_observer(cfg)
    state.routes = write_routes(monkeypatch, cfg)
    connected = {"dst": "169.254.10.0/30", "dev": "xfrm0", "protocol": "kernel", "scope": "link"}
    if kind == "numeric-kernel":
        connected.update(protocol=2, scope=253)
    elif kind == "boot":
        connected["protocol"] = "boot"
    elif kind == "gateway":
        connected["gateway"] = "169.254.10.2"
    elif kind == "multipath":
        connected["nexthops"] = [{"dev": "xfrm0"}, {"dev": "xfrm1"}]
    state.routes.append(connected)
    if kind in {"kernel", "numeric-kernel"}:
        assert len(ordinary.observe_local(cfg)) == 64
    else:
        with pytest.raises(RuntimeError, match="ordinary local invariant"):
            ordinary.observe_local(cfg)


@pytest.mark.parametrize("transition", ["same", "reorder", "disable", "remove", "bgp", "overlap"])
def test_effective_selection_matches_existing_writer_without_changing_claims(
    monkeypatch, transition
):
    cfg = shared_config()
    tunnels = cfg["connections"][0]["tunnels"]
    if transition == "reorder":
        tunnels.reverse()
    elif transition == "disable":
        tunnels[-1]["ha_role"] = "disable"
    elif transition == "remove":
        tunnels.pop()
    elif transition == "bgp":
        tunnels[-1]["routing_mode"] = "bgp"
    elif transition == "overlap":
        tunnels[-1]["static_routes"] = {"remote_prefixes": ["192.168.1.0/24"]}
    projection = routes.projection(cfg)
    before = copy.deepcopy(projection)
    actual = {row["dst"]: row["dev"] for row in write_routes(monkeypatch, cfg)}
    assert routes.effective_static_routes(projection) == actual
    assert projection == before
    if transition in {"same", "reorder"}:
        assert routes.keys(projection, static_only=True) == {
            ("xfrm0", "192.168.0.0/16"),
            ("xfrm1", "192.168.0.0/16"),
        }


def test_equal_normalized_destinations_and_distinct_overlap():
    cfg = shared_config()
    tunnels = cfg["connections"][0]["tunnels"]
    tunnels[0]["static_routes"] = {"remote_prefixes": ["192.0.2.1", "192.0.2.0/24"]}
    tunnels[1]["static_routes"] = {"remote_prefixes": ["192.0.2.1/32"]}
    assert routes.effective_static_routes(routes.projection(cfg)) == {
        str(ipaddress.ip_network("192.0.2.0/24")): "xfrm0",
        "192.0.2.1/32": "xfrm1",
    }


def test_shared_static_apply_completes_and_reapply_is_inert(
    monkeypatch, tmp_path, ordinary_operation_guest, ordinary_static_observer
):
    cfg = shared_config()
    state = ordinary_static_observer(cfg)
    raw = yaml.safe_dump(cfg).encode()
    config_path = tmp_path / "resolved.yaml"
    config_path.write_bytes(raw)
    monkeypatch.setattr(ordinary, "CONFIG", config_path)
    monkeypatch.setattr(ordinary, "STATE", tmp_path / "last-applied.json")
    monkeypatch.setattr(ordinary, "PROOF", tmp_path / "proof.json")
    monkeypatch.setattr(ordinary, "BOOT", ops.BOOT)
    monkeypatch.setattr(ordinary, "package_identity", lambda: "a" * 64)
    monkeypatch.setattr(
        ordinary,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args, 0, json.dumps(state.ip_rows(args)), ""
        ),
    )
    # Non-route render/service effects stay inside the fixture; route writing,
    # full ordinary verification, persistence and operation completion are real.
    endpoints = collect_tunnel_state(cfg, log=lambda _: None)[2]
    monkeypatch.setattr(ordinary.StrongSwanRenderer, "render_and_apply", lambda *a, **kw: endpoints)
    monkeypatch.setattr(ordinary.FRRRenderer, "render_and_apply", lambda *a, **kw: None)
    monkeypatch.setattr(ordinary.firewall, "update_firewall_from_config", lambda *a, **kw: None)
    monkeypatch.setattr(ordinary, "enforce_routing_invariants_locked", lambda cfg: None)
    writes = []

    def setup(*args):
        writes.append("routes")
        state.routes = write_routes(monkeypatch, cfg)

    monkeypatch.setattr(ordinary.XFRMManager, "setup_interfaces", setup)
    token = CURRENT.set(CommandBudget(time.monotonic() + 300))
    try:
        assert ordinary.reconcile_locked(cfg, raw, verify_only=False) == "applied"
        assert ops.Journal().read()["state"] == "complete"
        persisted = [ordinary.STATE, ordinary.PROOF, ops.JOURNAL, routes.RouteStore().history]
        before = [path.read_bytes() for path in persisted]
        assert ordinary.reconcile_locked(cfg, raw, verify_only=False) == "unchanged"
        assert ordinary.reconcile_locked(cfg, raw, verify_only=True) == "unchanged"
        assert writes == ["routes"]
        assert [path.read_bytes() for path in persisted] == before
    finally:
        CURRENT.reset(token)
