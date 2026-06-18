"""Live Nebius quota assessment helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

from .capacity_dashboard import (
    CapacityResourceAdvice,
    capacity_mode_available,
    capacity_mode_sort_key,
    filter_capacity_resource_advice,
    list_capacity_resource_advice,
)
from .component_defaults import resolve_component_defaults
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .components import ComponentEntry, component_entries
from .runtime_config import to_plain_data
from .sdk_auth import _ensure_iam_token_from_cli, init_nebius_sdk
from .terminal_styles import warning_markup

_GIB = 1024 * 1024 * 1024
_NPC_BENIGN_STDERR_MARKERS = ("token from NEBIUS_IAM_TOKEN env is used",)


class _NpcQuotaRequestUnavailableError(RuntimeError):
    """Raised when the internal quota-request API path is unavailable."""


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


def _gpu_quota_name(platform: str) -> str:
    suffix = _gpu_quota_suffix(platform)
    if suffix:
        return f"compute.instance.gpu.{suffix}"
    normalized = _as_text(platform).lower().replace("_", "-")
    return f"gpu.capacity.{normalized or 'unknown'}"


def _disk_size_bytes(mapping: dict[str, Any]) -> int | None:
    size_fields = (
        ("size_bytes", 1),
        ("size_kibibytes", 1024),
        ("size_mebibytes", 1024 * 1024),
        ("size_gibibytes", _GIB),
    )
    for key, multiplier in size_fields:
        resolved = _positive_int(mapping.get(key))
        if resolved is not None and resolved > 0:
            return resolved * multiplier
    return None


def _compute_boot_disk_gap_message() -> str:
    return (
        "Compute VM boot-disk quota could not be fully evaluated; set "
        "inputs.boot_disk_size_gib and inputs.boot_disk_type, or let cxcli "
        "materialize compute.boot_disk_defaults before running quota checks."
    )


def _format_disk_size_bytes(value: int) -> str:
    if value % _GIB == 0:
        return f"{value // _GIB} GiB"
    return f"{value} byte"


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
    id: str = ""


@dataclass(frozen=True)
class PlatformPresetResources:
    platform: str
    preset: str
    vcpu_count: int
    memory_gibibytes: int
    gpu_count: int
    allow_gpu_clustering: bool


@dataclass(frozen=True)
class GpuCapacityShape:
    platform: str
    preset: str
    fabric: str
    mode: str
    gpu_count_per_instance: int


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
    gpu_capacity_shape: GpuCapacityShape | None = None


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
    gpu_capacity_shape: GpuCapacityShape | None = None


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
    tenant_quota_id: str = ""
    project_quota_id: str = ""


@dataclass(frozen=True)
class QuotaRequestChange:
    container_id: str
    container_scope: str
    quota_name: str
    region: str
    current_limit: int | None
    current_usage: int
    required: int
    requested_limit: int
    unit: str


@dataclass(frozen=True)
class QuotaRequestFailure:
    change: QuotaRequestChange
    message: str
    permission_denied: bool = False


@dataclass(frozen=True)
class QuotaRequestResult:
    planned_changes: tuple[QuotaRequestChange, ...]
    submitted_changes: tuple[QuotaRequestChange, ...] = ()
    failed_changes: tuple[QuotaRequestFailure, ...] = ()
    unavailable_reason: str = ""

    @property
    def permission_denied_failures(self) -> tuple[QuotaRequestFailure, ...]:
        return tuple(item for item in self.failed_changes if item.permission_denied)


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


def _format_console_amount(amount: int | None, unit: str) -> str:
    formatted = _format_amount(amount, unit)
    if amount is None or unit != "byte" or amount == 0 or formatted == f"{amount} byte":
        return formatted
    return f"{formatted} ({amount} byte)"


def _available_quota(limit: Any, usage: Any) -> int | None:
    limit_value = _positive_int(limit)
    if limit_value is None:
        return None
    usage_value = _positive_int(usage) or 0
    return max(limit_value - usage_value, 0)


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message or "statuscode.not_found" in message


def _is_permission_denied_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "permission denied" in message
        or "permissiondenied" in message
        or "permission_denied" in message
        or "unauthorizedsingle" in message
        or "unauthorizedmany" in message
    )


def _container_scope_for_parent(parent_id: str) -> str:
    normalized = _as_text(parent_id)
    if normalized.startswith("tenant-"):
        return "tenant"
    if normalized.startswith("project-"):
        return "project"
    return "container"


def _clean_npc_stderr(stderr: str) -> str:
    filtered = [
        line.strip()
        for line in stderr.splitlines()
        if line.strip() and line.strip() not in _NPC_BENIGN_STDERR_MARKERS
    ]
    return "\n".join(filtered)


def _sorted_quota_regions(
    tenant_quotas: dict[tuple[str, str], QuotaRecord],
    project_quotas: dict[tuple[str, str], QuotaRecord],
    *,
    current_region: str,
    extra_regions: tuple[str, ...] = (),
) -> tuple[str, ...]:
    regions = {
        region
        for _, region in tuple(tenant_quotas.keys()) + tuple(project_quotas.keys())
        if _as_text(region)
    }
    regions.update(region for region in extra_regions if _as_text(region))
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
        dimension_label = (
            "checked quota dimension" if check_count == 1 else "checked quota dimensions"
        )
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
    uses_capacity_dashboard = any(
        check.source_scope.startswith("capacity-dashboard")
        for item in report.regional_availability
        for check in item.region_checks
    )
    lines = [
        "Regional live availability for the current config shape:"
        if uses_capacity_dashboard
        else "Regional quota availability for the current config shape:"
    ]
    for item in report.regional_availability:
        required = _format_amount(item.required, item.unit)
        lines.append(
            f"  - {item.component_label}: {item.quota_name} requires {required} ({item.reason})"
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


def _quota_check_uses_capacity_dashboard(item: QuotaCheck) -> bool:
    return item.source_scope.startswith("capacity-dashboard") or item.description.startswith(
        "Capacity Dashboard"
    )


def _quota_check_source_detail(item: QuotaCheck) -> str:
    if not _quota_check_uses_capacity_dashboard(item) or not item.description:
        return ""
    return f" via {item.description}"


def _quota_report_label(text: str, *, markup: bool) -> str:
    return warning_markup(text) if markup else text


def format_quota_report_lines(
    report: QuotaReport,
    *,
    phase: str,
    include_coverage_gaps: bool = True,
    include_confirmed_components: bool = False,
    markup: bool = True,
) -> list[str]:
    lines: list[str] = []
    if report.errors:
        lines.append(
            f"{_quota_report_label('Quota check warning:', markup=markup)} "
            f"{phase} quota assessment was not fully available."
        )
        for item in report.errors:
            lines.append(f"  - {item}")
    if report.insufficient_checks:
        shortage_kind = (
            "Nebius quota/capacity"
            if any(_quota_check_uses_capacity_dashboard(item) for item in report.insufficient_checks)
            else "Nebius quota"
        )
        lines.append(
            f"{_quota_report_label('Quota warning:', markup=markup)} "
            f"{phase} detected insufficient {shortage_kind}."
        )
        for item in report.insufficient_checks:
            required = _format_amount(item.required, item.unit)
            available = _format_amount(item.available, item.unit)
            source_detail = _quota_check_source_detail(item)
            lines.append(
                "  - "
                f"{item.component_label}: {item.region} {item.quota_name} requires {required}, "
                f"available {available}{source_detail} ({item.reason})"
            )
    if report.unknown_checks:
        unresolved_kind = (
            "live quota/capacity limits"
            if any(_quota_check_uses_capacity_dashboard(item) for item in report.unknown_checks)
            else "live quota limits"
        )
        lines.append(
            f"{_quota_report_label('Quota unresolved:', markup=markup)} "
            f"{phase} could not resolve one or more {unresolved_kind}."
        )
        for item in report.unknown_checks:
            required = _format_amount(item.required, item.unit)
            source_detail = _quota_check_source_detail(item)
            lines.append(
                "  - "
                f"{item.component_label}: {item.region} {item.quota_name} requires {required}, "
                f"available unknown{source_detail} ({item.reason})"
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
            f"{_quota_report_label('Quota coverage gap:', markup=markup)} "
            "quota could not be fully evaluated for the following component(s)."
        )
        for component_label, gap_messages in _coverage_gap_component_details(report):
            lines.append(f"  - {component_label}")
            lines.append("    gaps:")
            for gap_message in gap_messages:
                lines.append(f"      - {gap_message}")
    lines.extend(_regional_availability_lines(report))
    return lines


def plan_quota_request_changes(report: QuotaReport) -> tuple[QuotaRequestChange, ...]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in report.insufficient_checks:
        scope_specs = (
            (
                "tenant",
                report.tenant_id,
                item.tenant_limit,
                item.tenant_usage,
            ),
            (
                "project",
                report.project_id,
                item.project_limit,
                item.project_usage,
            ),
        )
        for scope, container_id, limit, usage in scope_specs:
            if not container_id or limit is None:
                continue
            available = _available_quota(limit, usage)
            if available is None or available >= item.required:
                continue
            key = (scope, container_id, item.quota_name, item.region)
            state = grouped.setdefault(
                key,
                {
                    "container_scope": scope,
                    "container_id": container_id,
                    "quota_name": item.quota_name,
                    "region": item.region,
                    "current_limit": limit,
                    "current_usage": int(usage or 0),
                    "required": 0,
                    "unit": item.unit,
                },
            )
            state["required"] = int(state["required"]) + item.required

    planned = [
        QuotaRequestChange(
            container_id=str(state["container_id"]),
            container_scope=str(state["container_scope"]),
            quota_name=str(state["quota_name"]),
            region=str(state["region"]),
            current_limit=cast(int | None, state["current_limit"]),
            current_usage=int(state["current_usage"]),
            required=int(state["required"]),
            requested_limit=max(
                int(state["current_limit"] or 0),
                int(state["current_usage"]) + int(state["required"]),
            ),
            unit=str(state["unit"]),
        )
        for _, state in sorted(grouped.items())
    ]
    return tuple(planned)


def format_quota_request_lines(changes: tuple[QuotaRequestChange, ...]) -> list[str]:
    if not changes:
        return []
    lines = ["Planned quota requests for confirmed shortages:"]
    for item in changes:
        current_limit = _format_amount(item.current_limit, item.unit)
        current_usage = _format_amount(item.current_usage, item.unit)
        requested_limit = _format_amount(item.requested_limit, item.unit)
        lines.append(
            "  - "
            f"{item.container_scope} {item.container_id}: {item.region} {item.quota_name} "
            f"target {requested_limit} (current limit {current_limit}, current usage {current_usage})"
        )
    return lines


def format_quota_request_manual_followup_lines(
    changes: tuple[QuotaRequestChange, ...],
) -> list[str]:
    if not changes:
        return []
    lines: list[str] = []
    for item in changes:
        current_limit = _format_console_amount(item.current_limit, item.unit)
        requested_limit = _format_console_amount(item.requested_limit, item.unit)
        minimum_increase = _format_console_amount(
            max(item.requested_limit - int(item.current_limit or 0), 0),
            item.unit,
        )
        lines.append(
            "  - "
            f"{item.container_scope} {item.container_id}: {item.region} {item.quota_name} "
            f"-> request total limit at least {requested_limit} "
            f"(increase by at least {minimum_increase} over current limit {current_limit})"
        )
    return lines


def request_quota_changes(
    report: QuotaReport,
    *,
    context: str = "quota request",
) -> QuotaRequestResult:
    candidate_changes = plan_quota_request_changes(report)
    if not candidate_changes:
        return QuotaRequestResult(planned_changes=())

    session = _QuotaSession(context=context, project_id=report.project_id)
    try:
        try:
            planned_changes = session.recommend_quota_request_changes(candidate_changes)
        except _NpcQuotaRequestUnavailableError as exc:
            return QuotaRequestResult(
                planned_changes=candidate_changes,
                unavailable_reason=str(exc),
            )
        if not planned_changes:
            planned_changes = candidate_changes
        try:
            session.submit_quota_requests(planned_changes)
        except _NpcQuotaRequestUnavailableError as exc:
            return QuotaRequestResult(
                planned_changes=planned_changes,
                unavailable_reason=str(exc),
            )
        except Exception as exc:
            if not _is_permission_denied_error(exc):
                raise
            return QuotaRequestResult(
                planned_changes=planned_changes,
                failed_changes=tuple(
                    QuotaRequestFailure(
                        change=item,
                        message=str(exc),
                        permission_denied=True,
                    )
                    for item in planned_changes
                ),
            )
    finally:
        session.close()
    return QuotaRequestResult(
        planned_changes=planned_changes,
        submitted_changes=planned_changes,
    )


class _QuotaSession:
    def __init__(self, *, context: str, project_id: str) -> None:
        self._sdk = init_nebius_sdk(
            parent_id=project_id or None,
            context=context,
            prefer_operator_auth=True,
        )
        self._context = context
        self._npc_path = shutil.which("npc") or ""
        self._preset_cache: dict[tuple[str, str, str], PlatformPresetResources | None] = {}
        self._capacity_resource_advice_cache: dict[str, tuple[CapacityResourceAdvice, ...]] = {}
        self._quota_cache: dict[str, dict[tuple[str, str], QuotaRecord]] = {}

    def close(self) -> None:
        self._sdk.sync_close()

    def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
        if parent_id in self._quota_cache:
            return self._quota_cache[parent_id]

        from nebius.api.nebius.quotas.v1 import (
            ListQuotaAllowancesRequest,
            QuotaAllowanceServiceClient,
        )

        client = QuotaAllowanceServiceClient(self._sdk)
        page_token = ""
        seen_tokens: set[str] = set()
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
                    limit=_positive_int(getattr(spec, "limit", None)),
                    usage=_positive_int(getattr(status, "usage", 0)) or 0,
                    service=_as_text(getattr(status, "service", None)),
                    description=_as_text(getattr(status, "description", None)),
                    unit=_as_text(getattr(status, "unit", None)),
                    state=_as_text(getattr(getattr(status, "state", None), "name", None)),
                    usage_state=_as_text(
                        getattr(getattr(status, "usage_state", None), "name", None)
                    ),
                    usage_percentage=_as_text(getattr(status, "usage_percentage", None)),
                    id=_as_text(getattr(metadata, "id", None)),
                )
            page_token = _as_text(getattr(response, "next_page_token", None))
            if not page_token:
                self._quota_cache[parent_id] = items
                return items
            if page_token in seen_tokens:
                raise RuntimeError(
                    "Quota allowance listing received a repeated pagination token from "
                    "the Nebius API; aborting to avoid an infinite list loop."
                )
            seen_tokens.add(page_token)

    def _npc_env(self) -> dict[str, str]:
        if not self._npc_path:
            raise _NpcQuotaRequestUnavailableError(
                "QuotaRequest submission requires the internal `npc` CLI because "
                "QuotaRequest/QuotaRecommendation are separate from QuotaAllowance and are "
                "not exposed through the public Nebius Python SDK in this environment."
            )
        env = dict(os.environ)
        token = _as_text(env.get("NEBIUS_IAM_TOKEN")) or _ensure_iam_token_from_cli()
        if not token:
            raise _NpcQuotaRequestUnavailableError(
                "QuotaRequest submission requires an IAM token that `npc` can reuse. "
                "Set NEBIUS_IAM_TOKEN or log in with the Nebius CLI so "
                "`nebius iam get-access-token` works."
            )
        env["NEBIUS_IAM_TOKEN"] = token
        return env

    def _run_npc_json(self, *args: str, timeout_seconds: int = 120) -> dict[str, Any]:
        result = subprocess.run(
            [self._npc_path, "--no-browser", "--format", "json", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=self._npc_env(),
        )
        stdout = result.stdout.strip()
        stderr = _clean_npc_stderr(result.stderr)
        if result.returncode != 0:
            detail = (
                stderr
                or stdout
                or f"`npc {' '.join(args)}` failed with exit code {result.returncode}"
            )
            raise RuntimeError(detail)
        if not stdout:
            return {}
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse JSON output from `npc {' '.join(args)}` during {self._context}: {exc}"
            ) from exc
        return parsed if isinstance(parsed, dict) else {"items": parsed}

    def recommend_quota_request_changes(
        self,
        changes: tuple[QuotaRequestChange, ...],
    ) -> tuple[QuotaRequestChange, ...]:
        if not changes:
            return ()
        requested_items = [
            {
                "metadata": {"parent_id": item.container_id},
                "spec": {
                    "quota_name": item.quota_name,
                    "region": item.region,
                    "requested_limit": item.requested_limit,
                },
            }
            for item in changes
        ]
        response = self._run_npc_json(
            "quotas",
            "quota-recommendation",
            "list",
            "--items",
            json.dumps(requested_items, separators=(",", ":")),
        )
        raw_items = list(response.get("items", []) or [])
        if not raw_items:
            return changes

        input_by_key = {(item.container_id, item.quota_name, item.region): item for item in changes}
        recommended: dict[tuple[str, str, str], QuotaRequestChange] = {}

        for raw_item in raw_items:
            metadata = raw_item.get("metadata", {}) if isinstance(raw_item, dict) else {}
            spec = raw_item.get("spec", {}) if isinstance(raw_item, dict) else {}
            status = raw_item.get("status", {}) if isinstance(raw_item, dict) else {}
            parent_id = _mapping_text(metadata, "parent_id")
            quota_name = _mapping_text(spec, "quota_name")
            region = _mapping_text(spec, "region")
            if not parent_id or not quota_name or not region:
                continue

            allowance = self.list_quotas(parent_id=parent_id).get((quota_name, region))
            current_limit = (
                _positive_int(status.get("current_limit")) if isinstance(status, dict) else None
            )
            if current_limit is None and allowance is not None:
                current_limit = allowance.limit

            recommended_limit = (
                _positive_int(status.get("recommended_limit")) if isinstance(status, dict) else None
            )
            if recommended_limit is None:
                recommended_limit = (
                    _positive_int(spec.get("requested_limit")) if isinstance(spec, dict) else None
                )

            fallback = input_by_key.get((parent_id, quota_name, region))
            if recommended_limit is None and fallback is not None:
                recommended_limit = fallback.requested_limit
            if recommended_limit is None:
                continue

            current_usage = allowance.usage if allowance is not None else 0
            unit = _mapping_text(status, "unit") or (
                allowance.unit if allowance is not None else ""
            )
            minimum_increase = max(recommended_limit - int(current_limit or 0), 0)
            required = max(minimum_increase, fallback.required if fallback is not None else 0)

            key = (parent_id, quota_name, region)
            recommended[key] = QuotaRequestChange(
                container_id=parent_id,
                container_scope=_container_scope_for_parent(parent_id),
                quota_name=quota_name,
                region=region,
                current_limit=current_limit,
                current_usage=current_usage,
                required=required,
                requested_limit=recommended_limit,
                unit=unit or (fallback.unit if fallback is not None else ""),
            )

        return tuple(item for _, item in sorted(recommended.items()))

    def submit_quota_requests(self, changes: tuple[QuotaRequestChange, ...]) -> None:
        if not changes:
            return
        requested_items = [
            {
                "metadata": {"parent_id": item.container_id},
                "spec": {
                    "quota_name": item.quota_name,
                    "region": item.region,
                    "requested_limit": item.requested_limit,
                },
            }
            for item in changes
        ]
        self._run_npc_json(
            "quotas",
            "quota-request",
            "batch-create",
            "--items",
            json.dumps(requested_items, separators=(",", ":")),
        )

    def list_capacity_resource_advice(
        self, *, parent_id: str
    ) -> tuple[CapacityResourceAdvice, ...]:
        if parent_id in self._capacity_resource_advice_cache:
            return self._capacity_resource_advice_cache[parent_id]

        resolved = list_capacity_resource_advice(self._sdk, parent_id=parent_id)
        self._capacity_resource_advice_cache[parent_id] = resolved
        return resolved

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
                allow_gpu_clustering=bool(getattr(item, "allow_gpu_clustering", False)),
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
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None = (),
) -> QuotaCheck:
    key = (requirement.quota_name, requirement.region)
    tenant = tenant_quotas.get(key)
    project = project_quotas.get(key)

    tenant_limit = tenant.limit if tenant is not None else None
    tenant_usage = tenant.usage if tenant is not None else None
    project_limit = project.limit if project is not None else None
    project_usage = project.usage if project is not None else None

    tenant_available = _available_quota(tenant_limit, tenant_usage)
    project_available = _available_quota(project_limit, project_usage)

    if tenant_available is not None and project_available is not None:
        regular_available = min(tenant_available, project_available)
        source_scope = "tenant+project"
    elif project_available is not None:
        regular_available = project_available
        source_scope = "project"
    elif tenant_available is not None:
        regular_available = tenant_available
        source_scope = "tenant"
    else:
        regular_available = None
        source_scope = "unresolved"

    record = project or tenant
    unit = record.unit if record is not None else ""
    description = record.description if record is not None else ""
    if requirement.gpu_capacity_shape is not None:
        available, sufficient, source_scope, dashboard_description = _gpu_capacity_availability(
            requirement,
            capacity_resource_advice=capacity_resource_advice,
        )
        unit = unit or "count"
        description = dashboard_description or description
    elif regular_available is not None:
        available = regular_available
        sufficient = requirement.required <= regular_available
    else:
        available = None
        sufficient = None
        source_scope = "unresolved"

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
        sufficient=sufficient,
        tenant_limit=tenant_limit,
        tenant_usage=tenant_usage,
        project_limit=project_limit,
        project_usage=project_usage,
        source_scope=source_scope,
        description=description,
        contributors=requirement.contributors,
        tenant_quota_id=tenant.id if tenant is not None else "",
        project_quota_id=project.id if project is not None else "",
    )


def _gpu_capacity_advice(
    requirement: AggregatedQuotaRequirement,
    *,
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None,
) -> CapacityResourceAdvice | None:
    shape = requirement.gpu_capacity_shape
    if shape is None or capacity_resource_advice is None:
        return None
    matching = filter_capacity_resource_advice(
        capacity_resource_advice,
        region_id=requirement.region,
        platform_name=shape.platform,
        preset_name=shape.preset,
        fabric=shape.fabric,
    )
    if not matching and shape.fabric:
        return None
    if not matching:
        matching = filter_capacity_resource_advice(
            capacity_resource_advice,
            region_id=requirement.region,
            platform_name=shape.platform,
            preset_name=shape.preset,
        )
    if not matching:
        return None
    return sorted(
        matching,
        key=lambda item: capacity_mode_sort_key(item, mode=shape.mode),
    )[0]


def _gpu_capacity_availability(
    requirement: AggregatedQuotaRequirement,
    *,
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None,
) -> tuple[int | None, bool | None, str, str]:
    shape = requirement.gpu_capacity_shape
    if shape is None:
        return None, None, "unresolved", ""
    if capacity_resource_advice is None:
        return (
            None,
            None,
            "unresolved",
            "Capacity Dashboard GPU availability could not be loaded",
        )

    selected = _gpu_capacity_advice(
        requirement,
        capacity_resource_advice=capacity_resource_advice,
    )
    if selected is None:
        fabric_detail = f", fabric {shape.fabric}" if shape.fabric else ""
        return (
            0,
            False,
            "capacity-dashboard",
            "Capacity Dashboard reported no matching GPU shape row for "
            f"{shape.platform}/{shape.preset}{fabric_detail}",
        )

    lane_name, available_vm_slots = capacity_mode_available(selected, mode=shape.mode)
    available = available_vm_slots * shape.gpu_count_per_instance
    fabric_detail = f", fabric {selected.fabric}" if selected.fabric else ""
    if lane_name == "auto":
        lane_detail = "AUTO reservation policy: reserved + on-demand VM slots"
    elif shape.mode == "reserved":
        lane_detail = "STRICT reservation policy: reserved VM slots"
    elif shape.mode == "on-demand":
        lane_detail = "FORBID reservation policy: on-demand VM slots"
    else:
        lane_detail = f"{lane_name} VM slots"
    description = f"Capacity Dashboard GPU availability ({lane_detail}{fabric_detail}, converted to GPU units)"
    return (
        available,
        requirement.required <= available,
        f"capacity-dashboard/{lane_name}",
        description,
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
        gpu_capacity_shape=requirement.gpu_capacity_shape,
    )


def _regional_availability_for_requirement(
    requirement: AggregatedQuotaRequirement,
    *,
    tenant_quotas: dict[tuple[str, str], QuotaRecord],
    project_quotas: dict[tuple[str, str], QuotaRecord],
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None,
    regions: tuple[str, ...],
    current_region: str,
) -> RegionalQuotaAvailability:
    region_checks = tuple(
        _evaluate_requirement(
            _regionalize_requirement(requirement, region=region),
            tenant_quotas=tenant_quotas,
            project_quotas=project_quotas,
            capacity_resource_advice=capacity_resource_advice,
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
    grouped: dict[tuple[str, str, GpuCapacityShape | None], list[QuotaRequirement]] = {}
    for item in requirements:
        grouped.setdefault(
            (item.quota_name, item.region, item.gpu_capacity_shape),
            [],
        ).append(item)

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
                gpu_capacity_shape=items[0].gpu_capacity_shape,
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
    gpu_capacity_shape: GpuCapacityShape | None = None,
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
            gpu_capacity_shape=gpu_capacity_shape,
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


def _gpu_capacity_shape(
    *,
    platform: str,
    preset: str,
    fabric: str,
    mode: str,
    resources: PlatformPresetResources,
) -> GpuCapacityShape:
    return GpuCapacityShape(
        platform=_as_text(platform),
        preset=_as_text(preset),
        fabric=_as_text(fabric) if resources.allow_gpu_clustering else "",
        mode=_as_text(mode).lower() or "regular",
        gpu_count_per_instance=resources.gpu_count,
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
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=_gpu_quota_name(platform),
                region=region,
                required=resources.gpu_count,
                reason=f"{resources.gpu_count} GPU(s) from {platform}/{preset}",
                gpu_capacity_shape=_gpu_capacity_shape(
                    platform=platform,
                    preset=preset,
                    fabric="",
                    mode="regular",
                    resources=resources,
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

    raw_disk_type = _mapping_text(inputs, "boot_disk_type")
    disk_type = _disk_quota_suffix(raw_disk_type)
    disk_size_gib = _positive_int(inputs.get("boot_disk_size_gib"))
    _append_requirement(
        requirements,
        component_id=component_id,
        instance_id=instance_id,
        quota_name="compute.disk.count",
        region=region,
        required=1,
        reason="one boot disk",
    )
    if disk_type and disk_size_gib is not None:
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name=f"compute.disk.size.{disk_type}",
            region=region,
            required=disk_size_gib * _GIB,
            reason=f"{disk_size_gib} GiB boot disk",
        )
    else:
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message=_compute_boot_disk_gap_message(),
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
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=_gpu_quota_name(platform),
                region=region,
                required=resources.gpu_count,
                reason=f"{resources.gpu_count} preemptible GPU(s) from {platform}/{preset}",
                gpu_capacity_shape=_gpu_capacity_shape(
                    platform=platform,
                    preset=preset,
                    fabric=gpu_cluster_fabric if gpu_cluster_enabled else "",
                    mode="preemptible",
                    resources=resources,
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
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=_gpu_quota_name(platform),
                region=region,
                required=resources.gpu_count,
                reason=f"{resources.gpu_count} GPU(s) from {platform}/{preset}",
                gpu_capacity_shape=_gpu_capacity_shape(
                    platform=platform,
                    preset=preset,
                    fabric=gpu_cluster_fabric if gpu_cluster_enabled else "",
                    mode="regular",
                    resources=resources,
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
        raw_disk_type = _mapping_text(inputs, "boot_disk_type")
        disk_type = _disk_quota_suffix(raw_disk_type)
        disk_size_gib = _positive_int(inputs.get("boot_disk_size_gib"))
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.disk.count",
            region=region,
            required=1,
            reason="one managed boot disk",
        )
        if disk_type and disk_size_gib is not None:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=f"compute.disk.size.{disk_type}",
                region=region,
                required=disk_size_gib * _GIB,
                reason=f"{disk_size_gib} GiB boot disk",
            )
        else:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=_compute_boot_disk_gap_message(),
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
    if not isinstance(secrets, list):
        _append_gap(
            gaps,
            component_id=component_id,
            instance_id=instance_id,
            message="`inputs.secrets` is missing or not a list; secret-count quota was not checked",
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


def _node_group_count(group: Mapping[str, Any]) -> int | None:
    fixed_count = _positive_int(group.get("node_count"))
    if fixed_count is not None:
        return fixed_count
    autoscaling = group.get("autoscaling")
    if isinstance(autoscaling, Mapping):
        for key in ("initial_count", "initial_node_count", "min_count", "min_node_count"):
            count = _positive_int(autoscaling.get(key))
            if count is not None:
                return count
    return None


def _node_group_template_value(group: Mapping[str, Any], path: str) -> Any:
    template = group.get("template")
    return _path_value(template, path) if isinstance(template, dict) else None


def _node_group_platform(inputs: dict[str, Any], group: Mapping[str, Any], *, gpu: bool) -> str:
    return _mapping_text(group, "platform") or _as_text(
        _node_group_template_value(group, "resources.platform")
    )


def _node_group_preset(inputs: dict[str, Any], group: Mapping[str, Any], *, gpu: bool) -> str:
    return _mapping_text(group, "preset") or _as_text(
        _node_group_template_value(group, "resources.preset")
    )


def _node_group_preemptible(inputs: dict[str, Any], group: Mapping[str, Any], *, gpu: bool) -> bool:
    if group.get("preemptible") is not None:
        return bool(group.get("preemptible"))
    template_preemptible = _node_group_template_value(group, "preemptible")
    if template_preemptible is not None:
        return bool(template_preemptible)
    return False


def _node_group_reservation_policy(group: Mapping[str, Any], *, gpu: bool) -> str:
    if not gpu:
        return ""
    reservation = group.get("reservation")
    if isinstance(reservation, Mapping):
        policy = _mapping_text(reservation, "policy")
        if policy:
            return policy.upper()
    policy = _mapping_text(group, "reservation_policy")
    return policy.upper() if policy else "FORBID"


def _gpu_capacity_mode(*, preemptible: bool, reservation_policy: str) -> str:
    if preemptible:
        return "preemptible"
    normalized_policy = _as_text(reservation_policy).upper()
    if normalized_policy == "AUTO":
        return "auto"
    if normalized_policy == "STRICT":
        return "reserved"
    if normalized_policy == "FORBID":
        return "on-demand"
    return "regular"


def _node_group_public_ips(inputs: dict[str, Any], group: Mapping[str, Any], *, gpu: bool) -> bool:
    if group.get("public_ips") is not None:
        return bool(group.get("public_ips"))
    network_interfaces = group.get("network_interfaces")
    if network_interfaces is None:
        network_interfaces = _node_group_template_value(group, "network_interfaces")
    if isinstance(network_interfaces, list):
        return any(
            isinstance(item, Mapping) and item.get("public_ip_address") is not None
            for item in network_interfaces
        )
    return False


def _node_group_boot_disk(
    inputs: dict[str, Any],
    group: Mapping[str, Any],
    *,
    gpu: bool,
) -> dict[str, Any]:
    value = group.get("boot_disk")
    if isinstance(value, dict):
        return value
    template_value = _node_group_template_value(group, "boot_disk")
    if isinstance(template_value, dict):
        return template_value
    return {}


def _estimate_mk8s_generic_node_group_requirements(
    *,
    session: _QuotaSession,
    project_id: str,
    region: str,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    requirements: list[QuotaRequirement],
    gaps: list[QuotaCoverageGap],
) -> int:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping):
        return 0

    generic_gpu_nodes = 0
    for group_name, raw_group in node_groups.items():
        if not isinstance(raw_group, Mapping) or raw_group.get("enabled") is False:
            continue
        count = _node_group_count(raw_group)
        group_label = str(group_name)
        gpu = bool(raw_group.get("gpu", False))
        if count is None:
            _append_gap(
                gaps,
                component_id=component_id,
                instance_id=instance_id,
                message=(
                    f"generic MK8s node group '{group_label}' does not declare a fixed or "
                    "initial autoscaling node count; node quotas were not checked"
                ),
            )
            continue
        if count <= 0:
            continue
        if gpu:
            generic_gpu_nodes += count

        platform = _node_group_platform(inputs, raw_group, gpu=gpu)
        preset = _node_group_preset(inputs, raw_group, gpu=gpu)
        preemptible = _node_group_preemptible(inputs, raw_group, gpu=gpu)
        reservation_policy = _node_group_reservation_policy(raw_group, gpu=gpu)
        public_ips = _node_group_public_ips(inputs, raw_group, gpu=gpu)

        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.disk.count",
            region=region,
            required=count,
            reason=f"{count} node boot disk(s) for MK8s node group '{group_label}'",
        )
        if public_ips:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="vpc.ipv4-address.public.count",
                region=region,
                required=count,
                reason=f"{count} node public IP(s) for MK8s node group '{group_label}'",
            )
        _append_requirement(
            requirements,
            component_id=component_id,
            instance_id=instance_id,
            quota_name="compute.instance.preemptible.count"
            if preemptible
            else "compute.instance.count",
            region=region,
            required=count,
            reason=(
                f"{count} {'preemptible ' if preemptible else ''}node(s) for MK8s "
                f"node group '{group_label}'"
            ),
        )

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
                    f"unable to resolve MK8s node group '{group_label}' preset "
                    f"'{platform}/{preset}' live; instance quotas were not checked"
                ),
            )
        elif gpu:
            gpu_cluster_key = _mapping_text(raw_group, "gpu_cluster_key")
            gpu_clusters = inputs.get("gpu_clusters")
            gpu_fabric = (
                _mapping_text(gpu_clusters.get(gpu_cluster_key), "infiniband_fabric")
                if isinstance(gpu_clusters, Mapping)
                and gpu_cluster_key
                and isinstance(gpu_clusters.get(gpu_cluster_key), Mapping)
                else ""
            )
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=_gpu_quota_name(platform),
                region=region,
                required=resources.gpu_count * count,
                reason=(
                    f"{count} GPU node(s) at {platform}/{preset} for '{group_label}'"
                    f" (reservation policy {reservation_policy or 'FORBID'})"
                ),
                gpu_capacity_shape=_gpu_capacity_shape(
                    platform=platform,
                    preset=preset,
                    fabric=gpu_fabric,
                    mode=_gpu_capacity_mode(
                        preemptible=preemptible,
                        reservation_policy=reservation_policy,
                    ),
                    resources=resources,
                ),
            )
        else:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name="compute.instance.non-gpu.vcpu",
                region=region,
                required=resources.vcpu_count * count,
                reason=f"{count} CPU node(s) at {platform}/{preset} for '{group_label}'",
            )

        boot_disk = _node_group_boot_disk(inputs, raw_group, gpu=gpu)
        disk_type = _disk_quota_suffix(_mapping_text(boot_disk, "type"))
        disk_bytes = _disk_size_bytes(boot_disk)
        if disk_type and disk_bytes is not None:
            _append_requirement(
                requirements,
                component_id=component_id,
                instance_id=instance_id,
                quota_name=f"compute.disk.size.{disk_type}",
                region=region,
                required=count * disk_bytes,
                reason=(
                    f"{count} boot disk(s) at {_format_disk_size_bytes(disk_bytes)} "
                    f"for MK8s node group '{group_label}'"
                ),
            )

    return generic_gpu_nodes


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

    _estimate_mk8s_generic_node_group_requirements(
        session=session,
        project_id=project_id,
        region=region,
        component_id=component_id,
        instance_id=instance_id,
        inputs=inputs,
        requirements=requirements,
        gaps=gaps,
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
    if component_id in {"ssh-jumphost", "wireguard-gw"}:
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


def estimate_mk8s_quota_requirements(
    *,
    project_id: str,
    region: str,
    instance_id: str,
    inputs: Mapping[str, Any],
    context: str = "quota assessment",
) -> tuple[tuple[QuotaRequirement, ...], tuple[QuotaCoverageGap, ...]]:
    """Estimate live MK8s quota requirements for one normalized MK8s input payload."""
    session = _QuotaSession(context=context, project_id=project_id)
    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []
    try:
        _estimate_mk8s_requirements(
            session=session,
            project_id=project_id,
            region=region,
            component_id="mk8s",
            instance_id=instance_id,
            inputs=dict(inputs),
            requirements=requirements,
            gaps=gaps,
        )
    finally:
        session.close()
    return tuple(requirements), tuple(gaps)


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
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None = ()
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
        if any(item.gpu_capacity_shape is not None for item in aggregated_requirements):
            try:
                capacity_resource_advice = session.list_capacity_resource_advice(
                    parent_id=tenant_id
                )
            except Exception as exc:
                capacity_resource_advice = None
                errors.append(f"tenant Capacity Dashboard lookup failed for {tenant_id}: {exc}")
        checks = tuple(
            _evaluate_requirement(
                item,
                tenant_quotas=tenant_quotas,
                project_quotas=project_quotas,
                capacity_resource_advice=capacity_resource_advice,
            )
            for item in aggregated_requirements
        )
        if all_regions and aggregated_requirements:
            regions = _sorted_quota_regions(
                tenant_quotas,
                project_quotas,
                current_region=region_id,
                extra_regions=tuple(
                    item.region for item in (capacity_resource_advice or ()) if item.region
                ),
            )
            regional_availability = tuple(
                _regional_availability_for_requirement(
                    item,
                    tenant_quotas=tenant_quotas,
                    project_quotas=project_quotas,
                    capacity_resource_advice=capacity_resource_advice,
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


def assess_live_quota_requirements(
    *,
    tenant_id: str,
    project_id: str,
    region_id: str,
    requirements: Sequence[QuotaRequirement],
    coverage_gaps: Sequence[QuotaCoverageGap] = (),
    context: str = "quota assessment",
    all_regions: bool = False,
) -> QuotaReport:
    """Assess an explicit set of live quota requirements.

    This is used by workflows that operate on external resources and therefore
    cannot be represented as normal enabled Terraform infra components.
    """
    checked_at = datetime.now(UTC).isoformat()
    tenant_id = _as_text(tenant_id)
    project_id = _as_text(project_id)
    region_id = _as_text(region_id)
    if not tenant_id or not project_id:
        return QuotaReport(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            checked_at=checked_at,
            errors=("quota assessment is missing tenant_id or project_id",),
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
    gaps = list(coverage_gaps)
    capacity_resource_advice: tuple[CapacityResourceAdvice, ...] | None = ()
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

        aggregated_requirements = _aggregate_requirements(list(requirements))
        if any(item.gpu_capacity_shape is not None for item in aggregated_requirements):
            try:
                capacity_resource_advice = session.list_capacity_resource_advice(
                    parent_id=tenant_id
                )
            except Exception as exc:
                capacity_resource_advice = None
                errors.append(f"tenant Capacity Dashboard lookup failed for {tenant_id}: {exc}")
        checks = tuple(
            _evaluate_requirement(
                item,
                tenant_quotas=tenant_quotas,
                project_quotas=project_quotas,
                capacity_resource_advice=capacity_resource_advice,
            )
            for item in aggregated_requirements
        )
        if all_regions and aggregated_requirements:
            regions = _sorted_quota_regions(
                tenant_quotas,
                project_quotas,
                current_region=region_id,
                extra_regions=tuple(
                    item.region for item in (capacity_resource_advice or ()) if item.region
                ),
            )
            regional_availability = tuple(
                _regional_availability_for_requirement(
                    item,
                    tenant_quotas=tenant_quotas,
                    project_quotas=project_quotas,
                    capacity_resource_advice=capacity_resource_advice,
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
