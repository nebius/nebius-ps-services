"""Composed Static recovery with native effects and an isolated kernel/daemon model."""

import json
from copy import deepcopy
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest
from test_ha_tunnel_selection import AUTH, Effects, config

from nebius_vpngw import ipsec_observation
from nebius_vpngw.agent.vm_ha import tunnel_runtime, tunnels
from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import STATE_NAME, Health, TunnelController, read_state
from nebius_vpngw.ha_tunnel_policy import connection_policies


class NativeModel:
    def __init__(self, directory, monkeypatch):
        self.cfg = config()
        alternate = deepcopy(self.cfg["connections"][0]["tunnels"][1])
        alternate.update(
            name="alternate", remote_public_ip="192.0.2.3", inner_cidr="169.254.3.0/30"
        )
        self.cfg["connections"][0]["tunnels"].append(alternate)
        unrelated = deepcopy(self.cfg["connections"][0])
        unrelated["name"] = "other-site"
        unrelated["routing_mode"] = "bgp"
        unrelated["remote_prefixes"] = ["10.2.0.0/16"]
        unrelated["tunnels"] = [unrelated["tunnels"][0]]
        unrelated["tunnels"][0]["name"] = "unrelated"
        unrelated["tunnels"][0]["remote_public_ip"] = "203.0.113.10"
        self.cfg["connections"].append(unrelated)
        self.policies = connection_policies(self.cfg)
        self.paths = {}
        for policy in self.policies:
            for path in policy.paths:
                self.paths[path.name] = path
        self.clock = 100.0
        self.valid = True
        self.health = {
            "preferred": Health.DOWN,
            "backup": Health.HEALTHY,
            "alternate": Health.HEALTHY,
            "unrelated": Health.HEALTHY,
        }
        self.loaded = set(self.paths)
        self.events = []
        self.routes = [{"dst": "10.1.0.0/16", "dev": "xfrm0"}]
        self.states = []
        self.policies_native = []
        self.sas = {}
        self.on_load = lambda: None
        self.on_routes = lambda: None
        self.ping_ok = True
        # One exact command times out once, as a stalled native read would.
        self.fail_once = None
        self.faults = 0
        self.conf = directory / "swanctl.conf"
        monkeypatch.setattr(tunnel_runtime, "SWANCTL_CONF", self.conf)

        class Renderer:
            def render_and_apply(_self, cfg, *, rendered_files):
                rendered_files[self.conf] = json.dumps(cfg, sort_keys=True)

        monkeypatch.setattr(tunnel_runtime, "StrongSwanRenderer", Renderer)
        for name, path in self.paths.items():
            child = {
                "name": name,
                "uniqueid": str(path.if_id + 10),
                "state": "INSTALLED",
                "protocol": "ESP",
                "reqid": str(path.if_id),
                "spi-in": f"{path.if_id + 1000:08x}",
                "spi-out": f"{path.if_id + 2000:08x}",
                "if-id-in": f"{path.if_id:08x}",
                "if-id-out": f"{path.if_id:08x}",
                "local-ts": ["0.0.0.0/0"],
                "remote-ts": ["0.0.0.0/0"],
            }
            self.sas[name] = {
                "uniqueid": str(path.if_id),
                "state": "ESTABLISHED",
                "local-host": "198.51.100.10",
                "remote-host": path.outer_pair[1],
                "local-id": "198.51.100.10",
                "remote-id": path.outer_pair[1],
                "child-sas": {name: child},
            }
            for src, dst, spi in (
                (path.outer_pair[1], "198.51.100.10", path.if_id + 1000),
                ("198.51.100.10", path.outer_pair[1], path.if_id + 2000),
            ):
                self.states.append(
                    {
                        "src": src,
                        "dst": dst,
                        "spi": hex(spi),
                        "proto": "esp",
                        "mode": "tunnel",
                        "if_id": path.if_id,
                        "reqid": path.if_id,
                    }
                )
            for index, direction in enumerate(("out", "in", "fwd"), 1):
                src, dst = "198.51.100.10", path.outer_pair[1]
                if direction != "out":
                    src, dst = dst, src
                self.policies_native.append(
                    {
                        "src": "0.0.0.0/0",
                        "dst": "0.0.0.0/0",
                        "dir": direction,
                        "index": path.if_id * 8 + index,
                        "priority": 1000,
                        "if_id": path.if_id,
                        "tmpl": [
                            {
                                "src": src,
                                "dst": dst,
                                "proto": "esp",
                                "mode": "tunnel",
                                "reqid": path.if_id,
                            }
                        ],
                    }
                )
        self.original = deepcopy(self.sas)
        self.effects = NativeTunnelEffects(
            self.cfg,
            directory,
            lambda authority: self.valid and authority == AUTH,
            active_revalidate=lambda authority: self.valid and authority == AUTH,
            routing_lock=directory / "routing.lock",
            clock=lambda: self.clock,
            command_runner=self.command,
        )
        self.selector = TunnelController(self.cfg, directory / STATE_NAME, self.effects)
        self.selector.bind(AUTH)
        self.selector._begin(self.policies[0], "backup", "automatic-recovery", AUTH, self.clock)
        self.selector.state["selected"]["site"] = "backup"
        self.selector._save()

    def command(self, argv, timeout):
        assert timeout > 0
        argv = tuple(argv)
        self.events.append(argv)
        if argv == self.fail_once:
            self.fail_once = None
            self.faults += 1
            raise TimeoutExpired(argv, timeout)
        output = ""
        if argv == ipsec_observation.command():
            entries = []
            for name, sa in self.sas.items():
                if self.health[name] is Health.HEALTHY or name == "preferred":
                    observed = deepcopy(sa)
                    if self.health[name] is not Health.HEALTHY:
                        observed["child-sas"][name]["state"] = "DELETING"
                    entries.append({name: observed})
            output = ipsec_observation.Inventory.from_events(entries).json()
        elif argv == (*ipsec_observation.command(), "--connections"):
            output = json.dumps(sorted(self.loaded))
        elif argv[:3] == ("ip", "xfrm", "state") or argv == ("ip", "-j", "xfrm", "state"):
            if "delete" in argv:
                spi = int(argv[argv.index("spi") + 1], 0)
                assert spi in {1100, 2100}, "Healthy or unrelated SA deletion"
                self.states[:] = [state for state in self.states if int(state["spi"], 0) != spi]
            else:
                output = json.dumps(self.states)
        elif argv[:3] == ("ip", "xfrm", "policy") or argv in {
            ("ip", "-s", "xfrm", "policy"),
            ("ip", "-j", "xfrm", "policy"),
        }:
            if "delete" in argv:
                assert argv[-1] == "100", "Healthy or unrelated policy deletion"
                index = int(argv[argv.index("index") + 1])
                self.policies_native[:] = [p for p in self.policies_native if p["index"] != index]
            else:
                output = json.dumps(self.policies_native)
        elif "link" in argv:
            output = json.dumps(
                [
                    {
                        "ifname": path.interface,
                        "flags": ["UP"],
                        "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": path.if_id}},
                    }
                    for path in self.paths.values()
                ]
            )
        elif "address" in argv:
            output = json.dumps(
                [{"addr_info": [{"local": "198.51.100.10"}, {"local": "169.254.2.1"}]}]
            )
        elif argv[:2] == ("ip", "-batch"):
            for line in Path(argv[2]).read_text().splitlines():
                _, _, destination, _, device = line.split()
                self.routes = [{"dst": destination, "dev": device}]
            self.on_routes()
        elif "route" in argv:
            output = json.dumps(self.routes)
        elif argv[0] == "ping":
            failed = not self.ping_ok or self.health["backup"] is not Health.HEALTHY
            return CompletedProcess(argv, int(failed), "", "")
        elif argv[:2] == ("swanctl", "--load-all"):
            cfg = json.loads(self.conf.read_text())
            assert not cfg.get("_ha_suspended_connections"), "Connection-wide suspension"
            suspended = set(cfg.get("_ha_suspended_tunnels", []))
            assert suspended <= {"preferred"}, "Healthy or unrelated path unloaded"
            self.loaded = set(self.paths) - suspended
            for name in suspended:
                self.sas.pop(name, None)
            self.on_load()
        elif argv[:2] == ("swanctl", "--terminate"):
            raise AssertionError("Unloaded failed IKE must already be absent")
        else:
            raise AssertionError(argv)
        return CompletedProcess(argv, 0, output, "")


