"""Forward-only recovery authority for a failed infrastructure install."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .component_sources import SourceProfile
from .generated_manifest import manifest_path_for_generated_dir
from .infra_render import render_terraform_artifacts, rendered_module_sources
from .paths import ProjectPaths
from .project_bundle_transaction import ProjectBundleTransaction
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json


def refresh_install_terraform_root(
    config: Any, paths: ProjectPaths, manifest: Mapping[str, Any]
) -> None:
    """Refresh root wiring without replacing frozen inputs, state, plans, or Flux."""
    render = manifest["render"]
    profile = SourceProfile(render["source_profile"])
    modules = [asdict(item) for item in rendered_module_sources(config, source_profile=profile)]
    if modules != render["module_sources"]:
        raise RuntimeError("Install recovery cannot change frozen Terraform module sources")
    names = (
        "backend.tf",
        "versions.tf",
        "providers.tf",
        "variables.tf",
        "main.tf",
        "outputs.tf",
        "terraform.auto.tfvars.json",
    )
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    transaction = ProjectBundleTransaction(paths.project_dir)
    snapshots = transaction.snapshot_preimages(
        (paths.config_path, manifest_path, *(paths.infra_dir / name for name in names))
    )
    with TemporaryDirectory(prefix="cxcli-install-render-") as temporary:
        staged = replace(paths, infra_dir=Path(temporary).resolve())
        written = render_terraform_artifacts(config, staged, source_profile=profile)
        if {path.name for path in written} != set(names):
            raise RuntimeError("Install recovery renderer changed its root file inventory")
        if (
            json.loads((staged.infra_dir / "terraform.auto.tfvars.json").read_text())
            != render["terraform_tfvars"]
        ):
            raise RuntimeError("Install recovery cannot change frozen Terraform inputs")
        for name in names:
            if (
                name != "main.tf"
                and snapshots[paths.infra_dir / name].content
                != (staged.infra_dir / name).read_bytes()
            ):
                raise RuntimeError(f"Install recovery cannot change frozen root artifact: {name}")
        main = paths.infra_dir / "main.tf"
        content = (staged.infra_dir / "main.tf").read_bytes()
        if snapshots[main].content == content:
            return
        updates = {main: content}
        for path in (
            paths.config_path,
            manifest_path,
            *(paths.infra_dir / name for name in names if name != "main.tf"),
        ):
            original = snapshots[path].content
            if original is None:
                raise RuntimeError("Install recovery requires frozen config and manifest files")
            updates[path] = original
        transaction.commit(
            updates,
            expected_preimages={path: snapshots[path].sha256 for path in updates},
        )


def failed_infrastructure_receipt(receipt: Mapping[str, Any]) -> bool:
    return (
        receipt.get("status") == "failed"
        and all(
            isinstance(receipt.get(key), str) and receipt[key]
            for key in ("startedAt", "failedAt", "failureType")
        )
        and "infraCompleteAt" not in receipt
        and "completedAt" not in receipt
    )


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _managed_changes(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise RuntimeError("Recovery plan has no resource inventory")
    inventory: dict[str, Mapping[str, Any]] = {}
    for item in changes:
        if not isinstance(item, Mapping):
            raise RuntimeError("Recovery plan has malformed resource inventory")
        if item.get("mode") == "data":
            continue
        address = item.get("address")
        change = item.get("change")
        if not isinstance(address, str) or not address or not isinstance(change, Mapping):
            raise RuntimeError("Recovery plan has malformed resource inventory")
        if address in inventory:
            raise RuntimeError("Recovery plan contains duplicate resource addresses")
        inventory[address] = change
    if not inventory:
        raise RuntimeError("Recovery plan has no managed resource inventory")
    return inventory


def recovery_provenance(
    previous_receipt: Mapping[str, Any],
    previous_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    *,
    allowed_new_access_permits: frozenset[str] = frozenset(),
    provider_schema: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Bind recovery to the original address closure and observed partial identities."""
    if not failed_infrastructure_receipt(previous_receipt):
        raise RuntimeError("Recovery requires a failed infrastructure receipt")
    identities_digest = validate_recovery_plan(
        previous_plan,
        candidate_plan,
        allowed_new_access_permits=allowed_new_access_permits,
        provider_schema=provider_schema,
    )
    return {
        "failedReceiptSha256": _digest(previous_receipt),
        "priorApprovalFingerprint": str(previous_receipt["approvalFingerprint"]),
        "priorTerraformPlanSha256": str(previous_receipt["inputs"]["terraformPlanSha256"]),
        "resourceIdentitiesSha256": identities_digest,
    }


