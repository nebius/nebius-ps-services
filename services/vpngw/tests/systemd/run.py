"""Run the real-manager regressions in a disposable, isolated Linux container.

Explicit local invocation: python3 tests/systemd/run.py. Requires Docker.
No host bus, network/PID namespace, credentials or writable source mount.
Privileged mode is restricted to this disposable systemd/networkd test fixture.
"""

from __future__ import annotations

import argparse
import subprocess
import time
import uuid
from pathlib import Path


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-bootstrap-only", action="store_true")
    parser.add_argument("--ingress-only", action="store_true")
    parser.add_argument("--modernization-only", action="store_true")
    parser.add_argument("--package-migration-only", action="store_true")
    parser.add_argument("--historical-package-upgrades", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--release-wheels", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--historical-version", help="One manifest version for partial development checks"
    )
    parser.add_argument("--platform", default="linux/amd64")
    args = parser.parse_args()
    if args.historical_package_upgrades:
        if not args.candidate_wheel or not args.report:
            parser.error("historical qualification requires --candidate-wheel and --report")
        if any(
            (
                args.ssh_bootstrap_only,
                args.ingress_only,
                args.modernization_only,
                args.package_migration_only,
            )
        ):
            parser.error("historical qualification is a separate fixture lane")
        from historical_packages import run_historical

        run_historical(args)
        return
    project = Path(__file__).resolve().parents[2]
    tests = ["/workspace/tests/integration/test_ssh_bootstrap.py"]
    if args.package_migration_only:
        tests = ["/workspace/tests/integration/test_runtime_modernization.py"]
    elif args.modernization_only:
        tests = [
            "/workspace/tests/integration/test_runtime_modernization.py",
            "/workspace/tests/integration/test_strongswan_sessions.py",
        ]
    elif args.ingress_only:
        tests.extend(
            [
                "/workspace/tests/integration/test_tunnel_host_packets.py",
                "/workspace/tests/integration/test_ingress_packets.py",
            ]
        )
    elif not args.ssh_bootstrap_only:
        tests.extend(
            [
                "/workspace/tests/integration/test_ordinary_systemd_jobs.py",
                "/workspace/tests/integration/test_ordinary_transport.py",
                "/workspace/tests/integration/test_strongswan_sessions.py",
                "/workspace/tests/integration/test_ha_tunnel_native.py",
                "/workspace/tests/integration/test_tunnel_host_packets.py",
                "/workspace/tests/integration/test_ingress_packets.py",
                "/workspace/tests/integration/test_runtime_modernization.py",
            ]
        )
    name = "vpngw-systemd-test-" + uuid.uuid4().hex[:12]
    image = name + ":fixture"
    created = False
    try:
        run(
            "docker",
            "build",
            "-q",
            "-t",
            image,
            "-f",
            str(project / "tests/systemd/Dockerfile"),
            str(project),
            timeout=600,
        )
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
            "/opt/vpngw-test/bin/python3",
            "-B",
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            *tests,
            # Includes real IKE cycles, HA recovery, eight fenced BGP replays,
            # and four Static activation/crash cases. Product deadlines are unchanged.
            timeout=1200,
        )
    finally:
        if created:
            subprocess.run(["docker", "stop", "--timeout", "10", name], check=False, timeout=20)
            subprocess.run(["docker", "rm", name], check=False, timeout=20)
        subprocess.run(["docker", "image", "rm", image], check=False, timeout=30)


if __name__ == "__main__":
    main()
