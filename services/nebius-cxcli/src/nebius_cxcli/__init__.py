"""nebius-cxcli package."""

from __future__ import annotations

try:
    from ._version import version as __version__
except ImportError:  # pragma: no cover - fallback for local editable worktrees
    __version__ = "0.0.0"

__all__ = ["__version__"]
