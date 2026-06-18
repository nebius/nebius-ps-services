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
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .component_sources import resolve_component_sources_file
from .helm_client import HelmChartReference, HelmClient
from .managed_tools import resolve_terraform_binary

_MODULE_PROBE_ROOTS: set[Path] = set()


def _split_local_package_source(source: str) -> tuple[str, str] | None:
    if "://" in source or source.startswith("//"):
        return None
    marker = source.find("//")
    if marker <= 0:
        return None
    package_source = source[:marker].strip()
    module_subdir = source[marker + 2 :].strip("/")
    if not package_source or not module_subdir:
        return None
    return package_source, module_subdir


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
    if source.startswith("file://"):
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme == "file" and parsed.path:
            candidate = Path(urllib.parse.unquote(parsed.path))
            if candidate.is_absolute() and candidate.exists() and candidate.is_dir():
                return candidate
        return None

    split_source = _split_local_package_source(source)
    package_source = split_source[0] if split_source is not None else source
    module_subdir = split_source[1] if split_source is not None else ""

    candidate = Path(package_source)
    if candidate.is_absolute():
        module_path = (candidate / module_subdir).resolve() if module_subdir else candidate
        if module_path.exists() and module_path.is_dir():
            return module_path

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
        resolved_package = (root / package_source).resolve()
        resolved_module = (
            (resolved_package / module_subdir).resolve() if module_subdir else resolved_package
        )
        if resolved_module.exists() and resolved_module.is_dir():
            return resolved_module
    return None


def canonical_local_module_source(module_source: str) -> str | None:
    source = module_source.strip()
    split_source = _split_local_package_source(source)
    if split_source is None:
        path = resolve_module_source_path(source)
        return str(path) if path is not None else None

    package_source, module_subdir = split_source
    package_path = resolve_module_source_path(package_source)
    if package_path is None:
        return None
    module_path = (package_path / module_subdir).resolve()
    if not module_path.exists() or not module_path.is_dir():
        return None
    return f"{package_path}//{module_subdir}"


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


def _git_module_source_subdir(source: str) -> str:
    raw = str(source).strip()
    if raw.lower().startswith("git::"):
        raw = raw[5:]
    raw = raw.split("?", 1)[0]
    search_from = 0
    scheme_marker = raw.find("://")
    if scheme_marker >= 0:
        search_from = scheme_marker + 3
    subdir_marker = raw.find("//", search_from)
    if subdir_marker < 0:
        return ""
    return raw[subdir_marker + 2 :].strip("/")


def _module_dir_from_probe_manifest(tmp_root: Path, module_source: str = "") -> Path | None:
    manifest_path = tmp_root / ".terraform" / "modules" / "modules.json"
    if manifest_path.exists() and manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
    module_subdir = _git_module_source_subdir(module_source)
    if module_subdir:
        fallback_subdir = fallback / module_subdir
        if fallback_subdir.exists() and fallback_subdir.is_dir():
            return fallback_subdir
    if fallback.exists() and fallback.is_dir():
        return fallback
    return None


