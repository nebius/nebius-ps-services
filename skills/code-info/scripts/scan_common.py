"""Shared read-only filesystem traversal helpers for code-info analysis."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

EXCLUDED_CODE_DIRS = {
    ".cache",
    ".git",
    ".gradle",
    ".hg",
    ".mypy_cache",
    ".next",
    ".nox",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".terraform",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "target",
    "vendor",
    "venv",
}
PACKAGE_MARKER_NAMES = {
    "Chart.yaml",
    "Cargo.toml",
    "Gemfile",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
}
PACKAGE_MARKER_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}


def iter_files(root: Path, excluded_dirs: set[str]) -> Iterator[Path]:
    """Yield regular, non-symlinked files beneath root in stable order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in excluded_dirs
            and not name.endswith(".egg-info")
            and not (Path(dirpath) / name).is_symlink()
        )
        base = Path(dirpath)
        for filename in sorted(filenames):
            path = base / filename
            if not path.is_symlink():
                yield path


def is_test_file(path: Path, root: Path) -> bool:
    lowered_name = path.name.lower()
    try:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        relative_parts = path.parts
    lowered_parts = {part.lower() for part in relative_parts}
    if lowered_parts & {"__tests__", "spec", "specs", "test", "tests"}:
        return True
    patterns = (
        "test_*.py",
        "*_test.py",
        "*_test.go",
        "*.test.*",
        "*.spec.*",
        "*tests.*",
    )
    return any(fnmatch.fnmatchcase(lowered_name, pattern) for pattern in patterns)


def package_markers(root: Path) -> list[Path]:
    return sorted(
        path
        for path in iter_files(root, EXCLUDED_CODE_DIRS)
        if path.name in PACKAGE_MARKER_NAMES or path.suffix in PACKAGE_MARKER_SUFFIXES
    )


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def format_int(value: int) -> str:
    return f"{value:,}"
