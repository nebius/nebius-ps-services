"""Execute generated FRR shell with inert commands, never the host bootstrap."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.mark.parametrize("vm_ha", [False, True])
@pytest.mark.parametrize("comment", ['operator: "primary"', "secondary # literal key comment"])
def test_cloud_init_preserves_authorized_key_as_a_yaml_string(vm_ha: bool, comment: str) -> None:
    key = f"ssh-ed25519 AAAAfirst {comment}"
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(
            ssh_key=key,
            vm_ha=vm_ha,
        )
    )
    assert config["users"] == [{"name": "ubuntu", "ssh_authorized_keys": [key]}]


@pytest.mark.parametrize("failure", ["", "curl", "lsb_release", "apt-get", "install"])
def test_generated_frr_shell_stops_on_required_failure(tmp_path: Path, failure: str) -> None:
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(ssh_key="test")
    )
    files = {item["path"]: item for item in config["write_files"]}
    assert "/etc/frr/daemons" not in files
    assert files["/etc/nebius-vpngw/frr-daemons.bootstrap"]["owner"] == "root:root"
    commands = config["runcmd"]
    assert commands[0] == "set -eu"
    start = next(
        i for i, item in enumerate(commands) if isinstance(item, list) and item[0] == "curl"
    )
    end = next(
        i for i, item in enumerate(commands) if isinstance(item, list) and item[0] == "install"
    )
    assert commands[end][1:7] == ["-o", "frr", "-g", "frr", "-m", "0644"]
    assert commands.index(["systemctl", "mask", "frr.service"]) < start
    assert commands.index(["systemctl", "unmask", "frr.service"]) > end
    # Match cloud-init util.shellify: strings are shell, lists are individually
    # quoted argv. Only execute the FRR block; redirect all paths into tmp_path.
    shell = "#!/bin/sh\nset -eu\n" + "\n".join(
        shlex.join(item) for item in commands[start : end + 1]
    )
    shell = shell.replace("/etc/", str(tmp_path / "etc") + "/")
    shell += '\nprintf complete >> "$TRACE"\n'
    (tmp_path / "etc/apt/sources.list.d").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("curl", "lsb_release", "apt-get", "install"):
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\nprintf "%s\\n" {name} >> "$TRACE"\n'
            f'[ "$FAIL" != {name} ] || exit 42\n'
            + ("echo noble\n" if name == "lsb_release" else "exit 0\n")
        )
        stub.chmod(0o700)
    trace = tmp_path / "trace"
    result = subprocess.run(
        ["/bin/sh", "-c", shell],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAIL": failure,
            "TRACE": str(trace),
        },
    )
    calls = trace.read_text().splitlines()
    assert (result.returncode == 0) is (failure == "")
    assert ("complete" in calls) is (failure == "")
    if failure:
        assert calls[-1] == failure


def test_required_firewall_is_not_masked_and_esp4_deferral_is_preserved() -> None:
    config = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(ssh_key="test")
    )
    commands = config["runcmd"]
    firewall = next(item[-1] for item in commands if "setup-vpngw-firewall.sh >" in str(item))
    assert "|| true" not in firewall
    assert any("rc -eq 75" in str(item) for item in commands)
    assert any("augenrules --load || true" in str(item) for item in commands)


def test_firewall_uses_xfrm_name_without_parent_suffix() -> None:
    path = Path(__file__).parents[2] / "src/nebius_vpngw/systemd/setup-vpngw-firewall.sh"
    source = path.read_text()
    start = source.index("for xfrm_if in ")
    loop = source[start : source.index("\ndone", start) + len("\ndone")]
    script = (
        """
ip() { printf 'link fixture\\n'; }
grep() { cat >/dev/null; printf 'xfrm0@eth0\\nxfrm1@eth0\\n'; }
ufw() { printf '%s\\n' "$*"; }
logger() { :; }
"""
        + loop
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [
        "allow in on xfrm0",
        "allow out on xfrm0",
        "allow in on xfrm1",
        "allow out on xfrm1",
    ]
