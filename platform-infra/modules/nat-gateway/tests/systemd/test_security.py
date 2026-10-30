"""Real rendered bootstrap, SSH and banning; only inside an explicit disposable fixture."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NAT_ISOLATED_SYSTEMD_TEST") != "1", reason="requires disposable systemd fixture"
)


def command(*args: str, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def eventually(check, seconds=15):
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() >= deadline:
            pytest.fail("Runtime postcondition did not converge")
        time.sleep(0.2)


@pytest.fixture(params=["ssh.service", "ssh.socket"])
def gateway(request):
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    cloud = json.loads(Path("/fixture/cloud.json").read_text())
    for service in ("fail2ban.service", "ssh.socket", "ssh.service", "nebius-nat.service"):
        command("systemctl", "disable", "--now", service, check=False)
    command("useradd", "-m", "-s", "/bin/bash", "natadmin", check=False)
    # Cloud-init's user/key setup, using the actual rendered public key.
    user = cloud["users"][0]
    keys = Path("/home/natadmin/.ssh/authorized_keys")
    keys.parent.mkdir(mode=0o700, exist_ok=True)
    keys.write_text(user["ssh_authorized_keys"][0] + "\n")
    keys.chmod(0o600)
    command("chown", "-R", "natadmin:natadmin", str(keys.parent))
    command("usermod", "-p", "*", "natadmin")
    # Even a root-authorized key must not allow root login.
    root_keys = Path("/root/.ssh/authorized_keys")
    root_keys.parent.mkdir(mode=0o700, exist_ok=True)
    root_keys.write_text(keys.read_text())
    root_keys.chmod(0o600)
    for file in cloud["write_files"]:
        path = Path(file["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file["content"])
        path.chmod(int(file["permissions"], 8))
    command("ssh-keygen", "-A")
    command("systemctl", "enable", request.param)
    # Run the actual one-time early mask and rendered runcmd in their declared order.
    for cmd in cloud["bootcmd"]:
        command(*cmd)
    for cmd in cloud["runcmd"]:
        command(*cmd)
    command("fail2ban-client", "unban", "--all")
    command("ip", "netns", "add", "nat-client")
    command("ip", "link", "add", "nat-host", "type", "veth", "peer", "name", "nat-peer")
    command("ip", "link", "set", "nat-peer", "netns", "nat-client")
    command("ip", "addr", "add", "192.0.2.1/24", "dev", "nat-host")
    command("ip", "link", "set", "nat-host", "up")
    command("ip", "-n", "nat-client", "addr", "add", "192.0.2.2/24", "dev", "nat-peer")
    command("ip", "-n", "nat-client", "addr", "add", "192.0.2.3/24", "dev", "nat-peer")
    command("ip", "-n", "nat-client", "link", "set", "nat-peer", "up")
    host_key = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()[:2]
    Path("/tmp/known_hosts").write_text("192.0.2.1 " + " ".join(host_key) + "\n")
    yield cloud
    command("ip", "link", "del", "nat-host", check=False)
    command("ip", "netns", "del", "nat-client", check=False)


def ssh(user="natadmin", key="/tmp/client-key", source="192.0.2.2", method="publickey"):
    return command(
        "ip",
        "netns",
        "exec",
        "nat-client",
        "ssh",
        "-F",
        "/dev/null",
        "-b",
        source,
        "-i",
        key,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=2",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=/tmp/known_hosts",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "PreferredAuthentications=" + method,
        user + "@192.0.2.1",
        "true",
        check=False,
    )


def ready():
    report = json.loads(command("/usr/local/sbin/nebius-nat", "status").stdout)
    assert report["schema"] == 2
    assert all(
        report[k]
        for k in (
            "forwarding",
            "rules_valid",
            "ssh_policy_valid",
            "ssh_listener_ready",
            "ssh_ban_service_ready",
        )
    )


def test_rendered_security_and_ban_lifecycle(gateway):
    ready()
    assert ssh().returncode == 0
    assert ssh(user="root", source="192.0.2.3").returncode != 0
    assert "publickey" in ssh(method="password", source="192.0.2.3").stderr
    assert not command("ss", "-H", "-6", "-lnt", "sport = :22").stdout.strip()
    if not Path("/tmp/wrong-key").exists():
        command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "/tmp/wrong-key")
    for _ in range(3):
        assert ssh(key="/tmp/wrong-key").returncode != 0
    eventually(lambda: "192.0.2.2" in command("fail2ban-client", "get", "sshd", "banip").stdout)
    assert ssh().returncode != 0
    ready()  # Dynamic bans do not invalidate exact NAT readiness.
    command("systemctl", "restart", "nebius-nat.service")
    assert ssh().returncode != 0
    ready()
    command("systemctl", "restart", "fail2ban.service")
    eventually(lambda: "192.0.2.2" in command("iptables", "-S", "CXCLI_NAT_SSH_BANS").stdout)
    assert ssh().returncode != 0
    command("fail2ban-client", "set", "sshd", "unbanip", "192.0.2.2")
    assert ssh().returncode == 0
    ready()
    # cloud-init bootcmd repeats on reboot; the once guard must not remask fail2ban.
    for cmd in gateway["bootcmd"]:
        command(*cmd)
    assert command("systemctl", "is-enabled", "fail2ban.service").stdout.strip() == "enabled"
    # A short fixture-only ban expires without touching the stable NAT hook.
    command("fail2ban-client", "set", "sshd", "bantime", "1")
    command("fail2ban-client", "set", "sshd", "banip", "192.0.2.2")
    eventually(lambda: "192.0.2.2" not in command("iptables", "-S", "CXCLI_NAT_SSH_BANS").stdout)
    command("fail2ban-client", "set", "sshd", "bantime", "3600")
    assert ssh().returncode == 0
    ready()
    # An unexpected allow inside the ban chain invalidates readiness.
    command("iptables", "-I", "CXCLI_NAT_SSH_BANS", "-j", "ACCEPT")
    assert command("/usr/local/sbin/nebius-nat", "status", check=False).returncode != 0
    command("iptables", "-D", "CXCLI_NAT_SSH_BANS", "-j", "ACCEPT")
    command("systemctl", "stop", "fail2ban.service")
    assert command("/usr/local/sbin/nebius-nat", "status", check=False).returncode != 0


def test_exact_identity_with_many_agent_keys(gateway, monkeypatch):
    spec = importlib.util.spec_from_file_location("nat_identity", "/identity.py")
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    agent = command("ssh-agent", "-s").stdout
    for variable in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
        monkeypatch.setenv(variable, re.search(variable + r"=([^;]+);", agent)[1])
    try:
        for i in range(4):
            key = f"/tmp/unrelated-key-{i}"
            if not Path(key).exists():
                command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
            command("ssh-add", key)
        command("ssh-add", "/tmp/client-key")
        public = gateway["users"][0]["ssh_authorized_keys"][0]
        with identity.ssh_identity_options(public) as options:
            result = command(
                "ip",
                "netns",
                "exec",
                "nat-client",
                "ssh",
                *options,
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=/tmp/known_hosts",
                "-o",
                "GlobalKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=2",
                "natadmin@192.0.2.1",
                "true",
                check=False,
            )
            assert result.returncode == 0, result.stderr
        assert "192.0.2.2" not in command("fail2ban-client", "get", "sshd", "banip").stdout
        ready()
    finally:
        command("ssh-agent", "-k")


def test_invalid_effective_ssh_policy_blocks_bootstrap(gateway):
    invalid = Path("/etc/ssh/sshd_config.d/01-invalid.conf")
    try:
        command("systemctl", "stop", "fail2ban.service")
        command("systemctl", "mask", "fail2ban.service")
        invalid.write_text("InvalidSshOption yes\n")
        assert (
            command(
                "python3", "/usr/local/sbin/nat_security.py", "bootstrap", check=False
            ).returncode
            != 0
        )
        assert (
            command("systemctl", "is-active", "--quiet", "fail2ban.service", check=False).returncode
            != 0
        )
        assert command("/usr/local/sbin/nebius-nat", "status", check=False).returncode != 0
    finally:
        invalid.unlink()
        command("systemctl", "unmask", "fail2ban.service")


def test_stale_installed_programs_fail_readiness(gateway):
    for name in ("nebius-nat", "nat_security.py"):
        path = Path("/usr/local/sbin") / name
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n# stale installed revision\n")
            result = command("/usr/local/sbin/nebius-nat", "status", check=False)
            assert result.returncode != 0
            assert "installed program differs" in result.stderr
        finally:
            path.write_bytes(original)
        ready()
