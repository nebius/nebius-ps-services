try:
    from ._version import version as __version__
except Exception:  # pragma: no cover - during local development
    __version__ = "0.0.0"

__all__ = ["__version__"]
