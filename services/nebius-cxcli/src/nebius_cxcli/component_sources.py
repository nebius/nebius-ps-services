"""Runtime component source registry loader and discovery helpers."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import lru_cache
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any

import yaml

from .cluster_handoffs import Handoff, resolve_builtin_handoff
from .component_instances import INSTANCE_ID_PATTERN, normalize_component_token
from .ssh_public_keys import normalize_ssh_public_key_value
from .validation_profiles import resolve_builtin_validation_profile
from .wizard_profiles import resolve_builtin_wizard_profile

DEFAULT_COMPONENT_SOURCES_FILE = (
    Path(__file__).resolve().parents[2] / "component_sources.yaml"
).resolve()
DEFAULT_COMPONENT_CLI_SETTINGS_FILE = (
    Path(__file__).resolve().parents[2] / "component_cli_settings.yaml"
).resolve()
USER_COMPONENT_SOURCES_FILE = (
    Path.home() / ".config" / "nebius-cxcli" / "component_sources.yaml"
).resolve()
GLOBAL_COMPONENT_SOURCES_FILE = Path("/etc/nebius-cxcli/component_sources.yaml")
BUNDLED_COMPONENT_SOURCES_FILENAME = "component_sources.yaml"
BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME = "component_cli_settings.yaml"
COMPONENT_SOURCES_FILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_FILE"
COMPONENT_SOURCES_PROFILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE"
DEFAULT_FLUX_VERSION = "v2.8.0"
DEFAULT_FLUX_RELEASE_TIMEOUT = "5m"
DEFAULT_TERRAFORM_VERSION = "1.15.5"
GO_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+")
GRAFANA_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h|d|w|M))+")
GRAFANA_CLI_SETTING_KEYS = frozenset(
    {
        "admin",
        "datasources",
        "explore_queries",
        "logout-timeout",
        "orgId",
        "read_token",
        "dashboard_signals",
    }
)
LINUX_USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
COMPUTE_DISK_TYPES = frozenset(
    {
        "NETWORK_SSD",
        "NETWORK_HDD",
        "NETWORK_SSD_NON_REPLICATED",
        "NETWORK_SSD_IO_M3",
    }
)

_CLI_COMPONENT_SOURCES_FILE_OVERRIDE: Path | None = None
_CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE: SourceProfile | None = None


class _MultilineYamlDumper(yaml.SafeDumper):
    pass


def _represent_multiline_yaml_str(
    dumper: yaml.SafeDumper,
    data: str,
) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_MultilineYamlDumper.add_representer(str, _represent_multiline_yaml_str)


class SourceProfile(StrEnum):
    PORTABLE = "portable"
    LOCAL = "local"


@dataclass(frozen=True)
class ComponentOutput:
    name: str
    kind: str
    source_path: str = ""
    value: Any = None
    sensitive: bool = False


@dataclass(frozen=True)
class ComponentInputBinding:
    target_path: str
    source_component_id: str
    source_output_name: str
    source_instance_id: str | None = None
    key: str | None = None
    attribute: str | None = None


@dataclass(frozen=True)
class ComponentDefault:
    target_path: str
    value: Any = None
    kind: str = "literal"
    source_path: str = ""


@dataclass(frozen=True)
class StatusWatcher:
    kind: str
    parent_input: str = "parent_id"
    name_input: str = "name"
    name_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FluxSettings:
    version: str = DEFAULT_FLUX_VERSION
    release_timeout: str = DEFAULT_FLUX_RELEASE_TIMEOUT


@dataclass(frozen=True)
class TerraformSettings:
    version: str = DEFAULT_TERRAFORM_VERSION


@dataclass(frozen=True)
class Mk8sGpuImagePreferenceSettings:
    preferred_gpu_stack_presets: tuple[str, ...] = ()
    preferred_os: tuple[str, ...] = ()


@dataclass(frozen=True)
class FluxPostRenderPatchTarget:
    group: str = ""
    version: str = ""
    kind: str = ""
    name: str = ""
    namespace: str = ""


@dataclass(frozen=True)
class FluxPostRenderPatch:
    target: FluxPostRenderPatchTarget = FluxPostRenderPatchTarget()
    patch: str = ""


@dataclass(frozen=True)
class Mk8sGpuAppDefaultSet:
    name: str
    defaults: tuple[ComponentDefault, ...] = ()


@dataclass(frozen=True)
class Mk8sGpuAppPostRenderPatchSet:
    name: str
    patches: tuple[FluxPostRenderPatch, ...] = ()


@dataclass(frozen=True)
class Mk8sGpuAppRule:
    gpu_stack_source: str = ""
    gpu_cluster_enabled: bool | None = None
    match_platforms: tuple[str, ...] = ()
    match_presets: tuple[str, ...] = ()
    auto_enable: bool = False
    defaults: tuple[ComponentDefault, ...] = ()
    defaults_from: tuple[str, ...] = ()
    post_render_patches: tuple[FluxPostRenderPatch, ...] = ()
    post_render_patches_from: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mk8sGpuOperatorReadinessSettings:
    enabled_by_default: bool = False
    timeout: str = ""


@dataclass(frozen=True)
class Mk8sGpuVisibilitySettings:
    enabled_by_default: bool = False
    namespace: str = ""
    image: str = ""
    timeout: str = ""
    cleanup: bool = True
    max_nodes: int = 3


@dataclass(frozen=True)
class Mk8sNcclSettings:
    chart_component_id: str = ""
    timeout: str = ""
    training_operator_manifest: str = ""
    training_operator_namespace: str = ""
    average_bus_bandwidth_threshold_gbps: float = 0.0
    max_nodes: int | None = None
    rdma_mpi_extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mk8sGpuHealthCheckerSettings:
    enabled_by_default: bool = False


@dataclass(frozen=True)
class Mk8sGpuDeploymentTestingSettings:
    operator_readiness: Mk8sGpuOperatorReadinessSettings = Mk8sGpuOperatorReadinessSettings()
    gpu_visibility: Mk8sGpuVisibilitySettings = Mk8sGpuVisibilitySettings()
    health_checker: Mk8sGpuHealthCheckerSettings = Mk8sGpuHealthCheckerSettings()


@dataclass(frozen=True)
class Mk8sGpuBenchmarkSettings:
    nccl: Mk8sNcclSettings = Mk8sNcclSettings()


@dataclass(frozen=True)
class Mk8sGpuSettings:
    default_stack_source: str = ""
    image_preferences: Mk8sGpuImagePreferenceSettings = Mk8sGpuImagePreferenceSettings()
    deployment_testing: Mk8sGpuDeploymentTestingSettings = Mk8sGpuDeploymentTestingSettings()
    benchmarks: Mk8sGpuBenchmarkSettings = Mk8sGpuBenchmarkSettings()


@dataclass(frozen=True)
class Mk8sGpuAppPolicy:
    role: str = ""
    default_sets: tuple[Mk8sGpuAppDefaultSet, ...] = ()
    post_render_patch_sets: tuple[Mk8sGpuAppPostRenderPatchSet, ...] = ()
    rules: tuple[Mk8sGpuAppRule, ...] = ()
    install_after: tuple[str, ...] = ()
    disable_target_validations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComputeBootDiskTypeChoice:
    value: str
    label: str = ""
    allocation_unit_gib: int = 1
    explicit_encryption_supported: bool = False


@dataclass(frozen=True)
class ComputeBootDiskRule:
    min_vcpu: int | None = None
    max_vcpu: int | None = None
    min_memory_gib: int | None = None
    max_memory_gib: int | None = None
    min_gpu: int | None = None
    max_gpu: int | None = None
    gpu_cluster_enabled: bool | None = None
    match_platforms: tuple[str, ...] = ()
    match_presets: tuple[str, ...] = ()
    size_gib: int | None = None
    type: str = ""


@dataclass(frozen=True)
class ComputeBootDiskPolicy:
    default_type: str = ""
    rules: tuple[ComputeBootDiskRule, ...] = ()


@dataclass(frozen=True)
class ComputeBootDiskSettings:
    disk_types: tuple[ComputeBootDiskTypeChoice, ...] = ()
    cpu: ComputeBootDiskPolicy = ComputeBootDiskPolicy()
    gpu: ComputeBootDiskPolicy = ComputeBootDiskPolicy()


@dataclass(frozen=True)
class ComputeSettings:
    boot_disk_defaults: ComputeBootDiskSettings = ComputeBootDiskSettings()


@dataclass(frozen=True)
class ObservabilityLogsSettings:
    enabled_by_default: bool = True
    collect_agent_logs: bool = False
    excluded_namespaces: tuple[str, ...] = ()
    systemd_units: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservabilityMetricsSettings:
    enabled_by_default: bool = True
    collect_agent_metrics: bool = False
    collect_k8s_cluster_metrics: bool = True
    excluded_namespaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservabilityTracesSettings:
    enabled_by_default: bool = True


@dataclass(frozen=True)
class ObservabilityTraceServiceValidationSettings:
    name: str = "nebius-observability-agent"
    port: int = 4317
    endpoint_slice_selector: str = "kubernetes.io/service-name=nebius-observability-agent"
    endpoint_slice_check_limit: int = 5


def _default_observability_signal_value_paths() -> dict[str, str]:
    return {
        "logs": "spec.values.config.logs.enabled",
        "metrics": "spec.values.config.metrics.enabled",
        "traces": "spec.values.config.traces.enabled",
    }


@dataclass(frozen=True)
class ObservabilityAgentValidationSettings:
    enabled: bool = True
    helmrelease_ready_condition: str = "Ready"
    signal_value_paths: dict[str, str] = field(
        default_factory=_default_observability_signal_value_paths
    )
    cluster_metric_targets_path: str = "spec.values.config.metrics.additionalTargets"
    daemonset_name: str = "o11y-agent"
    pod_selector: str = "app.kubernetes.io/instance=nebius-observability-agent"
    pod_failure_sample_limit: int = 5
    trace_otlp_service: ObservabilityTraceServiceValidationSettings = (
        ObservabilityTraceServiceValidationSettings()
    )


@dataclass(frozen=True)
class ObservabilityEndpointTemplate:
    key: str
    label: str
    template: str
    include_when: tuple[str, ...] = ()
    bucket_placeholder: str = ""


@dataclass(frozen=True)
class ObservabilityEndpointTemplates:
    read: tuple[ObservabilityEndpointTemplate, ...] = ()
    write: tuple[ObservabilityEndpointTemplate, ...] = ()


@dataclass(frozen=True)
class GlobalObservabilitySettings:
    endpoints: ObservabilityEndpointTemplates = ObservabilityEndpointTemplates()


@dataclass(frozen=True)
class ObservabilityServiceBucket:
    name: str
    label: str = ""
    include_when: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservabilityGrafanaSettings:
    chart_component_id: str = ""
    gateway_chart_component_id: str = ""
    enabled_by_default: bool = True


@dataclass(frozen=True)
class InfraObservabilitySettings:
    mode: str = ""
    chart_component_id: str = ""
    logs: ObservabilityLogsSettings = ObservabilityLogsSettings()
    metrics: ObservabilityMetricsSettings = ObservabilityMetricsSettings()
    traces: ObservabilityTracesSettings = ObservabilityTracesSettings()
    validation: ObservabilityAgentValidationSettings = ObservabilityAgentValidationSettings()
    service_metrics: tuple[ObservabilityServiceBucket, ...] = ()
    service_logs: tuple[ObservabilityServiceBucket, ...] = ()
    grafana: ObservabilityGrafanaSettings = ObservabilityGrafanaSettings()


@dataclass(frozen=True)
class ObservabilityMetricTarget:
    job_name: str = ""
    discovery: str = ""
    service_name: str = ""
    port: int | None = None
    required_gpu_node_labels: tuple[tuple[str, str], ...] = ()
    required_gpu_node_selector: tuple[tuple[str, str], ...] = ()
    required_gpu_node_label_stack_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppObservabilitySettings:
    metric_targets: tuple[ObservabilityMetricTarget, ...] = ()


@dataclass(frozen=True)
class GrafanaDashboardSignalBinding:
    signal: str
    folder: str
    dashboard: str
    gnet_id: int
    datasource: str
    read_endpoint: str = ""
    dashboard_uid: str = ""


@dataclass(frozen=True)
class GrafanaDatasourceSpec:
    key: str
    name: str
    uid: str
    datasource_type: str
    read_endpoint: str
    is_default: bool = False
    description: str = ""


@dataclass(frozen=True)
class _GrafanaDashboardSource:
    folder: str
    dashboard: str
    gnet_id: int
    datasource: str
    read_endpoint: str
    dashboard_uid: str


@dataclass(frozen=True)
class GrafanaAdminSecretSpec:
    secret_name: str = ""
    user: str = ""
    user_key: str = ""
    password_key: str = ""


@dataclass(frozen=True)
class GrafanaReadTokenSecretSpec:
    env: str = ""
    secret_name: str = ""
    key: str = ""


@dataclass(frozen=True)
class GrafanaExploreQuerySpec:
    signal: str
    query: str


@dataclass(frozen=True)
class GrafanaCliSettings:
    admin_secret: GrafanaAdminSecretSpec = GrafanaAdminSecretSpec()
    datasources: tuple[GrafanaDatasourceSpec, ...] = ()
    explore_queries: tuple[GrafanaExploreQuerySpec, ...] = ()
    logout_timeout: str = "20m"
    org_id: int = 1
    read_token: GrafanaReadTokenSecretSpec = GrafanaReadTokenSecretSpec()
    dashboard_signals: tuple[GrafanaDashboardSignalBinding, ...] = ()


@dataclass(frozen=True)
class CliSettings:
    flux: FluxSettings = FluxSettings()
    terraform: TerraformSettings = TerraformSettings()


@dataclass(frozen=True)
class ComponentCliSettingsPayload:
    cli: CliSettings = CliSettings()
    compute: ComputeSettings = ComputeSettings()
    observability: GlobalObservabilitySettings = GlobalObservabilitySettings()
    infra: dict[str, Any] = field(default_factory=dict)
    apps: dict[str, Any] = field(default_factory=dict)


def _normalize_component_output_name(value: str) -> str:
    token = str(value).strip().lower().replace("-", "_")
    token = re.sub(r"[^a-z0-9_]+", "_", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def component_output_root_name(component_id: str, output_name: str) -> str:
    normalized_component = str(component_id).strip().replace("-", "_")
    normalized_output = _normalize_component_output_name(output_name)
    return f"{normalized_component}_{normalized_output}"


def component_input_binding_ref(binding: ComponentInputBinding) -> str:
    component_selector = binding.source_component_id
    if binding.source_instance_id:
        component_selector = f"{component_selector}@{binding.source_instance_id}"
    ref = f"{component_selector}.{binding.source_output_name}"
    if binding.key:
        ref = f"{ref}.{binding.key}"
    if binding.attribute:
        ref = f"{ref}.{binding.attribute}"
    return ref


@dataclass(frozen=True)
class TFModuleSource:
    module: str
    source: str
    portable_source: str
    local_source: str | None = None
    metadata_source: str | None = None
    description: str | None = None
    version: str | None = None
    enable: bool = False
    group: str | None = None
    validation_profile: str = ""
    wizard_fields: dict[str, dict[str, Any]] | None = None
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()
    handoff: Handoff | None = None
    status: StatusWatcher | None = None
    mk8s_gpu: Mk8sGpuSettings = Mk8sGpuSettings()
    observability: InfraObservabilitySettings = InfraObservabilitySettings()


@dataclass(frozen=True)
class HelmChartLocator:
    repo: str = ""
    chart_name: str | None = None
    version: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class HelmChartUsage:
    lifecycle: str = ""
    config_ref: str = ""


@dataclass(frozen=True)
class HelmChartSource:
    name: str
    source: HelmChartLocator = HelmChartLocator()
    portable_source: HelmChartLocator = HelmChartLocator()
    local_source: HelmChartLocator = HelmChartLocator()
    namespace: str | None = None
    release_name: str | None = None
    release_timeout: str | None = None
    release_install_after: tuple[str, ...] = ()
    enable: bool = False
    selectable: bool = True
    description: str | None = None
    group: str | None = None
    wizard_fields: dict[str, dict[str, Any]] | None = None
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()
    usage: HelmChartUsage = HelmChartUsage()
    mk8s_gpu: Mk8sGpuAppPolicy = Mk8sGpuAppPolicy()
    observability: AppObservabilitySettings = AppObservabilitySettings()
    grafana: GrafanaCliSettings = GrafanaCliSettings()

    @property
    def chart_name(self) -> str | None:
        return self.source.chart_name

    @property
    def repo(self) -> str | None:
        return self.source.repo or None

    @property
    def version(self) -> str | None:
        return self.source.version

    @property
    def path(self) -> str | None:
        return self.source.path


@dataclass(frozen=True)
class ComponentSources:
    cli: CliSettings
    compute: ComputeSettings
    shared: dict[str, Any]
    tf_modules: tuple[TFModuleSource, ...]
    helm_charts: tuple[HelmChartSource, ...]
    observability: GlobalObservabilitySettings = GlobalObservabilitySettings()


def tf_module_source_by_id(
    component_id: str,
    *,
    sources: ComponentSources | None = None,
) -> TFModuleSource | None:
    resolved_sources = sources or load_component_sources()
    normalized = _as_text(component_id).lower()
    for module in resolved_sources.tf_modules:
        if _as_text(module.module).lower() == normalized:
            return module
    return None


def helm_chart_source_by_id(
    component_id: str,
    *,
    sources: ComponentSources | None = None,
) -> HelmChartSource | None:
    resolved_sources = sources or load_component_sources()
    normalized = _as_text(component_id).lower()
    for chart in resolved_sources.helm_charts:
        if _as_text(chart.name).lower() == normalized:
            return chart
    return None


def set_component_sources_file_override(path: Path | None) -> None:
    """Set process-level CLI override for component source registry location."""
    global _CLI_COMPONENT_SOURCES_FILE_OVERRIDE
    if path is None:
        _CLI_COMPONENT_SOURCES_FILE_OVERRIDE = None
        return
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"Component sources file not found: {resolved}")
    _CLI_COMPONENT_SOURCES_FILE_OVERRIDE = resolved


def get_component_sources_file_override() -> Path | None:
    """Return process-level CLI override path when set, otherwise ``None``."""
    return _CLI_COMPONENT_SOURCES_FILE_OVERRIDE


def set_component_sources_profile_override(profile: SourceProfile | None) -> None:
    """Set process-level CLI override for the active component source profile."""
    global _CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE
    _CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE = profile


def get_component_sources_profile_override() -> SourceProfile | None:
    """Return the process-level CLI source profile override when set."""
    return _CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE


def resolve_component_sources_profile(*, explicit: SourceProfile | None = None) -> SourceProfile:
    """Resolve the active component source profile.

    Precedence:
    1. explicit caller-provided value
    2. process-level CLI override (`set_component_sources_profile_override`)
    3. environment variable (`NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`)
    4. default `portable`
    """
    if explicit is not None:
        return explicit

    if _CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE is not None:
        return _CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE

    env_value = os.environ.get(COMPONENT_SOURCES_PROFILE_ENV, "").strip().lower()
    if env_value:
        try:
            return SourceProfile(env_value)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in SourceProfile)
            raise ValueError(f"{COMPONENT_SOURCES_PROFILE_ENV} must be one of: {allowed}") from exc

    return SourceProfile.PORTABLE


def resolve_component_sources_file(*, explicit: Path | None = None) -> Path:
    """Resolve component source registry path with precedence.

    Precedence:
    1. explicit CLI argument (when provided)
    2. process-level CLI override (`set_component_sources_file_override`)
    3. current working directory (`./component_sources.yaml`)
    4. environment variable (`NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`)
    5. user config (`~/.config/nebius-cxcli/component_sources.yaml`)
    6. global config (`/etc/nebius-cxcli/component_sources.yaml`)
    7. repo default `component_sources.yaml` (when present)
    """
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Component sources file not found: {resolved}")
        return resolved

    if _CLI_COMPONENT_SOURCES_FILE_OVERRIDE is not None:
        return _CLI_COMPONENT_SOURCES_FILE_OVERRIDE

    cwd_path = (Path.cwd().resolve() / BUNDLED_COMPONENT_SOURCES_FILENAME).resolve()
    if cwd_path.exists() and cwd_path.is_file():
        return cwd_path

    env_path = os.environ.get(COMPONENT_SOURCES_FILE_ENV, "").strip()
    if env_path:
        resolved = Path(env_path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"{COMPONENT_SOURCES_FILE_ENV} points to a missing file: {resolved}")
        return resolved

    if USER_COMPONENT_SOURCES_FILE.exists() and USER_COMPONENT_SOURCES_FILE.is_file():
        return USER_COMPONENT_SOURCES_FILE
    if GLOBAL_COMPONENT_SOURCES_FILE.exists() and GLOBAL_COMPONENT_SOURCES_FILE.is_file():
        return GLOBAL_COMPONENT_SOURCES_FILE
    if DEFAULT_COMPONENT_SOURCES_FILE.exists() and DEFAULT_COMPONENT_SOURCES_FILE.is_file():
        return DEFAULT_COMPONENT_SOURCES_FILE
    raise ValueError(
        "No component sources file found. "
        f"Expected one of: explicit/CLI override, {cwd_path}, ${COMPONENT_SOURCES_FILE_ENV}, "
        f"{USER_COMPONENT_SOURCES_FILE}, {GLOBAL_COMPONENT_SOURCES_FILE}, "
        f"or repo default {DEFAULT_COMPONENT_SOURCES_FILE}. "
        "A bundled package default is used automatically by load_component_sources() "
        "when no external source file is configured."
    )


def resolve_component_cli_settings_file(*, component_sources_file: Path) -> Path:
    """Resolve the CLI settings file paired with a component sources file."""
    resolved_sources = component_sources_file.expanduser().resolve()
    candidate = resolved_sources.with_name(BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME)
    if candidate.exists() and candidate.is_file():
        return candidate
    raise ValueError(
        "No component CLI settings file found. "
        f"Expected sibling {candidate} for component sources file {resolved_sources}."
    )


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _resolve_existing_local_module_source(
    source: str, *, source_root: Path | None = None
) -> str | None:
    token = str(source).strip()
    if not token or token.startswith(("git::", "http://", "https://", "oci://")):
        return None

    candidate = Path(token).expanduser()
    if candidate.is_absolute():
        if candidate.exists() and candidate.is_dir():
            return str(candidate.resolve())
        return None

    roots: list[Path] = []
    if source_root is not None:
        roots.append(source_root)
    else:
        with suppress(ValueError):
            roots.append(resolve_component_sources_file().parent)
    roots.extend(
        [
            Path.cwd(),
            Path(__file__).resolve().parents[1],
            Path(__file__).resolve().parents[2],
            Path(__file__).resolve().parents[3],
        ]
    )
    for root in roots:
        resolved = (root / token).resolve()
        if resolved.exists() and resolved.is_dir():
            return str(resolved)
    return None


def _metadata_module_source(
    *,
    portable_source: str,
    local_source: str | None,
    resolved_source: str,
    source_root: Path | None = None,
) -> str:
    resolved_local_source = _resolve_existing_local_module_source(
        str(local_source or ""),
        source_root=source_root,
    )
    if resolved_local_source:
        return resolved_local_source

    resolved_active_source = _resolve_existing_local_module_source(
        resolved_source,
        source_root=source_root,
    )
    if resolved_active_source:
        return resolved_active_source

    return portable_source or resolved_source


def _parse_shared_values(raw: Any, *, source_root: Path | None = None) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("shared must be a mapping")

    supported_shared_keys = {"admin_ssh"}
    unknown_shared = sorted(str(key) for key in raw if str(key) not in supported_shared_keys)
    if unknown_shared:
        raise ValueError("shared has unsupported field(s): " + ", ".join(unknown_shared))

    admin_ssh_raw = raw.get("admin_ssh")
    if admin_ssh_raw is None:
        return {}
    if not isinstance(admin_ssh_raw, dict):
        raise ValueError("shared.admin_ssh must be a mapping")

    supported_admin_ssh_keys = {"user_name", "public_key"}
    unknown_admin_ssh = sorted(
        str(key) for key in admin_ssh_raw if str(key) not in supported_admin_ssh_keys
    )
    if unknown_admin_ssh:
        raise ValueError(
            "shared.admin_ssh has unsupported field(s): " + ", ".join(unknown_admin_ssh)
        )

    user_name = admin_ssh_raw.get("user_name")
    public_key = admin_ssh_raw.get("public_key", "")
    if user_name is not None and not isinstance(user_name, str):
        raise ValueError("shared.admin_ssh.user_name must be a string when set")
    if (
        isinstance(user_name, str)
        and user_name.strip()
        and not LINUX_USER_NAME_RE.fullmatch(user_name)
    ):
        raise ValueError(
            "shared.admin_ssh.user_name must match Linux username format (for example ubuntu, admin_user)"
        )
    if not isinstance(public_key, str):
        raise ValueError("shared.admin_ssh.public_key must be a string")
    normalized = copy.deepcopy(dict(raw))
    admin_ssh = normalized.get("admin_ssh")
    if isinstance(admin_ssh, dict) and "public_key" in admin_ssh:
        admin_ssh["public_key"] = normalize_ssh_public_key_value(
            admin_ssh.get("public_key"),
            field_label="shared.admin_ssh.public_key",
            base_dir=source_root,
        )

    return normalized


def _parse_string_list(
    raw: Any,
    *,
    field_label: str,
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list of strings when set")
    values: list[str] = []
    for item in raw:
        token = _as_text(item)
        if not token:
            raise ValueError(f"{field_label} entries must be non-empty strings")
        values.append(token)
    return tuple(values)


def _parse_target_value_overrides(
    raw: Any,
    *,
    field_label: str,
    required_prefix: str,
) -> tuple[ComponentDefault, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    overrides: list[ComponentDefault] = []
    for target_path_raw, value_raw in raw.items():
        target_path = _as_text(target_path_raw)
        if not target_path:
            raise ValueError(f"{field_label} keys must be non-empty")
        if not target_path.startswith(required_prefix):
            raise ValueError(f"{field_label} keys must start with '{required_prefix}'")
        overrides.append(
            ComponentDefault(
                target_path=target_path,
                value=copy.deepcopy(value_raw),
            )
        )
    return tuple(overrides)


def _parse_value_overrides(
    raw: Any,
    *,
    field_label: str,
) -> tuple[ComponentDefault, ...]:
    return _parse_target_value_overrides(
        raw,
        field_label=field_label,
        required_prefix="",
    )


def _parse_flux_post_render_patch_target(
    raw: Any,
    *,
    field_label: str,
) -> FluxPostRenderPatchTarget:
    if raw is None:
        raise ValueError(f"{field_label} must be a mapping")
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"group", "version", "kind", "name", "namespace"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    kind = _as_text(raw.get("kind"))
    if not kind:
        raise ValueError(f"{field_label}.kind is required")
    return FluxPostRenderPatchTarget(
        group=_as_text(raw.get("group")),
        version=_as_text(raw.get("version")),
        kind=kind,
        name=_as_text(raw.get("name")),
        namespace=_as_text(raw.get("namespace")),
    )


def _parse_flux_post_render_patch_text(
    raw: Any,
    *,
    field_label: str,
) -> str:
    if raw is None:
        raise ValueError(f"{field_label} is required")
    if isinstance(raw, str):
        patch = raw.strip()
        if not patch:
            raise ValueError(f"{field_label} must not be empty")
        return patch
    if not isinstance(raw, (dict, list)):
        raise ValueError(f"{field_label} must be a string, mapping, or list")
    patch = yaml.dump(raw, Dumper=_MultilineYamlDumper, sort_keys=False).strip()
    if not patch:
        raise ValueError(f"{field_label} must not be empty")
    return patch


def _parse_flux_post_render_patches(
    raw: Any,
    *,
    field_label: str,
) -> tuple[FluxPostRenderPatch, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list")
    patches: list[FluxPostRenderPatch] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{field_label}[{index}] must be a mapping")
        supported_keys = {"target", "patch"}
        unknown = sorted(str(key) for key in item if str(key) not in supported_keys)
        if unknown:
            raise ValueError(
                f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown)
            )
        patches.append(
            FluxPostRenderPatch(
                target=_parse_flux_post_render_patch_target(
                    item.get("target"),
                    field_label=f"{field_label}[{index}].target",
                ),
                patch=_parse_flux_post_render_patch_text(
                    item.get("patch"),
                    field_label=f"{field_label}[{index}].patch",
                ),
            )
        )
    return tuple(patches)


def _parse_named_target_value_override_sets(
    raw: Any,
    *,
    field_label: str,
    required_prefix: str = "",
) -> tuple[Mk8sGpuAppDefaultSet, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    sets: list[Mk8sGpuAppDefaultSet] = []
    for raw_name, raw_defaults in raw.items():
        name = _as_text(raw_name)
        if not name:
            raise ValueError(f"{field_label} keys must not be empty")
        defaults = _parse_target_value_overrides(
            raw_defaults,
            field_label=f"{field_label}.{name}",
            required_prefix=required_prefix,
        )
        if not defaults:
            raise ValueError(f"{field_label}.{name} must not be empty")
        sets.append(Mk8sGpuAppDefaultSet(name=name, defaults=defaults))
    return tuple(sets)


def _parse_named_flux_post_render_patch_sets(
    raw: Any,
    *,
    field_label: str,
) -> tuple[Mk8sGpuAppPostRenderPatchSet, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    sets: list[Mk8sGpuAppPostRenderPatchSet] = []
    for raw_name, raw_patches in raw.items():
        name = _as_text(raw_name)
        if not name:
            raise ValueError(f"{field_label} keys must not be empty")
        patches = _parse_flux_post_render_patches(
            raw_patches,
            field_label=f"{field_label}.{name}",
        )
        if not patches:
            raise ValueError(f"{field_label}.{name} must not be empty")
        sets.append(Mk8sGpuAppPostRenderPatchSet(name=name, patches=patches))
    return tuple(sets)


def _render_chart_version_template(
    text: str,
    *,
    chart_version: str,
    field_label: str,
) -> str:
    token = "{chart_version}"
    if token not in text:
        return text
    if not chart_version:
        raise ValueError(f"{field_label} references {token} but source.portable.version is empty")
    return text.replace(token, chart_version)


def _render_flux_patch_chart_version_templates(
    patches: tuple[FluxPostRenderPatch, ...],
    *,
    chart_version: str,
    field_label: str,
) -> tuple[FluxPostRenderPatch, ...]:
    rendered: list[FluxPostRenderPatch] = []
    for index, patch in enumerate(patches):
        rendered.append(
            replace(
                patch,
                patch=_render_chart_version_template(
                    patch.patch,
                    chart_version=chart_version,
                    field_label=f"{field_label}[{index}].patch",
                ),
            )
        )
    return tuple(rendered)


def _render_mk8s_gpu_policy_chart_version_templates(
    policy: Mk8sGpuAppPolicy,
    *,
    chart_version: str,
    field_label: str,
) -> Mk8sGpuAppPolicy:
    post_render_patch_sets = tuple(
        replace(
            patch_set,
            patches=_render_flux_patch_chart_version_templates(
                patch_set.patches,
                chart_version=chart_version,
                field_label=f"{field_label}.post_render_patch_sets.{patch_set.name}",
            ),
        )
        for patch_set in policy.post_render_patch_sets
    )
    rules = tuple(
        replace(
            rule,
            post_render_patches=_render_flux_patch_chart_version_templates(
                rule.post_render_patches,
                chart_version=chart_version,
                field_label=f"{field_label}.rules[{index}].post_render_patches",
            ),
            post_render_patches_from=rule.post_render_patches_from,
        )
        for index, rule in enumerate(policy.rules)
    )
    return replace(
        policy,
        post_render_patch_sets=post_render_patch_sets,
        rules=rules,
    )


def _parse_portable_local_source_block(
    raw: Any,
    *,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> tuple[str, str, str | None]:
    if raw is None:
        raise ValueError(f"{field_label} must be a mapping")
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"portable", "local"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    portable_source = _as_text(raw.get("portable"))
    local_source = _as_text(raw.get("local")) or None
    resolved_source = _resolved_portable_local_source(
        field_label=field_label,
        portable_source=portable_source,
        local_source=local_source,
        source_profile=source_profile,
        source_root=source_root,
    )
    return resolved_source, portable_source, local_source


def _parse_mk8s_gpu_image_preference_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuImagePreferenceSettings:
    if raw is None:
        return Mk8sGpuImagePreferenceSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "preferred_gpu_stack_presets",
        "preferred_os",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuImagePreferenceSettings(
        preferred_gpu_stack_presets=_parse_string_list(
            raw.get("preferred_gpu_stack_presets"),
            field_label=f"{field_label}.preferred_gpu_stack_presets",
        ),
        preferred_os=_parse_string_list(
            raw.get("preferred_os"),
            field_label=f"{field_label}.preferred_os",
        ),
    )


def _parse_mk8s_gpu_stack_source(
    raw: Any,
    *,
    field_label: str,
    required: bool = False,
) -> str:
    value = _as_text(raw)
    if not value:
        if required:
            raise ValueError(f"{field_label} is required")
        return ""
    if value not in {"nebius_image", "operator_managed"}:
        raise ValueError(f"{field_label} must be 'nebius_image' or 'operator_managed'")
    return value


def _parse_optional_bool(raw: Any, *, field_label: str) -> bool | None:
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise ValueError(f"{field_label} must be a boolean when provided")
    return raw


def _parse_optional_positive_int(raw: Any, *, field_label: str) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} must be an integer >= 1 when provided") from exc
    if value < 1:
        raise ValueError(f"{field_label} must be an integer >= 1 when provided")
    return value


def _parse_optional_disk_type(raw: Any, *, field_label: str) -> str:
    value = _as_text(raw)
    if not value:
        return ""
    if value not in COMPUTE_DISK_TYPES:
        supported = ", ".join(sorted(COMPUTE_DISK_TYPES))
        raise ValueError(f"{field_label} must be one of: {supported}")
    return value


def _parse_compute_boot_disk_type_choices(
    raw: Any,
    *,
    field_label: str,
) -> tuple[ComputeBootDiskTypeChoice, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list")
    choices: list[ComputeBootDiskTypeChoice] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{field_label}[{index}] must be a mapping")
        supported_keys = {
            "value",
            "label",
            "allocation_unit_gib",
            "explicit_encryption_supported",
        }
        unknown = sorted(str(key) for key in item if str(key) not in supported_keys)
        if unknown:
            raise ValueError(
                f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown)
            )
        value = _parse_optional_disk_type(
            item.get("value"),
            field_label=f"{field_label}[{index}].value",
        )
        if not value:
            raise ValueError(f"{field_label}[{index}].value is required")
        if value in seen:
            raise ValueError(f"{field_label}[{index}].value duplicates {value}")
        seen.add(value)
        allocation_unit_gib = _parse_optional_positive_int(
            item.get("allocation_unit_gib"),
            field_label=f"{field_label}[{index}].allocation_unit_gib",
        )
        choices.append(
            ComputeBootDiskTypeChoice(
                value=value,
                label=_as_text(item.get("label")) or value,
                allocation_unit_gib=allocation_unit_gib or 1,
                explicit_encryption_supported=_parse_optional_bool(
                    item.get("explicit_encryption_supported"),
                    field_label=f"{field_label}[{index}].explicit_encryption_supported",
                )
                or False,
            )
        )
    return tuple(choices)


def _parse_compute_boot_disk_rules(
    raw: Any,
    *,
    field_label: str,
) -> tuple[ComputeBootDiskRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list")
    rules: list[ComputeBootDiskRule] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{field_label}[{index}] must be a mapping")
        supported_keys = {
            "min_vcpu",
            "max_vcpu",
            "min_memory_gib",
            "max_memory_gib",
            "min_gpu",
            "max_gpu",
            "gpu_cluster_enabled",
            "match_platforms",
            "match_presets",
            "size_gib",
            "type",
        }
        unknown = sorted(str(key) for key in item if str(key) not in supported_keys)
        if unknown:
            raise ValueError(
                f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown)
            )
        min_vcpu = _parse_optional_positive_int(
            item.get("min_vcpu"),
            field_label=f"{field_label}[{index}].min_vcpu",
        )
        max_vcpu = _parse_optional_positive_int(
            item.get("max_vcpu"),
            field_label=f"{field_label}[{index}].max_vcpu",
        )
        min_memory_gib = _parse_optional_positive_int(
            item.get("min_memory_gib"),
            field_label=f"{field_label}[{index}].min_memory_gib",
        )
        max_memory_gib = _parse_optional_positive_int(
            item.get("max_memory_gib"),
            field_label=f"{field_label}[{index}].max_memory_gib",
        )
        min_gpu = _parse_optional_positive_int(
            item.get("min_gpu"),
            field_label=f"{field_label}[{index}].min_gpu",
        )
        max_gpu = _parse_optional_positive_int(
            item.get("max_gpu"),
            field_label=f"{field_label}[{index}].max_gpu",
        )
        if min_vcpu is not None and max_vcpu is not None and min_vcpu > max_vcpu:
            raise ValueError(f"{field_label}[{index}] min_vcpu cannot exceed max_vcpu")
        if (
            min_memory_gib is not None
            and max_memory_gib is not None
            and min_memory_gib > max_memory_gib
        ):
            raise ValueError(f"{field_label}[{index}] min_memory_gib cannot exceed max_memory_gib")
        if min_gpu is not None and max_gpu is not None and min_gpu > max_gpu:
            raise ValueError(f"{field_label}[{index}] min_gpu cannot exceed max_gpu")
        size_gib = _parse_optional_positive_int(
            item.get("size_gib"),
            field_label=f"{field_label}[{index}].size_gib",
        )
        disk_type = _parse_optional_disk_type(
            item.get("type"),
            field_label=f"{field_label}[{index}].type",
        )
        if size_gib is None and not disk_type:
            raise ValueError(f"{field_label}[{index}] must set size_gib and/or type")
        rules.append(
            ComputeBootDiskRule(
                min_vcpu=min_vcpu,
                max_vcpu=max_vcpu,
                min_memory_gib=min_memory_gib,
                max_memory_gib=max_memory_gib,
                min_gpu=min_gpu,
                max_gpu=max_gpu,
                gpu_cluster_enabled=_parse_optional_bool(
                    item.get("gpu_cluster_enabled"),
                    field_label=f"{field_label}[{index}].gpu_cluster_enabled",
                ),
                match_platforms=_parse_string_list(
                    item.get("match_platforms"),
                    field_label=f"{field_label}[{index}].match_platforms",
                ),
                match_presets=_parse_string_list(
                    item.get("match_presets"),
                    field_label=f"{field_label}[{index}].match_presets",
                ),
                size_gib=size_gib,
                type=disk_type,
            )
        )
    return tuple(rules)


def _parse_compute_boot_disk_policy(
    raw: Any,
    *,
    field_label: str,
) -> ComputeBootDiskPolicy:
    if raw is None:
        return ComputeBootDiskPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"default_type", "rules"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return ComputeBootDiskPolicy(
        default_type=_parse_optional_disk_type(
            raw.get("default_type"),
            field_label=f"{field_label}.default_type",
        ),
        rules=_parse_compute_boot_disk_rules(
            raw.get("rules"),
            field_label=f"{field_label}.rules",
        ),
    )


def _parse_compute_boot_disk_settings(
    raw: Any,
    *,
    field_label: str,
) -> ComputeBootDiskSettings:
    if raw is None:
        return ComputeBootDiskSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"disk_types", "cpu", "gpu"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return ComputeBootDiskSettings(
        disk_types=_parse_compute_boot_disk_type_choices(
            raw.get("disk_types"),
            field_label=f"{field_label}.disk_types",
        ),
        cpu=_parse_compute_boot_disk_policy(
            raw.get("cpu"),
            field_label=f"{field_label}.cpu",
        ),
        gpu=_parse_compute_boot_disk_policy(
            raw.get("gpu"),
            field_label=f"{field_label}.gpu",
        ),
    )


def _parse_compute_settings(raw: Any) -> ComputeSettings:
    if raw is None:
        return ComputeSettings()
    if not isinstance(raw, dict):
        raise ValueError("compute must be a mapping")
    supported_keys = {"boot_disk_defaults"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError("compute has unsupported field(s): " + ", ".join(unknown))
    return ComputeSettings(
        boot_disk_defaults=_parse_compute_boot_disk_settings(
            raw.get("boot_disk_defaults"),
            field_label="compute.boot_disk_defaults",
        )
    )


def _parse_mk8s_gpu_app_rules(
    raw: Any,
    *,
    field_label: str,
    available_default_sets: tuple[str, ...] = (),
    available_post_render_patch_sets: tuple[str, ...] = (),
) -> tuple[Mk8sGpuAppRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list")
    rules: list[Mk8sGpuAppRule] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{field_label}[{index}] must be a mapping")
        supported_keys = {
            "gpu_stack_source",
            "gpu_cluster_enabled",
            "match_platforms",
            "match_presets",
            "auto_enable",
            "defaults",
            "defaults_from",
            "post_render_patches",
            "post_render_patches_from",
        }
        unknown = sorted(str(key) for key in item if str(key) not in supported_keys)
        if unknown:
            raise ValueError(
                f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown)
            )
        auto_enable = _parse_optional_bool(
            item.get("auto_enable"),
            field_label=f"{field_label}[{index}].auto_enable",
        )
        defaults = _parse_target_value_overrides(
            item.get("defaults"),
            field_label=f"{field_label}[{index}].defaults",
            required_prefix="values.",
        )
        defaults_from = _parse_string_list(
            item.get("defaults_from"),
            field_label=f"{field_label}[{index}].defaults_from",
        )
        unknown_default_sets = sorted(
            name for name in defaults_from if name not in available_default_sets
        )
        if unknown_default_sets:
            raise ValueError(
                f"{field_label}[{index}].defaults_from references unknown default_set(s): "
                + ", ".join(unknown_default_sets)
            )
        post_render_patches = _parse_flux_post_render_patches(
            item.get("post_render_patches"),
            field_label=f"{field_label}[{index}].post_render_patches",
        )
        post_render_patches_from = _parse_string_list(
            item.get("post_render_patches_from"),
            field_label=f"{field_label}[{index}].post_render_patches_from",
        )
        unknown_patch_sets = sorted(
            name
            for name in post_render_patches_from
            if name not in available_post_render_patch_sets
        )
        if unknown_patch_sets:
            raise ValueError(
                f"{field_label}[{index}].post_render_patches_from references unknown post_render_patch_set(s): "
                + ", ".join(unknown_patch_sets)
            )
        if (
            not auto_enable
            and not defaults
            and not defaults_from
            and not post_render_patches
            and not post_render_patches_from
        ):
            raise ValueError(
                f"{field_label}[{index}] must set auto_enable: true, defaults/defaults_from, and/or post_render_patches/post_render_patches_from"
            )
        rule = Mk8sGpuAppRule(
            gpu_stack_source=_parse_mk8s_gpu_stack_source(
                item.get("gpu_stack_source"),
                field_label=f"{field_label}[{index}].gpu_stack_source",
            ),
            gpu_cluster_enabled=_parse_optional_bool(
                item.get("gpu_cluster_enabled"),
                field_label=f"{field_label}[{index}].gpu_cluster_enabled",
            ),
            match_platforms=_parse_string_list(
                item.get("match_platforms"),
                field_label=f"{field_label}[{index}].match_platforms",
            ),
            match_presets=_parse_string_list(
                item.get("match_presets"),
                field_label=f"{field_label}[{index}].match_presets",
            ),
            auto_enable=bool(auto_enable),
            defaults=defaults,
            defaults_from=defaults_from,
            post_render_patches=post_render_patches,
            post_render_patches_from=post_render_patches_from,
        )
        rules.append(rule)
    return tuple(rules)


def _parse_mk8s_gpu_operator_readiness_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuOperatorReadinessSettings:
    if raw is None:
        return Mk8sGpuOperatorReadinessSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled_by_default", "timeout"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    timeout = _as_text(raw.get("timeout"))
    if timeout and not GO_DURATION_RE.fullmatch(timeout):
        raise ValueError(f"{field_label}.timeout must be a Go-style duration like '5m' or '45s'")
    return Mk8sGpuOperatorReadinessSettings(
        enabled_by_default=bool(raw.get("enabled_by_default", False)),
        timeout=timeout,
    )


def _parse_mk8s_gpu_visibility_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuVisibilitySettings:
    if raw is None:
        return Mk8sGpuVisibilitySettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled_by_default", "namespace", "image", "timeout", "cleanup", "max_nodes"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    timeout = _as_text(raw.get("timeout"))
    if timeout and not GO_DURATION_RE.fullmatch(timeout):
        raise ValueError(f"{field_label}.timeout must be a Go-style duration like '10m' or '45s'")
    max_nodes_raw = raw.get("max_nodes", Mk8sGpuVisibilitySettings().max_nodes)
    try:
        max_nodes = int(max_nodes_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label}.max_nodes must be an integer >= 1") from exc
    if max_nodes < 1:
        raise ValueError(f"{field_label}.max_nodes must be >= 1")
    return Mk8sGpuVisibilitySettings(
        enabled_by_default=bool(raw.get("enabled_by_default", True)),
        namespace=_as_text(raw.get("namespace")),
        image=_as_text(raw.get("image")),
        timeout=timeout,
        cleanup=bool(raw.get("cleanup", True)),
        max_nodes=max_nodes,
    )


def _parse_mk8s_nccl_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sNcclSettings:
    if raw is None:
        return Mk8sNcclSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "chart_component_id",
        "timeout",
        "training_operator_manifest",
        "training_operator_namespace",
        "average_bus_bandwidth_threshold_gbps",
        "max_nodes",
        "rdma_mpi_extra_args",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    timeout = _as_text(raw.get("timeout"))
    if timeout and not GO_DURATION_RE.fullmatch(timeout):
        raise ValueError(f"{field_label}.timeout must be a Go-style duration like '45m'")
    threshold_raw = raw.get("average_bus_bandwidth_threshold_gbps", 0)
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_label}.average_bus_bandwidth_threshold_gbps must be numeric"
        ) from exc
    if threshold < 0:
        raise ValueError(f"{field_label}.average_bus_bandwidth_threshold_gbps must be >= 0")
    max_nodes: int | None = None
    if "max_nodes" in raw and raw.get("max_nodes") is not None:
        max_nodes_raw = raw.get("max_nodes")
        try:
            max_nodes = int(max_nodes_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_label}.max_nodes must be an integer >= 1") from exc
        if max_nodes < 1:
            raise ValueError(f"{field_label}.max_nodes must be >= 1")
    if "rdma_mpi_extra_args" not in raw or raw.get("rdma_mpi_extra_args") is None:
        rdma_mpi_extra_args: tuple[str, ...] = ()
        rdma_mpi_extra_args_raw = None
    else:
        rdma_mpi_extra_args_raw = raw.get("rdma_mpi_extra_args")
    if isinstance(rdma_mpi_extra_args_raw, list):
        rdma_mpi_extra_args = tuple(_as_text(item) for item in rdma_mpi_extra_args_raw)
        if any(not item for item in rdma_mpi_extra_args):
            raise ValueError(
                f"{field_label}.rdma_mpi_extra_args must be a list of non-empty strings"
            )
    elif rdma_mpi_extra_args_raw is not None:
        raise ValueError(f"{field_label}.rdma_mpi_extra_args must be a list")
    return Mk8sNcclSettings(
        chart_component_id=_as_text(raw.get("chart_component_id")),
        timeout=timeout,
        training_operator_manifest=_as_text(raw.get("training_operator_manifest")),
        training_operator_namespace=_as_text(raw.get("training_operator_namespace")),
        average_bus_bandwidth_threshold_gbps=threshold,
        max_nodes=max_nodes,
        rdma_mpi_extra_args=rdma_mpi_extra_args,
    )


def _parse_mk8s_gpu_health_checker_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuHealthCheckerSettings:
    if raw is None:
        return Mk8sGpuHealthCheckerSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled_by_default"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuHealthCheckerSettings(
        enabled_by_default=bool(raw.get("enabled_by_default", False))
    )


def _parse_mk8s_gpu_deployment_testing_settings(
    raw: Any,
    *,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> Mk8sGpuDeploymentTestingSettings:
    if raw is None:
        return Mk8sGpuDeploymentTestingSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "operator_readiness",
        "gpu_visibility",
        "health_checker",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuDeploymentTestingSettings(
        operator_readiness=_parse_mk8s_gpu_operator_readiness_settings(
            raw.get("operator_readiness"),
            field_label=f"{field_label}.operator_readiness",
        ),
        gpu_visibility=_parse_mk8s_gpu_visibility_settings(
            raw.get("gpu_visibility"),
            field_label=f"{field_label}.gpu_visibility",
        ),
        health_checker=_parse_mk8s_gpu_health_checker_settings(
            raw.get("health_checker"),
            field_label=f"{field_label}.health_checker",
        ),
    )


def _parse_mk8s_gpu_benchmark_settings(
    raw: Any,
    *,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> Mk8sGpuBenchmarkSettings:
    if raw is None:
        return Mk8sGpuBenchmarkSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"nccl"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuBenchmarkSettings(
        nccl=_parse_mk8s_nccl_settings(
            raw.get("nccl"),
            field_label=f"{field_label}.nccl",
        )
    )


def _parse_mk8s_gpu_settings(
    raw: Any,
    *,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> Mk8sGpuSettings:
    if raw is None:
        return Mk8sGpuSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"default_stack_source", "image_preferences", "deployment_testing", "benchmarks"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuSettings(
        default_stack_source=_parse_mk8s_gpu_stack_source(
            raw.get("default_stack_source"),
            field_label=f"{field_label}.default_stack_source",
        ),
        image_preferences=_parse_mk8s_gpu_image_preference_settings(
            raw.get("image_preferences"),
            field_label=f"{field_label}.image_preferences",
        ),
        deployment_testing=_parse_mk8s_gpu_deployment_testing_settings(
            raw.get("deployment_testing"),
            field_label=f"{field_label}.deployment_testing",
            source_profile=source_profile,
            source_root=source_root,
        ),
        benchmarks=_parse_mk8s_gpu_benchmark_settings(
            raw.get("benchmarks"),
            field_label=f"{field_label}.benchmarks",
            source_profile=source_profile,
            source_root=source_root,
        ),
    )


def _parse_mk8s_gpu_app_policy(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuAppPolicy:
    if raw is None:
        return Mk8sGpuAppPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "role",
        "default_sets",
        "post_render_patch_sets",
        "rules",
        "install_after",
        "disable_target_validations",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    role = _as_text(raw.get("role"))
    if role and role not in {"gpu_operator", "network_operator", "health_checker"}:
        raise ValueError(
            f"{field_label}.role must be one of: gpu_operator, network_operator, health_checker"
        )
    default_sets = _parse_named_target_value_override_sets(
        raw.get("default_sets"),
        field_label=f"{field_label}.default_sets",
        required_prefix="values.",
    )
    post_render_patch_sets = _parse_named_flux_post_render_patch_sets(
        raw.get("post_render_patch_sets"),
        field_label=f"{field_label}.post_render_patch_sets",
    )
    disabled_target_validations = _parse_string_list(
        raw.get("disable_target_validations"),
        field_label=f"{field_label}.disable_target_validations",
    )
    supported_target_validations = {
        "operator_readiness",
        "gpu_visibility",
        "health_checker",
    }
    unknown_target_validations = sorted(
        item for item in disabled_target_validations if item not in supported_target_validations
    )
    if unknown_target_validations:
        raise ValueError(
            f"{field_label}.disable_target_validations has unsupported value(s): "
            + ", ".join(unknown_target_validations)
        )
    return Mk8sGpuAppPolicy(
        role=role,
        default_sets=default_sets,
        post_render_patch_sets=post_render_patch_sets,
        rules=_parse_mk8s_gpu_app_rules(
            raw.get("rules"),
            field_label=f"{field_label}.rules",
            available_default_sets=tuple(item.name for item in default_sets),
            available_post_render_patch_sets=tuple(item.name for item in post_render_patch_sets),
        ),
        install_after=_parse_string_list(
            raw.get("install_after"),
            field_label=f"{field_label}.install_after",
        ),
        disable_target_validations=disabled_target_validations,
    )


def _parse_observability_logs_settings(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityLogsSettings:
    if raw is None:
        return ObservabilityLogsSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "default_enabled",
        "collect_agent_logs",
        "excluded_namespaces",
        "systemd_units",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return ObservabilityLogsSettings(
        enabled_by_default=bool(raw.get("default_enabled", True)),
        collect_agent_logs=bool(raw.get("collect_agent_logs", False)),
        excluded_namespaces=_parse_string_list(
            raw.get("excluded_namespaces"),
            field_label=f"{field_label}.excluded_namespaces",
        ),
        systemd_units=_parse_string_list(
            raw.get("systemd_units"),
            field_label=f"{field_label}.systemd_units",
        ),
    )


def _parse_observability_metrics_settings(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityMetricsSettings:
    if raw is None:
        return ObservabilityMetricsSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "default_enabled",
        "collect_agent_metrics",
        "collect_k8s_cluster_metrics",
        "excluded_namespaces",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return ObservabilityMetricsSettings(
        enabled_by_default=bool(raw.get("default_enabled", True)),
        collect_agent_metrics=bool(raw.get("collect_agent_metrics", False)),
        collect_k8s_cluster_metrics=bool(raw.get("collect_k8s_cluster_metrics", True)),
        excluded_namespaces=_parse_string_list(
            raw.get("excluded_namespaces"),
            field_label=f"{field_label}.excluded_namespaces",
        ),
    )


def _parse_observability_traces_settings(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityTracesSettings:
    if raw is None:
        return ObservabilityTracesSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"default_enabled"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return ObservabilityTracesSettings(enabled_by_default=bool(raw.get("default_enabled", True)))


def _parse_observability_agent_validation_settings(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityAgentValidationSettings:
    if raw is None:
        return ObservabilityAgentValidationSettings()
    if not isinstance(raw, bool):
        raise ValueError(f"{field_label} must be a boolean")
    return ObservabilityAgentValidationSettings(enabled=raw)


def _parse_observability_endpoint_templates(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityEndpointTemplates:
    if raw is None:
        return ObservabilityEndpointTemplates()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"read", "write"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    read_raw = raw.get("read") or {}
    write_raw = raw.get("write") or {}
    if not isinstance(read_raw, dict):
        raise ValueError(f"{field_label}.read must be a mapping")
    if not isinstance(write_raw, dict):
        raise ValueError(f"{field_label}.write must be a mapping")
    return ObservabilityEndpointTemplates(
        read=_parse_observability_endpoint_group(
            read_raw,
            field_label=f"{field_label}.read",
        ),
        write=_parse_observability_endpoint_group(
            write_raw,
            field_label=f"{field_label}.write",
        ),
    )


def _parse_observability_endpoint_group(
    raw: Mapping[Any, Any],
    *,
    field_label: str,
) -> tuple[ObservabilityEndpointTemplate, ...]:
    endpoints: list[ObservabilityEndpointTemplate] = []
    seen: set[str] = set()
    for key_raw, value_raw in raw.items():
        key = _as_text(key_raw)
        item_label = f"{field_label}.{key}"
        if not key:
            raise ValueError(f"{field_label} keys must be non-empty strings")
        if key in seen:
            raise ValueError(f"{item_label} duplicates another observability endpoint")
        seen.add(key)
        if not isinstance(value_raw, dict):
            raise ValueError(f"{item_label} must be a mapping")
        supported_keys = {"label", "template", "include_when", "bucket_placeholder"}
        unknown = sorted(str(item) for item in value_raw if str(item) not in supported_keys)
        if unknown:
            raise ValueError(f"{item_label} has unsupported field(s): " + ", ".join(unknown))
        label = _as_text(value_raw.get("label"))
        template = _as_text(value_raw.get("template"))
        if not label:
            raise ValueError(f"{item_label}.label is required")
        if not template:
            raise ValueError(f"{item_label}.template is required")
        include_when = _parse_string_list(
            value_raw.get("include_when"),
            field_label=f"{item_label}.include_when",
        )
        if not include_when:
            include_when = ("always",)
        endpoints.append(
            ObservabilityEndpointTemplate(
                key=key,
                label=label,
                template=template,
                include_when=include_when,
                bucket_placeholder=_as_text(value_raw.get("bucket_placeholder")),
            )
        )
    return tuple(endpoints)


def _parse_global_observability_settings(
    raw: Any,
    *,
    field_label: str = "observability",
) -> GlobalObservabilitySettings:
    if raw is None:
        return GlobalObservabilitySettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"endpoints"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return GlobalObservabilitySettings(
        endpoints=_parse_observability_endpoint_templates(
            raw.get("endpoints"),
            field_label=f"{field_label}.endpoints",
        )
    )


def _parse_observability_service_buckets(
    raw: Any,
    *,
    field_label: str,
) -> tuple[ObservabilityServiceBucket, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"buckets"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    buckets_raw = raw.get("buckets") or {}
    if not isinstance(buckets_raw, dict):
        raise ValueError(f"{field_label}.buckets must be a mapping")
    buckets: list[ObservabilityServiceBucket] = []
    seen: set[str] = set()
    for bucket_raw, value_raw in buckets_raw.items():
        bucket = _as_text(bucket_raw)
        item_label = f"{field_label}.buckets.{bucket}"
        if not bucket:
            raise ValueError(f"{field_label}.buckets keys must be non-empty strings")
        if bucket in seen:
            raise ValueError(f"{item_label} duplicates another observability service bucket")
        seen.add(bucket)
        if not isinstance(value_raw, dict):
            raise ValueError(f"{item_label} must be a mapping")
        supported_item_keys = {"label", "include_when"}
        item_unknown = sorted(
            str(item) for item in value_raw if str(item) not in supported_item_keys
        )
        if item_unknown:
            raise ValueError(f"{item_label} has unsupported field(s): " + ", ".join(item_unknown))
        buckets.append(
            ObservabilityServiceBucket(
                name=bucket,
                label=_as_text(value_raw.get("label")),
                include_when=_parse_string_list(
                    value_raw.get("include_when"),
                    field_label=f"{item_label}.include_when",
                ),
            )
        )
    return tuple(buckets)


def _parse_observability_grafana_settings(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityGrafanaSettings:
    if raw is None:
        return ObservabilityGrafanaSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"chart_component_id", "gateway_chart_component_id", "enabled_by_default"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    chart_component_id = _as_text(raw.get("chart_component_id"))
    if not chart_component_id:
        raise ValueError(f"{field_label}.chart_component_id is required")
    return ObservabilityGrafanaSettings(
        chart_component_id=chart_component_id,
        gateway_chart_component_id=_as_text(raw.get("gateway_chart_component_id")),
        enabled_by_default=bool(raw.get("enabled_by_default", True)),
    )


def _parse_infra_observability_settings(
    raw: Any,
    *,
    module_name: str,
    field_label: str,
) -> InfraObservabilitySettings:
    if raw is None:
        return InfraObservabilitySettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "primary_agent",
        "service_metrics",
        "service_logs",
        "grafana",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    primary_agent_raw = raw.get("primary_agent")
    if primary_agent_raw is None:
        mode = ""
        chart_component_id = ""
    else:
        if not isinstance(primary_agent_raw, dict):
            raise ValueError(f"{field_label}.primary_agent must be a mapping")
        primary_supported_keys = {
            "kind",
            "chart_component_id",
            "logs",
            "metrics",
            "traces",
            "validation",
        }
        primary_unknown = sorted(
            str(key) for key in primary_agent_raw if str(key) not in primary_supported_keys
        )
        if primary_unknown:
            raise ValueError(
                f"{field_label}.primary_agent has unsupported field(s): "
                + ", ".join(primary_unknown)
            )
        mode = _as_text(primary_agent_raw.get("kind"))
        chart_component_id = _as_text(primary_agent_raw.get("chart_component_id"))
        if not mode:
            raise ValueError(f"{field_label}.primary_agent.kind is required")
    allowed_modes = {
        "mk8s": {"kubernetes_agent"},
        "vm": {"monitoring_agent"},
    }.get(module_name, set())
    if mode and mode not in allowed_modes:
        allowed = ", ".join(sorted(allowed_modes)) or "<none>"
        raise ValueError(f"{field_label}.primary_agent.kind must be one of: {allowed}")
    if mode == "kubernetes_agent" and not chart_component_id:
        raise ValueError(
            f"{field_label}.primary_agent.chart_component_id is required when primary_agent.kind=kubernetes_agent"
        )
    if mode != "kubernetes_agent" and chart_component_id:
        raise ValueError(
            f"{field_label}.primary_agent.chart_component_id is only supported when primary_agent.kind=kubernetes_agent"
        )
    allowed_signal_keys = {
        "kubernetes_agent": {"logs", "metrics", "traces", "validation"},
        "monitoring_agent": {"logs", "metrics"},
    }.get(mode, set())
    if primary_agent_raw is None:
        primary_logs_raw = None
        primary_metrics_raw = None
        primary_traces_raw = None
        primary_validation_raw = None
    else:
        signal_unknown = sorted(
            str(key)
            for key in ("logs", "metrics", "traces", "validation")
            if primary_agent_raw.get(key) is not None and key not in allowed_signal_keys
        )
        if signal_unknown:
            raise ValueError(
                f"{field_label}.primary_agent has unsupported field(s) for {mode}: "
                + ", ".join(signal_unknown)
            )
        primary_logs_raw = primary_agent_raw.get("logs")
        primary_metrics_raw = primary_agent_raw.get("metrics")
        primary_traces_raw = primary_agent_raw.get("traces")
        primary_validation_raw = primary_agent_raw.get("validation")
    grafana_settings = _parse_observability_grafana_settings(
        raw.get("grafana"),
        field_label=f"{field_label}.grafana",
    )
    if module_name != "mk8s" and grafana_settings != ObservabilityGrafanaSettings():
        raise ValueError(f"{field_label}.grafana is only supported for the mk8s component")
    return InfraObservabilitySettings(
        mode=mode,
        chart_component_id=chart_component_id,
        logs=_parse_observability_logs_settings(
            primary_logs_raw,
            field_label=f"{field_label}.primary_agent.logs",
        ),
        metrics=_parse_observability_metrics_settings(
            primary_metrics_raw,
            field_label=f"{field_label}.primary_agent.metrics",
        ),
        traces=_parse_observability_traces_settings(
            primary_traces_raw,
            field_label=f"{field_label}.primary_agent.traces",
        ),
        validation=_parse_observability_agent_validation_settings(
            primary_validation_raw if primary_agent_raw is not None else None,
            field_label=f"{field_label}.primary_agent.validation",
        ),
        service_metrics=_parse_observability_service_buckets(
            raw.get("service_metrics"),
            field_label=f"{field_label}.service_metrics",
        ),
        service_logs=_parse_observability_service_buckets(
            raw.get("service_logs"),
            field_label=f"{field_label}.service_logs",
        ),
        grafana=grafana_settings,
    )


def _parse_observability_gpu_node_labels(
    raw: Any,
    *,
    field_label: str,
) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    labels: list[tuple[str, str]] = []
    for key, value in raw.items():
        label_key = str(key).strip()
        if not label_key:
            raise ValueError(f"{field_label} keys must be non-empty strings")
        label_value = ("true" if value else "false") if isinstance(value, bool) else _as_text(value)
        if not label_value:
            raise ValueError(f"{field_label}.{label_key} must be a non-empty string")
        labels.append((label_key, label_value))
    return tuple(labels)


def _parse_observability_gpu_node_label_stack_sources(
    raw: Any,
    *,
    field_label: str,
) -> tuple[str, ...]:
    values = _parse_string_list(raw, field_label=field_label)
    return tuple(
        _parse_mk8s_gpu_stack_source(
            value,
            field_label=f"{field_label}[{index}]",
            required=True,
        )
        for index, value in enumerate(values)
    )


def _parse_observability_metric_target(
    raw: Any,
    *,
    field_label: str,
) -> ObservabilityMetricTarget:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"job_name", "discovery", "managed_gpu_node_policy"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    job_name = _as_text(raw.get("job_name"))
    if not job_name:
        raise ValueError(f"{field_label}.job_name is required")
    discovery_raw = raw.get("discovery")
    if not isinstance(discovery_raw, dict):
        raise ValueError(f"{field_label}.discovery must be a mapping")
    discovery_supported_keys = {"kind", "service_name", "port"}
    discovery_unknown = sorted(
        str(key) for key in discovery_raw if str(key) not in discovery_supported_keys
    )
    if discovery_unknown:
        raise ValueError(
            f"{field_label}.discovery has unsupported field(s): " + ", ".join(discovery_unknown)
        )
    discovery = _as_text(discovery_raw.get("kind"))
    if discovery not in {"prometheus_annotations", "additional_target"}:
        raise ValueError(
            f"{field_label}.discovery.kind must be one of: additional_target, prometheus_annotations"
        )
    service_name = _as_text(discovery_raw.get("service_name"))
    if not service_name:
        raise ValueError(f"{field_label}.discovery.service_name is required")
    raw_port = discovery_raw.get("port")
    port: int | None = None
    if raw_port is not None:
        if isinstance(raw_port, bool):
            raise ValueError(f"{field_label}.discovery.port must be an integer")
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_label}.discovery.port must be an integer") from exc
        if port <= 0 or port > 65535:
            raise ValueError(f"{field_label}.discovery.port must be between 1 and 65535")
    gpu_policy_raw = raw.get("managed_gpu_node_policy")
    if gpu_policy_raw is None:
        gpu_policy_raw = {}
    if not isinstance(gpu_policy_raw, dict):
        raise ValueError(f"{field_label}.managed_gpu_node_policy must be a mapping")
    gpu_policy_supported_keys = {"labels", "selector", "stack_sources"}
    gpu_policy_unknown = sorted(
        str(key) for key in gpu_policy_raw if str(key) not in gpu_policy_supported_keys
    )
    if gpu_policy_unknown:
        raise ValueError(
            f"{field_label}.managed_gpu_node_policy has unsupported field(s): "
            + ", ".join(gpu_policy_unknown)
        )
    return ObservabilityMetricTarget(
        job_name=job_name,
        discovery=discovery,
        service_name=service_name,
        port=port,
        required_gpu_node_labels=_parse_observability_gpu_node_labels(
            gpu_policy_raw.get("labels"),
            field_label=f"{field_label}.managed_gpu_node_policy.labels",
        ),
        required_gpu_node_selector=_parse_observability_gpu_node_labels(
            gpu_policy_raw.get("selector"),
            field_label=f"{field_label}.managed_gpu_node_policy.selector",
        ),
        required_gpu_node_label_stack_sources=_parse_observability_gpu_node_label_stack_sources(
            gpu_policy_raw.get("stack_sources"),
            field_label=f"{field_label}.managed_gpu_node_policy.stack_sources",
        ),
    )


def _parse_app_observability_settings(
    raw: Any,
    *,
    field_label: str,
) -> AppObservabilitySettings:
    if raw is None:
        return AppObservabilitySettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"metric_targets"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    metric_targets_raw = raw.get("metric_targets")
    if metric_targets_raw is None:
        metric_targets: tuple[ObservabilityMetricTarget, ...] = ()
    elif not isinstance(metric_targets_raw, list):
        raise ValueError(f"{field_label}.metric_targets must be a list")
    else:
        metric_targets = tuple(
            _parse_observability_metric_target(
                item,
                field_label=f"{field_label}.metric_targets[{index}]",
            )
            for index, item in enumerate(metric_targets_raw)
        )
    return AppObservabilitySettings(metric_targets=metric_targets)


def _parse_grafana_dashboard_signal_bindings(
    raw: Any,
    *,
    field_label: str,
) -> tuple[GrafanaDashboardSignalBinding, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    valid_signals = {"metrics", "logs", "traces"}
    unknown_signals = sorted(str(key) for key in raw if str(key) not in valid_signals)
    if unknown_signals:
        raise ValueError(f"{field_label} has unsupported signal(s): " + ", ".join(unknown_signals))
    bindings: list[GrafanaDashboardSignalBinding] = []
    for signal, item in raw.items():
        item_label = f"{field_label}.{signal}"
        ref = _as_text(item)
        if not ref or "/" not in ref:
            raise ValueError(f"{item_label} must use '<folder>/<dashboard>'")
        folder, _, dashboard = ref.partition("/")
        folder = folder.strip()
        dashboard = dashboard.strip()
        if not folder or not dashboard or "/" in dashboard:
            raise ValueError(f"{item_label} must use '<folder>/<dashboard>'")
        bindings.append(
            GrafanaDashboardSignalBinding(
                signal=str(signal),
                folder=folder,
                dashboard=dashboard,
                gnet_id=0,
                datasource="",
                read_endpoint="",
                dashboard_uid="",
            )
        )
    return tuple(bindings)


def _parse_grafana_datasources(
    raw: Any,
    *,
    field_label: str,
) -> tuple[GrafanaDatasourceSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    datasources: list[GrafanaDatasourceSpec] = []
    names: set[str] = set()
    uids: set[str] = set()
    default_count = 0
    for key, item in raw.items():
        item_label = f"{field_label}.{key}"
        if not isinstance(item, dict):
            raise ValueError(f"{item_label} must be a mapping")
        supported_keys = {"name", "uid", "type", "read_endpoint", "isDefault", "description"}
        unknown = sorted(str(value) for value in item if str(value) not in supported_keys)
        if unknown:
            raise ValueError(f"{item_label} has unsupported field(s): " + ", ".join(unknown))
        name = _as_text(item.get("name"))
        uid = _as_text(item.get("uid"))
        datasource_type = _as_text(item.get("type"))
        read_endpoint = _as_text(item.get("read_endpoint"))
        is_default = _parse_optional_bool(
            item.get("isDefault"),
            field_label=f"{item_label}.isDefault",
        )
        if not name:
            raise ValueError(f"{item_label}.name is required")
        if name in names:
            raise ValueError(f"{item_label}.name duplicates another Grafana datasource")
        names.add(name)
        if not uid:
            raise ValueError(f"{item_label}.uid is required")
        if uid in uids:
            raise ValueError(f"{item_label}.uid duplicates another Grafana datasource")
        uids.add(uid)
        if not datasource_type:
            raise ValueError(f"{item_label}.type is required")
        if not read_endpoint:
            raise ValueError(f"{item_label}.read_endpoint is required")
        if bool(is_default):
            default_count += 1
        datasources.append(
            GrafanaDatasourceSpec(
                key=str(key),
                name=name,
                uid=uid,
                datasource_type=datasource_type,
                read_endpoint=read_endpoint,
                is_default=bool(is_default),
                description=_as_text(item.get("description")),
            )
        )
    if default_count > 1:
        raise ValueError(f"{field_label} must not declare more than one default datasource")
    return tuple(datasources)


def _parse_grafana_admin_secret(
    raw: Any,
    *,
    field_label: str,
) -> GrafanaAdminSecretSpec:
    if raw is None:
        return GrafanaAdminSecretSpec()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"secret", "user", "user_key", "password_key"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    secret_name = _as_text(raw.get("secret"))
    user = _as_text(raw.get("user"))
    user_key = _as_text(raw.get("user_key"))
    password_key = _as_text(raw.get("password_key"))
    if not secret_name:
        raise ValueError(f"{field_label}.secret is required")
    if not user:
        raise ValueError(f"{field_label}.user is required")
    if not user_key:
        raise ValueError(f"{field_label}.user_key is required")
    if not password_key:
        raise ValueError(f"{field_label}.password_key is required")
    return GrafanaAdminSecretSpec(
        secret_name=secret_name,
        user=user,
        user_key=user_key,
        password_key=password_key,
    )


def _parse_grafana_read_token_secret(
    raw: Any,
    *,
    field_label: str,
) -> GrafanaReadTokenSecretSpec:
    if raw is None:
        return GrafanaReadTokenSecretSpec()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"env", "secret", "key"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    env = _as_text(raw.get("env"))
    secret_name = _as_text(raw.get("secret"))
    key = _as_text(raw.get("key"))
    if not env:
        raise ValueError(f"{field_label}.env is required")
    if not secret_name:
        raise ValueError(f"{field_label}.secret is required")
    if not key:
        raise ValueError(f"{field_label}.key is required")
    return GrafanaReadTokenSecretSpec(
        env=env,
        secret_name=secret_name,
        key=key,
    )


def _parse_grafana_explore_queries(
    raw: Any,
    *,
    field_label: str,
) -> tuple[GrafanaExploreQuerySpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    valid_signals = {"metrics", "logs", "traces"}
    unknown_signals = sorted(str(key) for key in raw if str(key) not in valid_signals)
    if unknown_signals:
        raise ValueError(f"{field_label} has unsupported signal(s): " + ", ".join(unknown_signals))
    queries: list[GrafanaExploreQuerySpec] = []
    for signal, query_raw in raw.items():
        signal_text = _as_text(signal)
        if not signal_text:
            raise ValueError(f"{field_label} keys must be non-empty")
        query = _as_text(query_raw)
        if not query:
            raise ValueError(f"{field_label}.{signal_text} must be a non-empty string")
        queries.append(GrafanaExploreQuerySpec(signal=signal_text, query=query))
    return tuple(queries)


def _parse_grafana_logout_timeout(raw: Any, *, field_label: str) -> str:
    if raw is None:
        return "20m"
    value = _as_text(raw)
    if not value:
        raise ValueError(f"{field_label} must be a non-empty Grafana duration")
    if value.lower() == "never":
        raise ValueError(
            f"{field_label} must be a Grafana duration such as 20m, 1h, or 7d; "
            "Grafana does not support a safe 'never' value for auth session expiry"
        )
    if not GRAFANA_DURATION_RE.fullmatch(value):
        raise ValueError(f"{field_label} must be a Grafana duration such as 20m, 1h, or 7d")
    return value


def _parse_grafana_cli_settings(
    raw: Any,
    *,
    field_label: str,
) -> GrafanaCliSettings:
    if raw is None:
        return GrafanaCliSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    unknown = sorted(str(key) for key in raw if str(key) not in GRAFANA_CLI_SETTING_KEYS)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    org_id = _parse_optional_positive_int(
        raw.get("orgId"),
        field_label=f"{field_label}.orgId",
    )
    return GrafanaCliSettings(
        admin_secret=_parse_grafana_admin_secret(
            raw.get("admin"),
            field_label=f"{field_label}.admin",
        ),
        datasources=_parse_grafana_datasources(
            raw.get("datasources"),
            field_label=f"{field_label}.datasources",
        ),
        explore_queries=_parse_grafana_explore_queries(
            raw.get("explore_queries"),
            field_label=f"{field_label}.explore_queries",
        ),
        logout_timeout=_parse_grafana_logout_timeout(
            raw.get("logout-timeout"),
            field_label=f"{field_label}.logout-timeout",
        ),
        org_id=org_id or 1,
        read_token=_parse_grafana_read_token_secret(
            raw.get("read_token"),
            field_label=f"{field_label}.read_token",
        ),
        dashboard_signals=_parse_grafana_dashboard_signal_bindings(
            raw.get("dashboard_signals"),
            field_label=f"{field_label}.dashboard_signals",
        ),
    )


def _grafana_dashboard_defaults(
    defaults: tuple[ComponentDefault, ...],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    dashboards: dict[tuple[str, str], Mapping[str, Any]] = {}
    for default in defaults:
        if default.kind != "literal" or not default.target_path.startswith("values.dashboards"):
            continue
        path_parts = default.target_path.split(".")
        if default.target_path == "values.dashboards" and isinstance(default.value, Mapping):
            for folder, folder_dashboards in default.value.items():
                if not isinstance(folder_dashboards, Mapping):
                    continue
                for dashboard, dashboard_spec in folder_dashboards.items():
                    if isinstance(dashboard_spec, Mapping):
                        dashboards[(str(folder), str(dashboard))] = dashboard_spec
        elif len(path_parts) == 3 and isinstance(default.value, Mapping):
            folder = path_parts[2]
            for dashboard, dashboard_spec in default.value.items():
                if isinstance(dashboard_spec, Mapping):
                    dashboards[(folder, str(dashboard))] = dashboard_spec
        elif len(path_parts) == 4 and isinstance(default.value, Mapping):
            dashboards[(path_parts[2], path_parts[3])] = default.value
    return dashboards


def _parse_dashboard_json_uid(raw_json: str, *, field_label: str) -> str:
    dashboard_json = _as_text(raw_json)
    if not dashboard_json:
        raise ValueError(f"{field_label} must be a non-empty dashboard JSON string")
    try:
        payload = json.loads(dashboard_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_label} must be valid dashboard JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field_label} must be a dashboard JSON object")
    dashboard_uid = _as_text(payload.get("uid"))
    if not dashboard_uid:
        raise ValueError(f"{field_label} must declare a top-level uid")
    return dashboard_uid


def _read_dashboard_json_file(
    json_file: str,
    *,
    source_root: Path | None,
    field_label: str,
) -> str:
    path_text = _as_text(json_file)
    if not path_text:
        raise ValueError(f"{field_label} must be a non-empty dashboard JSON file path")
    path = Path(path_text).expanduser()
    if path.is_absolute():
        candidates = (path,)
    else:
        candidates = ((source_root / path),) if source_root is not None else ()
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            dashboard_json = candidate.read_text(encoding="utf-8")
            _parse_dashboard_json_uid(dashboard_json, field_label=field_label)
            return dashboard_json

    if path.is_absolute():
        raise ValueError(f"{field_label} does not resolve to an existing dashboard JSON file")

    resource_parts = tuple(part for part in path.parts if part not in {"", "."})
    resource = importlib_resources.files("nebius_cxcli").joinpath(*resource_parts)
    try:
        dashboard_json = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(
            f"{field_label} does not resolve to an existing dashboard JSON file"
        ) from exc
    _parse_dashboard_json_uid(dashboard_json, field_label=field_label)
    return dashboard_json


def _materialize_dashboard_json_file(
    dashboard: Mapping[str, Any],
    *,
    source_root: Path | None,
    field_label: str,
) -> dict[str, Any]:
    materialized = copy.deepcopy(dict(dashboard))
    json_file = materialized.get("json_file")
    if json_file is None:
        return materialized
    if "json" in materialized:
        raise ValueError(f"{field_label} must not declare both json and json_file")
    materialized["json"] = _read_dashboard_json_file(
        _as_text(json_file),
        source_root=source_root,
        field_label=f"{field_label}.json_file",
    )
    materialized.pop("json_file", None)
    return materialized


def _materialize_grafana_dashboard_defaults(
    defaults: tuple[ComponentDefault, ...],
    *,
    source_root: Path | None,
    field_label: str,
) -> tuple[ComponentDefault, ...]:
    materialized_defaults: list[ComponentDefault] = []
    for default in defaults:
        if default.kind != "literal" or not default.target_path.startswith("values.dashboards"):
            materialized_defaults.append(default)
            continue
        value = copy.deepcopy(default.value)
        path_parts = default.target_path.split(".")
        if default.target_path == "values.dashboards" and isinstance(value, Mapping):
            next_value: dict[str, Any] = copy.deepcopy(dict(value))
            for folder, folder_dashboards in list(next_value.items()):
                if not isinstance(folder_dashboards, Mapping):
                    continue
                next_folder = copy.deepcopy(dict(folder_dashboards))
                for dashboard, dashboard_spec in list(next_folder.items()):
                    if isinstance(dashboard_spec, Mapping):
                        next_folder[dashboard] = _materialize_dashboard_json_file(
                            dashboard_spec,
                            source_root=source_root,
                            field_label=f"{field_label}.{default.target_path}.{folder}.{dashboard}",
                        )
                next_value[folder] = next_folder
            value = next_value
        elif len(path_parts) == 3 and isinstance(value, Mapping):
            next_value = copy.deepcopy(dict(value))
            for dashboard, dashboard_spec in list(next_value.items()):
                if isinstance(dashboard_spec, Mapping):
                    next_value[dashboard] = _materialize_dashboard_json_file(
                        dashboard_spec,
                        source_root=source_root,
                        field_label=f"{field_label}.{default.target_path}.{dashboard}",
                    )
            value = next_value
        elif len(path_parts) == 4 and isinstance(value, Mapping):
            folder = path_parts[2]
            dashboard = path_parts[3]
            value = _materialize_dashboard_json_file(
                value,
                source_root=source_root,
                field_label=f"{field_label}.values.dashboards.{folder}.{dashboard}",
            )
        materialized_defaults.append(
            ComponentDefault(
                target_path=default.target_path,
                value=value,
                kind=default.kind,
                source_path=default.source_path,
            )
        )
    return tuple(materialized_defaults)


def _grafana_inline_dashboard_uid(
    dashboard: Mapping[str, Any],
    *,
    field_label: str,
) -> str:
    raw_json = dashboard.get("json")
    if raw_json is None:
        return ""
    return _parse_dashboard_json_uid(_as_text(raw_json), field_label=f"{field_label}.json")


def _validate_grafana_dashboard_sources(
    *,
    component_id: str,
    defaults: tuple[ComponentDefault, ...],
    grafana: GrafanaCliSettings,
) -> dict[tuple[str, str], _GrafanaDashboardSource]:
    if not grafana.datasources and not grafana.dashboard_signals:
        return {}

    resolved: dict[tuple[str, str], _GrafanaDashboardSource] = {}
    datasources_by_name = {datasource.name: datasource for datasource in grafana.datasources}
    folder_source_modes: dict[str, str] = {}
    for (folder, dashboard_name), dashboard in _grafana_dashboard_defaults(defaults).items():
        field_label = (
            f"components.apps.{component_id}.defaults.values.dashboards.{folder}.{dashboard_name}"
        )
        gnet_id = _parse_optional_positive_int(
            dashboard.get("gnetId"),
            field_label=f"{field_label}.gnetId",
        )
        dashboard_uid = _grafana_inline_dashboard_uid(dashboard, field_label=field_label)
        if gnet_id is not None:
            if dashboard_uid:
                raise ValueError(f"{field_label} must not declare both gnetId and dashboard JSON")
            source_mode = "gnetId"
            revision = _parse_optional_positive_int(
                dashboard.get("revision"),
                field_label=f"{field_label}.revision",
            )
            if revision is None:
                raise ValueError(f"{field_label}.revision is required for gnetId dashboards")
            dashboard_uid = _as_text(dashboard.get("uid"))
            if not dashboard_uid:
                raise ValueError(
                    f"{field_label}.uid is required for gnetId dashboards so "
                    "validate-dashboards can look up the imported dashboard"
                )
        elif not dashboard_uid:
            raise ValueError(
                f"{field_label} must declare gnetId plus uid or dashboard JSON with a top-level uid"
            )
        else:
            source_mode = "json"

        previous_source_mode = folder_source_modes.setdefault(folder, source_mode)
        if previous_source_mode != source_mode:
            raise ValueError(
                f"components.apps.{component_id}.defaults.values.dashboards.{folder} "
                "must not mix Grafana.com gnetId dashboards with dashboard JSON. "
                "The Grafana Helm chart requires a provider key to use either "
                "values.dashboards imports or dashboardsConfigMaps, not both."
            )

        datasource = _as_text(dashboard.get("datasource"))
        if not datasource:
            raise ValueError(f"{field_label}.datasource is required")
        datasource_spec = datasources_by_name.get(datasource)
        if datasource_spec is None:
            raise ValueError(
                f"{field_label}.datasource references '{datasource}', but that datasource "
                "is not declared in cli.datasources"
            )
        resolved[(folder, dashboard_name)] = _GrafanaDashboardSource(
            folder=folder,
            dashboard=dashboard_name,
            gnet_id=gnet_id or 0,
            datasource=datasource,
            read_endpoint=datasource_spec.read_endpoint,
            dashboard_uid=dashboard_uid,
        )
    return resolved


def _validate_grafana_dashboard_signal_bindings(
    *,
    component_id: str,
    defaults: tuple[ComponentDefault, ...],
    grafana: GrafanaCliSettings,
) -> GrafanaCliSettings:
    resolved_bindings: list[GrafanaDashboardSignalBinding] = []
    dashboard_sources = _validate_grafana_dashboard_sources(
        component_id=component_id,
        defaults=defaults,
        grafana=grafana,
    )
    for binding in grafana.dashboard_signals:
        field_label = f"components.apps.{component_id}.cli.dashboard_signals.{binding.signal}"
        source = dashboard_sources.get((binding.folder, binding.dashboard))
        if source is None:
            raise ValueError(
                f"{field_label} references values.dashboards.{binding.folder}."
                f"{binding.dashboard}, but that dashboard is not declared in defaults"
            )
        resolved_bindings.append(
            GrafanaDashboardSignalBinding(
                signal=binding.signal,
                folder=binding.folder,
                dashboard=binding.dashboard,
                gnet_id=source.gnet_id,
                datasource=source.datasource,
                read_endpoint=source.read_endpoint,
                dashboard_uid=source.dashboard_uid,
            )
        )
    return GrafanaCliSettings(
        admin_secret=grafana.admin_secret,
        datasources=grafana.datasources,
        explore_queries=grafana.explore_queries,
        logout_timeout=grafana.logout_timeout,
        org_id=grafana.org_id,
        read_token=grafana.read_token,
        dashboard_signals=tuple(resolved_bindings),
    )


def _validate_grafana_datasource_read_endpoints(
    *,
    observability: GlobalObservabilitySettings,
    helm_charts: tuple[HelmChartSource, ...],
) -> None:
    read_endpoint_keys = {endpoint.key for endpoint in observability.endpoints.read}
    for chart in helm_charts:
        for datasource in chart.grafana.datasources:
            if datasource.read_endpoint not in read_endpoint_keys:
                raise ValueError(
                    f"components.apps.{chart.name}.cli.datasources."
                    f"{datasource.key}.read_endpoint references "
                    f"'{datasource.read_endpoint}', but that read endpoint is not "
                    "declared under observability.endpoints.read"
                )


def _parse_infra_component_cli(
    raw: Any,
    *,
    module_name: str,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> tuple[
    Mk8sGpuSettings,
    InfraObservabilitySettings,
]:
    if raw is None:
        return (
            Mk8sGpuSettings(),
            InfraObservabilitySettings(),
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")

    if module_name == "mk8s":
        supported_keys = {"gpu", "observability"}
        unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
        if unknown:
            raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
        return (
            _parse_mk8s_gpu_settings(
                raw.get("gpu"),
                field_label=f"{field_label}.gpu",
                source_profile=source_profile,
                source_root=source_root,
            ),
            _parse_infra_observability_settings(
                raw.get("observability"),
                module_name=module_name,
                field_label=f"{field_label}.observability",
            ),
        )

    if module_name == "vm":
        supported_keys = {"observability"}
        unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
        if unknown:
            raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
        return (
            Mk8sGpuSettings(),
            _parse_infra_observability_settings(
                raw.get("observability"),
                module_name=module_name,
                field_label=f"{field_label}.observability",
            ),
        )

    supported_keys = {"observability"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return (
        Mk8sGpuSettings(),
        _parse_infra_observability_settings(
            raw.get("observability"),
            module_name=module_name,
            field_label=f"{field_label}.observability",
        ),
    )


def _parse_app_component_cli(
    raw: Any,
    *,
    field_label: str,
) -> tuple[
    Mk8sGpuAppPolicy,
    AppObservabilitySettings,
    GrafanaCliSettings,
]:
    if raw is None:
        return (
            Mk8sGpuAppPolicy(),
            AppObservabilitySettings(),
            GrafanaCliSettings(),
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "mk8s_gpu_policy",
        "observability",
    } | GRAFANA_CLI_SETTING_KEYS
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    grafana_raw = {key: raw[key] for key in GRAFANA_CLI_SETTING_KEYS if key in raw}
    return (
        _parse_mk8s_gpu_app_policy(
            raw.get("mk8s_gpu_policy"),
            field_label=f"{field_label}.mk8s_gpu_policy",
        ),
        _parse_app_observability_settings(
            raw.get("observability"),
            field_label=f"{field_label}.observability",
        ),
        _parse_grafana_cli_settings(
            grafana_raw or None,
            field_label=field_label,
        ),
    )


def _parse_cli_settings(raw: Any) -> CliSettings:
    if raw is None:
        return CliSettings()
    if not isinstance(raw, dict):
        raise ValueError("cli must be a mapping")

    supported_cli_keys = {"flux", "terraform"}
    unknown_cli = sorted(str(key) for key in raw if str(key) not in supported_cli_keys)
    if unknown_cli:
        raise ValueError("cli has unsupported field(s): " + ", ".join(unknown_cli))

    flux_raw = raw.get("flux", {})
    if flux_raw is None:
        flux_raw = {}
    if not isinstance(flux_raw, dict):
        raise ValueError("cli.flux must be a mapping")

    supported_flux_keys = {"version", "release_timeout"}
    unknown_flux = sorted(str(key) for key in flux_raw if str(key) not in supported_flux_keys)
    if unknown_flux:
        raise ValueError("cli.flux has unsupported field(s): " + ", ".join(unknown_flux))

    raw_version = _as_text(flux_raw.get("version")) or DEFAULT_FLUX_VERSION
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,2}", raw_version):
        raise ValueError("cli.flux.version must be a semantic version like 'v2.8.0'")
    version = raw_version if raw_version.startswith("v") else f"v{raw_version}"
    release_timeout = _as_text(flux_raw.get("release_timeout")) or DEFAULT_FLUX_RELEASE_TIMEOUT
    if not GO_DURATION_RE.fullmatch(release_timeout):
        raise ValueError(
            "cli.flux.release_timeout must be a Go-style duration like '5m' or '12m30s'"
        )
    terraform_raw = raw.get("terraform", {})
    if terraform_raw is None:
        terraform_raw = {}
    if not isinstance(terraform_raw, dict):
        raise ValueError("cli.terraform must be a mapping")

    supported_terraform_keys = {"version"}
    unknown_terraform = sorted(
        str(key) for key in terraform_raw if str(key) not in supported_terraform_keys
    )
    if unknown_terraform:
        raise ValueError("cli.terraform has unsupported field(s): " + ", ".join(unknown_terraform))

    terraform_version = _as_text(terraform_raw.get("version")) or DEFAULT_TERRAFORM_VERSION
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", terraform_version):
        raise ValueError("cli.terraform.version must be a semantic version like '1.15.5'")

    return CliSettings(
        flux=FluxSettings(version=version, release_timeout=release_timeout),
        terraform=TerraformSettings(version=terraform_version),
    )


def _discover_terraform_outputs(module_source: str) -> tuple[ComponentOutput, ...]:
    from .runtime_introspection import module_outputs, module_source_validation_issues

    discovered = tuple(
        ComponentOutput(
            name=_normalize_component_output_name(output.name),
            kind="terraform_output",
            source_path=_as_text(output.name),
            sensitive=bool(output.sensitive),
        )
        for output in module_outputs(module_source)
        if _as_text(output.name) and _normalize_component_output_name(output.name)
    )
    if discovered:
        return discovered

    issues = module_source_validation_issues(module_source)
    if issues:
        raise ValueError(
            f"component outputs could not be discovered for module source "
            f"'{module_source}': {issues[0]}"
        )
    raise ValueError(
        f"component outputs could not be discovered for module source "
        f"'{module_source}'. Expose Terraform outputs in the module before using this component."
    )


def _discover_component_outputs(module_source: str) -> tuple[ComponentOutput, ...]:
    source = _as_text(module_source)
    if not source:
        return ()
    with suppress(ValueError):
        return _discover_terraform_outputs(source)
    return ()


def _append_component_output(
    outputs: list[ComponentOutput],
    *,
    seen_aliases: set[str],
    output: ComponentOutput,
    field_label: str,
) -> None:
    if output.name in seen_aliases:
        raise ValueError(
            f"{field_label} declares duplicate output alias '{output.name}'. "
            "Each exported output alias must be unique per component."
        )
    seen_aliases.add(output.name)
    outputs.append(output)


def _component_outputs_with_builtin_handoff(
    outputs: tuple[ComponentOutput, ...],
    *,
    field_label: str,
    handoff: Handoff | None,
) -> tuple[ComponentOutput, ...]:
    if handoff is None:
        return outputs

    if any(output.name == handoff.cluster_id_output_name for output in outputs):
        return outputs

    merged = list(outputs)
    seen_aliases = {output.name for output in outputs}
    _append_component_output(
        merged,
        seen_aliases=seen_aliases,
        output=ComponentOutput(
            name=handoff.cluster_id_output_name,
            kind="terraform_output",
            source_path=handoff.cluster_id_output_name,
        ),
        field_label=field_label,
    )
    return tuple(merged)


def _parse_component_input_bindings(raw: Any) -> tuple[ComponentInputBinding, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("input must be a mapping of target path -> component output reference")

    bindings: list[ComponentInputBinding] = []
    for target_path_raw, ref_raw in raw.items():
        target_path = _as_text(target_path_raw)
        ref = _as_text(ref_raw)
        if not target_path or not ref:
            raise ValueError(
                "input entries must use non-empty target path and component output reference"
            )
        component_selector, separator, output_token = ref.partition(".")
        component_token, instance_separator, instance_token = component_selector.partition("@")
        component_id = normalize_component_token(component_token)
        source_instance_id = (
            normalize_component_token(instance_token) if instance_separator else None
        )
        output_name = _normalize_component_output_name(output_token) if separator else ""
        if not component_id or not separator or not output_name:
            raise ValueError(
                f"input binding '{target_path}' must use '<component-id>.<output-alias>' or "
                "'<component-id>@<instance-id>.<output-alias>' reference syntax. "
                "Use 'defaults' for literal values."
            )
        if instance_separator:
            if not source_instance_id:
                raise ValueError(
                    f"input binding '{target_path}' uses empty instance selector in '{ref}'. "
                    "Use '<component-id>@<instance-id>.<output-alias>'."
                )
            if not INSTANCE_ID_PATTERN.fullmatch(source_instance_id):
                raise ValueError(
                    f"input binding '{target_path}' instance selector '{source_instance_id}' "
                    "must use lowercase letters, digits, and hyphens"
                )
        bindings.append(
            ComponentInputBinding(
                target_path=target_path,
                source_component_id=component_id,
                source_output_name=output_name,
                source_instance_id=source_instance_id,
            )
        )
    return tuple(bindings)


def _parse_component_defaults(
    raw: Any,
    *,
    field_label: str,
) -> tuple[ComponentDefault, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} defaults must be a mapping of target path -> value")

    defaults: list[ComponentDefault] = []
    for target_path_raw, value_raw in raw.items():
        target_path = _as_text(target_path_raw)
        if not target_path:
            raise ValueError(f"{field_label} defaults entries must use non-empty target paths")
        if isinstance(value_raw, str) and value_raw.strip().startswith("shared."):
            source_path = value_raw.strip()
            defaults.append(
                ComponentDefault(
                    target_path=target_path,
                    kind="shared",
                    source_path=source_path,
                )
            )
            continue
        defaults.append(
            ComponentDefault(
                target_path=target_path,
                value=copy.deepcopy(value_raw),
            )
        )
    return tuple(defaults)


def _parse_wizard_fields(
    raw: Any,
    *,
    field_label: str,
) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} wizard must be a mapping of field path -> spec mapping")

    wizard_fields: dict[str, dict[str, Any]] = {}
    for field_path_raw, spec_raw in raw.items():
        field_path = _as_text(field_path_raw)
        if not field_path:
            raise ValueError(f"{field_label} wizard entries must use non-empty field paths")
        if not isinstance(spec_raw, dict):
            raise ValueError(f"{field_label} wizard['{field_path}'] must be a mapping when set")
        spec = copy.deepcopy(dict(spec_raw))
        if "materialize_default" in spec:
            raise ValueError(
                f"{field_label} wizard['{field_path}'] uses unsupported field "
                "'materialize_default'; use 'write_default_to_config' instead"
            )
        if "write_default_to_config" in spec and not isinstance(
            spec.get("write_default_to_config"), bool
        ):
            raise ValueError(
                f"{field_label} wizard['{field_path}'] write_default_to_config "
                "must be a boolean when set"
            )
        options = spec.get("options")
        if isinstance(options, dict):
            normalized_options: dict[str, Any] = {}
            provider = _as_text(options.get("from"))
            if provider:
                normalized_options["from"] = provider
            filter_regex = _as_text(options.get("filter_regex"))
            if filter_regex:
                normalized_options["filter"] = filter_regex
            args_raw = options.get("args")
            args: dict[str, Any] = copy.deepcopy(args_raw) if isinstance(args_raw, dict) else {}
            prefix = _as_text(options.get("prefix"))
            if prefix:
                args["platform_prefix"] = prefix
            depends_on = _as_text(options.get("depends_on"))
            if depends_on:
                args["platform_path"] = depends_on
            if args:
                normalized_options["args"] = args
            auto_select_single = options.get("auto_select_single")
            if isinstance(auto_select_single, bool):
                normalized_options["auto_select_single"] = auto_select_single
            auto_select_first = options.get("auto_select_first")
            if isinstance(auto_select_first, bool):
                normalized_options["auto_select_first"] = auto_select_first
            skip_prompt_if_no_choices = options.get("skip_prompt_if_no_choices")
            if isinstance(skip_prompt_if_no_choices, bool):
                normalized_options["skip_prompt_if_no_choices"] = skip_prompt_if_no_choices
            spec["options"] = normalized_options
        wizard_fields[field_path] = spec
    return wizard_fields


def _parse_component_wizard_fields(
    *,
    component_id: str,
    raw_profile: Any,
    raw_wizard: Any,
    derived_wizard: Mapping[str, Any] | None = None,
    field_label: str,
) -> dict[str, dict[str, Any]]:
    merged_raw: dict[str, Any] = {}

    if raw_profile is not None:
        profile_name = _as_text(raw_profile)
        if not profile_name:
            raise ValueError(f"{field_label} wizard_profile must be a non-empty string when set")
        profile_fields = resolve_builtin_wizard_profile(profile_name)
        if profile_name != component_id:
            raise ValueError(
                f"{field_label} wizard_profile must match component id '{component_id}' when set"
            )
        merged_raw.update(profile_fields)

    if derived_wizard is not None:
        if not isinstance(derived_wizard, Mapping):
            raise ValueError(f"{field_label} derived wizard fields must be a mapping when set")
        for key, value in derived_wizard.items():
            merged_raw.setdefault(str(key), copy.deepcopy(value))

    if raw_wizard is not None:
        if not isinstance(raw_wizard, dict):
            raise ValueError(
                f"{field_label} wizard must be a mapping of field path -> spec mapping"
            )
        for key, value in raw_wizard.items():
            merged_raw[key] = copy.deepcopy(value)

    return _parse_wizard_fields(merged_raw or None, field_label=field_label)


def _derived_mk8s_gpu_deployment_testing_wizard_fields(
    mk8s_gpu: Mk8sGpuSettings,
) -> dict[str, dict[str, Any]]:
    deployment_testing = mk8s_gpu.deployment_testing
    return {
        "deploy.targets[].deployment_testing.mk8s_gpu.operator_readiness.enabled": {
            "default": deployment_testing.operator_readiness.enabled_by_default,
            "write_default_to_config": True,
            "required": True,
            "type_hint": "bool",
        },
        "deploy.targets[].deployment_testing.mk8s_gpu.gpu_visibility.enabled": {
            "default": deployment_testing.gpu_visibility.enabled_by_default,
            "write_default_to_config": True,
            "required": True,
            "type_hint": "bool",
        },
        "deploy.targets[].deployment_testing.mk8s_gpu.gpu_visibility.max_nodes": {
            "default": deployment_testing.gpu_visibility.max_nodes,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "deploy.targets[].deployment_testing.mk8s_gpu.health_checker.enabled": {
            "default": deployment_testing.health_checker.enabled_by_default,
            "write_default_to_config": True,
            "required": True,
            "type_hint": "bool",
        },
    }


def _derived_observability_wizard_fields(
    observability: InfraObservabilitySettings,
) -> dict[str, dict[str, Any]]:
    if observability.mode == "kubernetes_agent":
        return {
            "deploy.targets[].observability.enabled": {
                "default": False,
            },
            "deploy.targets[].observability.kubernetes.logs.enabled": {
                "default": observability.logs.enabled_by_default,
            },
            "deploy.targets[].observability.kubernetes.logs.collect_agent_logs": {
                "default": observability.logs.collect_agent_logs,
                "prompt": False,
            },
            "deploy.targets[].observability.kubernetes.logs.excluded_namespaces": {
                "default": list(observability.logs.excluded_namespaces),
                "prompt": False,
            },
            "deploy.targets[].observability.kubernetes.metrics.enabled": {
                "default": observability.metrics.enabled_by_default,
            },
            "deploy.targets[].observability.kubernetes.metrics.collect_agent_metrics": {
                "default": observability.metrics.collect_agent_metrics,
                "prompt": False,
            },
            "deploy.targets[].observability.kubernetes.metrics.collect_k8s_cluster_metrics": {
                "default": observability.metrics.collect_k8s_cluster_metrics,
            },
            "deploy.targets[].observability.kubernetes.metrics.excluded_namespaces": {
                "default": list(observability.metrics.excluded_namespaces),
                "prompt": False,
            },
            "deploy.targets[].observability.kubernetes.traces.enabled": {
                "default": observability.traces.enabled_by_default,
            },
        }
    if observability.mode == "monitoring_agent":
        return {
            "deploy.observability.enabled": {
                "default": False,
            },
            "deploy.observability.vm.logs.enabled": {
                "default": observability.logs.enabled_by_default,
            },
            "deploy.observability.vm.logs.systemd_units": {
                "default": list(observability.logs.systemd_units),
            },
        }
    return {}


def _derived_infra_component_wizard_fields(
    *,
    module_name: str,
    raw_cli: Any,
    mk8s_gpu: Mk8sGpuSettings,
    observability: InfraObservabilitySettings,
) -> dict[str, dict[str, Any]]:
    derived: dict[str, dict[str, Any]] = {}
    if (
        module_name == "mk8s"
        and isinstance(raw_cli, dict)
        and isinstance(raw_cli.get("gpu"), dict)
        and isinstance(raw_cli.get("gpu", {}).get("deployment_testing"), dict)
    ):
        derived.update(_derived_mk8s_gpu_deployment_testing_wizard_fields(mk8s_gpu))
    if (
        module_name in {"mk8s", "vm"}
        and isinstance(raw_cli, dict)
        and isinstance(raw_cli.get("observability"), dict)
    ):
        derived.update(_derived_observability_wizard_fields(observability))
    return derived


def _parse_status_watcher(raw: Any) -> StatusWatcher | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("status must be a mapping")

    supported_status_keys = {"kind", "parent_input", "name_input", "name_inputs"}
    unknown_status_keys = sorted(str(key) for key in raw if str(key) not in supported_status_keys)
    if unknown_status_keys:
        raise ValueError("status has unsupported field(s): " + ", ".join(unknown_status_keys))

    kind = _as_text(raw.get("kind")).strip().lower()
    if not kind:
        raise ValueError("status.kind is required")

    parent_input = _as_text(raw.get("parent_input")) or "parent_id"
    name_input = _as_text(raw.get("name_input")) or "name"
    name_inputs = _parse_string_list(raw.get("name_inputs"), field_label="status.name_inputs")
    if not parent_input:
        raise ValueError("status.parent_input cannot be empty")
    if not name_input:
        raise ValueError("status.name_input cannot be empty")
    return StatusWatcher(
        kind=kind,
        parent_input=parent_input,
        name_input=name_input,
        name_inputs=name_inputs,
    )


def _resolved_module_source(
    *,
    module_name: str,
    portable_source: str,
    local_source: str | None,
    source_profile: SourceProfile,
) -> str:
    if not portable_source:
        raise ValueError(f"components.infra.{module_name} source.portable is required")
    if source_profile == SourceProfile.LOCAL and str(local_source or "").strip():
        return str(local_source).strip()
    return portable_source


def _resolved_portable_local_source(
    *,
    field_label: str,
    portable_source: str,
    local_source: str | None,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> str:
    if not portable_source:
        raise ValueError(f"{field_label}.portable is required")
    if source_profile == SourceProfile.LOCAL:
        resolved_local = _resolve_existing_local_module_source(
            str(local_source or ""),
            source_root=source_root,
        )
        if resolved_local:
            return resolved_local
        if str(local_source or "").strip():
            return str(local_source).strip()
    return portable_source


def _compose_chart_source_ref(*, repo: str, chart_name: str, path: str = "") -> str:
    if str(path).strip():
        return str(path).strip()
    normalized_repo = str(repo).strip().rstrip("/")
    normalized_chart = str(chart_name).strip().strip("/")
    if not normalized_repo:
        return normalized_chart
    if normalized_repo.startswith("oci://") and normalized_chart:
        repo_tail = normalized_repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail == normalized_chart.lower():
            return normalized_repo
    if not normalized_chart:
        return normalized_repo
    return f"{normalized_repo}/{normalized_chart}"


def _parse_helm_chart_locator(
    raw: Any,
    *,
    field_label: str,
    default_chart_name: str,
    allow_path: bool,
    source_root: Path | None = None,
) -> HelmChartLocator:
    if raw is None:
        return HelmChartLocator()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = (
        {"repo", "chart", "version"} if not allow_path else {"path", "chart", "version"}
    )
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))

    path = ""
    if allow_path:
        path = _as_text(raw.get("path"))
        if path:
            resolved_path = _resolve_existing_local_module_source(path, source_root=source_root)
            path = resolved_path or path

    repo = _as_text(raw.get("repo")).rstrip("/")
    raw_chart_name = _as_text(raw.get("chart"))
    version = _as_text(raw.get("version")) or None
    if not path and not repo and not raw_chart_name and not version:
        return HelmChartLocator()
    if path:
        metadata_chart_name, metadata_version = _local_helm_chart_metadata(path)
        chart_name = raw_chart_name or metadata_chart_name or default_chart_name
        version = version or metadata_version or None
        return HelmChartLocator(chart_name=chart_name, version=version, path=path)
    if allow_path:
        raise ValueError(f"{field_label}.path is required")
    if not repo:
        raise ValueError(f"{field_label}.repo is required")
    chart_name = raw_chart_name or default_chart_name
    return HelmChartLocator(repo=repo, chart_name=chart_name, version=version)


def _local_helm_chart_metadata(path: str) -> tuple[str, str]:
    chart_yaml = Path(path).expanduser() / "Chart.yaml"
    if not chart_yaml.exists() or not chart_yaml.is_file():
        return "", ""
    with suppress(Exception):
        payload = yaml.safe_load(chart_yaml.read_text(encoding="utf-8")) or {}
        if isinstance(payload, Mapping):
            return _as_text(payload.get("name")), _as_text(payload.get("version"))
    return "", ""


def _parse_helm_chart_source_block(
    raw: Any,
    *,
    field_label: str,
    default_chart_name: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> tuple[HelmChartLocator, HelmChartLocator, HelmChartLocator]:
    if raw is None:
        raise ValueError(f"{field_label} must be a mapping")
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"portable", "local"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))

    portable_raw = raw.get("portable")
    local_raw = raw.get("local")
    portable = _parse_helm_chart_locator(
        portable_raw,
        field_label=f"{field_label}.portable",
        default_chart_name=default_chart_name,
        allow_path=False,
        source_root=source_root,
    )
    local = _parse_helm_chart_locator(
        local_raw,
        field_label=f"{field_label}.local",
        default_chart_name=default_chart_name,
        allow_path=True,
        source_root=source_root,
    )
    if not portable.repo and not str(local.path or "").strip():
        raise ValueError(
            f"{field_label} must define at least one of source.portable or source.local.path"
        )
    if source_profile == SourceProfile.LOCAL and str(local.path or "").strip():
        return local, portable, local
    if portable.repo and portable.chart_name:
        return portable, portable, local
    return portable, portable, local


def _parse_ui_block(
    raw: Any,
    *,
    field_label: str,
) -> tuple[str | None, str | None, bool, bool]:
    if raw is None:
        return None, None, False, True
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} ui must be a mapping")

    supported_ui_keys = {"title", "group", "enabled", "selectable"}
    unknown_ui_keys = sorted(str(key) for key in raw if str(key) not in supported_ui_keys)
    if unknown_ui_keys:
        raise ValueError(
            f"{field_label} ui has unsupported field(s): " + ", ".join(unknown_ui_keys)
        )

    title = _as_text(raw.get("title")) or None
    group = _as_text(raw.get("group")) or None
    enabled = bool(raw.get("enabled", False))
    selectable = bool(raw.get("selectable", True))
    return title, group, enabled, selectable


def _parse_helm_chart_usage(raw: Any, *, field_label: str) -> HelmChartUsage:
    if raw is None:
        return HelmChartUsage()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label}.usage must be a mapping")

    supported_usage_keys = {"lifecycle", "config"}
    unknown_usage_keys = sorted(str(key) for key in raw if str(key) not in supported_usage_keys)
    if unknown_usage_keys:
        raise ValueError(
            f"{field_label}.usage has unsupported field(s): "
            + ", ".join(unknown_usage_keys)
        )

    lifecycle = _as_text(raw.get("lifecycle"))
    if lifecycle and lifecycle != "transient":
        raise ValueError(f"{field_label}.usage.lifecycle must be 'transient' when set")

    config_ref = ""
    raw_config = raw.get("config")
    if raw_config is not None:
        if not isinstance(raw_config, dict):
            raise ValueError(f"{field_label}.usage.config must be a mapping")
        supported_config_keys = {"ref"}
        unknown_config_keys = sorted(
            str(key) for key in raw_config if str(key) not in supported_config_keys
        )
        if unknown_config_keys:
            raise ValueError(
                f"{field_label}.usage.config has unsupported field(s): "
                + ", ".join(unknown_config_keys)
            )
        config_ref = _as_text(raw_config.get("ref"))
        if not lifecycle:
            raise ValueError(
                f"{field_label}.usage.lifecycle is required when usage.config is set"
            )

    return HelmChartUsage(lifecycle=lifecycle, config_ref=config_ref)


def _parse_component_cli_settings_payload(payload: Any) -> ComponentCliSettingsPayload:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("component_cli_settings root must be a mapping")
    supported_root_keys = {"cli", "compute", "observability", "components"}
    unknown_root = sorted(str(key) for key in payload if str(key) not in supported_root_keys)
    if unknown_root:
        raise ValueError(
            "component_cli_settings root has unsupported field(s): " + ", ".join(unknown_root)
        )

    components = payload.get("components", {}) or {}
    if not isinstance(components, dict):
        raise ValueError("component_cli_settings.components must be a mapping")
    supported_component_scopes = {"infra", "apps"}
    unknown_components = sorted(
        str(key) for key in components if str(key) not in supported_component_scopes
    )
    if unknown_components:
        raise ValueError(
            "component_cli_settings.components has unsupported field(s): "
            + ", ".join(unknown_components)
        )

    component_cli: dict[str, dict[str, Any]] = {"infra": {}, "apps": {}}
    for scope in ("infra", "apps"):
        scope_raw = components.get(scope, {}) or {}
        if not isinstance(scope_raw, dict):
            raise ValueError(f"component_cli_settings.components.{scope} must be a mapping")
        for component_id_raw, component_raw in scope_raw.items():
            component_id = _as_text(component_id_raw)
            if scope == "infra":
                component_id = component_id.lower()
            if not component_id:
                raise ValueError(
                    f"component_cli_settings.components.{scope} keys must not be empty"
                )
            if not isinstance(component_raw, dict):
                raise ValueError(
                    f"component_cli_settings.components.{scope}.{component_id} must be a mapping"
                )
            supported_component_keys = {"cli"}
            unknown_component_keys = sorted(
                str(key) for key in component_raw if str(key) not in supported_component_keys
            )
            if unknown_component_keys:
                raise ValueError(
                    f"component_cli_settings.components.{scope}.{component_id} "
                    "has unsupported field(s): " + ", ".join(unknown_component_keys)
                )
            component_cli[scope][component_id] = component_raw.get("cli")

    return ComponentCliSettingsPayload(
        cli=_parse_cli_settings(payload.get("cli")),
        compute=_parse_compute_settings(payload.get("compute")),
        observability=_parse_global_observability_settings(payload.get("observability")),
        infra=component_cli["infra"],
        apps=component_cli["apps"],
    )


def _parse_sources_payload(
    payload: Any,
    *,
    cli_settings_payload: Any,
    source_profile: SourceProfile,
    source_root: Path | None = None,
    cli_source_root: Path | None = None,
) -> ComponentSources:
    if not isinstance(payload, dict):
        raise ValueError("component_sources root must be a mapping")
    supported_root_keys = {"shared", "components"}
    unknown_root = sorted(str(key) for key in payload if str(key) not in supported_root_keys)
    if unknown_root:
        raise ValueError(
            "component_sources root has unsupported field(s): " + ", ".join(unknown_root)
        )

    cli_settings = _parse_component_cli_settings_payload(cli_settings_payload)
    cli = cli_settings.cli
    shared = _parse_shared_values(payload.get("shared"), source_root=source_root)
    global_observability = cli_settings.observability
    components = payload.get("components", {})
    if components is None:
        components = {}
    if not isinstance(components, dict):
        raise ValueError("components must be a mapping")
    supported_component_scopes = {"infra", "apps"}
    unknown_components = sorted(
        str(key) for key in components if str(key) not in supported_component_scopes
    )
    if unknown_components:
        raise ValueError("components has unsupported field(s): " + ", ".join(unknown_components))

    infra = components.get("infra", {})
    apps = components.get("apps", {})
    if infra is None:
        infra = {}
    if apps is None:
        apps = {}
    if not isinstance(infra, dict):
        raise ValueError("components.infra must be a mapping of component id -> component config")
    if not isinstance(apps, dict):
        raise ValueError("components.apps must be a mapping of component id -> chart config")
    unknown_infra_cli = sorted(set(cli_settings.infra) - {_as_text(key).lower() for key in infra})
    if unknown_infra_cli:
        raise ValueError(
            "component_cli_settings.components.infra references unknown component(s): "
            + ", ".join(unknown_infra_cli)
        )
    unknown_app_cli = sorted(set(cli_settings.apps) - {_as_text(key) for key in apps})
    if unknown_app_cli:
        raise ValueError(
            "component_cli_settings.components.apps references unknown component(s): "
            + ", ".join(unknown_app_cli)
        )

    tf_modules: list[TFModuleSource] = []
    for module_name_raw, raw in infra.items():
        module_name = _as_text(module_name_raw).lower()
        if not module_name:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"components.infra.{module_name} must be a mapping")
        supported_module_keys = {
            "source",
            "ui",
            "status",
            "defaults",
            "wizard_profile",
            "wizard",
            "input",
        }
        unknown_module_keys = sorted(
            str(key) for key in raw if str(key) not in supported_module_keys
        )
        if unknown_module_keys:
            raise ValueError(
                f"components.infra.{module_name} has unsupported field(s): "
                + ", ".join(unknown_module_keys)
            )

        source_block = raw.get("source", {})
        if not isinstance(source_block, dict):
            raise ValueError(f"components.infra.{module_name} source must be a mapping")
        supported_source_keys = {"portable", "local"}
        unknown_source_keys = sorted(
            str(key) for key in source_block if str(key) not in supported_source_keys
        )
        if unknown_source_keys:
            raise ValueError(
                f"components.infra.{module_name} source has unsupported field(s): "
                + ", ".join(unknown_source_keys)
            )
        portable_source = _as_text(source_block.get("portable"))
        local_source = _as_text(source_block.get("local")) or None
        source = _resolved_module_source(
            module_name=module_name,
            portable_source=portable_source,
            local_source=local_source,
            source_profile=source_profile,
        )
        metadata_source = _metadata_module_source(
            portable_source=portable_source,
            local_source=local_source,
            resolved_source=source,
            source_root=source_root,
        )
        description, group, enable, _selectable = _parse_ui_block(
            raw.get("ui"),
            field_label=f"components.infra.{module_name}",
        )
        validation_profile = resolve_builtin_validation_profile(module_name)
        raw_cli = cli_settings.infra.get(module_name)
        mk8s_gpu, observability = _parse_infra_component_cli(
            raw_cli,
            module_name=module_name,
            field_label=f"components.infra.{module_name}.cli",
            source_profile=source_profile,
            source_root=cli_source_root,
        )
        wizard_fields = _parse_component_wizard_fields(
            component_id=module_name,
            raw_profile=raw.get("wizard_profile"),
            raw_wizard=raw.get("wizard"),
            derived_wizard=_derived_infra_component_wizard_fields(
                module_name=module_name,
                raw_cli=raw_cli,
                mk8s_gpu=mk8s_gpu,
                observability=observability,
            ),
            field_label=f"components.infra.{module_name}",
        )
        defaults = _parse_component_defaults(
            raw.get("defaults"),
            field_label=f"components.infra.{module_name}",
        )
        handoff = resolve_builtin_handoff(module_name)
        outputs = _component_outputs_with_builtin_handoff(
            _discover_component_outputs(metadata_source),
            field_label=f"components.infra.{module_name}",
            handoff=handoff,
        )
        input_bindings = _parse_component_input_bindings(raw.get("input"))
        status = _parse_status_watcher(raw.get("status"))
        tf_modules.append(
            TFModuleSource(
                module=module_name,
                source=source,
                portable_source=portable_source,
                local_source=local_source,
                metadata_source=metadata_source,
                description=description,
                enable=enable,
                group=group,
                validation_profile=validation_profile,
                wizard_fields=wizard_fields,
                defaults=defaults,
                outputs=outputs,
                input_bindings=input_bindings,
                handoff=handoff,
                status=status,
                mk8s_gpu=mk8s_gpu,
                observability=observability,
            )
        )

    helm_charts: list[HelmChartSource] = []
    for component_id_raw, raw in apps.items():
        component_id = _as_text(component_id_raw)
        if not component_id:
            continue
        if normalize_component_token(component_id) == "soperator":
            raise ValueError(
                "components.apps.soperator is no longer supported in component_sources; "
                "use the dedicated `nebius-cxcli soperator` lifecycle commands"
            )
        if not isinstance(raw, dict):
            raise ValueError(f"components.apps.{component_id} must be a mapping")
        supported_chart_keys = {
            "source",
            "usage",
            "ui",
            "release",
            "defaults",
            "wizard_profile",
            "wizard",
            "input",
        }
        unknown_chart_keys = sorted(str(key) for key in raw if str(key) not in supported_chart_keys)
        if unknown_chart_keys:
            raise ValueError(
                f"components.apps.{component_id} has unsupported field(s): "
                + ", ".join(unknown_chart_keys)
            )

        source, portable_source, local_source = _parse_helm_chart_source_block(
            raw.get("source"),
            field_label=f"components.apps.{component_id}.source",
            default_chart_name=component_id,
            source_profile=source_profile,
            source_root=source_root,
        )

        release_block = raw.get("release", {})
        if release_block is None:
            release_block = {}
        if not isinstance(release_block, dict):
            raise ValueError(f"components.apps.{component_id} release must be a mapping")
        supported_release_keys = {"namespace", "name", "timeout", "install_after"}
        unknown_release_keys = sorted(
            str(key) for key in release_block if str(key) not in supported_release_keys
        )
        if unknown_release_keys:
            raise ValueError(
                f"components.apps.{component_id} release has unsupported field(s): "
                + ", ".join(unknown_release_keys)
            )
        namespace = _as_text(release_block.get("namespace")) or None
        release_name = _as_text(release_block.get("name")) or None
        release_timeout = _as_text(release_block.get("timeout")) or cli.flux.release_timeout
        if not GO_DURATION_RE.fullmatch(release_timeout):
            raise ValueError(
                f"components.apps.{component_id} release.timeout must be a Go-style duration "
                "like '5m' or '12m30s'"
            )

        description, group, enable, selectable = _parse_ui_block(
            raw.get("ui"),
            field_label=f"components.apps.{component_id}",
        )
        usage = _parse_helm_chart_usage(
            raw.get("usage"),
            field_label=f"components.apps.{component_id}",
        )
        if usage.lifecycle == "transient":
            if enable:
                raise ValueError(
                    f"components.apps.{component_id}.ui.enabled must be false "
                    "when usage.lifecycle=transient"
                )
            if selectable:
                raise ValueError(
                    f"components.apps.{component_id}.ui.selectable must be false "
                    "when usage.lifecycle=transient"
                )
        wizard_fields = _parse_component_wizard_fields(
            component_id=component_id,
            raw_profile=raw.get("wizard_profile"),
            raw_wizard=raw.get("wizard"),
            field_label=f"components.apps.{component_id}",
        )
        defaults = _parse_component_defaults(
            raw.get("defaults"),
            field_label=f"components.apps.{component_id}",
        )
        defaults = _materialize_grafana_dashboard_defaults(
            defaults,
            source_root=source_root,
            field_label=f"components.apps.{component_id}.defaults",
        )
        raw_cli = cli_settings.apps.get(component_id)
        mk8s_gpu, observability, grafana = _parse_app_component_cli(
            raw_cli,
            field_label=f"components.apps.{component_id}.cli",
        )
        mk8s_gpu = _render_mk8s_gpu_policy_chart_version_templates(
            mk8s_gpu,
            chart_version=_as_text(portable_source.version),
            field_label=f"components.apps.{component_id}.cli.mk8s_gpu_policy",
        )
        grafana = _validate_grafana_dashboard_signal_bindings(
            component_id=component_id,
            defaults=defaults,
            grafana=grafana,
        )
        input_bindings = _parse_component_input_bindings(raw.get("input"))
        helm_charts.append(
            HelmChartSource(
                name=component_id,
                source=source,
                portable_source=portable_source,
                local_source=local_source,
                namespace=namespace,
                release_name=release_name,
                release_timeout=release_timeout,
                release_install_after=_parse_string_list(
                    release_block.get("install_after"),
                    field_label=f"components.apps.{component_id}.release.install_after",
                ),
                enable=enable,
                selectable=selectable,
                description=description,
                group=group,
                wizard_fields=wizard_fields,
                defaults=defaults,
                outputs=(),
                input_bindings=input_bindings,
                usage=usage,
                mk8s_gpu=mk8s_gpu,
                observability=observability,
                grafana=grafana,
            )
        )

    tf_module_tuple = tuple(tf_modules)
    helm_chart_tuple = tuple(helm_charts)
    _validate_grafana_datasource_read_endpoints(
        observability=global_observability,
        helm_charts=helm_chart_tuple,
    )
    return ComponentSources(
        cli=cli,
        compute=cli_settings.compute,
        shared=shared,
        tf_modules=tf_module_tuple,
        helm_charts=helm_chart_tuple,
        observability=global_observability,
    )


def _load_yaml_mapping_from_path(path: Path, *, subject: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} root must be a mapping")
    return payload


def _load_sources_from_path(path: Path, *, source_profile: SourceProfile) -> ComponentSources:
    payload = _load_yaml_mapping_from_path(path, subject="component_sources")
    cli_source_root = path.parent
    try:
        cli_settings_path = resolve_component_cli_settings_file(component_sources_file=path)
    except ValueError:
        cli_settings_payload: dict[str, Any] = {}
    else:
        cli_settings_payload = _load_yaml_mapping_from_path(
            cli_settings_path,
            subject="component_cli_settings",
        )
        cli_source_root = cli_settings_path.parent
    return _parse_sources_payload(
        payload,
        cli_settings_payload=cli_settings_payload,
        source_profile=source_profile,
        source_root=path.parent,
        cli_source_root=cli_source_root,
    )


def _load_cli_settings_from_path(path: Path) -> CliSettings:
    payload = _load_yaml_mapping_from_path(path, subject="component_cli_settings")
    return _parse_component_cli_settings_payload(payload).cli


def _load_bundled_component_sources(*, source_profile: SourceProfile) -> ComponentSources:
    package_resources = importlib_resources.files("nebius_cxcli")
    resource = package_resources.joinpath(BUNDLED_COMPONENT_SOURCES_FILENAME)
    cli_settings_resource = package_resources.joinpath(BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME)
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        cli_settings_payload = (
            yaml.safe_load(cli_settings_resource.read_text(encoding="utf-8")) or {}
        )
        return _parse_sources_payload(
            payload,
            cli_settings_payload=cli_settings_payload,
            source_profile=source_profile,
        )
    except FileNotFoundError:
        pass
    except OSError:
        pass

    prefix_candidate = Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_SOURCES_FILENAME
    prefix_cli_settings = (
        Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME
    )
    if (
        prefix_candidate.exists()
        and prefix_candidate.is_file()
        and prefix_cli_settings.exists()
        and prefix_cli_settings.is_file()
    ):
        return _load_sources_from_path(prefix_candidate, source_profile=source_profile)

    if DEFAULT_COMPONENT_SOURCES_FILE.exists() and DEFAULT_COMPONENT_SOURCES_FILE.is_file():
        return _load_sources_from_path(
            DEFAULT_COMPONENT_SOURCES_FILE, source_profile=source_profile
        )

    raise FileNotFoundError(
        "Bundled component CLI settings file is missing from the installed package layout."
    )


def _load_bundled_cli_settings() -> CliSettings:
    resource = importlib_resources.files("nebius_cxcli").joinpath(
        BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME
    )
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        return _parse_component_cli_settings_payload(payload).cli
    except FileNotFoundError:
        pass
    except OSError:
        pass

    prefix_candidate = Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_CLI_SETTINGS_FILENAME
    if prefix_candidate.exists() and prefix_candidate.is_file():
        return _load_cli_settings_from_path(prefix_candidate)

    if (
        DEFAULT_COMPONENT_CLI_SETTINGS_FILE.exists()
        and DEFAULT_COMPONENT_CLI_SETTINGS_FILE.is_file()
    ):
        return _load_cli_settings_from_path(DEFAULT_COMPONENT_CLI_SETTINGS_FILE)

    raise FileNotFoundError(
        "Bundled component CLI settings file is missing from the installed package layout."
    )


@lru_cache(maxsize=16)
def _load_sources_cached(path_text: str, profile_value: str) -> ComponentSources:
    return _load_sources_from_path(Path(path_text), source_profile=SourceProfile(profile_value))


@lru_cache(maxsize=2)
def _load_bundled_sources_cached(profile_value: str) -> ComponentSources:
    return _load_bundled_component_sources(source_profile=SourceProfile(profile_value))


@lru_cache(maxsize=8)
def _load_cli_settings_cached(path_text: str) -> CliSettings:
    return _load_cli_settings_from_path(Path(path_text))


@lru_cache(maxsize=1)
def _load_bundled_cli_settings_cached() -> CliSettings:
    return _load_bundled_cli_settings()


def _can_use_bundled_default(*, explicit: Path | None) -> bool:
    if explicit is not None:
        return False
    if _CLI_COMPONENT_SOURCES_FILE_OVERRIDE is not None:
        return False
    return not bool(os.environ.get(COMPONENT_SOURCES_FILE_ENV, "").strip())


def load_component_sources(
    *,
    explicit: Path | None = None,
    source_profile: SourceProfile | None = None,
) -> ComponentSources:
    resolved_profile = resolve_component_sources_profile(explicit=source_profile)
    try:
        path = resolve_component_sources_file(explicit=explicit)
    except ValueError:
        if _can_use_bundled_default(explicit=explicit):
            return _load_bundled_sources_cached(resolved_profile.value)
        raise
    return _load_sources_cached(str(path), resolved_profile.value)


def load_cli_settings(*, explicit: Path | None = None) -> CliSettings:
    try:
        sources_path = resolve_component_sources_file(explicit=explicit)
    except ValueError:
        if _can_use_bundled_default(explicit=explicit):
            return _load_bundled_cli_settings_cached()
        raise
    try:
        path = resolve_component_cli_settings_file(component_sources_file=sources_path)
    except ValueError:
        return CliSettings()
    return _load_cli_settings_cached(str(path))


def reset_component_sources_cache() -> None:
    _load_sources_cached.cache_clear()
    _load_bundled_sources_cached.cache_clear()
    _load_cli_settings_cached.cache_clear()
    _load_bundled_cli_settings_cached.cache_clear()