@pytest.fixture
def native(tmp_path, monkeypatch):
    return NativeModel(tmp_path, monkeypatch)


def test_promotion_preserves_proven_sa_and_unrelated_connection(native):
    native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.sas["backup"] == native.original["backup"]
    assert native.sas["unrelated"] == native.original["unrelated"]
    assert "preferred" not in native.sas
    assert native.routes == [{"dst": "10.1.0.0/16", "dev": "xfrm1"}]
    assert native.effects.verify(native.policies[0], "backup") is Health.HEALTHY
    assert len([call for call in native.events if call[0] == "ping"]) == 3


@pytest.mark.parametrize("health", [Health.UNKNOWN, Health.HEALTHY])
def test_cleanup_excludes_other_paths_without_current_down_proof(native, health):
    original_health = native.effects._path_health
    native.effects._path_health = lambda policy, name, selected: (
        health if name == "alternate" else original_health(policy, name, selected=selected)
    )
    native.effects.activate(native.policies[0], "backup", AUTH)
    for name in ("backup", "alternate", "unrelated"):
        assert native.sas[name] == native.original[name]


def test_primary_recovery_cancels_promotion_before_any_native_mutation(native):
    native.health["preferred"] = Health.HEALTHY
    with pytest.raises(RuntimeError, match="no longer confirmed down"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.sas == native.original
    assert not any(call[0] == "swanctl" or "-batch" in call for call in native.events)


@pytest.mark.parametrize("health", [Health.DOWN, Health.UNKNOWN])
def test_stale_candidate_health_cannot_authorize_mutation(native, health):
    native.health["backup"] = health
    with pytest.raises(RuntimeError, match="native health"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.sas == native.original
    assert not any(call[0] == "swanctl" or "-batch" in call for call in native.events)


def test_candidate_loss_during_cleanup_prevents_route_write(native):
    native.on_load = lambda: native.health.update(backup=Health.DOWN)
    with pytest.raises(RuntimeError, match="native health"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.routes[0]["dev"] == "xfrm0"
    assert native.sas["unrelated"] == native.original["unrelated"]


def test_authority_loss_during_cleanup_prevents_more_configuration_or_routes(native):
    def revoke():
        native.valid = False

    native.on_load = revoke
    with pytest.raises(RuntimeError, match="authority"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.routes[0]["dev"] == "xfrm0"
    assert len([call for call in native.events if call[:2] == ("swanctl", "--load-all")]) == 1


def test_failure_after_route_switch_is_detected_on_next_controller_pass(native):
    native.on_routes = lambda: native.health.update(backup=Health.DOWN)
    native.selector._advance(AUTH, native.clock)
    assert native.selector.state["operation"]["phase"] == "verify"
    native.clock += 1
    native.selector._advance(AUTH, native.clock)
    assert native.selector.state["operation"]["phase"] == "restore"
    assert native.sas["unrelated"] == native.original["unrelated"]


def test_post_switch_verification_does_not_reuse_a_cached_inner_peer_success(native):
    native.selector._advance(AUTH, native.clock)
    native.ping_ok = False
    native.clock += 1
    native.selector._advance(AUTH, native.clock)
    operation = native.selector.state["operation"]
    assert operation["phase"] == "verify"
    assert operation["successes"] == 0
    assert native.sas["backup"] == native.original["backup"]


def test_slow_work_does_not_steal_post_activation_verification_window(tmp_path):
    effects = Effects()
    selector = TunnelController(config(), tmp_path / STATE_NAME, effects)
    selector.bind(AUTH)
    selector._begin(selector.policies["site"], "backup", "automatic-recovery", AUTH, 100)
    # Native mutation completed near its deadline. The two subsequent slow
    # controller passes are observations, not renewed mutation authority.
    selector._advance(AUTH, 124)
    deadline = selector.state["operation"]["deadline"]
    verification = selector.state["operation"]["attempt_deadline"]
    assert deadline == 129
    assert 153 <= verification <= 158
    selector._advance(AUTH, 137)
    assert selector.state["operation"]["successes"] == 1
    selector = TunnelController(config(), tmp_path / STATE_NAME, effects)
    assert selector.state["operation"]["attempt_deadline"] == verification
    selector._advance(AUTH, 149)
    assert selector.state["operation"]["phase"] == "succeeded"
    assert effects.calls == [("activate", "backup")]


def test_composed_native_recovery_with_slow_authority_and_controller_passes(native, monkeypatch):
    monkeypatch.setattr(tunnels, "monotonic", lambda: native.clock)

    def slow_authority(authority):
        native.clock += 1
        return native.valid and authority == AUTH

    native.effects.revalidate = slow_authority
    native.selector._advance(AUTH, native.clock)
    operation = native.selector.state["operation"]
    assert operation["phase"] == "verify"
    assert operation["deadline"] == 129
    verification_deadline = operation["attempt_deadline"]
    mutation_commands = list(native.events)
    for _ in range(2):
        native.clock += 12
        native.selector._advance(AUTH, native.clock)
    assert native.clock > 129
    assert native.clock < verification_deadline <= 158
    assert native.selector.state["operation"]["phase"] == "succeeded"
    assert native.sas["backup"] == native.original["backup"]
    assert native.sas["unrelated"] == native.original["unrelated"]
    for command in native.events[len(mutation_commands) :]:
        assert command[0] != "swanctl" and "delete" not in command and "-batch" not in command


def test_post_activation_window_expires_without_renewal(tmp_path):
    effects = Effects()
    selector = TunnelController(config(), tmp_path / STATE_NAME, effects)
    selector.bind(AUTH)
    selector._begin(selector.policies["site"], "backup", "automatic-recovery", AUTH, 100)
    selector._advance(AUTH, 124)
    verification = selector.state["operation"]["attempt_deadline"]
    effects.health["backup"] = Health.UNKNOWN
    selector._advance(AUTH, 140)
    assert selector.state["operation"]["attempt_deadline"] == verification
    selector._advance(AUTH, verification + 1)
    assert selector.state["operation"]["phase"] == "verify-restore"
    assert effects.calls[-1] == ("activate", "preferred")


def test_expired_terminal_deadline_allows_fresh_readonly_restoration_proof(native):
    # Arrange completed native policy independently of activate(), so this also
    # reproduces the old expired-read deadline bug without a teardown side effect.
    native.conf.write_text(json.dumps(native.effects._effective(AUTH), sort_keys=True))
    native.routes[0]["dev"] = "xfrm1"
    native.selector.state["operation"].update(phase="failed", result="native-recovery-retained")
    native.selector._save()
    native.clock = 500
    native.events.clear()
    assert native.effects.authority_restored(native.policies[0], "backup", AUTH)
    assert native.events == [
        (*ipsec_observation.command(), "--connections"),
        ("ip", "-j", "-4", "route", "show", "table", "main"),
    ]
    assert read_state(native.effects.state_dir / STATE_NAME) == native.selector.state


def test_foreign_persisted_authority_cannot_admit_preserving_promotion(native):
    native.selector.state["authority"]["epoch"] = "foreign"
    native.selector._save()
    with pytest.raises(RuntimeError, match="accepted operation"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.events == []


def test_malformed_fresh_inner_address_evidence_is_not_health(native):
    original = native.effects.command_runner

    def command(argv, timeout):
        if "address" in argv:
            return CompletedProcess(argv, 0, "not JSON", "")
        return original(argv, timeout)

    native.effects.command_runner = command
    with pytest.raises(RuntimeError, match="observation is unavailable"):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.sas == native.original


def test_readonly_proof_does_not_extend_expired_mutation_authority(native):
    native.effects.activate(native.policies[0], "backup", AUTH)
    native.clock = 500
    assert native.effects.authority_restored(native.policies[0], "backup", AUTH)
    native.events.clear()
    with pytest.raises(RuntimeError):
        native.effects.activate(native.policies[0], "backup", AUTH)
    assert native.events == []


@pytest.mark.parametrize("failure", ["configuration", "loaded-names", "routes", "authority"])
def test_fresh_retry_observation_still_requires_exact_restoration(native, failure):
    native.effects.activate(native.policies[0], "backup", AUTH)
    native.selector.state["operation"].update(phase="failed", result="native-recovery-retained")
    native.selector._save()
    native.clock = 500
    if failure == "configuration":
        native.conf.write_text("foreign configuration")
    elif failure == "loaded-names":
        native.loaded.remove("preferred")
    elif failure == "routes":
        native.routes[0]["dev"] = "xfrm0"
    else:
        native.valid = False
    native.events.clear()
    assert not native.effects.authority_restored(native.policies[0], "backup", AUTH)
    assert all(
        call == (*ipsec_observation.command(), "--connections") or "show" in call
        for call in native.events
    )


def recovery_loop(native, monkeypatch):
    """Drive the composed controller from detected loss, not a pre-admitted operation.

    Configure the common one-active, one-backup Static topology: with no other
    alternative, a backup that is never retried leaves nothing to recover to.
    The model has no FRR, so the unrelated BGP connection cannot be observed by
    a full controller pass. Its SA, like the third path's, stays in the native
    model and must remain untouched. Admission uses the native SA/kernel path
    proof; the configuration-inventory part of the inner-peer probe is outside
    this model and is covered elsewhere.
    """
    native.cfg["connections"] = [c for c in native.cfg["connections"] if c["name"] == "site"]
    site = native.cfg["connections"][0]
    site["tunnels"] = [t for t in site["tunnels"] if t["name"] in {"preferred", "backup"}]
    state = native.conf.parent / STATE_NAME
    state.unlink()
    monkeypatch.setattr(tunnels, "monotonic", lambda: native.clock)
    selector = TunnelController(
        native.cfg,
        state,
        native.effects,
        backup_probe=lambda policy, target, authority: native.effects.observe(
            policy, target, selected=False
        ),
        static_recovery_observations={},
        monotonic=lambda: native.clock,
    )
    selector.bind(AUTH)
    # Apply installed the normal policy before the remote failed.
    native.effects._write_swan_config(native.effects._effective(AUTH))
    return selector


@pytest.mark.parametrize(
    "command",
    [
        # Suspension scope proof of the dead path, before any mutation.
        ("ip", "xfrm", "state", "list", "nokeys"),
        # Promotion's recheck that the selected path is still DOWN.
        ("ip", "-j", "-4", "route", "show", "table", "main"),
    ],
    ids=["kernel-state-read", "route-read"],
)
def test_transient_read_failure_during_promotion_still_recovers_to_backup(
    native, monkeypatch, command
):
    """Regression: one stalled read must not disable recovery for the loss window.

    The read fails before any native mutation, so nothing needs rolling back.
    The selected path stays dead and the backup stays healthy throughout, so
    the controller must collect the evidence again and complete the promotion.
    """
    selector = recovery_loop(native, monkeypatch)
    activate = native.effects.activate

    def fail_first_promotion_read(policy, target, authority):
        if target == "backup" and native.faults == 0:
            native.fail_once = command
        return activate(policy, target, authority)

    native.effects.activate = fail_first_promotion_read
    for _ in range(180):
        native.clock += 1
        selector.tick(AUTH, native.clock)
    operation = selector.state["operation"]
    assert native.faults == 1
    assert selector.state["selected"] == {"site": "backup"}, (
        "backup was never promoted after one transient read failure; "
        f"last operation ended {operation['phase']}/{operation['result']}"
    )
    assert operation["result"] == "verified"
    assert native.routes == [{"dst": "10.1.0.0/16", "dev": "xfrm1"}]
    assert native.sas["backup"] == native.original["backup"]
    assert native.sas["unrelated"] == native.original["unrelated"]


def test_kernel_ownership_ambiguity_never_promotes_or_mutates(native, monkeypatch):
    """Guard for the fix: real scope ambiguity stays fail-closed, unlike a stalled read."""
    selector = recovery_loop(native, monkeypatch)
    preferred = native.paths["preferred"]
    native.states.append(
        {
            "src": preferred.outer_pair[1],
            "dst": "198.51.100.10",
            "spi": hex(0x4242),
            "proto": "esp",
            "mode": "tunnel",
            "if_id": preferred.if_id,
            "reqid": preferred.if_id,
            # A marked SA on the managed interface has unprovable ownership.
            "mark": "0x1",
        }
    )
    for _ in range(300):
        native.clock += 1
        selector.tick(AUTH, native.clock)
    assert selector.state["selected"] == {}
    assert selector.state["operation"]["phase"] == "failed"
    assert 1 <= selector.state["sequence"] <= 3
    assert native.routes == [{"dst": "10.1.0.0/16", "dev": "xfrm0"}]
    assert not any(
        call[:2] in {("swanctl", "--load-all"), ("ip", "-batch")} for call in native.events
    )
    assert native.sas == native.original