def validate_recovery_plan(
    previous_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    *,
    allowed_new_access_permits: frozenset[str] = frozenset(),
    provider_schema: Mapping[str, Any] | None = None,
) -> str:
    """Retain infrastructure; allow only uncreated grants to receive corrected wiring."""
    original = _managed_changes(previous_plan)
    candidate = _managed_changes(candidate_plan)
    schemas = _resource_schemas(candidate_plan, provider_schema)
    removed = original.keys() - candidate.keys()
    added = candidate.keys() - original.keys()
    permit_prefix = "nebius_iam_v1_access_permit."
    if (
        (removed and not added)
        or any(
            not address.startswith(permit_prefix)
            or original[address].get("before") is not None
            or original[address].get("actions") != ["create"]
            for address in removed
        )
        or not added <= allowed_new_access_permits
    ):
        raise RuntimeError("Recovery plan changes the original managed resource address inventory")
    original_scopes = {
        change["after"].get("resource_id")
        for address, change in original.items()
        if address.startswith(permit_prefix) and isinstance(change.get("after"), Mapping)
    } - {None, ""}
    retained_groups = {
        change["before"].get("id")
        for address, change in candidate.items()
        if address in original
        and address.startswith("nebius_iam_v1_group.")
        and isinstance(change.get("before"), Mapping)
    } - {None, ""}
    identities: dict[str, str] = {}
    for address, change in candidate.items():
        actions = change.get("actions")
        if actions not in (["create"], ["update"], ["no-op"]):
            raise RuntimeError(f"Recovery plan has a destructive or unsupported action: {address}")
        before = change.get("before")
        after = change.get("after")
        previous_before = original.get(address, {}).get("before")
        prior_id = previous_before.get("id") if isinstance(previous_before, Mapping) else None
        current_id = before.get("id") if isinstance(before, Mapping) else None
        after_id = after.get("id") if isinstance(after, Mapping) else None
        if prior_id and prior_id != current_id:
            raise RuntimeError(
                f"Recovery plan changes a previously known resource identity: {address}"
            )
        if current_id:
            if not isinstance(current_id, str) or after_id != current_id:
                raise RuntimeError(
                    f"Recovery plan does not preserve a partial resource identity: {address}"
                )
            identities[address] = current_id
        elif actions != ["create"]:
            raise RuntimeError(
                f"Recovery plan cannot prove an existing resource identity: {address}"
            )
        if address in added:
            if (
                not address.startswith(permit_prefix)
                or actions != ["create"]
                or before is not None
                or not isinstance(after, Mapping)
                or after.get("parent_id") not in retained_groups
                or after.get("resource_id") not in original_scopes
            ):
                raise RuntimeError(
                    "Recovery grant is outside the original project and retained groups"
                )
        elif not _known_values_match(
            _desired_values(original[address].get("after"), schemas.get(address, {})),
            _desired_values(after, schemas.get(address, {})),
            _desired_values(original[address].get("after_unknown", {}), schemas.get(address, {})),
        ):
            raise RuntimeError(f"Recovery changes previously approved resource settings: {address}")
    return _digest(identities)


