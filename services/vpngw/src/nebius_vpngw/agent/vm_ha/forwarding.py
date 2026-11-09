"""Persistent, drop-only destination protection owned by the HA dataplane.

Withdrawal is not retirement. The admitted ledger survives process/kernel
restart; only an authoritative configuration retirement may remove intent.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...ha_tunnel_policy import ConnectionPolicy
from .store import atomic_write_json

TABLE = "nebius_vpngw_forward"
SCHEMA = "nebius-vpngw.destination-protection.v1"


def network(value: str) -> str:
    return str(ipaddress.IPv4Network(value, strict=True))


def objects(destinations: dict[str, tuple[str, ...]], local: tuple[str, ...]) -> list[dict]:
    base = {"family": "inet", "table": TABLE}
    result: list[dict] = [
        {"table": {"family": "inet", "name": TABLE}},
        {
            "chain": {
                **base,
                "name": "forward",
                "type": "filter",
                "hook": "forward",
                "prio": -10,
                "policy": "accept",
            }
        },
    ]

    def rule(prefix: str, verdict: str, interfaces: tuple[str, ...] = ()) -> None:
        net = ipaddress.IPv4Network(network(prefix))
        expr: list[dict] = [
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "ip", "field": "daddr"}},
                    "right": str(net.network_address)
                    if net.prefixlen == 32
                    else {"prefix": {"addr": str(net.network_address), "len": net.prefixlen}},
                }
            }
        ]
        if interfaces:
            if any(not re.fullmatch(r"xfrm[0-9]+", name) for name in interfaces):
                raise ValueError("invalid protected interface")
            expr.append(
                {
                    "match": {
                        "op": "!=",
                        "left": {"meta": {"key": "oifname"}},
                        "right": interfaces[0]
                        if len(interfaces) == 1
                        else {"set": sorted(interfaces)},
                    }
                }
            )
        result.append({"rule": {**base, "chain": "forward", "expr": [*expr, {verdict: None}]}})

    # RETURN neither accepts traffic nor bypasses later customer filter chains.
    if set(local) & destinations.keys():
        raise ValueError("local and VPN destination ownership is ambiguous")
    for prefix in sorted(
        set(local) | destinations.keys(), key=lambda p: (-ipaddress.IPv4Network(p).prefixlen, p)
    ):
        if prefix in destinations:
            rule(prefix, "drop", destinations[prefix])
        rule(prefix, "return")  # Longest local or VPN destination ownership wins.
    return result


class DestinationProtection:
    def __init__(
        self,
        path: Path,
        *,
        cluster: str,
        policies: tuple[ConnectionPolicy, ...],
        local_prefixes: tuple[str, ...],
        run: Any = subprocess.run,
    ) -> None:
        self.path, self.cluster, self.policies, self.run = path, cluster, policies, run
        self.local = tuple(sorted({network(p) for p in local_prefixes}))

    def _ledger(self) -> dict[str, str]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                    or info.st_size > 1024 * 1024
                ):
                    raise RuntimeError("destination protection ledger is unsafe")
                value = json.load(stream)
        except FileNotFoundError:
            return {}
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "cluster", "admitted"}
            or value["schema"] != SCHEMA
            or value["cluster"] != self.cluster
            or not isinstance(value["admitted"], dict)
        ):
            raise RuntimeError("destination protection ledger is unproven")
        for prefix, owner in value["admitted"].items():
            network(prefix)
            if not isinstance(owner, str) or not owner:
                raise RuntimeError("destination protection ownership is unproven")
        return value["admitted"]

    def _destinations(self, ledger: dict[str, str]) -> dict[str, tuple[str, ...]]:
        owners: dict[str, str] = dict(ledger)
        for policy in self.policies:
            for prefix in policy.destinations:
                if prefix in owners and owners[prefix] != policy.name:
                    raise RuntimeError("destination protection ownership is ambiguous")
                owners[prefix] = policy.name
        policies = {p.name: p for p in self.policies}
        result: dict[str, tuple[str, ...]] = {}
        for prefix, owner in owners.items():
            if owner not in policies:
                result[prefix] = ()  # Retain a drop until authoritative retirement.
                continue
            result[prefix] = tuple(sorted(p.interface for p in policies[owner].paths))
        return result

    def admit(self, prefixes: frozenset[str]) -> None:
        """Bind new admitted BGP routes to their actual configured XFRM path.

        Caller supplies only policy-validated eligible prefixes (or exact owned
        predecessor publications). Every new entry needs kernel route provenance.
        """
        ledger = self._ledger()
        missing = prefixes - ledger.keys()
        if missing:
            result = self.run(
                ["ip", "-j", "-4", "route", "show", "table", "main"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode:
                raise RuntimeError("destination provenance is unavailable")
            routes = json.loads(result.stdout)
            if not isinstance(routes, list):
                raise RuntimeError("destination provenance is malformed")
            for prefix in sorted(missing):
                network(prefix)
                devices = {
                    r.get("dev")
                    for r in routes
                    if isinstance(r, dict)
                    # iproute2 omits /32 for host routes in its JSON output.
                    and network(r.get("dst", "0.0.0.0/0").replace("default", "0.0.0.0/0")) == prefix
                }
                candidates = [
                    p
                    for p in self.policies
                    if p.mode == "bgp" and any(path.interface in devices for path in p.paths)
                ]
                if len(candidates) != 1:
                    raise RuntimeError("destination provenance is ambiguous")
                ledger[prefix] = candidates[0].name
            self._destinations(ledger)
            atomic_write_json(
                self.path, {"schema": SCHEMA, "cluster": self.cluster, "admitted": ledger}
            )
        self.install()

    def retire(self, published: frozenset[str]) -> None:
        """Called only after verified route reconciliation under current config."""
        configured = frozenset(p for policy in self.policies for p in policy.destinations)
        ledger = self._ledger()
        retained = {
            p: owner
            for p, owner in ledger.items()
            if p in published
            or p in configured
            or any(policy.name == owner and policy.mode == "bgp" for policy in self.policies)
        }
        if retained != ledger:
            atomic_write_json(
                self.path, {"schema": SCHEMA, "cluster": self.cluster, "admitted": retained}
            )
            self.install()

    def _expected(self) -> list[dict]:
        return objects(self._destinations(self._ledger()), self.local)

    def verify(self) -> None:
        result = self.run(
            ["nft", "-j", "list", "table", "inet", TABLE], capture_output=True, text=True, timeout=5
        )
        if result.returncode:
            raise RuntimeError("destination protection is missing")
        value = json.loads(result.stdout)
        actual = [
            {k: {key: val for key, val in v.items() if key != "handle"}}
            for row in value["nftables"]
            for k, v in row.items()
            if k != "metainfo"
        ]
        if actual != self._expected():
            raise RuntimeError("destination protection differs from admitted intent")
        links = self.run(
            ["ip", "-d", "-j", "link", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if links.returncode:
            raise RuntimeError("protected interface identity is unavailable")
        identities = {v["ifname"]: v.get("linkinfo", {}) for v in json.loads(links.stdout)}
        for policy in self.policies:
            for path in policy.paths:
                if path.interface not in identities:
                    # A proven absent interface cannot carry a protected route.
                    # Read ALL links so a reused name with another kind cannot
                    # masquerade as absence and bypass this guard.
                    continue
                info = identities.get(path.interface, {})
                identity = info.get("info_data", {}).get("if_id")
                # iproute2 emits native XFRM IDs as hexadecimal JSON strings.
                # Compare the numeric identity, retaining fail-closed parsing.
                if isinstance(identity, str):
                    identity = int(identity, 0)
                if (
                    info.get("info_kind") != "xfrm"
                    or type(identity) is not int
                    or identity != path.if_id
                ):
                    raise RuntimeError("protected interface identity differs")

    def ready(self) -> bool:
        try:
            self.verify()
            return True
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            return False

    def install(self) -> None:
        ledger = self._ledger()
        for policy in self.policies:
            for prefix in policy.destinations:
                if prefix in ledger and ledger[prefix] != policy.name:
                    raise RuntimeError("destination protection ownership changed")
                ledger[prefix] = policy.name
        expected = objects(self._destinations(ledger), self.local)
        if not self.path.exists() or ledger != self._ledger():
            atomic_write_json(
                self.path, {"schema": SCHEMA, "cluster": self.cluster, "admitted": ledger}
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A private file permits bounded argument-vector runners without shell/stdin.
        fd, name = tempfile.mkstemp(prefix=".forward-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "nftables": [
                            {"add": expected[0]},
                            {"delete": expected[0]},
                            *({"add": obj} for obj in expected),
                        ]
                    },
                    stream,
                )
            for args in (["--check"], []):
                result = self.run(
                    ["nft", "-j", *args, "-f", name], capture_output=True, text=True, timeout=5
                )
                if result.returncode:
                    raise RuntimeError("destination protection installation failed")
            self.verify()
        finally:
            Path(name).unlink(missing_ok=True)
