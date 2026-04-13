"""Dynamic provider-backed option lookup for create wizard fields."""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from .sdk_auth import init_nebius_sdk

SUPPORTED_PROVIDER_OPTION_SOURCES = frozenset(
    {
        "mk8s_compatible_platforms",
        "mk8s_gpu_driver_presets",
        "mk8s_infiniband_fabrics",
        "compute_platforms",
        "compute_platform_presets",
        "project_subnets",
        "project_networks",
        "tenant_projects",
        "mk8s_control_plane_versions",
    }
)

ProviderOptionPlugin = Callable[..., Iterable[object] | None]
_OPTION_PLUGIN_ENV = "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS"


@dataclass(frozen=True)
class OptionChoice:
    value: str
    label: str


@dataclass(frozen=True)
class TenantProjectValidationResult:
    valid: bool
    message: str = ""
    retryable: bool = True


@dataclass(frozen=True)
class _Mk8sInfiniBandFabric:
    value: str
    platform: str
    region: str


@dataclass(frozen=True)
class _Mk8sCompatibilityItem:
    compatible_platforms: tuple[str, ...]
    drivers_preset: str | None
    os: str | None


@dataclass(frozen=True)
class _ComputePlatformPreset:
    name: str
    vcpu_count: int | None
    memory_gibibytes: int | None
    gpu_count: int | None
    allow_gpu_clustering: bool


