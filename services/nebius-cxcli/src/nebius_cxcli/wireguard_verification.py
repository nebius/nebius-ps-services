"""Current-run laptop VPN qualification, separate from cloud readiness."""

from __future__ import annotations

import configparser
import ipaddress
import json
import os
import platform
import re
import selectors
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import kubernetes_process
from .kubeconfig_target import verify_context_cluster
from .wireguard_networking import remote_network


class VerificationPending(RuntimeError):
    """A prerequisite or sufficient observation is unavailable."""


class ClientVerificationFailed(RuntimeError):
    def __init__(self):
        super().__init__(
            "Infrastructure deployment completed, but WireGuard client access failed; see wireguard-client-verification.md"
        )


def read_client(path: Path) -> dict:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > 65536
    ):
        raise VerificationPending("Client configuration must be an owned, private regular file")
    text = path.read_text()
    parsed = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parsed.read_string(text)
        if parsed.sections() != ["Interface", "Peer"]:
            raise ValueError
        interface, peer = parsed["Interface"], parsed["Peer"]
        address = ipaddress.ip_interface(interface["Address"])
        if address.version != 4 or address.network.prefixlen != 32:
            raise ValueError
        routes = [
            ipaddress.ip_network(x.strip(), strict=False) for x in peer["AllowedIPs"].split(",")
        ]
        key = subprocess.run(
            ["wg", "pubkey"],
            input=interface["PrivateKey"] + "\n",
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        return {
            "path": path,
            "address": str(address.ip),
            "routes": routes,
            "public_key": key,
            "server_key": peer["PublicKey"],
            "endpoint": peer["Endpoint"],
            "text": text,
            "mtu": int(interface["MTU"]) if "MTU" in interface else None,
        }
    except (KeyError, ValueError, configparser.Error, subprocess.SubprocessError):
        raise VerificationPending("Client configuration cannot be safely identified") from None


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise VerificationPending("Required local networking evidence is unavailable") from None


def resolve_addresses(host: str | None, port: int) -> list[str]:
    if not host:
        raise RuntimeError("Private Kubernetes endpoint has no hostname")
    # libc DNS has no per-call deadline; isolate resolution in a bounded child.
    code = "import json,socket,sys; print(json.dumps(sorted({x[4][0] for x in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)})))"
    values = json.loads(_run([sys.executable, "-c", code, host, str(port)]))
    if not values or len(values) > 16:
        raise RuntimeError("Private Kubernetes endpoint resolution is absent or ambiguous")
    return [str(ipaddress.ip_address(value)) for value in values]


def active_interface(client: dict) -> tuple[str, int]:
    name = client["path"].stem
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,14}", name):
        raise VerificationPending("Client filename is not a supported WireGuard interface name")
    if platform.system() == "Darwin":
        # wg-quick's mapping directory is root-only on macOS. Identify the
        # unique active interface by its exact tunnel address, then bind it to
        # the authenticated gateway peer and observed traffic below.
        interfaces = re.split(r"(?m)^(?=\S[^\n]*: flags=)", _run(["ifconfig", "-a"]))
        matches = []
        for text in interfaces:
            name_match = re.match(r"(utun[0-9]+):", text)
            mtu_match = re.search(r"\bmtu (\d+)", text)
            if name_match and mtu_match and client["address"] in re.findall(r"\binet (\S+)", text):
                matches.append((name_match[1], int(mtu_match[1])))
        if len(matches) != 1:
            raise VerificationPending(
                "The selected WireGuard client has no unique active tunnel interface"
            )
        return matches[0]
    if platform.system() == "Linux":
        rows = json.loads(_run(["ip", "-j", "address", "show", "dev", name]))
        if len(rows) != 1 or client["address"] not in [
            a.get("local") for a in rows[0]["addr_info"]
        ]:
            raise VerificationPending("The selected WireGuard client is not connected")
        return name, int(rows[0]["mtu"])
    raise VerificationPending("Automatic WireGuard verification supports Linux and macOS")


def assert_route(address: str, interface: str) -> None:
    if platform.system() == "Darwin":
        match = re.search(r"\binterface:\s*(\S+)", _run(["route", "-n", "get", address]))
        actual = match[1] if match else ""
    else:
        rows = json.loads(_run(["ip", "-j", "route", "get", address]))
        actual = rows[0].get("dev") if len(rows) == 1 else ""
    if actual != interface:
        raise RuntimeError(
            "Private Kubernetes traffic is not routed through the selected WireGuard interface"
        )


