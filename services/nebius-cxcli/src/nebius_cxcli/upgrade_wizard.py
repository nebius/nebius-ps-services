"""Reusable choice builders for day-2 upgrade wizards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .mk8s_upgrade import (
    CompatibilityChoice,
    LiveNodeGroup,
    live_node_group_from_sdk,
    node_group_uses_nebius_gpu_image,
    select_live_node_groups_for_node_template,
    sort_live_node_groups,
    source_node_groups_by_name,
)
from .provider_options import OptionChoice


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _raw_node_group_name(node_group: Any) -> str:
    metadata = getattr(node_group, "metadata", None)
    return _text(getattr(metadata, "name", None)) or _text(getattr(metadata, "id", None))


def live_node_groups_from_sdk(
    *,
    source_component: Mapping[str, Any],
    live_node_groups: Sequence[Any],
) -> tuple[LiveNodeGroup, ...]:
    """Bind raw live SDK node groups to source config rows when possible."""

    source_groups = source_node_groups_by_name(source_component)
    return sort_live_node_groups(
        tuple(
            live_node_group_from_sdk(
                raw,
                source=source_groups.get(_raw_node_group_name(raw)),
            )
            for raw in live_node_groups
        )
    )


def recommended_choice_value(choices: Sequence[OptionChoice]) -> str:
    """Return the value that should be preselected for a wizard list."""

    if not choices:
        return ""
    return next((choice.value for choice in choices if choice.recommended), choices[0].value)


def _finalize_upgrade_choices(
    choices: Sequence[OptionChoice],
    *,
    current_values: set[str],
    current_label: str,
) -> list[OptionChoice]:
    resolved: list[OptionChoice] = []
    for choice in choices:
        label = choice.label
        is_current = choice.value in current_values
        if is_current and "current" not in label:
            label = f"{label}  ({current_label})"
        resolved.append(
            OptionChoice(
                value=choice.value,
                label=label,
                recommended=choice.recommended and not is_current,
                metadata=choice.metadata,
            )
        )

    if resolved and not any(choice.recommended for choice in resolved):
        for index, choice in enumerate(resolved):
            if choice.value not in current_values:
                resolved[index] = replace(choice, recommended=True)
                break
        else:
            resolved[0] = replace(resolved[0], recommended=True)
    return resolved


def _choice_field(choice: CompatibilityChoice | Any, field: str) -> str:
    return _text(getattr(choice, field, None))


def _common_ordered_values(value_sets: Sequence[Sequence[str]]) -> list[str]:
    if not value_sets:
        return []
    common = set(value_sets[0])
    for values in value_sets[1:]:
        common &= set(values)
    return [value for value in value_sets[0] if value in common]


def _node_template_choice_matches_group(
    choice: CompatibilityChoice | Any,
    group: LiveNodeGroup,
    *,
    target_os: str = "",
    target_gpu_stack_preset: str = "",
) -> bool:
    choice_platform = _choice_field(choice, "platform")
    if choice_platform and group.platform and choice_platform != group.platform:
        return False
    choice_os = _choice_field(choice, "os")
    if target_os and choice_os != target_os:
        return False
    choice_driver = _choice_field(choice, "drivers_preset")
    if not group.gpu:
        return not choice_driver
    if node_group_uses_nebius_gpu_image(group):
        if target_gpu_stack_preset:
            return choice_driver == target_gpu_stack_preset
        return bool(choice_driver)
    return not choice_driver


def node_template_os_choices(
    *,
    source_component: Mapping[str, Any],
    target_version: str,
    target_gpu_stack_preset: str,
    live_node_groups: Sequence[Any],
    node_group: str,
    compatibility_lookup: Any,
) -> list[OptionChoice]:
    """Build OS choices valid for every selected node-template group."""

    groups = live_node_groups_from_sdk(
        source_component=source_component,
        live_node_groups=live_node_groups,
    )
    selected_groups = select_live_node_groups_for_node_template(groups, node_group=node_group)
    current_values = {group.os for group in selected_groups if group.os}
    per_group_values: list[list[str]] = []
    labels: dict[str, str] = {}

    for group in selected_groups:
        group_values: list[str] = []
        for choice in compatibility_lookup(target_version=target_version, platform=group.platform):
            if not _node_template_choice_matches_group(
                choice,
                group,
                target_gpu_stack_preset=target_gpu_stack_preset,
            ):
                continue
            os_value = _choice_field(choice, "os")
            if not os_value or os_value in group_values:
                continue
            group_values.append(os_value)
            labels.setdefault(os_value, os_value)
        per_group_values.append(group_values)

    choices = [
        OptionChoice(value=value, label=labels.get(value, value))
        for value in _common_ordered_values(per_group_values)
    ]
    return _finalize_upgrade_choices(
        choices,
        current_values=current_values,
        current_label="current on selected live node group",
    )

def node_template_gpu_stack_choices(
    *,
    source_component: Mapping[str, Any],
    target_version: str,
    target_os: str,
    live_node_groups: Sequence[Any],
    node_group: str,
    compatibility_lookup: Any,
) -> list[OptionChoice]:
    """Build GPU stack choices valid for every selected Nebius-image GPU group."""

    groups = live_node_groups_from_sdk(
        source_component=source_component,
        live_node_groups=live_node_groups,
    )
    selected_groups = select_live_node_groups_for_node_template(groups, node_group=node_group)
    nebius_image_groups = tuple(
        group for group in selected_groups if node_group_uses_nebius_gpu_image(group)
    )
    current_values = {group.drivers_preset for group in nebius_image_groups if group.drivers_preset}
    per_group_values: list[list[str]] = []
    labels: dict[str, str] = {}

    for group in nebius_image_groups:
        group_values: list[str] = []
        for choice in compatibility_lookup(target_version=target_version, platform=group.platform):
            if not _node_template_choice_matches_group(choice, group, target_os=target_os):
                continue
            driver_preset = _choice_field(choice, "drivers_preset")
            if not driver_preset or driver_preset in group_values:
                continue
            group_values.append(driver_preset)
            labels.setdefault(driver_preset, f"{driver_preset}  ({target_os})")
        per_group_values.append(group_values)

    choices = [
        OptionChoice(value=value, label=labels.get(value, value))
        for value in _common_ordered_values(per_group_values)
    ]
    return _finalize_upgrade_choices(
        choices,
        current_values=current_values,
        current_label="current on selected live node group",
    )
