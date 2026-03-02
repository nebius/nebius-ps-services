"""Deterministic generic Terraform artifact rendering helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .components import component_entries
from .paths import InstancePaths
from .provider_components import provider_resource_name_for_component
from .runtime_config import to_plain_data
from .templates import NEBIUS_PROVIDER_SOURCE, NEBIUS_PROVIDER_VERSION


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _terraform_provider_only_block() -> str:
    return (
        "terraform {\n"
        "  required_providers {\n"
        "    nebius = {\n"
        f'      source  = "{NEBIUS_PROVIDER_SOURCE}"\n'
        f'      version = "{NEBIUS_PROVIDER_VERSION}"\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "nebius" {\n'
        "  service_account = {\n"
        '    account_id_env       = "NEBIUS_SA_ID"\n'
        '    public_key_id_env    = "NEBIUS_AUTH_PUBLIC_KEY_ID"\n'
        '    private_key_file_env = "NEBIUS_AUTH_PRIVATE_KEY_FILE"\n'
        "  }\n"
        "}\n"
    )


def _terraform_module_only_main_block(
    *,
    custom_infra_module_blocks: tuple[str, ...] = (),
    provider_resource_blocks: tuple[str, ...] = (),
) -> str:
    lines: list[str] = []
    for resource_block in provider_resource_blocks:
        if resource_block:
            lines.extend([resource_block, ""])
    for module_block in custom_infra_module_blocks:
        if module_block:
            lines.extend([module_block, ""])
    return "\n".join(lines).strip() + "\n"


def _json_hcl_string(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


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


def _render_custom_infra_module_block(
    *,
    module_name: str,
    module_source: str,
    module_version: str | None,
    depends_on_platform: bool,
    inputs: dict[str, Any],
) -> str:
    lines = [
        f'module "{module_name}" {{',
        f'  source = "{module_source}"',
    ]
    if module_version:
        lines.append(f'  version = "{module_version}"')
    if depends_on_platform:
        lines.append("  depends_on = [module.customer_platform]")
    lines.append(f"  inputs = jsondecode({_json_hcl_string(json.dumps(inputs, sort_keys=True))})")
    lines.append("}")
    return "\n".join(lines)


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
        inputs = item.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        source = str(item.get("source", "")).strip()

        entry = entry_by_id.get(component_id)
        if source:
            mode = "custom"
        elif any(key in inputs for key in ("resource_type", "provider_resource_type")) or (
            entry is not None and entry.origin == "provider"
        ):
            mode = "provider"
        else:
            mode = "custom"

        if mode != "provider":
            continue
        rows.append(
            {
                "id": component_id,
                "source": source,
                "inputs": inputs,
            }
        )
    return tuple(rows)


def _render_dynamic_provider_resource_block(
    *,
    component_id: str,
    resource_type: str,
    resource_name: str,
    depends_on_platform: bool,
    attributes: dict[str, Any],
) -> str:
    lines = [
        f'resource "{resource_type}" "{resource_name}" {{',
    ]
    if depends_on_platform:
        lines.append("  depends_on = [module.customer_platform]")
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
        depends_on_platform = bool(inputs.get("depends_on_platform", True))

        blocks.append(
            _render_dynamic_provider_resource_block(
                component_id=component_id,
                resource_type=resource_type,
                resource_name=resource_name,
                depends_on_platform=depends_on_platform,
                attributes=attributes,
            )
        )
    return tuple(blocks)


def _custom_infra_module_blocks(config: Any) -> tuple[str, ...]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ()

    blocks: list[str] = []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        inputs = item.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        source = str(item.get("source", "")).strip()
        version = str(item.get("version", "")).strip()

        entry = entry_by_id.get(component_id)
        if source:
            mode = "custom"
        elif any(key in inputs for key in ("resource_type", "provider_resource_type")) or (
            entry is not None and entry.origin == "provider"
        ):
            mode = "provider"
        else:
            mode = "custom"
        if mode != "custom":
            continue

        default_source = str(entry.source).strip() if entry and entry.source else ""
        default_version = str(entry.version).strip() if entry and entry.version else ""
        module_name = str(inputs.get("module_name") or f"custom-{component_id}").strip()
        module_source = str(source or default_source).strip()
        module_version = str(version or default_version).strip() or None
        depends_on_platform = bool(inputs.get("depends_on_platform", False))
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

        if not module_name:
            module_name = f"custom-{component_id}"
        if not module_source:
            raise ValueError(
                f"infra component '{component_id}' is enabled for module rendering but has no source"
            )
        if not isinstance(module_inputs, dict):
            raise ValueError(
                f"infra component '{component_id}' module inputs must be a mapping"
            )

        blocks.append(
            _render_custom_infra_module_block(
                module_name=module_name,
                module_source=module_source,
                module_version=module_version,
                depends_on_platform=depends_on_platform,
                inputs=module_inputs,
            )
        )

    return tuple(blocks)


def render_terraform_artifacts(config: Any, paths: InstancePaths) -> list[Path]:
    """Render deterministic Terraform artifacts for one validated config."""
    written: list[Path] = []

    custom_infra_module_blocks = _custom_infra_module_blocks(config)
    provider_resource_blocks = _provider_resource_blocks(config)

    terraform_tf_path = paths.infra_dir / "terraform.tf"
    _write_text(terraform_tf_path, _terraform_provider_only_block())
    written.append(terraform_tf_path)

    main_tf_path = paths.infra_dir / "main.tf"
    _write_text(
        main_tf_path,
        _terraform_module_only_main_block(
            custom_infra_module_blocks=custom_infra_module_blocks,
            provider_resource_blocks=provider_resource_blocks,
        ),
    )
    written.append(main_tf_path)

    tfvars_payload: dict[str, Any] = {}
    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    _write_text(tfvars_path, json.dumps(tfvars_payload, indent=2, sort_keys=True) + "\n")
    written.append(tfvars_path)

    return written
