"""Read-only XFRM interface evidence shared by route selection and HA readiness."""

from __future__ import annotations

import re
from collections.abc import Mapping


def _recursive_values(value: object, key_names: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in key_names and isinstance(item, (str, int)):
                found.add(str(item))
            found.update(_recursive_values(item, key_names))
    elif isinstance(value, list):
        for item in value:
            found.update(_recursive_values(item, key_names))
    return found


def xfrm_interface_ids(value: object) -> set[str]:
    raw = _recursive_values(value, frozenset({"if_id", "ifId"}))
    if isinstance(value, str):
        raw.update(re.findall(r"\bif_id\s+(0[xX][0-9a-fA-F]+|[0-9]+)\b", value))
    normalized: set[str] = set()
    for item in raw:
        try:
            interface_id = int(item, 0)
        except ValueError:
            continue
        if interface_id > 0:
            normalized.add(str(interface_id))
    return normalized


def usable_xfrm_devices(links: object, state: object, policies: object) -> set[str]:
    if (
        not isinstance(links, list)
        or not isinstance(state, (list, str))
        or not isinstance(policies, (list, str))
    ):
        return set()
    active_if_ids = xfrm_interface_ids(state) & xfrm_interface_ids(policies)
    devices: set[str] = set()
    for link in links:
        if not isinstance(link, Mapping):
            continue
        if_name = link.get("ifname")
        link_info = link.get("linkinfo")
        if not isinstance(if_name, str) or not isinstance(link_info, Mapping):
            continue
        info_data = link_info.get("info_data")
        if not isinstance(info_data, Mapping):
            continue
        if_id = info_data.get("if_id", info_data.get("ifId"))
        link_if_ids = xfrm_interface_ids({"if_id": if_id})
        if if_name.startswith("xfrm") and bool(link_if_ids & active_if_ids):
            devices.add(if_name)
    return devices
