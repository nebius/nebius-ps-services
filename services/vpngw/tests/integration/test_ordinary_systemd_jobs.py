"""Actual PID1/networkd regressions; run only through tests/systemd/run.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from nebius_vpngw import ordinary_operations as ops

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable systemd fixture",
    ),
]


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout


def eventually(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("fixture did not converge")


@pytest.fixture(scope="module", autouse=True)
def network():
    if os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1":
        return
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    link = json.loads(command("ip", "-j", "-4", "addr", "show", "eth0"))[0]
    addresses = [
        f"{a['local']}/{a['prefixlen']}" for a in link["addr_info"] if a["scope"] == "global"
    ]
    gateway = json.loads(command("ip", "-j", "route", "show", "default"))[0]["gateway"]
    cfg = {
        "network": {
            "version": 2,
            "renderer": "networkd",
            "ethernets": {
                "eth0": {"addresses": addresses, "routes": [{"to": "default", "via": gateway}]}
            },
        }
    }
    Path("/etc/netplan").mkdir(exist_ok=True)
    path = Path("/etc/netplan/50-cloud-init.yaml")
    path.write_text(yaml.safe_dump(cfg))
    path.chmod(0o600)
    # Remove Docker's externally configured route before networkd owns the
    # fixture. Otherwise duplicate proto-boot/static defaults contaminate the
    # initial snapshot and disappear on the next native apply.
    command("ip", "route", "flush", "default")
    command("netplan", "apply")
    manager = ops.Manager(time.monotonic() + 30)

    def configured():
        try:
            return bool(manager.network())
        except ops.OperationBlocked:
            return False

    eventually(configured)


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "JOURNAL", tmp_path / "ordinary/operation.json")
    monkeypatch.setattr(ops, "ROUTING_LOCK", tmp_path / "routing.lock")
    manager = ops.Manager(time.monotonic() + 30)
    unit = Path("/etc/systemd/system/frr.service")
    unit.write_text(
        "[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\nExecReload=/bin/true\n"
    )
    command("systemctl", "daemon-reload")
    yield manager
    command("systemctl", "stop", "frr.service")
    unit.unlink()
    command("systemctl", "daemon-reload")


def begin(manager):
    return ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
    )


@pytest.mark.parametrize("fault", [None, "wrong", "loser", "multipath"])
def test_shared_static_prefix_real_kernel_verification(
    ordinary_static_observer, monkeypatch, fault
):
    import uuid

    from nebius_vpngw.agent import ordinary, xfrm_manager
    from nebius_vpngw.tunnel_state import collect_tunnel_state

    cfg = {
        "gateway": {},
        "connections": [
            {
                "routing_mode": "static",
                "remote_prefixes": ["192.0.2.0/24"],
                "tunnels": [
                    {
                        "name": f"static-{i}",
                        "remote_public_ip": f"203.0.113.{10 + i}",
                        "ha_role": "active" if i == 0 else "passive",
                        "inner_local_ip": f"169.254.{10 + i}.1",
                        "inner_cidr": f"169.254.{10 + i}.0/30",
                    }
                    for i in range(2)
                ],
            }
        ],
    }
    state = ordinary_static_observer(cfg)
    namespace = "static-test-" + uuid.uuid4().hex[:12]
    command("ip", "netns", "add", namespace)

    def run(args, **kwargs):
        return subprocess.run(["ip", "netns", "exec", namespace, *args], timeout=20, **kwargs)

    def configure(*args):
        result = run(list(args), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result

    try:
        configure("ip", "link", "add", "eth0", "type", "dummy")
        configure("ip", "link", "set", "eth0", "mtu", "1500", "up")
        monkeypatch.setattr(xfrm_manager, "run", run)
        for endpoint in collect_tunnel_state(cfg, log=lambda _: None)[2]:
            name = endpoint["name"]
            configure(
                "ip",
                "link",
                "add",
                name,
                "type",
                "xfrm",
                "dev",
                "eth0",
                "if_id",
                str(endpoint["if_id"]),
            )
            configure("ip", "link", "set", name, "mtu", "1436", "up")
            configure("ip", "addr", "add", endpoint["local_inner_ip"] + "/30", "dev", name)
            xfrm_manager.XFRMManager()._add_static_routes(name, endpoint["remote_prefixes"])
        rows = json.loads(configure("ip", "-j", "route", "show", "192.0.2.0/24").stdout)
        assert len(rows) == 1 and rows[0]["dev"] == "xfrm1"
        if fault == "wrong":
            configure("ip", "route", "replace", "192.0.2.0/24", "dev", "xfrm0")
        elif fault == "loser":
            configure("ip", "route", "add", "192.0.2.0/24", "dev", "xfrm0", "metric", "50")
        elif fault == "multipath":
            configure(
                "ip",
                "route",
                "add",
                "192.0.2.0/24",
                "metric",
                "50",
                "nexthop",
                "dev",
                "xfrm0",
                "nexthop",
                "dev",
                "xfrm1",
            )
        state.ip_reader = lambda args: json.loads(
            run(args, check=True, capture_output=True, text=True).stdout
        )
        if fault:
            with pytest.raises(RuntimeError, match="ordinary local invariant"):
                ordinary.observe_local(cfg)
        else:
            assert len(ordinary.observe_local(cfg)) == 64
    finally:
        command("ip", "netns", "del", namespace)


def test_restart_wait_is_admitted_then_stopped_through_real_job(manager):
    name = "nebius-vpngw-agent.service"
    path = Path("/etc/systemd/system") / name
    previous = path.read_bytes() if path.exists() else None
    path.write_text("[Service]\nExecStart=/bin/false\nRestart=always\nRestartSec=30\n")
    command("systemctl", "daemon-reload")
    try:
        command("systemctl", "start", name)
        eventually(lambda: manager.unit(name).get("SubState") == "auto-restart")
        manager.preflight()
        assert not manager.quiet(ops.MANAGEMENT)
        with ops.mutation_lock():
            operation = begin(manager)
            operation.service("stop", name)
            assert manager.stopped(name)
            assert ops.Journal().read()["effects"][0]["state"] == "settled"
            operation.complete()
    finally:
        command("systemctl", "stop", name)
        if previous is None:
            path.unlink()
        else:
            path.write_bytes(previous)
        command("systemctl", "daemon-reload")


def test_missing_failure_handler_then_inactive_installed_handler(manager):
    handler = Path("/etc/systemd/system/vpngw-fixture-failure@.service")
    assert not handler.exists()
    unit = Path("/etc/systemd/system/frr.service")
    unit.write_text("[Unit]\nOnFailure=vpngw-fixture-failure@%n\n" + unit.read_text())
    command("systemctl", "daemon-reload")
    try:
        assert manager.environment()["missing_failure_units"] == [
            "vpngw-fixture-failure@frr.service"
        ]
        handler.write_text("[Service]\nType=oneshot\nExecStart=/bin/true\n")
        command("systemctl", "daemon-reload")
        with pytest.raises(ops.OperationBlocked, match="service_scope_unknown"):
            manager.environment()
        assert manager.unit("vpngw-fixture-failure@frr.service")["ActiveState"] == "inactive"
    finally:
        handler.unlink(missing_ok=True)
        command("systemctl", "daemon-reload")


@pytest.mark.parametrize(
    "asset", ["nebius-vpngw-agent.service", "nebius-vpngw-ordinary-agent.service"]
)
def test_agent_unit_waits_for_first_configuration(manager, tmp_path, asset):
    source = Path(ops.__file__).parent / "systemd" / asset
    config = tmp_path / "config-resolved.yaml"
    name = "vpngw-fixture-config-condition.service"
    unit = Path("/etc/systemd/system") / name
    assert not unit.exists()
    unit.write_text(
        source.read_text()
        .replace("/etc/nebius-vpngw/config-resolved.yaml", str(config))
        .replace("/usr/bin/python3 -m nebius_vpngw.agent.main", "/bin/sleep infinity")
    )
    command("systemctl", "daemon-reload")
    try:
        command("systemctl", "start", name)
        observed = manager.unit(name)
        assert observed["ActiveState"] == "inactive"
        assert observed["ConditionResult"] is False
        assert observed["NRestarts"] == 0
        config.touch()
        command("systemctl", "start", name)
        assert manager.unit(name)["ActiveState"] == "active"
    finally:
        command("systemctl", "stop", name)
        unit.unlink()
        command("systemctl", "daemon-reload")


@pytest.mark.parametrize("timeout", [False, True])
def test_local_command_settles_real_descendants_before_return(manager, tmp_path, timeout):
    from nebius_vpngw.agent import local_commands

    operation = begin(manager)
    ready = tmp_path / "child-ready"
    marker = tmp_path / "late-write"
    script = (
        "import os,time,pathlib\n"
        "if os.fork() == 0:\n"
        f" pathlib.Path({str(ready)!r}).touch()\n"
        " time.sleep(1)\n"
        f" pathlib.Path({str(marker)!r}).touch()\n"
        " os._exit(0)\n"
        f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(.005)\n"
        + ("time.sleep(10)\n" if timeout else "")
    )
    budget = local_commands.CommandBudget(
        time.monotonic() + 5, operation=operation, effect_deadline=time.monotonic() + 0.4
    )
    token = local_commands.CURRENT.set(budget)
    try:
        if timeout:
            with pytest.raises(subprocess.TimeoutExpired):
                local_commands.run([sys.executable, "-c", script], capture_output=True, text=True)
            with pytest.raises(RuntimeError):
                budget.require_success()
            operation.fail()
        else:
            result = local_commands.run(
                [sys.executable, "-c", script], capture_output=True, text=True
            )
            assert result.returncode == 0
            budget.require_success()
            operation.complete()
        effect = ops.Journal().read()["effects"][0]
        assert ready.exists() and effect["state"] == "settled"
        assert ops.process_group_empty(effect["process"])
        time.sleep(1.1)
        assert not marker.exists()
    finally:
        local_commands.CURRENT.reset(token)


def test_real_wire_jobs_and_void_reload(manager):
    manager.preflight()
    operation = begin(manager)
    operation.reload_manager()  # real busctl emits no JSON for this void method
    operation.service("start", "frr.service")
    effect = ops.Journal().read()["effects"][-1]
    assert effect["jobs"][0][0] > 0
    assert effect["jobs"][0][1].startswith("/org/freedesktop/systemd1/job/")
    assert manager.unit("frr.service")["ActiveState"] == "active"
    operation.service("reload", "frr.service")
    operation.service("stop", "frr.service")
    assert manager.stopped("frr.service")
    operation.complete()
    ops.require_idle()


def test_failed_reload_cannot_retire_as_success(manager):
    unit = Path("/etc/systemd/system/frr.service")
    unit.write_text(unit.read_text().replace("ExecReload=/bin/true", "ExecReload=/bin/false"))
    command("systemctl", "daemon-reload")
    operation = begin(manager)
    operation.service("start", "frr.service")
    with pytest.raises(ops.OperationBlocked, match="service_failed"):
        operation.service("reload", "frr.service")
    operation.fail()
    assert manager.unit("frr.service")["ActiveState"] == "active"
    assert manager.unit("frr.service")["ReloadResult"] == "exit-code"
    with pytest.raises(ops.OperationBlocked):
        ops.require_idle()


def test_dead_caller_does_not_release_delegated_job(manager, tmp_path):
    marker = tmp_path / "late-effect"
    unit = Path("/etc/systemd/system/frr.service")
    unit.write_text(
        f'[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/sh -c "sleep 1; touch {marker}"\n'
    )
    command("systemctl", "daemon-reload")
    code = f"""import os,time
