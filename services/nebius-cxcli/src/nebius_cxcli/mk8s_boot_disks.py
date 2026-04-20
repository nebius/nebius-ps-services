"""Bundled MK8s boot-disk sizing and default materialization."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .component_sources import Mk8sBootDiskRule, Mk8sNodeBootDiskPolicy, tf_module_source_by_id
from .provider_options import ProviderOptionLookup

_PRESET_RESOURCE_RE = re.compile(r"(?P<value>\d+)(?P<kind>gpu|vcpu|gb)")
_DEFAULT_SIZE_STEP_GIB = 1
_HIGH_PERFORMANCE_ALLOCATION_UNIT_GIB = 93
_CPU_BOOT_DISK_FLOOR_GIB = 128
_GPU_BOOT_DISK_FLOOR_GIB = 256
_HEURISTIC_TARGET_FILL_RATIO = 0.50
_HEURISTIC_BASE_GIB = 30.0
_HEURISTIC_VCPU_FACTOR = 0.75
_HEURISTIC_MEMORY_FACTOR = 0.10
_HEURISTIC_GPU_NODE_OVERHEAD_GIB = 12.0
_HEURISTIC_PER_GPU_GIB = 8.0
_HIGH_PERFORMANCE_BOOT_DISK_TYPES = frozenset(
    {"NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"}
)


@dataclass(frozen=True)
class Mk8sBootDiskContext:
    component_id: str
    instance_id: str
    scope: str
    platform: str
    preset: str
    gpu_cluster_enabled: bool
    vcpu_count: int | None
    memory_gibibytes: int | None
    gpu_count: int | None


@dataclass(frozen=True)
class Mk8sBootDiskRecommendation:
    context: Mk8sBootDiskContext
    disk_type: str
    size_gib: int | None


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _path_value(mapping: Any, dotted_path: str) -> Any | None:
    current = mapping
    for segment in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        candidates = (segment, segment.replace("-", "_"), segment.replace("_", "-"))
        matched = next((candidate for candidate in candidates if candidate in current), None)
        if matched is None:
            return None
        current = current[matched]
    return current


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved > 0 else None


def _override_boot_disk(inputs: dict[str, Any], *, gpu: bool) -> dict[str, Any]:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    overrides = inputs.get(override_key)
    boot_disk = _path_value(overrides, "template.boot_disk") if isinstance(overrides, dict) else None
    return dict(boot_disk) if isinstance(boot_disk, dict) else {}


def _effective_platform(inputs: dict[str, Any], *, gpu: bool) -> str:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    platform_key = "gpu_nodes_platform" if gpu else "cpu_nodes_platform"
    overrides = inputs.get(override_key)
    override_value = _path_value(overrides, "template.resources.platform") if isinstance(overrides, dict) else None
    return _as_text(override_value) or _as_text(inputs.get(platform_key))


def _effective_preset(inputs: dict[str, Any], *, gpu: bool) -> str:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    preset_key = "gpu_nodes_preset" if gpu else "cpu_nodes_preset"
    overrides = inputs.get(override_key)
    override_value = _path_value(overrides, "template.resources.preset") if isinstance(overrides, dict) else None
    return _as_text(override_value) or _as_text(inputs.get(preset_key))


def _scope_enabled(inputs: dict[str, Any], *, gpu: bool) -> bool:
    override_key = "mk8s_gpu_node_group_overrides" if gpu else "mk8s_cpu_node_group_overrides"
    count_key = "gpu_nodes_count_per_group" if gpu else "cpu_nodes_count"
    if gpu and not bool(inputs.get("gpu_enabled", False)):
        return False
    overrides = inputs.get(override_key)
    autoscaling = _path_value(overrides, "autoscaling") if isinstance(overrides, dict) else None
    if autoscaling is not None:
        return True
    fixed_count = _positive_int(_path_value(overrides, "fixed_node_count"))
    if fixed_count is not None:
        return fixed_count > 0
    configured_count = _positive_int(inputs.get(count_key))
    if configured_count is not None:
        return configured_count > 0
    return True


def _boot_disk_value_explicit(inputs: dict[str, Any], *, gpu: bool, key: str) -> bool:
    override_value = _path_value(_override_boot_disk(inputs, gpu=gpu), key)
    if key == "type":
        return bool(_as_text(override_value))
    if key.startswith("size_"):
        return _positive_int(override_value) is not None
    return override_value is not None


def _first_class_boot_disk_fields(gpu: bool) -> tuple[str, str]:
    return (
        "gpu_nodes_boot_disk_size_gib" if gpu else "cpu_nodes_boot_disk_size_gib",
        "gpu_nodes_boot_disk_type" if gpu else "cpu_nodes_boot_disk_type",
    )


def _resolved_first_class_disk_type(inputs: dict[str, Any], *, gpu: bool) -> str:
    _size_field, type_field = _first_class_boot_disk_fields(gpu)
    return _as_text(inputs.get(type_field)).upper()


def _resolved_first_class_disk_size(inputs: dict[str, Any], *, gpu: bool) -> int | None:
    size_field, _type_field = _first_class_boot_disk_fields(gpu)
    return _positive_int(inputs.get(size_field))


def _parse_preset_resources_from_name(preset_name: str) -> tuple[int | None, int | None, int | None]:
    vcpu_count: int | None = None
    memory_gibibytes: int | None = None
    gpu_count: int | None = None
    for match in _PRESET_RESOURCE_RE.finditer(_as_text(preset_name).lower()):
        value = int(match.group("value"))
        kind = match.group("kind")
        if kind == "vcpu":
            vcpu_count = value
        elif kind == "gb":
            memory_gibibytes = value
        elif kind == "gpu":
            gpu_count = value
    return vcpu_count, memory_gibibytes, gpu_count


def _resolved_preset_resources(
    *,
    project_id: str,
    platform: str,
    preset: str,
    provider_lookup: ProviderOptionLookup | None,
) -> tuple[int | None, int | None, int | None]:
    resolver = (
        getattr(provider_lookup, "compute_platform_preset_resources", None)
        if provider_lookup is not None
        else None
    )
    if callable(resolver):
        resolved = resolver(
            project_id=project_id,
            platform_name=platform,
            preset_name=preset,
        )
        if resolved is not None:
            return resolved
    return _parse_preset_resources_from_name(preset)


def _context_for_scope(
    *,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    project_id: str,
    gpu: bool,
    provider_lookup: ProviderOptionLookup | None,
) -> Mk8sBootDiskContext | None:
    if not _scope_enabled(inputs, gpu=gpu):
        return None
    platform = _effective_platform(inputs, gpu=gpu)
    preset = _effective_preset(inputs, gpu=gpu)
    if not platform or not preset:
        return None
    vcpu_count, memory_gibibytes, gpu_count = _resolved_preset_resources(
        project_id=project_id,
        platform=platform,
        preset=preset,
        provider_lookup=provider_lookup,
    )
    return Mk8sBootDiskContext(
        component_id=component_id,
        instance_id=instance_id,
        scope="gpu" if gpu else "cpu",
        platform=platform,
        preset=preset,
        gpu_cluster_enabled=bool(_as_text(inputs.get("infiniband_fabric"))),
        vcpu_count=vcpu_count,
        memory_gibibytes=memory_gibibytes,
        gpu_count=gpu_count,
    )


def _mk8s_boot_disk_settings():
    module = tf_module_source_by_id("mk8s")
    return module.mk8s_boot_disks if module is not None else None


def _policy_for_scope(*, gpu: bool) -> Mk8sNodeBootDiskPolicy:
    settings = _mk8s_boot_disk_settings()
    if settings is None:
        return Mk8sNodeBootDiskPolicy()
    return settings.gpu if gpu else settings.cpu


def _default_disk_type(*, gpu: bool) -> str:
    return _as_text(_policy_for_scope(gpu=gpu).default_type).upper()


def _rule_matches(context: Mk8sBootDiskContext, rule: Mk8sBootDiskRule) -> bool:
    if rule.gpu_cluster_enabled is not None and context.gpu_cluster_enabled != rule.gpu_cluster_enabled:
        return False
    if rule.match_platforms and context.platform not in rule.match_platforms:
        return False
    if rule.match_presets and context.preset not in rule.match_presets:
        return False
    if rule.min_vcpu is not None and (context.vcpu_count is None or context.vcpu_count < rule.min_vcpu):
        return False
    if rule.max_vcpu is not None and (context.vcpu_count is None or context.vcpu_count > rule.max_vcpu):
        return False
    if (
        rule.min_memory_gib is not None
        and (context.memory_gibibytes is None or context.memory_gibibytes < rule.min_memory_gib)
    ):
        return False
    if (
        rule.max_memory_gib is not None
        and (context.memory_gibibytes is None or context.memory_gibibytes > rule.max_memory_gib)
    ):
        return False
    if rule.min_gpu is not None and (context.gpu_count is None or context.gpu_count < rule.min_gpu):
        return False
    return not (rule.max_gpu is not None and (context.gpu_count is None or context.gpu_count > rule.max_gpu))


def _matched_policy_rule(
    context: Mk8sBootDiskContext,
    policy: Mk8sNodeBootDiskPolicy,
) -> Mk8sBootDiskRule | None:
    for rule in policy.rules:
        if _rule_matches(context, rule):
            return rule
    return None


def _size_step_gib(disk_type: str) -> int:
    normalized = _as_text(disk_type).upper()
    if normalized in _HIGH_PERFORMANCE_BOOT_DISK_TYPES:
        return _HIGH_PERFORMANCE_ALLOCATION_UNIT_GIB
    return _DEFAULT_SIZE_STEP_GIB


def _round_up_to_allocation_unit(value: float, *, disk_type: str) -> int:
    unit = _size_step_gib(disk_type)
    return max(unit, int(math.ceil(value / unit) * unit))


def _heuristic_size_gib(context: Mk8sBootDiskContext) -> float | None:
    if context.vcpu_count is None or context.memory_gibibytes is None:
        return None
    gpu_count = context.gpu_count or 0
    is_gpu_node = 1 if context.scope == "gpu" or gpu_count > 0 else 0
    return (
        _HEURISTIC_BASE_GIB
        + (_HEURISTIC_VCPU_FACTOR * context.vcpu_count)
        + (_HEURISTIC_MEMORY_FACTOR * context.memory_gibibytes)
        + (_HEURISTIC_GPU_NODE_OVERHEAD_GIB * is_gpu_node)
        + (_HEURISTIC_PER_GPU_GIB * gpu_count)
    )


def _recommended_size_gib(
    context: Mk8sBootDiskContext,
    *,
    disk_type: str,
    policy: Mk8sNodeBootDiskPolicy,
) -> int | None:
    matched_rule = _matched_policy_rule(context, policy)
    if matched_rule is not None and matched_rule.size_gib is not None:
        return _round_up_to_allocation_unit(matched_rule.size_gib, disk_type=disk_type)
    heuristic = _heuristic_size_gib(context)
    if heuristic is None:
        return None
    floor_gib = (
        _GPU_BOOT_DISK_FLOOR_GIB
        if context.scope == "gpu" or (context.gpu_count or 0) > 0
        else _CPU_BOOT_DISK_FLOOR_GIB
    )
    raw_size = max(floor_gib, heuristic / _HEURISTIC_TARGET_FILL_RATIO)
    return _round_up_to_allocation_unit(raw_size, disk_type=disk_type)


def resolve_mk8s_boot_disk_recommendation(
    *,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    project_id: str,
    gpu: bool,
    provider_lookup: ProviderOptionLookup | None,
    disk_type_override: str | None = None,
) -> Mk8sBootDiskRecommendation | None:
    policy = _policy_for_scope(gpu=gpu)
    context = _context_for_scope(
        component_id=component_id,
        instance_id=instance_id,
        inputs=inputs,
        project_id=project_id,
        gpu=gpu,
        provider_lookup=provider_lookup,
    )
    if context is None:
        return None
    matched_rule = _matched_policy_rule(context, policy)
    disk_type = (
        _as_text(disk_type_override).upper()
        or _as_text(matched_rule.type if matched_rule is not None else "").upper()
        or _as_text(policy.default_type).upper()
    )
    size_gib = _recommended_size_gib(context, disk_type=disk_type, policy=policy) if disk_type else None
    return Mk8sBootDiskRecommendation(
        context=context,
        disk_type=disk_type,
        size_gib=size_gib,
    )


def materialize_mk8s_boot_disk_defaults(
    payload: dict[str, Any],
    *,
    provider_lookup: ProviderOptionLookup | None = None,
) -> bool:
    settings = _mk8s_boot_disk_settings()
    if settings is None:
        return False
    client_info = _mapping(payload.get("client_info"))
    nebius = _mapping(client_info.get("nebius"))
    project_id = _as_text(nebius.get("project_id"))
    if not project_id:
        return False
    infra = _mapping(payload.get("infra"))
    components = infra.get("components")
    if not isinstance(components, list):
        return False

    changed = False
    for row in components:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        if component_id != "mk8s":
            continue
        instance_id = component_instance_id(row) or component_id
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            continue

        for gpu in (False, True):
            recommendation = resolve_mk8s_boot_disk_recommendation(
                component_id=component_id,
                instance_id=instance_id,
                inputs=inputs,
                project_id=project_id,
                gpu=gpu,
                provider_lookup=provider_lookup,
            )
            if recommendation is None:
                continue
            size_field, type_field = _first_class_boot_disk_fields(gpu)
            if (
                recommendation.size_gib is not None
                and not _positive_int(inputs.get(size_field))
                and not _boot_disk_value_explicit(inputs, gpu=gpu, key="size_bytes")
                and not _boot_disk_value_explicit(inputs, gpu=gpu, key="size_kibibytes")
                and not _boot_disk_value_explicit(inputs, gpu=gpu, key="size_mebibytes")
                and not _boot_disk_value_explicit(inputs, gpu=gpu, key="size_gibibytes")
            ):
                inputs[size_field] = recommendation.size_gib
                changed = True
            if (
                recommendation.disk_type
                and not _resolved_first_class_disk_type(inputs, gpu=gpu)
                and not _boot_disk_value_explicit(inputs, gpu=gpu, key="type")
            ):
                inputs[type_field] = recommendation.disk_type
                changed = True
    return changed


def refresh_mk8s_boot_disk_defaults(
    inputs: dict[str, Any],
    previous_inputs: dict[str, Any],
    *,
    component_id: str,
    instance_id: str,
    project_id: str,
    provider_lookup: ProviderOptionLookup | None = None,
) -> bool:
    changed = False
    for gpu in (False, True):
        size_field, type_field = _first_class_boot_disk_fields(gpu)
        previous_auto_recommendation = resolve_mk8s_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
        )
        previous_context = _context_for_scope(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
        )
        current_context = _context_for_scope(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
        )
        if previous_context is None and current_context is None:
            continue
        previous_auto_type = (
            _as_text(previous_auto_recommendation.disk_type).upper()
            if previous_auto_recommendation is not None
            else _default_disk_type(gpu=gpu)
        )
        previous_type = _resolved_first_class_disk_type(previous_inputs, gpu=gpu) or previous_auto_type
        previous_recommendation = resolve_mk8s_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
            disk_type_override=previous_type,
        )
        previous_size = previous_recommendation.size_gib if previous_recommendation is not None else None
        current_type = _resolved_first_class_disk_type(inputs, gpu=gpu)
        current_size = _resolved_first_class_disk_size(inputs, gpu=gpu)
        size_override_explicit = any(
            _boot_disk_value_explicit(inputs, gpu=gpu, key=key)
            for key in ("size_bytes", "size_kibibytes", "size_mebibytes", "size_gibibytes")
        )
        type_override_explicit = _boot_disk_value_explicit(inputs, gpu=gpu, key="type")
        current_auto_recommendation = resolve_mk8s_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
        )
        new_default_type = (
            _as_text(current_auto_recommendation.disk_type).upper()
            if current_auto_recommendation is not None
            else _default_disk_type(gpu=gpu)
        )
        refresh_first_class_type = (
            not type_override_explicit
            and (not current_type or current_type == previous_auto_type)
        )
        effective_type = current_type
        if refresh_first_class_type and new_default_type:
            effective_type = new_default_type
            if current_type != new_default_type:
                inputs[type_field] = new_default_type
                current_type = new_default_type
                changed = True

        new_recommendation = resolve_mk8s_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            gpu=gpu,
            provider_lookup=provider_lookup,
            disk_type_override=effective_type,
        )
        if new_recommendation is None:
            if refresh_first_class_type and current_type and current_type == previous_auto_type:
                inputs.pop(type_field, None)
                changed = True
            if (
                not size_override_explicit
                and current_size is not None
                and previous_size is not None
                and current_size == previous_size
            ):
                inputs.pop(size_field, None)
                changed = True
            continue

        refresh_first_class_size = (
            not size_override_explicit
            and (
                current_size is None
                or previous_size is None
                or current_size == previous_size
            )
        )
        if (
            refresh_first_class_size
            and new_recommendation.size_gib is not None
            and current_size != new_recommendation.size_gib
        ):
            inputs[size_field] = new_recommendation.size_gib
            changed = True
    return changed


__all__ = [
    "Mk8sBootDiskContext",
    "Mk8sBootDiskRecommendation",
    "materialize_mk8s_boot_disk_defaults",
    "refresh_mk8s_boot_disk_defaults",
    "resolve_mk8s_boot_disk_recommendation",
]
