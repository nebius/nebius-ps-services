"""Client-side route selection and certificate-verified data-path evidence."""

from __future__ import annotations

import http.client
import ipaddress
import json
import platform
import re
import shutil
import socket
import ssl
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .tailscale_config import digest, networks


def client_status() -> dict[str, Any]:
    binary = shutil.which("tailscale")
    if not binary:
        for candidate in (
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/tailscale",
        ):
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        raise RuntimeError(
            "Install and connect the Tailscale client on this computer, then retry verify"
        )
    result = subprocess.run(
        [binary, "status", "--json"], capture_output=True, text=True, timeout=15, check=False
    )
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("The local Tailscale client is unavailable")
    value = json.loads(result.stdout)
    if value.get("BackendState") != "Running":
        raise RuntimeError("The local Tailscale client is not connected")
    return value


def selected_router(status: dict[str, Any], address: str, owned: set[str]) -> str:
    """Longest-prefix selection, including more-specific routes from other routers."""
    candidates: list[tuple[int, str]] = []
    for peer in (status.get("Peer") or {}).values():
        for prefix in peer.get("PrimaryRoutes") or []:
            net = ipaddress.ip_network(prefix, strict=True)
            if ipaddress.ip_address(address) in net:
                candidates.append((net.prefixlen, peer.get("ID", "")))
    if not candidates:
        raise RuntimeError(
            "Client has no selected subnet router for this target; check route approval and route acceptance"
        )
    longest = max(length for length, _ in candidates)
    chosen = {node for length, node in candidates if length == longest}
    if len(chosen) != 1 or not chosen.issubset(owned):
        raise RuntimeError("The target uses an ambiguous or unowned more-specific route")
    return chosen.pop()


def verify_os_route(address: str, status: dict[str, Any], node_id: str) -> str:
    peer: dict[str, Any] = next(
        (p for p in (status.get("Peer") or {}).values() if p.get("ID") == node_id), {}
    )
    peer_ip = next(
        (v for v in peer.get("TailscaleIPs", []) if ipaddress.ip_address(v).version == 4), None
    )
    if not peer_ip:
        raise RuntimeError("Selected router has no IPv4 tailnet address")

    def interface(destination: str) -> str:
        system = platform.system()
        command = (
            ["route", "-n", "get", destination]
            if system == "Darwin"
            else ["ip", "-json", "route", "get", destination]
        )
        if system not in {"Darwin", "Linux"}:
            raise RuntimeError("Client route verification supports macOS and Linux")
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode or len(result.stdout) > 65536:
            raise RuntimeError("Cannot establish the operating system route to the target")
        if system == "Darwin":
            match = re.search(r"^\s*interface:\s*(\S+)\s*$", result.stdout, re.MULTILINE)
            return match.group(1) if match else ""
        values = json.loads(result.stdout)
        return values[0].get("dev", "") if len(values) == 1 else ""

    target, overlay = interface(address), interface(peer_ip)
    if not target or target != overlay or not (target.startswith("utun") or target == "tailscale0"):
        raise RuntimeError(
            "The OS routes this target outside the Tailscale tunnel; check overlapping VPNs and accepted routes"
        )
    return target


def probe(target: dict[str, Any], address: str, base: Path, timeout: float) -> dict[str, Any]:
    url = urlsplit(target["url"])
    ca = target.get("ca_file")
    path = Path(ca).expanduser() if ca else None
    if path and not path.is_absolute():
        path = base / path
    context = ssl.create_default_context(cafile=str(path) if path else None)
    connection = http.client.HTTPSConnection(
        url.hostname, url.port or 443, timeout=timeout, context=context
    )
    # Connect to the exact address whose selected route was observed. Preserve TLS SNI.
    raw = socket.create_connection((address, url.port or 443), timeout=timeout)
    try:
        connection.sock = context.wrap_socket(raw, server_hostname=url.hostname)
        connection.request(
            "GET",
            (url.path or "/") + ("?" + url.query if url.query else ""),
            headers={"Connection": "close"},
        )
        response = connection.getresponse()
        payload = response.read(1024 * 1024 + 1)
        if (
            response.status not in {200, 204, 401, 403}
            or len(payload) < target.get("min_bytes", 1)
            or len(payload) > 1024 * 1024
        ):
            raise RuntimeError("HTTPS target did not satisfy the response contract")
        return {
            "address": address,
            "http_status": response.status,
            "bytes": len(payload),
            "tls": "verified",
        }
    finally:
        connection.close()
        raw.close()


