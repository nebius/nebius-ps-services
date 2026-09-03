from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from email import message_from_string
from pathlib import Path
from zipfile import ZipFile

import pytest

pytestmark = pytest.mark.integration


def _build_frozen_binary(tmp_path: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "nebius_vpngw.build"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    binary_name = "nebius-vpngw.exe" if sys.platform == "win32" else "nebius-vpngw"
    binary_path = tmp_path / "dist" / binary_name
    assert binary_path.is_file()
    return binary_path


def _build_wheel(tmp_path: Path) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    source_root = tmp_path / "source"
    wheel_dir = tmp_path / "wheel"
    shutil.copytree(
        project_root,
        source_root,
        ignore=shutil.ignore_patterns(
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
        ),
    )
    wheel_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
        ],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    wheels = list(wheel_dir.glob("nebius_vpngw-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_build_uses_package_local_version_file(tmp_path) -> None:
    wheel_path = _build_wheel(tmp_path)

    with ZipFile(wheel_path) as wheel_zip:
        names = set(wheel_zip.namelist())

    assert "nebius_vpngw/_version.py" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-esp4-preflight.sh" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-fix-routes.service" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-fix-routes.timer" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-vm-ha-guard.service" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-vm-ha-peer-firewall.sh" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-vm-ha-rearm.service" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-vm-ha.service" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-vm-ha-ordering.conf" in names
    assert "nebius_vpngw/systemd/nebius-vpngw-ufw-lock.conf" in names
    assert "nebius_vpngw/agent/vm_ha/promotion_receipt.py" in names
    assert "nebius_vpngw/agent/vm_ha/restoration.py" in names
    assert "services/vpngw/src/nebius_vpngw/_version.py" not in names

    install_root = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert install.returncode == 0, install.stderr

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(install_root)
    capability = subprocess.run(
        [
            sys.executable,
            "-m",
            "nebius_vpngw.agent.main",
            "--agent-capabilities",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert capability.returncode == 0, capability.stderr
    assert "vm-ha-standby-restoration-v2" in json.loads(capability.stdout)["features"]


def test_wheel_metadata_retains_runtime_dependency_bounds(tmp_path) -> None:
    wheel_path = _build_wheel(tmp_path)

    with ZipFile(wheel_path) as wheel_zip:
        metadata_name = next(
            name for name in wheel_zip.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = message_from_string(wheel_zip.read(metadata_name).decode("utf-8"))

    requires_dist = metadata.get_all("Requires-Dist") or []
    assert any(
        requirement.startswith("click") and ">=8.3.0" in requirement and "<8.4.0" in requirement
        for requirement in requires_dist
    )
    assert any(
        requirement.startswith("typer") and ">=0.20.0" in requirement and "<0.26.0" in requirement
        for requirement in requires_dist
    )
    assert any(
        requirement.startswith("nebius") and ">=0.3.18" in requirement and "<0.4.0" in requirement
        for requirement in requires_dist
    )
    assert any(
        requirement.startswith("Pygments") and ">=2.20.0" in requirement and "<3.0.0" in requirement
        for requirement in requires_dist
    )
    assert any(
        requirement.startswith("cryptography")
        and ">=42.0.0" in requirement
        and "<50.0.0" in requirement
        for requirement in requires_dist
    )


def test_frozen_binary_starts_and_contains_routing_assets(tmp_path) -> None:
    binary_path = _build_frozen_binary(tmp_path)

    version = subprocess.run(
        [str(binary_path), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("nebius-vpngw ")

    archive = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller.utils.cliutils.archive_viewer",
            "-l",
            str(binary_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert archive.returncode == 0, archive.stderr
    assert "nebius_vpngw/systemd/nebius-vpngw-fix-routes.service" in archive.stdout
    assert "nebius_vpngw/systemd/nebius-vpngw-fix-routes.timer" in archive.stdout
