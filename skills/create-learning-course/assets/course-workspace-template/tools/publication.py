"""Portable, offline publication helpers; rendering remains project-owned."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path


def format_mb(size: int, *, precision: int = 2) -> str:
    """Format decimal megabytes for display; budgets still compare exact bytes."""
    return f"{size / 1_000_000:,.{precision}f} MB"


def relative_path(relative: str) -> Path:
    """Validate a relative name without consulting a filesystem namespace."""
    if not isinstance(relative, str):
        raise ValueError("Publication paths must be strings")
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or ":" in relative
        or path.as_posix() != relative
        or relative == "."
        or any(ord(c) < 32 for c in relative)
    ):
        raise ValueError(f"Unsafe publication path: {relative!r}")
    return path


def validate_root(root: Path) -> None:
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Symlinked publication root")
    if not root.is_dir():
        raise ValueError("Publication root must exist")


def contained_path(root: Path, relative: str) -> Path:
    """Reject ambiguous paths and linked input/output components."""
    path = relative_path(relative)
    root = root.absolute()
    target = root / path
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError(f"Symlinked publication path: {relative}")
    if any(p.exists() and not p.is_dir() for p in target.parents):
        raise ValueError(f"Non-directory publication ancestor: {relative}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Out-of-root publication path: {relative}")
    return target


def git_sizes(root: Path) -> dict[str, int]:
    """Conservative branch-publication inventory; never include Git history."""
    validate_root(root)

    def git(*args: str) -> bytes:
        try:
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.PIPE
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("Cannot inventory publication root using Git") from error

    top = Path(os.fsdecode(git("rev-parse", "--show-toplevel").strip())).resolve()
    if top != root.resolve():
        raise ValueError("Git publication inventory requires the repository root")
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if entry.startswith(b"160000 "):
            raise ValueError(
                "Submodule publication inventory is unsupported; cannot establish complete size"
            )
    paths = set(
        git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    ) - {b""}
    sizes = {}
    for raw in sorted(paths):
        name = os.fsdecode(raw)
        path = contained_path(root, name)
        try:
            info = path.stat()
        except FileNotFoundError:
            continue  # Deleted tracked files are absent from the candidate tree.
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Non-regular publication input: {name}")
        sizes[name] = info.st_size
    return sizes


def directory_sizes(root: Path) -> dict[str, int]:
    """Inventory a dedicated staged publication directory, without exclusions."""
    validate_root(root)
    sizes = {}

    def fail(error: OSError) -> None:
        raise error

    for directory, directories, files in os.walk(root, followlinks=False, onerror=fail):
        for name in directories + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            contained_path(root, relative)
            if path.is_file():
                sizes[relative] = path.stat().st_size
            elif not path.is_dir():
                raise ValueError(f"Non-regular publication input: {relative}")
    return sizes


def check_budget(
    root: Path,
    outputs: dict[str, bytes],
    *,
    max_file_bytes: int | None,
    max_site_bytes: int | None,
    inventory: str = "git",
) -> dict:
    validate_root(root)
    if inventory not in {"git", "directory"}:
        raise ValueError("Publication inventory must be git or directory")
    for limit in (max_file_bytes, max_site_bytes):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Publication limits must be positive byte counts or None")
    planned = {contained_path(root, name) for name in outputs}
    for path in planned:
        if any(parent in planned for parent in path.parents):
            raise ValueError("Planned output paths conflict as file and directory")
        if path.exists() and not path.is_file():
            raise ValueError(
                f"Output destination is not a regular file: {path.relative_to(root)}"
            )
    sizes = git_sizes(root) if inventory == "git" else directory_sizes(root)
    for name, content in outputs.items():
        contained_path(root, name)
        if not isinstance(content, bytes):
            raise ValueError("Planned publication outputs must be bytes")
        sizes[name] = len(content)
    total = sum(sizes.values())
    largest = max(sizes, key=sizes.get) if sizes else None
    over = [
        f"{name}: {format_mb(size)} "
        f"(over by {format_mb(size - max_file_bytes, precision=6)})"
        for name, size in sorted(sizes.items())
        if max_file_bytes is not None and size > max_file_bytes
    ]
    if over:
        raise ValueError(
            f"File limit {format_mb(max_file_bytes)} exceeded: " + "; ".join(over)
        )
    if max_site_bytes is not None and total > max_site_bytes:
        raise ValueError(
            f"Site limit {format_mb(max_site_bytes)} exceeded: {format_mb(total)} "
            f"(over by {format_mb(total - max_site_bytes, precision=6)})"
        )
    return {
        "total_bytes": total,
        "largest_path": largest,
        "largest_bytes": sizes.get(largest, 0),
        "max_file_bytes": max_file_bytes,
        "max_site_bytes": max_site_bytes,
        "headroom_bytes": None if max_site_bytes is None else max_site_bytes - total,
    }


def results_zip(
    root: Path, members: dict[str, str], *, max_file_bytes: int | None = None
) -> bytes:
    """Archive explicitly selected files; keys are member names, values source paths."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(members.items()):
            relative_path(name)
            path = contained_path(root, source)
            if not path.is_file():
                raise ValueError(f"Missing archive source: {source}")
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            archive.writestr(info, path.read_bytes())
    content = output.getvalue()
    if max_file_bytes is not None and len(content) > max_file_bytes:
        raise ValueError(
            f"Results archive exceeds {format_mb(max_file_bytes)}: {format_mb(len(content))} "
            f"(over by {format_mb(len(content) - max_file_bytes, precision=6)})"
        )
    return content


def write_atomic(path: Path, content: bytes) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Generated destination must not use symlinks")
    if path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".course-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
