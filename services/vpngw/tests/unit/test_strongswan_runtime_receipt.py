from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_vpngw.agent import strongswan_renderer as swan


@pytest.fixture
def runtime(tmp_path, monkeypatch, sample_config):
    root = tmp_path / "etc"
    directory = root / "strongswan.d" / "charon"
    directory.mkdir(parents=True)
    (root / "strongswan.conf").write_text(
        "charon {\n plugins {\n include strongswan.d/charon/*.conf\n }\n}\n"
        "include strongswan.d/*.conf\n"
    )
    receipt = tmp_path / "run" / "strongswan-runtime.json"
    monkeypatch.setattr(swan, "STRONGSWAN_CONF_DIR", directory)
    monkeypatch.setattr(swan, "SWANCTL_CONF", root / "swanctl.conf")
    monkeypatch.setattr(swan, "NETPLAN_DIR", root / "netplan")
    monkeypatch.setattr(swan, "RUNTIME_RECEIPT", receipt)
    monkeypatch.setattr(swan, "_daemon_uses_default_config", lambda pid, path: True)
    monkeypatch.setattr(swan, "_wait_for_vici_socket", lambda: True)
    monkeypatch.setattr(swan.time, "sleep", lambda delay: None)
    state = dict(pid=100, invocation=1, load_failure=False, on_load=None)
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        stdout = ""
        if argv[:2] == ["systemctl", "show"]:
            stdout = (
                f"MainPID={state['pid']}\nInvocationID={state['invocation']:032x}\n"
                "ActiveState=active\n"
            )
        elif argv[:2] == ["systemctl", "restart"]:
            state["pid"] += 1
            state["invocation"] += 1
            if state.get("on_restart"):
                state["on_restart"]()
        elif argv[0] == "swanctl":
            if state["on_load"]:
                state["on_load"]()
            if state["load_failure"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="unavailable")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(swan, "run", command)
    monkeypatch.setattr(swan, "bounded", command)
    return sample_config, receipt, root, state, calls


def test_passive_materialization_reuses_only_recorded_startup_runtime(runtime):
    cfg, receipt, _, state, calls = runtime
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    assert receipt.exists()
    pid = state["pid"]
    for _ in range(2):
        renderer.render_and_apply({**cfg, "_ha_static_passive": True})
    assert state["pid"] == pid
    assert sum(argv == ["netplan", "apply"] for argv in calls) == 1
    assert sum(argv[:2] == ["systemctl", "restart"] for argv in calls) == 1
    renderer.render_and_apply(cfg)
    assert state["pid"] != pid  # Ordinary materialization retains restart behavior.


@pytest.mark.parametrize(
    "drift",
    [
        "missing",
        "content",
        "mode",
        "symlink",
        "pid",
        "invocation",
        "runtime",
        "include",
        "config_symlink",
    ],
)
def test_passive_unknown_or_changed_runtime_cannot_skip_restart(runtime, drift):
    cfg, receipt, root, state, calls = runtime
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    if drift == "missing":
        receipt.unlink()
    elif drift == "content":
        receipt.write_text("{}")
    elif drift == "mode":
        receipt.chmod(0o666)
    elif drift == "symlink":
        target = receipt.with_name("other")
        receipt.rename(target)
        receipt.symlink_to(target)
    elif drift in {"pid", "invocation"}:
        state[drift] += 1
    elif drift == "runtime":
        (root / "strongswan.d" / "nebius-vpngw.conf").write_text("charon { reqid_base = 1 }")
    elif drift == "include":
        (root / "strongswan.conf").write_text("include /external/runtime.conf\n")
    elif drift == "config_symlink":
        path = root / "strongswan.d" / "foreign.conf"
        path.symlink_to(root / "strongswan.conf")
    calls.clear()
    renderer.render_and_apply({**cfg, "_ha_static_passive": True})
    assert ["systemctl", "restart", "strongswan"] in calls


