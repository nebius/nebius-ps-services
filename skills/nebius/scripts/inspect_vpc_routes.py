#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
sys.path.insert(0, str(ASSET_DIR))

from sdk.inspection import Report
from sdk.runtime import (
    CloudError,
    collect_pages,
    init_nebius_sdk,
    rpc,
    verify_identity,
)


def _cidr_strings(values: list[Any] | None) -> list[str]:
    cidrs: list[str] = []
    for value in values or []:
        text = str(getattr(value, "cidr", value)).strip()
        if text:
            cidrs.append(text)
    return cidrs


def _explicit_subnet_cidrs(subnet_obj: Any) -> list[str]:
    subnet_spec = getattr(subnet_obj, "spec", None)
    ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
    if not ipv4_private_pools or getattr(
        ipv4_private_pools, "use_network_pools", False
    ):
        return []
    cidrs: list[str] = []
    for pool in getattr(ipv4_private_pools, "pools", []) or []:
        cidrs.extend(_cidr_strings(getattr(pool, "cidrs", None)))
    return cidrs


def _subnet_mode(subnet_obj: Any) -> str:
    subnet_spec = getattr(subnet_obj, "spec", None)
    ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
    if getattr(ipv4_private_pools, "use_network_pools", False):
        return "inherit_network_pool"
    return "explicit"


def _route_next_hop_text(route_obj: Any) -> str:
    route_spec = getattr(route_obj, "spec", None)
    next_hop = getattr(route_spec, "next_hop", None) if route_spec else None
    if not next_hop:
        return "unknown"

    if hasattr(next_hop, "default_egress_gateway") and getattr(
        next_hop, "default_egress_gateway", False
    ):
        return "default-egress"
    if hasattr(next_hop, "default_internet_gateway") and getattr(
        next_hop, "default_internet_gateway", False
    ):
        return "default-gateway"
    if hasattr(next_hop, "allocation"):
        allocation = next_hop.allocation
        alloc_id = getattr(allocation, "id", None)
        if alloc_id:
            return f"allocation:{alloc_id}"
    if hasattr(next_hop, "instance"):
        instance = next_hop.instance
        instance_id = getattr(instance, "id", None)
        if instance_id:
            return f"instance:{instance_id}"
    return "unknown"


def _route_destination_text(route_obj: Any) -> str:
    route_spec = getattr(route_obj, "spec", None)
    destination = getattr(route_spec, "destination", None) if route_spec else None
    cidr = getattr(destination, "cidr", None) if destination else None
    return str(cidr or "unknown")


