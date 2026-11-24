"""Freeze reverse conversion authority without relabeling existing HA routes.

The ordinary route owner copies whole tables and creates its own routes. The HA
source is an exclusion from those copies, never synthetic ordinary ownership.
"""

from __future__ import annotations

from copy import deepcopy

from ..apply_approval import digest
from ..schema import VMHARouteTarget
from .enforced_predecessors import validate as validate_predecessors
from .ha_enforced_tables import TableJournal
from .ordinary_route_ownership import snapshot
from .route_manager import NebiusSDKRouteBackend


def observe(project, gateway, ordinary, targets, routes):
    saved = TableJournal(project, gateway).snapshot()
    if saved is None or (ordinary or {}).get("ha_route_source") == digest(saved):
        return None
    tx = (ordinary or {}).get("transaction")
    if tx and tx["phase"] != "complete" and (tx.get("plan") or {}).get("ha_conversion"):
        value = tx["plan"]["ha_conversion"]
        verify(value)
        return value
    child = saved["transaction"]
    if child is None or child["phase"] != "complete":
        raise RuntimeError("Complete the HA enforced table transition before ordinary conversion")
    bindings = saved["bindings"]
    if not bindings.get("targets"):
        return None
    previous = {
        t["workload_subnet_id"]: VMHARouteTarget.model_validate(t) for t in bindings["targets"]
    }
    current = {t["subnet_id"]: t["table_id"] for t in targets}
    if set(previous) - set(current) or any(
        current[s] != t.route_table_id for s, t in previous.items()
    ):
        raise RuntimeError("HA conversion workload attachments changed outside their owner")
    backend = NebiusSDKRouteBackend(None)
    backend.bind_route_authority(
        cluster_id=bindings["cluster"],
        allocation_id=bindings["allocation"],
        route_targets=tuple(previous.values()),
    )
    owned = {}
    for target in previous.values():
        for route in routes[target.route_table_id]:
            labels, authority = backend._cloud_authority(route, target)
            if labels and authority is None:
                raise RuntimeError("HA conversion contains foreign managed route authority")
            if authority is not None:
                owned[str(route.metadata.id)] = snapshot(route)
    predecessors = {}
    for subnet, destinations in bindings.get("predecessors", {}).items():
        for destination, before in destinations.items():
            predecessors[digest([subnet, destination])] = {
                "subnet_id": subnet,
                "table_id": previous[subnet].route_table_id,
                "destination": destination,
                "predecessor": deepcopy(before),
            }
    validate_predecessors(predecessors)
    return {
        "project": project,
        "gateway": gateway,
        "source_sha256": digest(saved),
        "targets": [t.model_dump(mode="json") for t in previous.values()],
        "routes": owned,
        "predecessors": predecessors,
    }


def verify(value):
    if value is None:
        return
    saved = TableJournal(value["project"], value["gateway"]).snapshot()
    if saved is None or digest(saved) != value["source_sha256"]:
        raise RuntimeError("HA conversion source authority changed")
    bindings = saved["bindings"]
    if value["targets"] != bindings["targets"]:
        raise RuntimeError("HA conversion source targets changed")
    validate_predecessors(value["predecessors"])
    expected = {}
    targets = {t["workload_subnet_id"]: t for t in bindings["targets"]}
    for subnet, destinations in bindings.get("predecessors", {}).items():
        for destination, before in destinations.items():
            expected[digest([subnet, destination])] = {
                "subnet_id": subnet,
                "table_id": targets[subnet]["route_table_id"],
                "destination": destination,
                "predecessor": before,
            }
    if value["predecessors"] != expected:
        raise RuntimeError("HA conversion predecessor history changed")
    for row in value["routes"].values():
        target = next(
            (
                VMHARouteTarget.model_validate(t)
                for t in value["targets"]
                if t["route_table_id"] == row["parent_id"]
            ),
            None,
        )
        from .vm_ha_routes import ManagedRouteKind

        if (
            target is None
            or row["allocation_id"] != bindings["allocation"]
            or not any(
                row["labels"]
                == NebiusSDKRouteBackend._authority_labels(
                    cluster_id=bindings["cluster"],
                    allocation_id=bindings["allocation"],
                    route_target=target,
                    route_kind=kind,
                )
                for kind in ManagedRouteKind
            )
        ):
            raise RuntimeError("HA conversion route is outside its exact source authority")
