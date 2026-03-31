from __future__ import annotations

import json
import os
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from nebius_vpngw.deploy.ssh_push import SSHPush


class _FakeDistribution:
    def __init__(self, direct_url: str, version: str = "0.5.4") -> None:
        self._direct_url = direct_url
        self.version = version

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url
        return None


class _FakeResponse(BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def test_select_wheel_from_dirs_prefers_installed_version(tmp_path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    older = dist_dir / "nebius_vpngw-0.9.0-py3-none-any.whl"
    preferred = dist_dir / "nebius_vpngw-1.2.3-py3-none-any.whl"
    newest_other = dist_dir / "nebius_vpngw-2.0.0-py3-none-any.whl"

    older.write_text("", encoding="utf-8")
    preferred.write_text("", encoding="utf-8")
    newest_other.write_text("", encoding="utf-8")

    os.utime(older, (1, 1))
    os.utime(preferred, (2, 2))
    os.utime(newest_other, (3, 3))

    with patch("nebius_vpngw.deploy.ssh_push.metadata.version", return_value="1.2.3"):
        selected = SSHPush()._select_wheel_from_dirs([dist_dir])

    assert selected == preferred


def test_build_wheel_uses_environment_override(tmp_path, monkeypatch) -> None:
    wheel_path = tmp_path / "custom.whl"
    wheel_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", str(wheel_path))

    selected = SSHPush()._build_wheel()

    assert selected == wheel_path


def test_build_wheel_downloads_wheel_from_install_url(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    direct_url = json.dumps(
        {
            "url": (
                "https://github.com/nebius/nebius-ps-services/releases/download/"
                "nebius-vpngw-v0.5.4/nebius_vpngw-0.5.4-py3-none-any.whl"
            )
        }
    )
    monkeypatch.setattr(SSHPush, "_find_project_root", lambda self: None)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.metadata.distribution",
        lambda _: _FakeDistribution(direct_url),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.urlopen",
        lambda url, timeout: _FakeResponse(b"wheel-bytes"),
    )

    selected = SSHPush()._build_wheel()

    assert selected is not None
    assert selected.name == "nebius_vpngw-0.5.4-py3-none-any.whl"
    assert selected.read_bytes() == b"wheel-bytes"


def test_build_wheel_falls_back_to_original_local_wheel_when_build_fails(
    tmp_path, monkeypatch
) -> None:
    original_wheel = tmp_path / "nebius_vpngw-0.5.4-py3-none-any.whl"
    original_wheel.write_text("wheel", encoding="utf-8")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        "[project]\nname='nebius-vpngw'\n", encoding="utf-8"
    )
    direct_url = json.dumps({"url": original_wheel.resolve().as_uri()})

    monkeypatch.setattr(SSHPush, "_find_project_root", lambda self: project_root)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stderr="No module named build", stdout=""
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.metadata.distribution",
        lambda _: _FakeDistribution(direct_url),
    )

    selected = SSHPush()._build_wheel()

    assert selected == original_wheel
