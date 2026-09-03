"""Durable first-seen identity ledger for official Soperator release tags."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .soperator_cache import locked_cache_entry, prepare_private_cache_root
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import SoperatorReleaseMetadata

SOPERATOR_RELEASE_IDENTITY_SCHEMA = "nebius-cxcli.soperator-release-identity.v1"


def default_soperator_release_identity_root() -> Path:
    """Return the user-private durable release-identity state directory."""

    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "nebius-cxcli" / "soperator" / "release-identities"


@dataclass(frozen=True)
class SoperatorReleaseIdentity:
    schema: str
    repository: str
    tag: str
    commit: str
    tree: str

    @classmethod
    def from_metadata(cls, metadata: SoperatorReleaseMetadata) -> SoperatorReleaseIdentity:
        return cls(
            schema=SOPERATOR_RELEASE_IDENTITY_SCHEMA,
            repository=metadata.repository,
            tag=metadata.tag,
            commit=metadata.commit,
            tree=metadata.tree,
        )


class SoperatorReleaseIdentityLedger:
    """Serialize and enforce first-seen repository/tag commit and tree identities."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = prepare_private_cache_root(
            (root or default_soperator_release_identity_root()).expanduser()
        )

    @staticmethod
    def _key(identity: SoperatorReleaseIdentity) -> str:
        material = f"{identity.repository}\0{identity.tag}".encode()
        return "release-" + hashlib.sha256(material).hexdigest()

    def _path(self, identity: SoperatorReleaseIdentity) -> Path:
        return self.root / f"{self._key(identity)}.json"

    @contextmanager
    def locked(self, metadata: SoperatorReleaseMetadata) -> Iterator[SoperatorReleaseIdentity]:
        identity = SoperatorReleaseIdentity.from_metadata(metadata)
        with locked_cache_entry(self.root, self._key(identity)):
            self.verify_existing(identity)
            yield identity

    def verify_existing(self, identity: SoperatorReleaseIdentity) -> None:
        path = self._path(identity)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("Soperator release identity record is unsafe")
        try:
            payload = read_owner_only_json(
                path,
                label="Soperator release identity record",
            )
            if not isinstance(payload, Mapping):
                raise TypeError("release identity payload must be a mapping")
            recorded = SoperatorReleaseIdentity(**payload)
        except RuntimeError as exc:
            raise ValueError("Soperator release identity record is unsafe") from exc
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Soperator release identity record is invalid") from exc
        if recorded != identity:
            raise RuntimeError(
                "official Soperator release tag identity changed after it was first verified; "
                "refusing the moved tag"
            )

    def record(self, identity: SoperatorReleaseIdentity) -> Path:
        """Publish the first fully verified observation without replacing prior history."""

        self.verify_existing(identity)
        path = self._path(identity)
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            return path
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        if not nofollow or not directory:
            raise RuntimeError("This platform cannot safely record Soperator release identity")
        parent_descriptor = os.open(
            self.root,
            os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        descriptor = -1
        try:
            descriptor = os.open(
                path.name,
                flags | nofollow,
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise ValueError("Soperator release identity record is unsafe")
                payload: Mapping[str, str] = asdict(identity)
                encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("could not write the Soperator release identity record")
                    remaining = remaining[written:]
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
                descriptor = -1
            os.fsync(parent_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_descriptor)
        return path


__all__ = [
    "SOPERATOR_RELEASE_IDENTITY_SCHEMA",
    "SoperatorReleaseIdentity",
    "SoperatorReleaseIdentityLedger",
    "default_soperator_release_identity_root",
]
