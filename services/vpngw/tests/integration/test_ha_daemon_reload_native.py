"""Real VICI/kernel reload recovery; cloud and manager admission are fixture inputs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest
from test_ha_tunnel_native import drive, identities, run, wait_until
from test_ha_tunnel_native import native_paths as native_paths

from nebius_vpngw.agent.vm_ha.tunnel_observation import xfrm_records

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit isolated systemd fixture",
    ),
]


@pytest.mark.parametrize("native_paths", ["static", "bgp"], indirect=True)
def test_component_reload_removes_forgotten_kernel_sas_before_native_initiation(native_paths):
    gateway = native_paths[0][0]

    def states():
        return {
            (value["src"], value["dst"], value["proto"], value["spi"])
            for value in xfrm_records(
                gateway.command("ip", "xfrm", "state", "list", "nokeys").stdout, policy=False
            )
        }

    before = states()
    assert before
    old = gateway.process
    anchor = subprocess.Popen(
        ["nsenter", "-t", str(old.pid), "-m", "-n", "--", "sleep", "120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until(
            lambda: (
                Path(f"/proc/{anchor.pid}/ns/mnt").stat().st_ino
                == Path(f"/proc/{old.pid}/ns/mnt").stat().st_ino
            )
        )
        os.kill(old.pid, signal.SIGKILL)
        old.wait(timeout=5)
        with gateway.log.open("a") as log:
            gateway.process = subprocess.Popen(
                ["nsenter", "-t", str(anchor.pid), "-m", "-n", "--", "/usr/sbin/charon-systemd"],
                env={**os.environ, "STRONGSWAN_CONF": str(gateway.config.parent / "charon-0.conf")},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        wait_until(
            lambda: (
                run(
                    "nsenter",
                    "-t",
                    str(gateway.process.pid),
                    "-m",
                    "-n",
                    "--",
                    "swanctl",
                    "--stats",
                    check=False,
                ).returncode
                == 0
            )
        )
        assert identities(gateway) == {}
        assert before.issubset(states()), "fixture must preserve the original kernel residue"
        daemon_pid = gateway.process.pid
        result = json.loads(drive(gateway, "reload-after-daemon-crash"))
        assert result["reloaded"] and result["seconds"] < 4.0
        assert before.isdisjoint(states()), "reload retained the previous daemon's kernel SAs"
        wait_until(lambda: bool(identities(gateway)))
        assert gateway.process.pid == daemon_pid and gateway.process.poll() is None
    finally:
        if anchor.poll() is None:
            anchor.terminate()
            anchor.wait(timeout=5)
