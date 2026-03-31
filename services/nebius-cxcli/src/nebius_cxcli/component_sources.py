"""Runtime component source registry loader and discovery helpers."""

from __future__ import annotations

import copy
import os
import re
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any

import yaml

DEFAULT_COMPONENT_SOURCES_FILE = (Path(__file__).resolve().parents[2] / "component_sources.yaml").resolve()
USER_COMPONENT_SOURCES_FILE = (Path.home() / ".config" / "nebius-cxcli" / "component_sources.yaml").resolve()
GLOBAL_COMPONENT_SOURCES_FILE = Path("/etc/nebius-cxcli/component_sources.yaml")
BUNDLED_COMPONENT_SOURCES_FILENAME = "component_sources.yaml"
COMPONENT_SOURCES_FILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_FILE"
COMPONENT_SOURCES_PROFILE_ENV = "NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE"
DEFAULT_FLUX_VERSION = "v2.8.0"
DEFAULT_TERRAFORM_VERSION = "1.14.1"

_CLI_COMPONENT_SOURCES_FILE_OVERRIDE: Path | None = None
_CLI_COMPONENT_SOURCES_PROFILE_OVERRIDE: SourceProfile | None = None


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


@dataclass(frozen=True)
class ComponentDefault:
    target_path: str
    value: Any = None
    kind: str = "literal"
    source_path: str = ""


@dataclass(frozen=True)
class Handoff:
    cluster_id_output_name: str
    access_output_name: str


@dataclass(frozen=True)
class FluxSettings:
    version: str = DEFAULT_FLUX_VERSION


@dataclass(frozen=True)
class TerraformSettings:
    version: str = DEFAULT_TERRAFORM_VERSION


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
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()
    handoff: Handoff | None = None


@dataclass(frozen=True)
class HelmChartSource:
    name: str
    repo: str | None = None
    version: str | None = None
    namespace: str | None = None
    release_name: str | None = None
    enable: bool = False
    description: str | None = None
    group: str | None = None
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()


@dataclass(frozen=True)
class ComponentSources:
    cli: CliSettings
    shared: dict[str, Any]
    tf_modules: tuple[TFModuleSource, ...]
    helm_charts: tuple[HelmChartSource, ...]


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
            raise ValueError(
                f"{COMPONENT_SOURCES_PROFILE_ENV} must be one of: {allowed}"
            ) from exc

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
            raise ValueError(
                f"{COMPONENT_SOURCES_FILE_ENV} points to a missing file: {resolved}"
            )
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


def _resolve_existing_local_module_source(source: str, *, source_root: Path | None = None) -> str | None:
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


def _parse_shared_values(raw: Any) -> dict[str, Any]:
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
    if not isinstance(public_key, str):
        raise ValueError("shared.admin_ssh.public_key must be a string")

    return copy.deepcopy(dict(raw))


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

    supported_flux_keys = {"version"}
    unknown_flux = sorted(str(key) for key in flux_raw if str(key) not in supported_flux_keys)
    if unknown_flux:
        raise ValueError("cli.flux has unsupported field(s): " + ", ".join(unknown_flux))

    raw_version = _as_text(flux_raw.get("version")) or DEFAULT_FLUX_VERSION
    if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,2}", raw_version):
        raise ValueError("cli.flux.version must be a semantic version like 'v2.8.0'")
    version = raw_version if raw_version.startswith("v") else f"v{raw_version}"
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
        flux=FluxSettings(version=version),
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
            f"outputs.tf_outputs could not discover Terraform outputs for module source "
            f"'{module_source}': {issues[0]}"
        )
    raise ValueError(
        f"outputs.tf_outputs could not discover any Terraform outputs for module source "
        f"'{module_source}'. Expose Terraform outputs in the module or declare explicit "
        "outputs.terraform aliases instead."
    )


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


