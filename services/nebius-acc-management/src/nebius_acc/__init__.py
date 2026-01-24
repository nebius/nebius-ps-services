from importlib import metadata

try:
    from ._version import version as __version__
except ImportError:  # Generated file is present only after building/installing
    try:
        __version__ = metadata.version("nebius-acc")
    except metadata.PackageNotFoundError:  # pragma: no cover - local dev
        __version__ = "0.0.0"

__all__ = ["__version__"]
