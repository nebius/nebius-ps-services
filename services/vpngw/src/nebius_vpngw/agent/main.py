from __future__ import annotations

import signal
from pathlib import Path

import yaml

from .firewall_manager import update_firewall_from_config
from .frr_renderer import FRRRenderer
from .routing_guard import enforce_routing_invariants
from .state_store import StateStore
from .strongswan_renderer import StrongSwanRenderer
from .xfrm_manager import XFRMManager

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")
STATE_PATH = Path("/etc/nebius-vpngw/last-applied.json")


class Agent:
    def __init__(self) -> None:
        self.state = StateStore(STATE_PATH)
        self.ss = StrongSwanRenderer()
        self.frr = FRRRenderer()
        self.xfrm = XFRMManager()

    def reload(self) -> None:
        if not CONFIG_PATH.exists():
            print(f"[Agent] Config not found: {CONFIG_PATH}")
            return
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

        # Update firewall rules based on config (peer IPs, management CIDRs)
        # With no management CIDRs provided, SSH remains open from anywhere.
        # Safe to call on every reload - only updates if peer IPs changed
        try:
            update_firewall_from_config(cfg)
        except Exception as e:
            # Log but don't fail - firewall updates are not critical for VPN functionality
            print(f"[Agent] WARNING: Firewall update failed: {e}")

        if not self.state.is_changed(cfg):
            print("[Agent] No changes detected; skipping config render")
            # CRITICAL: Enforce routing invariants even when config unchanged
            # This prevents routing issues (table 220, broad APIPA) from persisting
            # across agent restarts when config hasn't changed
            enforce_routing_invariants(cfg)
            return

        # Render strongSwan config (returns interface endpoints for XFRM/VTI management)
        interface_endpoints = self.ss.render_and_apply(cfg)

        # Setup XFRM interfaces. Must happen AFTER strongSwan config is written
        # but BEFORE tunnels come up so CHILD_SAs can bind to the devices.
        if interface_endpoints:
            print("[Agent] Setting up XFRM interfaces...")
            self.xfrm.setup_interfaces(interface_endpoints)

        # Render FRR BGP config
        self.frr.render_and_apply(cfg)

        # Persist state
        self.state.save_last_applied(cfg)
        print("[Agent] Applied and persisted new configuration")

        # CRITICAL: Enforce routing invariants AFTER config rendering completes
        # Must run after strongSwan/FRR rendering because those operations can
        # trigger systemd-networkd reload, which causes DHCP renewal that adds
        # back the problematic routes (table 220, broad APIPA 169.254.0.0/16)
        enforce_routing_invariants(cfg)


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
