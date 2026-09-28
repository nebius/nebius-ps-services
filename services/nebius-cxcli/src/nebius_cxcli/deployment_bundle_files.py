"""Read target-local files from an immutable execution snapshot."""

from __future__ import annotations

import base64

from .deployment_state import DeploymentGeneration


def target_files(generation: DeploymentGeneration, ref: str) -> dict[str, bytes]:
    rows = [r for r in generation.manifest["deploy"]["targets"] if r["target_ref"] == ref]
    if len(rows) != 1:
        raise RuntimeError("Observability recovery target is ambiguous")
    prefix = rows[0]["flux_dir"].rstrip("/") + "/"
    return {
        k.removeprefix(prefix): base64.b64decode(v, validate=True)
        for k, v in generation.files.items()
        if k.startswith(prefix)
    }
