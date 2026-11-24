"""Owned guest policy rules and persistent source-qualified drop protection.

Install is called under the existing ordinary/HA routing writer. Old obligations
survive configuration changes; only the deployment's completed cloud transition
may call retire. Packet guards are installed before changing routing rules.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..ha_tunnel_policy import ConnectionPolicy
from .vm_ha.store import atomic_write_json

SCHEMA = "nebius-vpngw.enforced-routing.v2"
TABLE = "nebius_vpngw_enforced"
PROTOCOL = 196
RULE_BASE = 12000
MAX_SCOPES = 9000
ORDINARY_BASE = 30000
RULE_END = 30064
MAX_ORDINARY = 1000


def table_matches(row: Mapping, table: int) -> bool:
    return str(row.get("table", "main")) in ({"main", "254"} if table == 254 else {str(table)})


def normalize_rule(row: dict) -> dict:
    """Ignore no foreign selectors: an exact owned tuple is the authority."""
    allowed = {
        "priority",
        "src",
        "srclen",
        "dst",
        "dstlen",
        "iif",
        "table",
        "action",
        "protocol",
        "suppress_prefixlength",
        "suppress_prefixlen",
    }
    if set(row) - allowed:
        return {}
    result = dict(row)
    if "suppress_prefixlen" in result:
        if "suppress_prefixlength" in result:
            return {}
        result["suppress_prefixlength"] = result.pop("suppress_prefixlen")
    if result.get("action") in (7, "7"):
        result["action"] = "unreachable"  # Linux FR_ACT_UNREACHABLE under ip -N.
    result.setdefault("dst", "0.0.0.0/0")
    result.setdefault("src", "0.0.0.0/0")
    for key in ("priority", "protocol", "table", "suppress_prefixlength"):
        if key in result:
            try:
                result[key] = int(result[key])
            except (TypeError, ValueError):
                return {}
    if "suppress_prefixlength" in result and not 0 <= result["suppress_prefixlength"] <= 32:
        return {}
    for key in ("src", "dst"):
        if key in result:
            try:
                address = "0.0.0.0/0" if result[key] == "all" else result[key]
                length = result.pop(key + "len", None)
                if length is not None:
                    if type(length) is not int or not 0 <= length <= 32 or "/" in address:
                        return {}
                    address += "/" + str(length)
                result[key] = str(ipaddress.IPv4Network(address, strict=True))
            except (ValueError, TypeError):
                return {}
    return result


def rule_sort_key(rule: dict) -> tuple[int, str]:
    return (rule.get("priority", -1), json.dumps(rule, sort_keys=True))


def scope_id(scope: dict) -> str:
    return hashlib.sha256(
        json.dumps({k: v for k, v in scope.items() if k != "priority"}, sort_keys=True).encode()
    ).hexdigest()


def scope_priority(scope: dict) -> int:
    base = RULE_BASE if scope["kind"] == "selected" else ORDINARY_BASE
    return base + 2 * (32 - ipaddress.IPv4Network(scope["destination"]).prefixlen)


class PolicyRouting:
    def __init__(
        self, path: Path, *, cluster: str, policies: tuple[ConnectionPolicy, ...], run: Any
    ):
        self.path, self.cluster, self.policies, self.run = path, cluster, policies, run

    def desired(self) -> dict[str, dict]:
        result = {}
        for policy in self.policies:
            for source in policy.enforced_sources:
                for destination in policy.enforced_destinations:
                    scope = {
                        "kind": "selected",
                        "connection": policy.name,
                        "source": source,
                        "destination": destination,
                        "table": policy.enforced_table,
                        "ingress": policy.ingress,
                        "mode": policy.mode,
                        "paths": [
                            {"device": p.interface, "if_id": p.if_id, "peer": p.peer}
                            for p in sorted(policy.paths, key=lambda p: p.interface)
                        ],
                    }
                    result[scope_id(scope)] = scope
            for destination in policy.ordinary_shared:
                scope = {
                    "kind": "ordinary",
                    "connection": policy.name,
                    "source": "0.0.0.0/0",
                    "destination": destination,
                    "table": policy.enforced_table,
                    "ingress": policy.ingress,
                    "mode": policy.mode,
                    "paths": [
                        {"device": p.interface, "if_id": p.if_id, "peer": p.peer}
                        for p in sorted(policy.paths, key=lambda p: p.interface)
                    ],
                }
                result[scope_id(scope)] = scope
        if sum(s["kind"] == "ordinary" for s in result.values()) > MAX_ORDINARY:
            raise ValueError("ordinary shared routing exceeds its rule budget")
        if sum(s["kind"] == "selected" for s in result.values()) > MAX_SCOPES:
            raise ValueError("enforced routing source/destination matrix exceeds its rule budget")
        return result

    def read(self) -> dict[str, dict]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_size > 8 * 1024 * 1024
            ):
                raise RuntimeError("enforced routing ledger is unsafe")
            value = json.load(stream)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "cluster", "scopes"}
            or value["schema"] != SCHEMA
            or value["cluster"] != self.cluster
            or not isinstance(value["scopes"], dict)
            or len(value["scopes"]) > MAX_SCOPES + MAX_ORDINARY
        ):
            raise RuntimeError("enforced routing ledger is invalid")
        for key, scope in value["scopes"].items():
            if (
                not isinstance(scope, dict)
                or set(scope)
                != {
                    "kind",
                    "connection",
                    "source",
                    "destination",
                    "table",
                    "ingress",
                    "priority",
                    "mode",
                    "paths",
                }
                or scope["kind"] not in {"selected", "ordinary"}
                or not isinstance(scope["connection"], str)
                or not scope["connection"]
                or type(scope["table"]) is not int
                or not 10000 <= scope["table"] < 60000
                or type(scope["priority"]) is not int
                or scope["priority"] != scope_priority(scope)
                or (
                    scope["kind"] == "ordinary"
                    and (
                        scope["source"] != "0.0.0.0/0"
                        or ipaddress.IPv4Network(scope["destination"]).prefixlen == 0
                    )
                )
                or not isinstance(scope["ingress"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", scope["ingress"])
                or scope["mode"] not in {"static", "bgp"}
                or not isinstance(scope["paths"], list)
                or not scope["paths"]
                or key != scope_id(scope)
            ):
                raise RuntimeError("enforced routing scope is invalid")
            for path in scope["paths"]:
                if (
                    not isinstance(path, dict)
                    or set(path) != {"device", "if_id", "peer"}
                    or not re.fullmatch(r"xfrm[0-9]+", path["device"])
                    or type(path["if_id"]) is not int
                    or path["if_id"] <= 0
                    or not isinstance(path["peer"], str)
                ):
                    raise RuntimeError("enforced route path identity is invalid")
            for field in ("source", "destination"):
                if str(ipaddress.IPv4Network(scope[field], strict=True)) != scope[field]:
                    raise RuntimeError("enforced routing prefix is invalid")
        return value["scopes"]

    def scopes(self) -> dict[str, dict]:
        return self.merge(self.read(), self.desired())

    @staticmethod
    def merge(previous: dict[str, dict], desired: dict[str, dict]) -> dict[str, dict]:
        scopes = dict(previous)
        for key, scope in sorted(desired.items()):
            if key not in scopes:
                # Prefix tiers remain stable when more-specific intent is added.
                # Equal-length scopes are disjoint or share an exact rule tuple;
                # retained conflicting obligations remain fail-closed until retirement.
                scopes[key] = {**scope, "priority": scope_priority(scope)}
        return scopes

    @staticmethod
    def rules(scopes: dict[str, dict]) -> list[dict]:
        if (
            sum(s["kind"] == "selected" for s in scopes.values()) > MAX_SCOPES
            or sum(s["kind"] == "ordinary" for s in scopes.values()) > MAX_ORDINARY
        ):
            raise RuntimeError("retained enforced routing exceeds its rule budget")
        result = []
        for scope in sorted(scopes.values(), key=lambda s: s["priority"]):
            common = {
                "src": scope["source"],
                "dst": scope["destination"],
                "iif": scope["ingress"],
                "protocol": PROTOCOL,
            }
            priority = scope["priority"]
            if scope["kind"] == "ordinary":
                length = ipaddress.IPv4Network(scope["destination"]).prefixlen
                result.extend(
                    [
                        {
                            **common,
                            "priority": priority,
                            "table": 254,
                            "suppress_prefixlength": length,
                        },
                        {
                            **common,
                            "priority": priority + 1,
                            "table": scope["table"],
                            "suppress_prefixlength": length - 1,
                        },
                    ]
                )
                continue
            lookup = {**common, "priority": priority, "table": scope["table"]}
            length = ipaddress.IPv4Network(scope["destination"]).prefixlen
            if length:
                # An explicit finite destination cannot borrow a broader route
                # from this connection's table after its exact route withdraws.
                lookup["suppress_prefixlength"] = length - 1
            result.extend(
                [
                    lookup,
                    {**common, "priority": priority + 1, "action": "unreachable"},
                ]
            )
        return sorted(
            {json.dumps(r, sort_keys=True): r for r in result}.values(), key=rule_sort_key
        )

    def owns_rule(self, row: dict) -> bool:
        return normalize_rule(row) in self.rules(self.read())

    def _json(self, argv: list[str]) -> Any:
        result = self.run(argv, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError("enforced routing observation failed")
        return json.loads(result.stdout)

    def _command(self, argv: list[str]) -> None:
        result = self.run(argv, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError("enforced routing mutation failed")

    @staticmethod
    def _rule_command(action: str, rule: dict) -> list[str]:
        argv = [
            "ip",
            "-4",
            "rule",
            action,
            "priority",
            str(rule["priority"]),
            "from",
            rule["src"],
            "to",
            rule["dst"],
            "iif",
            rule["iif"],
            "protocol",
            str(PROTOCOL),
        ]
        argv += ["lookup", str(rule["table"])] if "table" in rule else ["unreachable"]
        if "suppress_prefixlength" in rule:
            argv += ["suppress_prefixlength", str(rule["suppress_prefixlength"])]
        return argv

    def objects(self, scopes: dict[str, dict]) -> list[dict]:
        base = {"family": "inet", "table": TABLE}
        objects: list[dict] = [
            {"table": {"family": "inet", "name": TABLE}},
            {
                "chain": {
                    **base,
                    "name": "forward",
                    "type": "filter",
                    "hook": "forward",
                    "prio": -20,
                    "policy": "accept",
                }
            },
        ]
        current = {p.name: p for p in self.policies}
        for _, scope in sorted(scopes.items()):
            if scope["kind"] == "ordinary":
                continue
            expr: list[dict] = [
                {
                    "match": {
                        "op": "==",
                        "left": {"meta": {"key": "iifname"}},
                        "right": scope["ingress"],
                    }
                }
            ]
            for field, selector in (("saddr", "source"), ("daddr", "destination")):
                net = ipaddress.IPv4Network(scope[selector])
                right: Any = (
                    str(net.network_address)
                    if net.prefixlen == 32
                    else {"prefix": {"addr": str(net.network_address), "len": net.prefixlen}}
                )
                expr.append(
                    {
                        "match": {
                            "op": "==",
                            "left": {"payload": {"protocol": "ip", "field": field}},
                            "right": right,
                        }
                    }
                )
            policy = current.get(scope["connection"])
            interfaces = sorted({p.interface for p in policy.paths}) if policy else []
            if interfaces:
                expr.append(
                    {
                        "match": {
                            "op": "!=",
                            "left": {"meta": {"key": "oifname"}},
                            "right": interfaces[0] if len(interfaces) == 1 else {"set": interfaces},
                        }
                    }
                )
            objects.append({"rule": {**base, "chain": "forward", "expr": [*expr, {"drop": None}]}})
        return objects

    def _guard(self, scopes: dict[str, dict]) -> None:
        objects = self.objects(scopes)
        fd, name = tempfile.mkstemp(prefix=".enforced-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "nftables": [
                            {"add": objects[0]},
                            {"delete": objects[0]},
                            *({"add": obj} for obj in objects),
                        ]
                    },
                    stream,
                )
            self._command(["nft", "-j", "--check", "-f", name])
            self._command(["nft", "-j", "-f", name])
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def _route_claimed(row: dict, scopes: dict[str, dict], devices: dict[str, Any]) -> bool:
        destination = str(
            ipaddress.IPv4Network(
                "0.0.0.0/0" if row.get("dst") == "default" else row["dst"], strict=False
            )
        )
        return any(
            scope["destination"] == destination
            and table_matches(row, scope["table"])
            and any(
                path["device"] == row.get("dev")
                and path["if_id"] == devices.get(path["device"])
                and (
                    scope["mode"] == "bgp"
                    and row.get("protocol") == "bgp"
                    and row.get("gateway") == path["peer"]
                    or scope["mode"] == "static"
                    and row.get("protocol", "boot") == "boot"
                    and "gateway" not in row
                    and "nhid" not in row
                )
                for path in scope["paths"]
            )
            for scope in scopes.values()
        )

    def _route_inventory(self, scopes: dict[str, dict]) -> tuple[list[dict], dict[str, Any]]:
        """Admit only exact declared route tuples in exclusively claimed tables."""
        tables = {s["table"] for s in scopes.values()}
        rows = self._json(["ip", "-j", "-4", "route", "show", "table", "all"])
        rows = [r for r in rows if any(table_matches(r, t) for t in tables)]
        if not rows:
            return [], {}
        links = self._json(["ip", "-d", "-j", "link", "show", "type", "xfrm"])
        devices = {}
        for link in links:
            info = link.get("linkinfo", {})
            if info.get("info_kind") == "xfrm":
                identifier = info.get("info_data", {}).get("if_id")
                devices[link["ifname"]] = (
                    int(identifier, 0) if isinstance(identifier, str) else identifier
                )
        for row in rows:
            if (
                set(row)
                - {
                    "dst",
                    "dev",
                    "table",
                    "protocol",
                    "scope",
                    "flags",
                    "metric",
                    "gateway",
                    "type",
                    "nhid",
                }
                or row.get("type", "unicast") != "unicast"
                or row.get("flags")
                # FRR may use native nexthop objects. The ID is metadata, never
                # path authority: the resolved single gateway/device below must
                # still match the admitted XFRM path. Multipath stays rejected.
                or "nhid" in row
                and (type(row["nhid"]) is not int or not 0 < row["nhid"] < 2**32)
                or not self._route_claimed(row, scopes, devices)
            ):
                raise RuntimeError("enforced route table contains an unowned route")
        return rows, devices

    def install(self) -> None:
        previous, desired = self.read(), self.desired()
        scopes = self.scopes()
        if not scopes and not self.path.exists():
            return
        before_rules = self.rules(previous)
        wanted_rules = self.rules(scopes)
        rows = self._json(["ip", "-j", "-4", "-details", "-N", "rule", "show"])
        for row in rows:
            if (
                RULE_BASE <= row.get("priority", -1) < RULE_END
                and normalize_rule(row) not in before_rules
            ):
                raise RuntimeError("enforced routing conflicts with an unowned policy rule")
        tables = {s["table"] for s in previous.values()}
        routes = self._json(["ip", "-j", "-4", "route", "show", "table", "all"])
        new_tables = {s["table"] for s in desired.values()} - tables
        if any(any(table_matches(row, table) for table in new_tables) for row in routes):
            raise RuntimeError("enforced routing table already contains unowned routes")
        if previous:
            self._route_inventory(scopes)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish obligations before any effect; replay may reconstruct missing rules.
        atomic_write_json(self.path, {"schema": SCHEMA, "cluster": self.cluster, "scopes": scopes})
        self._guard(scopes)
        for row in rows:
            rule = normalize_rule(row)
            if rule in before_rules and rule not in wanted_rules:
                self._command(self._rule_command("del", rule))
        actual = [normalize_rule(row) for row in rows]
        for rule in wanted_rules:
            if rule not in actual:
                self._command(self._rule_command("add", rule))
        self.verify()

    def verify(self) -> None:
        scopes = self.scopes()
        if not scopes and not self.path.exists():
            return
        if not self.desired().keys() <= self.read().keys():
            raise RuntimeError("enforced routing intent is not admitted")
        expected = self.rules(scopes)
        rows = self._json(["ip", "-j", "-4", "-details", "-N", "rule", "show"])
        actual = [normalize_rule(r) for r in rows if RULE_BASE <= r.get("priority", -1) < RULE_END]
        if sorted(actual, key=rule_sort_key) != expected:
            raise RuntimeError("enforced routing policy rules differ")
        value = self._json(["nft", "-j", "list", "table", "inet", TABLE])
        actual_objects = [
            {k: {key: val for key, val in obj.items() if key != "handle"}}
            for row in value["nftables"]
            for k, obj in row.items()
            if k != "metainfo"
        ]
        if actual_objects != self.objects(scopes):
            raise RuntimeError("enforced routing forwarding guard differs")
        self._route_inventory(scopes)

    def retire(self) -> None:
        """Retire obsolete claims only after the caller commits cloud cutover."""
        previous = self.read()
        if not previous:
            return
        self.verify()
        wanted = {key: scope for key, scope in previous.items() if key in self.desired()}
        routes, devices = self._route_inventory(previous)
        for row in routes:
            destination = (
                "0.0.0.0/0"
                if row["dst"] == "default"
                else str(ipaddress.IPv4Network(row["dst"], strict=False))
            )
            table = int(row["table"])
            if self._route_claimed(row, wanted, devices):
                continue
            argv = ["ip", "-4", "route", "del", destination, "table", str(table)]
            argv.extend(["nhid", str(row["nhid"])] if "nhid" in row else ["dev", row["dev"]])
            # iproute2 omits boot protocol and zero metric in its JSON output.
            # Keep deletion exact when successor and predecessor share a prefix.
            argv.extend(["proto", row.get("protocol", "boot"), "metric", str(row.get("metric", 0))])
            for key, flag in (
                ("gateway", "via"),
                ("scope", "scope"),
            ):
                if key in row and not (key == "gateway" and "nhid" in row):
                    argv.extend([flag, str(row[key])])
            self._command(argv)
        wanted_rules = self.rules(wanted)
        for rule in self.rules(previous):
            if rule not in wanted_rules:
                self._command(self._rule_command("del", rule))
        actual = [
            normalize_rule(r)
            for r in self._json(["ip", "-j", "-4", "-details", "-N", "rule", "show"])
            if RULE_BASE <= r.get("priority", -1) < RULE_END
        ]
        remaining, devices = self._route_inventory(previous)
        if sorted(actual, key=rule_sort_key) != wanted_rules or any(
            not self._route_claimed(row, wanted, devices) for row in remaining
        ):
            raise RuntimeError("enforced routing retirement has incomplete postconditions")
        atomic_write_json(self.path, {"schema": SCHEMA, "cluster": self.cluster, "scopes": wanted})
        self._guard(wanted)
        self.verify()
