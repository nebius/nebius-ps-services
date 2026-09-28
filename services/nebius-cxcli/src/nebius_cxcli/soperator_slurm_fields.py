"""Parse native Slurm one-line records without executing Slurm commands."""

from __future__ import annotations

import re


def slurm_fields(line: str) -> dict[str, str]:
    """Retain spaces and full timestamps in native one-line fields."""
    return dict(
        re.findall(
            r"(?:^| +)([A-Za-z][A-Za-z0-9_:]*)=(.*?)(?= +[A-Za-z][A-Za-z0-9_:]*=|$)",
            line.strip(),
        )
    )
