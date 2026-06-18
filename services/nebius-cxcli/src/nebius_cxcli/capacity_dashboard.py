"""Nebius Capacity Dashboard helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


@dataclass(frozen=True)
class CapacityAdviceAvailability:
    available: int
    limit: int
    availability_level: str
    data_state: str


@dataclass(frozen=True)
class CapacityResourceAdvice:
    region: str
    platform: str
    preset: str
    fabric: str
    on_demand: CapacityAdviceAvailability
    reserved: CapacityAdviceAvailability
    preemptible: CapacityAdviceAvailability

    @property
    def best_regular_available(self) -> int:
        return max(self.on_demand.available, self.reserved.available)


def capacity_availability(value: object) -> CapacityAdviceAvailability:
    return CapacityAdviceAvailability(
        available=int(getattr(value, "available", 0) or 0),
        limit=int(getattr(value, "limit", 0) or 0),
        availability_level=_as_text(
            getattr(getattr(value, "availability_level", None), "name", None)
        )
        or "",
        data_state=_as_text(getattr(getattr(value, "data_state", None), "name", None)) or "",
    )


def regular_capacity_lane(item: CapacityResourceAdvice) -> tuple[str, CapacityAdviceAvailability]:
    if item.on_demand.available >= item.reserved.available:
        return "on-demand", item.on_demand
    return "reserved", item.reserved


def capacity_lane(
    item: CapacityResourceAdvice, *, mode: str
) -> tuple[str, CapacityAdviceAvailability]:
    normalized_mode = _as_text(mode).lower() or "regular"
    if normalized_mode == "preemptible":
        return "preemptible", item.preemptible
    if normalized_mode in {"on-demand", "on_demand", "forbid", "reservation-forbid"}:
        return "on-demand", item.on_demand
    if normalized_mode in {"reserved", "strict", "reservation-strict"}:
        return "reserved", item.reserved
    return regular_capacity_lane(item)


def capacity_mode_available(item: CapacityResourceAdvice, *, mode: str) -> tuple[str, int]:
    normalized_mode = _as_text(mode).lower() or "regular"
    if normalized_mode in {"auto", "reservation-auto"}:
        return "auto", item.reserved.available + item.on_demand.available
    lane_name, lane = capacity_lane(item, mode=mode)
    return lane_name, lane.available


def capacity_level_rank(level: str) -> int:
    return {
        "AVAILABILITY_LEVEL_HIGH": 0,
        "AVAILABILITY_LEVEL_MEDIUM": 1,
        "AVAILABILITY_LEVEL_LOW": 2,
        "AVAILABILITY_LEVEL_LIMIT_REACHED": 3,
        "AVAILABILITY_LEVEL_UNKNOWN": 4,
    }.get(level, 5)


def capacity_regular_sort_key(
    item: CapacityResourceAdvice,
    *,
    fabric_order: dict[str, int] | None = None,
) -> tuple[int, int, int, int, str]:
    lane_name, lane = regular_capacity_lane(item)
    fabric_rank = (fabric_order or {}).get(item.fabric, len(fabric_order or {}))
    return (
        -item.best_regular_available,
        0 if lane_name == "on-demand" else 1,
        capacity_level_rank(lane.availability_level),
        fabric_rank,
        item.fabric,
    )


def capacity_mode_sort_key(
    item: CapacityResourceAdvice,
    *,
    mode: str,
    fabric_order: dict[str, int] | None = None,
) -> tuple[int, int, int, int, str]:
    lane_name, available = capacity_mode_available(item, mode=mode)
    if lane_name == "auto":
        availability_rank = min(
            capacity_level_rank(item.reserved.availability_level),
            capacity_level_rank(item.on_demand.availability_level),
        )
    else:
        _, lane = capacity_lane(item, mode=mode)
        availability_rank = capacity_level_rank(lane.availability_level)
    fabric_rank = (fabric_order or {}).get(item.fabric, len(fabric_order or {}))
    return (
        -available,
        0 if lane_name == "on-demand" else 1 if lane_name == "reserved" else 2,
        availability_rank,
        fabric_rank,
        item.fabric,
    )


def capacity_summary_text(item: CapacityResourceAdvice) -> str:
    return f"live on-demand VMs={item.on_demand.available}, reserved VMs={item.reserved.available}"


def filter_capacity_resource_advice(
    items: tuple[CapacityResourceAdvice, ...],
    *,
    region_id: str = "",
    platform_name: str = "",
    preset_name: str = "",
    fabric: str = "",
) -> tuple[CapacityResourceAdvice, ...]:
    return tuple(
        item
        for item in items
        if (not region_id or item.region == region_id)
        and (not platform_name or item.platform == platform_name)
        and (not preset_name or item.preset == preset_name)
        and (not fabric or item.fabric == fabric)
    )


def _compute_instance_details(spec: object) -> object | None:
    compute_instance = getattr(spec, "compute_instance", None)
    if compute_instance is not None:
        return compute_instance
    resource_details = getattr(spec, "resource_details", None)
    return getattr(resource_details, "compute_instance", None)


def list_capacity_resource_advice(
    sdk: Any,
    *,
    parent_id: str,
    page_size: int = 200,
) -> tuple[CapacityResourceAdvice, ...]:
    from nebius.api.nebius.capacity.v1 import (
        ListResourceAdviceRequest,
        ResourceAdviceServiceClient,
    )

    client = ResourceAdviceServiceClient(sdk)
    page_token = ""
    seen_tokens: set[str] = set()
    resolved: list[CapacityResourceAdvice] = []
    while True:
        response = client.list(
            ListResourceAdviceRequest(
                parent_id=parent_id,
                page_size=page_size,
                page_token=page_token,
            )
        ).wait()
        for item in list(getattr(response, "items", []) or []):
            spec = getattr(item, "spec", None)
            status = getattr(item, "status", None)
            compute_instance = _compute_instance_details(spec)
            region = _as_text(getattr(spec, "region", None))
            platform = _as_text(getattr(compute_instance, "platform", None))
            preset = _as_text(getattr(getattr(compute_instance, "preset", None), "name", None))
            if not region or not platform:
                continue
            resolved.append(
                CapacityResourceAdvice(
                    region=region,
                    platform=platform,
                    preset=preset,
                    fabric=_as_text(getattr(spec, "fabric", None)),
                    on_demand=capacity_availability(getattr(status, "on_demand", None)),
                    reserved=capacity_availability(getattr(status, "reserved", None)),
                    preemptible=capacity_availability(getattr(status, "preemptible", None)),
                )
            )
        page_token = _as_text(getattr(response, "next_page_token", None))
        if not page_token:
            return tuple(resolved)
        if page_token in seen_tokens:
            raise RuntimeError(
                "Capacity Dashboard listing received a repeated pagination token from "
                "the Nebius API; aborting to avoid an infinite list loop."
            )
        seen_tokens.add(page_token)
