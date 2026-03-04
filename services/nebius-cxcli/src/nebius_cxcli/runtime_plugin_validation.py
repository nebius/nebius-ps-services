"""Runtime validation plugin loader for optional vendor/component rules."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

ValidationPlugin = Callable[..., None]
_DEFAULT_PLUGIN = "nebius_cxcli.runtime_component_validation:validate_component_runtime_rules"


def _plugin_specs_from_env() -> tuple[str, ...]:
    raw = os.environ.get("NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS", "").strip()
    if not raw:
        return ()
    if raw.lower() in {"0", "none", "off", "false"}:
        return ()
    specs = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(specs)


@lru_cache(maxsize=8)
def _load_plugins(specs: tuple[str, ...]) -> tuple[ValidationPlugin, ...]:
    plugins: list[ValidationPlugin] = []
    for spec in specs:
        if ":" not in spec:
            raise ValueError(
                "runtime validation plugin spec must use 'module.path:function_name' format"
            )
        module_name, function_name = spec.split(":", 1)
        module = importlib.import_module(module_name.strip())
        func = getattr(module, function_name.strip(), None)
        if not callable(func):
            raise ValueError(f"runtime validation plugin is not callable: {spec}")
        plugins.append(func)
    return tuple(plugins)


def run_runtime_validation_plugins(*, payload: dict[str, Any], **kwargs: Any) -> None:
    """Execute configured runtime validation plugins in declaration order."""
    specs = _plugin_specs_from_env()
    for plugin in _load_plugins(specs):
        plugin(payload, **kwargs)
