"""Native Ubuntu package/loader/VICI checks; not historical artifact qualification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from nebius_vpngw import ipsec_observation as vici
from nebius_vpngw import ordinary_operations as ops
from nebius_vpngw import runtime_migration as migration
from nebius_vpngw.deploy import ordinary_remote as remote

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit disposable Ubuntu systemd fixture",
    ),
]


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=120)


def configure_fixture_network():
    """Give networkd the same single-interface authority required on a guest."""
    link = json.loads(command("ip", "-j", "-4", "addr", "show", "eth0").stdout)[0]
    addresses = [
        f"{a['local']}/{a['prefixlen']}" for a in link["addr_info"] if a["scope"] == "global"
    ]
    gateway = json.loads(command("ip", "-j", "route", "show", "default").stdout)[0]["gateway"]
    config = {
        "network": {
            "version": 2,
            "renderer": "networkd",
            "ethernets": {
                "eth0": {"addresses": addresses, "routes": [{"to": "default", "via": gateway}]}
            },
        }
    }
    path = Path("/etc/netplan/50-cloud-init.yaml")
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(config))
    path.chmod(0o600)
    command("ip", "route", "flush", "default")
    command("netplan", "apply")


def test_starter_replacement_uses_staged_closure_and_product_loader(tmp_path):
    assert Path("/.dockerenv").is_file() and os.geteuid() == 0
    configure_fixture_network()
    command("apt-get", "update", "-qq")
    command("apt-get", "install", "-y", "--no-install-recommends", "strongswan", "charon-systemd-")
    command("systemctl", "disable", "strongswan-starter.service")
    # Existing admission controls coexist with the migration guard. Native
    # systemd prints command arrays as repeated ExecCondition properties.
    condition = Path("/etc/systemd/system/strongswan-starter.service.d/10-fixture.conf")
    condition.parent.mkdir(parents=True, exist_ok=True)
    condition.write_text("[Service]\nExecCondition=/usr/bin/true\n")
    command("systemctl", "daemon-reload")
    # An image can retain stale or absent repository indexes. Resolution and
    # staging must use fresh authenticated metadata without rewriting those
    # indexes or running host update hooks during the product's preview.
    lists = Path("/var/lib/apt/lists")
    # OverlayFS may reject directory renames even within one mount. A private
    # tmpfs provides an empty cache and leaves the original tree untouched.
    command("mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", str(lists))
    hook_effect = tmp_path / "unexpected-update-hook"
    hook = Path("/etc/apt/apt.conf.d/99-vpngw-fixture-hook")
    hook.write_text(f'APT::Update::Post-Invoke {{ "touch {hook_effect}"; }};\n')
    try:
        approved = migration.plan()
        assert "strongswan-starter" in approved["remove"]
        assert "charon-systemd" in approved["install"]
        migration.stage(approved)
        assert not list(lists.iterdir()), "product wrote the system APT indexes"
        assert not hook_effect.exists(), "product executed a host APT update hook"
    finally:
        hook.unlink()
        command("umount", str(lists))
    before_policy = migration.policy_snapshot()
    identity = "f" * 32
    migration.begin(approved, identity, {"wheel_sha256": "e" * 64})
    remote.DEADLINE = time.monotonic() + 300
    manager = ops.Manager(remote.DEADLINE)
    operation = ops.Operation.begin(
        request=identity,
        config="a" * 64,
        artifact="b" * 64,
        predecessor="c" * 64,
        manager=manager,
        network=manager.network(),
    )
    remote.OPERATION = operation
    migration.COMMAND, migration.CHECK = remote.command, operation.check
    try:
        migration.inhibit()
        for unit in ("strongswan-starter.service", "strongswan.service"):
            if not manager.unit(unit).get("missing") or not manager.stopped(unit):
                operation.service("stop", unit)
            assert manager.stopped(unit), {
                key: manager.unit(unit).get(key)
                for key in (
                    "LoadState",
                    "ActiveState",
                    "MainPID",
                    "ControlPID",
                    "Job",
                    "ControlGroup",
                )
            }
        assert not migration.startup_allowed()
        original_run = migration.run
        interrupted = False

        def interrupt_after_unpack(args, **kwargs):
            nonlocal interrupted
            result = original_run(args, **kwargs)
            if args[:2] == ["dpkg", "--unpack"] and not interrupted:
                interrupted = True
                raise RuntimeError("fixture interruption after unpack")
            return result

        migration.run = interrupt_after_unpack
        try:
            with pytest.raises(RuntimeError, match="fixture interruption"):
                migration.install()
        finally:
            migration.run = original_run
        assert migration.state()["phase"] == "packages"
        assert migration.packages()["charon-systemd"][1] == "unpacked"
        assert not migration.startup_allowed()
        # Resume the exact operation from partially configured dpkg state.
        migration.begin(approved, identity, {"wheel_sha256": "e" * 64})
        migration.install()
        assert migration.unit_policy()["strongswan.service"]["UnitFileState"] == "disabled"
        # Exercise idempotent roll-forward after a completed package boundary.
        migration.install()
        asset = Path("/workspace/src/nebius_vpngw/systemd/nebius-vpngw-strongswan.conf").read_text()
        # Fixture has no product ingress/preflight service. Keep the canonical
        # daemon Service policy, with independent fixture-only startup ordering.
        service = "[Service]" + asset.split("[Service]", 1)[1]
        migration.write(
            Path("/etc/systemd/system/strongswan.service.d/override.conf"), service.encode(), 0o644
        )
        operation.reload_manager()
        migration.activate()
        operation.service("start", "strongswan.service")
        assert vici.snapshot().complete
        native = migration.native()
        if not native["ready"]:
            raise AssertionError(
                json.dumps(
                    {
                        "packages": native["packages"],
                        "policy": migration.command_policy(native["unit"]),
                        "executable": native.get("executable"),
                        "socket_mode": oct(Path("/run/charon.vici").stat().st_mode),
                    }
                )
            )
        assert not vici.snapshot().sas  # no unit-owned implicit configuration load
        assert vici.loaded_connections() == frozenset()
        # Additional loader hooks are unsafe even when the executable is a wrapper.
        rogue = Path("/etc/systemd/system/strongswan.service.d/zz-loader.conf")
        for extra in ("ExecStartPost=/usr/bin/true", "ExecReload=/usr/bin/true"):
            migration.write(rogue, ("[Service]\n" + extra + "\n").encode(), 0o644)
            operation.reload_manager()
            assert not migration.native()["ready"]
        migration.unlink(rogue)
        operation.reload_manager()
        assert migration.native()["ready"]
        migration.release()
        operation.complete()
        remote.OPERATION = None
        migration.COMMAND = migration.CHECK = None
        assert migration.policy_snapshot() == before_policy
        assert not (migration.ROOT / "inhibited").exists()
        assert migration.plan()["archives"] == {}
    finally:
        remote.OPERATION = None
        migration.COMMAND = migration.CHECK = None
        command("systemctl", "stop", "strongswan.service")


def test_management_activation_keeps_real_timer_inhibited_until_release(monkeypatch, tmp_path):
    assert Path("/.dockerenv").is_file() and os.geteuid() == 0
    monkeypatch.setattr(ops, "JOURNAL", tmp_path / "operation.json")
    names = (
        "nebius-vpngw-agent.service",
        "nebius-vpngw-health-monitor.service",
        "nebius-vpngw-fix-routes.timer",
        "nebius-vpngw-fix-routes.service",
    )
    paths = [Path("/etc/systemd/system") / name for name in names]
    assert all(not path.exists() for path in paths)
    try:
        for path in paths:
            path.write_text(
                "[Timer]\nOnActiveSec=1h\nUnit=nebius-vpngw-fix-routes.service\n"
                "[Install]\nWantedBy=timers.target\n"
                if path.suffix == ".timer"
                else "[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\n"
                "[Install]\nWantedBy=multi-user.target\n"
            )
        command("systemctl", "daemon-reload")
        approved = migration.plan()
        migration.stage(approved)
        migration.begin(approved, "d" * 32, {"wheel_sha256": "e" * 64})
        remote.DEADLINE = time.monotonic() + 180
        manager = ops.Manager(remote.DEADLINE)
        operation = ops.Operation.begin(
            request="d" * 32,
            config="a" * 64,
            artifact="e" * 64,
            predecessor="c" * 64,
            manager=manager,
            network=manager.network(),
        )
        remote.OPERATION = operation
        migration.COMMAND, migration.CHECK = remote.command, operation.check
        migration.inhibit()
        migration.activate()
        operation.phase("activate")
        operation.service("start", "strongswan.service")
        remote.activate_management(operation)
        assert (migration.ROOT / "inhibited").exists()
        assert manager.unit(names[0])["ActiveState"] == "active"
        assert manager.unit(names[1])["ActiveState"] == "active"
        assert manager.unit(names[2])["ActiveState"] == "inactive"
        assert not any(effect.get("unit") == names[2] for effect in operation.value["effects"])
        migration.release()
        operation.service("start", names[2])
        assert manager.unit(names[2])["ActiveState"] == "active"
        operation.complete()
    finally:
        remote.OPERATION = None
        migration.COMMAND = migration.CHECK = None
        command("systemctl", "stop", "strongswan.service")
        for name, path in zip(names, paths, strict=True):
            command("systemctl", "stop", name)
            path.unlink(missing_ok=True)
        command("systemctl", "daemon-reload")


def test_retained_daemon_alias_uses_primary_systemd_job_identity():
    """Fixture models a retained alias; the journal and PID 1 are real."""
    assert Path("/.dockerenv").is_file() and os.geteuid() == 0
    alias = Path("/etc/systemd/system/strongswan-swanctl.service")
    assert not alias.exists() and not alias.is_symlink()
    alias.symlink_to("/usr/lib/systemd/system/strongswan.service")
    command("systemctl", "daemon-reload")
    try:
        manager = ops.Manager(time.monotonic() + 60)
        metadata = manager.unit(alias.name)
        assert metadata["Id"] == "strongswan.service"
        assert alias.name in metadata["Names"]
        operation = ops.Operation.begin(
            request="a" * 32,
            config="b" * 64,
            artifact="c" * 64,
            predecessor="d" * 64,
            manager=manager,
            network=manager.network(),
        )
        operation.service("stop", alias.name)
        assert manager.stopped("strongswan.service")
        assert operation.value["effects"][-1]["unit"] == "strongswan.service"
        operation.complete()
    finally:
        alias.unlink()
        command("systemctl", "daemon-reload")


def test_same_boot_handoff_lost_stop_reply_resumes_real_journal(tmp_path):
    """A dead client may lose PID 1's reply after the stop was submitted."""
    path = tmp_path / "private" / "operation.json"
    command(
        sys.executable,
        "-c",
        "from pathlib import Path; import sys,time; "
        "from nebius_vpngw import ordinary_operations as o; "
        "m=o.Manager(time.monotonic()+30); "
        "x=o.Operation.begin(request='1'*32,config='2'*64,artifact='3'*64,"
        "predecessor='4'*64,manager=m,network={},journal=o.Journal(Path(sys.argv[1])),"
        "purpose='ha-handoff'); "
        "x.intent('unit',unit='strongswan.service',action='stop'); "
        "m.system('EnqueueUnitJob','sss','strongswan.service','stop','fail')",
        str(path),
    )
    journal = ops.Journal(path)
    previous = journal.read()
    assert not ops.alive(previous["owner"])
    manager = ops.Manager(time.monotonic() + 30)
    deadline = time.monotonic() + 10
    while not manager.unit_finished(previous["effects"][0]):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    operation = ops.Operation.begin(
        request=previous["id"],
        config=previous["config"],
        artifact=previous["artifact"],
        predecessor=previous["predecessor"],
        manager=manager,
        network={},
        previous=ops.digest(previous),
        journal=journal,
        purpose="ha-handoff",
        resume_inhibited_quiescence=True,
    )
    assert journal.read()["effects"][0]["state"] == "settled"
    operation.service("stop", "strongswan.service")
    operation.complete()
    assert journal.read()["state"] == "complete"