def _terraform_init_failed_only_for_missing_required_probe_args(output: str) -> bool:
    error_titles = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("Error:")
    ]
    return bool(error_titles) and all(
        title == "Error: Missing required argument" for title in error_titles
    )


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
    except (OSError, RuntimeError, ValueError) as exc:
        return (None, f"terraform is required to inspect module source '{source}': {exc}")

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
            "Confirm the source address, credentials, and network reachability.",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"terraform init could not validate module source '{source}': {exc}"

    if result.returncode == 0:
        module_dir = _module_dir_from_probe_manifest(tmp_root, probe_source)
        if module_dir is not None:
            return str(module_dir), None
        return (
            None,
            f"terraform init succeeded for module source '{source}', but nebius-cxcli could not locate "
            "the downloaded module directory for output/variable inspection.",
        )

    combined_output = result.stderr or result.stdout or ""
    if _terraform_init_failed_only_for_missing_required_probe_args(combined_output):
        module_dir = _module_dir_from_probe_manifest(tmp_root, probe_source)
        if module_dir is not None:
            return str(module_dir), None

    failure_line = _first_non_empty_line(combined_output)
    if failure_line:
        return (
            None,
            f"terraform init failed for module source '{source}': {failure_line}. "
            "Confirm the source address, credentials, and pinned ref. "
            "For local modules, run `terraform init -backend=false` and `terraform validate` in the module directory.",
        )
    return (
        None,
        f"terraform init failed for module source '{source}'. "
        "Confirm the source address, credentials, and pinned ref.",
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
    if token.startswith("{") and token.endswith("}"):
        parsed_mapping = _parse_hcl_mapping_default(token)
        if parsed_mapping is not None:
            return parsed_mapping
    if token.startswith("[") and token.endswith("]"):
        yaml_candidate = _hcl_default_to_yaml_candidate(token)
        with contextlib.suppress(Exception):
            return yaml.safe_load(yaml_candidate)
    try:
        return ast.literal_eval(token)
    except (SyntaxError, ValueError):
        return token


def _top_level_assignment_index(token: str) -> int | None:
    in_string = False
    escaped = False
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    for index, char in enumerate(token):
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            brace_depth += 1
            continue
        if char == "}":
            brace_depth = max(brace_depth - 1, 0)
            continue
        if char == "[":
            bracket_depth += 1
            continue
        if char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
            continue
        if char == "(":
            paren_depth += 1
            continue
        if char == ")":
            paren_depth = max(paren_depth - 1, 0)
            continue
        if (
            char == "="
            and not in_string
            and brace_depth == 0
            and bracket_depth == 0
            and paren_depth == 0
        ):
            return index
    return None


def _split_top_level_hcl_items(token: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    for char in token:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            current.append(char)
            continue
        if char == "{":
            brace_depth += 1
            current.append(char)
            continue
        if char == "}":
            brace_depth = max(brace_depth - 1, 0)
            current.append(char)
            continue
        if char == "[":
            bracket_depth += 1
            current.append(char)
            continue
        if char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
            current.append(char)
            continue
        if char == "(":
            paren_depth += 1
            current.append(char)
            continue
        if char == ")":
            paren_depth = max(paren_depth - 1, 0)
            current.append(char)
            continue
        if char in {",", "\n"} and brace_depth == 0 and bracket_depth == 0 and paren_depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _parse_hcl_key(token: str) -> str | None:
    text = token.strip().rstrip(",")
    if not text:
        return None
    if text.startswith('"') and text.endswith('"'):
        with contextlib.suppress(Exception):
            return ast.literal_eval(text)
    return text


def _parse_hcl_mapping_default(token: str) -> dict[str, Any] | None:
    text = token.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return {}
    mapping: dict[str, Any] = {}
    for item in _split_top_level_hcl_items(inner):
        assignment_index = _top_level_assignment_index(item)
        if assignment_index is None:
            return None
        key = _parse_hcl_key(item[:assignment_index])
        if key is None:
            return None
        mapping[str(key)] = _parse_hcl_default(item[assignment_index + 1 :])
    return mapping


def _hcl_default_to_yaml_candidate(token: str) -> str:
    return token.replace("=", ":")


def _extract_hcl_attribute_expression(block: str, attribute_name: str) -> str | None:
    match = re.search(
        rf"(^|\n)\s*{re.escape(attribute_name)}\s*=\s*",
        block,
        re.MULTILINE,
    )
    if match is None:
        return None
    index = match.end()
    while index < len(block) and block[index].isspace() and block[index] != "\n":
        index += 1
    if index >= len(block):
        return ""
    if block[index : index + 2] == "<<":
        line_end = block.find("\n", index)
        return block[index:] if line_end == -1 else block[index:line_end]

    start = index
    in_string = False
    escaped = False
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    for cursor in range(index, len(block)):
        char = block[cursor]
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            brace_depth += 1
            continue
        if char == "}":
            brace_depth = max(brace_depth - 1, 0)
        elif char == "[":
            bracket_depth += 1
            continue
        elif char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
        elif char == "(":
            paren_depth += 1
            continue
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)
        if char == "\n" and brace_depth == 0 and bracket_depth == 0 and paren_depth == 0:
            return block[start:cursor].rstrip()
    return block[start:]


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
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip() or None
        return None, error_text

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
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
        description = (
            _normalize_description(meta.get("description")) if isinstance(meta, dict) else None
        )
        has_default = isinstance(meta, dict) and "default" in meta
        default_value = (
            _deep_copy(meta.get("default")) if has_default and isinstance(meta, dict) else None
        )
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
        description = (
            _normalize_description(meta.get("description")) if isinstance(meta, dict) else None
        )
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
    nullable_pattern = re.compile(r"(^|\n)\s*nullable\s*=\s*(.+)", re.MULTILINE)
    description_pattern = re.compile(r'(^|\n)\s*description\s*=\s*"([^"]*)"', re.MULTILINE)

    discovered: dict[str, ModuleVariable] = {}
    for file_path in sorted(path.glob("*.tf")):
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
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
            default_expr = _extract_hcl_attribute_expression(block, "default")
            has_default = default_expr is not None
            default_value = _parse_hcl_default(default_expr) if default_expr is not None else None
            nullable_value: bool | None = None
            nullable_match = nullable_pattern.search(block)
            if nullable_match:
                nullable_token = str(nullable_match.group(2)).strip().lower().rstrip(",")
                if nullable_token in {"true", "false"}:
                    nullable_value = nullable_token == "true"
            required = (not has_default) and (nullable_value is not True)
            type_hint = _normalize_type_hint(_extract_hcl_attribute_expression(block, "type"))
            description_match = description_pattern.search(block)
            description = _normalize_description(
                description_match.group(2) if description_match else None
            )

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


_MODULE_PROVIDER_BLOCK_PATTERN = re.compile(r'(?m)^\s*provider\s+"[^"]+"\s*\{')
_MODULE_REQUIRED_VERSION_PATTERN = re.compile(r"\brequired_version\b")
_MODULE_REQUIRED_PROVIDERS_PATTERN = re.compile(r"\brequired_providers\b")
_MODULE_BACKEND_BLOCK_PATTERN = re.compile(r'(?s)\bterraform\s*\{.*?\bbackend\s+"[^"]+"\s*\{')


def _example_root_directories(examples_dir: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for child in sorted(examples_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "main.tf").exists():
            roots.append(child)
    return tuple(roots)


@lru_cache(maxsize=64)
def module_cli_contract_findings(
    module_source: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source = str(module_source).strip()
    if not source:
        return (), ()

    path_text, inspection_issue = _module_inspection_path(source)
    if inspection_issue or not path_text:
        return (), ()

    module_dir = Path(path_text)
    issues: list[str] = []
    warnings: list[str] = []

    canonical_tf_files = ("main.tf", "variables.tf", "outputs.tf")
    for file_name in canonical_tf_files:
        if not (module_dir / file_name).exists():
            warnings.append(
                f"module source '{source}' is missing canonical file '{file_name}' in {module_dir}"
            )

    versions_file = module_dir / "versions.tf"
    if not versions_file.exists():
        issues.append(f"module source '{source}' is missing versions.tf in {module_dir}")
    else:
        versions_text = versions_file.read_text(encoding="utf-8")
        if not _MODULE_REQUIRED_VERSION_PATTERN.search(versions_text):
            issues.append(f"module source '{source}' versions.tf is missing required_version")
        if not _MODULE_REQUIRED_PROVIDERS_PATTERN.search(versions_text):
            issues.append(f"module source '{source}' versions.tf is missing required_providers")

    readme_file = module_dir / "README.md"
    if not readme_file.exists():
        warnings.append(f"module source '{source}' is missing README.md in {module_dir}")

    examples_dir = module_dir / "examples"
    if not examples_dir.exists():
        warnings.append(f"module source '{source}' is missing examples/ in {module_dir}")
    elif not examples_dir.is_dir():
        issues.append(
            f"module source '{source}' examples exists but is not a directory in {module_dir}"
        )
    elif not _example_root_directories(examples_dir):
        warnings.append(
            f"module source '{source}' examples/ has no runnable example roots with main.tf"
        )

    for tf_file in sorted(module_dir.glob("*.tf")):
        text = tf_file.read_text(encoding="utf-8")
        if _MODULE_PROVIDER_BLOCK_PATTERN.search(text):
            issues.append(
                f"module source '{source}' contains provider blocks in {tf_file.name}; child modules must not configure providers"
            )
        if _MODULE_BACKEND_BLOCK_PATTERN.search(text):
            issues.append(
                f"module source '{source}' contains backend blocks in {tf_file.name}; child modules must not configure backends"
            )

    return tuple(issues), tuple(warnings)


@lru_cache(maxsize=64)
def module_variable_names(module_source: str) -> tuple[str, ...]:
    return tuple(variable.name for variable in module_variables(module_source))


@lru_cache(maxsize=64)
def module_required_variables(module_source: str) -> tuple[str, ...]:
    return tuple(variable.name for variable in module_variables(module_source) if variable.required)


def _local_chart_values_payload(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
) -> dict[str, Any]:
    if chart_repo.strip() or chart_version.strip():
        return {}

    chart_ref = chart_name_or_ref.strip()
    if not chart_ref:
        return {}

    candidates: list[Path] = []
    raw_candidate = Path(chart_ref)
    candidates.append(raw_candidate)
    if not raw_candidate.is_absolute():
        try:
            sources_file = resolve_component_sources_file()
        except (OSError, RuntimeError, ValueError):
            sources_file = None
        if sources_file is not None:
            candidates.append((sources_file.parent / raw_candidate).resolve())

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        values_file = resolved / "values.yaml"
        if not values_file.is_file():
            continue
        try:
            payload = yaml.safe_load(values_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if isinstance(payload, dict):
            return _deep_copy(payload)
        return {}

    return {}


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
    except (OSError, RuntimeError, ValueError):
        payload = _local_chart_values_payload(
            chart_name_or_ref=chart_name_or_ref,
            chart_repo=chart_repo,
            chart_version=chart_version,
        )
    if not isinstance(payload, dict):
        return {}
    return _deep_copy(payload)


def reset_runtime_introspection_cache() -> None:
    _module_inspection_path.cache_clear()
    module_variables.cache_clear()
    module_outputs.cache_clear()
    module_output_names.cache_clear()
    module_source_validation_issues.cache_clear()
    module_cli_contract_findings.cache_clear()
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
