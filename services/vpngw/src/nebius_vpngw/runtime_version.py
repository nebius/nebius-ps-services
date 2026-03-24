"""Runtime version resolution for source and installed environments."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

_DIST_NAME = "nebius-vpngw"
_TAG_REGEX = r"^nebius-vpngw-v(?P<version>\d+\.\d+\.\d+)$"
_GIT_DESCRIBE_COMMAND = "git describe --dirty --tags --long --abbrev=40 --match nebius-vpngw-v*"
_UNKNOWN_VERSION = "0.0.0"


def _service_root() -> Path | None:
    service_root = Path(__file__).resolve().parents[2]
    if (service_root / "pyproject.toml").exists():
        return service_root
    return None


def _repo_root() -> Path | None:
    service_root = _service_root()
    if service_root is None:
        return None
    repo_root = service_root.parents[1]
    if (repo_root / ".git").exists():
        return repo_root
    return None


def _version_from_source_tree() -> str | None:
    repo_root = _repo_root()
    if repo_root is None:
        return None
    try:
        from setuptools_scm import get_version
    except Exception:
        return None
    try:
        return get_version(
            root=str(repo_root),
            version_scheme="python-simplified-semver",
            local_scheme="no-local-version",
            tag_regex=_TAG_REGEX,
            git_describe_command=_GIT_DESCRIBE_COMMAND,
            fallback_version=_UNKNOWN_VERSION,
        )
    except Exception:
        return None


def _version_from_metadata() -> str | None:
    try:
        return metadata.version(_DIST_NAME)
    except metadata.PackageNotFoundError:
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

