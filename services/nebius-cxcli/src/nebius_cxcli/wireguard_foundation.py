"""Terraform-owned public access resources under the gateway lifecycle locks."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id
from .component_sources import component_output_root_name
from .terraform_roots import access_backend
from .wireguard_dependencies import ACCESS_OWNERSHIP, dependency_instance, secret_names


def ownership(settings: Any, row: dict[str, Any]) -> dict[str, str]:
    return {
        "contract": ACCESS_OWNERSHIP,
        "bucket": settings.bucket,
        "main_state_key": settings.key,
        "access_state_key": access_backend(settings).key,
        "dependency": dependency_instance(row),
    }


def assert_ownership(record: dict[str, Any], settings: Any, row: dict[str, Any]) -> None:
    if record.get("ownership") != ownership(settings, row):
        raise RuntimeError(
            "WireGuard access ownership differs from the Terraform contract; "
            "SDK-owned deployments require a separate migration, never automatic adoption"
        )


def _authority(owner: Any) -> None:
    owner.executor.lease.assert_held()
    for journal in owner.journals.values():
        journal.assert_held()


def assert_fresh_access_state(owner: Any, row: dict[str, Any]) -> None:
    executor = owner.executor
    dependency = dependency_instance(row)
    modules = {
        item["module_name"]
        for item in executor.manifest.get("render", {}).get("module_sources", [])
        if item.get("root") == "infra-access" and item.get("instance_id") == dependency
    }
    state = executor.cli.terraform_show_json(
        executor.paths.access_infra_dir, extra_env=executor.runtime_env, initialize=False
    )
    pending = [state.get("values", {}).get("root_module", {})]
    while pending:
        module = pending.pop()
        pending.extend(module.get("child_modules", []))
        for resource in module.get("resources", []):
            if any(
                str(resource.get("address", "")).startswith(f"module.{name}.") for name in modules
            ):
                raise RuntimeError(
                    "Existing access infrastructure has no ownership journal; refusing a new generation or automatic adoption"
                )
    output = (
        state.get("values", {})
        .get("outputs", {})
        .get(component_output_root_name(component_instance_id(row), "access_resources"))
    )
    if output:
        raise RuntimeError(
            "Existing access infrastructure has no ownership journal; refusing automatic adoption"
        )


def admit_access_plan(owner: Any, plan: dict[str, Any]) -> None:
    """Reject deletes, rebinding and unexpected resources before any provider write."""
    sources = owner.executor.manifest.get("render", {}).get("module_sources", [])
    allowed_modules = {
        source["module_name"] for source in sources if source.get("root") == "infra-access"
    }
    allowed_types = {
        "nebius_mysterybox_v1_secret",
        "nebius_iam_v1_service_account",
        "nebius_iam_v1_group",
        "nebius_iam_v1_group_membership",
        "nebius_iam_v1_access_permit",
    }
    for change in plan.get("resource_changes", []):
        if change.get("mode") == "data":
            continue
        module = str(change.get("address", "")).split(".")
        if (
            len(module) < 3
            or module[0] != "module"
            or module[1] not in allowed_modules
            or change.get("type") not in allowed_types
        ):
            raise RuntimeError("Access plan contains a resource outside its declared ownership")
        if "delete" in change.get("change", {}).get("actions", []):
            raise RuntimeError(
                "Access infrastructure deletion/replacement is forbidden; retain its declared ownership"
            )
    planned = plan.get("planned_values", {}).get("outputs", {})
    for row in owner.owners:
        instance = component_instance_id(row)
        expected = {
            key: item["id"]
            for key, item in owner.journals[instance].value.get("resources", {}).items()
        }
        if expected:
            result = planned.get(component_output_root_name(instance, "access_resources"), {}).get(
                "value"
            )
            if result != expected:
                raise RuntimeError(
                    "Access plan would rebind an owned generation; never recreate or replace credentials"
                )


def reconcile_access_root(owner: Any, *, verify_only: bool = False) -> None:
    """Apply one saved, admitted foundation plan; never target the main root."""
    executor = owner.executor
    root = executor.paths.access_infra_dir
    if not (root / "main.tf").is_file():
        raise RuntimeError("Missing WireGuard access Terraform root; run full render")
    _authority(owner)
    executor.cli.progress_console.print(
        "Deploy: Verify access infrastructure"
        if verify_only
        else "Deploy: SecretStash access infrastructure"
    )
    with tempfile.TemporaryDirectory(prefix=".access-plan-", dir=root) as temporary:
        plan_file = Path(temporary) / "access.tfplan"
        executor.cli.terraform_plan(root, extra_env=executor.runtime_env, plan_file=plan_file)
        plan = executor.cli.terraform_show_json(
            root, extra_env=executor.runtime_env, initialize=False, plan_file=plan_file
        )
        admit_access_plan(owner, plan)
        changes = [
            change
            for change in plan.get("resource_changes", [])
            if change.get("change", {}).get("actions") not in (["no-op"], ["read"])
        ]
        if verify_only and changes:
            raise RuntimeError("WireGuard access Terraform state has not converged")
        if changes:
            _authority(owner)
            executor.cli.terraform_apply(
                root,
                extra_env=executor.runtime_env,
                initialize=False,
                plan_file=plan_file,
                expected_plan_sha256="sha256:" + hashlib.sha256(plan_file.read_bytes()).hexdigest(),
                abort_check=lambda: _authority(owner),
            )
    values = executor.cli.terraform_output_json(
        root, extra_env=executor.runtime_env, initialize=False
    )
    for row in owner.owners:
        instance = component_instance_id(row)
        journal = owner.journals[instance]
        output = values.get(component_output_root_name(instance, "access_resources"), {})
        resources = output.get("value") if isinstance(output, dict) else None
        if (
            not isinstance(resources, dict)
            or not resources
            or any(not isinstance(value, str) or not value for value in resources.values())
        ):
            raise RuntimeError("Access infrastructure public outputs are incomplete")
        expected = {key: value["id"] for key, value in journal.value.get("resources", {}).items()}
        if expected and expected != resources:
            raise RuntimeError(
                "Terraform access resources differ from the owned generation; never replace credentials"
            )
        owner.api.verify_access_resources(
            resources,
            project=journal.project,
            admin_group=row["access"]["admin_group_id"],
            names=secret_names(dependency_instance(row)),
        )
        if not expected:
            journal.value["resources"] = {key: {"id": value} for key, value in resources.items()}
            journal.save()
