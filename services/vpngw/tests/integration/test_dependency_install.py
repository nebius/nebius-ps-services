"""Offline pip regression with distro-style metadata in a disposable interpreter."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

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


def test_recovery_package_install_preserves_one_product_distribution(tmp_path, monkeypatch):
    """Execute the SSH installer's actual pip commands against a real predecessor."""
    from nebius_vpngw.agent.vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY
    from nebius_vpngw.deploy import ordinary_apply, ssh_push, vm_ha_package

    env = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    env.pop("PYTHONPATH", None)
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1")
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], env=env, check=True, timeout=60)
    python = str(venv / "bin/python")

    def run(args):
        return subprocess.run(args, env=env, capture_output=True, timeout=60)

    def wheel(version):
        path = tmp_path / f"nebius_vpngw-{version}.0-py3-none-any.whl"
        prefix = f"nebius_vpngw-{version}.0.dist-info"
        files = {
            "nebius_vpngw/__init__.py": f"version = {version}\n",
            f"{prefix}/METADATA": f"Metadata-Version: 2.1\nName: nebius-vpngw\nVersion: {version}.0\n",
            f"{prefix}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        files[f"{prefix}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{prefix}/RECORD"])
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return path

    assert run([python, "-m", "pip", "install", "--no-deps", str(wheel(1))]).returncode == 0
    closed = []
    observation = dict.fromkeys(vm_ha_package.PACKAGE_FIELDS)

    def inspect_plan(*args, **kwargs):
        assert kwargs["wheel"].is_file()
        return SimpleNamespace(
            manifest={"dependency_wheels": {}},
            dependency_paths=[],
            observation=observation,
            close=lambda: closed.append(True),
        )

    monkeypatch.setattr(vm_ha_package, "inspect_plan", inspect_plan)
    monkeypatch.setattr(ordinary_apply, "remote", lambda *args: {"observation": observation})
    monkeypatch.setattr(ssh_push, "configure_paramiko_host_verification", lambda *a, **k: None)
    monkeypatch.setattr(ssh_push.ingress_ssh, "preflight", lambda *a: None)

    class Stream(io.BytesIO):
        def __init__(self, data=b"", code=0):
            super().__init__(data)
            self.channel = SimpleNamespace(
                recv_exit_status=lambda: code,
                settimeout=lambda _: None,
                send=self.write,
                shutdown_write=lambda: None,
            )

    class Client:
        def __init__(self, version):
            self.version = version
            self.staged = tmp_path / ("staging-" + str(version))
            self.staged.mkdir()

        def open_sftp(self):
            def putfo(stream, destination, **kwargs):
                (self.staged / Path(destination).name).write_bytes(stream.read())

            return contextlib.nullcontext(SimpleNamespace(putfo=putfo))

        def exec_command(self, command, **kwargs):
            if command == "sudo -n PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B":
                # Checkpoint observation is simulated; emitted pip commands run below.
                return Stream(), Stream(b'{"checkpoint":"unstarted"}'), io.BytesIO()
            if "mktemp -d" in command:
                return io.BytesIO(), Stream(b"/tmp/nebius-vpngw-agent.ABCDEFGHIJ\n"), io.BytesIO()
            if "pip install" in command:
                # apt/SSH boundaries are simulated; the emitted pip invocation is real.
                command = command.split(" && ")[-1]
                args = shlex.split(command)[1:]
                args[0] = python
                args[-1] = str(self.staged / Path(args[-1]).name)
                result = run(args)
                return (
                    io.BytesIO(),
                    Stream(result.stdout, result.returncode),
                    io.BytesIO(result.stderr),
                )
            if "import cffi,cryptography" in command:
                value = {
                    "schema": "nebius-vpngw/vm-ha-package-v1",
                    "package_version": f"{self.version}.0",
                    "cryptography_version": "50.0.0",
                    "cffi_version": "2.0.0",
                }
            elif "--agent-capabilities" in command:
                value = {
                    "schema": "nebius-vpngw.agent-capabilities.v1",
                    "features": [STANDBY_RESTORATION_CAPABILITY],
                }
            else:
                return io.BytesIO(), Stream(), io.BytesIO()
            return io.BytesIO(), Stream(json.dumps(value).encode()), io.BytesIO()

        def close(self):
            pass

    push = ssh_push.SSHPush(ssh_policy=object())
    monkeypatch.setattr(push, "_connect_client", lambda *a, **k: None)
    manifest = SimpleNamespace(hostname="fixture", config_yaml="gateway: {}\n")
    for version in (2, 3):
        path = wheel(version)
        artifact = SimpleNamespace(
            path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            capabilities=(),
            dependency_plans=(),
            open_verified=lambda p=path: p.open("rb"),
        )
        client = Client(version)
        push._paramiko = SimpleNamespace(SSHClient=lambda c=client: c)
        push.ensure_vm_ha_agent_package("203.0.113.10", manifest, {}, artifact=artifact)
        probe = "import importlib.metadata as m,json,nebius_vpngw as p;print(json.dumps({'versions':[d.version for d in m.distributions() if d.metadata['Name']=='nebius-vpngw'],'code':p.version}))"
        result = run([python, "-B", "-c", probe])
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"versions": [f"{version}.0"], "code": version}
    assert closed == [True, True]
