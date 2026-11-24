"""Persistent physical routes; callers own lifecycle fencing and the routing lock.

Networkd owns persistence and DHCP renewal. The periodic owner may restore only
the exact routes described by the committed drop-in. Neither owner grants VPN
forwarding, BGP export, or cloud mutation authority.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import underlay_state
from .local_commands import run
from .vm_ha.store import atomic_write_json

STATE = Path("/var/lib/nebius-vpngw/underlay.json")
BOOT = Path("/proc/sys/kernel/random/boot_id")
CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
NETWORK_ROOTS = tuple(
    Path(p)
    for p in (
        "/etc/systemd/network",
        "/run/systemd/network",
        "/usr/lib/systemd/network",
    )
)
DROPIN = "50-nebius-vpngw-underlay.conf"
SCHEMA = "nebius-vpngw.underlay.v1"
METRIC = 50


class UnderlayError(RuntimeError):
    """Actionable local-route failure, without credentials or command output."""


class LeaseNotReady(UnderlayError):
    """A bounded reconfiguration may temporarily withdraw the DHCP lease."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _parents(path: Path) -> None:
    for parent in path.parents:
        info = parent.lstat()
        # A root-owned sticky temporary ancestor is safe for a private, owned
        # child. This also permits isolated native qualification directories.
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o022
            and not sticky
        ):
            raise UnderlayError("underlay file has an unsafe ancestor")


def _read(path: Path) -> bytes:
    _parents(path)
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
            raise UnderlayError("underlay file ownership or type is unsafe")
        return stream.read(4 * 1024 * 1024 + 1)


