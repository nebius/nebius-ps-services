import os
import shutil
import subprocess
import sys
from pathlib import Path


def build_binary() -> None:
    """Build a single-file PyInstaller binary named 'nebius-vpngw'.

    Requirements:
      - PyInstaller installed in the current environment (dev dependency)
      - __main__.py present to serve as entry point
    """
    if shutil.which("pyinstaller") is None:
        print("[build-binary] PyInstaller not found. Install with: pip install pyinstaller")
        sys.exit(1)

    entry = Path(__file__).parent / "__main__.py"
    if not entry.exists():
        print(f"[build-binary] Entry point not found: {entry}")
        sys.exit(1)

    systemd_dir = Path(__file__).parent / "systemd"
    add_data_args = []
    if systemd_dir.exists():
        add_data_args = [
            "--add-data",
            f"{systemd_dir}{os.pathsep}nebius_vpngw/systemd",
        ]
    else:
        print("[build-binary] WARNING: systemd assets not found; binary may miss agent units")

    cmd = [
        "pyinstaller",
        "--onefile",
        "--name",
        "nebius-vpngw",
        *add_data_args,
        str(entry),
    ]
    print("[build-binary] Running:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[build-binary] PyInstaller build failed")
        sys.exit(result.returncode)

    dist_path = Path("dist") / "nebius-vpngw"
    if dist_path.exists():
        print(f"[build-binary] Success. Binary at: {dist_path}")
    else:
        print("[build-binary] Build finished but binary not found in dist/")


if __name__ == "__main__":  # Allow direct invocation if desired
    build_binary()
