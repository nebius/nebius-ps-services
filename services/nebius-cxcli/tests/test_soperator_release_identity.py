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
