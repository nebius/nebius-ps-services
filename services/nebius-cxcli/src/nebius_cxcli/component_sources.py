"""Runtime component source registry loader and discovery helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
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

_CLI_COMPONENT_SOURCES_FILE_OVERRIDE: Path | None = None


@dataclass(frozen=True)
class TFModuleSource:
    module: str
    source: str
    description: str | None = None
    version: str | None = None
    enable: bool = False
    group: str | None = None


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


@dataclass(frozen=True)
class ComponentSources:
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


def _parse_sources_payload(payload: Any) -> ComponentSources:
    if not isinstance(payload, dict):
        raise ValueError("component_sources root must be a mapping")

    infra = payload.get("infra", {})
    apps = payload.get("apps", {})
    if not isinstance(infra, dict):
        infra = {}
    if not isinstance(apps, dict):
        apps = {}

    tf_modules: list[TFModuleSource] = []
    for raw in infra.get("tf_modules", []):
        if not isinstance(raw, dict):
            continue
        module_name = _as_text(raw.get("module")).lower()
        source = _as_text(raw.get("source"))
        if not module_name or not source:
            continue
        description = _as_text(raw.get("description")) or None
        version = _as_text(raw.get("version")) or None
        enable = bool(raw.get("enable", False))
        group = _as_text(raw.get("group")) or None
        tf_modules.append(
            TFModuleSource(
                module=module_name,
                source=source,
                description=description,
                version=version,
                enable=enable,
                group=group,
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
        repo = repo_raw or None
        version = _as_text(raw.get("version")) or None
        namespace = _as_text(raw.get("namespace")) or None
        release_name = _as_text(raw.get("releasename")) or None
        enable = bool(raw.get("enable", False))
        description = _as_text(raw.get("description")) or None
        group = _as_text(raw.get("group")) or None
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
            )
        )

    return ComponentSources(
        tf_modules=tuple(tf_modules),
        helm_charts=tuple(helm_charts),
    )


def _load_sources_from_path(path: Path) -> ComponentSources:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return _parse_sources_payload(payload)


def _load_bundled_component_sources() -> ComponentSources:
    resource = importlib_resources.files("nebius_cxcli").joinpath(BUNDLED_COMPONENT_SOURCES_FILENAME)
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        return _parse_sources_payload(payload)
    except FileNotFoundError:
        pass
    except OSError:
        pass

    prefix_candidate = Path(sys.prefix) / "nebius_cxcli" / BUNDLED_COMPONENT_SOURCES_FILENAME
    if prefix_candidate.exists() and prefix_candidate.is_file():
        return _load_sources_from_path(prefix_candidate)

    raise FileNotFoundError(
        "Bundled component sources file is missing from the installed package layout."
    )


@lru_cache(maxsize=8)
def _load_sources_cached(path_text: str) -> ComponentSources:
    return _load_sources_from_path(Path(path_text))


@lru_cache(maxsize=1)
def _load_bundled_sources_cached() -> ComponentSources:
    return _load_bundled_component_sources()


def _can_use_bundled_default(*, explicit: Path | None) -> bool:
    if explicit is not None:
        return False
    if _CLI_COMPONENT_SOURCES_FILE_OVERRIDE is not None:
        return False
    return not bool(os.environ.get(COMPONENT_SOURCES_FILE_ENV, "").strip())


def load_component_sources(*, explicit: Path | None = None) -> ComponentSources:
    try:
        path = resolve_component_sources_file(explicit=explicit)
    except ValueError:
        if _can_use_bundled_default(explicit=explicit):
            return _load_bundled_sources_cached()
        raise
    return _load_sources_cached(str(path))


def reset_component_sources_cache() -> None:
    _load_sources_cached.cache_clear()
    _load_bundled_sources_cached.cache_clear()
