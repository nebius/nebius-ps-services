"""Exact forgotten-kernel teardown within an admitted HA component repair."""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import vici.exception

from ... import ipsec_observation
from ...ha_tunnel_policy import connection_policies
from .runtime import CommandRunner
from .tunnel_observation import scoped_ike_ids


def reconcile_lost_daemon_kernel(
    cfg: dict[str, Any],
    state_dir: Path,
    *,
    expected: ipsec_observation.ConfigurationInventory,
    permitted: Callable[[], bool],
    runner: CommandRunner,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Revoke only absent native paths; the caller owns cloud admission/writer.

    This is one bounded local revocation transaction, not a selector operation.
    Current cloud authority must be read again before the caller reloads policy.
    A surviving IKE (including a rekey overlap) excludes its entire path.
    """
    from .tunnel_runtime import NativeTunnelEffects

    def remaining() -> float:
        value = deadline - clock()
        if value <= 0:
            raise RuntimeError("daemon kernel recovery deadline exhausted")
        return min(3.0, value)

    def require_local() -> None:
        remaining()
        if not permitted():
            raise RuntimeError("daemon kernel recovery authority changed")
        remaining()

    def configuration() -> ipsec_observation.ConfigurationInventory:
        return ipsec_observation.loaded_configuration(timeout=remaining())

    def sessions() -> str:
        return ipsec_observation.snapshot(timeout=remaining()).require_complete().json()

    def require_absent(names: set[str]) -> None:
        require_local()
        if names & configuration().connections or scoped_ike_ids(sessions(), names):
            raise RuntimeError("native authority returned during daemon kernel recovery")
        require_local()

    try:
        require_local()
        loaded = configuration()
        missing = expected.connections - loaded.connections
        if not missing:
            return  # Credential-only loss never permits kernel teardown.
        sas = sessions()
        eligible = {name for name in missing if not scoped_ike_ids(sas, {name})}
        effects = NativeTunnelEffects(
            cfg,
            state_dir,
            lambda _authority: permitted(),
            active_revalidate=lambda _authority: permitted(),
            clock=clock,
            command_runner=runner,
            routing_lock_held=True,
        )
        effects.effect_deadline = deadline
        scopes = [
            replace(policy, paths=tuple(path for path in policy.paths if path.name in eligible))
            for policy in connection_policies(cfg)
            if any(path.name in eligible for path in policy.paths)
        ]
        if {path.name for scope in scopes for path in scope.paths} != eligible:
            raise RuntimeError("daemon kernel recovery policy scope is incomplete")
        # Prove every affected path before the first mutation. The planner
        # rejects foreign endpoints, interfaces, reqids and ambiguous identities.
        plans = [(scope, effects._kernel_deletions(scope)) for scope in scopes]
        commands = [command for _, plan in plans for command in plan]
        if len(set(commands)) != len(commands):
            raise RuntimeError("daemon kernel recovery scopes overlap")
        remaining_plans = {scope.name: plan for scope, plan in plans}

        def current_plan(scope):
            current = effects._kernel_deletions(scope)
            admitted = remaining_plans[scope.name]
            # Expiry/deletion may remove objects. New objects or changed policy
            # precedence invalidate the admitted order before the next effect.
            if current != tuple(command for command in admitted if command in current):
                raise RuntimeError("daemon kernel recovery plan changed")
            remaining_plans[scope.name] = current
            return current

        # A retained kernel policy can issue ACQUIRE as soon as its SA is
        # removed (for example, on the next BGP keepalive). Revoke all exact
        # policies first so teardown cannot manufacture an unowned SPI-zero SA.
        ordered = sorted(
            ((scope, command) for scope, plan in plans for command in plan),
            key=lambda item: item[1][:3] != ("ip", "xfrm", "policy"),
        )
        policies_revoked = False
        for scope, command in ordered:
            names = {path.name for path in scope.paths}
            require_local()
            if not policies_revoked and command[:3] == ("ip", "xfrm", "state"):
                for candidate, _ in plans:
                    if any(
                        item[:3] == ("ip", "xfrm", "policy") for item in current_plan(candidate)
                    ):
                        raise RuntimeError("daemon kernel recovery policies returned")
                policies_revoked = True
            if command not in current_plan(scope):
                continue  # Natural expiry is verified, never replayed blindly.
            require_absent(names)
            try:
                effects.run(command)
            except RuntimeError:
                if command in current_plan(scope):
                    raise
            remaining_plans[scope.name] = tuple(
                item for item in remaining_plans[scope.name] if item != command
            )
        for scope, _ in plans:
            names = {path.name for path in scope.paths}
            require_absent(names)
            if current_plan(scope):
                raise RuntimeError("daemon kernel recovery did not converge")
        require_local()
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        vici.exception.SessionException,
        vici.exception.CommandException,
        vici.exception.EventUnknownException,
        vici.exception.DeserializationException,
        struct.error,
    ):
        raise RuntimeError("daemon kernel recovery evidence is unavailable") from None