from pathlib import Path
from nebius_vpngw import ordinary_operations as ops
ops.JOURNAL=Path({str(ops.JOURNAL)!r})
m=ops.Manager(time.monotonic()+20)
o=ops.Operation.begin(request='1'*32,config='2'*64,artifact='3'*64,predecessor='4'*64,manager=m,network=m.network())
i=o.intent('unit',unit='frr.service',action='start')
r=m.system('EnqueueUnitJob','sss','frr.service','start','fail')['data']
o.update(i,jobs=[r[:5],*r[5]],state='accepted')
os._exit(17)
"""
    assert subprocess.run([sys.executable, "-B", "-c", code], timeout=20).returncode == 17
    assert not marker.exists()
    assert not ops.recoverable(ops.Journal().read(), manager)
    with pytest.raises(ops.OperationBlocked), ops.mutation_lock():
        pytest.fail("concurrent writer admitted")
    eventually(marker.exists)
    eventually(lambda: ops.recoverable(ops.Journal().read(), manager))
    with pytest.raises(ops.OperationBlocked):
        ops.require_idle()  # settlement is observation, not automatic retirement
    old = ops.Journal().read()
    fresh = ops.Operation.begin(
        request="5" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        previous=ops.digest(old),
    )
    fresh.complete()


def test_lost_reply_never_resubmits(manager, monkeypatch):
    operation = begin(manager)
    real = manager.system
    calls = []

    def lost(method, *args):
        calls.append(method)
        result = real(method, *args)
        if method == "EnqueueUnitJob":
            raise TimeoutError("reply deliberately lost after acceptance")
        return result

    monkeypatch.setattr(manager, "system", lost)
    with pytest.raises(TimeoutError):
        operation.service("start", "frr.service")
    operation.fail()
    assert calls.count("EnqueueUnitJob") == 1
    assert ops.Journal().read()["effects"][0]["state"] == "intent"
    with pytest.raises(ops.OperationBlocked):
        ops.require_idle()


def test_stop_requires_control_process_and_cgroup_exit(manager, tmp_path):
    unit = Path("/etc/systemd/system/frr.service")
    unit.write_text(
        "[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\nExecStop=/bin/sleep 1\n"
    )
    command("systemctl", "daemon-reload")
    operation = begin(manager)
    operation.service("start", "frr.service")
    before = time.monotonic()
    operation.service("stop", "frr.service", timeout=5)
    assert time.monotonic() - before >= 0.9
    assert manager.stopped("frr.service")
    operation.complete()


def test_native_netplan_convergence_and_management_address_drift(manager):
    operation = begin(manager)
    index = operation.before_netplan()
    command("netplan", "apply")
    operation.update(index, state="accepted")
    operation.after_netplan(index)
    operation.complete()
    command("ip", "addr", "add", "192.0.2.123/32", "dev", "eth0")
    try:
        assert manager.network()["links"] != operation.value["network"]["links"]
    finally:
        command("ip", "addr", "del", "192.0.2.123/32", "dev", "eth0")


def test_dormant_extra_interface_is_rejected_before_netplan_apply(manager):
    path = Path("/etc/netplan/80-dormant.yaml")
    path.write_text("network:\n  version: 2\n  ethernets:\n    eth1:\n      dhcp4: true\n")
    path.chmod(0o600)
    try:
        with pytest.raises(ops.OperationBlocked, match="scope_unknown"):
            manager.network()
    finally:
        path.unlink()


def test_effective_ordinary_units_remove_bootstrap_dataplane_wants(manager):
    assets = Path("/workspace/src/nebius_vpngw/systemd")
    unit = Path("/etc/systemd/system/nebius-vpngw-agent.service")
    dropin = Path(str(unit) + ".d/override.conf")
    dropin.parent.mkdir(exist_ok=True)
    unit.write_bytes((assets / "nebius-vpngw-agent.service").read_bytes())
    dropin.write_bytes((assets / "nebius-vpngw-agent-ordering.conf").read_bytes())
    try:
        command("systemctl", "daemon-reload")
        assert not manager.management_admitted()
        unit.write_bytes((assets / "nebius-vpngw-ordinary-agent.service").read_bytes())
        dropin.write_bytes((assets / "nebius-vpngw-ordinary-agent-ordering.conf").read_bytes())
        command("systemctl", "daemon-reload")
        assert manager.management_admitted()
        assert "strongswan-starter.service" in manager.unit(unit.name)["After"]
        extra = dropin.with_name("custom.conf")
        extra.write_text("[Unit]\nWants=frr.service\n")
        try:
            command("systemctl", "daemon-reload")
            with pytest.raises(ops.OperationBlocked, match="scope_unknown"):
                manager.environment()
        finally:
            extra.unlink()
    finally:
        dropin.unlink()
        unit.unlink()
        command("systemctl", "daemon-reload")


def test_streamed_handoff_recovers_a_stop_accepted_before_holder_died(manager, tmp_path):
    import select

    from nebius_vpngw.handoff_bootstrap import streamed_source

    config = ops.JOURNAL.parent.parent / "resolved.yaml"
    config.write_text("gateway: {}\n")
    code = streamed_source() + (
        "\nfrom pathlib import Path\n"
        "_ops=sys.modules['_vpngw_ordinary_operations']\n"
        "_remote=sys.modules['_vpngw_ordinary_remote']\n"
        "_handoff=sys.modules['_vpngw_handoff_remote']\n"
        f"_ops.JOURNAL=Path({str(ops.JOURNAL)!r})\n"
        f"_ops.ROUTING_LOCK=Path({str(ops.ROUTING_LOCK)!r})\n"
        f"_remote.ROUTING_LOCK=Path({str(ops.ROUTING_LOCK)!r})\n"
        f"_remote.CONFIG=Path({str(config)!r})\n"
        f"_handoff.MARKER=Path({str(config.parent / 'marker')!r})\n"
        "_handoff.main()\n"
    )
    binding = "6" * 64
    ha = dict(
        cluster_id="fixture-cluster", node_id="fixture-node", generation_id="fixture-generation"
    )
    unit = Path("/etc/systemd/system/nebius-vpngw-health-monitor.service")
    unit.write_text("[Service]\nExecStart=/bin/sleep infinity\nExecStop=/bin/sleep 3\n")
    command("systemctl", "daemon-reload")
    command("systemctl", "start", unit.name)
    processes = []

    def inspect():
        return json.loads(
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import sys; exec(compile(bytes.fromhex(sys.stdin.readline().strip()), '<fixture>', 'exec'))",
                ],
                input=code.encode().hex() + "\n" + json.dumps({"action": "inspect"}) + "\n",
                text=True,
                capture_output=True,
                check=True,
                timeout=20,
            ).stdout
        )

    def reserve(observed):
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                "import sys; exec(compile(bytes.fromhex(sys.stdin.readline().strip()), '<fixture>', 'exec'))",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(child)
        child.stdin.write(code.encode().hex() + "\n")
        child.stdin.write(
            json.dumps(
                dict(
                    action="reserve",
                    observed=observed,
                    binding=binding,
                    artifact="3" * 64,
                    approval="4" * 64,
                    ha=ha,
                )
            )
            + "\n"
        )
        child.stdin.flush()
        return child

    try:
        first = reserve(inspect())

        def accepted():
            value = ops.Journal().read()
            return value and any(e["state"] == "accepted" for e in value["effects"])

        eventually(accepted)
        first.kill()
        first.wait(timeout=5)
        eventually(lambda: manager.stopped(unit.name))
        assert ops.Journal().read()["effects"][0]["state"] == "accepted"
        assert not ops.recoverable(ops.Journal().read(), manager)
        second = reserve(inspect())
        assert select.select([second.stdout], [], [], 20)[0]
        assert json.loads(second.stdout.readline()) == {"ready": True}
        assert ops.Journal().read()["purpose"] == "ha-handoff"
        # A stop-only recovery cannot retire the guard without package and
        # activation proof. The composed migration test exercises that success.
        second.stdin.write(json.dumps({"action": "complete", "operation": "7" * 64}) + "\n")
        second.stdin.flush()
        assert second.wait(timeout=5) != 0
        assert ops.Journal().read()["state"] == "unresolved"
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
        command("systemctl", "stop", unit.name)
        unit.unlink()
        command("systemctl", "daemon-reload")


def test_persisted_startup_precondition_blocks_old_and_partial_packages(
    manager, monkeypatch, tmp_path
):
    import hashlib

    real_journal = Path("/var/lib/nebius-vpngw/ordinary/operation.json")
    monkeypatch.setattr(ops, "JOURNAL", real_journal)
    assert not real_journal.exists()
    unit = Path("/etc/systemd/system/nebius-vpngw-health-monitor.service")
    marker = tmp_path / "started"
    package = Path("/workspace/src/nebius_vpngw")
    artifact = ops.digest(
        {
            path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(package.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
    )
    unit.write_text(
        f"[Service]\nType=oneshot\nRemainAfterExit=yes\nEnvironment=PYTHONPATH=/workspace/src\nExecStart=/bin/touch {marker}\n"
    )
    before = manager.environment()
    ops.install_startup_guard()
    command("systemctl", "daemon-reload")
    assert manager.environment() == before  # owned guard cannot stale its own plan
    helper = Path("/var/lib/nebius-vpngw/ordinary-startup.py")
    helper.chmod(0o666)
    with pytest.raises(ops.OperationBlocked, match="unsafe"):
        ops.install_startup_guard()
    helper.chmod(0o600)
    begin(manager)  # wrong/old package digest cannot bypass journal
    try:
        # A fresh manager reload models reading the persistent precondition on
        # boot; the journal alone cannot retrofit old executable behavior.
        command("systemctl", "daemon-reload")

        command("systemctl", "start", unit.name)
        assert not marker.exists()
        value = ops.Journal().read()
        value["artifact"] = artifact
        ops.Journal().write(value)
        command("systemctl", "start", unit.name)
        assert marker.exists()  # exact guard-aware package can start and wait
        command("systemctl", "stop", unit.name)
        marker.unlink()
        # Corrupt records fail closed even for a guard-aware installation.
        real_journal.write_text("{malformed")
        command("systemctl", "start", unit.name)
        assert not marker.exists()
    finally:
        real_journal.unlink()
        command("systemctl", "stop", unit.name)
        unit.unlink()
        for name in ops.MANAGEMENT:
            if name.endswith(".service"):
                (Path("/etc/systemd/system") / (name + ".d") / "ordinary-admission.conf").unlink(
                    missing_ok=True
                )
        Path("/var/lib/nebius-vpngw/ordinary-startup.py").unlink(missing_ok=True)
        command("systemctl", "daemon-reload")


def test_ha_install_phase_blocks_controller_and_keeps_independent_guard(
    manager, monkeypatch, tmp_path
):
    from nebius_vpngw import ha_repair

    journal = Path("/var/lib/nebius-vpngw/ordinary/operation.json")
    monkeypatch.setattr(ops, "JOURNAL", journal)
    assert not journal.exists()
    marker = Path("/etc/nebius-vpngw/vm-ha-enabled")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"")
    units = [Path("/etc/systemd/system") / name for name in ops.HA_WRITERS]
    started = tmp_path / "writer-started"
    for unit in units:
        unit.write_text(
            f"[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/touch {started}\n"
        )
    ops.install_startup_guard(ha_repair=True)
    operation = ops.Operation.begin(
        request="8" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        purpose="ha-handoff",
    )
    operation.phase("install")
    ha_repair.persist_cold_guard(Path(ha_repair.__file__).read_bytes())
    guard_unit = Path("/etc/systemd/system") / ops.HA_GUARD
    guard_unit.write_bytes(
        Path("/workspace/src/nebius_vpngw/systemd/nebius-vpngw-vm-ha-guard.service").read_bytes()
    )
    try:
        command("systemctl", "daemon-reload")
        for unit in units:
            command("systemctl", "start", unit.name)
        assert not started.exists()
        # A fresh interpreter cannot import any product code. The persisted
        # canonical guard must still disable forwarding and bind this boot.
        command("sysctl", "-w", "net.ipv4.ip_forward=1")
        command("systemctl", "restart", ops.HA_GUARD)
        assert Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "0"
        guard = json.loads(Path("/var/lib/nebius-vpngw/vm-ha/guard.json").read_text())
        assert guard["guard_boot_id"] == ops.BOOT.read_text().strip()
        assert guard["data_plane_mode"] == "blocked"
        operation.phase("publish")
        for unit in units:
            command("systemctl", "start", unit.name)
        assert not started.exists()  # includes the first-marker interruption
        operation.phase("activate")
        command("systemctl", "start", units[0].name)
        assert started.exists()
    finally:
        journal.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        command("systemctl", "stop", ops.HA_GUARD)
        guard_unit.unlink(missing_ok=True)
        for unit in units:
            command("systemctl", "stop", unit.name)
            unit.unlink()
        for name in (*ops.MANAGEMENT, *ops.HA_WRITERS):
            if name.endswith(".service"):
                (Path("/etc/systemd/system") / (name + ".d/ordinary-admission.conf")).unlink(
                    missing_ok=True
                )
        Path(
            "/etc/systemd/system/nebius-vpngw-vm-ha-guard.service.d/package-independent.conf"
        ).unlink(missing_ok=True)
        Path("/var/lib/nebius-vpngw/ordinary-startup.py").unlink(missing_ok=True)
        Path("/var/lib/nebius-vpngw/ha-cold-guard.py").unlink(missing_ok=True)
        command("systemctl", "daemon-reload")


def test_process_group_writer_survives_direct_child_exit(manager, tmp_path):
    import signal

    script = "import os,time; child=os.fork(); time.sleep(30) if child==0 else time.sleep(0.5)"
    leader = subprocess.Popen([sys.executable, "-B", "-c", script], start_new_session=True)
    try:
        process = ops.identity(leader.pid)
        leader.wait(timeout=5)
        assert not ops.alive(process)
        assert not ops.process_group_empty(process)
        os.killpg(process["pid"], signal.SIGKILL)
        eventually(lambda: ops.process_group_empty(process))
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        leader.wait(timeout=5)


@pytest.mark.parametrize("transition", ["remove", "reindex"])
def test_exact_static_route_retirement_in_private_kernel_namespace(manager, tmp_path, transition):
    import uuid

    from nebius_vpngw import ordinary_routes as routes
    from nebius_vpngw.agent import local_commands
    from nebius_vpngw.agent.state_store import StateStore

    namespace = "route-test-" + uuid.uuid4().hex[:12]
    command("ip", "netns", "add", namespace)

    def run(args, **kwargs):
        return local_commands.run(["ip", "netns", "exec", namespace, *args], timeout=20, **kwargs)

    def configure(*args):
        return run(list(args), check=True, capture_output=True, text=True)

    old = {
        "gateway": {},
        "connections": [
            {
                "routing_mode": "static",
                "tunnels": [
                    {
                        "name": "first",
                        "remote_public_ip": "203.0.113.10",
                        "static_routes": {"remote_prefixes": ["10.10.0.0/16"]},
                    },
                    {
                        "name": "second",
                        "remote_public_ip": "203.0.113.20",
                        "static_routes": {"remote_prefixes": ["10.20.0.0/16"]},
                    },
                ],
            }
        ],
    }
    desired = {
        "gateway": {},
        "connections": [
            {
                "routing_mode": "static",
                "tunnels": old["connections"][0]["tunnels"][1:] if transition == "reindex" else [],
            }
        ],
    }
    token = None
    try:
        configure("ip", "link", "add", "eth0", "type", "dummy")
        configure("ip", "link", "set", "eth0", "up")
        for index, dest in enumerate(("10.10.0.0/16", "10.20.0.0/16")):
            configure(
                "ip",
                "link",
                "add",
                f"xfrm{index}",
                "type",
                "xfrm",
                "dev",
                "eth0",
                "if_id",
                str(100 + index),
            )
            configure("ip", "link", "set", f"xfrm{index}", "up")
            configure("ip", "route", "add", dest, "dev", f"xfrm{index}")
        configure("ip", "addr", "add", "169.254.1.1/30", "dev", "xfrm0")
        configure(
            "ip", "route", "add", "10.10.0.0/16", "dev", "xfrm0", "proto", "186", "metric", "50"
        )
        configure("ip", "route", "add", "10.10.0.0/16", "dev", "xfrm0", "table", "200")
        state = tmp_path / "last-applied.json"
        StateStore(state).save_last_applied(old)
        store = routes.RouteStore()
        boot = ops.BOOT.read_text().strip()
        plan = routes.RouteRetirementPlan.build(
            store.snapshot(old, state, run, boot, future=routes.projection(desired)),
            routes.projection(desired),
        )
        operation = begin(manager)
        operation.phase("reconcile")
        store.prepare(operation.check(), plan, approved=True)
        token = local_commands.CURRENT.set(
            local_commands.CommandBudget(time.monotonic() + 60, operation=operation)
        )
        obligations = store.cleanup(operation, desired, run, boot)
        for endpoint in routes.projection(desired):
            for dest in endpoint["prefixes"]:
                configure("ip", "route", "replace", dest, "dev", endpoint["name"])
        store.commit(operation.check(), store.verify(desired, run, boot, obligations=obligations))
        StateStore(state).save_last_applied(desired)
        observed = json.loads(configure("ip", "-j", "-4", "route", "show", "table", "all").stdout)
        assert any(row.get("protocol") == "bgp" and row.get("metric") == 50 for row in observed)
        assert any(str(row.get("table")) == "200" for row in observed)
        assert any(
            row.get("protocol") == "kernel" and row.get("dst") == "169.254.1.0/30"
            for row in observed
        )
        assert (
            len(json.loads(configure("ip", "-d", "-j", "link", "show", "type", "xfrm").stdout)) == 2
        )
        assert not any(
            row.get("dst") == "10.20.0.0/16" and row.get("dev") == "xfrm1" for row in observed
        )
        again = routes.RouteRetirementPlan.build(
            store.snapshot(desired, state, run, boot), routes.projection(desired)
        )
        assert not again.payload["deletions"]
        local_commands.CURRENT.get().require_success()
        operation.complete()
    finally:
        if token is not None:
            local_commands.CURRENT.reset(token)
        command("ip", "netns", "del", namespace)
