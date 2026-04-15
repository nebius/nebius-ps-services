"""Live Nebius quota assessment helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .components import ComponentEntry, component_entries
from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk
from .terminal_styles import warning_markup

_GIB = 1024 * 1024 * 1024


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mapping_text(mapping: Any, key: str, *, default: str = "") -> str:
    if not isinstance(mapping, dict):
        return default
    return _as_text(mapping.get(key)) or default


def _mapping_bool(mapping: Any, key: str, *, default: bool = False) -> bool:
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(key)
    if value is None:
        return default
    return bool(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    text = _as_text(value)
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _path_value(mapping: Any, path: str) -> Any:
    current = mapping
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _effective_region(*, inputs: dict[str, Any], default_region: str) -> str:
    return _mapping_text(inputs, "region", default=default_region) or default_region


def _disk_quota_suffix(raw_type: str) -> str | None:
    normalized = _as_text(raw_type).upper()
    mapping = {
        "NETWORK_HDD": "network-hdd",
        "NETWORK_SSD": "network-ssd",
        "NETWORK_SSD_NON_REPLICATED": "network-ssd-non-replicated",
        "NETWORK_SSD_IO_M3": "network-ssd-io-m3",
    }
    return mapping.get(normalized)


def _gpu_quota_suffix(platform: str) -> str | None:
    normalized = _as_text(platform).lower()
    markers = (
        "b300",
        "b200",
        "h200",
        "h100",
        "l40s",
        "rtx-pro-6000",
        "rtxpro6000",
    )
    for marker in markers:
        if marker in normalized:
            return "rtx-pro-6000" if marker == "rtxpro6000" else marker
    return None


def _mk8s_effective_node_count(inputs: dict[str, Any], *, gpu: bool) -> int | None:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    count_key = "gpu_nodes_count_per_group" if gpu else "cpu_nodes_count"
    overrides = inputs.get(override_key)
    autoscaling = _path_value(overrides, "autoscaling") if isinstance(overrides, dict) else None
    override_fixed_count = (
        _positive_int(_path_value(overrides, "fixed_node_count"))
        if isinstance(overrides, dict)
        else None
    )
    if autoscaling is None:
        return override_fixed_count if override_fixed_count is not None else _positive_int(inputs.get(count_key))

    if override_fixed_count is not None:
        return override_fixed_count
    if isinstance(autoscaling, dict):
        for key in ("initial_count", "initial_node_count", "min_count", "min_node_count"):
            resolved = _positive_int(autoscaling.get(key))
            if resolved is not None:
                return resolved
    return None


def _mk8s_effective_platform(inputs: dict[str, Any], *, gpu: bool) -> str:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    field_key = "gpu_nodes_platform" if gpu else "cpu_nodes_platform"
    overrides = inputs.get(override_key)
    override_value = (
        _as_text(_path_value(overrides, "template.resources.platform"))
        if isinstance(overrides, dict)
        else ""
    )
    return override_value or _mapping_text(inputs, field_key)


def _mk8s_effective_preset(inputs: dict[str, Any], *, gpu: bool) -> str:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    field_key = "gpu_nodes_preset" if gpu else "cpu_nodes_preset"
    overrides = inputs.get(override_key)
    override_value = (
        _as_text(_path_value(overrides, "template.resources.preset"))
        if isinstance(overrides, dict)
        else ""
    )
    return override_value or _mapping_text(inputs, field_key)


def _mk8s_effective_preemptible(inputs: dict[str, Any], *, gpu: bool) -> bool:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    field_key = "gpu_nodes_preemptible" if gpu else "cpu_nodes_preemptible"
    overrides = inputs.get(override_key)
    override_value = _path_value(overrides, "template.preemptible") if isinstance(overrides, dict) else None
    if override_value is not None:
        return True
    return _mapping_bool(inputs, field_key, default=False)


def _mk8s_effective_public_ips(inputs: dict[str, Any], *, gpu: bool) -> bool:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    field_key = "gpu_nodes_public_ips" if gpu else "cpu_nodes_public_ips"
    overrides = inputs.get(override_key)
    override_value = (
        _path_value(overrides, "template.network_interfaces")
        if isinstance(overrides, dict)
        else None
    )
    if isinstance(override_value, list):
        for item in override_value:
            if isinstance(item, dict) and item.get("public_ip_address") is not None:
                return True
    return _mapping_bool(inputs, field_key, default=False)


@dataclass(frozen=True)
class QuotaRecord:
    name: str
    region: str
    limit: int | None
    usage: int
    service: str
    description: str
    unit: str
    state: str
    usage_state: str
    usage_percentage: str


@dataclass(frozen=True)
class PlatformPresetResources:
    platform: str
    preset: str
    vcpu_count: int
    memory_gibibytes: int
    gpu_count: int


@dataclass(frozen=True)
class QuotaContributor:
    component_id: str
    instance_id: str
    component_label: str
    required: int
    reason: str


@dataclass(frozen=True)
class QuotaRequirement:
    component_id: str
    instance_id: str
    component_label: str
    quota_name: str
    region: str
    required: int
    reason: str


@dataclass(frozen=True)
class QuotaCoverageGap:
    component_id: str
    instance_id: str
    component_label: str
    message: str


@dataclass(frozen=True)
class AggregatedQuotaRequirement:
    component_id: str
    instance_id: str
    component_label: str
    quota_name: str
    region: str
    required: int
    reason: str
    contributors: tuple[QuotaContributor, ...] = ()


@dataclass(frozen=True)
class QuotaCheck:
    component_id: str
    instance_id: str
    component_label: str
    quota_name: str
    region: str
    required: int
    reason: str
    unit: str
    available: int | None
    sufficient: bool | None
    tenant_limit: int | None
    tenant_usage: int | None
    project_limit: int | None
    project_usage: int | None
    source_scope: str
    description: str
    contributors: tuple[QuotaContributor, ...] = ()


@dataclass(frozen=True)
class RegionalQuotaAvailability:
    component_id: str
    instance_id: str
    component_label: str
    quota_name: str
    required: int
    reason: str
    unit: str
    current_region: str
    region_checks: tuple[QuotaCheck, ...] = ()


@dataclass(frozen=True)
class QuotaReport:
    tenant_id: str
    project_id: str
    region_id: str
    checked_at: str
    checks: tuple[QuotaCheck, ...] = ()
    coverage_gaps: tuple[QuotaCoverageGap, ...] = ()
    errors: tuple[str, ...] = ()
    regional_availability: tuple[RegionalQuotaAvailability, ...] = ()

    @property
    def insufficient_checks(self) -> tuple[QuotaCheck, ...]:
        return tuple(item for item in self.checks if item.sufficient is False)

    @property
    def unknown_checks(self) -> tuple[QuotaCheck, ...]:
        return tuple(item for item in self.checks if item.sufficient is None)

    @property
    def sufficient_checks(self) -> tuple[QuotaCheck, ...]:
        return tuple(item for item in self.checks if item.sufficient is True)

    @property
    def has_confirmed_insufficiency(self) -> bool:
        return bool(self.insufficient_checks)

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "region_id": self.region_id,
            "checked_at": self.checked_at,
            "confirmed_insufficient": self.has_confirmed_insufficiency,
            "checks": [asdict(item) for item in self.checks],
            "coverage_gaps": [asdict(item) for item in self.coverage_gaps],
            "errors": list(self.errors),
        }


def _format_amount(amount: int | None, unit: str) -> str:
    if amount is None:
        return "unknown"
    if unit == "byte":
        value = float(amount)
        for suffix in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
            if value < 1024 or suffix == "PiB":
                return f"{value:.1f} {suffix}" if suffix != "B" else f"{int(value)} {suffix}"
            value /= 1024
    return str(amount)


def _sorted_quota_regions(
    tenant_quotas: dict[tuple[str, str], QuotaRecord],
    project_quotas: dict[tuple[str, str], QuotaRecord],
    *,
    current_region: str,
) -> tuple[str, ...]:
    regions = {
        region
        for _, region in tuple(tenant_quotas.keys()) + tuple(project_quotas.keys())
        if _as_text(region)
    }
    if not regions:
        return (current_region,) if current_region else ()
    return tuple(
        sorted(
            regions,
            key=lambda region: (0 if region == current_region else 1, region),
        )
    )


def _coverage_gap_component_details(report: QuotaReport) -> list[tuple[str, tuple[str, ...]]]:
    grouped: dict[str, list[str]] = {}
    for item in report.coverage_gaps:
        label = item.component_label.strip()
        if not label:
            continue
        grouped.setdefault(label, []).append(item.message)
    details: list[tuple[str, tuple[str, ...]]] = []
    for label, messages in grouped.items():
        unique_messages: list[str] = []
        seen: set[str] = set()
        for item in messages:
            message = item.strip()
            if not message or message in seen:
                continue
            seen.add(message)
            unique_messages.append(message)
        details.append(
            (
                label,
                tuple(unique_messages or ("quota coverage was incomplete",)),
            )
        )
    return details


def _check_contributors(item: QuotaCheck) -> tuple[QuotaContributor, ...]:
    if item.contributors:
        return item.contributors
    return (
        QuotaContributor(
            component_id=item.component_id,
            instance_id=item.instance_id,
            component_label=item.component_label,
            required=item.required,
            reason=item.reason,
        ),
    )


def _confirmed_component_summaries(report: QuotaReport) -> list[tuple[str, tuple[str, ...]]]:
    if report.errors:
        return []

    component_states: dict[tuple[str, str], dict[str, Any]] = {}

    def _component_state(
        *,
        component_id: str,
        instance_id: str,
        component_label: str,
    ) -> dict[str, Any]:
        label = component_label.strip() or component_instance_label(component_id, instance_id)
        key = (component_id, instance_id)
        state = component_states.setdefault(
            key,
            {
                "label": label,
                "check_count": 0,
                "regions": set(),
                "blocked": False,
                "coverage_gap": False,
                "quota_names": [],
            },
        )
        if label:
            state["label"] = label
        return state

    for item in report.checks:
        for contributor in _check_contributors(item):
            state = _component_state(
                component_id=contributor.component_id,
                instance_id=contributor.instance_id,
                component_label=contributor.component_label,
            )
            state["check_count"] = int(state["check_count"]) + 1
            if item.region:
                state["regions"].add(item.region)
            if item.quota_name and item.quota_name not in state["quota_names"]:
                state["quota_names"].append(item.quota_name)
            if item.sufficient is not True:
                state["blocked"] = True

    for item in report.coverage_gaps:
        state = _component_state(
            component_id=item.component_id,
            instance_id=item.instance_id,
            component_label=item.component_label,
        )
        state["coverage_gap"] = True

    summaries: list[tuple[str, tuple[str, ...]]] = []
    for state in sorted(component_states.values(), key=lambda item: str(item["label"])):
        check_count = int(state["check_count"])
        if state["blocked"] or check_count <= 0:
            continue
        dimension_label = "checked quota dimension" if check_count == 1 else "checked quota dimensions"
        regions = sorted(str(item) for item in state["regions"] if str(item).strip())
        if not regions:
            scope = "in the selected region"
        elif len(regions) == 1:
            scope = f"in {regions[0]}"
        else:
            scope = f"across {', '.join(regions)}"
        summary = f"{state['label']}: {check_count} {dimension_label} confirmed {scope}"
        if state["coverage_gap"]:
            summary += " (partial coverage; see gaps below)"
        quota_names = tuple(str(item) for item in state["quota_names"] if str(item).strip())
        summaries.append((summary, quota_names))
    return summaries


def _regional_availability_lines(report: QuotaReport) -> list[str]:
    if not report.regional_availability:
        return []
    lines = [
        "Regional quota availability for the current config shape "
        "(quota-only; region-specific platform/preset availability is not revalidated):"
    ]
    for item in report.regional_availability:
        required = _format_amount(item.required, item.unit)
        lines.append(
            "  - "
            f"{item.component_label}: {item.quota_name} requires {required} ({item.reason})"
        )
        for check in item.region_checks:
            available = _format_amount(check.available, check.unit)
            status = (
                "sufficient"
                if check.sufficient is True
                else "insufficient"
                if check.sufficient is False
                else "unknown"
            )
            current_suffix = " (current)" if check.region == item.current_region else ""
            lines.append(f"    {check.region}{current_suffix}: available {available} ({status})")
    return lines


def format_quota_report_lines(
    report: QuotaReport,
    *,
    phase: str,
    include_coverage_gaps: bool = True,
    include_confirmed_components: bool = False,
) -> list[str]:
    lines: list[str] = []
    if report.errors:
        lines.append(
            f"{warning_markup('Quota check warning:')} "
            f"{phase} quota assessment was not fully available."
        )
        for item in report.errors:
            lines.append(f"  - {item}")
    if report.insufficient_checks:
        lines.append(
            f"{warning_markup('Quota warning:')} "
            f"{phase} detected insufficient Nebius quota."
        )
        for item in report.insufficient_checks:
            required = _format_amount(item.required, item.unit)
            available = _format_amount(item.available, item.unit)
            lines.append(
                "  - "
                f"{item.component_label}: {item.region} {item.quota_name} requires {required}, "
                f"available {available} ({item.reason})"
            )
    if report.unknown_checks:
        lines.append(
            f"{warning_markup('Quota unresolved:')} "
            f"{phase} could not resolve one or more live quota limits."
        )
        for item in report.unknown_checks:
            required = _format_amount(item.required, item.unit)
            lines.append(
                "  - "
                f"{item.component_label}: {item.region} {item.quota_name} requires {required}, "
                f"available unknown ({item.reason})"
            )
    if include_confirmed_components:
        confirmed_component_summaries = _confirmed_component_summaries(report)
        if confirmed_component_summaries:
            lines.append(
                "Quota confirmed: live quota was sufficient for the following checked component(s)."
            )
            for summary, quota_names in confirmed_component_summaries:
                lines.append(f"  - {summary}")
                if quota_names:
                    lines.append("    checked:")
                    for quota_name in quota_names:
                        lines.append(f"      - {quota_name}")
    if include_coverage_gaps and report.coverage_gaps:
        lines.append(
            f"{warning_markup('Quota coverage gap:')} "
            "quota could not be fully evaluated for the following component(s)."
        )
        for component_label, gap_messages in _coverage_gap_component_details(report):
            lines.append(f"  - {component_label}")
            lines.append("    gaps:")
            for gap_message in gap_messages:
                lines.append(f"      - {gap_message}")
    lines.extend(_regional_availability_lines(report))
    return lines


class _QuotaSession:
    def __init__(self, *, context: str, project_id: str) -> None:
        self._sdk = init_nebius_sdk(parent_id=project_id or None, context=context)
        self._preset_cache: dict[tuple[str, str, str], PlatformPresetResources | None] = {}

    def close(self) -> None:
        self._sdk.sync_close()

    def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
        from nebius.api.nebius.quotas.v1 import (
            ListQuotaAllowancesRequest,
            QuotaAllowanceServiceClient,
        )

        client = QuotaAllowanceServiceClient(self._sdk)
        page_token = ""
        items: dict[tuple[str, str], QuotaRecord] = {}
        while True:
            response = client.list(
                ListQuotaAllowancesRequest(
                    parent_id=parent_id,
                    page_size=500,
                    page_token=page_token,
                )
            ).wait()
            for item in list(getattr(response, "items", []) or []):
                metadata = getattr(item, "metadata", None)
                spec = getattr(item, "spec", None)
                status = getattr(item, "status", None)
                name = _as_text(getattr(metadata, "name", None))
                region = _as_text(getattr(spec, "region", None))
                if not name or not region:
                    continue
                items[(name, region)] = QuotaRecord(
                    name=name,
                    region=region,
                    limit=getattr(spec, "limit", None),
                    usage=int(getattr(status, "usage", 0) or 0),
                    service=_as_text(getattr(status, "service", None)),
                    description=_as_text(getattr(status, "description", None)),
                    unit=_as_text(getattr(status, "unit", None)),
                    state=_as_text(getattr(getattr(status, "state", None), "name", None)),
                    usage_state=_as_text(
                        getattr(getattr(status, "usage_state", None), "name", None)
                    ),
                    usage_percentage=_as_text(getattr(status, "usage_percentage", None)),
                )
            page_token = _as_text(getattr(response, "next_page_token", None))
            if not page_token:
                return items

    def preset_resources(
        self,
        *,
        project_id: str,
        platform: str,
        preset: str,
    ) -> PlatformPresetResources | None:
        cache_key = (project_id, platform, preset)
        if cache_key in self._preset_cache:
            return self._preset_cache[cache_key]

        from nebius.api.nebius.common.v1 import GetByNameRequest
        from nebius.api.nebius.compute.v1 import PlatformServiceClient

        client = PlatformServiceClient(self._sdk)
        try:
            resource = client.get_by_name(
                GetByNameRequest(parent_id=project_id, name=platform)
            ).wait()
        except Exception:
            self._preset_cache[cache_key] = None
            return None

        spec = getattr(resource, "spec", None)
        for item in list(getattr(spec, "presets", []) or []):
            if _as_text(getattr(item, "name", None)) != preset:
                continue
            resources = getattr(item, "resources", None)
            resolved = PlatformPresetResources(
                platform=platform,
                preset=preset,
                vcpu_count=int(getattr(resources, "vcpu_count", 0) or 0),
                memory_gibibytes=int(getattr(resources, "memory_gibibytes", 0) or 0),
                gpu_count=int(getattr(resources, "gpu_count", 0) or 0),
            )
            self._preset_cache[cache_key] = resolved
            return resolved

        self._preset_cache[cache_key] = None
        return None


def _evaluate_requirement(
    requirement: AggregatedQuotaRequirement,
    *,
    tenant_quotas: dict[tuple[str, str], QuotaRecord],
    project_quotas: dict[tuple[str, str], QuotaRecord],
) -> QuotaCheck:
    key = (requirement.quota_name, requirement.region)
    tenant = tenant_quotas.get(key)
    project = project_quotas.get(key)

    tenant_limit = tenant.limit if tenant is not None else None
    tenant_usage = tenant.usage if tenant is not None else None
    project_limit = project.limit if project is not None else None
    project_usage = project.usage if project is not None else None

    tenant_available = (
        max(tenant_limit - int(tenant_usage or 0), 0) if tenant_limit is not None else None
    )
    project_available = (
        max(project_limit - int(project_usage or 0), 0) if project_limit is not None else None
    )

    if tenant_available is not None and project_available is not None:
        available = min(tenant_available, project_available)
        source_scope = "tenant+project"
    elif project_available is not None:
        available = project_available
        source_scope = "project"
    elif tenant_available is not None:
        available = tenant_available
        source_scope = "tenant"
    else:
        available = None
        source_scope = "unresolved"

    record = project or tenant
    unit = record.unit if record is not None else ""
    description = record.description if record is not None else ""

    return QuotaCheck(
        component_id=requirement.component_id,
        instance_id=requirement.instance_id,
        component_label=requirement.component_label,
        quota_name=requirement.quota_name,
        region=requirement.region,
        required=requirement.required,
        reason=requirement.reason,
        unit=unit,
        available=available,
        sufficient=(requirement.required <= available) if available is not None else None,
        tenant_limit=tenant_limit,
        tenant_usage=tenant_usage,
        project_limit=project_limit,
        project_usage=project_usage,
        source_scope=source_scope,
        description=description,
        contributors=requirement.contributors,
    )


def _regionalize_requirement(
    requirement: AggregatedQuotaRequirement,
    *,
    region: str,
) -> AggregatedQuotaRequirement:
    return AggregatedQuotaRequirement(
        component_id=requirement.component_id,
        instance_id=requirement.instance_id,
        component_label=requirement.component_label,
        quota_name=requirement.quota_name,
        region=region,
        required=requirement.required,
        reason=requirement.reason,
        contributors=requirement.contributors,
    )


def _regional_availability_for_requirement(
    requirement: AggregatedQuotaRequirement,
    *,
    tenant_quotas: dict[tuple[str, str], QuotaRecord],
    project_quotas: dict[tuple[str, str], QuotaRecord],
    regions: tuple[str, ...],
    current_region: str,
) -> RegionalQuotaAvailability:
    region_checks = tuple(
        _evaluate_requirement(
            _regionalize_requirement(requirement, region=region),
            tenant_quotas=tenant_quotas,
            project_quotas=project_quotas,
        )
        for region in regions
    )
    unit = next((item.unit for item in region_checks if item.unit), "")
    return RegionalQuotaAvailability(
        component_id=requirement.component_id,
        instance_id=requirement.instance_id,
        component_label=requirement.component_label,
        quota_name=requirement.quota_name,
        required=requirement.required,
        reason=requirement.reason,
        unit=unit,
        current_region=current_region,
        region_checks=region_checks,
    )


def _aggregate_requirements(
    requirements: list[QuotaRequirement],
) -> tuple[AggregatedQuotaRequirement, ...]:
    grouped: dict[tuple[str, str], list[QuotaRequirement]] = {}
    for item in requirements:
        grouped.setdefault((item.quota_name, item.region), []).append(item)

    aggregated: list[AggregatedQuotaRequirement] = []
    for items in grouped.values():
        contributors = tuple(
            QuotaContributor(
                component_id=item.component_id,
                instance_id=item.instance_id,
                component_label=item.component_label,
                required=item.required,
                reason=item.reason,
            )
            for item in items
        )
        component_ids = list(dict.fromkeys(item.component_id for item in items))
        instance_ids = list(dict.fromkeys(item.instance_id for item in items))
        component_labels = list(dict.fromkeys(item.component_label for item in items))
        component_id = component_ids[0] if len(component_ids) == 1 else "multiple"
        instance_id = instance_ids[0] if len(instance_ids) == 1 else "multiple"
        component_label = (
            component_labels[0]
            if len(component_labels) <= 1
            else f"{component_labels[0]} + {len(component_labels) - 1} more"
        )
        reason = "; ".join(
            f"{contributor.component_label}: {contributor.reason}" for contributor in contributors
        )
        aggregated.append(
            AggregatedQuotaRequirement(
                component_id=component_id,
                instance_id=instance_id,
                component_label=component_label,
                quota_name=items[0].quota_name,
                region=items[0].region,
                required=sum(item.required for item in items),
                reason=reason,
                contributors=contributors,
            )
        )
    return tuple(aggregated)


def _append_requirement(
    target: list[QuotaRequirement],
    *,
    component_id: str,
    instance_id: str,
    quota_name: str,
    region: str,
    required: int | None,
    reason: str,
) -> None:
    if required is None or required <= 0:
        return
    target.append(
        QuotaRequirement(
            component_id=component_id,
            instance_id=instance_id,
            component_label=component_instance_label(component_id, instance_id),
            quota_name=quota_name,
            region=region,
            required=required,
            reason=reason,
        )
    )


def _append_gap(
    target: list[QuotaCoverageGap],
    *,
    component_id: str,
    instance_id: str,
    message: str,
) -> None:
    target.append(
        QuotaCoverageGap(
            component_id=component_id,
            instance_id=instance_id,
            component_label=component_instance_label(component_id, instance_id),
            message=message,
        )
    )


def _estimate_jump_host_requirements(
    *,
    session: _QuotaSession,
    project_id: str,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> None:
    platform = _mapping_text(inputs, "platform", default="cpu-d3")
    preset = _mapping_text(inputs, "preset", default="4vcpu-16gb")
    resources = session.preset_resources(project_id=project_id, platform=platform, preset=preset)
    if resources is None:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=(
                f"unable to resolve compute preset '{platform}/{preset}' live via the Nebius SDK; "
                "instance vCPU/GPU quotas were not checked"
            ),
        )
    else:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.instance.count",
            region=region,
            required=1,
            reason="one VM",
        )
        if resources.gpu_count > 0:
            gpu_suffix = _gpu_quota_suffix(platform)
            if gpu_suffix:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name=f"compute.instance.gpu.{gpu_suffix}",
                    region=region,
                    required=resources.gpu_count,
                    reason=f"{resources.gpu_count} GPU(s) from {platform}/{preset}",
                )
            else:
                _append_gap(
                    gaps,
                    component_id=component_id,
                    instance_id=instance_id,
                    message=(
                        f"unable to derive GPU quota name from platform '{platform}'; "
                        "GPU-type quota was not checked"
                    ),
                )
        else:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.instance.non-gpu.vcpu",
                region=region,
                required=resources.vcpu_count,
                reason=f"{resources.vcpu_count} vCPU(s) from {platform}/{preset}",
            )

    disk_type = _disk_quota_suffix(_mapping_text(inputs, "boot_disk_type", default="NETWORK_SSD"))
    disk_size_gib = _positive_int(inputs.get("boot_disk_size_gib")) or 60
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="compute.disk.count",
        region=region,
        required=1,
        reason="one boot disk",
    )
    if disk_type:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name=f"compute.disk.size.{disk_type}",
            region=region,
            required=disk_size_gib * _GIB,
            reason=f"{disk_size_gib} GiB boot disk",
        )

    create_allocation = _mapping_bool(inputs, "create_public_ip_allocation", default=True)
    has_public_ip = create_allocation or bool(_mapping_text(inputs, "public_ip_allocation_id"))
    if has_public_ip:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="vpc.ipv4-address.public.count",
            region=region,
            required=1,
            reason="one attached public IP",
        )
    if create_allocation and not _mapping_text(inputs, "public_ip_allocation_id"):
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="vpc.allocation.count",
            region=region,
            required=1,
            reason="one new public IP allocation",
        )


def _estimate_vm_requirements(
    *,
    session: _QuotaSession,
    project_id: str,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> None:
    platform = _mapping_text(inputs, "platform")
    preset = _mapping_text(inputs, "preset")
    preemptible = _mapping_bool(inputs, "preemptible_enabled", default=False)
    gpu_cluster_enabled = _mapping_bool(inputs, "gpu_cluster_enabled", default=False)
    gpu_cluster_id = _mapping_text(inputs, "gpu_cluster_id")
    gpu_cluster_fabric = _mapping_text(inputs, "gpu_cluster_infiniband_fabric")

    resources = (
        session.preset_resources(project_id=project_id, platform=platform, preset=preset)
        if platform and preset
        else None
    )
    if resources is None:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=(
                f"unable to resolve compute preset '{platform}/{preset}' live via the Nebius SDK; "
                "instance vCPU/GPU quotas were not checked"
            ),
        )
    elif preemptible:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.instance.preemptible.count",
            region=region,
            required=1,
            reason=f"one preemptible VM at {platform}/{preset}",
        )
        if resources.gpu_count > 0:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    "GPU-type quota mapping for preemptible standalone VMs is not exposed by the "
                    "current quota API surface; only preemptible VM count was checked"
                ),
            )
        else:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    f"preemptible VM {platform}/{preset} did not resolve to a GPU shape; "
                    "non-GPU preemptible quota semantics were not checked"
                ),
            )
    else:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.instance.count",
            region=region,
            required=1,
            reason="one regular VM",
        )
        if resources.gpu_count > 0:
            gpu_suffix = _gpu_quota_suffix(platform)
            if gpu_suffix:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name=f"compute.instance.gpu.{gpu_suffix}",
                    region=region,
                    required=resources.gpu_count,
                    reason=f"{resources.gpu_count} GPU(s) from {platform}/{preset}",
                )
            else:
                _append_gap(
                    gaps,
                    component_id=component_id,
                    instance_id=instance_id,
                    message=(
                        f"unable to derive GPU quota name from platform '{platform}'; "
                        "GPU-type quota was not checked"
                    ),
                )
        else:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.instance.non-gpu.vcpu",
                region=region,
                required=resources.vcpu_count,
                reason=f"{resources.vcpu_count} vCPU(s) from {platform}/{preset}",
            )

    if not _mapping_text(inputs, "boot_disk_existing_id"):
        disk_type = _disk_quota_suffix(_mapping_text(inputs, "boot_disk_type", default="NETWORK_SSD"))
        disk_size_gib = _positive_int(inputs.get("boot_disk_size_gib")) or 60
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.disk.count",
            region=region,
            required=1,
            reason="one managed boot disk",
        )
        if disk_type:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=f"compute.disk.size.{disk_type}",
                region=region,
                required=disk_size_gib * _GIB,
                reason=f"{disk_size_gib} GiB boot disk",
            )

    data_disks = inputs.get("data_disks")
    if isinstance(data_disks, list):
        for disk in data_disks:
            if not isinstance(disk, dict):
                continue
            disk_type = _disk_quota_suffix(_mapping_text(disk, "type", default="NETWORK_SSD"))
            disk_size_gib = _positive_int(disk.get("size_gib"))
            disk_name = _mapping_text(disk, "name", default="data disk")
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.disk.count",
                region=region,
                required=1,
                reason=f"managed data disk {disk_name}",
            )
            if disk_type and disk_size_gib is not None:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name=f"compute.disk.size.{disk_type}",
                    region=region,
                    required=disk_size_gib * _GIB,
                    reason=f"{disk_size_gib} GiB managed data disk {disk_name}",
                )

    public_ip_mode = _mapping_text(inputs, "public_ip_mode", default="dynamic").lower()
    if public_ip_mode in {"dynamic", "static", "allocation"}:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="vpc.ipv4-address.public.count",
            region=region,
            required=1,
            reason=f"one {public_ip_mode} public IP",
        )
    if public_ip_mode == "static":
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="vpc.allocation.count",
            region=region,
            required=1,
            reason="one static public IP allocation managed with the VM",
        )

    if gpu_cluster_enabled and not gpu_cluster_id and gpu_cluster_fabric:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.gpucluster.count",
            region=region,
            required=1,
            reason=f"one new GPU cluster in fabric {gpu_cluster_fabric}",
        )


def _estimate_sfs_requirements(
    *,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
) -> None:
    filesystem_type = _disk_quota_suffix(_mapping_text(inputs, "type", default="NETWORK_SSD"))
    size_gib = _positive_int(inputs.get("size_gib"))
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="compute.filesystem.count",
        region=region,
        required=1,
        reason="one shared filesystem",
    )
    if filesystem_type and size_gib is not None:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name=f"compute.filesystem.size.{filesystem_type}",
            region=region,
            required=size_gib * _GIB,
            reason=f"{size_gib} GiB shared filesystem",
        )


def _estimate_object_storage_requirements(
    *,
    region: str,
    component_id: str,
    instance_id: str,
    requirements: list[QuotaRequirement],
) -> None:
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="storage.bucket.count",
        region=region,
        required=1,
        reason="one bucket",
    )


def _estimate_mysterybox_requirements(
    *,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> None:
    secrets = inputs.get("secrets")
    if not isinstance(secrets, dict):
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message="`inputs.secrets` is missing or not a mapping; secret-count quota was not checked",
        )
        return
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="mysterybox.secret.count",
        region=region,
        required=len(secrets),
        reason=f"{len(secrets)} secret definition(s)",
    )


def _estimate_managed_postgresql_requirements(
    *,
    session: _QuotaSession,
    project_id: str,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> None:
    tier = _mapping_text(inputs, "tier", default="medium").lower() or "medium"
    tier_profiles = {
        "small": ("cpu-e2", "2vcpu-8gb", "network-ssd", 1),
        "medium": ("cpu-d3", "4vcpu-16gb", "network-ssd", 1),
        "large": ("cpu-d3", "8vcpu-32gb", "network-ssd", 2),
    }
    platform, preset, disk_suffix, hosts = tier_profiles.get(tier, tier_profiles["medium"])
    storage_gib = _positive_int(inputs.get("storage_gib")) or 100
    resources = session.preset_resources(project_id=project_id, platform=platform, preset=preset)
    if resources is None:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=(
                f"unable to resolve Managed PostgreSQL tier preset '{platform}/{preset}' live; "
                "CPU/RAM quota checks were skipped"
            ),
        )
    else:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="msp.postgres.cpu",
            region=region,
            required=resources.vcpu_count * hosts,
            reason=f"{hosts} host(s) at {platform}/{preset}",
        )
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="msp.postgres.ram",
            region=region,
            required=resources.memory_gibibytes * hosts * _GIB,
            reason=f"{hosts} host(s) at {platform}/{preset}",
        )
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="msp.postgres.count",
        region=region,
        required=1,
        reason="one Managed PostgreSQL cluster",
    )
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name=f"msp.postgres.disk.size.{disk_suffix}",
        region=region,
        required=storage_gib * hosts * _GIB,
        reason=f"{hosts} host(s) with {storage_gib} GiB disks",
    )


def _estimate_mk8s_requirements(
    *,
    session: _QuotaSession,
    project_id: str,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> None:
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="mk8s.cluster.count",
        region=region,
        required=1,
        reason="one Managed Kubernetes cluster",
    )

    cpu_count = _mk8s_effective_node_count(inputs, gpu=False)
    cpu_platform = _mk8s_effective_platform(inputs, gpu=False)
    cpu_preset = _mk8s_effective_preset(inputs, gpu=False)
    cpu_autoscaling = _path_value(inputs.get("mk8s_cpu_node_group_overrides"), "autoscaling")
    cpu_preemptible = _mk8s_effective_preemptible(inputs, gpu=False)
    cpu_public_ips = _mk8s_effective_public_ips(inputs, gpu=False)
    cpu_group_enabled = (cpu_count is not None and cpu_count > 0) or cpu_autoscaling is not None
    if cpu_group_enabled:
        if cpu_count is None:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    "CPU node group autoscaling is enabled but the current node count could not be "
                    "derived from the config; CPU node quotas were not checked"
                ),
            )
        else:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.disk.count",
                region=region,
                required=cpu_count,
                reason=f"{cpu_count} CPU node boot disk(s)",
            )
            if cpu_public_ips:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name="vpc.ipv4-address.public.count",
                    region=region,
                    required=cpu_count,
                    reason=f"{cpu_count} CPU node public IP(s)",
                )
            if cpu_preemptible:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name="compute.instance.preemptible.count",
                    region=region,
                    required=cpu_count,
                    reason=f"{cpu_count} preemptible CPU node(s)",
                )
            else:
                resources = (
                    session.preset_resources(project_id=project_id, platform=cpu_platform, preset=cpu_preset)
                    if cpu_platform and cpu_preset
                    else None
                )
                if resources is None:
                    _append_gap(
                        gaps,
                        component_id=component_id,
                        instance_id=instance_id,
                        message=(
                            f"unable to resolve CPU node preset '{cpu_platform}/{cpu_preset}' live; "
                            "CPU instance/vCPU quotas were not checked"
                        ),
                    )
                else:
                    _append_requirement(
                        requirements,
                        component_id=component_id,
                        instance_id=instance_id,
                        quota_name="compute.instance.count",
                        region=region,
                        required=cpu_count,
                        reason=f"{cpu_count} regular CPU node(s)",
                    )
                    _append_requirement(
                        requirements,
                        component_id=component_id,
                        instance_id=instance_id,
                        quota_name="compute.instance.non-gpu.vcpu",
                        region=region,
                        required=resources.vcpu_count * cpu_count,
                        reason=f"{cpu_count} CPU node(s) at {cpu_platform}/{cpu_preset}",
                    )
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    "MK8s node-group boot disk size/type is not exposed by current module inputs; "
                    "disk-size quotas were not checked"
                ),
            )

    gpu_enabled = _mapping_bool(inputs, "gpu_enabled", default=False)
    if not gpu_enabled:
        return

    group_count = _positive_int(inputs.get("gpu_node_groups"))
    nodes_per_group = _mk8s_effective_node_count(inputs, gpu=True)
    gpu_platform = _mk8s_effective_platform(inputs, gpu=True)
    gpu_preset = _mk8s_effective_preset(inputs, gpu=True)
    gpu_autoscaling = _path_value(inputs.get("mk8s_gpu_node_group_overrides"), "autoscaling")
    gpu_preemptible = _mk8s_effective_preemptible(inputs, gpu=True)
    gpu_public_ips = _mk8s_effective_public_ips(inputs, gpu=True)

    if _mapping_text(inputs, "infiniband_fabric"):
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.gpucluster.count",
            region=region,
            required=1,
            reason="one GPU cluster for InfiniBand fabric",
        )

    if group_count is None or group_count <= 0:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message="GPU is enabled but `gpu_node_groups` is missing or invalid",
        )
        return
    if nodes_per_group is None:
        if gpu_autoscaling is not None:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    "GPU node-group autoscaling is enabled but the current node count could not be "
                    "derived from the config; GPU node quotas were not checked"
                ),
            )
        else:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message="GPU is enabled but `gpu_nodes_count_per_group` is missing or invalid",
            )
        return

    total_gpu_nodes = group_count * nodes_per_group
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="compute.disk.count",
        region=region,
        required=total_gpu_nodes,
        reason=f"{total_gpu_nodes} GPU node boot disk(s)",
    )
    if gpu_public_ips:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="vpc.ipv4-address.public.count",
            region=region,
            required=total_gpu_nodes,
            reason=f"{total_gpu_nodes} GPU node public IP(s)",
        )
    if gpu_preemptible:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.instance.preemptible.count",
            region=region,
            required=total_gpu_nodes,
            reason=f"{total_gpu_nodes} preemptible GPU node(s)",
        )
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=(
                "GPU-type quota mapping for preemptible MK8s nodes is not exposed by the current "
                "quota API surface; only preemptible VM count was checked"
            ),
        )
    else:
        resources = (
            session.preset_resources(project_id=project_id, platform=gpu_platform, preset=gpu_preset)
            if gpu_platform and gpu_preset
            else None
        )
        if resources is None:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    f"unable to resolve GPU node preset '{gpu_platform}/{gpu_preset}' live; "
                    "GPU instance quotas were not checked"
                ),
            )
        else:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.instance.count",
                region=region,
                required=total_gpu_nodes,
                reason=f"{total_gpu_nodes} regular GPU node(s)",
            )
            gpu_suffix = _gpu_quota_suffix(gpu_platform)
            if gpu_suffix:
                _append_requirement(
                    requirements,
                    component_id=component_id,
                    instance_id=instance_id,
                    quota_name=f"compute.instance.gpu.{gpu_suffix}",
                    region=region,
                    required=resources.gpu_count * total_gpu_nodes,
                    reason=f"{total_gpu_nodes} GPU node(s) at {gpu_platform}/{gpu_preset}",
                )
            else:
                _append_gap(
                    gaps,
                    component_id=component_id,
                    instance_id=instance_id,
                    message=(
                        f"unable to derive GPU quota name from platform '{gpu_platform}'; "
                        "GPU-type quota was not checked"
                    ),
                )
    _append_gap(
        gaps,
        component_id=component_id,
        instance_id=instance_id,
        message=(
            "MK8s GPU node boot disk size/type is not exposed by current module inputs; "
            "disk-size quotas were not checked"
        ),
    )


def _requirements_for_component(
    *,
    session: _QuotaSession,
    payload: dict[str, Any],
    entry: ComponentEntry,
    row: dict[str, Any],
    project_id: str,
    default_region: str,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap]]:
    instance_id = component_instance_id(row)
    component_id = entry.id
    resolved_row = resolve_component_defaults(
        payload=payload,
        component_node=dict(row),
        entry=entry,
        preserve_existing_literal=True,
        preserve_existing_shared=False,
        include_shared=False,
    )
    inputs = resolved_row.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    region = _effective_region(inputs=inputs, default_region=default_region)

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []
    if component_id in {"ssh-jumphost", "wireguard-jumphost"}:
        _estimate_jump_host_requirements(
            session=session,
            project_id=project_id,
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
            gaps=gaps,
        )
    elif component_id == "vm":
        _estimate_vm_requirements(
            session=session,
            project_id=project_id,
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
            gaps=gaps,
        )
    elif component_id == "sfs":
        _estimate_sfs_requirements(
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
        )
    elif component_id == "object-storage":
        _estimate_object_storage_requirements(
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            requirements=requirements,
        )
    elif component_id == "mysterybox":
        _estimate_mysterybox_requirements(
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
            gaps=gaps,
        )
    elif component_id == "managed-postgresql":
        _estimate_managed_postgresql_requirements(
            session=session,
            project_id=project_id,
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
            gaps=gaps,
        )
    elif component_id == "mk8s":
        _estimate_mk8s_requirements(
            session=session,
            project_id=project_id,
            region=region,
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            requirements=requirements,
            gaps=gaps,
        )
    else:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=(
                f"no built-in quota estimator exists for component '{component_id}'; "
                "its Nebius quota usage was not checked"
            ),
        )
    return requirements, gaps


def assess_live_quotas(
    config: Any,
    *,
    context: str = "quota assessment",
    all_regions: bool = False,
) -> QuotaReport:
    payload = to_plain_data(config)
    checked_at = datetime.now(UTC).isoformat()
    if not isinstance(payload, dict):
        return QuotaReport(
            tenant_id="",
            project_id="",
            region_id="",
            checked_at=checked_at,
            errors=("runtime config payload is not a mapping",),
        )

    client_info = payload.get("client_info")
    nebius = client_info.get("nebius") if isinstance(client_info, dict) else None
    tenant_id = _as_text(nebius.get("tenant_id") if isinstance(nebius, dict) else None)
    project_id = _as_text(nebius.get("project_id") if isinstance(nebius, dict) else None)
    region_id = _as_text(nebius.get("region_id") if isinstance(nebius, dict) else None)
    if not tenant_id or not project_id:
        return QuotaReport(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            checked_at=checked_at,
            errors=("config is missing client_info.nebius.tenant_id or project_id",),
        )

    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    enabled_rows = [
        item
        for item in (components if isinstance(components, list) else [])
        if isinstance(item, dict) and bool(item.get("enabled", False))
    ]
    if not enabled_rows:
        return QuotaReport(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            checked_at=checked_at,
        )

    try:
        session = _QuotaSession(context=context, project_id=project_id)
    except Exception as exc:
        return QuotaReport(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            checked_at=checked_at,
            errors=(str(exc).strip() or exc.__class__.__name__,),
        )

    errors: list[str] = []
    checks: tuple[QuotaCheck, ...] = ()
    regional_availability: tuple[RegionalQuotaAvailability, ...] = ()
    gaps: list[QuotaCoverageGap] = []
    try:
        try:
            tenant_quotas = session.list_quotas(parent_id=tenant_id)
        except Exception as exc:
            tenant_quotas = {}
            errors.append(f"tenant quota lookup failed for {tenant_id}: {exc}")
        try:
            project_quotas = session.list_quotas(parent_id=project_id)
        except Exception as exc:
            project_quotas = {}
            errors.append(f"project quota lookup failed for {project_id}: {exc}")

        requirements: list[QuotaRequirement] = []
        for row in enabled_rows:
            component_id = component_type_id(row)
            if not component_id:
                continue
            entry = entry_by_id.get(component_id)
            instance_id = component_instance_id(row)
            if entry is None:
                _append_gap(
                    gaps,
                    component_id=component_id,
                    instance_id=instance_id,
                    message=(
                        f"component '{component_id}' is enabled but not present in the active catalog; "
                        "quota usage was not checked"
                    ),
                )
                continue
            row_requirements, row_gaps = _requirements_for_component(
                session=session,
                payload=payload,
                entry=entry,
                row=row,
                project_id=project_id,
                default_region=region_id,
            )
            requirements.extend(row_requirements)
            gaps.extend(row_gaps)

        aggregated_requirements = _aggregate_requirements(requirements)
        checks = tuple(
            _evaluate_requirement(
                item,
                tenant_quotas=tenant_quotas,
                project_quotas=project_quotas,
            )
            for item in aggregated_requirements
        )
        if all_regions and aggregated_requirements:
            regions = _sorted_quota_regions(
                tenant_quotas,
                project_quotas,
                current_region=region_id,
            )
            regional_availability = tuple(
                _regional_availability_for_requirement(
                    item,
                    tenant_quotas=tenant_quotas,
                    project_quotas=project_quotas,
                    regions=regions,
                    current_region=region_id,
                )
                for item in aggregated_requirements
            )
    except Exception as exc:
        errors.append(str(exc).strip() or exc.__class__.__name__)
    finally:
        try:
            session.close()
        except Exception as exc:
            errors.append(f"quota session cleanup failed: {exc}")

    return QuotaReport(
        tenant_id=tenant_id,
        project_id=project_id,
        region_id=region_id,
        checked_at=checked_at,
        checks=checks,
        coverage_gaps=tuple(gaps),
        errors=tuple(errors),
        regional_availability=regional_availability,
    )
