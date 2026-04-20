"""Runtime component source registry loader and discovery helpers."""

from __future__ import annotations

import copy
import os
import re
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
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
USER_COMPONENT_SOURCES_FILE = (
    Path.home() / ".config" / "nebius-cxcli" / "component_sources.yaml"
).resolve()
GLOBAL_COMPONENT_SOURCES_FILE = Path("/etc/nebius-cxcli/component_sources.yaml")
BUNDLED_COMPONENT_SOURCES_FILENAME = "component_sources.yaml"
COMPONENT_SOURCES_FILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_FILE"
COMPONENT_SOURCES_PROFILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE"
DEFAULT_FLUX_VERSION = "v2.8.0"
DEFAULT_FLUX_RELEASE_TIMEOUT = "5m"
DEFAULT_TERRAFORM_VERSION = "1.14.1"
GO_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+")
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
    enabled_by_default: bool = False
    chart_component_id: str = ""
    timeout: str = ""
    training_operator_manifest: str = ""
    training_operator_namespace: str = ""
    average_bus_bandwidth_threshold_gbps: float = 0.0
    max_nodes: int = 8


@dataclass(frozen=True)
class Mk8sGpuHealthCheckerSettings:
    enabled_by_default: bool = False


@dataclass(frozen=True)
class Mk8sGpuValidationSettings:
    operator_readiness: Mk8sGpuOperatorReadinessSettings = Mk8sGpuOperatorReadinessSettings()
    gpu_visibility: Mk8sGpuVisibilitySettings = Mk8sGpuVisibilitySettings()
    nccl: Mk8sNcclSettings = Mk8sNcclSettings()
    health_checker: Mk8sGpuHealthCheckerSettings = Mk8sGpuHealthCheckerSettings()


@dataclass(frozen=True)
class Mk8sGpuSettings:
    image_preferences: Mk8sGpuImagePreferenceSettings = Mk8sGpuImagePreferenceSettings()
    validations: Mk8sGpuValidationSettings = Mk8sGpuValidationSettings()


@dataclass(frozen=True)
class Mk8sGpuAppPolicy:
    role: str = ""
    default_sets: tuple[Mk8sGpuAppDefaultSet, ...] = ()
    post_render_patch_sets: tuple[Mk8sGpuAppPostRenderPatchSet, ...] = ()
    rules: tuple[Mk8sGpuAppRule, ...] = ()
    install_after: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mk8sBootDiskRule:
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
class Mk8sNodeBootDiskPolicy:
    default_type: str = ""
    rules: tuple[Mk8sBootDiskRule, ...] = ()


@dataclass(frozen=True)
class Mk8sBootDiskSettings:
    cpu: Mk8sNodeBootDiskPolicy = Mk8sNodeBootDiskPolicy()
    gpu: Mk8sNodeBootDiskPolicy = Mk8sNodeBootDiskPolicy()


@dataclass(frozen=True)
class VmImagePreferenceSettings:
    preferred_cpu_image_families: tuple[str, ...] = ()
    preferred_gpu_image_families: tuple[str, ...] = ()


@dataclass(frozen=True)
class CliSettings:
    flux: FluxSettings = FluxSettings()
    terraform: TerraformSettings = TerraformSettings()


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
    return f"{component_selector}.{binding.source_output_name}"


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
    mk8s_boot_disks: Mk8sBootDiskSettings = Mk8sBootDiskSettings()
    vm_images: VmImagePreferenceSettings = VmImagePreferenceSettings()


