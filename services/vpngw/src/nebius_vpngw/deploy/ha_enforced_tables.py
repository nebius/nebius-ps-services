"""Approval-bound private route tables for HA enforced routing.

Candidates are populated while unattached. Only the HA lifecycle's stopped-pair
barrier may publish their subnet CAS operations. Cloud receipts, not resource
names, resolve generated identities. Source tables remain untouched.
"""

from __future__ import annotations

import ipaddress
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..apply_approval import digest
from ..schema import VMHARouteTarget
from .ordinary_route_conversion import copy_inventory, readback_matches, resolve
from .ordinary_route_ownership import snapshot
from .ordinary_route_plan import request_record
from .replacement import Journal, metadata_only
from .replacement_cloud import Cloud, effect_key
from .vm_ha_cloud import clone_nebius_sdk_message, vm_ha_request_kwargs
from .vm_ha_routes import ManagedRouteOwnership

SCHEMA = "nebius-vpngw.ha-enforced-tables.v1"


class TableJournal(Journal):
    schema = SCHEMA

    def __init__(self, project: str, gateway: str, *, root: Path | None = None):
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home() / ".local/state/nebius-vpngw/ha-route-tables" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        if (
            set(state)
            != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
            or state["schema"] != SCHEMA
            or state["project_id"] != self.project
            or state["gateway_name"] != self.gateway
            or type(state["revision"]) is not int
            or state["revision"] < 0
            or not isinstance(state["bindings"], dict)
        ):
            raise ValueError("Invalid HA table journal identity")
        tx = state["transaction"]
        if tx is None:
            return
        if (
            set(tx) != {"plan", "approval", "member", "operations", "phase"}
            or tx["approval"] != digest(tx["plan"])
            or tx["member"] != 0
            or tx["phase"] not in {"preparing", "prepared", "attached", "complete"}
            or not isinstance(tx["operations"], dict)
        ):
            raise ValueError("Invalid HA table transaction")
        validate_plan(tx["plan"])
        if tx["plan"]["project"] != self.project or tx["plan"]["gateway"] != self.gateway:
            raise ValueError("HA table plan crosses journal authority")
        for key, op in tx["operations"].items():
            if (
                op["label"] not in tx["plan"]["requests"]
                or key != effect_key(tx["approval"], 0, op["label"])
                or op["status"] not in {"intent", "accepted", "settled", "rejected", "failed"}
            ):
                raise ValueError("Invalid HA table operation")

    def begin_plan(self, plan: dict) -> None:
        validate_plan(plan)
        if plan["project"] != self.project or plan["gateway"] != self.gateway:
            raise ValueError("HA table plan crosses journal authority")
        tx = self.state["transaction"]
        if tx is not None and tx["plan"] == plan:
            return
        if tx is not None and tx["phase"] != "complete":
            raise RuntimeError("An exact HA table transition is already pending")
        if tx is not None:
            self.archive()
        self.state["transaction"] = {
            "plan": deepcopy(plan),
            "approval": digest(plan),
            "member": 0,
            "operations": {},
            "phase": "preparing",
        }
        self.save()


