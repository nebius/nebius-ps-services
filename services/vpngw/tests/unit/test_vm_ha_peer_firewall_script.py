from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = (
    Path(__file__).parents[2]
    / "src/nebius_vpngw/systemd/nebius-vpngw-vm-ha-peer-firewall.sh"
)


def _config() -> dict:
    return {
        "vm_ha": {
            "node": {"node_id": "node-a"},
            "runtime_binding": {
                "nodes": [
                    {
                        "node_id": "node-a",
                        "network_interface_name": "eth0",
                        "peer_endpoint": "172.16.30.6:9443",
                    },
                    {
                        "node_id": "node-b",
                        "network_interface_name": "eth0",
                        "peer_endpoint": "172.16.30.2:9443",
                    },
                ]
            },
        }
    }


def _run(tmp_path: Path, payload: dict) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    commands = tmp_path / "commands"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ufw = bin_dir / "ufw"
    ufw.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$VPNGW_UFW_LOG"\n')
    ufw.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "VPNGW_UFW_LOG": str(commands),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), str(config)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, commands.read_text(encoding="utf-8").splitlines() if commands.exists() else []


def test_exact_private_peer_rule_is_idempotent(tmp_path: Path) -> None:
    first, first_commands = _run(tmp_path, _config())
    second, second_commands = _run(tmp_path, _config())

    assert first.returncode == second.returncode == 0
    expected = (
        "allow in on eth0 proto tcp from 172.16.30.2 to 172.16.30.6 "
        "port 9443 comment VM-HA peer mTLS"
    )
    assert first_commands == [expected]
    assert second_commands == [expected, expected]


def test_ordinary_config_is_a_noop(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, {"version": 1})

    assert result.returncode == 0
    assert commands == []


def test_foreign_local_node_fails_before_firewall_mutation(tmp_path: Path) -> None:
    payload = _config()
    payload["vm_ha"]["node"]["node_id"] = "foreign"

    result, commands = _run(tmp_path, payload)

    assert result.returncode != 0
    assert commands == []
