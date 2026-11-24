"""Read-only HA table transition inventory, separate from runtime target admission."""

from __future__ import annotations

import ipaddress
import json
from typing import cast

from ..enforced_routing import Policy, prefixes, resolve_sources
from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from ..schema import VMHARouteTarget
from .ha_enforced_tables import TableJournal, freeze
from .route_manager import NebiusSDKRouteBackend, RouteManager
from .vm_ha_cloud import vm_ha_request_kwargs
from .vm_ha_lifecycle import VMHALifecycleStatus


def private_table_creations(observation) -> int:
    """Describe pending table creation without treating historical audit as new work."""
    from .ha_enforced_tables import validate_plan

    raw = observation.get("enforced_table_plan")
    if raw is None:
        return 0
    plan = json.loads(raw)
    validate_plan(plan)
    current = {
        t.workload_subnet_id: t.route_table_id
        for t in (VMHARouteTarget.model_validate(row) for row in observation["route_targets"])
    }
    return sum(
        row["type"] == "CreateRouteTableRequest"
        and current.get(row["subnet"]) == plan["sources"][row["subnet"]]["table"]
        for row in plan["requests"].values()
    )


def committed_by_table(state) -> dict[str, frozenset[str]]:
    """Resolve committed source-specific intent against current lifecycle targets."""
    from .ha_enforced_tables import validate_plan

    desired = {}
    raw = (
        dict(state.transaction.resource_bindings).get("enforced-route-plan")
        if state.transaction
        else None
    )
    if raw is None:
        return {}
    targets = [VMHARouteTarget.model_validate_json(row) for row in state.route_targets]
    plan = json.loads(raw)
    validate_plan(plan)
    if (
        plan["project"] != state.project_id
        or plan["gateway"] != state.gateway_name
        or plan["cluster"] != state.cluster_id
        or plan["allocation"] not in {"", state.allocation_id}
        or {(t["project_id"], t["network_id"], t["workload_subnet_id"]) for t in plan["targets"]}
        != {(t.project_id, t.network_id, t.workload_subnet_id) for t in targets}
        or not isinstance(plan["desired"], dict)
        or not set(plan["desired"]) <= {t.workload_subnet_id for t in targets}
    ):
        raise ValueError("Committed HA enforced policy crossed lifecycle authority")
    for subnet, values in plan["desired"].items():
        if not isinstance(values, list) or not values or list(prefixes(values)) != values:
            raise ValueError("Committed HA enforced destinations are not canonical")
        desired[subnet] = frozenset(values)
    return {t.route_table_id: desired.get(t.workload_subnet_id, frozenset()) for t in targets}


def declarations(spec) -> tuple[Policy, ...]:
    manifests = spec.vm_ha.generation.logical_manifests
    return tuple(
        Policy(
            row["connection"],
            prefixes(row["enforced_routing"]["source_subnets"]),
            prefixes(row["enforced_routing"]["destinations"]),
            0,
        )
        for row in [
            *json.loads(manifests.static_routes_json),
            *json.loads(manifests.bgp_policy_json),
        ]
        if row.get("enforced_routing")
    )


