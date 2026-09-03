from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli.soperator_cache import locked_cache_entry, prepare_private_cache_root
from nebius_cxcli.soperator_release import SoperatorGitTreeEntry, SoperatorReleaseMetadata
from nebius_cxcli.soperator_release_source import (
    SoperatorArchiveLimits,
    acquire_soperator_release_source,
    extract_soperator_release_archive,
)


class _ArchiveResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str) -> None:
        super().__init__(payload)
        self.headers: dict[str, str] = {}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _ArchiveResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _ArchiveOpener:
    def __init__(self, payload: bytes, url: str) -> None:
        self.payload = payload
        self.url = url

    def open(self, _request: Any, timeout: float = 0) -> _ArchiveResponse:
        del timeout
        return _ArchiveResponse(self.payload, self.url)


def _archive(
    tmp_path: Path,
    members: list[tuple[tarfile.TarInfo, bytes]],
) -> tuple[Path, str]:
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        root = tarfile.TarInfo("soperator-4.1.7")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        for member, content in members:
            bundle.addfile(member, io.BytesIO(content) if member.isreg() else None)
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, digest


def _file(name: str, content: bytes = b"data") -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    return member, content


def test_archive_rejects_parent_traversal_before_writing_outside_destination(
    tmp_path: Path,
) -> None:
    archive, digest = _archive(
        tmp_path,
        [_file("soperator-4.1.7/../escape.txt", b"escape")],
    )

    with pytest.raises(ValueError, match="unsafe archive member path"):
        extract_soperator_release_archive(
            archive,
            tmp_path / "source",
            expected_root="soperator-4.1.7",
            expected_archive_sha256=digest,
            expected_manifest_sha256=None,
        )

    assert not (tmp_path / "escape.txt").exists()


def test_archive_rejects_symlink_members(tmp_path: Path) -> None:
    member = tarfile.TarInfo("soperator-4.1.7/link")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/passwd"
    archive, digest = _archive(tmp_path, [(member, b"")])

    with pytest.raises(ValueError, match="not a regular file"):
        extract_soperator_release_archive(
            archive,
            tmp_path / "source",
            expected_root="soperator-4.1.7",
            expected_archive_sha256=digest,
            expected_manifest_sha256=None,
        )


def test_archive_enforces_per_file_and_expanded_size_limits(tmp_path: Path) -> None:
    archive, digest = _archive(
        tmp_path,
        [_file("soperator-4.1.7/large.bin", b"12345")],
    )

    with pytest.raises(ValueError, match="per-file limit"):
        extract_soperator_release_archive(
            archive,
            tmp_path / "source",
            expected_root="soperator-4.1.7",
            expected_archive_sha256=digest,
            expected_manifest_sha256=None,
            limits=SoperatorArchiveLimits(max_file_bytes=4, max_expanded_bytes=10),
        )


def test_archive_counts_pax_metadata_in_decompressed_stream_limit(tmp_path: Path) -> None:
    member, content = _file("soperator-4.1.7/Chart.yaml", b"name: soperator\n")
    member.pax_headers = {"comment": "x" * 4096}
    archive, digest = _archive(tmp_path, [(member, content)])

    with pytest.raises(ValueError, match="decompressed tar-stream limit"):
        extract_soperator_release_archive(
            archive,
            tmp_path / "source",
            expected_root="soperator-4.1.7",
            expected_archive_sha256=digest,
            expected_manifest_sha256=None,
            limits=SoperatorArchiveLimits(max_tar_bytes=1024),
        )


def test_acquire_rejects_cached_receipt_with_foreign_source_directory(
    tmp_path: Path,
) -> None:
    content = b"apiVersion: v2\nname: soperator\n"
    archive, _digest = _archive(
        tmp_path,
        [_file("soperator-4.1.7/Chart.yaml", content)],
    )
    archive_url = "https://github.com/nebius/soperator/archive/refs/tags/4.1.7.tar.gz"
    blob = hashlib.sha1(  # noqa: S324 - intentional Git object identity.
        f"blob {len(content)}\0".encode() + content
    ).hexdigest()
    metadata = SoperatorReleaseMetadata(
        selector="4.1.7",
        release="4.1.7",
        repository="https://github.com/nebius/soperator",
        tag="4.1.7",
        commit="a" * 40,
        tree="b" * 40,
        archive_url=archive_url,
        archive_root="soperator-4.1.7",
        published_at="",
        tree_entries=(
            SoperatorGitTreeEntry("Chart.yaml", "100644", "blob", blob, len(content)),
        ),
    )
    opener = _ArchiveOpener(archive.read_bytes(), archive_url)
    cache_root = tmp_path / "cache"

    first = acquire_soperator_release_source(
        metadata,
        cache_root=cache_root,
        opener=opener,
    )
    receipt_path = Path(first.source_dir).with_name(f"{Path(first.source_dir).name}.receipt.json")
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["source_dir"] = str(tmp_path / "foreign-source")
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    second = acquire_soperator_release_source(
        metadata,
        cache_root=cache_root,
        opener=opener,
    )

    assert second.source_dir == first.source_dir
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["source_dir"] == first.source_dir


def test_cache_root_and_lock_are_owner_only(tmp_path: Path) -> None:
    root = prepare_private_cache_root(tmp_path / "cache")
    root.chmod(0o777)
    assert prepare_private_cache_root(root).stat().st_mode & 0o777 == 0o700

    key = "a" * 64
    with locked_cache_entry(root, key):
        lock = root / ".locks" / f"{key}.lock"
        assert lock.stat().st_mode & 0o777 == 0o600


def test_cache_root_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "cache"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="not a safe directory"):
        prepare_private_cache_root(link)
