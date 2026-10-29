from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nebius_vpngw.agent import xfrm_manager
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha.runtime import (
    BoundRouteRuntime,
    CommandResult,
    SystemDataPlaneRuntime,
)
from nebius_vpngw.agent.vm_ha.static_routes import select_static_routes, static_route_candidates
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
)
from nebius_vpngw.tunnel_state import collect_tunnel_state

PREFIX = "10.80.1.0/24"


def config(*, mixed=False):
    connections = []
    if mixed:
        connections.append(
            {
                "name": "other",
                "routing_mode": "bgp",
                "tunnels": [
                    {"name": "other-1", "remote_public_ip": "192.0.2.30", "ha_role": "active"}
                ],
            }
        )
    connections.append(
        {
            "name": "uk-us",
            "routing_mode": "static",
            "remote_prefixes": [PREFIX],
            "tunnels": [
                {"name": "uk-us-active", "remote_public_ip": "192.0.2.10", "ha_role": "active"},
                {"name": "uk-us-passive", "remote_public_ip": "192.0.2.20", "ha_role": "passive"},
            ],
        }
    )
    return {
        "vm_ha": {"enabled": True},
        "connections": connections,
        "gateway": {"local_prefixes": ["10.81.0.0/16"]},
    }


class Kernel:
    """Model destination replacement and independent IPsec state, rather than echoing intent."""

    def __init__(self):
        self.routes = {}
        self.devices = ["xfrm0", "xfrm1", "xfrm2"]
        self.usable = set()
        self.calls = []
        self.forwarding = "0"
        self.fail_write = False

    def run(self, arguments, _timeout=5):
        args = tuple(arguments)
        self.calls.append(args)
        command = args[1:]
        if args[0].endswith("systemctl"):
            return CommandResult(0)
        if args[0].endswith("vtysh"):
            return CommandResult(0, "" if command[-1] == "show running-config" else "{}")
        if args[0].endswith("sysctl"):
            if command[-1] == "net.ipv4.ip_forward":
                return CommandResult(0, self.forwarding)
            self.forwarding = command[-1].split("=")[-1]
            return CommandResult(0)
        if command == ("-d", "-j", "link", "show", "type", "xfrm"):
            links = []
            for index, device in enumerate(self.devices):
                links.append({"ifname": device, "linkinfo": {"info_data": {"if_id": 100 + index}}})
            return CommandResult(0, json.dumps(links))
        if command in {("-j", "xfrm", "state"), ("-j", "xfrm", "policy")}:
            ids = []
            for device in self.usable:
                ids.append({"if_id": 100 + self.devices.index(device)})
            return CommandResult(0, json.dumps(ids))
        if command == ("-j", "-4", "route", "show", "table", "all"):
            return CommandResult(
                0,
                json.dumps(
                    [{"dst": prefix, "dev": device} for prefix, device in self.routes.items()]
                ),
            )
        if command in {("rule", "show"), ("route", "show", "169.254.0.0/16")}:
            return CommandResult(0, "")
        if command[:2] == ("route", "replace"):
            if self.fail_write:
                return CommandResult(1, "denied")
            self.routes[command[2]] = command[4]
            return CommandResult(0)
        raise AssertionError(args)

    def subprocess(self, args, **kwargs):
        result = self.run(args)
        return subprocess.CompletedProcess(args, result.returncode, result.stdout, "")


def action(kind=ActionKind.PREPARE_CANDIDATE_DATAPLANE):
    return ControllerAction(
        kind,
        "operation",
        "boot",
        "node-a",
        "allocation",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )


def runtime(tmp_path, kernel, cfg):
    return SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest=hashlib.sha256(b"{}").hexdigest(),
        static_candidates=static_route_candidates(cfg),
        runner=kernel.run,
        monotonic_clock=lambda: 10.0,
    )


def readiness(data_plane):
    # The calculation has no cloud effects. Its only inputs are the committed
    # manifest and the real local observer, fed by our stateful kernel model.
    routes = object.__new__(BoundRouteRuntime)
    routes.data_plane = data_plane
    routes.static_manifest = SimpleNamespace(prefixes=frozenset({PREFIX}))
    routes.bgp_policies = []
    routes.expected_import_policy_digest = hashlib.sha256(b"{}").hexdigest()
    return routes.readiness()


@pytest.mark.parametrize("mixed", [False, True])
def test_real_writer_and_observer_select_established_path(tmp_path, mixed):
    cfg = config(mixed=mixed)
    kernel = Kernel()
    active = "xfrm1" if mixed else "xfrm0"
    passive = "xfrm2" if mixed else "xfrm1"
    kernel.usable = {active}
    endpoints = collect_tunnel_state(cfg, log=lambda _: None)[2]
    with patch.object(xfrm_manager, "run", side_effect=kernel.subprocess):
        # The historical writer reproduces the live failure before selection.
        for endpoint in endpoints:
            if endpoint["mode"] == "static":
                xfrm_manager.XFRMManager()._add_static_routes(
                    endpoint["name"], endpoint["remote_prefixes"]
                )
        assert kernel.routes == {PREFIX: passive}
        assert not readiness(runtime(tmp_path, kernel, cfg)).static_ready
        selected = xfrm_manager.vm_ha_static_endpoints(cfg, endpoints)
        for endpoint in selected:
            if endpoint["mode"] == "static":
                xfrm_manager.XFRMManager()._add_static_routes(
                    endpoint["name"], endpoint["remote_prefixes"]
                )
    assert kernel.routes == {PREFIX: active}
    assert passive not in kernel.routes.values()
    assert readiness(runtime(tmp_path, kernel, cfg)).promotion_ready


