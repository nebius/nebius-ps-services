"""Package-independent validation of persistent physical-route ownership."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA = "nebius-vpngw.underlay.v1"
DROPIN = "50-nebius-vpngw-underlay.conf"
METRIC = 50
NETWORK_ROOT = Path("/etc/systemd/network")


def render(destinations: tuple[str, ...]) -> str:
    return "# Managed by nebius-vpngw; edit the gateway YAML instead.\n" + "".join(
        f"\n[Route]\nDestination={prefix}\nGateway=_dhcp4\nGatewayOnLink=yes\n"
        f"Table=254\nProtocol=16\nMetric={METRIC}\n"
        for prefix in destinations
    )


def read(path: Path) -> bytes:
    """Accept safe 0644 ownership evidence, including protected repair archives."""
    for parent in path.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or (info.st_mode & 0o022 and not sticky)
        ):
            raise ValueError("underlay ownership has an unsafe ancestor")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or info.st_size > 4 * 1024 * 1024
        ):
            raise ValueError("underlay ownership has unsafe file metadata")
        raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("underlay ownership exceeds its limit")
        return raw


def _content(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefixes = tuple(re.findall(r"^Destination=(.+)$", value, re.MULTILINE))
    return all(
        str(ipaddress.IPv4Network(p, strict=True)) == p for p in prefixes
    ) and value == render(prefixes)


def _network(value: Any, network_root: Path) -> None:
    required = {
        "interface",
        "index",
        "address",
        "gateway",
        "network_file",
        "config_digest",
        "routes",
    }
    optional = {"managed_routes", "owned_dropin_loaded", "dhcp_routes"}
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - optional
        or not isinstance(value.get("owned_dropin_loaded", False), bool)
        or any(
            not isinstance(value.get(name, []), list)
            or any(not isinstance(row, dict) for row in value.get(name, []))
            for name in ("routes", "managed_routes")
        )
        or not isinstance(value.get("dhcp_routes", []), list)
    ):
        raise ValueError("underlay network ownership is invalid")
    if (
        not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", value.get("interface", ""))
        or type(value.get("index")) is not int
        or value["index"] <= 0
        or Path(value["network_file"]).parent
        not in {network_root, Path("/run/systemd/network"), Path("/usr/lib/systemd/network")}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+\.network", Path(value["network_file"]).name)
        or not re.fullmatch(r"[a-f0-9]{64}", value.get("config_digest", ""))
    ):
        raise ValueError("underlay network identity is invalid")
    ipaddress.IPv4Address(value["address"])
    ipaddress.IPv4Address(value["gateway"])
    for row in value.get("dhcp_routes", []):
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("underlay DHCP route ownership is invalid")
        prefix, gateway = row
        ipaddress.IPv4Network(prefix, strict=True)
        ipaddress.IPv4Address(gateway)


def decode(raw: bytes, *, network_root: Path = NETWORK_ROOT) -> dict[str, Any]:
    """Validate ownership without observing a possibly broken current network."""
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("phase") not in {"pending", "committed"}
        or not re.fullmatch(r"[a-f0-9]{64}", value.get("intent", ""))
        or not isinstance(value.get("boot"), str)
        or not 1 <= len(value["boot"]) <= 256
        or not isinstance(value.get("destinations"), list)
        or not isinstance(value.get("retire_files", []), list)
        or not isinstance(value.get("retire", []), list)
        or any(str(ipaddress.IPv4Network(p, strict=True)) != p for p in value["destinations"])
    ):
        raise ValueError("underlay ownership ledger is invalid")
    _network(value.get("network"), network_root)
    expected = network_root / (Path(value["network"]["network_file"]).name + ".d") / DROPIN
    if value.get("dropin") != str(expected) or value.get("content") != render(
        tuple(value["destinations"])
    ):
        raise ValueError("underlay ownership ledger has an invalid drop-in")
    if value.get("previous_content") is not None and not _content(value["previous_content"]):
        raise ValueError("underlay predecessor content is invalid")
    for retired in value.get("retire_files", []):
        if not isinstance(retired, dict) or set(retired) != {"path", "content"}:
            raise ValueError("underlay retired file ownership is invalid")
        path = Path(retired["path"])
        if (
            path.name != DROPIN
            or path.parent.parent != network_root
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.network\.d", path.parent.name)
            or not _content(retired.get("content"))
        ):
            raise ValueError("underlay retired drop-in identity is unsafe")
    for retired in value.get("retire", []):
        if not isinstance(retired, dict) or set(retired) != {"prefix", "network"}:
            raise ValueError("underlay retired route ownership is invalid")
        ipaddress.IPv4Network(retired["prefix"], strict=True)
        _network(retired["network"], network_root)
    return value


def verify_files(value: dict[str, Any] | None, *, network_root: Path = NETWORK_ROOT) -> None:
    """Existing files must match recorded bytes; absence grants no readiness."""
    files = []
    if value:
        files = [(value["dropin"], {value["content"], value.get("previous_content")})]
        files += [(item["path"], {item["content"]}) for item in value.get("retire_files", [])]
    owned = {Path(filename) for filename, _ in files}
    if any(path not in owned for path in network_root.glob("*.network.d/" + DROPIN)):
        raise ValueError("underlay drop-in exists without recorded ownership")
    for filename, accepted in files:
        try:
            content = read(Path(filename)).decode()
        except FileNotFoundError:
            continue
        if content not in accepted:
            raise ValueError("underlay drop-in differs from its recorded ownership")
