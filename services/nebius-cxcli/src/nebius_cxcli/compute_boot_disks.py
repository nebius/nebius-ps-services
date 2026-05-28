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
from .mk8s_node_groups import Mk8sNodeGroup, gpu_cluster_fabric, iter_node_groups
from .provider_options import ProviderOptionLookup
from .runtime_introspection import module_variable_names

_PRESET_RESOURCE_RE = re.compile(r"(?P<value>\d+)(?P<kind>gpu|vcpu|gb)")
_VM_STYLE_BOOT_DISK_INPUTS = frozenset(
    {
        "platform",
        "preset",
        "boot_disk_size_gib",
        "boot_disk_type",
    }
)


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


def _vm_style_component_ids() -> frozenset[str]:
    ids: set[str] = set()
    try:
        modules = load_component_sources().tf_modules
    except Exception:
        return frozenset()
    for module in modules:
        source = str(module.metadata_source or module.source or "").strip()
        if not source:
            continue
        variable_names = set(module_variable_names(source))
        if variable_names >= _VM_STYLE_BOOT_DISK_INPUTS:
            ids.add(module.module)
    return frozenset(ids)


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


def compute_disk_type_allocation_unit_gib(disk_type: str) -> int:
    return _boot_disk_type_allocation_unit_gib(disk_type)


def _round_up_to_allocation_unit(value: int, *, disk_type: str) -> int:
    unit = _boot_disk_type_allocation_unit_gib(disk_type)
    return max(unit, int(math.ceil(value / unit) * unit))


def align_compute_disk_size_to_allocation_unit(size_gib: int, *, disk_type: str) -> int:
    return _round_up_to_allocation_unit(size_gib, disk_type=disk_type)


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


def _set_path_value(mapping: dict[str, Any], dotted_path: str, value: Any) -> None:
    current = mapping
    segments = dotted_path.split(".")
    for segment in segments[:-1]:
        next_value = current.setdefault(segment, {})
        if not isinstance(next_value, dict):
            next_value = {}
            current[segment] = next_value
        current = next_value
    current[segments[-1]] = value


def _pop_path_value(mapping: dict[str, Any], dotted_path: str) -> None:
    current = mapping
    segments = dotted_path.split(".")
    for segment in segments[:-1]:
        next_value = current.get(segment)
        if not isinstance(next_value, dict):
            return
        current = next_value
    current.pop(segments[-1], None)


def _field_value(
    component_id: str,
    inputs: dict[str, Any],
    field_name: str,
) -> Any | None:
    if component_id == "mk8s":
        return _path_value(inputs, field_name)
    return inputs.get(field_name)


def _set_field_value(
    component_id: str,
    inputs: dict[str, Any],
    field_name: str,
    value: Any,
) -> None:
    if component_id == "mk8s":
        _set_path_value(inputs, field_name, value)
        return
    inputs[field_name] = value


def _pop_field_value(component_id: str, inputs: dict[str, Any], field_name: str) -> None:
    if component_id == "mk8s":
        _pop_path_value(inputs, field_name)
        return
    inputs.pop(field_name, None)


def _mk8s_node_group_defaults(inputs: dict[str, Any], *, field_scope: str) -> dict[str, Any]:
    defaults = _path_value(inputs, f"node_group_defaults.{field_scope}")
    return dict(defaults) if isinstance(defaults, dict) else {}