def _build_report(
    project_id: str,
    network_id: str | None,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
) -> list[dict[str, Any]]:
    from nebius.api.nebius.vpc.v1 import (
        GetRouteTableRequest,
        ListNetworksRequest,
        ListRoutesRequest,
        ListRouteTablesRequest,
        ListSubnetsByNetworkRequest,
        NetworkServiceClient,
        RouteServiceClient,
        RouteTableServiceClient,
        SubnetServiceClient,
    )

    client = init_nebius_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        parent_id=project_id,
    )

    try:
        network_client = NetworkServiceClient(client)
        route_table_client = RouteTableServiceClient(client)
        route_client = RouteServiceClient(client)
        subnet_client = SubnetServiceClient(client)

        route_tables_by_id: dict[str, Any] = {}
        listed_route_tables = collect_pages(
            route_table_client.list,
            lambda token: ListRouteTablesRequest(
                parent_id=project_id, page_token=token
            ),
        )
        for rt_obj in listed_route_tables:
            rt_id = verify_identity(rt_obj, parent_id=project_id)
            route_tables_by_id[rt_id] = rt_obj

        report: list[dict[str, Any]] = []
        network_list = collect_pages(
            network_client.list,
            lambda token: ListNetworksRequest(parent_id=project_id, page_token=token),
        )
        for network_obj in network_list:
            network_meta = getattr(network_obj, "metadata", None)
            current_network_id = verify_identity(network_obj, parent_id=project_id)
            if network_id and str(current_network_id) != network_id:
                continue

            subnet_list = collect_pages(
                subnet_client.list_by_network,
                lambda token, current_network_id=current_network_id: (
                    ListSubnetsByNetworkRequest(
                        network_id=str(current_network_id), page_token=token
                    )
                ),
            )

            subnet_rows: list[dict[str, Any]] = []
            subnet_route_refs: dict[str, list[str]] = {}
            network_route_table_ids: set[str] = set()

            for subnet_obj in subnet_list:
                subnet_meta = getattr(subnet_obj, "metadata", None)
                subnet_status = getattr(subnet_obj, "status", None)
                subnet_spec = getattr(subnet_obj, "spec", None)
                verify_identity(subnet_obj, parent_id=project_id)
                if subnet_spec.network_id != current_network_id:
                    raise CloudError("SUBNET_NETWORK_MISMATCH")
                route_info = getattr(subnet_status, "route_table", None)
                route_table_id = getattr(route_info, "id", None) if route_info else None
                if not route_table_id:
                    raise CloudError("SUBNET_ROUTE_TABLE_UNRESOLVED")
                route_is_default = (
                    bool(getattr(route_info, "default", False)) if route_info else False
                )

                subnet_id = getattr(subnet_meta, "id", None) or getattr(
                    subnet_obj, "id", None
                )
                subnet_name = getattr(subnet_meta, "name", None)

                if route_table_id:
                    subnet_route_refs.setdefault(str(route_table_id), []).append(
                        subnet_name or str(subnet_id or "unknown-subnet")
                    )
                    network_route_table_ids.add(str(route_table_id))

                subnet_rows.append(
                    {
                        "subnet_id": subnet_id,
                        "subnet_name": subnet_name,
                        "allocation_mode": _subnet_mode(subnet_obj),
                        "explicit_private_cidrs": _explicit_subnet_cidrs(subnet_obj),
                        "route_table_id": route_table_id,
                        "route_table_default": route_is_default,
                        "spec_route_table_id": getattr(
                            subnet_spec, "route_table_id", None
                        ),
                    }
                )

            route_table_rows: list[dict[str, Any]] = []
            for route_table_id in sorted(network_route_table_ids):
                rt_obj = route_tables_by_id.get(route_table_id)
                if rt_obj is None:
                    rt_obj = rpc(
                        route_table_client.get, GetRouteTableRequest(id=route_table_id)
                    )
                    route_tables_by_id[route_table_id] = rt_obj

                verify_identity(
                    rt_obj, parent_id=project_id, resource_id=route_table_id
                )
                if rt_obj.spec.network_id != current_network_id:
                    raise CloudError("ROUTE_TABLE_NETWORK_MISMATCH")
                rt_meta = (
                    getattr(rt_obj, "metadata", None) if rt_obj is not None else None
                )
                rt_spec = getattr(rt_obj, "spec", None) if rt_obj is not None else None
                rt_name = getattr(rt_meta, "name", None) or "unknown"

                routes_resp = collect_pages(
                    route_client.list,
                    lambda token, route_table_id=route_table_id: ListRoutesRequest(
                        parent_id=route_table_id, page_token=token
                    ),
                )
                route_rows: list[dict[str, Any]] = []
                for route_obj in routes_resp:
                    verify_identity(route_obj, parent_id=route_table_id)
                    route_rows.append(
                        {
                            "route_id": getattr(
                                getattr(route_obj, "metadata", None), "id", None
                            )
                            or getattr(route_obj, "id", None),
                            "route_name": getattr(
                                getattr(route_obj, "metadata", None), "name", None
                            ),
                            "destination": _route_destination_text(route_obj),
                            "next_hop": _route_next_hop_text(route_obj),
                        }
                    )
                route_rows.sort(
                    key=lambda item: (item["destination"], item["route_name"] or "")
                )

                route_table_rows.append(
                    {
                        "route_table_id": route_table_id,
                        "route_table_name": rt_name,
                        "network_id": getattr(rt_spec, "network_id", None),
                        "attached_subnets": sorted(
                            subnet_route_refs.get(route_table_id, [])
                        ),
                        "routes": route_rows,
                    }
                )

            route_table_rows.sort(
                key=lambda item: (
                    item["route_table_name"] or "",
                    item["route_table_id"],
                )
            )
            subnet_rows.sort(
                key=lambda item: (item["subnet_name"] or "", item["subnet_id"] or "")
            )

            report.append(
                {
                    "project_id": project_id,
                    "network_id": str(current_network_id),
                    "network_name": getattr(network_meta, "name", None),
                    "subnets": subnet_rows,
                    "route_tables": route_table_rows,
                }
            )

        if network_id and not report:
            raise CloudError("NETWORK_NOT_FOUND_IN_PROJECT")
        report.sort(key=lambda item: (item["network_name"] or "", item["network_id"]))
        return report
    finally:
        client.sync_close(timeout=10.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Nebius VPC route tables, subnet attachments, and routes for a project."
    )
    parser.add_argument("--project-id", required=True, help="Nebius project ID")
    parser.add_argument("--network-id", help="Optional network ID filter")
    parser.add_argument("--profile", help="Optional Nebius CLI profile")
    parser.add_argument("--endpoint", help="Optional Nebius API endpoint override")
    parser.add_argument(
        "--config-file", type=Path, help="Optional Nebius CLI config file path"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = parser.parse_args()

    config_file = args.config_file.expanduser().resolve() if args.config_file else None
    report = Report({"project_id": args.project_id, "network_id": args.network_id})
    report.collect(
        "vpc",
        lambda: _build_report(
            project_id=args.project_id,
            network_id=args.network_id,
            profile=args.profile,
            endpoint=args.endpoint,
            config_file=config_file,
        ),
    )
    return report.emit(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
