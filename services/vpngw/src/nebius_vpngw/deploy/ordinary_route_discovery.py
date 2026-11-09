"""Read-only SDK and configured-peer boundary for ordinary route planning."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, cast

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .ordinary_route_ownership import RouteJournal
from .ordinary_route_policy import Policy, freeze, prefix
from .replacement_cloud import message_digest, resource_id, single_nic_status
from .route_manager import RouteManager
from .vm_ha_cloud import (
    InstanceCloudState,
    allocation_observation,
    instance_cloud_state,
    nebius_request_error_code_is,
    vm_ha_request_kwargs,
)


def _get(service: Any, request: Any) -> Any:
    return service.get(request, **vm_ha_request_kwargs()).wait()


def _ready(resource: Any) -> bool:
    state = getattr(getattr(resource, "status", None), "state", None)
    return getattr(state, "name", str(state)) == "READY"


def _list(service: Any, request_type: Any, project: str) -> tuple[Any, ...]:
    return tuple(
        collect_nebius_pages(
            lambda token: service.list(
                request_type(parent_id=project, page_size=100, page_token=token),
                **vm_ha_request_kwargs(),
            ),
            context="Ordinary route resource",
            item_identity=nebius_resource_id,
        )
    )


@dataclass
class Discovery:
    manager: Any
    deployment: Any
    local: dict
    journal: RouteJournal
    routes: RouteManager
    summarize: bool = False
    force_copy: bool = False

    def observe_infrastructure(self, *, completed: bool = False) -> dict:
        from nebius.api.nebius.vpc import v1 as vpc

        sdk = self.manager._get_client()
        if sdk is None or not self.manager.project_id:
            raise RuntimeError("Ordinary route discovery requires an exact project and SDK")
        project = self.manager.project_id
        subnets_client = vpc.SubnetServiceClient(sdk)
        networks_client = vpc.NetworkServiceClient(sdk)
        network_id = str(self.local.get("gateway_group", {}).get("network_id") or "")
        if not network_id:
            try:
                subnet = subnets_client.get_by_name(
                    vpc.GetSubnetByNameRequest(
                        parent_id=project,
                        name=self.routes._gateway_subnet_name(self.local),
                    ),
                    **vm_ha_request_kwargs(),
                ).wait()
                if subnet.metadata.parent_id != project or not resource_id(subnet):
                    raise RuntimeError("Gateway subnet discovery returned foreign identity")
                network_id = str(subnet.spec.network_id)
            except Exception as error:
                if not nebius_request_error_code_is(error, "NOT_FOUND"):
                    raise
                networks = _list(networks_client, vpc.ListNetworksRequest, project)
                defaults = [
                    network for network in networks if network.metadata.name == "default-network"
                ]
                candidates = defaults or list(networks)
                if len(candidates) != 1:
                    raise RuntimeError("Ordinary route VPC discovery is ambiguous")
                network_id = resource_id(candidates[0])
        network = _get(networks_client, vpc.GetNetworkRequest(id=network_id))
        if (
            resource_id(network) != network_id
            or network.metadata.parent_id != project
            or not _ready(network)
        ):
            raise RuntimeError("Ordinary route network identity changed")
        subnets = _list(subnets_client, vpc.ListSubnetsRequest, project)
        if any(subnet.metadata.parent_id != project for subnet in subnets):
            raise RuntimeError("Subnet inventory crossed project authority")
        subnet_map = {resource_id(subnet): subnet for subnet in subnets}
        prefixes: set[str] = set()
        for reference in network.spec.ipv4_private_pools.pools:
            pool = _get(vpc.PoolServiceClient(sdk), vpc.GetPoolRequest(id=reference.id))
            if resource_id(pool) != reference.id or not pool.spec.cidrs:
                raise RuntimeError("VPC private pool observation is incomplete")
            prefixes.update(prefix(cidr.cidr) for cidr in pool.spec.cidrs)
        for subnet in subnets:
            if subnet.spec.network_id == network_id:
                prefixes.update(str(cidr) for cidr in self.routes._effective_subnet_cidrs(subnet))
        policy = freeze(
            self.local, network_prefixes=tuple(sorted(prefixes)), summarize=self.summarize
        )
        selected_raw, _diagnostics = self.routes._select_local_prefix_subnets(
            subnets,
            [
                ipaddress.IPv4Network(p)
                for p in self.local.get("gateway", {}).get("local_prefixes", [])
            ],
            target_network_id=network_id,
            gateway_subnet_name=self.routes._gateway_subnet_name(self.local),
        )
        selected = cast(list[tuple[Any, list[ipaddress.IPv4Network]]], selected_raw)
        if not selected:
            raise RuntimeError("No workload subnets match the approved local-prefix intent")
        targets = []
        selected_ids = {resource_id(subnet) for subnet, _ in selected}
        for subnet, _ in selected:
            if not _ready(subnet):
                raise RuntimeError("Workload subnet is not ready")
            table = str(subnet.status.route_table.id or network.status.default_route_table_id)
            if not table:
                raise RuntimeError("Workload subnet has no exact current route table")
            targets.append(
                {
                    "subnet_id": resource_id(subnet),
                    "table_id": table,
                    "spec_sha256": message_digest(subnet.spec),
                }
            )
        active_tables = tuple(sorted({target["table_id"] for target in targets}))
        ledger = self.journal.inventory()
        transaction = self.journal.transaction()
        retained_tables = set()
        if transaction and (transaction["phase"] != "complete" or completed):
            retained_tables.update(transaction["intent"]["infrastructure"]["table_ids"])
            retained_tables.update(
                operation["resource_id"]
                for operation in transaction["operations"].values()
                if operation["request_type"] == "CreateRouteTableRequest"
                and operation["status"] == "settled"
            )
        table_ids = sorted(
            set(active_tables)
            | retained_tables
            | {record["snapshot"]["parent_id"] for record in ledger.values()}
        )
        tables: dict[str, dict] = {}
        inventory: dict[str, tuple[Any, ...]] = {}
        conversions: list[dict] = []
        table_resources = {}
        for identifier in table_ids:
            table = _get(vpc.RouteTableServiceClient(sdk), vpc.GetRouteTableRequest(id=identifier))
            if (
                resource_id(table) != identifier
                or table.metadata.parent_id != project
                or table.spec.network_id != network_id
                or not _ready(table)
            ):
                raise RuntimeError("Route table inventory crossed approved project or VPC")
            assigned = {
                resource_id(subnet)
                for subnet in subnets
                if subnet.spec.network_id == network_id
                and str(subnet.status.route_table.id or network.status.default_route_table_id)
                == identifier
            }
            if set(table.status.assignment.subnets) != assigned:
                raise RuntimeError("Route table assignment evidence is incomplete")
            tables[identifier] = {
                "spec_sha256": message_digest(table.spec),
                "default": bool(table.status.default),
                "subnets": sorted(assigned),
            }
            table_resources[identifier] = table
            inventory[identifier] = _list(
                vpc.RouteServiceClient(sdk), vpc.ListRoutesRequest, identifier
            )
            if identifier in active_tables and (
                self.force_copy or table.status.default or assigned - selected_ids
            ):
                conversions.extend(target for target in targets if target["table_id"] == identifier)
        from .ordinary_route_conversion import copy_inventory
        from .ordinary_route_plan import request_record
        from .vm_ha_cloud import clone_nebius_sdk_message

        conversion_requests: dict[str, dict] = {}
        for target in conversions:
            source = target["table_id"]
            subnet = subnet_map[target["subnet_id"]]
            if subnet.metadata.resource_version <= 0:
                raise RuntimeError(
                    "Subnet conversion requires an exact optimistic-concurrency revision"
                )
            conversion = conversion_requests.setdefault(
                target["subnet_id"],
                {"source": source, "routes": copy_inventory(inventory[source]), "subnets": {}},
            )
            conversion["subnets"][target["subnet_id"]] = request_record(
                vpc.UpdateSubnetRequest(
                    metadata=clone_nebius_sdk_message(subnet.metadata),
                    spec=clone_nebius_sdk_message(subnet.spec),
                )
            )
        return {
            "infrastructure": {
                "project": project,
                "network_id": network_id,
                "network_spec_sha256": message_digest(network.spec),
                "table_ids": table_ids,
                "tables": tables,
                "targets": sorted(targets, key=lambda item: item["subnet_id"]),
                "gateways": {},
                "excluded_prefixes": list(policy.excluded_prefixes),
            },
            "allocations": {},
            "routes": inventory,
            "active_tables": active_tables,
            "conversions": sorted(conversions, key=lambda item: item["subnet_id"]),
            "conversion_requests": conversion_requests,
            "table_resources": table_resources,
            "subnet_resources": subnet_map,
            "policy": policy,
        }

    def observe(self, *, completed: bool = False) -> dict:
        """Add exact Running gateway ownership to the frozen VPC boundary."""
        observation = self.observe_infrastructure(completed=completed)
        gateways, allocations = self.gateway_owners(
            observation, tuple(self.deployment.iter_instance_configs())
        )
        observation["infrastructure"]["gateways"] = gateways
        observation["allocations"] = allocations
        return observation

    def gateway_owners(self, observation: dict, instances: tuple) -> tuple[dict, dict]:
        """Observe only the supplied configured members, with exact ownership."""
        from nebius.api.nebius.vpc import v1 as vpc

        sdk = self.manager._get_client()
        project = self.manager.project_id
        network_id = observation["infrastructure"]["network_id"]
        subnet_map = observation["subnet_resources"]
        found = self.manager.inspect_ordinary_targets(instance.hostname for instance in instances)
        if set(found) != {instance.hostname for instance in instances}:
            raise RuntimeError("Ordinary routes require all planned gateway identities")
        gateways, allocations = {}, {}
        for instance in instances:
            compute_id, _transport = found[instance.hostname]
            compute = self.manager.get_ha_instance(compute_id)
            if (
                resource_id(compute) != compute_id
                or compute.metadata.parent_id != project
                or instance_cloud_state(compute) is not InstanceCloudState.RUNNING
            ):
                raise RuntimeError("Ordinary route gateway is not the exact Running Compute")
            nic = single_nic_status(compute)
            spec_nic = next(
                item for item in compute.spec.network_interfaces if item.name == nic.name
            )
            subnet = subnet_map.get(str(spec_nic.subnet_id))
            if subnet is None or subnet.spec.network_id != network_id:
                raise RuntimeError("Ordinary route gateway NIC crossed the selected VPC")
            allocation_id = str(nic.ip_address.allocation_id)
            allocation = _get(
                vpc.AllocationServiceClient(sdk), vpc.GetAllocationRequest(id=allocation_id)
            )
            owner = allocation_observation(allocation_id, allocation).owner
            if (
                resource_id(allocation) != allocation_id
                or allocation.metadata.parent_id != project
                or owner is None
                or owner.instance_id != compute_id
                or owner.network_interface_name != nic.name
                or allocation.status.details.subnet_id != subnet.metadata.id
            ):
                raise RuntimeError("Gateway primary allocation ownership is not exact")
            address = ipaddress.ip_network(
                str(allocation.status.details.allocated_cidr), strict=True
            )
            if (
                address.version != 4
                or address.prefixlen != 32
                or str(address.network_address) != str(nic.ip_address.address).split("/")[0]
            ):
                raise RuntimeError("Gateway route next hop lacks an exact IPv4 host allocation")
            allocations[instance.instance_index] = allocation_id
            gateways[str(instance.instance_index)] = {
                "name": instance.hostname,
                "compute_id": compute_id,
                "nic": nic.name,
                "subnet_id": subnet.metadata.id,
                "allocation_id": allocation_id,
            }
        return gateways, allocations

    def learned(self, policy: Policy) -> dict[str, tuple[str, ...]]:
        configured = {
            connection["name"]: connection for connection in self.local.get("connections", [])
        }
        return {
            connection.name: tuple(
                self.routes._get_bgp_learned_routes(
                    self.deployment, configured[connection.name], self.local
                )
            )
            for connection in policy.connections
            if connection.mode == "bgp"
        }