def test_destination_guard_native_json_is_exact_and_drop_only(tmp_path):
    from nebius_vpngw.agent.vm_ha.forwarding import TABLE, objects

    expected = objects(
        {"0.0.0.0/0": ("xfrm0", "xfrm1"), "10.2.1.0/24": ("xfrm2",), "192.0.2.1/32": ()},
        ("10.2.0.0/16",),
    )
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"nftables": [{"add": obj} for obj in expected]}))
    try:
        command("nft", "-j", "--check", "-f", str(path))
        command("nft", "-j", "-f", str(path))
        actual = json.loads(command("nft", "-j", "list", "table", "inet", TABLE).stdout)
        normalized = [
            {k: {key: value for key, value in obj.items() if key != "handle"}}
            for row in actual["nftables"]
            for k, obj in row.items()
            if k != "metainfo"
        ]
        assert normalized == expected
        assert all(
            "accept" not in expression
            for obj in expected
            if "rule" in obj
            for expression in obj["rule"]["expr"]
        )
    finally:
        subprocess.run(["nft", "delete", "table", "inet", TABLE], capture_output=True)


def test_destination_guard_blocks_plaintext_after_withdrawal_and_restart(tmp_path):
    """Use real routed packets; no replacement observer or verdict evaluator."""
    import uuid

    from nebius_vpngw.agent.vm_ha.forwarding import TABLE, DestinationProtection
    from nebius_vpngw.ha_tunnel_policy import ConnectionPolicy, TunnelPath

    assert Path("/.dockerenv").is_file() and os.geteuid() == 0
    suffix = uuid.uuid4().hex[:6]
    router, source, sink = namespaces = [f"guard-{suffix}-{i}" for i in range(3)]

    def ns(name, *args):
        return command("ip", "netns", "exec", name, *args)

    def routed(args, **kwargs):
        return subprocess.run(["ip", "netns", "exec", router, *args], **kwargs)

    def ping(origin, destination, *, expected):
        result = subprocess.run(
            ["ip", "netns", "exec", origin, "ping", "-n", "-c", "1", "-W", "1", destination],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert (result.returncode == 0) is expected

    def policy(name, mode, prefixes):
        destinations = frozenset(prefixes)
        path = TunnelPath("guard-path", "xfrm0", 100, True, destinations, "", ("", ""))
        return ConnectionPolicy(name, mode, (path,), destinations)

    try:
        for name in namespaces:
            command("ip", "netns", "add", name)
            ns(name, "ip", "link", "set", "lo", "up")
        for index, (peer, device, subnet) in enumerate(
            [(source, "lan", "10.40.0"), (sink, "wan", "192.0.2")]
        ):
            left, right = f"g{suffix}{index}r", f"g{suffix}{index}p"
            command("ip", "link", "add", left, "type", "veth", "peer", "name", right)
            command("ip", "link", "set", left, "netns", router)
            command("ip", "link", "set", right, "netns", peer)
            ns(router, "ip", "link", "set", left, "name", device)
            ns(router, "ip", "address", "add", f"{subnet}.1/24", "dev", device)
            ns(router, "ip", "link", "set", device, "up")
            ns(peer, "ip", "address", "add", f"{subnet}.2/24", "dev", right)
            ns(peer, "ip", "link", "set", right, "up")
        ns(source, "ip", "route", "add", "default", "via", "10.40.0.1")
        ns(sink, "ip", "route", "add", "10.40.0.0/24", "via", "192.0.2.1")
        ns(router, "ip", "route", "add", "default", "via", "192.0.2.2")
        ns(router, "sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
        for address in ["10.80.0.1", "10.80.1.1", "10.90.0.1"]:
            ns(sink, "ip", "address", "add", address + "/32", "dev", "lo")
        ns(router, "ip", "link", "add", "xfrm0", "type", "xfrm", "if_id", "100")
        ns(router, "ip", "link", "set", "xfrm0", "up")
        ping(source, "10.80.0.1", expected=True)
        ping(source, "10.90.0.1", expected=True)
        static = policy("static", "static", ["10.80.0.0/24"])
        bgp = policy("bgp", "bgp", [])
        local = ("10.40.0.0/24", "10.80.0.0/16")
        ledger = tmp_path / "protection.json"
        protection = DestinationProtection(
            ledger, cluster="native", policies=(static, bgp), local_prefixes=local, run=routed
        )
        protection.install()
        assert protection.ready()
        # The more-specific VPN prefix wins over the broader local prefix.
        ping(source, "10.80.0.1", expected=False)
        ping(source, "10.80.1.1", expected=True)
        # Host-origin control traffic is outside the forwarding hook.
        ping(router, "10.80.0.1", expected=True)
        ns(router, "ip", "route", "add", "10.90.0.0/24", "dev", "xfrm0")
        protection.admit(frozenset({"10.90.0.0/24"}))
        ns(router, "ip", "route", "del", "10.90.0.0/24")
        # Withdrawing the XFRM route exposes a usable plaintext default route.
        # The persisted admitted-prefix ledger must still block that escape.
        ping(source, "10.90.0.1", expected=False)
        ns(router, "nft", "delete", "table", "inet", TABLE)
        protection = DestinationProtection(
            ledger, cluster="native", policies=(static, bgp), local_prefixes=local, run=routed
        )
        protection.install()
        assert protection.ready()
        ping(source, "10.90.0.1", expected=False)
        ping(source, "10.80.1.1", expected=True)
        # A protected default preserves more-specific canonical local prefixes.
        default = DestinationProtection(
            tmp_path / "default.json",
            cluster="native",
            policies=(policy("default", "static", ["0.0.0.0/0"]),),
            local_prefixes=("10.40.0.0/24", "10.80.1.0/24"),
            run=routed,
        )
        default.install()
        ping(source, "10.80.0.1", expected=False)
        ping(source, "10.80.1.1", expected=True)
        ns(router, "nft", "add", "table", "inet", "customer")
        ns(
            router,
            "nft",
            "add",
            "chain",
            "inet",
            "customer",
            "forward",
            "{ type filter hook forward priority 0; policy accept; }",
        )
        ns(
            router,
            "nft",
            "add",
            "rule",
            "inet",
            "customer",
            "forward",
            "ip",
            "daddr",
            "10.80.1.1",
            "drop",
        )
        before = ns(router, "nft", "-j", "list", "table", "inet", "customer").stdout
        default.install()
        assert ns(router, "nft", "-j", "list", "table", "inet", "customer").stdout == before
        # RETURN in the product guard cannot bypass later customer filtering.
        ping(source, "10.80.1.1", expected=False)
    finally:
        for name in namespaces:
            subprocess.run(["ip", "netns", "delete", name], capture_output=True, timeout=5)


@pytest.mark.parametrize("mode", ["active", "passive"])
@pytest.mark.parametrize("partial_config", [False, True])
def test_autorestarted_daemon_reloads_current_authorized_configuration(
    tmp_path, mode, partial_config
):
    """Exercise the bounded HA component effect; cloud admission is tested separately."""
    from nebius_vpngw.agent.strongswan_renderer import (
        STRONGSWAN_CONF_DIR,
        SWANCTL_CONF,
        StrongSwanRenderer,
        _write_secret_file,
        authorized_configuration,
    )
    from nebius_vpngw.agent.vm_ha.runtime import SystemDataPlaneRuntime
    from nebius_vpngw.agent.vm_ha_controller import (
        ActionKind,
        ControllerAction,
        DataPlaneMode,
        DigestSet,
    )

    cfg = {
        "connections": [
            {
                "name": "reload",
                "routing_mode": "static",
                "tunnels": [
                    {
                        "name": "reload-fixture",
                        "remote_public_ip": "192.0.2.254",
                        "psk": "synthetic-native-reload-fixture",
                    }
                ],
            }
        ]
    }
    if mode == "passive":
        cfg["_ha_static_passive"] = True
        cfg["connections"][0]["routing_mode"] = "bgp"
        cfg["connections"].append(
            {
                "name": "cold",
                "routing_mode": "static",
                "tunnels": [
                    {
                        "name": "cold-static",
                        "remote_public_ip": "192.0.2.253",
                        "psk": "synthetic-cold-fixture",
                    }
                ],
            }
        )
    loaded_names = frozenset({"reload-fixture"})
    files = {}
    StrongSwanRenderer().render_and_apply(cfg, rendered_files=files)
    runtime_conf = STRONGSWAN_CONF_DIR.parent / "nebius-vpngw.conf"
    old_runtime_conf = runtime_conf.read_bytes() if runtime_conf.exists() else None
    old_file = SWANCTL_CONF.read_bytes() if SWANCTL_CONF.exists() else None
    override = Path("/etc/systemd/system/strongswan.service.d/override.conf")
    old_override = override.read_bytes() if override.exists() else None
    asset = Path("/workspace/src/nebius_vpngw/systemd/nebius-vpngw-strongswan.conf").read_text()
    # This standalone component fixture has no product ingress service. Apply
    # the canonical loader policy before establishing the fault baseline.
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("[Service]" + asset.split("[Service]", 1)[1])
    command("systemctl", "daemon-reload")
    old_forwarding = command("sysctl", "-n", "net.ipv4.ip_forward").stdout.strip()
    state = tmp_path / "data-plane.json"
    state.write_text(json.dumps({"schema": "nebius-vpngw/vm-ha-data-plane-v1", "mode": mode}))
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot", "data_plane_mode": mode})
    )
    runtime = SystemDataPlaneRuntime(
        state_path=state,
        guard_path=tmp_path / "guard.json",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="a" * 64,
        routing_lock_path=tmp_path / "routing.lock",
    )
    runtime.ipsec_configuration_provider = lambda: authorized_configuration(cfg)

    def pid():
        return command("systemctl", "show", "strongswan", "-p", "MainPID", "--value").stdout.strip()

    try:
        command("systemctl", "stop", "strongswan")
        runtime_conf.parent.mkdir(parents=True, exist_ok=True)
        runtime_conf.write_text(files[runtime_conf])
        _write_secret_file(SWANCTL_CONF, files[SWANCTL_CONF])
        command("systemctl", "start", "strongswan")
        # Reset setup's start budget while the unit is loaded and running; an
        # inactive unit may already have been garbage-collected by PID 1.
        # The declared one-crash trial starts only after this healthy baseline.
        command("systemctl", "reset-failed", "strongswan")
        command("swanctl", "--load-all", "--noprompt")
        # The supported post-configuration checkpoint has no table-220 rule.
        # Establish that baseline before the declared daemon-crash boundary.
        for family in ("-4", "-6"):
            for _ in range(8):
                if "lookup 220" not in command("ip", family, "rule", "show").stdout:
                    break
                command("ip", family, "rule", "del", "lookup", "220")
            else:
                pytest.fail("fixture policy-rule baseline did not settle")
        assert vici.loaded_connections() == loaded_names
        assert authorized_configuration(cfg).connections == frozenset({"reload-fixture"})
        before = pid()
        command("systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", "strongswan")
        deadline = time.monotonic() + 20
        while True:
            current = pid()
            try:
                if current not in {"0", before} and vici.loaded_connections() == frozenset():
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline, command(
                "systemctl", "show", "strongswan", "-p", "Result", "-p", "ActiveState"
            ).stdout
            time.sleep(0.1)
        if partial_config:
            # Reproduce the first divergence: a selector has restored only
            # connections after the daemon lost its credential store.
            command("swanctl", "--load-conns")
            partial = vici.loaded_configuration()
            assert partial.connections == loaded_names and not partial.shared_keys
        forwarding = "1" if mode == "active" else "0"
        assert "lookup 220" not in command("ip", "-4", "rule", "show").stdout
        assert "lookup 220" not in command("ip", "-6", "rule", "show").stdout
        command("sysctl", "-w", "net.ipv4.ip_forward=" + forwarding)
        assert runtime._missing_ipsec_configuration()
        action = ControllerAction(
            ActionKind.REPAIR_LOCAL_DATAPLANE,
            "reload-fixture",
            "boot",
            "node",
            "allocation",
            "revision",
            "a" * 64,
            DigestSet("a" * 64, "b" * 64, "c" * 64),
            repair_deadline_at=time.monotonic() + 5,
            repair_reasons=("daemon-configuration-missing:strongswan",),
            repair_mode=DataPlaneMode(mode),
        )
        runtime.repair_local(action)
        assert vici.loaded_connections() == loaded_names
        assert vici.loaded_configuration().includes(authorized_configuration(cfg))
        assert pid() == current, "configuration repair restarted the healthy daemon"
        assert command("sysctl", "-n", "net.ipv4.ip_forward").stdout.strip() == forwarding
        if mode == "passive":
            from nebius_vpngw.agent.vm_ha.tunnel_observation import xfrm_records

            inventory = vici.snapshot()
            assert inventory.complete
            assert all(sa.name != "cold-static" for sa in inventory.sas)
            policies = xfrm_records(command("ip", "-j", "xfrm", "policy").stdout, policy=True)
            assert not any(row.get("if_id") in (101, "0x65") for row in policies)
        assert SWANCTL_CONF.read_text() == files[SWANCTL_CONF]
    finally:
        command("sysctl", "-w", "net.ipv4.ip_forward=" + old_forwarding)
        command("systemctl", "stop", "strongswan")
        if old_file is None:
            SWANCTL_CONF.unlink(missing_ok=True)
        else:
            _write_secret_file(SWANCTL_CONF, old_file.decode())
        if old_override is None:
            override.unlink()
        else:
            override.write_bytes(old_override)
        if old_runtime_conf is None:
            runtime_conf.unlink()
        else:
            runtime_conf.write_bytes(old_runtime_conf)
        command("systemctl", "daemon-reload")
        command("systemctl", "start", "strongswan")
