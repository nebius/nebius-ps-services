"""Quota demand from the fresh Terraform plan, including transient replacements."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .nat_gateway_sdk import NatGatewaySDK
from .nat_gateway_state import assert_quota_resolved
from .quota_checks import QuotaRequirement, assess_live_quota_requirements


def plan_requirements(
    payload: Mapping[str, Any], plan: Mapping[str, Any], *, sdk_factory: Any = NatGatewaySDK
) -> list[QuotaRequirement]:
    scope = payload["client_info"]["nebius"]
    requirements: list[QuotaRequirement] = []

    def add(name: str, amount: int, address: str) -> None:
        if amount > 0:
            requirements.append(
                QuotaRequirement(
                    "nat-gateway",
                    "deployment",
                    "NAT deployment",
                    name,
                    scope["region_id"],
                    amount,
                    address,
                )
            )

    for resource in plan.get("resource_changes", []):
        change = resource.get("change", {})
        if resource.get("mode") == "data" or change.get("actions") in (
            ["no-op"],
            ["read"],
            ["delete"],
        ):
            continue
        kind, address = resource["type"], resource["address"]
        after, before = change.get("after") or {}, change.get("before") or {}
        creates = "create" in change["actions"]
        if kind == "nebius_mk8s_v1_node_group":
            template = after.get("template") or {}
            old_template = before.get("template") or {}

            def count(value: Mapping[str, Any]) -> int:
                fixed = value.get("fixed_node_count")
                result = (
                    fixed
                    if fixed is not None
                    else (value.get("autoscaling") or {}).get("max_node_count")
                )
                if not isinstance(result, int) or result < 0:
                    raise RuntimeError(
                        "Shared MK8s quota demand is unresolved in the NAT deployment plan"
                    )
                return result

            # Template changes can overlap old workers; do not assume quota is already released.
            nodes = (
                count(after)
                if creates or template != old_template
                else max(0, count(after) - count(before))
            )
            add(
                "compute.instance.preemptible.count"
                if template.get("preemptible") is not None
                else "compute.instance.count",
                nodes,
                address,
            )
            add("compute.disk.count", nodes, address)
            with sdk_factory(scope["project_id"]) as sdk:
                shape = template.get("resources") or {}
                resources = sdk.preset_resources(shape.get("platform", ""), shape.get("preset", ""))
                if resources["gpu_count"] == 0:
                    add("compute.instance.non-gpu.vcpu", nodes * resources["vcpu_count"], address)
            disk = template.get("boot_disk") or {}
            size = disk.get("size_gibibytes")
            if not isinstance(size, int) or not disk.get("type"):
                raise RuntimeError("Shared MK8s boot disk quota demand is unresolved")
            add(
                "compute.disk.size." + disk["type"].lower().replace("_", "-"),
                nodes * size * 1024**3,
                address,
            )
            public_interfaces = sum(
                1
                for interface in template.get("network_interfaces", [])
                if interface.get("public_ip_address") is not None
            )
            add("vpc.ipv4-address.public.count", nodes * public_interfaces, address)
        elif kind == "nebius_compute_v1_instance":
            add(
                "compute.instance.preemptible.count"
                if after.get("preemptible") is not None
                else "compute.instance.count",
                int(creates),
                address,
            )
            with sdk_factory(scope["project_id"]) as sdk:
                shape = after.get("resources", {})
                # GPU machines are assessed by the existing GPU quota owner.
                resources = sdk.preset_resources(shape.get("platform", ""), shape.get("preset", ""))
                if resources["gpu_count"] != 0:
                    continue
                new = resources["vcpu_count"]
                old_shape = before.get("resources", {})
                old = (
                    0
                    if creates
                    else sdk.cpu_preset(old_shape.get("platform", ""), old_shape.get("preset", ""))[
                        "vcpu_count"
                    ]
                )
                add("compute.instance.non-gpu.vcpu", max(0, new - old), address)
        elif kind == "nebius_compute_v1_disk":
            add("compute.disk.count", int(creates), address)
            size = after.get("size_gibibytes")
            if not isinstance(size, int):
                size_bytes = after.get("size_bytes")
                if not isinstance(size_bytes, int):
                    raise RuntimeError("NAT disk quota demand is unresolved in Terraform plan")
            else:
                size_bytes = size * 1024**3
            old_bytes = (
                0
                if creates
                else before.get("size_bytes") or (before.get("size_gibibytes") or 0) * 1024**3
            )
            disk_type = after.get("type", "").lower().replace("_", "-")
            if not disk_type:
                raise RuntimeError("NAT disk type is unresolved in Terraform plan")
            add("compute.disk.size." + disk_type, max(0, size_bytes - old_bytes), address)
        elif kind == "nebius_vpc_v1_allocation" and creates:
            add("vpc.allocation.count", 1, address)
            if after.get("ipv4_public") is not None:
                add("vpc.ipv4-address.public.count", 1, address)
        elif kind == "nebius_vpc_v1_subnet" and creates:
            add("vpc.subnet.count", 1, address)
            for field in ("ipv4_private_pools", "ipv4_public_pools"):
                spec = after.get(field) or {}
                add(
                    "vpc.pool.count",
                    sum(1 for pool in spec.get("pools") or [] if not pool.get("id")),
                    address,
                )
        elif kind == "nebius_vpc_v1_network" and creates:
            add("vpc.network.count", 1, address)
            add("vpc.route-table.count", 1, address)
            add("vpc.route.count", 1, address)
        elif creates and kind in {
            "nebius_vpc_v1_pool",
            "nebius_vpc_v1_route_table",
            "nebius_vpc_v1_route",
        }:
            name = {
                "nebius_vpc_v1_pool": "pool",
                "nebius_vpc_v1_route_table": "route-table",
                "nebius_vpc_v1_route": "route",
            }[kind]
            add(f"vpc.{name}.count", 1, address)
    return requirements


def check_plan_quotas(payload: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    scope = payload["client_info"]["nebius"]
    requirements = plan_requirements(payload, plan)
    if not requirements:
        return
    report = assess_live_quota_requirements(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        region_id=scope["region_id"],
        requirements=requirements,
        context="NAT plan quota admission",
    )
    assert_quota_resolved(report)
