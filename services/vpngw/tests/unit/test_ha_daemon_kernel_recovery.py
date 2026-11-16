from __future__ import annotations

from types import SimpleNamespace

import pytest
import vici.exception
import yaml
from tests.unit.test_ha_tunnel_selection import config

from nebius_vpngw import ipsec_observation
from nebius_vpngw.agent.vm_ha.daemon_recovery import reconcile_lost_daemon_kernel
from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "partial",
        "credentials-only",
        "surviving-ike",
        "unknown",
        "native-returned",
        "authority-lost",
        "deadline",
        "lost-success",
        "failed-delete",
        "foreign-kernel",
        "incomplete-readback",
        "vici-session",
        "vici-command",
        "vici-event",
        "vici-deserialization",
    ],
)
def test_reload_cleanup_requires_complete_absence_and_preserves_surviving_paths(
    tmp_path, monkeypatch, mode, case
):
    expected = ipsec_observation.ConfigurationInventory(
        frozenset({"preferred", "backup"}), frozenset({"ike-psk-1"})
    )
    configured = {"preferred", "backup"} if case == "credentials-only" else set()
    if case == "partial":
        configured.add("preferred")
    native_reads = 0
    calls = []
    pending = {name: ("exact-delete", name) for name in expected.connections}
    timeouts = []
    now = [10.0]

    def configuration(timeout):
        nonlocal native_reads
        timeouts.append(timeout)
        native_reads += 1
        if calls and case.startswith("vici-"):
            error = {
                "vici-session": vici.exception.SessionException,
                "vici-command": vici.exception.CommandException,
                "vici-event": vici.exception.EventUnknownException,
                "vici-deserialization": vici.exception.DeserializationException,
            }[case]
            raise error("native inventory disconnected")
        if case == "unknown":
            raise ipsec_observation.IncompleteInventory("incomplete")
        names = set(configured)
        if case == "native-returned" and native_reads > 1:
            names.add("preferred")
        return ipsec_observation.ConfigurationInventory(frozenset(names), frozenset())

    def sessions(timeout):
        timeouts.append(timeout)
        return ipsec_observation.Inventory.from_events(
            [{"preferred": {"uniqueid": "1", "state": "ESTABLISHED", "child-sas": {}}}]
            if case == "surviving-ike"
            else []
        )

    def plan(self, policy):
        if case == "foreign-kernel" or case == "incomplete-readback" and calls:
            raise RuntimeError("scope unproven")
        return tuple(pending[path.name] for path in policy.paths if path.name in pending)

    def runner(argv, timeout):
        timeouts.append(timeout)
        command = tuple(argv)
        assert command[0] == "exact-delete"
        calls.append(command)
        if case == "failed-delete":
            return SimpleNamespace(returncode=1, stdout="")
        del pending[command[1]]
        if case == "deadline":
            now[0] = 14.0
        return SimpleNamespace(returncode=int(case == "lost-success"), stdout="")

    monkeypatch.setattr(ipsec_observation, "loaded_configuration", configuration)
    monkeypatch.setattr(ipsec_observation, "snapshot", sessions)
    monkeypatch.setattr(NativeTunnelEffects, "_kernel_deletions", plan)
    args = dict(
        expected=expected,
        permitted=lambda: case != "authority-lost" or not calls,
        runner=runner,
        deadline=14.0,
        clock=lambda: now[0],
    )
    failing = {
        "unknown",
        "native-returned",
        "authority-lost",
        "deadline",
        "failed-delete",
        "foreign-kernel",
        "incomplete-readback",
        "vici-session",
        "vici-command",
        "vici-event",
        "vici-deserialization",
    }
    if case in failing:
        with pytest.raises(RuntimeError):
            reconcile_lost_daemon_kernel(config(mode), tmp_path, **args)
        assert len(calls) <= 1
    else:
        reconcile_lost_daemon_kernel(config(mode), tmp_path, **args)
        preserved = (
            {"preferred", "backup"}
            if case == "credentials-only"
            else {"preferred"}
            if case in {"partial", "surviving-ike"}
            else set()
        )
        assert set(pending) == preserved
    assert all(0 < value <= 3 for value in timeouts)
    assert all(command[1] in expected.connections for command in calls)


