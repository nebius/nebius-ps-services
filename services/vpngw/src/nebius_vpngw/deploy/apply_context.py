"""Invocation-scoped selectors shared by apply's existing private effect owners."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..config_loader import load_local_config as read_config


@dataclass(frozen=True)
class Selectors:
    project: str | None = None
    region: str | None = None


SELECTORS: ContextVar[Selectors] = ContextVar("apply_selectors", default=Selectors())  # noqa: B039 - frozen immutable value


def select(config: dict[str, Any]) -> dict[str, Any]:
    selectors = SELECTORS.get()
    result = dict(config)
    if selectors.project is not None:
        result["project_id"] = selectors.project
    if selectors.region is not None:
        result["region_id"] = selectors.region
        if isinstance(result.get("gateway_group"), dict):
            result["gateway_group"] = {
                **result["gateway_group"],
                "region": selectors.region,
            }
    return result


def load_local_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if SELECTORS.get().project is not None:
        kwargs.setdefault("project_override", SELECTORS.get().project)
    if SELECTORS.get().region is not None:
        kwargs.setdefault("region_override", SELECTORS.get().region)
    return select(read_config(*args, **kwargs))


@contextmanager
def invocation(arguments: dict[str, Any]):
    token = SELECTORS.set(Selectors(arguments.get("project_id"), arguments.get("region")))
    try:
        yield
    finally:
        SELECTORS.reset(token)
