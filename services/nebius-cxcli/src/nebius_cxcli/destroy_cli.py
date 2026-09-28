"""CLI orchestration for the single cloud-only MK8s destroy workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import shlex
import tempfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal

import typer

from .deployment_cli import _execution_paths
from .deployment_local import LocalObjectStore
from .deployment_state import DeploymentGeneration, DeploymentState
from .destroy import (
    DestroyReceipt,
    build_destroy_receipt,
    format_destroy_inventory,
    receipt_confirmation,
    run_destroy,
    write_destroy_receipt,
)
from .destroy_cloud import DestroyCloud
from .destroy_generation import (
    freeze_publication,
    freeze_terraform,
    publish,
    remove_sfs_entries,
    resources,
    validate_final_state,
    validate_reconciliation,
    verify_publication,
    vm_nfs_config,
)
from .destroy_resources import check_remaining_references
from .destroy_state import DestroyState, destroy_owner, destroy_receipt_path
from .destroy_target import check_retired_target_references, resolve_destroy_target
from .generated_manifest import manifest_path_for_generated_dir, runtime_config_from_manifest
from .infra_render import RenderedModuleSource, rendered_soperator_observability_iam_instances
from .object_storage_transport import object_storage_scope
from .soperator_receipt_io import read_owner_only_json
from .soperator_registration import soperator_registration_app_row
from .terraform_backend import backend_settings_from_config


def _terraform_call[T](operation: str, call: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Keep arbitrary provider diagnostics out of the destroy terminal/report."""
    try:
        return call(*args, **kwargs)
    except Exception:
        raise RuntimeError(
            f"Terraform {operation} failed during destroy; verify backend access and rendered "
            "configuration before retrying. Raw provider diagnostics were suppressed."
        ) from None


def cleanup_payload(
    *,
    source_payload: Mapping[str, Any],
    target_ref: str,
    ownership: str,
    base_dir: Path,
) -> dict[str, Any]:
    from . import cli

    next_payload = copy.deepcopy(dict(source_payload))
    cli._remove_target_scoped_app_rows(
        payload=next_payload,
        target_instance_ids={target_ref},
    )
    cli._remove_deploy_target_rows(
        payload=next_payload,
        target_instance_ids={target_ref},
    )
    if ownership == "managed":
        removed_mk8s = cli._remove_component_instance_row(
            payload=next_payload,
            scope="infra",
            instance_id=target_ref,
            component_id="mk8s",
        )
        if removed_mk8s is None:
            raise RuntimeError("managed MK8s config row is missing")
    cli._refresh_soperator_registration_fingerprints(next_payload)
    cli.validate_config(copy.deepcopy(next_payload), base_dir=base_dir)
    cli.normalize_runtime_config_payload(next_payload, base_dir=base_dir)
    return next_payload


