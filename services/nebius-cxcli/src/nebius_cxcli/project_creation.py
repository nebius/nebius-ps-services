"""Shared project-creation workflow; command adapters provide interactive services."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import typer
import yaml
from rich.console import Console

from .components import ComponentEntry, ComponentScope, soperator_install_entry
from .mk8s_gpu import (
    ensure_mk8s_gpu_app_rows,
    materialize_mk8s_gpu_app_values,
    prune_inactive_mk8s_gpu_app_rows,
    resolve_mk8s_gpu_app_selection,
)
from .mysterybox_eso import ensure_mysterybox_eso_app_rows
from .observability import (
    materialize_observability_infra_values,
    resolve_observability_app_selection,
)
from .provider_options import ProviderOptionLookup
from .quota_checks import QuotaReport
from .soperator_config_materialization import _SOPERATOR_APP_ID, _default_soperator_profile_name
from .soperator_install_policy import (
    validate_soperator_install_configuration as _validate_soperator_install_configuration,
)
from .soperator_release import SoperatorReleaseSnapshot
from .terminal_styles import warning_markup


class ScaffoldResult(Protocol):
    @property
    def deployments_root(self) -> Path: ...
    @property
    def project_path(self) -> Path: ...
    @property
    def config_path(self) -> Path: ...
    @property
    def wrote_config(self) -> bool: ...


class GitignoreResult(Protocol):
    @property
    def path(self) -> Path | None: ...
    @property
    def wrote(self) -> bool: ...
    @property
    def inside_git_repo(self) -> bool: ...


@dataclass(frozen=True)
class ProjectCreationServices:
    """Explicit seams for the existing composition-root wizard and I/O helpers."""

    provider_lookup: Callable[[], ProviderOptionLookup]
    soperator_required_infra_ids: tuple[str, ...]
    wizard_back_requested: type[Exception]
    wizard_quit_requested: type[Exception]
    active_chart_count: Callable[..., int]
    align_new_infra_instance_ids_with_resource_names: Callable[..., dict[str, str]]
    app_chart_ids_with_non_catalog_versions: Callable[..., set[str]]
    app_selection_without_cluster_target_issue: Callable[..., str | None]
    apply_soperator_profile_to_payload: Callable[..., None]
    apply_vpc_id_overrides: Callable[..., None]
    apply_vpc_ref_overrides: Callable[..., None]
    assert_not_nested_deployments_root: Callable[..., None]
    client_name_or_prompt: Callable[..., str]
    command_status: Callable[..., AbstractContextManager[None]]
    config_uses_private_cluster_handoff: Callable[..., bool]
    confirm_existing_project_overwrite: Callable[..., bool]
    dependency_seed_payload: Callable[..., dict[str, Any] | None]
    enabled_ids_from_runtime_payload: Callable[..., set[str]]
    ensure_deployments_gitignore: Callable[..., GitignoreResult]
    ensure_mysterybox_eso_app_dependency_selection: Callable[..., tuple[set[str], tuple[str, ...]]]
    ensure_nfs_csi_app_dependency_selection: Callable[..., tuple[set[str], tuple[str, ...]]]
    ensure_payload_contains_component_rows: Callable[..., None]
    ensure_project_auth_identity: Callable[..., None]
    expand_soperator_app_selection: Callable[..., set[str]]
    expand_soperator_component_selection: Callable[..., set[str]]
    identity_values_from_payload: Callable[..., tuple[str, str, str, str, str | None]]
    materialize_create_soperator_component_defaults: Callable[..., bool]
    materialize_mk8s_image_defaults: Callable[..., None]
    materialize_planned_vpc_binding_tokens: Callable[..., None]
    materialize_singleton_provider_defaults: Callable[..., None]
    materialize_soperator_child_chart_secret_dependencies: Callable[
        ..., tuple[set[str], tuple[str, ...]]
    ]
    materialize_vm_image_defaults: Callable[..., None]
    normalize_component_dependencies: Callable[..., tuple[set[str], set[str]]]
    optional_email_or_prompt: Callable[..., str | None]
    print_component_selection_summary: Callable[..., None]
    print_create_next_steps: Callable[..., None]
    print_incomplete_wizard_no_write_warning: Callable[..., None]
    print_mk8s_gpu_validation_warnings: Callable[..., None]
    print_mysterybox_eso_app_dependency_adjustment: Callable[..., None]
    print_nfs_csi_app_dependency_adjustment: Callable[..., None]
    print_quota_remediation_hint: Callable[..., None]
    print_soperator_selection_adjustments: Callable[..., None]
    private_cluster_handoff_note: Callable[..., str]
    project_config_path: Callable[..., Path]
    prompt_soperator_profile: Callable[..., str]
    prune_mk8s_node_group_defaults_without_soperator: Callable[..., None]
    prune_redundant_app_chart_default_values: Callable[..., None]
    refresh_soperator_registration_fingerprints: Callable[..., None]
    region_or_prompt: Callable[..., str]
    require_vpc_networking_for_noninteractive: Callable[..., None]
    resolve_component_ids: Callable[..., set[str]]
    resolve_create_target_folders: Callable[..., tuple[str, str]]
    resolve_deployments_root: Callable[..., Path]
    run_component_field_wizard: Callable[..., tuple[str, bool]]
    run_runtime_validation: Callable[..., None]
    scaffold_instance: Callable[..., ScaffoldResult]
    selected_cluster_target_component_ids: Callable[..., set[str]]
    starter_component_payload: Callable[..., dict[str, Any]]
    validate_component_sources_or_raise: Callable[..., None]
    validate_deployments_root_target: Callable[..., None]
    validate_final_enabled_app_sources_or_raise: Callable[..., set[str]]
    validate_requested_app_chart_versions_or_raise: Callable[..., None]
    validate_tenant_project_ids_or_prompt: Callable[..., tuple[str, str]]
    value_or_prompt: Callable[..., str]
    warn_existing_project_overwrite: Callable[..., None]
    warn_on_live_quota_issues: Callable[..., QuotaReport]
    with_infra_provider_groups: Callable[..., tuple[ComponentEntry, ...]]
    wizard_continue_phase: Callable[..., object]
    wizard_followup_required_field_issues: Callable[..., list[str]]
    wizard_phase_stop_requested: Callable[..., bool]
    component_entries: Callable[..., tuple[ComponentEntry, ...]]
    console: Console
    materialize_compute_boot_disk_defaults: Callable[..., bool]


class ProjectCreationWorkflow:
    def __init__(self, services: Callable[[], ProjectCreationServices]) -> None:
        self._services = services

    def __call__(
        self,
        *,
        target_path: Path,
        client_name: str | None = None,
        tenant_id: str | None = None,
        project_id: str | None = None,
        region_id: str | None = None,
        email: str | None = None,
        infra_components_opt: list[str] | None = None,
        apps_components_opt: list[str] | None = None,
        app_namespace_overrides: dict[str, str] | None = None,
        app_releasename_overrides: dict[str, str] | None = None,
        app_version_overrides: dict[str, str] | None = None,
        network_ids_opt: list[str] | None = None,
        subnet_ids_opt: list[str] | None = None,
        network_refs_opt: list[str] | None = None,
        subnet_refs_opt: list[str] | None = None,
        validate_sources: bool = True,
        validate_config: bool = True,
        no_interactive: bool = False,
        force: bool = False,
        soperator_release: SoperatorReleaseSnapshot | None = None,
        soperator_profile: str | None = None,
        soperator_values: Mapping[str, Any] | None = None,
    ) -> Path | None:
        """Create one project from typed inputs; command adapters own token parsing."""
        services = self._services()
        from .soperator_release_resolver import current_frozen_soperator_release
        from .soperator_values import (
            apply_frozen_feature_defaults,
            seed_soperator_values,
            soperator_rows,
            validate_feature_values,
            validate_frozen_input,
        )

        frozen = (
            current_frozen_soperator_release(soperator_release.release)
            if soperator_release is not None
            else None
        )
        if soperator_values is not None:
            if frozen is None:
                raise ValueError("Soperator values input requires the frozen install release")
            validate_frozen_input(soperator_values, frozen)
        app_namespace_overrides = dict(app_namespace_overrides or {})
        app_releasename_overrides = dict(app_releasename_overrides or {})
        app_version_overrides = dict(app_version_overrides or {})
        if soperator_release is not None:
            if apps_components_opt and apps_components_opt != [_SOPERATOR_APP_ID]:
                raise ValueError(
                    "Soperator install accepts only its upstream core and required prerequisites; "
                    "add optional apps afterward with 'component add'."
                )
            app_version_overrides = {_SOPERATOR_APP_ID: soperator_release.release}
        base_path = target_path.resolve()
        services.validate_deployments_root_target(base_path, allow_missing=True)
        deployments_root = services.resolve_deployments_root(base_path)
        services.assert_not_nested_deployments_root(deployments_root)
        interactive_mode = not no_interactive
        resolved_tenant_id = services.value_or_prompt(
            tenant_id,
            option_name="--tenant-id",
            prompt_text="Tenant ID",
            interactive=interactive_mode,
            default_value=None,
        )
        resolved_project_id = services.value_or_prompt(
            project_id,
            option_name="--project-id",
            prompt_text="Project ID",
            interactive=interactive_mode,
            default_value=None,
        )
        provider_lookup = services.provider_lookup()
        resolved_tenant_id, resolved_project_id = services.validate_tenant_project_ids_or_prompt(
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            interactive=interactive_mode,
            provider_lookup=provider_lookup,
        )
        resolved_tenant_folder, resolved_project_folder = services.resolve_create_target_folders(
            provider_lookup=provider_lookup,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
        )

        existing_config_path = services.project_config_path(
            deployments_root=deployments_root,
            tenant_folder=resolved_tenant_folder,
            project_folder=resolved_project_folder,
        )
        had_existing_config = existing_config_path.exists()
        infra_source_validation_ran = False
        if had_existing_config:
            with existing_config_path.open("r", encoding="utf-8") as handle:
                loaded_payload = yaml.safe_load(handle) or {}
            if not isinstance(loaded_payload, dict):
                raise RuntimeError("Existing config.yaml payload must be a mapping")
            (
                _existing_client_name,
                existing_tenant_id,
                existing_project_id,
                _existing_region_id,
                _existing_email,
            ) = services.identity_values_from_payload(loaded_payload)
            if (
                existing_tenant_id != resolved_tenant_id
                or existing_project_id != resolved_project_id
            ):
                raise RuntimeError(
                    "Resolved name-based project path collision: "
                    f"{existing_config_path.parent} already belongs to tenant_id/project_id "
                    f"'{existing_tenant_id}'/'{existing_project_id}', not "
                    f"'{resolved_tenant_id}'/'{resolved_project_id}'. "
                    "Move the existing folder or rename one of the Nebius resources before rerunning `create`."
                )
            if not interactive_mode and not force:
                raise RuntimeError(
                    "Existing project found: "
                    f"{existing_config_path.parent}. `create` no longer reconciles existing configs. "
                    "Use `component list/add/remove --config <config.yaml>` for day-2 "
                    "component edits, or rerun with "
                    "`--force` to overwrite this one project folder from scratch."
                )
            if validate_sources:
                services.validate_component_sources_or_raise(selected_app_ids=set())
                infra_source_validation_ran = True
            if interactive_mode:
                if force:
                    services.warn_existing_project_overwrite(config_path=existing_config_path)
                    services.console.print(
                        "[dim]`--force` confirms the overwrite. "
                        "This only affects that one resolved project folder.[/dim]"
                    )
                elif not services.confirm_existing_project_overwrite(
                    config_path=existing_config_path
                ):
                    services.console.print("No changes applied.")
                    return None
            else:
                services.warn_existing_project_overwrite(config_path=existing_config_path)
                services.console.print(
                    "[dim]`--force` confirms the overwrite in non-interactive mode. "
                    "This only affects that one resolved project folder.[/dim]"
                )
        if validate_sources and not infra_source_validation_ran:
            services.validate_component_sources_or_raise(selected_app_ids=set())

        resolved_client_name = services.client_name_or_prompt(
            client_name,
            interactive=interactive_mode,
        )
        services.ensure_project_auth_identity(
            project_id=resolved_project_id,
            client_name=resolved_client_name,
        )
        resolved_region_id = services.region_or_prompt(
            region_id,
            interactive=interactive_mode,
        )
        resolved_email = services.optional_email_or_prompt(
            email,
            interactive=interactive_mode,
        )

        infra_entries = services.with_infra_provider_groups(services.component_entries("infra"))
        app_entries = tuple(
            entry for entry in services.component_entries("apps") if entry.id != _SOPERATOR_APP_ID
        )
        if soperator_release is not None:
            release = soperator_release.release
            if not release:
                raise RuntimeError(
                    "Soperator lifecycle create requires a frozen official upstream release."
                )
            app_entries = (*app_entries, soperator_install_entry(release))

        optional_wizard_mode = interactive_mode
        if soperator_release is not None:
            selected_infra_raw = set(services.soperator_required_infra_ids)
            selected_apps_raw = {_SOPERATOR_APP_ID}
            services.console.print(
                "[dim]Soperator is configured to export metrics and logs to Nebius Observability. "
                "Use Public Nebius Grafana at https://grafana.nebius.dev/. "
                "Add optional apps after installation with 'component add'.[/dim]"
            )
        elif interactive_mode:
            selected_infra_raw = set()
            selected_apps_raw = set()
            while True:
                optional_decision = services.wizard_continue_phase(
                    "Continue with optional wizard phases (component selection and fields)?",
                    default=True,
                )
                if services.wizard_phase_stop_requested(optional_decision) or not optional_decision:
                    optional_wizard_mode = False
                    selected_infra_raw = services.resolve_component_ids(
                        scope="infra",
                        raw_values=infra_components_opt,
                        interactive=False,
                        entries=infra_entries,
                    )
                    selected_apps_raw = services.resolve_component_ids(
                        scope="apps",
                        raw_values=apps_components_opt,
                        interactive=False,
                        entries=app_entries,
                    )
                    break

                optional_wizard_mode = True
                selection_stage: ComponentScope = "infra"
                try:
                    while True:
                        try:
                            if selection_stage == "infra":
                                selected_infra_raw = services.resolve_component_ids(
                                    scope="infra",
                                    raw_values=infra_components_opt,
                                    interactive=True,
                                    entries=infra_entries,
                                    seed_defaults=selected_infra_raw or None,
                                )
                                selection_stage = "apps"
                                if (
                                    apps_components_opt is None
                                    and not services.selected_cluster_target_component_ids(
                                        selected_infra_raw,
                                        infra_entries,
                                    )
                                ):
                                    selected_apps_raw = set()
                                    services.console.print(
                                        "[dim]Skipping app chart selection because no MK8s "
                                        "target was selected. Select infra:mk8s to add Helm "
                                        "charts or Soperator.[/dim]"
                                    )
                                    break
                            selected_apps_raw = services.resolve_component_ids(
                                scope="apps",
                                raw_values=apps_components_opt,
                                interactive=True,
                                entries=app_entries,
                                seed_defaults=selected_apps_raw or None,
                            )
                            app_target_issue = services.app_selection_without_cluster_target_issue(
                                selected_infra=selected_infra_raw,
                                selected_apps=selected_apps_raw,
                                infra_entries=infra_entries,
                            )
                            if app_target_issue:
                                services.console.print(
                                    f"{warning_markup('Invalid app selection:')} {app_target_issue}"
                                )
                                if infra_components_opt is not None:
                                    raise RuntimeError(app_target_issue)
                                selection_stage = "infra"
                                continue
                            break
                        except services.wizard_back_requested:
                            if selection_stage == "apps":
                                selection_stage = "infra"
                                continue
                            raise
                except services.wizard_back_requested:
                    continue
                except services.wizard_quit_requested:
                    optional_wizard_mode = False
                    selected_infra_raw = services.resolve_component_ids(
                        scope="infra",
                        raw_values=infra_components_opt,
                        interactive=False,
                        entries=infra_entries,
                    )
                    selected_apps_raw = services.resolve_component_ids(
                        scope="apps",
                        raw_values=apps_components_opt,
                        interactive=False,
                        entries=app_entries,
                    )
                    break
                break
        else:
            selected_infra_raw = services.resolve_component_ids(
                scope="infra",
                raw_values=infra_components_opt,
                interactive=False,
                entries=infra_entries,
            )
            selected_apps_raw = services.resolve_component_ids(
                scope="apps",
                raw_values=apps_components_opt,
                interactive=False,
                entries=app_entries,
            )
        if interactive_mode and optional_wizard_mode and _SOPERATOR_APP_ID in selected_apps_raw:
            services.console.print(
                "[dim]Soperator install configures the full production MK8s+SFS "
                "bundle. Use `nebius-cxcli soperator onboard "
                "<config.yaml-or-deployments-root>` for existing Nebius MK8s "
                "clusters.[/dim]"
            )
            services.console.print(
                "[dim]Soperator worker profile controls whether the fresh "
                "production bundle uses CPU-only, GPU-only, or mixed worker "
                "NodeSets before MK8s fields and deployment-testing prompts.[/dim]"
            )
            if soperator_profile is None:
                try:
                    soperator_profile = services.prompt_soperator_profile()
                except services.wizard_quit_requested:
                    optional_wizard_mode = False
                    soperator_profile = _default_soperator_profile_name()
        elif _SOPERATOR_APP_ID in selected_apps_raw:
            soperator_profile = soperator_profile or _default_soperator_profile_name()
        selected_infra_before_soperator_expansion = set(selected_infra_raw)
        selected_apps_before_soperator_expansion = set(selected_apps_raw)
        selected_infra_raw = services.expand_soperator_component_selection(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        selected_apps_raw = services.expand_soperator_app_selection(
            selected_apps=selected_apps_raw,
            app_entries=app_entries,
        )
        services.print_soperator_selection_adjustments(
            selected_infra_before=selected_infra_before_soperator_expansion,
            selected_apps_before=selected_apps_before_soperator_expansion,
            selected_infra_after=selected_infra_raw,
            selected_apps_after=selected_apps_raw,
        )
        app_target_issue = services.app_selection_without_cluster_target_issue(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        if app_target_issue:
            raise RuntimeError(app_target_issue)

        dependency_seed_payload = services.dependency_seed_payload(
            client_name=resolved_client_name,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            email=resolved_email,
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
            app_entries=app_entries,
            existing_payload=None,
            merge_existing=False,
            app_version_overrides=app_version_overrides,
            soperator_profile=soperator_profile,
        )

        dependency_resolution_started = time.monotonic()
        if selected_apps_raw:
            with services.command_status("[cyan]Resolving app chart dependencies...[/cyan]"):
                selected_infra, selected_apps = services.normalize_component_dependencies(
                    selected_infra=selected_infra_raw,
                    selected_apps=selected_apps_raw,
                    infra_entries=infra_entries,
                    app_entries=app_entries,
                    payload_for_app_chart_deps=dependency_seed_payload,
                )
        else:
            selected_infra, selected_apps = services.normalize_component_dependencies(
                selected_infra=selected_infra_raw,
                selected_apps=selected_apps_raw,
                infra_entries=infra_entries,
                app_entries=app_entries,
                payload_for_app_chart_deps=dependency_seed_payload,
            )
        dependency_resolution_elapsed = time.monotonic() - dependency_resolution_started
        if dependency_resolution_elapsed >= 1:
            services.console.print(
                f"[dim]App dependency resolution finished in {dependency_resolution_elapsed:.1f}s[/dim]"
            )
        source_validated_app_ids: set[str] = set()
        if validate_sources and selected_apps:
            services.validate_component_sources_or_raise(
                selected_app_ids=selected_apps,
                include_infra=False,
            )
            source_validated_app_ids.update(selected_apps)

        wizard_completed = True
        starter_payload = services.starter_component_payload(
            client_name=resolved_client_name,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            email=resolved_email,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
            app_namespace_overrides=app_namespace_overrides,
            app_releasename_overrides=app_releasename_overrides,
            app_version_overrides=app_version_overrides,
            soperator_profile=soperator_profile,
        )
        if soperator_profile is not None:
            services.apply_soperator_profile_to_payload(
                starter_payload,
                profile=soperator_profile,
            )
        services.apply_vpc_ref_overrides(
            payload=starter_payload,
            selected_infra=selected_infra,
            network_refs=network_refs_opt,
            subnet_refs=subnet_refs_opt,
        )
        services.apply_vpc_id_overrides(
            payload=starter_payload,
            selected_infra=selected_infra,
            network_ids=network_ids_opt,
            subnet_ids=subnet_ids_opt,
        )
        final_payload = starter_payload
        services.materialize_singleton_provider_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_mk8s_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_vm_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_compute_boot_disk_defaults(
            final_payload,
            provider_lookup=provider_lookup,
        )
        if frozen is not None:
            apply_frozen_feature_defaults(final_payload, frozen)
        if soperator_values is not None:
            seed_soperator_values(final_payload, soperator_values)
        services.materialize_create_soperator_component_defaults(final_payload)
        selected_apps, mysterybox_eso_app_labels = (
            services.ensure_mysterybox_eso_app_dependency_selection(
                final_payload,
                selected_apps=selected_apps,
                app_entries=app_entries,
            )
        )
        services.print_mysterybox_eso_app_dependency_adjustment(mysterybox_eso_app_labels)
        if interactive_mode and soperator_release is None:
            services.print_component_selection_summary(
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                payload=final_payload,
            )

        if interactive_mode and optional_wizard_mode:
            field_wizard_kwargs: dict[str, Any] = {}
            if soperator_profile is not None:
                field_wizard_kwargs["skip_soperator_profile_prompt"] = True
            field_wizard_kwargs["align_infra_resource_names_before_apps"] = True
            field_wizard_kwargs["prompt_app_version_before_app_config"] = soperator_release is None
            field_wizard_kwargs["soperator_install"] = soperator_release is not None
            config_yaml_override, wizard_completed = services.run_component_field_wizard(
                config_yaml=yaml.safe_dump(final_payload, sort_keys=False),
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                provider_lookup=provider_lookup,
                **field_wizard_kwargs,
            )
            parsed_override = yaml.safe_load(config_yaml_override) or {}
            if not isinstance(parsed_override, dict):
                raise RuntimeError("Updated config payload must be a mapping")
            final_payload = parsed_override
            services.materialize_planned_vpc_binding_tokens(final_payload)
            selected_apps = services.enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )

        services.materialize_create_soperator_component_defaults(final_payload)
        selected_apps, mysterybox_eso_app_labels = (
            services.materialize_soperator_child_chart_secret_dependencies(
                final_payload,
                selected_apps=selected_apps,
                app_entries=app_entries,
            )
        )
        services.print_mysterybox_eso_app_dependency_adjustment(mysterybox_eso_app_labels)
        services.materialize_mk8s_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_vm_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_singleton_provider_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        services.materialize_planned_vpc_binding_tokens(final_payload)
        services.materialize_compute_boot_disk_defaults(
            final_payload,
            provider_lookup=provider_lookup,
        )
        if not interactive_mode:
            services.require_vpc_networking_for_noninteractive(
                payload=final_payload,
                selected_infra=selected_infra,
                infra_entries=infra_entries,
                provider_lookup=provider_lookup,
            )
        if soperator_release is not None:
            # These rows are derived from the profile. Reconcile them after the full
            # field wizard changes topology, before selecting its final dependencies.
            prune_inactive_mk8s_gpu_app_rows(final_payload)
            selected_apps = services.enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )
        gpu_app_selection = resolve_mk8s_gpu_app_selection(
            final_payload,
            selected_app_ids=selected_apps,
            app_entries=app_entries,
        )
        if gpu_app_selection.issues:
            raise RuntimeError(
                "MK8s GPU app defaults are incomplete:\n  - "
                + "\n  - ".join(gpu_app_selection.issues)
            )
        if gpu_app_selection.auto_enabled_app_ids:
            selected_apps = set(gpu_app_selection.selected_app_ids)
            auto_enabled_seed = services.starter_component_payload(
                client_name=resolved_client_name,
                tenant_id=resolved_tenant_id,
                project_id=resolved_project_id,
                region_id=resolved_region_id,
                email=resolved_email,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                app_namespace_overrides=app_namespace_overrides,
                app_releasename_overrides=app_releasename_overrides,
                app_version_overrides=app_version_overrides,
                soperator_profile=soperator_profile,
            )
            services.ensure_payload_contains_component_rows(
                payload=final_payload,
                seed_payload=auto_enabled_seed,
            )
            services.console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(f"'apps:{item}'" for item in gpu_app_selection.auto_enabled_app_ids)
                + " because the selected MK8s GPU configuration requires them."
            )
        if ensure_mk8s_gpu_app_rows(final_payload, app_entries=app_entries):
            selected_apps = services.enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )
        observability_selection = resolve_observability_app_selection(
            final_payload,
            selected_app_ids=selected_apps,
            app_entries=app_entries,
        )
        if observability_selection.issues:
            raise RuntimeError(
                "Observability app defaults are incomplete:\n  - "
                + "\n  - ".join(observability_selection.issues)
            )
        if observability_selection.auto_enabled_app_ids:
            selected_apps = set(observability_selection.selected_app_ids)
            auto_enabled_seed = services.starter_component_payload(
                client_name=resolved_client_name,
                tenant_id=resolved_tenant_id,
                project_id=resolved_project_id,
                region_id=resolved_region_id,
                email=resolved_email,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                app_namespace_overrides=app_namespace_overrides,
                app_releasename_overrides=app_releasename_overrides,
                app_version_overrides=app_version_overrides,
                soperator_profile=soperator_profile,
            )
            services.ensure_payload_contains_component_rows(
                payload=final_payload,
                seed_payload=auto_enabled_seed,
            )
            services.console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(
                    f"'apps:{item}'" for item in observability_selection.auto_enabled_app_ids
                )
                + " because the selected observability configuration requires them."
            )
        services.align_new_infra_instance_ids_with_resource_names(final_payload)
        services.materialize_create_soperator_component_defaults(final_payload)
        services.materialize_compute_boot_disk_defaults(
            final_payload,
            provider_lookup=provider_lookup,
        )
        if ensure_mysterybox_eso_app_rows(final_payload, app_entries=app_entries):
            selected_apps = services.enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )
        selected_apps, nfs_csi_app_labels = services.ensure_nfs_csi_app_dependency_selection(
            final_payload,
            selected_apps=selected_apps,
            app_entries=app_entries,
        )
        services.print_nfs_csi_app_dependency_adjustment(nfs_csi_app_labels)
        materialize_mk8s_gpu_app_values(final_payload)
        materialize_observability_infra_values(final_payload)
        services.prune_mk8s_node_group_defaults_without_soperator(
            final_payload,
            infra_entries=infra_entries,
        )
        services.refresh_soperator_registration_fingerprints(final_payload)

        create_required_field_issues = (
            services.wizard_followup_required_field_issues(
                payload=final_payload,
                infra_entries=infra_entries,
            )
            if interactive_mode and (not optional_wizard_mode or not wizard_completed)
            else []
        )
        if create_required_field_issues:
            services.print_incomplete_wizard_no_write_warning(
                issues=create_required_field_issues,
                message="No project config or generated output was written.",
                preserved_path=existing_config_path.parent if had_existing_config else None,
                skipped_path=existing_config_path.parent if not had_existing_config else None,
            )
            raise typer.Exit(code=1)
        services.prune_redundant_app_chart_default_values(
            payload=final_payload,
            app_entries=app_entries,
        )
        services.refresh_soperator_registration_fingerprints(final_payload)
        if validate_sources:
            selected_apps = services.validate_final_enabled_app_sources_or_raise(
                payload=final_payload,
                app_entries=app_entries,
                already_validated_app_ids=source_validated_app_ids,
            )
            requested_version_app_ids = set(app_version_overrides)
            requested_version_app_ids.update(
                services.app_chart_ids_with_non_catalog_versions(
                    payload=final_payload,
                    app_entries=app_entries,
                )
            )
            services.validate_requested_app_chart_versions_or_raise(
                payload=final_payload,
                candidate_app_ids=requested_version_app_ids,
            )
        selected_infra = services.enabled_ids_from_runtime_payload(
            payload=final_payload,
            entries=infra_entries,
        )
        selected_apps = services.enabled_ids_from_runtime_payload(
            payload=final_payload,
            entries=app_entries,
        )

        if soperator_release is not None:
            for row in soperator_rows(final_payload):
                validate_feature_values(row.get("values", {}))
            _validate_soperator_install_configuration(final_payload, soperator_release)

        result = services.scaffold_instance(
            base_path=base_path,
            client_name=resolved_client_name,
            tenant_folder=resolved_tenant_folder,
            project_folder=resolved_project_folder,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            email=resolved_email,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
            force=force or had_existing_config,
            config_yaml=yaml.safe_dump(final_payload, sort_keys=False),
        )
        gitignore_result = services.ensure_deployments_gitignore(
            deployments_root=result.deployments_root,
        )

        services.console.print(f"Deployments root: {result.deployments_root}")
        if gitignore_result.path is not None:
            if gitignore_result.wrote:
                services.console.print(f"Ensured deployments .gitignore: {gitignore_result.path}")
            else:
                services.console.print(
                    f"Deployments .gitignore up-to-date: {gitignore_result.path}"
                )
        if result.wrote_config:
            if had_existing_config:
                services.console.print(f"Overwritten project: {result.project_path}")
            else:
                services.console.print(f"Created project: {result.project_path}")
        else:
            if had_existing_config:
                services.console.print(
                    f"Project already matched the overwrite target: {result.project_path}"
                )
            else:
                services.console.print(f"Config up-to-date: {result.config_path}")
        if validate_config:
            services.run_runtime_validation(
                config_path=result.config_path,
                strict=False,
                title="Post-create validation",
            )
        quota_report = services.warn_on_live_quota_issues(final_payload, phase="create")
        if quota_report.has_confirmed_insufficiency:
            services.console.print(
                f"{warning_markup('Create completed with quota warnings.')} "
                "Render can continue, but deploy will fail until the required quota is available "
                "and any selected GPU shape has matching Capacity Dashboard capacity."
            )
            services.print_quota_remediation_hint(result.config_path, quota_report)
        if not validate_config:
            services.print_mk8s_gpu_validation_warnings(final_payload)
        display_payload = final_payload
        with suppress(Exception):
            persisted_payload = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
            if isinstance(persisted_payload, dict):
                display_payload = persisted_payload
        display_selected_infra = services.enabled_ids_from_runtime_payload(
            payload=display_payload,
            entries=infra_entries,
        )
        display_selected_apps = services.enabled_ids_from_runtime_payload(
            payload=display_payload,
            entries=app_entries,
        )
        services.console.print(
            "Enabled infra components: "
            + (", ".join(sorted(display_selected_infra)) if display_selected_infra else "(none)")
        )
        services.console.print(
            "Enabled apps components: "
            + (", ".join(sorted(display_selected_apps)) if display_selected_apps else "(none)")
        )
        if services.active_chart_count(
            final_payload
        ) > 0 and services.config_uses_private_cluster_handoff(final_payload):
            services.console.print(
                f"[yellow]NOTE:[/yellow] {services.private_cluster_handoff_note()}"
            )
        services.console.print(
            f"Ensured generated skeleton: {result.config_path.parent / 'generated'}"
        )
        if soperator_release is None:
            services.print_create_next_steps(result.config_path)
        if gitignore_result.inside_git_repo:
            services.console.print(
                f"{warning_markup('Security warning:')} keep this customer repository private "
                "because the deployments root contains sensitive operational metadata."
            )
        return result.config_path.resolve()
