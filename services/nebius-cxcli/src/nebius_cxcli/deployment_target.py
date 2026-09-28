"""Canonical per-target application deployment under local process ownership."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from .deployment_local import LocalExecutionOwner
from .deployment_plan import DeploymentAction, DeploymentPlan
from .paths import ProjectPaths
from .soperator_operation_lock import SoperatorLeaseAuthority, SoperatorOperationLease
from .soperator_upgrade_progress import SoperatorUpgradeProgress


def prepare_application_runtime(
    cli: Any,
    config: Any,
    target_paths: ProjectPaths,
    *,
    target_ref: str,
    kube_env: Mapping[str, str],
    assert_authority: Callable[[], object],
    soperator_owned: bool,
    soperator_runtime_input_env: Mapping[str, str] | None = None,
    soperator_runtime_prompt: bool = False,
) -> None:
    """Run the shared prerequisites under the current deployment owner."""
    with cli.app_mutation_scope(assert_authority):
        assert_authority()
        from .deployment_app_inventory import assert_live_release_inventory
        from .grafana_cluster import preflight_dashboard_ownership
        from .grafana_database_runtime import preflight_grafana_database
        from .nsight_runtime import prepare_nsight_viewers

        preflight_grafana_database(config, target_ref=target_ref, extra_env=kube_env)
        assert_live_release_inventory(
            cli, config, target_ref=target_ref, flux_dir=target_paths.flux_dir, kube_env=kube_env
        )
        preflight_dashboard_ownership(config, target=target_ref, env=kube_env)
        prepare_nsight_viewers(config, extra_env=kube_env, target_ref=target_ref)
        cli._reconcile_observability_gpu_node_labels(
            config,
            extra_env=kube_env,
            target_ref=target_ref,
        )
        assert_authority()
        cli._ensure_mysterybox_eso_runtime_before_flux(
            config,
            extra_env=kube_env,
            target_ref=target_ref,
        )
        with (
            cli.runtime_app_mutations(
                config,
                target_ref=target_ref,
                env=kube_env or {},
                authority=assert_authority,
            )
            if soperator_owned
            else nullcontext()
        ):
            assert_authority()
            cli._ensure_grafana_runtime_before_flux(
                config,
                extra_env=kube_env,
                target_ref=target_ref,
            )
        assert_authority()
        cli._ensure_soperator_notifier_runtime_before_flux(
            config,
            extra_env=kube_env,
            target_ref=target_ref,
            externally_managed_secret_keys=cli._mysterybox_eso_rendered_secret_keys(target_paths),
        )
        assert_authority()
        cli._ensure_soperator_runtime_before_flux(
            config,
            paths=target_paths,
            extra_env={**(kube_env or {}), **(soperator_runtime_input_env or {})},
            target_ref=target_ref,
            assert_authority=assert_authority,
            prompt=soperator_runtime_prompt and cli._console_is_terminal(),
            emit=lambda message: cli.console.print(message),
        )


def apply_ordinary_bundle(
    cli: Any,
    target_paths: ProjectPaths,
    *,
    kube_env: Mapping[str, str],
    assert_authority: Callable[[], object],
    config: Any,
    target_ref: str,
) -> None:
    """Apply the renderer-owned ordinary sibling before its dependent releases."""
    ordinary_dir = target_paths.flux_dir / "ordinary"
    if ordinary_dir.is_dir():
        with cli.app_mutation_scope(assert_authority):
            assert_authority()
            from .grafana_cluster import preflight_dashboard_ownership

            preflight_dashboard_ownership(config, target=target_ref, env=kube_env)
            with tempfile.TemporaryDirectory(prefix="cxcli-install-apps-") as ordinary_temp:
                prerequisite_dir = Path(ordinary_temp)
                shutil.copytree(ordinary_dir, prerequisite_dir, dirs_exist_ok=True)
                protected_secrets = target_paths.flux_dir / "post-flux-mysterybox-eso.yaml"
                if protected_secrets.is_file():
                    shutil.copyfile(
                        protected_secrets,
                        prerequisite_dir / "post-flux-00-mysterybox-eso.yaml",
                    )
                cli._apply_rendered_flux(
                    replace(target_paths, flux_dir=prerequisite_dir),
                    extra_env=kube_env,
                    assert_authority=assert_authority,
                )


def deploy_application_target(
    cli: Any,
    config: Any,
    paths: ProjectPaths,
    target: Mapping[str, Any],
    *,
    deploy_validations: Sequence[Mapping[str, Any]],
    job_policy: str = "fail",
    cancel_job_ids: Sequence[str] = (),
    requeue_job_ids: Sequence[str] = (),
    job_wait_timeout_seconds: int = 0,
    job_refresh_interval_seconds: int = 30,
    skip_terraform_apply: bool = False,
    deployment_lease: Any = None,
    soperator_plan: DeploymentPlan | None = None,
    soperator_release_complete: bool = False,
    soperator_install_approval_fingerprint: str = "",
    soperator_runtime_input_env: Mapping[str, str] | None = None,
    soperator_runtime_prompt: bool = False,
    soperator_infrastructure_plan_sha256: str = "",
    soperator_operation_started_at: float | None = None,
    expected_identity: Mapping[str, str] | None = None,
    on_identity: Callable[[str, Mapping[str, str]], None] | None = None,
    on_inputs: Callable[..., None] | None = None,
    report_paths: ProjectPaths | None = None,
) -> Mapping[str, Any]:
    """Canonical application sequencing for one immutable cluster target."""
    if deployment_lease is None:
        raise RuntimeError("Application execution requires the shared deployment fence")
    deployment_lease.assert_held()
    target = dict(target)
    target.pop("kube_context", None)
    if expected_identity:
        target["cluster_id"] = expected_identity["cluster_id"]
    cluster_identities: dict[str, dict[str, str]] = {}
    grafana_statuses: list[dict[str, Any]] = []
    gitops_bootstrap_commands: list[str] = []
    operation_anchor = None
    operation_completion = None
    target_ref = str(target["target_ref"])
    target_paths = cli._paths_for_target_flux_dir(paths, target)
    target_has_apps = cli._active_chart_count_for_target(config, target_ref=target_ref) > 0
    target_validations = cli._filter_validations_for_target(
        deploy_validations,
        target_ref=target_ref,
    )
    pre_app_validations = (
        cli._pre_app_cluster_smoke_validations(target_validations) if target_has_apps else []
    )
    pre_soperator_validations = cli._pre_soperator_gpu_validations(
        config,
        target=target,
        validations=[item for item in target_validations if item not in pre_app_validations],
    )
    post_soperator_validations = [
        item
        for item in target_validations
        if item not in pre_app_validations and item not in pre_soperator_validations
    ]
    post_soperator_validations = cli._ordered_post_soperator_validations(
        config,
        target_ref=target_ref,
        validations=post_soperator_validations,
    )
    needs_cluster_ready = target_has_apps or bool(target_validations)
    progress = SoperatorUpgradeProgress(cli.progress_console, prefix="Deploy")
    with ExitStack() as stack:
        with progress.phase("application-handoff", f"Connect to target {target_ref}"):
            kube_env = cli._prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                target=target,
                persist_local_kubeconfig=False,
                set_current_context=False,
                allow_terraform_output=not bool(expected_identity),
                require_renewable_auth=True,
            )
            if not kube_env:
                raise RuntimeError("Application target has no authoritative cluster handoff")
            identity = {
                "cluster_id": cli._non_empty_text(kube_env.get(cli.GRAFANA_TARGET_CLUSTER_ID_ENV)),
                "kubernetes_uid": cli._read_kube_system_namespace_uid(
                    kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
                    extra_env=kube_env,
                ),
            }
            if not all(identity.values()) or (
                expected_identity and identity != dict(expected_identity)
            ):
                raise RuntimeError(
                    "Application target immutable identity differs or is unavailable"
                )
            cluster_identities[target_ref] = identity
            if deployment_lease is not None:
                deployment_lease.assert_held()
        install_cluster_lease: SoperatorOperationLease | None = None
        install_anchor_name = ""
        install_kube_context = ""
        if deployment_lease is not None and cli._soperator_release_refs_for_job_policy(
            config, target_ref=target_ref
        ):
            with progress.phase(
                "application-authority", f"Acquire application authority for {target_ref}"
            ):
                if kube_env is None:
                    raise RuntimeError(
                        "Soperator install could not establish target Kubernetes handoff"
                    )
                cluster_id = cli._non_empty_text(kube_env.get(cli.GRAFANA_TARGET_CLUSTER_ID_ENV))
                install_kube_context = cli._non_empty_text(
                    kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV)
                )
                if not cluster_id or not install_kube_context:
                    raise RuntimeError(
                        "Soperator install handoff omitted the immutable cluster id or context"
                    )
                kubernetes_uid = cli._read_kube_system_namespace_uid(
                    kube_context=install_kube_context,
                    extra_env=kube_env,
                )
                if not kubernetes_uid:
                    raise RuntimeError(
                        "Soperator install could not read the kube-system namespace UID"
                    )
                cluster_identities[target_ref] = {
                    "cluster_id": cluster_id,
                    "kubernetes_uid": kubernetes_uid,
                }
                deployment_lease.bind_cluster_identity(
                    cluster_id=cluster_id,
                    kubernetes_uid=kubernetes_uid,
                )
                deployment_lease.assert_held()
                install_cluster_lease = stack.enter_context(
                    SoperatorOperationLease(
                        kube_context=install_kube_context,
                        cluster_id=cluster_id,
                        operation_fingerprint=deployment_lease.operation_id,
                        extra_env=kube_env,
                    )
                )
                install_cluster_lease.assert_held()
                install_anchor_name = cli._apply_soperator_install_operation_anchor(
                    extra_env=kube_env,
                    kube_context=install_kube_context,
                    cluster_id=cluster_id,
                    kubernetes_uid=kubernetes_uid,
                    operation_id=deployment_lease.operation_id,
                    approval_fingerprint=soperator_install_approval_fingerprint,
                )

        def _assert_soperator_authority(
            _cluster_lease: SoperatorOperationLease | None = (install_cluster_lease),
            _local_owner: LocalExecutionOwner | None = (deployment_lease),
        ) -> SoperatorLeaseAuthority:
            if _cluster_lease is None or _local_owner is None:
                raise RuntimeError(
                    "Soperator in-cluster mutation is available only through "
                    "`nebius-cxcli deploy` or `soperator upgrade`"
                )
            _local_owner.assert_held()
            return _cluster_lease.assert_held()

        def _assert_application_authority() -> object:
            if install_cluster_lease is not None:
                return _assert_soperator_authority()
            return deployment_lease.assert_held()

        stack.enter_context(cli.app_mutation_scope(_assert_application_authority))
        if on_inputs is not None or on_identity is not None:
            with progress.phase(
                "application-admission", f"Admit application inputs for {target_ref}"
            ):
                if on_inputs is not None:
                    on_inputs(target_ref, identity, kube_env, _assert_application_authority)
                if on_identity is not None:
                    on_identity(target_ref, identity)
        if target_has_apps:
            prepare_application_runtime(
                cli,
                config,
                target_paths,
                target_ref=target_ref,
                kube_env=kube_env,
                assert_authority=_assert_application_authority,
                soperator_owned=install_cluster_lease is not None,
                soperator_runtime_input_env=soperator_runtime_input_env,
                soperator_runtime_prompt=soperator_runtime_prompt,
            )
        else:
            from .deployment_app_inventory import assert_live_release_inventory

            _assert_application_authority()
            assert_live_release_inventory(
                cli,
                config,
                target_ref=target_ref,
                flux_dir=target_paths.flux_dir,
                kube_env=kube_env,
            )
        if needs_cluster_ready:
            cli._report_cluster_nodes_status(
                extra_env=kube_env, emit=lambda message: cli.console.print(message)
            )
        if target_has_apps and pre_app_validations:
            try:
                cli._run_target_deploy_validations(
                    pre_app_validations,
                    target_ref=target_ref,
                    reports_dir=paths.reports_dir,
                    extra_env=kube_env,
                )
            except Exception:
                raise
        if target_has_apps:
            ordinary_dir = target_paths.flux_dir / "ordinary"
            apply_ordinary_bundle(
                cli,
                target_paths,
                kube_env=kube_env,
                assert_authority=_assert_application_authority,
                config=config,
                target_ref=target_ref,
            )
            if pre_soperator_validations:
                cli.console.print(
                    "Applying platform Flux resources before Soperator GPU validations "
                    f"for target {target_ref}..."
                )
                if not ordinary_dir.is_dir():
                    with cli._staged_flux_without_soperator(
                        target_paths,
                        config=config,
                        target_ref=target_ref,
                    ) as staged_target_paths:
                        cli._apply_rendered_flux(
                            staged_target_paths,
                            extra_env=kube_env,
                            assert_authority=_assert_application_authority,
                        )
                try:
                    cli._run_target_deploy_validations(
                        pre_soperator_validations,
                        target_ref=target_ref,
                        reports_dir=paths.reports_dir,
                        extra_env=kube_env,
                    )
                except Exception:
                    raise
            target_has_soperator = bool(
                cli._soperator_release_refs_for_job_policy(config, target_ref=target_ref)
            )
            if target_has_soperator and soperator_plan is None:
                raise RuntimeError("Soperator deployment requires its admitted semantic plan")
            if (
                target_has_soperator
                and soperator_plan is not None
                and (soperator_release_complete or soperator_plan.action is DeploymentAction.NOOP)
            ):
                snapshot = cli.load_soperator_release_snapshot(
                    cli.soperator_release_snapshot_path(paths.reports_dir, target_ref)
                )
                noop_strategy = cli.resolve_soperator_reconcile_strategy(
                    current_release=snapshot.release,
                    target_release=snapshot.release,
                    source_contract=snapshot.capability_contract,
                    target_contract=snapshot.capability_contract,
                )
                operation_anchor = cli._apply_rendered_flux(
                    target_paths,
                    config=config,
                    target_ref=target_ref,
                    extra_env=kube_env,
                    strategy=noop_strategy,
                    infrastructure_plan_sha256=soperator_infrastructure_plan_sha256,
                    assert_authority=_assert_application_authority,
                )
            else:
                operation_anchor = cli._apply_rendered_flux_with_soperator_job_policy(
                    config,
                    target_paths,
                    install_recovery=(
                        target_has_soperator
                        and skip_terraform_apply
                        and soperator_plan is not None
                        and not soperator_plan.source_release
                    ),
                    operation_source_release=(
                        soperator_plan.source_release
                        if target_has_soperator and soperator_plan is not None
                        else None
                    ),
                    command_name="deploy",
                    target_ref=target_ref,
                    extra_env=kube_env,
                    job_policy=job_policy,
                    cancel_job_ids=cancel_job_ids,
                    requeue_job_ids=requeue_job_ids,
                    job_wait_timeout_seconds=job_wait_timeout_seconds,
                    job_refresh_interval_seconds=job_refresh_interval_seconds,
                    infrastructure_plan_sha256=soperator_infrastructure_plan_sha256,
                    assert_authority=_assert_application_authority,
                    operation_started_at=soperator_operation_started_at,
                )
            if target_has_soperator and operation_anchor is None:
                raise RuntimeError("Soperator deployment returned no operation anchor")
            if (
                target_has_soperator
                and soperator_plan is not None
                and soperator_plan.action is DeploymentAction.INSTALL
            ):
                cli._ensure_soperator_gpu_ephemeral_bootstrap_power_state(
                    config,
                    target_ref=target_ref,
                    extra_env=kube_env,
                )
            from .grafana_cluster import replay_dashboards

            replay_dashboards(config, target_paths, target=target_ref, env=kube_env)
            grafana_statuses.extend(
                cli._collect_grafana_status_after_flux(
                    config,
                    extra_env=kube_env,
                    target_ref=target_ref,
                )
            )
            from .nsight_runtime import collect_nsight_status

            collect_nsight_status(
                config,
                extra_env=kube_env,
                target_ref=target_ref,
                emit=lambda message: cli.console.print(message, markup=False, soft_wrap=True),
            )
            bootstrap_command = cli._warn_if_flux_gitops_not_bootstrapped(
                config,
                report_paths or target_paths,
                extra_env=kube_env,
                target_ref=target_ref,
                print_command=False,
            )
            if bootstrap_command:
                gitops_bootstrap_commands.append(bootstrap_command)
        if post_soperator_validations:
            try:
                cli._run_target_deploy_validations(
                    post_soperator_validations,
                    target_ref=target_ref,
                    reports_dir=paths.reports_dir,
                    extra_env=kube_env,
                )
            except Exception:
                raise
        if operation_anchor is not None:
            _assert_application_authority()
            from .operation_completion import prepare_completion

            operation_completion = prepare_completion(
                operation_anchor, report_paths or paths, target_ref, source_paths=paths
            )
            _assert_application_authority()
            operation_anchor.complete()
        if install_cluster_lease is not None:
            if deployment_lease is None:
                raise RuntimeError("Initial deployment lost its shared backend fence")
            install_cluster_lease.assert_held()
            deployment_lease.assert_held()
            cli._complete_soperator_install_operation_anchor(
                extra_env=kube_env or {},
                kube_context=install_kube_context,
                name=install_anchor_name,
                operation_id=deployment_lease.operation_id,
            )
    return {
        "identity": cluster_identities[target_ref],
        "grafana_statuses": grafana_statuses,
        "bootstrap_commands": gitops_bootstrap_commands,
        "operation_completion": operation_completion,
    }
