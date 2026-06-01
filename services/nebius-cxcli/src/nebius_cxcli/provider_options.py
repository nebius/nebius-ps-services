"""Dynamic provider-backed option lookup for create wizard fields."""

from __future__ import annotations

import importlib
import ipaddress
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from .capacity_dashboard import (
    CapacityAdviceAvailability,
    CapacityResourceAdvice,
    capacity_level_rank,
    capacity_regular_sort_key,
    capacity_summary_text,
    filter_capacity_resource_advice,
    list_capacity_resource_advice,
)
from .deploy_targets import deploy_target_is_external_mk8s
from .sdk_auth import init_nebius_sdk

SUPPORTED_PROVIDER_OPTION_SOURCES = frozenset(
    {
        "mk8s_compatible_platforms",
        "mk8s_gpu_stack_presets",
        "mk8s_node_group_os_values",
        "mk8s_infiniband_fabrics",
        "compute_platforms",
        "compute_platform_presets",
        "compute_public_image_families",
        "compute_boot_disk_types",
        "capacity_block_groups",
        "operator_public_ip_cidr",
        "project_filesystems",
        "project_private_allocations",
        "project_private_pools",
        "project_subnets",
        "project_networks",
        "tenant_projects",
        "mk8s_control_plane_versions",
        "soperator_node_groups",
        "soperator_nodesets_profiles",
        "soperator_partition_profiles",
        "soperator_topology_profiles",
    }
)

ProviderOptionPlugin = Callable[..., Iterable[object] | None]
_OPTION_PLUGIN_ENV = "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS"
_REQUEST_TIMEOUT_ENV = "NEBIUS_CXCLI_PROVIDER_REQUEST_TIMEOUT_SECONDS"
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
_NEBIUS_LIST_PAGE_SIZE = 999
_CAPACITY_BLOCK_GROUP_LIST_PAGE_SIZE = 200
_DEFAULT_PROJECT_NETWORK_NAME = "default-network"


@dataclass(frozen=True)
class OptionChoice:
    value: str
    label: str
    recommended: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class TenantProjectValidationResult:
    valid: bool
    message: str = ""
    retryable: bool = True


@dataclass(frozen=True)
class _Mk8sCompatibilityItem:
    compatible_platforms: tuple[str, ...]
    # Nebius compatibility matrix field name. Although the API calls this
    # `drivers_preset`, it selects the bundled GPU image/software-stack family.
    drivers_preset: str | None
    os: str | None


@dataclass(frozen=True)
class _ComputePlatformPreset:
    name: str
    vcpu_count: int | None
    memory_gibibytes: int | None
    gpu_count: int | None
    allow_gpu_clustering: bool


@dataclass(frozen=True)
class _ComputePublicImageFamily:
    family: str
    human_name: str
    compatibility: str


@dataclass(frozen=True)
class _CapacityLaneSummary:
    availability: CapacityAdviceAvailability
    fabric: str


@dataclass(frozen=True)
class _PresetCapacitySummary:
    preset: str
    best_regular: CapacityResourceAdvice
    on_demand: _CapacityLaneSummary
    reserved: _CapacityLaneSummary

    @property
    def best_regular_available(self) -> int:
        return max(self.on_demand.availability.available, self.reserved.availability.available)


def _normalize_plugin_choices(items: Iterable[object] | None) -> list[OptionChoice]:
    if not items:
        return []
    out: list[OptionChoice] = []
    seen: set[str] = set()
    for item in items:
        value: str | None = None
        label: str | None = None
        if isinstance(item, OptionChoice):
            value = item.value
            label = item.label
            recommended = item.recommended
        elif isinstance(item, dict):
            raw_value = item.get("value")
            raw_label = item.get("label")
            value = str(raw_value).strip() if raw_value is not None else ""
            label = str(raw_label).strip() if raw_label is not None else value
            recommended = bool(item.get("recommended", False))
        else:
            value = str(item).strip()
            label = value
            recommended = False
        if not value or value in seen:
            continue
        out.append(OptionChoice(value=value, label=label or value, recommended=recommended))
        seen.add(value)
    return out


def _is_default_project_network_name(value: str) -> bool:
    return value.strip().casefold() == _DEFAULT_PROJECT_NETWORK_NAME


def _is_live_fabric_name(value: object) -> bool:
    normalized = _as_str(value)
    return bool(normalized) and normalized.upper() not in {"N/A", "NA", "NONE"}


def _provider_request_timeout_seconds() -> float:
    raw = os.environ.get(_REQUEST_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            parsed = float(raw)
        except ValueError:
            return _DEFAULT_REQUEST_TIMEOUT_SECONDS
        if parsed > 0:
            return parsed
    return _DEFAULT_REQUEST_TIMEOUT_SECONDS


def _provider_request_kwargs() -> dict[str, float | int]:
    timeout_seconds = _provider_request_timeout_seconds()
    return {
        "timeout": timeout_seconds,
        "per_retry_timeout": timeout_seconds,
        "auth_timeout": timeout_seconds,
        "retries": 0,
    }


@lru_cache(maxsize=8)
def _load_option_plugins(specs: str) -> tuple[ProviderOptionPlugin, ...]:
    if not specs.strip():
        return ()
    plugins: list[ProviderOptionPlugin] = []
    for raw_spec in specs.split(","):
        spec = raw_spec.strip()
        if not spec:
            continue
        if ":" not in spec:
            continue
        module_name, function_name = spec.split(":", 1)
        try:
            module = importlib.import_module(module_name.strip())
            resolver = getattr(module, function_name.strip(), None)
        except Exception as exc:
            raise RuntimeError(
                f"Provider option plugin {spec!r} could not be loaded: {exc}"
            ) from exc
        if not callable(resolver):
            raise RuntimeError(f"Provider option plugin {spec!r} did not resolve to a callable")
        plugins.append(cast(ProviderOptionPlugin, resolver))
    return tuple(plugins)


def _payload_value(payload: dict[str, Any], dotted_path: str) -> object | None:
    def _token_parts(token: str) -> list[str | int] | None:
        if "[" not in token:
            return [token]
        base = token.split("[", maxsplit=1)[0]
        suffix = token[len(base) :]
        parts: list[str | int] = []
        if base:
            parts.append(base)
        while suffix:
            if not suffix.startswith("["):
                return None
            end = suffix.find("]")
            if end <= 1:
                return None
            index_raw = suffix[1:end]
            try:
                parts.append(int(index_raw))
            except ValueError:
                return None
            suffix = suffix[end + 1 :]
        return parts

    current: object = payload
    for segment in dotted_path.split("."):
        token = segment.strip()
        if not token:
            return None
        token_parts = _token_parts(token)
        if token_parts is None:
            return None
        for part in token_parts:
            if isinstance(part, int):
                if not isinstance(current, list):
                    return None
                if part < 0 or part >= len(current):
                    return None
                current_list = cast(list[object], current)
                part_index: int = part
                current = current_list[part_index]
                continue
            if not isinstance(current, dict):
                return None
            candidates = [part]
            underscore = part.replace("-", "_")
            if underscore not in candidates:
                candidates.append(underscore)
            hyphen = part.replace("_", "-")
            if hyphen not in candidates:
                candidates.append(hyphen)
            matched = False
            for candidate in candidates:
                if candidate in current:
                    current = current[candidate]
                    matched = True
                    break
            if not matched:
                return None
    return current


def _as_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _enum_token(value: object | None) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).strip().rsplit(".", maxsplit=1)[-1].upper()
    text = _as_str(value) or ""
    return text.rsplit(".", maxsplit=1)[-1].upper()


def _cidr_text(value: object | None) -> str | None:
    return _as_str(getattr(value, "cidr", None)) or _as_str(value)


def _cidr_texts(values: object) -> tuple[str, ...]:
    return tuple(
        cidr for cidr in (_cidr_text(item) for item in list(values or [])) if cidr
    )


def _arg_texts(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Mapping):
        return ()
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _ipv4_cidr_text(value: object | None) -> str | None:
    text = _as_str(value)
    if not text:
        return None
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    if network.version != 4:
        return None
    return str(network)


def _pool_cidrs(*, spec: object | None, status: object | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *_cidr_texts(getattr(status, "cidrs", [])),
                *_cidr_texts(getattr(spec, "cidrs", [])),
            ]
        )
    )


