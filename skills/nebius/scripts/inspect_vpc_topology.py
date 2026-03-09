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


def _status_private_cidrs(subnet_obj: Any) -> list[str]:
    subnet_status = getattr(subnet_obj, "status", None)
    return _cidr_strings(getattr(subnet_status, "ipv4_private_cidrs", None))


def _pool_cidrs(pool_obj: Any) -> list[str]:
    pool_spec = getattr(pool_obj, "spec", None)
    return _cidr_strings(getattr(pool_spec, "cidrs", None))


def _build_report(
    project_id: str,
    network_id: str | None,
    profile: str | None,
    endpoint: str | None,
    config_file: Path | None,
) -> list[dict[str, Any]]:
    import nebius.sdk as sdk
    from nebius.api.nebius.vpc.v1 import (
        GetPoolRequest,
        ListNetworksRequest,
        ListPoolsRequest,
        ListSubnetsByNetworkRequest,
        NetworkServiceClient,
        PoolServiceClient,
        SubnetServiceClient,
    )

    client = init_nebius_sdk(
        profile=profile,
        endpoint=endpoint,
        config_file=config_file,
        parent_id=project_id,
    )
    assert isinstance(client, sdk.SDK)

    try:
        network_client = NetworkServiceClient(client)
        pool_client = PoolServiceClient(client)
        subnet_client = SubnetServiceClient(client)

        pools_by_id: dict[str, Any] = {}
        all_pools = pool_client.list(ListPoolsRequest(parent_id=project_id)).wait()
        for pool_obj in getattr(all_pools, "items", []) or []:
            pool_meta = getattr(pool_obj, "metadata", None)
            pool_id = getattr(pool_meta, "id", None) or getattr(pool_obj, "id", None)
            if pool_id:
                pools_by_id[str(pool_id)] = pool_obj

        report: list[dict[str, Any]] = []
        network_list = network_client.list(ListNetworksRequest(parent_id=project_id)).wait()
        for network_obj in getattr(network_list, "items", []) or []:
            network_meta = getattr(network_obj, "metadata", None)
            current_network_id = getattr(network_meta, "id", None) or getattr(network_obj, "id", None)
            if not current_network_id:
                continue
            if network_id and str(current_network_id) != network_id:
                continue

            network_spec = getattr(network_obj, "spec", None)
            parent_refs = getattr(getattr(network_spec, "ipv4_private_pools", None), "pools", []) or []

            parent_pools: list[dict[str, Any]] = []
            parent_pool_ids: set[str] = set()
            for ref in parent_refs:
                pool_id = getattr(ref, "pool_id", None) or getattr(ref, "id", None)
                if not pool_id:
                    continue
                pool_id = str(pool_id)
                parent_pool_ids.add(pool_id)
                pool_obj = pools_by_id.get(pool_id)
                if pool_obj is None:
                    pool_obj = pool_client.get(GetPoolRequest(id=pool_id)).wait()
                    pools_by_id[pool_id] = pool_obj
                pool_meta = getattr(pool_obj, "metadata", None)
                parent_pools.append(
                    {
                        "pool_id": pool_id,
                        "pool_name": getattr(pool_meta, "name", None),
                        "cidrs": _pool_cidrs(pool_obj),
                    }
                )

            child_pools: list[dict[str, Any]] = []
            for pool_obj in pools_by_id.values():
                pool_spec = getattr(pool_obj, "spec", None)
                source_pool_id = getattr(pool_spec, "source_pool_id", "") or ""
                if not source_pool_id or str(source_pool_id) not in parent_pool_ids:
                    continue
                pool_meta = getattr(pool_obj, "metadata", None)
                child_pools.append(
                    {
                        "pool_id": getattr(pool_meta, "id", None) or getattr(pool_obj, "id", None),
                        "pool_name": getattr(pool_meta, "name", None),
                        "source_pool_id": str(source_pool_id),
                        "cidrs": _pool_cidrs(pool_obj),
                    }
                )
            child_pools.sort(key=lambda item: (item["pool_name"] or "", item["pool_id"] or ""))

            subnet_rows: list[dict[str, Any]] = []
            subnet_list = subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(network_id=str(current_network_id))
            ).wait()
            for subnet_obj in getattr(subnet_list, "items", []) or []:
                subnet_meta = getattr(subnet_obj, "metadata", None)
                subnet_spec = getattr(subnet_obj, "spec", None)
                subnet_rows.append(
                    {
                        "subnet_id": getattr(subnet_meta, "id", None) or getattr(subnet_obj, "id", None),
                        "subnet_name": getattr(subnet_meta, "name", None),
                        "allocation_mode": _subnet_mode(subnet_obj),
                        "use_network_pools": bool(
                            getattr(
                                getattr(subnet_spec, "ipv4_private_pools", None),
                                "use_network_pools",
                                False,
                            )
                        ),
                        "explicit_private_cidrs": _explicit_subnet_cidrs(subnet_obj),
                        "status_private_cidrs": _status_private_cidrs(subnet_obj),
                    }
                )
            subnet_rows.sort(key=lambda item: (item["subnet_name"] or "", item["subnet_id"] or ""))

            report.append(
                {
                    "project_id": project_id,
                    "network_id": str(current_network_id),
                    "network_name": getattr(network_meta, "name", None),
                    "parent_private_pools": parent_pools,
                    "derived_child_pools": child_pools,
                    "subnets": subnet_rows,
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
        lines.append("Parent private pools:")
        if network["parent_private_pools"]:
            for pool in network["parent_private_pools"]:
                cidrs = ", ".join(pool["cidrs"]) if pool["cidrs"] else "none"
                lines.append(f"- {pool['pool_name']} ({pool['pool_id']}): {cidrs}")
        else:
            lines.append("- none")

        lines.append("Derived child pools:")
        if network["derived_child_pools"]:
            for pool in network["derived_child_pools"]:
                cidrs = ", ".join(pool["cidrs"]) if pool["cidrs"] else "none"
                lines.append(
                    f"- {pool['pool_name']} ({pool['pool_id']}), source={pool['source_pool_id']}: {cidrs}"
                )
        else:
            lines.append("- none")

        lines.append("Subnets:")
        for subnet in network["subnets"]:
            explicit = ", ".join(subnet["explicit_private_cidrs"]) or "none"
            status = ", ".join(subnet["status_private_cidrs"]) or "none"
            lines.append(f"- {subnet['subnet_name']} ({subnet['subnet_id']})")
            lines.append(f"  mode: {subnet['allocation_mode']}")
            lines.append(f"  use_network_pools: {str(subnet['use_network_pools']).lower()}")
            lines.append(f"  explicit_private_cidrs: {explicit}")
            lines.append(f"  status_private_cidrs: {status}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Nebius VPC networks, pools, and subnets for a project."
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
