"""Compile against the CUDA headers owned by the isolated training environment."""

import shutil
import subprocess

import pytest

from course_bootstrap import catalog
from course_bootstrap.installer import Installer
from course_bootstrap.state import State


def test_training_extension_finds_its_wheel_headers(tmp_path, monkeypatch):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("A C++ preprocessor is required")
    prefix = tmp_path / "isolated environment"
    site = prefix / "venv/lib/python3.12/site-packages/nvidia"
    headers = {
        "cu13": ("cusparse.h", "cusolverDn.h"),
        "cudnn": ("cudnn.h",),
        "nccl": ("nccl.h",),
    }
    for package, names in headers.items():
        include = site / package / "include"
        include.mkdir(parents=True)
        for name in names:
            (include / name).write_text("/* environment-owned header */\n")
    unrelated = tmp_path / "unrelated headers"
    unrelated.mkdir()
    (unrelated / "cusparse.h").write_text("#error External headers selected\n")
    monkeypatch.setenv("CPATH", str(unrelated))
    for name in ("C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH"):
        monkeypatch.delenv(name, raising=False)
    definitions = catalog.load_catalog()
    command = definitions["components"]["training-te"]["commands"][0]
    values = {
        "prefix": str(prefix),
        "toolchain": str(tmp_path / "toolchain"),
        "build-tools": str(tmp_path / "build-tools"),
    }
    environment = {
        key: Installer.expand(value, values) for key, value in command["env"].items()
    }
    installer = Installer(tmp_path, {}, definitions, State(tmp_path))
    source = "".join(
        f"#include <{name}>\n" for names in headers.values() for name in names
    )
    checked = subprocess.run(
        [compiler, "-E", "-x", "c++", "-"],
        input=source,
        env=installer.environment(environment),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert checked.returncode == 0, checked.stderr