def local_mtu_limit(client: dict) -> int:
    host = client["endpoint"].rsplit(":", 1)[0].strip("[]")
    if platform.system() == "Darwin":
        match = re.search(r"\binterface:\s*(\S+)", _run(["route", "-n", "get", host]))
        if not match:
            raise VerificationPending("Cannot identify the local WireGuard uplink")
        mtu = re.search(r"\bmtu (\d+)", _run(["ifconfig", match[1]]))
        if not mtu:
            raise VerificationPending("Cannot identify the local uplink MTU")
        value = int(mtu[1])
    elif platform.system() == "Linux":
        rows = json.loads(_run(["ip", "-j", "route", "get", host]))
        if len(rows) != 1 or not rows[0].get("dev"):
            raise VerificationPending("Cannot identify the local WireGuard uplink")
        value = int(json.loads(_run(["ip", "-j", "link", "show", "dev", rows[0]["dev"]]))[0]["mtu"])
    else:
        raise VerificationPending("Client refresh supports Linux and macOS")
    return min(1420, value - 80)


def selected_context(
    server: str, ca: str, *, kubeconfig: Path | None = None, context: str | None = None
) -> tuple[Path, str]:
    paths = (
        [kubeconfig]
        if kubeconfig
        else [
            Path(x).expanduser()
            for x in os.environ.get("KUBECONFIG", str(Path.home() / ".kube/config")).split(
                os.pathsep
            )
            if x
        ]
    )
    matches = []
    for path in paths:
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            payload = yaml.safe_load(path.read_text())
            for row in payload.get("contexts", []):
                name = row.get("name")
                if not name or (context and context != name):
                    continue
                try:
                    verify_context_cluster(path, context=name, server=server, ca_pem=ca)
                except RuntimeError:
                    continue
                matches.append((path, name))
        except (OSError, AttributeError, yaml.YAMLError):
            continue
    if len(matches) != 1:
        raise VerificationPending(
            "Select one existing kubeconfig context matching the private cluster endpoint and CA"
        )
    return matches[0]


