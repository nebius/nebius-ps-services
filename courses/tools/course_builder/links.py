"""Shared navigation attributes for static course publications."""

from __future__ import annotations

import html


def link_attributes(href: str, *, download: bool = False) -> str:
    """Keep reading in place when following another page, without scripting."""
    navigation = (
        'target="_blank" rel="noopener noreferrer" '
        if href and not href.startswith("#") and not download
        else ""
    )
    return f'{navigation}href="{html.escape(href, quote=True)}"'
