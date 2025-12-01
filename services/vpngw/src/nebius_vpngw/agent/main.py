from __future__ import annotations

import signal
from pathlib import Path

import yaml

from .state_store import StateStore
from .strongswan_renderer import StrongSwanRenderer
from .frr_renderer import FRRRenderer

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
        if not self.state.is_changed(cfg):
            print("[Agent] No changes detected; skipping apply")
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
    signal.pause()


if __name__ == "__main__":
    main()
