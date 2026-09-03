"""Owner-only, symlink-safe local I/O for Soperator authority receipts."""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any


def _open_private_parent(path: Path, *, create: bool, label: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise RuntimeError(f"{label} directory cannot be opened safely on this platform")
    flags = os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)
    if path.is_absolute():
        descriptor = os.open(path.anchor, flags)
        components = path.parts[1:]
    else:
        descriptor = os.open(".", flags)
        components = path.parts
    try:
        for component in components:
            if component in {"", "."}:
                continue
            if component == "..":
                raise RuntimeError(f"{label} directory is not safe")
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                with suppress(FileExistsError):
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        os.close(descriptor)
        raise RuntimeError(f"{label} directory is not safe") from exc
    except Exception:
        os.close(descriptor)
        raise
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
    ):
        os.close(descriptor)
        raise RuntimeError(f"{label} directory is not safe")
    return descriptor


def read_owner_only_json(path: Path, *, label: str) -> object:
    """Read JSON from one owner-only regular file without following path symlinks."""

    if path.name in {"", ".", ".."}:
        raise RuntimeError(f"{label} path is invalid")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError(f"{label} cannot be opened safely on this platform")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    parent_descriptor = _open_private_parent(path.parent, create=False, label=label)
    try:
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise RuntimeError(f"{label} is not an owner-only regular file") from exc
    finally:
        os.close(parent_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise RuntimeError(f"{label} is not an owner-only regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def write_owner_only_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish JSON through an exclusive owner-only temporary file."""

    if path.name in {"", ".", ".."}:
        raise RuntimeError("Soperator receipt path is invalid")
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode()
    parent_descriptor = _open_private_parent(
        path.parent,
        create=True,
        label="Soperator receipt",
    )
    os.fchmod(parent_descriptor, 0o700)
    descriptor = -1
    temporary_name = ""
    try:
        for _attempt in range(16):
            temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_CREAT
                    | os.O_EXCL
                    | os.O_WRONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not allocate a private Soperator receipt file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = ""
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.close(parent_descriptor)


__all__ = ["read_owner_only_json", "write_owner_only_json"]