def validate_plan(plan: dict) -> None:
    if (
        set(plan)
        != {
            "project",
            "gateway",
            "cluster",
            "allocation",
            "targets",
            "sources",
            "requests",
            "predecessors",
            "desired",
            "ordinary_sources",
            "ordinary_predecessors",
        }
        or not all(isinstance(plan[k], str) and plan[k] for k in ("project", "gateway", "cluster"))
        or not isinstance(plan["allocation"], str)
    ):
        raise ValueError("Invalid HA table plan")
    targets = {}
    for target in plan["targets"]:
        parsed = VMHARouteTarget.model_validate(target)
        if parsed.project_id != plan["project"] or parsed.workload_subnet_id in targets:
            raise ValueError("HA table plan crosses project authority")
        targets[parsed.workload_subnet_id] = parsed
    if not targets or len({t.network_id for t in targets.values()}) != 1:
        raise ValueError("HA table plan crosses network authority")
    for label, row in plan["requests"].items():
        request = resolve(row, {}, {})
        if type(request).__name__ not in {
            "CreateRouteRequest",
            "CreateRouteTableRequest",
            "UpdateSubnetRequest",
        }:
            raise ValueError("HA table plan contains unsupported mutation")
        target = targets.get(row.get("subnet"))
        if target is None or row["subnet"] not in plan["sources"]:
            raise ValueError("HA request crossed approved subnet authority")
        if row["type"] == "CreateRouteTableRequest":
            if (
                label != "table-" + row["subnet"]
                or request.metadata.parent_id != plan["project"]
                or request.spec.network_id != target.network_id
            ):
                raise ValueError("HA candidate table crossed project or network authority")
        elif row["type"] == "UpdateSubnetRequest":
            if (
                label != "attach-" + row["subnet"]
                or request.metadata.id != row["subnet"]
                or request.metadata.parent_id != plan["project"]
                or request.spec.network_id != target.network_id
                or request.spec.route_table_id not in {"", target.route_table_id}
                or request.metadata.resource_version <= 0
            ):
                raise ValueError("HA attachment crossed exact source-subnet authority")
        else:
            expected_parent = row.get("restoration", {}).get("parent_id", target.route_table_id)
            if request.metadata.parent_id != expected_parent:
                raise ValueError("HA route crossed its frozen source-table authority")
            destination = request.spec.destination.cidr
            if str(ipaddress.IPv4Network(destination, strict=True)) != destination:
                raise ValueError("HA route destination is not canonical")
    for subnet, source in plan["sources"].items():
        if (
            subnet not in targets
            or source["table"] != targets[subnet].route_table_id
            or not {"table-" + subnet, "attach-" + subnet} <= plan["requests"].keys()
        ):
            raise ValueError("HA table source does not bind its target and CAS")
    if not set(plan["sources"]) <= {t["workload_subnet_id"] for t in plan["targets"]}:
        raise ValueError("HA table source lies outside approved workload targets")


