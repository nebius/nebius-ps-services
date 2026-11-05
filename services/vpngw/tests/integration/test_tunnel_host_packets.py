"""Real UFW host-policy regressions, only inside the disposable systemd fixture.

Veth links model tunnel host interfaces; these tests do not claim ESP/IPsec proof.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit isolated systemd fixture",
    ),
]

CONFIG = {"connections": [{"routing_mode": "bgp", "tunnels": [{"remote_public_ip": "192.0.2.1"}]}]}


def test_ufw_boot_transaction_preserves_early_firewall_ordering(tmp_path):
    """Use the real vendor units; verifier exit status alone misses deleted jobs."""
    from nebius_vpngw.deploy.ordinary_apply import PREVIOUS_UFW_ORDERING

    assert Path("/.dockerenv").is_file()
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    root = tmp_path / "units"
    override = root / "ufw.service.d/override.conf"
    override.parent.mkdir(parents=True)
    # A cloud guest enables networkd wait-online. Model that enablement in a
    # private unit search path without touching the running manager or firewall.
    wants = root / "network-online.target.wants"
    wants.mkdir()
    wait_online = Path("/usr/lib/systemd/system/systemd-networkd-wait-online.service")
    assert wait_online.is_file()
    (wants / wait_online.name).symlink_to(wait_online)
    env = dict(os.environ, SYSTEMD_UNIT_PATH=str(root) + ":", LC_ALL="C")

    def verify(payload):
        override.write_bytes(payload)
        result = subprocess.run(
            ["systemd-analyze", "verify", "ufw.service", "network-online.target"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout + result.stderr

    _code, negative = verify(PREVIOUS_UFW_ORDERING)
    assert "ordering cycle" in negative.lower(), negative
    canonical = (
        Path(__file__).parents[2] / "src/nebius_vpngw/systemd/nebius-vpngw-ufw-ordering.conf"
    )
    code, positive = verify(canonical.read_bytes())
    assert code == 0, positive
    assert "ordering cycle" not in positive.lower(), positive
    assert "deleted to break" not in positive.lower(), positive


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=20)


def ns(namespace: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("ip", "netns", "exec", namespace, *args, check=check)


def reconcile(router: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # The whole product helper shares UFW's real files and lock in this disposable
    # container. No command translation or saved-policy substitutes are involved.
    return ns(
        router,
        sys.executable,
        "-B",
        "-c",
        "import json,sys; from nebius_vpngw.agent.firewall_manager import "
        "ensure_tunnel_host_rules; ensure_tunnel_host_rules(json.loads(sys.argv[1]))",
        json.dumps(CONFIG),
        check=check,
    )


@pytest.fixture
def network():
    assert sys.platform == "linux" and os.geteuid() == 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    assert Path("/.dockerenv").is_file()
    for command in ("ip", "iptables", "iptables-save", "iptables-restore", "ufw", "sysctl"):
        assert shutil.which(command), f"required fixture command unavailable: {command}"
    suffix = uuid.uuid4().hex[:7]
    router, peer, public = [f"fw-{suffix}-{name}" for name in ("r", "p", "e")]
    namespaces = (router, peer, public)
    try:
        for namespace in namespaces:
            run("ip", "netns", "add", namespace)
            ns(namespace, "ip", "link", "set", "lo", "up")
        for index, (endpoint, interface, subnet) in enumerate(
            ((peer, "pending0", "10.1.0"), (public, "eth0", "10.2.0"))
        ):
            router_link, endpoint_link = f"r{suffix}{index}", f"e{suffix}{index}"
            run("ip", "link", "add", router_link, "type", "veth", "peer", "name", endpoint_link)
            run("ip", "link", "set", router_link, "netns", router)
            run("ip", "link", "set", endpoint_link, "netns", endpoint)
            ns(router, "ip", "link", "set", router_link, "name", interface)
            ns(router, "ip", "addr", "add", f"{subnet}.1/24", "dev", interface)
            ns(router, "ip", "link", "set", interface, "up")
            ns(endpoint, "ip", "addr", "add", f"{subnet}.2/24", "dev", endpoint_link)
            ns(endpoint, "ip", "link", "set", endpoint_link, "up")
        # Real saved policy is container-local and reset serially between cases.
        # UFW names backups with one-second precision and refuses to overwrite
        # them. Fast consecutive cases must reserve a new timestamp first.
        deadline = time.monotonic() + 2
        while list(Path("/etc/ufw").glob(f"*.rules.{time.strftime('%Y%m%d_%H%M%S')}")):
            assert time.monotonic() < deadline, "UFW fixture backup clock did not advance"
            time.sleep(0.05)
        reset = ns(router, "ufw", "--force", "reset", check=False)
        assert reset.returncode == 0, reset.stderr
        ns(router, "ufw", "default", "deny", "incoming")
        ns(router, "ufw", "default", "deny", "outgoing")
        ns(router, "ufw", "--force", "enable")
        yield router, peer, public
    finally:
        if ns(router, "ip", "link", "show", check=False).returncode == 0:
            ns(router, "ufw", "--force", "disable", check=False)
        for namespace in namespaces:
            run("ip", "netns", "del", namespace, check=False)


def activate_interface(router: str) -> None:
    ns(router, "ip", "link", "set", "pending0", "down")
    ns(router, "ip", "link", "set", "pending0", "name", "xfrm0")
    ns(router, "ip", "link", "set", "xfrm0", "up")


def connection(server_ns: str, client_ns: str, address: str, *, allowed: bool) -> None:
    server = subprocess.Popen(
        [
            "ip",
            "netns",
            "exec",
            server_ns,
            sys.executable,
            "-u",
            "-c",
            "import socket\n"
            "server = socket.socket()\n"
            "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"server.bind(({address!r}, 179))\n"
            "server.listen(1)\nprint('ready', flush=True)\n"
            "connection, _ = server.accept()\nconnection.close()\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        assert select.select([server.stdout], [], [], 5)[0]
        assert server.stdout.readline().strip() == "ready"
        client = f"import socket; socket.create_connection(({address!r}, 179), timeout=1).close()"
        result = ns(client_ns, sys.executable, "-c", client, check=False)
        assert (result.returncode == 0) is allowed, result.stderr
        if allowed:
            assert server.wait(timeout=5) == 0
    finally:
        if server.poll() is None:
            server.kill()
        server.communicate(timeout=5)


def policy(router: str, table: str = "filter") -> list[str]:
    return [
        " ".join(line.split()[:2]) if line.startswith(":") else line
        for line in ns(router, "iptables-save", "-t", table).stdout.splitlines()
        if line.startswith(("-A ", ":"))
    ]


def test_configured_host_rules_admit_bgp_after_late_interface_creation(network):
    router, peer, public = network
    assert ns(router, "ip", "link", "show", "dev", "xfrm0", check=False).returncode != 0
    reconcile(router)
    connection(router, peer, "10.1.0.1", allowed=False)
    activate_interface(router)
    connection(router, peer, "10.1.0.1", allowed=True)
    connection(peer, router, "10.1.0.2", allowed=True)
    connection(router, public, "10.2.0.1", allowed=False)
    before = policy(router)
    saved = Path("/etc/ufw/user.rules").read_bytes()
    reconcile(router)
    assert policy(router) == before
    assert Path("/etc/ufw/user.rules").read_bytes() == saved


@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize("forwarding", ["0", "1"])
def test_saved_ufw_permit_repairs_kernel_loss_without_policy_reset(network, direction, forwarding):
    router, peer, public = network
    activate_interface(router)
    # Other customer saved rules include protocol matches that iptables-save
    # canonicalizes differently. The native restore must preserve their semantics.
    ns(router, "ufw", "allow", "in", "on", "eth0", "proto", "tcp", "to", "any", "port", "22")
    ns(
        router,
        "ufw",
        "allow",
        "in",
        "on",
        "eth0",
        "proto",
        "tcp",
        "from",
        "10.2.0.2",
        "to",
        "any",
        "port",
        "9443",
    )
    reconcile(router)
    flag = "-i" if direction == "input" else "-o"
    ufw_direction = "in" if direction == "input" else "out"
    ns(
        router,
        "ufw",
        "deny",
        ufw_direction,
        "on",
        "xfrm0",
        "proto",
        "tcp",
        "to",
        "any",
        "port",
        "179",
    )
    # Unrelated chains/hooks, forwarding rules and MSS must survive exactly.
    ns(router, "iptables", "-N", "fixture-unrelated")
    ns(router, "iptables", "-A", "INPUT", "-j", "fixture-unrelated")
    ns(router, "iptables", "-A", "FORWARD", "-i", "eth0", "-j", "DROP")
    ns(
        router,
        "iptables",
        "-t",
        "mangle",
        "-A",
        "FORWARD",
        "-p",
        "tcp",
        "--tcp-flags",
        "SYN,RST",
        "SYN",
        "-j",
        "TCPMSS",
        "--clamp-mss-to-pmtu",
    )
    ns(router, "sysctl", "-q", "-w", f"net.ipv4.ip_forward={forwarding}")
    before = policy(router)
    mangle = policy(router, "mangle")
    saved = Path("/etc/ufw/user.rules").read_bytes()
    ns(router, "iptables", "-D", f"ufw-user-{direction}", flag, "xfrm0", "-j", "ACCEPT")
    if direction == "input":
        connection(router, peer, "10.1.0.1", allowed=False)
    else:
        connection(peer, router, "10.1.0.2", allowed=False)
    reconcile(router)
    assert policy(router) == before
    assert policy(router, "mangle") == mangle
    assert Path("/etc/ufw/user.rules").read_bytes() == saved
    assert ns(router, "sysctl", "-n", "net.ipv4.ip_forward").stdout.strip() == forwarding
    connection(router, peer, "10.1.0.1", allowed=True)
    connection(peer, router, "10.1.0.2", allowed=True)
    connection(router, public, "10.2.0.1", allowed=False)


def test_invalid_saved_dependency_fails_before_any_restore(network):
    router, _, _ = network
    reconcile(router)
    ns(router, "iptables", "-D", "ufw-user-input", "-i", "xfrm0", "-j", "ACCEPT")
    before = policy(router)
    path = Path("/etc/ufw/user.rules")
    path.write_text(
        path.read_text().replace("COMMIT", "-A ufw-user-input -j fixture-missing\nCOMMIT")
    )
    assert reconcile(router, check=False).returncode != 0
    assert policy(router) == before


def test_saved_deny_before_permit_retains_precedence(network):
    router, peer, _ = network
    activate_interface(router)
    ns(router, "ufw", "deny", "in", "on", "xfrm0", "proto", "tcp", "to", "any", "port", "179")
    reconcile(router)
    before = policy(router)
    ns(router, "iptables", "-D", "ufw-user-input", "-i", "xfrm0", "-j", "ACCEPT")
    reconcile(router)
    assert policy(router) == before
    connection(router, peer, "10.1.0.1", allowed=False)
    assert any(
        shlex.split(line)[1:4] == ["ufw-user-input", "-i", "xfrm0"]
        for line in before
        if line.startswith("-A ")
    )