@object_storage_scope()
def execute_destroy(
    config_path: Path,
    *,
    cluster_id: str,
    dry_run: bool,
    delete_sfs: bool,
    progress: Any,
    preserve_pvc_disks: bool = False,
    yes: bool = False,
) -> None:
    from . import cli

    config_path = config_path.expanduser().resolve()
    from .deployment_recovery import deployment_preview

    with deployment_preview(dry_run), ExitStack() as stack:
        progress.start("startup", "Reading deployment identity and backend recovery state")
        config = cli.load_config(config_path, persist_normalized=False)
        paths = cli.resolve_project_paths(config_path)
        cli._ensure_runtime_auth_material(config, need_terraform=True)
        settings = backend_settings_from_config(config)
        store = LocalObjectStore.for_project(paths)
        state = DestroyState(store, settings)
        remote = state.read()
        if any(paths.reports_dir.glob("soperator-destroy-*.json")):
            raise RuntimeError(
                "Unsupported destroy receipt at a predecessor cache path; finish any active "
                "operation with its original build and explicitly retire incompatible completed "
                "receipts before using this command. No receipt was modified."
            )
        if not cluster_id or cluster_id != cluster_id.strip():
            raise ValueError("--target requires an exact immutable Nebius cluster ID")
        target_ref = remote.target_ref if remote and remote.cluster_id == cluster_id else ""
        receipt_path = destroy_receipt_path(paths, cluster_id)
        inspect_cache = not (remote and remote.cluster_id == cluster_id)
        cached_payload = None
        if receipt_path.exists() and inspect_cache:
            cached_payload = read_owner_only_json(receipt_path, label="MK8s destroy receipt")
            if not isinstance(cached_payload, Mapping):
                raise ValueError("Destroy receipt must be a mapping")
            cached = DestroyReceipt.from_payload(cached_payload)
            if cached.checkpoints and cached.status != "complete" and remote is None:
                raise RuntimeError(
                    "Local destroy cache has no authoritative backend record; recovery requires the original backend"
                )

        def verify_cache_unchanged() -> None:
            if not inspect_cache:
                return
            current = (
                read_owner_only_json(receipt_path, label="MK8s destroy receipt")
                if receipt_path.exists()
                else None
            )
            if current != cached_payload:
                raise RuntimeError("Destroy local cache changed during planning; rerun the command")

        receipt = (
            remote
            if remote and remote.cluster_id == cluster_id and remote.status != "complete"
            else None
        )
        if remote and remote.status != "complete" and receipt is None:
            raise RuntimeError(
                f"Resume unfinished destroy with --target {remote.cluster_id} before another mutation"
            )
        matching = remote if remote and remote.cluster_id == cluster_id else None
        if matching and (
            matching.approved["delete_sfs"] != delete_sfs
            or matching.approved["preserve_pvc_disks"] != preserve_pvc_disks
        ):
            raise RuntimeError(
                "Destroy storage disposition differs; resume with the exact original --delete-sfs and --preserve-pvc-disks settings"
            )
        if matching and matching.project_id != settings.project_id:
            raise RuntimeError("Destroy project differs from backend approval")
        stack.enter_context(
            destroy_owner(state, target_ref, receipt.approval_fingerprint if receipt else "")
        )
        if not dry_run:
            progress.update("Acquiring local execution ownership")
            lease = stack.enter_context(
                cli._deployment_execution(
                    config=config,
                    paths=paths,
                    target_ref=target_ref or "project",
                    operation_id="destroy",
                )
            )
            state.assert_held = lease.assert_held
            # Read again under local execution ownership; an earlier observation grants no authority.
            locked = state.read()
            if locked != remote:
                raise RuntimeError(
                    "Local destroy receipt changed while acquiring execution ownership; rerun"
                )
            stack.enter_context(
                cli.SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock")
            )
            verify_cache_unchanged()
        deployments = DeploymentState(store, settings, assert_held=state.assert_held)
        cloud = DestroyCloud(
            settings.project_id, assert_held=state.assert_held, progress=progress.update
        )
        stack.callback(cloud.close)
        if receipt is None:
            source_sha256 = cli._sha256_file(config_path)
            source = cli._load_source_payload(config_path)
            if remote and remote.status == "complete" and remote.cluster_id == cluster_id:
                if cli._sha256_file(config_path) != remote.approved["post_cleanup_config_sha256"]:
                    raise RuntimeError(
                        "Completed destroy config differs from its published generation"
                    )
                write_destroy_receipt(receipt_path, remote)
                cli.console.print(f"MK8s destroy complete: cluster {remote.cluster_id}.")
                return
            progress.start("target", "Resolving exact cloud cluster identity")
            config, paths, manifest = cli._load_deploy_context_readonly(config_path)
            selected = cli._manifest_deploy_targets(manifest)
            env = cli._terraform_runtime_env(config)
            _terraform_call(
                "initialization", cli.terraform_init, paths.infra_dir, extra_env=env, quiet=True
            )
            before = _terraform_call(
                "state read",
                cli.terraform_show_json,
                paths.infra_dir,
                extra_env=env,
                initialize=False,
                quiet=True,
            )
            before_values = before.get("values", {})
            if not isinstance(before_values, Mapping):
                raise RuntimeError("Terraform state values are incomplete")
            module_sources = cli._generated_bundle_module_sources(manifest)
            target = resolve_destroy_target(
                cluster_id=cluster_id,
                source=source,
                targets=selected,
                module_sources=module_sources,
                state_values=before_values,
            )
            target_ref, ownership = target.target_ref, target.ownership
            module_names = target.module_names
            operation = cli.read_soperator_operation_status(
                paths=paths, target_ref=target_ref, include_destroy=False
            )
            if operation is not None:
                raise RuntimeError(
                    f"Destroy is blocked by an active {operation.operation} operation"
                )
            progress.start("inventory", "Reading cloud node groups, workers and attached storage")
            managed_gpu_ids = sorted(
                {
                    row["values"]["id"]
                    for address, row in resources(before_values).items()
                    if row.get("type") == "nebius_compute_v1_gpu_cluster"
                    and row.get("mode") == "managed"
                    and any(address.startswith(f"module.{name}.") for name in module_names)
                }
            )
            inventory = cloud.inventory(
                cluster_id,
                delete_sfs=delete_sfs,
                preserve_pvc_disks=preserve_pvc_disks,
                managed_gpu_ids=managed_gpu_ids,
            )
            chart = soperator_registration_app_row(source, target_ref=target_ref)
            values = chart.get("values", {}) if chart else {}
            inventory["vm_nfs"] = cloud.discover_vm_nfs(
                vm_nfs_config(source, values, target_ref=target_ref, state_values=before_values)
            )
            cleaned = cleanup_payload(
                source_payload=source,
                target_ref=target_ref,
                ownership=ownership,
                base_dir=config_path.parent,
            )
            if delete_sfs:
                cleaned = remove_sfs_entries(
                    cleaned,
                    filesystem_ids=set(inventory["filesystem_ids"]),
                    module_sources=module_sources,
                    state_values=before_values,
                )
            check_remaining_references(cleaned, inventory, preserve_pvc_disks)
            check_retired_target_references(cleaned, target_ref)
            progress.start("plan", "Staging and validating the final project generation")
            stage = cli._render_soperator_upgrade_admission(
                source_payload=cleaned,
                config_path=config_path,
                paths=paths,
                require_soperator_flux=False,
                preserve_ordinary=False,
            )
            try:
                _terraform_call(
                    "preview initialization",
                    cli.terraform_init,
                    stage.staged_paths.infra_dir,
                    extra_env=env,
                    quiet=True,
                )
                plan_path = stage.staged_paths.infra_dir / ".destroy-preview.tfplan"
                _terraform_call(
                    "preview plan",
                    cli.terraform_plan,
                    stage.staged_paths.infra_dir,
                    extra_env=env,
                    initialize=False,
                    plan_file=plan_path,
                    quiet=True,
                )
                plan = _terraform_call(
                    "preview read",
                    cli.terraform_show_json,
                    stage.staged_paths.infra_dir,
                    extra_env=env,
                    initialize=False,
                    plan_file=plan_path,
                    quiet=True,
                )
                check_remaining_references(plan["planned_values"], inventory, preserve_pvc_disks)
                terraform = freeze_terraform(
                    plan,
                    inventory=inventory,
                    delete_sfs=delete_sfs,
                    module_names=module_names,
                    managed_cluster=ownership == "managed",
                    ancillary_addresses=rendered_soperator_observability_iam_instances(
                        source,
                        tuple(
                            RenderedModuleSource(**row)
                            for row in module_sources
                            if row["module_name"] in module_names
                        ),
                    ),
                )
                staged_manifest = json.loads(
                    manifest_path_for_generated_dir(stage.staged_paths.generated_dir).read_text()
                )
                execution = DeploymentGeneration.capture(stage.staged_paths, staged_manifest)
                publication_plan = cli.build_project_generation_plan(
                    final_paths=paths,
                    staged_paths=stage.staged_paths,
                    config_path=config_path,
                    config_content=stage.proposed_config_text,
                )
                if (
                    cli._sha256_file(config_path) != source_sha256
                    or publication_plan.expected_preimages.get(config_path) != source_sha256
                ):
                    raise RuntimeError("Source config changed while preparing destroy approval")
                generation = {
                    "execution": execution.as_payload(),
                    "execution_id": execution.identity,
                    "publication": freeze_publication(publication_plan, paths.project_dir),
                }
                destroyed = [
                    f"mk8s:{cluster_id}",
                    *[f"node-group:{identifier}" for identifier in inventory["node_group_ids"]],
                    *[f"gpu-cluster:{identifier}" for identifier in inventory["gpu_cluster_ids"]],
                    *[
                        f"managed:{address}:{row['id']}"
                        for address, row in terraform["deletes"].items()
                        if not row["sdk"]
                    ],
                ]
                preserved: list[str] = []
                (destroyed if delete_sfs else preserved).extend(
                    f"sfs:{identifier}" for identifier in inventory["filesystem_ids"]
                )
                (preserved if preserve_pvc_disks else destroyed).extend(
                    f"pvc-disk:{row['id']} ({row['pvc_namespace']}/{row['pvc_name']}, "
                    f"{row['size_bytes'] / 1024**3:g} GiB)"
                    for row in inventory["pvc_disks"].values()
                )
                preserved.extend(
                    f"unclassified-disk:{identifier}"
                    for identifier in inventory["unclassified_disk_ids"]
                )
                preserved.extend(
                    f"excluded-boot-or-instance-managed-disk:{identifier} (no separate deletion)"
                    for identifier in inventory["excluded_disk_ids"]
                )
                if inventory["vm_nfs"]:
                    preserved.extend(
                        f"vm-nfs-{kind}:{identifier}"
                        for kind, identifiers in inventory["vm_nfs"]["resources"].items()
                        for identifier in identifiers
                    )
                receipt = build_destroy_receipt(
                    target_ref=target_ref,
                    ownership=ownership,
                    project_id=settings.project_id,
                    cluster_id=cluster_id,
                    delete_sfs=delete_sfs,
                    preserve_pvc_disks=preserve_pvc_disks,
                    inventory=inventory,
                    config_sha256=cli._sha256_file(config_path),
                    post_cleanup_config_sha256="sha256:"
                    + hashlib.sha256(stage.proposed_config_text.encode()).hexdigest(),
                    backend=state.backend,
                    generation=generation,
                    terraform=terraform,
                    destroy_inventory=sorted(destroyed),
                    preserve_inventory=sorted(preserved),
                )
            finally:
                stage.cleanup()
        assert receipt is not None
        frozen = receipt.approved
        publication = frozen["generation"]["publication"]
        verify_publication(
            publication,
            paths.project_dir,
            allow_postimage="state_reconciled" in receipt.checkpoints,
            allow_missing_generated=bool(receipt.checkpoints),
        )
        if dry_run:
            with cli.SoperatorOperationLocalLock(
                paths.project_dir / ".nebius-cxcli" / "config.lock"
            ):
                verify_cache_unchanged()
                write_destroy_receipt(receipt_path, receipt)
        else:
            verify_cache_unchanged()
            write_destroy_receipt(receipt_path, receipt)
        progress.success("Destroy inventory ready")
        approval_mode: Literal["interactive", "yes", "resume"] = "resume"
        confirmation: str | None = None
        with progress.paused():
            for line in format_destroy_inventory(receipt):
                cli.console.print(line)
            cli.console.print(f"Destroy receipt: {receipt_path}")
            cli.console.print(
                "Scope: selected MK8s cluster and approved inventory; unrelated infrastructure and arbitrary application-controller external resources are outside this teardown."
            )
            if dry_run:
                return
            if not receipt.checkpoints or any(
                row.get("terminal_failed") for row in (receipt.requests or {}).values()
            ):
                if yes:
                    approval_mode = "yes"
                else:
                    if not cli._is_tty_session():
                        raise RuntimeError(
                            "MK8s destroy requires an interactive TTY or explicit --yes"
                        )
                    approval_mode = "interactive"
                    expected = receipt_confirmation(receipt)
                    confirmation = typer.prompt(
                        f"Type exactly `{expected}` to continue", default=""
                    )
        stack.enter_context(destroy_owner(state, target_ref, receipt.approval_fingerprint))

        def save(current: Any) -> None:
            state.save(current)
            write_destroy_receipt(receipt_path, current)

        def reconcile() -> None:
            verify_publication(publication, paths.project_dir, allow_missing_generated=True)
            with tempfile.TemporaryDirectory(prefix="cxcli-destroy-reconcile-") as directory:
                scratch = _execution_paths(paths, Path(directory))
                generation = DeploymentGeneration.from_payload(
                    frozen["generation"]["execution"],
                    expected_id=frozen["generation"]["execution_id"],
                )
                manifest = generation.materialize(scratch)
                runtime = runtime_config_from_manifest(manifest)
                runtime_env = cli._terraform_runtime_env(runtime)
                _terraform_call(
                    "reconciliation initialization",
                    cli.terraform_init,
                    scratch.infra_dir,
                    extra_env=runtime_env,
                    quiet=True,
                )
                saved = scratch.infra_dir / ".destroy-reconcile.tfplan"
                _terraform_call(
                    "reconciliation plan",
                    cli.terraform_plan,
                    scratch.infra_dir,
                    extra_env=runtime_env,
                    initialize=False,
                    plan_file=saved,
                    quiet=True,
                )
                plan = _terraform_call(
                    "reconciliation read",
                    cli.terraform_show_json,
                    scratch.infra_dir,
                    extra_env=runtime_env,
                    initialize=False,
                    plan_file=saved,
                    quiet=True,
                )
                validate_reconciliation(plan, frozen["terraform"])
                state.assert_held()
                verify_publication(publication, paths.project_dir, allow_missing_generated=True)

                def assert_fence(_event):
                    state.assert_held()

                _terraform_call(
                    "reconciliation apply",
                    cli.terraform_apply,
                    scratch.infra_dir,
                    extra_env=runtime_env,
                    initialize=False,
                    plan_file=saved,
                    expected_plan_sha256=cli._sha256_file(saved),
                    event_callback=assert_fence,
                    abort_check=lambda: _abort_reason(state),
                )
                observed = _terraform_call(
                    "final state read",
                    cli.terraform_show_json,
                    scratch.infra_dir,
                    extra_env=runtime_env,
                    initialize=False,
                    quiet=True,
                )
                values = observed.get("values", {})
                if not isinstance(values, Mapping):
                    raise RuntimeError("Terraform state values are incomplete")
                validate_final_state(values, frozen["terraform"])

        def commit_generation() -> None:
            state.assert_held()
            publish(publication, paths.project_dir)

        progress.start("execute", "Rechecking the approved cloud inventory")
        try:
            result = run_destroy(
                receipt=receipt,
                cloud=cloud,
                save=save,
                approval_mode=approval_mode,
                confirmation=confirmation,
                reconcile=reconcile,
                publish=commit_generation,
                clear_baseline=lambda: deployments.clear_accepted(target_ref=target_ref),
                verify_preserved=lambda: cloud.verify_vm_nfs(frozen["inventory"].get("vm_nfs")),
                progress=progress.update,
            )
        except BaseException:
            if state.receipt and state.receipt.checkpoints:
                resume = [
                    "nebius-cxcli",
                    "destroy",
                    str(config_path),
                    "--target",
                    cluster_id,
                ]
                if yes:
                    resume.append("--yes")
                if delete_sfs:
                    resume.append("--delete-sfs")
                if preserve_pvc_disks:
                    resume.append("--preserve-pvc-disks")
                with progress.paused():
                    cli.console.print(
                        "Destroy incomplete after checkpoint: " + state.receipt.checkpoints[-1],
                        markup=False,
                    )
                    pending = [
                        request
                        for request in state.receipt.requests.values()
                        if not request["absent"]
                    ]
                    for request in pending[-3:]:
                        cli.console.print(
                            f"Provider operation for {request['resource_id']}: "
                            + (request["operation_id"] or "acceptance requires recovery"),
                            markup=False,
                        )
                    cli.console.print("Resume: " + shlex.join(resume), markup=False)
            raise
        progress.success()
        with progress.paused():
            disposition = "attached SFS deleted" if delete_sfs else "attached SFS preserved"
            cli.console.print(
                f"MK8s destroy {result.status}: cluster {result.cluster_id}; dedicated GPU clusters deleted; "
                f"PVC disks {'preserved' if preserve_pvc_disks else 'deleted'}; {disposition}."
            )


def _abort_reason(state: DestroyState) -> str | None:
    try:
        state.assert_held()
    except Exception:
        return "Destroy lost local execution ownership"
    return None