@pytest.mark.parametrize("change", ["load_failure", "daemon", "configuration"])
def test_failed_or_racing_passive_reload_never_reports_materialized(runtime, change):
    cfg, receipt, root, state, calls = runtime
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    original = receipt.read_bytes()
    if change == "load_failure":
        state["load_failure"] = True
    elif change == "daemon":
        state["on_load"] = lambda: state.__setitem__("invocation", state["invocation"] + 1)
    else:
        state["on_load"] = lambda: (root / "strongswan.d" / "changed.conf").write_text("charon {}")
    with pytest.raises(RuntimeError, match="passive reload"):
        renderer.render_and_apply({**cfg, "_ha_static_passive": True})
    assert receipt.read_bytes() == original


def test_failed_initial_load_does_not_publish_startup_receipt(runtime):
    cfg, receipt, _, state, _ = runtime
    state["load_failure"] = True
    swan.StrongSwanRenderer().render_and_apply(cfg)
    assert not receipt.exists()


@pytest.mark.parametrize("predecessor", ["missing", "stale", "failed-reuse"])
@pytest.mark.parametrize("change", ["daemon", "configuration", "proof-lost"])
def test_restart_after_unproven_runtime_rejects_racing_load(
    runtime, monkeypatch, predecessor, change
):
    cfg, receipt, root, state, calls = runtime
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    original = receipt.read_bytes()
    sequence = 0

    def race():
        nonlocal sequence
        sequence += 1
        if change == "daemon":
            state["invocation"] += 1
        elif change == "configuration":
            (root / "strongswan.d" / "changed.conf").write_text(f"# change {sequence}\n")
        else:
            monkeypatch.setattr(swan, "_daemon_uses_default_config", lambda *_: False)

    state["on_load"] = race
    if predecessor == "failed-reuse":
        with pytest.raises(RuntimeError, match="passive reload"):
            renderer.render_and_apply({**cfg, "_ha_static_passive": True})
        assert receipt.read_bytes() == original
        # A later attempt can observe the daemon again, but the old receipt
        # cannot authorize reuse after this interrupted observation.
        if change == "proof-lost":
            monkeypatch.setattr(swan, "_daemon_uses_default_config", lambda *_: True)
            state["invocation"] += 1
    elif predecessor == "missing":
        receipt.unlink()
    else:
        receipt.write_text("{}")
    before = receipt.read_bytes() if receipt.exists() else None
    calls.clear()
    with pytest.raises(RuntimeError, match="changed during policy load"):
        renderer.render_and_apply({**cfg, "_ha_static_passive": True})
    assert ["systemctl", "restart", "strongswan"] in calls
    assert (receipt.read_bytes() if receipt.exists() else None) == before


def test_restart_rejects_configuration_changed_before_load(runtime):
    cfg, receipt, root, state, _ = runtime
    state["on_restart"] = lambda: (root / "strongswan.d" / "changed.conf").write_text(
        "# changed while restarting\n"
    )
    with pytest.raises(RuntimeError, match="changed during policy load"):
        swan.StrongSwanRenderer().render_and_apply(cfg)
    assert not receipt.exists()


