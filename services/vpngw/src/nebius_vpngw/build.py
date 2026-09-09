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

    # SSH sends this stdlib source to guests, including previous-release agents.
    # A module inside PyInstaller's bytecode archive is not a readable source file.
    remote_runner = Path(__file__).parent / "deploy" / "ordinary_remote.py"
    if not remote_runner.is_file():
        raise RuntimeError("ordinary transaction runner source is missing")
    add_data_args.extend(
        [
            "--add-data",
            f"{remote_runner}{os.pathsep}nebius_vpngw/deploy",
        ]
    )
    from .ordinary_routes import OWNERSHIP_SOURCES

    for source_name in (*OWNERSHIP_SOURCES, "ordinary_bootstrap.py"):
        source_path = Path(__file__).parent / source_name
        destination = str(Path("nebius_vpngw") / Path(source_name).parent)
        add_data_args.extend(["--add-data", f"{source_path}{os.pathsep}{destination}"])
    operation_source = Path(__file__).parent / "ordinary_operations.py"
    if not operation_source.is_file():
        raise RuntimeError("ordinary operation helper source is missing")
    add_data_args.extend(["--add-data", f"{operation_source}{os.pathsep}nebius_vpngw"])

    for relative in (
        "ha_repair.py",
        "deploy/handoff_remote.py",
        "agent/vm_ha_checkpoint.py",
        "agent/vm_ha_controller.py",
        "agent/vm_ha/models.py",
        "agent/vm_ha/mtls.py",
    ):
        source = Path(__file__).parent / relative
        if not source.is_file():
            raise RuntimeError("HA handoff helper source is missing")
        destination = "nebius_vpngw" + ("/" + str(Path(relative).parent) if "/" in relative else "")
        add_data_args.extend(["--add-data", f"{source}{os.pathsep}{destination}"])

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
