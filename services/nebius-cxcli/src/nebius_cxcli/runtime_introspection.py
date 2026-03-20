"""Runtime introspection helpers for provider/module/chart-backed prompting."""

from __future__ import annotations

import ast
import atexit
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .component_sources import resolve_component_sources_file
from .helm_client import HelmChartReference, HelmClient
from .managed_tools import resolve_terraform_binary

_MODULE_PROBE_ROOTS: set[Path] = set()


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


def resolve_module_source_path(module_source: str) -> Path | None:
    source = module_source.strip()
    if not source:
        return None
    if source.startswith(("git::", "http://", "https://", "oci://")):
        return None
    candidate = Path(source)
    if candidate.is_absolute() and candidate.exists() and candidate.is_dir():
        return candidate

    roots: list[Path] = []
    with contextlib.suppress(ValueError):
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
        resolved = (root / source).resolve()
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def _supported_git_module_source_example() -> str:
    return "git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3"


def _first_non_empty_line(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return None


def _normalized_module_source_for_probe(module_source: str) -> tuple[str | None, str | None]:
    source = str(module_source).strip()
    if not source:
        return None, None

    path = resolve_module_source_path(source)
    if path is not None:
        return str(path), None

    lowered = source.lower()
    if lowered.startswith("git::"):
        return source, None

    if lowered.startswith("oci://"):
        return (
            None,
            f"module source '{source}' uses unsupported Terraform module source prefix 'oci://'. "
            "Supported Terraform module source formats here are: relative local path, absolute local path, "
            f"or Git source address like '{_supported_git_module_source_example()}'.",
        )

    explicit_local_path = Path(source).expanduser()
    if explicit_local_path.is_absolute() or source.startswith(("./", "../", "~/")):
        return None, f"module source '{source}' does not resolve to an existing local directory"

    if lowered.startswith(("http://", "https://")):
        return (
            None,
            f"module source '{source}' is not supported as a plain HTTP(S) Terraform module source. "
            "nebius-cxcli supports Terraform modules from local filesystem paths or Git repository "
            f"source addresses only. Use the Git format '{_supported_git_module_source_example()}' "
            "instead of a raw https:// module URL.",
        )

    return (
        None,
        f"module source '{source}' is not supported. "
        "Supported Terraform module source formats here are: relative local path, absolute local path, "
        f"or Git source address like '{_supported_git_module_source_example()}'.",
    )


def _register_module_probe_root(path: Path) -> None:
    _MODULE_PROBE_ROOTS.add(path)


def _cleanup_module_probe_roots() -> None:
    for root in tuple(_MODULE_PROBE_ROOTS):
        shutil.rmtree(root, ignore_errors=True)
        _MODULE_PROBE_ROOTS.discard(root)


@atexit.register
def _cleanup_module_probe_roots_at_exit() -> None:
    _cleanup_module_probe_roots()


def _module_dir_from_probe_manifest(tmp_root: Path) -> Path | None:
    manifest_path = tmp_root / ".terraform" / "modules" / "modules.json"
    if manifest_path.exists() and manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        modules = payload.get("Modules", []) if isinstance(payload, dict) else []
        if isinstance(modules, list):
            for module in modules:
                if not isinstance(module, dict):
                    continue
                if str(module.get("Key", "")).strip() != "probe":
                    continue
                dir_token = str(module.get("Dir", "")).strip()
                if not dir_token:
                    continue
                candidate = Path(dir_token)
                if not candidate.is_absolute():
                    candidate = (tmp_root / candidate).resolve()
                if candidate.exists() and candidate.is_dir():
                    return candidate

    fallback = tmp_root / ".terraform" / "modules" / "probe"
    if fallback.exists() and fallback.is_dir():
        return fallback
    return None


@lru_cache(maxsize=64)
def _module_inspection_path(module_source: str) -> tuple[str | None, str | None]:
    source = str(module_source).strip()
    if not source:
        return None, None

    local_path = resolve_module_source_path(source)
    if local_path is not None:
        return str(local_path), None

    probe_source, source_error = _normalized_module_source_for_probe(source)
    if source_error:
        return None, source_error
    if not probe_source:
        return None, None

    try:
        terraform_bin = resolve_terraform_binary()
    except Exception as exc:
        return (
            None,
            f"terraform is required to inspect module source '{source}': {exc}"
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-module-probe-"))
    _register_module_probe_root(tmp_root)
    try:
        probe_config = tmp_root / "main.tf"
        probe_config.write_text(
            "\n".join(
                [
                    'module "probe" {',
                    f"  source = {json.dumps(probe_source)}",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [terraform_bin, "init", "-backend=false", "-input=false", "-no-color"],
            cwd=tmp_root,
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "TF_IN_AUTOMATION": "1"},
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            f"terraform init timed out while validating module source '{source}'. "
            "Confirm the source address, credentials, and network reachability."
        )
    except Exception as exc:
        return None, f"terraform init could not validate module source '{source}': {exc}"

    if result.returncode == 0:
        module_dir = _module_dir_from_probe_manifest(tmp_root)
        if module_dir is not None:
            return str(module_dir), None
        return (
            None,
            f"terraform init succeeded for module source '{source}', but nebius-cxcli could not locate "
            "the downloaded module directory for output/variable inspection.",
        )

    failure_line = _first_non_empty_line(result.stderr or result.stdout or "")
    if failure_line:
        return (
            None,
            f"terraform init failed for module source '{source}': {failure_line}. "
            "Confirm the source address, credentials, and pinned ref. "
            "For local modules, run `terraform init -backend=false` and `terraform validate` in the module directory."
        )
    return (
        None,
        f"terraform init failed for module source '{source}'. "
        "Confirm the source address, credentials, and pinned ref."
    )


@dataclass(frozen=True)
class ModuleVariable:
    name: str
    required: bool
    type_hint: str | None = None
    description: str | None = None
    has_default: bool = False
    default: Any = None
    nullable: bool | None = None


@dataclass(frozen=True)
class ModuleOutput:
    name: str
    sensitive: bool = False
    description: str | None = None


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


def _terraform_config_inspect_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not shutil.which("terraform-config-inspect"):
        return None, None
    try:
        result = subprocess.run(
            ["terraform-config-inspect", "-json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None, None
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip() or None
        return None, error_text

    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = yaml.safe_load(result.stdout) or {}
    if not isinstance(payload, dict):
        return None, "terraform-config-inspect returned a non-mapping payload"
    return payload, None


def _module_variables_from_config_inspect(path: Path) -> tuple[ModuleVariable, ...] | None:
    payload, error = _terraform_config_inspect_payload(path)
    if payload is None or error is not None:
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


def _module_outputs_from_config_inspect(path: Path) -> tuple[ModuleOutput, ...] | None:
    payload, error = _terraform_config_inspect_payload(path)
    if payload is None or error is not None:
        return None

    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        return ()

    collected: dict[str, ModuleOutput] = {}
    for raw_name, meta in outputs.items():
        name = str(raw_name).strip()
        if not name:
            continue
        sensitive = bool(meta.get("sensitive")) if isinstance(meta, dict) else False
        description = _normalize_description(meta.get("description")) if isinstance(meta, dict) else None
        existing = collected.get(name)
        if existing is None:
            collected[name] = ModuleOutput(
                name=name,
                sensitive=sensitive,
                description=description,
            )
            continue
        collected[name] = ModuleOutput(
            name=name,
            sensitive=existing.sensitive or sensitive,
            description=existing.description or description,
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


def _module_outputs_from_tf_files(path: Path) -> tuple[ModuleOutput, ...]:
    output_block_pattern = re.compile(r'output\s+"([^"]+)"\s*\{', re.MULTILINE)
    sensitive_pattern = re.compile(r"(^|\n)\s*sensitive\s*=\s*(.+)", re.MULTILINE)
    description_pattern = re.compile(r'(^|\n)\s*description\s*=\s*"([^"]*)"', re.MULTILINE)

    discovered: dict[str, ModuleOutput] = {}
    for tf_file in sorted(path.glob("*.tf")):
        try:
            text = tf_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in output_block_pattern.finditer(text):
            name = str(match.group(1) or "").strip()
            if not name:
                continue
            open_brace_index = text.find("{", match.start(), match.end())
            if open_brace_index < 0:
                continue
            block = _extract_braced_block(text, open_brace_index)
            if block is None:
                continue
            sensitive = False
            sensitive_match = sensitive_pattern.search(block)
            if sensitive_match:
                sensitive_token = str(sensitive_match.group(2)).strip().lower().rstrip(",")
                sensitive = sensitive_token == "true"
            description_match = description_pattern.search(block)
            description = _normalize_description(
                description_match.group(2) if description_match else None
            )
            existing = discovered.get(name)
            if existing is None:
                discovered[name] = ModuleOutput(
                    name=name,
                    sensitive=sensitive,
                    description=description,
                )
                continue
            discovered[name] = ModuleOutput(
                name=name,
                sensitive=existing.sensitive or sensitive,
                description=existing.description or description,
            )
    return tuple(discovered[name] for name in sorted(discovered))


@lru_cache(maxsize=64)
def module_variables(module_source: str) -> tuple[ModuleVariable, ...]:
    path_text, _issue = _module_inspection_path(module_source)
    if not path_text:
        return ()
    path = Path(path_text)

    inspected = _module_variables_from_config_inspect(path)
    if inspected is not None:
        return inspected

    return _module_variables_from_tf_files(path)


@lru_cache(maxsize=64)
def module_outputs(module_source: str) -> tuple[ModuleOutput, ...]:
    path_text, _issue = _module_inspection_path(module_source)
    if not path_text:
        return ()
    path = Path(path_text)

    inspected = _module_outputs_from_config_inspect(path)
    if inspected is not None:
        return inspected

    return _module_outputs_from_tf_files(path)


@lru_cache(maxsize=64)
def module_output_names(module_source: str) -> tuple[str, ...]:
    return tuple(output.name for output in module_outputs(module_source))


@lru_cache(maxsize=64)
def module_source_validation_issues(module_source: str) -> tuple[str, ...]:
    source = str(module_source).strip()
    if not source:
        return ()

    path_text, inspection_issue = _module_inspection_path(source)
    if inspection_issue:
        return (inspection_issue,)
    path = Path(path_text) if path_text else None
    issues: list[str] = []
    if path is not None:
        tf_files = tuple(path.glob("*.tf"))
        if not tf_files:
            return (f"module source '{source}' has no Terraform .tf files",)

        _payload, inspect_error = _terraform_config_inspect_payload(path)
        if inspect_error:
            first_line = _first_non_empty_line(inspect_error)
            if first_line:
                issues.append(
                    f"terraform-config-inspect failed for module source '{source}': {first_line}"
                )
            else:
                issues.append(f"terraform-config-inspect failed for module source '{source}'")

    return tuple(issues)


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


def reset_runtime_introspection_cache() -> None:
    _module_inspection_path.cache_clear()
    module_variables.cache_clear()
    module_outputs.cache_clear()
    module_output_names.cache_clear()
    module_source_validation_issues.cache_clear()
    module_variable_names.cache_clear()
    module_required_variables.cache_clear()
    helm_chart_default_values.cache_clear()
    _cleanup_module_probe_roots()


def merge_chart_defaults_with_overrides(
    *,
    chart_defaults: dict[str, Any],
    current_values: dict[str, Any],
) -> dict[str, Any]:
    return _deep_merge(chart_defaults, current_values)
