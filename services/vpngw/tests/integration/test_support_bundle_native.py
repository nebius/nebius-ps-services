"""Installed public CLI over real SSH/systemd; no cloud credentials or collector mocks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import venv
import zipfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable systemd fixture",
    ),
]


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=120, **kwargs)


@pytest.fixture(scope="module")
def native():
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    assert Path("/.dockerenv").exists() and os.geteuid() == 0
    root = Path(tempfile.mkdtemp(prefix="support-native-", dir="/var/lib"))
    os.chmod(root, 0o700)
    (root / "tmp").mkdir(mode=0o700)
    home = root / "operator-home"
    home.mkdir(mode=0o700)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("NEBIUS_", "VPNGW_", "PYTHON"))
    }
    env.update(HOME=str(home), TMPDIR=str(root / "tmp"), TERM="dumb", NO_COLOR="1", COLUMNS="160")
    wheel = next(Path("/support-candidate").glob("*.whl"))
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(root / "operator")
    python = root / "operator/bin/python"
    run([str(python), "-m", "pip", "install", str(wheel)], env=env)
    run([str(python), "-m", "pip", "install", "pyinstaller>=6.17,<7"], env=env)
    env["VPNGW_AGENT_WHEEL"] = str(wheel)
    built = subprocess.run(
        [str(python), "-m", "nebius_vpngw.build"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert built.returncode == 0, built.stderr[-4000:]
    binary = root / "dist/nebius-vpngw"
    assert binary.is_file()
    client_key = root / "client"
    host_key = root / "host"
    for key in (client_key, host_key):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    authorized = root / "authorized_keys"
    shutil.copyfile(client_key.with_suffix(".pub"), authorized)
    authorized.chmod(0o600)
    pins = root / "known_hosts"
    pins.write_text("127.0.0.1 " + host_key.with_suffix(".pub").read_text())
    pins.chmod(0o600)
    env["VPNGW_SSH_KNOWN_HOSTS_FILE"] = str(pins)
    config = root / "gateway.json"
    config.write_text(
        json.dumps(
            {
                "project_id": "project-fixture",
                "tenant_id": "tenant-fixture",
                "gateway_group": {
                    "name": "fixture",
                    "instance_count": 1,
                    "region": "eu-west1",
                    "external_ips": [["127.0.0.1"]],
                    "vm_spec": {
                        "ssh_username": "root",
                        "ssh_public_key_path": str(client_key.with_suffix(".pub")),
                        "ssh_private_key_path": str(client_key),
                    },
                },
            }
        )
    )
    config.chmod(0o600)
    ssh_config = root / "sshd_config"
    ssh_config.write_text(
        f"Port 22\nListenAddress 127.0.0.1\nHostKey {host_key}\nAuthorizedKeysFile {authorized}\nPermitRootLogin yes\nPasswordAuthentication no\nUsePAM no\nPidFile {root / 'sshd.pid'}\n"
    )
    run(["systemctl", "stop", "ssh.socket", "ssh.service"])
    Path("/run/sshd").mkdir(exist_ok=True)
    sshd = subprocess.Popen(
        ["/usr/sbin/sshd", "-D", "-e", "-f", str(ssh_config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", 22), timeout=1):
                    break
            except OSError:
                assert time.monotonic() < until
                time.sleep(0.1)
        # Harness-owned evidence and service fixture, frozen before each trial.
        script = root / "agent-fixture.sh"
        script.write_text(
            '#!/bin/sh\necho "ordinary-fixture-diagnostic"\necho "password=credential-canary-value"\nexec /bin/sleep infinity\n'
        )
        script.chmod(0o700)
        unit = Path("/etc/systemd/system/nebius-vpngw-agent.service")
        unit.write_text(f"[Service]\nExecStart={script}\n")
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", "nebius-vpngw-agent"])
        run(["journalctl", "--sync"])
        Path("/var/log/vpngw-fix-routes.log").write_text(
            'route-log-fixture\npsk="route-secret-canary"\n'
        )
        yield root, config, env, python, binary, pins
    finally:
        sshd.terminate()
        sshd.wait(timeout=10)
        run(["systemctl", "stop", "nebius-vpngw-agent"])


@pytest.mark.parametrize("artifact", ["wheel", "binary"])
@pytest.mark.parametrize("broken_agent", [False, True], ids=["no-agent", "broken-agent"])
def test_installed_collector_collects_live_guest_evidence(native, artifact, broken_agent):
    root, config, env, python, binary, pins = native
    # The guest /usr/bin/python3 deliberately has no gateway package. In the
    # second trial an import failure is installed only as harness precondition.
    guest = Path("/usr/local/lib/python3.12/dist-packages/nebius_vpngw")
    if broken_agent:
        guest.mkdir(exist_ok=True)
        (guest / "__init__.py").write_text('raise RuntimeError("synthetic broken guest agent")\n')
    elif guest.exists():
        shutil.rmtree(guest)
    before = {path: path.read_bytes() for path in (config, pins)}
    before_service = run(
        ["systemctl", "show", "nebius-vpngw-agent", "--property=InvocationID,MainPID"]
    ).stdout
    command = [str(python), "-m", "nebius_vpngw"] if artifact == "wheel" else [str(binary)]
    output = root / f"{artifact}-{broken_agent}.zip"
    result = subprocess.run(
        [*command, "support-bundle", "-c", str(config), "--output", str(output)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=310,
    )
    assert result.returncode == 1, result.stdout[-1500:] + result.stderr[-1500:]
    assert "Guest evidence collected from 1/1 gateways." in result.stdout, result.stdout
    assert "Gateway 0: connect started." in result.stdout
    assert before == {path: path.read_bytes() for path in before}
    assert (
        before_service
        == run(
            ["systemctl", "show", "nebius-vpngw-agent", "--property=InvocationID,MainPID"]
        ).stdout
    )
    with zipfile.ZipFile(output) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
        all_bytes = b"\n".join(files.values())
        assert b"ordinary-fixture-diagnostic" in files["nodes/0/logs/nebius-vpngw-agent.stdout.txt"]
        assert b"credential-canary-value" not in all_bytes
        assert b"route-secret-canary" not in all_bytes
        assert b"route-log-fixture" in files["nodes/0/file-logs/route-fix.stdout.txt"]
        assert b"active (running)" in files["nodes/0/service-status/nebius-vpngw-agent.stdout.txt"]
        assert files["nodes/0/host/uptime.stdout.txt"]
        assert files["nodes/0/host/free.stdout.txt"]
        assert files["nodes/0/host/vmstat.stdout.txt"]
        assert files["operator/status.txt"]
        assert files["nodes/0/ha-0/native.stderr.txt"]
        manifest = json.loads(files["manifest.json"])
        assert manifest["schema"].endswith("v4") and manifest["guest_nodes"] == [0]
        for row in manifest["probes"]:
            if row["sha256"]:
                assert row["sha256"] == hashlib.sha256(files[row["file"]]).hexdigest()
