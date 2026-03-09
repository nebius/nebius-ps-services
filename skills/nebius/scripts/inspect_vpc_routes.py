#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ASSET_IAM_DIR = Path(__file__).resolve().parents[1] / "assets" / "iam"
sys.path.insert(0, str(ASSET_IAM_DIR))

from iam_api import init_nebius_sdk  # type: ignore  # noqa: E402


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
    if not ipv4_private_pools or getattr(ipv4_private_pools, "use_network_pools", False):
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
    return str(next_hop)


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
        listed_route_tables = route_table_client.list(ListRouteTablesRequest(parent_id=project_id)).wait()
        for rt_obj in getattr(listed_route_tables, "items", []) or []:
            rt_id = getattr(getattr(rt_obj, "metadata", None), "id", None) or getattr(rt_obj, "id", None)
            if rt_id:
                route_tables_by_id[str(rt_id)] = rt_obj

        report: list[dict[str, Any]] = []
        network_list = network_client.list(ListNetworksRequest(parent_id=project_id)).wait()
        for network_obj in getattr(network_list, "items", []) or []:
            network_meta = getattr(network_obj, "metadata", None)
            current_network_id = getattr(network_meta, "id", None) or getattr(network_obj, "id", None)
            if not current_network_id:
                continue
            if network_id and str(current_network_id) != network_id:
                continue

            subnet_list = subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(network_id=str(current_network_id))
            ).wait()

            subnet_rows: list[dict[str, Any]] = []
            subnet_route_refs: dict[str, list[str]] = {}
            network_route_table_ids: set[str] = set()

            for subnet_obj in getattr(subnet_list, "items", []) or []:
                subnet_meta = getattr(subnet_obj, "metadata", None)
                subnet_status = getattr(subnet_obj, "status", None)
                subnet_spec = getattr(subnet_obj, "spec", None)
                route_info = getattr(subnet_status, "route_table", None)
                route_table_id = getattr(route_info, "id", None) if route_info else None
                route_is_default = bool(getattr(route_info, "default", False)) if route_info else False

                subnet_id = getattr(subnet_meta, "id", None) or getattr(subnet_obj, "id", None)
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
                        "spec_route_table_id": getattr(subnet_spec, "route_table_id", None),
                    }
                )

            route_table_rows: list[dict[str, Any]] = []
            for route_table_id in sorted(network_route_table_ids):
                rt_obj = route_tables_by_id.get(route_table_id)
                if rt_obj is None:
                    try:
                        rt_obj = route_table_client.get(GetRouteTableRequest(id=route_table_id)).wait()
                        route_tables_by_id[route_table_id] = rt_obj
                    except Exception:
                        rt_obj = None

                rt_meta = getattr(rt_obj, "metadata", None) if rt_obj is not None else None
                rt_spec = getattr(rt_obj, "spec", None) if rt_obj is not None else None
                rt_name = getattr(rt_meta, "name", None) or "unknown"

                routes_resp = route_client.list(ListRoutesRequest(parent_id=route_table_id)).wait()
                route_rows: list[dict[str, Any]] = []
                for route_obj in getattr(routes_resp, "items", []) or []:
                    route_rows.append(
                        {
                            "route_id": getattr(getattr(route_obj, "metadata", None), "id", None)
                            or getattr(route_obj, "id", None),
                            "route_name": getattr(getattr(route_obj, "metadata", None), "name", None),
                            "destination": _route_destination_text(route_obj),
                            "next_hop": _route_next_hop_text(route_obj),
                        }
                    )
                route_rows.sort(key=lambda item: (item["destination"], item["route_name"] or ""))

                route_table_rows.append(
                    {
                        "route_table_id": route_table_id,
                        "route_table_name": rt_name,
                        "network_id": getattr(rt_spec, "network_id", None),
                        "attached_subnets": sorted(subnet_route_refs.get(route_table_id, [])),
                        "routes": route_rows,
                    }
                )

            route_table_rows.sort(key=lambda item: (item["route_table_name"] or "", item["route_table_id"]))
            subnet_rows.sort(key=lambda item: (item["subnet_name"] or "", item["subnet_id"] or ""))

            report.append(
                {
                    "project_id": project_id,
                    "network_id": str(current_network_id),
                    "network_name": getattr(network_meta, "name", None),
                    "subnets": subnet_rows,
                    "route_tables": route_table_rows,
                }
            )

        report.sort(key=lambda item: (item["network_name"] or "", item["network_id"]))
        return report
    finally:
        try:
            client.sync_close()
        except Exception:
            pass


def _format_text(report: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for network in report:
        lines.append(f"Project: {network['project_id']}")
        lines.append(f"Network: {network['network_name']} ({network['network_id']})")
        lines.append("Subnets:")
        for subnet in network["subnets"]:
            explicit = ", ".join(subnet["explicit_private_cidrs"]) or "none"
            rt_id = subnet["route_table_id"] or "none"
            lines.append(f"- {subnet['subnet_name']} ({subnet['subnet_id']})")
            lines.append(f"  mode: {subnet['allocation_mode']}")
            lines.append(f"  explicit_private_cidrs: {explicit}")
            lines.append(f"  effective_route_table_id: {rt_id}")
            lines.append(f"  route_table_default: {str(subnet['route_table_default']).lower()}")
            if subnet["spec_route_table_id"]:
                lines.append(f"  spec_route_table_id: {subnet['spec_route_table_id']}")

        lines.append("Route tables:")
        if network["route_tables"]:
            for rt in network["route_tables"]:
                attached = ", ".join(rt["attached_subnets"]) or "none"
                lines.append(f"- {rt['route_table_name']} ({rt['route_table_id']})")
                lines.append(f"  attached_subnets: {attached}")
                if rt["routes"]:
                    for route in rt["routes"]:
                        lines.append(
                            f"  route: {route['destination']} -> {route['next_hop']}"
                        )
                else:
                    lines.append("  route: none")
        else:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Nebius VPC route tables, subnet attachments, and routes for a project."
    )
    parser.add_argument("--project-id", required=True, help="Nebius project ID")
    parser.add_argument("--network-id", help="Optional network ID filter")
    parser.add_argument("--profile", help="Optional Nebius CLI profile")
    parser.add_argument("--endpoint", help="Optional Nebius API endpoint override")
    parser.add_argument("--config-file", type=Path, help="Optional Nebius CLI config file path")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = parser.parse_args()

    config_file = args.config_file.expanduser().resolve() if args.config_file else None
    report = _build_report(
        project_id=args.project_id,
        network_id=args.network_id,
        profile=args.profile,
        endpoint=args.endpoint,
        config_file=config_file,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
