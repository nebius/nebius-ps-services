"""nebius-cxcli package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

try:
    from ._version import version as __version__
except ImportError:  # pragma: no cover - fallback for local editable worktrees
    try:
        __version__ = package_version("nebius-cxcli")
    except PackageNotFoundError:
        __version__ = "0+unknown"

__all__ = ["__version__"]
