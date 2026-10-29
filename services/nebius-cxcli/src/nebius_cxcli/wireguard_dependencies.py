"""Explicit WireGuard access-infrastructure ownership and wizard materialization."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .runtime_config import to_plain_data

ACCESS_ROLES = ("login", "host", "connection")
ACCESS_OWNERSHIP = "terraform-access-v1"


def access_owner_rows(config: Any) -> list[dict[str, Any]]:
    """Retained owners keep IAM renderable without selecting a VM or its network."""
    return [
        row
        for row in to_plain_data(config).get("infra", {}).get("components", [])
        if component_type_id(row) == "wireguard-gw"
        and (row.get("enabled", False) or row.get("access", {}).get("lifecycle") == "retained")
    ]


def metadata_only(row: Mapping[str, Any]) -> bool:
    return (
        component_type_id(row) == "mysterybox"
        and row.get("inputs", {}).get("create_initial_versions") is False
    )


def dependency_instance(row: Mapping[str, Any]) -> str:
    reference = row.get("access", {}).get("secretstash_ref", "")
    if not isinstance(reference, str) or not reference.startswith("mysterybox@"):
        raise ValueError("WireGuard requires access.secretstash_ref=mysterybox@<instance>")
    instance = reference.removeprefix("mysterybox@")
    if not instance or "@" in instance:
        raise ValueError("WireGuard SecretStash reference must identify exactly one instance")
    return instance


def secret_names(instance: str) -> dict[str, str]:
    return {role: f"{instance}-{role}" for role in ACCESS_ROLES}


def access_dependencies(config: Any) -> dict[str, dict[str, Any]]:
    """Validate source intent without inferring dependencies or adopting resources."""
    payload = to_plain_data(config)
    rows = payload.get("infra", {}).get("components", [])
    project = payload.get("client_info", {}).get("nebius", {}).get("project_id", "")
    result: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for owner in access_owner_rows(payload):
        instance = dependency_instance(owner)
        if instance in used:
            raise ValueError("WireGuard gateways cannot share a SecretStash dependency")
        used.add(instance)
        matches = [row for row in rows if component_instance_id(row) == instance]
        if len(matches) != 1 or not matches[0].get("enabled") or not metadata_only(matches[0]):
            raise ValueError(
                f"WireGuard dependency mysterybox@{instance} must be enabled and metadata-only"
            )
        dependency = matches[0]
        inputs = dependency.get("inputs", {})
        owner_project = owner.get("inputs", {}).get("parent_id") or project
        if not project or inputs.get("parent_id") != project or owner_project != project:
            raise ValueError("WireGuard and its SecretStash dependency must use the project scope")
        names = [item.get("name") for item in inputs.get("secrets", []) if isinstance(item, dict)]
        if len(names) != 3 or set(names) != set(secret_names(instance).values()):
            raise ValueError(
                "WireGuard SecretStash dependency requires its three stable secret names"
            )
        if "payload_values" in inputs or any(
            set(item) - {"name", "description", "labels"} for item in inputs["secrets"]
        ):
            raise ValueError(
                "WireGuard secret declarations contain metadata only; remove payload/version inputs"
            )
        access = owner.get("access", {})
        if not access.get("admin_group_id"):
            raise ValueError("WireGuard access owners require an exact administrator group")
        if access.get("lifecycle") == "retained" and (
            owner.get("enabled")
            or not access.get("generation")
            or not access.get("connection_secret_id")
        ):
            raise ValueError(
                "Retained WireGuard owners must be disabled with complete generation references"
            )
        result[component_instance_id(owner)] = dependency
    return result


def materialize_wireguard_dependencies(
    payload: dict[str, Any], *, selected_instances: set[str] | None = None
) -> list[str]:
    """Only creation wizards call this; loaders/renderers never repair missing intent."""
    rows = payload.get("infra", {}).get("components", [])
    added: list[str] = []
    for row in list(rows):
        if component_type_id(row) != "wireguard-gw" or not row.get("enabled"):
            continue
        owner = component_instance_id(row)
        if (
            selected_instances is not None
            and owner not in selected_instances
            and "wireguard-gw" not in selected_instances
        ):
            continue
        access = row.setdefault("access", {})
        if access.get("secretstash_ref"):
            continue
        if access.get("generation") or access.get("connection_secret_id"):
            raise ValueError(
                "Existing SDK-owned WireGuard deployments require a separate migration; no automatic adoption"
            )
        # Keep valid gateway identities valid after adding the dependency suffix.
        stem = owner
        if len(stem) > 56:
            stem = f"{owner[:43].rstrip('-')}-{hashlib.sha256(owner.encode()).hexdigest()[:12]}"
        instance = f"{stem}-access"
        if any(component_instance_id(other) == instance for other in rows):
            raise ValueError(
                f"WireGuard dependency instance '{instance}' already exists; choose a distinct gateway identity"
            )
        project = row.get("inputs", {}).get("parent_id") or payload.get("client_info", {}).get(
            "nebius", {}
        ).get("project_id")
        rows.append(
            {
                "id": "mysterybox",
                "instance_id": instance,
                "enabled": True,
                "inputs": {
                    "parent_id": project,
                    "create_initial_versions": False,
                    "secrets": [{"name": name} for name in secret_names(instance).values()],
                },
            }
        )
        access["secretstash_ref"] = f"mysterybox@{instance}"
        added.append(instance)
    return added


def assert_access_removal_allowed(payload: Mapping[str, Any], removed: set[str]) -> None:
    for row in access_owner_rows(payload):
        owner = component_instance_id(row)
        dependency = dependency_instance(row)
        if dependency in removed and owner not in removed:
            raise ValueError(
                "Cannot remove the SecretStash dependency while its WireGuard owner remains"
            )
        if removed & {owner, dependency} and row.get("access", {}).get("generation"):
            raise ValueError(
                "WireGuard access resources are retained; preserve their owner and dependency declarations"
            )


def skipped_new_dependencies(
    payload: Mapping[str, Any], skipped_owners: set[str], added_dependencies: set[str]
) -> set[str]:
    """Discard only fresh wizard-owned dependencies alongside their skipped owners."""
    rows = payload.get("infra", {}).get("components", [])
    removed: set[str] = set()
    for row in rows:
        if (
            component_type_id(row) != "wireguard-gw"
            or component_instance_id(row) not in skipped_owners
        ):
            continue
        access = row.get("access", {})
        if (
            access.get("generation")
            or access.get("connection_secret_id")
            or access.get("lifecycle")
        ):
            continue
        try:
            instance = dependency_instance(row)
        except ValueError:
            continue
        matches = [item for item in rows if component_instance_id(item) == instance]
        owners = [
            item
            for item in rows
            if component_type_id(item) == "wireguard-gw"
            and item.get("access", {}).get("secretstash_ref") == f"mysterybox@{instance}"
        ]
        if (
            instance in added_dependencies
            and len(matches) == 1
            and metadata_only(matches[0])
            and len(owners) == 1
        ):
            removed.add(instance)
    return removed


def assert_access_owner_continuity(previous: Any, current: Any) -> None:
    """A rendered access owner cannot disappear from the tracked Terraform root."""
    current_owners = {component_instance_id(row): row for row in access_owner_rows(current)}
    for row in access_owner_rows(previous):
        instance = component_instance_id(row)
        next_row = current_owners.get(instance)
        if next_row is None or dependency_instance(next_row) != dependency_instance(row):
            raise ValueError(
                "Rendered WireGuard access ownership cannot be removed or rebound; preserve its owner and dependency declarations"
            )
        if row.get("access", {}).get("lifecycle") == "retained" and next_row.get(
            "access"
        ) != row.get("access"):
            raise ValueError(
                "Retained WireGuard ownership is immutable; use a new instance for a fresh gateway"
            )


def assert_rendered_access_continuity(
    final_paths: Any, staged_paths: Any, current: Any = None
) -> None:
    import json

    from .generated_manifest import manifest_path_for_generated_dir

    old_path = manifest_path_for_generated_dir(final_paths.generated_dir)
    new_path = manifest_path_for_generated_dir(staged_paths.generated_dir)
    if not old_path.exists():
        return
    previous = json.loads(old_path.read_text()).get("runtime_config", {})
    if not access_owner_rows(previous):
        return
    if current is None:
        if not new_path.exists():
            raise ValueError("Rendered WireGuard ownership requires a complete generated manifest")
        current = json.loads(new_path.read_text()).get("runtime_config", {})
    assert_access_owner_continuity(previous, current)
