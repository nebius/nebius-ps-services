"""Exact kernel-object scope after a connection loses daemon ownership.

An interface identifier and configured endpoints establish scope. Broad traffic
selectors never establish ownership. This planner grants no forwarding authority
and never emits a flush or a selector-wide deletion.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from ...ha_tunnel_policy import ConnectionPolicy


def number(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid kernel identity")
    text = str(value)
    result = int(text, 16 if text.lower().startswith("0x") else 10)
    if not 0 <= result <= 0xFFFFFFFF:
        raise ValueError("invalid kernel identity")
    return result


def deletions(
    policy: ConnectionPolicy,
    states: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    links: object,
    addresses: object,
) -> tuple[tuple[str, ...], ...]:
    """Validate the entire affected scope before returning any exact mutation."""
    paths = {path.if_id: path for path in policy.paths}
    if len(paths) != len(policy.paths) or not paths:
        raise ValueError("ambiguous configured kernel scope")
    scoped = [
        (kind, entry, paths[number(entry["if_id"])])
        for kind, records in (("state", states), ("policy", policies))
        for entry in records
        if "if_id" in entry and number(entry["if_id"]) in paths
    ]
    if not scoped:
        return ()
    if not isinstance(links, list) or not isinstance(addresses, list):
        raise ValueError("kernel scope lacks local interface evidence")
    local = {
        str(ipaddress.ip_address(address["local"]))
        for interface in addresses
        for address in interface.get("addr_info", [])
        if "local" in address
    }
    commands = []
    for kind, entry, path in scoped:
        matching = [link for link in links if link.get("ifname") == path.interface]
        if len(matching) != 1:
            raise ValueError("managed XFRM interface is unproven")
        info = matching[0].get("linkinfo", {})
        if (
            info.get("info_kind") != "xfrm"
            or number(info.get("info_data", {}).get("if_id")) != path.if_id
            or entry.get("proto") != "esp"
            or entry.get("mode") != "tunnel"
            or number(entry.get("reqid")) == 0
            or any(key in entry for key in ("mark", "ctx", "output-mark", "output_mark"))
        ):
            raise ValueError("kernel object has ambiguous ownership")
        prefix = "tmpl_" if kind == "policy" else ""
        src = str(ipaddress.ip_address(entry[prefix + "src"]))
        dst = str(ipaddress.ip_address(entry[prefix + "dst"]))
        peer = str(ipaddress.ip_address(path.outer_pair[1]))

        # A trap may have an unspecified local template; an SA must use an
        # actually assigned local address.
        local_endpoints = local | ({"0.0.0.0", "::"} if kind == "policy" else set())
        if not (
            (src in local_endpoints and dst == peer) or (dst in local_endpoints and src == peer)
        ):
            raise ValueError("kernel endpoints are outside configured ownership")
        command: tuple[str, ...]
        if kind == "state":
            spi = number(entry.get("spi"))
            if spi == 0:
                raise ValueError("kernel SA lacks an exact SPI")
            if (
                sum(
                    state.get("src") == src
                    and state.get("dst") == dst
                    and state.get("proto") == "esp"
                    and number(state.get("spi")) == spi
                    for state in states
                )
                != 1
            ):
                raise ValueError("kernel SA identity is shared outside its scope")
            command = (
                "ip",
                "xfrm",
                "state",
                "delete",
                "src",
                src,
                "dst",
                dst,
                "proto",
                "esp",
                "spi",
                hex(spi),
            )
        else:
            index = number(entry.get("index"))
            direction = entry.get("dir")
            if index == 0 or direction not in {"in", "out", "fwd"}:
                raise ValueError("kernel policy lacks an exact identity")
            command = (
                "ip",
                "xfrm",
                "policy",
                "delete",
                "index",
                str(index),
                "dir",
                direction,
                "if_id",
                str(path.if_id),
            )
        if command in commands:
            raise ValueError("kernel mutation identity is ambiguous")
        commands.append(command)
    return tuple(commands)
