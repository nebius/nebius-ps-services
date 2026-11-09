"""Ordinary authority adapter for the shared selector, inside the existing agent."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .. import ordinary_operations as operations
from .local_commands import CURRENT, CommandBudget, run
from .vm_ha.runtime import CommandResult
from .vm_ha.store import atomic_write_json
from .vm_ha.tunnel_runtime import NativeTunnelEffects
from .vm_ha.tunnels import (
    STATE_NAME,
    OrdinaryAuthority,
    TunnelController,
    effective_config,
    read_state,
)

CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
STATE_DIR = Path("/var/lib/nebius-vpngw/ordinary")


def authority(cfg: dict, boot: str) -> OrdinaryAuthority:
    configured = {key: value for key, value in cfg.items() if not key.startswith("_")}
    if configured.get("vm_ha") is not None:
        raise ValueError("Ordinary selection cannot acquire HA authority")
    return OrdinaryAuthority(boot, operations.digest(configured))


def effective(cfg: dict, state_dir: Path | None = None) -> dict:
    path = (state_dir or STATE_DIR) / STATE_NAME
    if not path.exists():
        return cfg
    bound = authority(cfg, operations.BOOT.read_text().strip())
    return effective_config(cfg, read_state(path), bound)


class PendingSelection(RuntimeError):
    """The agent must resume selection before it can reconcile configuration."""


def require_quiescent(cfg: dict, state_dir: Path | None = None) -> None:
    path = (state_dir or STATE_DIR) / STATE_NAME
    if not path.exists():
        return
    state = read_state(path)
    current = authority(cfg, operations.BOOT.read_text().strip())
    op = state["operation"]
    if state["authority"] == asdict(current) and op and op["phase"] not in {"failed", "succeeded"}:
        raise PendingSelection("ordinary tunnel operation outstanding; wait for agent convergence")


class OrdinarySelectionRuntime:
    def __init__(self, config_path: Path = CONFIG, state_dir: Path = STATE_DIR) -> None:
        self.config_path, self.state_dir = config_path, state_dir
        self.preferred_observations: dict[str, Any] = {}
        self.proven_operation: dict | None = None

    def step(self, request: dict | None = None) -> dict | None:
        if not self.config_path.exists():
            return None
        # This is the guest operation/routing lock used by apply and streamed
        # historical helpers, not a workstation-only mutex.
        with operations.mutation_lock():
            raw = self.config_path.read_bytes()
            cfg = yaml.safe_load(raw)
            if not isinstance(cfg, dict) or cfg.get("vm_ha") is not None:
                return None
            bound = authority(cfg, operations.BOOT.read_text().strip())
            if (
                request is not None
                and request.get("config_sha256") != hashlib.sha256(raw).hexdigest()
            ):
                raise RuntimeError("ordinary tunnel request configuration changed")
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

            def revalidate(expected: OrdinaryAuthority) -> bool:
                operations.require_idle()
                return (
                    expected == bound
                    and self.config_path.read_bytes() == raw
                    and operations.BOOT.read_text().strip() == bound.boot_id
                )

            def command(argv, timeout):
                value = run(argv, capture_output=True, text=True, timeout=timeout)
                return CommandResult(value.returncode, value.stdout)

            from .ordinary import destination_protection

            protection = destination_protection(cfg, state_dir=self.state_dir)

            def active(expected: OrdinaryAuthority) -> bool:
                return revalidate(expected) and protection.ready()

            effects = NativeTunnelEffects(
                cfg,
                self.state_dir,
                revalidate,
                active_revalidate=active,
                command_runner=command,
                routing_lock_held=True,
            )
            selector = TunnelController(
                cfg,
                self.state_dir / STATE_NAME,
                effects,
                preferred_probe=effects.probe_preferred,
                preferred_observations=self.preferred_observations,
            )
            token = CURRENT.set(CommandBudget(time.monotonic() + 10))
            try:
                if request is not None:
                    selector.request(request["verb"], request.get("target"), bound, time.time())
                else:
                    if not revalidate(bound):
                        return selector.state
                    if not protection.ready():
                        protection.install()
                    selector.tick(bound, time.time())
                after = selector.state["operation"]
                if (
                    request is None
                    and after
                    and after["phase"] in {"succeeded", "failed"}
                    and after != self.proven_operation
                ):
                    self._refresh_proof(cfg, raw, selector, bound)
                    self.proven_operation = dict(after)
                return selector.state
            finally:
                CURRENT.reset(token)

    def _refresh_proof(self, cfg, raw, selector, bound) -> None:
        from . import ordinary

        # Native selection changes the expected loaded policy. Refresh only a
        # proof whose package/configuration/boot binding is still exact; preserve
        # route-retirement authority and the ordinary apply journal unchanged.
        try:
            saved = json.loads(ordinary.PROOF.read_text())
            if saved.get("binding") != ordinary.binding(raw):
                return
            observed = ordinary.observe_local(effective_config(cfg, selector.state, bound))
            if (
                self.config_path.read_bytes() != raw
                or operations.BOOT.read_text().strip() != bound.boot_id
            ):
                raise RuntimeError("ordinary selection proof authority changed")
            saved["runtime"] = observed
            atomic_write_json(ordinary.PROOF, saved)
        except (FileNotFoundError, ValueError):
            return


def action(encoded: str | None) -> int:
    """A request persists intent only; the running ordinary agent owns effects."""
    try:
        if encoded is None:
            with operations.mutation_lock():
                cfg = yaml.safe_load(CONFIG.read_bytes())
                current = authority(cfg, operations.BOOT.read_text().strip())
                value = read_state(STATE_DIR / STATE_NAME)
                if value["authority"] != asdict(current):
                    raise RuntimeError("ordinary tunnel status authority changed")
        else:
            if len(encoded) > 8192:
                raise ValueError("request too large")
            request = json.loads(base64.b64decode(encoded, validate=True))
            if not isinstance(request, dict) or set(request) != {"verb", "target", "config_sha256"}:
                raise ValueError("request malformed")
            if request["target"] is not None and not isinstance(request["target"], str):
                raise ValueError("target malformed")
            result = operations.bounded(
                ["systemctl", "is-active", "nebius-vpngw-agent.service"],
                timeout=3,
                capture_output=True,
                text=True,
            )
            if result.returncode or result.stdout.strip() != "active":
                raise RuntimeError("agent unavailable")
            observed = OrdinarySelectionRuntime().step(request)
            if observed is None:
                raise RuntimeError("ordinary configuration unavailable")
            value = observed
        if encoded is None:
            running = operations.bounded(
                ["systemctl", "is-active", "nebius-vpngw-agent.service"],
                timeout=3,
                capture_output=True,
                text=True,
            )
            if running.returncode or running.stdout.strip() != "active":
                raise RuntimeError("ordinary agent unavailable")
        print(json.dumps(value, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "ordinary-tunnel-authority-or-request-unavailable"}))
        return 1
