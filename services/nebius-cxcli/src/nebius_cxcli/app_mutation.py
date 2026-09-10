"""Command-scoped authority for ordinary app mutation boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_AUTHORITY: ContextVar[Callable[[], object] | None] = ContextVar(
    "app_mutation_authority", default=None
)
_MANIFEST_GUARD: ContextVar[Callable[[Mapping[str, Any]], dict[str, Any] | None] | None] = (
    ContextVar("app_manifest_guard", default=None)
)


def assert_app_mutation_authority() -> None:
    authority = _AUTHORITY.get()
    if authority is not None:
        authority()


def guarded_app_manifest(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    assert_app_mutation_authority()
    guard = _MANIFEST_GUARD.get()
    return guard(manifest) if guard is not None else dict(manifest)


@contextmanager
def app_mutation_scope(
    authority: Callable[[], object],
    manifest_guard: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None,
) -> Iterator[None]:
    authority_token = _AUTHORITY.set(authority)
    guard_token = _MANIFEST_GUARD.set(manifest_guard or _MANIFEST_GUARD.get())
    try:
        yield
    finally:
        _MANIFEST_GUARD.reset(guard_token)
        _AUTHORITY.reset(authority_token)
