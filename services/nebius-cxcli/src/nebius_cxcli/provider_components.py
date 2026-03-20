"""Terraform provider-driven infrastructure component discovery helpers."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .managed_tools import resolve_terraform_binary
from .templates import NEBIUS_PROVIDER_SOURCE, NEBIUS_PROVIDER_VERSION


@dataclass(frozen=True)
class ProviderResource:
    name: str
    namespace: str
    category: str


_CATEGORY_BY_NAMESPACE: dict[str, str] = {
    "compute": "Compute",
    "mk8s": "Compute",
    "applications": "Compute",
    "storage": "Storage",
    "msp": "Storage",
    "mysterybox": "Security",
    "iam": "IAM",
    "vpc": "Network",
    "quotas": "IAM",
    "registry": "Storage",
}


def _resource_category(namespace: str) -> str:
    return _CATEGORY_BY_NAMESPACE.get(namespace, "Other")


def _provider_source() -> str:
    override = os.environ.get("NEBIUS_CXCLI_INFRA_PROVIDER_SOURCE", "").strip()
    if override:
        return override
    return NEBIUS_PROVIDER_SOURCE


def _provider_version() -> str:
    override = os.environ.get("NEBIUS_CXCLI_INFRA_PROVIDER_VERSION", "").strip()
    if override:
        return override
    return NEBIUS_PROVIDER_VERSION


def _provider_resource_prefix() -> str:
    override = os.environ.get("NEBIUS_CXCLI_INFRA_RESOURCE_PREFIX", "").strip()
    if override:
        return override
    return "nebius_"


def _provider_schema_payload(timeout_seconds: int = 60) -> dict:
    try:
        terraform_bin = resolve_terraform_binary()
    except Exception:
        return {}
    provider_source = _provider_source()
    provider_version = _provider_version()
    if not provider_source or not provider_version:
        return {}

    timeout_override = os.environ.get(
        "NEBIUS_CXCLI_PROVIDER_SCHEMA_TIMEOUT_SECONDS", ""
    ).strip()
    if timeout_override:
        try:
            timeout_seconds = max(1, int(timeout_override))
        except ValueError:
            timeout_seconds = 10
    else:
        timeout_seconds = 10

    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-provider-") as tmp:
        tmp_path = Path(tmp)
        tf_main = tmp_path / "main.tf"
        tf_main.write_text(
            (
                "terraform {\n"
                "  required_providers {\n"
                "    nebius = {\n"
                f'      source  = "{provider_source}"\n'
                f'      version = "{provider_version}"\n'
                "    }\n"
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        init = subprocess.run(
            [terraform_bin, "init", "-backend=false", "-input=false", "-no-color"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if init.returncode != 0:
            return {}

        schema = subprocess.run(
            [terraform_bin, "providers", "schema", "-json"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if schema.returncode != 0:
            return {}
        try:
            return json.loads(schema.stdout)
        except Exception:
            return {}


def _collect_required_leaf_names_from_block(block: Any) -> set[str]:
    required: set[str] = set()
    if not isinstance(block, dict):
        return required
    attributes = block.get("attributes", {})
    if isinstance(attributes, dict):
        for name, meta in attributes.items():
            if not isinstance(name, str) or not isinstance(meta, dict):
                continue
            if bool(meta.get("required")):
                required.add(name)
    block_types = block.get("block_types", {})
    if isinstance(block_types, dict):
        for nested_name, nested_meta in block_types.items():
            if not isinstance(nested_name, str) or not isinstance(nested_meta, dict):
                continue
            nested_block = nested_meta.get("block")
            nested_required = _collect_required_leaf_names_from_block(nested_block)
            required.update(nested_required)
            if bool(nested_meta.get("min_items")):
                required.add(nested_name)
    return required


@lru_cache(maxsize=1)
def discover_nebius_provider_resources() -> tuple[ProviderResource, ...]:
    payload = _provider_schema_payload()
    provider_schemas = payload.get("provider_schemas", {})
    if not isinstance(provider_schemas, dict):
        return ()

    provider_source = _provider_source()
    provider_resource_prefix = _provider_resource_prefix()
    provider_key = next(
        (
            key
            for key in provider_schemas
            if isinstance(key, str) and (key.endswith("/nebius/nebius") or provider_source in key)
        ),
        None,
    )
    if provider_key is None:
        provider_key = next(
            (key for key in provider_schemas if isinstance(key, str)),
            None,
        )
        if provider_key is None:
            return ()

    schema = provider_schemas.get(provider_key, {})
    resource_schemas = (
        schema.get("resource_schemas", {}) if isinstance(schema, dict) else {}
    )
    if not isinstance(resource_schemas, dict):
        return ()

    resources: list[ProviderResource] = []
    for resource_name in sorted(resource_schemas):
        if not isinstance(resource_name, str):
            continue
        if provider_resource_prefix and not resource_name.startswith(provider_resource_prefix):
            continue
        segments = resource_name.split("_")
        if len(segments) < 2:
            continue
        namespace = segments[1]
        resources.append(
            ProviderResource(
                name=resource_name,
                namespace=namespace,
                category=_resource_category(namespace),
            )
        )
    return tuple(resources)


@lru_cache(maxsize=1)
def discover_provider_resource_required_leaf_names() -> dict[str, set[str]]:
    payload = _provider_schema_payload()
    provider_schemas = payload.get("provider_schemas", {})
    if not isinstance(provider_schemas, dict):
        return {}

    provider_source = _provider_source()
    provider_resource_prefix = _provider_resource_prefix()
    provider_key = next(
        (
            key
            for key in provider_schemas
            if isinstance(key, str) and (key.endswith("/nebius/nebius") or provider_source in key)
        ),
        None,
    )
    if provider_key is None:
        provider_key = next((key for key in provider_schemas if isinstance(key, str)), None)
        if provider_key is None:
            return {}

    schema = provider_schemas.get(provider_key, {})
    resource_schemas = (
        schema.get("resource_schemas", {}) if isinstance(schema, dict) else {}
    )
    if not isinstance(resource_schemas, dict):
        return {}

    result: dict[str, set[str]] = {}
    for resource_name, resource_schema in resource_schemas.items():
        if not isinstance(resource_name, str) or not isinstance(resource_schema, dict):
            continue
        if provider_resource_prefix and not resource_name.startswith(provider_resource_prefix):
            continue
        block = resource_schema.get("block")
        result[resource_name] = _collect_required_leaf_names_from_block(block)
    return result


def required_provider_leaf_names_for_component(component_id: str) -> set[str]:
    component = component_id.strip().lower()
    if not component:
        return set()
    required_by_resource = discover_provider_resource_required_leaf_names()
    required: set[str] = set()
    for resource_name, names in required_by_resource.items():
        if _component_matches_provider_resource(component, resource_name.lower()):
            required.update(name.lower() for name in names)
    return required


def required_provider_leaf_names_for_resource(resource_name: str) -> set[str]:
    token = resource_name.strip().lower()
    if not token:
        return set()
    required_by_resource = discover_provider_resource_required_leaf_names()
    for known_name, names in required_by_resource.items():
        if known_name.strip().lower() == token:
            return {name.lower() for name in names}
    return set()


def provider_resource_name_for_component(component_id: str) -> str | None:
    component = component_id.strip().lower()
    if not component:
        return None
    resources = discover_nebius_provider_resources()
    if not resources:
        return None
    matches = [
        resource.name
        for resource in resources
        if _component_matches_provider_resource(component, resource.name.lower())
    ]
    if not matches:
        return None
    return sorted(matches)[0]


def provider_resource_exists(resource_name: str) -> bool | None:
    token = resource_name.strip().lower()
    if not token:
        return False
    resources = discover_nebius_provider_resources()
    if not resources:
        return None
    return any(resource.name.strip().lower() == token for resource in resources)


def reset_provider_component_cache() -> None:
    """Reset cached provider schema discovery."""
    discover_nebius_provider_resources.cache_clear()
    discover_provider_resource_required_leaf_names.cache_clear()


def infer_infra_component_category(component_id: str) -> str:
    component = component_id.strip().lower()
    if not component:
        return "Other"

    resources = discover_nebius_provider_resources()
    if not resources:
        return _fallback_component_category(component)

    tokens = set(component.replace("_", "-").split("-"))
    alias_tokens = set(tokens)
    if "sfs" in tokens:
        alias_tokens.add("filesystem")
    if "object" in tokens:
        alias_tokens.add("bucket")
    if "postgresql" in tokens:
        alias_tokens.add("msp")
    if "mk8s" in tokens:
        alias_tokens.add("cluster")
    if "jumphost" in tokens:
        alias_tokens.add("instance")

    category_scores: dict[str, int] = {}
    for resource in resources:
        resource_name = resource.name.lower()
        if any(token and token in resource_name for token in alias_tokens):
            category_scores[resource.category] = category_scores.get(resource.category, 0) + 1

    if category_scores:
        return sorted(
            category_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[0][0]
    return _fallback_component_category(component)


def _component_matches_provider_resource(component_id: str, resource_name: str) -> bool:
    component_tokens = set(component_id.replace("_", "-").split("-"))
    resource_tokens = set(resource_name.replace("_", "-").split("-"))
    if component_tokens <= resource_tokens:
        return True

    alias_groups: tuple[set[str], ...] = (
        {"mk8s", "k8s", "cluster", "clusters"},
        {"postgresql", "msp", "postgres"},
        {"object", "storage", "bucket", "buckets"},
        {"sfs", "filesystem", "mounted", "fs"},
        {"wireguard", "wg"},
        {"jumphost", "jump", "host", "instance"},
    )
    return any(component_tokens & group and resource_tokens & group for group in alias_groups)


def infer_infra_component_origin(component_id: str) -> str:
    resources = discover_nebius_provider_resources()
    if not resources:
        return _fallback_component_origin(component_id)

    component = component_id.strip().lower()
    if not component:
        return "custom"
    for resource in resources:
        if _component_matches_provider_resource(component, resource.name.lower()):
            return "provider"
    return _fallback_component_origin(component)


def _fallback_component_category(component_id: str) -> str:
    tokens = set(component_id.replace("_", "-").split("-"))
    if {"mk8s", "wireguard", "ssh", "jumphost"} & tokens:
        return "Compute"
    if {"object", "storage", "sfs", "postgresql"} & tokens:
        return "Storage"
    if {"mysterybox"} & tokens:
        return "Security"
    return "Other"


def _fallback_component_origin(component_id: str) -> str:
    tokens = set(component_id.replace("_", "-").split("-"))
    provider_like = {
        "mk8s",
        "postgresql",
        "object",
        "storage",
        "sfs",
        "mysterybox",
    }
    if tokens & provider_like:
        return "provider"
    return "custom"


def provider_component_match_status(component_id: str) -> bool | None:
    """Return provider schema match status for a component id.

    Returns:
    - ``True`` when provider schemas are available and at least one resource matches.
    - ``False`` when provider schemas are available and no resource matches.
    - ``None`` when provider schemas are unavailable (for example terraform/provider init failed).
    """

    resources = discover_nebius_provider_resources()
    if not resources:
        return None

    component = component_id.strip().lower()
    if not component:
        return False
    for resource in resources:
        if _component_matches_provider_resource(component, resource.name.lower()):
            return True
    return False