@pytest.mark.parametrize("scope_count", [1, 2])
def test_kernel_policies_are_revoked_before_sas_can_trigger_a_larval_acquire(
    tmp_path, monkeypatch, scope_count
):
    import copy

    cfg = config("bgp")
    states, policies, pending = [], [], {}
    for index in range(scope_count):
        if index:
            other = copy.deepcopy(cfg["connections"][0])
            other["name"] = "other-site"
            other["remote_prefixes"] = ["10.2.0.0/16"]
            for path in other["tunnels"]:
                path["name"] += "-other"
            cfg["connections"].append(other)
        name = cfg["connections"][index]["name"]
        state = ("ip", "xfrm", "state", "delete", "spi", hex(0x1234 + index))
        policy = (
            "ip",
            "xfrm",
            "policy",
            "delete",
            "index",
            str(17 + index * 8),
            "dir",
            "out",
            "if_id",
            str(100 + index * 2),
        )
        states.append(state)
        policies.append(policy)
        pending[name] = [state, policy]
    deleted = []
    larval = [False]

    def plan(self, scope):
        if larval[0]:
            raise ValueError("kernel SA lacks an exact SPI")
        return tuple(pending[scope.name])

    def runner(argv, timeout):
        command = tuple(argv)
        assert timeout > 0
        deleted.append(command)
        if command in states and any(
            policy in values for values in pending.values() for policy in policies
        ):
            larval[0] = True  # Real kernel behavior while BGP traffic hits an orphan trap.
        next(values for values in pending.values() if command in values).remove(command)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(NativeTunnelEffects, "_kernel_deletions", plan)
    monkeypatch.setattr(
        ipsec_observation,
        "loaded_configuration",
        lambda timeout: ipsec_observation.ConfigurationInventory(),
    )
    monkeypatch.setattr(
        ipsec_observation, "snapshot", lambda timeout: ipsec_observation.Inventory.from_events([])
    )
    reconcile_lost_daemon_kernel(
        cfg,
        tmp_path,
        expected=ipsec_observation.ConfigurationInventory(
            frozenset({"preferred", "preferred-other"} if scope_count == 2 else {"preferred"})
        ),
        permitted=lambda: True,
        runner=runner,
        deadline=14.0,
        clock=lambda: 10.0,
    )
    assert deleted == policies + states
    assert not larval[0] and not any(pending.values())


def test_unknown_passive_fence_uses_at_most_the_one_second_reserve(tmp_path):
    from dataclasses import replace

    from tests.unit.test_vm_ha_runtime_ports import _repair_action

    from nebius_vpngw.agent.vm_ha.models import RepairDisposition
    from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
    from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode

    now, forwarding, calls = [14.0], ["unknown"], []

    def runner(argv, timeout):
        calls.append((tuple(argv), timeout))
        now[0] += timeout
        if "net.ipv4.ip_forward=0" in argv:
            forwarding[0] = "0"
        return CommandResult(0, forwarding[0])

    plane = SystemDataPlaneRuntime(
        state_path=tmp_path / "state.json",
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        monotonic_clock=lambda: now[0],
    )
    action = replace(
        _repair_action(deadline=15, reasons=("daemon-configuration-missing:strongswan",)),
        repair_mode=DataPlaneMode.PASSIVE,
    )
    plane._write_state(action, action.repair_mode)
    plane._write_guard(action, action.repair_mode)
    assert plane.repair_local(action) is RepairDisposition.ATTEMPTED
    assert forwarding[0] == "0" and now[0] < action.repair_deadline_at
    assert [timeout for _, timeout in calls] == [0.1, 0.75, 0.1]


@pytest.mark.parametrize("change", ["new", "reordered", "returned", "expiry"])
def test_cleanup_revalidates_a_monotonically_shrinking_order(tmp_path, monkeypatch, change):
    original = [("exact-delete", str(index)) for index in range(3)]
    pending, deleted = list(original), []

    def runner(argv, timeout):
        command = tuple(argv)
        deleted.append(command)
        pending.remove(command)
        if len(deleted) == 1:
            if change == "new":
                pending.insert(0, ("exact-delete", "new"))
            elif change == "returned":
                pending.insert(0, original[0])
            elif change == "reordered":
                pending.reverse()
            else:
                pending.pop()
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(
        NativeTunnelEffects, "_kernel_deletions", lambda self, scope: tuple(pending)
    )
    monkeypatch.setattr(
        ipsec_observation,
        "loaded_configuration",
        lambda timeout: ipsec_observation.ConfigurationInventory(),
    )
    monkeypatch.setattr(
        ipsec_observation, "snapshot", lambda timeout: ipsec_observation.Inventory.from_events([])
    )
    args = dict(
        expected=ipsec_observation.ConfigurationInventory(frozenset({"preferred"})),
        permitted=lambda: True,
        runner=runner,
        deadline=14.0,
        clock=lambda: 10.0,
    )
    if change == "expiry":
        reconcile_lost_daemon_kernel(config("bgp"), tmp_path, **args)
        assert deleted == original[:2] and not pending
    else:
        with pytest.raises(RuntimeError, match="plan changed"):
            reconcile_lost_daemon_kernel(config("bgp"), tmp_path, **args)
        assert deleted == original[:1]


