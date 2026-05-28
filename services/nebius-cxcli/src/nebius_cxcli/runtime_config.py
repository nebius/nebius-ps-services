"""Runtime config wrappers and helpers for command execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AttrDict(dict[str, Any]):
    """Mapping with attribute access and hyphen/underscore key normalization."""

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("__"):
            if dict.__contains__(self, name):
                return dict.__getitem__(self, name)
            hyphen = name.replace("_", "-")
            if dict.__contains__(self, hyphen):
                return dict.__getitem__(self, hyphen)
            underscore = name.replace("-", "_")
            if dict.__contains__(self, underscore):
                return dict.__getitem__(self, underscore)
        return dict.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        # Runtime mode keeps optional fields permissive; missing values behave like None.
        return None


def _wrap_runtime(value: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        wrapped = AttrDict()
        for key, item in value.items():
            wrapped[key] = _wrap_runtime(item, path=path + (str(key),))
        return wrapped
    if isinstance(value, list):
        return [_wrap_runtime(item, path=path) for item in value]
    return value


def wrap_runtime_config(payload: Mapping[str, Any]) -> AttrDict:
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    wrapped = _wrap_runtime(dict(payload))
    if not isinstance(wrapped, AttrDict):
        raise ValueError("config.yaml root must be a mapping")
    return wrapped


def to_plain_data(value: Any) -> Any:
    """Convert runtime and YAML wrapper types to plain Python data."""
    if isinstance(value, Mapping):
        return {key: to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value


def read_path(payload: Mapping[str, Any], dotted_path: str) -> Any | None:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        candidates = [segment, segment.replace("-", "_"), segment.replace("_", "-")]
        matched = next((candidate for candidate in candidates if candidate in current), None)
        if matched is None:
            return None
        current = current[matched]
    return current


def _catalog_shared_payload() -> Mapping[str, Any]:
    from .component_sources import load_component_sources

    sources = load_component_sources()
    shared = to_plain_data(sources.shared)
    if not isinstance(shared, Mapping):
        return {}
    return {"shared": dict(shared)}


def read_path_with_catalog(payload: Mapping[str, Any], dotted_path: str) -> Any | None:
    value = read_path(payload, dotted_path)
    if value is not None:
        return value
    if not str(dotted_path).strip().startswith("shared."):
        return None
    return read_path(_catalog_shared_payload(), dotted_path)
