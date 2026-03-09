from __future__ import annotations

import os
from unittest.mock import patch

from nebius_vpngw.deploy.ssh_push import SSHPush


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
