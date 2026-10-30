#!/usr/bin/env python3
"""Owned IPv4 gateway rules. Verification never modifies the guest."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shlex
import subprocess
import sys
from pathlib import Path

import nat_security
from nat_security import BAN_CHAIN, security_status, valid_config

CONFIG = Path("/etc/nebius-nat.json")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=20)


def rules(config: dict, interface: str) -> list[tuple[str, str, list[str]]]:
    result = [
        ("filter", "CXCLI_NAT_INPUT", ["-i", "lo", "-j", "ACCEPT"]),
        (
            "filter",
            "CXCLI_NAT_INPUT",
            ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ),
        ("filter", "CXCLI_NAT_INPUT", ["-p", "icmp", "-j", "ACCEPT"]),
        (
            "filter",
            "CXCLI_NAT_INPUT",
            ["-p", "udp", "--sport", "67", "--dport", "68", "-j", "ACCEPT"],
        ),
        ("filter", "CXCLI_NAT_FORWARD", ["-d", "169.254.0.0/16", "-j", "DROP"]),
        (
            "filter",
            "CXCLI_NAT_FORWARD",
            ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ),
    ]
    result.extend(
        [
            ("filter", "CXCLI_NAT_INPUT", ["-p", "tcp", "--dport", "22", "-j", BAN_CHAIN]),
            ("filter", "CXCLI_NAT_INPUT", ["-p", "tcp", "--dport", "22", "-j", "ACCEPT"]),
        ]
    )
    for cidr in config["source_cidrs"]:
        result.append(
            ("filter", "CXCLI_NAT_FORWARD", ["-s", cidr, "-o", interface, "-j", "ACCEPT"])
        )
        result.append(
            ("nat", "CXCLI_NAT_POSTROUTING", ["-s", cidr, "-o", interface, "-j", "MASQUERADE"])
        )
    result.extend(
        [
            ("filter", "CXCLI_NAT_INPUT", ["-j", "DROP"]),
            ("filter", "CXCLI_NAT_FORWARD", ["-j", "DROP"]),
        ]
    )
    return result


def normalized_rule(tokens: list[str]) -> list[str]:
    result = []
    index = 0
    while index < len(tokens):
        # iptables prints protocol modules even when they were inferred on input.
        if tokens[index : index + 2] in (["-m", "tcp"], ["-m", "udp"]):
            index += 2
            continue
        token = tokens[index]
        if index and tokens[index - 1] == "--ctstate":
            token = ",".join(sorted(token.split(",")))
        result.append(token)
        index += 1
    return result


def exact_rules(desired: list, hooks: list) -> bool:
    bans = run("iptables", "-w", "5", "-S", BAN_CHAIN, check=False)
    if bans.returncode:
        return False
    for line in bans.stdout.splitlines():
        if not line.startswith("-A "):
            continue
        tokens = shlex.split(line)
        if (
            len(tokens) != 6
            or tokens[:3] != ["-A", BAN_CHAIN, "-s"]
            or tokens[4:] != ["-j", "DROP"]
        ):
            return False
        try:
            if ipaddress.IPv4Network(tokens[3], strict=True).prefixlen != 32:
                return False
        except ValueError:
            return False
    for table, parent, chain in hooks:
        actual = run("iptables", "-w", "5", "-t", table, "-S", chain, check=False)
        if actual.returncode:
            return False
        actual_rules = [
            normalized_rule(shlex.split(line)[2:])
            for line in actual.stdout.splitlines()
            if line.startswith("-A ")
        ]
        expected = [normalized_rule(args) for t, c, args in desired if t == table and c == chain]
        if actual_rules != expected:
            return False
        parent_rules = run("iptables", "-w", "5", "-t", table, "-S", parent).stdout.splitlines()
        appended = [shlex.split(line) for line in parent_rules if line.startswith("-A ")]
        if not appended or appended[0] != ["-A", parent, "-j", chain]:
            return False
    return True


def main() -> int:
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action not in {"configure", "status"}:
        raise ValueError("usage: nebius-nat configure|status")
    content = CONFIG.read_bytes().strip()
    config = json.loads(content)
    if not valid_config(config) or "admin_cidrs" in config:
        raise ValueError("Invalid NAT security profile")
    for key, path in (
        ("guest_program_sha256", Path(__file__)),
        ("security_program_sha256", Path(nat_security.__file__)),
    ):
        if config.get(key) != hashlib.sha256(path.read_bytes().strip()).hexdigest():
            raise ValueError("NAT installed program differs from Terraform intent")
    for key in ("source_cidrs",):
        if not config.get(key):
            raise ValueError("Empty NAT source policy")
        for cidr in config[key]:
            network = ipaddress.IPv4Network(cidr, strict=True)
            if network.prefixlen == 0:
                raise ValueError("Unrestricted NAT source policy")
    routes = json.loads(run("ip", "-j", "-4", "route", "show", "default").stdout)
    interfaces = {row["dev"] for row in routes if row.get("dev")}
    if len(interfaces) != 1:
        raise ValueError("Cannot resolve one gateway egress interface")
    interface = interfaces.pop()
    desired = rules(config, interface)
    hooks = [
        ("filter", "INPUT", "CXCLI_NAT_INPUT"),
        ("filter", "FORWARD", "CXCLI_NAT_FORWARD"),
        ("nat", "POSTROUTING", "CXCLI_NAT_POSTROUTING"),
    ]
    if action == "configure":
        # Fail2ban owns entries only. Never flush live bans on NAT restart.
        if run("iptables", "-w", "5", "-S", BAN_CHAIN, check=False).returncode:
            run("iptables", "-w", "5", "-N", BAN_CHAIN)
        run("sysctl", "-w", "net.ipv4.ip_forward=1")
        for table, _, chain in hooks:
            run("iptables", "-w", "5", "-t", table, "-N", chain, check=False)
            run("iptables", "-w", "5", "-t", table, "-F", chain)
        for table, chain, args in desired:
            run("iptables", "-w", "5", "-t", table, "-A", chain, *args)
        for table, parent, chain in hooks:
            if run(
                "iptables", "-w", "5", "-t", table, "-C", parent, "-j", chain, check=False
            ).returncode:
                run("iptables", "-w", "5", "-t", table, "-I", parent, "1", "-j", chain)
    forwarding = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    valid = exact_rules(desired, hooks)
    security = security_status(config) if action == "status" else {}
    print(
        json.dumps(
            {
                "schema": 2,
                "config_sha256": hashlib.sha256(content).hexdigest(),
                "interface": interface,
                "forwarding": forwarding,
                "rules_valid": valid,
                **security,
            }
        )
    )
    return 0 if forwarding and valid and all(security.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
