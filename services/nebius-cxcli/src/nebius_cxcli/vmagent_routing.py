"""Replace VMAgent destinations; never represent removal with disabled URLs."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

# These flags address a destination by position. Authentication/relabel/TLS now
# belongs to the corresponding remoteWrite object, never an inherited array.
_DESTINATION_PREFIXES = (
    "remoteWrite.basicAuth.",
    "remoteWrite.oauth2.",
    "remoteWrite.tls",
)
_DESTINATION_FLAGS = {
    "remoteWrite.url",
    "remoteWrite.bearerToken",
    "remoteWrite.bearerTokenFile",
    "remoteWrite.headers",
    "remoteWrite.proxyURL",
    "remoteWrite.urlRelabelConfig",
}


def replace_destinations(spec: dict[str, Any], destinations: Sequence[Mapping[str, Any]]) -> None:
    if not destinations or any(not item.get("url") for item in destinations):
        raise ValueError("VMAgent needs at least one complete write destination")
    previous_urls = [item.get("url") for item in spec.get("remoteWrite", [])]
    next_urls = [item["url"] for item in destinations]
    if len(set(next_urls)) != len(next_urls):
        raise ValueError("VMAgent write destinations must be unique")
    args = spec.setdefault("extraArgs", {})
    for key in list(args):
        if key in _DESTINATION_FLAGS or key.startswith(_DESTINATION_PREFIXES):
            args.pop(key)
        elif key.startswith("remoteWrite.") and previous_urls != next_urls:
            value = args[key]
            if isinstance(value, list) or (isinstance(value, str) and "," in value):
                raise ValueError(
                    f"Destination change requires explicit remapping of positional VMAgent option {key}; shared scalar settings are retained"
                )
    spec["remoteWrite"] = copy.deepcopy(list(destinations))


def assert_queue_identity_preserved(previous: Sequence[str], desired: Sequence[str]) -> None:
    """A removed URL must not silently reassign a retained URL's disk queue.

    Queue names contain both the flag's one-based index and a URL hash. Existing
    queues need an explicit drained/stopped handoff before their index can change.
    Fresh installations keep local first so ordinary local/both changes are safe.
    """
    for url in set(previous) & set(desired):
        if previous.index(url) != desired.index(url):
            raise RuntimeError(
                "VMAgent destination change moves a retained disk queue. Complete an explicit "
                "stopped-collector queue handoff before applying this routing change; no "
                "collector or queue was modified. Local-only desired configuration still "
                "contains only the internal remoteWrite URL."
            )
