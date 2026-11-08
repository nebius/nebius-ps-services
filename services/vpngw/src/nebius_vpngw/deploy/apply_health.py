"""Configuration convergence is independent of remote VPN coverage.

This projection grants no forwarding or configuration-writer authority. It is
used only to decide whether apply has proven local drift, or must remain idle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from .apply_plan import Evidence


@dataclass(frozen=True)
class HAConvergence:
    local_runtime: Evidence
    connectivity: Evidence
    identity: str


def ha_convergence(snapshot: Any) -> HAConvergence:
    authority = snapshot.authority
    if (
        authority.lifecycle != "active"
        or authority.ownership_condition != "exact"
        or not authority.ownership_digest
        or authority.operation_id is not None
        or len(authority.member_compute_states) != 2
        or any(state != "running" for _node, state in authority.member_compute_states)
    ):
        raise RuntimeError("Apply convergence is blocked by incomplete ownership evidence")
    records = []
    policies = set()
    local = Evidence.READY
    coverage = Evidence.READY
    for member in snapshot.members:
        record = member.record
        if member.condition != "exact" or not isinstance(record, dict):
            raise RuntimeError("Apply convergence is blocked by incomplete member evidence")
        projection = record.get("apply_convergence")
        if (
            not isinstance(projection, dict)
            or set(projection)
            != {
                "boot_id",
                "observed_at",
                "data_plane_mode",
                "local_runtime",
                "authority_ready",
                "quiescent",
                "coverage",
                "fresh",
            }
            or projection["fresh"] is not True
            or projection["authority_ready"] is not True
            or projection["quiescent"] is not True
            or projection["boot_id"] != record.get("guard_boot_id")
            or not isinstance(projection["boot_id"], str)
            or not projection["boot_id"]
            or projection["data_plane_mode"] != record.get("data_plane_mode")
            or record.get("observed_owner_node_id") != authority.owner_node_id
            or record.get("pending_operation_id") is not None
            or record.get("apply_locked") is not False
            or record.get("transfer_inhibition_operation_id") is not None
            or projection["local_runtime"] not in {"ready", "degraded", "unknown"}
            or projection["coverage"] not in {"ready", "degraded", "unknown"}
        ):
            raise RuntimeError("Apply convergence is blocked by stale or incomplete guard evidence")
        mode = "active" if member.node_id == authority.owner_node_id else "passive"
        if projection["data_plane_mode"] != mode:
            raise RuntimeError("Apply convergence has conflicting owner forwarding evidence")
        healing, mtls, heartbeat = (
            record.get("auto_healing") or {},
            record.get("mtls") or {},
            record.get("heartbeat") or {},
        )
        if (
            healing.get("state") not in {"enabled", "disabled"}
            or healing.get("peer_agrees") is not True
            or healing.get("accepted_start") is not False
            or mtls.get("state") != "healthy"
            or mtls.get("operation_id") is not None
            or mtls.get("inhibited") is not False
            or heartbeat.get("peer_v6_fresh") is not True
            or heartbeat.get("peer_health_fresh") is not True
        ):
            raise RuntimeError("Apply convergence is blocked by incomplete peer or policy evidence")
        policies.add(healing["state"])
        if projection["local_runtime"] == "unknown":
            raise RuntimeError("Apply local runtime evidence is unknown; no repair is admitted")
        if projection["local_runtime"] == "degraded":
            local = Evidence.DEGRADED
        if projection["coverage"] == "unknown":
            coverage = Evidence.UNKNOWN
        elif projection["coverage"] == "degraded" and coverage is Evidence.READY:
            coverage = Evidence.DEGRADED
        records.append(
            {
                "node": member.node_id,
                "boot": projection["boot_id"],
                "mode": mode,
                "generation": record.get("generation_id"),
                "digests": record.get("digests"),
                "policy": healing["state"],
                "local_runtime": projection["local_runtime"],
                "mtls_epoch": mtls.get("epoch"),
                "mtls_fingerprint": mtls.get("certificate_fingerprint"),
            }
        )
    if len(records) != 2 or len(policies) != 1:
        raise RuntimeError("Apply convergence requires agreement from both members")
    return HAConvergence(
        local,
        coverage,
        digest(
            {
                "ownership": authority.ownership_digest,
                "members": records,
                "lifecycle": snapshot.lifecycle_state.record_sha256,
            }
        ),
    )
