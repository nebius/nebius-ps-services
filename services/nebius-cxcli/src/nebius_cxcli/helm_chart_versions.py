"""Exact chart selectors shared by catalog validation and rendering."""

from __future__ import annotations

import re

_EXACT_VERSION = re.compile(
    r"v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def exact_helm_chart_version(value: str) -> str:
    """Reject selectors that Helm can resolve to different chart versions."""
    version = value.strip()
    match = _EXACT_VERSION.fullmatch(version)
    if match is not None:
        prerelease = match.group("prerelease") or ""
        if not any(
            part.isdigit() and len(part) > 1 and part.startswith("0")
            for part in prerelease.split(".")
        ):
            return version
    raise ValueError(
        "configure an exact chart version (X.Y.Z); "
        "latest, ranges, wildcards, and incomplete versions are not allowed"
    )
