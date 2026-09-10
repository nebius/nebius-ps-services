"""Synchronous ordinary deployment confirmation; never an HA activation owner."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import ipaddress
import json
import os
import re
import signal
import time
from pathlib import Path
from typing import Any

import yaml

from .. import ordinary_routes
from ..ordinary_operations import Journal, Manager, Operation, require_idle
from . import firewall_manager as firewall
from .frr_renderer import FRR_CONF, FRRRenderer
from .local_commands import CURRENT, CommandBudget, run
from .routing_guard import REQUIRED_SYSCTLS, acquire_routing_lock, enforce_routing_invariants_locked
from .state_store import RENDER_VERSION, StateStore, _get_package_version
from .strongswan_renderer import SWANCTL_CONF, StrongSwanRenderer
from .tunnel_iterator import iter_active_tunnels
from .vm_ha.store import atomic_write_json
from .xfrm_manager import IPSEC_OVERHEAD_BYTES, XFRMManager

CAPABILITY = "ordinary-apply-v1"
SCHEMA = "nebius-vpngw.ordinary-apply.v1"
CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
PROOF = Path("/etc/nebius-vpngw/ordinary-verification.json")
STATE = Path("/etc/nebius-vpngw/last-applied.json")
BOOT = Path("/proc/sys/kernel/random/boot_id")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def package_identity() -> str:
    """Hash actual imported package contents, not version metadata or RECORD claims."""
    root = Path(__file__).resolve().parents[1]
    return digest(
        {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
    )


def _output(argv: list[str]) -> str:
    result = run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode or len(result.stdout.encode()) > 1024 * 1024:
        raise RuntimeError("local observation failed")
    return result.stdout.strip()


def _json(argv: list[str]) -> list[dict[str, Any]]:
    result = json.loads(_output(argv))
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise RuntimeError("local observation malformed")
    return result


def _require(condition: bool) -> None:
    if not condition:
        raise RuntimeError("ordinary local invariant failed")


def managed_files(cfg: dict[str, Any]) -> dict[Path, str | None]:
    files: dict[Path, str | None] = {}
    # Preview is pure: it renders active syntax without writing or activation.
    with contextlib.redirect_stdout(io.StringIO()):
        StrongSwanRenderer().render_and_apply(cfg, rendered_files=files)
        FRRRenderer().render_and_apply(cfg, rendered_files=files)
    peers = sorted(
        {
            str(t.get("remote_public_ip") or "").strip()
            for c in cfg.get("connections", [])
            for t in c.get("tunnels", [])
        }
        - {""}
    )
    files[firewall.PEER_IPS_FILE] = (
        "\n".join(
            [
                "# VPN peer public IPs (auto-generated from config)",
                "# One IP per line - used by UFW to allow IPsec protocols",
                "",
                *peers,
            ]
        )
        + "\n"
    )
    prefixes = sorted(set((cfg.get("gateway") or {}).get("local_prefixes") or []))
    files[firewall.LOCAL_PREFIXES_FILE] = (
        "\n".join(
            [
                "# Local VPC prefixes allowed through firewall (auto-generated)",
                "# One CIDR per line",
                "",
                *prefixes,
            ]
        )
        + "\n"
    )
    return files


def frr_policy_rows(text: str) -> set[tuple[str, str]]:
    """Compare explicit routing policy despite FRR ordering/indentation defaults."""
    context = ""
    rows = set()
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or line.startswith("!"):
            continue
        if not raw.startswith(" "):
            context = line if line.startswith(("router bgp ", "route-map ")) else ""
        if line.startswith(
            (
                "router bgp ",
                "route-map ",
                "ip prefix-list ",
                "neighbor ",
                "network ",
                "match ",
                "set ",
                "no bgp default ipv4-unicast",
            )
        ):
            rows.add((context, line))
    return rows


def observe_local(cfg: dict[str, Any]) -> str:
    """Verify local invariants and fingerprint loaded config, excluding peer liveness."""
    files = managed_files(cfg)
    for path, expected in files.items():
        if expected is None:
            _require(not path.exists())
        else:
            _require(path.read_text() == expected)
    _require(SWANCTL_CONF.stat().st_mode & 0o777 == 0o600)
    _require(_output(["systemctl", "is-active", "strongswan-starter"]) == "active")
    _require(_output(["systemctl", "is-active", "frr"]) == "active")
    _require("Status: active" in _output(["ufw", "status"]))
    for key, value in REQUIRED_SYSCTLS.items():
        _require(_output(["sysctl", "-n", key]) == value)
    rules = _json(["ip", "-j", "-4", "rule", "show"])
    routes = _json(["ip", "-j", "-4", "route", "show", "table", "all"])
    _require(not any(str(item.get("table")) == "220" for item in [*rules, *routes]))
    _require(not any(item.get("dst") == "169.254.0.0/16" for item in routes))
    parent = _json(["ip", "-d", "-j", "link", "show", "dev", "eth0"])
    _require(len(parent) == 1 and isinstance(parent[0].get("mtu"), int))
    endpoints = StrongSwanRenderer().build_interface_endpoints(cfg)
    effective_static = ordinary_routes.effective_static_routes(ordinary_routes.projection(cfg))
    allowed_apipa = set()
    static_routes: dict[str, set[ipaddress.IPv4Network | ipaddress.IPv6Network]] = {}
    static_inner: dict[str, ipaddress.IPv4Network | ipaddress.IPv6Network] = {}
    for endpoint in endpoints:
        name = endpoint["name"]
        if endpoint.get("mode", "bgp") == "static":
            static_routes[name] = set()
        links = _json(["ip", "-d", "-j", "link", "show", "dev", name])
        _require(len(links) == 1)
        link = links[0]
        info = link.get("linkinfo") or {}
        actual_id = (info.get("info_data") or {}).get("if_id")
        if isinstance(actual_id, str):
            actual_id = int(actual_id, 0)
        _require(info.get("info_kind") == "xfrm" and actual_id == endpoint["if_id"])
        _require(link.get("link_index") == parent[0].get("ifindex") or link.get("link") == "eth0")
        _require(
            "UP" in link.get("flags", [])
            and link.get("mtu") == parent[0]["mtu"] - IPSEC_OVERHEAD_BYTES
        )
        _require(_output(["sysctl", "-n", f"net.ipv4.conf.{name}.rp_filter"]) == "0")
        if endpoint.get("local_inner_ip") and endpoint.get("cidr"):
            addresses = _json(["ip", "-j", "-4", "addr", "show", "dev", name])
            prefix_length = ipaddress.ip_network(endpoint["cidr"], strict=False).prefixlen
            _require(
                any(
                    a.get("local") == endpoint["local_inner_ip"]
                    and a.get("prefixlen") == prefix_length
                    for row in addresses
                    for a in row.get("addr_info", [])
                )
            )
            allowed_apipa.add((ipaddress.ip_network(endpoint["cidr"], strict=False), name))
            if name in static_routes:
                static_inner[name] = ipaddress.ip_network(endpoint["cidr"], strict=False)
        if endpoint.get("mode", "bgp") == "static":
            required_routes = [
                prefix for prefix, owner in effective_static.items() if owner == name
            ]
        elif endpoint.get("remote_inner_ip"):
            required_routes = [f"{endpoint['remote_inner_ip']}/32"]
        else:
            required_routes = endpoint.get("remote_prefixes", [])
        for prefix in required_routes:
            network = ipaddress.ip_network(prefix, strict=False)
            allowed_apipa.add((network, name))
            if name in static_routes:
                static_routes[name].add(network)
            _require(
                any(
                    ipaddress.ip_network(
                        "0.0.0.0/0" if row.get("dst") == "default" else row.get("dst", "0.0.0.0/0"),
                        strict=False,
                    )
                    == network
                    and row.get("dev") == name
                    and str(row.get("table", "main")) in {"main", "254"}
                    for row in routes
                )
            )
        if endpoint.get("mode", "bgp") == "bgp" and endpoint.get("remote_inner_ip"):
            neighbors = _json(["ip", "-j", "neigh", "show", "dev", name])
            _require(
                any(
                    row.get("dst") == endpoint["remote_inner_ip"]
                    and "PERMANENT" in row.get("state", [])
                    for row in neighbors
                )
            )
    local_prefixes = {
        ipaddress.ip_network(prefix, strict=False)
        for prefix in (cfg.get("gateway") or {}).get("local_prefixes", [])
    }
    for row in routes:
        if str(row.get("table", "main")) not in {"main", "254"}:
            continue
        network = ipaddress.IPv4Network(
            "0.0.0.0/0" if row.get("dst") == "default" else row.get("dst", "0.0.0.0/0"),
            strict=False,
        )
        _require(not (network in local_prefixes and row.get("scope") == "link"))
        if row.get("type", "unicast") == "unicast":
            devices = {row.get("dev"), *(hop.get("dev") for hop in row.get("nexthops", []))}
            for name in devices & static_routes.keys():
                connected = (
                    network == static_inner.get(name)
                    and row.get("dev") == name
                    and str(row.get("protocol")) in {"kernel", "2"}
                    and str(row.get("scope")) in {"link", "253"}
                    and not any(row.get(key) for key in ("gateway", "via", "nexthops", "nhid"))
                )
                _require(network in static_routes[name] or connected)
        if network.subnet_of(ipaddress.IPv4Network("169.254.0.0/16")) and not network.subnet_of(
            ipaddress.IPv4Network("169.254.169.0/24")
        ):
            _require((network, row.get("dev")) in allowed_apipa)
    # No SA/session establishment requirement: these reads report loaded local
    # configuration. Fingerprint it again on no-op, never trust a state hash alone.
    conns = _output(["swanctl", "--list-conns", "--raw"])
    tunnels, _, _ = StrongSwanRenderer()._collect_tunnel_state(cfg)
    _require(len(tunnels) == len(list(iter_active_tunnels(cfg))))
    for tunnel in tunnels:
        _require(
            re.search(r"(?:^|[\s{])" + re.escape(tunnel["name"]) + r"\s*\{", conns) is not None
        )
    frr = _output(["vtysh", "-c", "show running-config"])
    _require(frr_policy_rows(files[FRR_CONF] or "") == frr_policy_rows(frr))
    firewall_rules = _output(["iptables-save"])
    _require("ufw-before-input" in firewall_rules)
    for endpoint in endpoints:
        _require(f"-A ufw-user-input -i {endpoint['name']} -j ACCEPT" in firewall_rules)
        _require(f"-A ufw-user-output -o {endpoint['name']} -j ACCEPT" in firewall_rules)
    normalized_rules = "\n".join(
        re.sub(r"\[\d+:\d+\]", "[0:0]", line)
        for line in firewall_rules.splitlines()
        if not line.startswith("#")
    )
    route_store = ordinary_routes.RouteStore()
    if route_store.ledger() is not None:
        route_store.verify(cfg, run, BOOT.read_text().strip())
    return digest(
        {
            "files": {str(p): v for p, v in files.items()},
            "connections": conns,
            "frr": frr,
            "firewall": normalized_rules,
        }
    )


def binding(raw: bytes) -> dict[str, Any]:
    return {
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "boot_id": BOOT.read_text().strip(),
        "package_identity": package_identity(),
        "render_version": RENDER_VERSION,
    }


def verify_unchanged(cfg: dict[str, Any], raw: bytes) -> bool:
    try:
        proof = json.loads(PROOF.read_text())
        if ordinary_routes.PROOF_KEY in proof:
            ledger = ordinary_routes.RouteStore().ledger()
            _require(
                ledger is not None
                and ordinary_routes.digest(ledger) == proof[ordinary_routes.PROOF_KEY]
            )
        return proof.get("binding") == binding(raw) and proof.get("runtime") == observe_local(cfg)
    except Exception:
        return False


def reconcile_locked(
    cfg: dict[str, Any], raw: bytes, *, verify_only: bool, force: bool = False
) -> str:
    budget = CURRENT.get()
    if budget is None or budget.operation is None:
        require_idle()
    start_boot = BOOT.read_text().strip()
    # A newly approved operation must publish its own retirement binding, even
    # when its predecessor saved a valid proof before losing the final reply.
    if (
        not force
        and verify_unchanged(cfg, raw)
        and (verify_only or budget is None or budget.operation is None)
    ):
        return "unchanged"
    if verify_only:
        raise RuntimeError("ordinary reconciliation required")
    if budget is not None:
        # Failed observations describe the predecessor, not attempted effects.
        budget.failures.clear()
    _require(budget is not None)
    assert budget is not None
    root = budget.operation is None
    route_store = ordinary_routes.RouteStore()
    if root:
        snapshot = route_store.snapshot(cfg, STATE, run, start_boot, proof=PROOF)
        route_plan = ordinary_routes.RouteRetirementPlan.build(
            snapshot, ordinary_routes.projection(cfg)
        )
        ordinary_routes.require(not route_plan.retirement, "ordinary_route_approval_required")

        manager = Manager(budget.deadline)
        manager.preflight()
        request_id = os.urandom(16).hex()
        predecessor = digest(binding(raw))
        route_store.prepare(
            {
                "id": request_id,
                "config": hashlib.sha256(raw).hexdigest(),
                "artifact": package_identity(),
                "predecessor": predecessor,
            },
            route_plan,
            approved=False,
        )
        budget.operation = Operation.begin(
            request=request_id,
            config=hashlib.sha256(raw).hexdigest(),
            artifact=package_identity(),
            predecessor=predecessor,
            manager=manager,
            network=manager.network(),
        )
        budget.operation.phase("reconcile")
    operation = budget.operation
    assert operation is not None
    budget.effect_deadline = budget.deadline - 30
    try:
        obligations = route_store.cleanup(operation, cfg, run, start_boot)
        endpoints = StrongSwanRenderer().render_and_apply(cfg)
        if endpoints:
            XFRMManager().setup_interfaces(endpoints)
        firewall.update_firewall_from_config(cfg, require_reload=True)
        FRRRenderer().render_and_apply(cfg, require_reload=True)
        enforce_routing_invariants_locked(cfg)
        budget.require_success()
        runtime = observe_local(cfg)
        budget.require_success()
        _require(CONFIG.read_bytes() == raw and BOOT.read_text().strip() == start_boot)
        _require(
            all(
                effect["state"] == "settled"
                for effect in operation.check()["effects"]
                if effect["writer"]["pid"] == os.getpid()
            )
        )
        obligations = route_store.verify(cfg, run, start_boot, obligations=obligations)
        ledger_digest = route_store.commit(operation.check(), obligations)
        StateStore(STATE).save_last_applied(cfg)
        atomic_write_json(
            PROOF,
            {
                "schema": SCHEMA,
                "binding": binding(raw),
                "runtime": runtime,
                ordinary_routes.PROOF_KEY: ledger_digest,
            },
        )
        if root:
            operation.complete()
        return "applied"
    except BaseException:
        operation.fail()
        raise
    finally:
        if root:
            budget.operation = None


def action(encoded_request: str) -> int:
    """Emit only safe, bounded terminal JSON. All failure detail stays local."""
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "failed",
        "stage": "request",
        "error_code": "ordinary_apply_failed",
    }
    lock = None

    def interrupted(_signal, _frame):
        budget = CURRENT.get()
        if budget is not None:
            budget.deadline = 0
        raise TimeoutError("ordinary action interrupted")

    previous_handlers = {
        sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)
    }
    token = CURRENT.set(CommandBudget(time.monotonic() + 300))
    try:
        _require(len(encoded_request) <= 8192)
        request = json.loads(base64.b64decode(encoded_request, validate=True))
        _require(
            isinstance(request, dict)
            and set(request) == {"request_id", "config_sha256", "boot_id", "verify_only"}
        )
        _require(
            isinstance(request["request_id"], str)
            and re.fullmatch(r"[a-f0-9]{32}", request["request_id"]) is not None
        )
        _require(
            isinstance(request["config_sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", request["config_sha256"]) is not None
        )
        _require(
            isinstance(request["boot_id"], str)
            and re.fullmatch(r"[a-f0-9-]{36}", request["boot_id"]) is not None
        )
        _require(type(request["verify_only"]) is bool)
        receipt.update({key: request[key] for key in ("request_id", "config_sha256", "boot_id")})
        receipt["stage"] = "lock"
        # Nonblocking acquisition avoids an unbounded flock inside the deadline.
        while lock is None:
            lock = acquire_routing_lock(blocking=False)
            budget = CURRENT.get()
            if budget is None or time.monotonic() >= budget.deadline:
                raise TimeoutError("routing_lock_deadline")
            if lock is None:
                time.sleep(0.1)
        _require(lock is not None)
        receipt["stage"] = "identity"
        raw = CONFIG.read_bytes()
        _require(len(raw) <= 4 * 1024 * 1024)
        _require(hashlib.sha256(raw).hexdigest() == request["config_sha256"])
        _require(BOOT.read_text().strip() == request["boot_id"])
        cfg = yaml.safe_load(raw)
        _require(
            isinstance(cfg, dict)
            and isinstance(cfg.get("gateway"), dict)
            and isinstance(cfg.get("connections"), list)
            and cfg.get("vm_ha") is None
        )
        pending = Journal().read()
        budget = CURRENT.get()
        assert budget is not None
        if pending is not None and pending["state"] != "complete":
            budget.operation = Operation.join(request, package_identity(), Manager(budget.deadline))
        receipt["stage"] = "reconcile"
        # Historical helpers may print command output containing config values.
        # Suppress it here; the receipt exposes only the closed stage/error code.
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            status = reconcile_locked(cfg, raw, verify_only=request["verify_only"])
        receipt["stage"] = "verify"
        _require(CONFIG.read_bytes() == raw and BOOT.read_text().strip() == request["boot_id"])
        receipt.update(binding(raw), status=status, package_version=_get_package_version())
        receipt["proof_sha256"] = hashlib.sha256(PROOF.read_bytes()).hexdigest()
        receipt.pop("error_code")
        return_code = 0
    except Exception:
        return_code = 1
    finally:
        if lock is not None:
            os.close(lock)
        CURRENT.reset(token)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return return_code
