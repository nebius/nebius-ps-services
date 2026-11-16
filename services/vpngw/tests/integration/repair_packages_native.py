"""Explicit disposable Ubuntu package-boundary trial; no HA/runtime claim.

Run only inside an owned disposable container with VPNGW_REPAIR_PACKAGE_TEST=1.
Real dpkg and pip execute the production repair package owners. The guard and
cloud orchestration are outside this trial and require a real VM qualification.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from package_recovery_native import command, wheel

from nebius_vpngw import python_package_recovery, repair_runtime, runtime_migration
from nebius_vpngw.deploy.repair_remote import native_exclusion, python_command


def main() -> None:
    if os.environ.get("VPNGW_REPAIR_PACKAGE_TEST") != "1" or not Path("/.dockerenv").exists():
        raise RuntimeError("This destructive fixture requires its disposable container")
    with tempfile.TemporaryDirectory(prefix="repair-native-", dir="/root") as temporary:
        root = Path(temporary)
        plan = runtime_migration.plan_current(reinstall=True)
        staged = runtime_migration.stage(plan, root=root / "native")
        inventory = repair_runtime.archive_inventory(plan, staged, command)
        with native_exclusion():
            repair_runtime.install(plan, staged, inventory, command=command, check=lambda: None)
        binary = Path("/usr/sbin/swanctl")
        original = binary.read_bytes()
        binary.write_bytes(b"damaged-package-payload\n")
        conffile = next(Path(name) for name in inventory["conffiles"] if Path(name).is_file())
        conffile.unlink()
        with native_exclusion():
            repair_runtime.install(plan, staged, inventory, command=command, check=lambda: None)
        assert binary.read_bytes() == original
        assert conffile.is_file()
        repair_runtime.verify_inventory(inventory)
        print(
            "PASS: complete native repair closure and same-version damaged/missing file reconstruction",
            flush=True,
        )

        package = wheel(root, "1.0")
        python_command(
            [
                sys.executable,
                "-B",
                "-m",
                "pip",
                "install",
                "--break-system-packages",
                "--no-deps",
                str(package),
            ]
        )
        distribution = importlib.metadata.distribution("vpngw-recovery-fixture")
        metadata = Path(distribution._path)
        payload = Path(distribution.locate_file("vpngw_recovery_fixture/__init__.py"))
        payload.write_bytes(b"raise RuntimeError('damaged')\n")
        (metadata / "RECORD").unlink()
        checkpoint = root / "python-repair"
        python_package_recovery.prepare(checkpoint, package, python_command, reconstruct=True)
        python_package_recovery.install(checkpoint, package, python_command, lambda: None)
        assert python_package_recovery.complete(
            checkpoint, python_package_recovery.load(checkpoint, package)
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        output = subprocess.check_output(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import vpngw_recovery_fixture as m;print(m.VERSION)",
            ],
            env=environment,
            text=True,
        )
        assert output.strip() == "1.0"
        print(
            "PASS: missing Python RECORD and broken installed import repaired from the same-version wheel",
            flush=True,
        )
        print(
            json.dumps(
                {"native_packages": plan["install"], "python": sys.version.split()[0]},
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