def test_composed_mixed_passive_materialization_preserves_bgp_session(runtime, tmp_path):
    import copy
    import re

    from nebius_vpngw import ipsec_observation
    from nebius_vpngw.agent.vm_ha.models import DigestSet
    from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
    from nebius_vpngw.agent.vm_ha_controller import ActionKind, ControllerAction

    cfg, _, _, state, calls = runtime
    bgp = copy.deepcopy(cfg["connections"][0])
    cfg["connections"][0]["tunnels"][0]["name"] = "static-tunnel"
    bgp.update(name="bgp", routing_mode="bgp", remote_prefixes=["10.50.0.0/16"])
    bgp["tunnels"][0]["name"] = "bgp-tunnel"
    bgp["tunnels"][0]["remote_public_ip"] = "192.0.2.20"
    cfg["connections"].append(bgp)
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    pid = state["pid"]
    sas = {
        "bgp-tunnel": {
            "uniqueid": "42",
            "state": "ESTABLISHED",
            "child-sas": {
                "bgp-tunnel-99": {
                    "name": "bgp-tunnel",
                    "uniqueid": "99",
                    "state": "INSTALLED",
                    "protocol": "ESP",
                    "if-id-in": "65",
                    "if-id-out": "65",
                }
            },
        },
        "static-tunnel": {"uniqueid": "7", "state": "ESTABLISHED"},
    }
    original_bgp = copy.deepcopy(sas["bgp-tunnel"])
    loaded = {"bgp-tunnel", "static-tunnel"}

    def load():
        loaded.clear()
        loaded.update(
            re.findall(
                r"^  ([\w-]+) \{$", swan.SWANCTL_CONF.read_text().partition("secrets {")[0], re.M
            )
        )

    state["on_load"] = load
    state["on_restart"] = sas.clear
    forwarding = ["1"]
    runtime_calls = []

    def runner(argv, timeout):
        runtime_calls.append(argv)
        if argv[-1] == "net.ipv4.ip_forward":
            return CommandResult(0, forwarding[0])
        if argv[-1].startswith("net.ipv4.ip_forward="):
            forwarding[0] = argv[-1].split("=")[1]
        elif "--configuration" in argv:
            return CommandResult(
                0, ipsec_observation.ConfigurationInventory(frozenset(loaded)).json()
            )
        elif "nebius_vpngw.ipsec_observation" in argv:
            return CommandResult(
                0, ipsec_observation.Inventory.from_events([{k: v} for k, v in sas.items()]).json()
            )
        elif "--terminate" in argv:
            identity = argv[argv.index("--ike-id") + 1]
            for name, sa in list(sas.items()):
                if sa["uniqueid"] == identity:
                    assert name not in loaded  # Initiation/responders removed before termination.
                    del sas[name]
        elif argv[1:3] == ("reload-or-restart", "nebius-vpngw-agent"):
            renderer.render_and_apply({**cfg, "_ha_static_passive": True})
        return CommandResult(0)

    dataplane = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        tunnel_cold_passive=True,
        static_tunnel_names=("static-tunnel",),
    )
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive",
        "boot-a",
        "node-a",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    for _ in range(2):
        dataplane.enter_passive(action)
        assert state["pid"] == pid and forwarding[0] == "0"
        assert sas == {"bgp-tunnel": original_bgp}
        assert loaded == {"bgp-tunnel"}
    assert not any("--load-conns" in argv for argv in runtime_calls)


@pytest.mark.parametrize("unavailable", [False, True])
def test_runtime_probe_does_not_enter_ordinary_mutation_admission(
    runtime, monkeypatch, unavailable
):
    import time

    from nebius_vpngw.agent import local_commands

    cfg, _, _, state, calls = runtime
    effect_runner = swan.run
    services = []
    operation = SimpleNamespace(
        before_netplan=lambda: None,
        service=lambda action, name, **kwargs: (
            services.append((action, name)),
            effect_runner(["systemctl", action, name.removesuffix(".service")]),
        ),
    )
    monkeypatch.setattr(swan, "run", local_commands.run)
    monkeypatch.setattr(local_commands, "_bounded_run", effect_runner)
    if unavailable:
        monkeypatch.setattr(
            swan,
            "bounded",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="unavailable"),
        )
    budget = local_commands.CommandBudget(time.monotonic() + 30, operation=operation)
    token = local_commands.CURRENT.set(budget)
    try:
        swan.StrongSwanRenderer().render_and_apply(cfg)
        budget.require_success()
    finally:
        local_commands.CURRENT.reset(token)
    assert services == [("restart", "strongswan.service")]
    assert not budget.failures


def test_changed_network_configuration_keeps_restart_and_netplan(runtime):
    cfg, receipt, root, state, calls = runtime
    renderer = swan.StrongSwanRenderer()
    renderer.render_and_apply(cfg)
    calls.clear()
    (root / "netplan" / "99-nebius-vpngw.yaml").write_text("# changed\n")
    renderer.render_and_apply({**cfg, "_ha_static_passive": True})
    assert ["netplan", "apply"] in calls
    assert ["systemctl", "restart", "strongswan"] in calls


@pytest.mark.parametrize("unsafe", ["writable", "symlink"])
def test_unsafe_runtime_receipt_directory_never_receives_evidence(runtime, unsafe):
    cfg, receipt, root, state, calls = runtime
    receipt.parent.mkdir()
    if unsafe == "writable":
        receipt.parent.chmod(0o777)
    else:
        receipt.parent.rmdir()
        receipt.parent.symlink_to(root, target_is_directory=True)
    swan.StrongSwanRenderer().render_and_apply(cfg)
    assert not receipt.exists()