@dataclass(frozen=True)
class HelmChartLocator:
    repo: str = ""
    chart_name: str | None = None
    version: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class HelmChartSource:
    name: str
    source: HelmChartLocator = HelmChartLocator()
    portable_source: HelmChartLocator = HelmChartLocator()
    local_source: HelmChartLocator = HelmChartLocator()
    namespace: str | None = None
    release_name: str | None = None
    release_timeout: str | None = None
    enable: bool = False
    selectable: bool = True
    description: str | None = None
    group: str | None = None
    wizard_fields: dict[str, dict[str, Any]] | None = None
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()
    mk8s_gpu: Mk8sGpuAppPolicy = Mk8sGpuAppPolicy()

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
    shared: dict[str, Any]
    tf_modules: tuple[TFModuleSource, ...]
    helm_charts: tuple[HelmChartSource, ...]


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
    if isinstance(user_name, str) and user_name.strip() and not LINUX_USER_NAME_RE.fullmatch(user_name):
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
            raise ValueError(
                f"{field_label} keys must start with '{required_prefix}'"
            )
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
            raise ValueError(f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown))
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
    if value not in {"nebius_image", "manual"}:
        raise ValueError(f"{field_label} must be 'nebius_image' or 'manual'")
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


def _parse_mk8s_boot_disk_rules(
    raw: Any,
    *,
    field_label: str,
) -> tuple[Mk8sBootDiskRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_label} must be a list")
    rules: list[Mk8sBootDiskRule] = []
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
            raise ValueError(f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown))
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
            Mk8sBootDiskRule(
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


def _parse_mk8s_node_boot_disk_policy(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sNodeBootDiskPolicy:
    if raw is None:
        return Mk8sNodeBootDiskPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"default_type", "rules"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sNodeBootDiskPolicy(
        default_type=_parse_optional_disk_type(
            raw.get("default_type"),
            field_label=f"{field_label}.default_type",
        ),
        rules=_parse_mk8s_boot_disk_rules(
            raw.get("rules"),
            field_label=f"{field_label}.rules",
        ),
    )


def _parse_mk8s_boot_disk_settings(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sBootDiskSettings:
    if raw is None:
        return Mk8sBootDiskSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"cpu", "gpu"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sBootDiskSettings(
        cpu=_parse_mk8s_node_boot_disk_policy(
            raw.get("cpu"),
            field_label=f"{field_label}.cpu",
        ),
        gpu=_parse_mk8s_node_boot_disk_policy(
            raw.get("gpu"),
            field_label=f"{field_label}.gpu",
        ),
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
            raise ValueError(f"{field_label}[{index}] has unsupported field(s): " + ", ".join(unknown))
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
        unknown_default_sets = sorted(name for name in defaults_from if name not in available_default_sets)
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
            name for name in post_render_patches_from if name not in available_post_render_patch_sets
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
        "enabled_by_default",
        "chart_component_id",
        "timeout",
        "training_operator_manifest",
        "training_operator_namespace",
        "average_bus_bandwidth_threshold_gbps",
        "max_nodes",
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
        raise ValueError(f"{field_label}.average_bus_bandwidth_threshold_gbps must be numeric") from exc
    if threshold < 0:
        raise ValueError(f"{field_label}.average_bus_bandwidth_threshold_gbps must be >= 0")
    max_nodes_raw = raw.get("max_nodes", Mk8sNcclSettings().max_nodes)
    try:
        max_nodes = int(max_nodes_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label}.max_nodes must be an integer >= 1") from exc
    if max_nodes < 1:
        raise ValueError(f"{field_label}.max_nodes must be >= 1")
    return Mk8sNcclSettings(
        enabled_by_default=bool(raw.get("enabled_by_default", True)),
        chart_component_id=_as_text(raw.get("chart_component_id")),
        timeout=timeout,
        training_operator_manifest=_as_text(raw.get("training_operator_manifest")),
        training_operator_namespace=_as_text(raw.get("training_operator_namespace")),
        average_bus_bandwidth_threshold_gbps=threshold,
        max_nodes=max_nodes,
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


def _parse_mk8s_gpu_validation_settings(
    raw: Any,
    *,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> Mk8sGpuValidationSettings:
    if raw is None:
        return Mk8sGpuValidationSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"operator_readiness", "gpu_visibility", "nccl", "health_checker"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuValidationSettings(
        operator_readiness=_parse_mk8s_gpu_operator_readiness_settings(
            raw.get("operator_readiness"),
            field_label=f"{field_label}.operator_readiness",
        ),
        gpu_visibility=_parse_mk8s_gpu_visibility_settings(
            raw.get("gpu_visibility"),
            field_label=f"{field_label}.gpu_visibility",
        ),
        nccl=_parse_mk8s_nccl_settings(
            raw.get("nccl"),
            field_label=f"{field_label}.nccl",
        ),
        health_checker=_parse_mk8s_gpu_health_checker_settings(
            raw.get("health_checker"),
            field_label=f"{field_label}.health_checker",
        ),
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
    supported_keys = {"image_preferences", "validations"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuSettings(
        image_preferences=_parse_mk8s_gpu_image_preference_settings(
            raw.get("image_preferences"),
            field_label=f"{field_label}.image_preferences",
        ),
        validations=_parse_mk8s_gpu_validation_settings(
            raw.get("validations"),
            field_label=f"{field_label}.validations",
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
    )


def _parse_vm_image_preference_settings(
    raw: Any,
    *,
    field_label: str,
) -> VmImagePreferenceSettings:
    if raw is None:
        return VmImagePreferenceSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {
        "preferred_cpu_image_families",
        "preferred_gpu_image_families",
    }
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return VmImagePreferenceSettings(
        preferred_cpu_image_families=_parse_string_list(
            raw.get("preferred_cpu_image_families"),
            field_label=f"{field_label}.preferred_cpu_image_families",
        ),
        preferred_gpu_image_families=_parse_string_list(
            raw.get("preferred_gpu_image_families"),
            field_label=f"{field_label}.preferred_gpu_image_families",
        ),
    )


def _parse_infra_component_cli(
    raw: Any,
    *,
    module_name: str,
    field_label: str,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> tuple[Mk8sGpuSettings, Mk8sBootDiskSettings, VmImagePreferenceSettings]:
    if raw is None:
        return Mk8sGpuSettings(), Mk8sBootDiskSettings(), VmImagePreferenceSettings()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")

    if module_name == "mk8s":
        supported_keys = {"gpu", "boot_disk_defaults"}
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
            _parse_mk8s_boot_disk_settings(
                raw.get("boot_disk_defaults"),
                field_label=f"{field_label}.boot_disk_defaults",
            ),
            VmImagePreferenceSettings(),
        )

    if module_name == "vm":
        supported_keys = {"image_preferences"}
        unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
        if unknown:
            raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
        return (
            Mk8sGpuSettings(),
            Mk8sBootDiskSettings(),
            _parse_vm_image_preference_settings(
                raw.get("image_preferences"),
                field_label=f"{field_label}.image_preferences",
            ),
        )

    unknown = sorted(str(key) for key in raw)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return Mk8sGpuSettings(), Mk8sBootDiskSettings(), VmImagePreferenceSettings()


def _parse_app_component_cli(
    raw: Any,
    *,
    field_label: str,
) -> Mk8sGpuAppPolicy:
    if raw is None:
        return Mk8sGpuAppPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"mk8s_gpu_policy"}
    unknown = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    return _parse_mk8s_gpu_app_policy(
        raw.get("mk8s_gpu_policy"),
        field_label=f"{field_label}.mk8s_gpu_policy",
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
        raise ValueError("cli.terraform.version must be a semantic version like '1.14.1'")

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


def _derived_mk8s_gpu_validation_wizard_fields(
    mk8s_gpu: Mk8sGpuSettings,
) -> dict[str, dict[str, Any]]:
    validations = mk8s_gpu.validations
    return {
        "deploy.validations.mk8s_gpu.operator_readiness.enabled": {
            "default": validations.operator_readiness.enabled_by_default,
        },
        "deploy.validations.mk8s_gpu.gpu_visibility.enabled": {
            "default": validations.gpu_visibility.enabled_by_default,
        },
        "deploy.validations.mk8s_gpu.gpu_visibility.max_nodes": {
            "default": validations.gpu_visibility.max_nodes,
        },
        "deploy.validations.mk8s_gpu.nccl.enabled": {
            "default": validations.nccl.enabled_by_default,
        },
        "deploy.validations.mk8s_gpu.nccl.max_nodes": {
            "default": validations.nccl.max_nodes,
        },
        "deploy.validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps": {
            "default": validations.nccl.average_bus_bandwidth_threshold_gbps,
        },
        "deploy.validations.mk8s_gpu.health_checker.enabled": {
            "default": validations.health_checker.enabled_by_default,
        },
    }


def _derived_infra_component_wizard_fields(
    *,
    module_name: str,
    raw_cli: Any,
    mk8s_gpu: Mk8sGpuSettings,
) -> dict[str, dict[str, Any]]:
    if (
        module_name == "mk8s"
        and isinstance(raw_cli, dict)
        and isinstance(raw_cli.get("gpu"), dict)
        and isinstance(raw_cli.get("gpu", {}).get("validations"), dict)
    ):
        return _derived_mk8s_gpu_validation_wizard_fields(mk8s_gpu)
    return {}


def _parse_status_watcher(raw: Any) -> StatusWatcher | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("status must be a mapping")

    supported_status_keys = {"kind", "parent_input", "name_input"}
    unknown_status_keys = sorted(str(key) for key in raw if str(key) not in supported_status_keys)
    if unknown_status_keys:
        raise ValueError("status has unsupported field(s): " + ", ".join(unknown_status_keys))

    kind = _as_text(raw.get("kind")).strip().lower()
    if not kind:
        raise ValueError("status.kind is required")

    parent_input = _as_text(raw.get("parent_input")) or "parent_id"
    name_input = _as_text(raw.get("name_input")) or "name"
    if not parent_input:
        raise ValueError("status.parent_input cannot be empty")
    if not name_input:
        raise ValueError("status.name_input cannot be empty")
    return StatusWatcher(
        kind=kind,
        parent_input=parent_input,
        name_input=name_input,
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
    supported_keys = {"repo", "chart", "version"} if not allow_path else {"path", "chart", "version"}
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
        chart_name = raw_chart_name or default_chart_name
        return HelmChartLocator(chart_name=chart_name, version=version, path=path)
    if allow_path:
        raise ValueError(f"{field_label}.path is required")
    if not repo:
        raise ValueError(f"{field_label}.repo is required")
    chart_name = raw_chart_name or default_chart_name
    return HelmChartLocator(repo=repo, chart_name=chart_name, version=version)


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


def _parse_sources_payload(
    payload: Any,
    *,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> ComponentSources:
    if not isinstance(payload, dict):
        raise ValueError("component_sources root must be a mapping")
    supported_root_keys = {"cli", "shared", "components"}
    unknown_root = sorted(str(key) for key in payload if str(key) not in supported_root_keys)
    if unknown_root:
        raise ValueError(
            "component_sources root has unsupported field(s): " + ", ".join(unknown_root)
        )

    cli = _parse_cli_settings(payload.get("cli"))
    shared = _parse_shared_values(payload.get("shared"), source_root=source_root)
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
            "cli",
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
        mk8s_gpu, mk8s_boot_disks, vm_images = _parse_infra_component_cli(
            raw.get("cli"),
            module_name=module_name,
            field_label=f"components.infra.{module_name}.cli",
            source_profile=source_profile,
            source_root=source_root,
        )
        wizard_fields = _parse_component_wizard_fields(
            component_id=module_name,
            raw_profile=raw.get("wizard_profile"),
            raw_wizard=raw.get("wizard"),
            derived_wizard=_derived_infra_component_wizard_fields(
                module_name=module_name,
                raw_cli=raw.get("cli"),
                mk8s_gpu=mk8s_gpu,
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
                mk8s_boot_disks=mk8s_boot_disks,
                vm_images=vm_images,
            )
        )

    helm_charts: list[HelmChartSource] = []
    for component_id_raw, raw in apps.items():
        component_id = _as_text(component_id_raw)
        if not component_id:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"components.apps.{component_id} must be a mapping")
        supported_chart_keys = {
            "source",
            "ui",
            "release",
            "defaults",
            "cli",
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
        supported_release_keys = {"namespace", "name", "timeout"}
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
        wizard_fields = _parse_component_wizard_fields(
            component_id=component_id,
            raw_profile=None,
            raw_wizard=raw.get("wizard"),
            field_label=f"components.apps.{component_id}",
        )
        defaults = _parse_component_defaults(
            raw.get("defaults"),
            field_label=f"components.apps.{component_id}",
        )
        mk8s_gpu = _parse_app_component_cli(
            raw.get("cli"),
            field_label=f"components.apps.{component_id}.cli",
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
                enable=enable,
                selectable=selectable,
                description=description,
                group=group,
                wizard_fields=wizard_fields,
                defaults=defaults,
                outputs=(),
                input_bindings=input_bindings,
                mk8s_gpu=mk8s_gpu,
            )
        )

    return ComponentSources(
        cli=cli,
        shared=shared,
        tf_modules=tuple(tf_modules),
        helm_charts=tuple(helm_charts),
    )


def _load_sources_from_path(path: Path, *, source_profile: SourceProfile) -> ComponentSources:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return _parse_sources_payload(
        payload,
        source_profile=source_profile,
        source_root=path.parent,
    )


def _load_cli_settings_from_path(path: Path) -> CliSettings:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("component sources root must be a mapping")
    return _parse_cli_settings(payload.get("cli"))


def _load_bundled_component_sources(*, source_profile: SourceProfile) -> ComponentSources:
    resource = importlib_resources.files("nebius_cxcli").joinpath(
        BUNDLED_COMPONENT_SOURCES_FILENAME
    )
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        return _parse_sources_payload(payload, source_profile=source_profile)
    except FileNotFoundError:
        pass
    except OSError:
        pass

    prefix_candidate = Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_SOURCES_FILENAME
    if prefix_candidate.exists() and prefix_candidate.is_file():
        return _load_sources_from_path(prefix_candidate, source_profile=source_profile)

    if DEFAULT_COMPONENT_SOURCES_FILE.exists() and DEFAULT_COMPONENT_SOURCES_FILE.is_file():
        return _load_sources_from_path(
            DEFAULT_COMPONENT_SOURCES_FILE, source_profile=source_profile
        )

    raise FileNotFoundError(
        "Bundled component sources file is missing from the installed package layout."
    )


def _load_bundled_cli_settings() -> CliSettings:
    resource = importlib_resources.files("nebius_cxcli").joinpath(
        BUNDLED_COMPONENT_SOURCES_FILENAME
    )
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("component sources root must be a mapping")
        return _parse_cli_settings(payload.get("cli"))
    except FileNotFoundError:
        pass
    except OSError:
        pass

    prefix_candidate = Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_SOURCES_FILENAME
    if prefix_candidate.exists() and prefix_candidate.is_file():
        return _load_cli_settings_from_path(prefix_candidate)

    if DEFAULT_COMPONENT_SOURCES_FILE.exists() and DEFAULT_COMPONENT_SOURCES_FILE.is_file():
        return _load_cli_settings_from_path(DEFAULT_COMPONENT_SOURCES_FILE)

    raise FileNotFoundError(
        "Bundled component sources file is missing from the installed package layout."
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
        path = resolve_component_sources_file(explicit=explicit)
    except ValueError:
        if _can_use_bundled_default(explicit=explicit):
            return _load_bundled_cli_settings_cached()
        raise
    return _load_cli_settings_cached(str(path))


def reset_component_sources_cache() -> None:
    _load_sources_cached.cache_clear()
    _load_bundled_sources_cached.cache_clear()
    _load_cli_settings_cached.cache_clear()
    _load_bundled_cli_settings_cached.cache_clear()
