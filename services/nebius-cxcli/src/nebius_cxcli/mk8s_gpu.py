"""MK8s GPU policy helpers and deploy-time validation workflows."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .component_defaults import resolve_component_defaults
from .component_instances import INSTANCE_ID_FIELD, component_instance_id, component_type_id
from .component_sources import (
    ComponentDefault,
    FluxPostRenderPatch,
    Mk8sGpuAppPolicy,
    Mk8sGpuAppRule,
    Mk8sGpuSettings,
    helm_chart_source_by_id,
    load_component_sources,
    tf_module_source_by_id,
)
from .components import ComponentEntry, component_entries
from .deploy_targets import (
    TARGET_REF_FIELD,
    app_chart_target_ref,
    is_auto_target_scoped_app_instance_id,
    target_scoped_app_instance_id,
)
from .helm_client import render_chart_template_documents
from .runtime_config import to_plain_data
from .runtime_introspection import helm_chart_default_values

_GPU_QUANTITY_RE = re.compile(r"^\d+")
_K8S_QUANTITY_RE = re.compile(
    r"^\s*(?P<number>[+-]?(?:\d+(?:\.\d+)?|\.\d+))(?P<suffix>Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|k|m|u|n)?\s*$"
)
_PRESET_RESOURCE_HINT_RE = re.compile(
    r"(?i)(?P<gpu>\d+)\s*gpu.*?(?P<vcpu>\d+)\s*vcpu.*?(?P<memory>\d+)\s*g(?:i?b)?"
)
_AVG_BUS_BANDWIDTH_RE = re.compile(r"Avg bus bandwidth\s*:\s*([0-9]+(?:\.[0-9]+)?)")
_OUT_OF_BOUNDS_OK_RE = re.compile(r"Out of bounds values\s*:\s*0 OK")
_NCCL_JSON_BEGIN_MARKER = "__NCCL_JSON_BEGIN__"
_NCCL_JSON_END_MARKER = "__NCCL_JSON_END__"
_NCCL_DMABUF_ENV_NAME = "NCCL_DMABUF_ENABLE"
_VALIDATION_POLL_INTERVAL_SECONDS = 5.0
_VALIDATION_REPEAT_INTERVAL_SECONDS = 20.0
_GPU_STACK_VALIDATION_NAME = "GPU stack readiness"
_BINARY_QUANTITY_UNITS: dict[str, int] = {
    "Ki": 1 << 10,
    "Mi": 1 << 20,
    "Gi": 1 << 30,
    "Ti": 1 << 40,
    "Pi": 1 << 50,
    "Ei": 1 << 60,
}
_DECIMAL_QUANTITY_UNITS: dict[str, Decimal] = {
    "": Decimal(1),
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "k": Decimal("1e3"),
    "M": Decimal("1e6"),
    "G": Decimal("1e9"),
    "T": Decimal("1e12"),
    "P": Decimal("1e15"),
    "E": Decimal("1e18"),
}


@dataclass(frozen=True)
class Mk8sGpuClusterContext:
    component_id: str
    instance_id: str
    gpu_platform: str
    gpu_preset: str
    gpu_cluster_enabled: bool
    gpu_stack_source: str


@dataclass(frozen=True)
class Mk8sGpuAppSelection:
    selected_app_ids: tuple[str, ...]
    auto_enabled_app_ids: tuple[str, ...]
    issues: tuple[str, ...]
    cluster_contexts: tuple[Mk8sGpuClusterContext, ...]
    gpu_stack_source: str


@dataclass(frozen=True)
class Mk8sGpuValidationOverrides:
    operator_readiness_enabled: bool
    gpu_visibility_enabled: bool
    gpu_visibility_max_nodes: int
    nccl_enabled: bool
    nccl_max_nodes: int
    nccl_average_bus_bandwidth_threshold_gbps: float
    health_checker_enabled: bool


@dataclass(frozen=True)
class Mk8sNcclValidationContext:
    context: Mk8sGpuClusterContext
    gpu_count: int | None
    allow_gpu_clustering: bool | None
    transport_mode: str
    threshold_enforced: bool


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    if not isinstance(payload, dict):
        return {}
    return payload


def _catalog_sources():
    return load_component_sources()


def _mk8s_gpu_settings() -> Mk8sGpuSettings:
    module = tf_module_source_by_id("mk8s", sources=_catalog_sources())
    return module.mk8s_gpu if module is not None else Mk8sGpuSettings()


def _mk8s_gpu_app_policies() -> dict[str, Mk8sGpuAppPolicy]:
    policies: dict[str, Mk8sGpuAppPolicy] = {}
    for chart in _catalog_sources().helm_charts:
        app_id = _as_text(chart.name).lower()
        policy = chart.mk8s_gpu
        if not app_id:
            continue
        if policy.role or policy.rules or policy.install_after:
            policies[app_id] = policy
    return policies


def _mk8s_gpu_app_id_by_role() -> dict[str, str]:
    roles: dict[str, str] = {}
    for app_id, policy in _mk8s_gpu_app_policies().items():
        if policy.role and policy.role not in roles:
            roles[policy.role] = app_id
    return roles


def has_mk8s_gpu_health_checker_app() -> bool:
    return bool(_mk8s_gpu_app_id_by_role().get("health_checker"))


def _include_health_checker_project_setting() -> bool:
    return has_mk8s_gpu_health_checker_app()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _deep_merge_mapping(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        existing = merged.get(str(key))
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _deep_merge_mapping(existing, value)
        else:
            merged[str(key)] = copy.deepcopy(value)
    return merged


def _deploy_target_rows(payload: dict[str, Any], *, create: bool = False) -> list[Any]:
    deploy = payload.get("deploy")
    if not isinstance(deploy, dict):
        if not create:
            return []
        deploy = {}
        payload["deploy"] = deploy
    targets = deploy.get("targets")
    if not isinstance(targets, list):
        if not create:
            return []
        targets = []
        deploy["targets"] = targets
    return targets


def _deploy_target_ref(row: Mapping[str, Any]) -> str:
    return _as_text(row.get(INSTANCE_ID_FIELD)).lower().replace("_", "-")


def _deploy_target_row_for_ref(
    payload: dict[str, Any],
    *,
    target_ref: str,
    create: bool = False,
) -> dict[str, Any] | None:
    normalized_target_ref = _as_text(target_ref).lower().replace("_", "-")
    if not normalized_target_ref:
        return None
    rows = _deploy_target_rows(payload, create=create)
    for row in rows:
        if isinstance(row, dict) and _deploy_target_ref(row) == normalized_target_ref:
            return row
    if not create:
        return None
    row: dict[str, Any] = {INSTANCE_ID_FIELD: normalized_target_ref}
    rows.append(row)
    return row


def _deploy_target_row_has_settings(row: Mapping[str, Any]) -> bool:
    return any(str(key) != INSTANCE_ID_FIELD for key in row)


def _nested_path_value(value: Any, dotted_path: str) -> Any:
    current = value
    for raw_segment in dotted_path.split("."):
        segment = str(raw_segment).strip()
        if not segment or not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def mk8s_gpu_project_validation_defaults(
    *,
    gpu_settings: Mk8sGpuSettings | None = None,
) -> dict[str, Any]:
    settings = gpu_settings or _mk8s_gpu_settings()
    validations = settings.validations
    defaults = {
        "operator_readiness": {
            "enabled": validations.operator_readiness.enabled_by_default,
        },
        "gpu_visibility": {
            "enabled": validations.gpu_visibility.enabled_by_default,
            "max_nodes": validations.gpu_visibility.max_nodes,
        },
        "nccl": {
            "enabled": validations.nccl.enabled_by_default,
            "max_nodes": validations.nccl.max_nodes,
            "average_bus_bandwidth_threshold_gbps": (
                validations.nccl.average_bus_bandwidth_threshold_gbps
            ),
        },
    }
    if _include_health_checker_project_setting():
        defaults["health_checker"] = {
            "enabled": validations.health_checker.enabled_by_default,
        }
    return defaults


def _prune_unavailable_health_checker_setting(settings: dict[str, Any]) -> bool:
    if _include_health_checker_project_setting():
        return False
    raw_health_checker = settings.get("health_checker")
    if not isinstance(raw_health_checker, Mapping):
        return False
    if raw_health_checker.get("enabled") is True:
        return False
    del settings["health_checker"]
    return True


def normalize_mk8s_gpu_project_validation_settings(payload: dict[str, Any]) -> bool:
    payload_map = _as_payload(payload)
    if not payload_map:
        return False

    infra = payload_map.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return False

    enabled_gpu_target_refs: list[str] = []
    changed = False

    for item in components:
        if not isinstance(item, dict):
            continue
        if component_type_id(item) != "mk8s" or not bool(item.get("enabled", False)):
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, Mapping) or not bool(inputs.get("gpu_enabled", False)):
            continue
        instance_id = component_instance_id(item)
        if instance_id:
            enabled_gpu_target_refs.append(instance_id)

    if not enabled_gpu_target_refs:
        deploy = payload_map.get("deploy")
        if not isinstance(deploy, dict):
            return changed
        targets = deploy.get("targets")
        if isinstance(targets, list):
            next_targets: list[Any] = []
            for row in targets:
                if not isinstance(row, dict):
                    next_targets.append(row)
                    continue
                validations = row.get("validations")
                if isinstance(validations, dict) and "mk8s_gpu" in validations:
                    validations.pop("mk8s_gpu", None)
                    changed = True
                    if not validations:
                        row.pop("validations", None)
                if _deploy_target_row_has_settings(row):
                    next_targets.append(row)
            if next_targets != targets:
                deploy["targets"] = next_targets
                changed = True
            if not next_targets:
                deploy.pop("targets", None)
        if not deploy:
            payload_map.pop("deploy", None)
        return changed

    project_defaults = mk8s_gpu_project_validation_defaults()
    seen_target_refs = set(enabled_gpu_target_refs)
    targets = _deploy_target_rows(payload_map, create=True)
    next_targets: list[Any] = []
    for row in targets:
        if not isinstance(row, dict):
            next_targets.append(row)
            continue
        target_ref = _deploy_target_ref(row)
        if target_ref and target_ref not in seen_target_refs:
            validations = row.get("validations")
            if isinstance(validations, dict) and "mk8s_gpu" in validations:
                validations.pop("mk8s_gpu", None)
                changed = True
                if not validations:
                    row.pop("validations", None)
        if _deploy_target_row_has_settings(row) or target_ref in seen_target_refs:
            next_targets.append(row)
    if next_targets != targets:
        deploy = payload_map.get("deploy")
        if isinstance(deploy, dict):
            deploy["targets"] = next_targets
            targets = next_targets
            changed = True

    for target_ref in enabled_gpu_target_refs:
        row = _deploy_target_row_for_ref(payload_map, target_ref=target_ref, create=True)
        if row is None:
            continue
        validations = row.get("validations")
        if not isinstance(validations, dict):
            validations = {}
            row["validations"] = validations
            changed = True
        existing = validations.get("mk8s_gpu")
        merged = copy.deepcopy(project_defaults)
        if isinstance(existing, Mapping):
            merged = _deep_merge_mapping(merged, existing)
        if _prune_unavailable_health_checker_setting(merged):
            changed = True
        if validations.get("mk8s_gpu") != merged:
            validations["mk8s_gpu"] = merged
            changed = True

    deploy = payload_map.get("deploy")
    if isinstance(deploy, dict) and not deploy:
        payload_map.pop("deploy", None)
    return changed


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = _as_text(value).lower()
    if normalized in {"true", "t", "1", "yes", "y"}:
        return True
    if normalized in {"false", "f", "0", "no", "n"}:
        return False
    return None


def _coerce_optional_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_optional_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _gpu_stack_source(inputs: dict[str, Any]) -> str:
    stack_source = _as_text(inputs.get("gpu_stack_source")).lower()
    return stack_source if stack_source in {"nebius_image", "operator_managed"} else "nebius_image"


def _rule_matches_context(
    context: Mk8sGpuClusterContext,
    *,
    gpu_stack_source: str = "",
    gpu_cluster_enabled: bool | None = None,
    match_platforms: tuple[str, ...] = (),
    match_presets: tuple[str, ...] = (),
) -> bool:
    if gpu_stack_source and context.gpu_stack_source != gpu_stack_source:
        return False
    if gpu_cluster_enabled is not None and context.gpu_cluster_enabled != gpu_cluster_enabled:
        return False
    if match_platforms and context.gpu_platform not in match_platforms:
        return False
    return not match_presets or context.gpu_preset in match_presets


def _app_rule_matches(
    context: Mk8sGpuClusterContext,
    rule: Mk8sGpuAppRule,
) -> bool:
    return _rule_matches_context(
        context,
        gpu_stack_source=rule.gpu_stack_source,
        gpu_cluster_enabled=rule.gpu_cluster_enabled,
        match_platforms=rule.match_platforms,
        match_presets=rule.match_presets,
    )


def _required_gpu_app_ids(
    contexts: tuple[Mk8sGpuClusterContext, ...],
    *,
    gpu_settings: Mk8sGpuSettings | None = None,
    health_checker_enabled: bool | None = None,
) -> tuple[str, ...]:
    required_ids = {
        app_id
        for app_id, policy in _mk8s_gpu_app_policies().items()
        if any(
            rule.auto_enable and _app_rule_matches(context, rule)
            for rule in policy.rules
            for context in contexts
        )
    }
    settings = gpu_settings or _mk8s_gpu_settings()
    resolved_health_checker_enabled = (
        settings.validations.health_checker.enabled_by_default
        if health_checker_enabled is None
        else health_checker_enabled
    )
    if resolved_health_checker_enabled:
        health_checker_id = _mk8s_gpu_app_id_by_role().get("health_checker")
        if health_checker_id:
            required_ids.add(health_checker_id)
    return tuple(sorted(required_ids))


def _project_mk8s_gpu_validation_config(
    payload: dict[str, Any],
    *,
    target_ref: str,
) -> dict[str, Any]:
    target_row = _deploy_target_row_for_ref(payload, target_ref=target_ref)
    if not isinstance(target_row, Mapping):
        return {}
    validations = target_row.get("validations")
    if not isinstance(validations, Mapping):
        return {}
    return _mapping(validations.get("mk8s_gpu"))


def _resolve_project_gpu_validation_setting(
    payload: dict[str, Any],
    *,
    target_ref: str,
    dotted_path: str,
    coerce: Callable[[Any], Any | None],
    default: Any,
    field_label: str,
) -> Any:
    project_config = _project_mk8s_gpu_validation_config(payload, target_ref=target_ref)
    raw_value = _nested_path_value(project_config, dotted_path)
    if raw_value is None:
        return default
    coerced = coerce(raw_value)
    if coerced is None:
        raise ValueError(f"{field_label} must use a supported value")
    return coerced


def _effective_mk8s_gpu_validation_overrides(
    payload: dict[str, Any],
    *,
    target_ref: str,
    gpu_settings: Mk8sGpuSettings | None = None,
) -> Mk8sGpuValidationOverrides:
    settings = gpu_settings or _mk8s_gpu_settings()
    validations = settings.validations
    return Mk8sGpuValidationOverrides(
        operator_readiness_enabled=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="operator_readiness.enabled",
            coerce=_coerce_optional_bool,
            default=validations.operator_readiness.enabled_by_default,
            field_label="deploy.targets[].validations.mk8s_gpu.operator_readiness.enabled",
        ),
        gpu_visibility_enabled=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="gpu_visibility.enabled",
            coerce=_coerce_optional_bool,
            default=validations.gpu_visibility.enabled_by_default,
            field_label="deploy.targets[].validations.mk8s_gpu.gpu_visibility.enabled",
        ),
        gpu_visibility_max_nodes=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="gpu_visibility.max_nodes",
            coerce=_coerce_optional_positive_int,
            default=validations.gpu_visibility.max_nodes,
            field_label="deploy.targets[].validations.mk8s_gpu.gpu_visibility.max_nodes",
        ),
        nccl_enabled=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="nccl.enabled",
            coerce=_coerce_optional_bool,
            default=validations.nccl.enabled_by_default,
            field_label="deploy.targets[].validations.mk8s_gpu.nccl.enabled",
        ),
        nccl_max_nodes=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="nccl.max_nodes",
            coerce=_coerce_optional_positive_int,
            default=validations.nccl.max_nodes,
            field_label="deploy.targets[].validations.mk8s_gpu.nccl.max_nodes",
        ),
        nccl_average_bus_bandwidth_threshold_gbps=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="nccl.average_bus_bandwidth_threshold_gbps",
            coerce=_coerce_optional_positive_float,
            default=validations.nccl.average_bus_bandwidth_threshold_gbps,
            field_label="deploy.targets[].validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps",
        ),
        health_checker_enabled=_resolve_project_gpu_validation_setting(
            payload,
            target_ref=target_ref,
            dotted_path="health_checker.enabled",
            coerce=_coerce_optional_bool,
            default=validations.health_checker.enabled_by_default,
            field_label="deploy.targets[].validations.mk8s_gpu.health_checker.enabled",
        ),
    )


def _enabled_mk8s_gpu_contexts(payload: dict[str, Any]) -> tuple[Mk8sGpuClusterContext, ...]:
    infra_node = payload.get("infra")
    if not isinstance(infra_node, dict):
        return ()
    components = infra_node.get("components")
    if not isinstance(components, list):
        return ()

    mk8s_entry_ids = {
        entry.id
        for entry in component_entries("infra")
        if entry.validation_profile == "mk8s_cluster"
    }
    contexts: list[Mk8sGpuClusterContext] = []
    for row in components:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        if component_id not in mk8s_entry_ids:
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, dict) or not bool(inputs.get("gpu_enabled", False)):
            continue
        gpu_overrides = _mapping(inputs.get("mk8s_gpu_node_group_overrides"))
        gpu_template = _mapping(gpu_overrides.get("template"))
        gpu_resources = _mapping(gpu_template.get("resources"))
        gpu_platform = _as_text(gpu_resources.get("platform")) or _as_text(
            inputs.get("gpu_nodes_platform")
        )
        gpu_preset = _as_text(gpu_resources.get("preset")) or _as_text(
            inputs.get("gpu_nodes_preset")
        )
        gpu_stack_source = _gpu_stack_source(inputs)
        gpu_cluster_enabled = bool(_as_text(inputs.get("infiniband_fabric")))
        contexts.append(
            Mk8sGpuClusterContext(
                component_id=component_id,
                instance_id=component_instance_id(row) or component_id,
                gpu_platform=gpu_platform,
                gpu_preset=gpu_preset,
                gpu_cluster_enabled=gpu_cluster_enabled,
                gpu_stack_source=gpu_stack_source,
            )
        )
    return tuple(contexts)


def _app_chart_row(payload: dict[str, Any], app_id: str) -> dict[str, Any] | None:
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, dict) else None
    if not isinstance(charts, list):
        return None
    for row in charts:
        if isinstance(row, dict) and component_type_id(row) == app_id:
            return row
    return None


def _app_chart_rows(payload: dict[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        apps = {}
        payload["apps"] = apps
    charts = apps.get("charts")
    if not isinstance(charts, list):
        charts = []
        apps["charts"] = charts
    return charts


def _app_chart_rows_for_id(payload: dict[str, Any], app_id: str) -> tuple[dict[str, Any], ...]:
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, dict) else None
    if not isinstance(charts, list):
        return ()
    return tuple(
        row for row in charts if isinstance(row, dict) and component_type_id(row) == app_id
    )


def _chart_source_parts(entry: ComponentEntry) -> tuple[str, str]:
    source = str(entry.source or "").strip().rstrip("/")
    if "/" not in source:
        return "", source
    repo, chart_name = source.rsplit("/", maxsplit=1)
    return repo, chart_name


def _canonical_chart_repo(*, chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    if repo.startswith("oci://"):
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail != chart_name.strip().lower():
            return f"{repo}/{chart_name.strip()}"
    return repo


def _normalized_app_group(entry: ComponentEntry) -> str:
    raw_group = str(entry.group or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"


def _target_scoped_app_instance_id(app_id: str, *, target_ref: str) -> str:
    return target_scoped_app_instance_id(app_id, target_ref=target_ref)


def _normalize_target_scoped_app_instance_id(
    row: dict[str, Any],
    *,
    app_id: str,
    target_ref: str,
) -> None:
    if not str(row.get("instance_id", "")).strip() or is_auto_target_scoped_app_instance_id(
        row.get("instance_id"),
        app_id=app_id,
        target_ref=target_ref,
    ):
        row["instance_id"] = _target_scoped_app_instance_id(app_id, target_ref=target_ref)


def _apply_mk8s_gpu_app_entry_defaults(
    *,
    row: dict[str, Any],
    entry: ComponentEntry,
) -> None:
    chart_repo = str(entry.chart_repo or "").strip()
    chart_name = str(entry.chart_name or "").strip()
    if not chart_name:
        chart_repo, chart_name = _chart_source_parts(entry)
    if not chart_name:
        chart_name = entry.id
    if not str(row.get("repo", "")).strip():
        row["repo"] = _canonical_chart_repo(chart_repo=chart_repo, chart_name=chart_name)
    if not str(row.get("version", "")).strip() and entry.version:
        row["version"] = str(entry.version)
    if not str(row.get("namespace", "")).strip():
        row["namespace"] = str(entry.default_namespace or "").strip() or entry.id
    if not str(row.get("release-name", "")).strip():
        row["release-name"] = str(entry.default_release_name or "").strip() or entry.id
    if not str(row.get("group", "")).strip():
        row["group"] = _normalized_app_group(entry)
    if not isinstance(row.get("values"), dict):
        row["values"] = {}
    if entry.defaults:
        resolved = resolve_component_defaults(
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            include_shared=False,
        )
        row.clear()
        row.update(resolved)


def _new_mk8s_gpu_app_row(entry: ComponentEntry, *, target_ref: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": entry.id,
        "instance_id": _target_scoped_app_instance_id(entry.id, target_ref=target_ref),
        "group": _normalized_app_group(entry),
        "enabled": True,
        "repo": "",
        "version": "",
        "namespace": "",
        "release-name": "",
        "values": {},
    }
    if target_ref:
        row[TARGET_REF_FIELD] = target_ref
    _apply_mk8s_gpu_app_entry_defaults(row=row, entry=entry)
    return row


def _contexts_for_app_chart(
    chart: Mapping[str, Any],
    *,
    contexts: tuple[Mk8sGpuClusterContext, ...],
) -> tuple[Mk8sGpuClusterContext, ...]:
    target_ref = _app_chart_effective_target_ref(chart, contexts=contexts)
    if target_ref:
        return tuple(context for context in contexts if context.instance_id == target_ref)
    if len(contexts) == 1:
        return contexts
    return contexts


def _app_chart_effective_target_ref(
    chart: Mapping[str, Any],
    *,
    contexts: tuple[Mk8sGpuClusterContext, ...],
) -> str:
    target_ref = app_chart_target_ref(chart)
    if target_ref:
        return target_ref
    instance_id = component_instance_id(chart)
    if instance_id in {context.instance_id for context in contexts}:
        return instance_id
    return ""


def _selected_app_target_refs(
    payload: dict[str, Any],
    *,
    app_id: str,
    contexts: tuple[Mk8sGpuClusterContext, ...],
) -> set[str]:
    target_refs: set[str] = set()
    for row in _app_chart_rows_for_id(payload, app_id):
        if not bool(row.get("enabled", False)):
            continue
        row_target_ref = _app_chart_effective_target_ref(row, contexts=contexts)
        if row_target_ref:
            target_refs.add(row_target_ref)
        elif len(contexts) == 1:
            target_refs.add(contexts[0].instance_id)
    return target_refs


def _mk8s_gpu_shape_capabilities(
    payload: dict[str, Any],
    *,
    context: Mk8sGpuClusterContext,
) -> tuple[int | None, bool | None]:
    _vcpu_count, _memory_gibibytes, gpu_count, allow_gpu_clustering = (
        _mk8s_gpu_shape_resource_profile(
            payload,
            context=context,
        )
    )
    return gpu_count, allow_gpu_clustering


def _parse_preset_resource_hint(preset_name: str) -> tuple[int | None, int | None, int | None]:
    match = _PRESET_RESOURCE_HINT_RE.search(_as_text(preset_name))
    if match is None:
        return None, None, None
    return (
        int(match.group("vcpu")),
        int(match.group("memory")),
        int(match.group("gpu")),
    )


def _mk8s_gpu_shape_resource_profile(
    payload: dict[str, Any],
    *,
    context: Mk8sGpuClusterContext,
) -> tuple[int | None, int | None, int | None, bool | None]:
    vcpu_count: int | None = None
    memory_gibibytes: int | None = None
    gpu_count = _parse_gpu_quantity(context.gpu_preset) or None
    allow_gpu_clustering: bool | None = True if context.gpu_cluster_enabled else None
    project_id = _as_text(
        _nested_path_value(
            _mapping(_mapping(payload.get("client_info")).get("nebius")), "project_id"
        )
    )
    if project_id and context.gpu_platform and context.gpu_preset:
        from .provider_options import ProviderOptionLookup

        lookup = ProviderOptionLookup()
        resources = lookup.compute_platform_preset_resources(
            project_id=project_id,
            platform_name=context.gpu_platform,
            preset_name=context.gpu_preset,
        )
        if resources is not None:
            if resources[0] is not None:
                vcpu_count = resources[0]
            if resources[1] is not None:
                memory_gibibytes = resources[1]
            if resources[2] is not None:
                gpu_count = resources[2]
        resolved_clustering = lookup.compute_platform_preset_allows_gpu_clustering(
            project_id=project_id,
            platform_name=context.gpu_platform,
            preset_name=context.gpu_preset,
        )
        if resolved_clustering is not None:
            allow_gpu_clustering = resolved_clustering

    hinted_vcpu_count, hinted_memory_gibibytes, hinted_gpu_count = _parse_preset_resource_hint(
        context.gpu_preset
    )
    if vcpu_count is None:
        vcpu_count = hinted_vcpu_count
    if memory_gibibytes is None:
        memory_gibibytes = hinted_memory_gibibytes
    if gpu_count is None:
        gpu_count = hinted_gpu_count
    return vcpu_count, memory_gibibytes, gpu_count, allow_gpu_clustering


def _nccl_transport_label(mode: str) -> str:
    normalized = _as_text(mode).lower()
    if normalized == "rdma":
        return "RDMA verbs (IB/RoCE)"
    if normalized == "socket":
        return "Socket/TCPIP"
    return "auto"


def _target_scoped_validation_name(name: str, *, target_ref: str) -> str:
    normalized_target_ref = _as_text(target_ref)
    if not normalized_target_ref:
        return name
    return f"{name} ({normalized_target_ref})"


def _target_scoped_report_file(report_file: str, *, target_ref: str) -> str:
    normalized_target_ref = _as_text(target_ref)
    if not normalized_target_ref:
        return report_file
    report_path = Path(report_file)
    return f"{report_path.stem}-{normalized_target_ref}{report_path.suffix}"


def mk8s_gpu_validation_warnings(
    payload_or_config: Any,
    *,
    cli_settings: Any | None = None,
) -> tuple[str, ...]:
    _ = cli_settings
    payload = _as_payload(payload_or_config)
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return ()

    warnings: list[str] = []
    generic_warning_emitted = False
    for context in contexts:
        validation_overrides = _effective_mk8s_gpu_validation_overrides(
            payload,
            target_ref=context.instance_id,
        )
        if not validation_overrides.nccl_enabled or context.gpu_cluster_enabled:
            continue
        gpu_count, allow_gpu_clustering = _mk8s_gpu_shape_capabilities(payload, context=context)
        shape_label = (
            "/".join(part for part in (context.gpu_platform, context.gpu_preset) if part)
            or context.instance_id
        )
        if gpu_count == 1 and allow_gpu_clustering is not True:
            warnings.append(
                "deploy.targets[].validations.mk8s_gpu.nccl.enabled is set, but selected MK8s GPU shape "
                f"{shape_label} is a 1-GPU Ethernet-only node group. NCCL here uses "
                "Ethernet/TCPIP rather than InfiniBand / GPUDirect-RDMA, so performance is "
                "degraded and this is not a representative production training test."
            )
            continue
        if allow_gpu_clustering is False:
            warnings.append(
                "deploy.targets[].validations.mk8s_gpu.nccl.enabled is set, but selected MK8s GPU shape "
                f"{shape_label} is not compatible with GPU clusters according to live Nebius "
                "preset metadata. NCCL will run in Ethernet/TCPIP socket mode instead of "
                "GPUDirect-RDMA for this shape."
            )
            continue
        if not generic_warning_emitted:
            warnings.append(
                "deploy.targets[].validations.mk8s_gpu.nccl.enabled is set, but no MK8s GPU node group has "
                "an InfiniBand fabric configured. NCCL will run in Ethernet/TCPIP socket mode "
                "until a GPU-cluster-compatible preset and fabric are selected."
            )
            generic_warning_emitted = True
    return tuple(dict.fromkeys(warnings))


def resolve_mk8s_gpu_app_selection(
    payload_or_config: Any,
    *,
    selected_app_ids: set[str] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> Mk8sGpuAppSelection:
    payload = _as_payload(payload_or_config)
    contexts = _enabled_mk8s_gpu_contexts(payload)
    selected = {
        str(item).strip().lower() for item in (selected_app_ids or set()) if str(item).strip()
    }
    if not contexts:
        return Mk8sGpuAppSelection(
            selected_app_ids=tuple(sorted(selected)),
            auto_enabled_app_ids=(),
            issues=(),
            cluster_contexts=(),
            gpu_stack_source="",
        )

    available_ids = (
        {entry.id for entry in app_entries}
        if app_entries is not None
        else {entry.id for entry in component_entries("apps")}
    )
    settings = _mk8s_gpu_settings()
    stack_sources = {item.gpu_stack_source for item in contexts}
    resolved_stack_source = next(iter(stack_sources)) if len(stack_sources) == 1 else "mixed"
    health_checker_enabled = any(
        _effective_mk8s_gpu_validation_overrides(
            payload,
            target_ref=context.instance_id,
            gpu_settings=settings,
        ).health_checker_enabled
        for context in contexts
    )
    required_app_ids = _required_gpu_app_ids(
        contexts,
        gpu_settings=settings,
        health_checker_enabled=health_checker_enabled,
    )

    issues: list[str] = []
    auto_enabled: list[str] = []
    if health_checker_enabled and "health_checker" not in _mk8s_gpu_app_id_by_role():
        issues.append(
            "deploy.targets[].validations.mk8s_gpu.health_checker.enabled requires one apps "
            "component with cli.mk8s_gpu_policy.role: health_checker"
        )
    gpu_operator_id = _mk8s_gpu_app_id_by_role().get("gpu_operator")
    if gpu_operator_id:
        gpu_operator_rows = [
            row
            for row in _app_chart_rows_for_id(payload, gpu_operator_id)
            if bool(row.get("enabled", False))
        ]
        if not gpu_operator_rows:
            gpu_operator_row = _app_chart_row(payload, gpu_operator_id)
            gpu_operator_rows = [gpu_operator_row] if gpu_operator_row else []
        for gpu_operator_row in gpu_operator_rows:
            if (
                _nested_path_value(_mapping(gpu_operator_row), "values.dcgmExporter.enabled")
                is not False
            ):
                continue
            target_ref = app_chart_target_ref(gpu_operator_row)
            target_phrase = f" target '{target_ref}'" if target_ref else ""
            issues.append(
                f"GPU-enabled MK8s{target_phrase} deployment requires "
                f"'{gpu_operator_id}.values.dcgmExporter.enabled' to stay true"
            )
    for app_id in required_app_ids:
        if app_id not in available_ids:
            issues.append(
                f"components.apps.{app_id}.cli.mk8s_gpu_policy references an unknown or unavailable app component"
            )
            continue
        if app_id not in selected:
            selected.add(app_id)
            auto_enabled.append(app_id)

    return Mk8sGpuAppSelection(
        selected_app_ids=tuple(sorted(selected)),
        auto_enabled_app_ids=tuple(sorted(auto_enabled)),
        issues=tuple(issues),
        cluster_contexts=contexts,
        gpu_stack_source=resolved_stack_source,
    )


def ensure_mk8s_gpu_app_rows(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> bool:
    _ = cli_settings
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    if not payload:
        return False
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return False
    entries = app_entries if app_entries is not None else component_entries("apps")
    entry_by_id = {entry.id: entry for entry in entries}
    settings = _mk8s_gpu_settings()
    charts = _app_chart_rows(payload)
    changed = False

    for context in contexts:
        validation_overrides = _effective_mk8s_gpu_validation_overrides(
            payload,
            target_ref=context.instance_id,
            gpu_settings=settings,
        )
        required_app_ids = _required_gpu_app_ids(
            (context,),
            gpu_settings=settings,
            health_checker_enabled=validation_overrides.health_checker_enabled,
        )
        for app_id in required_app_ids:
            entry = entry_by_id.get(app_id)
            if entry is None:
                continue
            rows_for_app = _app_chart_rows_for_id(payload, app_id)
            existing = next(
                (
                    row
                    for row in rows_for_app
                    if _app_chart_effective_target_ref(
                        row,
                        contexts=contexts,
                    )
                    == context.instance_id
                ),
                None,
            )
            if existing is None:
                existing = next(
                    (
                        row
                        for row in rows_for_app
                        if not app_chart_target_ref(row) and not bool(row.get("enabled", False))
                    ),
                    None,
                )
            if existing is None:
                existing = next(
                    (row for row in rows_for_app if not app_chart_target_ref(row)),
                    None,
                )
            if existing is None:
                charts.append(_new_mk8s_gpu_app_row(entry, target_ref=context.instance_id))
                changed = True
                continue

            before = copy.deepcopy(existing)
            previous_target_ref = app_chart_target_ref(existing)
            existing["enabled"] = True
            if not str(existing.get("id", "")).strip():
                existing["id"] = entry.id
            if app_chart_target_ref(existing) != context.instance_id:
                existing[TARGET_REF_FIELD] = context.instance_id
            if not previous_target_ref:
                existing["instance_id"] = _target_scoped_app_instance_id(
                    app_id,
                    target_ref=context.instance_id,
                )
            else:
                _normalize_target_scoped_app_instance_id(
                    existing,
                    app_id=app_id,
                    target_ref=context.instance_id,
                )
            _apply_mk8s_gpu_app_entry_defaults(row=existing, entry=entry)
            if existing != before:
                changed = True
    return changed


def mk8s_gpu_dependency_issues(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> list[str]:
    payload = _as_payload(payload_or_config)
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, dict) else []
    selected = {
        component_type_id(row)
        for row in charts
        if isinstance(row, dict) and bool(row.get("enabled", False)) and component_type_id(row)
    }
    resolution = resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=selected,
        app_entries=app_entries,
        cli_settings=cli_settings,
    )
    issues = list(resolution.issues)
    expected = set(
        resolve_mk8s_gpu_app_selection(
            payload,
            selected_app_ids=set(),
            app_entries=app_entries,
            cli_settings=cli_settings,
        ).selected_app_ids
    )
    missing = sorted(expected - selected)
    for app_id in missing:
        issues.append(f"GPU-enabled MK8s deployment requires 'apps:{app_id}' to be enabled")
    if len(resolution.cluster_contexts) > 1:
        for context in resolution.cluster_contexts:
            for app_id in _required_gpu_app_ids((context,)):
                target_refs = _selected_app_target_refs(
                    payload,
                    app_id=app_id,
                    contexts=resolution.cluster_contexts,
                )
                if context.instance_id not in target_refs:
                    issues.append(
                        f"GPU-enabled MK8s target '{context.instance_id}' requires "
                        f"'apps:{app_id}' to be enabled with instance_id '{context.instance_id}'"
                    )
    return issues


def mk8s_gpu_flux_release_dependencies(
    payload_or_config: Any,
    *,
    release_entry_ids: set[str],
    cli_settings: Any | None = None,
) -> dict[str, tuple[str, ...]]:
    payload = _as_payload(payload_or_config)
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return {}

    dependencies: dict[str, tuple[str, ...]] = {}
    for app_id in release_entry_ids:
        policy = _mk8s_gpu_app_policies().get(app_id)
        if policy is None or not policy.install_after:
            continue
        required = tuple(dep for dep in policy.install_after if dep in release_entry_ids)
        if required:
            dependencies[app_id] = required
    return dependencies


def mk8s_gpu_validation_specs(
    payload_or_config: Any,
    *,
    cli_settings: Any | None = None,
) -> list[dict[str, Any]]:
    payload = _as_payload(payload_or_config)
    settings = _mk8s_gpu_settings()
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return []
    validation_overrides_by_target = {
        context.instance_id: _effective_mk8s_gpu_validation_overrides(
            payload,
            target_ref=context.instance_id,
            gpu_settings=settings,
        )
        for context in contexts
    }
    validations: list[dict[str, Any]] = []
    app_entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    role_map = _mk8s_gpu_app_id_by_role()
    # Keep the validation chain layered from cheapest to most expensive:
    # 1. operator/network control-plane readiness
    # 2. bounded single-node CUDA workload visibility
    # 3. optional multi-node NCCL communication/performance
    # The checks are intentionally complementary rather than interchangeable.
    operator_readiness = settings.validations.operator_readiness
    if any(item.operator_readiness_enabled for item in validation_overrides_by_target.values()):
        if not operator_readiness.timeout:
            raise ValueError(
                "components.infra.mk8s.cli.gpu.validations.operator_readiness.timeout is required when operator_readiness is enabled"
            )
        gpu_operator_id = role_map.get("gpu_operator", "")
        network_operator_id = role_map.get("network_operator", "")
        gpu_operator_namespace = _as_text(
            getattr(app_entry_by_id.get(gpu_operator_id), "default_namespace", "")
        )
        network_operator_namespace = _as_text(
            getattr(app_entry_by_id.get(network_operator_id), "default_namespace", "")
        )
        if not gpu_operator_namespace:
            raise ValueError(
                "component_cli_settings.yaml must declare one apps component with "
                "cli.mk8s_gpu_policy.role: gpu_operator, and component_sources.yaml "
                "must declare that app release namespace"
            )
        for context in contexts:
            validation_overrides = validation_overrides_by_target[context.instance_id]
            if not validation_overrides.operator_readiness_enabled:
                continue
            required_app_ids = set(
                _required_gpu_app_ids(
                    (context,),
                    gpu_settings=settings,
                    health_checker_enabled=validation_overrides.health_checker_enabled,
                )
            )
            if network_operator_id in required_app_ids and not network_operator_namespace:
                raise ValueError(
                    "component_cli_settings.yaml must declare one apps component with "
                    "cli.mk8s_gpu_policy.role: network_operator, and component_sources.yaml "
                    "must declare that app release namespace"
                )
            validations.append(
                {
                    "kind": "mk8s_gpu_operator_readiness",
                    "target_ref": context.instance_id,
                    "name": _target_scoped_validation_name(
                        _GPU_STACK_VALIDATION_NAME,
                        target_ref=context.instance_id,
                    ),
                    "timeout": operator_readiness.timeout,
                    "gpu_operator_namespace": gpu_operator_namespace,
                    "network_operator_namespace": network_operator_namespace,
                    "network_operator_required": network_operator_id in required_app_ids,
                    "gpu_cluster_enabled": context.gpu_cluster_enabled,
                    "report_file": _target_scoped_report_file(
                        "gpu-stack-readiness-report.json",
                        target_ref=context.instance_id,
                    ),
                }
            )

    gpu_visibility = settings.validations.gpu_visibility
    if any(item.gpu_visibility_enabled for item in validation_overrides_by_target.values()):
        if not gpu_visibility.namespace or not gpu_visibility.image or not gpu_visibility.timeout:
            raise ValueError(
                "components.infra.mk8s.cli.gpu.validations.gpu_visibility must set namespace, image, and timeout"
            )
        for context in contexts:
            validation_overrides = validation_overrides_by_target[context.instance_id]
            if not validation_overrides.gpu_visibility_enabled:
                continue
            validations.append(
                {
                    "kind": "mk8s_gpu_visibility",
                    "target_ref": context.instance_id,
                    "name": _target_scoped_validation_name(
                        "GPU Visibility test",
                        target_ref=context.instance_id,
                    ),
                    "namespace": gpu_visibility.namespace,
                    "image": gpu_visibility.image,
                    "timeout": gpu_visibility.timeout,
                    "cleanup": gpu_visibility.cleanup,
                    "max_nodes": validation_overrides.gpu_visibility_max_nodes,
                    "report_file": _target_scoped_report_file(
                        "gpu-visibility-report.json",
                        target_ref=context.instance_id,
                    ),
                }
            )

    nccl = settings.validations.nccl
    if any(item.nccl_enabled for item in validation_overrides_by_target.values()):
        if (
            not nccl.chart_component_id
            or not nccl.timeout
            or not nccl.training_operator_manifest
            or not nccl.training_operator_namespace
        ):
            raise ValueError(
                "components.infra.mk8s.cli.gpu.validations.nccl is missing one or more required settings"
            )
        chart_entry = helm_chart_source_by_id(nccl.chart_component_id, sources=_catalog_sources())
        if chart_entry is None:
            raise ValueError(
                "components.infra.mk8s.cli.gpu.validations.nccl.chart_component_id references "
                f"unknown apps chart '{nccl.chart_component_id}'"
            )
        chart_name_or_ref, chart_repo, chart_version = _helm_chart_reference(chart_entry)
        if not chart_name_or_ref:
            raise ValueError(
                f"components.apps.{nccl.chart_component_id} has no resolvable Helm chart source"
            )
        chart_values = _nccl_chart_runtime_defaults(
            chart_name_or_ref=chart_name_or_ref,
            chart_repo=chart_repo,
            chart_version=chart_version,
        )
        chart_values = _deep_merge_dict(
            chart_values,
            _defaults_to_mapping(chart_entry.defaults, strip_root="values"),
        )
        namespace = _as_text(chart_entry.namespace) or nccl.chart_component_id
        for context in contexts:
            validation_overrides = validation_overrides_by_target[context.instance_id]
            if not validation_overrides.nccl_enabled:
                continue
            gpu_count, allow_gpu_clustering = _mk8s_gpu_shape_capabilities(payload, context=context)
            nccl_context = Mk8sNcclValidationContext(
                context=context,
                gpu_count=gpu_count,
                allow_gpu_clustering=allow_gpu_clustering,
                transport_mode="rdma" if context.gpu_cluster_enabled else "socket",
                threshold_enforced=bool(context.gpu_cluster_enabled),
            )
            scoped_chart_values = copy.deepcopy(chart_values)
            scoped_chart_values = _deep_merge_dict(
                scoped_chart_values,
                _defaults_to_mapping(
                    _resolved_app_value_defaults(
                        app_id=nccl.chart_component_id,
                        contexts=(nccl_context.context,),
                    ),
                    strip_root="values",
                ),
            )
            scoped_chart_values = _deep_merge_dict(
                scoped_chart_values,
                _nccl_chart_transport_defaults(nccl_context.transport_mode),
            )
            if nccl_context.transport_mode == "rdma" and nccl.rdma_mpi_extra_args:
                _append_nccl_benchmark_mpi_extra_args(
                    scoped_chart_values,
                    nccl.rdma_mpi_extra_args,
                )
            (
                _worker_vcpu_count,
                _worker_memory_gibibytes,
                worker_gpu_count,
                _allow_gpu_clustering,
            ) = _mk8s_gpu_shape_resource_profile(payload, context=nccl_context.context)
            scoped_chart_values = _deep_merge_dict(
                scoped_chart_values,
                _nccl_chart_worker_shape_defaults(
                    gpu_count=worker_gpu_count,
                ),
            )
            validations.append(
                {
                    "kind": "mk8s_nccl",
                    "target_ref": context.instance_id,
                    "name": _target_scoped_validation_name(
                        "NCCL test",
                        target_ref=context.instance_id,
                    ),
                    "chart_component_id": nccl.chart_component_id,
                    "chart_name_or_ref": chart_name_or_ref,
                    "chart_repo": chart_repo,
                    "chart_version": chart_version,
                    "namespace": namespace,
                    "timeout": nccl.timeout,
                    "max_nodes": validation_overrides.nccl_max_nodes,
                    "training_operator_manifest": nccl.training_operator_manifest,
                    "training_operator_namespace": nccl.training_operator_namespace,
                    "average_bus_bandwidth_threshold_gbps": validation_overrides.nccl_average_bus_bandwidth_threshold_gbps,
                    "threshold_enforced": nccl_context.threshold_enforced,
                    "chart_values": scoped_chart_values,
                    "gpu_platform": nccl_context.context.gpu_platform,
                    "gpu_cluster_enabled": nccl_context.context.gpu_cluster_enabled,
                    "transport_mode": nccl_context.transport_mode,
                    "report_file": _target_scoped_report_file(
                        "nccl-test-report.json",
                        target_ref=context.instance_id,
                    ),
                }
            )

    return validations


def _parse_gpu_quantity(raw: Any) -> int:
    match = _GPU_QUANTITY_RE.match(_as_text(raw))
    if match is None:
        return 0
    return int(match.group(0))


def _path_segments(path: str) -> list[str]:
    return [segment for segment in path.split(".") if segment]


def _path_value(node: dict[str, Any], path: str) -> Any:
    current: Any = node
    for segment in _path_segments(path):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_path_value(node: dict[str, Any], path: str, value: Any) -> None:
    segments = _path_segments(path)
    if not segments:
        return
    current = node
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
        current[segment] = child
        current = child
    current[segments[-1]] = value


def _delete_path_value(node: dict[str, Any], path: str) -> None:
    segments = _path_segments(path)
    if not segments:
        return
    current: Any = node
    parents: list[tuple[dict[str, Any], str]] = []
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return
        parents.append((current, segment))
        current = current[segment]
    if not isinstance(current, dict) or segments[-1] not in current:
        return
    del current[segments[-1]]
    for parent, segment in reversed(parents):
        child = parent.get(segment)
        if isinstance(child, dict) and not child:
            del parent[segment]
            continue
        break


def _managed_app_value_paths(*, app_id: str) -> tuple[str, ...]:
    policy = _mk8s_gpu_app_policies().get(app_id)
    if policy is None:
        return ()
    paths: set[str] = set()
    for default_set in policy.default_sets:
        for default in default_set.defaults:
            if default.target_path:
                paths.add(default.target_path)
    for rule in policy.rules:
        for default in rule.defaults:
            if default.target_path:
                paths.add(default.target_path)
    return tuple(sorted(paths))


def _defaults_to_mapping(
    defaults: tuple[ComponentDefault, ...],
    *,
    strip_root: str = "",
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for item in defaults:
        if not item.target_path:
            continue
        target_path = item.target_path
        if strip_root:
            prefix = f"{strip_root}."
            if target_path == strip_root:
                continue
            if target_path.startswith(prefix):
                target_path = target_path[len(prefix) :]
        _set_path_value(rendered, target_path, item.value)
    return rendered


def _deep_merge_dict(target: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge_dict(target[key], value)
            continue
        target[key] = value
    return target


def _nccl_chart_runtime_defaults(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
) -> dict[str, Any]:
    defaults = helm_chart_default_values(
        chart_name_or_ref=chart_name_or_ref,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )
    if not isinstance(defaults, dict):
        return {}

    result: dict[str, Any] = {}

    image = defaults.get("image")
    if isinstance(image, dict):
        image_defaults: dict[str, Any] = {}
        repository = _as_text(image.get("repository"))
        tag = _as_text(image.get("tag"))
        if repository:
            image_defaults["repository"] = repository
        if tag:
            image_defaults["tag"] = tag
        if image_defaults:
            result["image"] = image_defaults

    benchmark = defaults.get("benchmark")
    if isinstance(benchmark, dict):
        benchmark_defaults: dict[str, Any] = {}
        binary_path = _as_text(benchmark.get("binaryPath"))
        result_json_path = _as_text(benchmark.get("resultJsonPath"))
        if binary_path:
            benchmark_defaults["binaryPath"] = binary_path
        if result_json_path:
            benchmark_defaults["resultJsonPath"] = result_json_path
        for key in ("mpiBaseArgs", "mpiExtraArgs", "args"):
            value = benchmark.get(key)
            if isinstance(value, list) and value:
                benchmark_defaults[key] = copy.deepcopy(value)
        transport = benchmark.get("transport")
        if isinstance(transport, dict):
            transport_defaults: dict[str, Any] = {}
            mode = _as_text(transport.get("mode"))
            socket_if_name = _as_text(transport.get("socketIfName"))
            ib_hca = _as_text(transport.get("ibHca"))
            if mode:
                transport_defaults["mode"] = mode
            if socket_if_name:
                transport_defaults["socketIfName"] = socket_if_name
            if ib_hca:
                transport_defaults["ibHca"] = ib_hca
            if transport_defaults:
                benchmark_defaults["transport"] = transport_defaults
        if benchmark_defaults:
            result["benchmark"] = benchmark_defaults

    worker = defaults.get("worker")
    if isinstance(worker, dict) and worker.get("gpus") is not None:
        result["worker"] = {"gpus": copy.deepcopy(worker.get("gpus"))}

    return result


def _nccl_chart_worker_shape_defaults(
    *,
    gpu_count: int | None,
) -> dict[str, Any]:
    worker_defaults: dict[str, Any] = {}
    if gpu_count is not None and gpu_count > 0:
        worker_defaults["gpus"] = gpu_count
    return {"worker": worker_defaults} if worker_defaults else {}


def _helm_chart_reference(chart: Any) -> tuple[str, str, str]:
    chart_path = _as_text(getattr(chart, "path", ""))
    if chart_path:
        return chart_path, "", ""
    chart_name = _as_text(getattr(chart, "chart_name", "")) or _as_text(getattr(chart, "name", ""))
    chart_repo = _as_text(getattr(chart, "repo", ""))
    chart_version = _as_text(getattr(chart, "version", ""))
    return chart_name, chart_repo, chart_version


def _resolved_app_value_defaults(
    *,
    app_id: str,
    contexts: tuple[Mk8sGpuClusterContext, ...],
) -> tuple[ComponentDefault, ...]:
    policy = _mk8s_gpu_app_policies().get(app_id)
    if policy is None:
        return ()
    default_set_map = {item.name: item.defaults for item in policy.default_sets}
    defaults: list[ComponentDefault] = []
    for rule in policy.rules:
        if not rule.defaults and not rule.defaults_from:
            continue
        if any(_app_rule_matches(context, rule) for context in contexts):
            defaults.extend(rule.defaults)
            for set_name in rule.defaults_from:
                defaults.extend(default_set_map.get(set_name, ()))
    return tuple(defaults)


def _nccl_chart_transport_defaults(transport_mode: str) -> dict[str, Any]:
    normalized = _as_text(transport_mode).lower()
    if normalized == "socket":
        return {
            "benchmark": {
                "transport": {
                    "mode": "socket",
                }
            }
        }
    if normalized == "rdma":
        return {
            "benchmark": {
                "transport": {
                    "mode": "rdma",
                }
            }
        }
    return {}


def _append_nccl_benchmark_mpi_extra_args(
    chart_values: dict[str, Any],
    extra_args: tuple[str, ...],
) -> None:
    if not extra_args:
        return
    benchmark = chart_values.setdefault("benchmark", {})
    if not isinstance(benchmark, dict):
        benchmark = {}
        chart_values["benchmark"] = benchmark
    existing = benchmark.get("mpiExtraArgs")
    merged: list[Any] = []
    if isinstance(existing, list):
        merged.extend(existing)
    merged.extend(extra_args)
    benchmark["mpiExtraArgs"] = merged


def _resolved_app_post_render_patches(
    *,
    app_id: str,
    contexts: tuple[Mk8sGpuClusterContext, ...],
) -> tuple[FluxPostRenderPatch, ...]:
    policy = _mk8s_gpu_app_policies().get(app_id)
    if policy is None:
        return ()
    patch_set_map = {item.name: item.patches for item in policy.post_render_patch_sets}
    patches: list[FluxPostRenderPatch] = []
    for rule in policy.rules:
        if not rule.post_render_patches and not rule.post_render_patches_from:
            continue
        if any(_app_rule_matches(context, rule) for context in contexts):
            patches.extend(rule.post_render_patches)
            for set_name in rule.post_render_patches_from:
                patches.extend(patch_set_map.get(set_name, ()))
    return tuple(patches)


def _ensure_mk8s_gpu_rule_compatibility(
    *,
    contexts: tuple[Mk8sGpuClusterContext, ...],
    policies: Mapping[str, Mk8sGpuAppPolicy],
) -> None:
    stack_sources = {item.gpu_stack_source for item in contexts}
    if len(stack_sources) != 1:
        raise RuntimeError(
            "GPU-enabled MK8s config mixes Nebius-image and operator-managed GPU stack sources; split them into separate deployments"
        )
    if any(
        (
            rule.defaults
            or rule.defaults_from
            or rule.post_render_patches
            or rule.post_render_patches_from
        )
        and (rule.match_platforms or rule.match_presets)
        for policy in policies.values()
        for rule in policy.rules
    ):
        platforms = {item.gpu_platform for item in contexts}
        presets = {item.gpu_preset for item in contexts}
        if len(platforms) > 1 or len(presets) > 1:
            raise RuntimeError(
                "Platform- or preset-specific MK8s app rules with defaults or post-render patches require a single GPU platform/preset per deployment"
            )
    if any(
        (
            rule.defaults
            or rule.defaults_from
            or rule.post_render_patches
            or rule.post_render_patches_from
        )
        and rule.gpu_cluster_enabled is not None
        for policy in policies.values()
        for rule in policy.rules
    ):
        cluster_states = {item.gpu_cluster_enabled for item in contexts}
        if len(cluster_states) > 1:
            raise RuntimeError(
                "GPU-cluster-specific MK8s app rules with defaults or post-render patches require all GPU-enabled MK8s components in a deployment to agree on whether GPU clustering is enabled"
            )


def _post_render_patch_target_dict(patch: FluxPostRenderPatch) -> dict[str, str]:
    target = patch.target
    return {
        key: value
        for key, value in {
            "group": _as_text(target.group),
            "version": _as_text(target.version),
            "kind": _as_text(target.kind),
            "name": _as_text(target.name),
            "namespace": _as_text(target.namespace),
        }.items()
        if value
    }


def mk8s_gpu_flux_release_post_render_patches(
    payload_or_config: Any,
    *,
    release_entry_ids: set[str],
    cli_settings: Any | None = None,
) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    payload = _as_payload(payload_or_config)
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return {}
    policies = _mk8s_gpu_app_policies()
    patch_map: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
    for app_id in release_entry_ids:
        if app_id not in policies:
            continue
        for context in contexts:
            scoped_contexts = (context,)
            _ensure_mk8s_gpu_rule_compatibility(contexts=scoped_contexts, policies=policies)
            patches = _resolved_app_post_render_patches(app_id=app_id, contexts=scoped_contexts)
            if not patches:
                continue
            patch_map[(context.instance_id, app_id)] = tuple(
                {
                    "target": _post_render_patch_target_dict(patch),
                    "patch": patch.patch,
                }
                for patch in patches
            )
    return patch_map


def materialize_mk8s_gpu_app_values(payload_or_config: Any) -> bool:
    payload = (
        payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    )
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, dict) else None
    if not isinstance(charts, list):
        return False
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return False

    policies = _mk8s_gpu_app_policies()
    changed = False

    for chart in charts:
        if not isinstance(chart, dict) or not bool(chart.get("enabled", False)):
            continue
        chart_id = component_type_id(chart)
        if chart_id not in policies:
            continue
        scoped_contexts = _contexts_for_app_chart(chart, contexts=contexts)
        if not scoped_contexts:
            continue
        before = copy.deepcopy(chart)
        _ensure_mk8s_gpu_rule_compatibility(contexts=scoped_contexts, policies=policies)
        resolved_defaults = _resolved_app_value_defaults(
            app_id=chart_id,
            contexts=scoped_contexts,
        )
        # Shared Helm releases cannot safely accept conflicting values from
        # different GPU contexts in one deployment.
        values_by_path: dict[str, Any] = {}
        for default in resolved_defaults:
            existing = values_by_path.get(default.target_path, default.value)
            if default.target_path in values_by_path and existing != default.value:
                raise RuntimeError(
                    f"MK8s GPU app rules resolve conflicting defaults for '{chart_id}' at '{default.target_path}'"
                )
            values_by_path[default.target_path] = default.value
        for stale_path in sorted(
            set(_managed_app_value_paths(app_id=chart_id)) - set(values_by_path),
            key=lambda item: item.count("."),
            reverse=True,
        ):
            _delete_path_value(chart, stale_path)
        for target_path, target_value in values_by_path.items():
            _set_path_value(chart, target_path, copy.deepcopy(target_value))
        if chart != before:
            changed = True
    return changed


def _run_kubectl(
    args: list[str],
    *,
    extra_env: dict[str, str] | None,
    timeout_seconds: int = 300,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            ["kubectl", *args],
            env=env,
            capture_output=True,
            text=True,
            input=stdin_text,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl is required for MK8s GPU validation workflows") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"kubectl {' '.join(args)} timed out after {timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "kubectl command failed"
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {detail}")
    return completed


def _kubectl_json(
    args: list[str],
    *,
    extra_env: dict[str, str] | None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    completed = _run_kubectl(args, extra_env=extra_env, timeout_seconds=timeout_seconds)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kubectl {' '.join(args)} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"kubectl {' '.join(args)} did not return a JSON object")
    return payload


def _parse_go_duration_seconds(raw: str) -> int:
    text = _as_text(raw)
    if not text:
        raise RuntimeError("GPU validation timeout must not be empty")
    total = 0.0
    position = 0
    for match in re.finditer(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)", text):
        if match.start() != position:
            raise RuntimeError(f"Unsupported Go-style duration: {raw}")
        value = float(match.group(1))
        unit = match.group(2)
        position = match.end()
        total += (
            value
            * {
                "ns": 1e-9,
                "us": 1e-6,
                "µs": 1e-6,
                "ms": 1e-3,
                "s": 1.0,
                "m": 60.0,
                "h": 3600.0,
            }[unit]
        )
    if position != len(text):
        raise RuntimeError(f"Unsupported Go-style duration: {raw}")
    return max(1, int(total))


def _write_report(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _report_log_excerpt(logs: str, *, limit: int) -> str:
    text = logs or ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _gpu_visibility_node_report(
    *,
    node_name: str,
    pod_name: str,
    gpu_count: int,
    allocatable_resources: Mapping[str, str] | None = None,
    phase: str,
    passed: bool,
    logs: str,
) -> dict[str, Any]:
    report = {
        "node_name": node_name,
        "pod_name": pod_name,
        "gpu_count": gpu_count,
        "phase": phase,
        "passed": passed,
    }
    if allocatable_resources:
        report["allocatable_resources"] = dict(allocatable_resources)
    if not passed and logs:
        report["log_excerpt"] = _report_log_excerpt(logs, limit=4000)
    return report


def _resource_ready(
    *,
    resource_type: str,
    namespace: str | None,
    expected_status_key: str,
    expected_ready_value: str,
    extra_env: dict[str, str] | None,
    timeout_seconds: int,
) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        args = ["get", resource_type]
        if namespace:
            args.extend(["-n", namespace])
        args.extend(["-o", "json"])
        try:
            payload = _kubectl_json(args, extra_env=extra_env, timeout_seconds=30)
        except RuntimeError:
            time.sleep(5)
            continue
        last_payload = payload
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            time.sleep(5)
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if not isinstance(status, dict):
                continue
            if _as_text(status.get(expected_status_key)).lower() == expected_ready_value.lower():
                return True, item
        time.sleep(5)
    return False, last_payload


def _daemonset_summary(
    *,
    namespace: str,
    extra_env: dict[str, str] | None,
) -> list[dict[str, Any]]:
    try:
        payload = _kubectl_json(
            ["get", "daemonset", "-n", namespace, "-o", "json"],
            extra_env=extra_env,
            timeout_seconds=30,
        )
    except RuntimeError:
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    summary: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        if not isinstance(metadata, dict) or not isinstance(status, dict):
            continue
        summary.append(
            {
                "name": _as_text(metadata.get("name")),
                "desired": int(status.get("desiredNumberScheduled", 0) or 0),
                "ready": int(status.get("numberReady", 0) or 0),
                "available": int(status.get("numberAvailable", 0) or 0),
            }
        )
    return summary


def _daemonset_rollout_summary(daemonsets: list[dict[str, Any]]) -> str:
    if not daemonsets:
        return "daemonsets not reported yet"
    ready_sets = sum(
        1
        for item in daemonsets
        if int(item.get("desired", 0) or 0) > 0
        and int(item.get("ready", 0) or 0) >= int(item.get("desired", 0) or 0)
    )
    details = ", ".join(
        f"{_as_text(item.get('name'))}:{int(item.get('ready', 0) or 0)}/{int(item.get('desired', 0) or 0)}"
        for item in daemonsets[:3]
    )
    if len(daemonsets) > 3:
        details += f", +{len(daemonsets) - 3} more"
    return f"daemonsets {ready_sets}/{len(daemonsets)} ready; {details}"


def _resource_state_snapshot(
    *,
    resource_type: str,
    namespace: str | None,
    expected_status_key: str,
    expected_ready_value: str,
    extra_env: dict[str, str] | None,
) -> tuple[bool, dict[str, Any], str]:
    args = ["get", resource_type]
    if namespace:
        args.extend(["-n", namespace])
    args.extend(["-o", "json"])
    try:
        payload = _kubectl_json(args, extra_env=extra_env, timeout_seconds=30)
    except RuntimeError as exc:
        return False, {}, str(exc)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return False, {}, f"{resource_type} not reported yet"
    item = next((candidate for candidate in items if isinstance(candidate, dict)), {})
    metadata = item.get("metadata") if isinstance(item, dict) else {}
    status = item.get("status") if isinstance(item, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(status, dict):
        status = {}
    name = _as_text(metadata.get("name")) or resource_type
    state = _as_text(status.get(expected_status_key)) or "state not reported yet"
    ready = state.lower() == expected_ready_value.lower()
    return ready, item, f"{name}:{state}"


def _resource_ready_condition(resource: dict[str, Any]) -> dict[str, str]:
    status = resource.get("status")
    if not isinstance(status, dict):
        return {}
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return {}
    for item in conditions:
        if not isinstance(item, dict):
            continue
        if _as_text(item.get("type")) != "Ready":
            continue
        return {
            "status": _as_text(item.get("status")),
            "reason": _as_text(item.get("reason")),
            "message": _as_text(item.get("message")),
        }
    return {}


def _resource_applied_states(resource: dict[str, Any]) -> list[dict[str, str]]:
    status = resource.get("status")
    if not isinstance(status, dict):
        return []
    applied_states = status.get("appliedStates")
    if not isinstance(applied_states, list):
        return []
    results: list[dict[str, str]] = []
    for item in applied_states:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        state = _as_text(item.get("state"))
        if not name and not state:
            continue
        results.append({"name": name, "state": state})
    return results


def _interesting_allocatable_resources(allocatable: Mapping[str, Any]) -> dict[str, str]:
    interesting: dict[str, str] = {}
    for key, value in allocatable.items():
        name = _as_text(key)
        if not name:
            continue
        if _is_nvidia_extended_resource_name(name) or _has_rdma_resource_signal(name):
            interesting[name] = _as_text(value)
    return interesting


def _extended_resource_prefix(name: str) -> str:
    prefix, separator, _resource_name = name.partition("/")
    if not separator:
        return ""
    return prefix.strip().lower()


def _is_nvidia_extended_resource_name(name: str) -> bool:
    return _extended_resource_prefix(name) == "nvidia.com"


def _has_rdma_resource_signal(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("rdma", "infiniband", "mlx"))


def _rdma_resource_keys(allocatable_resources: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key in allocatable_resources
            if not _is_nvidia_extended_resource_name(key) and _has_rdma_resource_signal(key)
        )
    )


def _gpu_device_plugin_snapshot(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    rdma_keys = sorted(
        {
            key
            for node in nodes
            for key in _rdma_resource_keys(
                node.get("allocatable_resources", {})
                if isinstance(node.get("allocatable_resources"), Mapping)
                else {}
            )
        }
    )
    return {
        "ready_gpu_node_count": len(nodes),
        "rdma_resource_keys": rdma_keys,
        "rdma_resource_node_count": sum(
            1
            for node in nodes
            if _rdma_resource_keys(
                node.get("allocatable_resources", {})
                if isinstance(node.get("allocatable_resources"), Mapping)
                else {}
            )
        ),
        "nodes": [
            {
                "node_name": _as_text(node.get("name")),
                "gpu_count": int(node.get("gpu_count", 0) or 0),
                "allocatable_resources": dict(node.get("allocatable_resources", {}))
                if isinstance(node.get("allocatable_resources"), Mapping)
                else {},
            }
            for node in nodes
        ],
    }


def _rdma_device_plugin_ready(snapshot: Mapping[str, Any]) -> bool:
    ready_gpu_node_count = int(snapshot.get("ready_gpu_node_count", 0) or 0)
    rdma_resource_node_count = int(snapshot.get("rdma_resource_node_count", 0) or 0)
    rdma_resource_keys = snapshot.get("rdma_resource_keys")
    if not isinstance(rdma_resource_keys, list):
        rdma_resource_keys = []
    return (
        ready_gpu_node_count > 0
        and rdma_resource_node_count == ready_gpu_node_count
        and bool(rdma_resource_keys)
    )


def _rdma_device_plugin_summary(snapshot: Mapping[str, Any]) -> str:
    ready_gpu_node_count = int(snapshot.get("ready_gpu_node_count", 0) or 0)
    rdma_resource_node_count = int(snapshot.get("rdma_resource_node_count", 0) or 0)
    raw_keys = snapshot.get("rdma_resource_keys")
    rdma_resource_keys = (
        [str(item).strip() for item in raw_keys] if isinstance(raw_keys, list) else []
    )
    keys_summary = ", ".join(key for key in rdma_resource_keys if key) or "none"
    return (
        "scheduler-visible RDMA resources on "
        f"{rdma_resource_node_count}/{ready_gpu_node_count} Ready GPU node(s); "
        f"keys: {keys_summary}"
    )


def _gpu_nodes(*, extra_env: dict[str, str] | None) -> list[dict[str, Any]]:
    return [
        {
            "name": _as_text(node.get("name")),
            "gpu_count": int(node.get("gpu_count", 0) or 0),
            "allocatable_resources": dict(node.get("allocatable_resources", {}))
            if isinstance(node.get("allocatable_resources"), Mapping)
            else {},
        }
        for node in _ready_node_inventory(extra_env=extra_env)
        if int(node.get("gpu_count", 0) or 0) > 0
    ]


def _parse_kubernetes_quantity(raw: Any) -> Decimal | None:
    normalized = _as_text(raw)
    if not normalized:
        return None
    match = _K8S_QUANTITY_RE.match(normalized)
    if match is None:
        return None
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation:
        return None
    suffix = match.group("suffix") or ""
    if suffix in _BINARY_QUANTITY_UNITS:
        return number * _BINARY_QUANTITY_UNITS[suffix]
    multiplier = _DECIMAL_QUANTITY_UNITS.get(suffix)
    if multiplier is None:
        return None
    return number * multiplier


def _parse_cpu_millicores(raw: Any) -> int:
    quantity = _parse_kubernetes_quantity(raw)
    if quantity is None:
        return 0
    return int((quantity * 1000).to_integral_value(rounding=ROUND_DOWN))


def _parse_memory_bytes(raw: Any) -> int:
    quantity = _parse_kubernetes_quantity(raw)
    if quantity is None:
        return 0
    return int(quantity.to_integral_value(rounding=ROUND_DOWN))


def _format_cpu_millicores(millicores: int) -> str:
    return f"{max(1, millicores)}m"


def _format_memory_bytes_as_binary_quantity(bytes_value: int) -> str:
    gibibytes = 1 << 30
    if bytes_value >= gibibytes and bytes_value % gibibytes == 0:
        return f"{bytes_value // gibibytes}Gi"
    mebibytes = max(1, bytes_value // (1 << 20))
    return f"{mebibytes}Mi"


def _ready_node_inventory(*, extra_env: dict[str, str] | None) -> list[dict[str, Any]]:
    payload = _kubectl_json(["get", "nodes", "-o", "json"], extra_env=extra_env)
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    nodes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        if not isinstance(metadata, dict) or not isinstance(status, dict):
            continue
        name = _as_text(metadata.get("name"))
        allocatable = status.get("allocatable")
        conditions = status.get("conditions")
        if not name or not isinstance(allocatable, dict) or not isinstance(conditions, list):
            continue
        ready = any(
            isinstance(condition, dict)
            and _as_text(condition.get("type")) == "Ready"
            and _as_text(condition.get("status")) == "True"
            for condition in conditions
        )
        if not ready:
            continue
        nodes.append(
            {
                "name": name,
                "gpu_count": _parse_gpu_quantity(allocatable.get("nvidia.com/gpu")),
                "allocatable_cpu_millicores": _parse_cpu_millicores(allocatable.get("cpu")),
                "allocatable_memory_bytes": _parse_memory_bytes(allocatable.get("memory")),
                "allocatable_resources": _interesting_allocatable_resources(allocatable),
            }
        )
    return nodes


def _pod_request_totals_by_node(
    *,
    extra_env: dict[str, str] | None,
) -> dict[str, dict[str, int]]:
    payload = _kubectl_json(["get", "pods", "-A", "-o", "json"], extra_env=extra_env)
    items = payload.get("items")
    if not isinstance(items, list):
        return {}
    usage: dict[str, dict[str, int]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        spec = item.get("spec")
        status = item.get("status")
        if not isinstance(spec, dict) or not isinstance(status, dict):
            continue
        node_name = _as_text(spec.get("nodeName"))
        phase = _as_text(status.get("phase"))
        if not node_name or phase in {"Succeeded", "Failed"}:
            continue
        container_cpu_millicores = 0
        container_memory_bytes = 0
        containers = spec.get("containers")
        if isinstance(containers, list):
            for container in containers:
                if not isinstance(container, dict):
                    continue
                resources = container.get("resources")
                requests = resources.get("requests") if isinstance(resources, dict) else {}
                if not isinstance(requests, Mapping):
                    requests = {}
                container_cpu_millicores += _parse_cpu_millicores(requests.get("cpu"))
                container_memory_bytes += _parse_memory_bytes(requests.get("memory"))
        init_cpu_millicores = 0
        init_memory_bytes = 0
        init_containers = spec.get("initContainers")
        if isinstance(init_containers, list):
            for container in init_containers:
                if not isinstance(container, dict):
                    continue
                resources = container.get("resources")
                requests = resources.get("requests") if isinstance(resources, dict) else {}
                if not isinstance(requests, Mapping):
                    requests = {}
                init_cpu_millicores = max(
                    init_cpu_millicores,
                    _parse_cpu_millicores(requests.get("cpu")),
                )
                init_memory_bytes = max(
                    init_memory_bytes,
                    _parse_memory_bytes(requests.get("memory")),
                )
        totals = usage.setdefault(node_name, {"cpu_millicores": 0, "memory_bytes": 0})
        totals["cpu_millicores"] += max(container_cpu_millicores, init_cpu_millicores)
        totals["memory_bytes"] += max(container_memory_bytes, init_memory_bytes)
    return usage


def _nccl_live_runtime_overrides(
    *,
    spec: dict[str, Any],
    worker_nodes: list[dict[str, Any]],
    extra_env: dict[str, str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ready_nodes = _ready_node_inventory(extra_env=extra_env)
    ready_node_by_name = {
        _as_text(node.get("name")): node for node in ready_nodes if _as_text(node.get("name"))
    }
    requested_by_node = _pod_request_totals_by_node(extra_env=extra_env)
    worker_names = {
        _as_text(node.get("name")) for node in worker_nodes if _as_text(node.get("name"))
    }
    launcher_candidates = tuple(
        sorted(
            _as_text(node.get("name"))
            for node in ready_nodes
            if _as_text(node.get("name")) not in worker_names
            and int(node.get("gpu_count", 0) or 0) <= 0
        )
    )

    chart_values = spec.get("chart_values")
    values_map = dict(chart_values) if isinstance(chart_values, dict) else {}
    launcher_map = (
        values_map.get("launcher") if isinstance(values_map.get("launcher"), dict) else {}
    )
    launcher_resources = (
        launcher_map.get("resources") if isinstance(launcher_map.get("resources"), dict) else {}
    )
    launcher_requests = (
        launcher_resources.get("requests")
        if isinstance(launcher_resources.get("requests"), dict)
        else {}
    )
    launcher_cpu_millicores = _parse_cpu_millicores(launcher_requests.get("cpu"))
    launcher_memory_bytes = _parse_memory_bytes(launcher_requests.get("memory"))

    available_cpu_millicores: list[int] = []
    available_memory_bytes: list[int] = []
    for worker in worker_nodes:
        node_name = _as_text(worker.get("name"))
        inventory = ready_node_by_name.get(node_name)
        if inventory is None:
            raise RuntimeError(
                f"NCCL test could not resolve live node allocatable resources for worker node '{node_name}'."
            )
        used = requested_by_node.get(node_name, {})
        free_cpu_millicores = max(
            0,
            int(inventory.get("allocatable_cpu_millicores", 0) or 0)
            - int(used.get("cpu_millicores", 0) or 0),
        )
        free_memory_bytes = max(
            0,
            int(inventory.get("allocatable_memory_bytes", 0) or 0)
            - int(used.get("memory_bytes", 0) or 0),
        )
        if not launcher_candidates:
            free_cpu_millicores = max(0, free_cpu_millicores - launcher_cpu_millicores)
            free_memory_bytes = max(0, free_memory_bytes - launcher_memory_bytes)
        available_cpu_millicores.append(free_cpu_millicores)
        available_memory_bytes.append(free_memory_bytes)

    worker_cpu_millicores = min(available_cpu_millicores) if available_cpu_millicores else 0
    worker_memory_bytes = min(available_memory_bytes) if available_memory_bytes else 0
    if worker_cpu_millicores <= 0 or worker_memory_bytes <= 0:
        raise RuntimeError(
            "NCCL test could not find schedulable CPU/memory headroom on the selected GPU worker nodes."
        )

    worker_resources = {
        "requests": {
            "cpu": _format_cpu_millicores(worker_cpu_millicores),
            "memory": _format_memory_bytes_as_binary_quantity(worker_memory_bytes),
        },
        "limits": {
            "cpu": _format_cpu_millicores(worker_cpu_millicores),
            "memory": _format_memory_bytes_as_binary_quantity(worker_memory_bytes),
        },
    }
    overrides: dict[str, Any] = {"worker": {"resources": worker_resources}}
    if launcher_candidates:
        overrides["launcher"] = {
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": list(launcher_candidates),
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }

    metadata = {
        "worker_request_cpu": worker_resources["requests"]["cpu"],
        "worker_request_memory": worker_resources["requests"]["memory"],
        "launcher_non_gpu_node_names": list(launcher_candidates),
    }
    return overrides, metadata


def _select_gpu_validation_nodes(
    nodes: list[dict[str, Any]],
    *,
    max_nodes: int,
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(nodes, key=lambda item: _as_text(item.get("name")))
    limited = ordered[:max_nodes]
    return limited, len(ordered)


def _validation_run_token() -> str:
    return f"run-{time.time_ns() & 0xFFFFFFFF:08x}"


def _phase_count_summary(phases: dict[str, str], *, total_items: int) -> str:
    counts: dict[str, int] = {}
    missing = max(0, total_items - len(phases))
    if missing:
        counts["NotCreated"] = missing
    for phase in phases.values():
        label = phase or "Unknown"
        counts[label] = counts.get(label, 0) + 1
    order = ("NotCreated", "Pending", "Running", "Succeeded", "Failed", "Unknown")
    parts = [f"{name}={counts[name]}" for name in order if counts.get(name)]
    for name in sorted(counts):
        if name not in order:
            parts.append(f"{name}={counts[name]}")
    return ", ".join(parts) if parts else "no pod status reported yet"


def _apply_docs(
    docs: list[dict[str, Any]],
    *,
    extra_env: dict[str, str] | None,
) -> None:
    _run_kubectl(
        ["apply", "-f", "-"],
        extra_env=extra_env,
        stdin_text=yaml.safe_dump_all(docs, sort_keys=False),
        timeout_seconds=180,
    )


def _delete_resource(
    args: list[str],
    *,
    extra_env: dict[str, str] | None,
    timeout_seconds: int = 180,
) -> None:
    try:
        _run_kubectl(args, extra_env=extra_env, timeout_seconds=timeout_seconds)
    except RuntimeError:
        return


def _gpu_visibility_pod_name(node_name: str, *, run_token: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", node_name.strip().lower()).strip("-")
    return f"gpu-visibility-{slug[:39]}-{run_token[:8]}"


def _gpu_visibility_selector(*, run_token: str) -> str:
    return ",".join(
        (
            "app.kubernetes.io/name=gpu-visibility-test",
            "app.kubernetes.io/component=gpu-visibility-validation",
            f"nebius.cxcli.validation-run={run_token}",
        )
    )


def _pod_phase_snapshot(
    *,
    namespace: str,
    label_selector: str,
    pod_names: list[str],
    extra_env: dict[str, str] | None,
) -> tuple[dict[str, str], bool, bool, str]:
    payload = _kubectl_json(
        ["get", "pod", "-n", namespace, "-l", label_selector, "-o", "json"],
        extra_env=extra_env,
        timeout_seconds=30,
    )
    items = payload.get("items")
    phase_by_name: dict[str, str] = {}
    if isinstance(items, list):
        expected = set(pod_names)
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            name = _as_text(metadata.get("name"))
            if name not in expected:
                continue
            phase_by_name[name] = _as_text(item.get("status", {}).get("phase"))
    all_terminal = len(phase_by_name) == len(pod_names) and all(
        phase in {"Succeeded", "Failed"} for phase in phase_by_name.values()
    )
    any_failed = any(phase == "Failed" for phase in phase_by_name.values())
    summary = _phase_count_summary(phase_by_name, total_items=len(pod_names))
    return phase_by_name, all_terminal, any_failed, summary


def _run_gpu_visibility_validation(
    *,
    spec: dict[str, Any],
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None,
) -> Path:
    namespace = _as_text(spec.get("namespace"))
    image = _as_text(spec.get("image"))
    cleanup = bool(spec.get("cleanup", True))
    timeout_seconds = _parse_go_duration_seconds(_as_text(spec.get("timeout")))
    max_nodes = max(1, int(spec.get("max_nodes", 1) or 1))
    report_path = inventory_dir / _as_text(spec.get("report_file"))
    all_nodes = _gpu_nodes(extra_env=extra_env)
    if not all_nodes:
        raise RuntimeError(
            "GPU Visibility test could not find any Ready Kubernetes node with allocatable GPUs"
        )
    nodes, total_gpu_nodes = _select_gpu_validation_nodes(all_nodes, max_nodes=max_nodes)
    skipped_nodes = max(0, total_gpu_nodes - len(nodes))
    device_plugin_snapshot = _gpu_device_plugin_snapshot(all_nodes)
    run_token = _validation_run_token()
    label_selector = _gpu_visibility_selector(run_token=run_token)

    if emit:
        message = (
            f"Running GPU Visibility test on {len(nodes)} of {total_gpu_nodes} Ready GPU node(s)"
            f" (max_nodes={max_nodes})."
        )
        if skipped_nodes:
            message += f" Skipping {skipped_nodes} additional GPU node(s) to keep deploy-time validation bounded."
        emit(message)

    docs: list[dict[str, Any]] = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
    ]
    pod_names: list[str] = []
    try:
        for node in nodes:
            pod_name = _gpu_visibility_pod_name(node["name"], run_token=run_token)
            pod_names.append(pod_name)
            docs.append(
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": pod_name,
                        "namespace": namespace,
                        "labels": {
                            "app.kubernetes.io/name": "gpu-visibility-test",
                            "app.kubernetes.io/component": "gpu-visibility-validation",
                            "nebius.cxcli.validation-run": run_token,
                        },
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "nodeName": node["name"],
                        "tolerations": [{"operator": "Exists"}],
                        "containers": [
                            {
                                "name": "cuda-vectoradd",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "resources": {"limits": {"nvidia.com/gpu": 1}},
                            }
                        ],
                    },
                }
            )
        _apply_docs(docs, extra_env=extra_env)
        phases: dict[str, str] = {}
        deadline = time.monotonic() + timeout_seconds
        last_summary = ""
        last_emit_at = 0.0
        last_poll_error = ""
        while True:
            try:
                phases, all_terminal, any_failed, summary = _pod_phase_snapshot(
                    namespace=namespace,
                    label_selector=label_selector,
                    pod_names=pod_names,
                    extra_env=extra_env,
                )
                last_poll_error = ""
            except RuntimeError as exc:
                all_terminal = False
                any_failed = False
                last_poll_error = str(exc)
                summary = f"waiting for pod status; last kubectl poll failed: {last_poll_error}"
            now = time.monotonic()
            if emit and (
                summary != last_summary
                or (now - last_emit_at) >= _VALIDATION_REPEAT_INTERVAL_SECONDS
            ):
                elapsed = int(max(0.0, now - (deadline - timeout_seconds)))
                emit(
                    f"GPU Visibility test [{elapsed}s] "
                    f"{len([phase for phase in phases.values() if phase in {'Succeeded', 'Failed'}])}/{len(pod_names)} terminal; "
                    f"{summary}"
                )
                last_summary = summary
                last_emit_at = now
            if any_failed or all_terminal:
                break
            if now >= deadline:
                detail = (
                    f" Last pod poll error: {last_poll_error}"
                    if last_poll_error
                    else ""
                )
                raise RuntimeError(
                    f"Timed out waiting for GPU Visibility pods in namespace '{namespace}' to finish.{detail}"
                )
            time.sleep(_VALIDATION_POLL_INTERVAL_SECONDS)
        results: list[dict[str, Any]] = []
        passed = True
        for node, pod_name in zip(nodes, pod_names, strict=True):
            phase = phases.get(pod_name, "")
            logs = ""
            if phase:
                with suppress(RuntimeError):
                    logs = _run_kubectl(
                        ["logs", pod_name, "-n", namespace],
                        extra_env=extra_env,
                        timeout_seconds=60,
                    ).stdout
            pod_passed = "Test PASSED" in logs or "PASSED" in logs
            if phase == "Succeeded" and not logs:
                pod_passed = True
            passed = passed and pod_passed and phase == "Succeeded"
            results.append(
                _gpu_visibility_node_report(
                    node_name=node["name"],
                    pod_name=pod_name,
                    gpu_count=node["gpu_count"],
                    allocatable_resources=(
                        node.get("allocatable_resources", {})
                        if isinstance(node.get("allocatable_resources"), Mapping)
                        else {}
                    ),
                    phase=phase,
                    passed=pod_passed and phase == "Succeeded",
                    logs=logs,
                )
            )
        passed_node_count = sum(1 for item in results if bool(item.get("passed")))
        report = {
            "validation": "GPU Visibility test",
            "namespace": namespace,
            "image": image,
            "max_nodes": max_nodes,
            "selected_node_count": len(nodes),
            "total_gpu_node_count": total_gpu_nodes,
            "skipped_node_count": skipped_nodes,
            "device_plugin_snapshot": device_plugin_snapshot,
            "passed": passed,
            "passed_node_count": passed_node_count,
            "failed_node_count": len(results) - passed_node_count,
            "nodes": results,
        }
        _write_report(report_path, report)
        if not passed:
            raise RuntimeError(f"GPU Visibility test failed. Report: {report_path}")
        return report_path
    finally:
        if cleanup:
            _delete_resource(
                [
                    "delete",
                    "pod",
                    "-n",
                    namespace,
                    "-l",
                    label_selector,
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                extra_env=extra_env,
                timeout_seconds=60,
            )


def _training_operator_present(
    *,
    namespace: str,
    extra_env: dict[str, str] | None,
) -> bool:
    try:
        _run_kubectl(
            ["get", "deployment", "training-operator", "-n", namespace],
            extra_env=extra_env,
            timeout_seconds=30,
        )
    except RuntimeError:
        return False
    return True


def _deployment_rollout_status(
    *,
    name: str,
    namespace: str,
    extra_env: dict[str, str] | None,
) -> tuple[bool, str]:
    try:
        payload = _kubectl_json(
            ["get", "deployment", name, "-n", namespace, "-o", "json"],
            extra_env=extra_env,
            timeout_seconds=30,
        )
    except RuntimeError:
        return False, f"deployment {name}: not visible yet"
    spec = payload.get("spec")
    status = payload.get("status")
    if not isinstance(spec, dict):
        spec = {}
    if not isinstance(status, dict):
        status = {}
    desired = int(spec.get("replicas", 1) or 1)
    updated = int(status.get("updatedReplicas", 0) or 0)
    ready = int(status.get("readyReplicas", 0) or 0)
    available = int(status.get("availableReplicas", 0) or 0)
    done = desired > 0 and updated >= desired and ready >= desired and available >= desired
    return (
        done,
        f"deployment {name}: ready {ready}/{desired}, updated {updated}/{desired}, available {available}/{desired}",
    )


def _nccl_job_name() -> str:
    return "nccl-test-nebius"


def _wait_for_launcher_completion(
    *,
    namespace: str,
    launcher_pod_name: str,
    extra_env: dict[str, str] | None,
    timeout_seconds: int,
    emit: Callable[[str], None] | None,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    started_at = time.monotonic()
    last_summary = ""
    last_emit_at = 0.0
    while time.monotonic() < deadline:
        try:
            payload = _kubectl_json(
                ["get", "pod", launcher_pod_name, "-n", namespace, "-o", "json"],
                extra_env=extra_env,
                timeout_seconds=30,
            )
        except RuntimeError:
            phase = "NotCreated"
            summary = f"NCCL launcher pod {launcher_pod_name}: {phase}"
            now = time.monotonic()
            if emit and (
                summary != last_summary
                or (now - last_emit_at) >= _VALIDATION_REPEAT_INTERVAL_SECONDS
            ):
                elapsed = int(max(0.0, now - started_at))
                emit(f"NCCL test [{elapsed}s] {summary}")
                last_summary = summary
                last_emit_at = now
            time.sleep(_VALIDATION_POLL_INTERVAL_SECONDS)
            continue
        phase = _as_text(payload.get("status", {}).get("phase"))
        summary = f"NCCL launcher pod {launcher_pod_name}: {phase or 'Unknown'}"
        now = time.monotonic()
        if emit and (
            summary != last_summary or (now - last_emit_at) >= _VALIDATION_REPEAT_INTERVAL_SECONDS
        ):
            elapsed = int(max(0.0, now - started_at))
            emit(f"NCCL test [{elapsed}s] {summary}")
            last_summary = summary
            last_emit_at = now
        if phase in {"Succeeded", "Failed"}:
            return phase
        time.sleep(_VALIDATION_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Timed out waiting for NCCL launcher pod '{launcher_pod_name}' to finish")


def _extract_nccl_json(logs: str) -> dict[str, Any] | None:
    begin = logs.find(_NCCL_JSON_BEGIN_MARKER)
    end = logs.find(_NCCL_JSON_END_MARKER)
    if begin < 0 or end < 0 or end <= begin:
        return None
    payload_text = logs[begin + len(_NCCL_JSON_BEGIN_MARKER) : end].strip()
    if not payload_text:
        return None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _nccl_json_summary(payload: dict[str, Any] | None) -> tuple[float, bool | None]:
    if not isinstance(payload, dict):
        return 0.0, None
    average = payload.get("average_bus_bandwidth")
    out_of_bounds = payload.get("out_of_bounds")
    average_bandwidth = 0.0
    out_of_bounds_ok = None
    if isinstance(average, dict):
        try:
            average_bandwidth = float(average.get("bandwidth", 0.0) or 0.0)
        except (TypeError, ValueError):
            average_bandwidth = 0.0
    if isinstance(out_of_bounds, dict):
        okay = out_of_bounds.get("okay")
        if isinstance(okay, bool):
            out_of_bounds_ok = okay
        elif isinstance(okay, str):
            out_of_bounds_ok = okay.lower() == "true"
    return average_bandwidth, out_of_bounds_ok


def _nccl_json_report_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    out_of_bounds = (
        payload.get("out_of_bounds") if isinstance(payload.get("out_of_bounds"), dict) else {}
    )
    average = (
        payload.get("average_bus_bandwidth")
        if isinstance(payload.get("average_bus_bandwidth"), dict)
        else {}
    )
    devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    results = payload.get("results") if isinstance(payload.get("results"), list) else []

    peak_bus_bandwidth = 0.0
    peak_size_bytes = 0
    peak_variant = ""
    for item in results:
        if not isinstance(item, dict):
            continue
        size_bytes = int(item.get("size", 0) or 0)
        for variant in ("in_place", "out_of_place"):
            metrics = item.get(variant)
            if not isinstance(metrics, dict):
                continue
            try:
                bus_bandwidth = float(metrics.get("bus_bw", 0.0) or 0.0)
            except (TypeError, ValueError):
                bus_bandwidth = 0.0
            if bus_bandwidth > peak_bus_bandwidth:
                peak_bus_bandwidth = bus_bandwidth
                peak_size_bytes = size_bytes
                peak_variant = variant

    device_info = sorted(
        {
            _as_text(item.get("device_info"))
            for item in devices
            if isinstance(item, dict) and _as_text(item.get("device_info"))
        }
    )
    hostnames = sorted(
        {
            _as_text(item.get("hostname"))
            for item in devices
            if isinstance(item, dict) and _as_text(item.get("hostname"))
        }
    )

    summary = {
        "nccl_version": payload.get("nccl_version"),
        "config": {
            "minimum_bytes": config.get("minimum_bytes"),
            "maximum_bytes": config.get("maximum_bytes"),
            "step_factor": config.get("step_factor"),
            "step_bytes": config.get("step_bytes"),
            "warmup_iterations": config.get("warmup_iters"),
            "iterations": config.get("iterations"),
            "aggregated_iterations": config.get("aggregated_iterations"),
            "validation_checks": config.get("validation"),
            "graph_launches": config.get("graph"),
            "blocking_collectives": config.get("blocking_collectives"),
            "parallel_init": config.get("parallel_init"),
        },
        "device_count": len(devices),
        "hostnames": hostnames,
        "device_info": device_info,
        "result_count": len(results),
        "out_of_bounds_count": out_of_bounds.get("count"),
        "average_bus_bandwidth_gbps": average.get("bandwidth"),
        "average_bus_bandwidth_ok": average.get("okay"),
        "peak_bus_bandwidth_gbps": peak_bus_bandwidth,
        "peak_bus_bandwidth_size_bytes": peak_size_bytes,
        "peak_bus_bandwidth_variant": peak_variant,
    }
    return summary


def _nccl_mpi_export_value(
    args: list[Any],
    *,
    env_name: str,
) -> tuple[str, str]:
    previous = ""
    found_value = ""
    found_source = "unset"
    for raw in args:
        token = _as_text(raw)
        if not token:
            previous = ""
            continue
        candidate = ""
        if previous == "-x":
            candidate = token
        elif token.startswith("-x") and token != "-x":
            candidate = token[2:].strip()
        previous = token
        if not candidate:
            continue
        if candidate == env_name:
            found_value = ""
            found_source = "inherited MPI environment"
            continue
        prefix = f"{env_name}="
        if candidate.startswith(prefix):
            found_value = candidate[len(prefix) :]
            found_source = "explicit MPI environment"
    return found_value, found_source


def _nccl_dmabuf_metadata(
    *,
    transport_mode: str,
    benchmark_map: Mapping[str, Any],
) -> dict[str, str]:
    mpi_env_args = []
    for key in ("mpiBaseArgs", "mpiExtraArgs"):
        value = benchmark_map.get(key)
        if isinstance(value, list):
            mpi_env_args.extend(value)
    env_value, env_source = _nccl_mpi_export_value(
        mpi_env_args,
        env_name=_NCCL_DMABUF_ENV_NAME,
    )
    normalized_transport = _as_text(transport_mode).lower()
    if normalized_transport != "rdma":
        gpudirect_mode = ""
    elif env_value == "0":
        gpudirect_mode = "legacy nvidia-peermem"
    elif env_value == "1":
        gpudirect_mode = "dma-buf"
    elif env_source == "unset":
        gpudirect_mode = "dma-buf default when supported"
    else:
        gpudirect_mode = "unknown"
    return {
        "gpudirect_mode": gpudirect_mode,
        "nccl_dmabuf_env_name": _NCCL_DMABUF_ENV_NAME,
        "nccl_dmabuf_enable": env_value if env_value else "unset",
        "nccl_dmabuf_enable_source": env_source,
    }


def _nccl_chart_documents(
    *,
    spec: dict[str, Any],
    runtime_values: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    chart_values = spec.get("chart_values")
    values = copy.deepcopy(chart_values) if isinstance(chart_values, dict) else {}
    merged_values = _deep_merge_dict(values, runtime_values or {})
    return render_chart_template_documents(
        chart_name=_as_text(spec.get("chart_name_or_ref")),
        chart_repo=_as_text(spec.get("chart_repo")),
        chart_version=_as_text(spec.get("chart_version")),
        release_name=_nccl_job_name(),
        namespace=_as_text(spec.get("namespace")),
        values=merged_values,
    )


def _run_nccl_validation(
    *,
    spec: dict[str, Any],
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None,
) -> Path:
    timeout_seconds = _parse_go_duration_seconds(_as_text(spec.get("timeout")))
    namespace = _as_text(spec.get("namespace"))
    max_nodes = max(1, int(spec.get("max_nodes", 1) or 1))
    report_path = inventory_dir / _as_text(spec.get("report_file"))
    transport_mode = _as_text(spec.get("transport_mode")).lower() or "auto"
    transport_label = _nccl_transport_label(transport_mode)
    threshold_gbps = float(spec.get("average_bus_bandwidth_threshold_gbps", 0) or 0)
    threshold_enforced = bool(spec.get("threshold_enforced", True))
    all_worker_nodes = _gpu_nodes(extra_env=extra_env)
    if not all_worker_nodes:
        raise RuntimeError(
            "NCCL test could not find any Ready Kubernetes node with allocatable GPUs"
        )
    worker_nodes, total_gpu_nodes = _select_gpu_validation_nodes(
        all_worker_nodes, max_nodes=max_nodes
    )
    skipped_nodes = max(0, total_gpu_nodes - len(worker_nodes))
    worker_gpu_counts = {
        int(node.get("gpu_count", 0) or 0)
        for node in worker_nodes
        if int(node.get("gpu_count", 0) or 0) > 0
    }
    if not worker_gpu_counts:
        raise RuntimeError("NCCL test could not resolve GPU count for the selected worker nodes")
    if len(worker_gpu_counts) != 1:
        raise RuntimeError(
            "NCCL test requires a uniform GPU count across the selected worker nodes"
        )
    worker_gpu_count = next(iter(worker_gpu_counts))

    training_operator_namespace = _as_text(spec.get("training_operator_namespace"))
    training_operator_manifest = _as_text(spec.get("training_operator_manifest"))
    operator_preexisting = _training_operator_present(
        namespace=training_operator_namespace,
        extra_env=extra_env,
    )
    if not operator_preexisting:
        if emit:
            emit("Installing Kubeflow Training Operator for NCCL test.")
        _run_kubectl(
            ["apply", "--server-side", "-k", training_operator_manifest],
            extra_env=extra_env,
            timeout_seconds=600,
        )
        deadline = time.monotonic() + timeout_seconds
        started_at = time.monotonic()
        last_summary = ""
        last_emit_at = 0.0
        while True:
            ready, summary = _deployment_rollout_status(
                name="training-operator",
                namespace=training_operator_namespace,
                extra_env=extra_env,
            )
            now = time.monotonic()
            if emit and (
                summary != last_summary
                or (now - last_emit_at) >= _VALIDATION_REPEAT_INTERVAL_SECONDS
            ):
                elapsed = int(max(0.0, now - started_at))
                emit(f"NCCL prerequisite [{elapsed}s] {summary}")
                last_summary = summary
                last_emit_at = now
            if ready:
                break
            if now >= deadline:
                raise RuntimeError(
                    "Timed out waiting for Kubeflow Training Operator deployment to become Ready"
                )
            time.sleep(_VALIDATION_POLL_INTERVAL_SECONDS)

    job_name = _nccl_job_name()
    launcher_pod_name = f"{job_name}-launcher"
    with suppress(RuntimeError):
        _run_kubectl(["create", "namespace", namespace], extra_env=extra_env, timeout_seconds=60)
    try:
        if emit:
            message = (
                f"Running NCCL test on {len(worker_nodes)} of {total_gpu_nodes} Ready GPU node(s)"
                f" (max_nodes={max_nodes}) for platform {_as_text(spec.get('gpu_platform'))}"
                f" using {transport_label}."
            )
            if skipped_nodes:
                message += (
                    f" Skipping {skipped_nodes} additional GPU node(s) for deploy-time validation."
                )
            emit(message)
        runtime_values = {
            "worker": {"replicas": len(worker_nodes), "gpus": worker_gpu_count},
            "report": {
                "jsonBeginMarker": _NCCL_JSON_BEGIN_MARKER,
                "jsonEndMarker": _NCCL_JSON_END_MARKER,
            },
            "fullnameOverride": _nccl_job_name(),
        }
        _delete_resource(
            ["delete", "mpijob", job_name, "-n", namespace, "--ignore-not-found=true"],
            extra_env=extra_env,
            timeout_seconds=120,
        )
        live_runtime_overrides, runtime_metadata = _nccl_live_runtime_overrides(
            spec=spec,
            worker_nodes=worker_nodes,
            extra_env=extra_env,
        )
        runtime_values = _deep_merge_dict(runtime_values, live_runtime_overrides)
        rendered_docs = _nccl_chart_documents(
            spec=spec,
            runtime_values=runtime_values,
        )
        _apply_docs(rendered_docs, extra_env=extra_env)
        launcher_phase = _wait_for_launcher_completion(
            namespace=namespace,
            launcher_pod_name=launcher_pod_name,
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
            emit=emit,
        )
        logs = _run_kubectl(
            ["logs", launcher_pod_name, "-n", namespace],
            extra_env=extra_env,
            timeout_seconds=120,
        ).stdout
        result_json = _extract_nccl_json(logs)
        avg_bus_bandwidth, out_of_bounds_ok = _nccl_json_summary(result_json)
        result_summary = _nccl_json_report_summary(result_json)
        if avg_bus_bandwidth <= 0:
            match = _AVG_BUS_BANDWIDTH_RE.search(logs)
            avg_bus_bandwidth = float(match.group(1)) if match is not None else 0.0
        if out_of_bounds_ok is None:
            out_of_bounds_ok = _OUT_OF_BOUNDS_OK_RE.search(logs) is not None
        chart_values = spec.get("chart_values")
        values_map = dict(chart_values) if isinstance(chart_values, dict) else {}
        image_map = values_map.get("image") if isinstance(values_map.get("image"), dict) else {}
        benchmark_map = (
            values_map.get("benchmark") if isinstance(values_map.get("benchmark"), dict) else {}
        )
        dmabuf_metadata = _nccl_dmabuf_metadata(
            transport_mode=transport_mode,
            benchmark_map=benchmark_map,
        )
        bandwidth_observed = avg_bus_bandwidth > 0
        passed = (
            launcher_phase == "Succeeded"
            and bool(out_of_bounds_ok)
            and bandwidth_observed
            and (not threshold_enforced or avg_bus_bandwidth > threshold_gbps)
        )
        report = {
            "validation": "NCCL test",
            "chart_component_id": _as_text(spec.get("chart_component_id")),
            "chart_name_or_ref": _as_text(spec.get("chart_name_or_ref")),
            "chart_repo": _as_text(spec.get("chart_repo")),
            "namespace": namespace,
            "job_name": job_name,
            "gpu_platform": _as_text(spec.get("gpu_platform")),
            "max_nodes": max_nodes,
            "selected_worker_node_count": len(worker_nodes),
            "total_gpu_node_count": total_gpu_nodes,
            "skipped_node_count": skipped_nodes,
            "worker_node_count": len(worker_nodes),
            "worker_node_names": [node["name"] for node in worker_nodes],
            "worker_gpus": worker_gpu_count,
            "worker_request_cpu": runtime_metadata.get("worker_request_cpu"),
            "worker_request_memory": runtime_metadata.get("worker_request_memory"),
            "launcher_non_gpu_node_names": runtime_metadata.get("launcher_non_gpu_node_names", []),
            "transport_mode": transport_mode,
            "transport_label": transport_label,
            **dmabuf_metadata,
            "avg_bus_bandwidth_gbps": avg_bus_bandwidth,
            "threshold_gbps": threshold_gbps,
            "threshold_enforced": threshold_enforced,
            "bandwidth_observed": bandwidth_observed,
            "out_of_bounds_ok": bool(out_of_bounds_ok),
            "launcher_phase": launcher_phase,
            "training_operator_installed_transiently": not operator_preexisting,
            "runtime_image": _as_text(image_map.get("repository")),
            "runtime_image_tag": _as_text(image_map.get("tag")),
            "test_binary_path": _as_text(benchmark_map.get("binaryPath")),
            "result_json_path": _as_text(benchmark_map.get("resultJsonPath")),
            "benchmark_args": list(benchmark_map.get("args", []))
            if isinstance(benchmark_map.get("args"), list)
            else [],
            "mpi_base_args": list(benchmark_map.get("mpiBaseArgs", []))
            if isinstance(benchmark_map.get("mpiBaseArgs"), list)
            else [],
            "mpi_extra_args": list(benchmark_map.get("mpiExtraArgs", []))
            if isinstance(benchmark_map.get("mpiExtraArgs"), list)
            else [],
            "result_summary": result_summary,
            "passed": passed,
        }
        if not passed:
            report["launcher_log_excerpt"] = _report_log_excerpt(logs, limit=12000)
        _write_report(report_path, report)
        if not passed:
            raise RuntimeError(f"NCCL test failed. Report: {report_path}")
        return report_path
    finally:
        _delete_resource(
            ["delete", "mpijob", job_name, "-n", namespace, "--ignore-not-found=true"],
            extra_env=extra_env,
            timeout_seconds=120,
        )
        if not operator_preexisting:
            _delete_resource(
                ["delete", "-k", training_operator_manifest],
                extra_env=extra_env,
                timeout_seconds=300,
            )


def _run_operator_readiness_validation(
    *,
    spec: dict[str, Any],
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None,
) -> Path:
    timeout_seconds = _parse_go_duration_seconds(_as_text(spec.get("timeout")))
    report_path = inventory_dir / _as_text(spec.get("report_file"))
    gpu_namespace = _as_text(spec.get("gpu_operator_namespace")) or "nvidia-gpu-operator"
    network_namespace = (
        _as_text(spec.get("network_operator_namespace")) or "nvidia-network-operator"
    )
    network_required = bool(spec.get("network_operator_required"))
    deadline = time.monotonic() + timeout_seconds
    started_at = time.monotonic()
    last_summary = ""
    last_emit_at = 0.0
    gpu_ready = False
    network_ready = not network_required
    gpu_resource: dict[str, Any] = {}
    network_resource: dict[str, Any] = {}
    network_state_summary = ""
    network_applied_states: list[dict[str, str]] = []
    gpu_daemonsets: list[dict[str, Any]] = []
    network_daemonsets: list[dict[str, Any]] = []
    gpu_nodes: list[dict[str, Any]] = []
    gpu_ready_condition: dict[str, str] = {}
    rdma_snapshot: dict[str, Any] = {}
    rdma_required = bool(spec.get("gpu_cluster_enabled"))
    rdma_ready = not rdma_required

    while True:
        gpu_control_plane_ready, gpu_resource, gpu_state_summary = _resource_state_snapshot(
            resource_type="clusterpolicy",
            namespace=None,
            expected_status_key="state",
            expected_ready_value="ready",
            extra_env=extra_env,
        )
        gpu_ready_condition = _resource_ready_condition(gpu_resource)
        gpu_nodes = _gpu_nodes(extra_env=extra_env)
        rdma_snapshot = _gpu_device_plugin_snapshot(gpu_nodes)
        gpu_ready = gpu_control_plane_ready and bool(gpu_nodes)
        gpu_allocatable_summary = (
            f"allocatable GPUs on {len(gpu_nodes)} Ready node(s)"
            if gpu_nodes
            else "allocatable GPUs not reported on any Ready node yet"
        )
        summary_parts = [f"GPU Operator {gpu_state_summary}; {gpu_allocatable_summary}"]
        if gpu_ready_condition:
            condition_summary = f"condition Ready={_as_text(gpu_ready_condition.get('status'))}"
            reason = _as_text(gpu_ready_condition.get("reason"))
            if reason:
                condition_summary += f"/{reason}"
            summary_parts.append(condition_summary)
        if network_required:
            (
                network_ready,
                network_resource,
                network_state_summary,
            ) = _resource_state_snapshot(
                resource_type="nicclusterpolicy",
                namespace=network_namespace,
                expected_status_key="state",
                expected_ready_value="ready",
                extra_env=extra_env,
            )
            network_applied_states = _resource_applied_states(network_resource)
            if rdma_required:
                rdma_ready = _rdma_device_plugin_ready(rdma_snapshot)
                network_ready = network_ready and rdma_ready
            summary_parts.append(f"Network Operator {network_state_summary}")
            if rdma_required:
                summary_parts.append(_rdma_device_plugin_summary(rdma_snapshot))
        summary = " | ".join(summary_parts)
        now = time.monotonic()
        if emit and (
            summary != last_summary or (now - last_emit_at) >= _VALIDATION_REPEAT_INTERVAL_SECONDS
        ):
            elapsed = int(max(0.0, now - started_at))
            emit(f"{_GPU_STACK_VALIDATION_NAME} [{elapsed}s] {summary}")
            last_summary = summary
            last_emit_at = now
        if gpu_ready and network_ready:
            break
        if now >= deadline:
            break
        time.sleep(_VALIDATION_POLL_INTERVAL_SECONDS)

    gpu_daemonsets = _daemonset_summary(namespace=gpu_namespace, extra_env=extra_env)
    if network_required:
        network_daemonsets = _daemonset_summary(namespace=network_namespace, extra_env=extra_env)

    report = {
        "validation": _GPU_STACK_VALIDATION_NAME,
        "passed": gpu_ready and network_ready,
        "gpu_operator": {
            "namespace": gpu_namespace,
            "ready": gpu_ready,
            "resource_name": _as_text(gpu_resource.get("metadata", {}).get("name")),
            "state": _as_text(gpu_resource.get("status", {}).get("state")),
            "ready_condition": gpu_ready_condition,
            "gpu_nodes": gpu_nodes,
            "daemonsets": gpu_daemonsets,
        },
        "network_operator": {
            "namespace": network_namespace,
            "required": network_required,
            "ready": network_ready,
            "resource_name": _as_text(network_resource.get("metadata", {}).get("name")),
            "state": _as_text(network_resource.get("status", {}).get("state")),
            "applied_states": network_applied_states if network_required else [],
            "rdma_required": rdma_required if network_required else False,
            "rdma_ready": rdma_ready if network_required else False,
            "device_plugin_snapshot": rdma_snapshot if network_required and rdma_required else {},
            "daemonsets": network_daemonsets if network_required else [],
        },
        "gpudirect_mode": "dma-buf" if bool(spec.get("gpu_cluster_enabled")) else "",
    }
    _write_report(report_path, report)
    if not report["passed"]:
        raise RuntimeError(f"{_GPU_STACK_VALIDATION_NAME} check failed. Report: {report_path}")
    return report_path


def _write_validation_error_report(
    *,
    spec: Mapping[str, Any],
    inventory_dir: Path,
    error: Exception,
) -> None:
    report_file = _as_text(spec.get("report_file"))
    if not report_file:
        return
    report_path = inventory_dir / report_file
    if report_path.exists():
        return
    kind = _as_text(spec.get("kind"))
    validation_name = _as_text(spec.get("name")) or kind or "Validation"
    report: dict[str, Any] = {
        "validation": validation_name,
        "kind": kind,
        "passed": False,
        "error": str(error),
    }
    if kind == "mk8s_nccl":
        transport_mode = _as_text(spec.get("transport_mode")).lower() or "auto"
        chart_values = spec.get("chart_values")
        values_map = dict(chart_values) if isinstance(chart_values, Mapping) else {}
        benchmark_map = (
            values_map.get("benchmark") if isinstance(values_map.get("benchmark"), Mapping) else {}
        )
        report.update(
            {
                "launcher_phase": "error",
                "transport_mode": transport_mode,
                "transport_label": _nccl_transport_label(transport_mode),
                **_nccl_dmabuf_metadata(
                    transport_mode=transport_mode,
                    benchmark_map=benchmark_map,
                ),
                "avg_bus_bandwidth_gbps": 0.0,
                "threshold_gbps": float(spec.get("average_bus_bandwidth_threshold_gbps", 0) or 0),
                "threshold_enforced": bool(spec.get("threshold_enforced", True)),
                "selected_worker_node_count": 0,
            }
        )
    _write_report(report_path, report)


def run_mk8s_gpu_validations(
    validations: list[dict[str, Any]],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    written_reports: list[Path] = []
    total = len(validations)
    for index, spec in enumerate(validations, start=1):
        kind = _as_text(spec.get("kind"))
        name = _as_text(spec.get("name")) or kind or f"validation-{index}"
        if emit:
            emit(f"Starting validation {index}/{total}: {name}.")
        try:
            if kind == "mk8s_gpu_operator_readiness":
                written_reports.append(
                    _run_operator_readiness_validation(
                        spec=spec,
                        inventory_dir=inventory_dir,
                        extra_env=extra_env,
                        emit=emit,
                    )
                )
            elif kind == "mk8s_gpu_visibility":
                written_reports.append(
                    _run_gpu_visibility_validation(
                        spec=spec,
                        inventory_dir=inventory_dir,
                        extra_env=extra_env,
                        emit=emit,
                    )
                )
            elif kind == "mk8s_nccl":
                written_reports.append(
                    _run_nccl_validation(
                        spec=spec,
                        inventory_dir=inventory_dir,
                        extra_env=extra_env,
                        emit=emit,
                    )
                )
        except Exception as exc:
            _write_validation_error_report(spec=spec, inventory_dir=inventory_dir, error=exc)
            raise
    return written_reports


__all__ = [
    "Mk8sGpuAppSelection",
    "Mk8sGpuClusterContext",
    "ensure_mk8s_gpu_app_rows",
    "has_mk8s_gpu_health_checker_app",
    "materialize_mk8s_gpu_app_values",
    "mk8s_gpu_dependency_issues",
    "mk8s_gpu_flux_release_dependencies",
    "mk8s_gpu_flux_release_post_render_patches",
    "mk8s_gpu_validation_specs",
    "mk8s_gpu_validation_warnings",
    "resolve_mk8s_gpu_app_selection",
    "run_mk8s_gpu_validations",
]