@pytest.mark.parametrize("mode", ["active", "passive"])
@pytest.mark.parametrize(
    "failure", [None, "guard", "apply-lock", "cloud-after", "native-after", "inherited-deadline"]
)
def test_real_reload_wiring_consumes_partial_cleanup_and_never_loads_without_proof(
    tmp_path, monkeypatch, mode, failure
):
    from dataclasses import replace

    from tests.unit.test_vm_ha_runtime_ports import _repair_action

    from nebius_vpngw.agent import strongswan_renderer
    from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget
    from nebius_vpngw.agent.vm_ha.models import RepairDisposition
    from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
    from nebius_vpngw.agent.vm_ha.tunnel_runtime import ControllerTunnelRuntime
    from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode

    cfg = config("bgp")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    swan = tmp_path / "swanctl.conf"
    monkeypatch.setattr(strongswan_renderer, "SWANCTL_CONF", swan)
    rendered = {}
    effective = {**cfg, "_ha_static_passive": True} if mode == "passive" else cfg
    strongswan_renderer.StrongSwanRenderer().render_and_apply(effective, rendered_files=rendered)
    swan.write_text(rendered[swan])
    swan.chmod(0o600)
    now, forwarding = [10.0], ["1" if mode == "active" else "0"]
    calls, cloud_reads = [], []
    pending = [("exact-delete", "preferred"), ("exact-delete", "backup")]

    def runner(argv, timeout):
        call = tuple(argv)
        assert 0 < timeout <= 5
        calls.append(call)
        if call[1:] == ("-n", "net.ipv4.ip_forward"):
            return CommandResult(0, forwarding[0])
        if call[1:] == ("-w", "net.ipv4.ip_forward=0"):
            forwarding[0] = "0"
            return CommandResult(0, "")
        if call == (*ipsec_observation.command(), "--configuration"):
            return CommandResult(0, ipsec_observation.ConfigurationInventory().json())
        if call[0] == "exact-delete":
            pending.remove(call)
            if failure == "inherited-deadline":
                now[0] = 11.0
            return CommandResult(0, "")
        if call[1:] == ("--load-all", "--noprompt"):
            return CommandResult(0, "")
        if call[1:3] == ("show", "strongswan"):
            return CommandResult(0, "active")
        raise AssertionError(call)

    def native_configuration(timeout):
        if failure == "native-after" and len(pending) < 2:
            raise vici.exception.SessionException("lost inventory")
        return ipsec_observation.ConfigurationInventory()

    monkeypatch.setattr(ipsec_observation, "loaded_configuration", native_configuration)
    monkeypatch.setattr(
        ipsec_observation, "snapshot", lambda timeout: ipsec_observation.Inventory.from_events([])
    )
    monkeypatch.setattr(
        NativeTunnelEffects,
        "_kernel_deletions",
        lambda self, scope: tuple(pending),
    )
    plane = SystemDataPlaneRuntime(
        state_path=state_dir / "data-plane.json",
        guard_path=state_dir / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        runner=runner,
        monotonic_clock=lambda: now[0],
        routing_lock_path=tmp_path / "writer.lock",
    )
    action = replace(
        _repair_action(deadline=15, reasons=("daemon-configuration-missing:strongswan",)),
        repair_mode=DataPlaneMode(mode),
    )
    plane._write_guard(action, action.repair_mode)
    plane._write_state(action, action.repair_mode)
    if mode == "active":
        from nebius_vpngw.agent.main import DefaultVMHAControllerRuntime

        # The service loop runs after activation and between component failures.
        # Exercise it before repair so the fixture does not hide guard truncation.
        outer = DefaultVMHAControllerRuntime(
            SimpleNamespace(
                step=lambda: {"promotion_ready": True},
                snapshots=SimpleNamespace(clock=lambda: now[0]),
                controller=SimpleNamespace(effects=SimpleNamespace()),
            ),
            SimpleNamespace(local=SimpleNamespace(role=SimpleNamespace(value="active"))),
            boot_id=action.boot_id,
            sequence_path=state_dir / "heartbeat-sequence.json",
            guard_path=plane.guard_path,
        )
        outer.started_guarded = True
        monkeypatch.setattr(outer, "_start_peer_listener", lambda: None)
        outer.publisher = SimpleNamespace(
            start=lambda: None, check=lambda: None, progress=lambda phase: None
        )
        outer.step()
    if failure == "guard":
        import json

        guard = json.loads(plane.guard_path.read_text())
        guard["node_id"] = "foreign"
        plane.guard_path.write_text(json.dumps(guard))
    if failure == "apply-lock":
        (state_dir / "apply.lock").write_text("inhibited")

    def cloud(current):
        assert current == action
        cloud_reads.append(current)
        if failure == "cloud-after":
            raise RuntimeError("current cloud authority unavailable")

    ControllerTunnelRuntime(
        config_path,
        state_dir,
        None,
        SimpleNamespace(data_plane=plane, routes=SimpleNamespace(), require_repair_authority=cloud),
    )
    token = CURRENT.set(CommandBudget(11.0 if failure == "inherited-deadline" else 14.0))
    try:
        assert plane.repair_local(action) is RepairDisposition.ATTEMPTED
    finally:
        CURRENT.reset(token)
    loaded = any("--load-all" in call for call in calls)
    assert loaded is (failure is None)
    assert forwarding[0] == ("1" if mode == "active" and failure is None else "0")
    assert len(pending) == (
        2
        if failure in {"guard", "apply-lock"}
        else 1
        if failure in {"native-after", "inherited-deadline"}
        else 0
    )
    assert bool(cloud_reads) is (failure in {None, "cloud-after"})
