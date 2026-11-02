"""Native composition for the HA controller's connection tunnel operations."""

from __future__ import annotations

import contextlib
import fcntl
import io
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from ...ha_tunnel_policy import (
    ConnectionPolicy,
    connection_policies,
    selections,
    tunnel_exports,
    tunnel_preferred,
)
from ..frr_renderer import FRR_CONF, FRRRenderer
from ..strongswan_renderer import SWANCTL_CONF, StrongSwanRenderer, _write_secret_file
from ..vm_ha_checkpoint import controller_checkpoint_from_dict
from ..vm_ha_controller import ActionKind, DataPlaneMode
from .runtime import (
    _advertised_bgp_prefixes,
    _configured_bgp_summary_sessions,
    _confirmed_down_bgp_sessions,
    _established_bgp_sessions,
    _learned_bgp_prefixes,
    _run_command,
)
from .store import atomic_write_json
from .tunnel_observation import NativeTunnelObserver, route_destination, scoped_ike_ids
from .tunnels import (
    STATE_NAME,
    Authority,
    Health,
    TunnelController,
    TunnelScopeError,
    effective_config,
    read_state,
)


def _selection_context(
    cfg: dict[str, Any], state_dir: Path
) -> tuple[dict[str, Any], Authority] | None:
    if not cfg.get("vm_ha") or not (state_dir / STATE_NAME).exists():
        return None
    state = read_state(state_dir / STATE_NAME)
    stored = state["authority"]
    if stored is None:
        return None
    try:
        checkpoint = controller_checkpoint_from_dict(
            json.loads((state_dir / "controller-checkpoint.json").read_text())
        )
    except FileNotFoundError:
        return None
    ha = cfg["vm_ha"]
    authority = Authority(
        str(ha["cluster_id"]),
        str(ha["node"]["node_id"]),
        str(ha["runtime_binding"]["shared_allocation_id"]),
        str(stored["epoch"]),
        checkpoint.ownership_incarnation,
        str(ha["generation"]["generation_id"]),
    )
    # Lost ownership invalidates even a repeated provider epoch.
    if checkpoint.ownership_continuity_invalidated:
        return None
    return state, authority


def tunnel_operation_pending(cfg: dict[str, Any], state_dir: Path) -> bool:
    """Inspect under the rearm lock before admitting another HA writer."""
    context = _selection_context(cfg, state_dir)
    if context is None:
        return False
    state, authority = context
    operation = state["operation"]
    return (
        state["authority"] == asdict(authority)
        and operation is not None
        and operation["phase"] not in {"succeeded", "failed"}
    )