def observe(manager, client, spec, local_prefixes, *, allocation: str) -> dict | None:
    from nebius.api.nebius.vpc.v1 import (
        GetRouteTableRequest,
        ListSubnetsByNetworkRequest,
        RouteTableServiceClient,
        Subnet,
    )

    journal = TableJournal(manager.project_id, spec.name)
    saved = journal.snapshot()
    from .ordinary_route_ownership import RouteJournal

    ordinary = RouteJournal(manager.project_id, spec.name).snapshot()
    from ..apply_approval import digest

    if saved is not None and (ordinary or {}).get("ha_route_source") == digest(saved):
        # The ordinary transaction copied and superseded this exact HA history.
        # A subsequent HA conversion starts from its genuine ordinary receipts.
        saved = None
    elif saved is not None:
        ordinary = None
    policies = declarations(spec)
    previous = saved["bindings"] if saved else {}
    transaction = saved.get("transaction") if saved else None
    pending = transaction if transaction and transaction["phase"] != "complete" else None
    lifecycle = getattr(manager, "_vm_ha_journal", None)
    retained = (
        dict(lifecycle.state.transaction.resource_bindings).get("enforced-route-plan")
        if lifecycle is not None
        and lifecycle.state.transaction is not None
        and lifecycle.state.status
        in {VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING}
        else None
    )
    if (
        not policies
        and not previous.get("desired")
        and pending is None
        and retained is None
        and transaction is None
        and not (ordinary or {}).get("enforced_predecessors")
    ):
        return None
    _, network, _, service = manager._resolve_gateway_network(client, spec)
    raw_subnets = tuple(
        collect_nebius_pages(
            lambda token: service.list_by_network(
                ListSubnetsByNetworkRequest(network_id=network, page_size=1000, page_token=token),
                **vm_ha_request_kwargs(),
            ),
            context="HA enforced source subnets",
            item_identity=nebius_resource_id,
        )
    )
    subnets = cast(tuple[Subnet, ...], raw_subnets)
    if any(
        s.metadata.parent_id != manager.project_id or s.spec.network_id != network for s in subnets
    ):
        raise RuntimeError("HA table inventory crossed exact project and network authority")
    route_manager = RouteManager(project_id=manager.project_id)
    gateway_name = str(manager._gateway_subnet_settings(spec)["name"])
    gateways = [str(s.metadata.id) for s in subnets if s.metadata.name == gateway_name]
    if len(gateways) > 1:
        raise RuntimeError("HA enforced gateway subnet identity is ambiguous")
    sources = resolve_sources(
        policies,
        {
            str(s.metadata.id): tuple(str(p) for p in route_manager._effective_subnet_cidrs(s))
            for s in subnets
            if not s.spec.ipv4_private_pools.use_network_pools
        },
        gateway_subnet_id=next(iter(gateways), ""),
    )
    desired: dict[str, list[str]] = {}
    for policy in policies:
        for subnet in sources[policy.connection]:
            desired[subnet] = sorted(set(desired.get(subnet, [])) | set(policy.destinations))
    selected, _ = route_manager._select_local_prefix_subnets(
        subnets,
        [ipaddress.IPv4Network(p) for p in local_prefixes],
        target_network_id=network,
        gateway_subnet_name=gateway_name,
    )
    targets = tuple(
        sorted(
            (
                VMHARouteTarget(
                    project_id=manager.project_id,
                    network_id=network,
                    workload_subnet_id=str(s.metadata.id),
                    route_table_id=str(s.status.route_table.id),
                )
                for s, _ in selected
            ),
            key=lambda t: t.workload_subnet_id,
        )
    )
    consumers, routes = {}, {}
    backend = NebiusSDKRouteBackend(client)
    for table in sorted({t.route_table_id for t in targets}):
        actual = (
            RouteTableServiceClient(client)
            .get(GetRouteTableRequest(id=table), **vm_ha_request_kwargs())
            .wait()
        )
        assigned = {str(s.metadata.id) for s in subnets if str(s.status.route_table.id) == table}
        if (
            actual.metadata.id != table
            or actual.metadata.parent_id != manager.project_id
            or actual.spec.network_id != network
            or set(actual.status.assignment.subnets) != assigned
            or any(
                s.spec.route_table_id and s.spec.route_table_id != table
                for s in subnets
                if str(s.metadata.id) in assigned
            )
        ):
            raise RuntimeError("HA source table membership is not authoritative")
        consumers[table] = assigned
        routes[table] = tuple(backend._raw_routes(table))
    if pending is not None or retained is not None:
        if pending is not None:
            plan = pending["plan"]
        else:
            assert retained is not None
            plan = json.loads(retained)
        if (
            plan["cluster"] != spec.vm_ha.cluster_id
            or plan["desired"] != desired
            or {t["workload_subnet_id"] for t in plan["targets"]}
            != {t.workload_subnet_id for t in targets}
        ):
            raise RuntimeError("Resume the exact pending HA enforced routing declaration")
    else:
        from .vm_ha_routes import LogicalStaticRouteManifest

        plan = freeze(
            project=manager.project_id,
            gateway=spec.name,
            cluster=spec.vm_ha.cluster_id,
            allocation=allocation,
            targets=targets,
            subnets={str(s.metadata.id): s for s in subnets},
            routes=routes,
            consumers=consumers,
            desired=desired,
            previous=previous,
            ordinary=ordinary,
            static_prefixes=LogicalStaticRouteManifest.from_generation(
                spec.vm_ha.generation
            ).prefixes,
        )
    audit = plan if plan is not None else transaction["plan"] if transaction else None
    return {"targets": targets, "plan": plan, "audit": audit, "sources": sources}
