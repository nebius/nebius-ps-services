"""Runtime config wrappers and helpers for command execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class EnvString(str):
    """String wrapper exposing `.value` for call sites expecting enum-like env objects."""

    @property
    def value(self) -> str:
        return str(self)


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
    if path == ("client_info", "env") and isinstance(value, str):
        return EnvString(value)
    return value


def wrap_runtime_config(payload: Mapping[str, Any]) -> AttrDict:
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    wrapped = _wrap_runtime(dict(payload))
    if not isinstance(wrapped, AttrDict):
        raise ValueError("config.yaml root must be a mapping")
    return wrapped


def to_plain_data(value: Any) -> Any:
    """Convert runtime wrappers (AttrDict/EnvString) to plain Python data."""
    if isinstance(value, AttrDict):
        return {key: to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, EnvString):
        return str(value)
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
