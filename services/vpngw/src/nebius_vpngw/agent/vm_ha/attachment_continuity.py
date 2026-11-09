"""Exact spec-revision continuity for an approved attachment-only update.

This never changes a raw Compute CAS revision or grants promotion authority.
Positive, strictly increasing integer revisions make a one-step transition an
unambiguous single spec change. Larger jumps deliberately receive no continuity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...deploy.replacement_cloud import message_digest
from .inhibition import standby_replacement_inhibition_operation_id
from .store import atomic_write_json

FILENAME = "attachment-ownership-continuity.json"
SCHEMA = "nebius-vpngw/attachment-ownership-continuity-v1"
_FIELDS = {
    "schema",
    "cluster_id",
    "generation_id",
    "owner_node_id",
    "compute_id",
    "operation_id",
    "before_revision",
    "after_revision",
    "before_spec",
    "after_spec",
}


def validate(value: object, *, receipt: bool = False) -> dict[str, str]:
    fields = _FIELDS | ({"ownership_epoch"} if receipt else set())
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or not all(isinstance(item, str) and item for item in value.values())
    ):
        raise ValueError("Attachment continuity has invalid fields")
    if value["schema"] != SCHEMA or any(
        re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
        for key in ("generation_id", "operation_id", "before_spec", "after_spec")
    ):
        raise ValueError("Attachment continuity identity is invalid")
    revisions = [value["before_revision"], value["after_revision"]]
    if receipt:
        revisions.append(value["ownership_epoch"])
    if any(re.fullmatch(r"[1-9][0-9]*", item) is None for item in revisions):
        raise ValueError("Attachment continuity requires positive canonical revisions")
    if (
        int(revisions[1]) != int(revisions[0]) + 1
        or (receipt and int(value["ownership_epoch"]) > int(revisions[0]))
        or value["before_spec"] == value["after_spec"]
    ):
        raise ValueError("Attachment continuity requires exactly one spec change")
    return value


def _load(path: Path) -> dict[str, str] | None:
    if path.is_symlink():
        raise ValueError("Attachment continuity cannot be a symlink")
    if not path.exists():
        return None
    return validate(json.loads(path.read_text()), receipt=True)


def epoch_for(
    *,
    path: Path,
    cluster_id: str,
    generation_id: str,
    compute_id: str,
    revision: str,
    spec_digest: str,
) -> str:
    """Retain an established epoch only for the exact prepared before/after spec."""
    record = _load(path)
    if record is None or any(
        record[key] != expected
        for key, expected in (
            ("cluster_id", cluster_id),
            ("generation_id", generation_id),
            ("compute_id", compute_id),
        )
    ):
        return revision
    if (revision, spec_digest) in {
        (record["before_revision"], record["before_spec"]),
        (record["after_revision"], record["after_spec"]),
    }:
        return record["ownership_epoch"]
    return revision


def observe_epoch(instance: Any, *, state_dir: Path, binding: Any) -> str:
    record = _load(state_dir / FILENAME)
    if record is not None and not any(
        node.node_id == record["owner_node_id"] and node.compute_id == record["compute_id"]
        for node in binding.nodes
    ):
        return str(instance.metadata.resource_version)
    return epoch_for(
        path=state_dir / FILENAME,
        cluster_id=binding.cluster_id,
        generation_id=binding.generation_id,
        compute_id=str(instance.metadata.id),
        revision=str(instance.metadata.resource_version),
        spec_digest=message_digest(instance.spec),
    )


def prepare(
    proposal: dict[str, str],
    *,
    config: Mapping[str, Any],
    state_dir: Path,
    boot_id: str,
) -> dict[str, str]:
    """Install command-bound continuity on an already inhibited, unchanged guest."""
    from ...schema import VMHARuntimeBinding
    from ..main import _strict_apply_lock_record

    validate(proposal)
    binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])
    node_id = config["node"]["node_id"]
    owner = next(
        (node for node in binding.nodes if node.node_id == proposal["owner_node_id"]), None
    )
    if not (
        binding.cluster_id == proposal["cluster_id"]
        and binding.generation_id == proposal["generation_id"]
        and owner is not None
        and owner.compute_id == proposal["compute_id"]
    ):
        raise ValueError("Attachment continuity differs from installed member authority")
    status = json.loads((state_dir / "status.json").read_text())
    if not (
        status.get("guard_boot_id") == boot_id
        and status.get("cluster_id") == binding.cluster_id
        and status.get("node_id") == node_id
        and status.get("generation_id") == binding.generation_id
        and status.get("observed_owner_node_id") == owner.node_id
        and status.get("pending_operation_id") is None
    ):
        raise RuntimeError("Attachment continuity requires current quiescent guest identity")
    if node_id == owner.node_id:
        # The caller already verifies the exact canonical owner gate through the
        # installed command. Recheck the same authority, including fresh status.
        if not (
            status.get("data_plane_mode") == "active"
            and status.get("apply_locked") is False
            and status.get("transfer_inhibition_operation_id") == proposal["operation_id"]
            and status.get("transfer_inhibition_quiescent") is True
            and standby_replacement_inhibition_operation_id(
                state_dir,
                cluster_id=binding.cluster_id,
                node_id=node_id,
                generation_id=binding.generation_id,
            )
            == proposal["operation_id"]
        ):
            raise RuntimeError("Attachment continuity requires inhibited serving owner")
    elif (
        _strict_apply_lock_record(
            state_dir / "apply.lock",
            cluster_id=binding.cluster_id,
            node_id=node_id,
            generation_id=binding.generation_id,
        )
        != proposal["operation_id"]
    ):
        raise RuntimeError("Attachment continuity requires exact passive apply lock")
    path = state_dir / FILENAME
    previous = _load(path)
    if previous is not None and previous["operation_id"] == proposal["operation_id"]:
        if {key: value for key, value in previous.items() if key != "ownership_epoch"} != proposal:
            raise RuntimeError("Attachment continuity retry changed its prepared intent")
        if (
            node_id == owner.node_id
            and status.get("ownership_epoch") != previous["ownership_epoch"]
        ):
            raise RuntimeError("Attachment continuity retry lost established owner authority")
        return previous
    epoch = epoch_for(
        path=path,
        cluster_id=binding.cluster_id,
        generation_id=binding.generation_id,
        compute_id=proposal["compute_id"],
        revision=proposal["before_revision"],
        spec_digest=proposal["before_spec"],
    )
    if node_id == owner.node_id and status.get("ownership_epoch") != epoch:
        raise RuntimeError("Attachment continuity cannot adopt an unestablished owner epoch")
    result = {**proposal, "ownership_epoch": epoch}
    validate(result, receipt=True)
    atomic_write_json(path, result)
    return result
