"""Built-in cluster handoff contracts used by deploy/bootstrap flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

AccessKind = Literal["input", "literal"]


@dataclass(frozen=True)
class Handoff:
    cluster_id_output_name: str
    access_kind: AccessKind
    access_source_path: str = ""
    access_value: Any = None


_BUILTIN_HANDOFFS: dict[str, Handoff] = {
    "mk8s": Handoff(
        cluster_id_output_name="cluster_id",
        access_kind="input",
        access_source_path="inputs.mk8s_cluster_public_endpoint",
    ),
}


def resolve_builtin_handoff(component_id: str) -> Handoff | None:
    return _BUILTIN_HANDOFFS.get(str(component_id).strip().lower())
