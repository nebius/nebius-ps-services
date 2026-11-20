"""Read installed release metadata without importing installed product code."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from pathlib import Path


def observe_installation() -> dict:
    """Fingerprint installed release evidence independently of transaction state."""
    versions = []
    try:
        for distribution in importlib.metadata.distributions():
            name = distribution.metadata["Name"] or ""
            if re.sub(r"[-_.]+", "-", name).lower() == "nebius-vpngw":
                versions.append(distribution.version)
        if len(versions) == 1 and isinstance(versions[0], str) and versions[0]:
            return {"state": "present", "version": versions[0]}
        if versions or importlib.util.find_spec("nebius_vpngw") is not None:
            return {"state": "unknown", "version": None}
        if Path("/etc/nebius-vpngw/config-resolved.yaml").exists():
            return {"state": "unknown", "version": None}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
        return {"state": "unknown", "version": None}
    return {"state": "absent", "version": None}


def observe() -> dict:
    """Keep an incomplete transaction fail-closed at release admission."""
    installed = observe_installation()
    if installed["state"] == "absent":
        try:
            if Path("/var/lib/nebius-vpngw/runtime-migration/operation.json").exists():
                return {"state": "unknown", "version": None}
        except OSError:
            return {"state": "unknown", "version": None}
    return installed
