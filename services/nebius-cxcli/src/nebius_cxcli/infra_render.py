"""Deterministic Terraform root-module rendering helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cluster_handoffs import Handoff
from .component_defaults import (
    component_path_has_material_value,
    read_component_path,
    resolve_component_defaults,
    set_component_path,
)
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .component_sources import (
    ComponentOutput,
    SourceProfile,
    component_input_binding_ref,
    component_output_root_name,
)
from .component_wiring import (
    _UNRESOLVED,
    component_output_ref,
    input_binding_conflicts,
    output_lookup,
    resolve_component_output_value,
    resolve_input_binding_source,
    row_input_bindings,
)
from .components import ComponentEntry, component_entries
from .deploy_targets import app_chart_target_ref
from .paths import ProjectPaths
from .runtime_config import to_plain_data
from .runtime_introspection import (
    canonical_local_module_source,
    module_variables,
    resolve_module_source_path,
)
from .templates import NEBIUS_PROVIDER_SOURCE, NEBIUS_PROVIDER_VERSION
from .terraform_backend import backend_settings_from_config, render_backend_tf
from .terraform_provider import DEFAULT_PROVIDER_MODULE_NAME, build_provider_module_name

DEFAULT_TERRAFORM_REQUIRED_VERSION = ">= 1.10.0"
_ALLOWED_TERRAFORM_TYPE_TOKENS = re.compile(r'^[A-Za-z0-9_(),.{}\[\]="\s]+$')
_ALLOWED_GIT_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")
_LOCAL_SOURCE_PATTERN = re.compile(r"^(?:\.\.?/|/|~/|[A-Za-z]:[\\/])")
_PROVIDER_VAR_MODULE_NAME = "nebius_provider_module_name"
_PROVIDER_VAR_PARENT_ID = "nebius_provider_parent_id"
_PROVIDER_VAR_SA_ID = "nebius_service_account_id"
_PROVIDER_VAR_AUTH_PUBLIC_KEY_ID = "nebius_auth_public_key_id"
_PROVIDER_VAR_AUTH_PRIVATE_KEY_FILE = "nebius_auth_private_key_file"
_PROVIDER_VAR_CREDENTIALS_FILE = "nebius_service_account_credentials_file"
_NO_VARIABLE_DEFAULT = object()
_RUNTIME_ONLY_MODULE_ARGUMENTS = frozenset({"payload_values"})
_MYSTERYBOX_CXCLI_ONLY_SECRET_KEYS = frozenset({"eso_version_policy", "kubernetes_secret_name"})


@dataclass(frozen=True)
class _VariableBinding:
    argument_name: str
    variable_name: str | None
    type_expr: str | None
    value: Any | None
    description: str
    expression: str | None = None
    default: Any = _NO_VARIABLE_DEFAULT
    sensitive: bool = False
    include_in_tfvars: bool = True


@dataclass(frozen=True)
class _HclExpression:
    expression: str


@dataclass(frozen=True)
class _ModulePlan:
    component_id: str
    instance_id: str
    module_name: str
    module_source: str
    module_version: str | None
    bindings: tuple[_VariableBinding, ...]
    outputs: tuple[ComponentOutput, ...] = ()
    handoff: Handoff | None = None


@dataclass(frozen=True)
class RenderedModuleSource:
    component_id: str
    instance_id: str
    module_name: str
    source: str


def _declared_wizard_input_root_keys(entry: ComponentEntry) -> set[str]:
    roots: set[str] = set()
    for raw_path in entry.wizard_fields:
        field_path = str(raw_path).strip()
        if not field_path:
            continue
        if field_path.startswith("inputs."):
            relative = field_path[len("inputs.") :]
        elif field_path.startswith(f"{entry.config_path}.inputs."):
            relative = field_path[len(f"{entry.config_path}.inputs.") :]
        else:
            continue
        if "." not in relative:
            continue
        root = relative.split(".", maxsplit=1)[0].strip().replace("-", "_").lower()
        if root:
            roots.add(root)
    return roots


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
) -> str:
    lines: list[str] = []
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


def _hcl_literal_string(value: str) -> str:
    return json.dumps(value.replace("${", "$${").replace("%{", "%%{"))


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
        return _hcl_literal_string(value)
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
            lines.append(" " * indent + f"{map_key} = {_hcl_value(value[key], indent=indent + 2)}")
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


def _effective_component_source(*, row: dict[str, Any], entry: ComponentEntry | None) -> str:
    if entry is not None and str(entry.source or "").strip():
        return str(entry.source).strip()
    return str(row.get("source", "")).strip()


def _effective_component_version(*, row: dict[str, Any], entry: ComponentEntry | None) -> str:
    if entry is not None and str(entry.version or "").strip():
        return str(entry.version).strip()
    return str(row.get("version", "")).strip()


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


def _module_source_for_profile(
    *,
    component_id: str,
    module_source: str,
    module_version: str | None,
    source_profile: SourceProfile,
) -> str:
    source = module_source.strip()
    if source_profile == SourceProfile.LOCAL or not _source_uses_local_path(source):
        return source
    if module_version:
        raise ValueError(
            f"module source '{module_source}' resolves to a local directory, so version/ref "
            f"'{module_version}' is not supported. Use an explicit Git source like "
            "'git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3' if you need "
            "a pinned remote ref."
        )

    raise ValueError(
        f"infra component '{component_id}' resolves to local module source '{module_source}', "
        f"but source profile '{source_profile.value}' requires a portable Git or registry source. "
        "Set source.portable in component_sources.yaml or rerun with --source-profile local."
    )


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
    local_source = canonical_local_module_source(source)
    if local_source is not None:
        if explicit_ref:
            raise ValueError(
                f"module source '{module_source}' resolves to a local directory, so version/ref "
                f"'{module_version}' is not supported. Use an explicit Git source like "
                "'git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3' if you need "
                "a pinned remote ref."
            )
        return local_source

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


def _runtime_only_module_argument_names(declared_argument_names: set[str]) -> set[str]:
    return {name for name in _RUNTIME_ONLY_MODULE_ARGUMENTS if name in declared_argument_names}


def _module_inputs_for_terraform(
    component_id: str, module_inputs: dict[str, Any]
) -> dict[str, Any]:
    if component_id != "mysterybox":
        return module_inputs
    secrets = module_inputs.get("secrets")
    if not isinstance(secrets, list):
        return module_inputs
    cleaned_secrets: list[Any] = []
    changed = False
    for item in secrets:
        if not isinstance(item, dict):
            cleaned_secrets.append(item)
            continue
        cleaned = {
            key: value
            for key, value in item.items()
            if str(key).strip().replace("-", "_").lower() not in _MYSTERYBOX_CXCLI_ONLY_SECRET_KEYS
        }
        if cleaned != item:
            changed = True
        cleaned_secrets.append(cleaned)
    if not changed:
        return module_inputs
    result = dict(module_inputs)
    result["secrets"] = cleaned_secrets
    return result


def _binding_module_input_path(target_path: str) -> str:
    path = str(target_path or "").strip()
    if path.startswith("inputs."):
        return path[len("inputs.") :]
    return path


def _reserved_variable_name(
    base: str,
    *,
    used_variable_names: set[str],
    fallback_prefix: str,
) -> str:
    var_base = _safe_hcl_identifier(base, fallback_prefix=fallback_prefix)
    variable_name = var_base
    suffix = 2
    while variable_name in used_variable_names:
        variable_name = f"{var_base}_{suffix}"
        suffix += 1
    used_variable_names.add(variable_name)
    return variable_name


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


def _enabled_soperator_target_refs(payload: dict[str, Any]) -> set[str]:
    apps = payload.get("apps")
    charts = apps.get("charts") if isinstance(apps, dict) else None
    if not isinstance(charts, list):
        return set()
    refs: set[str] = set()
    for row in charts:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != "soperator":
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_ref:
            refs.add(target_ref)
    return refs


def _enabled_infra_instance_ids(payload: dict[str, Any], component_id: str) -> tuple[str, ...]:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return ()
    instance_ids: list[str] = []
    for row in components:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != component_id:
            continue
        instance_id = component_instance_id(row)
        if instance_id:
            instance_ids.append(instance_id)
    return tuple(instance_ids)


def _enabled_mk8s_target_refs(payload: dict[str, Any]) -> tuple[str, ...]:
    return _enabled_infra_instance_ids(payload, "mk8s")


def _sfs_module_name_for_target(
    payload: dict[str, Any],
    *,
    target_ref: str,
    sfs_module_name_by_instance_id: dict[str, str],
) -> str:
    sfs_instance_ids = _enabled_infra_instance_ids(payload, "sfs")
    if target_ref in sfs_instance_ids:
        return sfs_module_name_by_instance_id.get(target_ref, "")
    if len(sfs_instance_ids) == 1 and len(_enabled_mk8s_target_refs(payload)) <= 1:
        return sfs_module_name_by_instance_id.get(sfs_instance_ids[0], "")
    return ""


def _sfs_filesystem_attachments_expr(
    module_name: str,
    filesystem_keys: list[str],
    *,
    attach_mode: str = "READ_WRITE",
) -> _HclExpression:
    keys_expr = json.dumps(filesystem_keys)
    return _HclExpression(
        "\n".join(
            [
                f"[for key in {keys_expr} : {{",
                f"  attach_mode = {json.dumps(attach_mode)}",
                f"  mount_tag = module.{module_name}.filesystems[key].mount_tag",
                "  existing_filesystem = {",
                f"    id = module.{module_name}.filesystems[key].id",
                "  }",
                "}]",
            ]
        )
    )


def _binding_selected_static_value(
    *,
    component_label: str,
    binding_ref: str,
    binding: Any,
    value: Any,
) -> Any:
    if not binding.key and not binding.attribute:
        return value
    if binding.key is None or binding.attribute is None:
        raise ValueError(
            f"infra component '{component_label}' input binding '{binding.target_path}' "
            f"uses incomplete selector for output '{binding_ref}'"
        )
    if not isinstance(value, dict):
        raise ValueError(
            f"infra component '{component_label}' input binding '{binding.target_path}' "
            f"requires map/object output '{binding_ref}'"
        )
    if binding.key not in value:
        raise ValueError(
            f"infra component '{component_label}' input binding '{binding.target_path}' "
            f"references missing key '{binding.key}' on output '{binding_ref}'"
        )
    selected = value[binding.key]
    if not isinstance(selected, dict):
        raise ValueError(
            f"infra component '{component_label}' input binding '{binding.target_path}' "
            f"requires object value at '{binding_ref}.{binding.key}'"
        )
    if binding.attribute not in selected:
        raise ValueError(
            f"infra component '{component_label}' input binding '{binding.target_path}' "
            f"references missing attribute '{binding.attribute}' on output '{binding_ref}.{binding.key}'"
        )
    return selected[binding.attribute]


def _binding_output_expression(
    *, module_name: str, source_path: str, binding: Any
) -> _HclExpression:
    expression = f"module.{module_name}.{source_path}"
    if binding.key:
        expression = f"{expression}[{json.dumps(binding.key)}]"
    if binding.attribute:
        expression = f"{expression}.{binding.attribute}"
    return _HclExpression(expression)


def _node_group_sfs_filesystem_keys(group_key: str, group: dict[str, Any]) -> list[str]:
    filesystem_keys: list[str] = []
    workload = str(group.get("workload") or group.get("placement_name") or group_key).strip()
    if bool(group.get("jail", False)):
        filesystem_keys.append("jail")
    if workload == "controller" or group_key == "controller":
        filesystem_keys.extend(["jail", "controller-spool"])
    if workload == "accounting" or group_key == "accounting":
        filesystem_keys.extend(["jail", "accounting"])
    explicit_keys = group.get("sfs_filesystem_keys", group.get("filesystem_keys"))
    if isinstance(explicit_keys, str):
        filesystem_keys.extend(key.strip() for key in explicit_keys.split(",") if key.strip())
    elif isinstance(explicit_keys, list):
        filesystem_keys.extend(str(key).strip() for key in explicit_keys if str(key).strip())
    return filesystem_keys


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _vm_sfs_attachments_value(
    *,
    attachments: Any,
    sfs_module_name_by_instance_id: dict[str, str],
) -> Any:
    if not isinstance(attachments, list):
        return None

    literal_items: list[dict[str, Any]] = []
    expression_lists: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        attach_mode = str(attachment.get("attach_mode") or "READ_WRITE").strip() or "READ_WRITE"
        source_instance = str(attachment.get("source_instance") or "").strip().lower()
        keys = _string_list(attachment.get("keys") or attachment.get("filesystem_keys"))
        if source_instance:
            module_name = sfs_module_name_by_instance_id.get(source_instance)
            if not module_name:
                raise ValueError(
                    f"VM sfs_attachments references missing infra:sfs@{source_instance}"
                )
            if not keys:
                raise ValueError(
                    f"VM sfs_attachments for infra:sfs@{source_instance} must set keys"
                )
            expression_lists.append(
                _sfs_filesystem_attachments_expr(
                    module_name,
                    list(dict.fromkeys(keys)),
                    attach_mode=attach_mode,
                ).expression
            )
            continue

        filesystem_id = str(attachment.get("id") or attachment.get("existing_id") or "").strip()
        existing_filesystem = attachment.get("existing_filesystem")
        if not filesystem_id and isinstance(existing_filesystem, dict):
            filesystem_id = str(existing_filesystem.get("id") or "").strip()
        mount_tag = str(attachment.get("mount_tag") or "").strip()
        if not filesystem_id or not mount_tag:
            raise ValueError(
                "VM sfs_attachments entries must set source_instance+keys or literal id+mount_tag"
            )
        literal_items.append(
            {
                "attach_mode": attach_mode,
                "mount_tag": mount_tag,
                "existing_filesystem": {"id": filesystem_id},
            }
        )

    if not expression_lists:
        return literal_items or None
    if literal_items:
        expression_lists.insert(0, _hcl_value(literal_items, indent=2))
    if len(expression_lists) == 1:
        return _HclExpression(expression_lists[0])
    return _HclExpression(f"concat({', '.join(expression_lists)})")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _materialize_mk8s_sfs_attachments(
    *,
    payload: dict[str, Any],
    component_id: str,
    instance_id: str,
    module_inputs: dict[str, Any],
    sfs_module_name_by_instance_id: dict[str, str],
) -> None:
    if component_id != "mk8s":
        return
    sfs_module_name = _sfs_module_name_for_target(
        payload,
        target_ref=instance_id,
        sfs_module_name_by_instance_id=sfs_module_name_by_instance_id,
    )
    if not sfs_module_name:
        return

    node_groups = module_inputs.get("node_groups")
    if isinstance(node_groups, dict):
        for key, group in node_groups.items():
            if not isinstance(group, dict) or "filesystems" in group:
                continue
            filesystem_keys = _node_group_sfs_filesystem_keys(str(key), group)
            if (
                instance_id not in _enabled_soperator_target_refs(payload)
                and not group.get("sfs_filesystem_keys")
                and not group.get("filesystem_keys")
            ):
                continue
            if filesystem_keys:
                group["filesystems"] = _sfs_filesystem_attachments_expr(
                    sfs_module_name,
                    list(dict.fromkeys(filesystem_keys)),
                )


def _build_module_plans(
    config: Any,
    *,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> tuple[_ModulePlan, ...]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ()

    plans: list[_ModulePlan] = []
    used_module_names: set[str] = set()
    used_variable_names: set[str] = set()

    infra_entry_by_id = {
        entry.id: entry for entry in component_entries("infra", source_profile=source_profile)
    }
    all_entry_by_id = {
        entry.id: entry
        for entry in (
            *component_entries("infra", source_profile=source_profile),
            *component_entries("apps", source_profile=source_profile),
        )
    }
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    prepared_rows: list[dict[str, Any]] = []
    module_name_by_component_instance: dict[tuple[str, str], str] = {}
    sfs_module_name_by_instance_id: dict[str, str] = {}
    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        instance_id = component_instance_id(item)
        if not instance_id:
            continue
        component_label = component_instance_label(component_id, instance_id)
        entry = infra_entry_by_id.get(component_id)
        resolved_item = dict(item)
        if entry is not None and entry.defaults:
            resolved_item = resolve_component_defaults(
                payload=payload,
                component_node=resolved_item,
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
                include_shared=False,
            )
        inputs = resolved_item.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        source = _effective_component_source(row=resolved_item, entry=entry)
        version = _effective_component_version(row=resolved_item, entry=entry) or None

        default_source = str(entry.source).strip() if entry and entry.source else ""
        default_version = str(entry.version).strip() if entry and entry.version else ""
        raw_module_name = str(inputs.get("module_name") or instance_id).strip()
        module_name_base = _safe_hcl_identifier(
            raw_module_name or instance_id,
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
                f"infra component '{component_label}' is enabled for module rendering but has no source"
            )
        metadata_module_source = (
            str(entry.metadata_source or "").strip() if entry is not None else ""
        ) or module_source_raw
        declared_argument_names = {
            str(spec.name).strip().lower().replace("-", "_")
            for spec in module_variables(metadata_module_source)
            if str(spec.name).strip()
        }
        runtime_only_argument_names = _runtime_only_module_argument_names(declared_argument_names)
        wizard_input_roots = _declared_wizard_input_root_keys(entry) if entry is not None else set()
        helper_keys = {
            "module_name",
            *({"sfs_attachments"} if component_id == "vm" else set()),
            *runtime_only_argument_names,
            *(root for root in wizard_input_roots if root not in declared_argument_names),
        }
        for raw_arg_name in sorted(inputs):
            normalized_argument_name = str(raw_arg_name).strip().replace("-", "_").lower()
            if normalized_argument_name not in runtime_only_argument_names:
                continue
            runtime_variable_name = _safe_hcl_identifier(
                f"{module_name}_{normalized_argument_name}",
                fallback_prefix=f"{module_name}_input",
            )
            raise ValueError(
                f"infra component '{component_label}' input '{raw_arg_name}' is runtime-only "
                f"and must not be stored in config.yaml. Provide it at deploy time with "
                f"TF_VAR_{runtime_variable_name}."
            )
        module_inputs = {
            key: value
            for key, value in inputs.items()
            if str(key).strip().replace("-", "_").lower() not in helper_keys
        }
        module_inputs = _module_inputs_for_terraform(component_id, module_inputs)
        if declared_argument_names:
            for raw_arg_name in sorted(module_inputs.keys()):
                argument_name = str(raw_arg_name).strip().replace("-", "_")
                normalized_argument_name = argument_name.lower()
                if normalized_argument_name not in declared_argument_names:
                    raise ValueError(
                        f"infra component '{component_label}' input '{raw_arg_name}' "
                        f"is not declared by module '{module_source_raw}'"
                    )
        selected_module_source = _module_source_for_profile(
            component_id=instance_id,
            module_source=module_source_raw,
            module_version=module_version,
            source_profile=source_profile,
        )
        module_source = _canonical_module_source(
            module_source=selected_module_source,
            module_version=module_version,
        )

        prepared_rows.append(
            {
                "component_id": component_id,
                "instance_id": instance_id,
                "entry": entry,
                "resolved_item": resolved_item,
                "module_name": module_name,
                "module_source": module_source,
                "module_version": module_version,
                "module_inputs": module_inputs,
                "cxcli_sfs_attachments": inputs.get("sfs_attachments")
                if component_id == "vm"
                else None,
                "type_hints": _module_type_hints(metadata_module_source),
                "runtime_only_argument_names": runtime_only_argument_names,
            }
        )
        module_name_by_component_instance[(component_id, instance_id)] = module_name
        if component_id == "sfs":
            sfs_module_name_by_instance_id[instance_id] = module_name

    for prepared in prepared_rows:
        component_id = str(prepared["component_id"])
        instance_id = str(prepared["instance_id"])
        component_label = component_instance_label(component_id, instance_id)
        entry = prepared["entry"]
        resolved_item = prepared["resolved_item"]
        module_name = str(prepared["module_name"])
        module_source = str(prepared["module_source"])
        module_version = prepared["module_version"]
        module_inputs = dict(prepared["module_inputs"])
        cxcli_sfs_attachments = prepared["cxcli_sfs_attachments"]
        type_hints = dict(prepared["type_hints"])
        runtime_only_argument_names = set(prepared["runtime_only_argument_names"])

        catalog_bindings = tuple(entry.input_bindings) if entry is not None else ()
        if catalog_bindings:
            conflicts = input_binding_conflicts(resolved_item, entry)
            if conflicts:
                target_path, source_ref = conflicts[0]
                raise ValueError(
                    f"infra component '{component_label}' field '{target_path}' is managed by component input "
                    f"binding '{source_ref}' and must not be set explicitly"
                )
        row_bindings = row_input_bindings(
            resolved_item,
            field_label=f"infra component '{component_label}'",
        )
        for binding in row_bindings:
            existing_value = read_component_path(resolved_item, binding.target_path)
            if component_path_has_material_value(existing_value):
                raise ValueError(
                    f"infra component '{component_label}' field '{binding.target_path}' conflicts with "
                    "row-level binding and must not be set explicitly"
                )

        for binding in (*catalog_bindings, *row_bindings):
            declared_source_ref = component_input_binding_ref(binding)
            source_entry = all_entry_by_id.get(binding.source_component_id)
            if source_entry is None:
                raise ValueError(
                    f"infra component '{component_label}' input binding '{binding.target_path}' references "
                    f"unknown component '{binding.source_component_id}'"
                )
            source_output = output_lookup(source_entry).get(binding.source_output_name)
            if source_output is None:
                raise ValueError(
                    f"infra component '{component_label}' input binding '{binding.target_path}' references "
                    f"undeclared output '{declared_source_ref}'"
                )
            _resolved_source_entry, _resolved_source_row, source_instance_id = (
                resolve_input_binding_source(payload, binding=binding)
            )
            source_ref = (
                component_output_ref(source_instance_id, binding.source_output_name)
                if source_instance_id
                else declared_source_ref
            )

            static_value = resolve_component_output_value(
                payload,
                component_id=binding.source_component_id,
                output_name=binding.source_output_name,
                instance_id=source_instance_id or binding.source_instance_id,
            )
            module_target_path = _binding_module_input_path(binding.target_path)
            if static_value is not _UNRESOLVED:
                set_component_path(
                    module_inputs,
                    module_target_path,
                    _binding_selected_static_value(
                        component_label=component_label,
                        binding_ref=source_ref,
                        binding=binding,
                        value=static_value,
                    ),
                )
                continue

            if source_output.kind != "terraform_output":
                raise ValueError(
                    f"infra component '{component_label}' input binding '{binding.target_path}' could not "
                    f"resolve output '{source_ref}' "
                    "from the current config payload"
                )
            if source_entry.scope != "infra":
                raise ValueError(
                    f"infra component '{component_label}' input binding '{binding.target_path}' references "
                    f"Terraform output '{source_ref}' "
                    f"from non-infra component '{binding.source_component_id}'"
                )
            source_module_name = module_name_by_component_instance.get(
                (binding.source_component_id, source_instance_id)
            )
            if not source_module_name:
                raise ValueError(
                    f"infra component '{component_label}' input binding '{binding.target_path}' requires "
                    f"enabled infra component matching '{declared_source_ref}'"
                )
            set_component_path(
                module_inputs,
                module_target_path,
                _binding_output_expression(
                    module_name=source_module_name,
                    source_path=source_output.source_path,
                    binding=binding,
                ),
            )

        if component_id == "vm" and cxcli_sfs_attachments is not None:
            if "filesystems" in module_inputs:
                raise ValueError(
                    f"infra component '{component_label}' cannot set both filesystems and sfs_attachments"
                )
            filesystems_value = _vm_sfs_attachments_value(
                attachments=cxcli_sfs_attachments,
                sfs_module_name_by_instance_id=sfs_module_name_by_instance_id,
            )
            if filesystems_value is not None:
                module_inputs["filesystems"] = filesystems_value

        _materialize_mk8s_sfs_attachments(
            payload=payload,
            component_id=component_id,
            instance_id=instance_id,
            module_inputs=module_inputs,
            sfs_module_name_by_instance_id=sfs_module_name_by_instance_id,
        )

        bindings: list[_VariableBinding] = []
        for raw_arg_name in sorted(module_inputs.keys()):
            argument_name = str(raw_arg_name).strip().replace("-", "_")
            if not _is_hcl_identifier(argument_name):
                raise ValueError(
                    f"infra component '{component_label}' input '{raw_arg_name}' is not a valid Terraform argument name"
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
                        description=f"{component_label} module argument '{argument_name}'",
                        expression=_hcl_value(value, indent=4),
                    )
                )
                continue
            variable_name = _reserved_variable_name(
                f"{module_name}_{arg_key}",
                used_variable_names=used_variable_names,
                fallback_prefix=f"{module_name}_input",
            )
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
                    description=f"{component_label} module argument '{argument_name}'",
                )
            )
        for argument_name in sorted(runtime_only_argument_names):
            if not _is_hcl_identifier(argument_name):
                continue
            variable_name = _reserved_variable_name(
                f"{module_name}_{argument_name}",
                used_variable_names=used_variable_names,
                fallback_prefix=f"{module_name}_input",
            )
            type_expr = _variable_type_expr(
                type_hint=type_hints.get(argument_name),
                value={},
            )
            bindings.append(
                _VariableBinding(
                    argument_name=argument_name,
                    variable_name=variable_name,
                    type_expr=type_expr,
                    value=None,
                    description=f"{component_label} module runtime-only argument '{argument_name}'",
                    default={},
                    sensitive=True,
                    include_in_tfvars=False,
                )
            )

        plans.append(
            _ModulePlan(
                component_id=component_id,
                instance_id=instance_id,
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
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> tuple[RenderedModuleSource, ...]:
    return tuple(
        RenderedModuleSource(
            component_id=plan.component_id,
            instance_id=plan.instance_id,
            module_name=plan.module_name,
            source=plan.module_source,
        )
        for plan in _build_module_plans(config, source_profile=source_profile)
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
            f"  type        = {type_expr}",
            f"  description = {json.dumps(description)}",
        ]
        if default is not None:
            lines.append(f"  default     = {json.dumps(default)}")
        if sensitive:
            lines.append("  sensitive   = true")
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
                f"  type        = {binding.type_expr}",
                f"  description = {json.dumps(binding.description)}",
            ]
            if binding.default is not _NO_VARIABLE_DEFAULT:
                lines.append(f"  default     = {_hcl_value(binding.default, indent=2)}")
            if binding.sensitive:
                lines.append("  sensitive   = true")
            lines.append("}")
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
            if not binding.include_in_tfvars:
                continue
            payload[binding.variable_name] = binding.value
    return payload


def _handoff_output_name(plan: _ModulePlan) -> str:
    return component_output_root_name(
        plan.instance_id,
        plan.handoff.cluster_id_output_name,
    )


def _render_outputs_tf(plans: tuple[_ModulePlan, ...]) -> str:
    blocks: list[str] = []
    for plan in plans:
        output_by_name = {output.name: output for output in plan.outputs}
        for output in plan.outputs:
            if output.kind != "terraform_output":
                continue
            description = (
                f"Exported output '{output.name}' from component "
                f"'{component_instance_label(plan.component_id, plan.instance_id)}'"
            )
            if plan.handoff is not None and output.name == plan.handoff.cluster_id_output_name:
                description = "Cluster ID used for kubeconfig handoff during deploy/bootstrap flows"
            blocks.append(
                "\n".join(
                    [
                        f'output "{component_output_root_name(plan.instance_id, output.name)}" {{',
                        f"  description = {json.dumps(description)}",
                        f"  value       = module.{plan.module_name}.{output.source_path}",
                        *(["  sensitive   = true"] if output.sensitive else []),
                        "}",
                    ]
                )
            )
        if plan.handoff is not None and plan.handoff.cluster_id_output_name not in output_by_name:
            raise ValueError(
                f"infra component '{component_instance_label(plan.component_id, plan.instance_id)}' "
                "cluster handoff requires Terraform output "
                f"'{plan.handoff.cluster_id_output_name}' to be exported by the module"
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
    paths: ProjectPaths,
    *,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> list[Path]:
    """Render deterministic Terraform root-module artifacts for one validated config."""
    written: list[Path] = []

    backend_settings = backend_settings_from_config(config)
    module_plans = _build_module_plans(config, source_profile=source_profile)
    module_blocks = tuple(_render_module_block(plan) for plan in module_plans)
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
