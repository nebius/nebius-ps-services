from .runtime_version import resolve_runtime_version

__version__ = resolve_runtime_version()

__all__ = [
    "__version__",
]
