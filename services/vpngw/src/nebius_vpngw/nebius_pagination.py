"""Fail-closed synchronous pagination for Nebius collection reads."""

from __future__ import annotations

import re
import typing as t
from collections.abc import Callable, Iterable

DEFAULT_MAX_PAGES = 1000
_SAFE_CONTEXT = re.compile(r"[A-Za-z][A-Za-z0-9 -]{0,63}\Z")


class NebiusPaginationError(RuntimeError):
    """A complete Nebius collection could not be proven."""


def nebius_resource_id(resource: object) -> str | None:
    """Return a stable generated-resource ID when it is present."""

    metadata = getattr(resource, "metadata", None)
    value = getattr(metadata, "id", None)
    if isinstance(value, str) and value:
        return value
    value = getattr(resource, "id", None)
    return value if isinstance(value, str) and value else None


def _unavailable(context: str, error: Exception | None = None) -> t.NoReturn:
    message = f"{context} inventory is unavailable"
    if error is None:
        raise NebiusPaginationError(message)
    raise NebiusPaginationError(message) from error


def _wait(value: object) -> object:
    wait = getattr(value, "wait", None)
    return wait() if callable(wait) else value


def collect_nebius_pages(
    fetch_page: Callable[[str], object],
    *,
    context: str,
    items_field: str = "items",
    item_identity: Callable[[object], str | None] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[object, ...]:
    """Return one complete collection or fail without exposing partial items.

    ``fetch_page`` owns the provider request so every domain-specific parent,
    filter, page size, and retry option remains explicit at the call site.
    """

    if _SAFE_CONTEXT.fullmatch(context) is None:
        raise ValueError("pagination context must be a fixed safe label")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if not isinstance(items_field, str) or not items_field:
        raise ValueError("items_field must be a non-empty string")

    buffered: list[object] = []
    page_token = ""
    seen_tokens = {page_token}
    seen_identities: set[str] = set()
    missing = object()

    for _page_number in range(max_pages):
        try:
            response = _wait(fetch_page(page_token))
        except Exception as error:
            _unavailable(context, error)

        try:
            raw_items = getattr(response, items_field, missing)
            next_page_token = getattr(response, "next_page_token", missing)
        except Exception as error:
            _unavailable(context, error)
        if raw_items is missing or next_page_token is missing:
            _unavailable(context)
        try:
            if isinstance(raw_items, (str, bytes, bytearray, dict)) or not isinstance(
                raw_items, Iterable
            ):
                _unavailable(context)
            page_items = tuple(raw_items)
        except NebiusPaginationError:
            raise
        except Exception as error:
            _unavailable(context, error)

        if item_identity is not None:
            for item in page_items:
                try:
                    identity = item_identity(item)
                except Exception as error:
                    _unavailable(context, error)
                if identity is None:
                    continue
                if not isinstance(identity, str) or not identity:
                    _unavailable(context)
                if identity in seen_identities:
                    _unavailable(context)
                seen_identities.add(identity)

        if not isinstance(next_page_token, str):
            _unavailable(context)
        buffered.extend(page_items)
        if not next_page_token:
            return tuple(buffered)
        if next_page_token in seen_tokens:
            _unavailable(context)
        seen_tokens.add(next_page_token)
        page_token = next_page_token

    _unavailable(context)
