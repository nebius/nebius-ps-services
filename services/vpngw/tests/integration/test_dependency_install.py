"""Offline pip regression with distro-style metadata in a disposable interpreter."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from nebius_vpngw.deploy.ordinary_remote import DEPENDENCY_INSTALL_SCRIPT

pytestmark = pytest.mark.integration


def test_selected_dependency_shadows_distro_but_replaces_pip_owned_version(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    env.pop("PYTHONPATH", None)
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1")

    def command(args, *, check=True):
        result = subprocess.run(
            args, env=env, capture_output=True, text=True, timeout=60, check=False
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    venv = tmp_path / "venv"
    command([sys.executable, "-m", "venv", str(venv)])
    python = str(venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))
    site = Path(
        command(
            [python, "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"]
        ).stdout.strip()
    )
    # Keep both roots within the interpreter prefix, as /usr and /usr/local
    # are on a gateway. An out-of-venv fixture makes pip skip uninstallation.
    distro = venv / "distro"
    distro.mkdir()
    (distro / "align_dependency.py").write_text("value = 1\n")
    info = distro / "align_dependency-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: align-dependency\nVersion: 1.0\n")
    (info / "INSTALLER").write_text("deb\n")
    (site / "distro.pth").write_text(str(distro) + "\n")
    before = {path: path.read_bytes() for path in distro.rglob("*") if path.is_file()}

    def wheel(version):
        path = tmp_path / f"align_dependency-{version}.0-py3-none-any.whl"
        prefix = f"align_dependency-{version}.0.dist-info"
        files = {
            "align_dependency.py": f"value = {version}\n",
            f"{prefix}/METADATA": f"Metadata-Version: 2.1\nName: align-dependency\nVersion: {version}.0\n",
            f"{prefix}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        files[f"{prefix}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{prefix}/RECORD"])
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return str(path)

    first = wheel(2)
    # Negative control: the original install path cannot uninstall missing RECORD.
    failed = command(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            first,
        ],
        check=False,
    )
    assert failed.returncode != 0 and "RECORD" in failed.stderr
    command([python, "-B", "-c", DEPENDENCY_INSTALL_SCRIPT, first])
    probe = "import align_dependency as d,importlib.metadata as m;print(d.value,m.version('align-dependency'))"
    assert command([python, "-B", "-c", probe]).stdout.strip() == "2 2.0"
    command([python, "-B", "-c", DEPENDENCY_INSTALL_SCRIPT, wheel(3)])
    assert command([python, "-B", "-c", probe]).stdout.strip() == "3 3.0"
    assert not (site / "align_dependency-2.0.dist-info").exists()
    assert before == {path: path.read_bytes() for path in distro.rglob("*") if path.is_file()}
