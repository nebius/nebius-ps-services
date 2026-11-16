"""Static route count must not multiply full authority collection latency."""

from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from test_ha_tunnel_selection import AUTH, config

from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects
from nebius_vpngw.agent.vm_ha.tunnels import TunnelController


@pytest.fixture
def restoration(tmp_path):
    cfg = config()
    cfg["connections"][0]["remote_prefixes"] = [f"10.1.{index}.0/24" for index in range(12)]
    clock = [100.0]
    calls = {"authority": 0, "suspend": 0, "load": 0, "batches": []}
    valid = [True]
    restored = [False]

    def revalidate(authority):
        calls["authority"] += 1
        clock[0] += 1.9
        return valid[0] and authority == AUTH

    def command(argv, timeout):
        assert timeout > 0
        if argv[:2] == ("ip", "-batch"):
            commands = Path(argv[2]).read_text().splitlines()
        else:
            assert argv[:3] == ("ip", "route", "replace")
            commands = [" ".join(argv[1:])]
        calls["batches"].append(commands)
        clock[0] += len(commands) * 0.001
        restored[0] = True
        return CompletedProcess(argv, 0, "", "")

    effects = NativeTunnelEffects(
        cfg,
        tmp_path,
        revalidate,
        active_revalidate=revalidate,
        routing_lock=tmp_path / "routing.lock",
        clock=lambda: clock[0],
        command_runner=command,
    )
    selector = TunnelController(cfg, tmp_path / "tunnel-selection.json", effects)
    selector.bind(AUTH)
    selector._episode(clock[0], AUTH)
    selector._begin(selector.policies["site"], "backup", "automatic", AUTH, clock[0])
    selector.state["operation"]["phase"] = "restore"
    selector.state["selected"]["site"] = "preferred"
    selector._save()

    def suspend(*args):
        calls["suspend"] += 1

    def load(*args):
        calls["load"] += 1

    effects.suspend = suspend
    effects._swan_config = load
    effects.authority_restored = lambda *args: restored[0]
    return effects, selector, clock, calls, valid, restored


@pytest.mark.parametrize("prefix_count", [1, 12, 100])
def test_restore_batches_routes_with_slow_full_authority_snapshots(restoration, prefix_count):
    effects, selector, clock, calls, _, _ = restoration
    policy = selector.policies["site"]
    policy = replace(
        policy, destinations=frozenset(f"10.1.{index}.0/24" for index in range(prefix_count))
    )
    start = clock[0]
    effects.activate(policy, "preferred", AUTH)
    assert len(calls["batches"]) == 1
    assert len(calls["batches"][0]) == prefix_count
    assert calls["authority"] == 3
    assert clock[0] - start < 10
    assert calls["suspend"] == calls["load"] == 1
    assert not list(effects.state_dir.glob("tmp*"))


def test_completed_restore_replay_retains_native_ike_recovery(restoration):
    effects, selector, _, calls, _, _ = restoration
    policy = selector.policies["site"]
    effects.activate(policy, "preferred", AUTH)
    effects.activate(policy, "preferred", AUTH)
    assert calls["suspend"] == calls["load"] == len(calls["batches"]) == 1


def test_changed_authority_prevents_route_batch(restoration):
    effects, selector, _, calls, valid, _ = restoration

    def load(*args):
        valid[0] = False

    effects._swan_config = load
    with pytest.raises(RuntimeError, match="route authority changed"):
        effects.activate(selector.policies["site"], "preferred", AUTH)
    assert calls["batches"] == []
    assert not list(effects.state_dir.glob("tmp*"))


def test_authority_collection_cannot_extend_expired_batch_budget(restoration):
    effects, selector, clock, calls, _, _ = restoration

    def load(*args):
        clock[0] += 10

    effects._swan_config = load
    with pytest.raises(RuntimeError, match="effect deadline exhausted"):
        effects.activate(selector.policies["site"], "preferred", AUTH)
    assert calls["batches"] == []


def test_failed_native_batch_does_not_report_restoration_complete(restoration):
    effects, selector, _, _, _, restored = restoration
    effects.command_runner = lambda argv, timeout: CompletedProcess(argv, 2, "", "route failure")
    selector._advance(AUTH, 101)
    assert selector.state["operation"]["phase"] == "restore"
    assert not restored[0]


def test_restoration_advances_to_verification_after_all_routes(restoration):
    _, selector, _, calls, _, _ = restoration
    selector._advance(AUTH, 101)
    assert selector.state["operation"]["phase"] == "verify-restore"
    assert len(calls["batches"][0]) == 12
