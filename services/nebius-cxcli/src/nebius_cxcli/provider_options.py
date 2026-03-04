"""Dynamic provider-backed option lookup for create wizard fields."""

from __future__ import annotations

import importlib
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

SUPPORTED_PROVIDER_OPTION_SOURCES = frozenset(
    {
        "mk8s_compatible_platforms",
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
        plugins.append(resolver)
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
                current = current[part]
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


class ProviderOptionLookup:
    """Resolve dynamic field choices from Nebius APIs with in-process caching."""

    def __init__(self) -> None:
        self._sdk: object | None = None
        self._sdk_failed = False
        self._token_checked = False
        self._cache: dict[tuple[object, ...], tuple[OptionChoice, ...]] = {}

    def resolve(
        self,
        *,
        provider: str,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> list[OptionChoice]:
        try:
            resolver = {
                "mk8s_compatible_platforms": self._resolve_mk8s_compatible_platforms,
                "compute_platforms": self._resolve_compute_platforms,
                "compute_platform_presets": self._resolve_compute_platform_presets,
                "project_subnets": self._resolve_project_subnets,
                "project_networks": self._resolve_project_networks,
                "tenant_projects": self._resolve_tenant_projects,
                "mk8s_control_plane_versions": self._resolve_mk8s_control_plane_versions,
            }.get(provider)
            if provider in SUPPORTED_PROVIDER_OPTION_SOURCES and resolver is not None:
                resolved = list(resolver(args=args, payload=payload, field_path=field_path))
                if resolved:
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
                except Exception:
                    continue
                resolved = _normalize_plugin_choices(items)
                if resolved:
                    return resolved
            return []
        except Exception:
            return []

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
                    "Run `nebius iam get-access-token --format text` and verify Nebius CLI auth/profile."
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
            project = ProjectServiceClient(sdk).get(GetProjectRequest(id=normalized_project_id)).wait()
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

    def _resolve_k8s_version(self, payload: dict[str, Any], args: dict[str, Any]) -> str | None:
        version_path = (
            _as_str(args.get("kubernetes_version_path"))
            or "infra.mk8s.cluster_overrides.control_plane.version"
        )
        explicit = _as_str(_payload_value(payload, version_path))
        if explicit:
            return explicit

        configured_default = _as_str(args.get("kubernetes_version_default"))
        if configured_default:
            return configured_default

        versions = self._resolve_mk8s_control_plane_versions(args={}, payload=payload, field_path="")
        if not versions:
            return None
        return versions[0].value

    def _resolve_mk8s_compatible_platforms(
        self,
        *,
        args: dict[str, Any],
        payload: dict[str, Any],
        field_path: str,
    ) -> tuple[OptionChoice, ...]:
        version = self._resolve_k8s_version(payload, args)
        if not version:
            return ()
        prefix = _as_str(args.get("platform_prefix"))
        cache_key = ("mk8s_compatible_platforms", version, prefix)
        if cache_key in self._cache:
            return self._cache[cache_key]

        sdk = self._sdk_or_none()
        if sdk is None:
            return ()
        from nebius.api.nebius.mk8s.v1 import (
            GetNodeGroupCompatibilityMatrixRequest,
            NodeGroupServiceClient,
        )

        response = NodeGroupServiceClient(sdk).get_compatibility_matrix(
            GetNodeGroupCompatibilityMatrixRequest(cluster_kubernetes_version=version)
        ).wait()

        platforms: set[str] = set()
        for version_item in list(getattr(response, "versions", [])):
            for item in list(getattr(version_item, "items", [])):
                for platform in list(getattr(item, "compatible_platforms", [])):
                    text = _as_str(platform)
                    if not text:
                        continue
                    if prefix and not text.startswith(prefix):
                        continue
                    platforms.add(text)

        resolved = tuple(OptionChoice(value=name, label=name) for name in sorted(platforms))
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
            if prefix and not name.startswith(prefix):
                continue
            short_name = _as_str(getattr(spec, "short_human_readable_name", None))
            label = f"{name}  ({short_name})" if short_name else name
            options.append(OptionChoice(value=name, label=label))

        options.sort(key=lambda item: item.value)
        resolved = tuple(options)
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
                platform_path = f"{field_path[:-len('.preset')]}.platform"
            else:
                return ()

        platform_name = _as_str(_payload_value(payload, platform_path))
        if not platform_name:
            return ()

        cache_key = ("compute_platform_presets", project_id, platform_name)
        if cache_key in self._cache:
            return self._cache[cache_key]

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

        presets = list(getattr(getattr(platform, "spec", None), "presets", []))
        options: list[OptionChoice] = []
        for preset in presets:
            preset_name = _as_str(getattr(preset, "name", None))
            if not preset_name:
                continue
            resources = getattr(preset, "resources", None)
            cpu = getattr(resources, "vcpu_count", None) if resources is not None else None
            memory = getattr(resources, "memory_gibibytes", None) if resources is not None else None
            gpu = getattr(resources, "gpu_count", None) if resources is not None else None
            suffix_parts: list[str] = []
            if cpu is not None:
                suffix_parts.append(f"vCPU={cpu}")
            if memory is not None:
                suffix_parts.append(f"RAM={memory}GiB")
            if gpu not in (None, 0):
                suffix_parts.append(f"GPU={gpu}")
            label = (
                f"{preset_name}  ({', '.join(suffix_parts)})" if suffix_parts else preset_name
            )
            options.append(OptionChoice(value=preset_name, label=label))

        resolved = tuple(options)
        self._cache[cache_key] = resolved
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

        response = ClusterServiceClient(sdk).list_control_plane_versions(
            ListClusterControlPlaneVersionsRequest()
        ).wait()
        items = list(getattr(response, "items", []))
        versions = [
            _as_str(getattr(item, "version", None))
            for item in items
            if _as_str(getattr(item, "version", None))
        ]
        resolved = tuple(OptionChoice(value=version, label=version) for version in versions)
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

    def _sdk_or_none(self):
        if self._sdk_failed:
            return None
        if self._sdk is not None:
            return self._sdk

        self._ensure_runtime_token()
        try:
            from nebius.aio.cli_config import Config
            from nebius.sdk import SDK
        except Exception:
            self._sdk_failed = True
            return None

        config_kwargs: dict[str, object] = {}
        sdk_config_file = os.environ.get("NEBIUS_CXCLI_PROVIDER_SDK_CONFIG_FILE", "").strip()
        if sdk_config_file:
            config_kwargs["config_file"] = Path(sdk_config_file)
        sdk_profile = os.environ.get("NEBIUS_CXCLI_PROVIDER_AUTH_PROFILE", "").strip()
        if sdk_profile:
            config_kwargs["profile"] = sdk_profile
        sdk_endpoint = os.environ.get("NEBIUS_CXCLI_PROVIDER_AUTH_ENDPOINT", "").strip()
        if sdk_endpoint:
            config_kwargs["endpoint"] = sdk_endpoint

        try:
            self._sdk = SDK(config_reader=Config(**config_kwargs))
            return self._sdk
        except Exception:
            self._sdk_failed = True
            return None

    def _ensure_runtime_token(self) -> None:
        if self._token_checked:
            return
        self._token_checked = True
        if os.environ.get("NEBIUS_IAM_TOKEN", "").strip():
            return
        try:
            completed = subprocess.run(
                ["nebius", "iam", "get-access-token", "--format", "text"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return
        token = completed.stdout.strip()
        if token:
            os.environ["NEBIUS_IAM_TOKEN"] = token
