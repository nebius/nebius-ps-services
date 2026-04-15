"""MK8s GPU policy helpers and deploy-time validation workflows."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .component_instances import component_instance_id, component_type_id
from .component_sources import (
    ComponentDefault,
    Mk8sGpuAppActivationRule,
    Mk8sGpuAppPolicy,
    Mk8sGpuAppValueRule,
    Mk8sGpuSettings,
    helm_chart_source_by_id,
    load_component_sources,
    tf_module_source_by_id,
)
from .components import ComponentEntry, component_entries
from .helm_client import render_chart_template_documents
from .runtime_config import to_plain_data

_GPU_QUANTITY_RE = re.compile(r"^\d+")
_AVG_BUS_BANDWIDTH_RE = re.compile(r"Avg bus bandwidth\s*:\s*([0-9]+(?:\.[0-9]+)?)")
_OUT_OF_BOUNDS_OK_RE = re.compile(r"Out of bounds values\s*:\s*0 OK")
_NCCL_JSON_BEGIN_MARKER = "__NCCL_JSON_BEGIN__"
_NCCL_JSON_END_MARKER = "__NCCL_JSON_END__"


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
        if policy.role or policy.auto_enable or policy.value_overrides or policy.install_after:
            policies[app_id] = policy
    return policies


def _mk8s_gpu_app_id_by_role() -> dict[str, str]:
    roles: dict[str, str] = {}
    for app_id, policy in _mk8s_gpu_app_policies().items():
        if policy.role and policy.role not in roles:
            roles[policy.role] = app_id
    return roles


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _gpu_stack_source(inputs: dict[str, Any]) -> str:
    stack_source = _as_text(inputs.get("gpu_stack_source")).lower()
    return stack_source if stack_source in {"nebius_image", "manual"} else "nebius_image"


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


def _app_activation_rule_matches(
    context: Mk8sGpuClusterContext,
    rule: Mk8sGpuAppActivationRule,
) -> bool:
    return _rule_matches_context(
        context,
        gpu_stack_source=rule.gpu_stack_source,
        gpu_cluster_enabled=rule.gpu_cluster_enabled,
        match_platforms=rule.match_platforms,
        match_presets=rule.match_presets,
    )


def _app_value_rule_matches(
    context: Mk8sGpuClusterContext,
    rule: Mk8sGpuAppValueRule,
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
) -> tuple[str, ...]:
    required_ids = {
        app_id
        for app_id, policy in _mk8s_gpu_app_policies().items()
        if any(
            _app_activation_rule_matches(context, rule)
            for rule in policy.auto_enable
            for context in contexts
        )
    }
    settings = gpu_settings or _mk8s_gpu_settings()
    if settings.validations.health_checker.enabled_by_default:
        health_checker_id = _mk8s_gpu_app_id_by_role().get("health_checker")
        if health_checker_id:
            required_ids.add(health_checker_id)
    return tuple(sorted(required_ids))


def _enabled_mk8s_gpu_contexts(payload: dict[str, Any]) -> tuple[Mk8sGpuClusterContext, ...]:
    infra_node = payload.get("infra")
    if not isinstance(infra_node, dict):
        return ()
    components = infra_node.get("components")
    if not isinstance(components, list):
        return ()

    mk8s_entry_ids = {
        entry.id for entry in component_entries("infra") if entry.validation_profile == "mk8s_cluster"
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
        gpu_platform = _as_text(
            gpu_resources.get("platform")
        ) or _as_text(inputs.get("gpu_nodes_platform"))
        gpu_preset = _as_text(
            gpu_resources.get("preset")
        ) or _as_text(inputs.get("gpu_nodes_preset"))
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


def resolve_mk8s_gpu_app_selection(
    payload_or_config: Any,
    *,
    selected_app_ids: set[str] | None = None,
    app_entries: tuple[ComponentEntry, ...] | None = None,
    cli_settings: Any | None = None,
) -> Mk8sGpuAppSelection:
    payload = _as_payload(payload_or_config)
    contexts = _enabled_mk8s_gpu_contexts(payload)
    selected = {str(item).strip().lower() for item in (selected_app_ids or set()) if str(item).strip()}
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
    stack_sources = {item.gpu_stack_source for item in contexts}
    resolved_stack_source = next(iter(stack_sources)) if len(stack_sources) == 1 else "mixed"
    required_app_ids = _required_gpu_app_ids(contexts)

    issues: list[str] = []
    auto_enabled: list[str] = []
    for app_id in required_app_ids:
        if app_id not in available_ids:
            issues.append(
                f"components.apps.{app_id}.cli.mk8s_gpu references an unknown or unavailable app component"
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
    expected = set(resolve_mk8s_gpu_app_selection(
        payload,
        selected_app_ids=set(),
        app_entries=app_entries,
        cli_settings=cli_settings,
    ).selected_app_ids)
    missing = sorted(expected - selected)
    for app_id in missing:
        issues.append(
            f"GPU-enabled MK8s deployment requires 'apps:{app_id}' to be enabled"
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
    required_app_ids = set(_required_gpu_app_ids(contexts, gpu_settings=settings))

    validations: list[dict[str, Any]] = []
    app_entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    role_map = _mk8s_gpu_app_id_by_role()
    operator_readiness = settings.validations.operator_readiness
    if operator_readiness.enabled_by_default:
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
                "component_sources.yaml must declare one apps component with cli.mk8s_gpu.role: gpu_operator and a release namespace"
            )
        if network_operator_id in required_app_ids and not network_operator_namespace:
            raise ValueError(
                "component_sources.yaml must declare one apps component with cli.mk8s_gpu.role: network_operator and a release namespace"
            )
        validations.append(
            {
                "kind": "mk8s_gpu_operator_readiness",
                "name": "GPU Operator readiness",
                "timeout": operator_readiness.timeout,
                "gpu_operator_namespace": gpu_operator_namespace,
                "network_operator_namespace": network_operator_namespace,
                "network_operator_required": network_operator_id in required_app_ids,
                "gpu_cluster_enabled": any(item.gpu_cluster_enabled for item in contexts),
                "report_file": "gpu-operator-readiness-report.json",
            }
        )

    gpu_visibility = settings.validations.gpu_visibility
    if gpu_visibility.enabled_by_default:
        if not gpu_visibility.namespace or not gpu_visibility.image or not gpu_visibility.timeout:
            raise ValueError(
                "components.infra.mk8s.cli.gpu.validations.gpu_visibility must set namespace, image, and timeout"
            )
        validations.append(
            {
                "kind": "mk8s_gpu_visibility",
                "name": "GPU Visibility test",
                "namespace": gpu_visibility.namespace,
                "image": gpu_visibility.image,
                "timeout": gpu_visibility.timeout,
                "cleanup": gpu_visibility.cleanup,
                "report_file": "gpu-visibility-report.json",
            }
        )

    gpu_cluster_context = next((item for item in contexts if item.gpu_cluster_enabled), None)
    nccl = settings.validations.nccl
    if gpu_cluster_context is not None and nccl.enabled_by_default:
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
        chart_values = _deep_merge_dict(
            _defaults_to_mapping(chart_entry.defaults, strip_root="values"),
            _defaults_to_mapping(
                _resolved_app_value_defaults(
                    app_id=nccl.chart_component_id,
                    contexts=(gpu_cluster_context,),
                ),
                strip_root="values",
            ),
        )
        namespace = _as_text(chart_entry.namespace) or nccl.chart_component_id
        validations.append(
            {
                "kind": "mk8s_nccl",
                "name": "NCCL test",
                "chart_component_id": nccl.chart_component_id,
                "chart_name_or_ref": chart_name_or_ref,
                "chart_repo": chart_repo,
                "chart_version": chart_version,
                "namespace": namespace,
                "timeout": nccl.timeout,
                "training_operator_manifest": nccl.training_operator_manifest,
                "training_operator_namespace": nccl.training_operator_namespace,
                "average_bus_bandwidth_threshold_gbps": nccl.average_bus_bandwidth_threshold_gbps,
                "chart_values": chart_values,
                "gpu_platform": gpu_cluster_context.gpu_platform,
                "report_file": "nccl-test-report.json",
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
    defaults: list[ComponentDefault] = []
    for rule in policy.value_overrides:
        if any(_app_value_rule_matches(context, rule) for context in contexts):
            defaults.extend(rule.values)
    return tuple(defaults)


def materialize_mk8s_gpu_app_values(payload_or_config: Any) -> None:
    payload = payload_or_config if isinstance(payload_or_config, dict) else _as_payload(payload_or_config)
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, dict) else None
    if not isinstance(charts, list):
        return
    contexts = _enabled_mk8s_gpu_contexts(payload)
    if not contexts:
        return

    stack_sources = {item.gpu_stack_source for item in contexts}
    if len(stack_sources) != 1:
        raise RuntimeError(
            "GPU-enabled MK8s config mixes Nebius-image and manual GPU stack sources; split them into separate deployments"
        )
    policies = _mk8s_gpu_app_policies()
    if any(
        rule.match_platforms or rule.match_presets
        for policy in policies.values()
        for rule in policy.value_overrides
    ):
        platforms = {item.gpu_platform for item in contexts}
        presets = {item.gpu_preset for item in contexts}
        if len(platforms) > 1 or len(presets) > 1:
            raise RuntimeError(
                "Platform- or preset-specific MK8s GPU app value rules require a single GPU platform/preset per deployment"
            )
    if any(
        rule.gpu_cluster_enabled is not None
        for policy in policies.values()
        for rule in policy.value_overrides
    ):
        cluster_states = {item.gpu_cluster_enabled for item in contexts}
        if len(cluster_states) > 1:
            raise RuntimeError(
                "GPU-cluster-specific MK8s app value rules require all GPU-enabled MK8s components in a deployment to agree on whether GPU clustering is enabled"
            )

    for chart in charts:
        if not isinstance(chart, dict) or not bool(chart.get("enabled", False)):
            continue
        chart_id = component_type_id(chart)
        if chart_id not in policies:
            continue
        resolved_defaults = _resolved_app_value_defaults(
            app_id=chart_id,
            contexts=contexts,
        )
        # Shared Helm releases cannot safely accept conflicting values from
        # different GPU contexts in one deployment.
        values_by_path: dict[str, Any] = {}
        for default in resolved_defaults:
            existing = values_by_path.get(default.target_path, default.value)
            if default.target_path in values_by_path and existing != default.value:
                raise RuntimeError(
                    f"MK8s GPU app value rules resolve conflicting values for '{chart_id}' at '{default.target_path}'"
                )
            values_by_path[default.target_path] = default.value
        for default in resolved_defaults:
            if _path_value(chart, default.target_path) is not None:
                continue
            _set_path_value(chart, default.target_path, default.value)


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
        total += value * {
            "ns": 1e-9,
            "us": 1e-6,
            "µs": 1e-6,
            "ms": 1e-3,
            "s": 1.0,
            "m": 60.0,
            "h": 3600.0,
        }[unit]
    if position != len(text):
        raise RuntimeError(f"Unsupported Go-style duration: {raw}")
    return max(1, int(total))


def _write_report(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


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


def _gpu_nodes(*, extra_env: dict[str, str] | None) -> list[dict[str, Any]]:
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
        gpu_count = _parse_gpu_quantity(allocatable.get("nvidia.com/gpu"))
        if not ready or gpu_count <= 0:
            continue
        nodes.append({"name": name, "gpu_count": gpu_count})
    return nodes


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


def _gpu_visibility_pod_name(node_name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", node_name.strip().lower()).strip("-")
    return f"gpu-visibility-{slug[:40]}"


def _wait_for_pod_phases(
    *,
    namespace: str,
    pod_names: list[str],
    extra_env: dict[str, str] | None,
    timeout_seconds: int,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    phases: dict[str, str] = {name: "" for name in pod_names}
    while time.monotonic() < deadline:
        all_terminal = True
        for pod_name in pod_names:
            try:
                payload = _kubectl_json(
                    ["get", "pod", pod_name, "-n", namespace, "-o", "json"],
                    extra_env=extra_env,
                    timeout_seconds=30,
                )
            except RuntimeError:
                all_terminal = False
                continue
            phase = _as_text(payload.get("status", {}).get("phase"))
            phases[pod_name] = phase
            if phase not in {"Succeeded", "Failed"}:
                all_terminal = False
        if all_terminal:
            return phases
        time.sleep(3)
    raise RuntimeError(
        f"Timed out waiting for pods in namespace '{namespace}' to finish: {', '.join(pod_names)}"
    )


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
    report_path = inventory_dir / _as_text(spec.get("report_file"))
    nodes = _gpu_nodes(extra_env=extra_env)
    if not nodes:
        raise RuntimeError("GPU Visibility test could not find any Ready Kubernetes node with allocatable GPUs")

    if emit:
        emit(f"Running GPU Visibility test on {len(nodes)} GPU node(s).")

    docs: list[dict[str, Any]] = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
    ]
    pod_names: list[str] = []
    try:
        for node in nodes:
            pod_name = _gpu_visibility_pod_name(node["name"])
            pod_names.append(pod_name)
            docs.append(
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": pod_name,
                        "namespace": namespace,
                        "labels": {"app.kubernetes.io/name": "gpu-visibility-test"},
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
        phases = _wait_for_pod_phases(
            namespace=namespace,
            pod_names=pod_names,
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
        )
        results: list[dict[str, Any]] = []
        passed = True
        for node, pod_name in zip(nodes, pod_names, strict=True):
            logs = _run_kubectl(
                ["logs", pod_name, "-n", namespace],
                extra_env=extra_env,
                timeout_seconds=60,
            ).stdout
            pod_passed = "Test PASSED" in logs or "PASSED" in logs
            passed = passed and pod_passed and phases.get(pod_name) == "Succeeded"
            results.append(
                {
                    "node_name": node["name"],
                    "pod_name": pod_name,
                    "gpu_count": node["gpu_count"],
                    "phase": phases.get(pod_name, ""),
                    "passed": pod_passed,
                    "logs": logs,
                }
            )
        report = {
            "validation": "GPU Visibility test",
            "namespace": namespace,
            "image": image,
            "passed": passed,
            "nodes": results,
        }
        _write_report(report_path, report)
        if not passed:
            raise RuntimeError(f"GPU Visibility test failed. Report: {report_path}")
        return report_path
    finally:
        if cleanup:
            for pod_name in pod_names:
                _delete_resource(
                    ["delete", "pod", pod_name, "-n", namespace, "--ignore-not-found=true"],
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


def _nccl_job_name() -> str:
    return "nccl-test-nebius"


def _wait_for_launcher_completion(
    *,
    namespace: str,
    launcher_pod_name: str,
    extra_env: dict[str, str] | None,
    timeout_seconds: int,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            payload = _kubectl_json(
                ["get", "pod", launcher_pod_name, "-n", namespace, "-o", "json"],
                extra_env=extra_env,
                timeout_seconds=30,
            )
        except RuntimeError:
            time.sleep(5)
            continue
        phase = _as_text(payload.get("status", {}).get("phase"))
        if phase in {"Succeeded", "Failed"}:
            return phase
        time.sleep(5)
    raise RuntimeError(
        f"Timed out waiting for NCCL launcher pod '{launcher_pod_name}' to finish"
    )


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


def _nccl_chart_documents(
    *,
    spec: dict[str, Any],
    worker_count: int,
) -> list[dict[str, Any]]:
    chart_values = spec.get("chart_values")
    values = copy.deepcopy(chart_values) if isinstance(chart_values, dict) else {}
    runtime_values = {
        "worker": {"replicas": worker_count},
        "report": {
            "jsonBeginMarker": _NCCL_JSON_BEGIN_MARKER,
            "jsonEndMarker": _NCCL_JSON_END_MARKER,
        },
        "fullnameOverride": _nccl_job_name(),
    }
    merged_values = _deep_merge_dict(values, runtime_values)
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
    report_path = inventory_dir / _as_text(spec.get("report_file"))
    worker_nodes = _gpu_nodes(extra_env=extra_env)
    if not worker_nodes:
        raise RuntimeError("NCCL test could not find any Ready Kubernetes node with allocatable GPUs")

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
        _run_kubectl(
            [
                "rollout",
                "status",
                "deployment/training-operator",
                "-n",
                training_operator_namespace,
                f"--timeout={timeout_seconds}s",
            ],
            extra_env=extra_env,
            timeout_seconds=timeout_seconds + 30,
        )

    job_name = _nccl_job_name()
    launcher_pod_name = f"{job_name}-launcher"
    with suppress(RuntimeError):
        _run_kubectl(["create", "namespace", namespace], extra_env=extra_env, timeout_seconds=60)
    try:
        if emit:
            emit(
                f"Running NCCL test on {len(worker_nodes)} GPU node(s) for platform "
                f"{_as_text(spec.get('gpu_platform'))}."
            )
        rendered_docs = _nccl_chart_documents(spec=spec, worker_count=len(worker_nodes))
        _apply_docs(rendered_docs, extra_env=extra_env)
        launcher_phase = _wait_for_launcher_completion(
            namespace=namespace,
            launcher_pod_name=launcher_pod_name,
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
        )
        logs = _run_kubectl(
            ["logs", launcher_pod_name, "-n", namespace],
            extra_env=extra_env,
            timeout_seconds=120,
        ).stdout
        result_json = _extract_nccl_json(logs)
        avg_bus_bandwidth, out_of_bounds_ok = _nccl_json_summary(result_json)
        if avg_bus_bandwidth <= 0:
            match = _AVG_BUS_BANDWIDTH_RE.search(logs)
            avg_bus_bandwidth = float(match.group(1)) if match is not None else 0.0
        if out_of_bounds_ok is None:
            out_of_bounds_ok = _OUT_OF_BOUNDS_OK_RE.search(logs) is not None
        chart_values = spec.get("chart_values")
        values_map = dict(chart_values) if isinstance(chart_values, dict) else {}
        image_map = values_map.get("image") if isinstance(values_map.get("image"), dict) else {}
        worker_map = values_map.get("worker") if isinstance(values_map.get("worker"), dict) else {}
        benchmark_map = (
            values_map.get("benchmark") if isinstance(values_map.get("benchmark"), dict) else {}
        )
        passed = (
            launcher_phase == "Succeeded"
            and bool(out_of_bounds_ok)
            and avg_bus_bandwidth > float(spec.get("average_bus_bandwidth_threshold_gbps", 0))
        )
        report = {
            "validation": "NCCL test",
            "chart_component_id": _as_text(spec.get("chart_component_id")),
            "chart_name_or_ref": _as_text(spec.get("chart_name_or_ref")),
            "chart_repo": _as_text(spec.get("chart_repo")),
            "namespace": namespace,
            "job_name": job_name,
            "gpu_platform": _as_text(spec.get("gpu_platform")),
            "worker_node_count": len(worker_nodes),
            "worker_gpus": int(worker_map.get("gpus", 0) or 0),
            "avg_bus_bandwidth_gbps": avg_bus_bandwidth,
            "threshold_gbps": float(spec.get("average_bus_bandwidth_threshold_gbps", 0)),
            "out_of_bounds_ok": bool(out_of_bounds_ok),
            "launcher_phase": launcher_phase,
            "runtime_image": _as_text(image_map.get("repository")),
            "test_binary_path": _as_text(benchmark_map.get("binaryPath")),
            "result_json_path": _as_text(benchmark_map.get("resultJsonPath")),
            "passed": passed,
            "result_json": result_json or {},
            "logs": logs,
        }
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
    network_namespace = _as_text(spec.get("network_operator_namespace")) or "nvidia-network-operator"
    network_required = bool(spec.get("network_operator_required"))

    if emit:
        emit("Waiting for GPU operator resources to report Ready.")

    gpu_ready, gpu_resource = _resource_ready(
        resource_type="clusterpolicy",
        namespace=None,
        expected_status_key="state",
        expected_ready_value="ready",
        extra_env=extra_env,
        timeout_seconds=timeout_seconds,
    )

    network_ready = True
    network_resource: dict[str, Any] = {}
    if network_required:
        if emit:
            emit("Waiting for NVIDIA Network Operator resources to report Ready.")
        network_ready, network_resource = _resource_ready(
            resource_type="nicclusterpolicy",
            namespace=network_namespace,
            expected_status_key="state",
            expected_ready_value="ready",
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
        )

    report = {
        "validation": "GPU Operator readiness",
        "passed": gpu_ready and network_ready,
        "gpu_operator": {
            "namespace": gpu_namespace,
            "ready": gpu_ready,
            "resource_name": _as_text(gpu_resource.get("metadata", {}).get("name")),
            "state": _as_text(gpu_resource.get("status", {}).get("state")),
            "daemonsets": _daemonset_summary(namespace=gpu_namespace, extra_env=extra_env),
        },
        "network_operator": {
            "namespace": network_namespace,
            "required": network_required,
            "ready": network_ready,
            "resource_name": _as_text(network_resource.get("metadata", {}).get("name")),
            "state": _as_text(network_resource.get("status", {}).get("state")),
            "daemonsets": _daemonset_summary(namespace=network_namespace, extra_env=extra_env)
            if network_required
            else [],
        },
        "gpudirect_mode": "dma-buf" if bool(spec.get("gpu_cluster_enabled")) else "",
    }
    _write_report(report_path, report)
    if not report["passed"]:
        raise RuntimeError(f"GPU operator readiness check failed. Report: {report_path}")
    return report_path


def run_mk8s_gpu_validations(
    validations: list[dict[str, Any]],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    written_reports: list[Path] = []
    for spec in validations:
        kind = _as_text(spec.get("kind"))
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
    return written_reports


__all__ = [
    "Mk8sGpuAppSelection",
    "Mk8sGpuClusterContext",
    "materialize_mk8s_gpu_app_values",
    "mk8s_gpu_dependency_issues",
    "mk8s_gpu_flux_release_dependencies",
    "mk8s_gpu_validation_specs",
    "resolve_mk8s_gpu_app_selection",
    "run_mk8s_gpu_validations",
]
