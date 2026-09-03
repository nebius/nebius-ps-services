"""Content-free identity for one materialized Soperator jail/rootfs."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from .oci_image import is_immutable_oci_image_reference

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class RootfsEntry:
    """Non-secret identity for one materialized rootfs path."""

    path: str
    kind: str
    digest: str
    metadata_digest: str

    def __post_init__(self) -> None:
        normalized = _absolute_path(self.path, field="rootfs entry path")
        if normalized != self.path:
            raise ValueError(f"rootfs entry path must be normalized: {self.path!r}")
        if self.kind not in {"directory", "file", "symlink"}:
            raise ValueError(f"unsupported rootfs entry kind: {self.kind!r}")
        if not _SHA256.fullmatch(self.digest):
            raise ValueError("rootfs entry digest must be an exact SHA-256")
        if not _SHA256.fullmatch(self.metadata_digest):
            raise ValueError("rootfs entry metadata digest must be an exact SHA-256")


@dataclass(frozen=True)
class RootfsManifest:
    """Content-free inventory of one exact materialized rootfs slot."""

    image: str
    entries: tuple[RootfsEntry, ...]

    def __post_init__(self) -> None:
        if not is_immutable_oci_image_reference(self.image):
            raise ValueError("rootfs manifest image must be digest-addressed")
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("rootfs manifest entries must be unique and path-sorted")

    @property
    def manifest_sha256(self) -> str:
        return _stable_sha256(
            {"image": self.image, "entries": [asdict(entry) for entry in self.entries]}
        )


def rootfs_manifest(
    *, image: str, entries: Sequence[Mapping[str, object] | RootfsEntry]
) -> RootfsManifest:
    """Normalize a content-free rootfs inventory without persisting file contents."""

    normalized: list[RootfsEntry] = []
    for item in entries:
        if isinstance(item, RootfsEntry):
            entry = item
        elif isinstance(item, Mapping):
            entry = RootfsEntry(
                path=_absolute_path(item.get("path"), field="rootfs entry path"),
                kind=str(item.get("kind") or "").strip(),
                digest=str(item.get("digest") or "").strip(),
                metadata_digest=str(item.get("metadata_digest") or "").strip(),
            )
        else:
            raise ValueError("rootfs manifest entries must be mappings")
        normalized.append(entry)
    return RootfsManifest(
        image=str(image or "").strip(),
        entries=tuple(sorted(normalized, key=lambda item: item.path)),
    )


def _absolute_path(value: object, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("/") or "\x00" in raw:
        raise ValueError(f"{field} must be an absolute POSIX path")
    normalized = posixpath.normpath(raw)
    if normalized == "/" or normalized.startswith("/../"):
        raise ValueError(f"{field} must be below the rootfs root")
    return normalized


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["RootfsEntry", "RootfsManifest", "rootfs_manifest"]
