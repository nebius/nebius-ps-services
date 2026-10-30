#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

CONFIG_PATH = Path("/etc/nebius-wireguard/config.json")
WG_DIR = Path("/etc/wireguard")
WG_IF = "wg0"
STATE_DIR = Path("/var/lib/nebius-wireguard")
CLIENTS_DIR = Path("/var/lib/wireguard/clients")
REGISTRY_PATH = STATE_DIR / "clients.json"
RUNTIME_CONFIG_PATH = STATE_DIR / "runtime.json"
LOCK_PATH = STATE_DIR / "clients.lock"
CLIENT_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,13}[a-z0-9])?$")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(
    cmd: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, input=input_text, check=check, capture_output=True, text=True, timeout=45
    )


def require_root() -> None:
    if os.geteuid() != 0:
        fail("nebius-wireguard-client must run as root")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        fail(f"missing WireGuard config: {CONFIG_PATH}")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        fail(f"{CONFIG_PATH} must contain a JSON object")
    return payload


def ensure_dirs() -> None:
    WG_DIR.mkdir(parents=True, exist_ok=True)
    WG_DIR.chmod(0o700)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    CLIENTS_DIR.chmod(0o750)


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"version": 1, "clients": []}
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        fail(f"{REGISTRY_PATH} must contain a JSON object")
    clients = payload.get("clients")
    if not isinstance(clients, list):
        fail(f"{REGISTRY_PATH} must contain a clients list")
    return payload


def save_registry(registry: dict) -> None:
    tmp_path = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.chmod(0o600)
    tmp_path.replace(REGISTRY_PATH)


def with_registry_lock(path: Path | None = None):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = (path or LOCK_PATH).open("w", encoding="utf-8")
    deadline = time.monotonic() + 30
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                fail("WireGuard client state is busy; retry after the current operation completes")
            time.sleep(0.1)
    return handle


def wg_genkey() -> str:
    return run(["wg", "genkey"]).stdout.strip()


def wg_pubkey(private_key: str) -> str:
    return run(["wg", "pubkey"], input_text=private_key + "\n").stdout.strip()


def wg_genpsk() -> str:
    return run(["wg", "genpsk"]).stdout.strip()


def ensure_server_keys() -> tuple[str, str]:
    private_path = WG_DIR / "private.key"
    public_path = WG_DIR / "public.key"
    if not private_path.exists():
        private_path.write_text(wg_genkey() + "\n", encoding="utf-8")
        private_path.chmod(0o600)
    private_key = private_path.read_text(encoding="utf-8").strip()
    if not public_path.exists():
        public_path.write_text(wg_pubkey(private_key) + "\n", encoding="utf-8")
        public_path.chmod(0o644)
    public_key = public_path.read_text(encoding="utf-8").strip()
    return private_key, public_key


def tunnel_interface(config: dict) -> ipaddress.IPv4Interface:
    try:
        interface = ipaddress.ip_interface(str(config["wireguard_tunnel_cidr"]))
    except Exception as exc:
        fail(f"wireguard_tunnel_cidr must be a valid interface CIDR: {exc}")
    if interface.version != 4:
        fail("wireguard_tunnel_cidr must be IPv4")
    return interface


def detect_wan_interface() -> str:
    routes = json.loads(run(["ip", "-j", "-4", "route", "show", "default"]).stdout)
    devices = {r.get("dev") for r in routes if r.get("dev")}
    if len(devices) != 1:
        fail("WireGuard requires one unambiguous uplink interface")
    return devices.pop()


def interface_mtu(interface: str) -> int:
    links = json.loads(run(["ip", "-j", "link", "show", "dev", interface]).stdout)
    if len(links) != 1 or type(links[0].get("mtu")) is not int:
        fail("Cannot determine WireGuard interface MTU")
    return links[0]["mtu"]


def choose_mtu(requested: int | None, uplink_mtu: int) -> int:
    limit = min(1420, uplink_mtu - 80)
    if limit < 1280:
        fail("Uplink MTU cannot support the configured WireGuard MTU range")
    if requested is not None and (type(requested) is not int or not 1280 <= requested <= limit):
        fail("wireguard_mtu must be an integer from 1280 through the detected uplink limit")
    return limit if requested is None else requested


