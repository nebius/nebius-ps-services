"""Real same-owner selection with isolated strongSwan, XFRM and FRR.

Cloud ownership is a frozen fixture input. These tests prove native local tunnel
execution, not cloud fencing, deployment upgrades or a live-provider traffic SLO.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_strongswan_sessions import Endpoint, run

from nebius_vpngw.agent.vm_ha.tunnel_observation import parse_sas, xfrm_records
from nebius_vpngw.ipsec_observation import command as ipsec_command
from nebius_vpngw.tunnel_state import collect_tunnel_state

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit isolated systemd fixture",
    ),
]
DRIVER = "/workspace/tests/systemd/ha_tunnel_driver.py"


def test_ha_xfrm_preparation_respects_systemd_filesystem_sandbox():
    """Exercise the real kernel helper under the controller's write restriction."""
    script = """
import errno
from pathlib import Path
from nebius_vpngw.agent.xfrm_manager import XFRMManager
path = Path('/etc/sysctl.d/99-vpn-gateway.conf')
before = path.read_bytes() if path.exists() else None
forward = Path('/proc/sys/net/ipv4/ip_forward')
forward.write_text('0')  # Establish the already-fenced candidate precondition.
fenced = forward.read_text()
assert fenced.strip() == '0'
try:
    XFRMManager().setup_interfaces([], parent_dev='lo')
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError('fixture did not enforce the production filesystem restriction')
XFRMManager().setup_interfaces([], parent_dev='lo', persist_sysctl=False)
assert (path.read_bytes() if path.exists() else None) == before
assert forward.read_text() == fenced
print('runtime settings applied; persistent sysctl and forwarding fence unchanged')
"""
    result = subprocess.run(
        [
            "systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            f"--unit=vpngw-xfrm-sandbox-{uuid.uuid4().hex[:8]}",
            "--property=ProtectSystem=strict",
            "--property=PrivateNetwork=yes",
            "--property=NoNewPrivileges=yes",
            "--property=CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW",
            "--property=AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW",
            "--setenv=PYTHONPATH=/workspace/src",
            sys.executable,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "forwarding fence unchanged" in result.stdout


def test_ha_bgp_policy_write_respects_systemd_file_permissions():
    """The controller may update FRR's file, but cannot replace its inode."""
    script = r"""
import errno, json, os, stat, sys, time
from pathlib import Path
from types import SimpleNamespace
from nebius_vpngw.agent import frr_renderer
from nebius_vpngw.agent.strongswan_renderer import _write_secret_file
from nebius_vpngw.agent.vm_ha import tunnel_runtime
from nebius_vpngw.agent.vm_ha.tunnels import Authority, Health, TunnelController
from nebius_vpngw.ha_tunnel_policy import connection_policies
root = Path(sys.argv[1])
path = root / 'frr' / 'frr.conf'
before = path.stat()
try:
    _write_secret_file(path, 'must not replace installed configuration')
except PermissionError as error:
    assert error.errno == errno.EACCES
else:
    raise AssertionError('fixture permits directory creation outside controller authority')
assert path.read_text() == 'original configuration\n'
cfg = {'vm_ha': {'cluster_id': 'sandbox'},
       'gateway': {'local_prefixes': ['10.0.0.0/24']},
       'connections': [{'name': 'site', 'routing_mode': 'bgp',
          'remote_prefixes': ['10.1.0.0/24'], 'tunnels': [
              {'name': name, 'ha_role': role, 'remote_public_ip': f'192.0.2.{i}',
               'inner_local_ip': f'169.254.{i}.1', 'inner_remote_ip': f'169.254.{i}.2',
               'inner_cidr': f'169.254.{i}.0/30'}
              for i, (name, role) in enumerate([('preferred', 'active'), ('backup', 'passive')], 1)
          ]}]}
authority = Authority('sandbox', 'owner', 'allocation', 'epoch', 1, 'a' * 64)
state_dir = root / 'state'
state_dir.mkdir()
selector = TunnelController(cfg, state_dir / 'tunnel-selection.json',
    SimpleNamespace(revalidate=lambda value: value == authority,
                    observe=lambda *args, **kwargs: Health.HEALTHY))
selector.bind(authority)
selector.request('failover', 'backup', authority, time.time())
selector.state['selected'] = {'site': 'backup'}
selector._save()
frr_renderer.FRR_CONF = tunnel_runtime.FRR_CONF = path
effects = tunnel_runtime.NativeTunnelEffects(cfg, state_dir,
    lambda value: value == authority, active_revalidate=lambda value: value == authority, routing_lock=root / 'routing.lock')
commands = []
def command(argv):
    assert 'neighbor 169.254.2.2' in path.read_text()
    commands.append(list(argv))
    return ''
effects.run = command  # This test isolates the real sandboxed file effect.
effects.activate(connection_policies(cfg)[0], 'backup', authority)
after = path.stat()
assert (before.st_ino, before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) == (
    after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode))
assert commands == [['vtysh', '-b'],
    ['vtysh', '-c', 'clear bgp ipv4 unicast 169.254.1.2 soft'],
    ['vtysh', '-c', 'clear bgp ipv4 unicast 169.254.2.2 soft']]
print('existing FRR inode updated without directory or ownership privileges')
"""
    import grp
    import pwd

    uid, gid = pwd.getpwnam("frr").pw_uid, grp.getgrnam("frr").gr_gid
    # Keep the fixture traversable to the restricted root process.
    root = Path("/run") / f"vpngw-bgp-write-{uuid.uuid4().hex}"
    root.mkdir(mode=0o755)
    directory = root / "frr"
    directory.mkdir(mode=0o750)
    path = directory / "frr.conf"
    path.write_text("original configuration\n")
    os.chown(directory, uid, gid)
    os.chown(path, uid, gid)
    path.chmod(0o660)
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--quiet",
                "--wait",
                "--pipe",
                "--collect",
                f"--unit=vpngw-bgp-write-{uuid.uuid4().hex[:8]}",
                "--property=ProtectSystem=strict",
                "--property=NoNewPrivileges=yes",
                "--property=SupplementaryGroups=frr frrvty",
                "--property=CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW",
                "--property=AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW",
                f"--property=ReadWritePaths={root}",
                "--setenv=PYTHONPATH=/workspace/src",
                sys.executable,
                "-c",
                script,
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "without directory or ownership privileges" in result.stdout
    finally:
        import shutil

        shutil.rmtree(root)


def drive(endpoint, command, target="backup"):
    assert endpoint.process.poll() is None, "fixture charon exited"
    try:
        result = subprocess.run(
            [
                "nsenter",
                "-t",
                str(endpoint.process.pid),
                "-m",
                "-n",
                "--",
                sys.executable,
                DRIVER,
                str(endpoint.config),
                command,
                target,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"native driver {command} failed: {exc.stderr}") from exc
    return result.stdout.strip().splitlines()[-1]


def identities(endpoint):
    return {
        name: tuple(
            sorted(
                [
                    sa["uniqueid"],
                    *(str(child["uniqueid"]) for child in sa.get("child-sas", {}).values()),
                ]
            )
        )
        for name, sa in parse_sas(endpoint.command(*ipsec_command()).stdout)
        if sa.get("state") == "ESTABLISHED"
    }


def wait_until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "native fixture readiness did not converge"
        time.sleep(0.2)


@pytest.fixture(
    params=[
        "static",
        "static-nat",
        "bgp",
        "bgp-no-advertise",
        "static-mixed-owner0",
        "static-mixed-owner1",
    ]
)
def native_paths(tmp_path, request):
    assert sys.platform == "linux" and os.geteuid() == 0
    assert Path("/.dockerenv").is_file()
    mode = "static" if request.param.startswith("static") else "bgp"
    mixed_static = request.param.startswith("static-mixed-owner")
    behind_nat = request.param == "static-nat"
    advertise = request.param != "bgp-no-advertise"
    suffix = uuid.uuid4().hex[:6]
    namespaces = [f"ha-{suffix}-{i}" for i in range(3)]
    nodes = []
    logs = []
    psk = secrets.token_hex(32)
    gateway = {
        "vm_ha": {"cluster_id": "native-fixture"},
        "gateway": {"local_prefixes": ["10.200.1.1/32"]},
        "defaults": {
            "crypto": {
                "ike_proposals": ["aes256-sha256-modp2048"],
                "esp_proposals": ["aes256-sha256-modp2048"],
            },
            "routing": {
                "bgp": {
                    "hold_time_seconds": 3,
                    "keepalive_seconds": 1,
                    "graceful_restart": request.param == "bgp-graceful",
                }
            },
        },
        "connections": [],
    }
    if mixed_static:
        gateway["_fixture_owner"] = request.param.removeprefix("static-mixed-")
    if mode == "bgp" or mixed_static:
        gateway["gateway"]["local_asn"] = 65001
    for name, names, routing, destination in [
        ("site", ["preferred", "backup"], mode, "10.200.2.1/32"),
        ("other", ["unrelated"], "bgp" if mixed_static else "static", "10.201.2.1/32"),
    ]:
        connection = {
            "name": name,
            "routing_mode": routing,
            "remote_prefixes": [destination],
            "tunnels": [],
        }
        if routing == "bgp":
            connection["bgp"] = {"remote_asn": 65002, "advertise_local_prefixes": advertise}
        for tunnel_name in names:
            index = {"preferred": 1, "backup": 2, "unrelated": 3}[tunnel_name]
            connection["tunnels"].append(
                {
                    "name": tunnel_name,
                    "ha_role": "passive" if tunnel_name == "backup" else "active",
                    "local_public_ip": ("203.0.113.1" if behind_nat else "192.0.2.1")
                    if index < 3
                    else "198.51.100.1",
                    "remote_public_ip": f"192.0.2.{index + 1}" if index < 3 else "198.51.100.2",
                    "psk": psk,
                    "inner_cidr": f"169.254.{index}.0/30",
                    "inner_local_ip": f"169.254.{index}.1",
                    "inner_remote_ip": f"169.254.{index}.2",
                }
            )
        gateway["connections"].append(connection)
    if request.param == "static-probes":
        gateway["vm_ha"]["tunnel_probes"] = {
            name: {"source_ip": "10.200.1.1", "target_ip": "10.200.2.1"}
            for name in ("preferred", "backup")
        }
    configs = [gateway]
    for connection in gateway["connections"]:
        peer = {
            "gateway": {"local_prefixes": connection["remote_prefixes"]},
            "defaults": gateway["defaults"],
            "connections": [copy.deepcopy(connection)],
        }
        peer["connections"][0]["remote_prefixes"] = ["10.200.1.1/32"]
        if connection["routing_mode"] == "bgp":
            peer["gateway"]["local_asn"] = 65002
            peer["connections"][0]["bgp"] = {"remote_asn": 65001}
        peer["_fixture_remote_ts"] = ["10.200.1.1/32"]
        for tunnel in peer["connections"][0]["tunnels"]:
            tunnel["local_public_ip"], tunnel["remote_public_ip"] = (
                tunnel["remote_public_ip"],
                tunnel["local_public_ip"],
            )
            tunnel["inner_local_ip"], tunnel["inner_remote_ip"] = (
                tunnel["inner_remote_ip"],
                tunnel["inner_local_ip"],
            )
            peer["_fixture_remote_ts"].append(tunnel["inner_cidr"])
        configs.append(peer)
    if request.param == "static-single":
        # Retain the unused peer namespace but give the gateway exactly one
        # connection. Scoped suspension must also work with no other loaded
        # connections keeping swanctl's configuration nonempty.
        gateway["connections"] = gateway["connections"][:1]
    try:
        for namespace in namespaces:
            run("ip", "netns", "add", namespace)
            run("ip", "netns", "exec", namespace, "ip", "link", "set", "lo", "up")
        for peer_index, network in [(1, "192.0.2"), (2, "198.51.100")]:
            left, right = f"g{suffix}{peer_index}", f"p{suffix}{peer_index}"
            run("ip", "link", "add", left, "type", "veth", "peer", "name", right)
            for node_index, link, host in [(0, left, 1), (peer_index, right, 2)]:
                namespace = namespaces[node_index]
                run("ip", "link", "set", link, "netns", namespace)
                prefix = ("ip", "netns", "exec", namespace)
                device = f"eth{peer_index - 1}" if node_index == 0 else "eth0"
                run(*prefix, "ip", "link", "set", link, "name", device)
                run(*prefix, "ip", "address", "add", f"{network}.{host}/24", "dev", device)
                run(*prefix, "ip", "link", "set", device, "up")
        run(
            "ip",
            "netns",
            "exec",
            namespaces[1],
            "ip",
            "address",
            "add",
            "192.0.2.3/24",
            "dev",
            "eth0",
        )
        if behind_nat:
            run(
                "ip",
                "netns",
                "exec",
                namespaces[0],
                "iptables",
                "-t",
                "nat",
                "-A",
                "POSTROUTING",
                "-o",
                "eth0",
                "-s",
                "192.0.2.1",
                "-j",
                "SNAT",
                "--to-source",
                "203.0.113.1",
            )
            run(
                "ip",
                "netns",
                "exec",
                namespaces[1],
                "ip",
                "route",
                "add",
                "203.0.113.1/32",
                "via",
                "192.0.2.1",
            )
        for index, (namespace, cfg) in enumerate(zip(namespaces, configs, strict=True)):
            prefix = ("ip", "netns", "exec", namespace)
            address = cfg["gateway"]["local_prefixes"][0]
            run(*prefix, "ip", "address", "add", address, "dev", "lo")
            for endpoint in collect_tunnel_state(cfg)[2]:
                device = (
                    "eth1"
                    if index == 0 and endpoint["remote_public_ip"] == "198.51.100.2"
                    else "eth0"
                )
                interface = endpoint["name"]
                run(
                    *prefix,
                    "ip",
                    "link",
                    "add",
                    interface,
                    "type",
                    "xfrm",
                    "dev",
                    device,
                    "if_id",
                    str(endpoint["if_id"]),
                )
                run(*prefix, "ip", "link", "set", interface, "up")
                run(
                    *prefix,
                    "ip",
                    "address",
                    "add",
                    endpoint["local_inner_ip"] + "/30",
                    "dev",
                    interface,
                )
                if index == 0 and endpoint["mode"] == "static":
                    for destination in endpoint["remote_prefixes"]:
                        run(*prefix, "ip", "route", "replace", destination, "dev", interface)
            path = tmp_path / f"node-{index}.yaml"
            path.write_text(yaml.safe_dump(cfg))
            path.chmod(0o600)
            settings = tmp_path / f"charon-{index}.conf"
            settings.write_text(
                "charon {\n load_modular = yes\n reuse_ikesa = no\n install_routes = "
                + ("no" if index == 0 or cfg["gateway"].get("local_asn") else "yes")
                + """
 plugins {
  include /etc/strongswan.d/charon/*.conf
  ha { load = no }
  kernel-netlink { install_routes_xfrmi = yes }
 }
}
"""
            )
            bootstrap = """import os,subprocess,shutil
from pathlib import Path
for path in ['/run','/etc/swanctl','/etc/frr','/var/lib/nebius-vpngw']:
 Path(path).mkdir(parents=True,exist_ok=True)
 subprocess.run(['mount','-t','tmpfs','tmpfs',path],check=True)
Path('/run/frr').mkdir()
shutil.chown('/run/frr',user='frr',group='frr')
Path('/run/nebius-vpngw').mkdir()
os.execv('/usr/sbin/charon-systemd',['/usr/sbin/charon-systemd'])
"""
            log = (tmp_path / f"native-{index}.log").open("w")
            logs.append(log)
            process = subprocess.Popen(
                [
                    *prefix,
                    "unshare",
                    "--mount",
                    "--propagation",
                    "private",
                    sys.executable,
                    "-c",
                    bootstrap,
                ],
                env={**os.environ, "STRONGSWAN_CONF": str(settings)},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            node = Endpoint(
                namespace, process, path, address.split("/")[0], tmp_path / f"native-{index}.log"
            )
            nodes.append(node)
            wait_until(
                lambda process=process: (
                    Path(f"/proc/{process.pid}/exe").resolve()
                    == Path("/usr/sbin/charon-systemd").resolve()
                    and run(
                        "nsenter",
                        "-t",
                        str(process.pid),
                        "-m",
                        "-n",
                        "--",
                        "swanctl",
                        "--stats",
                        check=False,
                    ).returncode
                    == 0
                )
            )
        for node in nodes[1:]:
            drive(node, "peer")
        drive(nodes[0], "materialize")
        for node, cfg in zip(nodes, configs, strict=True):
            if cfg["gateway"].get("local_asn"):
                for daemon in ("mgmtd", "zebra", "bgpd"):
                    # FRR 10.5 children retain the launcher's output descriptors.
                    # A capture pipe waits for daemon exit even after startup
                    # succeeds; use a file while retaining the startup deadline.
                    with (tmp_path / f"{node.namespace}-{daemon}.log").open("w+") as output:
                        try:
                            subprocess.run(
                                [
                                    "nsenter",
                                    "-t",
                                    str(node.process.pid),
                                    "-m",
                                    "-n",
                                    "--",
                                    f"/usr/lib/frr/{daemon}",
                                    "-d",
                                    *([] if daemon == "mgmtd" else ["-f", "/dev/null"]),
                                ],
                                stdout=output,
                                stderr=subprocess.STDOUT,
                                check=True,
                                timeout=20,
                            )
                        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                            output.seek(0)
                            pytest.fail(f"fixture {daemon} startup failed: {output.read()[-2000:]}")
                node.command("vtysh", "-b")
        try:
            wait_until(lambda: drive(nodes[0], "health") == "healthy", seconds=25)
            if mixed_static:
                wait_until(
                    lambda: (
                        json.loads(nodes[0].command("vtysh", "-c", "show bgp summary json").stdout)[
                            "ipv4Unicast"
                        ]["peers"]["169.254.3.2"]["state"]
                        == "Established"
                    )
                )
                # Session establishment precedes receipt and installation of
                # UPDATEs. Finish fixture setup before testing selector effects.
                started = time.monotonic()

                def unrelated_routes_ready():
                    ready = []
                    for node, neighbor, destination in (
                        (nodes[0], "169.254.3.2", "10.201.2.1/32"),
                        (nodes[2], "169.254.3.1", "10.200.1.1/32"),
                    ):
                        received = json.loads(
                            node.command(
                                "vtysh",
                                "-c",
                                f"show bgp ipv4 unicast neighbors {neighbor} routes json",
                            ).stdout
                        )
                        routes = json.loads(node.command("ip", "-j", "-4", "route").stdout)
                        ready.append(
                            bool(received.get("routes", {}).get(destination))
                            and any(
                                str(ipaddress.ip_network(row["dst"])) == destination
                                and row.get("protocol") == "bgp"
                                for row in routes
                                if row.get("dst") not in {None, "default"}
                            )
                        )
                    return all(ready)

                initial = unrelated_routes_ready()
                wait_until(unrelated_routes_ready)
                (tmp_path / "unrelated-bgp-readiness.json").write_text(
                    json.dumps(
                        {
                            "routes_ready_at_established": initial,
                            "route_readiness_seconds": time.monotonic() - started,
                        }
                    )
                )
        except AssertionError:
            pytest.fail("native readiness evidence: " + drive(nodes[0], "diagnose"))
        if behind_nat:
            sas = parse_sas(nodes[0].command(*ipsec_command()).stdout)
            sa = next(sa for name, sa in sas if name == "preferred")
            assert sa["local-host"] == "192.0.2.1" and sa["local-id"] == "203.0.113.1"
        yield nodes, mode, advertise
    finally:
        for node in nodes:
            if node.process.poll() is None:
                for daemon in ("bgpd", "zebra", "mgmtd"):
                    result = run(
                        "nsenter",
                        "-t",
                        str(node.process.pid),
                        "-m",
                        "-n",
                        "--",
                        "cat",
                        f"/run/frr/{daemon}.pid",
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip().isdigit():
                        run("kill", "-TERM", result.stdout.strip(), check=False)
                os.killpg(node.process.pid, signal.SIGTERM)
                try:
                    node.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(node.process.pid, signal.SIGKILL)
                    node.process.wait(timeout=5)
        for log in logs:
            log.close()
        for namespace in namespaces:
            run("ip", "netns", "delete", namespace, check=False)


@pytest.mark.parametrize("native_paths", ["static-single"], indirect=True)
def test_prepared_static_native_health_preserves_fence_and_loader_admission(native_paths):
    gateway = native_paths[0][0]
    before = identities(gateway)
    gateway.command("sysctl", "-w", "net.ipv4.ip_forward=0")
    assert drive(gateway, "prepared-static-health") == "prepared-static-health-verified"
    assert identities(gateway) == before


@pytest.mark.parametrize("native_paths", ["static-single"], indirect=True)
def test_single_connection_static_switch_survives_empty_suspension_configuration(native_paths):
    nodes, _, _ = native_paths
    gateway, peer, _ = nodes
    assert set(identities(gateway)) == {"preferred"}
    outcome = json.loads(drive(gateway, "failover"))
    assert outcome == {"selected": {"site": "backup"}, "result": "verified", "phase": "succeeded"}
    assert set(identities(gateway)) == {"backup"}
    assert drive(gateway, "health") == "healthy"
    for source, target in [(gateway, peer), (peer, gateway)]:
        source.command("ping", "-n", "-c", "3", "-W", "1", "-I", source.address, target.address)
    assert json.loads(drive(gateway, "failback", "preferred"))["phase"] == "succeeded"
    assert set(identities(gateway)) == {"preferred"}


@pytest.mark.parametrize("native_paths", ["static", "static-nat"], indirect=True)
@pytest.mark.parametrize("operation", ["switch", "activation", "activation-policies-only"])
def test_static_switch_removes_orphans_after_real_daemon_crash(native_paths, operation):
    gateway, peer, _ = native_paths[0]

    def kernel_states():
        return xfrm_records(
            gateway.command("ip", "xfrm", "state", "list", "nokeys").stdout, policy=False
        )

    def identity(state):
        return state["src"], state["dst"], state["proto"], state["spi"]

    before = kernel_states()
    preferred = {identity(s) for s in before if int(str(s.get("if_id", 0)), 0) == 100}
    unrelated = {identity(s) for s in before if int(str(s.get("if_id", 0)), 0) == 102}
    assert preferred and unrelated
    if operation.startswith("activation"):
        # Retained selection is a fixture checkpoint, established before the
        # crash. No selector transition may pre-satisfy activation cleanup.
        assert drive(gateway, "activation-checkpoint") == "retained-backup-checkpoint"
        gateway.command("sysctl", "-w", "net.ipv4.ip_forward=0")
    old_process = gateway.process
    # Keep the fixture's private mounts alive across SIGKILL. This process owns
    # no product transition; the replacement daemon starts with an empty VICI
    # inventory, and the real selector must perform configuration and teardown.
    anchor = subprocess.Popen(
        ["nsenter", "-t", str(old_process.pid), "-m", "-n", "--", "sleep", "120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until(
            lambda: (
                Path(f"/proc/{anchor.pid}/ns/mnt").stat().st_ino
                == Path(f"/proc/{old_process.pid}/ns/mnt").stat().st_ino
            )
        )
        os.kill(old_process.pid, signal.SIGKILL)
        old_process.wait(timeout=5)
        with gateway.log.open("a") as log:
            gateway.process = subprocess.Popen(
                ["nsenter", "-t", str(anchor.pid), "-m", "-n", "--", "/usr/sbin/charon-systemd"],
                env={**os.environ, "STRONGSWAN_CONF": str(gateway.config.parent / "charon-0.conf")},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        wait_until(
            lambda: (
                run(
                    "nsenter",
                    "-t",
                    str(gateway.process.pid),
                    "-m",
                    "-n",
                    "--",
                    "swanctl",
                    "--stats",
                    check=False,
                ).returncode
                == 0
            )
        )
        assert identities(gateway) == {}
        assert preferred.issubset({identity(s) for s in kernel_states()})
        if operation == "activation-policies-only":
            # Emulate expired SAs while retaining the kernel policies seen in
            # the live failed upgrade. This is fault setup before activation.
            for src, dst, proto, spi in preferred:
                gateway.command(
                    "ip",
                    "xfrm",
                    "state",
                    "delete",
                    "src",
                    src,
                    "dst",
                    dst,
                    "proto",
                    proto,
                    "spi",
                    str(spi),
                )
        daemon_pid = gateway.process.pid
        outcome = json.loads(drive(gateway, "failover" if operation == "switch" else "activation"))
        assert outcome["phase"] == "succeeded" and outcome["selected"] == {"site": "backup"}
        after = kernel_states()
        assert not any(int(str(s.get("if_id", 0)), 0) == 100 for s in after)
        rules = xfrm_records(gateway.command("ip", "-s", "xfrm", "policy").stdout, policy=True)
        assert not any(int(str(rule.get("if_id", 0)), 0) == 100 for rule in rules)
        assert unrelated.issubset({identity(s) for s in after})
        assert gateway.process.pid == daemon_pid and gateway.process.poll() is None
        if operation == "switch":
            assert drive(gateway, "health") == "healthy"
        for source, target in ((gateway, peer), (peer, gateway)):
            source.command("ping", "-n", "-c", "3", "-W", "1", "-I", source.address, target.address)
    finally:
        if anchor.poll() is None:
            anchor.terminate()
            anchor.wait(timeout=5)


@pytest.mark.parametrize("native_paths", ["bgp"], indirect=True)
def test_unspecified_bgp_coverage_preserves_native_paths_and_rejects_selection(native_paths):
    gateway, peer, _ = native_paths[0]
    cfg = yaml.safe_load(gateway.config.read_text())
    connection = next(c for c in cfg["connections"] if c["name"] == "site")
    connection["remote_prefixes"] = None
    connection.setdefault("bgp", {})["remote_prefixes"] = None
    gateway.config.write_text(yaml.safe_dump(cfg))
    before = identities(gateway)
    assert {"preferred", "backup", "unrelated"}.issubset(before)
    assert json.loads(drive(gateway, "tick")) == {}
    assert json.loads(drive(gateway, "reconcile")) == {}
    with pytest.raises(AssertionError, match="selection is unavailable"):
        drive(gateway, "failover")
    assert identities(gateway) == before
    summary = json.loads(gateway.command("vtysh", "-c", "show bgp summary json").stdout)
    assert all(p["state"] == "Established" for p in summary["ipv4Unicast"]["peers"].values())
    for source, target in [(gateway, peer), (peer, gateway)]:
        source.command("ping", "-n", "-c", "3", "-W", "1", "-I", source.address, target.address)


@pytest.mark.parametrize("native_paths", ["static-single"], indirect=True)
def test_static_switch_tolerates_sa_disappearing_during_authority_recheck(native_paths):
    nodes, _, _ = native_paths
    gateway, peer, _ = nodes
    cfg = yaml.safe_load(gateway.config.read_text())
    cfg["_fixture_authority_delay_seconds"] = 0.4
    gateway.config.write_text(yaml.safe_dump(cfg))
    # Unloading start_action=start sends DELETE before explicit termination.
    # Let its reply arrive after SA enumeration but during authority reread.
    peer.command("tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "150ms")
    outcome = json.loads(drive(gateway, "failover"))
    assert outcome == {"selected": {"site": "backup"}, "result": "verified", "phase": "succeeded"}
    assert set(identities(gateway)) == {"backup"}
    assert drive(gateway, "health") == "healthy"


def test_native_manual_switch_preserves_unrelated_connection_and_reconciliation(native_paths):
    nodes, mode, advertise = native_paths
    gateway, peer, _ = nodes
    before = identities(gateway)
    unrelated_bgp = unrelated_bgp_evidence(nodes)
    assert "preferred" in before and "unrelated" in before
    assert ("backup" in before) == (mode == "bgp")
    unchanged = json.loads(drive(gateway, "failback", "preferred"))
    assert unchanged["phase"] == "succeeded"
    assert identities(gateway) == before
    assert unrelated_bgp_evidence(nodes) == unrelated_bgp
    outcome = json.loads(drive(gateway, "failover"))
    assert outcome == {"selected": {"site": "backup"}, "result": "verified", "phase": "succeeded"}
    assert identities(gateway)["unrelated"] == before["unrelated"]
    assert unrelated_bgp_evidence(nodes) == unrelated_bgp

    assert json.loads(drive(gateway, "reconcile")) == {"site": "backup"}
    assert drive(gateway, "health") == "healthy"
    if advertise:
        for source, target in [(gateway, peer), (peer, gateway)]:
            source.command(
                "ping",
                "-n",
                "-c",
                "5",
                "-i",
                "0.1",
                "-W",
                "1",
                "-I",
                source.address,
                target.address,
            )
    if mode == "static":
        assert "preferred" not in identities(gateway)
    else:
        assert {"preferred", "backup"}.issubset(identities(gateway))
        summary = json.loads(gateway.command("vtysh", "-c", "show bgp summary json").stdout)
        assert all(p["state"] == "Established" for p in summary["ipv4Unicast"]["peers"].values())
    outcome = json.loads(drive(gateway, "failback", "preferred"))
    assert outcome["phase"] == "succeeded"
    assert outcome["selected"] == ({"site": "preferred"} if mode == "static" else {})
    assert identities(gateway)["unrelated"] == before["unrelated"]
    assert unrelated_bgp_evidence(nodes) == unrelated_bgp
    restored = identities(gateway)
    # Even with the prior switch's episode retained, verification must neither
    # consume another window nor recreate the already preferred native SAs.
    unchanged = json.loads(drive(gateway, "failback", "preferred"))
    assert unchanged["phase"] == "succeeded"
    assert unchanged["selected"] == outcome["selected"]
    assert identities(gateway) == restored
    assert unrelated_bgp_evidence(nodes) == unrelated_bgp


def unrelated_bgp_evidence(nodes):
    """Prove Static connection effects preserve the other connection's BGP session."""
    gateway, _, peer = nodes
    cfg = yaml.safe_load(gateway.config.read_text())
    if not cfg.get("_fixture_owner"):
        return None
    neighbor = json.loads(
        gateway.command("vtysh", "-c", "show bgp neighbors 169.254.3.2 json").stdout
    )["169.254.3.2"]
    assert neighbor["bgpState"] == "Established"
    received = json.loads(
        gateway.command(
            "vtysh", "-c", "show bgp ipv4 unicast neighbors 169.254.3.2 routes json"
        ).stdout
    )
    assert received["routes"]["10.201.2.1/32"]
    advertised = json.loads(
        gateway.command(
            "vtysh", "-c", "show bgp ipv4 unicast neighbors 169.254.3.2 advertised-routes json"
        ).stdout
    )
    assert set(advertised["advertisedRoutes"]) == {"10.200.1.1/32"}
    for source, target in ((gateway, peer), (peer, gateway)):
        source.command(
            "ping", "-n", "-c", "5", "-i", "0.1", "-W", "1", "-I", source.address, target.address
        )
    return neighbor["connectionsEstablished"], neighbor["connectionsDropped"]


def isolate_path(gateway, tunnel, *, enabled):
    interface = "xfrm0" if tunnel == "preferred" else "xfrm1"
    gateway.command("ip", "link", "set", interface, "down" if enabled else "up")


@pytest.mark.parametrize("native_paths", ["static"], indirect=True)
def test_native_late_static_loss_uses_tunnel_episode_before_vm_repair(native_paths):
    gateway, peer, _ = native_paths[0]
    before = identities(gateway)["unrelated"]
    result = json.loads(drive(gateway, "recover-owner"))
    assert result["phase"] == "succeeded"
    assert result["selected"] == {"site": "backup"}
    assert drive(gateway, "health") == "healthy"
    assert identities(gateway)["unrelated"] == before
    for source, target in ((gateway, peer), (peer, gateway)):
        source.command(
            "ping", "-n", "-c", "5", "-i", "0.1", "-W", "1", "-I", source.address, target.address
        )


def test_native_path_failure_recovers_without_disturbing_other_connection(native_paths):
    nodes, mode, advertise = native_paths
    gateway, peer, _ = nodes
    if mode == "bgp":
        assert json.loads(drive(gateway, "failover"))["phase"] == "succeeded"
    before = identities(gateway)["unrelated"]
    unrelated_bgp = unrelated_bgp_evidence(nodes)
    failed = "preferred" if mode == "static" else "backup"
    isolate_path(gateway, failed, enabled=True)
    try:
        result = json.loads(drive(gateway, "recover"))
        assert result["phase"] == "succeeded"
        assert result["selected"] == ({"site": "backup"} if mode == "static" else {})
        assert drive(gateway, "health") == "healthy"
        assert identities(gateway)["unrelated"] == before
        assert unrelated_bgp_evidence(nodes) == unrelated_bgp
        if advertise:
            for source, target in ((gateway, peer), (peer, gateway)):
                source.command(
                    "ping",
                    "-n",
                    "-c",
                    "5",
                    "-i",
                    "0.1",
                    "-W",
                    "1",
                    "-I",
                    source.address,
                    target.address,
                )
    finally:
        isolate_path(gateway, failed, enabled=False)
    # Recovery does not automatically fail back a working Static selection.
    if mode == "static":
        assert json.loads(drive(gateway, "tick")) == {"site": "backup"}


@pytest.mark.parametrize("native_paths", ["static"], indirect=True)
def test_static_recovery_does_not_wait_for_unresponsive_ike_peer(native_paths):
    gateway = native_paths[0][0]
    before = identities(gateway)["unrelated"]
    # The former peer cannot acknowledge DELETE. Only local activation is the
    # oracle here; remote return-route convergence depends on that peer's DPD.
    gateway.command("iptables", "-I", "OUTPUT", "-d", "192.0.2.2", "-j", "DROP")
    isolate_path(gateway, "preferred", enabled=True)
    outcome = json.loads(drive(gateway, "recover"))
    assert outcome["phase"] == "succeeded"
    assert outcome["selected"] == {"site": "backup"}
    assert drive(gateway, "health") == "healthy"
    assert identities(gateway)["unrelated"] == before


def test_failed_manual_target_restores_or_preserves_working_path(native_paths):
    gateway = native_paths[0][0]
    mode = native_paths[1]
    before = identities(gateway)
    unrelated_bgp = unrelated_bgp_evidence(native_paths[0])
    isolate_path(gateway, "backup", enabled=True)
    if mode == "static":
        outcome = json.loads(drive(gateway, "failover"))
        assert outcome == {
            "phase": "failed",
            "result": "previous-path-restored",
            "selected": {"site": "preferred"},
        }
        assert drive(gateway, "health") == "healthy"
    else:
        with pytest.raises(AssertionError, match="manual BGP target health is not proven"):
            drive(gateway, "failover")
        assert identities(gateway)["preferred"] == before["preferred"]
    assert identities(gateway)["unrelated"] == before["unrelated"]
    assert unrelated_bgp_evidence(native_paths[0]) == unrelated_bgp


@pytest.mark.parametrize("native_paths", ["static-probes"], indirect=True)
def test_static_probe_loss_switches_with_live_ike_and_persisted_debounce(native_paths):
    gateway, peer, _ = native_paths[0]
    before = identities(gateway)
    # Only protected ICMP arriving through the preferred path is lost. IKE and
    # XFRM remain established, and the alternate can carry the same destination.
    peer.command("iptables", "-I", "INPUT", "-i", "xfrm0", "-p", "icmp", "-j", "DROP")
    started = time.monotonic()
    outcome = json.loads(drive(gateway, "recover"))
    assert time.monotonic() - started >= 10
    assert outcome["phase"] == "succeeded"
    assert outcome["selected"] == {"site": "backup"}
    assert identities(gateway)["unrelated"] == before["unrelated"]
    assert drive(gateway, "health") == "healthy"


@pytest.mark.parametrize("native_paths", ["bgp", "bgp-no-advertise"], indirect=True)
@pytest.mark.parametrize("scenario", ["activate", "verify", "release", "failed-selected"])
def test_owner_bgp_restart_replays_only_after_fenced_activation(native_paths, scenario):
    gateway, _, _ = native_paths[0]
    try:
        result = subprocess.run(
            [
                "nsenter",
                "-t",
                str(gateway.process.pid),
                "-m",
                "-n",
                "--",
                "env",
                "PYTHONPATH=/workspace/src:/workspace",
                sys.executable,
                "/workspace/tests/systemd/ha_bgp_restart_driver.py",
                str(gateway.config),
                scenario,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as error:
        pytest.fail(f"Restart driver failed:\n{error.stdout[-3500:]}\n{error.stderr[-3500:]}")
    row = json.loads(result.stdout.strip().splitlines()[-1])
    assert row["active"]
    assert row["phase"] == ("failed" if scenario in {"release", "failed-selected"} else "succeeded")


@pytest.mark.parametrize("native_paths", ["bgp", "bgp-no-advertise"], indirect=True)
def test_owner_bgp_sa_loss_retains_native_alternative_coverage(native_paths):
    gateway, peer, _ = native_paths[0]
    before = identities(gateway)
    try:
        result = gateway.command(
            "env",
            "PYTHONPATH=/workspace/src:/workspace",
            sys.executable,
            "/workspace/tests/systemd/ha_bgp_owner_driver.py",
            str(gateway.config),
        )
    except subprocess.CalledProcessError as error:
        pytest.fail(
            f"Owner coverage driver failed:\n{error.stdout[-4000:]}\n{error.stderr[-4000:]}"
        )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert [row["label"] for row in rows] == [
        "baseline",
        "failed-preferred-sa",
        "native-alternative-recovery",
        "backup-health-verification",
    ]
    assert rows[1]["receipt"] and rows[1]["digest_same"]
    assert all(row["action"] is None for row in rows)
    assert all(row["readiness"] == [] and row["action"] is None for row in rows[2:])
    after = identities(gateway)
    assert after["backup"] == before["backup"]
    assert after["unrelated"] == before["unrelated"]


@pytest.mark.parametrize("native_paths", ["bgp-graceful"], indirect=True)
def test_retained_graceful_bgp_route_does_not_prove_coverage(native_paths):
    gateway, _, _ = native_paths[0]
    before = identities(gateway)
    result = gateway.command(
        "env",
        "PYTHONPATH=/workspace/src:/workspace",
        sys.executable,
        "/workspace/tests/systemd/ha_bgp_owner_driver.py",
        str(gateway.config),
        "stale",
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert rows[-1]["label"] == "retained-route-not-coverage"
    assert "bgp-not-ready" in rows[-1]["readiness"]
    assert all(row["action"] is None for row in rows)
    after = identities(gateway)
    assert after["backup"] == before["backup"]
    assert after["unrelated"] == before["unrelated"]


def test_recovery_guard_fences_a_never_started_controller(tmp_path):
    """A missing ExecStopPost transition must not leave bootstrap forwarding enabled."""
    script = r"""
import json, tempfile
from pathlib import Path
from nebius_vpngw.agent.main import install_vm_ha_cold_start_guard
root = Path(tempfile.mkdtemp(prefix='vpngw-recovery-guard-'))
forward = Path('/proc/sys/net/ipv4/ip_forward')
forward.write_text('1')
assert forward.read_text().strip() == '1'
assert not (root / 'status.json').exists()
result = install_vm_ha_cold_start_guard(state_dir=root)
assert forward.read_text().strip() == '0'
assert result['data_plane_mode'] == 'blocked'
assert result['guard_boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert json.loads((root / 'guard.json').read_text()) == result
print('fresh recovery host is fenced')
"""
    result = subprocess.run(
        [
            "systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            f"--unit=vpngw-recovery-guard-{uuid.uuid4().hex[:8]}",
            "--property=PrivateNetwork=yes",
            "--property=PrivateTmp=yes",
            "--setenv=PYTHONPATH=/workspace/src",
            sys.executable,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fresh recovery host is fenced" in result.stdout


def test_persistent_ha_policy_survives_real_sysctl_reapplication():
    """Real sysctl and guard writers, in disposable mount/network namespaces.

    This is causal writer coverage, not a continuously observed kernel boot.
    """
    script = r"""
import subprocess, tempfile
from pathlib import Path
from nebius_vpngw.boot_policy import install_ha_policy, BootPolicyError
from nebius_vpngw.agent.main import install_vm_ha_cold_start_guard
from nebius_vpngw.agent.xfrm_manager import XFRMManager
forward = Path('/proc/sys/net/ipv4/ip_forward')
forward.write_text('0')
Path('/etc/sysctl.d').mkdir(parents=True)
Path('/etc/sysctl.conf').write_text('# no unmanaged forwarding writer\n')
Path('/etc/sysctl.d/99-zzz-vpngw.conf').write_text('net.ipv4.ip_forward = 1\n')
Path('/etc/sysctl.d/99-vpn-gateway.conf').write_text('net.ipv4.ip_forward = 1\n')
install_ha_policy()
for _ in range(2):
    subprocess.run(['sysctl', '--system'], check=True, capture_output=True)
    assert forward.read_text().strip() == '0'
XFRMManager().setup_interfaces([], parent_dev='lo', persist_sysctl=False)
assert forward.read_text().strip() == '0'
guard = install_vm_ha_cold_start_guard(state_dir=Path(tempfile.mkdtemp()))
assert guard['data_plane_mode'] == 'blocked' and forward.read_text().strip() == '0'
conflict = Path('/etc/sysctl.d/zzzz-admin.conf')
conflict.write_text('net.ipv4.conf.all.forwarding = 1\n')
try:
    install_ha_policy()
except BootPolicyError as error:
    assert str(error) == 'ha-boot-policy-unmanaged-forwarding-conflict'
else:
    raise AssertionError('late unmanaged writer was accepted')
assert conflict.read_text() == 'net.ipv4.conf.all.forwarding = 1\n'
assert forward.read_text().strip() == '0'
print('persistent HA policy and runtime writers keep the candidate fenced')
"""
    result = subprocess.run(
        [
            "systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            f"--unit=vpngw-boot-policy-{uuid.uuid4().hex[:8]}",
            "--property=PrivateNetwork=yes",
            "--property=PrivateTmp=yes",
            "--property=TemporaryFileSystem=/etc /run/sysctl.d /usr/lib/sysctl.d /usr/local/lib/sysctl.d",
            "--setenv=PYTHONPATH=/workspace/src",
            sys.executable,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime writers keep the candidate fenced" in result.stdout
