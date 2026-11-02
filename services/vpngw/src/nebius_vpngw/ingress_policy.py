"""Mandatory host ingress policy, also executed standalone during cloud-init.

Only the named nftables table belongs to this product. Its early INPUT hook
restricts admission; a RETURN still leaves UFW and other customer filters in force.
Keep this module stdlib-only so bootstrap and old-agent upgrades use identical code.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

CAPABILITY = "managed-ingress-v1"
TABLE = "nebius_vpngw_ingress"
POLICY_FILE = Path("/etc/nebius-vpngw/ingress-policy.json")
LOCK_FILE = Path("/run/nebius-vpngw/ingress.lock")
EXECUTABLE = Path("/usr/local/lib/nebius-vpngw-ingress.py")
FIELD = "gateway.ssh_allowed_cidrs"


class IngressPolicyError(ValueError):
    """Safe configuration migration diagnostic; contains no input values."""


class IngressPolicyDrift(RuntimeError):
    """Successful native observation proves absent or different owned rules."""


class IngressCapabilityError(RuntimeError):
    """Selected replacement artifact cannot implement mandatory ingress."""


def normalize_cidrs(value: Any) -> list[str]:
    """No omission/empty/unresolved fallback, including callers bypassing Pydantic."""
    error = (
        f"{FIELD} must be a nonempty list of IPv4/IPv6 source CIDRs. "
        "Add administrator and deployment SSH sources to the YAML, then run "
        "validate-config and apply from an included source. No gateway changes were made "
        "by configuration validation. See README: SSH allowlist migration."
    )
    if not isinstance(value, list) or not value:
        raise IngressPolicyError(error)
    networks = set()
    try:
        for item in value:
            if not isinstance(item, str) or "/" not in item or "%" in item:
                raise ValueError
            networks.add(ipaddress.ip_network(item.strip(), strict=False))
    except ValueError:
        raise IngressPolicyError(error) from None
    return [
        str(n)
        for n in sorted(networks, key=lambda n: (n.version, int(n.network_address), n.prefixlen))
    ]


def from_config(config: dict[str, Any]) -> dict[str, Any]:
    gateway = config.get("gateway") if isinstance(config, dict) else None
    cidrs = normalize_cidrs(gateway.get("ssh_allowed_cidrs") if isinstance(gateway, dict) else None)
    peers = set()
    for connection in config.get("connections", []):
        for tunnel in connection.get("tunnels", []):
            address = tunnel.get("remote_public_ip")
            try:
                if not isinstance(address, str) or "%" in address:
                    raise ValueError
                peers.add(str(ipaddress.ip_address(address)))
            except ValueError:
                raise ValueError(
                    "VPN ingress requires a resolved remote_public_ip per tunnel"
                ) from None
    return {"ssh_allowed_cidrs": cidrs, "peer_ips": sorted(peers)}


def validate(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != {"ssh_allowed_cidrs", "peer_ips"}:
        raise ValueError("Invalid managed ingress policy")
    peers = policy["peer_ips"]
    if not isinstance(peers, list) or any(not isinstance(p, str) or "%" in p for p in peers):
        raise ValueError("Invalid managed ingress peer addresses")
    return {
        "ssh_allowed_cidrs": normalize_cidrs(policy["ssh_allowed_cidrs"]),
        "peer_ips": sorted({str(ipaddress.ip_address(p)) for p in peers}),
    }


def serialized(policy: dict[str, Any]) -> str:
    return json.dumps(validate(policy), sort_keys=True, separators=(",", ":")) + "\n"


def identity(policy: dict[str, Any]) -> str:
    return hashlib.sha256(serialized(policy).encode()).hexdigest()


def require_source(policy: dict[str, Any], connection: str) -> str:
    """Consume sshd's four-field SSH_CONNECTION, never an inferred public address."""
    try:
        source, source_port, destination, destination_port = connection.split()
        address = ipaddress.ip_address(source)
        ipaddress.ip_address(destination)
        if not all(0 < int(p) < 65536 for p in (source_port, destination_port)):
            raise ValueError
    except (ValueError, AttributeError):
        raise RuntimeError("Cannot verify the authenticated server-observed SSH source") from None
    if not any(address in ipaddress.ip_network(c) for c in validate(policy)["ssh_allowed_cidrs"]):
        raise RuntimeError(
            f"SSH source {address} is excluded by {FIELD}; policy was not applied. "
            "Add the current and new sources, apply, connect from the new source, "
            "then remove the old source and apply again."
        )
    return str(address)


def _match(left: dict[str, Any], right: Any, op: str = "==") -> dict[str, Any]:
    return {"match": {"op": op, "left": left, "right": right}}


def _payload(protocol: str, field: str, value: Any) -> dict[str, Any]:
    return _match({"payload": {"protocol": protocol, "field": field}}, value)