def _parse_component_outputs(
    raw: Any,
    *,
    field_label: str,
    scope: str,
    module_source: str | None = None,
) -> tuple[ComponentOutput, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(
            f"{field_label} outputs must be a mapping with optional keys "
            "'tf_outputs', 'terraform', 'config', and 'static'"
        )

    supported_keys = {"tf_outputs", "terraform", "config", "static"}
    unknown_keys = sorted(str(key) for key in raw if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(
            f"{field_label} outputs use unsupported key(s): {', '.join(unknown_keys)}. "
            "Supported keys are 'tf_outputs', 'terraform', 'config', and 'static'."
        )

    tf_outputs_raw = raw.get("tf_outputs", False)
    if not isinstance(tf_outputs_raw, bool):
        raise ValueError(f"{field_label} outputs.tf_outputs must be a boolean")

    outputs: list[ComponentOutput] = []
    seen_aliases: set[str] = set()

    if tf_outputs_raw:
        if scope != "infra":
            raise ValueError(f"{field_label} outputs.tf_outputs is supported only for infra.tf_modules[]")
        source = _as_text(module_source)
        if not source:
            raise ValueError(f"{field_label} outputs.tf_outputs requires a non-empty module source")
        for terraform_output in _discover_terraform_outputs(source):
            _append_component_output(
                outputs,
                seen_aliases=seen_aliases,
                output=terraform_output,
                field_label=field_label,
            )

    terraform_raw = raw.get("terraform")
    if terraform_raw is not None:
        if scope != "infra":
            raise ValueError(f"{field_label} outputs.terraform is supported only for infra.tf_modules[]")
        if not isinstance(terraform_raw, dict):
            raise ValueError(
                f"{field_label} outputs.terraform must be a mapping of alias -> Terraform output name"
            )
        terraform_source_by_name: dict[str, ComponentOutput] = {}
        source = _as_text(module_source)
        if source:
            try:
                terraform_source_by_name = {
                    output.source_path: output for output in _discover_terraform_outputs(source)
                }
            except ValueError:
                terraform_source_by_name = {}
        for output_name_raw, source_raw in terraform_raw.items():
            output_name = _normalize_component_output_name(_as_text(output_name_raw))
            source_path = _as_text(source_raw)
            if not output_name or not source_path:
                raise ValueError(
                    f"{field_label} outputs.terraform entries must use non-empty alias and Terraform output name"
                )
            source_output = terraform_source_by_name.get(source_path)
            _append_component_output(
                outputs,
                seen_aliases=seen_aliases,
                output=ComponentOutput(
                    name=output_name,
                    kind="terraform_output",
                    source_path=source_path,
                    sensitive=bool(source_output.sensitive) if source_output is not None else False,
                ),
                field_label=field_label,
            )

    config_raw = raw.get("config")
    if config_raw is not None:
        if not isinstance(config_raw, dict):
            raise ValueError(
                f"{field_label} outputs.config must be a mapping of alias -> component config path"
            )
        for output_name_raw, source_raw in config_raw.items():
            output_name = _normalize_component_output_name(_as_text(output_name_raw))
            source_path = _as_text(source_raw)
            if not output_name or not source_path:
                raise ValueError(
                    f"{field_label} outputs.config entries must use non-empty alias and config path"
                )
            _append_component_output(
                outputs,
                seen_aliases=seen_aliases,
                output=ComponentOutput(
                    name=output_name,
                    kind="config",
                    source_path=source_path,
                ),
                field_label=field_label,
            )

    static_raw = raw.get("static")
    if static_raw is not None:
        if not isinstance(static_raw, dict):
            raise ValueError(f"{field_label} outputs.static must be a mapping of alias -> literal value")
        for output_name_raw, value_raw in static_raw.items():
            output_name = _normalize_component_output_name(_as_text(output_name_raw))
            if not output_name:
                raise ValueError(f"{field_label} outputs.static entries must use non-empty aliases")
            _append_component_output(
                outputs,
                seen_aliases=seen_aliases,
                output=ComponentOutput(
                    name=output_name,
                    kind="static",
                    value=copy.deepcopy(value_raw),
                ),
                field_label=field_label,
            )

    return tuple(outputs)


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
            raise ValueError("input entries must use non-empty target path and component output reference")
        component_token, separator, output_token = ref.partition(".")
        component_id = _as_text(component_token).lower()
        output_name = _normalize_component_output_name(output_token) if separator else ""
        if not component_id or not separator or not output_name:
            raise ValueError(
                f"input binding '{target_path}' must use '<component-id>.<output-alias>' reference syntax. "
                "Use 'defaults' for literal values."
            )
        bindings.append(
            ComponentInputBinding(
                target_path=target_path,
                source_component_id=component_id,
                source_output_name=output_name,
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


def _parse_handoff(raw: Any) -> Handoff | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("handoff must be a mapping")

    cluster_id_output_name = _normalize_component_output_name(_as_text(raw.get("cluster_id")))
    if not cluster_id_output_name:
        raise ValueError("handoff.cluster_id is required and must name a declared component output")
    access_output_name = _normalize_component_output_name(_as_text(raw.get("access")))
    if not access_output_name:
        raise ValueError("handoff.access is required and must name a declared component output")
    return Handoff(
        cluster_id_output_name=cluster_id_output_name,
        access_output_name=access_output_name,
    )


def _resolved_module_source(
    *,
    module_name: str,
    portable_source: str,
    local_source: str | None,
    source_profile: SourceProfile,
) -> str:
    if not portable_source:
        raise ValueError(
            f"infra.tf_modules[{module_name}] portable_source is required"
        )
    if source_profile == SourceProfile.LOCAL and str(local_source or "").strip():
        return str(local_source).strip()
    return portable_source


def _parse_sources_payload(
    payload: Any,
    *,
    source_profile: SourceProfile,
    source_root: Path | None = None,
) -> ComponentSources:
    if not isinstance(payload, dict):
        raise ValueError("component_sources root must be a mapping")
    supported_root_keys = {"cli", "shared", "infra", "apps"}
    unknown_root = sorted(str(key) for key in payload if str(key) not in supported_root_keys)
    if unknown_root:
        raise ValueError("component_sources root has unsupported field(s): " + ", ".join(unknown_root))

    cli = _parse_cli_settings(payload.get("cli"))
    shared = _parse_shared_values(payload.get("shared"))
    infra = payload.get("infra", {})
    apps = payload.get("apps", {})
    if not isinstance(infra, dict):
        infra = {}
    if not isinstance(apps, dict):
        apps = {}
    supported_infra_keys = {"tf_modules"}
    unknown_infra = sorted(str(key) for key in infra if str(key) not in supported_infra_keys)
    if unknown_infra:
        raise ValueError("infra has unsupported field(s): " + ", ".join(unknown_infra))
    supported_apps_keys = {"helm_charts"}
    unknown_apps = sorted(str(key) for key in apps if str(key) not in supported_apps_keys)
    if unknown_apps:
        raise ValueError("apps has unsupported field(s): " + ", ".join(unknown_apps))

    tf_modules: list[TFModuleSource] = []
    for raw in infra.get("tf_modules", []):
        if not isinstance(raw, dict):
            continue
        module_name = _as_text(raw.get("module")).lower()
        portable_source = _as_text(raw.get("portable_source"))
        local_source = _as_text(raw.get("local_source")) or None
        if not module_name:
            continue
        supported_module_keys = {
            "module",
            "portable_source",
            "local_source",
            "description",
            "version",
            "enable",
            "group",
            "defaults",
            "outputs",
            "input",
            "handoff",
        }
        unknown_module_keys = sorted(str(key) for key in raw if str(key) not in supported_module_keys)
        if unknown_module_keys:
            raise ValueError(
                f"infra.tf_modules[{module_name}] has unsupported field(s): "
                + ", ".join(unknown_module_keys)
            )
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
        description = _as_text(raw.get("description")) or None
        version = _as_text(raw.get("version")) or None
        enable = bool(raw.get("enable", False))
        group = _as_text(raw.get("group")) or None
        defaults = _parse_component_defaults(
            raw.get("defaults"),
            field_label=f"infra.tf_modules[{module_name}]",
        )
        outputs = _parse_component_outputs(
            raw.get("outputs"),
            field_label=f"infra.tf_modules[{module_name}]",
            scope="infra",
            module_source=metadata_source,
        )
        input_bindings = _parse_component_input_bindings(raw.get("input"))
        handoff = _parse_handoff(raw.get("handoff"))
        tf_modules.append(
            TFModuleSource(
                module=module_name,
                source=source,
                portable_source=portable_source,
                local_source=local_source,
                metadata_source=metadata_source,
                description=description,
                version=version,
                enable=enable,
                group=group,
                defaults=defaults,
                outputs=outputs,
                input_bindings=input_bindings,
                handoff=handoff,
            )
        )

    helm_charts: list[HelmChartSource] = []
    for raw in apps.get("helm_charts", []):
        if not isinstance(raw, dict):
            continue
        chart_name = _as_text(raw.get("name"))
        repo_raw = _as_text(raw.get("repo")).rstrip("/")
        if not chart_name:
            continue
        supported_chart_keys = {
            "name",
            "repo",
            "version",
            "namespace",
            "releasename",
            "enable",
            "description",
            "group",
            "defaults",
            "outputs",
            "input",
        }
        unknown_chart_keys = sorted(str(key) for key in raw if str(key) not in supported_chart_keys)
        if unknown_chart_keys:
            raise ValueError(
                f"apps.helm_charts[{chart_name}] has unsupported field(s): "
                + ", ".join(unknown_chart_keys)
            )
        repo = repo_raw or None
        version = _as_text(raw.get("version")) or None
        namespace = _as_text(raw.get("namespace")) or None
        release_name = _as_text(raw.get("releasename")) or None
        enable = bool(raw.get("enable", False))
        description = _as_text(raw.get("description")) or None
        group = _as_text(raw.get("group")) or None
        defaults = _parse_component_defaults(
            raw.get("defaults"),
            field_label=f"apps.helm_charts[{chart_name}]",
        )
        outputs = _parse_component_outputs(
            raw.get("outputs"),
            field_label=f"apps.helm_charts[{chart_name}]",
            scope="apps",
        )
        input_bindings = _parse_component_input_bindings(raw.get("input"))
        helm_charts.append(
            HelmChartSource(
                name=chart_name,
                repo=repo,
                version=version,
                namespace=namespace,
                release_name=release_name,
                enable=enable,
                description=description,
                group=group,
                defaults=defaults,
                outputs=outputs,
                input_bindings=input_bindings,
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
    resource = importlib_resources.files("nebius_cxcli").joinpath(BUNDLED_COMPONENT_SOURCES_FILENAME)
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
        return _load_sources_from_path(DEFAULT_COMPONENT_SOURCES_FILE, source_profile=source_profile)

    raise FileNotFoundError(
        "Bundled component sources file is missing from the installed package layout."
    )


def _load_bundled_cli_settings() -> CliSettings:
    resource = importlib_resources.files("nebius_cxcli").joinpath(BUNDLED_COMPONENT_SOURCES_FILENAME)
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