def effective_mtu(config: dict) -> int:
    return choose_mtu(config.get("wireguard_mtu"), interface_mtu(detect_wan_interface()))


def existing_server_keys() -> tuple[str, str]:
    try:
        private = (WG_DIR / "private.key").read_text().strip()
        public = (WG_DIR / "public.key").read_text().strip()
    except OSError:
        fail("Existing WireGuard server identity is missing; refusing to generate keys")
    if not private or wg_pubkey(private) != public:
        fail("Existing WireGuard server identity is inconsistent")
    return private, public


def atomic(path: Path, text: str, mode: int = 0o600) -> None:
    if path.is_symlink():
        fail("Refusing a symbolic link in WireGuard managed state")
    fd, temporary = tempfile.mkstemp(prefix=".network-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mss_rule(direction: str) -> list[str]:
    return [
        direction,
        WG_IF,
        "-p",
        "tcp",
        "--tcp-flags",
        "SYN,RST",
        "SYN",
        "-m",
        "comment",
        "--comment",
        "nebius-wireguard-mss-v1",
        "-j",
        "TCPMSS",
        "--clamp-mss-to-pmtu",
    ]


def mss_rules(action: str) -> None:
    with with_registry_lock(STATE_DIR / "network-firewall.lock"):
        _mss_rules_locked(action)


def _mss_rules_locked(action: str) -> None:
    for direction in ("-i", "-o"):
        rule = mss_rule(direction)

        def count(rule=rule):
            lines = run(
                ["iptables", "-w", "5", "-t", "mangle", "-S", "FORWARD"]
            ).stdout.splitlines()
            # iptables may insert its implicit TCP matcher on readback.
            wanted = ["-A", "FORWARD", *rule]
            return sum(shlex.split(line.replace(" -m tcp", "")) == wanted for line in lines)

        present = count()
        desired = 1 if action == "up" else 0
        if present < desired:
            run(["iptables", "-w", "5", "-t", "mangle", "-A", "FORWARD", *rule])
        for _ in range(max(0, present - desired)):
            run(["iptables", "-w", "5", "-t", "mangle", "-D", "FORWARD", *rule])
        if count() != desired:
            fail("WireGuard MSS rule readback failed")


def assert_no_pending_network() -> None:
    if (STATE_DIR / "network-rollback.json").exists() or (
        STATE_DIR / "network-upgrade.json"
    ).exists():
        fail("WireGuard networking update is pending; resume it before issuing client changes")


def refresh_config_text(text: str, mtu: int) -> str:
    # Preserve every client option and all credentials; only this owned field changes.
    lines = text.splitlines()
    section = ""
    result = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.lower()
            result.append(line)
            if section == "[interface]":
                if found:
                    fail("Duplicate WireGuard Interface section")
                result.append(f"MTU = {mtu}")
                found = True
        elif section == "[interface]" and re.match(r"(?i)^mtu\s*=", stripped):
            continue
        else:
            result.append(line)
    if not found:
        fail("Missing WireGuard Interface section")
    return "\n".join(result) + "\n"


def validate_clients(registry: dict) -> list[tuple[dict, Path, str]]:
    clients = []
    _server_private, server_public = existing_server_keys()
    for client in registry["clients"]:
        name = client.get("name", "")
        if not CLIENT_NAME_RE.fullmatch(name):
            fail("Invalid registered client name")
        folder = CLIENTS_DIR / name
        path = folder / f"{name}.conf"
        if folder.is_symlink() or path.is_symlink():
            fail("Invalid registered client path")
        try:
            private = (folder / "private.key").read_text().strip()
            public = (folder / "public.key").read_text().strip()
            psk = (folder / "preshared.key").read_text().strip()
            text = path.read_text()
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(text)
            if (
                wg_pubkey(private) != public
                or public != client["public_key"]
                or psk != client["preshared_key"]
                or config["Interface"]["PrivateKey"] != private
                or config["Peer"]["PresharedKey"] != psk
                or config["Peer"]["PublicKey"] != server_public
                or config["Interface"]["Address"] != client["client_wg_tunnel_address"]
            ):
                fail("Registered client identity is inconsistent")
        except (OSError, KeyError, configparser.Error):
            fail("Existing client identity is missing; refusing to generate keys")
        clients.append((client, path, text))
    return clients


def network_status(peer_public_key: str = "") -> dict:
    require_root()
    _private, public = existing_server_keys()
    if run(["wg", "show", WG_IF, "public-key"]).stdout.strip() != public:
        fail("Running WireGuard identity differs from its persisted identity")
    transfers = {}
    for line in run(["wg", "show", WG_IF, "transfer"]).stdout.splitlines():
        key, received, sent = line.split()
        transfers[key] = [int(received), int(sent)]
    handshakes = dict(
        line.split() for line in run(["wg", "show", WG_IF, "latest-handshakes"]).stdout.splitlines()
    )
    allowed = dict(
        line.split(None, 1)
        for line in run(["wg", "show", WG_IF, "allowed-ips"]).stdout.splitlines()
    )
    return {
        "schema": 1,
        "mtu": interface_mtu(WG_IF),
        "uplink_mtu": interface_mtu(detect_wan_interface()),
        "server_public_key": public,
        "peer_found": peer_public_key in transfers,
        "peer_transfer": transfers.get(peer_public_key),
        "peer_handshake": int(handshakes.get(peer_public_key, 0)),
        "peer_allowed_ips": allowed.get(peer_public_key, ""),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def reconcile_network(request: dict) -> dict:
    require_root()
    if request.get("revision") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        fail("WireGuard networking revision does not match the executing asset")
    with with_registry_lock():
        config = load_config()
        if not REGISTRY_PATH.is_file():
            fail("Existing WireGuard registry is missing; reconciliation cannot infer peers")
        registry = load_registry()
        private, public = existing_server_keys()
        clients = validate_clients(registry)
        before = CONFIG_PATH.read_text()
        server = WG_DIR / f"{WG_IF}.conf"
        old_server = server.read_text()
        configured_peers = set(re.findall(r"(?m)^PublicKey\s*=\s*(\S+)\s*$", old_server))
        expected_peers = {client["public_key"] for client in registry["clients"]}
        live_peers = set(run(["wg", "show", WG_IF, "peers"]).stdout.split())
        if (
            configured_peers != expected_peers
            or live_peers != expected_peers
            or run(["wg", "show", WG_IF, "public-key"]).stdout.strip() != public
            or re.findall(r"(?m)^PrivateKey\s*=\s*(\S+)\s*$", old_server) != [private]
        ):
            fail("WireGuard registry, persistent configuration and running identity disagree")
        old_mtu = interface_mtu(WG_IF)
        config["wireguard_mtu"] = request.get("wireguard_mtu")
        mtu = effective_mtu(config)
        rendered = refresh_config_text(old_server, mtu)
        hooks = (
            "PostUp = /usr/local/sbin/nebius-wireguard-client network-up",
            "PostDown = /usr/local/sbin/nebius-wireguard-client network-down",
        )
        rendered = (
            "\n".join(line for line in rendered.splitlines() if line.strip() not in hooks) + "\n"
        )
        rendered = rendered.replace("[Interface]\n", "[Interface]\n" + "\n".join(hooks) + "\n", 1)
        snapshots = [(CONFIG_PATH, before), (server, old_server), *[(p, t) for _, p, t in clients]]
        # Protected persistent recovery evidence survives an interrupted SSH session.
        rollback = STATE_DIR / "network-rollback.json"
        if rollback.exists():
            pending = json.loads(rollback.read_text())
            if pending["desired"] != request:
                fail("A different networking revision is pending; resume it before changing intent")
        else:
            atomic(
                rollback,
                json.dumps(
                    {"desired": request, "mtu": old_mtu, "files": {str(p): t for p, t in snapshots}}
                ),
            )
        try:
            mss_rules("up")
            run(["ip", "link", "set", "dev", WG_IF, "mtu", str(mtu)])
            atomic(CONFIG_PATH, json.dumps(config, sort_keys=True) + "\n")
            atomic(server, rendered)
            for _client, path, text in clients:
                atomic(path, refresh_config_text(text, mtu))
            if interface_mtu(WG_IF) != mtu:
                fail("WireGuard MTU readback disagrees with requested revision")
            atomic(
                STATE_DIR / "network-revision.json",
                json.dumps(
                    {
                        "schema": 1,
                        "mtu": mtu,
                        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    }
                )
                + "\n",
            )
            rollback.unlink()
        except BaseException:
            # Keep evidence and exact candidate for idempotent resume. Never undo an
            # uncertain remote effect or restore keys from a stale automatic rollback.
            raise
        return {"schema": 1, "mtu": mtu, "clients_preserved": len(clients)}


def refresh_client(request: dict) -> dict:
    require_root()
    with with_registry_lock():
        assert_no_pending_network()
        existing_server_keys()
        found = [
            (c, p, t)
            for c, p, t in validate_clients(load_registry())
            if c["public_key"] == request.get("public_key")
        ]
        if len(found) != 1:
            fail("Refresh requires one existing registered client")
        client, path, text = found[0]
        text = refresh_config_text(text, effective_mtu(load_config()))
        atomic(path, text)
        return {"name": client["name"], "config": text}


def public_endpoint_host(config: dict) -> str:
    endpoint = str(config.get("endpoint_host") or "").strip()
    if endpoint:
        return endpoint
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            detected = response.read().decode("utf-8").strip()
            if detected:
                return detected
    except Exception:
        pass
    return "REPLACE_WITH_SERVER_PUBLIC_IP_OR_DNS"


def normalize_cidr_list(values: list[str], *, field: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except Exception as exc:
            fail(f"{field} contains invalid CIDR {value!r}: {exc}")
        if network.version != 4:
            fail(f"{field} currently supports IPv4 CIDRs only")
        normalized.append(network.with_prefixlen)
    return normalized


def normalize_cli_cidr_values(
    values: list[str] | None, *, field: str, require_single_option: bool = False
) -> list[str]:
    if not values:
        fail(f"{field} requires at least one CIDR")
    if require_single_option and len(values) != 1:
        fail(f"{field} accepts one comma-separated CIDR list")
    expanded: list[str] = []
    for value in values:
        parts = [part.strip() for part in str(value).split(",")]
        if any(not part for part in parts):
            fail(f"{field} must be a comma-separated CIDR list without empty entries")
        expanded.extend(parts)
    deduped: list[str] = []
    seen: set[str] = set()
    for cidr in normalize_cidr_list(expanded, field=field):
        if cidr not in seen:
            deduped.append(cidr)
            seen.add(cidr)
    return deduped


def normalize_dns(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value))
        except Exception as exc:
            fail(f"dns contains invalid IP address {value!r}: {exc}")
        if address.version != 4:
            fail("dns currently supports IPv4 addresses only")
        normalized.append(str(address))
    return normalized


def initial_local_subnets(config: dict) -> list[str]:
    return normalize_cidr_list(list(config.get("local_subnets") or []), field="local_subnets")


def load_runtime_config(config: dict) -> dict:
    if not RUNTIME_CONFIG_PATH.exists():
        return {
            "version": 1,
            "local_subnets": initial_local_subnets(config),
        }
    payload = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        fail(f"{RUNTIME_CONFIG_PATH} must contain a JSON object")
    local_subnets = payload.get("local_subnets", [])
    if not isinstance(local_subnets, list):
        fail(f"{RUNTIME_CONFIG_PATH} local_subnets must be a list")
    return {
        "version": 1,
        "local_subnets": normalize_cidr_list(local_subnets, field="local_subnets"),
    }


def save_runtime_config(runtime_config: dict) -> None:
    tmp_path = RUNTIME_CONFIG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(runtime_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp_path.chmod(0o600)
    tmp_path.replace(RUNTIME_CONFIG_PATH)


def ensure_runtime_config(config: dict) -> dict:
    runtime_config = load_runtime_config(config)
    if not RUNTIME_CONFIG_PATH.exists():
        save_runtime_config(runtime_config)
    return runtime_config


def effective_local_subnets(config: dict) -> list[str]:
    return load_runtime_config(config)["local_subnets"]


def client_slots(config: dict, registry: dict) -> tuple[int, int]:
    interface = tunnel_interface(config)
    network = interface.network
    total_hosts = (
        network.num_addresses if network.prefixlen >= 31 else max(network.num_addresses - 2, 0)
    )
    reserved_server = 1 if interface.ip in network else 0
    used_clients = {
        str(ipaddress.ip_interface(str(client["client_wg_tunnel_address"])).ip)
        for client in registry.get("clients", [])
        if isinstance(client, dict) and client.get("client_wg_tunnel_address")
    }
    capacity = max(total_hosts - reserved_server, 0)
    remaining = max(capacity - len(used_clients), 0)
    return capacity, remaining


def next_client_address(config: dict, registry: dict) -> str:
    interface = tunnel_interface(config)
    network = interface.network
    used_ips = {interface.ip}
    for client in registry.get("clients", []):
        if not isinstance(client, dict):
            continue
        value = client.get("client_wg_tunnel_address")
        if value:
            used_ips.add(ipaddress.ip_interface(str(value)).ip)
    for host in network.hosts():
        if host not in used_ips:
            return f"{host}/32"
    fail(f"WireGuard tunnel CIDR {network.with_prefixlen} is exhausted")


def normalize_client_address(value: str | None, config: dict, registry: dict) -> str:
    if not value:
        return next_client_address(config, registry)
    interface = tunnel_interface(config)
    try:
        client_interface = ipaddress.ip_interface(str(value))
    except Exception as exc:
        fail(f"client_wg_tunnel_address must be a valid CIDR: {exc}")
    if client_interface.version != 4 or client_interface.network.prefixlen != 32:
        fail("client_wg_tunnel_address must be an IPv4 /32, for example 10.8.0.2/32")
    if client_interface.ip == interface.ip:
        fail("client_wg_tunnel_address cannot be the WireGuard server address")
    if client_interface.ip not in interface.network:
        fail("client_wg_tunnel_address must be inside wireguard_tunnel_cidr")
    for client in registry.get("clients", []):
        if not isinstance(client, dict) or not client.get("client_wg_tunnel_address"):
            continue
        if (
            ipaddress.ip_interface(str(client["client_wg_tunnel_address"])).ip
            == client_interface.ip
        ):
            fail(
                f"client_wg_tunnel_address is already allocated: {client_interface.with_prefixlen}"
            )
    return client_interface.with_prefixlen


def server_config_text(config: dict, registry: dict, server_private_key: str) -> str:
    interface = tunnel_interface(config)
    wg_net = interface.network.with_prefixlen
    wan_if = detect_wan_interface()
    nat_mode = bool(config.get("nat_mode", True))
    lines = [
        "[Interface]",
        f"PrivateKey = {server_private_key}",
        f"Address = {interface.with_prefixlen}",
        f"MTU = {effective_mtu(config)}",
        "PostUp = /usr/local/sbin/nebius-wireguard-client network-up",
        "PostDown = /usr/local/sbin/nebius-wireguard-client network-down",
        f"ListenPort = {int(config.get('wireguard_listen_port', 51820))}",
        "SaveConfig = false",
        "PostUp = sysctl -w net.ipv4.ip_forward=1",
        "PostUp = iptables -A FORWARD -i %i -j ACCEPT",
        "PostUp = iptables -A FORWARD -o %i -m state --state RELATED,ESTABLISHED -j ACCEPT",
    ]
    if nat_mode:
        lines.append(
            f"PostUp = iptables -t nat -A POSTROUTING -s {wg_net} -o {wan_if} -j MASQUERADE"
        )
    lines.extend(
        [
            "PostDown = iptables -D FORWARD -i %i -j ACCEPT",
            "PostDown = iptables -D FORWARD -o %i -m state --state RELATED,ESTABLISHED -j ACCEPT",
        ]
    )
    if nat_mode:
        lines.append(
            f"PostDown = iptables -t nat -D POSTROUTING -s {wg_net} -o {wan_if} -j MASQUERADE"
        )
    for client in registry.get("clients", []):
        if not isinstance(client, dict):
            continue
        lines.extend(
            [
                "",
                "[Peer]",
                f"# {client['name']}",
                f"PublicKey = {client['public_key']}",
                f"PresharedKey = {client['preshared_key']}",
                f"AllowedIPs = {client['client_wg_tunnel_address']}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_server_config(config: dict, registry: dict) -> None:
    server_private_key, _server_public_key = existing_server_keys()
    config_path = WG_DIR / f"{WG_IF}.conf"
    config_path.write_text(
        server_config_text(config, registry, server_private_key), encoding="utf-8"
    )
    config_path.chmod(0o600)
    run(["wg-quick", "strip", str(config_path)])


def configure_sysctl_and_firewall(config: dict) -> None:
    port = str(int(config.get("wireguard_listen_port", 51820)))
    wan_if = detect_wan_interface()
    Path("/etc/sysctl.d/99-wireguard.conf").write_text(
        "\n".join(
            [
                "net.ipv4.ip_forward=1",
                "net.ipv4.conf.all.accept_redirects=0",
                "net.ipv4.conf.default.accept_redirects=0",
                "net.ipv4.conf.all.secure_redirects=0",
                "net.ipv4.conf.default.secure_redirects=0",
                "net.ipv4.conf.all.send_redirects=0",
                "net.ipv4.conf.default.send_redirects=0",
                "net.ipv4.icmp_echo_ignore_broadcasts=1",
                "net.ipv4.tcp_syncookies=1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    run(["sysctl", "--system"], check=False)
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)

    ufw_defaults = Path("/etc/default/ufw")
    if ufw_defaults.exists():
        text = ufw_defaults.read_text(encoding="utf-8")
        text = re.sub(
            r"^DEFAULT_FORWARD_POLICY=.*$", 'DEFAULT_FORWARD_POLICY="DROP"', text, flags=re.M
        )
        ufw_defaults.write_text(text, encoding="utf-8")
    run(["ufw", "--force", "reset"])
    run(["ufw", "default", "deny", "incoming"])
    run(["ufw", "default", "allow", "outgoing"])
    run(["ufw", "logging", "on"], check=False)
    run(["ufw", "allow", f"{port}/udp"])
    run(["ufw", "allow", "OpenSSH"])
    run(["ufw", "route", "allow", "in", "on", WG_IF, "out", "on", wan_if])
    run(["ufw", "--force", "enable"])


def ensure_ssh_service() -> None:
    Path("/run/sshd").mkdir(mode=0o755, parents=True, exist_ok=True)
    result = run(["systemctl", "list-unit-files", "--type=service", "--no-legend"])
    service_names = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
    if "ssh.service" in service_names:
        service = "ssh"
    elif "sshd.service" in service_names:
        service = "sshd"
    else:
        fail("no ssh service found (ssh/sshd)")
    run(["sshd", "-t"])
    run(["augenrules", "--load"], check=False)
    run(["systemctl", "enable", "--now", "auditd"], check=False)
    run(["systemctl", "enable", "--now", "fail2ban"])
    run(["systemctl", "enable", "--now", service])
    run(["systemctl", "reload", service], check=False)


def init_server(_args: argparse.Namespace) -> None:
    require_root()
    ensure_dirs()
    with with_registry_lock():
        config = load_config()
        registry = load_registry()
        # An initialized gateway owns a registry even with no bootstrap clients.
        # Day-2 reconciliation must still reject a missing registry as lost state.
        if not REGISTRY_PATH.exists():
            save_registry(registry)
        ensure_server_keys()
        ensure_runtime_config(config)
        write_server_config(config, registry)
        configure_sysctl_and_firewall(config)
        ensure_ssh_service()
        run(["systemctl", "enable", "--now", f"wg-quick@{WG_IF}.service"])
        run(["systemctl", "restart", f"wg-quick@{WG_IF}.service"])


def build_client_config(config: dict, client: dict, client_private_key: str) -> str:
    _server_private_key, server_public_key = existing_server_keys()
    local_subnets = client.get("local_subnets") or [tunnel_interface(config).network.with_prefixlen]
    lines = [
        "[Interface]",
        f"PrivateKey = {client_private_key}",
        f"Address = {client['client_wg_tunnel_address']}",
        f"MTU = {effective_mtu(config)}",
    ]
    if client.get("dns"):
        lines.append(f"DNS = {','.join(client['dns'])}")
    lines.extend(
        [
            "",
            "[Peer]",
            f"PublicKey = {server_public_key}",
            f"PresharedKey = {client['preshared_key']}",
            f"Endpoint = {public_endpoint_host(config)}:{int(config.get('wireguard_listen_port', 51820))}",
            f"AllowedIPs = {','.join(local_subnets)}",
        ]
    )
    keepalive = int(client.get("persistent_keepalive", 0))
    if keepalive > 0:
        lines.append(f"PersistentKeepalive = {keepalive}")
    return "\n".join(lines) + "\n"


def apply_peer_runtime(client: dict, psk_path: Path) -> None:
    if run(["ip", "link", "show", WG_IF], check=False).returncode != 0:
        return
    run(
        [
            "wg",
            "set",
            WG_IF,
            "peer",
            client["public_key"],
            "preshared-key",
            str(psk_path),
            "allowed-ips",
            client["client_wg_tunnel_address"],
        ]
    )


def generated_client_name() -> str:
    return f"wg-{secrets.token_hex(6)}"


def add_client(args: argparse.Namespace) -> dict:
    require_root()
    ensure_dirs()
    lock_handle = with_registry_lock()
    with lock_handle:
        assert_no_pending_network()
        config = load_config()
        registry = load_registry()
        name = str(args.name or "").strip() or generated_client_name()
        if not CLIENT_NAME_RE.fullmatch(name):
            fail("client name must use lowercase letters, digits, and hyphens, up to 15 characters")
        if any(
            isinstance(client, dict) and client.get("name") == name
            for client in registry.get("clients", [])
        ):
            fail(f"WireGuard client already exists: {name}")

        local_subnets = (
            normalize_cli_cidr_values(args.local_subnet, field="local_subnets")
            if args.local_subnet
            else effective_local_subnets(config)
        )
        dns = normalize_dns(list(args.dns or config.get("client_default_dns") or []))
        keepalive = int(
            args.persistent_keepalive
            if args.persistent_keepalive is not None
            else config.get("client_default_persistent_keepalive", 25)
        )
        if keepalive < 0 or keepalive > 65535:
            fail("persistent_keepalive must be between 0 and 65535")
        address = normalize_client_address(args.client_wg_tunnel_address, config, registry)

        client_dir = CLIENTS_DIR / name
        client_dir.mkdir(parents=True, exist_ok=False)
        client_dir.chmod(0o700)
        private_key = wg_genkey()
        public_key = wg_pubkey(private_key)
        preshared_key = wg_genpsk()
        (client_dir / "private.key").write_text(private_key + "\n", encoding="utf-8")
        (client_dir / "private.key").chmod(0o600)
        (client_dir / "public.key").write_text(public_key + "\n", encoding="utf-8")
        (client_dir / "public.key").chmod(0o644)
        psk_path = client_dir / "preshared.key"
        psk_path.write_text(preshared_key + "\n", encoding="utf-8")
        psk_path.chmod(0o600)

        client = {
            "name": name,
            "client_wg_tunnel_address": address,
            "local_subnets": local_subnets,
            "dns": dns,
            "persistent_keepalive": keepalive,
            "public_key": public_key,
            "preshared_key": preshared_key,
            "config_path": str(client_dir / f"{name}.conf"),
            "created_at": datetime.now(UTC).isoformat(),
        }
        config_text = build_client_config(config, client, private_key)
        Path(client["config_path"]).write_text(config_text, encoding="utf-8")
        Path(client["config_path"]).chmod(0o600)
        registry.setdefault("clients", []).append(client)
        save_registry(registry)
        write_server_config(config, registry)
        apply_peer_runtime(client, psk_path)
        capacity, remaining = client_slots(config, registry)
        return {
            "name": name,
            "client_wg_tunnel_address": address,
            "local_subnets": local_subnets,
            "dns": dns,
            "persistent_keepalive": keepalive,
            "config_path": client["config_path"],
            "config": config_text,
            "clients_created": len(registry.get("clients", [])),
            "client_capacity": capacity,
            "remaining_client_slots": remaining,
        }


def bootstrap(args: argparse.Namespace) -> None:
    require_root()
    clients_path = Path(args.clients_file)
    if not clients_path.exists():
        return
    clients = json.loads(clients_path.read_text(encoding="utf-8"))
    if not isinstance(clients, list):
        fail(f"{clients_path} must contain a JSON list")
    for client in clients:
        if not isinstance(client, dict):
            fail("bootstrap client entries must be objects")
        registry = load_registry()
        name = str(client.get("name") or "").strip()
        if name and any(
            isinstance(item, dict) and item.get("name") == name
            for item in registry.get("clients", [])
        ):
            continue
        namespace = argparse.Namespace(
            name=name or None,
            client_wg_tunnel_address=client.get("client_wg_tunnel_address"),
            local_subnet=client.get("local_subnets") or None,
            dns=client.get("dns") or None,
            persistent_keepalive=client.get("persistent_keepalive"),
            output_json=False,
        )
        add_client(namespace)


def list_clients(args: argparse.Namespace) -> None:
    require_root()
    ensure_dirs()
    config = load_config()
    registry = load_registry()
    capacity, remaining = client_slots(config, registry)
    clients = []
    for client in registry.get("clients", []):
        if not isinstance(client, dict):
            continue
        sanitized = {
            key: value
            for key, value in client.items()
            if key not in {"preshared_key", "private_key"}
        }
        clients.append(sanitized)
    payload = {
        "clients": clients,
        "clients_created": len(registry.get("clients", [])),
        "client_capacity": capacity,
        "local_subnets": effective_local_subnets(config),
        "remaining_client_slots": remaining,
    }
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))


def update_local_subnets(args: argparse.Namespace, *, remove: bool) -> dict:
    require_root()
    ensure_dirs()
    requested = normalize_cli_cidr_values(
        args.local_subnet, field="local_subnet", require_single_option=True
    )
    lock_handle = with_registry_lock()
    with lock_handle:
        assert_no_pending_network()
        config = load_config()
        runtime_config = ensure_runtime_config(config)
        current = list(runtime_config.get("local_subnets") or [])
        requested_set = set(requested)
        if remove:
            changed = [cidr for cidr in requested if cidr in current]
            unchanged = [cidr for cidr in requested if cidr not in current]
            local_subnets = [cidr for cidr in current if cidr not in requested_set]
            payload_key = "removed"
        else:
            changed = [cidr for cidr in requested if cidr not in current]
            unchanged = [cidr for cidr in requested if cidr in current]
            local_subnets = current + changed
            payload_key = "added"
        runtime_config = {
            "version": 1,
            "local_subnets": local_subnets,
        }
        save_runtime_config(runtime_config)
        payload = {
            "local_subnets": local_subnets,
            payload_key: changed,
            "unchanged": unchanged,
        }
        if remove:
            payload["added"] = []
        else:
            payload["removed"] = []
        return payload


def main() -> None:
    parser = argparse.ArgumentParser(prog="nebius-wireguard-client")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init-server")
    init_parser.set_defaults(func=init_server)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--clients-file", required=True)
    bootstrap_parser.set_defaults(func=bootstrap)
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("--name")
    add_parser.add_argument("--client-wg-tunnel-address")
    add_parser.add_argument("--local-subnet", action="append")
    add_parser.add_argument("--dns", action="append")
    add_parser.add_argument("--persistent-keepalive", type=int)
    add_parser.add_argument("--output-json", action="store_true")
    add_parser.set_defaults(
        func=lambda args: (
            print(json.dumps(add_client(args), sort_keys=True))
            if args.output_json
            else add_client(args)
        )
    )
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--pretty", action="store_true")
    list_parser.set_defaults(func=list_clients)
    add_subnets_parser = subparsers.add_parser("add-local-subnets")
    add_subnets_parser.add_argument("--local-subnet", action="append", required=True)
    add_subnets_parser.set_defaults(
        func=lambda args: print(
            json.dumps(update_local_subnets(args, remove=False), sort_keys=True)
        )
    )
    remove_subnets_parser = subparsers.add_parser("remove-local-subnets")
    remove_subnets_parser.add_argument("--local-subnet", action="append", required=True)
    remove_subnets_parser.set_defaults(
        func=lambda args: print(json.dumps(update_local_subnets(args, remove=True), sort_keys=True))
    )
    for command, function in (
        ("reconcile-network", reconcile_network),
        ("refresh-client", refresh_client),
        ("network-status", lambda request: network_status(request.get("public_key", ""))),
    ):
        subparsers.add_parser(command).set_defaults(
            func=lambda args, f=function: print(json.dumps(f(json.load(sys.stdin)), sort_keys=True))
        )
    for command, action in (("network-up", "up"), ("network-down", "down")):
        subparsers.add_parser(command).set_defaults(
            func=lambda args, a=action: (require_root(), mss_rules(a))
        )
    args = parser.parse_args()
    if args.command not in {
        "reconcile-network",
        "network-status",
        "network-up",
        "network-down",
        "list",
    }:
        assert_no_pending_network()
    args.func(args)


if __name__ == "__main__":
    main()
