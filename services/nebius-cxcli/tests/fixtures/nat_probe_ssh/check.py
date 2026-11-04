"""Run only inside the disposable, network-isolated Ubuntu fixture."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import yaml

from nebius_cxcli import nat_gateway_probe_ssh as transport
from nebius_cxcli.nat_gateway_probe_guest import write_result


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=30, **kwargs)


def main() -> None:
    # Reproduce Linux bind-mount ownership even on Docker Desktop hosts that
    # remap mounted files to root. Only this disposable container copy changes.
    source = Path("/run/probe-input")
    shutil.copytree("/fixture", source)
    for path in (source, *source.iterdir()):
        os.chown(path, 65534, 65534)
    # Linux bind mounts preserve the host UID. Stage trusted fixture input as
    # the container's runtime owner without changing the read-only host mount.
    runtime = Path("/run/probe-ssh")
    runtime.mkdir(mode=0o700)
    for name in ("known_hosts", "client.key", "gateway.key"):
        path = runtime / name
        path.write_bytes((source / name).read_bytes())
        path.chmod(0o600)
        assert path.stat().st_uid == 0 and path.stat().st_mode & 0o777 == 0o600
    data = yaml.safe_load((source / "cloud-init.yaml").read_text())
    schema_input = Path("/run/probe-schema.yaml")
    schema_input.write_text(
        "#cloud-config\n"
        + yaml.safe_dump(
            data | {"ssh_keys": {"ed25519_private": "fixture", "ed25519_public": "fixture"}}
        )
    )
    schema = subprocess.run(
        ["cloud-init", "schema", "--config-file", str(schema_input)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if schema.returncode:
        raise RuntimeError(schema.stdout + schema.stderr)
    # Fixture setup applies the generated account/key inputs. It does not claim
    # to model a full VM boot or a real Internet egress measurement.
    user = data["users"][0]
    for name in ("gateway", user["name"]):
        run("useradd", "--create-home", "--shell", "/bin/sh", name)
        run("passwd", "--lock", name)
        directory = Path("/home") / name / ".ssh"
        directory.mkdir(mode=0o700)
        key = " ".join(user["ssh_authorized_keys"][0].split()[1:])
        (directory / "authorized_keys").write_text(
            (key if name == "gateway" else user["ssh_authorized_keys"][0]) + "\n"
        )
        run("chown", "-R", f"{name}:{name}", str(directory))
        (directory / "authorized_keys").chmod(0o600)
    Path("/etc/sudoers.d/gateway").write_text("gateway ALL=(root) NOPASSWD: ALL\n")
    Path("/etc/sudoers.d/gateway").chmod(0o440)
    Path("/run/sshd").mkdir(exist_ok=True)
    probe_host_key = Path("/etc/ssh/probe_host_key")
    probe_host_key.write_text(data["ssh_keys"]["ed25519_private"])
    probe_host_key.chmod(0o600)
    common = (
        "Port 22\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
        "PermitRootLogin no\nUsePAM yes\nPidFile none\n"
    )
    gateway_config = Path("/etc/ssh/sshd_config")
    gateway_config.write_text(
        common
        + f"ListenAddress 127.0.0.1\nAllowTcpForwarding yes\nHostKey {runtime / 'gateway.key'}\n"
    )
    probe_config = Path("/etc/ssh/probe_config")
    probe_config.write_text(
        common + "ListenAddress 127.0.0.2\nAllowTcpForwarding no\nHostKey /etc/ssh/probe_host_key\n"
    )
    servers = [
        subprocess.Popen(
            ["/usr/sbin/sshd", "-D", "-e", "-f", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for path in (gateway_config, probe_config)
    ]
    try:
        probe = json.loads((source / "probe.json").read_text())
        result = json.loads((source / "result.json").read_text())
        write_result(result)
        target = Path(transport.RESULT_PATH)
        assert target.stat().st_uid == 0 and target.stat().st_mode & 0o777 == 0o644
        assert (
            subprocess.run(
                ["sudo", "-u", user["name"], "test", "-w", str(target)], timeout=10
            ).returncode
            == 1
        )
        assert (
            subprocess.run(
                ["sudo", "-u", user["name"], "test", "-w", str(target.parent)], timeout=10
            ).returncode
            == 1
        )
        # Both remote servers, the exact identity selection and the supervisor are real.
        with transport.ProbeSSH(
            {"public_ip": "127.0.0.1"},
            {"ssh_user_name": "gateway", "ssh_public_key": key},
            known_hosts=runtime / "known_hosts",
            private_key=runtime / "client.key",
        ) as access:
            for _ in range(30):
                try:
                    access.preflight(assert_held=lambda: None)
                    break
                except RuntimeError:
                    if any(server.poll() is not None for server in servers):
                        raise RuntimeError("Fixture SSH server exited") from None
                    time.sleep(0.1)
            else:
                raise RuntimeError("Fixture gateway did not become ready")
            resources = NS(private_address=lambda **kwargs: "127.0.0.2")
            observed = access.wait(resources, probe, save=lambda: None, assert_held=lambda: None)
            assert observed["ok"] is True
            transport.RESULT_TIMEOUT = 1
            probe["host_public_key"] = (source / "wrong.pub").read_text().strip()
            try:
                access.wait(resources, probe, save=lambda: None, assert_held=lambda: None)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Wrong probe host key was accepted")
        print(
            "PASS: two authenticated SSH hops, locked user, immutable result, host mismatch rejection"
        )
    finally:
        for server in servers:
            server.terminate()
        for server in servers:
            server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
