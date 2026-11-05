"""Real SSH and VPN-port admission in isolated Linux network namespaces.

Uses real UFW/conntrack/nftables and OpenSSH. Veth models public/VPC/tunnel
interfaces; decrypted ESP proof requires a real XFRM-capable validation target.
"""

from __future__ import annotations

import os
import select
import shlex
import subprocess
import time
from pathlib import Path

import pytest
from test_tunnel_host_packets import activate_interface, ns, reconcile, run
from test_tunnel_host_packets import network as network

from nebius_vpngw import ingress_policy
from nebius_vpngw.agent import local_commands

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires isolated Linux fixture",
    ),
]


def test_packaged_firewall_permits_parse_with_native_ufw():
    """Validate the shipped shell rules with UFW, without changing live rules."""
    script = (
        Path(__file__).parents[2] / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    ).read_text()
    rules = [
        shlex.split(line)
        for line in script.splitlines()
        if line.startswith("ufw allow ") and "$" not in line
    ]
    assert rules and any("esp" in rule for rule in rules)
    for rule in rules:
        result = subprocess.run(
            ["ufw", "--dry-run", *rule[1:]], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, (rule, result.stderr)


@pytest.fixture
def ingress(network, tmp_path, monkeypatch):
    router, peer, public = network
    activate_interface(router)
    for namespace, interface, address in (
        (router, "xfrm0", "2001:db8:1::1"),
        (
            peer,
            ns(peer, "ip", "-o", "link", "show")
            .stdout.splitlines()[-1]
            .split(": ")[1]
            .split("@")[0],
            "2001:db8:1::2",
        ),
    ):
        ns(namespace, "ip", "-6", "addr", "add", address + "/64", "dev", interface, "nodad")
    ns(router, "ufw", "default", "allow", "outgoing")
    ns(router, "ufw", "allow", "22/tcp")
    ns(router, "ufw", "allow", "4500/udp")
    ns(router, "ufw", "allow", "from", "10.0.0.0/8")
    reconcile(router)
    monkeypatch.setattr(ingress_policy, "LOCK_FILE", tmp_path / "ingress.lock")
    monkeypatch.setattr(ingress_policy, "POLICY_FILE", tmp_path / "ingress.json")

    def runner(argv, **kwargs):
        return subprocess.run(["ip", "netns", "exec", router, *argv], **kwargs)

    def apply(peers=()):
        policy = {"peer_ips": list(peers)}
        ingress_policy.install(policy, run=runner)
        ingress_policy.verify(policy, run=runner)
        return policy

    return router, peer, public, apply, runner


@pytest.fixture
def ssh_server(ingress, tmp_path):
    router, peer, public, apply, runner = ingress
    host, key = tmp_path / "host", tmp_path / "client"
    for path in (host, key):
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path))
    known = tmp_path / "known_hosts"
    known.write_text("10.1.0.1,10.2.0.1,2001:db8:1::1 " + host.with_suffix(".pub").read_text())
    Path("/run/sshd").mkdir(exist_ok=True)
    config = tmp_path / "sshd_config"
    config.write_text(
        f"HostKey {host}\nAuthorizedKeysFile {key}.pub\nPidFile {tmp_path}/pid\n"
        "Port 22\nListenAddress 0.0.0.0\nListenAddress ::\nPasswordAuthentication no\n"
        "PermitRootLogin yes\nStrictModes no\nUsePAM no\nLogLevel ERROR\n"
    )
    server = subprocess.Popen(
        ["ip", "netns", "exec", router, "/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    try:
        while not ns(router, "ss", "-H", "-lnt", "sport", "=", ":22").stdout:
            assert server.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)

        def command(namespace=peer, address="10.1.0.1", remote="true"):
            return [
                "ip",
                "netns",
                "exec",
                namespace,
                "ssh",
                "-T",
                "-i",
                str(key),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=1",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={known}",
                f"root@{address}",
                remote,
            ]

        yield command
    finally:
        server.terminate()
        server.communicate(timeout=5)


def test_missing_table_installs_through_ordinary_command_accounting(ingress):
    router, _peer, _public, _apply, runner = ingress
    desired = {"peer_ips": []}
    budget = local_commands.CommandBudget(time.monotonic() + 30)
    token = local_commands.CURRENT.set(budget)
    try:
        ingress_policy.install(
            desired,
            run=lambda argv, **kwargs: local_commands.run(
                ["ip", "netns", "exec", router, *argv], **kwargs
            ),
        )
        budget.require_success()
    finally:
        local_commands.CURRENT.reset(token)
    assert budget.failures == []
    ingress_policy.verify(desired, run=runner)


def test_real_ssh_all_paths_ipv6_reload_and_peer_changes(ingress, ssh_server):
    router, peer, public, apply, runner = ingress
    for peers in (["10.1.0.2"], []):
        desired = apply(peers)
        for namespace, address in (
            (peer, "10.1.0.1"),
            (peer, "2001:db8:1::1"),
            (public, "10.2.0.1"),
        ):
            assert (
                subprocess.run(
                    ssh_server(namespace, address), capture_output=True, timeout=5
                ).returncode
                == 0
            )
        ns(router, "ufw", "reload")
        reconcile(router)
        ingress_policy.verify(desired, run=runner)


def test_removed_vpn_peer_is_denied_despite_established_udp_flow(ingress):
    router, peer, _public, apply, runner = ingress
    server_code = "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('10.1.0.1',4500));\nwhile True:\n data,a=s.recvfrom(1024);s.sendto(data,a)"
    server = subprocess.Popen(["ip", "netns", "exec", router, "python3", "-u", "-c", server_code])
    client_code = "import socket,sys;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1);s.connect(('10.1.0.1',4500));\nfor line in sys.stdin:\n s.send(line.encode())\n try: print(s.recv(1024).decode().strip(),flush=True)\n except TimeoutError: print('blocked',flush=True)"
    client = subprocess.Popen(
        ["ip", "netns", "exec", peer, "python3", "-u", "-c", client_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ns(router, "ss", "-H", "-lnu", "sport", "=", ":4500").stdout:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        apply(["10.1.0.2"])
        assert client.stdin is not None and client.stdout is not None
        client.stdin.write("permitted\n")
        client.stdin.flush()
        assert select.select([client.stdout], [], [], 3)[0]
        assert client.stdout.readline() == "permitted\n"
        empty = apply()
        client.stdin.write("removed\n")
        client.stdin.flush()
        assert select.select([client.stdout], [], [], 3)[0]
        assert client.stdout.readline() == "blocked\n"
        ingress_policy.verify(empty, run=runner)
    finally:
        client.terminate()
        server.terminate()
        client.communicate(timeout=5)
        server.communicate(timeout=5)


def test_boot_entrypoint_restores_persisted_policy_and_preserves_customer_rules(
    ingress, ssh_server
):
    router, peer, public, apply, runner = ingress
    ns(router, "nft", "add", "table", "inet", "customer_fixture")
    before = ns(router, "nft", "-j", "list", "table", "inet", "customer_fixture").stdout
    desired = apply()
    ufw_before = ns(router, "ufw", "status").stdout
    # Simulate an empty kernel table after a reboot, then execute the boot entrypoint
    # from the persisted policy without access to config.yaml or the CLI host.
    ns(router, "nft", "delete", "table", "inet", ingress_policy.TABLE)
    code = (
        "import sys;from pathlib import Path;from nebius_vpngw import ingress_policy as p;"
        "p.POLICY_FILE=Path(sys.argv[1]);p.LOCK_FILE=Path(sys.argv[2]);"
        "sys.argv=['ingress','apply'];p.main()"
    )
    ns(
        router,
        "/opt/vpngw-test/bin/python3",
        "-c",
        code,
        str(ingress_policy.POLICY_FILE),
        str(ingress_policy.LOCK_FILE),
    )
    ingress_policy.verify(desired, run=runner)
    assert ns(router, "nft", "-j", "list", "table", "inet", "customer_fixture").stdout == before
    assert ns(router, "ufw", "status").stdout == ufw_before
    assert subprocess.run(ssh_server(peer), capture_output=True, timeout=5).returncode == 0
    assert (
        subprocess.run(ssh_server(public, "10.2.0.1"), capture_output=True, timeout=5).returncode
        == 0
    )


def test_customer_ssh_denial_remains_effective(ingress, ssh_server):
    router, peer, public, apply, runner = ingress

    def connects(namespace=peer, address="10.1.0.1"):
        return (
            subprocess.run(
                ssh_server(namespace, address), capture_output=True, timeout=5
            ).returncode
            == 0
        )

    desired = apply()
    assert connects() and connects(public, "10.2.0.1") and connects(address="2001:db8:1::1")

    # Product-owned VPN rules never override a customer's SSH denial.
    ns(
        router,
        "ufw",
        "insert",
        "1",
        "deny",
        "from",
        "10.2.0.2",
        "to",
        "any",
        "port",
        "22",
        "proto",
        "tcp",
    )
    assert not connects(public, "10.2.0.1")
    assert connects() and connects(address="2001:db8:1::1")
    ns(router, "ufw", "reload")
    reconcile(router)
    ingress_policy.verify(desired, run=runner)
    assert not connects(public, "10.2.0.1")
    assert connects() and connects(address="2001:db8:1::1")
