from __future__ import annotations

import signal
from pathlib import Path

import yaml

from .state_store import StateStore
from .strongswan_renderer import StrongSwanRenderer
from .frr_renderer import FRRRenderer
from .routing_guard import enforce_routing_invariants
from .firewall_manager import update_firewall_from_config

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
STATE_PATH = Path("/etc/nebius-vpngw/last-applied.json")


class Agent:
    def __init__(self) -> None:
        self.state = StateStore(STATE_PATH)
        self.ss = StrongSwanRenderer()
        self.frr = FRRRenderer()

    def reload(self) -> None:
        if not CONFIG_PATH.exists():
            print(f"[Agent] Config not found: {CONFIG_PATH}")
            return
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        
        # CRITICAL: Enforce routing invariants on EVERY reload
        # This must run regardless of config changes to prevent routing issues
        # (e.g., table 220 breaking BGP after agent restart)
        enforce_routing_invariants(cfg)
        
        # Update firewall rules based on config (peer IPs, management CIDRs)
        # This keeps UFW synchronized with VPN peer connections
        # Safe to call on every reload - only updates if peer IPs changed
        try:
            update_firewall_from_config(cfg)
        except Exception as e:
            # Log but don't fail - firewall updates are not critical for VPN functionality
            # (Initial cloud-init already configured basic firewall rules)
            print(f"[Agent] WARNING: Firewall update failed: {e}")
        
        if not self.state.is_changed(cfg):
            print("[Agent] No changes detected; skipping config render")
            return
        # Render configs
        self.ss.render_and_apply(cfg)
        self.frr.render_and_apply(cfg)
        # Persist state
        self.state.save_last_applied(cfg)
        print("[Agent] Applied and persisted new configuration")


def main() -> None:
    agent = Agent()

    def handle_reload(signum, frame):
        print(f"[Agent] Received signal {signum}; reloading")
        agent.reload()

    # Run one reconcile on start
    agent.reload()

    # Daemon: wait for reloads
    signal.signal(signal.SIGHUP, handle_reload)
    print("[Agent] Running; await SIGHUP for reload")
    
    # Loop signal.pause() to handle the case where it returns after signal handling
    while True:
        try:
            signal.pause()
        except KeyboardInterrupt:
            print("[Agent] Received interrupt, exiting")
            break


if __name__ == "__main__":
    main()
