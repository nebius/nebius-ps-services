"""Bind the shared deployment workflow to the existing Terraform and release engines."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from rich.markup import escape

from .config_model import to_dynamic_payload
from .deployment_iam import assert_service_account_names_available
from .deployment_local import LocalExecutionOwner, LocalObjectStore
from .deployment_plan import (
    DeploymentAction,
    DeploymentPlan,
    DeploymentStage,
    DeploymentStageKind,
    assert_protected_terraform_scope,
    assert_stage_plan,
    plan_deployment,
    plan_with_observed_drift,
    soperator_target,
    terraform_admission,
    terraform_changes,
)
from .deployment_preparation import TerraformObservation, prepared_deployment, terraform_inputs
from .deployment_recovery import (
    capture_execution_cache,
    deployment_preview,
    restore_execution_cache,
)
from .deployment_state import (
    DeploymentGeneration,
    DeploymentState,
    digest,
)
from .deployment_target import deploy_application_target
from .deployment_timing import deployment_timing, selected_deployment_timing
from .deployment_workflow import StageAdmission, run_deployment
from .generated_manifest import runtime_config_from_manifest
from .object_storage_transport import object_storage_scope
from .paths import ProjectPaths, private_project_root
from .runtime_config import to_plain_data
from .soperator_config_materialization import frozen_soperator_topology
from .soperator_generation import use_generation_soperator_releases
from .soperator_upgrade_progress import SoperatorUpgradeProgress
from .terraform_backend import backend_settings_from_config


@dataclass(frozen=True)
class DeployOptions:
    dry_run: bool = False
    skip_validations: bool = False
    skip_validation_kinds: frozenset[str] = frozenset()
    target_ref: str | None = None
    all_targets: bool = False
    job_policy: str = "fail"
    cancel_job_ids: tuple[str, ...] = ()
    requeue_job_ids: tuple[str, ...] = ()
    job_wait_timeout: str = "0s"
    job_refresh_interval: str = "30s"

    def controls(self) -> dict[str, Any]:
        return {
            "jobPolicy": self.job_policy,
            "cancelJobIds": list(self.cancel_job_ids),
            "requeueJobIds": list(self.requeue_job_ids),
            "jobWaitTimeout": self.job_wait_timeout,
            "jobRefreshInterval": self.job_refresh_interval,
            "targetRef": self.target_ref,
            "allTargets": self.all_targets,
            "skipValidations": self.skip_validations,
            "skipValidationKinds": sorted(self.skip_validation_kinds),
        }


def _execution_paths(original: ProjectPaths, directory: Path) -> ProjectPaths:
    # Temporary roots can have OS aliases; all derived paths must use the same
    # canonical root as Terraform's saved-plan containment checks.
    directory = directory.resolve()
    project = directory / original.path_tenant_folder / original.path_project_folder
    project.mkdir(mode=0o700, parents=True)
    generated = project / "generated"
    return replace(
        original,
        repo_root=directory,
        deployments_dir=directory,
        project_dir=project,
        config_path=project / "config.yaml",
        generated_dir=generated,
        infra_dir=generated / "infra",
        flux_dir=generated / "flux",
        reports_dir=generated / "reports",
    )


def _plan_from_payload(payload: Mapping[str, Any]) -> DeploymentPlan:
    return DeploymentPlan(
        DeploymentAction(payload["action"]),
        str(payload["targetRef"]),
        str(payload["sourceRelease"]),
        str(payload["targetRelease"]),
        tuple(payload["changedFields"]),
        tuple(
            DeploymentStage(
                DeploymentStageKind(row["name"]),
                row["config"],
                tuple(row["retiredGroups"]),
                tuple(row["addedGroups"]),
            )
            for row in payload["stages"]
        ),
        tuple(payload["selectedTargets"]),
    )


@object_storage_scope()
@prepared_deployment()
def deploy_rendered_bundle(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    options: DeployOptions,
) -> Any:
    if options.target_ref and options.all_targets:
        raise ValueError("Choose either --target or --all-targets")
    result = _deploy_validated_bundle(config, paths, manifest, options=options)
    if not options.dry_run:
        from . import cli

        cli.progress_console.print("Deploy: Result — completed")
    return result


@deployment_timing
def _deploy_validated_bundle(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    options: DeployOptions,
) -> Any:
    from . import cli
    from .deployment_state import assert_admitted_application_generation
    from .generated_manifest import load_generated_manifest

    # Read the manifest and its files in one publication critical section.
    # Rendering may continue after this private snapshot has been captured.
    with render_publication_lock(config=config, paths=paths):
        manifest = load_generated_manifest(paths.generated_dir)
        if options.target_ref or options.all_targets:
            cli._resolve_selected_deploy_targets(
                manifest, requested_target_ref=options.target_ref, all_targets=options.all_targets
            )
        config = runtime_config_from_manifest(manifest)
        settings = backend_settings_from_config(config)
        if manifest.get("execution", {}).get("backend") != asdict(settings):
            raise RuntimeError("Deployment backend differs from the rendered identity; rerender")
        assert_admitted_application_generation(paths, manifest)
        generation = DeploymentGeneration.capture(paths, manifest)
    with deployment_preview(options.dry_run):
        cli._ensure_terraform_backend_ready(config)
    store = LocalObjectStore.for_project(paths)
    lease_context = (
        nullcontext(None)
        if options.dry_run
        else cli._deployment_execution(
            config=config,
            paths=paths,
            target_ref="project",
            operation_id=generation.identity,
            bootstrap_backend=False,
        )
    )
    with (
        lease_context as lease,
        tempfile.TemporaryDirectory(prefix="cxcli-deploy-") as directory,
        frozen_soperator_topology(),
        deployment_preview(options.dry_run),
        private_project_root(Path(directory)),
    ):
        from .deployment_workflow import _semantic_controls

        attempt = digest(
            {"generation": generation.identity, "controls": _semantic_controls(options.controls())}
        )[7:]
        state = DeploymentState(
            store,
            settings,
            assert_held=(lease.assert_held if lease else _preview_cannot_write),
            attempt=attempt,
        )
        record = state.read()
        active = record.value.get("active") if record else None
        # Only the selected attempt owns recovery. A prior completion is a local
        # report, never an input to a new plan or a source of physical bindings.
        accepted_record = record.value.get("accepted") if active else None
        source_generation = None
        target_generations: dict[str, DeploymentGeneration] = {}
        desired_generation = generation
        execution_paths = _execution_paths(paths, Path(directory).resolve())
        execution_manifest: Mapping[str, Any] = generation.materialize(execution_paths)
        execution_config = runtime_config_from_manifest(execution_manifest)
        execution_paths.config_path.write_text(
            yaml.safe_dump(to_dynamic_payload(to_plain_data(execution_config)), sort_keys=False)
        )
        execution_paths.config_path.chmod(0o600)
        executor = _CliDeploymentExecutor(
            execution_config, execution_paths, execution_manifest, options=options, lease=lease
        )
        executor.generation = generation
        executor.report_paths = paths
        executor.desired_generation = desired_generation
        executor.source_generation = source_generation
        executor.accepted_evidence = accepted_record.get("evidence", {}) if accepted_record else {}
        executor.target_generations = target_generations
        cli.progress_console.print("Deploy: Prepare")
        if active and active.get("recovery"):
            # Recheck both the authored render and its accepted jail projection
            # before restoring mutable execution files. Stage admission binds
            # the same effective inputs on the initial run and on recovery.
            executor.restore_execution(active["recovery"])
            from .deployment_effective_inputs import prepare_recovered_application_metadata

            executor.manifest = prepare_recovered_application_metadata(
                cli,
                generation,
                executor.paths,
                executor.manifest,
                selected=_plan_from_payload(active["plan"]["semanticPlan"]).selected_targets,
                assert_authority=lease.assert_held if lease else lambda: None,
            )
            execution_manifest = executor.manifest
            execution_config = executor.config
        executor.preflight()
        cli.progress_console.print("Deploy: Assess changes")
        if active:
            plan = _plan_from_payload(active["plan"]["semanticPlan"])
        else:
            live_release, absent, identities = executor.observe(accepted_record)
            if getattr(executor, "observed_reconcile", False):
                selected = soperator_target(to_plain_data(execution_config))
                assert selected is not None and live_release is not None
                plan = DeploymentPlan(
                    DeploymentAction.RECONCILE
                    if executor.settings_drift
                    else DeploymentAction.NOOP,
                    selected[0],
                    live_release,
                    live_release,
                    (),
                    (
                        DeploymentStage(
                            DeploymentStageKind.RECONCILE, to_plain_data(execution_config)
                        ),
                    ),
                )
            else:
                plan = plan_deployment(
                    to_plain_data(execution_config),
                    accepted=getattr(executor, "observed_source", None),
                    live_release=live_release,
                    infrastructure_absent=absent,
                )
            executor.identities = identities
            if executor.settings_drift:
                plan = replace(
                    plan,
                    action=DeploymentAction.RECONCILE
                    if plan.action is DeploymentAction.NOOP
                    else plan.action,
                    changed_fields=(*plan.changed_fields, "live:applications"),
                )
            if plan.target_ref and plan.action is not DeploymentAction.INSTALL:
                live_terraform = executor._observed_terraform_admission()
                modules = {
                    name
                    for name, value in cli._generated_bundle_mk8s_module_index(
                        execution_manifest
                    ).items()
                    if value[1] == plan.target_ref
                }
                if (
                    getattr(executor, "observed_source", None) is not None
                    and live_release == plan.target_release
                    and not any(
                        any(
                            str(row.get("address", "")).startswith(f"module.{name}.")
                            for name in modules
                        )
                        for row in terraform_changes(live_terraform)
                    )
                ):
                    # Expanded provider defaults are not configuration drift.
                    plan = replace(
                        plan,
                        action=DeploymentAction.RECONCILE
                        if executor.settings_drift
                        else DeploymentAction.NOOP,
                        changed_fields=("live:applications",) if executor.settings_drift else (),
                        stages=(
                            DeploymentStage(
                                DeploymentStageKind.RECONCILE, to_plain_data(execution_config)
                            ),
                        ),
                    )
                plan = plan_with_observed_drift(
                    plan,
                    live_terraform,
                    owned_modules=modules,
                )
        if (
            plan.target_ref
            and options.target_ref
            and options.target_ref != plan.target_ref
            and plan.action is not DeploymentAction.NOOP
        ):
            raise RuntimeError(
                f"Rendered Soperator changes require --target {plan.target_ref} or an all-target deploy"
            )
        selected_refs = tuple(
            str(row["target_ref"])
            for row in cli._resolve_deploy_run_targets(
                generation.manifest,
                requested_target_ref=options.target_ref,
                all_targets=options.all_targets,
            )
        )
        if active:
            if plan.selected_targets != selected_refs:
                raise RuntimeError("Recovery target selection differs from its frozen plan")
        else:
            stages = plan.stages
            if any(stage.name is not DeploymentStageKind.RECONCILE for stage in stages) and any(
                ref != plan.target_ref for ref in selected_refs
            ):
                stages = (
                    *stages,
                    DeploymentStage(
                        DeploymentStageKind.APPLICATIONS, to_plain_data(execution_config)
                    ),
                )
            plan = replace(plan, stages=stages, selected_targets=selected_refs)
        executor.plan = plan
        if plan.target_ref and not options.dry_run:
            from .soperator_acceptance import current_control

            current_control().announce()
        cli.console.print(f"Deployment workflow: {plan.action.value}")
        for stage in plan.stages:
            cli.console.print(f"  {stage.name.value}")
        executor.assert_supported_application_inventory()
        try:
            with selected_deployment_timing(to_plain_data(execution_config), plan.selected_targets):
                result = run_deployment(
                    generation=generation,
                    plan=plan,
                    state=state,
                    executor=executor,
                    dry_run=options.dry_run,
                    controls=options.controls(),
                )
            if result.preview:
                cli.console.print(
                    "Deployment preview complete; no execution checkpoints or acceptance were changed."
                )
                return None
            for outcome in (
                result.evidence.get("acceptanceControl", {}).get("outcomes", {}).values()
            ):
                validation = outcome["validation"]
                from .soperator_deployment_profile import diagnostics_notice

                notice = diagnostics_notice(validation.get("contract", {}))
                if validation.get("profile") == "fast-dev-test":
                    cli.console.print(
                        "Soperator fast deployment complete: service readiness and ordinary-user Slurm job passed."
                    )
                    cli.console.print(notice)
                    continue
                cli.console.print(
                    "Soperator deployment complete: readiness passed; extended tests "
                    + validation["extended"]
                    + "; Active/Passive checks restored; user scheduling reopened."
                )
                if notice:
                    cli.console.print(notice)
            if executor.summary is not None:
                record = state.read()
                accepted = record.value.get("accepted") if record else None
                if not accepted or accepted.get("generation") != generation.identity:
                    raise RuntimeError(
                        "Deployment acceptance changed before app baseline publication"
                    )
                baseline_published = cli.accept_ordinary_app_baseline(
                    paths,
                    identities=executor.summary.cluster_identities,
                    selected_target_refs=executor.plan.selected_targets,
                    deployment_generation=accepted["generation"],
                    expected_generation=generation,
                )
                if baseline_published is False:
                    cli.console.print(
                        "Deployment succeeded. Ordinary-app baseline was not refreshed because local source or generated files differ from the deployed snapshot."
                    )
                from .grafana_access import complete_grafana_handoff

                executor.summary = complete_grafana_handoff(
                    executor.config,
                    executor.paths,
                    paths,
                    executor.summary,
                    executor.plan.selected_targets,
                    cli._filter_validations_for_target_refs(
                        cli._manifest_deploy_validations(executor.manifest),
                        target_refs=set(executor.plan.selected_targets),
                    ),
                )
            return executor.summary
        except (Exception, KeyboardInterrupt) as exc:
            # Applications may have passed before final convergence or backend
            # acceptance failed. Publish that outcome before the private cache exits.
            if executor.summary is not None and executor.summary.validation_report is not None:
                from .deployment_reports import publish_deployment_reports

                with suppress(Exception):
                    try:
                        publish_deployment_reports(
                            execution_paths,
                            paths,
                            cli._manifest_deploy_validations(executor.manifest),
                            executor.summary.validation_report,
                            failure=exc,
                        )
                    except Exception:
                        cli.console.print(
                            "[red]Deployment reports could not record the final failure.[/red]"
                        )
                    cli._print_deploy_command_footer(
                        config, paths, executor.summary, succeeded=False
                    )
            raise


@object_storage_scope()
def read_local_deployment_record(config_path: Path) -> Any:
    from . import cli

    config = cli.load_config(config_path, persist_normalized=False)
    settings = backend_settings_from_config(config)
    paths = cli.resolve_project_paths(config_path)
    return DeploymentState(
        LocalObjectStore.for_project(paths), settings, assert_held=_preview_cannot_write
    ).read()


@object_storage_scope()
def hydrate_upgrade_jail_config(
    config_path: Path, desired: Mapping[str, Any], target_ref: str
) -> dict[str, Any]:
    from . import cli
    from .deployment_jail_state import accepted_effective_generation, merge_config_jail_state

    config = cli.load_config(config_path, persist_normalized=False)
    settings = backend_settings_from_config(config)
    paths = cli.resolve_project_paths(config_path)
    state = DeploymentState(
        LocalObjectStore.for_project(paths), settings, assert_held=_preview_cannot_write
    )
    record = state.read()
    accepted = record.value.get("accepted") if record else None
    evidence = (accepted or {}).get("evidence", {}).get("targets", {}).get(target_ref)
    if not evidence:
        return dict(desired)
    effective = accepted_effective_generation(state, target_ref, evidence)
    if effective is None:
        return dict(desired)
    authored = state.generation(evidence["generation"])
    return merge_config_jail_state(
        authored.manifest["runtime_config"],
        effective.manifest["runtime_config"],
        desired,
        target_ref,
    )


def _preview_cannot_write() -> None:
    raise RuntimeError("Preview cannot write deployment authority")


class _CliDeploymentExecutor:
    def __init__(
        self,
        config: Any,
        paths: ProjectPaths,
        manifest: Mapping[str, Any],
        *,
        options: DeployOptions,
        lease: Any,
    ) -> None:
        from . import cli

        self.cli = cli
        self.config, self.paths, self.manifest = config, paths, manifest
        self.report_paths = paths
        self.options, self.lease = options, lease
        self.plan: DeploymentPlan | None = None
        self.summary: Any = None
        self.runtime_env: dict[str, str] = {}
        self._runtime_payload_env: dict[str, str] = {}
        self.identities: dict[str, Any] = {}
        self.settings_drift = False
        self.owned_settings: dict[str, Any] = {}
        self._verifying_final = False
        self._noop_profile_summary_printed = False
        self._attempt = 0
        self._applied: set[str] = set()
        self.generation: DeploymentGeneration | None = None
        self.desired_generation: DeploymentGeneration | None = None
        self._jail_storage_evidence: Mapping[str, Any] | None = None
        self.source_generation: DeploymentGeneration | None = None
        self.hooks: Any = None
        self._campaign_complete = False
        self.campaign_intent: Any = None
        self.accepted_evidence: Mapping[str, Any] = {}
        self.target_generations: dict[str, DeploymentGeneration] = {}
        self._verified_targets: dict[str, Any] = {}
        self.compatibility_report: Mapping[str, Any] = {}
        self._observation: TerraformObservation | None = None
        self._prepared_release = None
        self._print_release_plan = cli.single_use_soperator_upgrade_plan_printer(
            cli._print_upgrade_plan_lines
        )

    def recovery_cache(self) -> Mapping[str, Any]:
        return capture_execution_cache(self.paths.repo_root)

    def preflight(self) -> None:
        self._runtime_payload_env = self.cli._run_deploy_preflight(
            self.config, self.paths, manifest=self.manifest
        )
        from .soperator_receipt_io import read_owner_only_json

        compatibility_report = read_owner_only_json(
            self.paths.reports_dir / "compatibility-admission.json", label="compatibility admission"
        )
        if (
            not isinstance(compatibility_report, Mapping)
            or compatibility_report.get("admitted") is not True
        ):
            raise RuntimeError("Compatibility admission is incomplete")
        self.compatibility_report = compatibility_report
        self._prepare_runtime_inputs()

    def _prepare_runtime_inputs(self) -> None:
        self.runtime_env = self.cli._terraform_runtime_env(self.config)
        self.runtime_env.update(self._runtime_payload_env)
        selected = soperator_target(to_plain_data(self.config))
        if selected:
            self.cli.preflight_soperator_backup_inputs(
                self.config, target_ref=selected[0], prompt=False
            )
            self.runtime_env.update(
                self.cli.preflight_soperator_sssd_inputs(
                    self.config, target_ref=selected[0], prompt=False
                )
            )

    def restore_execution(self, cache: Mapping[str, Any]) -> None:
        """Resume execution state without replacing fresh immutable admission."""
        from .compatibility_artifacts import bind_flux_artifacts

        restore_execution_cache(self.paths.repo_root, cache)
        self.manifest = self.cli.load_generated_manifest(self.paths.generated_dir)
        # Embedded render checkpoints can contain native chart references.
        # Restore their admitted artifact bindings before lifecycle hash checks.
        bind_flux_artifacts(
            self.paths,
            self.manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {}),
        )
        self.config = runtime_config_from_manifest(self.manifest)
        self._observation = None
        self._prepared_release = None
        self.runtime_env = {}
        self._runtime_payload_env = {}

    def observe(
        self, accepted: Mapping[str, Any] | None
    ) -> tuple[str | None, bool, dict[str, Any]]:
        if self.cli._config_has_enabled_infra_components(self.config) and not self._verifying_final:
            self._terraform_plan(purpose="Observe current infrastructure", include_noop=True)
        state = getattr(self, "observation_plan", {}).get("prior_state")
        if state is None:
            state = (
                self.cli.terraform_show_json(
                    self.paths.infra_dir, extra_env=self.runtime_env, initialize=False
                )
                if self.cli._config_has_enabled_infra_components(self.config)
                else {}
            )
        resources = self.cli._terraform_state_resources(state)
        clusters = [row for row in resources if row.get("type") == "nebius_mk8s_v1_cluster"]
        selected = soperator_target(to_plain_data(self.config))
        if not selected:
            return None, not clusters, {}
        target = self.cli._resolve_selected_deploy_targets(
            self.manifest,
            requested_target_ref=selected[0],
            all_targets=False,
        )[0]
        target_cluster = [
            row
            for row in clusters
            if self.cli._terraform_state_module_name(str(row.get("address", "")))
            in self.cli._generated_bundle_mk8s_module_index(self.manifest)
            and self.cli._generated_bundle_mk8s_module_index(self.manifest)[
                self.cli._terraform_state_module_name(str(row.get("address", "")))
            ][1]
            == target["target_ref"]
        ]
        if not target_cluster and target.get("ownership") == "managed":
            from .mk8s_upgrade import (
                Mk8sKubernetesVersionExecutor,
                find_source_mk8s_component,
                source_mk8s_cluster_name,
            )
            from .terraform_backend import _is_not_found_error

            component = find_source_mk8s_component(
                to_plain_data(self.config), str(target["target_ref"])
            )
            project_id = str(self.config.client_info.nebius.project_id)
            sdk = self.cli.init_nebius_sdk(
                parent_id=project_id,
                context="Deployment ownership observation",
                prefer_operator_auth=True,
            )
            try:
                provider = Mk8sKubernetesVersionExecutor(sdk)
                try:
                    provider.get_cluster_by_name(
                        project_id=project_id,
                        name=source_mk8s_cluster_name(
                            component, fallback=str(target["target_ref"])
                        ),
                    )
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise RuntimeError("Cannot prove provider cluster absence") from exc
                else:
                    raise RuntimeError(
                        "Existing provider cluster has no authoritative Terraform ownership; onboard it first"
                    )
            finally:
                sdk.sync_close()
            return None, True, {}
        with (
            ExitStack() as stack,
            nullcontext()
            if self._verifying_final
            else SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "observe-target", "Observe Soperator cluster state"
            ),
        ):
            env = self.cli._prepare_cluster_handoff_kube_env(
                self.config,
                self.paths,
                stack=stack,
                target=target,
                persist_local_kubeconfig=False,
                set_current_context=False,
                require_renewable_auth=True,
            )
            if not env:
                raise RuntimeError("Cannot observe Soperator cluster ownership")
            cluster_id = str(env.get(self.cli.GRAFANA_TARGET_CLUSTER_ID_ENV) or "")
            context = str(env.get(self.cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV) or "")
            uid = self.cli._read_kube_system_namespace_uid(kube_context=context, extra_env=env)
            if not cluster_id or not uid:
                raise RuntimeError("Soperator immutable cluster identity is incomplete")
            identities = {
                str(target["target_ref"]): {"cluster_id": cluster_id, "kubernetes_uid": uid}
            }
            from .deployment_observation import observe_release_settings

            if self._verifying_final:
                target_paths = self.cli._paths_for_target_flux_dir(self.paths, target)
                graph = self.cli.rendered_soperator_graph_contract(target_paths.flux_dir)
                if not graph or not isinstance(graph.get("releases"), list):
                    raise RuntimeError("Rendered Soperator release graph is unavailable")
                from .deployment_observation import rendered_release_identities

                # The desired-state verifier owns the graph readiness gate.
                self.verify_soperator_desired(kube_env=env)
                from .deployment_jail_state import jail_values, observe_jail_storage

                self._jail_storage_evidence = observe_jail_storage(
                    self.cli,
                    flux_dir=target_paths.flux_dir,
                    values=jail_values(
                        self._desired_application_generation().manifest["runtime_config"],
                        str(target["target_ref"]),
                    ),
                    kube_context=context,
                    kube_env=env,
                )
                self.owned_settings = observe_release_settings(
                    self.cli,
                    kube_env=env,
                    identities=rendered_release_identities(target_paths.flux_dir, graph),
                )
                if any(row.get("missing") for row in self.owned_settings.values()):
                    raise RuntimeError("A required Soperator HelmRelease is absent")
            live_release = self.cli._live_soperator_release_for_reconcile(env=env)
            if live_release and not self._verifying_final:
                from .deployment_observed_source import (
                    observe_upstream_values,
                    project_observed_source,
                )

                target_paths = self.cli._paths_for_target_flux_dir(self.paths, target)
                live_values = observe_upstream_values(
                    self.cli, flux_dir=target_paths.flux_dir, kube_env=env
                )
                self.observed_values = live_values
                modules = {
                    name
                    for name, value in self.cli._generated_bundle_mk8s_module_index(
                        self.manifest
                    ).items()
                    if value[1] == selected[0]
                }
                topology_changes = any(
                    any(
                        str(row.get("address", "")).startswith(f"module.{name}.")
                        for name in modules
                    )
                    for row in terraform_changes(getattr(self, "observation_plan", {}))
                )
                if live_release != str(selected[1].get("version")) or topology_changes:
                    self.settings_drift = (
                        live_values
                        != self.cli._rendered_soperator_upstream_values(target_paths.flux_dir)
                    )
                    if live_values is None:
                        raise RuntimeError(
                            "Coordinated deployment requires the current Soperator values "
                            "ConfigMap; reconcile the installed release before changing its "
                            "release or infrastructure"
                        )
                    self.observed_source = project_observed_source(
                        to_plain_data(self.config),
                        target_ref=selected[0],
                        resources=resources,
                        owned_modules=modules,
                        live_release=live_release,
                        live_values=live_values,
                        terraform=getattr(self, "observation_plan", {}),
                    )
                    from .deployment_observed_source import (
                        observe_adapter,
                        verify_source_projection,
                    )

                    self.observed_source = verify_source_projection(
                        self.cli,
                        self.observed_source,
                        paths=self.paths,
                        target_ref=selected[0],
                        upstream=live_values,
                        adapter=observe_adapter(self.cli, kube_env=env),
                        kube_env=env,
                    )
                else:
                    from .deployment_observed_source import (
                        resolved_settings_differ,
                        verify_reconcile_storage,
                    )

                    verify_reconcile_storage(
                        self.cli,
                        generation=self.generation,
                        flux_dir=target_paths.flux_dir,
                        target_ref=selected[0],
                        kube_env=env,
                    )
                    with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                        "observe-settings", "Compare effective Soperator settings"
                    ):
                        self.settings_drift = resolved_settings_differ(
                            self.cli,
                            generation=self.generation,
                            paths=self.paths,
                            target_ref=selected[0],
                            live_values=live_values,
                        )
                    self.observed_reconcile = True
            return live_release, False, identities

    def _terraform_plan(
        self,
        *,
        purpose: str = "Check deployment infrastructure",
        include_noop: bool = False,
        replace_addresses: Sequence[str] = (),
    ) -> tuple[Path | None, Mapping[str, Any]]:
        if not self.cli._config_has_enabled_infra_components(self.config):
            return None, {"resource_changes": []}
        self._attempt += 1
        # Execution plans are private runtime data, outside the sealed generated bundle.
        plan_file = (
            self.paths.infra_dir
            / ".terraform"
            / "cxcli-plans"
            / f"deployment-{self._attempt}.tfplan"
        )
        self.cli.progress_console.print(f"Deploy: {purpose} (plan {self._attempt})", markup=False)
        self.cli.terraform_plan(
            self.paths.infra_dir,
            extra_env=self.runtime_env,
            initialize=False,
            plan_file=plan_file,
            replace_addresses=replace_addresses,
        )
        with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
            "inspect-plan", f"Inspect saved Terraform plan {self._attempt}: {purpose}"
        ):
            payload = self.cli.terraform_show_json(
                self.paths.infra_dir,
                extra_env=self.runtime_env,
                initialize=False,
                plan_file=plan_file,
            )
        plan_file.chmod(0o600)
        self.observation_plan = payload
        self._observation = TerraformObservation(
            self.paths.infra_dir.resolve(), terraform_inputs(self.paths.infra_dir), payload
        )
        assert_service_account_names_available(payload)
        if replace_addresses:
            plan_file.unlink()
            return None, terraform_admission(payload, include_noop=include_noop)
        return plan_file, terraform_admission(payload, include_noop=include_noop)

    def _observed_terraform_admission(self) -> Mapping[str, Any]:
        observation = getattr(self, "_observation", None)
        if observation is None or not observation.matches(self.paths.infra_dir):
            return self._terraform_plan(purpose="Assess deployment changes")[1]
        # Observation includes no-op resources for identity discovery; admission
        # derives its own projection from the same raw plan without refreshing.
        return terraform_admission(observation.raw)

    def admit(self, plan: DeploymentPlan) -> tuple[StageAdmission, ...]:
        terraform = self._observed_terraform_admission()
        if plan.target_ref:
            assert_protected_terraform_scope(terraform, target_ref=plan.target_ref)
            with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "admission-inputs", "Validate frozen Soperator install inputs"
            ):
                self.cli._preflight_soperator_install_checks(self.paths, plan.target_ref)
        if any(stage.name is not DeploymentStageKind.RECONCILE for stage in plan.stages):
            return self._admit_coordinated_stages(plan)
        if (
            plan.target_ref
            and plan.action is not DeploymentAction.INSTALL
            and plan.action is not DeploymentAction.NOOP
        ):
            self._release(dry_run=True)
        return (
            StageAdmission(
                plan.stages[0],
                terraform,
                {
                    "desired": digest(plan.stages[0].config),
                    "release": plan.target_release,
                    "compatibilityAdmission": self.compatibility_report,
                },
            ),
        )

    def apply_admitted_project_terraform(
        self, admission: StageAdmission, assert_authority: Any
    ) -> None:
        from .compatibility_execution import verify_constraint_replay

        verify_constraint_replay(
            admission.application["compatibilityAdmission"], self.compatibility_report
        )
        plan_file, refreshed = self._terraform_plan(
            purpose="Refresh admitted project changes before apply"
        )
        assert_stage_plan(admission.terraform, refreshed)
        assert_authority()
        if terraform_changes(refreshed):
            if plan_file is None:
                raise RuntimeError("Admitted Terraform changes require a saved execution plan")
            self.cli._run_terraform_apply_with_status(
                self.config,
                self.paths,
                initialize=False,
                run_mk8s_preflight=False,
                plan_file=plan_file,
                expected_plan_sha256=self.cli._sha256_file(plan_file),
                extra_env=self.runtime_env,
                assert_authority=assert_authority,
            )
        assert_authority()
        _, after = self._terraform_plan(purpose="Verify project infrastructure convergence")
        if terraform_changes(after):
            raise RuntimeError("Project Terraform has not reached its admitted postconditions")

    def assert_supported_application_inventory(self) -> None:
        """Reject unowned pruning before creating active deployment authority."""
        from .deployment_observation import target_documents

        assert self.plan is not None and self.generation is not None
        for ref in self.plan.selected_targets:
            previous = self.target_generations.get(ref)
            if previous is None:
                continue
            prior = target_documents(previous, ref, ordinary_only=True)
            desired = target_documents(self.generation, ref, ordinary_only=True)
            if prior.keys() - desired.keys():
                raise RuntimeError(
                    "deploy does not own removal or renaming of rendered application resources; "
                    f"keep the accepted resource inventory for target {ref}"
                )

    def _desired_application_generation(
        self,
        *,
        fresh: bool = False,
        purpose: str = "Resolve application manifests and compatibility",
    ) -> DeploymentGeneration:
        from .deployment_resolution import resolved_application_generation

        assert self.generation is not None
        base = self.desired_generation or self.generation
        if self.campaign_intent is not None:
            from .deployment_resolution import project_jail_generation

            if self.hooks is None or "reconcile" not in self.hooks.admissions:
                raise RuntimeError("Campaign final application admission is missing")
            admission = self.hooks.admissions["reconcile"].application
            frozen = DeploymentGeneration.from_payload(
                admission["generation"], expected_id=admission["generationId"]
            )
            authority = self._campaign_jail_authority()
            inputs = frozen.manifest.get("render", {}).get("application_inputs")
            if not isinstance(inputs, Mapping):
                raise RuntimeError("Campaign application render inputs are missing")
            self._resolved_generation = self._campaign_application_journal().seal(
                "reconcile",
                binding={"generation": frozen.identity, "authority": authority},
                build=lambda: project_jail_generation(
                    self.cli,
                    frozen,
                    self.paths,
                    target_ref=self.campaign_intent.target_ref,
                    jail_protection=authority["jailProtection"],
                    render_inputs=inputs,
                ),
                assert_authority=self.lease.assert_held,
                required=self._campaign_complete,
            )
            return self._resolved_generation
        if fresh or not hasattr(self, "_resolved_generation"):
            with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "resolve-application-inputs", purpose
            ):
                self._resolved_generation = resolved_application_generation(
                    self.cli,
                    base,
                    self.paths,
                    initialize_terraform=False,
                    target_ref=(
                        self.plan.target_ref if base is not self.generation and self.plan else ""
                    ),
                )
        return self._resolved_generation

    def _campaign_application_journal(self) -> Any:
        from .deployment_jail_state import ApplicationGenerationJournal

        assert self.campaign_intent is not None
        return ApplicationGenerationJournal(
            self.paths.reports_dir / "soperator-campaign-application-generations.json",
            self.campaign_intent.digest,
        )

    def _campaign_jail_authority(self, *, before_release: bool = False) -> dict[str, Any]:
        from .soperator_full_stack_upgrade import campaign_receipt_path, load_campaign_receipt

        intent = self.campaign_intent
        if intent is None or not intent.jail_protection:
            raise RuntimeError("Campaign jail protection authority is missing")
        if before_release or "soperator-release" not in intent.segments:
            return {"intent": intent.digest, "jailProtection": intent.jail_protection}
        receipt = load_campaign_receipt(
            campaign_receipt_path(self.paths.project_dir, target_ref=intent.target_ref)
        )
        if receipt is None or receipt.intent_sha256 != intent.digest:
            raise RuntimeError("Campaign jail handoff receipt is missing")
        release = next(item for item in receipt.segments if item.name == "soperator-release")
        handoff = release.evidence.get("jailHandoff")
        if release.status != "complete" or not isinstance(handoff, Mapping):
            raise RuntimeError("Campaign has no completed release jail handoff")
        from .soperator_jail_protection import apply_frozen_jail_protection

        apply_frozen_jail_protection({}, handoff["jailProtection"])
        return {"intent": intent.digest, "releaseEvidence": release.evidence_sha256, **handoff}

    def campaign_application_generation(
        self,
        name: str,
        frozen: DeploymentGeneration,
        *,
        config_store: Any,
        assert_authority: Any,
    ) -> DeploymentGeneration:
        from .deployment_resolution import project_jail_generation

        assert self.campaign_intent is not None
        authority = self._campaign_jail_authority(before_release=name == "retire")
        inputs = frozen.manifest.get("render", {}).get("application_inputs")
        if not isinstance(inputs, Mapping):
            raise RuntimeError("Admitted application render inputs are missing")
        return self._campaign_application_journal().seal(
            name,
            binding={"generation": frozen.identity, "authority": authority},
            build=lambda: project_jail_generation(
                self.cli,
                frozen,
                self.paths,
                target_ref=self.campaign_intent.target_ref,
                jail_protection=authority["jailProtection"],
                render_inputs=inputs,
            ),
            assert_authority=assert_authority,
            required=config_store.get(f"deployment:{name}") is not None,
        )

    def _preflight_install_application_inputs(self) -> None:
        """Reject unfrozen render drift before resuming infrastructure execution."""
        from .deployment_install_repair import (
            install_input_transition,
            unbound_application_inputs_match,
        )

        journal = self._application_journal()
        unbound = [ref for ref in journal.payload["selected"] if not journal.entry(ref)]
        if unbound:
            from .deployment_plan import soperator_target
            from .deployment_resolution import resolved_application_generation

            assert self.generation is not None
            owner = soperator_target(self.generation.manifest.get("runtime_config", {}))
            if owner and owner[0] in unbound:
                ref = owner[0]
                # Replay with the admitted inputs, including pre-Terraform placeholders.
                # Reading current outputs here could block an unfinished infrastructure stage.
                with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                    "recovery-inputs", "Validate frozen application inputs before recovery"
                ):
                    replay = resolved_application_generation(
                        self.cli,
                        self.generation,
                        self.paths,
                        initialize_terraform=False,
                        target_ref=ref,
                        render_inputs=self.generation.manifest.get("render", {}).get(
                            "application_inputs", {}
                        ),
                    )
                if not unbound_application_inputs_match(self.generation, replay, ref):
                    raise RuntimeError(
                        "Frozen Soperator application bundle changed before its first application binding"
                    )
        executing = [
            ref
            for ref in journal.payload["selected"]
            if journal.entry(ref).get("status") == "executing"
        ]
        if not executing:
            return
        current = DeploymentGeneration.capture(self.paths, self.manifest)
        desired = self._desired_application_generation(
            fresh=True, purpose="Validate recovered application inputs"
        )
        for ref in executing:
            install_input_transition(
                self, current=current, desired=desired, target_ref=ref, entry=journal.entry(ref)
            )

    def _prepare_install_application_inputs(self) -> None:
        from .deployment_applications import target_bundle_digest
        from .deployment_install_repair import install_input_transition

        self.lease.assert_held()
        current = DeploymentGeneration.capture(self.paths, self.manifest)
        desired = self._desired_application_generation(
            fresh=True, purpose="Resolve application inputs for execution"
        )
        journal = self._application_journal()
        transitions = {}
        for ref in journal.payload["selected"]:
            transition = install_input_transition(
                self, current=current, desired=desired, target_ref=ref, entry=journal.entry(ref)
            )
            if transition is not None:
                transitions[ref] = transition
        if transitions:
            for ref in journal.payload["selected"]:
                if ref not in transitions and target_bundle_digest(
                    current, ref
                ) != target_bundle_digest(desired, ref):
                    raise RuntimeError(
                        "Install repair cannot include another target's render drift"
                    )
            # Keep authenticated predecessor bytes until the cluster owner seals
            # the repair. No application effect may consume unadmitted new bytes.
            self._install_input_transitions = transitions
        else:
            with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "refresh-flux-inputs", "Refresh Flux manifests from Terraform outputs"
            ):
                from .deployment_resolution import publish_application_inputs

                # Resolve once from frozen inputs. A second render from the
                # restored execution config can diverge from admitted bytes.
                self.manifest = publish_application_inputs(
                    self.cli,
                    desired,
                    self.paths,
                    current=current,
                    selected=journal.payload["selected"],
                    assert_authority=self.lease.assert_held,
                )
                refreshed = DeploymentGeneration.capture(self.paths, self.manifest)
                for ref in journal.payload["selected"]:
                    if target_bundle_digest(refreshed, ref) != target_bundle_digest(desired, ref):
                        raise RuntimeError(
                            "Refreshed application files differ from the admitted generation"
                        )

    def _admit_install_application_inputs(
        self,
        ref: str,
        identity: Mapping[str, str],
        kube_env: Mapping[str, str],
        assert_authority: Any,
    ) -> None:
        from .deployment_install_repair import admit_storage_input_transition

        admit_storage_input_transition(self, ref, identity, kube_env, assert_authority)

    def prepare_campaign_applications(
        self, *, kube_env: Mapping[str, str], assert_authority: Any
    ) -> None:
        from .deployment_target import apply_ordinary_bundle, prepare_application_runtime

        assert self.plan is not None
        assert_authority()
        desired = self._desired_application_generation(fresh=True)
        staged = self.cli.staged_generated_paths(self.paths)
        staged.generated_dir.rmdir()
        try:
            # Prerequisites consume resolved outputs without rewriting the parent's
            # sealed bundle. The release child owns transactional publication.
            manifest = desired.materialize(staged)
            target = self.cli._resolve_selected_deploy_targets(
                manifest, requested_target_ref=self.plan.target_ref, all_targets=False
            )[0]
            target_paths = self.cli._paths_for_target_flux_dir(staged, target)
            frozen_config = runtime_config_from_manifest(manifest)
            prepare_application_runtime(
                self.cli,
                frozen_config,
                target_paths,
                target_ref=self.plan.target_ref,
                kube_env=kube_env,
                assert_authority=assert_authority,
                soperator_owned=True,
            )
            apply_ordinary_bundle(
                self.cli,
                target_paths,
                kube_env=kube_env,
                assert_authority=assert_authority,
                config=frozen_config,
                target_ref=self.plan.target_ref,
            )
            from .grafana_cluster import replay_dashboards

            with self.cli.app_mutation_scope(assert_authority):
                replay_dashboards(frozen_config, staged, target=self.plan.target_ref, env=kube_env)
        finally:
            self.cli.reset_generated_bundle(staged)

    def verify_soperator_desired(self, *, kube_env: Mapping[str, str]) -> Mapping[str, Any]:
        assert self.plan is not None and self.generation is not None
        from .deployment_observation import verify_desired_target

        target = self.cli._resolve_selected_deploy_targets(
            self.manifest,
            requested_target_ref=self.plan.target_ref,
            all_targets=False,
        )[0]
        target_paths = self.cli._paths_for_target_flux_dir(self.paths, target)
        self.cli.wait_for_soperator_release_graph(
            target_paths,
            extra_env=dict(kube_env),
            readiness_policy=self._readiness_contract(target_paths),
        )
        proof = verify_desired_target(
            self.cli,
            generation=self._desired_application_generation(),
            target_ref=self.plan.target_ref,
            kube_env=kube_env,
            previous=self.source_generation,
            documents_projection=getattr(self, "_checks_documents_projection", None),
        )
        from .grafana_cluster import replay_dashboards

        if not replay_dashboards(
            self.config, self.paths, target=self.plan.target_ref, env=kube_env, verify_only=True
        ):
            raise RuntimeError("Declared Grafana dashboards have not converged")
        return proof

    def _readiness_contract(self, target_paths: ProjectPaths) -> Mapping[str, Any]:
        assert self.plan is not None
        snapshot = self.cli.load_soperator_release_snapshot(
            self.cli.soperator_release_snapshot_path(self.paths.reports_dir, self.plan.target_ref)
        )
        source = self.cli.ensure_soperator_release_source(snapshot)
        values = self.cli._rendered_soperator_upstream_values(target_paths.flux_dir)
        policy = self.cli.compile_checks_policy(
            Path(source.source_dir),
            values,
        )
        if self.plan.action is DeploymentAction.NOOP and not self._noop_profile_summary_printed:
            from .soperator_deployment_profile import deployment_profile_summary

            for line in deployment_profile_summary(
                values, target=self.plan.target_ref, stage="deployment", policy=policy
            ):
                self.cli.console.print(escape(line))
            self._noop_profile_summary_printed = True
        return policy.readiness_contract()

    def _release(self, *, dry_run: bool, campaign: tuple[Any, Any, Any] | None = None) -> None:
        from .soperator_full_stack_upgrade import CampaignNativeTransitionStore

        assert self.plan is not None
        assert self.generation is not None
        target, _, onboarded = self.cli._resolve_soperator_command_target(
            to_plain_data(self.config),
            target_ref=self.plan.target_ref,
            interactive=False,
        )
        # Recovery can overlay the policy's postimage into self.config. The
        # immutable rendered deployment retains the original proposal inputs.
        frozen_chart = self.cli._source_helm_chart_row(
            self.generation.manifest["runtime_config"], target
        )
        checks_policy_proposal = self.cli.freeze_checks_proposal(frozen_chart.get("values") or {})
        with use_generation_soperator_releases(self.generation, emit=lambda _: None) as snapshots:
            snapshot = snapshots.get(self.plan.target_ref)
            if snapshot is None:
                raise ValueError("Deployment release target is absent from frozen generation")
            prepared_release = self.cli._run_common_soperator_release_upgrade(
                config_path=self.paths.config_path,
                # The private execution config is restored with the admission
                # checkpoint. Runtime manifests also contain generated app
                # defaults, which must never become new authored recovery inputs.
                source_payload=self.cli._load_source_payload(self.paths.config_path),
                generated_context=(self.config, self.paths, self.manifest),
                target=target,
                ownership="onboarded" if onboarded else "managed",
                target_selector=self.plan.target_release,
                target_snapshot_sha256=snapshot.snapshot_sha256,
                desired_state_changed=bool(self.plan.changed_fields),
                prepared=getattr(self, "_prepared_release", None),
                admission_preview=dry_run and not self.options.dry_run,
                print_plan=self._print_release_plan,
                checks_policy_proposal=checks_policy_proposal,
                dry_run=dry_run,
                job_policy=self.options.job_policy,
                cancel_job_ids=self.options.cancel_job_ids,
                requeue_job_ids=self.options.requeue_job_ids,
                job_wait_timeout=self.options.job_wait_timeout,
                job_refresh_interval=self.options.job_refresh_interval,
                interactive=False,
                **(
                    {
                        "supervise": False,
                        "external_scheduling_evidence": {
                            "mode": "parent-campaign",
                            "campaignIntentSha256": campaign[0].digest,
                            "maintenanceOwner": "soperator-upgrade",
                            "requiresFreshChecks": campaign[0].requires_fresh_checks,
                            "reservationName": self.cli._soperator_upgrade_maintenance_reservation_name(
                                campaign[0].digest.removeprefix("sha256:")[:16]
                            ),
                        },
                        "config_transition_store": campaign[1],
                        "external_native_transition_store": CampaignNativeTransitionStore(
                            path=campaign[1].path, intent=campaign[0]
                        ),
                        "config_transition_owner": "soperator-upgrade",
                        "config_transition_stage": "deployment:final-release",
                        "assert_parent_authority": campaign[2],
                    }
                    if campaign
                    else {}
                ),
            )

            if dry_run:
                self._prepared_release = prepared_release

    def execute(self, admission: StageAdmission, *, recovering: bool) -> None:
        assert self.plan is not None
        self.cli.progress_console.print("Deploy: Deploy")
        if admission.stage.name is DeploymentStageKind.APPLICATIONS:
            self._execute_applications(admission)
            return
        if self.campaign_intent is not None:
            self._execute_coordinated_stage(admission, recovering=recovering)
            return
        plan_file, refreshed = self._terraform_plan(
            purpose="Refresh admitted changes before execution"
        )
        assert_stage_plan(admission.terraform, refreshed)
        self.lease.assert_held()
        release_complete = False
        if self.plan.target_ref and self.plan.action not in {
            DeploymentAction.INSTALL,
            DeploymentAction.NOOP,
        }:
            self._release(dry_run=False)
            release_complete = True
        if release_complete and terraform_changes(refreshed):
            # Release publication may change the materialized root or consume
            # time. Never apply the pre-release binary after that boundary.
            plan_file, refreshed = self._terraform_plan(
                purpose="Refresh infrastructure after release reconciliation"
            )
            assert_stage_plan(admission.terraform, refreshed)
            self.lease.assert_held()
        self.summary = self.cli._deploy_generated_artifacts(
            self.config,
            self.paths,
            self.manifest,
            skip_validations=self.options.skip_validations,
            skip_validation_kinds=set(self.options.skip_validation_kinds),
            requested_target_ref=self.options.target_ref,
            all_targets=self.options.all_targets,
            job_policy=self.options.job_policy,
            cancel_job_ids=self.options.cancel_job_ids,
            requeue_job_ids=self.options.requeue_job_ids,
            job_wait_timeout_seconds=self.cli._soperator_upgrade_duration_seconds(
                self.options.job_wait_timeout, option_name="--job-wait-timeout"
            ),
            job_refresh_interval_seconds=self.cli._soperator_upgrade_duration_seconds(
                self.options.job_refresh_interval, option_name="--job-refresh-interval"
            ),
            terraform_plan_file=plan_file,
            expected_terraform_plan_sha256=self.cli._sha256_file(plan_file) if plan_file else "",
            skip_terraform_apply=not terraform_changes(refreshed),
            preflight_runtime_env=self.runtime_env,
            deployment_lease=self.lease,
            expected_identities=self._bound_identities(),
            on_target_identity=self._bind_application_identity,
            prepare_application_inputs=self._prepare_install_application_inputs,
            on_target_inputs=self._admit_install_application_inputs,
            soperator_plan=self.plan,
            soperator_release_complete=release_complete,
            soperator_install_approval_fingerprint=self.lease.operation_id if self.lease else "",
            report_paths=self.report_paths,
        )
        self._applied.add(admission.stage.name.value)
        if self.summary and self.summary.cluster_identities:
            self.identities.update(self.summary.cluster_identities)

    def verify(self, admission: StageAdmission) -> Mapping[str, Any] | None:
        # A finished checkpoint is not itself proof. Terraform and the application
        # validators must both observe the intended postconditions on this runner.
        if admission.stage.name is DeploymentStageKind.APPLICATIONS:
            proofs = {
                ref: self._verify_application(ref) for ref in admission.application["targets"]
            }
            return proofs if all(value is not None for value in proofs.values()) else None
        if self.campaign_intent is not None:
            if not self._campaign_complete:
                return None
            return {
                "campaign": self.campaign_intent.digest,
                "stage": admission.stage.name.value,
                "finalPostconditions": "verified",
            }
        if admission.stage.name.value not in self._applied:
            return None
        _, observed = self._terraform_plan(purpose="Verify executed stage convergence")
        if terraform_changes(observed):
            return None
        return {"terraform": "converged", "application": admission.application}

    def verify_final(self, plan: DeploymentPlan) -> Mapping[str, Any]:
        self.cli.progress_console.print("Deploy: Verify")
        self._desired_application_generation(fresh=True, purpose="Verify final application inputs")
        progress = SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy")
        self._verifying_final = True
        try:
            with progress.phase("final-observation", "Verify final live cluster state"):
                release, _, identities = self.observe(None)
        finally:
            self._verifying_final = False
        if plan.target_ref and release != plan.target_release:
            raise RuntimeError("Final live Soperator release differs from the rendered target")
        _, observed = self._terraform_plan(purpose="Verify final deployment convergence")
        if terraform_changes(observed):
            raise RuntimeError("Final Terraform state has not converged to the rendered intent")
        assert self.generation is not None
        targets = {}
        proof: Mapping[str, Any] | None
        for ref in plan.selected_targets:
            with progress.phase(f"final-target-{ref}", f"Verify final acceptance for {ref}"):
                if ref == plan.target_ref:
                    proof = self._soperator_target_evidence(ref, identities)
                else:
                    proof = self._verify_application(ref, fresh=True)
                    if proof is None:
                        raise RuntimeError(f"Selected application target is incomplete: {ref}")
            targets[ref] = {**proof, "generation": self.generation.identity}
        if self.summary is None:
            self.summary = self.cli.DeployRunSummary(
                cluster_identities={**self.identities, **identities}
            )
        return {
            "selectedTargets": list(plan.selected_targets),
            "targets": targets,
            "identities": {**self.identities, **identities},
            "release": release,
            "ready": True,
            "ownedSettings": self.owned_settings,
            "compatibility": self.compatibility_report,
        }

    def acceptance_generations(self) -> tuple[DeploymentGeneration, ...]:
        if self.plan is not None and self.plan.target_ref in self.plan.selected_targets:
            return (self._desired_application_generation(),)
        return ()

    def _campaign_kwargs(self) -> dict[str, Any]:
        assert self.plan is not None and self.generation is not None
        from .deployment_plan import node_groups
        from .mk8s_upgrade import (
            DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
            DISRUPTION_POLICY_FORCE_DELETE,
            find_source_mk8s_component,
        )

        desired = (self.desired_generation or self.generation).manifest["runtime_config"]
        from .deployment_jail_state import jail_values
        from .soperator_jail_protection import freeze_jail_protection, protect_jail_directories
        from .soperator_registration import soperator_registration_target

        external = soperator_registration_target(desired, target_ref=self.plan.target_ref)
        desired_protection = freeze_jail_protection(
            protect_jail_directories(
                jail_values(desired, self.plan.target_ref),
                paths=[],
                layout="external"
                if external and external.get("kind") == "external-mk8s"
                else "managed",
                target_ref=self.plan.target_ref,
            )
        )
        if external and external.get("kind") == "external-mk8s":
            platform = external.get("soperator_desired_platform", {})
            version = str(platform.get("kubernetes_version") or "")
            groups = platform.get("node_groups", {})
            remaining = set(groups)
            if not version:
                if self.campaign_intent is not None:
                    version = self.campaign_intent.requested_kubernetes_selector
                else:
                    # Chart-only transitions keep the authoritative live minor;
                    # registration inventory contains identities, not versions.
                    cluster_id = str(external.get("cluster_id") or "")
                    project_id = str(self.config.client_info.nebius.project_id)
                    sdk = self.cli.init_nebius_sdk(
                        parent_id=project_id,
                        context="Deployment platform admission",
                        prefer_operator_auth=True,
                    )
                    try:
                        cluster = self.cli.Mk8sKubernetesVersionExecutor(sdk).get_cluster(
                            cluster_id
                        )
                        if (
                            str(cluster.metadata.id) != cluster_id
                            or str(cluster.metadata.parent_id) != project_id
                        ):
                            raise RuntimeError("External deployment platform ownership changed")
                        version = self.cli._cluster_control_plane_minor_version(
                            cluster, cluster_id=cluster_id
                        )
                    finally:
                        sdk.sync_close()
        else:
            component = find_source_mk8s_component(desired, self.plan.target_ref)
            cluster = component.get("inputs", {}).get("cluster", {})
            groups = node_groups(desired, self.plan.target_ref)
            remaining = set(self.hooks.source_groups)
            for stage in self.plan.stages:
                if stage.name is DeploymentStageKind.RETIRE:
                    remaining = set(node_groups(stage.config, self.plan.target_ref))
            version = str(cluster.get("k8s_version") or cluster.get("version") or "")
        if not version:
            raise RuntimeError("Rendered deployment has no exact Kubernetes version")
        rollout: Mapping[str, Any] = next(
            (
                row.get("soperator_rollout", {})
                for row in desired.get("deploy", {}).get("targets", [])
                if row.get("instance_id") == self.plan.target_ref
            ),
            {},
        )
        max_surge_count = rollout.get("max_surge_count")
        # Configuration stores the resolved count; the campaign accepts an
        # explicit surge selector only for safe-surge. Keep invalid values for
        # admission to reject, including bools that compare equal to zero.
        if (
            rollout.get("strategy")
            in {DISRUPTION_POLICY_ALLOW_UNAVAILABLE, DISRUPTION_POLICY_FORCE_DELETE}
            and isinstance(max_surge_count, int)
            and not isinstance(max_surge_count, bool)
            and max_surge_count == 0
        ):
            max_surge_count = None
        return {
            "node_group_strategy": rollout.get("strategy"),
            "strategy_max_surge_count": max_surge_count,
            "drain_timeout": rollout.get("drain_timeout"),
            "config_path": self.paths.config_path,
            "target_ref": self.plan.target_ref,
            "to_chart_version": self.plan.target_release,
            "to_k8s_version": version,
            "to_os": "keep",
            "to_gpu_stack_preset": "keep",
            "node_group_os": tuple(
                f"{key}={groups[key]['os']}"
                for key in sorted(remaining)
                if key in groups and groups[key].get("os")
            ),
            "node_group_gpu_stack_preset": tuple(
                f"{key}={groups[key]['gpu_stack_preset']}"
                for key in sorted(remaining)
                if key in groups and groups[key].get("gpu_stack_preset")
            ),
            "job_policy": self.options.job_policy,
            "cancel_job": self.options.cancel_job_ids,
            "requeue_job": self.options.requeue_job_ids,
            "job_wait_timeout": self.options.job_wait_timeout,
            "job_refresh_interval": self.options.job_refresh_interval,
            "interactive": False,
            "generated_context": (self.config, self.paths, self.manifest),
            "assert_parent_fence": self.lease.assert_held if self.lease else None,
            "deployment_hooks": self.hooks,
            "desired_jail_protection": desired_protection,
        }

    def _admit_coordinated_stages(self, plan: DeploymentPlan) -> tuple[StageAdmission, ...]:
        from .deployment_campaign import ApplicationCampaignHooks, TerraformCampaignHooks
        from .soperator_full_stack_upgrade import campaign_intent_from_payload

        assert self.generation is not None
        observed_source = getattr(self, "observed_source", None)
        if observed_source is not None:
            with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "source-admission", "Render observed Soperator source"
            ):
                rendered_source = self.cli._render_soperator_upgrade_admission(
                    source_payload=to_dynamic_payload(observed_source),
                    config_path=self.paths.config_path,
                    paths=self.paths,
                    require_soperator_flux=False,
                    preserve_ordinary=False,
                )
            try:
                source_executor = _CliDeploymentExecutor(
                    rendered_source.admitted_config,
                    rendered_source.staged_paths,
                    self.cli.load_generated_manifest(rendered_source.staged_paths.generated_dir),
                    options=self.options,
                    lease=self.lease,
                )
                source_executor.preflight()
                _, source_plan = source_executor._terraform_plan(
                    purpose="Verify observed source infrastructure"
                )
                modules = {
                    name
                    for name, value in self.cli._generated_bundle_mk8s_module_index(
                        source_executor.manifest
                    ).items()
                    if value[1] == plan.target_ref
                }
                if any(
                    any(
                        str(row.get("address", "")).startswith(f"module.{name}.")
                        for name in modules
                    )
                    for row in terraform_changes(source_plan)
                ):
                    raise RuntimeError(
                        "Observed source projection differs from current infrastructure; no changes were applied"
                    )
                self.source_generation = DeploymentGeneration.capture(
                    rendered_source.staged_paths,
                    self.cli.load_generated_manifest(rendered_source.staged_paths.generated_dir),
                )
            finally:
                rendered_source.cleanup()
        if self.source_generation is None:
            raise RuntimeError(
                "Coordinated deployment requires complete current source observations"
            )
        if any(
            row.get("id") == "mk8s" and row.get("instance_id") == plan.target_ref
            for row in self.source_generation.manifest["runtime_config"]
            .get("infra", {})
            .get("components", [])
        ):
            self.hooks = TerraformCampaignHooks(
                self,
                source=self.source_generation.manifest["runtime_config"],
                generation=self.generation,
            )
        else:
            self.hooks = ApplicationCampaignHooks(
                self,
                source=self.source_generation.manifest["runtime_config"],
                generation=self.generation,
            )
        from .deployment_dependencies import (
            admit_recreations,
            prior_deletions,
            recreation_candidates,
        )

        admissions: list[StageAdmission] = []
        deleted: list[Any] = []
        for stage in plan.stages:
            if stage.name is DeploymentStageKind.APPLICATIONS:
                admissions.append(
                    StageAdmission(
                        stage,
                        {"resource_changes": []},
                        {
                            "generation": self.generation.as_payload(),
                            "generationId": self.generation.identity,
                            "targets": [
                                ref for ref in plan.selected_targets if ref != plan.target_ref
                            ],
                        },
                    )
                )
                continue
            with SoperatorUpgradeProgress(self.cli.progress_console, prefix="Deploy").phase(
                "stage-admission", f"Render {stage.name.value} admission manifests"
            ):
                rendered = self.cli._render_soperator_upgrade_admission(
                    source_payload=to_dynamic_payload(stage.config),
                    config_path=self.paths.config_path,
                    paths=self.paths,
                    require_soperator_flux=False,
                    preserve_ordinary=False,
                )
            try:
                staged_manifest = self.cli.load_generated_manifest(
                    rendered.staged_paths.generated_dir
                )
                frozen = DeploymentGeneration.capture(rendered.staged_paths, staged_manifest)
                staged = _CliDeploymentExecutor(
                    rendered.admitted_config,
                    rendered.staged_paths,
                    frozen.manifest_for_paths(rendered.staged_paths),
                    options=self.options,
                    lease=self.lease,
                )
                staged.preflight()
                _, inventory = staged._terraform_plan(
                    purpose=f"Admit {stage.name.value} stage", include_noop=True
                )
                prerequisites = recreation_candidates(inventory, deleted)
                terraform = terraform_admission(inventory)
                if prerequisites:
                    _, diagnostic = staged._terraform_plan(
                        purpose=f"Check {stage.name.value} replacement dependencies",
                        replace_addresses=tuple(item.address for item in prerequisites),
                    )
                    terraform = admit_recreations(diagnostic, terraform, prerequisites)
                known_deleted = {item.address for item in deleted}
                deleted.extend(
                    item
                    for item in prior_deletions(stage.name.value, inventory)
                    if item.address not in known_deleted
                )
                assert_protected_terraform_scope(terraform, target_ref=plan.target_ref)
                admissions.append(
                    StageAdmission(
                        stage,
                        terraform,
                        {
                            "generation": frozen.as_payload(),
                            "generationId": frozen.identity,
                            "desired": digest(stage.config),
                            "compatibilityAdmission": staged.compatibility_report,
                        },
                        prerequisites,
                    )
                )
            finally:
                rendered.cleanup()
        if self.hooks is not None:
            self.hooks.admissions = {item.stage.name.value: item for item in admissions}
        # Campaign starts at the accepted source, entirely inside this private cache.
        self.cli.reset_generated_bundle(self.paths)
        self.manifest = self.source_generation.materialize(self.paths)
        self.config = runtime_config_from_manifest(self.manifest)
        self.paths.config_path.write_text(
            self.cli.render_updated_source_payload(to_dynamic_payload(to_plain_data(self.config)))
        )
        self.paths.config_path.chmod(0o600)
        self.preflight()
        intent = self.cli._run_soperator_upgrade_campaign(**self._campaign_kwargs(), dry_run=True)
        if intent is None:
            raise RuntimeError("Coordinated deployment did not produce an admitted campaign")
        from .compatibility_transitions import assess_upgrade_states

        compatibility = assess_upgrade_states(
            self.source_generation.manifest, self.generation.manifest, intent
        )
        # Round-trip the actual schema before persisting execution authority.
        self.campaign_intent = campaign_intent_from_payload(asdict(intent))
        if self.hooks is not None:
            self.hooks.intent = self.campaign_intent
        first = admissions[0]
        admissions[0] = replace(
            first,
            application={
                **first.application,
                "campaign": asdict(intent),
                "compatibility": compatibility,
                "sourceGenerationId": self.source_generation.identity,
                "sourceCompatibility": self.source_generation.manifest.get("render", {}).get(
                    "compatibility", {}
                ),
            },
        )
        if self.hooks is not None:
            self.hooks.admissions = {item.stage.name.value: item for item in admissions}
        return tuple(admissions)

    def _authored_compatibility_replay(self) -> Mapping[str, Any]:
        """Fresh authored proof, separate from restored effective preflight.

        Output hydration changes artifact subjects. Only an identical Terraform
        root may supply its native evidence to this source-artifact replay.
        """
        assert self.generation is not None
        if (
            DeploymentGeneration.capture(self.paths, self.manifest).files == self.generation.files
            and to_plain_data(self.config) == self.generation.manifest["runtime_config"]
            and self.manifest.get("render", {}).get("compatibility")
            == self.generation.manifest.get("render", {}).get("compatibility")
        ):
            return self.compatibility_report
        from .compatibility_artifacts import bind_flux_artifacts

        with tempfile.TemporaryDirectory(prefix="cxcli-compatibility-replay-") as directory:
            authored = _execution_paths(self.paths, Path(directory))
            manifest = self.generation.materialize(authored)
            if terraform_inputs(authored.infra_dir) != terraform_inputs(self.paths.infra_dir):
                raise RuntimeError("Recovery Terraform inputs differ from the admitted generation")
            config = runtime_config_from_manifest(manifest)
            original_env = self.cli._terraform_runtime_env(config)
            effective_env = self.cli._terraform_runtime_env(self.config)
            variable_keys = {
                key
                for key in original_env.keys() | effective_env.keys()
                if key.startswith(("TF_VAR_", "TF_CLI_ARGS")) or key == "TF_WORKSPACE"
            }
            if any(original_env.get(key) != effective_env.get(key) for key in variable_keys):
                raise RuntimeError(
                    "Recovery Terraform variables differ from the admitted generation"
                )
            frozen = manifest.get("render", {}).get("compatibility", {})
            bind_flux_artifacts(authored, frozen.get("chart_inputs", {}))
            # Artifact paths remain authored; only the proven-identical native
            # Terraform evidence comes from the initialized execution root.
            replay_paths = replace(authored, infra_dir=self.paths.infra_dir)
            with use_generation_soperator_releases(self.generation, emit=lambda _: None):
                return self.cli.admit_compatibility(
                    config,
                    replay_paths,
                    frozen,
                    terraform_validated=self.cli._config_has_enabled_infra_components(config)
                    or bool(self.cli._required_runtime_component_output_specs(config)),
                )

    def restore_admissions(self, admissions: Sequence[StageAdmission]) -> None:
        from .deployment_campaign import ApplicationCampaignHooks, TerraformCampaignHooks
        from .soperator_full_stack_upgrade import campaign_intent_from_payload

        if not any(item.stage.name is not DeploymentStageKind.RECONCILE for item in admissions):
            from .compatibility_execution import verify_constraint_replay

            verify_constraint_replay(
                admissions[0].application["compatibilityAdmission"],
                self._authored_compatibility_replay(),
            )
            self._preflight_install_application_inputs()
            return
        if self.source_generation is None or self.generation is None:
            raise RuntimeError("Recovery has no authoritative source generation")
        self.campaign_intent = campaign_intent_from_payload(admissions[0].application["campaign"])
        source_compatibility = admissions[0].application.get("sourceCompatibility")
        if not isinstance(source_compatibility, Mapping):
            raise RuntimeError("Recovery has no sealed accepted-source compatibility")
        self.source_generation = replace(
            self.source_generation,
            manifest={
                **self.source_generation.manifest,
                "render": {
                    **self.source_generation.manifest.get("render", {}),
                    "compatibility": source_compatibility,
                },
            },
        )
        if self.source_generation.identity != admissions[0].application.get("sourceGenerationId"):
            raise RuntimeError("Recovery accepted source differs from its sealed generation")
        from .compatibility_transitions import assess_upgrade_states

        if admissions[0].application.get("compatibility") != assess_upgrade_states(
            self.source_generation.manifest, self.generation.manifest, self.campaign_intent
        ):
            raise RuntimeError("Frozen upgrade compatibility assessment changed")
        if self.campaign_intent.deployment:
            hook_type = (
                TerraformCampaignHooks
                if self.campaign_intent.ownership == "managed"
                else ApplicationCampaignHooks
            )
            self.hooks = hook_type(
                self,
                source=self.source_generation.manifest["runtime_config"],
                generation=self.generation,
            )
            self.hooks.admissions = {item.stage.name.value: item for item in admissions}
            self.hooks.intent = self.campaign_intent
            self.hooks._contract = dict(self.campaign_intent.deployment)

    def _application_journal(self) -> Any:
        from .deployment_applications import ApplicationJournal

        assert self.generation is not None and self.plan is not None
        return ApplicationJournal(
            self.paths.reports_dir / "deployment-applications.json",
            generation=self.generation.identity,
            selected=self.plan.selected_targets,
        )

    def _bound_identities(self) -> dict[str, Any]:
        identities = dict(self.accepted_evidence.get("identities", {}))
        identities.update(self.identities)
        identities.update(
            {
                ref: value["identity"]
                for ref, value in self._application_journal().payload["targets"].items()
            }
        )
        return identities

    def _bind_application_identity(self, ref: str, identity: Mapping[str, str]) -> None:
        from .deployment_applications import target_bundle_digest

        if self.lease is None:
            raise RuntimeError(
                "Application identity publication requires local execution ownership"
            )
        self.lease.assert_held()
        prior = self._bound_identities().get(ref)
        if prior and prior != dict(identity):
            raise RuntimeError("Application immutable identity differs from accepted ownership")
        self._application_journal().bind(
            ref, identity, target_bundle_digest(self._desired_application_generation(), ref)
        )
        self.identities[ref] = dict(identity)

    def _selected_target(self, ref: str) -> Mapping[str, Any]:
        return self.cli._resolve_selected_deploy_targets(
            self.manifest, requested_target_ref=ref, all_targets=False
        )[0]

    def _verify_application(self, ref: str, *, fresh: bool = False) -> Mapping[str, Any] | None:
        from .deployment_applications import target_bundle_digest
        from .deployment_observation import DesiredStateNotConverged, verify_desired_target

        if ref in self._verified_targets and not fresh:
            return self._verified_targets[ref]
        journal = self._application_journal()
        entry = journal.entry(ref)
        desired = target_bundle_digest(self._desired_application_generation(), ref)
        accepted = self.accepted_evidence.get("targets", {}).get(ref, {})
        if not entry and accepted.get("desiredBundle") != desired:
            return None
        identity = self._bound_identities().get(ref)
        if not identity:
            return None
        target = dict(self._selected_target(ref))
        target.pop("kube_context", None)
        target["cluster_id"] = identity["cluster_id"]
        with ExitStack() as stack:
            env = self.cli._prepare_cluster_handoff_kube_env(
                self.config,
                self.paths,
                stack=stack,
                target=target,
                persist_local_kubeconfig=False,
                set_current_context=False,
                allow_terraform_output=False,
                require_renewable_auth=True,
            )
            if (
                not env
                or env.get(self.cli.GRAFANA_TARGET_CLUSTER_ID_ENV) != identity["cluster_id"]
                or self.cli._read_kube_system_namespace_uid(
                    kube_context=str(env.get(self.cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV) or ""),
                    extra_env=env,
                )
                != identity["kubernetes_uid"]
            ):
                raise RuntimeError("Application immutable identity differs during verification")
            try:
                proof = verify_desired_target(
                    self.cli,
                    generation=self._desired_application_generation(),
                    target_ref=ref,
                    kube_env=env,
                    previous=self.target_generations.get(ref),
                )
            except DesiredStateNotConverged:
                return None
            from .grafana_cluster import replay_dashboards

            if not replay_dashboards(
                self.config, self.paths, target=ref, env=env, verify_only=True
            ):
                return None
            from .deployment_plan import soperator_target
            from .operation_completion import load_completion, verify_completion

            soperator = soperator_target(
                self._desired_application_generation().manifest["runtime_config"]
            )
            if soperator and soperator[0] == ref:
                completion = load_completion(self.paths, ref) or accepted.get("operationCompletion")
                if completion is None:
                    return None
                proof["operationCompletion"] = verify_completion(
                    completion, identity=identity, target_ref=ref, desired_bundle=desired, env=env
                )
                if proof["operationCompletion"] is None:
                    return None
            validations = self.cli._filter_validations_for_target(
                self.cli._filter_deploy_validations(
                    self.cli._manifest_deploy_validations(self.manifest),
                    skip_validations=self.options.skip_validations,
                    skip_kinds=set(self.options.skip_validation_kinds),
                ),
                target_ref=ref,
            )
            if validations and ref not in self._verified_targets:
                self.lease.assert_held()
                with self.cli.app_mutation_scope(self.lease.assert_held):
                    self.cli._run_target_deploy_validations(
                        validations,
                        target_ref=ref,
                        reports_dir=self.paths.reports_dir,
                        extra_env=env,
                    )
        self._bind_application_identity(ref, identity)
        proof = {**proof, "identity": dict(identity), "validations": "passed"}
        self._application_journal().complete(ref, proof)
        self._verified_targets[ref] = proof
        return proof

    def _soperator_target_evidence(
        self, ref: str, identities: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        from .deployment_applications import target_bundle_digest

        expected = self._bound_identities().get(ref)
        if self.campaign_intent is not None:
            expected = {
                "cluster_id": self.campaign_intent.cluster_id,
                "kubernetes_uid": self.campaign_intent.kubernetes_uid,
            }
        if not expected or identities.get(ref) != expected:
            raise RuntimeError(
                "Soperator final immutable identity differs from its bound ownership"
            )
        from .deployment_jail_state import build_jail_state_receipt

        if self._jail_storage_evidence is None or self.generation is None:
            raise RuntimeError("Accepted jail state requires fresh physical storage admission")
        effective = self._desired_application_generation()
        from .deployment_jail_state import verify_storage_handoff

        previous_storage = (
            self.accepted_evidence.get("targets", {})
            .get(ref, {})
            .get("jailState", {})
            .get("storageEvidence", {})
        )
        verify_storage_handoff(
            previous=previous_storage,
            current=self._jail_storage_evidence,
            release=self._campaign_jail_authority() if self.campaign_intent is not None else {},
        )
        jail_state = build_jail_state_receipt(
            authored=self.generation,
            effective=effective,
            target_ref=ref,
            identity=identities[ref],
            storage_evidence=self._jail_storage_evidence,
        )
        return {
            "identity": identities[ref],
            "effectiveGeneration": effective.identity,
            "jailState": jail_state,
            "desiredBundle": target_bundle_digest(effective, ref),
            "ownedSettings": self.owned_settings,
            "ready": True,
            "validations": "required-readiness-passed",
            "operationCompletion": self._verified_targets.get(ref, {}).get("operationCompletion"),
        }

    def _execute_applications(self, admission: StageAdmission) -> None:
        if not self._campaign_complete:
            raise RuntimeError(
                "Other applications require verified Soperator maintenance restoration"
            )
        validations = self.cli._filter_deploy_validations(
            self.cli._manifest_deploy_validations(self.manifest),
            skip_validations=self.options.skip_validations,
            skip_kinds=set(self.options.skip_validation_kinds),
        )
        for ref in admission.application["targets"]:
            if self._verify_application(ref) is not None:
                continue
            self.lease.assert_held()
            deploy_application_target(
                self.cli,
                self.config,
                self.paths,
                self._selected_target(ref),
                deploy_validations=validations,
                deployment_lease=self.lease,
                expected_identity=self._bound_identities().get(ref),
                on_identity=self._bind_application_identity,
            )
            if self._verify_application(ref) is None:
                raise RuntimeError(f"Application target did not reach its desired state: {ref}")

    def _execute_coordinated_stage(self, admission: StageAdmission, *, recovering: bool) -> None:
        # All topology and platform stages run once under the campaign's single
        # maintenance and restoration owner. Outer checkpoints reference its proof.
        assert self.plan is not None
        intent = self.cli._run_soperator_upgrade_campaign(
            **self._campaign_kwargs(),
            dry_run=False,
            admitted_intent=self.campaign_intent,
        )
        if intent is None:
            raise RuntimeError("Coordinated campaign returned no completion evidence")
        from .soperator_full_stack_upgrade import campaign_receipt_path, load_campaign_receipt

        receipt = load_campaign_receipt(
            campaign_receipt_path(self.paths.project_dir, target_ref=self.plan.target_ref)
        )
        if receipt is None or receipt.status != "complete" or receipt.maintenance != "restored":
            raise RuntimeError(
                "Coordinated campaign has not restored maintenance after final proof"
            )
        self.manifest = self.cli.load_generated_manifest(self.paths.generated_dir)
        self.config = runtime_config_from_manifest(self.manifest)
        self._verifying_final = True
        try:
            release, _, identities = self.observe(None)
        finally:
            self._verifying_final = False
        expected = {
            "cluster_id": self.campaign_intent.cluster_id,
            "kubernetes_uid": self.campaign_intent.kubernetes_uid,
        }
        if identities.get(self.plan.target_ref) != expected or release != self.plan.target_release:
            raise RuntimeError("Completed campaign identity or desired release has changed")
        self.identities.update(identities)
        self._campaign_complete = True


@dataclass(frozen=True)
class _HeldExecutionLease:
    settings: object
    lease: LocalExecutionOwner
    local_lock_path: Path


_HELD_EXECUTION_LEASE: ContextVar[_HeldExecutionLease | None] = ContextVar(
    "held_execution_lease", default=None
)


@contextmanager
def render_publication_lock(*, config: Any, paths: ProjectPaths) -> Iterator[None]:
    """Serialize only artifact capture/publication, independently of execution."""
    from .deployment_local import DeploymentLocalLock

    with DeploymentLocalLock(paths.project_dir.resolve() / ".nebius-cxcli" / "render.lock"):
        yield


@contextmanager
def deployment_execution(
    *,
    config: Any,
    paths: ProjectPaths,
    target_ref: str,
    operation_id: str,
    bootstrap_backend: bool = True,
    pause: Any = None,
) -> Iterator[LocalExecutionOwner]:
    from . import cli

    settings = backend_settings_from_config(config)
    held = _HELD_EXECUTION_LEASE.get()
    if held is not None:
        if held.settings != settings:
            raise RuntimeError("A nested mutation cannot change the deployment backend")
        held.lease.assert_held()
        yield held.lease
        return
    if bootstrap_backend:
        cli._ensure_terraform_backend_ready(config)
    with LocalExecutionOwner(settings=settings, operation_id=operation_id) as owner:
        token = _HELD_EXECUTION_LEASE.set(_HeldExecutionLease(settings, owner, owner.lock.path))
        try:
            yield owner
        finally:
            _HELD_EXECUTION_LEASE.reset(token)
