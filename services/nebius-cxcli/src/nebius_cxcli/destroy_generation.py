"""Frozen publication and narrowly scoped, normal-plan Terraform reconciliation."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .destroy import digest
from .project_bundle_transaction import ProjectBundleTransaction, normalize_project_bundle_target
from .render import project_generation_plan_fingerprints

SDK_TYPES = {
    "nebius_mk8s_v1_cluster": "cluster",
    "nebius_mk8s_v1_node_group": "node_group",
    "nebius_compute_v1_filesystem": "filesystem",
    "nebius_compute_v1_gpu_cluster": "gpu_cluster",
}
ANCILLARY_TYPES = {
    "nebius_iam_v1_service_account",
    "nebius_iam_v1_group",
    "nebius_iam_v1_group_membership",
    "nebius_iam_v1_access_permit",
}


def resources(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(module: Mapping[str, Any]) -> None:
        for resource in module.get("resources", []):
            address = resource["address"]
            if address in found:
                raise RuntimeError("Terraform state repeats a resource address")
            found[address] = resource
        for child in module.get("child_modules", []):
            walk(child)

    walk(values.get("root_module", {}))
    return found


def freeze_publication(plan: Any, project_dir: Path) -> dict[str, Any]:
    def relative(path: Path) -> str:
        return path.relative_to(project_dir).as_posix()

    return {
        "writes": {
            relative(path): base64.b64encode(value).decode("ascii")
            for path, value in plan.writes.items()
        },
        "removals": [relative(path) for path in plan.removals],
        "preimages": {relative(path): value for path, value in plan.expected_preimages.items()},
        "sha256": plan.sha256,
        "preimage_sha256": plan.preimage_sha256,
    }


def publication_paths(
    frozen: Mapping[str, Any], project_dir: Path
) -> tuple[dict[Path, bytes], tuple[Path, ...], dict[Path, str]]:
    def target(name: str) -> Path:
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise RuntimeError("Destroy publication contains an unsafe path")
        return normalize_project_bundle_target(project_dir, Path(name))

    writes = {
        target(name): base64.b64decode(value, validate=True)
        for name, value in frozen["writes"].items()
    }
    removals = tuple(target(name) for name in frozen["removals"])
    preimages = {target(name): value for name, value in frozen["preimages"].items()}
    if set(writes) & set(removals) or set(preimages) != set(writes) | set(removals):
        raise RuntimeError("Destroy publication targets are incomplete or overlap")
    fingerprints = project_generation_plan_fingerprints(
        project_dir=project_dir, writes=writes, removals=removals, expected_preimages=preimages
    )
    if fingerprints != (frozen["sha256"], frozen["preimage_sha256"]):
        raise RuntimeError("Destroy publication identity differs from approval")
    return writes, removals, preimages


def verify_publication(
    frozen: Mapping[str, Any],
    project_dir: Path,
    *,
    allow_postimage: bool = False,
    allow_missing_generated: bool = False,
) -> dict[Path, str]:
    writes, removals, preimages = publication_paths(frozen, project_dir)
    observed = {}
    for path, before in preimages.items():
        if path.is_symlink() or (
            path.exists() and (not path.is_file() or path.stat().st_nlink != 1)
        ):
            raise RuntimeError("Destroy publication found an unsafe local file")
        content = path.read_bytes() if path.exists() else None
        actual = (
            "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else "absent"
        )
        missing_cache = (
            allow_missing_generated
            and content is None
            and path.relative_to(project_dir).parts[0] == "generated"
        )
        postimage = allow_postimage and (
            (path in writes and content == writes[path]) or (path in removals and content is None)
        )
        if actual != before and not postimage and not missing_cache:
            raise RuntimeError(
                "Project files changed after destroy approval; restore the frozen generation before resuming"
            )
        observed[path] = actual
    return observed


def publish(frozen: Mapping[str, Any], project_dir: Path) -> None:
    writes, removals, _preimages = publication_paths(frozen, project_dir)
    verify_publication(frozen, project_dir, allow_postimage=True, allow_missing_generated=True)
    transaction = ProjectBundleTransaction(project_dir)
    transaction.recover_expected_generation(frozen["sha256"])
    observed = verify_publication(
        frozen, project_dir, allow_postimage=True, allow_missing_generated=True
    )
    if all(
        observed[path] == "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in writes.items()
    ) and all(observed[path] == "absent" for path in removals):
        return
    remaining_removals = tuple(path for path in removals if observed[path] != "absent")
    expected = {path: observed[path] for path in set(writes) | set(remaining_removals)}
    transaction.commit(
        writes,
        removals=remaining_removals,
        expected_preimages=expected,
        generation_sha256=frozen["sha256"],
    )


def _outputs(values: Mapping[str, Any]) -> dict[str, str]:
    # Store hashes, not potentially sensitive output values.
    return {name: digest(row.get("value")) for name, row in values.get("outputs", {}).items()}


def _managed(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        address: row for address, row in resources(values).items() if row.get("mode") == "managed"
    }


def _check_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("errored") or plan.get("complete") is False or plan.get("deferred_changes"):
        raise RuntimeError("Destroy requires a complete Terraform plan")
    if not isinstance(plan.get("planned_values"), Mapping) or not isinstance(
        plan.get("prior_state"), Mapping
    ):
        raise RuntimeError("Destroy Terraform plan is missing authoritative state")
    for name, change in plan.get("output_changes", {}).items():
        if change.get("after_unknown"):
            raise RuntimeError(f"Destroy cannot freeze an unknown Terraform output: {name}")


def _validate_refresh_drift(
    plan: Mapping[str, Any],
    *,
    updates: Mapping[str, Any],
    absent: Mapping[str, Any],
    phase: str,
) -> None:
    """Refresh observations cannot change the identity or scope of a deletion."""
    seen = set()
    for row in plan.get("resource_drift", []):
        change = row.get("change", {})
        if change.get("actions") == ["no-op"]:
            continue
        address = row.get("address", "")
        deleting = change.get("actions") == ["delete"]
        approved = (absent if deleting else updates).get(address)
        before, after = change.get("before"), change.get("after")
        unknown = change.get("after_unknown", {})
        known_id = unknown is False or (
            isinstance(unknown, Mapping) and not unknown.get("id", False)
        )
        if (
            not approved
            or address in seen
            or row.get("mode") != "managed"
            or row.get("type") != approved["type"]
            or row.get("deposed")
            or row.get("previous_address")
            or not isinstance(before, Mapping)
            or not isinstance(before.get("id"), str)
            or not before["id"]
            or before["id"] != approved["id"]
            or not known_id
            or (
                after is not None
                if deleting
                else change.get("actions") != ["update"]
                or not isinstance(after, Mapping)
                or after.get("id") != approved["id"]
            )
        ):
            # Resource values can contain certificates, endpoints and credentials.
            # Report only a bounded Terraform address, never before/after values.
            label = (
                address
                if isinstance(address, str)
                and len(address) <= 240
                and re.fullmatch(r'[a-zA-Z0-9_.\[\]"-]+', address)
                else "<unrecognized address>"
            )
            raise RuntimeError(
                f"Unrelated or identity-changing Terraform drift during destroy {phase}: {label}"
            )
        seen.add(address)


def freeze_terraform(
    plan: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any],
    delete_sfs: bool,
    module_names: Sequence[str],
    managed_cluster: bool,
    ancillary_addresses: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    _check_plan(plan)
    ids = {
        "cluster": {inventory["cluster_id"]},
        "gpu_cluster": set(inventory["gpu_cluster_ids"]),
        "node_group": set(inventory["node_group_ids"]),
        "filesystem": set(inventory["filesystem_ids"]) if delete_sfs else set(),
    }
    deleted: dict[str, Any] = {}
    for row in plan.get("resource_changes", []):
        if row.get("mode") != "managed" or row["change"]["actions"] == ["no-op"]:
            continue
        address, kind, change = row["address"], row["type"], row["change"]
        identifier = (change.get("before") or {}).get("id")
        if (
            change["actions"] != ["delete"]
            or not isinstance(identifier, str)
            or not identifier
            or address in deleted
            or row.get("deposed")
            or row.get("previous_address")
        ):
            raise RuntimeError(
                f"Destroy plan contains an unapproved create, update or replacement: {address}"
            )
        sdk_kind = SDK_TYPES.get(kind)
        if sdk_kind:
            if identifier not in ids[sdk_kind]:
                raise RuntimeError(f"Destroy plan deletes an unapproved cloud identity: {address}")
        else:
            module_owned = any(address.startswith(f"module.{module}.") for module in module_names)
            root_owned = address in ancillary_addresses
            if kind not in ANCILLARY_TYPES or not (module_owned or root_owned):
                raise RuntimeError(f"Destroy plan deletes an unrelated managed resource: {address}")
        deleted[address] = {"id": identifier, "type": kind, "sdk": bool(sdk_kind)}
    _validate_refresh_drift(plan, updates=deleted, absent={}, phase="preview")
    if managed_cluster and not any(
        row["type"] == "nebius_mk8s_v1_cluster" for row in deleted.values()
    ):
        raise RuntimeError("The selected managed cluster is absent from the cleanup plan")
    remaining = _managed(plan["planned_values"])
    return {
        "deletes": deleted,
        "remaining": {address: digest(row["values"]) for address, row in remaining.items()},
        "outputs": _outputs(plan["planned_values"]),
    }


def validate_reconciliation(plan: Mapping[str, Any], frozen: Mapping[str, Any]) -> None:
    _check_plan(plan)
    allowed = frozen["deletes"]
    pending = {}
    for row in plan.get("resource_changes", []):
        if row.get("mode") != "managed" or row["change"]["actions"] == ["no-op"]:
            continue
        approved = allowed.get(row["address"])
        change = row["change"]
        if (
            not approved
            or approved["sdk"]
            or change["actions"] != ["delete"]
            or (change.get("before") or {}).get("id") != approved["id"]
            or row["type"] != approved["type"]
            or row["address"] in pending
            or row.get("deposed")
            or row.get("previous_address")
        ):
            raise RuntimeError(
                "Terraform reconciliation exceeds the frozen ancillary deletion scope"
            )
        pending[row["address"]] = approved
    _validate_refresh_drift(plan, updates=pending, absent=allowed, phase="reconciliation")
    validate_final_state(plan["planned_values"], frozen)


def validate_final_state(values: Mapping[str, Any], frozen: Mapping[str, Any]) -> None:
    actual = {address: digest(row["values"]) for address, row in _managed(values).items()}
    if actual != frozen["remaining"] or _outputs(values) != frozen["outputs"]:
        raise RuntimeError(
            "Terraform remaining resources or outputs differ from the frozen final generation"
        )


def remove_sfs_entries(
    payload: Mapping[str, Any],
    *,
    filesystem_ids: set[str],
    module_sources: Sequence[Mapping[str, Any]],
    state_values: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove exact state-bound entries; an empty map must never select default mode."""
    result = copy.deepcopy(dict(payload))
    modules = {
        row["module_name"]: row["instance_id"]
        for row in module_sources
        if row.get("component_id") == "sfs"
    }
    selected: dict[str, set[str]] = {}
    for address, row in resources(state_values).items():
        if (
            row.get("type") != "nebius_compute_v1_filesystem"
            or row.get("values", {}).get("id") not in filesystem_ids
        ):
            continue
        match = re.fullmatch(
            r'module\.([A-Za-z0-9_]+)\.(?:data\.)?nebius_compute_v1_filesystem\.(?:this|existing)\["([^"\\]+)"\]',
            address,
        )
        if not match or match[1] not in modules:
            raise RuntimeError("Attached SFS cannot be mapped to an exact configured entry")
        selected.setdefault(modules[match[1]], set()).add(match[2])
    components = result.get("infra", {}).get("components", [])
    kept = []
    for component in components:
        identity = component_instance_id(component)
        if component_type_id(component) != "sfs":
            kept.append(component)
            continue
        inputs = component.get("inputs", {})
        entries = inputs.get("filesystems")
        keys = selected.get(identity, set())
        if entries:
            # Explicit imported IDs remain resolvable without an existing state data row.
            keys |= {
                key for key, value in entries.items() if value.get("existing_id") in filesystem_ids
            }
            if keys:
                selected[identity] = keys
            remaining = {key: value for key, value in entries.items() if key not in keys}
            if not keys:
                kept.append(component)
            elif remaining:
                inputs["filesystems"] = remaining
                kept.append(component)
        elif "default" not in keys and inputs.get("existing_id") not in filesystem_ids:
            kept.append(component)
        else:
            selected.setdefault(identity, set()).add("default")
    result.setdefault("infra", {})["components"] = kept
    # A direct ID or reference remaining anywhere outside removed SFS entries
    # means the same storage still has a configured consumer.
    encoded = json.dumps(result, sort_keys=True)
    if any(identifier in encoded for identifier in filesystem_ids):
        raise RuntimeError("Attached SFS is still referenced by remaining configuration")
    sfs_instances = {
        component_instance_id(row) for row in components if component_type_id(row) == "sfs"
    }

    def check_references(value: Any) -> None:
        if isinstance(value, Mapping):
            instance = str(value.get("source_instance") or "")
            if value.get("source_component") == "sfs" and not instance:
                if len(sfs_instances) != 1:
                    raise RuntimeError("Remaining SFS reference has ambiguous ownership")
                instance = next(iter(sfs_instances))
            if instance in selected and value.get("source_component") in (None, "sfs"):
                keys = value.get("keys")
                keys = (
                    set(keys)
                    if isinstance(keys, list)
                    else {value["key"]}
                    if value.get("key")
                    else None
                )
                if keys is None or keys & selected[instance]:
                    raise RuntimeError("Remaining configuration references a deleted SFS entry")
            for nested in value.values():
                check_references(nested)
        elif isinstance(value, list):
            for nested in value:
                check_references(nested)
        elif isinstance(value, str) and any(
            value.startswith(instance + ".") for instance in selected
        ):
            raise RuntimeError("Remaining configuration references a changed SFS component output")

    check_references(result)
    return result


def vm_nfs_config(
    source: Mapping[str, Any],
    chart_values: Mapping[str, Any],
    *,
    target_ref: str,
    state_values: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve the same managed NFS output binding used by render, without Kubernetes."""
    from .component_sources import component_output_root_name
    from .nfs_csi import nfs_instance_id_for_target

    config = copy.deepcopy(dict(chart_values.get("externalNfs") or {}))
    instance = nfs_instance_id_for_target(source, target_ref=target_ref)
    if instance and config.get("enabled") is not False:
        outputs = state_values.get("outputs", {})
        config["enabled"] = True
        for field, output in (("server", "server_ip"), ("path", "export_path")):
            if not config.get(field):
                config[field] = outputs.get(component_output_root_name(instance, output), {}).get(
                    "value"
                )
    if config.get("enabled") and not config.get("server"):
        raise RuntimeError(
            "VM-NFS cloud identity requires its configured or state-bound server address"
        )
    return config
