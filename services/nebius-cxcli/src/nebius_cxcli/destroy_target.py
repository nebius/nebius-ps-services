"""Project-bound immutable MK8s identity and teardown scope."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .destroy_generation import resources
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class DestroyTarget:
    target_ref: str
    cluster_id: str
    ownership: str
    module_names: tuple[str, ...]


def has_mk8s(config: Any, manifest: Mapping[str, Any] | None = None) -> bool:
    """Include disabled and partially registered targets; disabling is not retirement."""
    for value in (to_plain_data(config), manifest or {}):
        if not isinstance(value, Mapping):
            continue
        infra = value.get("infra") or {}
        if isinstance(infra, Mapping) and any(
            isinstance(row, Mapping) and component_type_id(row) == "mk8s"
            for row in infra.get("components", [])
        ):
            return True
        deploy = value.get("deploy") or {}
        if isinstance(deploy, Mapping) and any(
            isinstance(row, Mapping)
            and (
                row.get("component_id") in {"mk8s", "external-mk8s"}
                or row.get("kind") == "external-mk8s"
                or row.get("cluster_id")
                or "soperator_registration" in row
            )
            for row in deploy.get("targets", [])
        ):
            return True
        apps = value.get("apps") or {}
        if isinstance(apps, Mapping) and any(
            isinstance(row, Mapping) and row.get("id") == "soperator"
            for row in apps.get("charts", [])
        ):
            return True
    return False


def require_non_mk8s_destroy(
    config: Any,
    manifest: Mapping[str, Any] | None = None,
    state_values: Mapping[str, Any] | None = None,
) -> None:
    if has_mk8s(config, manifest) or any(
        row.get("type") in {"nebius_mk8s_v1_cluster", "nebius_mk8s_v1_node_group"}
        for row in resources(state_values or {}).values()
    ):
        raise ValueError(
            "MK8s deletion requires `nebius-cxcli destroy CONFIG --target CLUSTER_ID` "
            "with the immutable Nebius cluster ID, even for a single cluster."
        )


def check_retired_target_references(value: Any, target_ref: str) -> None:
    """Reject surviving component-output bindings to the retired target."""
    if isinstance(value, Mapping):
        for nested in value.values():
            check_retired_target_references(nested, target_ref)
    elif isinstance(value, list):
        for nested in value:
            check_retired_target_references(nested, target_ref)
    elif isinstance(value, str) and value.startswith(target_ref + "."):
        raise RuntimeError("Remaining configuration references a retired MK8s target output")


def resolve_destroy_target(
    *,
    cluster_id: str,
    source: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    module_sources: Sequence[Mapping[str, Any]],
    state_values: Mapping[str, Any],
) -> DestroyTarget:
    """Never derive destructive identity from context names or discovery reports."""
    if not cluster_id or cluster_id != cluster_id.strip():
        raise ValueError("--target requires an exact immutable Nebius cluster ID")
    source_targets = (source.get("deploy") or {}).get("targets", [])
    components = (source.get("infra") or {}).get("components", [])
    state = resources(state_values)
    outputs = state_values.get("outputs", {})
    bindings: dict[str, DestroyTarget] = {}
    seen_refs: set[str] = set()
    unresolved: list[str] = []
    for row in targets:
        if row.get("component_id") not in {"mk8s", "external-mk8s"}:
            continue
        ref = str(row["target_ref"])
        if ref in seen_refs:
            raise ValueError("Destroy has duplicate project target bindings")
        seen_refs.add(ref)
        modules = tuple(
            str(module["module_name"])
            for module in module_sources
            if module["component_id"] == "mk8s" and module["instance_id"] == ref
        )
        external = row.get("ownership") == "external"
        source_rows = [
            value
            for value in source_targets
            if isinstance(value, Mapping) and value.get("instance_id") == ref
        ]
        owned_rows = [
            value
            for value in components
            if isinstance(value, Mapping)
            and component_type_id(value) == "mk8s"
            and component_instance_id(value) == ref
        ]
        ids = {str(row.get("cluster_id") or "")}
        if external:
            if owned_rows or modules:
                raise ValueError(f"Destroy has conflicting managed/onboarded ownership for {ref}")
            if len(source_rows) != 1 or source_rows[0].get("kind") != "external-mk8s":
                raise ValueError(f"Destroy source and generated registration differ for {ref}")
            registered_id = str(source_rows[0].get("cluster_id") or "")
            if not registered_id or not row.get("cluster_id"):
                unresolved.append(ref)
                continue
            ids.add(registered_id)
        else:
            if any(
                value.get("kind") == "external-mk8s" or value.get("ownership") == "external"
                for value in source_rows
            ):
                raise ValueError(f"Destroy has conflicting managed/onboarded ownership for {ref}")
            if len(owned_rows) != 1 or len(modules) != 1 or len(source_rows) > 1:
                raise ValueError(f"Destroy managed source/module ownership is ambiguous for {ref}")
            managed_ids = {
                str(value["values"].get("id") or "")
                for address, value in state.items()
                if value.get("mode") == "managed"
                and value.get("type") == "nebius_mk8s_v1_cluster"
                and address.startswith(f"module.{modules[0]}.")
            }
            output = outputs.get(str(row.get("cluster_id_output_name") or ""), {})
            output_id = output.get("value") if isinstance(output, Mapping) else None
            if (
                len(managed_ids) != 1
                or "" in managed_ids
                or not isinstance(output_id, str)
                or not output_id
            ):
                unresolved.append(ref)
                continue
            ids.update(managed_ids)
            ids.add(output_id)
            ids.update(str(value.get("cluster_id") or "") for value in source_rows)
        ids.discard("")
        if len(ids) != 1:
            raise ValueError(f"Destroy has conflicting immutable cluster IDs for {ref}")
        identifier = ids.pop()
        if identifier in bindings:
            raise ValueError("Destroy has duplicate immutable cluster ID bindings")
        bindings[identifier] = DestroyTarget(
            ref, identifier, "onboarded" if external else "managed", modules
        )
    if cluster_id not in bindings:
        known = (
            ", ".join(f"{row.target_ref} -> {row.cluster_id}" for row in bindings.values())
            or "none"
        )
        missing = ", ".join(unresolved) or "none"
        raise ValueError(
            "--target must match one verified project-bound immutable Nebius cluster ID. "
            f"Known bindings: {known}. Unresolved targets: {missing}. "
            "Bind onboarded targets to an explicit cluster_id and regenerate the project bundle; "
            "managed targets require consistent Terraform state and cluster ID outputs."
        )
    return bindings[cluster_id]