# Keep this in the documented Nebius fabric order for stable prompt rendering.
_MK8S_INFINIBAND_FABRICS: tuple[_Mk8sInfiniBandFabric, ...] = (
    _Mk8sInfiniBandFabric(value="fabric-2", platform="gpu-h100-sxm", region="eu-north1"),
    _Mk8sInfiniBandFabric(value="fabric-3", platform="gpu-h100-sxm", region="eu-north1"),
    _Mk8sInfiniBandFabric(value="fabric-4", platform="gpu-h100-sxm", region="eu-north1"),
    _Mk8sInfiniBandFabric(value="fabric-5", platform="gpu-h200-sxm", region="eu-west1"),
    _Mk8sInfiniBandFabric(value="fabric-6", platform="gpu-h100-sxm", region="eu-north1"),
    _Mk8sInfiniBandFabric(value="fabric-7", platform="gpu-h200-sxm", region="eu-north1"),
    _Mk8sInfiniBandFabric(value="eu-north2-a", platform="gpu-h200-sxm", region="eu-north2"),
    _Mk8sInfiniBandFabric(value="me-west1-a", platform="gpu-b200-sxm-a", region="me-west1"),
    _Mk8sInfiniBandFabric(value="uk-south1-a", platform="gpu-b300-sxm", region="uk-south1"),
    _Mk8sInfiniBandFabric(value="us-central1-a", platform="gpu-h200-sxm", region="us-central1"),
    _Mk8sInfiniBandFabric(value="us-central1-b", platform="gpu-b200-sxm", region="us-central1"),
)


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
        elif isinstance(item, dict):
            raw_value = item.get("value")
            raw_label = item.get("label")
            value = str(raw_value).strip() if raw_value is not None else ""
            label = str(raw_label).strip() if raw_label is not None else value
        else:
            value = str(item).strip()
            label = value
        if not value or value in seen:
            continue
        out.append(OptionChoice(value=value, label=label or value))
        seen.add(value)
    return out


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
        except Exception:
            continue
        if not callable(resolver):
            continue
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
                "mk8s_gpu_driver_presets": self._resolve_mk8s_gpu_driver_presets,
                "mk8s_infiniband_fabrics": self._resolve_mk8s_infiniband_fabrics,
                "compute_platforms": self._resolve_compute_platforms,
                "compute_platform_presets": self._resolve_compute_platform_presets,
                "project_subnets": self._resolve_project_subnets,
                "project_networks": self._resolve_project_networks,
                "tenant_projects": self._resolve_tenant_projects,
                "mk8s_control_plane_versions": self._resolve_mk8s_control_plane_versions,
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
        if (
            not normalized_project_id
            or not normalized_platform_name
            or not normalized_preset_name
        ):
            return None

        presets = self._resolve_compute_platform_preset_inventory(
            project_id=normalized_project_id,
            platform_name=normalized_platform_name,
        )
        for preset in presets:
            if preset.name == normalized_preset_name:
                return preset.allow_gpu_clustering
        return None

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
            tenant = TenantServiceClient(sdk).get(GetTenantRequest(id=normalized_tenant_id)).wait()
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
                ProjectServiceClient(sdk).get(GetProjectRequest(id=normalized_project_id)).wait()
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
        sibling_version_path = _component_input_sibling_path(field_path, "k8s_version")
        if sibling_version_path:
            candidate_paths.append(sibling_version_path)
        sibling_override_path = _component_input_sibling_path(
            field_path,
            "mk8s_cluster_overrides.control_plane.version",
        )
        if sibling_override_path:
            candidate_paths.append(sibling_override_path)
        candidate_paths.append("infra.mk8s.cluster_overrides.control_plane.version")

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
                page_size=1000,
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

    def _resolve_mk8s_gpu_driver_presets(
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
        if not platform_path and field_path.endswith(".gpu_drivers_preset"):
            platform_path = f"{field_path.rsplit('.', maxsplit=1)[0]}.gpu_nodes_platform"

        platform_name = _as_str(args.get("platform"))
        if not platform_name and platform_path:
            platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        cache_key = ("mk8s_gpu_driver_presets", version, platform_name)
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

        resolved = tuple(options)
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
            platform_path = f"{field_path.rsplit('.', maxsplit=1)[0]}.gpu_nodes_platform"

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
        cache_key = ("mk8s_infiniband_fabrics", platform_name, region_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved = tuple(
            OptionChoice(
                value=item.value,
                label=f"{item.value}  ({item.platform}, {item.region})",
            )
            for item in _MK8S_INFINIBAND_FABRICS
            if item.platform == platform_name and (not region_id or item.region == region_id)
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
            choice
            for choice in inventory
            if not prefix or choice.value.startswith(prefix)
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
            require_gpu_clustering = bool(_as_str(_payload_value(payload, gpu_cluster_required_path)))

        cache_key = (
            "compute_platform_presets",
            project_id,
            platform_name,
            require_gpu_clustering,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        options: list[OptionChoice] = []
        for preset in self._resolve_compute_platform_preset_inventory(
            project_id=project_id,
            platform_name=platform_name,
        ):
            preset_name = preset.name
            allow_gpu_clustering = preset.allow_gpu_clustering
            if require_gpu_clustering and not allow_gpu_clustering:
                continue
            suffix_parts: list[str] = []
            if preset.vcpu_count is not None:
                suffix_parts.append(f"vCPU={preset.vcpu_count}")
            if preset.memory_gibibytes is not None:
                suffix_parts.append(f"RAM={preset.memory_gibibytes}GiB")
            if preset.gpu_count not in (None, 0):
                suffix_parts.append(f"GPU={preset.gpu_count}")
            if allow_gpu_clustering:
                suffix_parts.append("GPU cluster")
            label = f"{preset_name}  ({', '.join(suffix_parts)})" if suffix_parts else preset_name
            options.append(OptionChoice(value=preset_name, label=label))

        resolved = tuple(options)
        self._cache[cache_key] = resolved
        return resolved

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
                GetByNameRequest(parent_id=project_id, name=platform_name)
            ).wait()
        except Exception:
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
                GetNodeGroupCompatibilityMatrixRequest(cluster_kubernetes_version=version)
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
        cache_key = ("project_subnets", project_id)
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
                page_size=1000,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            status = getattr(item, "status", None)
            subnet_id = _as_str(getattr(metadata, "id", None))
            if not subnet_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            cidrs = list(getattr(status, "ipv4_private_cidrs", [])) if status is not None else []
            cidr_suffix = f" ({', '.join(str(cidr) for cidr in cidrs)})" if cidrs else ""
            label = f"{subnet_id}  ({name}){cidr_suffix}" if name else f"{subnet_id}{cidr_suffix}"
            options.append(OptionChoice(value=subnet_id, label=label))

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
        from nebius.api.nebius.vpc.v1 import ListNetworksRequest, NetworkServiceClient

        client = NetworkServiceClient(sdk)
        items = self._paged_list(
            request_factory=lambda page_token: ListNetworksRequest(
                parent_id=project_id,
                page_size=1000,
                page_token=page_token,
            ),
            request_call=client.list,
        )

        options: list[OptionChoice] = []
        for item in items:
            metadata = getattr(item, "metadata", None)
            network_id = _as_str(getattr(metadata, "id", None))
            if not network_id:
                continue
            name = _as_str(getattr(metadata, "name", None))
            label = f"{network_id}  ({name})" if name else network_id
            options.append(OptionChoice(value=network_id, label=label))

        options.sort(key=lambda item: item.value)
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
                page_size=1000,
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
            .list_control_plane_versions(ListClusterControlPlaneVersionsRequest())
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
        while True:
            response = request_call(request_factory(page_token)).wait()
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
            )
            self._last_error = None
            return self._sdk
        except Exception as exc:
            self._last_error = str(exc).strip() or exc.__class__.__name__
            self._sdk_failed = True
            return None