def test_observation_detects_stale_route_without_mutating_then_owner_prepares(tmp_path):
    cfg = config(mixed=True)
    kernel = Kernel()
    kernel.usable = {"xfrm1"}
    kernel.routes = {PREFIX: "xfrm2"}
    data_plane = runtime(tmp_path, kernel, cfg)
    data_plane._write_state(action(), DataPlaneMode.PASSIVE)
    before = readiness(data_plane)
    assert not before.static_ready
    assert before.candidate_preparation_required
    assert before.static_route_repair_ready
    assert before.transfer_ready
    assert kernel.routes == {PREFIX: "xfrm2"}
    assert not any(call[1:3] == ("route", "replace") for call in kernel.calls)
    data_plane.prepare_candidate(action())
    assert kernel.routes == {PREFIX: "xfrm1"}
    assert readiness(data_plane).promotion_ready
    assert kernel.forwarding == "0"


def test_repair_follows_remote_owner_failover_and_failback(tmp_path):
    cfg = config(mixed=True)
    kernel = Kernel()
    kernel.usable = {"xfrm1"}
    kernel.routes = {PREFIX: "xfrm1"}
    data_plane = runtime(tmp_path, kernel, cfg)
    repair = replace(
        action(),
        kind=ActionKind.REPAIR_LOCAL_DATAPLANE,
        repair_deadline_at=15.0,
        repair_reasons=("static-routes-not-ready", "xfrm-not-ready"),
    )
    for usable in [{"xfrm2"}, {"xfrm1", "xfrm2"}, {"xfrm1"}]:
        kernel.usable = usable
        expected = "xfrm1" if "xfrm1" in usable else "xfrm2"
        data_plane.repair_local(repair)
        assert kernel.routes == {PREFIX: expected}
        assert readiness(data_plane).promotion_ready
    assert not any(call[0].endswith("systemctl") and "restart" in call for call in kernel.calls)


def test_no_sa_does_not_become_ready_or_promotable(tmp_path):
    cfg = config()
    kernel = Kernel()
    data_plane = runtime(tmp_path, kernel, cfg)
    assert select_static_routes(static_route_candidates(cfg), set()) == {PREFIX: "xfrm0"}
    assert not readiness(data_plane).promotion_ready
    assert not readiness(data_plane).static_route_repair_ready
    data_plane._write_state(action(), DataPlaneMode.PASSIVE)
    data_plane.prepare_candidate(action())
    assert not kernel.routes


@pytest.mark.parametrize("mode", [DataPlaneMode.ACTIVE, DataPlaneMode.BLOCKED])
def test_candidate_preparation_requires_passive_fence(tmp_path, mode):
    kernel = Kernel()
    kernel.usable = {"xfrm1"}
    data_plane = runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), mode)
    with pytest.raises(RuntimeError, match="passive fenced"):
        data_plane.prepare_candidate(action())
    assert not kernel.routes


def test_bgp_and_service_readiness_are_not_bypassed():
    repairable = LocalReadiness(True, False, True, False, static_route_repair_ready=True)
    assert repairable.transfer_ready
    assert not repairable.promotion_ready
    assert not replace(repairable, bgp_ready=False).transfer_ready
    assert not replace(repairable, service_healthy=False).transfer_ready
    assert not replace(repairable, routing_hygiene_ready=False).transfer_ready


def test_ordinary_selection_is_unchanged():
    cfg = config()
    cfg.pop("vm_ha")
    endpoints = collect_tunnel_state(cfg, log=lambda _: None)[2]
    with patch.object(
        xfrm_manager, "run", side_effect=AssertionError("ordinary path queried HA evidence")
    ):
        assert xfrm_manager.vm_ha_static_endpoints(cfg, endpoints) is endpoints


def test_write_failure_keeps_forwarding_fenced(tmp_path):
    kernel = Kernel()
    kernel.usable = {"xfrm0"}
    kernel.fail_write = True
    data_plane = runtime(tmp_path, kernel, config())
    data_plane._write_state(action(), DataPlaneMode.PASSIVE)
    with pytest.raises(RuntimeError, match="selection failed"):
        data_plane.prepare_candidate(action())
    assert kernel.forwarding == "0"
    assert not kernel.routes


def test_selection_does_not_depend_on_tunnel_order():
    cfg = config()
    cfg["connections"][0]["tunnels"].reverse()
    candidates = static_route_candidates(cfg)
    assert select_static_routes(candidates, {"xfrm0", "xfrm1"}) == {PREFIX: "xfrm1"}
    assert select_static_routes(candidates, {"xfrm0"}) == {PREFIX: "xfrm0"}
    assert select_static_routes(candidates, set()) == {PREFIX: "xfrm1"}
