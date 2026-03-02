"""Runtime introspection helpers for provider/module/chart-backed prompting."""

from __future__ import annotations

import re
import shutil
import subprocess
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


@lru_cache(maxsize=64)
def module_required_variables(module_source: str) -> tuple[str, ...]:
    path = _resolve_module_source_path(module_source)
    if path is None:
        return ()

    if shutil.which("terraform-config-inspect"):
        try:
            result = subprocess.run(
                ["terraform-config-inspect", "-json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                payload = yaml.safe_load(result.stdout) or {}
                if isinstance(payload, dict):
                    variables = payload.get("variables", {})
                    if isinstance(variables, dict):
                        required: list[str] = []
                        for name, meta in variables.items():
                            if not isinstance(name, str) or not isinstance(meta, dict):
                                continue
                            if bool(meta.get("required")):
                                required.append(name.strip().lower())
                        return tuple(sorted(set(required)))
        except Exception:
            pass

    # Fallback: parse variable blocks in *.tf
    required: set[str] = set()
    variable_block_pattern = re.compile(r'variable\s+"([^"]+)"\s*\{', re.MULTILINE)
    for file_path in sorted(path.glob("*.tf")):
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        starts = list(variable_block_pattern.finditer(text))
        for index, match in enumerate(starts):
            var_name = match.group(1).strip().lower()
            block_start = match.end()
            block_end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            block = text[block_start:block_end]
            if re.search(r"\bdefault\s*=", block):
                continue
            required.add(var_name)
    return tuple(sorted(required))


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