def _pool_assignment_ids(status: object | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    assignment = getattr(status, "assignment", None)
    if assignment is None:
        return (), ()

    def _assignment_texts(*field_names: str) -> tuple[str, ...]:
        values: list[str] = []
        for field_name in field_names:
            for item in list(getattr(assignment, field_name, []) or []):
                metadata = getattr(item, "metadata", None)
                text = (
                    _as_str(getattr(metadata, "id", None))
                    or _as_str(getattr(item, "id", None))
                    or _as_str(getattr(item, "network_id", None))
                    or _as_str(getattr(item, "subnet_id", None))
                    or _as_str(item)
                )
                if text:
                    values.append(text)
        return tuple(dict.fromkeys(values))

    networks = _assignment_texts("networks", "network_ids")
    subnets = _assignment_texts("subnets", "subnet_ids")
    return networks, subnets


def _component_input_sibling_path(field_path: str, input_name: str) -> str | None:
    marker = ".inputs."
    if marker not in field_path:
        return None
    prefix = field_path.rsplit(".", maxsplit=1)[0]
    if not prefix or not input_name.strip():
        return None
    return f"{prefix}.{input_name.strip()}"


def _provider_error_message(provider: str, exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{provider}: {message}"


def _unsupported_platform_names(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(str(key).strip() for key in value if str(key).strip())
    names: list[str] = []
    for item in list(value or []):
        key = _as_str(getattr(item, "key", None))
        if key:
            names.append(key)
    return tuple(names)


def _apply_choice_filter(
    choices: Iterable[OptionChoice],
    *,
    filter_pattern: str | None,
) -> list[OptionChoice]:
    resolved = list(choices)
    normalized_pattern = _as_str(filter_pattern)
    if not normalized_pattern:
        return resolved
    try:
        compiled = re.compile(normalized_pattern)
    except re.error:
        return resolved
    return [choice for choice in resolved if compiled.search(choice.value)]


def _mk8s_gpu_preference_lists() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from .component_sources import load_component_sources, tf_module_source_by_id
    except (ImportError, OSError, RuntimeError, ValueError):
        return (), ()
    settings = tf_module_source_by_id("mk8s", sources=load_component_sources())
    if settings is None:
        return (), ()
    preferences = settings.mk8s_gpu.image_preferences
    return preferences.preferred_gpu_stack_presets, preferences.preferred_os


def _sort_choices_by_preference(
    choices: Iterable[OptionChoice],
    *,
    preferred_values: tuple[str, ...],
) -> tuple[OptionChoice, ...]:
    preferred_index = {value: index for index, value in enumerate(preferred_values)}
    return tuple(
        sorted(
            choices,
            key=lambda choice: (
                preferred_index.get(choice.value, len(preferred_index)),
                0 if choice.recommended else 1,
                choice.value,
            ),
        )
    )


def _capacity_advice_sort_key(item: CapacityResourceAdvice) -> tuple[int, int, int, int, str]:
    return capacity_regular_sort_key(item)


def _capacity_fabric_sort_key(
    item: CapacityResourceAdvice,
    *,
    prefer_reserved: bool,
) -> tuple[int, int, int, int, int, int, int, str]:
    regular_key = _capacity_advice_sort_key(item)
    return (
        0 if not prefer_reserved or item.reserved.available > 0 else 1,
        -item.reserved.available if prefer_reserved else 0,
        capacity_level_rank(item.reserved.availability_level) if prefer_reserved else 0,
        *regular_key,
    )


def _capacity_summary_text(item: CapacityResourceAdvice) -> str:
    return capacity_summary_text(item)


def _capacity_lane_sort_key(
    item: CapacityResourceAdvice,
    *,
    lane_name: str,
) -> tuple[int, int, str]:
    lane = item.on_demand if lane_name == "on_demand" else item.reserved
    return (
        -lane.available,
        capacity_level_rank(lane.availability_level),
        item.fabric,
    )


def _capacity_preset_summary(items: Iterable[CapacityResourceAdvice]) -> _PresetCapacitySummary:
    rows = tuple(items)
    if not rows:
        raise ValueError("capacity preset summary requires at least one advice row")
    best_regular = sorted(rows, key=_capacity_advice_sort_key)[0]
    best_on_demand = sorted(
        rows,
        key=lambda item: _capacity_lane_sort_key(item, lane_name="on_demand"),
    )[0]
    best_reserved = sorted(
        rows,
        key=lambda item: _capacity_lane_sort_key(item, lane_name="reserved"),
    )[0]
    return _PresetCapacitySummary(
        preset=best_regular.preset,
        best_regular=best_regular,
        on_demand=_CapacityLaneSummary(
            availability=best_on_demand.on_demand,
            fabric=best_on_demand.fabric,
        ),
        reserved=_CapacityLaneSummary(
            availability=best_reserved.reserved,
            fabric=best_reserved.fabric,
        ),
    )


def _capacity_preset_summary_text(summary: _PresetCapacitySummary) -> str:
    return (
        f"live on-demand VMs={summary.on_demand.availability.available}, "
        f"reserved VMs={summary.reserved.availability.available}"
    )


def _capacity_preset_fabric_summary_parts(
    summary: _PresetCapacitySummary,
    *,
    allow_gpu_clustering: bool,
) -> tuple[str, ...]:
    if not allow_gpu_clustering:
        return ()
    parts: list[str] = []
    best_fabric = summary.best_regular.fabric
    if best_fabric:
        parts.append(f"best fabric {best_fabric}")
    reserved_fabric = summary.reserved.fabric
    if (
        reserved_fabric
        and reserved_fabric != best_fabric
        and summary.reserved.availability.available > 0
    ):
        parts.append(f"best reserved fabric {reserved_fabric}")
    return tuple(parts)


def _gpu_preset_interconnect_suffix(preset: _ComputePlatformPreset) -> tuple[str, ...]:
    gpu_count = preset.gpu_count
    if gpu_count in (None, 0):
        return ()
    if preset.allow_gpu_clustering:
        return ("GPU cluster", "InfiniBand")
    if gpu_count == 1:
        return ("Ethernet only", "testing/dev")
    return ("Ethernet only",)


def _enum_member_name(enum_cls: object, value: object) -> str:
    members = getattr(enum_cls, "__members__", {})
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return _as_str(value) or ""
    if isinstance(members, Mapping):
        for name, member in members.items():
            try:
                if int(member) == numeric_value:
                    return str(name)
            except (TypeError, ValueError):
                continue
    return str(numeric_value)


class ProviderOptionLookup:
    """Resolve dynamic field choices from Nebius APIs with in-process caching."""

    def __init__(self) -> None:
        self._sdk: Any | None = None
        self._sdk_failed = False
        self._cache: dict[tuple[object, ...], tuple[OptionChoice, ...]] = {}
        self._mk8s_compatibility_cache: dict[str, tuple[_Mk8sCompatibilityItem, ...]] = {}
        self._compute_platform_preset_cache: dict[
            tuple[str, str], tuple[_ComputePlatformPreset, ...]
        ] = {}
        self._compute_public_image_family_cache: dict[
            tuple[str, str], tuple[_ComputePublicImageFamily, ...]
        ] = {}
        self._capacity_resource_advice_cache: dict[str, tuple[CapacityResourceAdvice, ...]] = {}
        self._last_error: str | None = None

    def resolve(
        self,
        *,
        provider: str,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> list[OptionChoice]:
        self._last_error = None
        try:
            resolver = {
                "mk8s_compatible_platforms": self._resolve_mk8s_compatible_platforms,
                "mk8s_gpu_stack_presets": self._resolve_mk8s_gpu_stack_presets,
                "mk8s_node_group_os_values": self._resolve_mk8s_node_group_os_values,
                "mk8s_infiniband_fabrics": self._resolve_mk8s_infiniband_fabrics,
                "compute_boot_disk_types": self._resolve_compute_boot_disk_types,
                "capacity_block_groups": self._resolve_capacity_block_groups,
                "compute_platforms": self._resolve_compute_platforms,
                "compute_platform_presets": self._resolve_compute_platform_presets,
                "compute_public_image_families": self._resolve_compute_public_image_families,
                "project_filesystems": self._resolve_project_filesystems,
                "project_private_allocations": self._resolve_project_private_allocations,
                "project_private_pools": self._resolve_project_private_pools,
                "project_subnets": self._resolve_project_subnets,
                "project_networks": self._resolve_project_networks,
                "operator_public_ip_cidr": self._resolve_operator_public_ip_cidr,
                "tenant_projects": self._resolve_tenant_projects,
                "mk8s_control_plane_versions": self._resolve_mk8s_control_plane_versions,
                "soperator_node_groups": self._resolve_soperator_node_groups,
                "soperator_nodesets_profiles": self._resolve_soperator_nodesets_profiles,
                "soperator_partition_profiles": self._resolve_soperator_partition_profiles,
                "soperator_topology_profiles": self._resolve_soperator_topology_profiles,
            }.get(provider)
            if provider in SUPPORTED_PROVIDER_OPTION_SOURCES and resolver is not None:
                try:
                    builtin_choices = resolver(args=args, payload=payload, field_path=field_path)
                except Exception as exc:
                    self._last_error = _provider_error_message(provider, exc)
                else:
                    resolved = _apply_choice_filter(
                        builtin_choices,
                        filter_pattern=_as_str(args.get("_filter")),
                    )
                    if resolved:
                        self._last_error = None
                        return resolved

            plugin_specs = os.environ.get(_OPTION_PLUGIN_ENV, "")
            for plugin in _load_option_plugins(plugin_specs):
                try:
                    items = plugin(
                        provider=provider,
                        args=args,
                        payload=payload,
                        field_path=field_path,
                    )
                except Exception as exc:
                    self._last_error = _provider_error_message(provider, exc)
                    continue
                resolved = _apply_choice_filter(
                    _normalize_plugin_choices(items),
                    filter_pattern=_as_str(args.get("_filter")),
                )
                if resolved:
                    self._last_error = None
                    return resolved
            return []
        except Exception as exc:
            self._last_error = _provider_error_message(provider, exc)
            return []

    def last_error(self) -> str | None:
        return self._last_error

    def compute_platform_preset_allows_gpu_clustering(
        self,
        *,
        project_id: str,
        platform_name: str,
        preset_name: str,
    ) -> bool | None:
        normalized_project_id = _as_str(project_id)
        normalized_platform_name = _as_str(platform_name)
        normalized_preset_name = _as_str(preset_name)
        if not normalized_project_id or not normalized_platform_name or not normalized_preset_name:
            return None

        presets = self._resolve_compute_platform_preset_inventory(
            project_id=normalized_project_id,
            platform_name=normalized_platform_name,
        )
        for preset in presets:
            if preset.name == normalized_preset_name:
                return preset.allow_gpu_clustering
        return None

    def compute_platform_preset_resources(
        self,
        *,
        project_id: str,
        platform_name: str,
        preset_name: str,
    ) -> tuple[int | None, int | None, int | None] | None:
        normalized_project_id = _as_str(project_id)
        normalized_platform_name = _as_str(platform_name)
        normalized_preset_name = _as_str(preset_name)
        if not normalized_project_id or not normalized_platform_name or not normalized_preset_name:
            return None

        presets = self._resolve_compute_platform_preset_inventory(
            project_id=normalized_project_id,
            platform_name=normalized_platform_name,
        )
        for preset in presets:
            if preset.name == normalized_preset_name:
                return (preset.vcpu_count, preset.memory_gibibytes, preset.gpu_count)
        return None

    def compute_platform_preset_fabrics(
        self,
        *,
        tenant_id: str,
        project_id: str,
        region_id: str,
        platform_name: str,
        preset_name: str,
    ) -> tuple[CapacityResourceAdvice, ...]:
        normalized_tenant_id = _as_str(tenant_id)
        normalized_project_id = _as_str(project_id)
        normalized_region_id = _as_str(region_id)
        normalized_platform_name = _as_str(platform_name)
        normalized_preset_name = _as_str(preset_name)
        if (
            not normalized_tenant_id
            or not normalized_project_id
            or not normalized_region_id
            or not normalized_platform_name
            or not normalized_preset_name
        ):
            return ()

        allow_gpu_clustering = self.compute_platform_preset_allows_gpu_clustering(
            project_id=normalized_project_id,
            platform_name=normalized_platform_name,
            preset_name=normalized_preset_name,
        )
        if allow_gpu_clustering is False:
            return ()

        resolved = tuple(
            item
            for item in self._capacity_resource_advice_for_shape(
                tenant_id=normalized_tenant_id,
                region_id=normalized_region_id,
                platform_name=normalized_platform_name,
                preset_name=normalized_preset_name,
            )
            if _is_live_fabric_name(item.fabric)
        )
        return resolved

    def validate_tenant_project_scope(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> TenantProjectValidationResult:
        normalized_tenant_id = _as_str(tenant_id)
        if not normalized_tenant_id:
            return TenantProjectValidationResult(valid=False, message="Tenant ID is required.")
        normalized_project_id = _as_str(project_id)
        if not normalized_project_id:
            return TenantProjectValidationResult(valid=False, message="Project ID is required.")

        sdk = self._sdk_or_none()
        if sdk is None:
            return TenantProjectValidationResult(
                valid=False,
                message=(
                    "Unable to initialize Nebius SDK. "
                    "Provide Nebius SDK credentials via env vars, credentials file, or SDK config/profile."
                ),
                retryable=False,
            )

        try:
            from nebius.api.nebius.iam.v1 import (
                GetProjectRequest,
                GetTenantRequest,
                ProjectServiceClient,
                TenantServiceClient,
            )
        except Exception:
            return TenantProjectValidationResult(
                valid=False,
                message="Nebius IAM SDK bindings are unavailable in this environment.",
                retryable=False,
            )

        try:
            tenant = (
                TenantServiceClient(sdk)
                .get(
                    GetTenantRequest(id=normalized_tenant_id),
                    **_provider_request_kwargs(),
                )
                .wait()
            )
            resolved_tenant_id = _as_str(getattr(getattr(tenant, "metadata", None), "id", None))
            if resolved_tenant_id and resolved_tenant_id != normalized_tenant_id:
                return TenantProjectValidationResult(
                    valid=False,
                    message=(
                        f"Resolved tenant id '{resolved_tenant_id}' does not match "
                        f"input '{normalized_tenant_id}'."
                    ),
                )
        except Exception as exc:
            return TenantProjectValidationResult(
                valid=False,
                message=(
                    f"Tenant '{normalized_tenant_id}' does not exist or is not accessible "
                    f"with current credentials: {exc}"
                ),
            )

        try:
            project = (
                ProjectServiceClient(sdk)
                .get(
                    GetProjectRequest(id=normalized_project_id),
                    **_provider_request_kwargs(),
                )
                .wait()
            )
        except Exception as exc:
            return TenantProjectValidationResult(
                valid=False,
                message=(
                    f"Project '{normalized_project_id}' does not exist or is not accessible "
                    f"with current credentials: {exc}"
                ),
            )

        resolved_project_id = _as_str(getattr(getattr(project, "metadata", None), "id", None))
        if resolved_project_id and resolved_project_id != normalized_project_id:
            return TenantProjectValidationResult(
                valid=False,
                message=(
                    f"Resolved project id '{resolved_project_id}' does not match "
                    f"input '{normalized_project_id}'."
                ),
            )

        project_parent_id = _as_str(getattr(getattr(project, "metadata", None), "parent_id", None))
        if project_parent_id and project_parent_id != normalized_tenant_id:
            return TenantProjectValidationResult(
                valid=False,
                message=(
                    f"Project '{normalized_project_id}' belongs to tenant '{project_parent_id}', "
                    f"not '{normalized_tenant_id}'."
                ),
            )

        return TenantProjectValidationResult(valid=True)

    def resolve_tenant_project_names(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[str, str]:
        normalized_tenant_id = _as_str(tenant_id)
        normalized_project_id = _as_str(project_id)
        if not normalized_tenant_id or not normalized_project_id:
            return "", ""

        sdk = self._sdk_or_none()
        if sdk is None:
            return "", ""

        try:
            from nebius.api.nebius.iam.v1 import (
                GetProjectRequest,
                GetTenantRequest,
                ProjectServiceClient,
                TenantServiceClient,
            )
        except Exception:
            return "", ""

        tenant_name = ""
        project_name = ""
        try:
            tenant = (
                TenantServiceClient(sdk)
                .get(
                    GetTenantRequest(id=normalized_tenant_id),
                    **_provider_request_kwargs(),
                )
                .wait()
            )
            tenant_name = _as_str(getattr(getattr(tenant, "metadata", None), "name", None))
        except Exception:
            tenant_name = ""
        try:
            project = (
                ProjectServiceClient(sdk)
                .get(
                    GetProjectRequest(id=normalized_project_id),
                    **_provider_request_kwargs(),
                )
                .wait()
            )
            project_name = _as_str(getattr(getattr(project, "metadata", None), "name", None))
        except Exception:
            project_name = ""
        return tenant_name, project_name

    def _resolve_project_id(self, payload: dict[str, Any], args: dict[str, Any]) -> str | None:
        explicit_project = _as_str(args.get("project_id"))
        if explicit_project:
            return explicit_project

        project_path = _as_str(args.get("project_id_path")) or "client_info.nebius.project_id"
        resolved_project = _as_str(_payload_value(payload, project_path))
        if resolved_project:
            return resolved_project

        fallback_project_path = _as_str(args.get("fallback_project_id_path"))
        if fallback_project_path:
            fallback_project = _as_str(_payload_value(payload, fallback_project_path))
            if fallback_project:
                return fallback_project
        return None

    def _resolve_region_id(self, payload: dict[str, Any], args: dict[str, Any]) -> str | None:
        explicit_region = _as_str(args.get("region_id"))
        if explicit_region:
            return explicit_region

        region_path = _as_str(args.get("region_id_path")) or "client_info.nebius.region_id"
        resolved_region = _as_str(_payload_value(payload, region_path))
        if resolved_region:
            return resolved_region

        fallback_region_path = _as_str(args.get("fallback_region_id_path"))
        if fallback_region_path:
            fallback_region = _as_str(_payload_value(payload, fallback_region_path))
            if fallback_region:
                return fallback_region
        return None

    def _resolve_tenant_id(self, payload: dict[str, Any], args: dict[str, Any]) -> str | None:
        explicit_tenant = _as_str(args.get("tenant_id"))
        if explicit_tenant:
            return explicit_tenant
        tenant_path = _as_str(args.get("tenant_id_path")) or "client_info.nebius.tenant_id"
        return _as_str(_payload_value(payload, tenant_path))

    def _resolve_k8s_version(
        self,
        payload: dict[str, Any],
        args: dict[str, Any],
        field_path: str = "",
    ) -> str | None:
        candidate_paths: list[str] = []
        explicit_path = _as_str(args.get("kubernetes_version_path"))
        if explicit_path:
            candidate_paths.append(explicit_path)
        if ".inputs.node_group_defaults." in field_path:
            component_prefix = field_path.split(".inputs.", maxsplit=1)[0]
            candidate_paths.append(f"{component_prefix}.inputs.cluster.k8s_version")

        seen_paths: set[str] = set()
        for version_path in candidate_paths:
            normalized_path = version_path.strip()
            if not normalized_path or normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            explicit = _as_str(_payload_value(payload, normalized_path))
            if explicit:
                return explicit

        configured_default = _as_str(args.get("kubernetes_version_default"))
        if configured_default:
            return configured_default

        versions = self._resolve_mk8s_control_plane_versions(
            args={}, payload=payload, field_path=""
        )
        if not versions:
            return None
        return versions[0].value

    def _resolve_project_compute_platform_inventory(
        self,
        project_id: str,
    ) -> tuple[OptionChoice, ...]:
        cache_key = ("compute_platform_inventory", project_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.compute.v1 import ListPlatformsRequest, PlatformServiceClient

        client = PlatformServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListPlatformsRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            name = _as_str(getattr(metadata, "name", None))
            if not name:
                continue
            short_name = _as_str(getattr(spec, "short_human_readable_name", None))
            label = f"{name}  ({short_name})" if short_name else name
            options.append(OptionChoice(value=name, label=label))

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_compute_boot_disk_types(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        del args, payload, field_path
        from .compute_boot_disks import compute_boot_disk_type_choices

        return tuple(
            OptionChoice(value=item.value, label=item.label or item.value)
            for item in compute_boot_disk_type_choices()
        )

    def _resolve_capacity_block_groups(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        tenant_id = self._resolve_tenant_id(payload, args)
        if not tenant_id:
            return ()

        region_id = self._resolve_region_id(payload, args)
        platform_path = _as_str(args.get("platform_path"))
        platform_name = _as_str(args.get("platform"))
        if not platform_name and platform_path:
            platform_name = _as_str(_payload_value(payload, platform_path))

        fabric_path = _as_str(args.get("fabric_path"))
        fabric_name = _as_str(args.get("fabric"))
        if not fabric_name and fabric_path:
            fabric_name = _as_str(_payload_value(payload, fabric_path))

        service_name = _as_str(args.get("service")) or ""
        cache_key = (
            "capacity_block_groups",
            tenant_id,
            region_id,
            platform_name,
            fabric_name,
            service_name,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()

        from nebius.api.nebius.capacity.v1 import (
            CapacityBlockGroupServiceClient,
            CapacityBlockGroupStatus,
            ListCapacityBlockGroupsRequest,
        )

        client = CapacityBlockGroupServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListCapacityBlockGroupsRequest(
                parent_id=tenant_id,
                page_size=_CAPACITY_BLOCK_GROUP_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        choices: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            status = getattr(item, "status", None)
            block_id = _as_str(getattr(metadata, "id", None))
            if not block_id:
                continue
            status_region = _as_str(getattr(status, "region", None))
            if region_id and status_region and status_region != region_id:
                continue
            status_service = _as_str(getattr(status, "service", None))
            if service_name and status_service and status_service != service_name:
                continue
            affinity = getattr(status, "resource_affinity", None)
            compute_affinity = getattr(affinity, "compute_v1", None)
            affinity_platform = _as_str(getattr(compute_affinity, "platform", None))
            if platform_name and affinity_platform and affinity_platform != platform_name:
                continue
            affinity_fabric = _as_str(getattr(compute_affinity, "fabric", None))
            if fabric_name and affinity_fabric and affinity_fabric != fabric_name:
                continue

            state_name = _enum_member_name(
                CapacityBlockGroupStatus.State,
                getattr(status, "state", None),
            ).removeprefix("STATE_")
            if state_name in {"INACTIVE", "SHUTTING"}:
                continue
            usage_state_name = _enum_member_name(
                CapacityBlockGroupStatus.UsageState,
                getattr(status, "usage_state", None),
            ).removeprefix("USAGE_STATE_")
            current_limit = getattr(status, "current_limit", None)
            usage = getattr(status, "usage", None)
            available = (
                max(0, int(current_limit) - int(usage))
                if isinstance(current_limit, int) and isinstance(usage, int)
                else None
            )
            display_name = _as_str(getattr(metadata, "name", None)) or block_id
            label_parts = [display_name]
            details: list[str] = [block_id]
            if status_region:
                details.append(status_region)
            if affinity_platform:
                details.append(affinity_platform)
            if affinity_fabric:
                details.append(affinity_fabric)
            if state_name:
                details.append(state_name.lower())
            if usage_state_name:
                details.append(usage_state_name.lower().replace("_", "-"))
            if available is not None:
                details.append(f"available={available}")
            if details:
                label_parts.append(f"({', '.join(details)})")
            choices.append(
                OptionChoice(
                    value=block_id,
                    label="  ".join(label_parts),
                    recommended=bool(available and available > 0),
                )
            )

        choices.sort(key=lambda choice: (0 if choice.recommended else 1, choice.label))
        resolved = tuple(choices)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_operator_public_ip_cidr(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        del payload, field_path
        endpoint = _as_str(args.get("endpoint")) or "https://api.ipify.org"
        timeout = 5
        timeout_raw = args.get("timeout")
        if isinstance(timeout_raw, int | float) and timeout_raw > 0:
            timeout = int(timeout_raw)
        cache_key = ("operator_public_ip_cidr", endpoint, timeout)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            with urllib.request.urlopen(endpoint, timeout=timeout) as response:
                raw_ip = response.read().decode("utf-8").strip()
        except (TimeoutError, urllib.error.URLError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"operator public IP lookup failed: {exc}") from exc
        try:
            public_ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise RuntimeError(f"operator public IP lookup returned invalid IP: {raw_ip}") from exc
        if public_ip.version != 4:
            raise RuntimeError(
                f"operator public IP lookup returned IPv{public_ip.version}; expected IPv4"
            )
        value = f"{public_ip}/32"
        resolved = (
            OptionChoice(
                value=value,
                label=f"{value}  (detected operator public IP)",
                recommended=True,
            ),
        )
        self._cache[cache_key] = resolved
        return resolved

    def _soperator_nodesets_profile_catalog(
        self,
    ) -> tuple[str | None, Mapping[str, Mapping[str, Any]]]:
        from .component_sources import helm_chart_source_by_id

        chart = helm_chart_source_by_id("soperator")
        settings = getattr(chart, "soperator_nodesets", None)
        default = _as_str(getattr(settings, "default", None))
        raw_profiles = getattr(settings, "profiles", {}) if settings is not None else {}
        profiles = raw_profiles if isinstance(raw_profiles, Mapping) else {}
        return default, profiles

    @staticmethod
    def _soperator_catalog_label(raw: object, *, fallback: str) -> str:
        if isinstance(raw, Mapping):
            wizard = raw.get("wizard")
            if isinstance(wizard, Mapping):
                label = _as_str(wizard.get("label"))
                if label:
                    return label
            label = _as_str(raw.get("label"))
            if label:
                return label
        return fallback

    @staticmethod
    def _soperator_chart_row_path(field_path: str) -> str:
        if ".values." in field_path:
            return field_path.split(".values.", maxsplit=1)[0]
        if field_path.endswith(".profile"):
            return field_path.rsplit(".", maxsplit=1)[0]
        return field_path.rsplit(".", maxsplit=1)[0]

    @staticmethod
    def _normalize_component_token(value: object) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    def _soperator_target_ref_for_field(
        self,
        *,
        payload: dict[str, Any],
        field_path: str,
    ) -> str:
        row_path = self._soperator_chart_row_path(field_path)
        target_ref = self._normalize_component_token(
            _payload_value(payload, f"{row_path}.target_ref")
        )
        if target_ref:
            return target_ref
        target_ref = self._normalize_component_token(
            _payload_value(payload, f"{row_path}.instance_id")
        )
        if target_ref and target_ref != "soperator":
            return target_ref

        infra = payload.get("infra")
        components = infra.get("components") if isinstance(infra, Mapping) else None
        if not isinstance(components, list):
            return target_ref
        mk8s_refs: list[str] = []
        for row in components:
            if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
                continue
            if self._normalize_component_token(row.get("id")) != "mk8s":
                continue
            instance_id = self._normalize_component_token(row.get("instance_id")) or "mk8s"
            mk8s_refs.append(instance_id)
        if len(mk8s_refs) == 1:
            return mk8s_refs[0]
        return target_ref

    def _soperator_profile_for_field(
        self,
        *,
        payload: dict[str, Any],
        field_path: str,
    ) -> Mapping[str, Any]:
        from .component_sources import helm_chart_source_by_id

        chart = helm_chart_source_by_id("soperator")
        settings = getattr(chart, "soperator_nodesets", None)
        default = _as_str(getattr(settings, "default", None))
        profiles = getattr(settings, "profiles", {}) if settings is not None else {}
        row_path = self._soperator_chart_row_path(field_path)
        profile_name = _as_str(_payload_value(payload, f"{row_path}.profile")) or default
        profile = profiles.get(profile_name or "") if isinstance(profiles, Mapping) else None
        return profile if isinstance(profile, Mapping) else {}

    def _soperator_node_group_kind_for_role(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> str:
        explicit_kind = (_as_str(args.get("node_group_kind")) or "").lower()
        if explicit_kind:
            return explicit_kind
        role = _as_str(args.get("role"))
        if not role:
            return "all"
        profile = self._soperator_profile_for_field(payload=payload, field_path=field_path)
        role_mapping = profile.get("role_mapping")
        roles = role_mapping.get("roles") if isinstance(role_mapping, Mapping) else None
        raw_role = roles.get(role) if isinstance(roles, Mapping) else None
        if isinstance(raw_role, Mapping):
            return (_as_str(raw_role.get("default_node_group_kind")) or "all").lower()
        return "all"

    @staticmethod
    def _soperator_node_group_kind(raw_group: Mapping[str, Any]) -> str:
        gpu = raw_group.get("gpu")
        if isinstance(gpu, bool):
            return "gpu" if gpu else "cpu"
        if str(gpu or "").strip().lower() in {"1", "true", "yes", "on"}:
            return "gpu"
        return "cpu"

    def _resolve_soperator_node_groups(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        target_ref = self._soperator_target_ref_for_field(payload=payload, field_path=field_path)
        if not target_ref:
            return ()
        requested_kind = self._soperator_node_group_kind_for_role(
            args=args,
            payload=payload,
            field_path=field_path,
        )
        deploy = payload.get("deploy")
        targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
        if isinstance(targets, list):
            for row in targets:
                if not isinstance(row, Mapping) or not deploy_target_is_external_mk8s(row):
                    continue
                instance_id = self._normalize_component_token(row.get("instance_id"))
                if instance_id != target_ref:
                    continue
                inventory = row.get("inventory")
                node_groups = (
                    inventory.get("node_groups") if isinstance(inventory, Mapping) else None
                )
                if isinstance(node_groups, Mapping):
                    return self._soperator_node_group_choices_from_mapping(
                        node_groups,
                        requested_kind=requested_kind,
                    )
        infra = payload.get("infra")
        components = infra.get("components") if isinstance(infra, Mapping) else None
        if not isinstance(components, list):
            return ()

        for row in components:
            if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
                continue
            if self._normalize_component_token(row.get("id")) != "mk8s":
                continue
            instance_id = self._normalize_component_token(row.get("instance_id")) or "mk8s"
            if instance_id != target_ref:
                continue
            inputs = row.get("inputs")
            node_groups = inputs.get("node_groups") if isinstance(inputs, Mapping) else None
            if not isinstance(node_groups, Mapping):
                return ()
            return self._soperator_node_group_choices_from_mapping(
                node_groups,
                requested_kind=requested_kind,
            )
        return ()

    def _soperator_node_group_choices_from_mapping(
        self,
        node_groups: Mapping[str, Any],
        *,
        requested_kind: str,
    ) -> tuple[OptionChoice, ...]:
        choices: list[OptionChoice] = []
        for raw_key, raw_group in node_groups.items():
            key = self._normalize_component_token(raw_key)
            if not key or not isinstance(raw_group, Mapping):
                continue
            if raw_group.get("enabled") is False:
                continue
            kind = self._soperator_node_group_kind(raw_group)
            if requested_kind in {"cpu", "gpu"} and kind != requested_kind:
                continue
            platform = _as_str(raw_group.get("platform")) or ""
            preset = _as_str(raw_group.get("preset")) or ""
            suffix = " / ".join(part for part in (kind.upper(), platform, preset) if part)
            label = f"{key}  ({suffix})" if suffix else key
            choices.append(
                OptionChoice(
                    value=key,
                    label=label,
                    recommended=requested_kind == kind,
                )
            )
        return tuple(choices)

    def _resolve_soperator_nodesets_profiles(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        del args, payload, field_path
        default, profiles = self._soperator_nodesets_profile_catalog()
        choices: list[OptionChoice] = []
        for name, profile in profiles.items():
            profile_name = str(name).strip()
            if not profile_name:
                continue
            choices.append(
                OptionChoice(
                    value=profile_name,
                    label=self._soperator_catalog_label(profile, fallback=profile_name),
                    recommended=profile_name == default,
                )
            )
        return tuple(choices)

    def _resolve_soperator_partition_profiles(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        default_profile, profiles = self._soperator_nodesets_profile_catalog()
        default_partition_profile = _as_str(args.get("default"))
        chart_row_path = self._soperator_chart_row_path(field_path)
        profile_name = _as_str(_payload_value(payload, f"{chart_row_path}.profile"))
        profile_name = profile_name or default_profile
        profile = profiles.get(profile_name or "")
        if not isinstance(profile, Mapping):
            return ()
        chart = profile.get("chart")
        if not isinstance(chart, Mapping):
            return ()
        raw_partition_profiles = chart.get("partition_profiles")
        if not isinstance(raw_partition_profiles, Mapping):
            return ()
        choices: list[OptionChoice] = []
        for name, partition_profile in raw_partition_profiles.items():
            profile_value = str(name).strip()
            if not profile_value:
                continue
            choices.append(
                OptionChoice(
                    value=profile_value,
                    label=self._soperator_catalog_label(
                        partition_profile,
                        fallback=profile_value,
                    ),
                    recommended=bool(default_partition_profile)
                    and profile_value == default_partition_profile,
                )
            )
        return tuple(choices)

    def _resolve_soperator_topology_profiles(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        default_profile, profiles = self._soperator_nodesets_profile_catalog()
        default_topology_profile = _as_str(args.get("default"))
        chart_row_path = self._soperator_chart_row_path(field_path)
        profile_name = _as_str(_payload_value(payload, f"{chart_row_path}.profile"))
        profile_name = profile_name or default_profile
        profile = profiles.get(profile_name or "")
        if not isinstance(profile, Mapping):
            return ()
        chart = profile.get("chart")
        if not isinstance(chart, Mapping):
            return ()
        raw_topology_profiles = chart.get("topology_profiles")
        if not isinstance(raw_topology_profiles, Mapping):
            return ()
        choices: list[OptionChoice] = []
        for name, topology_profile in raw_topology_profiles.items():
            profile_value = str(name).strip()
            if not profile_value:
                continue
            choices.append(
                OptionChoice(
                    value=profile_value,
                    label=self._soperator_catalog_label(
                        topology_profile,
                        fallback=profile_value,
                    ),
                    recommended=bool(default_topology_profile)
                    and profile_value == default_topology_profile,
                )
            )
        return tuple(choices)

    def _resolve_mk8s_compatible_platforms(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        version = self._resolve_k8s_version(payload, args, field_path)
        if not version:
            return ()
        prefix = _as_str(args.get("platform_prefix"))
        project_id = self._resolve_project_id(payload, args)
        cache_key = ("mk8s_compatible_platforms", version, prefix, project_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        platforms: set[str] = set()
        for item in self._resolve_mk8s_compatibility_items(version):
            for platform in item.compatible_platforms:
                if prefix and not platform.startswith(prefix):
                    continue
                platforms.add(platform)

        compatible_names = sorted(platforms)
        if project_id:
            inventory = self._resolve_project_compute_platform_inventory(project_id)
            available_by_name = {choice.value: choice for choice in inventory}
            resolved = tuple(
                available_by_name[name] for name in compatible_names if name in available_by_name
            )
        else:
            resolved = tuple(OptionChoice(value=name, label=name) for name in compatible_names)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_mk8s_gpu_stack_presets(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        version = self._resolve_k8s_version(payload, args, field_path)
        if not version:
            return ()

        platform_path = _as_str(args.get("platform_path"))
        if not platform_path and field_path.endswith(".gpu_stack_preset"):
            prefix = field_path.rsplit(".", maxsplit=1)[0]
            if ".node_group_defaults.gpu." in field_path:
                platform_path = f"{prefix}.platform"

        platform_name = _as_str(args.get("platform"))
        if not platform_name and platform_path:
            platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        cache_key = ("mk8s_gpu_stack_presets", version, platform_name)
        if cache_key in self._cache:
            return self._cache[cache_key]

        options: list[OptionChoice] = []
        seen: set[str] = set()
        for item in self._resolve_mk8s_compatibility_items(version):
            if platform_name not in item.compatible_platforms:
                continue
            preset = item.drivers_preset
            if not preset or preset in seen:
                continue
            label = f"{preset}  ({item.os})" if item.os else preset
            options.append(OptionChoice(value=preset, label=label))
            seen.add(preset)

        preferred_gpu_stack_presets, _preferred_os = _mk8s_gpu_preference_lists()
        resolved = _sort_choices_by_preference(
            options,
            preferred_values=preferred_gpu_stack_presets,
        )
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_mk8s_node_group_os_values(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        version = self._resolve_k8s_version(payload, args, field_path)
        if not version:
            return ()

        platform_path = _as_str(args.get("platform_path"))
        if (
            not platform_path
            and field_path.endswith(".os")
            and (
                ".node_group_defaults.gpu." in field_path
                or ".node_group_defaults.cpu." in field_path
            )
        ):
            platform_path = f"{field_path.rsplit('.', maxsplit=1)[0]}.platform"
        platform_name = _as_str(args.get("platform"))
        if not platform_name and platform_path:
            platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        stack_preset_path = _as_str(args.get("stack_preset_path"))
        if not stack_preset_path and (
            ".node_group_defaults.gpu." in field_path and field_path.endswith(".os")
        ):
            stack_preset_path = f"{field_path.rsplit('.', maxsplit=1)[0]}.gpu_stack_preset"
        stack_preset = _as_str(args.get("stack_preset"))
        if not stack_preset and stack_preset_path:
            stack_preset = _as_str(_payload_value(payload, stack_preset_path))

        cache_key = ("mk8s_node_group_os_values", version, platform_name, stack_preset)
        if cache_key in self._cache:
            return self._cache[cache_key]

        options: list[OptionChoice] = []
        seen: set[str] = set()
        for item in self._resolve_mk8s_compatibility_items(version):
            if platform_name not in item.compatible_platforms:
                continue
            if item.drivers_preset != stack_preset:
                continue
            os_value = item.os
            if not os_value or os_value in seen:
                continue
            options.append(OptionChoice(value=os_value, label=os_value))
            seen.add(os_value)

        _preferred_gpu_stack_presets, preferred_os = _mk8s_gpu_preference_lists()
        resolved = _sort_choices_by_preference(options, preferred_values=preferred_os)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_mk8s_infiniband_fabrics(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        platform_path = _as_str(args.get("platform_path"))
        if not platform_path and field_path.endswith(".infiniband_fabric"):
            prefix = field_path.rsplit(".", maxsplit=1)[0]
            if ".node_group_defaults.gpu." in field_path:
                platform_path = f"{prefix}.platform"

        platform_name = _as_str(args.get("platform"))
        if not platform_name and platform_path:
            platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        project_id = self._resolve_project_id(payload, args)
        preset_path = _as_str(args.get("preset_path"))
        preset_name = _as_str(_payload_value(payload, preset_path)) if preset_path else None
        if project_id and preset_name:
            allow_gpu_clustering = self.compute_platform_preset_allows_gpu_clustering(
                project_id=project_id,
                platform_name=platform_name,
                preset_name=preset_name,
            )
            if allow_gpu_clustering is False:
                return ()

        region_id = _as_str(args.get("region_id")) or _as_str(
            _payload_value(payload, "client_info.nebius.region_id")
        )
        tenant_id = self._resolve_tenant_id(payload, args)
        cache_key = (
            "mk8s_infiniband_fabrics",
            platform_name,
            region_id,
            preset_name,
            tenant_id,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        advice_by_fabric: dict[str, CapacityResourceAdvice] = {}
        recommended_fabric = ""
        if tenant_id and region_id and preset_name and project_id:
            matching_advice = self.compute_platform_preset_fabrics(
                tenant_id=tenant_id,
                project_id=project_id,
                region_id=region_id,
                platform_name=platform_name,
                preset_name=preset_name,
            )
            if matching_advice:
                prefer_reserved = any(item.reserved.available > 0 for item in matching_advice)
                matching_advice = tuple(
                    sorted(
                        matching_advice,
                        key=lambda item: _capacity_fabric_sort_key(
                            item,
                            prefer_reserved=prefer_reserved,
                        ),
                    )
                )
                if prefer_reserved:
                    if matching_advice[0].reserved.available > 0:
                        recommended_fabric = matching_advice[0].fabric
                elif matching_advice[0].best_regular_available > 0:
                    recommended_fabric = matching_advice[0].fabric
                advice_by_fabric = {item.fabric: item for item in matching_advice}
            else:
                self._last_error = (
                    "Live Capacity Dashboard returned no fabric rows for the selected "
                    f"cluster-capable GPU shape {platform_name}/{preset_name} in {region_id}."
                )
                return ()

        resolved = tuple(
            OptionChoice(
                value=item.fabric,
                label=(
                    f"{item.fabric}  ({platform_name}, {region_id}), "
                    f"{_capacity_summary_text(item)}"
                    + (
                        ", recommended for reservations"
                        if item.fabric == recommended_fabric and item.reserved.available > 0
                        else ", recommended"
                        if item.fabric == recommended_fabric
                        else ""
                    )
                ),
                recommended=item.fabric == recommended_fabric,
                metadata={
                    "on_demand_vms": item.on_demand.available,
                    "reserved_vms": item.reserved.available,
                },
            )
            for item in advice_by_fabric.values()
        )
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_compute_platforms(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        prefix = _as_str(args.get("platform_prefix"))
        cache_key = ("compute_platforms", project_id, prefix)
        if cache_key in self._cache:
            return self._cache[cache_key]

        inventory = self._resolve_project_compute_platform_inventory(project_id)
        resolved = tuple(
            choice for choice in inventory if not prefix or choice.value.startswith(prefix)
        )
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_compute_platform_presets(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()

        platform_path = _as_str(args.get("platform_path"))
        if not platform_path:
            if field_path.endswith(".preset"):
                platform_path = f"{field_path[: -len('.preset')]}.platform"
            else:
                return ()

        platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        require_gpu_clustering = False
        gpu_cluster_required_path = _as_str(args.get("gpu_cluster_required_path"))
        if gpu_cluster_required_path:
            require_gpu_clustering = bool(
                _as_str(_payload_value(payload, gpu_cluster_required_path))
            )
        tenant_id = self._resolve_tenant_id(payload, args)
        region_id = self._resolve_region_id(payload, args)

        cache_key = (
            "compute_platform_presets",
            project_id,
            platform_name,
            require_gpu_clustering,
            tenant_id,
            region_id,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        options: list[OptionChoice] = []
        preset_by_name: dict[str, _ComputePlatformPreset] = {}
        for preset in self._resolve_compute_platform_preset_inventory(
            project_id=project_id,
            platform_name=platform_name,
        ):
            preset_name = preset.name
            allow_gpu_clustering = preset.allow_gpu_clustering
            if require_gpu_clustering and not allow_gpu_clustering:
                continue
            preset_by_name[preset_name] = preset
            suffix_parts: list[str] = []
            if preset.vcpu_count is not None:
                suffix_parts.append(f"vCPU={preset.vcpu_count}")
            if preset.memory_gibibytes is not None:
                suffix_parts.append(f"RAM={preset.memory_gibibytes}GiB")
            if preset.gpu_count not in (None, 0):
                suffix_parts.append(f"GPU={preset.gpu_count}")
            suffix_parts.extend(_gpu_preset_interconnect_suffix(preset))
            label = f"{preset_name}  ({', '.join(suffix_parts)})" if suffix_parts else preset_name
            options.append(OptionChoice(value=preset_name, label=label))

        if tenant_id and region_id and platform_name.startswith("gpu-"):
            advice_rows_by_preset: dict[str, list[CapacityResourceAdvice]] = {}
            for item in self._capacity_resource_advice_for_shape(
                tenant_id=tenant_id,
                region_id=region_id,
                platform_name=platform_name,
            ):
                if not item.preset:
                    continue
                advice_rows_by_preset.setdefault(item.preset, []).append(item)

            advice_by_preset = {
                preset: _capacity_preset_summary(rows)
                for preset, rows in advice_rows_by_preset.items()
                if rows
            }

            recommended_preset = ""
            ranked_advice = sorted(
                advice_by_preset.values(),
                key=lambda item: _capacity_advice_sort_key(item.best_regular),
            )
            if ranked_advice and ranked_advice[0].best_regular_available > 0:
                recommended_preset = ranked_advice[0].preset

            original_order = {choice.value: index for index, choice in enumerate(options)}
            options = [
                OptionChoice(
                    value=choice.value,
                    label=(
                        f"{choice.label}, "
                        f"{_capacity_preset_summary_text(advice_by_preset[choice.value])}"
                        + "".join(
                            f", {part}"
                            for part in _capacity_preset_fabric_summary_parts(
                                advice_by_preset[choice.value],
                                allow_gpu_clustering=(
                                    preset_by_name.get(choice.value) is not None
                                    and preset_by_name[choice.value].allow_gpu_clustering
                                ),
                            )
                        )
                        + (", recommended" if choice.value == recommended_preset else "")
                    )
                    if choice.value in advice_by_preset
                    else choice.label,
                    recommended=choice.value == recommended_preset,
                    metadata=(
                        {
                            **choice.metadata,
                            "on_demand_vms": advice_by_preset[
                                choice.value
                            ].on_demand.availability.available,
                            "reserved_vms": advice_by_preset[
                                choice.value
                            ].reserved.availability.available,
                        }
                        if choice.value in advice_by_preset
                        else choice.metadata
                    ),
                )
                for choice in sorted(
                    options,
                    key=lambda choice: (
                        0 if choice.value in advice_by_preset else 1,
                        _capacity_advice_sort_key(advice_by_preset[choice.value].best_regular)
                        if choice.value in advice_by_preset
                        else (0, 0, 0, 0, ""),
                        original_order[choice.value],
                    ),
                )
            ]

        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _capacity_resource_advice_for_shape(
        self,
        *,
        tenant_id: str,
        region_id: str,
        platform_name: str,
        preset_name: str = "",
    ) -> tuple[CapacityResourceAdvice, ...]:
        resolved = filter_capacity_resource_advice(
            self._resolve_capacity_resource_advice_inventory(tenant_id=tenant_id),
            region_id=region_id,
            platform_name=platform_name,
            preset_name=preset_name,
        )
        return tuple(sorted(resolved, key=_capacity_advice_sort_key))

    def _resolve_capacity_resource_advice_inventory(
        self,
        *,
        tenant_id: str,
    ) -> tuple[CapacityResourceAdvice, ...]:
        if tenant_id in self._capacity_resource_advice_cache:
            return self._capacity_resource_advice_cache[tenant_id]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        try:
            cached = list_capacity_resource_advice(sdk, parent_id=tenant_id)
        except Exception as exc:
            self._last_error = f"capacity resource advice lookup failed: {exc}"
            return ()
        self._capacity_resource_advice_cache[tenant_id] = cached
        return cached

    def _resolve_compute_platform_preset_inventory(
        self,
        *,
        project_id: str,
        platform_name: str,
    ) -> tuple[_ComputePlatformPreset, ...]:
        cache_key = (project_id, platform_name)
        if cache_key in self._compute_platform_preset_cache:
            return self._compute_platform_preset_cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.common.v1 import GetByNameRequest
        from nebius.api.nebius.compute.v1 import PlatformServiceClient

        client = PlatformServiceClient(sdk)
        try:
            platform = client.get_by_name(
                GetByNameRequest(parent_id=project_id, name=platform_name),
                **_provider_request_kwargs(),
            ).wait()
        except Exception as exc:
            self._last_error = f"compute platform lookup failed for {platform_name}: {exc}"
            return ()

        resolved = tuple(
            _ComputePlatformPreset(
                name=preset_name,
                vcpu_count=getattr(resources, "vcpu_count", None),
                memory_gibibytes=getattr(resources, "memory_gibibytes", None),
                gpu_count=getattr(resources, "gpu_count", None),
                allow_gpu_clustering=bool(getattr(preset, "allow_gpu_clustering", False)),
            )
            for preset in list(getattr(getattr(platform, "spec", None), "presets", []))
            if (preset_name := _as_str(getattr(preset, "name", None)))
            for resources in (getattr(preset, "resources", None),)
        )
        self._compute_platform_preset_cache[cache_key] = resolved
        return resolved

    def _resolve_compute_public_image_families(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        region_id = self._resolve_region_id(payload, args)
        if not region_id:
            return ()

        platform_path = _as_str(args.get("platform_path"))
        if not platform_path:
            platform_path = _component_input_sibling_path(field_path, "platform")
        platform_name = _as_str(_payload_value(payload, platform_path)) if platform_path else None
        if not platform_name:
            return ()

        ranked = self._resolve_compute_public_image_family_inventory(
            region_id=region_id,
            platform_name=platform_name,
        )
        return tuple(
            OptionChoice(
                value=item.family,
                label=(
                    f"{item.family}  ({item.human_name}, {item.compatibility})"
                    if item.human_name
                    else f"{item.family}  ({item.compatibility})"
                ),
                recommended=item.compatibility == "recommended",
            )
            for item in ranked
        )

    def _resolve_compute_public_image_family_inventory(
        self,
        *,
        region_id: str,
        platform_name: str,
    ) -> tuple[_ComputePublicImageFamily, ...]:
        cache_key = (region_id, platform_name)
        if cache_key in self._compute_public_image_family_cache:
            return self._compute_public_image_family_cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.compute.v1 import ImageServiceClient, ListPublicRequest

        client = ImageServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListPublicRequest(
                region=region_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list_public,
        )

        families: dict[str, _ComputePublicImageFamily] = {}
        for item in items:
            spec = getattr(item, "spec", None)
            family = _as_str(getattr(spec, "image_family", None))
            if not family:
                continue
            recommended_platforms: set[str] = set()
            for raw_platform_name in list(getattr(spec, "recommended_platforms", [])):
                normalized_platform_name = _as_str(raw_platform_name)
                if normalized_platform_name:
                    recommended_platforms.add(normalized_platform_name)
            unsupported_platforms = set(
                _unsupported_platform_names(getattr(spec, "unsupported_platforms", {}))
            )
            if platform_name in unsupported_platforms:
                continue
            compatibility = (
                "recommended" if platform_name in recommended_platforms else "compatible"
            )
            human_name = _as_str(getattr(spec, "image_family_human_readable", None)) or ""
            current = families.get(family)
            if current is not None and current.compatibility == "recommended":
                continue
            families[family] = _ComputePublicImageFamily(
                family=family,
                human_name=human_name,
                compatibility=compatibility,
            )

        resolved = tuple(
            sorted(
                families.values(),
                key=lambda item: (
                    0 if item.compatibility == "recommended" else 1,
                    item.family,
                ),
            )
        )
        self._compute_public_image_family_cache[cache_key] = resolved
        return resolved

    def _resolve_mk8s_compatibility_items(
        self,
        version: str,
    ) -> tuple[_Mk8sCompatibilityItem, ...]:
        if version in self._mk8s_compatibility_cache:
            return self._mk8s_compatibility_cache[version]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.mk8s.v1 import (
            GetNodeGroupCompatibilityMatrixRequest,
            NodeGroupServiceClient,
        )

        response = (
            NodeGroupServiceClient(sdk)
            .get_compatibility_matrix(
                GetNodeGroupCompatibilityMatrixRequest(cluster_kubernetes_version=version),
                **_provider_request_kwargs(),
            )
            .wait()
        )

        resolved: tuple[_Mk8sCompatibilityItem, ...] = tuple(
            _Mk8sCompatibilityItem(
                compatible_platforms=tuple(
                    platform_name
                    for platform in list(getattr(item, "compatible_platforms", []))
                    if (platform_name := _as_str(platform))
                ),
                drivers_preset=_as_str(getattr(item, "drivers_preset", None)),
                os=_as_str(getattr(item, "os", None)),
            )
            for version_item in list(getattr(response, "versions", []))
            for item in list(getattr(version_item, "items", []))
        )
        self._mk8s_compatibility_cache[version] = resolved
        return resolved

    def _resolve_project_subnets(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        network_id = _as_str(args.get("network_id"))
        network_id_path = _as_str(args.get("network_id_path"))
        if not network_id and network_id_path:
            network_id = _as_str(_payload_value(payload, network_id_path))
        cache_key = ("project_subnets", project_id, network_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.vpc.v1 import ListSubnetsRequest, SubnetServiceClient

        client = SubnetServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListSubnetsRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            item_network_id = _as_str(getattr(spec, "network_id", None))
            if network_id and item_network_id != network_id:
                continue
            status = getattr(item, "status", None)
            subnet_id = _as_str(getattr(metadata, "id", None))
            if not subnet_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            cidrs = list(getattr(status, "ipv4_private_cidrs", [])) if status is not None else []
            cidr_suffix = f" ({', '.join(str(cidr) for cidr in cidrs)})" if cidrs else ""
            label = f"{subnet_id}  ({name}){cidr_suffix}" if name else f"{subnet_id}{cidr_suffix}"
            private_pools = getattr(spec, "ipv4_private_pools", None)
            use_network_private_pools = (
                True
                if private_pools is None
                else bool(getattr(private_pools, "use_network_pools", False))
            )
            explicit_private_cidrs: tuple[str, ...] = ()
            if private_pools is not None and not use_network_private_pools:
                spec_private_cidrs = tuple(
                    cidr
                    for pool in list(getattr(private_pools, "pools", []) or [])
                    for cidr in (
                        _cidr_text(cidr_obj)
                        for cidr_obj in list(getattr(pool, "cidrs", []) or [])
                    )
                    if cidr
                )
                explicit_private_cidrs = spec_private_cidrs or tuple(
                    str(cidr).strip() for cidr in cidrs if str(cidr).strip()
                )
            options.append(
                OptionChoice(
                    value=subnet_id,
                    label=label,
                    metadata={
                        "private_cidrs": explicit_private_cidrs,
                        "use_network_private_pools": use_network_private_pools,
                    },
                )
            )

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_project_filesystems(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        cache_key = ("project_filesystems", project_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.compute.v1 import FilesystemServiceClient, ListFilesystemsRequest

        client = FilesystemServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListFilesystemsRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            status = getattr(item, "status", None)
            filesystem_id = _as_str(getattr(metadata, "id", None))
            if not filesystem_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            mount_tag = (
                _as_str(getattr(spec, "mount_tag", None))
                or _as_str(getattr(status, "mount_tag", None))
                or name
                or filesystem_id
            )
            label_parts = [filesystem_id]
            if name:
                label_parts.append(f"({name})")
            if mount_tag:
                label_parts.append(f"mount_tag={mount_tag}")
            options.append(
                OptionChoice(
                    value=filesystem_id,
                    label="  ".join(label_parts),
                    metadata={
                        "name": name or "",
                        "mount_tag": mount_tag,
                    },
                )
            )

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_project_private_allocations(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        subnet_ids = _arg_texts(args.get("subnet_ids"))
        pool_ids = _arg_texts(args.get("pool_ids"))
        cache_key = ("project_private_allocations", project_id, subnet_ids, pool_ids)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, ListAllocationsRequest

        client = AllocationServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListAllocationsRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        filter_by_resource = bool(subnet_ids or pool_ids)
        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            status = getattr(item, "status", None)
            spec_private = getattr(spec, "ipv4_private", None)
            details = getattr(status, "details", None)
            if spec_private is None and details is None:
                continue
            version = _enum_token(getattr(details, "version", None))
            if version == "IPV6":
                continue
            allocated_cidr = _ipv4_cidr_text(
                getattr(details, "allocated_cidr", None)
                or getattr(spec_private, "cidr", None)
            )
            if not allocated_cidr:
                continue
            subnet_id = (
                _as_str(getattr(details, "subnet_id", None))
                or _as_str(getattr(spec_private, "subnet_id", None))
                or ""
            )
            pool_id = (
                _as_str(getattr(details, "pool_id", None))
                or _as_str(getattr(spec_private, "pool_id", None))
                or ""
            )
            if filter_by_resource and subnet_id not in subnet_ids and pool_id not in pool_ids:
                continue
            allocation_id = _as_str(getattr(metadata, "id", None)) or allocated_cidr
            name = _as_str(getattr(metadata, "name", None))
            label_name = f"  ({name})" if name else ""
            options.append(
                OptionChoice(
                    value=allocation_id,
                    label=f"{allocation_id}{label_name} ({allocated_cidr})",
                    metadata={
                        "private_cidrs": (allocated_cidr,),
                        "subnet_id": subnet_id,
                        "pool_id": pool_id,
                    },
                )
            )

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_project_private_pools(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        cache_key = ("project_private_pools", project_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.vpc.v1 import ListPoolsRequest, PoolServiceClient

        client = PoolServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListPoolsRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            status = getattr(item, "status", None)
            pool_id = _as_str(getattr(metadata, "id", None))
            if not pool_id:
                continue
            if _enum_token(getattr(spec, "visibility", None)) != "PRIVATE":
                continue
            if _enum_token(getattr(spec, "version", None)) != "IPV4":
                continue
            assigned_networks, assigned_subnets = _pool_assignment_ids(status)
            if assigned_networks or assigned_subnets:
                continue
            name = _as_str(getattr(metadata, "name", None))
            cidrs = _pool_cidrs(spec=spec, status=status)
            if not cidrs:
                continue
            cidr_suffix = f" ({', '.join(cidrs)})" if cidrs else ""
            label = f"{pool_id}  ({name}){cidr_suffix}" if name else f"{pool_id}{cidr_suffix}"
            options.append(
                OptionChoice(
                    value=pool_id,
                    label=label,
                    metadata={
                        "name": name or "",
                        "cidrs": cidrs,
                        "source_pool_id": _as_str(getattr(spec, "source_pool_id", None)) or "",
                    },
                )
            )

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_project_networks(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        project_id = self._resolve_project_id(payload, args)
        if not project_id:
            return ()
        cache_key = ("project_networks", project_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.vpc.v1 import (
            GetPoolRequest,
            ListNetworksRequest,
            NetworkServiceClient,
            PoolServiceClient,
        )

        client = NetworkServiceClient(sdk)
        pool_client = PoolServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListNetworksRequest(
                parent_id=project_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        pool_cidr_cache: dict[str, tuple[str, ...]] = {}

        def _private_pool_cidrs(pool_id: str) -> tuple[str, ...]:
            if pool_id in pool_cidr_cache:
                return pool_cidr_cache[pool_id]
            try:
                pool = pool_client.get(
                    GetPoolRequest(id=pool_id),
                    **_provider_request_kwargs(),
                ).wait()
            except Exception:
                pool_cidr_cache[pool_id] = ()
                return ()
            spec = getattr(pool, "spec", None)
            status = getattr(pool, "status", None)
            cidrs = _pool_cidrs(spec=spec, status=status)
            pool_cidr_cache[pool_id] = cidrs
            return cidrs

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            network_id = _as_str(getattr(metadata, "id", None))
            if not network_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            pool_refs = getattr(getattr(spec, "ipv4_private_pools", None), "pools", []) or []
            private_pool_ids = tuple(
                pool_id
                for pool_ref in pool_refs
                if (
                    pool_id := _as_str(
                        getattr(pool_ref, "pool_id", None) or getattr(pool_ref, "id", None)
                    )
                )
            )
            private_cidrs = tuple(
                dict.fromkeys(
                    cidr for pool_id in private_pool_ids for cidr in _private_pool_cidrs(pool_id)
                )
            )
            label = f"{network_id}  ({name})" if name else network_id
            options.append(
                OptionChoice(
                    value=network_id,
                    label=label,
                    recommended=_is_default_project_network_name(name),
                    metadata={
                        "name": name or "",
                        "private_pool_ids": private_pool_ids,
                        "private_cidrs": private_cidrs,
                    },
                )
            )

        options.sort(key=lambda item: (0 if item.recommended else 1, item.value))
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_tenant_projects(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        tenant_id = self._resolve_tenant_id(payload, args)
        if not tenant_id:
            return ()
        cache_key = ("tenant_projects", tenant_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.iam.v1 import ListProjectsRequest, ProjectServiceClient

        client = ProjectServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListProjectsRequest(
                parent_id=tenant_id,
                page_size=_NEBIUS_LIST_PAGE_SIZE,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            project_id = _as_str(getattr(metadata, "id", None))
            if not project_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            label = f"{project_id}  ({name})" if name else project_id
            options.append(OptionChoice(value=project_id, label=label))

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _resolve_mk8s_control_plane_versions(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        cache_key = ("mk8s_control_plane_versions",)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.mk8s.v1 import (
            ClusterServiceClient,
            ListClusterControlPlaneVersionsRequest,
        )

        response = (
            ClusterServiceClient(sdk)
            .list_control_plane_versions(
                ListClusterControlPlaneVersionsRequest(),
                **_provider_request_kwargs(),
            )
            .wait()
        )
        items = list(getattr(response, "items", []))
        options: list[OptionChoice] = []
        for item in items:
            version = _as_str(getattr(item, "version", None))
            if not version:
                continue
            options.append(OptionChoice(value=version, label=version))
        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

    def _paged_list(self, *, request_factory, request_call) -> list[Any]:
        items: list[Any] = []
        page_token = ""
        request_kwargs = _provider_request_kwargs()
        while True:
            response = request_call(request_factory(page_token), **request_kwargs).wait()
            items.extend(list(getattr(response, "items", [])))
            next_page_token = _as_str(getattr(response, "next_page_token", None))
            if not next_page_token:
                return items
            page_token = next_page_token

    def _sdk_or_none(self) -> Any | None:
        if self._sdk_failed:
            return None
        if self._sdk is not None:
            return self._sdk

        sdk_config_file = os.environ.get("NEBIUS_CXCLI_PROVIDER_SDK_CONFIG_FILE", "").strip()
        sdk_profile = os.environ.get("NEBIUS_CXCLI_PROVIDER_AUTH_PROFILE", "").strip()
        sdk_endpoint = os.environ.get("NEBIUS_CXCLI_PROVIDER_AUTH_ENDPOINT", "").strip()

        try:
            self._sdk = init_nebius_sdk(
                profile=sdk_profile or None,
                endpoint=sdk_endpoint or None,
                config_file=Path(sdk_config_file) if sdk_config_file else None,
                context="provider option lookup",
                prefer_operator_auth=True,
            )
            self._last_error = None
            return self._sdk
        except Exception as exc:
            self._last_error = str(exc).strip() or exc.__class__.__name__
            self._sdk_failed = True
            return None
