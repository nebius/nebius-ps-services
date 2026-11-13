"""Continuously run the product selector inside an existing disposable namespace.

Only cloud authority is a fixture input. Native effects, observation, rendering,
SA negotiation and routes use the production implementation and real kernel.
Unlike the one-shot manual driver, this process retains observations across ticks.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

import yaml

from nebius_vpngw.agent.strongswan_renderer import SWANCTL_CONF
from nebius_vpngw.agent.vm_ha.tunnel_observation import parse_sas
from nebius_vpngw.agent.vm_ha.tunnel_runtime import NativeTunnelEffects, _run_command
from nebius_vpngw.agent.vm_ha.tunnels import Authority, TunnelController
from nebius_vpngw.ipsec_observation import command as ipsec_command


def loaded_settings(text: str) -> dict:
    """Keep recovery settings only, never the generated credential section."""
    result = {}
    connection = None
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.endswith(" {"):
            connection = line.strip().split()[0]
        if connection is None or "=" not in line:
            continue
        key, value = line.strip().split("=", 1)
        if key.strip() in {"start_action", "keyingtries", "dpd_action", "close_action"}:
            result.setdefault(connection, {})[key.strip()] = value.strip()
    return result


def snapshot(selector: TunnelController, effects: NativeTunnelEffects, started: float) -> dict:
    sas = {}
    for name, parent in parse_sas(effects.run(ipsec_command())):
        children = []
        for child in parent.get("child-sas", {}).values():
            children.append(
                {
                    key: child.get(key)
                    for key in (
                        "uniqueid",
                        "state",
                        "reqid",
                        "spi-in",
                        "spi-out",
                        "if-id-in",
                        "if-id-out",
                    )
                }
            )
        sas.setdefault(name, []).append(
            {"id": parent["uniqueid"], "state": parent["state"], "children": children}
        )
    operation = selector.state["operation"]
    return {
        "at": time.time(),
        "monotonic": time.monotonic(),
        "tick_seconds": time.monotonic() - started,
        "selected": selector.state["selected"].get("site", "preferred"),
        "health": selector.state["health"],
        "preferred_qualification": selector.preferred_observations,
        "operation": operation,
        "sas": sas,
        "settings": loaded_settings(SWANCTL_CONF.read_text()),
        "routes": json.loads(effects.run(("ip", "-j", "-4", "route", "show"))),
        "policies": json.loads(effects.run(("ip", "-j", "xfrm", "policy"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=720)
    args = parser.parse_args()
    if os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1":
        raise RuntimeError("Only the explicitly enabled disposable fixture is supported")
    cfg = yaml.safe_load(args.config.read_text())
    authority = Authority("native-fixture", "owner", "allocation", "1", 1, "a" * 64)
    state_dir = Path("/var/lib/nebius-vpngw/vm-ha")
    state_dir.mkdir(parents=True, exist_ok=True)
    calls = args.output.with_suffix(".commands.jsonl").open("x")

    def native_command(argv, timeout):
        # Record intent, not credentials/native output. Execute the unchanged runner.
        started = time.monotonic()
        if argv[0] == "swanctl" and "--initiate" in argv:
            target = argv[argv.index("--child") + 1]
            before = _run_command(ipsec_command(), 3)
            if before.returncode != 0:
                raise RuntimeError("Cannot establish pre-initiation fixture evidence")
            pending = False
            for name, parent in parse_sas(before.stdout):
                if name == target and parent["state"] == "CONNECTING":
                    pending = True
            calls.write(
                json.dumps(
                    {
                        "at": time.time(),
                        "argv": list(argv),
                        "phase": "intent",
                        "preexisting_connecting": pending,
                    }
                )
                + "\n"
            )
            calls.flush()
        result = _run_command(argv, timeout)
        if argv[0] == "swanctl":
            calls.write(
                json.dumps(
                    {
                        "at": time.time(),
                        "argv": list(argv),
                        "returncode": result.returncode,
                        "elapsed_seconds": time.monotonic() - started,
                        "phase": "returned",
                    }
                )
                + "\n"
            )
            calls.flush()
        return result

    effects = NativeTunnelEffects(
        cfg,
        state_dir,
        lambda observed: observed == authority,
        active_revalidate=lambda observed: observed == authority,
        command_runner=native_command,
    )
    selector = TunnelController(
        cfg,
        state_dir / "tunnel-selection.json",
        effects,
        preferred_probe=effects.probe_preferred,
        preferred_observations={},
        backup_probe=effects.probe_backup,
        static_recovery_observations={},
    )
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + args.seconds
    with args.output.open("x") as output:
        while running and time.monotonic() < deadline:
            started = time.monotonic()
            selector.tick(authority, time.time())
            output.write(json.dumps(snapshot(selector, effects, started)) + "\n")
            output.flush()
            time.sleep(0.25)
    calls.close()
    if running:
        raise RuntimeError("Scenario exceeded its finite controller lifetime")


if __name__ == "__main__":
    main()