def _mk8s_node_groups_for_scope(
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> tuple[Mk8sNodeGroup, ...]:
    gpu = field_scope == "gpu"
    return tuple(group for group in iter_node_groups(inputs) if group.gpu is gpu)


def _mk8s_has_node_groups(inputs: dict[str, Any]) -> bool:
    return bool(iter_node_groups(inputs))


def _mk8s_first_node_group_for_scope(
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> Mk8sNodeGroup | None:
    groups = _mk8s_node_groups_for_scope(inputs, field_scope=field_scope)
    return groups[0] if groups else None


def _mk8s_effective_platform(inputs: dict[str, Any], *, field_scope: str) -> str:
    group = _mk8s_first_node_group_for_scope(inputs, field_scope=field_scope)
    if group is not None and group.platform:
        return group.platform
    return _as_text(_mk8s_node_group_defaults(inputs, field_scope=field_scope).get("platform"))


def _mk8s_effective_preset(inputs: dict[str, Any], *, field_scope: str) -> str:
    group = _mk8s_first_node_group_for_scope(inputs, field_scope=field_scope)
    if group is not None and group.preset:
        return group.preset
    return _as_text(_mk8s_node_group_defaults(inputs, field_scope=field_scope).get("preset"))


def _mk8s_scope_enabled(inputs: dict[str, Any], *, field_scope: str) -> bool:
    if _mk8s_has_node_groups(inputs):
        return any(
            group.platform or group.preset
            for group in _mk8s_node_groups_for_scope(inputs, field_scope=field_scope)
        )
    defaults = _mk8s_node_group_defaults(inputs, field_scope=field_scope)
    return bool(_as_text(defaults.get("platform")) or _as_text(defaults.get("preset")))


def _mk8s_gpu_cluster_enabled(inputs: dict[str, Any], *, field_scope: str) -> bool:
    if field_scope != "gpu":
        return False
    groups = _mk8s_node_groups_for_scope(inputs, field_scope=field_scope)
    if groups:
        return any(group.gpu_cluster_key or group.gpu_cluster_id for group in groups)
    return bool(_as_text(_mk8s_node_group_defaults(inputs, field_scope=field_scope).get("infiniband_fabric")))


def _component_field_scopes(component_id: str) -> tuple[str, ...]:
    if component_id == "mk8s":
        return ("cpu", "gpu")
    if component_id in _vm_style_component_ids():
        return ("default",)
    return ()


def _boot_disk_fields(component_id: str, field_scope: str) -> tuple[str, str]:
    if component_id == "mk8s":
        return (
            f"node_group_defaults.{field_scope}.boot_disk.size_gibibytes",
            f"node_group_defaults.{field_scope}.boot_disk.type",
        )
    return "boot_disk_size_gib", "boot_disk_type"


def _size_override_explicit(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
    previous_inputs: dict[str, Any] | None = None,
) -> bool:
    if component_id != "mk8s":
        return False
    size_field, _type_field = _boot_disk_fields(component_id, field_scope)
    current_size = _positive_int(_field_value(component_id, inputs, size_field))
    if current_size is None:
        return False
    previous_size = (
        _positive_int(_field_value(component_id, previous_inputs, size_field))
        if previous_inputs is not None
        else None
    )
    return previous_size is None or current_size != previous_size


def _type_override_explicit(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
    previous_inputs: dict[str, Any] | None = None,
) -> bool:
    if component_id != "mk8s":
        return False
    _size_field, type_field = _boot_disk_fields(component_id, field_scope)
    current_type = _as_text(_field_value(component_id, inputs, type_field)).upper()
    if not current_type:
        return False
    previous_type = (
        _as_text(_field_value(component_id, previous_inputs, type_field)).upper()
        if previous_inputs is not None
        else ""
    )
    return not previous_type or current_type != previous_type


def _resolved_first_class_disk_type(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> str:
    _size_field, type_field = _boot_disk_fields(component_id, field_scope)
    return _as_text(_field_value(component_id, inputs, type_field)).upper()


def _resolved_first_class_disk_size(
    component_id: str,
    inputs: dict[str, Any],
    *,
    field_scope: str,
) -> int | None:
    size_field, _type_field = _boot_disk_fields(component_id, field_scope)
    return _positive_int(_field_value(component_id, inputs, size_field))


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
        gpu_cluster_enabled = _mk8s_gpu_cluster_enabled(inputs, field_scope=field_scope)
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


def _mk8s_recommendation_inputs_for_group(
    inputs: dict[str, Any],
    group: Mk8sNodeGroup,
) -> tuple[str, dict[str, Any]]:
    field_scope = "gpu" if group.gpu else "cpu"
    defaults: dict[str, Any] = {
        "platform": group.platform,
        "preset": group.preset,
    }
    if group.gpu:
        fabric = gpu_cluster_fabric(inputs, group)
        if fabric:
            defaults["infiniband_fabric"] = fabric
        elif group.gpu_cluster_key or group.gpu_cluster_id:
            defaults["infiniband_fabric"] = group.gpu_cluster_key or group.gpu_cluster_id
    return field_scope, {"node_group_defaults": {field_scope: defaults}}


def _mk8s_group_boot_disk(
    raw_group: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any]:
    boot_disk = raw_group.get("boot_disk")
    if isinstance(boot_disk, dict):
        return boot_disk
    if not create:
        return {}
    boot_disk = {}
    raw_group["boot_disk"] = boot_disk
    return boot_disk


def _materialize_mk8s_node_group_boot_disk_defaults(
    *,
    inputs: dict[str, Any],
    instance_id: str,
    project_id: str,
    provider_lookup: ProviderOptionLookup | None,
) -> bool:
    raw_node_groups = inputs.get("node_groups")
    if not isinstance(raw_node_groups, dict):
        return False
    changed = False
    parsed_groups = {group.key: group for group in iter_node_groups(inputs)}
    for group_key, raw_group in raw_node_groups.items():
        group = parsed_groups.get(_as_text(group_key))
        if group is None or not isinstance(raw_group, dict):
            continue
        boot_disk = _mk8s_group_boot_disk(raw_group)
        if _positive_int(boot_disk.get("size_gibibytes")) and _as_text(boot_disk.get("type")):
            continue
        field_scope, recommendation_inputs = _mk8s_recommendation_inputs_for_group(inputs, group)
        recommendation = resolve_compute_boot_disk_recommendation(
            component_id="mk8s",
            instance_id=instance_id,
            inputs=recommendation_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        if recommendation is None:
            continue
        boot_disk = _mk8s_group_boot_disk(raw_group, create=True)
        if _positive_int(boot_disk.get("size_gibibytes")) is None:
            boot_disk["size_gibibytes"] = recommendation.size_gib
            changed = True
        if not _as_text(boot_disk.get("type")):
            boot_disk["type"] = recommendation.disk_type
            changed = True
    return changed


def _refresh_mk8s_node_group_boot_disk_defaults(
    inputs: dict[str, Any],
    previous_inputs: dict[str, Any],
    *,
    instance_id: str,
    project_id: str,
    provider_lookup: ProviderOptionLookup | None,
) -> bool:
    raw_node_groups = inputs.get("node_groups")
    previous_raw_node_groups = previous_inputs.get("node_groups")
    if not isinstance(raw_node_groups, dict) or not isinstance(previous_raw_node_groups, dict):
        return False

    changed = False
    current_groups = {group.key: group for group in iter_node_groups(inputs)}
    previous_groups = {group.key: group for group in iter_node_groups(previous_inputs)}
    for group_key, raw_group in raw_node_groups.items():
        group = current_groups.get(_as_text(group_key))
        previous_group = previous_groups.get(_as_text(group_key))
        previous_raw_group = previous_raw_node_groups.get(group_key)
        if (
            group is None
            or previous_group is None
            or not isinstance(raw_group, dict)
            or not isinstance(previous_raw_group, dict)
        ):
            continue

        previous_boot_disk = _mk8s_group_boot_disk(previous_raw_group)
        current_boot_disk = _mk8s_group_boot_disk(raw_group)
        previous_field_scope, previous_recommendation_inputs = (
            _mk8s_recommendation_inputs_for_group(previous_inputs, previous_group)
        )
        previous_auto_recommendation = resolve_compute_boot_disk_recommendation(
            component_id="mk8s",
            instance_id=instance_id,
            inputs=previous_recommendation_inputs,
            project_id=project_id,
            field_scope=previous_field_scope,
            provider_lookup=provider_lookup,
        )
        if previous_auto_recommendation is None:
            continue
        previous_auto_type = _as_text(previous_auto_recommendation.disk_type).upper()
        previous_type = _as_text(previous_boot_disk.get("type")).upper() or previous_auto_type
        previous_recommendation = resolve_compute_boot_disk_recommendation(
            component_id="mk8s",
            instance_id=instance_id,
            inputs=previous_recommendation_inputs,
            project_id=project_id,
            field_scope=previous_field_scope,
            provider_lookup=provider_lookup,
            disk_type_override=previous_type,
        )
        previous_size = (
            previous_recommendation.size_gib if previous_recommendation is not None else None
        )
        current_type = _as_text(current_boot_disk.get("type")).upper()
        current_size = _positive_int(current_boot_disk.get("size_gibibytes"))
        type_override_explicit = bool(
            current_type and (not previous_type or current_type != previous_type)
        )
        size_override_explicit = bool(
            current_size is not None
            and (previous_size is None or current_size != previous_size)
        )

        field_scope, recommendation_inputs = _mk8s_recommendation_inputs_for_group(inputs, group)
        current_auto_recommendation = resolve_compute_boot_disk_recommendation(
            component_id="mk8s",
            instance_id=instance_id,
            inputs=recommendation_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
        )
        if current_auto_recommendation is None:
            continue
        new_default_type = _as_text(current_auto_recommendation.disk_type).upper()
        refresh_type = not type_override_explicit and (
            not current_type or current_type == previous_auto_type
        )
        effective_type = current_type
        if refresh_type and new_default_type:
            effective_type = new_default_type
            if current_type != new_default_type:
                current_boot_disk = _mk8s_group_boot_disk(raw_group, create=True)
                current_boot_disk["type"] = new_default_type
                current_type = new_default_type
                changed = True

        new_recommendation = resolve_compute_boot_disk_recommendation(
            component_id="mk8s",
            instance_id=instance_id,
            inputs=recommendation_inputs,
            project_id=project_id,
            field_scope=field_scope,
            provider_lookup=provider_lookup,
            disk_type_override=effective_type,
        )
        if new_recommendation is None:
            continue
        refresh_size = not size_override_explicit and (
            current_size is None or previous_size is None or current_size == previous_size
        )
        if refresh_size and current_size != new_recommendation.size_gib:
            current_boot_disk = _mk8s_group_boot_disk(raw_group, create=True)
            current_boot_disk["size_gibibytes"] = new_recommendation.size_gib
            changed = True
    return changed


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
        if component_id == "mk8s" and _mk8s_has_node_groups(inputs):
            if _materialize_mk8s_node_group_boot_disk_defaults(
                inputs=inputs,
                instance_id=instance_id,
                project_id=project_id,
                provider_lookup=provider_lookup,
            ):
                changed = True
            continue

        for field_scope in scopes:
            size_field, type_field = _boot_disk_fields(component_id, field_scope)
            if _positive_int(_field_value(component_id, inputs, size_field)) and _as_text(
                _field_value(component_id, inputs, type_field)
            ):
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
                _set_field_value(component_id, inputs, size_field, recommendation.size_gib)
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
                _set_field_value(component_id, inputs, type_field, recommendation.disk_type)
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
    if component_id == "mk8s" and _mk8s_has_node_groups(inputs):
        return _refresh_mk8s_node_group_boot_disk_defaults(
            inputs,
            previous_inputs,
            instance_id=instance_id,
            project_id=project_id,
            provider_lookup=provider_lookup,
        )

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
            previous_inputs=previous_inputs,
        )
        type_override_explicit = _type_override_explicit(
            component_id,
            inputs,
            field_scope=field_scope,
            previous_inputs=previous_inputs,
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
                _set_field_value(component_id, inputs, type_field, new_default_type)
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
                _pop_field_value(component_id, inputs, type_field)
                changed = True
            if (
                not size_override_explicit
                and current_size is not None
                and previous_size is not None
                and current_size == previous_size
            ):
                _pop_field_value(component_id, inputs, size_field)
                changed = True
            continue

        refresh_first_class_size = not size_override_explicit and (
            current_size is None or previous_size is None or current_size == previous_size
        )
        if refresh_first_class_size and current_size != new_recommendation.size_gib:
            _set_field_value(component_id, inputs, size_field, new_recommendation.size_gib)
            changed = True
    return changed


__all__ = [
    "ComputeBootDiskContext",
    "ComputeBootDiskRecommendation",
    "ComputeBootDiskRecommendationError",
    "align_compute_disk_size_to_allocation_unit",
    "compute_boot_disk_type_choices",
    "compute_boot_disk_type_supports_explicit_encryption",
    "compute_disk_type_allocation_unit_gib",
    "materialize_compute_boot_disk_defaults",
    "refresh_compute_boot_disk_defaults",
    "resolve_compute_boot_disk_recommendation",
]