def _resource_schemas(
    plan: Mapping[str, Any], provider_schema: Mapping[str, Any] | None
) -> dict[str, Mapping[str, Any]]:
    if provider_schema is None:
        return {}  # Without a schema, conservatively compare every known field.
    schemas = {}
    for row in plan["resource_changes"]:
        if row.get("mode") == "data":
            continue
        try:
            block = provider_schema["provider_schemas"][row["provider_name"]]["resource_schemas"][
                row["type"]
            ]["block"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Recovery requires a schema for every managed resource") from exc
        if not isinstance(block, Mapping):
            raise RuntimeError("Recovery encountered a malformed resource schema")
        schemas[row["address"]] = block
    return schemas


def _desired_values(value: Any, block: Mapping[str, Any]) -> Any:
    """Exclude only provider-computed fields; optional/computed inputs stay fenced."""
    if isinstance(value, list):
        return [_desired_values(item, block) for item in value]
    if not isinstance(value, Mapping):
        return value
    attributes = block.get("attributes", {})
    blocks = block.get("block_types", {})
    result = {}
    for key, item in value.items():
        attribute = attributes.get(key, {})
        if (
            attribute.get("computed") is True
            and not attribute.get("optional")
            and not attribute.get("required")
        ):
            continue
        nested = attribute.get("nested_type", blocks.get(key, {}).get("block", {}))
        mode = attribute.get("nested_type", blocks.get(key, {})).get("nesting_mode")
        if mode == "map" and isinstance(item, Mapping):
            result[key] = {name: _desired_values(entry, nested) for name, entry in item.items()}
        else:
            result[key] = _desired_values(item, nested)
    return result


def _known_values_match(previous: Any, candidate: Any, unknown: Any) -> bool:
    if unknown is True:
        return True
    if isinstance(previous, Mapping):
        return (
            isinstance(candidate, Mapping)
            and candidate.keys()
            <= (previous.keys() | (unknown.keys() if isinstance(unknown, Mapping) else set()))
            and all(
                _known_values_match(
                    value,
                    candidate.get(key),
                    unknown.get(key, {}) if isinstance(unknown, Mapping) else {},
                )
                for key, value in previous.items()
            )
        )
    if isinstance(previous, list):
        return (
            isinstance(candidate, list)
            and len(previous) == len(candidate)
            and all(
                _known_values_match(
                    value,
                    candidate[index],
                    unknown[index] if isinstance(unknown, list) and index < len(unknown) else {},
                )
                for index, value in enumerate(previous)
            )
        )
    return previous == candidate


def _archive_path(reports_dir: Path, digest: str) -> Path:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Invalid install recovery receipt digest")
    return reports_dir / "soperator-install-history" / (digest.removeprefix("sha256:") + ".json")


def archive_failed_receipt(reports_dir: Path, receipt: Mapping[str, Any]) -> None:
    """Keep content-free failed receipts; never copy secret-bearing Terraform plans."""
    path = _archive_path(reports_dir, _digest(receipt))
    if path.exists() or path.is_symlink():
        saved = read_owner_only_json(path, label="Failed Soperator install receipt")
        if saved != dict(receipt):
            raise RuntimeError("Failed install receipt archive changed")
        return
    write_owner_only_json(path, receipt)


def validate_recovery_archive(reports_dir: Path, recovery: Any) -> dict[str, str]:
    keys = {
        "failedReceiptSha256",
        "priorApprovalFingerprint",
        "priorTerraformPlanSha256",
        "resourceIdentitiesSha256",
    }
    if (
        not isinstance(recovery, dict)
        or recovery.keys() != keys
        or any(
            not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
            for value in recovery.values()
        )
    ):
        raise RuntimeError("Invalid install recovery provenance")
    receipt = read_owner_only_json(
        _archive_path(reports_dir, recovery["failedReceiptSha256"]),
        label="Failed Soperator install receipt",
    )
    if (
        not isinstance(receipt, Mapping)
        or not failed_infrastructure_receipt(receipt)
        or _digest(receipt) != recovery["failedReceiptSha256"]
        or receipt.get("approvalFingerprint") != recovery["priorApprovalFingerprint"]
        or not isinstance(receipt.get("inputs"), Mapping)
        or receipt["inputs"].get("terraformPlanSha256") != recovery["priorTerraformPlanSha256"]
    ):
        raise RuntimeError("Install recovery archive no longer matches its approval")
    return dict(recovery)
