"""NAT-owned SSH policy and read-only effective security checks (Ubuntu 24.04)."""

from __future__ import annotations

import json
import pwd
import re
import subprocess
import sys
import time
from pathlib import Path

BAN_CHAIN = "CXCLI_NAT_SSH_BANS"
PROFILE = "key-only-fail2ban-v1"
CONFIG = Path("/etc/nebius-nat.json")
SSH_CONFIG = """# Managed by nebius-cxcli NAT
Port 22
AddressFamily inet
ListenAddress 0.0.0.0
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
UsePAM yes
AllowUsers {user}
X11Forwarding no
AllowTcpForwarding yes
GatewayPorts no
PermitTunnel no
MaxAuthTries 3
LogLevel VERBOSE
"""
JAIL = """[sshd]
enabled = true
backend = systemd
filter = sshd[publickey=any]
port = 22
maxretry = 3
findtime = 600
bantime = 3600
usedns = no
action = nebius-nat
"""
ACTION_COMMANDS = {
    "actionstart": f"iptables -w 5 -S {BAN_CHAIN}",
    "actioncheck": f"iptables -w 5 -S {BAN_CHAIN}",
    "actionban": f"iptables -w 5 -I {BAN_CHAIN} 1 -s <ip> -j DROP",
    "actionunban": f"iptables -w 5 -D {BAN_CHAIN} -s <ip> -j DROP",
    "actionflush": f"iptables -w 5 -F {BAN_CHAIN}",
    "actionstop": f"iptables -w 5 -F {BAN_CHAIN}",
}
ACTION = (
    "[Definition]\n"
    + "\n".join(f"{key} = {value}" for key, value in ACTION_COMMANDS.items())
    + "\n[Init]\nactionstart_on_demand = false\n"
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)


def valid_config(config: dict) -> bool:
    return (
        config.get("security_profile") == PROFILE
        and isinstance(config.get("ssh_user_name"), str)
        and config["ssh_user_name"] != "root"
        and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", config["ssh_user_name"]) is not None
        and isinstance(config.get("ssh_public_key"), str)
        and len(config["ssh_public_key"].split()) >= 2
        and "\n" not in config["ssh_public_key"].strip()
    )


def policy_valid(config: dict) -> bool:
    if not valid_config(config):
        return False
    result = run(
        "/usr/sbin/sshd",
        "-T",
        "-C",
        f"user={config['ssh_user_name']},host=localhost,addr=192.0.2.1",
    )
    if result.returncode:
        return False
    actual = dict(line.split(None, 1) for line in result.stdout.splitlines() if " " in line)
    expected = {
        "port": "22",
        "addressfamily": "inet",
        "permitrootlogin": "no",
        "pubkeyauthentication": "yes",
        "authenticationmethods": "publickey",
        "passwordauthentication": "no",
        "kbdinteractiveauthentication": "no",
        "permitemptypasswords": "no",
        "usepam": "yes",
        "allowusers": config["ssh_user_name"],
        "x11forwarding": "no",
        "gatewayports": "no",
        "permittunnel": "no",
        "maxauthtries": "3",
        "loglevel": "VERBOSE",
    }
    if any(actual.get(key) != value for key, value in expected.items()):
        return False
    user = pwd.getpwnam(config["ssh_user_name"])
    keys = Path(user.pw_dir) / ".ssh/authorized_keys"
    lines = [
        line.split()[:2]
        for line in keys.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return lines == [config["ssh_public_key"].split()[:2]]


def listener_ready() -> bool:
    ipv4 = run("ss", "-H", "-4", "-lntp", "sport = :22")
    ipv6 = run("ss", "-H", "-6", "-lnt", "sport = :22")
    return (
        ipv4.returncode == ipv6.returncode == 0
        and "0.0.0.0:22" in ipv4.stdout
        and ("sshd" in ipv4.stdout or "systemd" in ipv4.stdout)
        and not ipv6.stdout.strip()
    )


def ban_service_ready() -> bool:
    if run("systemctl", "is-active", "--quiet", "fail2ban.service").returncode:
        return False
    status = run("fail2ban-client", "status", "sshd")
    if status.returncode or "Journal matches:" not in status.stdout:
        return False
    for key, value in (("maxretry", "3"), ("findtime", "600"), ("bantime", "3600")):
        result = run("fail2ban-client", "get", "sshd", key)
        if result.returncode or result.stdout.strip() != value:
            return False
    for key, expected in ACTION_COMMANDS.items():
        result = run("fail2ban-client", "get", "sshd", "action", "nebius-nat", key)
        if result.returncode or result.stdout.strip() != expected:
            return False
    return Path("/etc/fail2ban/jail.d/99-nebius-nat.local").read_text() == JAIL


def security_status(config: dict) -> dict[str, bool]:
    result = {}
    for key, check in (
        ("ssh_policy_valid", lambda: policy_valid(config)),
        ("ssh_listener_ready", listener_ready),
        ("ssh_ban_service_ready", ban_service_ready),
    ):
        try:
            result[key] = check()
        except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
            result[key] = False
    return result


def checked(*args: str) -> None:
    if run(*args).returncode:
        raise RuntimeError(f"NAT security bootstrap failed: {args[0]}")


def bootstrap(config: dict) -> None:
    if not valid_config(config):
        raise ValueError("Invalid NAT SSH security profile")
    files = {
        "/etc/ssh/sshd_config.d/00-nebius-nat.conf": SSH_CONFIG.format(
            user=config["ssh_user_name"]
        ),
        "/etc/systemd/system/ssh.socket.d/10-nebius-nat.conf": "[Socket]\nListenStream=\nListenStream=0.0.0.0:22\n",
        "/etc/fail2ban/jail.d/99-nebius-nat.local": JAIL,
        "/etc/fail2ban/action.d/nebius-nat.conf": ACTION,
        "/etc/systemd/system/fail2ban.service.d/10-nebius-nat.conf": "[Unit]\nWants=nebius-nat.service\nAfter=nebius-nat.service\n",
    }
    for name, content in files.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o644)
    Path("/run/sshd").mkdir(mode=0o755, exist_ok=True)
    checked("/usr/sbin/sshd", "-t")
    if not policy_valid(config):
        raise RuntimeError("Effective NAT SSH policy does not match required protection")
    checked("systemctl", "daemon-reload")
    socket_model = any(
        run("systemctl", verb, "--quiet", "ssh.socket").returncode == 0
        for verb in ("is-active", "is-enabled")
    )
    if socket_model:
        checked("systemctl", "stop", "ssh.service")
        checked("systemctl", "restart", "ssh.socket")
    else:
        checked("systemctl", "restart", "ssh.service")
    checked("systemctl", "is-active", "--quiet", "nebius-nat.service")
    checked("iptables", "-w", "5", "-S", BAN_CHAIN)
    checked("systemctl", "unmask", "fail2ban.service")
    checked("systemctl", "enable", "--now", "fail2ban.service")
    deadline = time.monotonic() + 30
    while not all(security_status(config).values()):
        if time.monotonic() >= deadline:
            raise RuntimeError("NAT SSH/fail2ban readiness did not converge")
        time.sleep(0.2)


if __name__ == "__main__":
    if sys.argv[1:] != ["bootstrap"]:
        raise SystemExit("usage: nat_security.py bootstrap")
    bootstrap(json.loads(CONFIG.read_text()))