def verify(
    config: dict[str, Any],
    members: dict[str, Any],
    *,
    base: Path,
    guests: dict[str, Any] | None = None,
    failover: bool = False,
) -> dict[str, Any]:
    settings = config.get("verification", {})
    targets = settings.get("targets", [])
    if not targets:
        raise RuntimeError(
            "Configure tailscale.verification.targets with reachable HTTPS endpoints before verify"
        )
    owned = {m.get("node_id", "") for m in members.values()} - {""}
    if len(owned) != len(members):
        raise RuntimeError("Every configured router must have a recorded node identity")
    prefixes = [ipaddress.ip_network(p) for p in networks(config["routes"])]
    timeout = settings.get("timeout_seconds", 15)

    def sample() -> list[dict[str, Any]]:
        status = client_status()
        if (status.get("CurrentTailnet") or {}).get("Name") != config["tailnet"]:
            raise RuntimeError("This computer is connected to a different tailnet")
        results = []
        for target in targets:
            url = urlsplit(target["url"])
            addresses = sorted(
                {
                    str(row[4][0])
                    for row in socket.getaddrinfo(
                        url.hostname,
                        url.port or 443,
                        family=socket.AF_INET,
                        type=socket.SOCK_STREAM,
                    )
                }
            )
            if not addresses or any(
                not any(ipaddress.ip_address(a) in p for p in prefixes) for a in addresses
            ):
                raise RuntimeError(
                    "Target DNS must resolve exclusively inside the advertised private prefixes"
                )
            for address in addresses:
                router = selected_router(status, address, owned)
                interface = verify_os_route(address, status, router)
                result = probe(target, address, base, timeout)
                after = client_status()
                if selected_router(after, address, owned) != router:
                    raise RuntimeError(
                        "Route selection changed during the probe; repeat verification"
                    )
                results.append(
                    {**result, "target": url.hostname, "router": router, "interface": interface}
                )
        return results

    initial = sample()
    result: dict[str, Any] = {
        "status": "PASS",
        "intent_sha256": digest(config),
        "targets": initial,
        "failover": {"status": "NOT_RUN"},
    }
    if not failover:
        return result
    if not guests or len(guests) != 2 or any(m.get("phase") != "READY" for m in members.values()):
        raise RuntimeError("Failover verification requires two independently ready owned routers")
    selected = {item["router"] for item in initial}
    if len(selected) != 1:
        raise RuntimeError(
            "This bounded failover trial requires the same selected router for all targets"
        )
    failed_node = selected.pop()
    member = next(k for k, v in members.items() if v["node_id"] == failed_node)
    started = time.monotonic()
    injected = False
    try:
        # Set before request: a lost SSH response may still have stopped the service.
        injected = True
        guests[member].call("fault", token=uuid.uuid4().hex, duration=120)
        deadline = started + 90
        while time.monotonic() < deadline:
            try:
                observations = sample()
                if (
                    time.monotonic() < deadline
                    and all(item["router"] != failed_node for item in observations)
                    and guests[member].call("trial-status").get("active") is False
                ):
                    result["failover"] = {
                        "status": "PASS",
                        "member": member,
                        "seconds": round(time.monotonic() - started, 2),
                        "targets": observations,
                    }
                    break
            except (RuntimeError, OSError, http.client.HTTPException):
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Failover data path did not converge within 90 seconds")
    finally:
        if injected:
            guests[member].call("restore")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if guests[member].call("status").get("online"):
            result["failover"]["restored"] = True
            sample()
            return result
        time.sleep(2)
    raise RuntimeError("Failover probe completed but router recovery remains unverified")