def load_effective_config(cfg: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    context = _selection_context(cfg, state_dir)
    if context is None:
        return cfg
    state, authority = context
    return effective_config(cfg, state, authority)


class NativeTunnelEffects:
    def __init__(
        self,
        cfg: dict[str, Any],
        state_dir: Path,
        revalidate: Callable[[Authority], bool],
        *,
        active_revalidate: Callable[[Authority], bool],
        routing_lock: Path = Path("/run/nebius-vpngw/fix-routes.lock"),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg, self.state_dir = cfg, state_dir
        self.revalidate = revalidate
        self.active_revalidate = active_revalidate
        self.routing_lock = routing_lock
        self.clock = clock
        self.observer = NativeTunnelObserver(self.run)
        self.effect_deadline: float | None = None

    def active(self, authority: Authority) -> bool:
        return self.revalidate(authority) and self.active_revalidate(authority)

    def candidate(self, policy: ConnectionPolicy, target: str) -> Health:
        """Prove usable received coverage without requiring active exports/routes."""
        health = self.observer.observe(policy, target, selected=False)
        if health is not Health.HEALTHY:
            return health
        peer = next(p.peer for p in policy.paths if p.name == target)
        try:
            value = json.loads(
                self.run(("vtysh", "-c", f"show bgp ipv4 unicast neighbors {peer} routes json"))
            )
            if not isinstance(value, dict) or not isinstance(value.get("routes"), dict):
                return Health.UNKNOWN
            received: set[str] = set()
            for prefix, paths in value["routes"].items():
                network = ipaddress.ip_network(prefix, strict=False)
                if network.version != 4 or not isinstance(paths, list) or not paths:
                    return Health.UNKNOWN
                if any(not isinstance(p, dict) or type(p.get("valid")) is not bool for p in paths):
                    return Health.UNKNOWN
                if any(p["valid"] for p in paths):
                    received.add(str(network))
            return Health.HEALTHY if policy.destinations.issubset(received) else Health.DOWN
        except (RuntimeError, ValueError, TypeError, KeyError):
            return Health.UNKNOWN

    def run(self, argv: Sequence[str]) -> str:
        limit = 3.0
        if self.effect_deadline is not None:
            limit = min(limit, self.effect_deadline - self.clock())
        if limit <= 0:
            raise RuntimeError("HA tunnel effect deadline exhausted")
        try:
            result = _run_command(argv, limit)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("HA tunnel native observation or effect is unavailable") from None
        if result.returncode != 0:
            raise RuntimeError("HA tunnel native command failed")
        return result.stdout

    @contextlib.contextmanager
    def locked(self, authority: Authority) -> Iterator[None]:
        descriptor = os.open(self.routing_lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("HA routing writer is busy") from error
            if not self.revalidate(authority):
                raise RuntimeError("HA tunnel authority changed")
            state = read_state(self.state_dir / STATE_NAME)
            self.effect_deadline = state["operation"]["deadline"]
            policy = next(
                p
                for p in connection_policies(self.cfg)
                if p.name == state["operation"]["connection"]
            )
            if policy.mode == "bgp" and state["operation"]["verb"] == "release":
                self.effect_deadline = self.clock() + 10
            if policy.mode == "static" and state["episode"] is not None:
                self.effect_deadline = min(self.effect_deadline, state["episode"]["deadline"] - 1)
            yield
        finally:
            self.effect_deadline = None
            os.close(descriptor)

    def observe(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health:
        health = self.observer.observe(policy, target, selected=selected)
        probe = self.cfg["vm_ha"].get("tunnel_probes", {}).get(target)
        if health is not Health.HEALTHY or probe is None:
            return health
        path = next(p for p in policy.paths if p.name == target)
        record_path = self.state_dir / "tunnel-probes.json"
        descriptor = None
        try:
            descriptor = os.open(
                self.state_dir / "tunnel-probes.lock", os.O_CREAT | os.O_RDWR, 0o600
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            records = json.loads(record_path.read_text()) if record_path.exists() else {}
            record = records.get(target, {})
            now = self.clock()
            if record.get("probe") != probe or now < record.get("observed_at", now):
                record = {}
            if (
                record.get("probe") == probe
                and now >= record.get("observed_at", now)
                and now - record.get("observed_at", 0) < probe.get("interval_seconds", 5)
            ):
                return Health(record["health"])
            addresses = json.loads(self.run(("ip", "-j", "-4", "address", "show")))
            if not any(
                a.get("local") == probe["source_ip"]
                for link in addresses
                for a in link.get("addr_info", [])
            ):
                return Health.UNKNOWN
            result = _run_command(
                (
                    "ping",
                    "-n",
                    "-c",
                    "1",
                    "-I",
                    path.interface,
                    "-I",
                    probe["source_ip"],
                    "-W",
                    str(probe.get("timeout_seconds", 1)),
                    probe["target_ip"],
                ),
                float(probe.get("timeout_seconds", 1)) + 0.2,
            )
            if result.returncode not in {0, 1}:
                return Health.UNKNOWN
            success = result.returncode == 0
            failures = 0 if success else int(record.get("failures", 0)) + 1
            successes = int(record.get("successes", 0)) + 1 if success else 0
            health = (
                Health.HEALTHY
                if successes >= probe.get("recovery_threshold", 2)
                else Health.DOWN
                if failures >= probe.get("failure_threshold", 3)
                else Health(record.get("health", Health.UNKNOWN.value))
            )
            records[target] = {
                "probe": probe,
                "observed_at": now,
                "failures": failures,
                "successes": successes,
                "health": health.value,
            }
            atomic_write_json(record_path, records)
            return health
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
            return Health.UNKNOWN
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _effective(self, authority: Authority) -> dict[str, Any]:
        return effective_config(self.cfg, read_state(self.state_dir / STATE_NAME), authority)

    def _swan_config(self, cfg: dict[str, Any]) -> None:
        rendered: dict[Path, str | None] = {}
        with contextlib.redirect_stdout(io.StringIO()):
            StrongSwanRenderer().render_and_apply(cfg, rendered_files=rendered)
        content = rendered[SWANCTL_CONF]
        if content is None:
            raise RuntimeError("HA tunnel configuration was not rendered")
        _write_secret_file(SWANCTL_CONF, content)
        self.run(("swanctl", "--load-conns"))

    def suspend(self, policy: ConnectionPolicy, authority: Authority) -> None:
        with self.locked(authority):
            names = {p.name for p in policy.paths}
            # Prove the complete termination scope before unloading or terminating.
            try:
                scoped_ike_ids(self.run(("swanctl", "--list-sas", "--raw")), names)
            except (ValueError, RuntimeError):
                raise TunnelScopeError("HA connection suspension scope is unproven") from None
            cfg = self._effective(authority)
            cfg["_ha_suspended_connections"] = [policy.name]
            self._swan_config(cfg)
            try:
                ids = scoped_ike_ids(self.run(("swanctl", "--list-sas", "--raw")), names)
            except ValueError:
                raise RuntimeError("HA connection suspension scope changed") from None
            for uniqueid in ids:
                if not self.revalidate(authority):
                    raise RuntimeError("HA tunnel authority changed before termination")
                # A failed peer cannot acknowledge DELETE. After proving the
                # exact IKE/CHILD scope, destroy local state without waiting
                # for retransmission; verify absence below before activation.
                try:
                    self.run(("swanctl", "--terminate", "--ike-id", uniqueid, "--force"))
                except RuntimeError:
                    # Unloading a start_action connection can already delete
                    # its SA while the authoritative owner is rechecked. A
                    # failed terminate is harmless only after a fresh, complete
                    # scoped observation proves that exact SA is absent.
                    try:
                        remaining = scoped_ike_ids(
                            self.run(("swanctl", "--list-sas", "--raw")), names
                        )
                    except (ValueError, RuntimeError):
                        raise RuntimeError("HA termination outcome is unproven") from None
                    if uniqueid in remaining:
                        raise
            try:
                remaining = scoped_ike_ids(self.run(("swanctl", "--list-sas", "--raw")), names)
            except ValueError:
                raise RuntimeError("HA connection suspension cannot be verified") from None
            if remaining:
                raise RuntimeError("HA connection suspension did not converge")

    def activate(self, policy: ConnectionPolicy, target: str | None, authority: Authority) -> None:
        if policy.mode == "static":
            # Also handles replay and restoration: no stale responder survives.
            self.suspend(policy, authority)
        with self.locked(authority):
            cfg = self._effective(authority)
            if policy.mode == "static":
                self._swan_config(cfg)
                path = next(p for p in policy.paths if p.name == target)
                self.run(("swanctl", "--initiate", "--child", path.name, "--timeout", "1"))
                for destination in sorted(policy.destinations):
                    if not self.revalidate(authority):
                        raise RuntimeError("HA tunnel route authority changed")
                    self.run(("ip", "route", "replace", destination, "dev", path.interface))
            else:
                if not self.active(authority):
                    raise RuntimeError("BGP tunnel policy requires active forwarding authority")
                rendered: dict[Path, str | None] = {}
                with contextlib.redirect_stdout(io.StringIO()):
                    FRRRenderer().render_and_apply(cfg, rendered_files=rendered)
                content = rendered[FRR_CONF]
                if content is None:
                    raise RuntimeError("HA BGP policy was not rendered")
                if not self.active(authority):
                    raise RuntimeError("BGP tunnel authority changed before policy write")
                # Apply grants group-write access to this installed inode, not
                # directory-create or ownership-changing capabilities. Preserve
                # that boundary. Durable operation intent and writer exclusion
                # make an interrupted write replayable before native activation.
                descriptor = os.open(FRR_CONF, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Synchronous native client; no systemd reload job can outlive
                # the routing writer and race restoration or apply.
                if not self.active(authority):
                    raise RuntimeError("BGP tunnel authority changed before native reload")
                self.run(("vtysh", "-b"))
                for path in policy.paths:
                    if not self.active(authority):
                        raise RuntimeError("BGP tunnel authority changed before route refresh")
                    self.run(("vtysh", "-c", f"clear bgp ipv4 unicast {path.peer} soft"))

    def verify(self, policy: ConnectionPolicy, target: str | None) -> Health:
        if policy.mode == "static":
            return self.observe(policy, target or policy.preferred, selected=True)
        try:
            summary = json.loads(self.run(("vtysh", "-c", "show bgp summary json")))
            configured = _configured_bgp_summary_sessions(summary)
            established = _established_bgp_sessions(summary)
            down = _confirmed_down_bgp_sessions(summary)
            peers = {p.peer for p in policy.paths}
            if (
                configured is None
                or not peers.issubset(configured)
                or not peers.issubset(established | down)
            ):
                return Health.UNKNOWN
            available = [
                p
                for p in policy.paths
                if p.peer in established and (target is None or p.name == target)
            ]
            if not available:
                return Health.DOWN
            for path in available:
                health = self.observe(policy, path.name, selected=False)
                if health is not Health.HEALTHY:
                    return health
            routes = json.loads(self.run(("ip", "-j", "-4", "route", "show", "table", "main")))
            for prefix in policy.destinations:
                matches = [r for r in routes if route_destination(r) == prefix]
                if (
                    len(matches) != 1
                    or matches[0].get("protocol") != "bgp"
                    or matches[0].get("nexthops")
                ):
                    return Health.DOWN
                selected = next(
                    (
                        p
                        for p in available
                        if matches[0].get("dev") == p.interface
                        and matches[0].get("gateway") == p.peer
                    ),
                    None,
                )
                if selected is None:
                    return Health.DOWN
                received = json.loads(
                    self.run(
                        (
                            "vtysh",
                            "-c",
                            f"show bgp ipv4 unicast neighbors {selected.peer} routes json",
                        )
                    )
                )
                if prefix not in _learned_bgp_prefixes(received):
                    return Health.DOWN
            connection = next(c for c in self.cfg["connections"] if c["name"] == policy.name)
            permits = connection.get("bgp", {}).get("advertise_local_prefixes", True)
            local = frozenset(self.cfg.get("gateway", {}).get("local_prefixes", []))
            for candidate in policy.paths:
                if candidate.peer in down:
                    continue
                exports = _advertised_bgp_prefixes(
                    json.loads(
                        self.run(
                            (
                                "vtysh",
                                "-c",
                                f"show bgp ipv4 unicast neighbors {candidate.peer} advertised-routes json",
                            )
                        )
                    )
                )
                expected = (
                    local
                    if permits and (target is None or candidate.name == target)
                    else frozenset()
                )
                if exports != expected:
                    return Health.UNKNOWN if exports is None else Health.DOWN
            return Health.HEALTHY
        except (RuntimeError, ValueError, TypeError, KeyError):
            return Health.UNKNOWN

    def fence(self) -> None:
        # Never wait for the routing writer when the episode reserves its final second.
        closed = _run_command(("sysctl", "-w", "net.ipv4.ip_forward=0"), 0.75)
        proof = _run_command(("sysctl", "-n", "net.ipv4.ip_forward"), 0.2)
        if closed.returncode or proof.returncode or proof.stdout.strip() != "0":
            raise RuntimeError("HA tunnel emergency fence could not be verified")


class ControllerTunnelRuntime:
    """Called by the existing HA controller service, under its writer exclusion."""

    def __init__(
        self,
        config_path: Path,
        state_dir: Path,
        controller: Any,
        ports: Any,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config_path, self.state_dir = config_path, state_dir
        self.controller, self.ports, self.clock = controller, ports, clock
        self.cfg = yaml.safe_load(config_path.read_text())
        self.effects = NativeTunnelEffects(
            self.cfg, state_dir, self.revalidate, active_revalidate=self.active, clock=clock
        )
        local = frozenset(self.cfg.get("gateway", {}).get("local_prefixes") or [])
        self.base_exports = {
            p.peer: local
            if next(c for c in self.cfg["connections"] if c["name"] == policy.name)
            .get("bgp", {})
            .get("advertise_local_prefixes", True)
            else frozenset()
            for policy in connection_policies(self.cfg)
            if policy.mode == "bgp"
            for p in policy.paths
        }
        ports.routes.static_readiness_provider = self.static_ready
        ports.routes.bgp_override_readiness_provider = self.bgp_overrides_ready
        ports.routes.passive_bgp_policy_provider = self.passive_bgp_policy_matches
        ports.data_plane.effective_export_provider = self.current_exports
        ports.data_plane.bgp_path_policies = tuple(
            policy for policy in connection_policies(self.cfg) if policy.mode == "bgp"
        )
        self.watchdog: threading.Timer | None = None
        self.watchdog_deadline: float | None = None
        self.restart_pending = True

    def active(self, authority: Authority) -> bool:
        # The normal authority callback still permits fenced Static preparation.
        return (
            self.ports.data_plane.mode() is DataPlaneMode.ACTIVE and self.authority() == authority
        )

    def bgp_overrides_ready(self) -> bool:
        """Read-only snapshot predicate; never recurse into authority/snapshots."""
        if self.ports.data_plane.mode() is DataPlaneMode.ACTIVE:
            return True
        effective = load_effective_config(self.cfg, self.state_dir)
        selected = selections(effective)
        return all(
            self.effects.candidate(policy, selected[policy.name]) is Health.HEALTHY
            for policy in connection_policies(effective)
            if policy.mode == "bgp" and policy.name in selected
        )

    def passive_bgp_policy_matches(self) -> bool | None:
        """Read-only evidence for the canonical passive materialization action.

        Clearing durable intent does not change FRR's retained routes. Refresh
        mismatched imports through ENTER_PASSIVE, with deny-all exports intact.
        No snapshot collection, writer lock, or native effect belongs here.
        """
        if self.ports.data_plane.mode() is not DataPlaneMode.PASSIVE:
            return True
        effective = load_effective_config(self.cfg, self.state_dir)
        expected = {
            str(t.get("bgp", {}).get("remote_ip") or t["inner_remote_ip"]): (
                "SET-LOCAL-PREF-200" if tunnel_preferred(effective, c, t) else "SET-LOCAL-PREF-100"
            )
            for c in effective.get("connections", [])
            if c.get("routing_mode") == "bgp"
            for t in c.get("tunnels", [])
            if t.get("ha_role") != "disable"
        }
        if not expected:
            return True
        try:
            running = self.effects.run(("vtysh", "-c", "show running-config"))
        except RuntimeError:
            return None
        bindings = re.findall(r"^\s*neighbor (\S+) route-map (\S+) in\s*$", running, re.MULTILINE)
        observed = {peer: policy for peer, policy in bindings if peer in expected}
        if len([peer for peer, _ in bindings if peer in expected]) != len(observed):
            return None
        if set(observed) != set(expected):
            return None
        return observed == expected

    def close(self) -> None:
        if self.watchdog is not None:
            self.watchdog.cancel()
        self.watchdog_deadline = None

    def current_exports(self) -> dict[str, frozenset[str]]:
        effective = load_effective_config(self.cfg, self.state_dir)
        exports = dict(self.base_exports)
        for connection in effective.get("connections", []):
            for tunnel in connection["tunnels"]:
                peer = tunnel.get("inner_remote_ip")
                if peer in exports and not tunnel_exports(effective, connection, tunnel):
                    exports[peer] = frozenset()
        return exports

    def static_ready(self) -> bool:
        effective = load_effective_config(self.cfg, self.state_dir)
        selected = selections(effective)
        for policy in connection_policies(effective):
            if policy.mode != "static":
                continue
            if policy.eligible:
                if (
                    self.effects.observe(
                        policy, selected.get(policy.name, policy.preferred), selected=True
                    )
                    is not Health.HEALTHY
                ):
                    return False
            else:
                routes = {
                    prefix: path.name for path in policy.paths for prefix in path.destinations
                }
                for path in policy.paths:
                    required = frozenset(p for p, name in routes.items() if name == path.name)
                    if (
                        required
                        and self.effects.observe(
                            replace(policy, destinations=required), path.name, selected=True
                        )
                        is not Health.HEALTHY
                    ):
                        return False
        return True

    def _watch_episode(self) -> None:
        state = read_state(self.state_dir / STATE_NAME)
        episode = state["episode"]
        pending = state["operation"] is not None and state["operation"]["phase"] not in {
            "failed",
            "succeeded",
        }
        if pending:
            operation = state["operation"]
            policy = next(
                p for p in connection_policies(self.cfg) if p.name == operation["connection"]
            )
            pending = policy.mode == "static" and operation["effects_started"]
        healthy = bool(state["health"]) and all(
            state["health"].get(p.name) == "healthy"
            for p in connection_policies(self.cfg)
            if p.mode == "static"
        )
        deadline = (
            episode["deadline"] - 1 if episode is not None and (pending or not healthy) else None
        )
        if deadline == self.watchdog_deadline:
            return
        if self.watchdog is not None:
            self.watchdog.cancel()
        self.watchdog_deadline = deadline
        if deadline is not None:
            self.watchdog = threading.Timer(max(0, deadline - self.clock()), self.effects.fence)
            self.watchdog.daemon = True
            self.watchdog.start()

    def recovery_claim(self) -> dict[str, Any] | None:
        state = read_state(self.state_dir / STATE_NAME)
        episode = state["episode"]
        if episode is None or episode["exhausted"] or self.clock() >= episode["deadline"] - 1:
            return None
        return {
            "owner": {**episode["owner"], "epoch": state["authority"]["epoch"]},
            "started": episode["started"],
            "deadline": episode["deadline"],
        }

    def authority(self) -> Authority | None:
        return self._authority(preparation=False)

    def _authority(self, *, preparation: bool) -> Authority | None:
        checkpoint = self.controller.controller.checkpoints.load()
        snapshot = self.controller.snapshots.observe()
        state = read_state(self.state_dir / STATE_NAME)
        operation = state["operation"]
        policy = next(
            (
                p
                for p in connection_policies(self.cfg)
                if operation and p.name == operation["connection"]
            ),
            None,
        )
        resuming_fenced = (
            snapshot.data_plane_mode is DataPlaneMode.PASSIVE
            and policy is not None
            and policy.mode == "static"
            and operation is not None
            and operation["phase"] not in {"failed", "succeeded"}
            and state["authority"] is not None
            and state["authority"]["owner"] == snapshot.local_node_id
            and state["authority"]["incarnation"] == checkpoint.ownership_incarnation
            and state["authority"]["generation"] == snapshot.local_generation_id
        )
        pending = checkpoint.pending_action
        activation_retry = bool(
            preparation
            and pending is not None
            and pending.kind is ActionKind.ENABLE_ACTIVE
            and pending.target_node_id == snapshot.local_node_id
            and pending.boot_id == snapshot.boot_id
            and pending.allocation_id == snapshot.cloud.allocation_id
            and pending.ownership_epoch == snapshot.cloud.ownership_epoch
            and pending.ownership_incarnation == checkpoint.ownership_incarnation
            and pending.generation_id == snapshot.local_generation_id
            and pending.digests == snapshot.local_digests
        )
        preparing = preparation and (
            snapshot.data_plane_mode is DataPlaneMode.PASSIVE
            or (snapshot.data_plane_mode is DataPlaneMode.BLOCKED and activation_retry)
        )
        if (
            snapshot.apply_locked
            or snapshot.transfer_inhibited
            or snapshot.transfer_intent is not None
            or snapshot.emergency_active_only
            or snapshot.standby_replacement_inhibited
            or (pending is not None and not activation_retry)
            or (
                snapshot.data_plane_mode is not DataPlaneMode.ACTIVE
                and not resuming_fenced
                and not preparing
            )
            or not snapshot.cloud.local_attachment_exact(snapshot.local_node_id)
            or (
                preparation
                and (
                    checkpoint.ownership_continuity_invalidated
                    or snapshot.guard_boot_id != snapshot.boot_id
                )
            )
        ):
            return None
        peer = snapshot.peer_heartbeat
        if (
            peer is None
            or not peer.tunnel_capability
            or peer.generation_id != snapshot.local_generation_id
            or snapshot.peer_received_at is None
            or not 0 <= snapshot.now - snapshot.peer_received_at < 10
        ):
            return None
        authority = Authority(
            snapshot.cluster_id,
            snapshot.local_node_id,
            snapshot.cloud.allocation_id,
            snapshot.cloud.ownership_epoch,
            checkpoint.ownership_incarnation,
            snapshot.local_generation_id,
        )
        if preparation and (not preparing or state["authority"] != asdict(authority)):
            return None
        return authority

    def revalidate(self, authority: Authority) -> bool:
        return self.authority() == authority

    def _consume_request(self, selector: TunnelController, authority: Authority | None) -> None:
        from .tunnel_requests import REQUEST_NAME, read_request, write_request

        request = read_request(self.state_dir)
        if request is None or request["result"] != "outstanding":
            return
        operation = selector.state["operation"]
        if operation is not None and operation["id"] == request["id"]:
            (self.state_dir / REQUEST_NAME).unlink()
            return
        if self.clock() >= request["expires"]:
            request["result"] = "request-expired"
        elif authority is None:
            return
        elif Authority(**request["authority"]) != authority:
            request["result"] = "authority-changed"
        else:
            try:
                selector.request(
                    request["verb"],
                    request["target"],
                    authority,
                    self.clock(),
                    operation_id=request["id"],
                )
            except (ValueError, RuntimeError):
                request["result"] = "request-rejected"
            else:
                (self.state_dir / REQUEST_NAME).unlink()
                return
        write_request(self.state_dir, request)

    def before_owner_repair(self) -> bool:
        if not connection_policies(self.cfg):
            return False
        # The path may have failed after this cycle's initial tunnel check.
        # Claim only through the canonical writer/authority/evidence gates,
        # before generic repair's pending action would exclude this runtime.
        self.step()
        return True

    def step(self) -> None:
        from ..vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock

        if not connection_policies(self.cfg):
            return
        descriptor = _acquire_rearm_lock(self.state_dir / REARM_LOCK_PATH.name, timeout_seconds=0.0)
        if descriptor is None:
            return
        try:
            selector = TunnelController(
                self.cfg, self.state_dir / STATE_NAME, self.effects, self._watch_episode
            )
            prepare_bgp = any(
                policy.mode == "bgp"
                and (
                    policy.name in selector.state["selected"]
                    or (
                        selector._pending()
                        and selector.state["operation"]["connection"] == policy.name
                    )
                )
                for policy in selector.policies.values()
            )
            prepared_authority = (
                self._authority(preparation=True)
                if prepare_bgp and self.ports.data_plane.mode() is not DataPlaneMode.ACTIVE
                else None
            )
            if prepared_authority is not None:
                selector.prepare_bgp_resume(
                    prepared_authority,
                    self.clock(),
                    revalidate=lambda: self._authority(preparation=True) == prepared_authority,
                )
            authority = self.authority()
            self._consume_request(selector, authority)
            if authority is None:
                snapshot = self.controller.snapshots.last_snapshot
                if (
                    snapshot is not None
                    and snapshot.cloud.authoritative
                    and snapshot.cloud.observed_owner_node_id not in {None, snapshot.local_node_id}
                    and selector._pending()
                ):
                    selector.state["operation"].update(phase="failed", result="authority-changed")
                    selector._save()
                return
            if self.restart_pending:
                if (
                    selector._pending()
                    and selector.policies[selector.state["operation"]["connection"]].mode == "bgp"
                ):
                    selector.state["operation"]["successes"] = 0
                    selector._save()
                if selector.state["episode"] is not None:
                    selector.state["episode"]["healthy_since"] = None
                    selector._save()
                self.restart_pending = False
            selector.tick(authority, self.clock())
            self._watch_episode()
            effective = effective_config(self.cfg, selector.state, authority)
            # Export expectations follow exactly the rendered operational policy.
            exports = dict(self.base_exports)
            for connection in effective["connections"]:
                for tunnel in connection["tunnels"]:
                    peer = tunnel.get("inner_remote_ip")
                    if peer in exports and not tunnel_exports(effective, connection, tunnel):
                        exports[peer] = frozenset()
            self.ports.data_plane.expected_bgp_exports = exports
        finally:
            os.close(descriptor)
