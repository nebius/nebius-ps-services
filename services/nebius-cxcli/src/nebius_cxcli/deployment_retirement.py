"""Exact whole-group retirement membership and provider capacity freezing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def retirement_members(
    nodes: Sequence[Mapping[str, Any]], *, group_ids: Mapping[str, str], counts: Mapping[str, int]
) -> dict[str, tuple[tuple[str, str], ...]]:
    if set(counts) != set(group_ids) or len(set(group_ids.values())) != len(group_ids):
        raise RuntimeError("Retirement capacity ownership is incomplete or ambiguous")
    members: dict[str, list[tuple[str, str]]] = {key: [] for key in group_ids}
    keys_by_id = {value: key for key, value in group_ids.items()}
    seen_names: set[str] = set()
    seen_uids: set[str] = set()
    for node in nodes:
        metadata = node.get("metadata", {})
        labels = metadata.get("labels", {})
        provider_id = labels.get("nebius.com/node-group-id")
        key = keys_by_id.get(provider_id)
        if key is None:
            # A descriptive key is never a substitute for provider membership.
            if labels.get("nebius.com/node-group") in group_ids:
                raise RuntimeError("Retirement node lacks exact provider group identity")
            continue
        name, uid = str(metadata.get("name") or ""), str(metadata.get("uid") or "")
        if not name or not uid or name in seen_names or uid in seen_uids:
            raise RuntimeError("Retirement node identity is missing or duplicated")
        if metadata.get("deletionTimestamp"):
            raise RuntimeError("Retirement membership is changing; retry after it stabilizes")
        seen_names.add(name)
        seen_uids.add(uid)
        members[key].append((name, uid))
    for key, count in counts.items():
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or len(members[key]) != count
        ):
            raise RuntimeError("Retirement cannot prove every member of the affected group")
    return {key: tuple(sorted(rows)) for key, rows in members.items()}


def freeze_capacity(sdk: Any, *, provider_id: str, count: int) -> None:
    """Optimistic update of an autoscaling group's bounds to its observed size."""
    from nebius.api.nebius.mk8s.v1 import (
        GetNodeGroupRequest,
        NodeGroupAutoscalingSpec,
        NodeGroupServiceClient,
        NodeGroupSpec,
        UpdateNodeGroupRequest,
    )

    client = NodeGroupServiceClient(sdk)
    group = client.get(GetNodeGroupRequest(id=provider_id)).wait()
    metadata, spec = group.metadata, group.spec
    if str(metadata.id) != provider_id or not metadata.resource_version:
        raise RuntimeError("Retirement capacity freeze lost provider identity")
    scaling = getattr(spec, "autoscaling", None)
    if scaling is None:
        if spec.fixed_node_count != count:
            raise RuntimeError("Retirement fixed capacity changed outside its admission")
        return
    if scaling.min_node_count == count and scaling.max_node_count == count:
        return
    client.update(
        UpdateNodeGroupRequest(
            metadata=metadata,
            spec=NodeGroupSpec(
                spec,
                autoscaling=NodeGroupAutoscalingSpec(min_node_count=count, max_node_count=count),
            ),
        )
    ).wait()
    current = client.get(GetNodeGroupRequest(id=provider_id)).wait()
    observed = current.spec.autoscaling
    if observed is None or observed.min_node_count != count or observed.max_node_count != count:
        raise RuntimeError("Retirement autoscaling freeze did not converge")
