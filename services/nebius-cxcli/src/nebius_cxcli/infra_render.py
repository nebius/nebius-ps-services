"""Deterministic Terraform root-module rendering helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .component_defaults import (
    resolve_component_defaults,
    set_component_path,
    shared_default_conflicts,
)
from .component_sources import (
    CANONICAL_PORTABLE_COMPONENT_SOURCES_FILE,
    ComponentOutput,
    Handoff,
    component_output_root_name,
)
from .component_wiring import (
    _UNRESOLVED,
    component_output_ref,
    input_binding_conflicts,
    output_lookup,
    resolve_static_component_output,
)
from .components import ComponentEntry, component_entries
from .paths import InstancePaths
from .provider_components import provider_resource_name_for_component
from .runtime_config import to_plain_data
from .runtime_introspection import module_variables, resolve_module_source_path
from .templates import NEBIUS_PROVIDER_SOURCE, NEBIUS_PROVIDER_VERSION
from .terraform_backend import backend_settings_from_config, render_backend_tf
from .terraform_provider import DEFAULT_PROVIDER_MODULE_NAME, build_provider_module_name

DEFAULT_TERRAFORM_REQUIRED_VERSION = ">= 1.10.0"
_ALLOWED_TERRAFORM_TYPE_TOKENS = re.compile(r"^[A-Za-z0-9_(),.\[\] ]+$")
_ALLOWED_GIT_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")
_LOCAL_SOURCE_PATTERN = re.compile(r"^(?:\.\.?/|/|~/|[A-Za-z]:[\\/])")
_PROVIDER_VAR_MODULE_NAME = "nebius_provider_module_name"
_PROVIDER_VAR_PARENT_ID = "nebius_provider_parent_id"
_PROVIDER_VAR_SA_ID = "nebius_service_account_id"
_PROVIDER_VAR_AUTH_PUBLIC_KEY_ID = "nebius_auth_public_key_id"
_PROVIDER_VAR_AUTH_PRIVATE_KEY_FILE = "nebius_auth_private_key_file"
_PROVIDER_VAR_CREDENTIALS_FILE = "nebius_service_account_credentials_file"


class RenderProfile(StrEnum):
    PORTABLE = "portable"
    LOCAL_DEV = "local-dev"


@dataclass(frozen=True)
class _VariableBinding:
    argument_name: str
    variable_name: str | None
    type_expr: str | None
    value: Any | None
    description: str
    expression: str | None = None


@dataclass(frozen=True)
class _HclExpression:
    expression: str


@dataclass(frozen=True)
class _ModulePlan:
    component_id: str
    module_name: str
    module_source: str
    module_version: str | None
    bindings: tuple[_VariableBinding, ...]
    outputs: tuple[ComponentOutput, ...] = ()
    handoff: Handoff | None = None


@dataclass(frozen=True)
class RenderedModuleSource:
    component_id: str
    module_name: str
    source: str
    portable: bool


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _terraform_required_version() -> str:
    token = os.environ.get("NEBIUS_CXCLI_TERRAFORM_REQUIRED_VERSION", "").strip()
    return token or DEFAULT_TERRAFORM_REQUIRED_VERSION


def _terraform_versions_block() -> str:
    return (
        "terraform {\n"
        f'  required_version = "{_terraform_required_version()}"\n\n'
        "  required_providers {\n"
        "    nebius = {\n"
        f'      source  = "{NEBIUS_PROVIDER_SOURCE}"\n'
        f'      version = "{NEBIUS_PROVIDER_VERSION}"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )


def _terraform_providers_block() -> str:
    return (
        'provider "nebius" {\n'
        f"  module_name = var.{_PROVIDER_VAR_MODULE_NAME}\n"
        f"  parent_id   = var.{_PROVIDER_VAR_PARENT_ID}\n"
        "\n"
        "  service_account = {\n"
        f"    account_id       = var.{_PROVIDER_VAR_SA_ID}\n"
        f"    public_key_id    = var.{_PROVIDER_VAR_AUTH_PUBLIC_KEY_ID}\n"
        f"    private_key_file = var.{_PROVIDER_VAR_AUTH_PRIVATE_KEY_FILE}\n"
        f"    credentials_file = var.{_PROVIDER_VAR_CREDENTIALS_FILE}\n"
        "  }\n"
        "}\n"
    )


def _terraform_main_block(
    *,
    module_blocks: tuple[str, ...] = (),
    provider_resource_blocks: tuple[str, ...] = (),
) -> str:
    lines: list[str] = []
    for resource_block in provider_resource_blocks:
        if resource_block:
            lines.extend([resource_block, ""])
    for module_block in module_blocks:
        if module_block:
            lines.extend([module_block, ""])
    rendered = "\n".join(lines).strip()
    return f"{rendered}\n" if rendered else ""


def _is_hcl_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _safe_hcl_identifier(value: str, *, fallback_prefix: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not token:
        token = fallback_prefix
    if not re.match(r"^[A-Za-z_]", token):
        token = f"{fallback_prefix}_{token}"
    return token


def _hcl_value(value: Any, *, indent: int = 2) -> str:
    if isinstance(value, _HclExpression):
        return value.expression
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ", ".join(_hcl_value(item, indent=indent + 2) for item in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        for key in sorted(value.keys()):
            raw_key = str(key)
            map_key = raw_key if _is_hcl_identifier(raw_key) else json.dumps(raw_key)
            lines.append(
                " " * indent
                + f"{map_key} = {_hcl_value(value[key], indent=indent + 2)}"
            )
        lines.append(" " * max(indent - 2, 0) + "}")
        return "\n".join(lines)
    return json.dumps(to_plain_data(value))


def _contains_hcl_expression(value: Any) -> bool:
    if isinstance(value, _HclExpression):
        return True
    if isinstance(value, list):
        return any(_contains_hcl_expression(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_hcl_expression(item) for item in value.values())
    return False


def _normalize_module_mode(
    *,
    source: str,
    inputs: dict[str, Any],
    entry_origin: str | None,
) -> str:
    if source:
        return "custom"
    if any(key in inputs for key in ("resource_type", "provider_resource_type")):
        return "provider"
    if (entry_origin or "").strip().lower() == "provider":
        return "provider"
    return "custom"


def _effective_component_source(*, row: dict[str, Any], entry: ComponentEntry | None) -> str:
    if entry is not None and str(entry.source or "").strip():
        return str(entry.source).strip()
    return str(row.get("source", "")).strip()


def _effective_component_version(*, row: dict[str, Any], entry: ComponentEntry | None) -> str:
    if entry is not None and str(entry.version or "").strip():
        return str(entry.version).strip()
    return str(row.get("version", "")).strip()


def _dynamic_provider_component_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    rows: list[dict[str, Any]] = []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        entry = entry_by_id.get(component_id)
        resolved_item = (
            resolve_component_defaults(
                payload=payload,
                component_node=item,
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
            )
            if entry is not None and entry.defaults
            else dict(item)
        )
        inputs = resolved_item.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        source = _effective_component_source(row=resolved_item, entry=entry)
        version = _effective_component_version(row=resolved_item, entry=entry)
        mode = _normalize_module_mode(
            source=source,
            inputs=inputs,
            entry_origin=entry.origin if entry else None,
        )
        if mode != "provider":
            continue
        rows.append(
            {
                "id": component_id,
                "source": source,
                "version": version,
                "inputs": inputs,
            }
        )
    return tuple(rows)


def _render_dynamic_provider_resource_block(
    *,
    resource_type: str,
    resource_name: str,
    attributes: dict[str, Any],
) -> str:
    lines = [
        f'resource "{resource_type}" "{resource_name}" {{',
    ]
    for key in sorted(attributes.keys()):
        attr_name = str(key).strip().replace("-", "_")
        if not attr_name:
            continue
        if not _is_hcl_identifier(attr_name):
            continue
        lines.append(f"  {attr_name} = {_hcl_value(attributes[key], indent=4)}")
    lines.append("}")
    return "\n".join(lines)


def _provider_resource_blocks(config: Any) -> tuple[str, ...]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ()

    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    rows = _dynamic_provider_component_rows(payload)
    blocks: list[str] = []
    for row in rows:
        component_id = str(row["id"])
        inputs = row["inputs"]
        if not isinstance(inputs, dict):
            continue

        explicit_resource_type = str(
            inputs.get("resource_type") or inputs.get("provider_resource_type") or ""
        ).strip()
        source_hint = str(row.get("source", "")).strip()
        if not source_hint:
            entry = entry_by_id.get(component_id)
            if entry and isinstance(entry.source, str):
                source_hint = entry.source.strip()

        resource_type = explicit_resource_type or source_hint
        if not resource_type:
            resource_type = provider_resource_name_for_component(component_id) or ""
        resource_type = resource_type.strip()
        if not resource_type:
            continue
        if not re.fullmatch(r"[a-z0-9_]+", resource_type):
            raise ValueError(
                f"infra component '{component_id}' resolved provider resource_type '{resource_type}' is invalid"
            )

        helper_keys = {
            "resource_type",
            "provider_resource_type",
            "resource_name",
            "depends_on_platform",
        }
        attributes = {
            key: value
            for key, value in inputs.items()
            if str(key).strip().lower() not in helper_keys
        }
        resource_name = str(
            inputs.get("resource_name")
            or inputs.get("name")
            or component_id.replace("-", "_")
        ).strip()
        if not _is_hcl_identifier(resource_name):
            resource_name = _safe_hcl_identifier(
                component_id.replace("-", "_"),
                fallback_prefix="component",
            )

        blocks.append(
            _render_dynamic_provider_resource_block(
                resource_type=resource_type,
                resource_name=resource_name,
                attributes=attributes,
            )
        )
    return tuple(blocks)


def _normalize_git_ref(value: str | None) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if not _ALLOWED_GIT_REF.fullmatch(token):
        return None
    return token


def _module_source_with_ref(source: str, ref: str | None) -> str:
    if not ref:
        return source
    if "?ref=" in source:
        return source
    joiner = "&" if "?" in source else "?"
    return f"{source}{joiner}ref={ref}"


def _source_uses_local_path(module_source: str) -> bool:
    source = module_source.strip()
    if not source:
        return False
    if source.startswith(("git::", "http://", "https://", "oci://")):
        return False
    if _LOCAL_SOURCE_PATTERN.match(source):
        return True
    return resolve_module_source_path(source) is not None


def is_portable_module_source(module_source: str) -> bool:
    return not _source_uses_local_path(module_source)


@lru_cache(maxsize=1)
def _portable_module_source_overrides() -> dict[str, str]:
    if (
        not CANONICAL_PORTABLE_COMPONENT_SOURCES_FILE.exists()
        or not CANONICAL_PORTABLE_COMPONENT_SOURCES_FILE.is_file()
    ):
        return {}

    payload = yaml.safe_load(
        CANONICAL_PORTABLE_COMPONENT_SOURCES_FILE.read_text(encoding="utf-8")
    ) or {}
    if not isinstance(payload, dict):
        return {}

    modules = (((payload or {}).get("infra") or {}).get("tf_modules") or [])
    if not isinstance(modules, list):
        return {}

    overrides: dict[str, str] = {}
    for raw in modules:
        if not isinstance(raw, dict):
            continue
        component_id = str(raw.get("module", "")).strip().lower()
        source = str(raw.get("source", "")).strip()
        if not component_id or not source:
            continue
        overrides[component_id] = source
    return overrides


def _module_source_for_render_profile(
    *,
    component_id: str,
    module_source: str,
    module_version: str | None,
    render_profile: RenderProfile,
) -> str:
    source = module_source.strip()
    if render_profile == RenderProfile.LOCAL_DEV or not _source_uses_local_path(source):
        return source
    if module_version:
        raise ValueError(
            f"module source '{module_source}' resolves to a local directory, so version/ref "
            f"'{module_version}' is not supported. Use an explicit Git source like "
            "'git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3' if you need "
            "a pinned remote ref."
        )

    override = _portable_module_source_overrides().get(component_id)
    if not override:
        raise ValueError(
            f"infra component '{component_id}' resolves to local module source '{module_source}', "
            f"but render profile '{render_profile.value}' requires a portable Git or registry source. "
            "Point the active catalog at a portable source or rerun with --render-profile local-dev."
        )
    if not is_portable_module_source(override):
        raise ValueError(
            f"Portable source override for infra component '{component_id}' is still local: '{override}'"
        )
    return override


def _canonical_module_source(
    *,
    module_source: str,
    module_version: str | None,
) -> str:
    source = module_source.strip()
    if not source:
        return source

    explicit_ref = _normalize_git_ref(module_version)
    if source.startswith("git::"):
        return _module_source_with_ref(source, explicit_ref)
    if source.startswith(("http://", "https://", "oci://")):
        return source
    local_path = resolve_module_source_path(source)
    if local_path is not None:
        if explicit_ref:
            raise ValueError(
                f"module source '{module_source}' resolves to a local directory, so version/ref "
                f"'{module_version}' is not supported. Use an explicit Git source like "
                "'git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3' if you need "
                "a pinned remote ref."
            )
        return str(local_path)

    return source


def _is_registry_source(module_source: str) -> bool:
    source = module_source.strip()
    if not source:
        return False
    if source.startswith(("git::", "http://", "https://", "oci://", "./", "../", "/")):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source))


def _module_type_hints(module_source: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    for spec in module_variables(module_source):
        name = str(spec.name).strip().lower().replace("-", "_")
        type_hint = str(spec.type_hint or "").strip()
        if not name or not type_hint:
            continue
        hints[name] = type_hint
    return hints


def _infer_variable_type_expr(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "any"


def _variable_type_expr(*, type_hint: str | None, value: Any) -> str:
    normalized = str(type_hint or "").strip()
    if normalized and _ALLOWED_TERRAFORM_TYPE_TOKENS.fullmatch(normalized):
        return normalized
    return _infer_variable_type_expr(value)


def _build_module_plans(
    config: Any,
    *,
    render_profile: RenderProfile = RenderProfile.PORTABLE,
) -> tuple[_ModulePlan, ...]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ()

    plans: list[_ModulePlan] = []
    used_module_names: set[str] = set()
    used_variable_names: set[str] = set()

    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    all_entry_by_id = {
        entry.id: entry for entry in (*component_entries("infra"), *component_entries("apps"))
    }
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    prepared_rows: list[dict[str, Any]] = []
    module_name_by_component_id: dict[str, str] = {}
    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        entry = infra_entry_by_id.get(component_id)
        resolved_item = dict(item)
        if entry is not None and entry.defaults:
            resolved_item = resolve_component_defaults(
                payload=payload,
                component_node=resolved_item,
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
            )
        if entry is not None:
            conflicts = shared_default_conflicts(item, entry)
            if conflicts:
                target_path, source_path = conflicts[0]
                raise ValueError(
                    f"infra component '{component_id}' field '{target_path}' is managed by shared default "
                    f"'{source_path}' and must not be set explicitly"
                )
        inputs = resolved_item.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        source = _effective_component_source(row=resolved_item, entry=entry)
        version = _effective_component_version(row=resolved_item, entry=entry) or None

        mode = _normalize_module_mode(
            source=source,
            inputs=inputs,
            entry_origin=entry.origin if entry else None,
        )
        if mode != "custom":
            continue

        helper_keys = {
            "module_name",
            "depends_on_platform",
            "resource_type",
            "provider_resource_type",
        }
        module_inputs = {
            key: value
            for key, value in inputs.items()
            if str(key).strip().lower() not in helper_keys
        }
        default_source = str(entry.source).strip() if entry and entry.source else ""
        default_version = str(entry.version).strip() if entry and entry.version else ""
        raw_module_name = str(inputs.get("module_name") or component_id).strip()
        module_name_base = _safe_hcl_identifier(
            raw_module_name or component_id,
            fallback_prefix="module",
        )
        module_name = module_name_base
        counter = 2
        while module_name in used_module_names:
            module_name = f"{module_name_base}_{counter}"
            counter += 1
        used_module_names.add(module_name)

        module_source_raw = str(source or default_source).strip()
        module_version = version or default_version or None
        if not module_source_raw:
            raise ValueError(
                f"infra component '{component_id}' is enabled for module rendering but has no source"
            )
        declared_argument_names = {
            str(spec.name).strip().lower().replace("-", "_")
            for spec in module_variables(module_source_raw)
            if str(spec.name).strip()
        }
        if declared_argument_names:
            for raw_arg_name in sorted(module_inputs.keys()):
                argument_name = str(raw_arg_name).strip().replace("-", "_")
                normalized_argument_name = argument_name.lower()
                if normalized_argument_name not in declared_argument_names:
                    raise ValueError(
                        f"infra component '{component_id}' input '{raw_arg_name}' "
                        f"is not declared by module '{module_source_raw}'"
                    )
        selected_module_source = _module_source_for_render_profile(
            component_id=component_id,
            module_source=module_source_raw,
            module_version=module_version,
            render_profile=render_profile,
        )
        module_source = _canonical_module_source(
            module_source=selected_module_source,
            module_version=module_version,
        )

        prepared_rows.append(
            {
                "component_id": component_id,
                "entry": entry,
                "resolved_item": resolved_item,
                "module_name": module_name,
                "module_source": module_source,
                "module_version": module_version,
                "module_inputs": module_inputs,
                "type_hints": _module_type_hints(module_source_raw),
            }
        )
        module_name_by_component_id[component_id] = module_name

    for prepared in prepared_rows:
        component_id = str(prepared["component_id"])
        entry = prepared["entry"]
        resolved_item = prepared["resolved_item"]
        module_name = str(prepared["module_name"])
        module_source = str(prepared["module_source"])
        module_version = prepared["module_version"]
        module_inputs = dict(prepared["module_inputs"])
        type_hints = dict(prepared["type_hints"])

        if entry is not None and entry.input_bindings:
            conflicts = input_binding_conflicts(resolved_item, entry)
            if conflicts:
                target_path, source_ref = conflicts[0]
                raise ValueError(
                    f"infra component '{component_id}' field '{target_path}' is managed by component input "
                    f"binding '{source_ref}' and must not be set explicitly"
                )
            for binding in entry.input_bindings:
                source_entry = all_entry_by_id.get(binding.source_component_id)
                source_ref = component_output_ref(
                    binding.source_component_id,
                    binding.source_output_name,
                )
                if source_entry is None:
                    raise ValueError(
                        f"infra component '{component_id}' input binding '{binding.target_path}' references "
                        f"unknown component '{binding.source_component_id}'"
                    )
                source_output = output_lookup(source_entry).get(binding.source_output_name)
                if source_output is None:
                    raise ValueError(
                        f"infra component '{component_id}' input binding '{binding.target_path}' references "
                        f"undeclared output '{source_ref}'"
                    )

                static_value = resolve_static_component_output(
                    payload,
                    component_id=binding.source_component_id,
                    output_name=binding.source_output_name,
                )
                if static_value is not _UNRESOLVED:
                    set_component_path(module_inputs, binding.target_path, static_value)
                    continue

                if source_output.kind != "terraform_output":
                    raise ValueError(
                        f"infra component '{component_id}' input binding '{binding.target_path}' could not "
                        f"resolve output '{source_ref}' from the current config payload"
                    )
                if source_entry.scope != "infra":
                    raise ValueError(
                        f"infra component '{component_id}' input binding '{binding.target_path}' references "
                        f"Terraform output '{source_ref}' from non-infra component '{binding.source_component_id}'"
                    )
                source_module_name = module_name_by_component_id.get(binding.source_component_id)
                if not source_module_name:
                    raise ValueError(
                        f"infra component '{component_id}' input binding '{binding.target_path}' requires "
                        f"enabled infra component '{binding.source_component_id}'"
                    )
                set_component_path(
                    module_inputs,
                    binding.target_path,
                    _HclExpression(f"module.{source_module_name}.{source_output.source_path}"),
                )

        bindings: list[_VariableBinding] = []
        for raw_arg_name in sorted(module_inputs.keys()):
            argument_name = str(raw_arg_name).strip().replace("-", "_")
            if not _is_hcl_identifier(argument_name):
                raise ValueError(
                    f"infra component '{component_id}' input '{raw_arg_name}' is not a valid Terraform argument name"
                )
            arg_key = argument_name.lower().replace("-", "_")
            value = module_inputs[raw_arg_name]
            if _contains_hcl_expression(value):
                bindings.append(
                    _VariableBinding(
                        argument_name=argument_name,
                        variable_name=None,
                        type_expr=None,
                        value=None,
                        description=f"{component_id} module argument '{argument_name}'",
                        expression=_hcl_value(value, indent=4),
                    )
                )
                continue
            var_base = _safe_hcl_identifier(
                f"{module_name}_{arg_key}",
                fallback_prefix=f"{module_name}_input",
            )
            variable_name = var_base
            suffix = 2
            while variable_name in used_variable_names:
                variable_name = f"{var_base}_{suffix}"
                suffix += 1
            used_variable_names.add(variable_name)
            type_expr = _variable_type_expr(
                type_hint=type_hints.get(arg_key),
                value=value,
            )
            bindings.append(
                _VariableBinding(
                    argument_name=argument_name,
                    variable_name=variable_name,
                    type_expr=type_expr,
                    value=value,
                    description=f"{component_id} module argument '{argument_name}'",
                )
            )

        plans.append(
            _ModulePlan(
                component_id=component_id,
                module_name=module_name,
                module_source=module_source,
                module_version=module_version,
                bindings=tuple(bindings),
                outputs=entry.outputs if entry is not None else (),
                handoff=entry.handoff if entry is not None else None,
            )
        )

    return tuple(plans)


def _render_module_block(plan: _ModulePlan) -> str:
    lines = [
        f'module "{plan.module_name}" {{',
        f'  source = "{plan.module_source}"',
    ]
    if plan.module_version and _is_registry_source(plan.module_source):
        lines.append(f'  version = "{plan.module_version}"')
    for binding in plan.bindings:
        if binding.expression is not None:
            lines.append(f"  {binding.argument_name} = {binding.expression}")
            continue
        lines.append(f"  {binding.argument_name} = var.{binding.variable_name}")
    lines.append("}")
    return "\n".join(lines)


def rendered_module_sources(
    config: Any,
    *,
    render_profile: RenderProfile = RenderProfile.PORTABLE,
) -> tuple[RenderedModuleSource, ...]:
    return tuple(
        RenderedModuleSource(
            component_id=plan.component_id,
            module_name=plan.module_name,
            source=plan.module_source,
            portable=is_portable_module_source(plan.module_source),
        )
        for plan in _build_module_plans(config, render_profile=render_profile)
    )


def _render_provider_variable_blocks() -> tuple[str, ...]:
    specs = (
        (
            _PROVIDER_VAR_MODULE_NAME,
            "string",
            "Nebius provider module_name for API traceability",
            DEFAULT_PROVIDER_MODULE_NAME,
            False,
        ),
        (
            _PROVIDER_VAR_PARENT_ID,
            "string",
            "Nebius provider parent_id (project scope for created resources)",
            "",
            False,
        ),
        (
            _PROVIDER_VAR_SA_ID,
            "string",
            "Nebius service account ID used by Terraform provider auth",
            "",
            True,
        ),
        (
            _PROVIDER_VAR_AUTH_PUBLIC_KEY_ID,
            "string",
            "Nebius authorized key ID used by Terraform provider auth",
            "",
            True,
        ),
        (
            _PROVIDER_VAR_AUTH_PRIVATE_KEY_FILE,
            "string",
            "Path to Nebius authorized private key PEM used by Terraform provider auth",
            "",
            True,
        ),
        (
            _PROVIDER_VAR_CREDENTIALS_FILE,
            "string",
            "Optional Nebius provider credentials_file path (overrides service_account fields)",
            "",
            True,
        ),
    )

    blocks: list[str] = []
    for name, type_expr, description, default, sensitive in specs:
        lines = [
            f'variable "{name}" {{',
            f"  type = {type_expr}",
            f"  description = {json.dumps(description)}",
        ]
        if default is not None:
            lines.append(f"  default = {json.dumps(default)}")
        if sensitive:
            lines.append("  sensitive = true")
        lines.append("}")
        blocks.append("\n".join(lines))
    return tuple(blocks)


def _render_variables_tf(plans: tuple[_ModulePlan, ...]) -> str:
    blocks: list[str] = list(_render_provider_variable_blocks())
    for plan in plans:
        for binding in plan.bindings:
            if binding.variable_name is None or binding.type_expr is None:
                continue
            lines = [
                f'variable "{binding.variable_name}" {{',
                f"  type = {binding.type_expr}",
                f"  description = {json.dumps(binding.description)}",
                "}",
            ]
            blocks.append("\n".join(lines))
    if not blocks:
        return "# No module input variables were generated\n"
    return "\n\n".join(blocks) + "\n"


def _render_tfvars_json(plans: tuple[_ModulePlan, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for plan in plans:
        for binding in plan.bindings:
            if binding.variable_name is None:
                continue
            payload[binding.variable_name] = binding.value
    return payload


def _handoff_output_name(plan: _ModulePlan) -> str:
    return component_output_root_name(
        plan.component_id,
        plan.handoff.cluster_id_output_name,
    )


def _render_outputs_tf(plans: tuple[_ModulePlan, ...]) -> str:
    blocks: list[str] = []
    for plan in plans:
        output_by_name = {output.name: output for output in plan.outputs}
        for output in plan.outputs:
            if output.kind != "terraform_output":
                continue
            description = f"Exported output '{output.name}' from component '{plan.component_id}'"
            if (
                plan.handoff is not None
                and output.name == plan.handoff.cluster_id_output_name
            ):
                description = "Cluster ID used for kubeconfig handoff during deploy/bootstrap flows"
            blocks.append(
                "\n".join(
                    [
                        f'output "{component_output_root_name(plan.component_id, output.name)}" {{',
                        f"  description = {json.dumps(description)}",
                        f"  value       = module.{plan.module_name}.{output.source_path}",
                        *(
                            ["  sensitive   = true"]
                            if output.sensitive
                            else []
                        ),
                        "}",
                    ]
                )
            )
        if plan.handoff is not None and plan.handoff.cluster_id_output_name not in output_by_name:
            raise ValueError(
                f"infra component '{plan.component_id}' handoff.cluster_id "
                f"'{plan.handoff.cluster_id_output_name}' is not declared under outputs"
            )
    if not blocks:
        return "# No Terraform outputs were generated\n"
    return "\n\n".join(blocks) + "\n"


def _provider_static_tfvars(config: Any) -> dict[str, Any]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return {
            _PROVIDER_VAR_MODULE_NAME: DEFAULT_PROVIDER_MODULE_NAME,
            _PROVIDER_VAR_PARENT_ID: "",
        }

    client_info = payload.get("client_info")
    if not isinstance(client_info, dict):
        client_info = {}
    nebius = client_info.get("nebius")
    if not isinstance(nebius, dict):
        nebius = {}

    client_name = str(client_info.get("client_name") or "").strip()
    project_id = str(nebius.get("project_id") or "").strip()

    return {
        _PROVIDER_VAR_MODULE_NAME: build_provider_module_name(
            client_name=client_name,
            project_id=project_id,
        ),
        _PROVIDER_VAR_PARENT_ID: project_id,
    }


def render_terraform_artifacts(
    config: Any,
    paths: InstancePaths,
    *,
    render_profile: RenderProfile = RenderProfile.PORTABLE,
) -> list[Path]:
    """Render deterministic Terraform root-module artifacts for one validated config."""
    written: list[Path] = []

    backend_settings = backend_settings_from_config(config)
    module_plans = _build_module_plans(config, render_profile=render_profile)
    module_blocks = tuple(_render_module_block(plan) for plan in module_plans)
    provider_resource_blocks = _provider_resource_blocks(config)
    tfvars_payload = {
        **_provider_static_tfvars(config),
        **_render_tfvars_json(module_plans),
    }

    backend_tf_path = paths.infra_dir / "backend.tf"
    _write_text(backend_tf_path, render_backend_tf(backend_settings))
    written.append(backend_tf_path)

    versions_tf_path = paths.infra_dir / "versions.tf"
    _write_text(versions_tf_path, _terraform_versions_block())
    written.append(versions_tf_path)

    providers_tf_path = paths.infra_dir / "providers.tf"
    _write_text(providers_tf_path, _terraform_providers_block())
    written.append(providers_tf_path)

    variables_tf_path = paths.infra_dir / "variables.tf"
    _write_text(variables_tf_path, _render_variables_tf(module_plans))
    written.append(variables_tf_path)

    main_tf_path = paths.infra_dir / "main.tf"
    _write_text(
        main_tf_path,
        _terraform_main_block(
            module_blocks=module_blocks,
            provider_resource_blocks=provider_resource_blocks,
        ),
    )
    written.append(main_tf_path)

    outputs_tf_path = paths.infra_dir / "outputs.tf"
    _write_text(outputs_tf_path, _render_outputs_tf(module_plans))
    written.append(outputs_tf_path)

    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    _write_text(tfvars_path, json.dumps(tfvars_payload, indent=2, sort_keys=True) + "\n")
    written.append(tfvars_path)

    return written
