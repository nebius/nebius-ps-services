"""Fail-closed native evidence for a specific encrypted tunnel path."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from ... import ipsec_observation
from ...ha_tunnel_policy import ConnectionPolicy, TunnelPath, covers
from ...ipsec_observation import parse_sas
from .tunnels import Health


def scoped_ike_ids(raw: str, names: set[str]) -> tuple[str, ...]:
    """Reject an IKE SA containing any unrelated CHILD before termination."""
    result = []
    for name, sa in parse_sas(raw):
        children = sa.get("child-sas", {})
        if not isinstance(children, dict):
            raise ValueError("invalid CHILD set")
        if any(
            not isinstance(child, dict) or not isinstance(child.get("name"), str)
            for child in children.values()
        ):
            raise ValueError("unavailable CHILD identity")
        child_names = {
            str(child.get("name", "")) for child in children.values() if isinstance(child, dict)
        }
        if name in names or child_names & names:
            if name not in names or child_names - names:
                raise ValueError("connection shares an unrelated IKE SA")
            result.append(str(sa["uniqueid"]))
    return tuple(result)


def _number(value: object) -> int:
    text = str(value)
    return int(text, 16 if text.lower().startswith("0x") else 10)


def xfrm_records(raw: str, *, policy: bool) -> list[dict[str, Any]]:
    """iproute2 XFRM -j is not supported on every supported Linux distribution."""
    if raw.lstrip().startswith("["):
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            raise ValueError("invalid XFRM JSON")
        if policy:
            for entry in value:
                templates = entry.get("tmpl", [])
                if templates:
                    if (
                        not isinstance(templates, list)
                        or len(templates) != 1
                        or not isinstance(templates[0], dict)
                    ):
                        raise ValueError("ambiguous XFRM policy template")
                    entry.update(
                        {
                            key: templates[0][key]
                            for key in ("reqid", "mode", "proto")
                            if key in templates[0]
                        }
                    )
                    entry.update(
                        {
                            "tmpl_" + key: templates[0][key]
                            for key in ("src", "dst")
                            if key in templates[0]
                        }
                    )
        return value
    if not raw.strip():
        return []
    records = []
    for block in re.split(r"(?m)(?=^src )", raw):
        if not block.strip():
            continue
        endpoints = re.match(r"src (\S+) dst (\S+)", block)
        if endpoints is None:
            raise ValueError("invalid XFRM record")
        # charon's IKE sockets install bypass policies. They have no template
        # or XFRM interface and are not evidence for any encrypted path.
        if policy and re.search(r"(?m)^\s+socket (?:in|out)\b", block):
            if re.search(r"\b(?:tmpl|if_id|reqid)\b", block):
                raise ValueError("ambiguous XFRM socket policy")
            continue
        record: dict[str, Any] = {"src": endpoints[1], "dst": endpoints[2]}
        for key in ("if_id", "reqid", "spi", "dir", "mode", "proto", "index", "mark", "ctx"):
            match = re.search(r"\b" + key + r"\s+(\S+)", block)
            if match:
                value = match[1]
                if key in {"reqid", "spi"} and "(" in value:
                    # iproute2 statistics print both decimal and hexadecimal
                    # identities. Accept the pair only when they agree.
                    pair = re.fullmatch(
                        r"(0x[0-9a-fA-F]+|[0-9]+)\((0x[0-9a-fA-F]+|[0-9]+)\)", value
                    )
                    if pair is None or _number(pair[1]) != _number(pair[2]):
                        raise ValueError("ambiguous XFRM numeric identity")
                    value = pair[1]
                record[key] = value
        if policy and "dir" not in record or not policy and "spi" not in record:
            raise ValueError("incomplete XFRM record")
        if policy:
            templates = re.findall(r"\btmpl src (\S+) dst (\S+)", block)
            if len(templates) != len(re.findall(r"\btmpl\b", block)) or len(templates) > 1:
                raise ValueError("ambiguous XFRM policy template")
            if templates:
                record.update(tmpl_src=templates[0][0], tmpl_dst=templates[0][1])
        records.append(record)
    return records


def route_destination(route: dict[str, Any]) -> str:
    destination = route["dst"]
    return str(ipaddress.IPv4Network("0.0.0.0/0" if destination == "default" else destination))


def path_health(
    path: TunnelPath,
    policy: ConnectionPolicy,
    *,
    sas: str,
    states: str,
    policies: str,
    links: object,
    routes: object,
    require_routes: bool,
) -> Health:
    try:
        ike = parse_sas(sas)
        state = xfrm_records(states, policy=False)
        rules = xfrm_records(policies, policy=True)
        if not isinstance(links, list) or not isinstance(routes, list):
            return Health.UNKNOWN
        link = next((x for x in links if x.get("ifname") == path.interface), None)
        if link is None:
            return Health.DOWN
        info = link.get("linkinfo", {})
        if (
            info.get("info_kind") != "xfrm"
            or _number(info.get("info_data", {}).get("if_id", -1)) != path.if_id
            or "UP" not in link.get("flags", [])
        ):
            return Health.DOWN
        # Any exact installed replacement can establish health during rekey overlap.
        installed = []
        for name, sa in ike:
            if name == path.name and sa.get("state") == "ESTABLISHED":
                if (
                    sa["remote-host"] != path.outer_pair[1]
                    or sa["remote-id"] != path.outer_pair[1]
                    or (
                        path.outer_pair[0] not in {"", "%any"}
                        and sa["local-id"] != path.outer_pair[0]
                    )
                ):
                    continue
                # Public local identity may be translated by cloud NAT. Kernel
                # state/policy templates must match the actual IKE socket hosts,
                # while the authenticated identities match the configured peer.
                for child in sa.get("child-sas", {}).values():
                    if child.get("name") == path.name and child.get("state") == "INSTALLED":
                        if not covers(
                            frozenset(child["remote-ts"]), policy.destinations
                        ) or not covers(frozenset(child["local-ts"]), path.local_selectors):
                            continue
                        installed.append((child, sa["local-host"], sa["remote-host"]))
        usable = False
        for child, local_host, remote_host in installed:
            reqid = _number(child["reqid"])
            kernel = [
                s
                for s in state
                if _number(s.get("if_id", -1)) == path.if_id
                and _number(s.get("reqid", -1)) == reqid
                and s.get("mode") == "tunnel"
                and s.get("proto") == "esp"
            ]
            if not all(
                any(
                    s.get("src") == src
                    and s.get("dst") == dst
                    and _number(s["spi"]) == int(str(child[spi]), 16)
                    for s in kernel
                )
                for spi, src, dst in (
                    ("spi-in", remote_host, local_host),
                    ("spi-out", local_host, remote_host),
                )
            ):
                continue
            matching = [
                r
                for r in rules
                if _number(r.get("if_id", -1)) == path.if_id
                and _number(r.get("reqid", -1)) == reqid
                and r.get("mode") == "tunnel"
                and r.get("proto") == "esp"
            ]
            if not all(
                any(
                    r.get("dir") == direction
                    and r.get("tmpl_src") == (local_host if direction == "out" else remote_host)
                    and r.get("tmpl_dst") == (remote_host if direction == "out" else local_host)
                    and ipaddress.IPv4Network(src).subnet_of(ipaddress.IPv4Network(r["src"]))
                    and ipaddress.IPv4Network(dst).subnet_of(ipaddress.IPv4Network(r["dst"]))
                    for r in matching
                )
                for local in path.local_selectors
                for remote in policy.destinations
                for direction, src, dst in (
                    ("out", local, remote),
                    ("in", remote, local),
                    ("fwd", remote, local),
                )
            ):
                continue
            usable = True
        if not usable:
            return Health.DOWN
        if require_routes:
            for prefix in policy.destinations:
                observed = [
                    r
                    for r in routes
                    if route_destination(r) == prefix and r.get("table", "main") in {"main", 254}
                ]
                if (
                    len(observed) != 1
                    or observed[0].get("dev") != path.interface
                    or observed[0].get("nexthops")
                ):
                    return Health.DOWN
        return Health.HEALTHY
    except (ValueError, KeyError, TypeError, AttributeError):
        return Health.UNKNOWN


class NativeTunnelObserver:
    def __init__(self, run: Callable[[Sequence[str]], str]) -> None:
        self.run = run

    def observe(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health:
        path = next(p for p in policy.paths if p.name == target)
        try:
            health = path_health(
                path,
                policy,
                sas=self.run(ipsec_observation.command()),
                states=self.run(("ip", "-j", "xfrm", "state")),
                policies=self.run(("ip", "-j", "xfrm", "policy")),
                links=json.loads(self.run(("ip", "-d", "-j", "link", "show", "type", "xfrm"))),
                routes=json.loads(self.run(("ip", "-j", "-4", "route", "show", "table", "main"))),
                require_routes=selected and policy.mode == "static",
            )
            if health is not Health.HEALTHY or policy.mode == "static":
                return health
            summary = json.loads(self.run(("vtysh", "-c", "show bgp summary json")))
            if not isinstance(summary, dict):
                return Health.UNKNOWN
            family = summary.get("ipv4Unicast")
            if not isinstance(family, dict):
                return Health.UNKNOWN
            peers = family.get("peers")
            if not isinstance(peers, dict):
                return Health.UNKNOWN
            peer = peers.get(path.peer)
            if not isinstance(peer, dict) or peer.get("state") not in {
                "Established",
                "Idle",
                "Connect",
                "Active",
                "OpenSent",
                "OpenConfirm",
                "Clearing",
                "Deleted",
            }:
                return Health.UNKNOWN
            return Health.HEALTHY if peer["state"] == "Established" else Health.DOWN
        except (RuntimeError, ValueError, KeyError, TypeError):
            return Health.UNKNOWN
