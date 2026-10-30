"""Verify host BGP access before tunnel interface creation on Linux."""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import uuid

import pytest

from nebius_vpngw.agent import firewall_manager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux"
        or os.geteuid() != 0
        or any(
            shutil.which(command) is None
            for command in ("ip", "iptables", "iptables-save", "ping", "sysctl")
        ),
        reason="requires Linux root, iproute2, iptables, and ping",
    ),
]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=15)


def ns(namespace: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("ip", "netns", "exec", namespace, *args, check=check)


@pytest.fixture
def network():
    suffix = uuid.uuid4().hex[:7]
    router, left, right, local = [f"fw-{suffix}-{name}" for name in ("r", "a", "b", "c")]
    namespaces = (router, left, right, local)
    try:
        for namespace in namespaces:
            run("ip", "netns", "add", namespace)
            ns(namespace, "ip", "link", "set", "lo", "up")
        for index, (endpoint, interface, subnet) in enumerate(
            ((left, "xfrm0", "10.1.0"), (right, "xfrm1", "10.2.0"), (local, "ethvpc", "10.0.0"))
        ):
            router_link = f"r{suffix}{index}"
            endpoint_link = f"e{suffix}{index}"
            run("ip", "link", "add", router_link, "type", "veth", "peer", "name", endpoint_link)
            run("ip", "link", "set", router_link, "netns", router)
            run("ip", "link", "set", endpoint_link, "netns", endpoint)
            ns(router, "ip", "link", "set", router_link, "name", interface)
            ns(router, "ip", "addr", "add", f"{subnet}.1/24", "dev", interface)
            ns(router, "ip", "link", "set", interface, "up")
            ns(endpoint, "ip", "addr", "add", f"{subnet}.2/24", "dev", endpoint_link)
            ns(endpoint, "ip", "link", "set", endpoint_link, "up")
            ns(endpoint, "ip", "route", "add", "default", "via", f"{subnet}.1")
        ns(left, "ip", "addr", "add", "10.9.0.2/32", "dev", f"e{suffix}0")
        ns(router, "ip", "route", "add", "10.9.0.2/32", "dev", "xfrm0")
        ns(router, "sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
        yield router, left, right, local
    finally:
        for namespace in namespaces:
            run("ip", "netns", "del", namespace, check=False)



def test_configured_host_rules_admit_bgp_after_late_interface_creation(network, monkeypatch):
    router, left, _, _ = network
    ns(router, "ip", "link", "set", "xfrm0", "down")
    ns(router, "ip", "link", "set", "xfrm0", "name", "pending0")
    ns(router, "ip", "link", "set", "pending0", "up")
    ns(router, "iptables", "-P", "INPUT", "DROP")
    ns(router, "iptables", "-N", "ufw-user-input")
    ns(router, "iptables", "-N", "ufw-user-output")
    ns(router, "iptables", "-A", "INPUT", "-j", "ufw-user-input")
    assert ns(router, "ip", "link", "show", "dev", "xfrm0", check=False).returncode != 0

    def apply_rule(arguments, **kwargs):
        if arguments[0] == "iptables-save":
            return ns(router, *arguments)
        assert arguments[:2] == ["ufw", "allow"]
        direction, _, interface = arguments[2:]
        chain, match = ("input", "-i") if direction == "in" else ("output", "-o")
        return ns(router, "iptables", "-A", f"ufw-user-{chain}", match, interface, "-j", "ACCEPT")

    # Translate UFW's interface rule into its kernel form in the isolated namespace.
    # Running UFW itself would write shared host files outside the namespace.
    monkeypatch.setattr(firewall_manager, "run", apply_rule)
    cfg = {"connections": [{"routing_mode": "bgp", "tunnels": [{"remote_public_ip": "192.0.2.1"}]}]}
    firewall_manager.ensure_tunnel_host_rules(cfg)

    server = subprocess.Popen(
        [
            "ip",
            "netns",
            "exec",
            router,
            sys.executable,
            "-u",
            "-c",
            "import socket\n"
            "server = socket.socket()\n"
            "server.bind(('10.1.0.1', 179))\n"
            "server.listen(1)\n"
            "print('ready', flush=True)\n"
            "connection, address = server.accept()\n"
            "connection.close()\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        assert select.select([server.stdout], [], [], 5)[0]
        assert server.stdout.readline().strip() == "ready"
        client = "import socket\nsocket.create_connection(('10.1.0.1', 179), timeout=1).close()"
        assert ns(left, sys.executable, "-c", client, check=False).returncode != 0
        ns(router, "ip", "link", "set", "pending0", "down")
        ns(router, "ip", "link", "set", "pending0", "name", "xfrm0")
        ns(router, "ip", "link", "set", "xfrm0", "up")
        assert ns(left, sys.executable, "-c", client).returncode == 0
        assert server.wait(timeout=5) == 0
        firewall_manager.verify_tunnel_host_rules(cfg)
    finally:
        if server.poll() is None:
            server.kill()
        server.communicate(timeout=5)