def _directory(path: Path) -> None:
    """Reject symlink ancestors before creating any product-owned destination."""
    missing = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise UnderlayError("underlay directory is a symlink")
        missing.append(current)
        current = current.parent
    for parent in (current, *current.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UnderlayError("underlay directory is unsafe")
    _parents(current)
    info = current.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise UnderlayError("underlay directory ownership is unsafe")
    for item in reversed(missing):
        item.mkdir(mode=0o755)


def _write(path: Path, text: str) -> None:
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _read(path)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _command(argv: list[str]) -> str:
    result = run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode or len(result.stdout.encode()) > 4 * 1024 * 1024:
        raise UnderlayError("underlay command failed: " + argv[0])
    return result.stdout


def _json(argv: list[str]) -> Any:
    try:
        return json.loads(_command(argv))
    except (TypeError, ValueError) as error:
        raise UnderlayError("underlay observation is malformed") from error


def _ip(value: Any) -> str:
    # systemd JSON encodes an address as an array of bytes, iproute2 as text.
    return str(ipaddress.IPv4Address(bytes(value) if isinstance(value, list) else value))


def _configured(row: dict[str, Any]) -> bool:
    # networkd exposes a bitset, not an enum. Renewal may retain CONFIGURED
    # while CONFIGURING is set; independent kernel checks still must match.
    states = set(str(row.get("ConfigState", "")).split(","))
    return "configured" in states and states <= {"configured", "configuring"}


@dataclass(frozen=True)
class Intent:
    interface: str
    destinations: tuple[str, ...]
    identity: str


def intent(cfg: dict[str, Any]) -> Intent:
    interface = "eth0"
    local = {
        str(ipaddress.IPv4Network(p, strict=True))
        for p in (cfg.get("gateway") or {}).get("local_prefixes", [])
    }
    local.discard("0.0.0.0/0")  # Preserve the existing full-tunnel contract.
    controls: set[str] = set()
    ha = cfg.get("vm_ha")
    if ha is not None:
        from ..schema import VMHARuntimeBinding

        binding = VMHARuntimeBinding.model_validate(ha.get("runtime_binding"))
        node = (ha.get("node") or {}).get("node_id")
        own = [n for n in binding.nodes if n.node_id == node]
        if len(own) != 1:
            raise UnderlayError("underlay HA node binding is missing")
        interface = own[0].network_interface_name
        for member in binding.nodes:
            if member.node_id != node:
                address, port = member.peer_endpoint.rsplit(":", 1)
                if not port.isdecimal():
                    raise UnderlayError("underlay HA endpoint is malformed")
                controls.add(f"{ipaddress.IPv4Address(address)}/32")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", interface):
        raise UnderlayError("underlay interface is invalid")
    remote: set[str] = set()
    for connection in cfg.get("connections", []):
        policy = connection.get("remote_prefixes")
        if policy is None:
            policy = (connection.get("bgp") or {}).get("remote_prefixes")
        remote.update(str(ipaddress.IPv4Network(p, strict=True)) for p in policy or [])
        for tunnel in connection.get("tunnels", []):
            if tunnel.get("remote_public_ip"):
                controls.add(f"{ipaddress.IPv4Address(tunnel['remote_public_ip'])}/32")
            remote.update(
                str(ipaddress.IPv4Network(p, strict=True))
                for p in (tunnel.get("static_routes") or {}).get("remote_prefixes", []) or []
            )
    if remote & (local | controls):
        raise UnderlayError(
            "underlay and VPN claim the same destination; correct the prefix policy"
        )
    destinations = tuple(sorted(local | controls))
    return Intent(
        interface,
        destinations,
        digest(
            {
                "destinations": destinations,
                "interface": interface,
                "binding": None
                if ha is None
                else {key: ha.get(key) for key in ("runtime_binding", "node", "generation")},
                "remote": sorted(remote),
            }
        ),
    )


@dataclass(frozen=True)
class Network:
    interface: str
    index: int
    address: str
    gateway: str
    network_file: str
    config_digest: str
    routes: tuple[dict[str, Any], ...]
    managed_routes: tuple[dict[str, Any], ...] = ()
    owned_dropin_loaded: bool = False
    dhcp_routes: tuple[tuple[str, str], ...] = ()


def observe(interface: str, *, configuration: bool = True) -> Network:
    data = _json(["networkctl", "status", "--json=short", "--no-pager", interface])
    if not isinstance(data, dict) or data.get("Name") != interface:
        raise UnderlayError("networkd cannot identify the primary interface")
    if (
        data.get("MasterInterfaceIndex")
        or data.get("Kind") not in (None, "", "veth")
        or data.get("Type") != "ether"
        or not isinstance(data.get("Index"), int)
    ):
        raise UnderlayError("underlay requires one physical primary interface without VRF")
    addresses = {
        _ip(row["Address"])
        for row in data.get("Addresses", [])
        if row.get("Family") == 2 and row.get("ConfigSource") == "DHCPv4" and _configured(row)
    }
    routers = {
        _ip(row["Gateway"])
        for row in data.get("Routes", [])
        if row.get("Family") == 2
        and row.get("DestinationPrefixLength") == 0
        and row.get("Table") == 254
        and row.get("Protocol") == 16
        and row.get("ConfigSource") == "DHCPv4"
        and _configured(row)
        and row.get("Gateway")
    }
    if len(addresses) != 1 or len(routers) != 1:
        raise LeaseNotReady("underlay requires one verified DHCP IPv4 address and router")
    path = Path(data.get("NetworkFile") or "")
    if path.parent not in NETWORK_ROOTS or not re.fullmatch(r"[A-Za-z0-9_.-]+\.network", path.name):
        raise UnderlayError("networkd matching file is missing or unsafe")
    files = {str(path): hashlib.sha256(_read(path)).hexdigest()} if configuration else {}
    for raw in data.get("NetworkFileDropins", []):
        extra = Path(raw)
        if (
            extra.parent.parent not in NETWORK_ROOTS
            or extra.parent.name != path.name + ".d"
            or extra.suffix != ".conf"
        ):
            raise UnderlayError("networkd drop-in identity is unsafe")
        if configuration and extra.name != DROPIN:
            files[str(extra)] = hashlib.sha256(_read(extra)).hexdigest()
    routes = _json(["ip", "-j", "-4", "route", "show", "table", "main", "dev", interface])
    if not isinstance(routes, list) or any(not isinstance(row, dict) for row in routes):
        raise UnderlayError("underlay kernel routes are malformed")
    gateway, address = next(iter(routers)), next(iter(addresses))
    if not any(
        row.get("dst") == "default"
        and row.get("gateway") == gateway
        and row.get("protocol") == "dhcp"
        and not row.get("nexthops")
        for row in routes
    ):
        raise LeaseNotReady("DHCP router does not match the physical kernel route")
    managed = tuple(
        {
            "dst": "default"
            if row["DestinationPrefixLength"] == 0
            else f"{_ip(row['Destination'])}/{row['DestinationPrefixLength']}",
            "gateway": _ip(row["Gateway"]) if row.get("Gateway") else None,
            "protocol": {2: "kernel", 4: "static", 16: "dhcp"}.get(
                row.get("Protocol"), str(row.get("Protocol"))
            ),
            "metric": row.get("Priority", 0),
            "scope": row.get("ScopeString"),
            "prefsrc": _ip(row["PreferredSource"]) if row.get("PreferredSource") else None,
        }
        for row in data.get("Routes", [])
        if row.get("Family") == 2
        and row.get("Table") == 254
        and row.get("ConfigSource") in {"static", "DHCPv4"}
        and _configured(row)
    )
    loaded = str(NETWORK_ROOTS[0] / (path.name + ".d") / DROPIN) in data.get(
        "NetworkFileDropins", []
    )
    dhcp_routes = tuple(
        (f"{_ip(row['Destination'])}/{row['DestinationPrefixLength']}", _ip(row["Gateway"]))
        for row in data.get("Routes", [])
        if row.get("Family") == 2
        and row.get("ConfigSource") == "DHCPv4"
        and row.get("Protocol") == 16
        and row.get("Table") == 254
        and _configured(row)
        and row.get("Gateway")
        and row.get("Destination")
        and row.get("PreferredSource")
        and _ip(row["PreferredSource"]) == address
    )
    return Network(
        interface,
        data["Index"],
        address,
        gateway,
        str(path),
        digest(files) if configuration else "",
        tuple(routes),
        managed,
        loaded,
        dhcp_routes,
    )


def _dropin(network: Network) -> Path:
    return NETWORK_ROOTS[0] / (Path(network.network_file).name + ".d") / DROPIN


def render(destinations: tuple[str, ...]) -> str:
    return underlay_state.render(destinations)


def _destination(row: dict[str, Any]) -> str:
    return str(ipaddress.IPv4Network("0.0.0.0/0" if row.get("dst") == "default" else row["dst"]))


def _gateway(prefix: str, network: Network) -> str:
    """Match networkd's DHCP classless-route precedence for Gateway=_dhcp4."""
    target = ipaddress.IPv4Network(prefix)
    candidates = [
        (ipaddress.IPv4Network(p).prefixlen, gateway)
        for p, gateway in network.dhcp_routes
        if target.subnet_of(ipaddress.IPv4Network(p))
    ]
    if not candidates:
        return network.gateway
    maximum = max(length for length, _ in candidates)
    choices = {gateway for length, gateway in candidates if length == maximum}
    if len(choices) != 1:
        raise UnderlayError("DHCP has ambiguous gateways for an underlay destination")
    return choices.pop()


def _exact(row: dict[str, Any], prefix: str, network: Network) -> bool:
    return (
        _destination(row) == prefix
        and row.get("dev", network.interface) == network.interface
        and row.get("gateway") == _gateway(prefix, network)
        and row.get("protocol") == "dhcp"
        and row.get("metric") == METRIC
        and row.get("scope", "global") == "global"
        and str(row.get("table", "main")) in {"main", "254"}
        and row.get("type", "unicast") == "unicast"
        and not any(row.get(k) for k in ("nexthops", "nhid", "via", "prefsrc", "src"))
    )


def _connected(row: dict[str, Any], prefix: str, network: Network) -> bool:
    return (
        _destination(row) == prefix
        and row.get("dev", network.interface) == network.interface
        and row.get("protocol") == "kernel"
        and row.get("scope") == "link"
        and not any(row.get(k) for k in ("gateway", "via", "nexthops", "nhid"))
    )


def _ledger() -> dict[str, Any] | None:
    try:
        return underlay_state.decode(_read(STATE), network_root=NETWORK_ROOTS[0])
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as error:
        raise UnderlayError("underlay ownership ledger is invalid") from error


def preflight_reconfigure(cfg: dict[str, Any], *, configuration: bool = True) -> None:
    """Do not let networkd reconfigure erase foreign physical-interface routes."""
    expected = intent(cfg)
    links = _json(["networkctl", "status", "--json=short", "--no-pager", "--all"])
    if not isinstance(links, dict) or not isinstance(links.get("Interfaces"), list):
        raise UnderlayError("networkd reload scope cannot be observed")
    if any(
        link.get("NetworkFile") and link.get("Name") != expected.interface
        for link in links["Interfaces"]
    ):
        raise UnderlayError(
            "networkd reload would affect another managed interface; "
            "multiple managed interfaces require an explicit routing design"
        )
    network = observe(expected.interface, configuration=configuration)
    previous = _ledger()
    known = set(previous["destinations"]) if previous else set()
    old = Network(**previous["network"]) if previous else network
    routes = []
    from .enforced_routing import normalize_rule

    rules_owner = enforcement(cfg)
    owned_rules = rules_owner.rules(rules_owner.read())
    # networkd's reconfiguration cleans every table and family on the link,
    # plus manager-owned routes and policy rules. Main-table readiness alone
    # cannot establish that this wider operation preserves foreign state.
    for family in ("-4", "-6"):
        rules = _json(["ip", "-j", family, "-details", "-N", "rule", "show"])
        rows = _json(["ip", "-j", family, "route", "show", "table", "all"])
        if any(
            not isinstance(values, list) or any(not isinstance(row, dict) for row in values)
            for values in (rules, rows)
        ):
            raise UnderlayError("networkd reconfiguration routing inventory is malformed")
        if any(
            rule.get("protocol") not in ("kernel", "2", 2)
            and not (family == "-4" and normalize_rule(rule) in owned_rules)
            for rule in rules
        ):
            raise UnderlayError("network reconfiguration may remove a foreign policy rule")
        for row in rows:
            if row.get("protocol") == "kernel":
                continue  # networkd never removes kernel-owned routes or rules.
            if row.get("dev") and row["dev"] != expected.interface:
                continue  # Other unmanaged links are not reconfigured.
            if family != "-4" or str(row.get("table", "main")) not in {"main", "254"}:
                raise UnderlayError("network reconfiguration may remove an unmanaged route")
            routes.append(row)
    for row in routes:
        if _destination(row) in known and _exact(row, _destination(row), old):
            continue
        if previous and any(
            _exact(row, retired["prefix"], Network(**retired["network"]))
            for retired in previous.get("retire", [])
        ):
            continue
        if any(
            all(
                (_destination(row) == ("0.0.0.0/0" if value == "default" else value))
                if key == "dst"
                else row.get(key, 0 if key == "metric" else "global" if key == "scope" else None)
                == value
                for key, value in managed.items()
            )
            for managed in network.managed_routes
        ):
            continue
        # A protocol name is not ownership; even an identical manual route is foreign.
        raise UnderlayError(
            "network reconfiguration would affect an unmanaged physical route; "
            "review and persist or remove that route before apply"
        )


def enforcement(cfg: dict[str, Any], *, configured: bool = False):
    from ..ha_tunnel_policy import connection_policies
    from .enforced_routing import PolicyRouting

    ha = cfg.get("vm_ha")
    return PolicyRouting(
        STATE.parent / ("vm-ha" if ha is not None else "ordinary") / "enforced-routing.json",
        cluster=ha["cluster_id"] if ha else "ordinary",
        policies=connection_policies(cfg) if configured else (),
        run=run,
    )


def netplan_content(interface: str = "eth0") -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", interface):
        raise UnderlayError("underlay interface is invalid")
    return f"""# generated by nebius-vpngw-agent
# Disable IPv4 link-local (APIPA 169.254/16) to prevent conflicts with VPN inner IPs
# This is SAFE - it does NOT block DHCP routes (default gateway, DNS, etc.)
network:
  version: 2
  ethernets:
    {interface}:
      dhcp4: true
      dhcp4-overrides:
        use-dns: true
        use-routes: true
      link-local: [ ipv6 ]
"""


def prepare_network_locked(
    cfg: dict[str, Any], *, netplan_path: Path | None = None, command=None
) -> None:
    """Fenced deployment owner: rebuild networking before strict route admission."""
    expected = intent(cfg)
    path = netplan_path or Path("/etc/netplan/99-nebius-vpngw.yaml")
    content = netplan_content(expected.interface)
    try:
        previous = _read(path).decode()
    except FileNotFoundError:
        previous = None
    if previous is not None and previous != content:
        raise UnderlayError("managed Netplan configuration was modified outside its owner")
    record = _ledger()
    if record and record["phase"] == "pending" and record["intent"] != expected.identity:
        raise UnderlayError(
            "underlay update is pending; resume its original apply before changing intent"
        )
    try:
        underlay_state.verify_files(record, network_root=NETWORK_ROOTS[0])
    except (OSError, ValueError) as error:
        raise UnderlayError("underlay drop-in ownership could not be verified") from error
    if previous == content and ready(cfg):
        return
    # The DHCP/NIC/kernel inventory remains authoritative even when networkd
    # still reports a generated filename removed by the retained-host reset.
    # It cannot be used as persistence proof: strict observation follows apply.
    preflight_reconfigure(cfg, configuration=False)
    _write(path, content)
    path.chmod(0o600)
    result = (command or run)(["netplan", "apply"], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise UnderlayError("managed network configuration could not be applied")
    deadline = time.monotonic() + 30
    while True:
        try:
            observe(expected.interface)
            break
        except (FileNotFoundError, LeaseNotReady):
            if time.monotonic() >= deadline:
                raise UnderlayError(
                    "networkd did not load the managed network configuration"
                ) from None
            time.sleep(0.2)
    reconcile_locked(cfg, reconfigure=True)


def _check_dropin(previous: dict[str, Any] | None, path: Path, content: str) -> None:
    try:
        actual = _read(path).decode()
    except FileNotFoundError:
        return
    if not previous or previous.get("dropin") != str(path):
        raise UnderlayError("underlay drop-in already exists without product ownership")
    accepted = {previous.get("content"), previous.get("previous_content")}
    if actual not in accepted:
        raise UnderlayError("underlay drop-in was modified outside its owner")


def reconcile_locked(cfg: dict[str, Any], *, reconfigure: bool = False) -> int:
    expected = intent(cfg)
    network = observe(expected.interface)
    path = _dropin(network)
    previous = _ledger()
    if previous and previous["phase"] == "pending" and previous["intent"] != expected.identity:
        raise UnderlayError(
            "underlay update is pending; resume its original apply before changing intent"
        )
    connected = {
        p for p in expected.destinations if any(_connected(r, p, network) for r in network.routes)
    }
    destinations = tuple(p for p in expected.destinations if p not in connected)
    content = render(destinations)
    _check_dropin(previous, path, content)
    retire_files = list(previous.get("retire_files", [])) if previous else []
    if previous and previous.get("dropin") != str(path):
        retire_files.append({"path": previous["dropin"], "content": previous["content"]})
    for retired in retire_files:
        old_path = Path(retired["path"])
        if old_path.exists() or old_path.is_symlink():
            if _read(old_path).decode() != retired["content"]:
                raise UnderlayError("retired underlay drop-in was modified outside its owner")
    known = set(previous["destinations"]) if previous else set()
    for prefix in destinations:
        for row in network.routes:
            if _destination(row) == prefix and not (
                prefix in known and _exact(row, prefix, network)
            ):
                raise UnderlayError("underlay destination conflicts with a foreign physical route")
    changed = (
        not previous
        or previous.get("intent") != expected.identity
        or previous.get("content") != content
        or previous.get("phase") != "committed"
        or previous.get("network", {}).get("config_digest") != network.config_digest
        or previous.get("network", {}).get("index") != network.index
        or previous.get("boot") != BOOT.read_text().strip()
        or previous.get("dropin") != str(path)
        or not path.exists()
        or not network.owned_dropin_loaded
    )
    if changed and not reconfigure:
        raise UnderlayError("underlay intent is not committed; run normal apply")
    if reconfigure and changed:
        preflight_reconfigure(cfg)
        record = {
            "schema": SCHEMA,
            "phase": "pending",
            "intent": expected.identity,
            "network": asdict(network) | {"routes": []},
            "dropin": str(path),
            "content": content,
            "previous_content": (
                previous.get("previous_content")
                if previous and previous["phase"] == "pending"
                else previous.get("content")
                if previous
                else None
            ),
            "destinations": list(destinations),
            "boot": BOOT.read_text().strip(),
            "retire_files": retire_files,
            "retire": (
                previous.get("retire", [])
                + [
                    {"prefix": p, "network": previous["network"]}
                    for p in sorted(known - set(destinations))
                ]
            )
            if previous
            else [],
        }
        _directory(STATE.parent)
        atomic_write_json(STATE, record)
        _write(path, content)
        for retired in retire_files:
            old_path = Path(retired["path"])
            if old_path.exists():
                if _read(old_path).decode() != retired["content"]:
                    raise UnderlayError("retired underlay drop-in changed before removal")
                old_path.unlink()
                fd = os.open(old_path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        _command(["networkctl", "reload"])
        _command(["networkctl", "reconfigure", expected.interface])
        deadline = time.monotonic() + 15
        while True:
            try:
                network = observe(expected.interface)
                if all(
                    any(_exact(row, p, network) for row in network.routes) for p in destinations
                ):
                    break
            except LeaseNotReady:
                pass
            if time.monotonic() >= deadline:
                raise UnderlayError("networkd did not install the owned underlay routes")
            time.sleep(0.2)
        if str(_dropin(network)) != str(path):
            raise UnderlayError("networkd matching file changed during reconciliation")
        if enforcement(cfg).read():
            # networkd may remove non-kernel rules; the persistent nft guard
            # remains in place throughout reconstruction of the owned RPDB.
            enforcement(cfg, configured=True).install()
        # Retire only exact predecessor tuples, never a destination-wide delete.
        for retired in record["retire"]:
            old = Network(**retired["network"])
            if any(_exact(row, retired["prefix"], old) for row in network.routes):
                _command(_route_command("del", retired["prefix"], old))
        record.update(
            phase="committed",
            network=asdict(network) | {"routes": []},
            previous_content=None,
            retire=[],
            retire_files=[],
        )
        atomic_write_json(STATE, record)
    else:
        if previous is None or previous["phase"] != "committed":
            raise UnderlayError("underlay persistence is not committed")
        if _read(path).decode() != content:
            raise UnderlayError("underlay persistence changed")
        for prefix in destinations:
            if not any(_exact(row, prefix, network) for row in network.routes):
                _command(_route_command("add", prefix, network))
    verify(cfg)
    return int(changed)


def _route_command(action: str, prefix: str, network: Network) -> list[str]:
    return [
        "ip",
        "-4",
        "route",
        action,
        prefix,
        "via",
        _gateway(prefix, network),
        "dev",
        network.interface,
        "onlink",
        "table",
        "main",
        "proto",
        "dhcp",
        "metric",
        str(METRIC),
    ]


def verify(cfg: dict[str, Any]) -> str:
    expected = intent(cfg)
    network = observe(expected.interface)
    record = _ledger()
    if (
        record is None
        or record["phase"] != "committed"
        or record["intent"] != expected.identity
        or record["dropin"] != str(_dropin(network))
        or record["network"]["config_digest"] != network.config_digest
        or record["network"]["index"] != network.index
        or record["boot"] != BOOT.read_text().strip()
        or not network.owned_dropin_loaded
        or _read(_dropin(network)).decode() != record["content"]
    ):
        raise UnderlayError("underlay persistence proof is missing or stale; run normal apply")
    owned = set(record["destinations"])
    all_routes = _json(["ip", "-j", "-4", "route", "show", "table", "main"])
    if not isinstance(all_routes, list) or any(not isinstance(row, dict) for row in all_routes):
        raise UnderlayError("underlay route inventory is malformed")
    for prefix in expected.destinations:
        valid = any(
            (_exact(row, prefix, network) if prefix in owned else _connected(row, prefix, network))
            for row in network.routes
        )
        if not valid:
            raise UnderlayError("underlay kernel route is missing or conflicts with intent")
        # Local ranges may legitimately contain more-specific VPN destinations.
        # Control /32 paths must always resolve physically. Exact-prefix conflicts
        # are checked independently rather than testing an arbitrary subnet host.
        if any(
            _destination(row) == prefix and row.get("dev") != network.interface
            for row in all_routes
        ):
            raise UnderlayError("underlay and VPN have conflicting exact kernel destinations")
        if ipaddress.IPv4Network(prefix).prefixlen != 32:
            continue
        target = str(ipaddress.IPv4Network(prefix).network_address)
        result = _json(["ip", "-j", "-4", "route", "get", target])
        if not isinstance(result, list) or len(result) != 1:
            raise UnderlayError("underlay route lookup is unavailable")
        selected = result[0]
        if selected.get("dev") != network.interface or selected.get("gateway") not in (
            None,
            _gateway(prefix, network),
        ):
            raise UnderlayError("underlay route lookup selects a different path")
    return digest(
        {
            "intent": expected.identity,
            "network": asdict(network) | {"routes": []},
            "boot": BOOT.read_text().strip(),
            "content": record["content"],
        }
    )


def ready(cfg: dict[str, Any]) -> bool:
    reason = "ready"
    try:
        verify(cfg)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        # Closed source-owned reasons only; never native output, paths or values.
        reason = {
            "underlay persistence proof is missing or stale; run normal apply": "persistence-unproven",
            "underlay route inventory is malformed": "route-inventory-malformed",
            "underlay kernel route is missing or conflicts with intent": "physical-route-missing",
            "underlay and VPN have conflicting exact kernel destinations": "vpn-route-conflict",
            "underlay route lookup is unavailable": "route-lookup-unavailable",
            "underlay route lookup selects a different path": "route-lookup-drift",
            "underlay requires one verified DHCP IPv4 address and router": "dhcp-lease-unavailable",
            "DHCP router does not match the physical kernel route": "dhcp-route-mismatch",
            "networkd cannot identify the primary interface": "interface-unavailable",
            "underlay command failed: networkctl": "networkd-command-failed",
            "underlay command failed: ip": "ip-command-failed",
        }.get(
            str(error),
            "deadline-exceeded" if isinstance(error, TimeoutError) else "observation-unavailable",
        )
    if cfg.get("vm_ha") is not None:
        from .vm_ha.diagnostics import diagnostic

        diagnostic("routing-safety-observation", scope="underlay", reason=reason)
    return reason == "ready"


def main() -> None:
    """Boot-only root owner. Service ordering and the current guard provide fencing."""
    import yaml

    from ..ha_repair import writer_locks
    from ..ordinary_operations import mutation_lock

    # An apply owner already holding the routing lock prepares and verifies the
    # routes directly. Its protocol service dependencies only read that proof.
    cfg = yaml.safe_load(_read(CONFIG))
    if ready(cfg):
        return
    ha = cfg.get("vm_ha") is not None
    state_dir = STATE.parent / "vm-ha"
    with (
        writer_locks(state_dir, deadline=time.monotonic() + 10)
        if ha
        else mutation_lock(deadline=time.monotonic() + 10)
    ):
        cfg = yaml.safe_load(_read(CONFIG))
        if (cfg.get("vm_ha") is not None) != ha:
            raise UnderlayError("underlay mode changed while acquiring writer locks")
        if cfg.get("vm_ha") is not None:
            from .main import _vm_ha_writer_inhibition_operation_id
            from .vm_ha.auto_healing import require_auto_healing_writer_quiescent

            manifest = cfg["vm_ha"]
            require_auto_healing_writer_quiescent(state_dir)
            inhibition = _vm_ha_writer_inhibition_operation_id(
                state_dir=state_dir,
                cluster_id=manifest["cluster_id"],
                node_id=manifest["node"]["node_id"],
                generation_id=manifest["generation"]["generation_id"],
            )
            if inhibition is not None:
                raise UnderlayError(
                    "underlay deployment preparation is pending; resume normal apply"
                )
            guard = json.loads(_read(state_dir / "guard.json"))
            if (
                guard.get("guard_boot_id") != BOOT.read_text().strip()
                or guard.get("data_plane_mode") not in {"blocked", "passive"}
                or _command(["sysctl", "-n", "net.ipv4.ip_forward"]).strip() != "0"
            ):
                raise UnderlayError(
                    "underlay boot preparation requires the current forwarding guard"
                )
        else:
            from .ordinary import destination_protection

            destination_protection(cfg).install()
        reconcile_locked(cfg, reconfigure=True)


if __name__ == "__main__":
    main()
