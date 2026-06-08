"""Shared helpers for OS-image upgrade planning across supported infra components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component_instances import component_instance_id, component_type_id

VM_COMPONENT_ID = "vm"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enabled_infra_components(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    infra = _mapping(payload.get("infra"))
    components = infra.get("components")
    if not isinstance(components, list):
        raise ValueError("config.yaml does not contain infra.components[].")
    return tuple(
        row
        for row in components
        if isinstance(row, dict) and row.get("enabled") is not False
    )


def find_source_vm_component(payload: Mapping[str, Any], instance_id: str) -> dict[str, Any]:
    """Return one enabled generic VM component from config.yaml."""

    normalized_instance_id = _text(instance_id).lower()
    for row in _enabled_infra_components(payload):
        if component_type_id(row) != VM_COMPONENT_ID:
            continue
        if component_instance_id(row) == normalized_instance_id:
            return row
    raise ValueError(f"Could not find enabled infra:vm@{normalized_instance_id} in config.yaml.")


def source_vm_inputs(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(row.get("inputs"))


def source_vm_name(row: Mapping[str, Any], *, fallback: str) -> str:
    return _text(source_vm_inputs(row).get("name")) or fallback


def source_vm_platform(row: Mapping[str, Any]) -> str:
    return _text(source_vm_inputs(row).get("platform"))


def source_vm_image_family(row: Mapping[str, Any]) -> str:
    return _text(source_vm_inputs(row).get("source_image_family"))


def source_vm_uses_managed_image_family(row: Mapping[str, Any]) -> bool:
    inputs = source_vm_inputs(row)
    return not _text(inputs.get("source_image_id")) and not _text(
        inputs.get("boot_disk_existing_id")
    )


@dataclass(frozen=True)
class VmOsImageUpgradePlan:
    """Concrete generic VM source-image-family upgrade plan."""

    selector: str
    instance_id: str
    name: str
    current_image_family: str
    target_image_family: str
    warnings: tuple[str, ...] = ()

    @property
    def mutates(self) -> bool:
        return self.current_image_family != self.target_image_family


def plan_vm_os_image_upgrade(
    *,
    selector: str,
    source_component: Mapping[str, Any],
    target_image_family: str,
) -> VmOsImageUpgradePlan:
    """Build a source-desired-state plan for a generic VM boot image family."""

    if not source_vm_uses_managed_image_family(source_component):
        raise ValueError(
            "Generic VM OS-image upgrades require a module-managed boot disk that uses "
            "inputs.source_image_family. Components with source_image_id or "
            "boot_disk_existing_id must be updated outside `upgrade os-image`."
        )
    target = _text(target_image_family)
    if not target:
        raise ValueError("--to-os must not be empty.")
    if any(char.isspace() for char in target):
        raise ValueError("--to-os must not contain whitespace.")
    instance_id = component_instance_id(source_component)
    current = source_vm_image_family(source_component)
    if not current:
        raise ValueError(
            f"infra:vm@{instance_id}.inputs.source_image_family is required for "
            "VM OS-image upgrades."
        )
    return VmOsImageUpgradePlan(
        selector=selector,
        instance_id=instance_id,
        name=source_vm_name(source_component, fallback=instance_id),
        current_image_family=current,
        target_image_family=target,
        warnings=(
            "VM OS-image upgrades change inputs.source_image_family and let Terraform "
            "replace the module-managed boot disk/instance as required by the provider. "
            "cxcli does not SSH to the VM or run package-manager upgrades.",
        ),
    )


def update_source_vm_image_family(
    payload: dict[str, Any],
    *,
    instance_id: str,
    target_image_family: str,
) -> bool:
    """Update one generic VM source_image_family in config.yaml."""

    row = find_source_vm_component(payload, instance_id)
    if not source_vm_uses_managed_image_family(row):
        raise ValueError(
            f"infra:vm@{instance_id} does not use inputs.source_image_family."
        )
    inputs = row.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError(f"infra:vm@{instance_id}.inputs must be a mapping.")
    target = _text(target_image_family)
    if not target:
        raise ValueError("--to-os must not be empty.")
    if inputs.get("source_image_family") == target:
        return False
    inputs["source_image_family"] = target
    return True


def format_vm_os_image_upgrade_plan(
    plan: VmOsImageUpgradePlan,
    *,
    dry_run: bool,
    repeat_dry_run_command: str | None = None,
) -> tuple[str, ...]:
    """Render VM OS-image upgrade plan lines for the CLI."""

    lines = [
        "VM OS image upgrade plan",
        f"- target: {plan.selector}",
        f"- VM: {plan.name}",
        f"- source image family: {plan.current_image_family} -> {plan.target_image_family}",
    ]
    if plan.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    if dry_run:
        if repeat_dry_run_command:
            lines.append("- repeat dry-run command:")
            lines.append(f"  {repeat_dry_run_command}")
        lines.append(
            "Dry run only: no config.yaml write, generated bundle render, "
            "or Terraform plan/apply was performed."
        )
    return tuple(lines)
