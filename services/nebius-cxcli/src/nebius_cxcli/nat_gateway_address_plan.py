"""Join fresh Terraform reservations to scoped NAT address-space evidence."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

from .nat_gateway import nat_rows
from .nat_gateway_address_space import (
    AddressAssessment,
    AddressBlock,
    AddressDemand,
    AddressPool,
    AddressSpaceUnresolved,
    IPv4,
    assess_address_space,
    subtract,
)

_FIELDS = {
    "nebius_vpc_v1_subnet": ("network_id", "ipv4_private_pools"),
    "nebius_vpc_v1_pool": ("source_pool_id", "cidrs"),
    "nebius_vpc_v1_allocation": ("ipv4_private",),
    "nebius_compute_v1_instance": ("network_interfaces",),
    "nebius_mk8s_v1_cluster": ("control_plane", "kube_network"),
    "nebius_mk8s_v1_node_group": (
        "template",
        "fixed_node_count",
        "autoscaling",
        "strategy",
        "version",
    ),
}


def _unknown(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(_unknown(item) for item in value)
    return value is True


def _changed(resource: Mapping[str, Any]) -> bool:
    if resource.get("mode") == "data" or resource.get("type") not in _FIELDS:
        return False
    change = resource.get("change", {})
    actions = change.get("actions", [])
    if resource["type"] == "nebius_compute_v1_instance":
        interfaces = (change.get("after") or {}).get("network_interfaces")
        if interfaces and all(
            (nic.get("ip_address") or {}).get("allocation_id") for nic in interfaces
        ):
            return False  # Existing stable allocations require no new NIC reservation.
        previous = (change.get("before") or {}).get("network_interfaces")
        if "create" not in actions and interfaces and previous:

            def sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    {key: nic.get(key) for key in ("name", "subnet_id", "ip_address")}
                    for nic in items
                ]

            unknown_nics = (change.get("after_unknown") or {}).get("network_interfaces")
            if not _unknown(unknown_nics) and sources(interfaces) == sources(previous):
                return False
    if "create" in actions:
        return True
    if "update" not in actions:
        return False
    before, after = change.get("before") or {}, change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    return any(
        before.get(field) != after.get(field) or _unknown(unknown.get(field))
        for field in _FIELDS[resource["type"]]
    )


def _resources(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}

    def visit(module: Mapping[str, Any]) -> None:
        for resource in module.get("resources", []):
            result[resource["address"]] = {
                **resource,
                "change": {
                    "before": resource.get("values") or {},
                    "after": resource.get("values") or {},
                    "actions": ["no-op"],
                },
            }
        for child in module.get("child_modules", []):
            visit(child)

    visit(((plan.get("prior_state") or {}).get("values") or {}).get("root_module") or {})
    for resource in plan.get("resource_changes", []):
        address = resource.get("address")
        if not address:
            raise AddressSpaceUnresolved("Terraform address identity is missing")
        result[address] = resource
    return result


def _values(resource: Mapping[str, Any]) -> dict[str, Any]:
    return dict(resource.get("change", {}).get("after") or {})


def _cidrs(spec: Mapping[str, Any]) -> list[str]:
    if spec.get("use_network_pools") is True:
        return []
    pools = spec.get("pools")
    if not isinstance(pools, list) or not pools:
        raise AddressSpaceUnresolved("Planned subnet private pool mode or ranges are unresolved")
    result = []
    for pool in pools:
        if pool.get("id"):
            # An attached existing pool is already reserved in its source.
            continue
        entries = pool.get("cidrs")
        if not isinstance(entries, list) or not entries:
            raise AddressSpaceUnresolved("Planned subnet has unresolved private CIDR demand")
        for entry in entries:
            if not isinstance(entry.get("cidr"), str) or not entry["cidr"]:
                raise AddressSpaceUnresolved("Planned subnet CIDR demand is unknown")
            result.append(entry["cidr"])
    return result


def _new_ranges(current: list[str], previous: list[str]) -> list[str]:
    """An in-place expansion needs only its delta; pending deletion frees nothing."""
    result = []
    retained = Counter(previous)
    for cidr in current:
        if retained[cidr]:
            retained[cidr] -= 1
            continue
        if cidr.startswith("/"):
            if previous:
                raise AddressSpaceUnresolved(
                    "Changed automatic prefix needs a resolved replacement plan"
                )
            result.append(cidr)
            continue
        fragments = [IPv4(cidr, strict=True)]
        for old in previous:
            if old.startswith("/"):
                raise AddressSpaceUnresolved(
                    "Existing reservation range is unresolved for expansion"
                )
            fragments = [part for fragment in fragments for part in subtract(fragment, IPv4(old))]
        result.extend(str(part) for part in fragments)
    return result


def _dedicated_subnet(resource: Mapping[str, Any]) -> bool:
    spec = _values(resource).get("ipv4_private_pools") or {}
    unknown = (resource.get("change", {}).get("after_unknown") or {}).get("ipv4_private_pools")
    if _unknown(unknown):
        raise AddressSpaceUnresolved("Subnet private pool isolation is unresolved")
    if spec.get("use_network_pools") is not False:
        return False
    _cidrs(spec)  # False alone does not prove an owned child reservation.
    return True


def _include_pool_sources(
    inventory: _Inventory, changes: list[Mapping[str, Any]], roots: set[str]
) -> None:
    """Charge expansions of selected derived pools to their upstream pools too."""
    pending = [r for r in changes if r["type"] == "nebius_vpc_v1_pool"]
    for _ in range(256):
        relevant = [r for r in pending if _values(r).get("id") in roots]
        if not relevant:
            return
        for resource in relevant:
            values = _values(resource)
            if _unknown((resource["change"].get("after_unknown") or {}).get("source_pool_id")):
                raise AddressSpaceUnresolved("Selected pool source is unresolved")
            if source := values.get("source_pool_id"):
                project = values.get("parent_id")
                if not project:
                    raise AddressSpaceUnresolved("Selected pool project is unresolved")
                roots.add(inventory.pool(project, source))
            pending.remove(resource)
    raise AddressSpaceUnresolved("Pool ancestry exceeds the bounded assessment limit")


class _Inventory:
    def __init__(
        self,
        payload: Mapping[str, Any],
        resources: dict[str, dict[str, Any]],
        modules: Mapping[tuple[str, str], str],
        stack: ExitStack,
        sdk_factory: Callable[..., Any],
    ) -> None:
        self.payload, self.resources, self.modules = payload, resources, modules
        self.stack, self.sdk_factory = stack, sdk_factory
        self.rows = {
            row["instance_id"]: row for row in payload["infra"]["components"] if row.get("enabled")
        }
        self.sdks: dict[str, Any] = {}
        self.pools: dict[str, AddressPool] = {}
        self.pool_projects: dict[str, str] = {}
        self.deadline = time.monotonic() + 120
        self.networks: dict[tuple[str, str], tuple[str, ...]] = {}
        self.demands: dict[str, AddressDemand] = {}

    def project(self, row: Mapping[str, Any]) -> str:
        return str(
            row.get("inputs", {}).get("parent_id")
            or self.payload["client_info"]["nebius"]["project_id"]
        )

    def sdk(self, project: str) -> Any:
        if project not in self.sdks:
            self.sdks[project] = self.stack.enter_context(self.sdk_factory(project))
        return self.sdks[project]

    def pool(self, project: str, identity: str) -> str:
        if not identity:
            raise AddressSpaceUnresolved("Source-pool identity is unresolved")
        self.pool_projects.setdefault(identity, project)
        return identity

    def load(self, identities: set[str]) -> None:
        for identity in sorted(identities):
            if time.monotonic() >= self.deadline:
                raise AddressSpaceUnresolved("Address inventory deadline exceeded")
            if identity not in self.pools:
                self.pools[identity] = self.sdk(self.pool_projects[identity]).address_pool(
                    identity, deadline=self.deadline
                )
            if sum(len(p.occupied) for p in self.pools.values()) > 100_000:
                raise AddressSpaceUnresolved("Address inventory resource limit exceeded")
        if time.monotonic() >= self.deadline:
            raise AddressSpaceUnresolved("Address inventory deadline exceeded")

    def network(self, project: str, identity: str) -> tuple[str, ...]:
        key = (project, identity)
        if key not in self.networks:
            records = [
                resource
                for resource in self.resources.values()
                if resource.get("type") == "nebius_vpc_v1_network"
                and resource.get("mode") != "data"
                and identity
                in {
                    _values(resource).get("id"),
                    (resource.get("change", {}).get("before") or {}).get("id"),
                }
                and resource.get("change", {}).get("after") is not None
            ]
            if len(records) > 1:
                raise AddressSpaceUnresolved("Planned network identity is ambiguous")
            if records:
                record = records[0]
                spec = _values(record).get("ipv4_private_pools") or {}
                unknown = record["change"].get("after_unknown", {}).get("ipv4_private_pools")
                if _unknown(unknown):
                    owner = self.owner(record["address"])
                    if (
                        owner is None
                        or owner["id"] != "vpc"
                        or owner["inputs"]["network"].get("existing_id")
                    ):
                        raise AddressSpaceUnresolved("Planned network pool membership is unknown")
                    roots = self.configured_roots(owner)
                else:
                    roots = tuple(
                        self.pool(project, item.get("id")) for item in spec.get("pools") or []
                    )
            else:
                if time.monotonic() >= self.deadline:
                    raise AddressSpaceUnresolved("Address inventory deadline exceeded")
                network = self.sdk(project).network(identity)
                roots = tuple(
                    self.pool(project, reference.id)
                    for reference in network.spec.ipv4_private_pools.pools
                )
            if not roots:
                raise AddressSpaceUnresolved(
                    "Selected network has no verified private source pools"
                )
            self.networks[key] = roots
        return self.networks[key]

    def module(self, row: Mapping[str, Any]) -> str:
        name = self.modules.get((row["id"], row["instance_id"]))
        if not name:
            raise AddressSpaceUnresolved("Rendered component module identity is unresolved")
        return f"module.{name}."

    def owner(self, address: str) -> Mapping[str, Any] | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row["id"] in {"vpc", "nat-gateway"} and address.startswith(self.module(row))
            ),
            None,
        )

    def roots(self, row: Mapping[str, Any]) -> tuple[str, ...]:
        project, inputs = self.project(row), row["inputs"]
        if row["id"] == "nat-gateway":
            binding = row.get("bindings", {}).get("inputs.network_id")
            if binding:
                return self.roots(self.rows[binding["source_instance"]])
            return self.network(project, inputs["network_id"])
        network = inputs["network"]
        if network.get("existing_id"):
            return self.network(project, network["existing_id"])
        prefix = self.module(row)
        records = [
            resource
            for address, resource in self.resources.items()
            if address.startswith(prefix)
            and resource.get("type") == "nebius_vpc_v1_network"
            and resource.get("mode") != "data"
        ]
        if len(records) != 1:
            raise AddressSpaceUnresolved("Planned VPC network identity is ambiguous")
        roots = self.configured_roots(row)
        values = _values(records[0])
        if values.get("id") and set(self.network(project, values["id"])) != set(roots):
            raise AddressSpaceUnresolved(
                "Planned network pool membership contradicts its component"
            )
        return roots

    def configured_roots(self, row: Mapping[str, Any]) -> tuple[str, ...]:
        project, prefix = self.project(row), self.module(row)
        network = row["inputs"]["network"]
        key = (project, prefix)
        if key not in self.networks:
            roots = [self.pool(project, item) for item in network.get("ipv4_private_pool_ids", [])]
            for cidr in network.get("ipv4_private_cidrs", []):
                matches = [
                    resource
                    for address, resource in self.resources.items()
                    if address.startswith(prefix)
                    and resource.get("type") == "nebius_vpc_v1_pool"
                    and resource.get("mode") != "data"
                    and [entry.get("cidr") for entry in _values(resource).get("cidrs") or []]
                    == [cidr]
                ]
                if len(matches) != 1:
                    raise AddressSpaceUnresolved("Planned VPC parent-pool ranges are unresolved")
                pool = matches[0]
                pool_values = _values(pool)
                if pool_values.get("id"):
                    roots.append(self.pool(project, pool_values["id"]))
                else:
                    identity = pool["address"]
                    source = pool_values.get("source_pool_id")
                    if source:
                        self.add(identity, (self.pool(project, source),), cidr)
                    elif _unknown(pool["change"].get("after_unknown", {}).get("source_pool_id")):
                        raise AddressSpaceUnresolved("Planned parent-pool source is unresolved")
                    self.pools[identity] = AddressPool(identity, _planned_blocks(pool))
                    roots.append(identity)
            if not roots:
                raise AddressSpaceUnresolved("Planned network private pools are unresolved")
            self.networks[key] = tuple(roots)
        return self.networks[key]

    def add(self, identity: str, pools: tuple[str, ...], cidr: str) -> None:
        demand = AddressDemand(identity, pools, cidr)
        previous = self.demands.get(identity)
        if previous is not None and previous != demand:
            raise AddressSpaceUnresolved("Conflicting address demand for one resource")
        self.demands[identity] = demand

    def subnet_roots(self, resource: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
        values = _values(resource)
        owner = self.owner(resource["address"])
        if owner is not None:
            project, roots = self.project(owner), self.roots(owner)
            network_id = values.get("network_id")
            if network_id and set(self.network(project, network_id)) != set(roots):
                raise AddressSpaceUnresolved(
                    "Planned subnet network contradicts its component binding"
                )
        else:
            project = str(values.get("parent_id") or "")
            network_id = values.get("network_id")
            if not project or not network_id:
                raise AddressSpaceUnresolved("Planned subnet source membership is unresolved")
            roots = self.network(project, network_id)
        if values.get("parent_id") and values["parent_id"] != project:
            raise AddressSpaceUnresolved("Planned subnet project contradicts its component")
        return project, roots


def _planned_blocks(resource: Mapping[str, Any]) -> tuple[AddressBlock, ...]:
    values = _values(resource)
    if _unknown(resource.get("change", {}).get("after_unknown", {}).get("cidrs")):
        raise AddressSpaceUnresolved("Planned pool CIDRs or block controls are unknown")
    if values.get("visibility") != "PRIVATE" or values.get("version") != "IPV4":
        raise AddressSpaceUnresolved("Planned pool is not a resolved private IPv4 pool")
    if not values.get("cidrs"):
        raise AddressSpaceUnresolved("Planned pool has no resolved address blocks")
    blocks = []
    for entry in values["cidrs"]:
        cidr = IPv4(entry["cidr"], strict=True)
        state = entry.get("state") or "AVAILABLE"
        if state == "STATE_UNSPECIFIED":
            state = "AVAILABLE"
        if state not in {"AVAILABLE", "DISABLED"}:
            raise AddressSpaceUnresolved("Planned pool availability is unresolved")
        if state == "AVAILABLE":
            blocks.append(AddressBlock(cidr, int(entry.get("max_mask_length") or 32)))
    return tuple(blocks)


def assess_plan_address_space(
    payload: Mapping[str, Any],
    plan: Mapping[str, Any],
    modules: Mapping[tuple[str, str], str],
    *,
    sdk_factory: Callable[..., Any] | None = None,
) -> AddressAssessment:
    from .nat_gateway_sdk import NatGatewaySDK

    changes = [
        resource
        for resource in plan.get("resource_changes", [])
        if _changed(resource)
        and not (
            resource.get("type") == "nebius_vpc_v1_allocation"
            and _values(resource).get("ipv4_public") is not None
        )
    ]
    if not changes or not nat_rows(payload):
        return AddressAssessment("feasible", "No new private address reservation demand")
    try:
        with ExitStack() as stack:
            inventory = _Inventory(
                payload, _resources(plan), modules, stack, sdk_factory or NatGatewaySDK
            )
            roots = {pool for row in nat_rows(payload) for pool in inventory.roots(row)}
            # Source pools of new network pools are also part of this proof.
            roots.update(pool for demand in inventory.demands.values() for pool in demand.pools)
            _include_pool_sources(inventory, changes, roots)
            updates = {}
            for resource in changes:
                if resource["type"] != "nebius_vpc_v1_pool":
                    continue
                values = _values(resource)
                if values.get("visibility") == "PUBLIC" or values.get("version") == "IPV6":
                    continue
                source = values.get("source_pool_id")
                if _unknown(resource["change"].get("after_unknown", {}).get("source_pool_id")):
                    raise AddressSpaceUnresolved("Planned pool source membership is unknown")
                before = resource["change"].get("before") or {}
                creating = "create" in resource["change"]["actions"]
                if source in roots:
                    if not values.get("cidrs") or _unknown(
                        resource["change"].get("after_unknown", {}).get("cidrs")
                    ):
                        raise AddressSpaceUnresolved(
                            "Planned child-pool demand or controls are unknown"
                        )
                    previous = (
                        []
                        if creating or source != before.get("source_pool_id")
                        else [entry["cidr"] for entry in before.get("cidrs") or []]
                    )
                    for index, cidr in enumerate(
                        _new_ranges([entry["cidr"] for entry in values["cidrs"]], previous)
                    ):
                        # Managed VPC single-CIDR pools were already collected above.
                        key = resource["address"]
                        if key not in inventory.demands:
                            inventory.add(f"{key}:{index}", (source,), cidr)
                identity = values.get("id")
                if identity in roots and not creating:
                    updates[identity] = _planned_blocks(resource)
            for resource in changes:
                if resource["type"] != "nebius_vpc_v1_subnet":
                    continue
                project, candidates = inventory.subnet_roots(resource)
                if not set(candidates) & roots:
                    continue
                values, change = _values(resource), resource["change"]
                spec = values.get("ipv4_private_pools") or {}
                previous = (
                    []
                    if "create" in change["actions"]
                    or (change.get("before") or {}).get("network_id") != values.get("network_id")
                    else _cidrs((change.get("before") or {}).get("ipv4_private_pools") or {})
                )
                for index, cidr in enumerate(_new_ranges(_cidrs(spec), previous)):
                    inventory.add(f"{resource['address']}:{index}", candidates, cidr)
            for resource in changes:
                if resource["type"] == "nebius_vpc_v1_allocation":
                    _allocation_demand(inventory, resource, roots)
                elif resource["type"] in {
                    "nebius_compute_v1_instance",
                    "nebius_mk8s_v1_cluster",
                    "nebius_mk8s_v1_node_group",
                }:
                    _implicit_demand(inventory, resource, roots)
            selected = {pool for demand in inventory.demands.values() for pool in demand.pools}
            inventory.load(selected)
            for identity, blocks in updates.items():
                if identity in selected:
                    inventory.pools[identity] = replace(inventory.pools[identity], blocks=blocks)
            return assess_address_space(
                tuple(inventory.pools[pool] for pool in sorted(selected)),
                tuple(inventory.demands.values()),
            )
    except (AddressSpaceUnresolved, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # SDK request errors are sanitized by NatGatewaySDK.call; never turn a
        # denied or partial inventory into an empty pool.
        return AddressAssessment("unresolved", f"Address-space evidence unavailable: {exc}")


def _allocation_demand(inventory: _Inventory, resource: Mapping[str, Any], roots: set[str]) -> None:
    values = _values(resource)
    if values.get("ipv4_public") is not None:
        return
    spec = values.get("ipv4_private")
    if not isinstance(spec, dict):
        raise AddressSpaceUnresolved("Planned allocation address type is unresolved")
    pool, subnet = spec.get("pool_id"), spec.get("subnet_id")
    candidates: tuple[str, ...]
    if pool and subnet:
        raise AddressSpaceUnresolved("Allocation has conflicting pool and subnet sources")
    if pool:
        candidates = (pool,)
    elif subnet:
        matches = [r for r in inventory.resources.values() if _values(r).get("id") == subnet]
        if len(matches) == 1:
            selected = matches[0]
            if _dedicated_subnet(selected):
                return  # The child reservation already accounts for its contents.
            _, candidates = inventory.subnet_roots(selected)
        else:
            project = values.get("parent_id")
            if not project:
                raise AddressSpaceUnresolved("Planned allocation project is unknown")
            from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient

            sdk = inventory.sdk(project)
            obj = sdk.call(SubnetServiceClient(sdk.sdk).get, GetSubnetRequest(id=subnet))
            if obj.metadata.id != subnet or obj.metadata.parent_id != project:
                raise AddressSpaceUnresolved("Allocation subnet identity is inconsistent")
            if (
                obj.spec.check_presence("ipv4_private_pools")
                and not obj.spec.ipv4_private_pools.use_network_pools
            ):
                if not obj.spec.ipv4_private_pools.pools:
                    raise AddressSpaceUnresolved("Subnet has no proven dedicated private pools")
                return
            candidates = inventory.network(project, obj.spec.network_id)
    else:
        owner = inventory.owner(resource["address"])
        if (
            owner is not None
            and owner["id"] == "nat-gateway"
            and resource["address"] == inventory.module(owner) + "nebius_vpc_v1_allocation.private"
        ):
            address = inventory.module(owner) + "nebius_vpc_v1_subnet.gateway"
            gateway_subnet = inventory.resources.get(address)
            if gateway_subnet and _dedicated_subnet(gateway_subnet):
                return
        raise AddressSpaceUnresolved("Planned allocation source membership is unresolved")
    if not set(candidates) & roots:
        return
    unknown = resource["change"].get("after_unknown", {}).get("ipv4_private") or {}
    if _unknown(unknown.get("cidr") if isinstance(unknown, dict) else unknown):
        raise AddressSpaceUnresolved("Planned private allocation prefix is unknown")
    inventory.add(resource["address"], candidates, spec.get("cidr") or "/32")


def _implicit_demand(inventory: _Inventory, resource: Mapping[str, Any], roots: set[str]) -> None:
    """Bound implicit compute demand; unmodelled MK8s parent-pool demand is unresolved."""
    values, kind = _values(resource), resource["type"]
    project = values.get("parent_id")
    is_vm = kind == "nebius_compute_v1_instance"
    owners = [
        row
        for row in inventory.rows.values()
        if (module := inventory.modules.get((row["id"], row["instance_id"])))
        and resource["address"].startswith(f"module.{module}.")
    ]
    owner = owners[0] if len(owners) == 1 else None
    if is_vm:
        interfaces = values.get("network_interfaces")
    elif kind == "nebius_mk8s_v1_cluster":
        interfaces = [values.get("control_plane") or {}]
    else:
        interfaces = (values.get("template") or {}).get("network_interfaces")
        # A node group's parent is its cluster, not the project. Use the
        # rendered component's project scope, then verify subnet identity below.
        if owner is None:
            raise AddressSpaceUnresolved("Implicit node-group project scope is unresolved")
        project = (owner["inputs"].get("cluster") or {}).get("parent_id") or inventory.project(
            owner
        )
    if not project or not isinstance(interfaces, list) or not interfaces:
        raise AddressSpaceUnresolved("Implicit private-address sources are unresolved")
    for index, interface in enumerate(interfaces):
        ip = interface.get("ip_address") or {}
        if is_vm and ip.get("allocation_id"):
            continue  # Stable allocation: its resource owns any new demand.
        if (
            not interface.get("subnet_id")
            and len(interfaces) == 1
            and owner is not None
            and _bound_isolated_subnet(inventory, owner, resource, project)
        ):
            continue
        address = f"{resource['address']}:implicit:{index}"
        # The canonical NAT VM binds its one NIC to the module's stable
        # allocation, whose unknown subnet ID is handled by that owner.
        if (
            is_vm
            and owner is not None
            and owner["id"] == "nat-gateway"
            and len(interfaces) == 1
            and resource["address"]
            == inventory.module(owner) + "module.vm.nebius_compute_v1_instance.vm"
        ):
            address = inventory.module(owner) + "nebius_vpc_v1_allocation.private"
        synthetic = {
            "address": address,
            "change": {
                "after": {
                    "parent_id": project,
                    "ipv4_private": {"subnet_id": interface.get("subnet_id"), "cidr": "/32"},
                },
                "after_unknown": {"ipv4_private": {"cidr": not is_vm}},
            },
        }
        try:
            _allocation_demand(inventory, synthetic, roots)
        except AddressSpaceUnresolved as exc:
            raise AddressSpaceUnresolved(
                f"Implicit private-address demand is unresolved for {resource['address']}: {exc}"
            ) from None


def _bound_isolated_subnet(
    inventory: _Inventory, owner: Mapping[str, Any], resource: Mapping[str, Any], project: str
) -> bool:
    """Prove a computed workload ID from its canonical VPC output binding."""
    kind, inputs = resource["type"], owner["inputs"]
    bindings = owner.get("bindings", {})
    prefix = inventory.module(owner)
    if owner["id"] == "vm" and kind == "nebius_compute_v1_instance":
        if resource["address"] != prefix + "nebius_compute_v1_instance.vm":
            return False
        path = "inputs.subnet_id"
    elif owner["id"] == "mk8s" and kind == "nebius_mk8s_v1_cluster":
        if resource["address"] != prefix + "nebius_mk8s_v1_cluster.this":
            return False
        path = "inputs.cluster.subnet_id"
        override = "inputs.cluster.control_plane.subnet_id"
        if override in bindings or "subnet_id" in (
            (inputs.get("cluster") or {}).get("control_plane") or {}
        ):
            path = override
    elif owner["id"] == "mk8s" and kind == "nebius_mk8s_v1_node_group":
        key = resource.get("index")
        group = (inputs.get("node_groups") or {}).get(key) or {}
        if (
            not resource["address"].startswith(prefix + "nebius_mk8s_v1_node_group.this[")
            or not key
            or group.get("network_interfaces") is not None
            or any(
                path.startswith(f"inputs.node_groups.{key}.network_interfaces") for path in bindings
            )
        ):
            return False
        path = f"inputs.node_groups.{key}.subnet_id"
        if path not in bindings:
            if group.get("subnet_id"):
                return False
            path = "inputs.cluster.subnet_id"
    else:
        return False
    binding = bindings.get(path) or {}
    if (
        binding.get("source_component") != "vpc"
        or binding.get("source_output") != "subnets"
        or binding.get("attribute") != "id"
    ):
        return False
    source = inventory.rows.get(binding.get("source_instance"))
    if source is None or source["id"] != "vpc" or inventory.project(source) != project:
        return False
    prefix = inventory.module(source) + "nebius_vpc_v1_subnet.this["
    matches = [
        item
        for item in inventory.resources.values()
        if item["address"].startswith(prefix) and item.get("index") == binding.get("key")
    ]
    return len(matches) == 1 and _dedicated_subnet(matches[0])


def assess_config_address_space(
    payload: Mapping[str, Any], *, sdk_factory: Callable[..., Any] | None = None
) -> AddressAssessment:
    """Advisory new-reservation preview; only the actual plan can prove retention."""
    rows = [row for row in payload["infra"]["components"] if row.get("enabled")]
    modules = {
        (row["id"], row["instance_id"]): f"preview_{index}" for index, row in enumerate(rows)
    }
    changes = []

    def add(address: str, kind: str, values: dict[str, Any]) -> None:
        changes.append(
            {
                "address": address,
                "type": "nebius_vpc_v1_" + kind,
                "mode": "managed",
                "change": {"actions": ["create"], "after": values, "after_unknown": {}},
            }
        )

    for row in rows:
        prefix, inputs = f"module.{modules[row['id'], row['instance_id']]}.", row["inputs"]
        project = inputs.get("parent_id") or payload["client_info"]["nebius"]["project_id"]
        if row["id"] == "vpc":
            network = inputs["network"]
            network_id = network.get("existing_id")
            if not network_id:
                add(prefix + "network", "network", {"parent_id": project})
                for index, cidr in enumerate(network.get("ipv4_private_cidrs", [])):
                    add(
                        prefix + f"pool[{index}]",
                        "pool",
                        {
                            "parent_id": project,
                            "visibility": "PRIVATE",
                            "version": "IPV4",
                            "source_pool_id": network.get("ipv4_private_source_pool_id"),
                            "cidrs": [{"cidr": cidr}],
                        },
                    )
            for key, subnet in inputs.get("subnets", {}).items():
                add(
                    prefix + f"subnet[{key}]",
                    "subnet",
                    {
                        "parent_id": project,
                        "network_id": network_id,
                        "ipv4_private_pools": {
                            "use_network_pools": False,
                            "pools": [
                                {
                                    "cidrs": [
                                        {"cidr": c} for c in subnet.get("ipv4_private_cidrs", [])
                                    ]
                                }
                            ],
                        },
                    },
                )
        elif row["id"] == "nat-gateway":
            add(
                prefix + "nebius_vpc_v1_subnet.gateway",
                "subnet",
                {
                    "parent_id": project,
                    "network_id": inputs.get("network_id"),
                    "ipv4_private_pools": {
                        "use_network_pools": False,
                        "pools": [{"cidrs": [{"cidr": inputs.get("gateway_subnet_cidr", "/28")}]}],
                    },
                },
            )
    return assess_plan_address_space(
        payload, {"resource_changes": changes}, modules, sdk_factory=sdk_factory
    )
