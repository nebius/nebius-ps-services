from __future__ import annotations

import os
import shlex
import socket
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.deploy.vm_manager import VMManager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable systemd fixture",
    ),
]


@pytest.fixture
def stopped_ssh():
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    subprocess.run(
        ["systemctl", "disable", "--now", "ssh.socket", "ssh.service"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    runtime = Path("/run/sshd")
    if runtime.exists():
        runtime.rmdir()
    config = Path("/etc/ssh/sshd_config")
    original = config.read_bytes()
    try:
        yield config
    finally:
        subprocess.run(["systemctl", "stop", "ssh.socket", "ssh.service"], check=True, timeout=30)
        config.write_bytes(original)


@pytest.mark.parametrize("unit", ["ssh.socket", "ssh.service"])
@pytest.mark.parametrize("invalid_config", [False, True], ids=["valid", "invalid"])
def test_missing_runtime_directory_does_not_abort_ssh_bootstrap(stopped_ssh, unit, invalid_config):
    subprocess.run(["systemctl", "enable", unit], check=True, timeout=30)
    if invalid_config:
        with stopped_ssh.open("a") as config:
            config.write("\nInvalidBootstrapOption yes\n")
    assert not Path("/run/sshd").exists()
    cloud = yaml.safe_load(
        VMManager(project_id="test", region="eu-west1")._build_cloud_init(ssh_key="test")
    )
    commands = cloud["runcmd"]
    ssh_commands = [
        item for item in commands if isinstance(item, list) and "/usr/sbin/sshd -t" in item[-1]
    ]
    assert len(ssh_commands) == 1
    script = commands[0] + "\n" + shlex.join(ssh_commands[0]) + "\nprintf bootstrap-continued"
    result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=30)
    listener = subprocess.run(
        ["ss", "-H", "-lnt", "sport", "=", ":22"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    active = subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=10).returncode == 0
    if invalid_config:
        assert result.returncode != 0
        assert "Bad configuration option: InvalidBootstrapOption" in result.stderr
        assert "bootstrap-continued" not in result.stdout
        assert not listener and not active
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout == "bootstrap-continued"
        directory = Path("/run/sshd").stat()
        assert directory.st_uid == directory.st_gid == 0
        assert stat.S_IMODE(directory.st_mode) == 0o755
        assert listener and active
        with socket.create_connection(("127.0.0.1", 22), timeout=5) as connection:
            assert connection.recv(255).startswith(b"SSH-2.0-OpenSSH_")
