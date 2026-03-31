from __future__ import annotations

import shutil
import subprocess
import sys
from email import message_from_string
from pathlib import Path
from zipfile import ZipFile


def _build_wheel(tmp_path: Path) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    shutil.rmtree(project_root / "build", ignore_errors=True)
    shutil.rmtree(project_root / "dist", ignore_errors=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    wheels = list(tmp_path.glob("nebius_vpngw-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_build_uses_package_local_version_file(tmp_path) -> None:
    wheel_path = _build_wheel(tmp_path)

    with ZipFile(wheel_path) as wheel_zip:
        names = set(wheel_zip.namelist())

    assert "nebius_vpngw/_version.py" in names
    assert "services/vpngw/src/nebius_vpngw/_version.py" not in names


def test_wheel_metadata_pins_pygments_floor(tmp_path) -> None:
    wheel_path = _build_wheel(tmp_path)

    with ZipFile(wheel_path) as wheel_zip:
        metadata_name = next(name for name in wheel_zip.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = message_from_string(wheel_zip.read(metadata_name).decode("utf-8"))

    requires_dist = metadata.get_all("Requires-Dist") or []
    assert any(
        requirement.startswith("Pygments")
        and ">=2.20.0" in requirement
        and "<3.0.0" in requirement
        for requirement in requires_dist
    )
