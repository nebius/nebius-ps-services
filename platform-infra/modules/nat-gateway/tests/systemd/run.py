"""Exercise rendered NAT bootstrap in a disposable Ubuntu systemd container."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kwargs)


def main() -> None:
    module = Path(__file__).resolve().parents[2]
    name = "cxcli-nat-security-" + uuid.uuid4().hex[:12]
    image = name + ":fixture"
    launch_attempted = False
    with tempfile.TemporaryDirectory(prefix="nat-systemd-render-") as directory:
        try:
            run("docker", "build", "-q", "-t", image, str(module / "tests/systemd"), timeout=600)
            launch_attempted = True
            run(
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--privileged",
                "--cgroupns=private",
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/run/lock",
                "--tmpfs",
                "/tmp",
                "--mount",
                f"type=bind,src={module},dst=/source,readonly",
                "--mount",
                f"type=bind,src={directory},dst=/fixture,readonly",
                "--mount",
                f"type=bind,src={module.parents[2] / 'services/nebius-cxcli/src/nebius_cxcli/nat_gateway_ssh.py'},dst=/identity.py,readonly",
                image,
                timeout=30,
            )
            deadline = time.monotonic() + 60
            while subprocess.run(
                ["docker", "exec", name, "busctl", "--system", "list"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).returncode:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated systemd did not start")
                time.sleep(0.2)
            # Only disposable fixture keys are generated; private values never leave the container.
            run(
                "docker",
                "exec",
                name,
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                "/tmp/client-key",
                timeout=20,
            )
            key = run(
                "docker",
                "exec",
                name,
                "cat",
                "/tmp/client-key.pub",
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            config = {
                "source_cidrs": ["10.0.0.0/8"],
                "security_profile": "key-only-fail2ban-v1",
                "ssh_user_name": "natadmin",
                "ssh_public_key": key,
                "guest_program_sha256": hashlib.sha256(
                    (module / "nat_guest.py").read_bytes().strip()
                ).hexdigest(),
                "security_program_sha256": hashlib.sha256(
                    (module / "nat_security.py").read_bytes().strip()
                ).hexdigest(),
            }
            params = {
                "ssh_host_keys": {},
                "ssh_user_name": "natadmin",
                "ssh_public_key": key,
                "guest_config": json.dumps(config, separators=(",", ":"), sort_keys=True),
                "guest_program": (module / "nat_guest.py").read_text(),
                "security_program": (module / "nat_security.py").read_text(),
            }
            expression = f"jsonencode(yamldecode(templatefile({json.dumps(str(module / 'cloud-init.tftpl'))}, {json.dumps(params)})))\n"
            rendered = run(
                "terraform",
                "console",
                input=expression,
                capture_output=True,
                text=True,
                cwd=directory,
                timeout=30,
            )
            (Path(directory) / "cloud.json").write_text(json.loads(rendered.stdout))
            run(
                "docker",
                "exec",
                "-e",
                "NAT_ISOLATED_SYSTEMD_TEST=1",
                name,
                "python3",
                "-B",
                "-m",
                "pytest",
                "-q",
                "-x",
                "-p",
                "no:cacheprovider",
                "/source/tests/systemd/test_security.py",
                timeout=300,
            )
        finally:
            primary_failure = sys.exc_info()[0] is not None
            commands = []
            if launch_attempted:
                commands.extend(
                    [
                        ["docker", "stop", "--timeout", "10", name],
                        ["docker", "rm", "--force", name],
                    ]
                )
            commands.append(["docker", "image", "rm", image])
            failures = []
            for args in commands:
                try:
                    result = subprocess.run(args, check=False, capture_output=True, timeout=30)
                    if result.returncode:
                        failures.append(" ".join(args[:3]))
                except (OSError, subprocess.TimeoutExpired):
                    failures.append(" ".join(args[:3]))
            if failures:
                message = f"NAT fixture cleanup commands failed for {name}: {', '.join(failures)}"
                print(message, file=sys.stderr)
                if not primary_failure:
                    raise RuntimeError(message)


if __name__ == "__main__":
    main()
