"""Runtime introspection helpers for provider/module/chart-backed prompting."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .helm_client import HelmChartReference, HelmClient


def _deep_copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _deep_copy(item) for key, item in value.items()}
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_copy(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
            continue
        merged[key] = _deep_copy(value)
    return merged


def _resolve_module_source_path(module_source: str) -> Path | None:
    source = module_source.strip()
    if not source:
        return None
    if source.startswith(("git::", "http://", "https://", "oci://")):
        return None
    candidate = Path(source)
    if candidate.is_absolute() and candidate.exists() and candidate.is_dir():
        return candidate

    roots = [
        Path.cwd(),
        Path(__file__).resolve().parents[1],
        Path(__file__).resolve().parents[2],
        Path(__file__).resolve().parents[3],
    ]
    for root in roots:
        resolved = (root / source).resolve()
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


@dataclass(frozen=True)
class ModuleVariable:
    name: str
    required: bool
    type_hint: str | None = None
    description: str | None = None
    has_default: bool = False
    default: Any = None
    nullable: bool | None = None


def _normalize_variable_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _normalize_type_hint(type_hint: object | None) -> str | None:
    if type_hint is None:
        return None
    text = str(type_hint).strip()
    return text or None


def _normalize_description(text: object | None) -> str | None:
    if text is None:
        return None
    value = str(text).strip()
    return value or None


def _parse_hcl_default(raw: str) -> Any:
    token = raw.strip().rstrip(",")
    if not token:
        return None
    lowered = token.lower()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in token:
            return float(token)
        return int(token)
    except ValueError:
        pass
    try:
        return ast.literal_eval(token)
    except Exception:
        return token


def _module_variables_from_config_inspect(path: Path) -> tuple[ModuleVariable, ...] | None:
    if not shutil.which("terraform-config-inspect"):
        return None
    try:
        result = subprocess.run(
            ["terraform-config-inspect", "-json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    payload = yaml.safe_load(result.stdout) or {}
    if not isinstance(payload, dict):
        return None
    variables = payload.get("variables", {})
    if not isinstance(variables, dict):
        return None

    collected: dict[str, ModuleVariable] = {}
    for raw_name, meta in variables.items():
        if not isinstance(raw_name, str):
            continue
        name = _normalize_variable_name(raw_name)
        if not name:
            continue
        required = bool(meta.get("required")) if isinstance(meta, dict) else False
        type_hint = _normalize_type_hint(meta.get("type")) if isinstance(meta, dict) else None
        description = _normalize_description(meta.get("description")) if isinstance(meta, dict) else None
        has_default = isinstance(meta, dict) and "default" in meta
        default_value = _deep_copy(meta.get("default")) if has_default and isinstance(meta, dict) else None
        nullable_value: bool | None = None
        if isinstance(meta, dict) and isinstance(meta.get("nullable"), bool):
            nullable_value = bool(meta.get("nullable"))

        existing = collected.get(name)
        if existing is None:
            collected[name] = ModuleVariable(
                name=name,
                required=required,
                type_hint=type_hint,
                description=description,
                has_default=has_default,
                default=default_value,
                nullable=nullable_value,
            )
            continue
        collected[name] = ModuleVariable(
            name=name,
            required=existing.required or required,
            type_hint=existing.type_hint or type_hint,
            description=existing.description or description,
            has_default=existing.has_default or has_default,
            default=existing.default if existing.has_default else default_value,
            nullable=existing.nullable if existing.nullable is not None else nullable_value,
        )

    return tuple(collected[name] for name in sorted(collected))


def _extract_braced_block(text: str, open_brace_index: int) -> str | None:
    if open_brace_index < 0 or open_brace_index >= len(text):
        return None
    if text[open_brace_index] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(open_brace_index, len(text)):
        token = text[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if token == "\\":
                escaped = True
                continue
            if token == '"':
                in_string = False
            continue
        if token == '"':
            in_string = True
            continue
        if token == "{":
            depth += 1
            continue
        if token == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : index]
            continue
    return None


def _module_variables_from_tf_files(path: Path) -> tuple[ModuleVariable, ...]:
    variable_block_pattern = re.compile(r'variable\s+"([^"]+)"\s*\{', re.MULTILINE)
    default_pattern = re.compile(r"(^|\n)\s*default\s*=\s*(.+)", re.MULTILINE)
    type_pattern = re.compile(r"(^|\n)\s*type\s*=\s*(.+)", re.MULTILINE)
    nullable_pattern = re.compile(r"(^|\n)\s*nullable\s*=\s*(.+)", re.MULTILINE)
    description_pattern = re.compile(r'(^|\n)\s*description\s*=\s*"([^"]*)"', re.MULTILINE)

    discovered: dict[str, ModuleVariable] = {}
    for file_path in sorted(path.glob("*.tf")):
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in variable_block_pattern.finditer(text):
            name = _normalize_variable_name(match.group(1))
            if not name:
                continue
            open_brace_index = text.find("{", match.start(), match.end())
            if open_brace_index < 0:
                continue
            block = _extract_braced_block(text, open_brace_index)
            if block is None:
                continue
            default_match = default_pattern.search(block)
            has_default = default_match is not None
            default_value = _parse_hcl_default(default_match.group(2)) if default_match else None
            required = not has_default
            type_match = type_pattern.search(block)
            type_hint = _normalize_type_hint(type_match.group(2) if type_match else None)
            description_match = description_pattern.search(block)
            description = _normalize_description(
                description_match.group(2) if description_match else None
            )
            nullable_value: bool | None = None
            nullable_match = nullable_pattern.search(block)
            if nullable_match:
                nullable_token = str(nullable_match.group(2)).strip().lower().rstrip(",")
                if nullable_token in {"true", "false"}:
                    nullable_value = nullable_token == "true"

            existing = discovered.get(name)
            if existing is None:
                discovered[name] = ModuleVariable(
                    name=name,
                    required=required,
                    type_hint=type_hint,
                    description=description,
                    has_default=has_default,
                    default=default_value,
                    nullable=nullable_value,
                )
                continue
            discovered[name] = ModuleVariable(
                name=name,
                required=existing.required or required,
                type_hint=existing.type_hint or type_hint,
                description=existing.description or description,
                has_default=existing.has_default or has_default,
                default=existing.default if existing.has_default else default_value,
                nullable=existing.nullable if existing.nullable is not None else nullable_value,
            )

    return tuple(discovered[name] for name in sorted(discovered))


@lru_cache(maxsize=64)
def module_variables(module_source: str) -> tuple[ModuleVariable, ...]:
    path = _resolve_module_source_path(module_source)
    if path is None:
        return ()

    inspected = _module_variables_from_config_inspect(path)
    if inspected is not None:
        return inspected

    return _module_variables_from_tf_files(path)


@lru_cache(maxsize=64)
def module_variable_names(module_source: str) -> tuple[str, ...]:
    return tuple(variable.name for variable in module_variables(module_source))


@lru_cache(maxsize=64)
def module_required_variables(module_source: str) -> tuple[str, ...]:
    return tuple(variable.name for variable in module_variables(module_source) if variable.required)


@lru_cache(maxsize=64)
def helm_chart_default_values(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
) -> dict[str, Any]:
    try:
        client = HelmClient()
        payload = client.show_values(
            reference=HelmChartReference(
                chart_name=chart_name_or_ref,
                chart_repo=chart_repo,
                chart_version=chart_version,
            )
        )
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return _deep_copy(payload)


def merge_chart_defaults_with_overrides(
    *,
    chart_defaults: dict[str, Any],
    current_values: dict[str, Any],
) -> dict[str, Any]:
    return _deep_merge(chart_defaults, current_values)