def bounded_kubectl(args: list[str], env: dict[str, str], *, limit: int = 4 * 1024 * 1024) -> bytes:
    process = kubernetes_process.popen(
        args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert process.stdout is not None and process.stderr is not None
    data = bytearray()
    deadline = time.monotonic() + 20
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Kubernetes verification timed out")
                for key, _events in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data:
                        data.extend(chunk)
                        if len(data) > limit:
                            raise RuntimeError(
                                "Kubernetes verification response exceeded its bound"
                            )
        if process.wait(timeout=2):
            raise RuntimeError("Kubernetes authentication or API verification failed")
        return bytes(data)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


def verify_client(
    client: dict,
    ssh,
    *,
    server: str,
    ca: str,
    kubeconfig: Path | None = None,
    context: str | None = None,
) -> dict:
    interface, mtu = active_interface(client)
    parsed = urlsplit(server)
    addresses = resolve_addresses(parsed.hostname, parsed.port or 443)
    if not addresses or any(
        not any(ipaddress.ip_address(ip) in route for route in client["routes"]) for ip in addresses
    ):
        raise RuntimeError(
            "Private Kubernetes endpoint is outside this client's configured VPN routes"
        )
    for address in addresses:
        assert_route(address, interface)
    before = remote_network(ssh, "network-status", {"public_key": client["public_key"]})
    if (
        before.get("server_public_key") != client["server_key"]
        or not before.get("peer_found")
        or before.get("peer_allowed_ips") != client["address"] + "/32"
    ):
        raise RuntimeError("Client and gateway peer identity do not agree")
    if not 1280 <= mtu <= min(before["mtu"], local_mtu_limit(client)) or client["mtu"] != mtu:
        raise RuntimeError(
            "Client MTU is stale or exceeds the gateway limit; refresh and reconnect the client"
        )
    with ExitStack() as stack:
        path, name = selected_context(server, ca, kubeconfig=kubeconfig, context=context)
        from . import cli

        with cli._temporary_env({"KUBECONFIG": str(path)}):
            frozen_env = cli._kubeconfig_env_for_context(name, stack=stack)
        if not frozen_env.get("KUBECONFIG"):
            raise VerificationPending("Selected kubeconfig could not be copied safely")
        path = Path(frozen_env["KUBECONFIG"])
        verify_context_cluster(path, context=name, server=server, ca_pem=ca)
        tls_context = ssl.create_default_context(cadata=ca)
        with (
            socket.create_connection((addresses[0], parsed.port or 443), timeout=15) as sock,
            tls_context.wrap_socket(sock, server_hostname=parsed.hostname),
        ):
            pass
        env = {
            k: v
            for k, v in os.environ.items()
            if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
        }
        env.update(
            KUBECONFIG=str(path), NEBIUS_CXCLI_TARGET_KUBE_CONTEXT=name, KUBECTL_KUBERC="false"
        )
        command = [
            "kubectl",
            "--kubeconfig",
            str(path),
            "--context",
            name,
            "--request-timeout=15s",
            "--disable-compression=true",
        ]
        identity = json.loads(
            bounded_kubectl([*command, "auth", "whoami", "-o", "json"], env, limit=256 * 1024)
        )
        user = identity.get("status", {}).get("userInfo", {})
        if (
            not user.get("username")
            or user["username"] == "system:anonymous"
            or "system:unauthenticated" in user.get("groups", [])
        ):
            raise RuntimeError(
                "Kubernetes verification did not establish an authenticated identity"
            )
        body = bounded_kubectl([*command, "get", "--raw=/api/v1"], env)
        if json.loads(body).get("kind") != "APIResourceList":
            raise RuntimeError("Kubernetes discovery response is invalid")
        if len(body) <= 4096:
            raise VerificationPending(
                "Kubernetes response is too small to qualify larger transfers"
            )
    after = remote_network(ssh, "network-status", {"public_key": client["public_key"]})
    if (
        not after.get("peer_handshake")
        or time.time() - after["peer_handshake"] > 180
        or any(a <= b for a, b in zip(after["peer_transfer"], before["peer_transfer"], strict=True))
    ):
        raise RuntimeError("WireGuard peer did not demonstrate fresh bidirectional transfer")
    return {
        "status": "PASS",
        "reason": "Private Kubernetes access verified",
        "mtu": mtu,
        "response_bytes": len(body),
    }


def probe(client: dict, ssh, **kwargs) -> dict:
    try:
        return verify_client(client, ssh, **kwargs)
    except VerificationPending as exc:
        return {"status": "PENDING", "reason": str(exc)}
    except Exception:
        # TLS, authentication and subprocess exceptions can contain private URLs
        # or command output. Reports deliberately never serialize those errors.
        return {
            "status": "FAIL",
            "reason": "Connected client failed private Kubernetes access verification; check MTU, routes, peer identity and kubeconfig authentication",
        }


def eligible_targets(config, row: dict, targets: list[dict]) -> list[dict]:
    from .component_instances import component_instance_id

    network = row.get("inputs", {}).get("network_id")
    clusters = {
        component_instance_id(c): c
        for c in config.get("infra", {}).get("components", [])
        if c.get("id") == "mk8s"
    }
    return [
        target
        for target in targets
        if network
        and clusters.get(target.get("instance_id", ""), {})
        .get("inputs", {})
        .get("cluster", {})
        .get("network_id")
        == network
    ]


def covers_endpoint(client: dict, server: str) -> bool:
    endpoint = urlsplit(server)
    return all(
        any(ipaddress.ip_address(address) in route for route in client["routes"])
        for address in resolve_addresses(endpoint.hostname, endpoint.port or 443)
    )


def verify_after_deploy(executor, original_paths) -> dict:
    from .component_instances import component_instance_id
    from .wireguard_access import client_access
    from .wireguard_commands import load_wireguard_context
    from .wireguard_host_identity import wireguard_rows
    from .wireguard_secret_api import wireguard_api

    starting = [
        {"status": "PENDING", "reason": "Current deployment client verification has not completed"}
    ]
    write_report(original_paths.reports_dir, starting)
    write_report(executor.paths.reports_dir, starting)
    rows = wireguard_rows(executor.config)
    targets = [
        t
        for t in executor.manifest["execution"]["targets"]
        if t.get("component_id") == "mk8s" and t["target_ref"] in executor.plan.selected_targets
    ]
    results = []
    if rows and targets:
        active = []
        directory = original_paths.project_dir / "wireguard-clients"
        candidates = (
            sorted(directory.glob("*.conf"))
            if directory.is_dir() and not directory.is_symlink()
            else []
        )
        if len(candidates) > 64:
            candidates = []
        for path in candidates:
            try:
                client = read_client(path)
                active_interface(client)
                active.append(client)
            except (VerificationPending, OSError):
                continue
        if not active:
            results.append(
                {
                    "status": "PENDING",
                    "reason": "No identifiable connected WireGuard client; connect a client and run wireguard --verify-client",
                }
            )
        else:
            outputs = executor.cli.terraform_output_json(
                executor.paths.infra_dir, extra_env=executor.runtime_env, initialize=False
            )
            for row in rows:
                instance = component_instance_id(row)
                selected = eligible_targets(executor.config, row, targets)
                if not selected:
                    results.append(
                        {
                            "status": "PENDING",
                            "reason": "No selected Kubernetes target has a matching gateway VPC binding; use explicit verification for routed access",
                        }
                    )
                    continue
                try:
                    _, _, component, source, project = load_wireguard_context(
                        original_paths.config_path, instance
                    )
                    with (
                        wireguard_api(
                            credentials_file=os.environ.get("CXCLI_WIREGUARD_CREDENTIALS_FILE")
                        ) as api,
                        client_access(
                            api,
                            project=project,
                            component=component.instance_id,
                            access=source["access"],
                        ) as ssh,
                    ):
                        status = remote_network(ssh, "network-status", {})
                        clients = [
                            c for c in active if c["server_key"] == status["server_public_key"]
                        ]
                        if len(clients) != 1:
                            results.append(
                                {
                                    "status": "PENDING",
                                    "reason": "Select one connected client for this gateway with wireguard --verify-client",
                                }
                            )
                            continue
                        for target in selected:
                            cluster_id = outputs[target["cluster_id_output_name"]]["value"]
                            spec = executor.cli._mk8s_cluster_handoff_spec(
                                executor.config,
                                cluster_id=cluster_id,
                                access="internal",
                                require_renewable_auth=True,
                            )
                            if not covers_endpoint(clients[0], spec.server):
                                results.append(
                                    {
                                        "status": "PENDING",
                                        "reason": "Client destination routes do not include this selected cluster; explicit verification requires the intended route",
                                    }
                                )
                                continue
                            results.append(
                                probe(clients[0], ssh, server=spec.server, ca=spec.ca_pem)
                            )
                except Exception:
                    results.append(
                        {
                            "status": "PENDING",
                            "reason": "Gateway or cluster verification prerequisites could not be resolved; run wireguard --verify-client explicitly",
                        }
                    )
    report = write_report(original_paths.reports_dir, results)
    write_report(executor.paths.reports_dir, results)
    executor.cli.console.print(
        f"WireGuard client verification: {report['status']} (wireguard-client-verification.md)"
    )
    if report["status"] == "FAIL":
        raise ClientVerificationFailed()
    return report


def write_report(directory: Path, results: list[dict]) -> dict:
    status = (
        "FAIL"
        if any(r["status"] == "FAIL" for r in results)
        else "PENDING"
        if any(r["status"] == "PENDING" for r in results)
        else "PASS"
        if results
        else "NOT_APPLICABLE"
    )
    report = {
        "schema": "nebius-cxcli/wireguard-client-verification/v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": status,
        "results": results,
    }
    markdown = (
        "# WireGuard client verification\n\nStatus: "
        + status
        + "\n\n"
        + "\n".join(f"- {r['status']}: {r['reason']}" for r in results)
        + "\n"
    )
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in (
        ("wireguard-client-verification.json", json.dumps(report, indent=2) + "\n"),
        ("wireguard-client-verification.md", markdown),
    ):
        if (directory / name).is_symlink() or directory.is_symlink():
            raise RuntimeError("Unsafe WireGuard report destination")
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            staging = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(text.encode())
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(staging, directory / name)
            finally:
                staging.unlink(missing_ok=True)
    return report
