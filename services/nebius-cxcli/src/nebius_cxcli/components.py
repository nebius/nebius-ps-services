"""Component registry helpers for selectable infra and app components."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, cast

from .component_sources import (
    ComponentDefault,
    ComponentInputBinding,
    ComponentOutput,
    Handoff,
    SourceProfile,
    StatusWatcher,
    load_component_sources,
    reset_component_sources_cache,
)

ComponentScope = Literal["infra", "apps"]
COMPONENT_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class ComponentEntry:
    id: str
    scope: ComponentScope
    config_path: str
    description: str
    name: str | None = None
    default_enabled: bool = False
    selectable: bool = True
    enabled_path: tuple[str, ...] | None = None
    engine_type: str = "registry"
    source: str | None = None
    metadata_source: str | None = None
    version: str | None = None
    depends_on: tuple[str, ...] = ()
    dependency_match_names: tuple[str, ...] = ()
    wizard_fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    group: str | None = None
    kind: str = ""
    validation_profile: str = ""
    chart_name: str | None = None
    chart_repo: str | None = None
    default_namespace: str | None = None
    default_release_name: str | None = None
    defaults: tuple[ComponentDefault, ...] = ()
    outputs: tuple[ComponentOutput, ...] = ()
    input_bindings: tuple[ComponentInputBinding, ...] = ()
    handoff: Handoff | None = None
    status: StatusWatcher | None = None


def _humanize_component_id(component_id: str) -> str:
    words = component_id.replace("-", " ").replace("_", " ").split()
    if not words:
        return component_id
    return " ".join(word.upper() if word.isupper() else word.capitalize() for word in words)


def _normalize_config_key(component_id: str) -> str:
    return component_id.strip().lower().replace("-", "_")


def _normalize_entry_id(value: str) -> str:
    token = value.strip().lower().replace("_", "-")
    token = re.sub(r"[^a-z0-9-]+", "-", token)
    token = re.sub(r"-{2,}", "-", token).strip("-")
    return token


def _normalize_app_section(group: str | None) -> str:
    raw = str(group or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return token or "workloads"


def _compose_chart_source(*, repo: str | None, chart_name: str) -> str:
    normalized_repo = str(repo or "").strip().rstrip("/")
    normalized_chart = chart_name.strip().strip("/")
    if not normalized_repo:
        return normalized_chart
    if normalized_repo.startswith("oci://") and normalized_chart:
        repo_tail = normalized_repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail == normalized_chart.lower():
            return normalized_repo
    if not normalized_chart:
        return normalized_repo
    return f"{normalized_repo}/{normalized_chart}"


@lru_cache(maxsize=2)
def _infra_component_entries(
    source_profile: SourceProfile | None = None,
) -> tuple[ComponentEntry, ...]:
    entries: list[ComponentEntry] = []
    entry_ids: set[str] = set()
    sources = load_component_sources(source_profile=source_profile)
    for module in sources.tf_modules:
        component_id = _normalize_entry_id(module.module)
        if not component_id or component_id in entry_ids:
            continue
        if not COMPONENT_ID_PATTERN.fullmatch(component_id):
            continue
        config_key = _normalize_config_key(component_id)
        entries.append(
            ComponentEntry(
                id=component_id,
                scope="infra",
                config_path=f"infra.{config_key}",
                description=module.description or _humanize_component_id(component_id),
                default_enabled=bool(module.enable),
                selectable=True,
                enabled_path=("infra", config_key, "enabled"),
                kind=module.kind,
                engine_type="terraform_module",
                source=module.source,
                metadata_source=module.metadata_source,
                version=module.version,
                group=module.group,
                wizard_fields=dict(module.wizard_fields or {}),
                defaults=module.defaults,
                outputs=module.outputs,
                input_bindings=module.input_bindings,
                handoff=module.handoff,
                status=module.status,
                validation_profile=module.validation_profile,
            )
        )
        entry_ids.add(component_id)
    return tuple(entries)


def _entry_from_helm_chart(
    *,
    component_id: str,
    release_name: str,
    description: str,
    group: str | None,
    repo: str | None,
    chart_name: str,
    version: str | None,
    namespace: str | None,
    default_enabled: bool = False,
    wizard_fields: dict[str, dict[str, Any]] | None = None,
    defaults: tuple[ComponentDefault, ...] = (),
    outputs: tuple[ComponentOutput, ...] = (),
    input_bindings: tuple[ComponentInputBinding, ...] = (),
) -> ComponentEntry:
    config_key = _normalize_config_key(component_id)
    normalized_group = (group or "").strip()
    normalized_section = _normalize_app_section(normalized_group)
    source = _compose_chart_source(repo=repo, chart_name=chart_name)
    return ComponentEntry(
        id=component_id,
        scope="apps",
        config_path=f"apps.{normalized_section}.{config_key}",
        description=description or _humanize_component_id(component_id),
        name=release_name,
        default_enabled=default_enabled,
        selectable=True,
        enabled_path=("apps", normalized_section, config_key, "enabled"),
        engine_type="helm_release",
        source=source,
        version=version,
        dependency_match_names=(component_id, chart_name.lower().strip(), release_name.lower().strip()),
        depends_on=(),
        group=normalized_group or None,
        chart_name=chart_name,
        chart_repo=repo,
        default_namespace=namespace,
        default_release_name=release_name,
        wizard_fields=dict(wizard_fields or {}),
        defaults=defaults,
        outputs=outputs,
        input_bindings=input_bindings,
    )


@lru_cache(maxsize=2)
def _app_component_entries(
    source_profile: SourceProfile | None = None,
) -> tuple[ComponentEntry, ...]:
    entries: list[ComponentEntry] = []
    entry_ids: set[str] = set()
    sources = load_component_sources(source_profile=source_profile)

    # Primary source: explicit chart entries in component_sources.yaml.
    for chart in sources.helm_charts:
        release_name = chart.release_name or chart.name
        component_id = _normalize_entry_id(chart.name)
        chart_name = chart.chart_name or chart.name
        if not component_id or component_id in entry_ids:
            continue
        if not COMPONENT_ID_PATTERN.fullmatch(component_id):
            continue
        entries.append(
            _entry_from_helm_chart(
                component_id=component_id,
                release_name=release_name,
                description=chart.description or f"Helm chart ({component_id})",
                group=chart.group,
                repo=chart.repo,
                chart_name=chart_name,
                version=chart.version,
                namespace=chart.namespace,
                default_enabled=bool(chart.enable),
                wizard_fields=chart.wizard_fields,
                defaults=chart.defaults,
                outputs=chart.outputs,
                input_bindings=chart.input_bindings,
            )
        )
        entry_ids.add(component_id)

    return tuple(entries)


def component_entries(
    scope: ComponentScope,
    *,
    source_profile: SourceProfile | None = None,
) -> tuple[ComponentEntry, ...]:
    if scope == "infra":
        return _infra_component_entries(source_profile)
    return _app_component_entries(source_profile)


def component_lookup(
    scope: ComponentScope,
    *,
    source_profile: SourceProfile | None = None,
) -> dict[str, ComponentEntry]:
    return {entry.id: entry for entry in component_entries(scope, source_profile=source_profile)}


def default_component_ids(
    scope: ComponentScope,
    *,
    source_profile: SourceProfile | None = None,
) -> list[str]:
    return [
        entry.id
        for entry in component_entries(scope, source_profile=source_profile)
        if entry.default_enabled
    ]


def all_component_ids(
    scope: ComponentScope,
    *,
    source_profile: SourceProfile | None = None,
) -> list[str]:
    return [entry.id for entry in component_entries(scope, source_profile=source_profile)]


def reset_component_entry_cache() -> None:
    """Clear in-process component entry caches (used by tests/overrides)."""
    _infra_component_entries.cache_clear()
    _app_component_entries.cache_clear()
    reset_component_sources_cache()


@dataclass(frozen=True)
class ComponentDependencyAdjustment:
    source_scope: ComponentScope
    source_id: str
    dependency_scope: ComponentScope
    dependency_id: str


@dataclass(frozen=True)
class ComponentDependencyResolution:
    selected_infra: set[str]
    selected_apps: set[str]
    adjustments: tuple[ComponentDependencyAdjustment, ...]


def _normalize_component_id(value: str, *, field_name: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    if not COMPONENT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must use lowercase letters, digits, and hyphens"
        )
    return normalized


def parse_dependency_ref(
    raw_ref: str,
    *,
    default_scope: ComponentScope,
) -> tuple[ComponentScope, str]:
    token = raw_ref.strip().lower()
    if not token:
        raise ValueError("depends_on entries cannot be empty")

    if ":" in token:
        scope_raw, entry_raw = token.split(":", 1)
        scope = cast(ComponentScope, scope_raw.strip())
        if scope not in {"infra", "apps"}:
            raise ValueError(
                f"depends_on scope '{scope_raw}' is invalid; use 'infra:<id>' or 'apps:<id>'"
            )
        entry_id = _normalize_component_id(entry_raw, field_name="depends_on id")
        return scope, entry_id

    entry_id = _normalize_component_id(token, field_name="depends_on id")
    return default_scope, entry_id


def normalize_dependency_refs(
    refs: Iterable[str],
    *,
    default_scope: ComponentScope,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in refs:
        for segment in str(raw).split(","):
            token = segment.strip()
            if not token:
                continue
            scope, entry_id = parse_dependency_ref(token, default_scope=default_scope)
            canonical = f"{scope}:{entry_id}"
            if canonical in seen:
                continue
            normalized.append(canonical)
            seen.add(canonical)
    return tuple(normalized)


def resolve_component_dependencies(
    *,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> ComponentDependencyResolution:
    selected: dict[ComponentScope, set[str]] = {
        "infra": set(selected_infra),
        "apps": set(selected_apps),
    }
    lookup: dict[ComponentScope, dict[str, ComponentEntry]] = {
        "infra": {entry.id: entry for entry in infra_entries},
        "apps": {entry.id: entry for entry in app_entries},
    }

    queue: deque[tuple[ComponentScope, str]] = deque()
    queued: set[tuple[ComponentScope, str]] = set()
    for scope in ("infra", "apps"):
        for entry_id in sorted(selected[cast(ComponentScope, scope)]):
            item = (cast(ComponentScope, scope), entry_id)
            queue.append(item)
            queued.add(item)

    adjustments: list[ComponentDependencyAdjustment] = []
    while queue:
        scope, entry_id = queue.popleft()
        queued.discard((scope, entry_id))
        entry = lookup[scope].get(entry_id)
        if entry is None:
            raise ValueError(f"Unknown component selection '{scope}:{entry_id}'")

        # App dependencies are resolved from Helm Chart.yaml at runtime.
        dependency_refs = entry.depends_on if scope == "infra" else ()
        for raw_ref in dependency_refs:
            dep_scope, dep_id = parse_dependency_ref(raw_ref, default_scope=scope)
            if dep_id not in lookup[dep_scope]:
                raise ValueError(
                    f"Component dependency '{dep_scope}:{dep_id}' required by "
                    f"'{scope}:{entry_id}' is not defined in current component registry"
                )
            if dep_id in selected[dep_scope]:
                continue

            selected[dep_scope].add(dep_id)
            adjustments.append(
                ComponentDependencyAdjustment(
                    source_scope=scope,
                    source_id=entry_id,
                    dependency_scope=dep_scope,
                    dependency_id=dep_id,
                )
            )

            dep_item = (dep_scope, dep_id)
            if dep_item not in queued:
                queue.append(dep_item)
                queued.add(dep_item)

    return ComponentDependencyResolution(
        selected_infra=selected["infra"],
        selected_apps=selected["apps"],
        adjustments=tuple(adjustments),
    )
