"""Reusable choice builders for day-2 upgrade wizards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .mk8s_upgrade import (
    LiveNodeGroup,
    NodeLayerUpgradeSpec,
    live_node_group_from_sdk,
    select_live_node_groups_for_node_layer,
    select_live_node_groups_for_os_image,
    sort_live_node_groups,
    source_node_groups_by_name,
)
from .provider_options import OptionChoice, ProviderOptionLookup


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _raw_node_group_name(node_group: Any) -> str:
    metadata = getattr(node_group, "metadata", None)
    return _text(getattr(metadata, "name", None)) or _text(getattr(metadata, "id", None))


def _node_group_field_path(
    *,
    component_path_label: str,
    group: LiveNodeGroup,
    field: str,
) -> str:
    if component_path_label and group.source is not None and group.source.key:
        return f"{component_path_label}.inputs.node_groups.{group.source.key}.{field}"
    return f"upgrade.node_groups.{group.name}.{field}"


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


def _append_choice(
    choices: list[OptionChoice],
    seen: set[str],
    choice: OptionChoice,
) -> None:
    value = _text(choice.value)
    if not value or value in seen:
        return
    choices.append(choice)
    seen.add(value)


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


def _provider_choice_args(
    *,
    project_id: str,
    tenant_id: str,
    region_id: str,
    k8s_version: str,
    group: LiveNodeGroup,
    spec: NodeLayerUpgradeSpec,
) -> tuple[str, dict[str, Any]]:
    if spec.live_field == "platform":
        platform_prefix = ""
        if group.platform.startswith("gpu-"):
            platform_prefix = "gpu-"
        elif group.platform.startswith("cpu-"):
            platform_prefix = "cpu-"
        args: dict[str, Any] = {
            "kubernetes_version_default": k8s_version,
            "project_id": project_id,
        }
        if platform_prefix:
            args["platform_prefix"] = platform_prefix
        return "mk8s_compatible_platforms", args

    if spec.live_field == "drivers_preset":
        return (
            "mk8s_gpu_stack_presets",
            {
                "kubernetes_version_default": k8s_version,
                "platform": group.platform,
                "os": group.os,
                "project_id": project_id,
            },
        )

    if spec.live_field == "preset":
        return (
            "compute_platform_presets",
            {
                "platform": group.platform,
                "project_id": project_id,
                "tenant_id": tenant_id,
                "region_id": region_id,
            },
        )

    return "", {}


def node_layer_value_choices(
    *,
    provider_lookup: ProviderOptionLookup,
    source_payload: dict[str, Any],
    source_component: Mapping[str, Any],
    component_path_label: str,
    project_id: str,
    tenant_id: str,
    region_id: str,
    k8s_version: str,
    spec: NodeLayerUpgradeSpec,
    live_node_groups: Sequence[Any],
    node_group: str,
) -> list[OptionChoice]:
    """Build live-provider choices for a node-template upgrade value."""

    groups = live_node_groups_from_sdk(
        source_component=source_component,
        live_node_groups=live_node_groups,
    )
    selected_groups = select_live_node_groups_for_node_layer(
        groups,
        node_group=node_group,
        group_filter=spec.group_filter,
        command=spec.command,
    )
    choices: list[OptionChoice] = []
    seen: set[str] = set()
    current_values: set[str] = set()

    for group in selected_groups:
        current = {
            "platform": group.platform,
            "preset": group.preset,
            "drivers_preset": group.drivers_preset,
        }.get(spec.live_field, "")
        if current:
            current_values.add(current)

        provider, args = _provider_choice_args(
            project_id=project_id,
            tenant_id=tenant_id,
            region_id=region_id,
            k8s_version=k8s_version,
            group=group,
            spec=spec,
        )
        if not provider:
            continue
        field_path = _node_group_field_path(
            component_path_label=component_path_label,
            group=group,
            field=spec.source_field,
        )
        for choice in provider_lookup.resolve(
            provider=provider,
            args=args,
            payload=source_payload,
            field_path=field_path,
        ):
            _append_choice(choices, seen, choice)

    return _finalize_upgrade_choices(
        choices,
        current_values=current_values,
        current_label="current on selected live node group",
    )


def mk8s_os_image_choices(
    *,
    provider_lookup: ProviderOptionLookup,
    source_payload: dict[str, Any],
    source_component: Mapping[str, Any],
    component_path_label: str,
    k8s_version: str,
    live_node_groups: Sequence[Any],
    node_group: str,
) -> list[OptionChoice]:
    """Build live compatibility-matrix OS choices for MK8s node groups."""

    groups = live_node_groups_from_sdk(
        source_component=source_component,
        live_node_groups=live_node_groups,
    )
    selected_groups = select_live_node_groups_for_os_image(groups, node_group=node_group)
    choices: list[OptionChoice] = []
    seen: set[str] = set()
    current_values = {group.os for group in selected_groups if group.os}

    for group in selected_groups:
        field_path = _node_group_field_path(
            component_path_label=component_path_label,
            group=group,
            field="os",
        )
        for choice in provider_lookup.resolve(
            provider="mk8s_node_group_os_values",
            args={
                "kubernetes_version_default": k8s_version,
                "platform": group.platform,
                "stack_preset": group.drivers_preset,
            },
            payload=source_payload,
            field_path=field_path,
        ):
            _append_choice(choices, seen, choice)

    return _finalize_upgrade_choices(
        choices,
        current_values=current_values,
        current_label="current on selected live node group",
    )
