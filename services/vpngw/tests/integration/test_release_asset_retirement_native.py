"""Real systemd state for a deleted, still-loaded obsolete release unit."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from nebius_vpngw import runtime_migration

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable systemd fixture",
    ),
]


def test_missing_loaded_unit_is_stopped_and_disabled():
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    unit = "nebius-vpngw-retirement-regression.service"
    path = Path("/etc/systemd/system") / unit
    enabled = path.parent / "multi-user.target.wants" / unit
    assert not path.exists() and not enabled.is_symlink()
    raw = b"[Unit]\nDescription=Retirement regression\n[Service]\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n"

    def systemctl(*args):
        return subprocess.run(
            ["systemctl", *args], check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()

    try:
        path.write_bytes(raw)
        path.chmod(0o644)
        systemctl("daemon-reload")
        systemctl("enable", "--now", unit)
        path.unlink()
        assert systemctl("show", unit, "--property=ActiveState", "--value") == "active"
        plan = {
            "predecessor": {
                "operation": "a" * 32,
                "wheel_sha256": "b" * 64,
                "assets_sha256": "c" * 64,
            },
            "removals": {
                str(path): {
                    "member": "nebius_vpngw/systemd/" + unit,
                    "mode": 0o644,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            },
        }
        runtime_migration.retire_owned_assets(plan)
        assert systemctl("show", unit, "--property=ActiveState", "--value") == "inactive"
        assert not enabled.is_symlink() and not path.exists()
        runtime_migration.retire_owned_assets(plan)
    finally:
        subprocess.run(["systemctl", "stop", unit], check=False, capture_output=True, timeout=15)
        enabled.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        systemctl("daemon-reload")
