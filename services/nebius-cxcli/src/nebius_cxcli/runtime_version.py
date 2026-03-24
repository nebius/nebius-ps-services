"""Runtime version resolution for source and installed environments."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

_DIST_NAME = "nebius-cxcli"
_TAG_REGEX = r"^nebius-cxcli-v(?P<version>\d+\.\d+\.\d+)$"
_GIT_DESCRIBE_COMMAND = "git describe --dirty --tags --long --match nebius-cxcli-v*"
_UNKNOWN_VERSION = "0+unknown"


def _source_checkout_root() -> Path | None:
    service_root = Path(__file__).resolve().parents[2]
    if (service_root / "pyproject.toml").exists():
        return service_root
    return None


def _version_from_source_tree() -> str | None:
    source_root = _source_checkout_root()
    if source_root is None:
        return None
    try:
        from setuptools_scm import get_version
    except Exception:
        return None
    try:
        return get_version(
            root=str(source_root),
            version_scheme="python-simplified-semver",
            local_scheme="no-local-version",
            tag_regex=_TAG_REGEX,
            git_describe_command=_GIT_DESCRIBE_COMMAND,
            search_parent_directories=True,
        )
    except Exception:
        return None


def _version_from_metadata() -> str | None:
    try:
        return package_version(_DIST_NAME)
    except PackageNotFoundError:
        return None


def _version_from_generated_file() -> str | None:
    try:
        from ._version import version as generated_version
    except Exception:
        return None
    return str(generated_version)


def resolve_runtime_version() -> str:
    for resolver in (
        _version_from_source_tree,
        _version_from_metadata,
        _version_from_generated_file,
    ):
        resolved = resolver()
        if resolved:
            return resolved
    return _UNKNOWN_VERSION

