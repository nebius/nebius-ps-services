"""Credit only exact retained Tailscale compute resources proven by a fresh plan."""

from __future__ import annotations

from typing import Any

from .quota_checks import QuotaRequirement


def retained_compute(
    config: Any, sources: Any, plan: Any, *, sdk_factory: Any = None
) -> tuple[QuotaRequirement, ...]:
    from .nat_gateway_sdk import NatGatewaySDK

    sdk_factory = sdk_factory or NatGatewaySDK
    scope = config.get("client_info", {}).get("nebius", {})
    credits: list[QuotaRequirement] = []
    for row in config.get("infra", {}).get("components", []):
        if row.get("id") != "tailscale-gw" or not row.get("enabled"):
            continue
        instance = row["instance_id"]
        modules = [
            s
            for s in sources
            if s.get("component_id") == "tailscale-gw" and s.get("instance_id") == instance
        ]
        if len(modules) != 1:
            continue
        prefix = "module." + modules[0]["module_name"] + "."
        project = row.get("inputs", {}).get("parent_id") or scope.get("project_id")
        region = scope.get("region_id")
        if not project or not region:
            continue
        resources = [
            r
            for r in plan.get("resource_changes", [])
            if r.get("address", "").startswith(prefix)
            and r.get("mode") == "managed"
            and r.get("type") in {"nebius_compute_v1_instance", "nebius_compute_v1_disk"}
        ]
        identities = [(r.get("change", {}).get("before") or {}).get("id") for r in resources]
        known = [v for v in identities if v]
        if len(known) != len(set(known)):
            continue
        for resource in resources:
            change = resource.get("change", {})
            before, after, unknown = (
                change.get("before") or {},
                change.get("after") or {},
                change.get("after_unknown") or {},
            )
            if (
                change.get("actions") not in (["no-op"], ["update"])
                or not before.get("id")
                or before.get("id") != after.get("id")
                or before.get("parent_id") != project
                or after.get("parent_id") != project
                or any(
                    unknown.get(k)
                    for k in ("id", "parent_id", "resources", "size_gibibytes", "type")
                )
            ):
                continue
            values: dict[str, int] = {}
            if resource["type"] == "nebius_compute_v1_disk":
                size = before.get("size_gibibytes")
                disk_type = before.get("type", "")
                if (
                    type(size) is not int
                    or size <= 0
                    or not disk_type
                    or disk_type != after.get("type")
                ):
                    continue
                values = {
                    "compute.disk.count": 1,
                    "compute.disk.size." + disk_type.lower().replace("_", "-"): size * 1024**3,
                }
            else:
                shape = before.get("resources") or {}
                if (
                    not shape
                    or shape != after.get("resources")
                    or before.get("preemptible") is not None
                    or after.get("preemptible") is not None
                ):
                    continue
                with sdk_factory(project) as sdk:
                    preset = sdk.preset_resources(
                        shape.get("platform", ""), shape.get("preset", "")
                    )
                if preset.get("gpu_count") != 0 or not preset.get("vcpu_count"):
                    continue
                values = {
                    "compute.instance.count": 1,
                    "compute.instance.non-gpu.vcpu": preset["vcpu_count"],
                }
            credits.extend(
                QuotaRequirement(
                    "tailscale-gw",
                    instance,
                    instance,
                    metric,
                    region,
                    amount,
                    "exact retained Tailscale resource in fresh plan",
                )
                for metric, amount in values.items()
            )
    return tuple(credits)