def freeze(
    *,
    project: str,
    gateway: str,
    cluster: str,
    allocation: str,
    targets: tuple[VMHARouteTarget, ...],
    subnets: dict[str, Any],
    routes: dict[str, tuple],
    consumers: dict[str, set[str]],
    desired: dict[str, list[str]],
    previous: dict,
    ordinary: dict | None = None,
    static_prefixes: frozenset[str] = frozenset(),
) -> dict | None:
    """Build immutable copy requests. No cloud mutation or implicit route adoption."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        AllocationNextHop,
        CreateRouteRequest,
        CreateRouteTableRequest,
        DestinationMatch,
        NextHop,
        RouteSpec,
        RouteTableSpec,
        UpdateSubnetRequest,
    )

    from .enforced_predecessors import validate as validate_predecessors
    from .ordinary_route_ownership import labels as ordinary_labels
    from .route_manager import NebiusSDKRouteBackend

    ordinary_predecessors = ordinary.get("enforced_predecessors", {}) if ordinary else {}
    validate_predecessors(ordinary_predecessors)
    if ordinary_predecessors and not previous.get("desired"):
        previous = deepcopy(previous)
        previous["desired"], previous["predecessors"] = {}, {}
        for record in ordinary_predecessors.values():
            subnet, destination = record["subnet_id"], record["destination"]
            previous["desired"].setdefault(subnet, []).append(destination)
            previous["predecessors"].setdefault(subnet, {})[destination] = record["predecessor"]
    desired = {s: sorted(set(v)) for s, v in desired.items() if v}
    old = previous.get("desired", {})
    if not desired and not old:
        return None
    if not set(desired) | set(old) <= {t.workload_subnet_id for t in targets}:
        raise RuntimeError("Retain workload local_prefixes until enforced routes are restored")
    backend = NebiusSDKRouteBackend(None)
    if allocation:
        backend.bind_route_authority(
            cluster_id=cluster, allocation_id=allocation, route_targets=targets
        )
    plan = {
        "project": project,
        "gateway": gateway,
        "cluster": cluster,
        "allocation": allocation,
        "targets": [t.model_dump(mode="json") for t in targets],
        "sources": {},
        "requests": {},
        "predecessors": deepcopy(previous.get("predecessors", {})),
        "desired": desired,
        "ordinary_sources": {},
        "ordinary_predecessors": deepcopy(ordinary_predecessors),
    }
    for target in targets:
        subnet, table = target.workload_subnet_id, target.route_table_id
        ordinary_rows = {
            str(r.metadata.id): r
            for r in routes[table]
            if ordinary is not None
            and any(
                dict(r.metadata.labels)
                == ordinary_labels(project, gateway, table, str(r.spec.destination.cidr), index)
                for index in (0, 1)
            )
        }
        change = desired.get(subnet, []) != old.get(subnet, []) or bool(ordinary_rows)
        if not change and consumers[table] == {subnet}:
            continue
        source = subnets[subnet]
        current = routes[table]
        if len({r.spec.destination.cidr for r in current}) != len(current):
            raise RuntimeError("HA table source has duplicate destinations")
        plan["sources"][subnet] = {
            "table": table,
            "routes": {str(r.metadata.id): snapshot(r) for r in current},
            "consumers": sorted(consumers[table]),
        }
        plan["requests"]["table-" + subnet] = request_record(
            CreateRouteTableRequest(
                metadata=ResourceMetadata(
                    parent_id=project,
                    name="vpngw-ha-private-" + digest([cluster, subnet, table, desired])[:24],
                ),
                spec=RouteTableSpec(network_id=target.network_id),
            ),
            subnet=subnet,
        )
        plan["requests"]["attach-" + subnet] = request_record(
            UpdateSubnetRequest(
                metadata=clone_nebius_sdk_message(source.metadata),
                spec=clone_nebius_sdk_message(source.spec),
            ),
            subnet=subnet,
        )
        copied = copy_inventory(current)
        owned: dict[str, ManagedRouteOwnership | None] = {}
        for route in current:
            if str(route.metadata.id) in ordinary_rows:
                plan["ordinary_sources"][str(route.metadata.id)] = snapshot(route)
                owned[str(route.metadata.id)] = None
                continue
            has_labels, owner = backend._cloud_authority(route, target)
            if has_labels and owner is None:
                raise RuntimeError("HA table contains foreign or unverifiable managed authority")
            owned[str(route.metadata.id)] = owner
        for destination in old.get(subnet, []):
            exact = [r for r in current if r.spec.destination.cidr == destination]
            owner = owned.get(str(exact[0].metadata.id)) if len(exact) == 1 else None
            if (
                len(exact) != 1
                or str(exact[0].metadata.id) not in ordinary_rows
                and (owner is None or owner.kind.value != "enforced")
            ):
                raise RuntimeError("Previously enforced HA route lost its exact managed authority")
        predecessors = plan["predecessors"].setdefault(subnet, {})
        for destination in desired.get(subnet, []):
            if destination not in predecessors:
                exact = [r for r in current if r.spec.destination.cidr == destination]
                predecessors[destination] = (
                    {"request": copied[str(exact[0].metadata.id)], "snapshot": snapshot(exact[0])}
                    if exact
                    and owned[str(exact[0].metadata.id)] is None
                    and str(exact[0].metadata.id) not in ordinary_rows
                    else None
                )
            plan["requests"]["enforce-" + digest([subnet, destination])[:32]] = request_record(
                CreateRouteRequest(
                    metadata=ResourceMetadata(parent_id=table, name="enforced-pending"),
                    spec=RouteSpec(
                        destination=DestinationMatch(cidr=destination),
                        next_hop=NextHop(
                            allocation=AllocationNextHop(id=allocation or "@shared-allocation")
                        ),
                    ),
                ),
                subnet=subnet,
                managed_kind="enforced",
            )
        for identifier, request in copied.items():
            destination = request["request"]["spec"]["destination"]["cidr"]
            if destination in desired.get(subnet, []) or destination in old.get(subnet, []):
                continue
            if identifier not in ordinary_rows and any(
                ipaddress.IPv4Network(destination).subnet_of(ipaddress.IPv4Network(p))
                and request["request"]["spec"].get("next_hop", {}).get("allocation", {}).get("id")
                != allocation
                for p in desired.get(subnet, [])
            ):
                raise RuntimeError("Enforced destination conflicts with more-specific predecessor")
            owner = owned[identifier]
            plan["requests"]["copy-" + digest([subnet, identifier])[:32]] = {
                **request,
                "subnet": subnet,
                **({"managed_kind": owner.kind.value} if owner else {}),
                **(
                    {"managed_kind": "static" if destination in static_prefixes else "bgp"}
                    if identifier in ordinary_rows
                    else {}
                ),
            }
        for destination in set(old.get(subnet, [])) - set(desired.get(subnet, [])):
            before = predecessors.pop(destination)
            if before is not None:
                plan["requests"]["restore-" + digest([subnet, destination])[:32]] = {
                    **before["request"],
                    "subnet": subnet,
                    "restoration": before["snapshot"],
                }
        if not predecessors:
            plan["predecessors"].pop(subnet, None)
    if not plan["sources"]:
        return None
    validate_plan(plan)
    return plan


def resolve_request(plan: dict, records: dict, label: str, *, allocation: str) -> Any:
    """Fill only receipt-bound table IDs and the exact lifecycle shared allocation."""
    row = plan["requests"][label]
    request = resolve(row, {}, {})
    if row["type"] == "CreateRouteTableRequest":
        return request
    receipt = records.get(effect_key(digest(plan), 0, "table-" + row["subnet"]))
    if receipt is None or receipt["status"] != "settled" or not receipt["resource_id"]:
        raise RuntimeError("HA private table has no settled creation receipt")
    table = receipt["resource_id"]
    if row["type"] == "UpdateSubnetRequest":
        request.spec.route_table_id = table
    else:
        request.metadata.parent_id = table
        if "managed_kind" in row:
            from .route_manager import NebiusSDKRouteBackend
            from .vm_ha_routes import ManagedRouteKind

            target = next(
                VMHARouteTarget.model_validate(t).model_copy(update={"route_table_id": table})
                for t in plan["targets"]
                if t["workload_subnet_id"] == row["subnet"]
            )
            kind = ManagedRouteKind(row["managed_kind"])
            if not allocation or plan["allocation"] not in {"", allocation}:
                raise RuntimeError("HA private table allocation differs from approved lifecycle")
            request.spec.next_hop.allocation.id = allocation
            request.metadata.labels = NebiusSDKRouteBackend._authority_labels(
                cluster_id=plan["cluster"],
                allocation_id=allocation,
                route_target=target,
                route_kind=kind,
            )
            identity = f"{plan['cluster']}:{table}:{request.spec.destination.cidr}:{kind.value}:{allocation}"
            import hashlib

            request.metadata.name = "vpngw-ha-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return request


def validate_ordinary_authority(plan: dict, bindings: dict[str, str]) -> None:
    """Preview labels never authorize migration; require captured creation receipts."""
    if not plan["ordinary_sources"] and not plan["ordinary_predecessors"]:
        return
    import json

    from .ordinary_route_migration import KEY, records

    value = json.loads(bindings.get(KEY, "null"))
    if value is None:
        raise RuntimeError("Enforced HA conversion lacks its ordinary handoff authority")
    inventory = {}
    predecessors = None
    for node in value["sources"]:
        inventory.update(records(value, plan["project"], plan["gateway"], node))
        journal = value["sources"][node]
        observed = journal.get("enforced_predecessors", {}) if journal else {}
        if predecessors is not None and predecessors != observed:
            raise RuntimeError("Enforced HA conversion has inconsistent predecessor history")
        predecessors = observed
    if predecessors != plan["ordinary_predecessors"] or any(
        inventory.get(identifier, {}).get("snapshot") != expected
        for identifier, expected in plan["ordinary_sources"].items()
    ):
        raise RuntimeError("Enforced HA conversion differs from ordinary creation receipts")


class Transition:
    def __init__(
        self,
        sdk: Any,
        journal: TableJournal,
        plan: dict,
        *,
        allocation: str,
        ordinary_bindings: dict[str, str] | None = None,
    ):
        validate_plan(plan)
        validate_ordinary_authority(plan, ordinary_bindings or {})
        if plan["project"] != journal.project or plan["gateway"] != journal.gateway:
            raise ValueError("HA table plan crosses journal authority")
        self.sdk, self.journal, self.plan, self.allocation = sdk, journal, plan, allocation
        if not allocation or plan["allocation"] not in {"", allocation}:
            raise RuntimeError("HA table transition requires the exact shared allocation")

    def _cloud(self, admission) -> Cloud:
        return Cloud(
            self.sdk,
            self.journal,
            member_index=0,
            admit_request=admission,
            restore_request=lambda op: resolve_request(
                self.plan,
                self.journal.state["transaction"]["operations"],
                op["label"],
                allocation=self.allocation,
            ),
        )

    def targets(self) -> tuple[VMHARouteTarget, ...]:
        tx = self.journal.state["transaction"]
        result = []
        for raw in self.plan["targets"]:
            target = VMHARouteTarget.model_validate(raw)
            if target.workload_subnet_id in self.plan["sources"]:
                op = tx["operations"].get(
                    effect_key(digest(self.plan), 0, "table-" + target.workload_subnet_id)
                )
                if op is None or op["status"] != "settled" or not op["resource_id"]:
                    raise RuntimeError("HA private target lacks its successful table receipt")
                target = target.model_copy(update={"route_table_id": op["resource_id"]})
            result.append(target)
        return tuple(result)

    def verify(self, *, attached: bool, uncertain: bool = False) -> None:
        from nebius.api.nebius.vpc.v1 import (
            GetRouteRequest,
            GetRouteTableRequest,
            GetSubnetRequest,
            RouteServiceClient,
            RouteTableServiceClient,
            SubnetServiceClient,
        )

        from .route_manager import NebiusSDKRouteBackend

        backend = NebiusSDKRouteBackend(self.sdk)
        tx = self.journal.state["transaction"]
        records = tx["operations"]
        expected_candidates: dict[str, dict[str, Any]] = {}
        for subnet, source in self.plan["sources"].items():
            current = backend._raw_routes(source["table"])
            if {str(r.metadata.id): snapshot(r) for r in current} != source["routes"]:
                raise RuntimeError("HA source table changed during private-table preparation")
            actual = (
                SubnetServiceClient(self.sdk)
                .get(GetSubnetRequest(id=subnet), **vm_ha_request_kwargs())
                .wait()
            )
            original = resolve(self.plan["requests"]["attach-" + subnet], {}, {})
            operation = records.get(effect_key(digest(self.plan), 0, "attach-" + subnet))
            switched = operation is not None and operation["status"] == "settled"
            if switched or attached:
                if not switched:
                    raise RuntimeError("HA attachment lacks a settled operation receipt")
                expected = resolve_request(
                    self.plan, records, "attach-" + subnet, allocation=self.allocation
                )
            elif uncertain and operation is not None:
                prospective = resolve_request(
                    self.plan, records, "attach-" + subnet, allocation=self.allocation
                )
                expected = prospective if readback_matches(actual, prospective) else original
            else:
                expected = original
            if (
                actual.metadata.id != subnet
                or not readback_matches(actual, expected)
                or str(actual.status.route_table.id)
                != (expected.spec.route_table_id or source["table"])
                or (
                    not switched
                    and expected is original
                    and actual.metadata.resource_version != original.metadata.resource_version
                )
            ):
                raise RuntimeError("HA source subnet changed outside its approved CAS")
        for label, row in self.plan["requests"].items():
            if "restoration" in row:
                before = row["restoration"]
                actual = (
                    RouteServiceClient(self.sdk)
                    .get(GetRouteRequest(id=before["id"]), **vm_ha_request_kwargs())
                    .wait()
                )
                if snapshot(actual) != before:
                    raise RuntimeError("HA displaced predecessor changed before restoration")
            op = records.get(effect_key(digest(self.plan), 0, label))
            if op is None:
                continue
            if op["status"] not in {"settled", "intent", "accepted"}:
                raise RuntimeError("HA table operation was rejected; retain its original authority")
            if row["type"] == "UpdateSubnetRequest":
                continue
            request = resolve_request(self.plan, records, label, allocation=self.allocation)
            if op["status"] != "settled":
                if not uncertain:
                    raise RuntimeError("HA table operation is not settled")
                continue
            if row["type"] == "CreateRouteTableRequest":
                actual = (
                    RouteTableServiceClient(self.sdk)
                    .get(GetRouteTableRequest(id=op["resource_id"]), **vm_ha_request_kwargs())
                    .wait()
                )
                if actual.metadata.id != op["resource_id"] or not readback_matches(actual, request):
                    raise RuntimeError("HA private table creation has no exact readback")
                assignments = set(actual.status.assignment.subnets)
                expected_assignments = (
                    {row["subnet"]}
                    if any(
                        x["label"] == "attach-" + row["subnet"] and x["status"] == "settled"
                        for x in records.values()
                    )
                    else set()
                )
                if assignments != expected_assignments and not (
                    uncertain and assignments == {row["subnet"]}
                ):
                    raise RuntimeError("HA private table has an unexpected consumer")
                expected_candidates[op["resource_id"]] = {}
        for label, row in self.plan["requests"].items():
            if row["type"] != "CreateRouteRequest":
                continue
            op = records.get(effect_key(digest(self.plan), 0, label))
            if op is None:
                continue
            request = resolve_request(self.plan, records, label, allocation=self.allocation)
            if op["status"] == "settled":
                expected_candidates[request.metadata.parent_id][op["resource_id"]] = request
            elif uncertain:
                matches = [
                    r
                    for r in backend._raw_routes(request.metadata.parent_id)
                    if readback_matches(r, request)
                ]
                if len(matches) > 1:
                    raise RuntimeError("Uncertain HA route creation is ambiguous")
                if matches:
                    expected_candidates[request.metadata.parent_id][str(matches[0].metadata.id)] = (
                        request
                    )
        for table, expected in expected_candidates.items():
            actual = {str(r.metadata.id): r for r in backend._raw_routes(table)}
            if set(actual) != set(expected) or any(
                not readback_matches(actual[k], v) for k, v in expected.items()
            ):
                raise RuntimeError("HA private table contents differ from settled copy receipts")

    def prepare(self, *, admit) -> tuple[VMHARouteTarget, ...]:
        from nebius.api.nebius.vpc.v1 import RouteServiceClient, RouteTableServiceClient

        with self.journal:
            self.journal.begin_plan(self.plan)
            tx = self.journal.state["transaction"]
            if any(op["request_type"] == "UpdateSubnetRequest" for op in tx["operations"].values()):
                # Preparation must never settle or replay an attachment: that
                # requires the caller's fresh stopped-pair barrier in cutover.
                if tx["phase"] == "preparing" or any(
                    op["request_type"] != "UpdateSubnetRequest" and op["status"] != "settled"
                    for op in tx["operations"].values()
                ):
                    raise RuntimeError("HA attachment preceded complete table preparation")
                admit()
                self.verify(attached=False, uncertain=True)
                return self.targets()

            def admission(_request):
                admit()
                self.verify(attached=False, uncertain=True)

            cloud = self._cloud(admission)
            admission(None)
            cloud.settle()
            for label in sorted(
                self.plan["requests"], key=lambda k: (not k.startswith("table-"), k)
            ):
                row = self.plan["requests"][label]
                if row["type"] == "UpdateSubnetRequest":
                    continue
                request = resolve_request(
                    self.plan, cloud.records, label, allocation=self.allocation
                )
                service = (
                    RouteTableServiceClient(self.sdk)
                    if row["type"] == "CreateRouteTableRequest"
                    else RouteServiceClient(self.sdk)
                )
                receipt = cloud.records.get(effect_key(digest(self.plan), 0, label))
                settled = receipt is not None and receipt["status"] == "settled"
                cloud.mutate(label, request, service.create)
                if not settled:
                    self.verify(attached=False)
            # Replaying settled receipts has no cloud effects. Validate their
            # complete footprint once, rather than once per unchanged request.
            # The final creation may also advance its next-hop allocation's
            # revision; admit that receipt before the parent begins its next step.
            admit()
            self.verify(attached=False)
            if tx["phase"] == "preparing":
                tx["phase"] = "prepared"
                self.journal.save()
            return self.targets()

    def cutover(self, *, quiescent) -> tuple[VMHARouteTarget, ...]:
        from nebius.api.nebius.vpc.v1 import SubnetServiceClient

        with self.journal:
            tx = self.journal.state["transaction"]
            if (
                tx is None
                or tx["plan"] != self.plan
                or tx["phase"] not in {"prepared", "attached", "complete"}
            ):
                raise RuntimeError("HA route cutover lacks a completed private-table preparation")

            def admission(_request):
                quiescent()
                self.verify(attached=False, uncertain=True)

            cloud = self._cloud(admission)
            admission(None)
            if tx["phase"] == "complete":
                self.verify(attached=True)
                return self.targets()
            cloud.settle()
            for subnet in sorted(self.plan["sources"]):
                label = "attach-" + subnet
                request = resolve_request(
                    self.plan, cloud.records, label, allocation=self.allocation
                )
                cloud.mutate(label, request, SubnetServiceClient(self.sdk).update)
            self.verify(attached=True)
            tx["phase"] = "attached"
            self.journal.save()
            return self.targets()

    def commit(self) -> dict:
        """Lifecycle calls this only after its exact target rebind is durable."""
        with self.journal:
            tx = self.journal.state["transaction"]
            if tx is None or tx["plan"] != self.plan or tx["phase"] not in {"attached", "complete"}:
                raise RuntimeError("HA table completion lacks its exact attached transition")
            self.verify(attached=True)
            retired = deepcopy(self.journal.state["bindings"].get("retired_routes", {}))
            retired.update(self.plan["ordinary_sources"])
            from .route_manager import NebiusSDKRouteBackend
            from .vm_ha_routes import ManagedRouteKind

            for raw in self.plan["targets"]:
                target = VMHARouteTarget.model_validate(raw)
                source = self.plan["sources"].get(target.workload_subnet_id)
                if source is None:
                    continue
                for identifier, value in source["routes"].items():
                    if any(
                        value["labels"]
                        == NebiusSDKRouteBackend._authority_labels(
                            cluster_id=self.plan["cluster"],
                            allocation_id=self.allocation,
                            route_target=target,
                            route_kind=kind,
                        )
                        for kind in ManagedRouteKind
                    ):
                        retired[identifier] = value
            self.journal.state["bindings"] = {
                "cluster": self.plan["cluster"],
                "allocation": self.allocation,
                "desired": self.plan["desired"],
                "predecessors": self.plan["predecessors"],
                "targets": [t.model_dump(mode="json") for t in self.targets()],
                "retired_routes": retired,
            }
            tx["phase"] = "complete"
            self.journal.save()
            return deepcopy(self.journal.state["bindings"])
