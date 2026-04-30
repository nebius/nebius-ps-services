"""Helpers for explicit cluster targets and target-scoped Flux layout.

User-authored config binds target-scoped app rows with ``instance_id`` only.
Generated/runtime metadata may carry ``target_ref``, but it is always a derived
copy of the same target identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_type_id, normalize_component_token
from .components import component_entries
from .paths import ProjectPaths
from .runtime_config import to_plain_data

TARGET_REF_FIELD = "target_ref"
DEPLOY_TARGET_INSTANCE_ID_FIELD = "instance_id"


def app_chart_target_ref(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(TARGET_REF_FIELD))


def deploy_target_instance_id(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(DEPLOY_TARGET_INSTANCE_ID_FIELD))


def _required_generated_target_field(
    row: Mapping[str, Any],
    *,
    index: int,
    field_name: str,
    normalize_token: bool = False,
) -> str:
    value = row.get(field_name)
    normalized = normalize_component_token(value) if normalize_token else str(value or "").strip()
    if not normalized:
        raise ValueError(f"Generated manifest deploy.targets[{index}].{field_name} is required")
    return normalized


def normalize_generated_deploy_target(row: Any, *, index: int) -> dict[str, str]:
    """Return a validated generated deploy target row.

    The generated manifest keeps ``target_ref`` for internal Flux/deploy routing,
    but the concrete contract is that it must exactly match the target
    ``instance_id`` from authored config.
    """
    if not isinstance(row, Mapping):
        raise ValueError(f"Generated manifest deploy.targets[{index}] must be a mapping")

    component_id = _required_generated_target_field(
        row, index=index, field_name="component_id", normalize_token=True
    )
    instance_id = _required_generated_target_field(
        row, index=index, field_name=DEPLOY_TARGET_INSTANCE_ID_FIELD, normalize_token=True
    )
    target_ref = _required_generated_target_field(
        row, index=index, field_name=TARGET_REF_FIELD, normalize_token=True
    )
    if target_ref != instance_id:
        raise ValueError(
            f"Generated manifest deploy.targets[{index}].{TARGET_REF_FIELD} must equal "
            f"{DEPLOY_TARGET_INSTANCE_ID_FIELD} '{instance_id}'"
        )

    access = _required_generated_target_field(row, index=index, field_name="access").lower()
    if access not in {"external", "internal"}:
        raise ValueError(
            f"Generated manifest deploy.targets[{index}].access must be external or internal"
        )

    return {
        "component_id": component_id,
        DEPLOY_TARGET_INSTANCE_ID_FIELD: instance_id,
        TARGET_REF_FIELD: target_ref,
        "cluster_id_output_name": _required_generated_target_field(
            row, index=index, field_name="cluster_id_output_name"
        ),
        "component_output_ref": _required_generated_target_field(
            row, index=index, field_name="component_output_ref"
        ),
        "access": access,
        "flux_dir": _required_generated_target_field(row, index=index, field_name="flux_dir"),
    }


def target_scoped_app_instance_id(app_id: Any, *, target_ref: Any) -> str:
    """Return the canonical app instance id for one chart installed on one target."""
    normalized_target_ref = normalize_component_token(target_ref)
    if normalized_target_ref:
        return normalized_target_ref
    return normalize_component_token(app_id)


def is_auto_target_scoped_app_instance_id(
    instance_id: Any,
    *,
    app_id: Any,
    target_ref: Any,
) -> bool:
    """Return true for the canonical target-bound chart instance id."""
    normalized_instance_id = normalize_component_token(instance_id)
    normalized_target_ref = normalize_component_token(target_ref)
    _ = app_id
    if not normalized_instance_id or not normalized_target_ref:
        return False
    return normalized_instance_id == normalized_target_ref


def enabled_cluster_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        return ()
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    handoff_component_ids = {
        entry.id for entry in component_entries("infra") if entry.handoff is not None
    }
    refs: list[str] = []
    seen: set[str] = set()
    for row in components:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) not in handoff_component_ids:
            continue
        instance_id = component_instance_id(row)
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        refs.append(instance_id)
    return tuple(refs)


def _app_charts_node(payload: dict[str, Any]) -> list[Any] | None:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        return None
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return None
    return charts


def materialize_app_chart_target_refs(payload: dict[str, Any]) -> bool:
    """Derive internal app chart target refs from target-scoped instance ids."""
    target_refs = set(enabled_cluster_target_refs(payload))
    charts = _app_charts_node(payload)
    if not target_refs or charts is None:
        return False

    changed = False
    for row in charts:
        if not isinstance(row, dict):
            continue
        instance_id = component_instance_id(row)
        if not instance_id or instance_id not in target_refs:
            continue
        if app_chart_target_ref(row) != instance_id:
            row[TARGET_REF_FIELD] = instance_id
            changed = True
    return changed


def strip_app_chart_target_refs(payload: dict[str, Any]) -> bool:
    """Remove derived app chart target refs from user-authored config payloads."""
    charts = _app_charts_node(payload)
    if charts is None:
        return False

    changed = False
    for row in charts:
        if isinstance(row, dict) and TARGET_REF_FIELD in row:
            row.pop(TARGET_REF_FIELD, None)
            changed = True
    return changed


def flux_targets_dir(paths: ProjectPaths) -> Path:
    return paths.flux_dir / "targets"


def flux_target_dir(paths: ProjectPaths, target_ref: str) -> Path:
    normalized = normalize_component_token(target_ref)
    if not normalized:
        raise ValueError("target_ref is required to resolve a target-scoped Flux directory")
    return flux_targets_dir(paths) / normalized


__all__ = [
    "DEPLOY_TARGET_INSTANCE_ID_FIELD",
    "TARGET_REF_FIELD",
    "app_chart_target_ref",
    "deploy_target_instance_id",
    "enabled_cluster_target_refs",
    "flux_target_dir",
    "flux_targets_dir",
    "is_auto_target_scoped_app_instance_id",
    "materialize_app_chart_target_refs",
    "normalize_generated_deploy_target",
    "strip_app_chart_target_refs",
    "target_scoped_app_instance_id",
]
