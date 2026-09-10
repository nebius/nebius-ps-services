"""Job-owned allocation directives around an unchanged upstream diagnostic."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SCRIPT_ANNOTATION = "cxcli.nebius.ai/acceptance-sbatch"
SCRIPT_VOLUME = {
    "name": "sbatch-volume",
    "downwardAPI": {
        "defaultMode": 493,
        "items": [
            {
                "path": "sbatch.sh",
                "fieldRef": {
                    "apiVersion": "v1",
                    "fieldPath": f"metadata.annotations['{SCRIPT_ANNOTATION}']",
                },
                "mode": 493,
            }
        ],
    },
}


def allocation_script(script: str, *, worker: str, gpu_count: int) -> str:
    """Insert supported sbatch options after the native directives, before code."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", worker) or gpu_count < 0:
        raise RuntimeError("invalid acceptance allocation")
    if not script.startswith("#!") or "\x00" in script or "\r" in script:
        raise RuntimeError("unsupported upstream sbatch script format")
    lines = script.splitlines(keepends=True)
    offset = next(
        (i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")),
        None,
    )
    if offset is None or any(
        re.match(r"\s*#SBATCH\s+(?:hetjob|:)(?:\s|$)", line) for line in lines[:offset]
    ):
        raise RuntimeError("unsupported upstream sbatch directive header")
    directives = f"#SBATCH --nodelist={worker}\n#SBATCH --nodes=1\n"
    if gpu_count:
        directives += f"#SBATCH --gpus-per-node={gpu_count}\n"
    result = "".join(lines[:offset]) + directives + "".join(lines[offset:])
    if len(result.encode()) > 65536:
        raise RuntimeError("acceptance sbatch script exceeds the annotation bound")
    return result


def projected_script(template: Mapping[str, Any]) -> str | None:
    """Executable identity includes the content of the exact downward projection."""
    annotations = template.get("metadata", {}).get("annotations", {})
    volumes = [
        v for v in template.get("spec", {}).get("volumes", []) if v.get("name") == "sbatch-volume"
    ]
    projected = any("downwardAPI" in v for v in volumes)
    if SCRIPT_ANNOTATION not in annotations and not projected:
        return None
    if len(volumes) != 1 or volumes[0] != SCRIPT_VOLUME:
        raise RuntimeError("acceptance script projection changed")
    script = annotations.get(SCRIPT_ANNOTATION)
    if not isinstance(script, str) or not script or len(script.encode()) > 65536:
        raise RuntimeError("acceptance script annotation is missing or invalid")
    return script
