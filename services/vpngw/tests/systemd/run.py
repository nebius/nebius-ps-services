"""Run the real-manager regressions in a disposable, isolated Linux container.

Explicit local invocation: python3 tests/systemd/run.py. Requires Docker.
No host bus, network/PID namespace, credentials or writable source mount.
Privileged mode is restricted to this disposable systemd/networkd test fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from qualification import native_files


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=True, **kwargs)


def main() -> None:
    os.umask(0o077)
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
    parser.add_argument("--topology", choices=("ha", "ordinary", "all"), default="all")
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
        tests = ["/workspace/tests/integration/" + name for name in native_files(args.topology)]
    name = "vpngw-systemd-test-" + uuid.uuid4().hex[:12]
    image = name + ":fixture"
    created = False
    image_id = None
    evidence = args.report.resolve() if args.report else Path.cwd() / (name + ".json")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    if evidence.exists() or evidence.with_suffix(".log").exists():
        parser.error("native evidence destination already exists; preserve the earlier trial")
    with evidence.open("x") as receipt:
        json.dump({"topology": args.topology, "status": "preparing"}, receipt)
    try:
        run(
            "docker",
            "build",
            "--platform",
            args.platform,
            "--label",
            "nebius-vpngw.owner=" + image,
            "-q",
            "-t",
            image,
            "-f",
            str(project / "tests/systemd/Dockerfile"),
            str(project),
            timeout=600,
        )
        identity = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{json .}}", image],
                text=True,
                timeout=30,
            )
        )
        if identity["Os"] + "/" + identity["Architecture"] != args.platform:
            raise RuntimeError("Native fixture platform differs from the declared platform")
        image_id = identity["Id"]
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--platform",
            args.platform,
            "--label",
            "nebius-vpngw.test=native-qualification",
            "--label",
            "nebius-vpngw.owner=" + image,
            "--privileged",
            "--cgroupns=private",
            "--memory",
            "3g",
            "--memory-swap",
            "3g",
            "--cpus",
            "2",
            "--pids-limit",
            "1024",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=2",
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
            image_id,
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
        command = [
            "docker",
            "exec",
            "-e",
            "VPNGW_ISOLATED_SYSTEMD_TEST=1",
            "-e",
            "PYTHONPATH=/workspace/src:/workspace/tests/systemd",
            "-e",
            "VPNGW_NATIVE_REPORT=/tmp/qualification.json",
            "-e",
            "VPNGW_NATIVE_TOPOLOGY=" + args.topology,
            name,
            "/opt/vpngw-test/bin/python3",
            "-B",
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            "-p",
            "qualification_plugin",
            *tests,
        ]
        with evidence.with_suffix(".collection.log").open("x") as log:
            run(*command, "--collect-only", stdout=log, stderr=subprocess.STDOUT, timeout=60)
        declared = json.loads(
            subprocess.check_output(
                ["docker", "exec", name, "cat", "/tmp/qualification.json"], text=True, timeout=10
            )
        )["expected_nodeids"]
        if not declared:
            raise RuntimeError("Native fixture collected no declared cases")
        dependencies = {}
        for key, argv in (
            ("native", ["dpkg-query", "-W"]),
            ("python", ["/opt/vpngw-test/bin/python3", "-m", "pip", "freeze", "--all"]),
        ):
            dependencies[key] = subprocess.check_output(
                ["docker", "exec", name, *argv], text=True, timeout=30
            )
        with evidence.with_suffix(".inputs.json").open("x") as receipt:
            json.dump(
                {
                    "image_id": image_id,
                    "platform": args.platform,
                    "expected_nodeids": declared,
                    "dependencies": dependencies,
                },
                receipt,
                indent=2,
                sort_keys=True,
            )
        with evidence.with_suffix(".log").open("x") as log:
            # Keep the entire diagnostic stream, including subprocess timeouts.
            run(*command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    finally:
        if created:
            captured = subprocess.run(
                ["docker", "exec", name, "cat", "/tmp/qualification.json"],
                check=False,
                timeout=30,
                capture_output=True,
            )
            if captured.returncode == 0:
                # Docker cp cannot read tmpfs/user mounts. Read through the
                # running container namespace before stopping its manager.
                evidence.write_bytes(captured.stdout)
            else:
                evidence.write_text(
                    json.dumps(
                        {
                            "topology": args.topology,
                            "status": "evidence-unavailable",
                            "selected_files": tests,
                        }
                    )
                    + "\n"
                )
        # The create reply may have been lost. Discover the exact invocation
        # label even then; cleanup failure cannot become a successful trial.
        from historical_packages import remove_owned_container

        remove_owned_container(name, image)
        remaining = subprocess.check_output(
            ["docker", "image", "ls", "--filter", "reference=" + image, "--format", "{{.ID}}"],
            text=True,
            timeout=30,
        ).strip()
        if remaining:
            owner = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "nebius-vpngw.owner"}}',
                    image,
                ],
                text=True,
                timeout=30,
            ).strip()
            if owner != image:
                raise RuntimeError("Native fixture image cleanup ownership changed")
            subprocess.run(["docker", "image", "rm", image], check=True, timeout=30)
    observed = json.loads(evidence.read_text())
    if observed.get("status") != "passed" or observed.get("expected_nodeids") != declared:
        raise RuntimeError("Native qualification has missing, skipped or unrun cases")


if __name__ == "__main__":
    main()
