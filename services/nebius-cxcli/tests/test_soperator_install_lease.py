from pathlib import Path

import pytest

from nebius_cxcli.deployment_local import DeploymentLocalLock


def test_local_install_lock_is_fail_fast_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "install.lock"

    with DeploymentLocalLock(path):
        lock_inode = path.stat().st_ino
        with (
            pytest.raises(RuntimeError, match="Another deployment"),
            DeploymentLocalLock(path),
        ):
            pass

    assert path.exists()
    with DeploymentLocalLock(path):
        assert path.stat().st_ino == lock_inode


def test_local_install_lock_rejects_links_without_truncating_target(tmp_path: Path) -> None:
    protected = tmp_path / "protected.txt"
    protected.write_text("preserve me\n", encoding="utf-8")
    symlink = tmp_path / "symlink.lock"
    symlink.symlink_to(protected)

    with (
        pytest.raises(RuntimeError, match="not a safe regular file"),
        DeploymentLocalLock(symlink),
    ):
        pass
    assert protected.read_text(encoding="utf-8") == "preserve me\n"

    hardlink = tmp_path / "hardlink.lock"
    hardlink.hardlink_to(protected)
    with (
        pytest.raises(RuntimeError, match="not a single-link regular file"),
        DeploymentLocalLock(hardlink),
    ):
        pass
    assert protected.read_text(encoding="utf-8") == "preserve me\n"
