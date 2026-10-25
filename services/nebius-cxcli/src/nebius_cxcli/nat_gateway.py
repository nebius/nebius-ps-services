"""Deterministic custom-NAT intent and routing policies (no cloud effects)."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from typing import Any

COMPONENT = "nat-gateway"
ROUTE_OVERRIDES = "cxcli_nat_route_overrides"
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def nat_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in payload.get("infra", {}).get("components", [])
        if row.get("id") == COMPONENT and row.get("enabled") is True
    ]


def canonical_routes(routes: Any) -> list[dict[str, Any]]:
    if not isinstance(routes, list):
        raise ValueError("NAT routing requires a complete route list")
    result = []
    destinations: set[str] = set()
    for route in routes:
        if not isinstance(route, Mapping) or set(route) - {
            "destination_cidr",
            "allocation_id",
            "default_egress_gateway",
            "type",
        }:
            raise ValueError("Unsupported NAT source route")
        if route.get("type", "STATIC") != "STATIC":
            raise ValueError("NAT cannot clone redistributed or unknown route types")
        cidr = str(ipaddress.IPv4Network(route.get("destination_cidr", ""), strict=True))
        if cidr in destinations:
            raise ValueError("Duplicate NAT source route destination")
        destinations.add(cidr)
        allocation = route.get("allocation_id") or ""
        default = route.get("default_egress_gateway", False)
        if (
            not isinstance(allocation, str)
            or not isinstance(default, bool)
            or bool(allocation) == default
        ):
            raise ValueError("A NAT source route requires exactly one supported next hop")
        if default and cidr != "0.0.0.0/0":
            raise ValueError("Default egress is valid only for 0.0.0.0/0")
        result.append(
            {
                "destination_cidr": cidr,
                "allocation_id": allocation,
                "default_egress_gateway": default,
            }
        )
    return sorted(result, key=lambda row: row["destination_cidr"])


def policy_snapshot(table_id: str, routes: Any) -> dict[str, Any]:
    normalized = canonical_routes(routes)
    return {
        "source_table_id": table_id,
        "source_fingerprint": fingerprint(normalized),
        "routes": normalized,
    }


def terraform_policies(row: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {
        key: [
            {"destination_cidr": route["destination_cidr"], "allocation_id": route["allocation_id"]}
            for route in canonical_routes(policy["routes"])
            if route["destination_cidr"] != "0.0.0.0/0"
        ]
        for key, policy in row.get("routing", {}).get("policies", {}).items()
    }


def target_key(target: Mapping[str, Any]) -> str:
    if target.get("owner") == "terraform":
        return f"{target['vpc_instance']}:{target['subnet_key']}"
    return str(target["subnet_id"])


def validate_nat_config(payload: Mapping[str, Any]) -> None:
    rows = nat_rows(payload)
    all_rows = {
        row.get("instance_id"): row
        for row in payload.get("infra", {}).get("components", [])
        if row.get("enabled")
    }
    used: set[str] = set()
    for row in rows:
        name = row.get("instance_id")
        inputs = row.get("inputs", {})
        if "module_name" in inputs:
            raise ValueError("NAT module identity is its component instance_id")
        user = inputs.get("ssh_user_name", "ubuntu")
        if (
            user == "root"
            or not isinstance(user, str)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user)
        ):
            raise ValueError("NAT SSH user must be a non-root Linux user name")
        if "routing_policies" in inputs:
            raise ValueError(
                "nat-gateway routing_policies is derived from routing.policies; do not author it in inputs"
            )
        routing = row.get("routing")
        if (
            not isinstance(routing, Mapping)
            or set(routing) != {"version", "enabled", "targets", "policies"}
            or routing.get("version") != 1
            or not isinstance(routing.get("enabled"), bool)
        ):
            raise ValueError(f"NAT {name} requires routing version, enabled, targets and policies")
        policies = routing["policies"]
        if not isinstance(policies, Mapping) or not policies:
            raise ValueError(f"NAT {name} requires at least one reviewed routing policy")
        for key, policy in policies.items():
            if (
                not isinstance(key, str)
                or not _KEY.fullmatch(key)
                or not isinstance(policy, Mapping)
            ):
                raise ValueError("Invalid NAT routing policy key")
            if set(policy) != {"source_table_id", "source_fingerprint", "routes"} or not isinstance(
                policy["source_table_id"], str
            ):
                raise ValueError("Invalid NAT routing policy snapshot")
            routes = canonical_routes(policy["routes"])
            if policy["source_fingerprint"] != fingerprint(routes):
                raise ValueError("NAT routing snapshot fingerprint does not match its routes")
        targets = routing["targets"]
        if not isinstance(targets, list) or not targets:
            raise ValueError("Select at least one NAT workload subnet")
        for target in targets:
            if not isinstance(target, Mapping) or target.get("policy") not in policies:
                raise ValueError("NAT target references an unknown routing policy")
            if target.get("owner") == "external":
                if (
                    set(target) != {"owner", "subnet_id", "policy", "delegated"}
                    or target.get("delegated") is not True
                    or not isinstance(target.get("subnet_id"), str)
                    or not target["subnet_id"]
                ):
                    raise ValueError(
                        "External NAT routing requires an explicit subnet ID and route-field delegation"
                    )
            elif target.get("owner") == "terraform":
                if set(target) != {"owner", "vpc_instance", "subnet_key", "policy"}:
                    raise ValueError("Terraform NAT targets require a VPC instance and subnet key")
                vpc = all_rows.get(target.get("vpc_instance"), {})
                subnet = vpc.get("inputs", {}).get("subnets", {}).get(target.get("subnet_key"))
                if vpc.get("id") != "vpc" or not isinstance(subnet, Mapping):
                    raise ValueError("NAT target must reference an enabled VPC subnet")
                binding = row.get("bindings", {}).get("inputs.network_id")
                if binding:
                    if binding != {
                        "source_component": "vpc",
                        "source_instance": target["vpc_instance"],
                        "source_output": "network_id",
                    }:
                        raise ValueError(
                            "NAT workload subnet and gateway must use the same VPC network binding"
                        )
                elif vpc.get("inputs", {}).get("network", {}).get("existing_id") != inputs.get(
                    "network_id"
                ):
                    raise ValueError(
                        "NAT workload subnet and gateway must use the same existing network"
                    )
                if f"inputs.subnets.{target['subnet_key']}.route_table_id" in vpc.get(
                    "bindings", {}
                ):
                    raise ValueError(
                        "NAT attachment conflicts with an existing route-table binding"
                    )
            else:
                raise ValueError("NAT target owner must be terraform or external")
            key = target_key(target)
            if key in used:
                raise ValueError("A workload subnet cannot have multiple NAT owners")
            used.add(key)
        for key in ("admin_cidrs", "source_cidrs"):
            cidrs = inputs.get(key)
            if not isinstance(cidrs, list) or not cidrs:
                raise ValueError(f"NAT {key} requires explicit IPv4 ranges")
            for cidr in cidrs:
                if ipaddress.IPv4Network(cidr, strict=True).prefixlen == 0:
                    raise ValueError(f"Unrestricted NAT {key} is not supported")
        cidr = inputs.get("gateway_subnet_cidr", "/28")
        if isinstance(cidr, str) and cidr.startswith("/"):
            if not cidr[1:].isdigit() or not 1 <= int(cidr[1:]) <= 32:
                raise ValueError("Invalid NAT subnet prefix")
        else:
            ipaddress.IPv4Network(cidr, strict=True)


def nat_module_inputs(row: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(row.get("inputs", {})))
    result["routing_policies"] = terraform_policies(row)
    return result


def routing_preview(row: Mapping[str, Any]) -> str:
    lines = [f"NAT {row.get('instance_id')}: single VM; replacement/reboot interrupts connections."]
    routing = row["routing"]
    for target in routing["targets"]:
        policy = routing["policies"][target["policy"]]
        lines.append(
            f"  {target_key(target)} ({target['owner']}): {policy['source_table_id'] or 'new network default'} -> NAT policy {target['policy']}"
        )
        lines.append("    0.0.0.0/0 -> gateway private allocation")
        for route in canonical_routes(policy["routes"]):
            if route["destination_cidr"] != "0.0.0.0/0":
                lines.append(f"    retain {route['destination_cidr']} -> {route['allocation_id']}")
    lines.append(
        "Activation requires trusted SSH readiness. Quota headroom is not a capacity reservation."
    )
    return "\n".join(lines)
