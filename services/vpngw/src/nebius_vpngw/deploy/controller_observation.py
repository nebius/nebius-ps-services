"""Semantic controller progress, never promotion or recovery authority."""

from __future__ import annotations

from ..agent.vm_ha.progress import validate_transfer_progress
from ..apply_approval import digest


def progress_key(snapshot) -> str:
    members = []
    for member in snapshot.members:
        record = member.record or {}
        transfer = record.get("transfer_progress")
        events = None
        if transfer is not None:
            transfer = validate_transfer_progress(transfer)
            identity = {
                "cluster_id": "cluster_id",
                "candidate_node_id": "node_id",
                "allocation_id": "allocation_id",
                "generation_id": "generation_id",
                "route_runtime_id": "route_runtime_id",
                "digests": "digests",
            }
            if any(transfer[key] != record.get(field) for key, field in identity.items()):
                raise ValueError("controller-progress-identity-conflict")
            # Repeated timestamps, heartbeats, retries and sequence numbers alone
            # do not extend observation. Each semantic transition counts once.
            events = sorted(
                {
                    (event["action"], event["operation_id"], event["state"])
                    for event in transfer["history"]
                }
            )
        repair = record.get("repair")
        members.append(
            {
                "node": member.node_id,
                "state": record.get("state"),
                "pending": record.get("pending_operation_id"),
                "rearm": record.get("rearm_phase"),
                "generation": record.get("generation_id"),
                "transfer": events,
                "repair": {
                    key: repair.get(key)
                    for key in (
                        "operation_id",
                        "healthy_observations",
                    )
                }
                if isinstance(repair, dict)
                else None,
            }
        )
    return digest(
        {
            "overall": snapshot.view.overall,
            "action": snapshot.view.action,
            "authority": snapshot.authority_digest,
            "members": members,
        }
    )
