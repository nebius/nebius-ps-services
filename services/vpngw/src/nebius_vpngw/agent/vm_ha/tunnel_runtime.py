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
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Generic

import yaml

from ... import ipsec_observation
from ...ha_tunnel_policy import (
    ConnectionPolicy,
    connection_policies,
    selections,
    tunnel_exports,
    tunnel_preferred,
)
from ..frr_renderer import FRR_CONF, FRRRenderer
from ..strongswan_renderer import (
    SWANCTL_CONF,
    StrongSwanRenderer,
    _write_secret_file,
    authorized_configuration,
)
from ..vm_ha_checkpoint import controller_checkpoint_from_dict
from ..vm_ha_controller import ActionKind, DataPlaneMode
from . import kernel_teardown
from .runtime import (
    CommandRunner,
    _advertised_bgp_prefixes,
    _configured_bgp_summary_sessions,
    _confirmed_down_bgp_sessions,
    _established_bgp_sessions,
    _learned_bgp_prefixes,
    _run_command,
)
from .store import atomic_write_json
from .tunnel_observation import (
    NativeTunnelObserver,
    route_destination,
    scoped_ike_ids,
    xfrm_records,
)
from .tunnels import (
    STATE_NAME,
    Authority,
    AuthorityT,
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


def reconcile_inactive_static(
    cfg: dict[str, Any],
    state_dir: Path,
    authority: Authority,
    *,
    candidate: bool,
    permitted: Callable[[float], bool],
    runner: CommandRunner,
) -> None:
    """Remove inactive kernel objects under the dataplane's existing writer lock.

    Candidate preparation retains the selected path; cold passive materialization
    retains none. Neither transition needs a selector operation or a new episode.
    The caller owns fenced allocation admission, including every deletion.
    """
    effective = load_effective_config(cfg, state_dir)
    if not candidate:
        effective = {**effective, "_ha_static_passive": True}
    current = effective
    expected = authorized_configuration(current)
    deadline = time.monotonic() + 30.0

    def revalidate(_authority: Authority) -> bool:
        return (
            time.monotonic() < deadline
            and permitted(deadline)
            and authorized_configuration(current) == expected
            and time.monotonic() < deadline
        )

    if not revalidate(authority):
        raise RuntimeError("inactive Static teardown authority is unproven")
    effects = NativeTunnelEffects(
        cfg,
        state_dir,
        revalidate,
        active_revalidate=revalidate,
        clock=time.monotonic,
        command_runner=runner,
    )
    effects.effect_deadline = deadline

    def install(configuration: dict[str, Any]) -> None:
        nonlocal current, expected
        if not revalidate(authority):
            raise RuntimeError("inactive Static teardown lost its fenced authority")
        effects._write_swan_config(configuration)
        current = configuration
        expected = authorized_configuration(current)
        if not revalidate(authority):
            raise RuntimeError("inactive Static teardown lost its fenced authority")
        effects.run(("swanctl", "--load-all", "--noprompt"))
        if not revalidate(authority):
            raise RuntimeError("inactive Static teardown lost its fenced authority")

    selected = selections(effective)
    for policy in connection_policies(cfg):
        if policy.mode != "static":
            continue
        inactive = tuple(
            path
            for path in policy.paths
            if path.name not in expected.connections
            or candidate
            and policy.name in selected
            and path.name != selected[policy.name]
        )
        if inactive:
            scope = replace(policy, paths=inactive)
            if candidate:
                # Loaded responder configurations are not evidence that their
                # kernel objects belong to the current daemon. Fence just these
                # paths before exact teardown, preserving the selected path and
                # unrelated connections, then restore responder readiness.
                names = {path.name for path in inactive}
                scoped_ike_ids(effects.run(ipsec_observation.command()), names)
                effects._kernel_deletions(scope)
                install({**effective, "_ha_suspended_tunnels": sorted(names)})
                effects._terminate_unloaded(scope, authority)
            effects._remove_orphan_kernel_objects(scope, authority)
            if candidate:
                install(effective)
    if not revalidate(authority):
        raise RuntimeError("inactive Static teardown lost its fenced authority")


class NativeTunnelEffects(Generic[AuthorityT]):
    def __init__(
        self,
        cfg: dict[str, Any],
        state_dir: Path,
        revalidate: Callable[[AuthorityT], bool],
        *,
        active_revalidate: Callable[[AuthorityT], bool],
        routing_lock: Path = Path("/run/nebius-vpngw/fix-routes.lock"),
        clock: Callable[[], float] = time.time,
        command_runner: CommandRunner | None = None,
        routing_lock_held: bool = False,
    ) -> None:
        self.cfg, self.state_dir = cfg, state_dir
        self.revalidate: Callable[[AuthorityT], bool] = revalidate
        self.active_revalidate: Callable[[AuthorityT], bool] = active_revalidate
        self.routing_lock = routing_lock
        self.clock = clock
        self.command_runner = command_runner
        self.routing_lock_held = routing_lock_held
        self.observer = NativeTunnelObserver(self.run)
        self.effect_deadline: float | None = None

    def active(self, authority: AuthorityT) -> bool:
        return self.revalidate(authority) and self.active_revalidate(authority)

    def _observed_policy(self, policy: ConnectionPolicy) -> ConnectionPolicy:
        """Protection history requires routes only while currently learned or configured."""
        if policy.mode != "bgp":
            return policy
        from .forwarding import DestinationProtection

        protection = DestinationProtection(
            self.state_dir / "destination-protection.json",
            cluster=(self.cfg.get("vm_ha") or {}).get("cluster_id", "ordinary"),
            policies=connection_policies(self.cfg),
            local_prefixes=tuple(
                str(ipaddress.ip_network(prefix, strict=False))
                for prefix in self.cfg.get("gateway", {}).get("local_prefixes", ())
            ),
        )
        admitted = frozenset(
            prefix for prefix, owner in protection._ledger().items() if owner == policy.name
        )
        if not admitted:
            return policy
        summary = json.loads(self.run(("vtysh", "-c", "show bgp summary json")))
        configured = _configured_bgp_summary_sessions(summary)
        established = _established_bgp_sessions(summary)
        down = _confirmed_down_bgp_sessions(summary)
        peers = {path.peer for path in policy.paths}
        if (
            configured is None
            or not peers.issubset(configured)
            or not peers.issubset(established | down)
        ):
            raise RuntimeError("Current BGP destination evidence is unavailable")
        received: set[str] = set()
        for peer in sorted(peers & established):
            received.update(self._received_prefixes(peer))
        return replace(policy, destinations=policy.destinations | (admitted & received))

    def _received_prefixes(self, peer: str) -> frozenset[str]:
        value = json.loads(
            self.run(("vtysh", "-c", f"show bgp ipv4 unicast neighbors {peer} routes json"))
        )
        if not isinstance(value, dict) or not isinstance(value.get("routes"), dict):
            raise ValueError("Current BGP routes are unavailable")
        received: set[str] = set()
        for prefix, paths in value["routes"].items():
            network = ipaddress.ip_network(prefix, strict=False)
            if network.version != 4 or not isinstance(paths, list) or not paths:
                raise ValueError("Current BGP routes are malformed")
            if any(not isinstance(p, dict) or type(p.get("valid")) is not bool for p in paths):
                raise ValueError("Current BGP route validity is unavailable")
            if any(p["valid"] for p in paths):
                received.add(str(network))
        return frozenset(received)

    def _path_health(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health:
        try:
            return self.observer.observe(self._observed_policy(policy), target, selected=selected)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return Health.UNKNOWN

    def probe_preferred(
        self, policy: ConnectionPolicy, target: str, authority: AuthorityT
    ) -> Health:
        """Bounded owner-only initiation/probe; never change workload route selection."""
        if policy.mode != "static" or not self.active(authority):
            return Health.UNKNOWN
        descriptor = (
            None
            if self.routing_lock_held
            else os.open(self.routing_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        )
        try:
            if descriptor is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.active(authority):
                return Health.UNKNOWN
            cfg = self._effective(authority)
            inventory = authorized_configuration(cfg)
            if target not in inventory.connections:
                return Health.UNKNOWN
            tunnel = next(
                tunnel
                for conn in cfg["connections"]
                for tunnel in conn["tunnels"]
                if tunnel["name"] == target
            )
            probe = self.cfg.get("gateway", {}).get("tunnel_probes", {}).get(target)
            source, peer = tunnel.get("inner_local_ip"), tunnel.get("inner_remote_ip")
            if probe is None:
                if not source or not peer or not tunnel.get("inner_cidr"):
                    return Health.UNKNOWN
                network = ipaddress.ip_network(tunnel["inner_cidr"], strict=False)
                if (
                    ipaddress.ip_address(source) not in network
                    or ipaddress.ip_address(peer) not in network
                ):
                    return Health.UNKNOWN
            self.effect_deadline = self.clock() + 5
            health = self._path_health(policy, target, selected=False)
            if health is Health.DOWN:
                if not self.active(authority):
                    return Health.UNKNOWN
                # keyingtries=1 and start_action=none bound the explicit attempt;
                # no trap or native autonomous restart is enabled for this path.
                self.run(("swanctl", "--initiate", "--child", target, "--timeout", "1"))
                health = self._path_health(policy, target, selected=False)
            if health is not Health.HEALTHY:
                return health
            if probe is not None:
                # Managed VPN peers need not answer their synthetic inner IP.
                # The configured probe already enforces assigned source, exact
                # XFRM interface, timeout and existing health hysteresis.
                health = self.observe(policy, target, selected=False)
                return health if self.active(authority) else Health.UNKNOWN
            path = next(path for path in policy.paths if path.name == target)
            addresses = json.loads(
                self.run(("ip", "-j", "-4", "address", "show", "dev", path.interface))
            )
            if not any(
                a.get("local") == source for row in addresses for a in row.get("addr_info", [])
            ):
                return Health.UNKNOWN
            self.run(("ping", "-n", "-c", "1", "-I", path.interface, "-I", source, "-W", "1", peer))
            return (
                self.observe(policy, target, selected=False)
                if self.active(authority)
                else Health.UNKNOWN
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, StopIteration):
            return Health.UNKNOWN
        finally:
            self.effect_deadline = None
            if descriptor is not None:
                os.close(descriptor)

    def candidate(self, policy: ConnectionPolicy, target: str) -> Health:
        """Prove usable received coverage without requiring active exports/routes."""
        health = self._path_health(policy, target, selected=False)
        if health is not Health.HEALTHY:
            return health
        peer = next(p.peer for p in policy.paths if p.name == target)
        try:
            received = self._received_prefixes(peer)
            return (
                Health.HEALTHY
                if self._observed_policy(policy).destinations.issubset(received)
                else Health.DOWN
            )
        except (RuntimeError, ValueError, TypeError, KeyError):
            return Health.UNKNOWN

    def run(self, argv: Sequence[str]) -> str:
        limit = 3.0
        if self.effect_deadline is not None:
            limit = min(limit, self.effect_deadline - self.clock())
        if limit <= 0:
            raise RuntimeError("HA tunnel effect deadline exhausted")
        try:
            result = (self.command_runner or _run_command)(argv, limit)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("HA tunnel native observation or effect is unavailable") from None
        if result.returncode != 0:
            raise RuntimeError("HA tunnel native command failed")
        return result.stdout

    @contextlib.contextmanager
    def locked(self, authority: AuthorityT) -> Iterator[None]:
        descriptor = (
            None
            if self.routing_lock_held
            else os.open(self.routing_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        )
        try:
            try:
                if descriptor is not None:
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
                if state["operation"]["phase"] in {"restore", "verify-restore"}:
                    # Cleanup can resume after reboot/expiry without admitting
                    # another candidate or renewing the persisted episode.
                    self.effect_deadline = self.clock() + 10
            yield
        finally:
            self.effect_deadline = None
            if descriptor is not None:
                os.close(descriptor)

    def observe(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health:
        health = self._path_health(policy, target, selected=selected)
        probe = self.cfg.get("gateway", {}).get("tunnel_probes", {}).get(target)
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
            limit = float(probe.get("timeout_seconds", 1)) + 0.2
            if self.effect_deadline is not None:
                limit = min(limit, self.effect_deadline - self.clock())
                if limit <= 0:
                    return Health.UNKNOWN
            result = (self.command_runner or _run_command)(
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
                limit,
            )
            if self.effect_deadline is not None and self.clock() >= self.effect_deadline:
                return Health.UNKNOWN
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

    def observe_readonly(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health:
        """Sample native state and probe the path without updating product state."""
        health = self._path_health(policy, target, selected=selected)
        probe = self.cfg.get("gateway", {}).get("tunnel_probes", {}).get(target)
        if health is not Health.HEALTHY or probe is None:
            return health
        try:
            record = json.loads((self.state_dir / "tunnel-probes.json").read_text())[target]
            # The record only gates controller-owned hysteresis. It is never
            # relabelled as fresh health: every positive sample probes anew.
            if record["probe"] != probe or Health(record["health"]) is not Health.HEALTHY:
                return Health.UNKNOWN
            path = next(p for p in policy.paths if p.name == target)
            addresses = json.loads(self.run(("ip", "-j", "-4", "address", "show")))
            if not any(
                a.get("local") == probe["source_ip"]
                for link in addresses
                for a in link.get("addr_info", [])
            ):
                return Health.UNKNOWN
            self.run(
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
                )
            )
            return Health.HEALTHY
        except (RuntimeError, OSError, ValueError, TypeError, KeyError):
            return Health.UNKNOWN

    def _effective(self, authority: AuthorityT) -> dict[str, Any]:
        return effective_config(self.cfg, read_state(self.state_dir / STATE_NAME), authority)

    def _write_swan_config(self, cfg: dict[str, Any]) -> None:
        rendered: dict[Path, str | None] = {}
        with contextlib.redirect_stdout(io.StringIO()):
            StrongSwanRenderer().render_and_apply(cfg, rendered_files=rendered)
        content = rendered[SWANCTL_CONF]
        if content is None:
            raise RuntimeError("HA tunnel configuration was not rendered")
        _write_secret_file(SWANCTL_CONF, content)

    def _swan_config(self, cfg: dict[str, Any]) -> None:
        self._write_swan_config(cfg)
        # Upstream loads credentials before connections on the same VICI
        # session; a daemon restart cannot split those loads across processes.
        self.run(("swanctl", "--load-all", "--noprompt"))

    def suspend(self, policy: ConnectionPolicy, authority: AuthorityT) -> None:
        with self.locked(authority):
            names = {p.name for p in policy.paths}
            # Prove the complete termination scope before unloading or terminating.
            try:
                scoped_ike_ids(self.run(ipsec_observation.command()), names)
            except (ValueError, RuntimeError):
                raise TunnelScopeError("HA connection suspension scope is unproven") from None
            try:
                self._kernel_deletions(policy)
            except RuntimeError:
                raise TunnelScopeError("HA kernel suspension scope is unproven") from None
            cfg = self._effective(authority)
            cfg["_ha_suspended_connections"] = [policy.name]
            self._swan_config(cfg)
            self._terminate_unloaded(policy, authority)
            self._remove_orphan_kernel_objects(policy, authority)

    def _terminate_unloaded(self, policy: ConnectionPolicy, authority: AuthorityT) -> None:
        names = {path.name for path in policy.paths}
        try:
            ids = scoped_ike_ids(self.run(ipsec_observation.command()), names)
        except ValueError:
            raise RuntimeError("HA connection suspension scope changed") from None
        for uniqueid in ids:
            if not self.revalidate(authority):
                raise RuntimeError("HA tunnel authority changed before termination")
            # A failed peer cannot acknowledge DELETE. Destroy only proven local
            # IKE/CHILD state without waiting for retransmission.
            try:
                self.run(("swanctl", "--terminate", "--ike-id", uniqueid, "--force"))
            except RuntimeError:
                # Unloading can already delete the SA. A failed terminate is
                # harmless only when fresh complete evidence proves its absence.
                try:
                    remaining = scoped_ike_ids(self.run(ipsec_observation.command()), names)
                except (ValueError, RuntimeError):
                    raise RuntimeError("HA termination outcome is unproven") from None
                if uniqueid in remaining:
                    raise
        try:
            remaining = scoped_ike_ids(self.run(ipsec_observation.command()), names)
        except ValueError:
            raise RuntimeError("HA connection suspension cannot be verified") from None
        if remaining:
            raise RuntimeError("HA connection suspension did not converge")

    def _kernel_deletions(self, policy: ConnectionPolicy) -> tuple[tuple[str, ...], ...]:
        try:
            states = xfrm_records(self.run(("ip", "xfrm", "state", "list", "nokeys")), policy=False)
            # iproute2 6.1 prints exact policy indices only with statistics.
            policies = xfrm_records(self.run(("ip", "-s", "xfrm", "policy")), policy=True)
            if not states and not policies:
                return ()
            return kernel_teardown.deletions(
                policy,
                states,
                policies,
                json.loads(self.run(("ip", "-d", "-j", "link", "show"))),
                json.loads(self.run(("ip", "-j", "address", "show"))),
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeError("HA kernel suspension scope is unproven") from None

    def _remove_orphan_kernel_objects(
        self, policy: ConnectionPolicy, authority: AuthorityT
    ) -> None:
        # A killed daemon forgets kernel SAs. VICI absence alone cannot prove
        # teardown. Native initiation/traps have already been unloaded above.
        names = {p.name for p in policy.paths}
        self._assert_native_suspended(names)
        for command in self._kernel_deletions(policy):
            if not self.revalidate(authority):
                raise RuntimeError("HA tunnel authority changed before kernel teardown")
            if command not in self._kernel_deletions(policy):
                continue  # An exact object may expire between observations.
            try:
                self.run(command)
            except RuntimeError:
                if command in self._kernel_deletions(policy):
                    raise
        if self._kernel_deletions(policy):
            raise RuntimeError("HA kernel suspension did not converge")
        self._assert_native_suspended(names)

    def _assert_native_suspended(self, names: set[str]) -> None:
        try:
            loaded = ipsec_observation.parse_connections(
                self.run((*ipsec_observation.command(), "--connections"))
            )
            sas = scoped_ike_ids(self.run(ipsec_observation.command()), names)
        except ValueError:
            raise RuntimeError("HA suspended native state is unproven") from None
        if names & loaded or sas:
            raise RuntimeError("HA native authority returned during kernel teardown")

    def authority_restored(
        self, policy: ConnectionPolicy, target: str, authority: AuthorityT
    ) -> bool:
        """Verify persisted policy and daemon/route authority without requiring an SA."""
        try:
            with self.locked(authority):
                rendered: dict[Path, str | None] = {}
                with contextlib.redirect_stdout(io.StringIO()):
                    StrongSwanRenderer().render_and_apply(
                        self._effective(authority), rendered_files=rendered
                    )
                if SWANCTL_CONF.read_text() != rendered[SWANCTL_CONF]:
                    return False
                loaded = json.loads(self.run((*ipsec_observation.command(), "--connections")))
                if not isinstance(loaded, list) or any(not isinstance(n, str) for n in loaded):
                    return False
                names = {p.name for p in policy.paths}
                if set(loaded) & names != names:
                    return False
                path = next(p for p in policy.paths if p.name == target)
                routes = json.loads(self.run(("ip", "-j", "-4", "route", "show", "table", "main")))
                return all(
                    any(
                        route_destination(r) == prefix and r.get("dev") == path.interface
                        for r in routes
                    )
                    for prefix in policy.destinations
                )
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, StopIteration):
            return False

    def activate(self, policy: ConnectionPolicy, target: str | None, authority: AuthorityT) -> None:
        if policy.mode == "static":
            # Also handles replay and restoration: no stale responder survives.
            self.suspend(policy, authority)
        with self.locked(authority):
            cfg = self._effective(authority)
            if policy.mode == "static":
                self._swan_config(cfg)
                path = next(p for p in policy.paths if p.name == target)
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
            policy = self._observed_policy(policy)
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
            local = frozenset(
                str(ipaddress.ip_network(prefix, strict=False))
                for prefix in self.cfg.get("gateway", {}).get("local_prefixes", [])
            )
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
        local = frozenset(
            str(ipaddress.ip_network(prefix, strict=False))
            for prefix in self.cfg.get("gateway", {}).get("local_prefixes") or []
        )
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
        ports.data_plane.ipsec_configuration_provider = self.current_ipsec_configuration
        ports.data_plane.ipsec_configuration_observer = self.observe_ipsec_configuration

        self.restart_pending = True
        self.preferred_observations: dict[str, Any] = {}

    def current_ipsec_configuration(self) -> ipsec_observation.ConfigurationInventory | None:
        """Read-only policy proof; pending selection never authorizes a reload."""
        mode = self.ports.data_plane.mode()
        if mode not in {
            DataPlaneMode.ACTIVE,
            DataPlaneMode.PASSIVE,
            DataPlaneMode.ORIGINATING,
        } or tunnel_operation_pending(self.cfg, self.state_dir):
            return None
        effective = load_effective_config(self.cfg, self.state_dir)
        if mode is DataPlaneMode.PASSIVE:
            effective = {**effective, "_ha_static_passive": True}
        return authorized_configuration(effective)

    def observe_ipsec_configuration(self) -> ipsec_observation.ConfigurationInventory | None:
        """Recognize fenced candidate preparation without granting loader authority.

        PREPARE_CANDIDATE_DATAPLANE writes the effective owner file before
        promotion, while mode deliberately remains PASSIVE. Its native coverage
        must remain observable. A missing SA/configuration in this transition
        belongs to candidate preparation, never the standby component loader.
        All repair paths continue to use current_ipsec_configuration instead.
        """
        try:
            return self.current_ipsec_configuration()
        except RuntimeError:
            if self.ports.data_plane.mode() is not DataPlaneMode.PASSIVE:
                raise
            # This proves exact private-file content and permissions only. It
            # neither writes nor proves ownership, coverage or forwarding safety.
            authorized_configuration(load_effective_config(self.cfg, self.state_dir))
            return None

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
        """No background path-expiry effects are owned by this adapter."""

    def current_exports(self) -> dict[str, frozenset[str]]:
        effective = load_effective_config(self.cfg, self.state_dir)
        exports = dict(self.base_exports)
        for connection in effective.get("connections", []):
            for tunnel in connection["tunnels"]:
                peer = tunnel.get("inner_remote_ip")
                if peer in exports and not tunnel_exports(effective, connection, tunnel):
                    exports[peer] = frozenset()
        return exports

    def serving_export_alternatives(self) -> tuple[dict[str, frozenset[str]], ...]:
        """Known serving policies during one bound BGP operation, never authority.

        Intent precedes cloud revalidation and native activation. The isolated
        health sampler must distinguish a still-serving predecessor from missing
        exports; completion and the controller continue to require current intent.
        """
        context = _selection_context(self.cfg, self.state_dir)
        if context is None:
            return ()
        state, authority = context
        operation = state["operation"]
        if (
            state["authority"] != asdict(authority)
            or operation is None
            or operation["phase"] in {"failed", "succeeded"}
        ):
            return ()
        policy = next(
            (p for p in connection_policies(self.cfg) if p.name == operation["connection"]),
            None,
        )
        if (
            policy is None
            or not policy.eligible
            or policy.mode != "bgp"
            or operation["previous"] not in {p.name for p in policy.paths}
            or operation["target"] not in {p.name for p in policy.paths}
        ):
            return ()
        alternatives = []
        targets = [None, operation["previous"]]
        if operation["verb"] == "release":
            # Release intent can precede restoration after the requested policy
            # was installed. That exact policy may still serve during revalidation.
            targets.append(operation["target"])
        for target in dict.fromkeys(targets):
            exports = self.current_exports()
            for path in policy.paths:
                exports[path.peer] = (
                    self.base_exports[path.peer]
                    if target is None or path.name == target
                    else frozenset()
                )
            alternatives.append(exports)
        return tuple(alternatives)

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

    def recovery_claim(self) -> dict[str, Any] | None:
        state = read_state(self.state_dir / STATE_NAME)
        episode = state["episode"]
        if episode is None:
            return None
        return {
            "owner": {**episode["owner"], "epoch": state["authority"]["epoch"]},
            "started": episode["started"],
            "deadline": episode["deadline"],
            "exhausted": bool(episode["exhausted"] or self.clock() >= episode["deadline"]),
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
            # Saved BGP intent can become unusable after passive preparation,
            # while the owner waits for remote routes in ORIGINATING. Permit
            # only the same fenced intent reconciliation here. Changed policy
            # invalidates origination proof; the controller revokes exports and
            # rematerializes it before any forwarding or native tunnel action.
            snapshot.data_plane_mode in {DataPlaneMode.PASSIVE, DataPlaneMode.ORIGINATING}
            or (snapshot.data_plane_mode is DataPlaneMode.BLOCKED and activation_retry)
        )
        if (
            snapshot.apply_locked
            # Component recovery must see the complete daemon defect before a
            # path change can repopulate just its connection definitions.
            or (
                not preparing
                and (
                    not snapshot.readiness.service_healthy
                    or not snapshot.readiness.sample_complete
                    or bool(snapshot.readiness.local_defects)
                )
            )
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
            # Apply owns generation adoption and passive materialization. A
            # snapshot may retire prior-generation proof under this same rearm
            # lock, so tunnel work must yield before observing it. Read the
            # strict inhibition under the lock; never infer authority from a
            # cached status or bypass the snapshot's retirement checks.
            if self.controller.snapshots.providers.apply_lock_operation_id() is not None:
                return
            selector = TunnelController(
                self.cfg,
                self.state_dir / STATE_NAME,
                self.effects,
                preferred_probe=self.effects.probe_preferred,
                preferred_observations=self.preferred_observations,
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
