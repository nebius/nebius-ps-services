"""Verified acquisition of official upstream Soperator release source.

The archive is untrusted data. It is never imported or executed on the host.
First acquisition proves every regular file against the Git tree resolved from
the official release tag; later reads verify the frozen content digests.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .archive_safety import open_bounded_tar_gz
from .soperator_cache import locked_cache_entry, prepare_private_cache_root
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_release import (
    SoperatorReleaseMetadata,
    SoperatorReleaseSnapshot,
    verify_soperator_source_git_tree,
)

SOPERATOR_SOURCE_CACHE_SCHEMA = "nebius-cxcli.soperator-source-cache.v1"
_ALLOWED_ARCHIVE_HOSTS = frozenset({"github.com", "codeload.github.com"})
_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SoperatorArchiveLimits:
    max_compressed_bytes: int = 100 * 1024 * 1024
    max_expanded_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 64 * 1024 * 1024
    max_members: int = 25_000
    max_tar_bytes: int = 640 * 1024 * 1024


@dataclass(frozen=True)
class SoperatorSourceReceipt:
    schema: str
    release: str
    commit: str
    tree: str
    archive_sha256: str
    manifest_sha256: str
    source_dir: str


def default_soperator_source_cache_root() -> Path:
    configured = str(os.environ.get("NEBIUS_CXCLI_CACHE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser() / "soperator" / "sources"
    xdg_cache = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return base / "nebius-cxcli" / "soperator" / "sources"


def _sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_BUFFER_SIZE):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"archive exceeds the {max_bytes}-byte compressed-size limit")
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _normalized_relative_path(value: str, *, expected_root: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe archive member path: {value!r}")
    normalized = unicodedata.normalize("NFC", value.rstrip("/"))
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive member path: {value!r}")
    if path.parts[0] != expected_root:
        raise ValueError(
            f"archive member {value!r} is outside expected top-level directory {expected_root!r}"
        )
    relative = PurePosixPath(*path.parts[1:])
    return "" if str(relative) == "." else str(relative)


def _open_exclusive_regular_file(path: Path) -> BinaryIO:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb")


def normalized_tree_manifest(root: Path) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Hash a tree by normalized path, size, and file-content digest."""

    base = root.resolve(strict=True)
    entries: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = unicodedata.normalize("NFC", path.relative_to(base).as_posix())
        file_stat = path.lstat()
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"source tree contains a non-regular file: {relative}")
        digest = _sha256_file(path)
        entries.append({"path": relative, "size": file_stat.st_size, "sha256": digest})
    encoded = "".join(
        f"{item['sha256']}  {item['size']}  {item['path']}\n" for item in entries
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", tuple(entries)


def normalized_script_manifest(
    root: Path,
    *,
    suffixes: Iterable[str] = (".sh", ".py"),
) -> tuple[str, tuple[dict[str, Any], ...]]:
    accepted = frozenset(suffixes)
    digest, entries = normalized_tree_manifest(root)
    del digest
    scripts = tuple(item for item in entries if Path(str(item["path"])).suffix in accepted)
    encoded = "".join(
        f"{item['sha256']}  {item['size']}  {item['path']}\n" for item in scripts
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", scripts


def extract_soperator_release_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_root: str,
    expected_archive_sha256: str,
    expected_manifest_sha256: str | None,
    limits: SoperatorArchiveLimits | None = None,
) -> Path:
    """Safely extract and verify one official release archive."""

    effective_limits = limits or SoperatorArchiveLimits()
    archive = archive_path.resolve(strict=True)
    archive_stat = archive.lstat()
    if not stat.S_ISREG(archive_stat.st_mode) or archive_stat.st_nlink != 1:
        raise ValueError("release archive must be a single-link regular file")
    actual_archive_sha = _sha256_file(
        archive,
        max_bytes=effective_limits.max_compressed_bytes,
    )
    if actual_archive_sha != expected_archive_sha256:
        raise ValueError(
            f"release archive digest mismatch: expected {expected_archive_sha256}, got {actual_archive_sha}"
        )

    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    seen_exact: set[str] = set()
    seen_casefold: dict[str, str] = {}
    expanded_bytes = 0
    member_count = 0
    with archive.open("rb") as compressed, open_bounded_tar_gz(
        compressed,
        max_uncompressed_bytes=effective_limits.max_tar_bytes,
        label="release archive",
    ) as bundle:
        for member in bundle:
            member_count += 1
            if member_count > effective_limits.max_members:
                raise ValueError("release archive exceeds the member-count limit")
            relative = _normalized_relative_path(member.name, expected_root=expected_root)
            if not relative:
                if not member.isdir():
                    raise ValueError("release archive root must be a directory")
                continue
            if relative in seen_exact:
                raise ValueError(f"release archive contains duplicate member {relative!r}")
            folded = relative.casefold()
            collision = seen_casefold.get(folded)
            if collision is not None:
                raise ValueError(
                    f"release archive contains case-colliding members {collision!r} and {relative!r}"
                )
            seen_exact.add(relative)
            seen_casefold[folded] = relative
            output = destination / relative
            try:
                output.resolve(strict=False).relative_to(destination.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"release archive member escapes destination: {relative!r}"
                ) from exc
            if member.isdir():
                output.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if not member.isreg():
                raise ValueError(f"release archive member is not a regular file: {relative!r}")
            if member.size < 0 or member.size > effective_limits.max_file_bytes:
                raise ValueError(f"release archive member exceeds the per-file limit: {relative!r}")
            expanded_bytes += member.size
            if expanded_bytes > effective_limits.max_expanded_bytes:
                raise ValueError("release archive exceeds the expanded-size limit")
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"release archive member could not be read: {relative!r}")
            written = 0
            with source, _open_exclusive_regular_file(output) as target:
                while chunk := source.read(_BUFFER_SIZE):
                    written += len(chunk)
                    if written > member.size or written > effective_limits.max_file_bytes:
                        raise ValueError(
                            f"release archive member expanded beyond its header: {relative!r}"
                        )
                    target.write(chunk)
            if written != member.size:
                raise ValueError(f"release archive member was truncated: {relative!r}")

    actual_manifest, _ = normalized_tree_manifest(destination)
    if expected_manifest_sha256 is not None and actual_manifest != expected_manifest_sha256:
        raise ValueError(
            f"release source manifest mismatch: expected {expected_manifest_sha256}, got {actual_manifest}"
        )
    return destination


