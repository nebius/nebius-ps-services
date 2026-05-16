"""Shared Compute boot-disk recommendation and default materialization."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .component_sources import (
    ComputeBootDiskPolicy,
    ComputeBootDiskRule,
    ComputeBootDiskSettings,
    ComputeBootDiskTypeChoice,
    load_component_sources,
)
from .provider_options import ProviderOptionLookup

_PRESET_RESOURCE_RE = re.compile(r"(?P<value>\d+)(?P<kind>gpu|vcpu|gb)")
_VM_STYLE_COMPONENT_IDS = frozenset({"vm", "ssh-jumphost", "wireguard-gw"})


class ComputeBootDiskRecommendationError(ValueError):
    """Raised when cxcli cannot derive a settings-backed disk recommendation."""


@dataclass(frozen=True)
class ComputeBootDiskContext:
    component_id: str
    instance_id: str
    field_scope: str
    policy_scope: str
    platform: str
    preset: str
    gpu_cluster_enabled: bool
    vcpu_count: int | None
    memory_gibibytes: int | None
    gpu_count: int | None


@dataclass(frozen=True)
class ComputeBootDiskRecommendation:
    context: ComputeBootDiskContext
    disk_type: str
    size_gib: int


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


def _boot_disk_settings() -> ComputeBootDiskSettings:
    return load_component_sources().compute.boot_disk_defaults


def compute_boot_disk_type_choices() -> tuple[ComputeBootDiskTypeChoice, ...]:
    return _boot_disk_settings().disk_types


def compute_boot_disk_type_supports_explicit_encryption(disk_type: str) -> bool:
    normalized = _as_text(disk_type).upper()
    for choice in compute_boot_disk_type_choices():
        if choice.value == normalized:
            return choice.explicit_encryption_supported
    raise ComputeBootDiskRecommendationError(
        f"Disk type {normalized!r} is not declared in "
        "compute.boot_disk_defaults.disk_types. Update component_cli_settings.yaml."
    )


def _boot_disk_type_allocation_unit_gib(disk_type: str) -> int:
    normalized = _as_text(disk_type).upper()
    for choice in compute_boot_disk_type_choices():
        if choice.value == normalized:
            return choice.allocation_unit_gib
    raise ComputeBootDiskRecommendationError(
        f"Disk type {normalized!r} is not declared in "
        "compute.boot_disk_defaults.disk_types. Update component_cli_settings.yaml."
    )


def _round_up_to_allocation_unit(value: int, *, disk_type: str) -> int:
    unit = _boot_disk_type_allocation_unit_gib(disk_type)
    return max(unit, int(math.ceil(value / unit) * unit))


def _parse_preset_resources_from_name(
    preset_name: str,
) -> tuple[int | None, int | None, int | None]:
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


def _mk8s_override_boot_disk(inputs: dict[str, Any], *, field_scope: str) -> dict[str, Any]:
    override_key = (
        "mk8s_gpu_node_group_overrides"
        if field_scope == "gpu"
        else "mk8s_cpu_node_group_overrides"
    )
    overrides = inputs.get(override_key)
    boot_disk = (
        _path_value(overrides, "template.boot_disk") if isinstance(overrides, dict) else None
    )
    return dict(boot_disk) if isinstance(boot_disk, dict) else {}


def _mk8s_effective_platform(inputs: dict[str, Any], *, field_scope: str) -> str:
    override_key = (
        "mk8s_gpu_node_group_overrides"
        if field_scope == "gpu"
        else "mk8s_cpu_node_group_overrides"
    )
    platform_key = "gpu_nodes_platform" if field_scope == "gpu" else "cpu_nodes_platform"
    overrides = inputs.get(override_key)
    override_value = (
        _path_value(overrides, "template.resources.platform")
        if isinstance(overrides, dict)
        else None
    )
    return _as_text(override_value) or _as_text(inputs.get(platform_key))


def _mk8s_effective_preset(inputs: dict[str, Any], *, field_scope: str) -> str:
    override_key = (
        "mk8s_gpu_node_group_overrides"
        if field_scope == "gpu"
        else "mk8s_cpu_node_group_overrides"
    )
    preset_key = "gpu_nodes_preset" if field_scope == "gpu" else "cpu_nodes_preset"
    overrides = inputs.get(override_key)
    override_value = (
        _path_value(overrides, "template.resources.preset") if isinstance(overrides, dict) else None
    )
    return _as_text(override_value) or _as_text(inputs.get(preset_key))


def _mk8s_scope_enabled(inputs: dict[str, Any], *, field_scope: str) -> bool:
    override_key = (
        "mk8s_gpu_node_group_overrides"
        if field_scope == "gpu"
        else "mk8s_cpu_node_group_overrides"
    )
    count_key = "gpu_nodes_count_per_group" if field_scope == "gpu" else "cpu_nodes_count"
    if field_scope == "gpu" and not bool(inputs.get("gpu_enabled", False)):
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


def _component_field_scopes(component_id: str) -> tuple[str, ...]:
    if component_id == "mk8s":
        return ("cpu", "gpu")
    if component_id in _VM_STYLE_COMPONENT_IDS:
        return ("default",)
    return ()


def _boot_disk_fields(component_id: str, field_scope: str) -> tuple[str, str]:
    if component_id == "mk8s":
        return (
            "gpu_nodes_boot_disk_size_gib"
            if field_scope == "gpu"
            else "cpu_nodes_boot_disk_size_gib",
            "gpu_nodes_boot_disk_type"
            if field_scope == "gpu"
            else "cpu_nodes_boot_disk_type",
        )
    return "boot_disk_size_gib", "boot_disk_type"


def _size_override_explicit(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> bool:
    if component_id != "mk8s":
        return False
    boot_disk = _mk8s_override_boot_disk(inputs, field_scope=field_scope)
    return any(
        _positive_int(_path_value(boot_disk, key)) is not None
        for key in ("size_bytes", "size_kibibytes", "size_mebibytes", "size_gibibytes")
    )


def _type_override_explicit(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> bool:
    if component_id != "mk8s":
        return False
    return bool(_as_text(_path_value(_mk8s_override_boot_disk(inputs, field_scope=field_scope), "type")))


def _resolved_first_class_disk_type(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> str:
    _size_field, type_field = _boot_disk_fields(component_id, field_scope)
    return _as_text(inputs.get(type_field)).upper()


def _resolved_first_class_disk_size(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> int | None:
    size_field, _type_field = _boot_disk_fields(component_id, field_scope)
    return _positive_int(inputs.get(size_field))


def _policy_scope(
    *,
    component_id: str,
    field_scope: str,
    platform: str,
    gpu_count: int | None,
) -> str:
    if component_id == "mk8s":
        return field_scope
    if (gpu_count or 0) > 0 or _as_text(platform).lower().startswith("gpu-"):
        return "gpu"
    return "cpu"


def _context_for_scope(
    *,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    project_id: str,
    field_scope: str,
    provider_lookup: ProviderOptionLookup | None,
) -> ComputeBootDiskContext | None:
    if component_id == "mk8s":
        if not _mk8s_scope_enabled(inputs, field_scope=field_scope):
            return None
        platform = _mk8s_effective_platform(inputs, field_scope=field_scope)
        preset = _mk8s_effective_preset(inputs, field_scope=field_scope)
        gpu_cluster_enabled = bool(_as_text(inputs.get("infiniband_fabric")))
    else:
        if _as_text(inputs.get("boot_disk_existing_id")):
            return None
        platform = _as_text(inputs.get("platform"))
        preset = _as_text(inputs.get("preset"))
        gpu_cluster_enabled = bool(_as_text(inputs.get("gpu_cluster_infiniband_fabric")))

    if not platform or not preset:
        return None
    vcpu_count, memory_gibibytes, gpu_count = _resolved_preset_resources(
        project_id=project_id,
        platform=platform,
        preset=preset,
        provider_lookup=provider_lookup,
    )
    resolved_policy_scope = _policy_scope(
        component_id=component_id,
        field_scope=field_scope,
        platform=platform,
        gpu_count=gpu_count,
    )
    return ComputeBootDiskContext(
        component_id=component_id,
        instance_id=instance_id,
        field_scope=field_scope,
        policy_scope=resolved_policy_scope,
        platform=platform,
        preset=preset,
        gpu_cluster_enabled=gpu_cluster_enabled,
        vcpu_count=vcpu_count,
        memory_gibibytes=memory_gibibytes,
        gpu_count=gpu_count,
    )


def _policy_for_scope(policy_scope: str) -> ComputeBootDiskPolicy:
    settings = _boot_disk_settings()
    return settings.gpu if policy_scope == "gpu" else settings.cpu


def _default_disk_type(policy_scope: str) -> str:
    return _as_text(_policy_for_scope(policy_scope).default_type).upper()


def _rule_matches(context: ComputeBootDiskContext, rule: ComputeBootDiskRule) -> bool:
    if (
        rule.gpu_cluster_enabled is not None
        and context.gpu_cluster_enabled != rule.gpu_cluster_enabled
    ):
        return False
    if rule.match_platforms and context.platform not in rule.match_platforms:
        return False
    if rule.match_presets and context.preset not in rule.match_presets:
        return False
    if rule.min_vcpu is not None and (
        context.vcpu_count is None or context.vcpu_count < rule.min_vcpu
    ):
        return False
    if rule.max_vcpu is not None and (
        context.vcpu_count is None or context.vcpu_count > rule.max_vcpu
    ):
        return False
    if rule.min_memory_gib is not None and (
        context.memory_gibibytes is None or context.memory_gibibytes < rule.min_memory_gib
    ):
        return False
    if rule.max_memory_gib is not None and (
        context.memory_gibibytes is None or context.memory_gibibytes > rule.max_memory_gib
    ):
        return False
    if rule.min_gpu is not None and (context.gpu_count is None or context.gpu_count < rule.min_gpu):
        return False
    return not (
        rule.max_gpu is not None and (context.gpu_count is None or context.gpu_count > rule.max_gpu)
    )


def _matched_policy_rule(
    context: ComputeBootDiskContext,
    policy: ComputeBootDiskPolicy,
) -> ComputeBootDiskRule | None:
    for rule in policy.rules:
        if _rule_matches(context, rule):
            return rule
    return None


def _format_context_for_error(context: ComputeBootDiskContext) -> str:
    resources = (
        f"vCPU={context.vcpu_count or 'unknown'}, "
        f"memory_gib={context.memory_gibibytes or 'unknown'}, "
        f"gpu={context.gpu_count or 0}"
    )
    return (
        f"{context.component_id}@{context.instance_id} "
        f"{context.platform}/{context.preset} ({context.policy_scope}, {resources})"
    )


def resolve_compute_boot_disk_recommendation(
    *,
    component_id: str,
    instance_id: str,
    inputs: dict[str, Any],
    project_id: str,
    field_scope: str = "default",
    provider_lookup: ProviderOptionLookup | None,
    disk_type_override: str | None = None,
) -> ComputeBootDiskRecommendation | None:
    context = _context_for_scope(
        component_id=component_id,
        instance_id=instance_id,
        inputs=inputs,
        project_id=project_id,
        field_scope=field_scope,
        provider_lookup=provider_lookup,
    )
    if context is None:
        return None
    policy = _policy_for_scope(context.policy_scope)
    matched_rule = _matched_policy_rule(context, policy)
    if matched_rule is None or matched_rule.size_gib is None:
        raise ComputeBootDiskRecommendationError(
            "No compute.boot_disk_defaults rule matches "
            f"{_format_context_for_error(context)}. Update component_cli_settings.yaml."
        )
    disk_type = (
        _as_text(disk_type_override).upper()
        or _as_text(matched_rule.type).upper()
        or _as_text(policy.default_type).upper()
    )
    if not disk_type:
        raise ComputeBootDiskRecommendationError(
            "compute.boot_disk_defaults must define a default_type for "
            f"{context.policy_scope} or set type on the matching rule."
        )
    return ComputeBootDiskRecommendation(
        context=context,
        disk_type=disk_type,
        size_gib=_round_up_to_allocation_unit(matched_rule.size_gib, disk_type=disk_type),
    )


def materialize_compute_boot_disk_defaults(
    payload: dict[str, Any],
    *,
    provider_lookup: ProviderOptionLookup | None = None,
) -> bool:
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
        scopes = _component_field_scopes(component_id)
        if not scopes:
            continue
        instance_id = component_instance_id(row) or component_id
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            continue

        for field_scope in scopes:
            size_field, type_field = _boot_disk_fields(component_id, field_scope)
            if _positive_int(inputs.get(size_field)) and _as_text(inputs.get(type_field)):
                continue
            recommendation = resolve_compute_boot_disk_recommendation(
                component_id=component_id,
                instance_id=instance_id,
                inputs=inputs,
                project_id=project_id,
                field_scope=field_scope,
                provider_lookup=provider_lookup,
            )
            if recommendation is None:
                continue
            if (
                recommendation.size_gib
                and _resolved_first_class_disk_size(
                    component_id,
                    inputs,
                    field_scope=field_scope,
                )
                is None
                and not _size_override_explicit(
                    component_id,
                    inputs,
                    field_scope=field_scope,
                )
            ):
                inputs[size_field] = recommendation.size_gib
                changed = True
            if (
                recommendation.disk_type
                and not _resolved_first_class_disk_type(
                    component_id,
                    inputs,
                    field_scope=field_scope,
                )
                and not _type_override_explicit(
                    component_id,
                    inputs,
                    field_scope=field_scope,
                )
            ):
                inputs[type_field] = recommendation.disk_type
                changed = True
    return changed


def refresh_compute_boot_disk_defaults(
    inputs: dict[str, Any],
    previous_inputs: dict[str, Any],
    *,
    component_id: str,
    instance_id: str,
    project_id: str,
    provider_lookup: ProviderOptionLookup | None = None,
) -> bool:
    changed = False
    for field_scope in _component_field_scopes(component_id):
        size_field, type_field = _boot_disk_fields(component_id, field_scope)
        previous_auto_recommendation = resolve_compute_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        previous_context = _context_for_scope(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        current_context = _context_for_scope(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        if previous_context is None and current_context is None:
            continue
        previous_policy_scope = (
            previous_context.policy_scope
            if previous_context is not None
            else (current_context.policy_scope if current_context is not None else "cpu")
        )
        previous_auto_type = (
            _as_text(previous_auto_recommendation.disk_type).upper()
            if previous_auto_recommendation is not None
            else _default_disk_type(previous_policy_scope)
        )
        previous_type = (
            _resolved_first_class_disk_type(
                component_id,
                previous_inputs,
                field_scope=field_scope,
            )
            or previous_auto_type
        )
        previous_recommendation = resolve_compute_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=previous_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
            disk_type_override=previous_type,
        )
        previous_size = (
            previous_recommendation.size_gib if previous_recommendation is not None else None
        )
        current_type = _resolved_first_class_disk_type(
            component_id,
            inputs,
            field_scope=field_scope,
        )
        current_size = _resolved_first_class_disk_size(
            component_id,
            inputs,
            field_scope=field_scope,
        )
        size_override_explicit = _size_override_explicit(
            component_id,
            inputs,
            field_scope=field_scope,
        )
        type_override_explicit = _type_override_explicit(
            component_id,
            inputs,
            field_scope=field_scope,
        )
        current_auto_recommendation = resolve_compute_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        current_policy_scope = (
            current_auto_recommendation.context.policy_scope
            if current_auto_recommendation is not None
            else previous_policy_scope
        )
        new_default_type = (
            _as_text(current_auto_recommendation.disk_type).upper()
            if current_auto_recommendation is not None
            else _default_disk_type(current_policy_scope)
        )
        refresh_first_class_type = not type_override_explicit and (
            not current_type or current_type == previous_auto_type
        )
        effective_type = current_type
        if refresh_first_class_type and new_default_type:
            effective_type = new_default_type
            if current_type != new_default_type:
                inputs[type_field] = new_default_type
                current_type = new_default_type
                changed = True

        new_recommendation = resolve_compute_boot_disk_recommendation(
            component_id=component_id,
            instance_id=instance_id,
            inputs=inputs,
            project_id=project_id,
            field_scope=field_scope,
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

        refresh_first_class_size = not size_override_explicit and (
            current_size is None or previous_size is None or current_size == previous_size
        )
        if refresh_first_class_size and current_size != new_recommendation.size_gib:
            inputs[size_field] = new_recommendation.size_gib
            changed = True
    return changed


__all__ = [
    "ComputeBootDiskContext",
    "ComputeBootDiskRecommendation",
    "ComputeBootDiskRecommendationError",
    "compute_boot_disk_type_choices",
    "compute_boot_disk_type_supports_explicit_encryption",
    "materialize_compute_boot_disk_defaults",
    "refresh_compute_boot_disk_defaults",
    "resolve_compute_boot_disk_recommendation",
]
