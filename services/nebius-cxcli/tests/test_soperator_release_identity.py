from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from nebius_cxcli.soperator_release import SoperatorReleaseMetadata
from nebius_cxcli.soperator_release_identity import (
    SoperatorReleaseIdentity,
    SoperatorReleaseIdentityLedger,
)


def _metadata() -> SoperatorReleaseMetadata:
    return SoperatorReleaseMetadata(
        selector="latest",
        release="4.1.7",
        repository="https://github.com/nebius/soperator",
        tag="4.1.7",
        commit="a" * 40,
        tree="b" * 40,
        archive_url="https://github.com/nebius/soperator/archive/refs/tags/4.1.7.tar.gz",
        archive_root="soperator-4.1.7",
        published_at="",
        tree_entries=(),
    )


def _record(root: Path, metadata: SoperatorReleaseMetadata) -> Path:
    ledger = SoperatorReleaseIdentityLedger(root)
    with ledger.locked(metadata) as identity:
        return ledger.record(identity)


def test_release_identity_first_seen_record_is_owner_only_and_idempotent(
    tmp_path: Path,
) -> None:
    path = _record(tmp_path / "identities", _metadata())

    assert _record(tmp_path / "identities", _metadata()) == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_release_identity_first_seen_record_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    synced_types: list[int] = []

    def _fsync(descriptor: int) -> None:
        synced_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr("nebius_cxcli.soperator_release_identity.os.fsync", _fsync)

    _record(tmp_path / "identities", _metadata())

    assert stat.S_IFREG in synced_types
    assert stat.S_IFDIR in synced_types


def test_release_identity_rejects_moved_tag(tmp_path: Path) -> None:
    root = tmp_path / "identities"
    metadata = _metadata()
    _record(root, metadata)

    with pytest.raises(RuntimeError, match="moved tag"):
        _record(root, replace(metadata, commit="c" * 40, tree="d" * 40))


def test_concurrent_release_identity_observation_publishes_one_record(tmp_path: Path) -> None:
    root = tmp_path / "identities"
    metadata = _metadata()

    with ThreadPoolExecutor(max_workers=4) as executor:
        paths = list(executor.map(lambda _index: _record(root, metadata), range(8)))

    assert len(set(paths)) == 1
    assert len(list(root.glob("release-*.json"))) == 1


def test_release_identity_rejects_symlink_record(tmp_path: Path) -> None:
    root = tmp_path / "identities"
    ledger = SoperatorReleaseIdentityLedger(root)
    identity = SoperatorReleaseIdentity.from_metadata(_metadata())
    target = tmp_path / "foreign.json"
    target.write_text("{}", encoding="utf-8")
    ledger._path(identity).symlink_to(target)

    with pytest.raises(ValueError, match="unsafe"), ledger.locked(_metadata()):
        pass


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_release_identity_failed_publication_can_retry(tmp_path, monkeypatch, failure):
    import errno

    from nebius_cxcli import soperator_release_identity as module

    root = tmp_path / "identities"
    ledger = SoperatorReleaseIdentityLedger(root)
    identity = SoperatorReleaseIdentity.from_metadata(_metadata())
    real_write, real_fsync = os.write, os.fsync
    wrote_partial = False

    def write(fd, data):
        nonlocal wrote_partial
        if not wrote_partial:
            wrote_partial = True
            return real_write(fd, data[:5])
        raise OSError(errno.ENOSPC, "fixture full disk")

    def fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "fixture failed sync")
        return real_fsync(fd)

    with ledger.locked(_metadata()), monkeypatch.context() as patch:
        patch.setattr(module.os, failure, write if failure == "write" else fsync)
        with pytest.raises(OSError):
            ledger.record(identity)
    assert not ledger._path(identity).exists()
    with ledger.locked(_metadata()):
        path = ledger.record(identity)
    ledger.verify_existing(identity)
    assert path.exists()
    assert path.stat().st_nlink == 1
    assert not list(root.glob("*.tmp"))


@pytest.mark.parametrize("failure", ["rename", "directory_sync"])
def test_release_identity_interrupted_publication_keeps_complete_pin_or_none(
    tmp_path, monkeypatch, failure
):
    from nebius_cxcli import soperator_release_identity as module

    ledger = SoperatorReleaseIdentityLedger(tmp_path / "identities")
    identity = SoperatorReleaseIdentity.from_metadata(_metadata())
    real_fsync = os.fsync

    def rename(*args, **kwargs):
        raise OSError("fixture interrupted rename")

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("fixture interrupted directory sync")
        return real_fsync(fd)

    with ledger.locked(_metadata()), monkeypatch.context() as patch:
        if failure == "rename":
            patch.setattr(module.os, "replace", rename)
        else:
            patch.setattr(module.os, "fsync", fsync)
        with pytest.raises(OSError):
            ledger.record(identity)
    assert ledger._path(identity).exists() == (failure == "directory_sync")
    ledger.verify_existing(identity)
    with ledger.locked(_metadata()):
        path = ledger.record(identity)
    assert path.stat().st_nlink == 1
    assert not list(ledger.root.glob("*.tmp"))


def test_release_identity_publication_requires_exact_thread_owned_lock(tmp_path):
    ledger = SoperatorReleaseIdentityLedger(tmp_path / "identities")
    identity = SoperatorReleaseIdentity.from_metadata(_metadata())
    with pytest.raises(RuntimeError, match="exact ledger lock"):
        ledger.record(identity)
    with ledger.locked(_metadata()):
        with pytest.raises(RuntimeError, match="exact ledger lock"):
            ledger.record(replace(identity, tag="4.1.8"))
        with ThreadPoolExecutor(max_workers=1) as executor:
            foreign_write = executor.submit(ledger.record, identity)
            with pytest.raises(RuntimeError, match="exact ledger lock"):
                foreign_write.result()
        ledger.record(identity)
    with pytest.raises(RuntimeError, match="exact ledger lock"):
        ledger.record(identity)


def test_concurrent_different_release_identities_preserve_winner(tmp_path):
    from threading import Barrier

    root = tmp_path / "identities"
    first = _metadata()
    second = replace(first, commit="c" * 40, tree="d" * 40)
    start = Barrier(2)

    def publish(metadata):
        start.wait(timeout=10)
        try:
            return _record(root, metadata), metadata
        except RuntimeError as exc:
            assert "moved tag" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, [first, second]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    path, metadata = winners[0]
    SoperatorReleaseIdentityLedger(root).verify_existing(
        SoperatorReleaseIdentity.from_metadata(metadata)
    )
    assert path.stat().st_nlink == 1