def _validated_https_url(url: str, *, label: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must be an allowlisted credential-free HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_ARCHIVE_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an allowlisted credential-free HTTPS URL")
    return urllib.parse.urlunsplit(parsed)


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validated_https_url(newurl, label="release archive redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_archive(
    url: str,
    destination: Path,
    *,
    limits: SoperatorArchiveLimits,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    validated = _validated_https_url(url, label="release archive URL")
    client = opener or urllib.request.build_opener(_AllowlistedRedirectHandler())
    request = urllib.request.Request(validated, headers={"User-Agent": "nebius-cxcli"})
    try:
        response = client.open(request, timeout=60)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"failed to download official Soperator release archive: {exc}") from exc
    with response:
        final_url = str(response.geturl() or "")
        _validated_https_url(final_url, label="release archive response URL")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limits.max_compressed_bytes:
            raise ValueError("release archive exceeds the compressed-size limit")
        written = 0
        with _open_exclusive_regular_file(destination) as target:
            while chunk := response.read(_BUFFER_SIZE):
                written += len(chunk)
                if written > limits.max_compressed_bytes:
                    raise ValueError("release archive exceeds the compressed-size limit")
                target.write(chunk)


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        mode = 0o555 if path.is_dir() else 0o444
        path.chmod(mode, follow_symlinks=False)
    root.chmod(0o555)


def _remove_private_staging_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        with suppress(OSError):
            path.chmod(0o700 if path.is_dir() else 0o600, follow_symlinks=False)
    with suppress(OSError):
        root.chmod(0o700)
    shutil.rmtree(root, ignore_errors=True)


def _receipt_path(source_dir: Path) -> Path:
    return source_dir.with_name(f"{source_dir.name}.receipt.json")


def _load_cached_receipt(
    source_dir: Path,
    snapshot: SoperatorReleaseSnapshot,
) -> SoperatorSourceReceipt:
    receipt_path = _receipt_path(source_dir)
    try:
        payload = read_owner_only_json(
            receipt_path,
            label="cached Soperator source receipt",
        )
        if not isinstance(payload, Mapping):
            raise TypeError("cached Soperator source receipt must be a mapping")
    except (OSError, RuntimeError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cached Soperator source has no valid receipt: {receipt_path}") from exc
    receipt = SoperatorSourceReceipt(**payload)
    expected = SoperatorSourceReceipt(
        schema=SOPERATOR_SOURCE_CACHE_SCHEMA,
        release=snapshot.release,
        commit=snapshot.commit,
        tree=snapshot.tree,
        archive_sha256=snapshot.archive_sha256,
        manifest_sha256=snapshot.source_manifest_sha256,
        source_dir=str(source_dir),
    )
    if receipt != expected:
        raise ValueError("cached Soperator source receipt does not match the operation snapshot")
    actual_manifest, _ = normalized_tree_manifest(source_dir)
    if actual_manifest != snapshot.source_manifest_sha256:
        raise ValueError("cached Soperator source tree no longer matches its accepted manifest")
    return receipt


def ensure_soperator_release_source(
    snapshot: SoperatorReleaseSnapshot,
    *,
    cache_root: Path | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    limits: SoperatorArchiveLimits | None = None,
) -> SoperatorSourceReceipt:
    """Download, verify, cache, and return one frozen upstream source."""

    effective_limits = limits or SoperatorArchiveLimits()
    root = prepare_private_cache_root(
        (cache_root or default_soperator_source_cache_root()).expanduser()
    )
    digest_token = snapshot.archive_sha256.removeprefix("sha256:")
    accepted_dir = root / digest_token
    with locked_cache_entry(root, digest_token):
        if accepted_dir.exists() or _receipt_path(accepted_dir).exists():
            try:
                if not accepted_dir.is_dir() or accepted_dir.is_symlink():
                    raise ValueError("cached Soperator source path is not a safe directory")
                return _load_cached_receipt(accepted_dir, snapshot)
            except ValueError:
                _remove_private_staging_tree(accepted_dir)
                with suppress(OSError):
                    _receipt_path(accepted_dir).unlink()

        staging = Path(tempfile.mkdtemp(prefix=f".{snapshot.release}-", dir=root))
        try:
            archive = staging / "release.tar.gz"
            _download_archive(
                snapshot.archive_url,
                archive,
                limits=effective_limits,
                opener=opener,
            )
            extracted = staging / "source"
            extract_soperator_release_archive(
                archive,
                extracted,
                expected_root=snapshot.archive_root,
                expected_archive_sha256=snapshot.archive_sha256,
                expected_manifest_sha256=snapshot.source_manifest_sha256,
                limits=effective_limits,
            )
            for chart in snapshot.charts.values():
                chart_root = extracted / chart.source_path
                actual_tree, _ = normalized_tree_manifest(chart_root)
                if actual_tree != chart.source_tree_sha256:
                    raise ValueError(
                        f"source chart {chart.name} tree mismatch: expected "
                        f"{chart.source_tree_sha256}, got {actual_tree}"
                    )
            script_manifest, _ = normalized_script_manifest(extracted)
            if script_manifest != snapshot.scripts_manifest_sha256:
                raise ValueError("upstream script manifest does not match the operation snapshot")
            os.replace(extracted, accepted_dir)
            _make_tree_read_only(accepted_dir)
            receipt = SoperatorSourceReceipt(
                schema=SOPERATOR_SOURCE_CACHE_SCHEMA,
                release=snapshot.release,
                commit=snapshot.commit,
                tree=snapshot.tree,
                archive_sha256=snapshot.archive_sha256,
                manifest_sha256=snapshot.source_manifest_sha256,
                source_dir=str(accepted_dir),
            )
            write_owner_only_json(_receipt_path(accepted_dir), asdict(receipt))
            return receipt
        finally:
            _remove_private_staging_tree(staging)


def acquire_soperator_release_source(
    metadata: SoperatorReleaseMetadata,
    *,
    cache_root: Path | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    limits: SoperatorArchiveLimits | None = None,
) -> SoperatorSourceReceipt:
    """Acquire a newly resolved release and bind the archive to its Git tree."""

    effective_limits = limits or SoperatorArchiveLimits()
    root = prepare_private_cache_root(
        (cache_root or default_soperator_source_cache_root()).expanduser()
    )
    with locked_cache_entry(root, metadata.commit):
        staging = Path(tempfile.mkdtemp(prefix=f".{metadata.release}-", dir=root))
        try:
            archive = staging / "release.tar.gz"
            _download_archive(metadata.archive_url, archive, limits=effective_limits, opener=opener)
            archive_sha256 = _sha256_file(
                archive,
                max_bytes=effective_limits.max_compressed_bytes,
            )
            accepted_dir = root / archive_sha256.removeprefix("sha256:")
            if accepted_dir.is_dir() and not accepted_dir.is_symlink():
                receipt_path = _receipt_path(accepted_dir)
                try:
                    receipt_payload = read_owner_only_json(
                        receipt_path,
                        label="cached Soperator source receipt",
                    )
                    if not isinstance(receipt_payload, Mapping):
                        raise TypeError("cached Soperator source receipt must be a mapping")
                    receipt = SoperatorSourceReceipt(**receipt_payload)
                    verify_soperator_source_git_tree(accepted_dir, metadata)
                    manifest_sha256, _ = normalized_tree_manifest(accepted_dir)
                    expected = SoperatorSourceReceipt(
                        schema=SOPERATOR_SOURCE_CACHE_SCHEMA,
                        release=metadata.release,
                        commit=metadata.commit,
                        tree=metadata.tree,
                        archive_sha256=archive_sha256,
                        manifest_sha256=manifest_sha256,
                        source_dir=str(accepted_dir),
                    )
                    if receipt == expected:
                        return receipt
                except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
                    pass
                _remove_private_staging_tree(accepted_dir)
                with suppress(OSError):
                    receipt_path.unlink()
            extracted = staging / "source"
            extract_soperator_release_archive(
                archive,
                extracted,
                expected_root=metadata.archive_root,
                expected_archive_sha256=archive_sha256,
                expected_manifest_sha256=None,
                limits=effective_limits,
            )
            verify_soperator_source_git_tree(extracted, metadata)
            manifest_sha256, _ = normalized_tree_manifest(extracted)
            os.replace(extracted, accepted_dir)
            _make_tree_read_only(accepted_dir)
            receipt = SoperatorSourceReceipt(
                schema=SOPERATOR_SOURCE_CACHE_SCHEMA,
                release=metadata.release,
                commit=metadata.commit,
                tree=metadata.tree,
                archive_sha256=archive_sha256,
                manifest_sha256=manifest_sha256,
                source_dir=str(accepted_dir),
            )
            write_owner_only_json(_receipt_path(accepted_dir), asdict(receipt))
            return receipt
        finally:
            _remove_private_staging_tree(staging)


__all__ = [
    "SOPERATOR_SOURCE_CACHE_SCHEMA",
    "SoperatorArchiveLimits",
    "SoperatorSourceReceipt",
    "acquire_soperator_release_source",
    "default_soperator_source_cache_root",
    "ensure_soperator_release_source",
    "extract_soperator_release_archive",
    "normalized_script_manifest",
    "normalized_tree_manifest",
]
