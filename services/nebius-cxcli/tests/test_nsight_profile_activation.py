import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from nebius_cxcli import nsight_profile_activation as activation


@pytest.fixture
def hook(tmp_path, monkeypatch):
    path = tmp_path / "hook.sh"
    monkeypatch.setattr(activation, "HOOK", path)
    monkeypatch.setattr(activation, "validate_parent", lambda: None)
    original = Path.lstat

    def root_owned(path, *args, **kwargs):
        fields = list(original(path, *args, **kwargs))
        fields[4] = 0  # Unit fixture models the root-owned jail on macOS too.
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", root_owned)
    return path


def test_create_then_replay_and_verify_do_not_write(hook, monkeypatch):
    activation.activate("install")
    before = hook.stat()
    assert hook.read_text() == activation.CONTENT
    monkeypatch.setattr(
        activation.tempfile, "NamedTemporaryFile", lambda **kw: pytest.fail("unexpected write")
    )
    activation.activate("install")
    activation.activate("verify")
    assert hook.stat() == before


def test_verify_does_not_create_missing_hook(hook):
    with pytest.raises(RuntimeError, match="missing"):
        activation.activate("verify")
    assert not hook.exists()


@pytest.mark.parametrize("kind", ["content", "writable", "symlink", "hardlink", "owner"])
def test_foreign_hook_is_never_replaced(hook, monkeypatch, kind):
    hook.write_text(activation.CONTENT)
    hook.chmod(0o644)
    if kind == "content":
        hook.write_text("unrelated admin configuration\n")
    elif kind == "writable":
        hook.chmod(0o666)
    elif kind == "symlink":
        target = hook.with_suffix(".target")
        hook.rename(target)
        hook.symlink_to(target)
    elif kind == "hardlink":
        os.link(hook, hook.with_suffix(".link"))
    else:
        original = Path.lstat

        def foreign(path, *args, **kwargs):
            fields = list(original(path, *args, **kwargs))
            fields[4] = 42
            return os.stat_result(fields)

        monkeypatch.setattr(Path, "lstat", foreign)
    before = hook.lstat(), hook.read_bytes()
    for action in ("install", "verify"):
        with pytest.raises(RuntimeError, match="conflicting|unowned extra hardlink"):
            activation.activate(action)
    assert (hook.lstat(), hook.read_bytes()) == before


@pytest.mark.parametrize(
    "mode,uid", [(stat.S_IFLNK | 0o755, 0), (stat.S_IFDIR | 0o777, 0), (stat.S_IFDIR | 0o755, 42)]
)
def test_unsafe_parent_is_rejected(monkeypatch, mode, uid):
    monkeypatch.setattr(
        Path, "lstat", lambda p: os.stat_result((mode, 1, 1, 1, uid, 0, 0, 0, 0, 0))
    )
    with pytest.raises(RuntimeError, match="parent"):
        activation.validate_parent()


def test_interrupted_atomic_creation_can_retry_without_partial_hook(hook, monkeypatch):
    link = os.link
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        activation.activate("install")
    assert not hook.exists() and list(hook.parent.iterdir()) == []
    monkeypatch.setattr(os, "link", link)
    activation.activate("install")


def test_abrupt_exit_after_publication_recovers_only_owned_temporary_link(hook):
    code = """
import os, sys
from pathlib import Path
from nebius_cxcli import nsight_profile_activation as activation
activation.HOOK = Path(sys.argv[1])
activation.validate_parent = lambda: None
original = os.link
def crash(source, target):
    original(source, target)
    os._exit(73)
os.link = crash
activation.activate('install')
"""
    result = subprocess.run([sys.executable, "-c", code, str(hook)], check=False)
    assert result.returncode == 73 and hook.stat().st_nlink == 2
    before = hook.read_bytes(), hook.stat().st_ino
    with pytest.raises(RuntimeError, match="conflicting"):
        activation.activate("verify")
    assert hook.stat().st_nlink == 2  # Verification must not repair publication.
    activation.activate("install")
    assert (hook.read_bytes(), hook.stat().st_ino) == before
    assert hook.stat().st_nlink == 1 and list(hook.parent.iterdir()) == [hook]


def test_late_hook_corrects_soperator_cuda_path_order(tmp_path):
    profile_dir = tmp_path / "profile.d"
    profile_dir.mkdir()
    for dirname in ("pinned", "cuda"):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "nsys").write_text("#!/bin/sh\nexit 0\n")
        (directory / "nsys").chmod(0o755)
    payload = profile_dir / "99-nsight.sh"
    payload.write_text(f'export PATH="{tmp_path}/pinned:$PATH"\n')
    (profile_dir / "path_cuda.sh").write_text(f'export PATH="{tmp_path}/cuda:$PATH"\n')
    command = 'for i in "$1"/*.sh; do . "$i"; done; command -v nsys'

    def selected():
        return subprocess.check_output(
            ["bash", "--noprofile", "--norc", "-c", command, "bash", str(profile_dir)], text=True
        ).strip()

    assert selected() == str(tmp_path / "cuda/nsys")
    (profile_dir / activation.HOOK.name).write_text(
        activation.CONTENT.replace("/etc/profile.d/99-nsight.sh", str(payload))
    )
    assert selected() == str(tmp_path / "pinned/nsys")
