"""Install an authentic source wheel using its own dependency-install policy."""

import ast
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1])
with zipfile.ZipFile(wheel) as archive:
    module = "nebius_vpngw/deploy/ordinary_remote.py"
    source = archive.read(module).decode() if module in archive.namelist() else ""
    scripts = [
        ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", "") == "DEPENDENCY_INSTALL_SCRIPT" for target in node.targets)
    ]
if scripts:
    assert len(scripts) == 1
    with tempfile.TemporaryDirectory(prefix="predecessor-dependencies-") as temporary:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--dest",
                temporary,
                str(wheel),
            ],
            check=True,
        )
        dependencies = [
            str(path) for path in sorted(Path(temporary).glob("*.whl")) if path.name != wheel.name
        ]
        subprocess.run([sys.executable, "-B", "-c", scripts[0], *dependencies], check=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--break-system-packages",
                str(wheel),
            ],
            check=True,
        )
else:
    # These source releases deployed their initial product+closure this way.
    # Qualification intentionally retains that predecessor, including apt shadows.
    assert any(f"-{version}-" in wheel.name for version in ("0.6.0", "0.6.1", "0.6.2"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--ignore-installed",
            "--break-system-packages",
            str(wheel),
        ],
        check=True,
    )
