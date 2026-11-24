"""Filesystem integrity primitives for trusted local training directories."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def tree_hash(path: Path) -> str:
    """Bind input bytes, excluding only Hugging Face's download cache."""
    digest = hashlib.sha256()
    count = 0
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        if relative.parts[0] == ".cache":
            continue
        if item.is_symlink():
            raise ValueError("Input artifacts must not contain symlinks")
        if item.is_file():
            digest.update(relative.as_posix().encode() + b"\0")
            digest.update(bytes.fromhex(file_hash(item)))
            count += 1
        elif not item.is_dir():
            raise ValueError("Input artifacts must be regular files")
    if not count:
        raise ValueError("Input artifact directory is empty")
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    """Commit a complete JSON record with a private temporary file."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def input_hash(path: Path, kind: str) -> str:
    """Enforce completion and integrity for downloader-managed asset bundles."""
    parent = path.parent
    if (parent / ".incomplete").exists():
        raise ValueError("Download bundle is incomplete")
    digest = tree_hash(path)
    manifest = parent / "manifest.json"
    if manifest.exists():
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("schema") != 1 or record.get(kind, {}).get("sha256") != digest:
            raise ValueError("Download manifest does not match input artifacts")
    return digest