def objects(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Exact native JSON objects; used for both installation and kernel proof."""
    policy = validate(policy)
    base = {"family": "inet", "table": TABLE}
    result: list[dict[str, Any]] = [
        {"table": {"family": "inet", "name": TABLE}},
        {
            "chain": {
                **base,
                "name": "ingress",
                "type": "filter",
                "hook": "input",
                "prio": -10,
                "policy": "accept",
            }
        },
    ]

    def rule(matches: list[dict[str, Any]], verdict: str = "return") -> None:
        result.append({"rule": {**base, "chain": "ingress", "expr": [*matches, {verdict: None}]}})

    ssh = _payload("tcp", "dport", 22)
    ike = _payload("udp", "dport", {"set": [500, 4500]})
    esp = _match({"meta": {"key": "l4proto"}}, "esp")
    rule([_match({"meta": {"key": "iifname"}}, "lo")])
    rule([ssh, _match({"ct": {"key": "state"}}, "established", "in")])
    for cidr in policy["ssh_allowed_cidrs"]:
        net = ipaddress.ip_network(cidr)
        value: Any = str(net.network_address)
        if net.prefixlen != net.max_prefixlen:
            value = {"prefix": {"addr": value, "len": net.prefixlen}}
        rule([_payload("ip" if net.version == 4 else "ip6", "saddr", value), ssh])
    rule([ssh], "drop")
    # Only outer VPN admission is bypassed by authenticated decrypted payload.
    rule([_match({"meta": {"key": "ipsec"}}, True)])
    for peer in policy["peer_ips"]:
        source = _payload("ip" if ipaddress.ip_address(peer).version == 4 else "ip6", "saddr", peer)
        rule([source, ike])
        rule([source, esp])
    rule([ike], "drop")
    rule([esp], "drop")
    return result


def transaction(policy: dict[str, Any]) -> str:
    expected = objects(policy)
    # 'add table' is idempotent; delete/recreate belongs to ONE netlink transaction.
    return json.dumps(
        {
            "nftables": [
                {"add": expected[0]},
                {"delete": expected[0]},
                *({"add": o} for o in expected),
            ]
        }
    )


def _native_objects(argv: list[str], run: Callable[..., Any]) -> list[dict[str, Any]]:
    result = run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Managed ingress kernel policy is unreadable")
    try:
        items = json.loads(result.stdout)["nftables"]
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or len(item) != 1
            or not isinstance(next(iter(item.values())), dict)
            for item in items
        ):
            raise ValueError
        return [
            {key: {k: v for k, v in obj.items() if k != "handle"}}
            for item in items
            for key, obj in item.items()
            if key != "metainfo"
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError("Managed ingress kernel policy observation is malformed") from None


def verify(policy: dict[str, Any], *, run: Callable[..., Any] = subprocess.run) -> None:
    # Listing tables succeeds for an empty ruleset. A missing owned table is an
    # expected observation, not a failed command in ordinary reconciliation's
    # bounded child-process ledger. Never suppress actual command failures.
    tables = _native_objects(["nft", "-j", "list", "tables", "inet"], run)
    if any(
        set(item) != {"table"}
        or item["table"].get("family") != "inet"
        or not isinstance(item["table"].get("name"), str)
        for item in tables
    ):
        raise RuntimeError("Managed ingress table inventory is malformed")
    if not any(item["table"]["name"] == TABLE for item in tables):
        raise IngressPolicyDrift("Managed ingress kernel policy is missing")
    actual = _native_objects(["nft", "-j", "list", "table", "inet", TABLE], run)
    if actual != objects(policy):
        raise IngressPolicyDrift("Managed ingress kernel policy differs from the desired policy")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ingress-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def install(
    policy: dict[str, Any], *, run: Callable[..., Any] = subprocess.run, persist: bool = True
) -> None:
    policy = validate(policy)
    payload = transaction(policy)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a") as lock:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Timed out locking managed ingress policy") from None
                time.sleep(0.05)
        try:
            verify(policy, run=run)
        except IngressPolicyDrift:
            pass
        else:
            if persist and (
                not POLICY_FILE.exists() or POLICY_FILE.read_text() != serialized(policy)
            ):
                _write(POLICY_FILE, serialized(policy))
            return
        for check in (True, False):
            # After native validation, persist approved intent before activation.
            # A crash must never restore an older, more permissive policy on boot.
            if not check and persist:
                _write(POLICY_FILE, serialized(policy))
            command = ["nft", "-j", *(["--check"] if check else []), "-f", "-"]
            result = run(command, input=payload, capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise RuntimeError(
                    "Managed ingress validation failed"
                    if check
                    else "Managed ingress activation failed; inspect effective policy before retrying"
                )
        verify(policy, run=run)


def streamed_bootstrap() -> str:
    import base64

    raw = base64.b64encode(Path(__file__).read_bytes()).decode()
    return (
        "import base64,sys,types;_m=types.ModuleType('_vpngw_ingress');"
        "sys.modules[_m.__name__]=_m;"
        f"exec(compile(base64.b64decode({raw!r}),'<ingress>','exec'),_m.__dict__);"
    )


INGRESS_SOURCES = (
    "ingress_policy.py",
    "schema.py",
    "config_loader.py",
    "agent/firewall_manager.py",
    "agent/ordinary.py",
    "agent/main.py",
    "systemd/setup-vpngw-firewall.sh",
    "systemd/nebius-vpngw-ingress.service",
    "systemd/nebius-vpngw-ingress.py",
    "systemd/nebius-vpngw-ssh-ingress.conf",
    "systemd/nebius-vpngw-vm-ha.service",
)


def require_wheel(archive: Any) -> None:
    # Bind implementation and boot/runtime wiring, not an untrusted feature string.
    for source in INGRESS_SOURCES:
        local = (Path(__file__).parent / source).read_bytes()
        try:
            member = archive.getinfo("nebius_vpngw/" + source)
            if member.file_size == len(local) and archive.read(member) == local:
                continue
        except KeyError:
            pass
        raise IngressCapabilityError(
            "Selected agent wheel does not implement this CLI's mandatory managed ingress "
            "policy; build or select the matching wheel with VPNGW_AGENT_WHEEL before applying"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "verify"))
    args = parser.parse_args()
    policy = validate(json.loads(POLICY_FILE.read_text()))
    if args.action == "apply":
        install(policy, persist=False)
    else:
        verify(policy)
        print(identity(policy))


if __name__ == "__main__":
    main()
