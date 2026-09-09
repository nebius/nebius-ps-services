"""Run the real-manager regressions in a disposable, isolated Linux container.

Explicit local invocation: python3 tests/systemd/run.py. Requires Docker.
No host bus, network/PID namespace, credentials or writable source mount.
Privileged mode is restricted to this disposable systemd/networkd test fixture.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=True, **kwargs)


def main() -> None:
    project = Path(__file__).resolve().parents[2]
    name = "vpngw-systemd-test-" + uuid.uuid4().hex[:12]
    image = name + ":fixture"
    created = False
    try:
        run("docker", "build", "-q", "-t", image, str(project / "tests/systemd"), timeout=600)
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "nebius-vpngw.test=ordinary-operation",
            "--privileged",
            "--cgroupns=private",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            "--tmpfs",
            "/tmp",
            "--mount",
            f"type=bind,src={project / 'src'},dst=/workspace/src,readonly",
            "--mount",
            f"type=bind,src={project / 'tests'},dst=/workspace/tests,readonly",
            "--mount",
            f"type=bind,src={project / 'pyproject.toml'},dst=/workspace/pyproject.toml,readonly",
            image,
            timeout=30,
        )
        created = True
        deadline = time.monotonic() + 60
        while True:
            result = subprocess.run(
                ["docker", "exec", name, "busctl", "--system", "list"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("isolated systemd did not become ready")
            time.sleep(0.2)
        run(
            "docker",
            "exec",
            "-e",
            "VPNGW_ISOLATED_SYSTEMD_TEST=1",
            "-e",
            "PYTHONPATH=/workspace/src",
            name,
            "python3",
            "-B",
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            "/workspace/tests/integration/test_ordinary_systemd_jobs.py",
            "/workspace/tests/integration/test_ordinary_transport.py",
            timeout=300,
        )
    finally:
        if created:
            subprocess.run(["docker", "stop", "--timeout", "10", name], check=False, timeout=20)
            subprocess.run(["docker", "rm", name], check=False, timeout=20)
        subprocess.run(["docker", "image", "rm", image], check=False, timeout=30)


if __name__ == "__main__":
    main()
