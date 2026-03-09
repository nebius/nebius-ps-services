from __future__ import annotations

from unittest.mock import Mock

import pytest

from nebius_vpngw import build


def test_build_binary_exits_when_pyinstaller_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(build.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit) as exc_info:
        build.build_binary()

    assert exc_info.value.code == 1


def test_build_binary_invokes_pyinstaller_with_systemd_assets(tmp_path, monkeypatch) -> None:
    run_mock = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(build.shutil, "which", lambda _: "/usr/bin/pyinstaller")
    monkeypatch.setattr(build.subprocess, "run", run_mock)
    monkeypatch.chdir(tmp_path)
    dist_binary = tmp_path / "dist" / "nebius-vpngw"
    dist_binary.parent.mkdir(parents=True, exist_ok=True)
    dist_binary.write_text("", encoding="utf-8")

    build.build_binary()

    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "pyinstaller"
    assert "--onefile" in cmd
    assert "--add-data" in cmd
    assert str(build.Path(build.__file__).parent / "__main__.py") in cmd
