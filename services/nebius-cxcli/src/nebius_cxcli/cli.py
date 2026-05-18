"""Typer CLI for nebius-cxcli."""

from __future__ import annotations

import atexit
import base64
import copy
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from . import __version__, native_logs, runtime_introspection
from .component_defaults import (
    default_target_paths,
    literal_default_input_leaf_names,
    materialize_shared_defaults,
    read_component_path,
    resolve_component_defaults,
    shared_default_input_sources,
    shared_default_payload_paths,
)
from .component_instances import (
    INSTANCE_ID_FIELD,
    INSTANCE_ID_PATTERN,
    component_instance_id,
    component_instance_label,
    component_type_id,
    ensure_component_instance_id,
    next_component_instance_id,
    normalize_component_token,
)
from .component_sources import (
    SourceProfile,
    component_input_binding_ref,
    component_output_root_name,
    helm_chart_source_by_id,
    load_component_sources,
    resolve_component_sources_file,
    resolve_component_sources_profile,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from .component_wiring import (
    _UNRESOLVED,
    component_entry_lookup,
    component_output_ref,
    input_binding_conflicts,
    input_binding_leaf_names,
    managed_input_binding_payload_paths,
    output_lookup,
    resolve_component_output_value,
    resolve_input_binding_source,
    resolved_component_row,
)
from .components import (
    COMPONENT_ID_PATTERN,
    ComponentEntry,
    ComponentScope,
    component_entries,
    component_entry_chart_name,
    resolve_component_dependencies,
)
from .compute_boot_disks import (
    align_compute_disk_size_to_allocation_unit,
    compute_boot_disk_type_supports_explicit_encryption,
    compute_disk_type_allocation_unit_gib,
    materialize_compute_boot_disk_defaults,
    refresh_compute_boot_disk_defaults,
)
from .config_loader import load_config, normalize_runtime_config_payload, validate_config
from .config_template import starter_config_yaml
from .deploy_targets import (
    TARGET_REF_FIELD,
    app_chart_target_ref,
    enabled_cluster_target_refs,
    flux_target_dir,
    is_auto_target_scoped_app_instance_id,
    normalize_generated_deploy_target,
    strip_app_chart_target_refs,
    target_scoped_app_instance_id,
)
from .deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    DeployValidationReport,
    build_deploy_validation_report,
    clear_deploy_validation_artifacts,
    status_label,
)
from .deployment_status import deployment_status_reporting
from .discover_ops import discover_configs
from .email_settings import (
    EmailSettings,
    disable_email_settings,
    email_environment_variables,
    email_runtime_settings,
    email_secret_values,
    load_email_settings,
    resolve_email_config_file,
    write_email_settings,
)
from .flux_ops import (
    CLUSTER_HANDOFF_ACCESS_ENV,
    cluster_handoff_reachability_guidance,
    delete_rendered_flux,
    ensure_flux,
    flux_bootstrap_resources_installed,
    flux_controllers_installed,
    flux_crds_installed,
    flux_dir_has_rendered_resources,
    install_flux_controllers,
    wait_for_flux_resource_apis,
    wait_for_rendered_flux_resources,
)
from .flux_render import render_flux
from .generated_manifest import (
    load_generated_manifest,
    manifest_path_for_generated_dir,
    runtime_config_from_manifest,
    terraform_tfvars_from_manifest,
    write_generated_manifest,
    write_generated_manifest_to_path,
)
from .github_secrets import (
    build_github_environment_name,
    delete_environment_secret,
    delete_environment_variable,
    detect_github_repo_slug,
    ensure_github_environment,
    environment_secrets_presence,
    read_github_token,
    upsert_environment_secrets,
    upsert_environment_variables,
)
from .grafana_dashboard_validation import validate_grafana_dashboard_fits
from .grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
    collect_grafana_runtime_status,
    ensure_grafana_runtime_secrets,
    grafana_enabled_for_target,
    read_grafana_status,
    write_grafana_status,
)
from .helm_client import HelmChartReference, HelmClient, chart_cli_contract_findings
from .iam_bootstrap import (
    auth_public_key_exists,
    bootstrap_ci_service_account,
    bootstrap_service_account_auth_key,
    ensure_ci_service_account_identity,
)
from .infra_render import (
    is_portable_module_source,
    render_terraform_artifacts,
    rendered_module_sources,
)
from .inventory_ops import ssh_jump_access_hints, wireguard_access_command_hints, write_inventory
from .managed_tools import FLUX_VERSION_ENV, TERRAFORM_VERSION_ENV
from .mk8s_destroy_recovery import (
    Mk8sNodeGroupDestroyCandidate,
)
from .mk8s_destroy_recovery import (
    delete_node_group as delete_stuck_mk8s_node_group,
)
from .mk8s_destroy_recovery import (
    find_stuck_node_groups as find_stuck_mk8s_node_groups,
)
from .mk8s_gpu import (
    ensure_mk8s_gpu_app_rows,
    has_mk8s_gpu_health_checker_app,
    materialize_mk8s_gpu_app_values,
    mk8s_gpu_dependency_issues,
    mk8s_gpu_validation_specs,
    mk8s_gpu_validation_warnings,
    resolve_mk8s_gpu_app_selection,
    run_mk8s_gpu_validations,
)
from .mk8s_preflight import (
    has_mk8s_resource_name_preflight_targets,
    validate_mk8s_network_preflight,
    validate_mk8s_resource_name_preflight,
)
from .mysterybox_eso import (
    EXTERNAL_SECRETS_APP_ID,
    MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
    MYSTERYBOX_INFRA_COMPONENT_ID,
    ensure_mysterybox_eso_app_rows,
    materialize_mysterybox_eso_app_values,
    mysterybox_eso_api_domains,
    mysterybox_eso_enabled,
    mysterybox_eso_runtime_secret_specs,
    mysterybox_eso_terraform_output_specs,
    mysterybox_eso_validation_specs,
)
from .nfs_csi import (
    ensure_nfs_csi_app_rows,
    nfs_csi_binding_issues,
    nfs_csi_terraform_output_specs,
)
from .notify_ops import DeployReportEmailResult, send_deploy_report_email
from .observability import (
    materialize_observability_app_values,
    materialize_observability_infra_values,
    observability_dependency_issues,
    observability_gpu_node_label_reconciliation,
    observability_validation_specs,
    resolve_observability_app_selection,
)
from .observability_validation import (
    OBSERVABILITY_INGESTION_VALIDATION_KIND,
    run_observability_validations,
)
from .paths import (
    ProjectPaths,
    normalize_project_folder_name,
    resolve_deploy_config_paths,
    resolve_destroy_config_paths,
    resolve_email_config_paths,
    resolve_generated_flux_paths,
    resolve_generated_infra_paths,
    resolve_generated_paths,
    resolve_project_paths,
    validate_path_alignment,
)
from .provider_options import (
    OptionChoice,
    ProviderOptionLookup,
    TenantProjectValidationResult,
)
from .quota_checks import (
    QuotaCheck,
    QuotaContributor,
    QuotaReport,
    assess_live_quotas,
    estimate_mk8s_quota_requirements,
    format_quota_report_lines,
    format_quota_request_lines,
    format_quota_request_manual_followup_lines,
    plan_quota_request_changes,
    request_quota_changes,
)
from .render import promote_staged_generated_paths, reset_generated_bundle, staged_generated_paths
from .runtime_config import read_path_with_catalog, to_plain_data
from .runtime_introspection import (
    helm_chart_default_values,
    module_cli_contract_findings,
    module_output_names,
    module_required_variables,
    module_source_validation_issues,
    module_variable_names,
    module_variables,
)
from .sdk_auth import init_nebius_sdk, suppress_expected_refresh_logs
from .slack_notifier_runtime import (
    ensure_soperator_notifier_runtime_secrets,
    soperator_notifier_enabled_for_target,
)
from .soperator_backup_runtime import (
    ensure_soperator_backup_runtime_secrets,
    soperator_backup_enabled_for_target,
)
from .soperator_companions import materialize_soperator_companion_app_values
from .ssh_jumphost import (
    SshJumphostAllowedCidrRequest,
    normalize_allowed_cidr_csv,
    select_ssh_jumphost_component,
    ssh_jumphost_public_ip_from_outputs,
    update_ssh_jumphost_allowed_cidrs,
)
from .ssh_public_keys import discover_ssh_public_key_files, normalize_ssh_public_key_value
from .templates import customer_workflow_yaml, default_cli_ref
from .terminal_styles import error_markup, warning_markup
from .terraform_backend import (
    TerraformStateLockInfo,
    backend_settings_from_config,
    ensure_state_bucket,
    read_state_lock_info,
)
from .terraform_ops import (
    terraform_apply,
    terraform_destroy,
    terraform_force_unlock,
    terraform_init,
    terraform_output_json,
    terraform_output_raw,
    terraform_plan,
    terraform_show_json,
    terraform_state_list,
    terraform_state_show,
    terraform_validate,
)
from .terraform_provider import build_provider_module_name
from .wireguard_clients import (
    WireGuardClientGenerationRequest,
    WireGuardLocalSubnetUpdateRequest,
    default_wireguard_client_output_dir,
    generate_wireguard_client_config,
    normalize_dns,
    normalize_local_subnet_csv,
    normalize_local_subnets,
    select_wireguard_component,
    update_wireguard_local_subnets,
    wireguard_public_ip_from_outputs,
)

console = Console()
GRAFANA_STATUS_POLL_INTERVAL_SECONDS = 15.0
GRAFANA_STATUS_TIMEOUT_SECONDS = 300.0


def _console_is_terminal() -> bool:
    return bool(console.is_terminal)


def _quota_failure_message(report: QuotaReport, *, phase: str) -> str:
    lines = [
        (
            f"Nebius quota/capacity is insufficient for {phase}. "
            "Increase the quota, or for GPU shortages choose a platform/preset/fabric "
            "with available Capacity Dashboard capacity, and retry."
        ),
    ]
    for item in report.insufficient_checks:
        unit = item.unit or "count"
        required = str(item.required) if unit != "byte" else f"{item.required} byte"
        available = str(item.available) if item.available is not None else "unknown"
        if unit == "byte" and item.available is not None:
            available = f"{item.available} byte"
        lines.append(
            f"- {item.component_label}: {item.region} {item.quota_name} "
            f"requires {required}, available {available} ({item.reason})"
        )
    return "\n".join(lines)


def _show_quota_coverage_gap_output(*, phase: str) -> bool:
    return phase in {"quota check", "quota request"}


def _show_quota_confirmed_output(*, phase: str) -> bool:
    return phase == "quota check"


def _print_live_quota_report(report: QuotaReport, *, phase: str) -> None:
    for line in format_quota_report_lines(
        report,
        phase=phase,
        include_coverage_gaps=_show_quota_coverage_gap_output(phase=phase),
        include_confirmed_components=_show_quota_confirmed_output(phase=phase),
    ):
        console.print(line)


def _quota_check_all_regions_command(config_path: Path) -> str:
    return f"nebius-cxcli quota-check --all-regions {shlex.quote(str(config_path))}"


def _quota_request_command(config_path: Path) -> str:
    return f"nebius-cxcli quota-request {shlex.quote(str(config_path))}"


def _print_quota_request_hint(config_path: Path) -> None:
    console.print("Next step: review and submit quota requests with:")
    console.print(f"  {_quota_request_command(config_path)}")


def _quota_report_has_capacity_dashboard_shortage(report: QuotaReport) -> bool:
    return any(
        item.sufficient is False and item.source_scope.startswith("capacity-dashboard")
        for item in report.insufficient_checks
    )


def _print_quota_remediation_hint(config_path: Path, report: QuotaReport) -> None:
    if plan_quota_request_changes(report):
        _print_quota_request_hint(config_path)
        return
    if _quota_report_has_capacity_dashboard_shortage(report):
        console.print(
            "Next step: choose a GPU platform/preset/fabric or region with available "
            "Capacity Dashboard capacity, then rerun quota-check."
        )
        return
    console.print(
        "Next step: resolve the live quota lookup details and rerun quota-check; "
        "no automatic quota request target could be derived."
    )


def _print_quota_check_all_regions_hint(config_path: Path, *, enabled: bool) -> None:
    if not enabled:
        return
    console.print("Next step: compare quota availability across regions with:")
    console.print(f"  {_quota_check_all_regions_command(config_path)}")


def _warn_on_live_quota_issues(
    config: Any,
    *,
    phase: str,
    all_regions: bool = False,
) -> QuotaReport:
    report = _assess_live_quota_report(config, phase=phase, all_regions=all_regions)
    _print_live_quota_report(report, phase=phase)
    return report


def _adjust_quota_report_for_existing_generated_state(
    config: Any,
    paths: ProjectPaths,
    report: QuotaReport,
) -> QuotaReport:
    if not report.checks:
        return report
    if not paths.infra_dir.exists():
        return report
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    if not manifest_path.exists():
        return report
    try:
        manifest = load_generated_manifest(paths.generated_dir)
        _ensure_runtime_auth_material(
            config,
            need_terraform=True,
            auto_bootstrap=False,
        )
        _ensure_backend_s3_env_aliases()
        runtime_env = _terraform_runtime_env(config)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        managed_requirements = _managed_mk8s_quota_requirements_from_terraform_state(
            config,
            paths,
            manifest,
            runtime_env=runtime_env,
        )
    except Exception:
        return report
    return _adjust_quota_report_for_managed_mk8s_state(
        report,
        managed_requirements=managed_requirements,
    )


def _warn_on_config_live_quota_issues(
    config: Any,
    paths: ProjectPaths,
    *,
    phase: str,
    all_regions: bool = False,
) -> QuotaReport:
    report = _assess_live_quota_report(config, phase=phase, all_regions=all_regions)
    report = _adjust_quota_report_for_existing_generated_state(config, paths, report)
    _print_live_quota_report(report, phase=phase)
    return report


def _assess_live_quota_report(
    config: Any,
    *,
    phase: str,
    all_regions: bool = False,
) -> QuotaReport:
    try:
        return assess_live_quotas(
            config,
            context=f"{phase} quota assessment",
            all_regions=all_regions,
        )
    except Exception as exc:
        return QuotaReport(
            tenant_id="",
            project_id="",
            region_id="",
            checked_at=datetime.now(UTC).isoformat(),
            errors=(f"{phase} quota assessment failed: {exc}",),
        )


def _raise_on_live_quota_issues(config: Any, *, phase: str) -> QuotaReport:
    report = _warn_on_live_quota_issues(config, phase=phase)
    if report.has_confirmed_insufficiency:
        raise RuntimeError(_quota_failure_message(report, phase=phase))
    return report


def _raise_on_config_live_quota_issues(
    config: Any,
    paths: ProjectPaths,
    *,
    phase: str,
) -> QuotaReport:
    report = _warn_on_config_live_quota_issues(config, paths, phase=phase)
    if report.has_confirmed_insufficiency:
        raise RuntimeError(_quota_failure_message(report, phase=phase))
    return report


_TERRAFORM_STATE_MODULE_RE = re.compile(r"^module\.([A-Za-z0-9_]+)(?:\.|$)")


def _terraform_state_module_name(address: str) -> str:
    match = _TERRAFORM_STATE_MODULE_RE.match(str(address).strip())
    return match.group(1).strip() if match is not None else ""


def _state_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _state_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _state_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _state_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    text = _state_text(value)
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _terraform_show_resources(module: Mapping[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for item in _state_list(module.get("resources")):
        if isinstance(item, Mapping):
            resources.append(dict(item))
    for child in _state_list(module.get("child_modules")):
        if isinstance(child, Mapping):
            resources.extend(_terraform_show_resources(child))
    return resources


def _terraform_state_resources(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("values")
    if not isinstance(values, Mapping):
        return []
    root_module = values.get("root_module")
    if not isinstance(root_module, Mapping):
        return []
    return _terraform_show_resources(root_module)


def _state_public_ips_enabled(template: Mapping[str, Any]) -> bool:
    for item in _state_list(template.get("network_interfaces")):
        if isinstance(item, Mapping) and item.get("public_ip_address") is not None:
            return True
    return False


def _state_boot_disk_size_gib(template: Mapping[str, Any]) -> int | None:
    boot_disk = _state_mapping(template.get("boot_disk"))
    for key in ("size_gibibytes", "size_gib", "size_gibs"):
        resolved = _state_positive_int(boot_disk.get(key))
        if resolved is not None and resolved > 0:
            return resolved
    size_bytes = _state_positive_int(boot_disk.get("size_bytes"))
    gib = 1024 * 1024 * 1024
    if size_bytes is not None and size_bytes > 0:
        return size_bytes // gib if size_bytes % gib == 0 else None
    return None


def _state_node_group_count(values: Mapping[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    fixed_count = _state_positive_int(values.get("fixed_node_count"))
    if fixed_count is not None:
        return fixed_count, None
    autoscaling = _state_mapping(values.get("autoscaling"))
    if not autoscaling:
        return None, None
    return None, autoscaling


def _state_node_group_inputs(values: Mapping[str, Any], *, gpu: bool) -> dict[str, Any]:
    template = _state_mapping(values.get("template"))
    resources = _state_mapping(template.get("resources"))
    count, autoscaling = _state_node_group_count(values)
    prefix = "gpu" if gpu else "cpu"
    inputs: dict[str, Any] = {}
    if count is not None:
        inputs[f"{prefix}_nodes_count_per_group" if gpu else f"{prefix}_nodes_count"] = count
    if autoscaling is not None:
        inputs[f"mk8s_{prefix}_node_group_overrides"] = {"autoscaling": autoscaling}
    platform = _state_text(resources.get("platform"))
    preset = _state_text(resources.get("preset"))
    if platform:
        inputs[f"{prefix}_nodes_platform"] = platform
    if preset:
        inputs[f"{prefix}_nodes_preset"] = preset
    if template.get("preemptible") is not None:
        inputs[f"{prefix}_nodes_preemptible"] = True
    if _state_public_ips_enabled(template):
        inputs[f"{prefix}_nodes_public_ips"] = True
    boot_disk = _state_mapping(template.get("boot_disk"))
    disk_type = _state_text(boot_disk.get("type"))
    if disk_type:
        inputs[f"{prefix}_nodes_boot_disk_type"] = disk_type
    disk_size_gib = _state_boot_disk_size_gib(template)
    if disk_size_gib is not None:
        inputs[f"{prefix}_nodes_boot_disk_size_gib"] = disk_size_gib
    return inputs


def _component_region_for_instance(
    config: Any,
    *,
    component_id: str,
    instance_id: str,
) -> str:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return ""
    client_info = _state_mapping(payload.get("client_info"))
    nebius = _state_mapping(client_info.get("nebius"))
    default_region = _state_text(nebius.get("region_id"))
    infra = _state_mapping(payload.get("infra"))
    for item in _state_list(infra.get("components")):
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        if component_type_id(item) != component_id:
            continue
        if component_instance_id(item) != instance_id:
            continue
        inputs = _state_mapping(item.get("inputs"))
        return _state_text(inputs.get("region")) or default_region
    return default_region


def _generated_bundle_mk8s_module_index(
    manifest: Mapping[str, Any],
) -> dict[str, tuple[str, str]]:
    return {
        item["module_name"]: (item["component_id"], item["instance_id"])
        for item in _generated_bundle_module_sources(manifest)
        if item.get("component_id") == "mk8s"
        and item.get("module_name")
        and item.get("instance_id")
    }


def _managed_mk8s_quota_requirements_from_terraform_state(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    runtime_env: Mapping[str, str] | None,
) -> tuple[Any, ...]:
    try:
        module_index = _generated_bundle_mk8s_module_index(manifest)
    except Exception:
        return ()
    if not module_index:
        return ()
    try:
        state_addresses = terraform_state_list(
            paths.infra_dir,
            extra_env=dict(runtime_env or {}),
            initialize=False,
        )
    except Exception:
        return ()
    if not state_addresses:
        return ()
    try:
        state_payload = terraform_show_json(
            paths.infra_dir,
            extra_env=dict(runtime_env or {}),
            initialize=False,
        )
    except Exception:
        return ()

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for resource in _terraform_state_resources(state_payload):
        address = _state_text(resource.get("address"))
        resource_type = _state_text(resource.get("type"))
        module_name = _terraform_state_module_name(address)
        component_identity = module_index.get(module_name)
        if component_identity is None:
            continue
        component_id, instance_id = component_identity
        state_bucket = grouped.setdefault(
            (component_id, instance_id),
            {
                "cluster_present": False,
                "project_id": "",
                "region": _component_region_for_instance(
                    config,
                    component_id=component_id,
                    instance_id=instance_id,
                ),
                "cpu_groups": [],
                "gpu_groups": [],
                "gpu_fabric": "",
            },
        )
        values = _state_mapping(resource.get("values"))
        if resource_type == "nebius_mk8s_v1_cluster":
            state_bucket["cluster_present"] = True
            state_bucket["project_id"] = _state_text(values.get("parent_id")) or str(
                state_bucket["project_id"]
            )
        elif resource_type == "nebius_compute_v1_gpu_cluster":
            state_bucket["gpu_fabric"] = _state_text(values.get("infiniband_fabric"))
        elif resource_type == "nebius_mk8s_v1_node_group":
            if ".nebius_mk8s_v1_node_group.cpu" in address:
                state_bucket["cpu_groups"].append(values)
            elif ".nebius_mk8s_v1_node_group.gpu" in address:
                state_bucket["gpu_groups"].append(values)

    collected: list[Any] = []
    default_project_id = ""
    payload = to_plain_data(config)
    if isinstance(payload, Mapping):
        default_project_id = _state_text(
            _state_mapping(_state_mapping(payload.get("client_info")).get("nebius")).get(
                "project_id"
            )
        )
    for (_component_id, instance_id), state_bucket in grouped.items():
        if not state_bucket["cluster_present"]:
            continue
        project_id = _state_text(state_bucket["project_id"]) or default_project_id
        region = _state_text(state_bucket["region"])
        if not project_id or not region:
            continue
        inputs: dict[str, Any] = {}
        cpu_groups = list(state_bucket["cpu_groups"])
        if cpu_groups:
            inputs.update(_state_node_group_inputs(_state_mapping(cpu_groups[0]), gpu=False))
        gpu_groups = list(state_bucket["gpu_groups"])
        if gpu_groups:
            inputs["gpu_enabled"] = True
            inputs["gpu_node_groups"] = len(gpu_groups)
            inputs.update(_state_node_group_inputs(_state_mapping(gpu_groups[0]), gpu=True))
            gpu_fabric = _state_text(state_bucket["gpu_fabric"])
            if gpu_fabric:
                inputs["infiniband_fabric"] = gpu_fabric
        try:
            requirements, _gaps = estimate_mk8s_quota_requirements(
                project_id=project_id,
                region=region,
                instance_id=instance_id,
                inputs=inputs,
                context="generated-bundle quota baseline",
            )
        except Exception:
            continue
        collected.extend(requirements)
    return tuple(collected)


def _baseline_requirement_key(
    *,
    component_id: str,
    instance_id: str,
    quota_name: str,
    region: str,
) -> tuple[str, str, str, str]:
    return component_id, instance_id, quota_name, region


def _adjust_quota_report_for_managed_mk8s_state(
    report: QuotaReport,
    *,
    managed_requirements: Sequence[Any],
) -> QuotaReport:
    baseline: dict[tuple[str, str, str, str], int] = {}
    for item in managed_requirements:
        component_id = _state_text(getattr(item, "component_id", None))
        instance_id = _state_text(getattr(item, "instance_id", None))
        quota_name = _state_text(getattr(item, "quota_name", None))
        region = _state_text(getattr(item, "region", None))
        required = _state_positive_int(getattr(item, "required", None))
        if not component_id or not instance_id or not quota_name or not region or required is None:
            continue
        key = _baseline_requirement_key(
            component_id=component_id,
            instance_id=instance_id,
            quota_name=quota_name,
            region=region,
        )
        baseline[key] = baseline.get(key, 0) + required
    if not baseline:
        return report

    adjusted_checks: list[QuotaCheck] = []
    for check in report.checks:
        adjusted_contributors: list[QuotaContributor] = []
        for contributor in check.contributors:
            key = _baseline_requirement_key(
                component_id=contributor.component_id,
                instance_id=contributor.instance_id,
                quota_name=check.quota_name,
                region=check.region,
            )
            remaining_baseline = baseline.get(key, 0)
            if remaining_baseline <= 0:
                adjusted_contributors.append(contributor)
                continue
            if remaining_baseline >= contributor.required:
                baseline[key] = remaining_baseline - contributor.required
                continue
            baseline[key] = 0
            adjusted_contributors.append(
                replace(
                    contributor,
                    required=contributor.required - remaining_baseline,
                    reason=(
                        f"{contributor.reason} (net-new after existing Terraform state discount)"
                    ),
                )
            )
        if not adjusted_contributors:
            continue
        component_ids = list(dict.fromkeys(item.component_id for item in adjusted_contributors))
        instance_ids = list(dict.fromkeys(item.instance_id for item in adjusted_contributors))
        component_labels = list(
            dict.fromkeys(item.component_label for item in adjusted_contributors)
        )
        component_id = component_ids[0] if len(component_ids) == 1 else "multiple"
        instance_id = instance_ids[0] if len(instance_ids) == 1 else "multiple"
        component_label = (
            component_labels[0]
            if len(component_labels) <= 1
            else f"{component_labels[0]} + {len(component_labels) - 1} more"
        )
        required = sum(item.required for item in adjusted_contributors)
        sufficient = None if check.available is None else required <= check.available
        adjusted_checks.append(
            replace(
                check,
                component_id=component_id,
                instance_id=instance_id,
                component_label=component_label,
                required=required,
                reason="; ".join(
                    f"{item.component_label}: {item.reason}" for item in adjusted_contributors
                ),
                sufficient=sufficient,
                contributors=tuple(adjusted_contributors),
            )
        )
    return replace(report, checks=tuple(adjusted_checks))


def _raise_on_generated_bundle_live_quota_issues(
    config: Any,
    paths: ProjectPaths,
    *,
    manifest: Mapping[str, Any] | None,
    runtime_env: Mapping[str, str] | None,
    phase: str,
) -> QuotaReport:
    terraform_init(paths.infra_dir, extra_env=dict(runtime_env or {}))
    report = _assess_live_quota_report(config, phase=phase)
    if manifest is not None:
        report = _adjust_quota_report_for_managed_mk8s_state(
            report,
            managed_requirements=_managed_mk8s_quota_requirements_from_terraform_state(
                config,
                paths,
                manifest,
                runtime_env=runtime_env,
            ),
        )
    _print_live_quota_report(report, phase=phase)
    if report.has_confirmed_insufficiency:
        _print_quota_remediation_hint(paths.config_path, report)
        _print_quota_check_all_regions_hint(paths.config_path, enabled=True)
        raise RuntimeError(_quota_failure_message(report, phase=phase))
    return report


DEFAULT_REGION_ID = "eu-north1"
SUPPORTED_REGION_IDS: tuple[str, ...] = (
    "eu-north1",
    "eu-west1",
    "me-west1",
    "us-central1",
    "eu-north2",
    "uk-south1",
)
NEBIUS_CI_SECRET_KEYS = [
    "NEBIUS_SA_ID",
    "NEBIUS_AUTH_PUBLIC_KEY_ID",
    "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
]
FLUX_SECRET_KEY = "FLUX_GITHUB_TOKEN"
WIZARD_EXIT_TOKEN = "q"
WIZARD_ABORT_TOKEN = "qq"
_WIZARD_BACK_CHOICE = "__wizard_back__"
_WIZARD_QUIT_CHOICE = "__wizard_quit__"
PayloadPath = tuple[str | int, ...]
_TEMP_PRIVATE_KEY_FILES: list[Path] = []
_RUNTIME_TF_SERVICE_ACCOUNT_NAME = "nebius-cxcli-tf-sa"
_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME = "mysterybox-sa"
_RUNTIME_AUTH_CACHE_ENV = "NEBIUS_CXCLI_RUNTIME_AUTH_DIR"
_RUNTIME_AUTH_CACHE_FILE = "runtime-auth.json"
_MYSTERYBOX_ESO_TLS_CHECK_IMAGE = "curlimages/curl:8.7.1"
_RUNTIME_AUTH_TOKEN_READY_TIMEOUT_ENV = "NEBIUS_CXCLI_RUNTIME_AUTH_TOKEN_READY_TIMEOUT_SECONDS"
_RUNTIME_AUTH_TOKEN_READY_POLL_ENV = "NEBIUS_CXCLI_RUNTIME_AUTH_TOKEN_READY_POLL_SECONDS"
_RUNTIME_AUTH_TOKEN_READY_TIMEOUT_SECONDS = 60.0
_RUNTIME_AUTH_TOKEN_READY_POLL_SECONDS = 2.0
_MYSTERYBOX_ESO_ROLE_IDS = ("mysterybox.payload-viewer",)
_SOPERATOR_APP_ID = "soperator"
_SOPERATOR_REQUIRED_INFRA_COMPONENT_IDS = ("mk8s", "sfs")
_SOPERATOR_REQUIRED_APP_COMPONENT_IDS = ("cert-manager",)
_BENIGN_KUBECTL_OUTPUT_MARKERS = (
    "token from NEBIUS_IAM_TOKEN env is used",
    "missing the kubectl.kubernetes.io/last-applied-configuration annotation",
    "The missing annotation will be patched automatically.",
    "Warning: v1 Endpoints is deprecated",
    "reflector.go:",
    "context canceled",
)
_POST_FLUX_WEBHOOK_TRANSIENT_MARKERS = (
    "failed calling webhook",
    "connect: connection refused",
    "connection reset by peer",
    "no endpoints available",
    "context deadline exceeded",
)
_WIZARD_BACKTRACK = object()
_WIZARD_DEFAULT_MISSING = object()


class _WizardBackRequested(Exception):
    """Raised when the interactive wizard should move to the previous step."""


class _WizardQuitRequested(Exception):
    """Raised when the interactive wizard should stop immediately."""


class _WizardComponentOutcome:
    CONTINUE = "continue"
    BACK = "back"
    QUIT = "quit"


_DEPLOYMENTS_ROOT_ARGUMENT_HELP = (
    "Deployments root directory. Pass the folder that contains or will contain "
    "<tenant-folder>/<project-folder>/config.yaml; any existing directory works. "
    "Do not pass a nested directory under another cxcli-managed deployments root."
)
_CONFIG_YAML_ARGUMENT_HELP = (
    "Path to project config.yaml under the deployments root "
    "(<tenant-folder>/<project-folder>/config.yaml)."
)
_COMPONENT_CONFIG_OPTION_HELP = (
    "Project config.yaml to inspect or edit; selectors stay unambiguous and are not path arguments."
)
_MK8S_TARGET_ID_HELP = (
    "MK8s target cluster instance_id (the normalized cluster resource name "
    "stored as that target's instance_id)"
)
_GENERATED_BUNDLE_CONFIG_ARGUMENT_HELP = (
    "Path to project config.yaml under the deployments root. "
    "This command resolves the sibling generated/ bundle automatically."
)
_DEPLOY_CONFIG_ARGUMENT_HELP = _GENERATED_BUNDLE_CONFIG_ARGUMENT_HELP
_WIREGUARD_CONFIG_ARGUMENT_HELP = (
    "Path to project config.yaml under the deployments root. The command reads "
    "the sibling generated/ Terraform state to find the deployed WireGuard VPN "
    "gateway, and requires both files to contain the same component row."
)
_GENERATED_PATH_ARGUMENT_HELP = (
    "Path to generated/, one of its subdirectories, or a file under generated/."
)
_GENERATED_INFRA_ARGUMENT_HELP = "Path to generated/ or generated/infra."
_GENERATED_FLUX_ARGUMENT_HELP = "Path to generated/ or generated/flux."
_GENERATED_INVENTORY_ARGUMENT_HELP = "Path to generated/ or generated/inventory."
_COMPONENT_SOURCES_ARGUMENT_HELP = (
    "Optional explicit component_sources.yaml path. "
    "The sibling component_cli_settings.yaml is loaded when present. "
    "When omitted, validate-sources uses the normal catalog resolution order "
    "from global flags, environment, and bundled defaults."
)


def _require_component_config_option(config_path: Path | None) -> Path:
    if config_path is None:
        raise RuntimeError(
            "Missing option '--config'. Pass the project config.yaml with --config <config.yaml>."
        )
    return config_path


app = typer.Typer(
    add_completion=False,
    help=(
        "Nebius artifact generator and deployer. Target guide: create bootstraps one "
        "name-based tenant/project folder from a deployments root directory and overwrites existing "
        "resolved project folders only with confirmation; component list/add/remove use "
        "--config CONFIG_YAML as the day-2 config.yaml editing surface; "
        "discover uses a deployment-scope directory; validate, validate-dashboards, "
        "quota-check, quota-request, render, deploy, and bootstrap-ci use config.yaml; "
        "destroy uses config.yaml to tear down all rendered project resources from sibling generated/; "
        "email also uses config.yaml and resolves sibling generated/ automatically; "
        "wireguard uses config.yaml to generate client configs and manage VM-local "
        "WireGuard route defaults from a deployed VPN gateway; "
        "ssh-jumphost uses config.yaml to manage VM-local SSH source CIDR allowlists; "
        "validate-generated uses generated/, terraform uses generated/infra, flux uses generated/flux, "
        "validate-sources accepts optional component_sources.yaml plus its sibling settings file, and "
        "auth has no positional path."
    ),
)
component_app = typer.Typer(
    help=(
        "Inspect or edit enabled source-driven infra/app component instances in an "
        "existing config.yaml. Use --config CONFIG_YAML after create for day-2 "
        "add/remove/list changes."
    )
)
terraform_app = typer.Typer(
    help="Run infra-only Terraform operations against generated/ or generated/infra."
)
flux_app = typer.Typer(
    help="Apply, bootstrap, or destroy Flux resources using generated/ or generated/flux."
)

app.add_typer(component_app, name="component")
app.add_typer(terraform_app, name="terraform")
app.add_typer(flux_app, name="flux")


def _version_callback(value: bool) -> bool:
    if value:
        console.print(f"nebius-cxcli {__version__}")
        raise typer.Exit()
    return value


def _cleanup_temp_private_key_files() -> None:
    for key_path in _TEMP_PRIVATE_KEY_FILES:
        try:
            key_path.unlink()
        except FileNotFoundError:
            continue
        except Exception:
            continue


atexit.register(_cleanup_temp_private_key_files)


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version"),
    ] = False,
    component_sources_file: Annotated[
        Path | None,
        typer.Option(
            "--component-sources-file",
            help=(
                "Global optional override for the component sources file. "
                "Use this to point nebius-cxcli at a different component_sources.yaml path. "
                "When omitted, nebius-cxcli resolves the default file name "
                "'component_sources.yaml' from the standard search order "
                "(cwd -> env -> user/global -> repo/bundled)."
            ),
        ),
    ] = None,
    source_profile: Annotated[
        SourceProfile | None,
        typer.Option(
            "--source-profile",
            help=(
                "Global optional override for the active component source profile. "
                "Defaults to portable. portable always uses source.portable. "
                "local prefers source.local and falls back to source.portable when "
                "source.local is unset."
            ),
            case_sensitive=False,
        ),
    ] = None,
) -> None:
    _ = version
    try:
        set_component_sources_file_override(component_sources_file)
        set_component_sources_profile_override(source_profile)
    except ValueError as exc:
        _exit_with_error(RuntimeError(str(exc)))


def _load_context(config_path: Path) -> tuple:
    config = load_config(config_path, persist_normalized=True)
    payload = to_plain_data(config)
    if isinstance(payload, dict) and materialize_compute_boot_disk_defaults(payload):
        strip_app_chart_target_refs(payload)
        config = validate_config(payload, base_dir=config_path.parent)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    return config, paths


def _load_context_readonly(config_path: Path) -> tuple:
    config = load_config(config_path, persist_normalized=False)
    payload = to_plain_data(config)
    if isinstance(payload, dict) and materialize_compute_boot_disk_defaults(payload):
        strip_app_chart_target_refs(payload)
        config = validate_config(payload, base_dir=config_path.parent)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    return config, paths


def _load_runtime_context(
    config_path: Path,
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
) -> tuple:
    validation_cache = _ValidationWorkCache()
    if chart_meta_cache is not None:
        validation_cache.chart_meta_cache = chart_meta_cache
    resolved_source_profile = resolve_component_sources_profile()
    phase_defs = [
        _ValidationPhase("load-config", "Load config and component catalog"),
        _ValidationPhase("active-sources", "Validate active component catalog/settings"),
        _ValidationPhase("dependencies", "Validate component dependencies"),
        _ValidationPhase("module-schema", "Validate Terraform module inputs"),
    ]
    with _ValidationProgress(title="Pre-render validation", phases=phase_defs) as progress:
        config, paths = progress.run("load-config", lambda: _load_context(config_path))
        progress.run(
            "active-sources",
            lambda: _validate_active_component_sources(
                config,
                chart_meta_cache=validation_cache.chart_meta_cache,
            ),
        )
        dependency_issues = progress.run(
            "dependencies",
            lambda: _validate_component_dependencies(
                config,
                chart_meta_cache=validation_cache.chart_meta_cache,
            ),
        )
        if dependency_issues:
            raise RuntimeError(
                "Runtime validation failed:\n  - " + "\n  - ".join(dependency_issues)
            )
        progress.run(
            "module-schema",
            lambda: rendered_module_sources(config, source_profile=resolved_source_profile),
        )
    return config, paths


def _load_generated_context(target_path: Path) -> tuple:
    paths = resolve_generated_paths(target_path)
    return _load_manifest_backed_context(paths)


def _load_generated_infra_context(target_path: Path) -> tuple:
    paths = resolve_generated_infra_paths(target_path)
    return _load_manifest_backed_context(paths)


def _load_generated_flux_context(target_path: Path) -> tuple:
    paths = resolve_generated_flux_paths(target_path)
    return _load_manifest_backed_context(paths)


def _load_deploy_context(target_path: Path) -> tuple:
    paths = resolve_deploy_config_paths(target_path)
    return _load_manifest_backed_context(paths)


def _load_destroy_context(target_path: Path) -> tuple:
    paths = resolve_destroy_config_paths(target_path)
    return _load_manifest_backed_context(paths)


def _load_email_context(target_path: Path) -> tuple:
    paths = resolve_email_config_paths(target_path)
    return _load_manifest_backed_context(paths)


def _select_deployed_day2_component(
    *,
    config_path: Path,
    generated_config: Any,
    component_label: str,
    select_component: Callable[..., Any],
    operation_label: str,
) -> Any:
    def _mismatch_error() -> RuntimeError:
        return RuntimeError(
            f"{component_label} is enabled in config.yaml but is not present "
            "in the rendered/deployed generated bundle. Run "
            f"`nebius-cxcli render {config_path}` and "
            f"`nebius-cxcli deploy {config_path}` before running "
            f"{operation_label} day-2 operations."
        )

    try:
        selected = select_component(
            generated_config,
            component_selector=component_label,
        )
    except RuntimeError as exc:
        raise _mismatch_error() from exc
    if getattr(selected, "label", None) != component_label:
        raise _mismatch_error()
    return selected


def _load_manifest_backed_context(paths: ProjectPaths) -> tuple:
    manifest = load_generated_manifest(paths.generated_dir)
    _apply_generated_tool_version_overrides(manifest)
    _materialize_generated_terraform_tfvars(paths, manifest)
    config = runtime_config_from_manifest(manifest)
    return config, paths, manifest


def _apply_generated_tool_version_overrides(manifest: Mapping[str, Any]) -> None:
    tools = manifest.get("tools")
    if not isinstance(tools, Mapping):
        return
    flux_version = str(tools.get("flux_version", "")).strip()
    terraform_version = str(tools.get("terraform_version", "")).strip()
    if flux_version and not os.environ.get(FLUX_VERSION_ENV, "").strip():
        os.environ[FLUX_VERSION_ENV] = flux_version
    if terraform_version and not os.environ.get(TERRAFORM_VERSION_ENV, "").strip():
        os.environ[TERRAFORM_VERSION_ENV] = terraform_version


def _materialize_generated_terraform_tfvars(
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
) -> Path:
    payload = terraform_tfvars_from_manifest(manifest)
    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    tfvars_path.parent.mkdir(parents=True, exist_ok=True)
    tfvars_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return tfvars_path


def _render_overwrite_warning(paths: ProjectPaths) -> str | None:
    if not paths.generated_dir.exists():
        return None
    existing_files = sorted(path for path in paths.generated_dir.rglob("*") if path.is_file())
    if not existing_files:
        return None
    return (
        "Render will overwrite existing generated artifacts under "
        f"{paths.generated_dir}. Keep using `config.yaml` as the original render contract, "
        "but treat the generated files as the deployable customer artifacts."
    )


def _can_prompt_for_render_overwrite() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm_render_overwrite(paths: ProjectPaths, *, force: bool) -> bool:
    overwrite_warning = _render_overwrite_warning(paths)
    if not overwrite_warning:
        return True
    console.print(f"{warning_markup('WARNING:', bold=True)} {overwrite_warning}")
    if force:
        return True
    if not _can_prompt_for_render_overwrite():
        raise RuntimeError(
            "Render would overwrite existing generated artifacts in a non-interactive session. "
            "Re-run with `--force` to confirm the reset."
        )
    return typer.confirm(
        "Continue and overwrite the existing generated artifacts?",
        default=False,
        show_default=True,
    )


def _confirm_generated_destroy(
    *,
    yes: bool,
    action_label: str,
    prompt_text: str,
    warning_text: str,
) -> bool:
    console.print(f"{warning_markup('WARNING:', bold=True)} {warning_text}")
    if yes:
        return True
    if not _can_prompt_for_render_overwrite():
        raise RuntimeError(
            f"{action_label} is destructive in a non-interactive session. Re-run with `--yes` to confirm."
        )
    return typer.confirm(
        prompt_text,
        default=False,
        show_default=True,
    )


def _exit_with_error(exc: Exception) -> None:
    console.print(f"{error_markup('ERROR:', bold=True)} {escape(str(exc))}")
    raise typer.Exit(code=1) from exc


@dataclass(frozen=True)
class _ValidationPhase:
    key: str
    label: str


@dataclass
class _ValidationWorkCache:
    chart_meta_cache: _ChartMetaCache = field(default_factory=dict)


class _ValidationProgress:
    def __init__(self, *, title: str, phases: Sequence[_ValidationPhase]) -> None:
        self._title = title
        self._phases = tuple(phases)
        self._phase_by_key = {phase.key: phase for phase in self._phases}
        self._progress: Progress | None = None
        self._task_id: int | None = None

    def __enter__(self) -> _ValidationProgress:
        if _console_is_terminal():
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=False,
            )
            self._progress.__enter__()
            self._task_id = self._progress.add_task(
                self._title,
                total=max(len(self._phases), 1),
            )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._progress is not None:
            if exc_type is None and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=f"{self._title} completed",
                    completed=max(len(self._phases), 1),
                    total=max(len(self._phases), 1),
                )
            self._progress.__exit__(exc_type, exc, tb)
            self._progress = None
            self._task_id = None

    def run(self, phase_key: str, fn: Callable[[], Any]) -> Any:
        phase = self._phase_by_key[phase_key]
        if self._progress is None:
            console.print(f"[cyan]{self._title}:[/cyan] {phase.label}")
        else:
            assert self._task_id is not None
            self._progress.update(self._task_id, description=f"{self._title}: {phase.label}")
        result = fn()
        if self._progress is not None:
            assert self._task_id is not None
            self._progress.advance(self._task_id, 1)
        return result


def _run_runtime_validation(
    *,
    config_path: Path,
    strict: bool,
    title: str = "Runtime validation",
) -> None:
    phase_defs = [
        _ValidationPhase("load-config", "Load config and component catalog"),
        _ValidationPhase("active-sources", "Validate active component catalog/settings"),
        _ValidationPhase("dependencies", "Validate component dependencies"),
        _ValidationPhase("module-schema", "Validate Terraform module inputs"),
    ]
    if strict:
        phase_defs.extend(
            [
                _ValidationPhase("strict-readiness", "Validate strict deployment readiness"),
                _ValidationPhase("mk8s-preflight", "Validate MK8s network preflight"),
                _ValidationPhase("quota-readiness", "Validate live Nebius quota/capacity"),
            ]
        )
    else:
        phase_defs.append(_ValidationPhase("quota-readiness", "Check live Nebius quota/capacity"))

    validation_cache = _ValidationWorkCache()
    resolved_source_profile = resolve_component_sources_profile()
    validated_scope_summary_lines: list[str] | None = None
    quota_report: QuotaReport | None = None
    with _ValidationProgress(title=title, phases=phase_defs) as progress:
        config, paths = progress.run("load-config", lambda: _load_context(config_path))
        progress.run(
            "active-sources",
            lambda: _validate_active_component_sources(
                config,
                chart_meta_cache=validation_cache.chart_meta_cache,
            ),
        )
        dependency_issues = progress.run(
            "dependencies",
            lambda: _validate_component_dependencies(
                config,
                chart_meta_cache=validation_cache.chart_meta_cache,
            ),
        )
        if dependency_issues:
            raise RuntimeError(
                "Runtime validation failed:\n  - " + "\n  - ".join(dependency_issues)
            )
        progress.run(
            "module-schema",
            lambda: rendered_module_sources(config, source_profile=resolved_source_profile),
        )
        if not strict:
            quota_report = progress.run(
                "quota-readiness",
                lambda: _warn_on_live_quota_issues(config, phase="validate"),
            )
        if strict:
            progress.run(
                "strict-readiness",
                lambda: _validate_strict_config(
                    config,
                    chart_meta_cache=validation_cache.chart_meta_cache,
                    include_common_checks=False,
                ),
            )
            progress.run("mk8s-preflight", lambda: validate_mk8s_network_preflight(config))
            quota_report = progress.run(
                "quota-readiness",
                lambda: _raise_on_config_live_quota_issues(
                    config,
                    paths,
                    phase="validate",
                ),
            )
        validated_scope_summary_lines = _validation_scope_summary_lines(
            config,
            source_profile=resolved_source_profile,
        )

    if validated_scope_summary_lines:
        for line in validated_scope_summary_lines:
            console.print(line)
    _print_mk8s_gpu_validation_warnings(config)
    if strict:
        console.print(f"[green]Valid:[/green] {config_path}")
        return
    if quota_report is not None and (
        quota_report.has_confirmed_insufficiency
        or quota_report.errors
        or quota_report.coverage_gaps
        or quota_report.unknown_checks
    ):
        console.print(f"{warning_markup('Valid with quota warnings:')} {config_path}")
        if quota_report.has_confirmed_insufficiency:
            _print_quota_remediation_hint(config_path, quota_report)
        return
    console.print(f"[green]Valid:[/green] {config_path}")


def _print_mk8s_gpu_validation_warnings(payload_or_config: Any) -> None:
    for warning in mk8s_gpu_validation_warnings(payload_or_config):
        console.print(f"{warning_markup('Deploy validation warning:')} {warning}")


def _configure_quiet_native_logs() -> None:
    """Reduce noisy native gRPC/absl logs while keeping warnings/errors visible."""
    for env_name, env_value in native_logs.QUIET_NATIVE_LOG_ENV_DEFAULTS.items():
        if not os.environ.get(env_name):
            os.environ[env_name] = env_value


def _filter_benign_kubectl_output(text: str) -> str:
    kept_lines = [
        line
        for line in text.splitlines()
        if not any(marker in line for marker in _BENIGN_KUBECTL_OUTPUT_MARKERS)
    ]
    return "\n".join(kept_lines).strip()


def _effective_catalog_component_source(
    *, row: Mapping[str, Any], entry: ComponentEntry | None
) -> str:
    if entry is not None and str(entry.source or "").strip():
        return str(entry.source).strip()
    return str(row.get("source", "")).strip()


def _entry_module_metadata_source(
    entry: ComponentEntry | None,
    *,
    fallback_source: str = "",
) -> str:
    if entry is not None and str(entry.metadata_source or "").strip():
        return str(entry.metadata_source).strip()
    return str(fallback_source).strip()


def _effective_catalog_component_version(
    *, row: Mapping[str, Any], entry: ComponentEntry | None
) -> str:
    if entry is not None and str(entry.version or "").strip():
        return str(entry.version).strip()
    return str(row.get("version", "")).strip()


def _resolve_deployments_root(base_path: Path) -> Path:
    """Treat user-provided target path as the deployments root."""
    return base_path.resolve()


def _relative_deployments_dir_for_ci(repo_root: Path, deployments_root: Path) -> str:
    try:
        return deployments_root.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Deployments directory '{deployments_root}' must be inside git root '{repo_root}'"
        ) from exc


def _relative_discover_target_for_ci(repo_root: Path, deployments_root: Path) -> str:
    try:
        return deployments_root.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Deployments directory '{deployments_root}' must be inside git root '{repo_root}'"
        ) from exc


def _try_git_root(start: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return Path(result.stdout.strip()).resolve()
    except Exception:
        return None


def _require_git_root(start: Path) -> Path:
    repo_root = _try_git_root(start)
    if repo_root is not None:
        return repo_root
    raise RuntimeError(
        "Target path must be inside a git repository. "
        "Clone the customer private repo and rerun this command."
    )


def _validate_deployments_root_target(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(
            f"Target directory does not exist: {path}. "
            "Create an empty folder and pass that path to create, or pass an existing "
            "deployment-scope directory to discover. "
            "For CI workflow generation, use a path inside the customer git repository."
        )
    if not path.is_dir():
        raise RuntimeError(f"Target directory must be a directory: {path}")


def _value_or_prompt(
    value: str | None,
    *,
    option_name: str,
    prompt_text: str,
    interactive: bool,
    default_value: str | None = None,
) -> str:
    if value:
        return value
    normalized_default = _non_empty_text(default_value) or None
    if normalized_default is not None and not interactive:
        return normalized_default
    if interactive:
        if normalized_default is not None:
            prompted = typer.prompt(prompt_text, default=normalized_default).strip()
            if prompted:
                return prompted
            return normalized_default
        prompted = typer.prompt(prompt_text).strip()
        if prompted:
            return prompted
    raise RuntimeError(f"Missing required option: {option_name}")


def _validate_client_name_or_raise(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise RuntimeError("Missing required option: --client-name")
    if not INSTANCE_ID_PATTERN.fullmatch(normalized):
        raise RuntimeError(
            "client_info.client_name must use lowercase letters, digits, and hyphens"
        )
    return normalized


def _client_name_or_prompt(value: str | None, *, interactive: bool) -> str:
    if value:
        return _validate_client_name_or_raise(value)
    if not interactive:
        raise RuntimeError("Missing required option: --client-name")
    while True:
        prompted = typer.prompt("Client name (lowercase letters, digits, and hyphens)").strip()
        try:
            return _validate_client_name_or_raise(prompted)
        except RuntimeError as exc:
            console.print(f"{error_markup('Invalid value')}. {exc}")


def _optional_email_or_prompt(value: str | None, *, interactive: bool) -> str | None:
    if value is not None:
        return value
    if not interactive:
        return None
    prompted = typer.prompt(
        "Notifications email (optional; leave blank to keep email disabled)",
        default="",
    ).strip()
    return prompted or None


def _non_empty_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validate_tenant_project_ids_or_prompt(
    *,
    tenant_id: str,
    project_id: str,
    interactive: bool,
    provider_lookup: ProviderOptionLookup,
) -> tuple[str, str]:
    current_tenant_id = tenant_id.strip()
    current_project_id = project_id.strip()
    while True:
        result: TenantProjectValidationResult = provider_lookup.validate_tenant_project_scope(
            tenant_id=current_tenant_id,
            project_id=current_project_id,
        )
        if result.valid:
            return current_tenant_id, current_project_id

        if not interactive:
            raise RuntimeError(
                f"Nebius scope validation failed for tenant/project selection: {result.message}"
            )

        if not result.retryable:
            raise RuntimeError(f"Nebius scope validation failed: {result.message}")

        console.print(f"{warning_markup('Nebius scope validation warning')}: {result.message}")
        current_tenant_id = typer.prompt("Tenant ID", default=current_tenant_id).strip()
        current_project_id = typer.prompt("Project ID", default=current_project_id).strip()


def _region_or_prompt(value: str | None, *, interactive: bool) -> str:
    if value:
        return value
    if interactive:
        if _is_tty_session():
            try:
                import questionary

                selected = questionary.select(
                    "Region ID",
                    choices=[
                        questionary.Choice(title=region, value=region)
                        for region in SUPPORTED_REGION_IDS
                    ],
                    instruction="Select one region.",
                    qmark="",
                ).ask()
                if selected:
                    return str(selected).strip()
            except Exception:
                pass

        available = ", ".join(SUPPORTED_REGION_IDS)
        while True:
            selected = (
                typer.prompt("Region ID", default=DEFAULT_REGION_ID).strip() or DEFAULT_REGION_ID
            )
            if selected in SUPPORTED_REGION_IDS:
                return selected
            console.print(f"{error_markup('Invalid region')}. Expected one of: {available}")
    return DEFAULT_REGION_ID


def _warn_existing_project_overwrite(*, config_path: Path) -> None:
    project_path = config_path.parent
    console.print(
        f"{warning_markup('Existing project detected.')} "
        f"Re-running `create` will replace the resolved project folder [bold]{project_path}[/bold] from scratch."
    )
    console.print(
        "[dim]Existing infra/apps selections, generated artifacts, and any other files under "
        "that resolved project folder will not be preserved. Any follow-up prompts restart "
        "from the normal create defaults instead of reusing the old config values.[/dim]"
    )
    console.print(
        "[dim]Use `component list/add/remove --config <config.yaml>` for day-2 "
        "component edits without replacing the project folder.[/dim]"
    )


def _confirm_existing_project_overwrite(*, config_path: Path) -> bool:
    _warn_existing_project_overwrite(config_path=config_path)
    console.print(
        "[dim]This only affects that one resolved project folder. "
        "It does not delete the deployments root or unrelated projects.[/dim]"
    )
    return _wizard_continue_phase(
        "Continue and overwrite the existing project folder from scratch?",
        default=False,
    )


def _is_tty_session() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _configure_questionary_checkbox_symbols() -> None:
    """Use classic checkbox markers for questionary multi-select prompts."""
    try:
        from questionary.prompts import common as questionary_common

        questionary_common.INDICATOR_SELECTED = "[x]"
        questionary_common.INDICATOR_UNSELECTED = "[ ]"
    except Exception:
        # Keep questionary defaults if internals are unavailable/changed.
        return


def _ask_questionary_with_wizard_navigation(question: Any) -> Any:
    application = getattr(question, "application", None)
    bindings = getattr(application, "key_bindings", None)
    if bindings is not None:
        try:

            @bindings.add(*tuple(WIZARD_ABORT_TOKEN), eager=True)
            def _wizard_quit(event: Any) -> None:
                event.app.exit(result=_WIZARD_QUIT_CHOICE)

            @bindings.add(WIZARD_EXIT_TOKEN, eager=False)
            def _wizard_back(event: Any) -> None:
                event.app.exit(result=_WIZARD_BACK_CHOICE)

        except Exception:
            # Keep the prompt usable if questionary/prompt-toolkit internals change.
            pass
    return question.ask()


def _split_multi_value_tokens(raw_values: list[str] | None) -> list[str]:
    if not raw_values:
        return []
    tokens: list[str] = []
    for raw in raw_values:
        for part in raw.split(","):
            token = part.strip()
            if token:
                tokens.append(token)
    return tokens


def _load_config_payload(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("config.yaml root must be a mapping")
    return payload


def _config_cli_arg(config_path: Path) -> str:
    return shlex.quote(str(config_path.resolve()))


def _print_component_edit_next_steps(config_path: Path) -> None:
    config_arg = _config_cli_arg(config_path)
    console.print(
        "Next steps: run "
        f"`nebius-cxcli validate {config_arg}`, then "
        f"`nebius-cxcli render {config_arg}`.",
        soft_wrap=True,
    )


def _print_create_next_steps(config_path: Path) -> None:
    config_arg = _config_cli_arg(config_path)
    console.print("Next steps:")
    for command, suffix in (
        (f"nebius-cxcli validate {config_arg}", ""),
        (f"nebius-cxcli render {config_arg}", ""),
        (f"nebius-cxcli deploy {config_arg}", ""),
        (f"nebius-cxcli bootstrap-ci {config_arg}", " (optional)"),
    ):
        console.print(f"  `{command}`{suffix}", soft_wrap=True)


def _print_component_edit_config_only_note() -> None:
    console.print(
        "Only config.yaml was updated. Existing generated/ artifacts and live resources are "
        "unchanged until you run render and then deploy/destroy as needed."
    )


def _component_source_validation_failure_message(
    source_path: Path,
    source_issues: Sequence[str],
    *,
    include_skip_guidance: bool = False,
) -> str:
    message = (
        f"Component catalog/settings validation failed for {source_path}:\n  - "
        + "\n  - ".join(source_issues)
        + "\n\nThis validation checks the full component catalog, including optional app charts. "
        "If the failure is a transient Helm repository, OCI registry, or network timeout, "
        "retry or increase NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS."
    )
    if include_skip_guidance:
        message += (
            " During create/component add, rerun with --no-validate-sources "
            "to skip this source check."
        )
    return message


def _validate_component_sources_or_raise() -> None:
    with console.status("[cyan]Validating component catalog/settings...[/cyan]"):
        source_path, source_issues, source_warnings = _validate_component_sources_registry()
    for warning in source_warnings:
        console.print(f"{warning_markup('Source validation warning:')} {warning}")
    if source_issues:
        raise RuntimeError(
            _component_source_validation_failure_message(
                source_path,
                source_issues,
                include_skip_guidance=True,
            )
        )


def _format_component_id_sample(component_ids: Sequence[str], *, limit: int = 8) -> str:
    sample = list(component_ids[:limit])
    suffix = ""
    remaining = len(component_ids) - len(sample)
    if remaining > 0:
        suffix = f", and {remaining} more"
    return ", ".join(sample) + suffix


def _component_source_tool_preflight_issues(*, explicit: Path | None = None) -> tuple[str, ...]:
    """Return missing-tool issues that can be checked before interactive prompts."""
    source_path = resolve_component_sources_file(explicit=explicit)
    sources = load_component_sources(explicit=explicit)
    issues: list[str] = []

    helm_chart_ids = sorted(
        _non_empty_text(chart.name) for chart in sources.helm_charts if _non_empty_text(chart.name)
    )
    if helm_chart_ids and not shutil.which("helm"):
        issues.append(
            "helm is required for component source validation because "
            f"{source_path} declares Helm app charts: "
            f"{_format_component_id_sample(helm_chart_ids)}. "
            "Install helm or rerun with --no-validate-sources."
        )
    git_tree_chart_ids = sorted(
        _non_empty_text(chart.name)
        for chart in sources.helm_charts
        if _non_empty_text(chart.name) and _is_github_tree_chart_repo(_non_empty_text(chart.repo))
    )
    if git_tree_chart_ids and not shutil.which("git"):
        issues.append(
            "git is required for component source validation because "
            f"{source_path} declares Git tree Helm app charts: "
            f"{_format_component_id_sample(git_tree_chart_ids)}. "
            "Install git or rerun with --no-validate-sources."
        )
    return tuple(issues)


def _preflight_component_source_tools_or_raise() -> None:
    issues = _component_source_tool_preflight_issues()
    if issues:
        raise RuntimeError(
            "Component source validation requires missing external tool(s):\n  - "
            + "\n  - ".join(issues)
        )


def _identity_values_from_payload(
    payload: Mapping[str, Any],
) -> tuple[str, str, str, str, str | None]:
    client_info = payload.get("client_info")
    if not isinstance(client_info, Mapping):
        raise RuntimeError("config.yaml is missing client_info")
    nebius = client_info.get("nebius")
    if not isinstance(nebius, Mapping):
        raise RuntimeError("config.yaml is missing client_info.nebius")
    notifications = client_info.get("notifications")
    if not isinstance(notifications, Mapping):
        raise RuntimeError("config.yaml is missing client_info.notifications")
    return (
        _non_empty_text(client_info.get("client_name")),
        _non_empty_text(nebius.get("tenant_id")),
        _non_empty_text(nebius.get("project_id")),
        _non_empty_text(nebius.get("region_id")),
        str(notifications.get("email")).strip() if notifications.get("email") is not None else None,
    )


def _dependency_seed_payload(
    *,
    client_name: str,
    tenant_id: str,
    project_id: str,
    region_id: str,
    email: str | None,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    existing_payload: dict[str, Any] | None,
    merge_existing: bool,
) -> dict[str, Any] | None:
    if not selected_apps:
        return None
    dependency_seed_yaml = starter_config_yaml(
        client_name=client_name,
        tenant_id=tenant_id,
        project_id=project_id,
        region_id=region_id,
        email=email,
        selected_infra=selected_infra,
        selected_apps=selected_apps,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    parsed_seed_payload = yaml.safe_load(dependency_seed_yaml) or {}
    if not isinstance(parsed_seed_payload, dict):
        return None
    if merge_existing and existing_payload is not None:
        parsed_seed_payload = _deep_merge_payload(parsed_seed_payload, existing_payload)
        parsed_seed_payload = _filter_runtime_payload_for_selected_components(
            payload=parsed_seed_payload,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
    materialize_shared_defaults(
        payload=parsed_seed_payload,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    return parsed_seed_payload


def _starter_component_payload(
    *,
    client_name: str,
    tenant_id: str,
    project_id: str,
    region_id: str,
    email: str | None,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    app_namespace_overrides: dict[str, str] | None = None,
    app_releasename_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    starter_yaml = starter_config_yaml(
        client_name=client_name,
        tenant_id=tenant_id,
        project_id=project_id,
        region_id=region_id,
        email=email,
        selected_infra=selected_infra,
        selected_apps=selected_apps,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    starter_payload = yaml.safe_load(starter_yaml) or {}
    if not isinstance(starter_payload, dict):
        raise RuntimeError("Generated starter config payload must be a mapping")
    starter_payload = _filter_runtime_payload_for_selected_components(
        payload=starter_payload,
        selected_infra=selected_infra,
        selected_apps=selected_apps,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    _apply_app_release_overrides(
        payload=starter_payload,
        selected_apps=selected_apps,
        namespace_overrides=app_namespace_overrides or {},
        release_name_overrides=app_releasename_overrides or {},
    )
    materialize_shared_defaults(
        payload=starter_payload,
        infra_entries=infra_entries,
        app_entries=app_entries,
    )
    _seed_infra_project_scope_defaults(
        payload=starter_payload,
        infra_entries=infra_entries,
    )
    _seed_infra_shared_admin_ssh_public_key(
        payload=starter_payload,
        infra_entries=infra_entries,
    )
    normalize_runtime_config_payload(starter_payload)
    return starter_payload


def _ensure_payload_contains_component_rows(
    *,
    payload: dict[str, Any],
    seed_payload: dict[str, Any],
) -> None:
    scopes = (
        ("infra", "components"),
        ("apps", "charts"),
    )
    for scope_name, collection_name in scopes:
        section = payload.get(scope_name)
        if not isinstance(section, dict):
            section = {}
            payload[scope_name] = section
        seed_section = seed_payload.get(scope_name)
        if not isinstance(seed_section, dict):
            continue
        rows = section.get(collection_name)
        if not isinstance(rows, list):
            rows = []
            section[collection_name] = rows
        seed_rows = seed_section.get(collection_name)
        if not isinstance(seed_rows, list):
            continue
        existing_keys = {
            (component_type_id(item), component_instance_id(item))
            for item in rows
            if isinstance(item, dict)
        }
        for item in seed_rows:
            if not isinstance(item, dict):
                continue
            dedupe_key = (component_type_id(item), component_instance_id(item))
            if dedupe_key in existing_keys:
                continue
            rows.append(copy.deepcopy(item))
            existing_keys.add(dedupe_key)


def _component_selector_scope_label(scope: ComponentScope) -> str:
    return "INFRA" if scope == "infra" else "APPS"


def _prompt_component_scope_selection(
    *,
    action: str,
    scope: ComponentScope,
    entries: tuple[ComponentEntry, ...],
) -> set[str]:
    if not entries:
        console.print(
            f"{_component_selector_scope_label(scope)} components available to {action}: (none)"
        )
        return set()
    selected = _prompt_component_with_checkboxes(
        scope=scope,
        entries=entries,
        defaults=set(),
    )
    selected_ids = {token for token in selected if token}
    _print_component_scope_selection_summary(
        scope=scope,
        selected=selected_ids,
        entries=entries,
    )
    return selected_ids


def _prompt_component_instance_selection(
    *,
    action: str,
    scope: ComponentScope,
    specs: tuple[tuple[ComponentEntry, dict[str, Any]], ...],
) -> set[str]:
    if not specs:
        console.print(
            f"{_component_selector_scope_label(scope)} component instances available to {action}: (none)"
        )
        return set()

    if _is_tty_session():
        try:
            import questionary
        except Exception as exc:
            install_hint = f"{sys.executable} -m pip install questionary"
            console.print(
                f"{warning_markup('Interactive checkbox UI unavailable:')} "
                f"{exc}. Falling back to text prompt. "
                f"Install it with: {install_hint}"
            )
        else:
            _configure_questionary_checkbox_symbols()
            selected = questionary.checkbox(
                f"Select {scope} component instances",
                choices=[
                    questionary.Choice(
                        title=(
                            f"{_component_instance_selector_label(entry, instance_id=str(row['instance_id']))}"
                            f"  ({entry.description})"
                        ),
                        value=str(row["instance_id"]),
                        checked=False,
                    )
                    for entry, row in specs
                ],
                instruction="Use arrows and space to toggle; press Enter to confirm.",
                qmark="",
            ).ask()
            if selected is None:
                raise typer.Abort()
            return {str(item).strip().lower() for item in selected if str(item).strip()}

    console.print(f"\n{scope.upper()} component instances:")
    for index, (entry, row) in enumerate(specs, start=1):
        instance_id = str(row["instance_id"])
        label = _component_instance_selector_label(entry, instance_id=instance_id)
        console.print(f"  [ ] [{index}] {label:<36} {entry.description}")
    raw = typer.prompt(
        f"Select {scope} component instances to {action} (comma-separated ids or indexes)",
        default="",
    ).strip()
    tokens = _split_multi_value_tokens([raw])
    if not tokens:
        return set()
    selected: set[str] = set()
    by_index = {
        str(index): str(row["instance_id"]) for index, (_entry, row) in enumerate(specs, start=1)
    }
    by_instance = {str(row["instance_id"]): str(row["instance_id"]) for _entry, row in specs}
    for token in tokens:
        if token in by_index:
            selected.add(by_index[token])
            continue
        normalized = normalize_component_token(token)
        if normalized in by_instance:
            selected.add(by_instance[normalized])
            continue
        raise RuntimeError(
            f"Unknown {scope} component instance '{token}'. Choose a listed instance id or index."
        )
    return selected


@dataclass(frozen=True)
class _ComponentAddTarget:
    scope: ComponentScope
    component_id: str
    requested_instance_id: str | None = None
    allocate_new_infra_instance_if_enabled: bool = False


@dataclass(frozen=True)
class _ComponentRemoveTarget:
    scope: ComponentScope
    component_id: str
    instance_id: str


def _parse_scoped_component_selector(token: str) -> tuple[ComponentScope | None, str]:
    normalized = token.strip().lower()
    if ":" not in normalized:
        return None, normalized
    scope_raw, body = normalized.split(":", maxsplit=1)
    scope = cast(ComponentScope, scope_raw)
    if scope not in {"infra", "apps"}:
        raise RuntimeError(
            f"Invalid component selector '{token}'. Use '<component-id>' or 'infra:<id>' / 'apps:<id>'."
        )
    return scope, body.strip()


def _resolve_component_add_targets(
    *,
    tokens: list[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> list[_ComponentAddTarget]:
    infra_lookup = {entry.id: entry for entry in infra_entries}
    app_lookup = {entry.id: entry for entry in app_entries}
    lookup = {**infra_lookup, **app_lookup}

    normalized = [token.strip() for token in tokens if token.strip()]
    if len(normalized) == 1:
        keyword = normalized[0].lower()
        if keyword == "none":
            return []
        if keyword == "all":
            return [
                *(
                    _ComponentAddTarget(scope="infra", component_id=entry.id)
                    for entry in infra_entries
                ),
                *(
                    _ComponentAddTarget(scope="apps", component_id=entry.id)
                    for entry in app_entries
                ),
            ]

    targets: list[_ComponentAddTarget] = []
    for token in normalized:
        scope, body = _parse_scoped_component_selector(token)
        component_raw, separator, instance_raw = body.partition("@")
        component_id = normalize_component_token(component_raw)
        if not component_id:
            raise RuntimeError(f"Invalid component selector '{token}'. Component id is required.")
        entry = lookup.get(component_id)
        if entry is None:
            available = ", ".join(sorted(lookup))
            raise RuntimeError(f"Unknown component id '{component_id}'. Available ids: {available}")
        if scope is not None and entry.scope != scope:
            raise RuntimeError(
                f"Component selector '{token}' targets scope '{scope}', but '{component_id}' is declared under '{entry.scope}'."
            )
        requested_instance_id = normalize_component_token(instance_raw) if separator else ""
        if requested_instance_id and not INSTANCE_ID_PATTERN.fullmatch(requested_instance_id):
            raise RuntimeError(
                f"Invalid resource name or target id '{instance_raw}'. "
                "Expected lowercase letters, digits, and hyphens."
            )
        targets.append(
            _ComponentAddTarget(
                scope=entry.scope,
                component_id=component_id,
                requested_instance_id=requested_instance_id or None,
                allocate_new_infra_instance_if_enabled=(
                    entry.scope == "infra" and not requested_instance_id
                ),
            )
        )
    return targets


def _resolve_component_remove_targets(
    *,
    tokens: list[str],
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> tuple[list[_ComponentRemoveTarget], tuple[str, ...]]:
    enabled_specs = [
        *_enabled_component_instance_specs(payload, scope="infra", entries=infra_entries),
        *_enabled_component_instance_specs(payload, scope="apps", entries=app_entries),
    ]
    by_instance: dict[str, list[_ComponentRemoveTarget]] = {}
    by_scope_and_type: dict[tuple[ComponentScope, str], list[_ComponentRemoveTarget]] = {}
    for entry, row in enabled_specs:
        instance_id = str(row["instance_id"])
        target = _ComponentRemoveTarget(
            scope=entry.scope,
            component_id=entry.id,
            instance_id=instance_id,
        )
        by_instance.setdefault(instance_id, []).append(target)
        by_scope_and_type.setdefault((entry.scope, entry.id), []).append(target)

    normalized = [token.strip() for token in tokens if token.strip()]
    if len(normalized) == 1:
        keyword = normalized[0].lower()
        if keyword == "none":
            return [], ()
        if keyword == "all":
            return [item for items in by_instance.values() for item in items], ()

    skipped: list[str] = []
    selected: list[_ComponentRemoveTarget] = []
    seen_instances: set[str] = set()
    for token in normalized:
        scope, body = _parse_scoped_component_selector(token)
        component_raw, separator, instance_raw = body.partition("@")
        if separator:
            component_id = normalize_component_token(component_raw)
            instance_id = normalize_component_token(instance_raw)
            if not component_id or not instance_id:
                raise RuntimeError(
                    f"Invalid component selector '{token}'. Use '<row-id>' or "
                    "'<component-id>@<resource-name-or-target-id>'."
                )
            candidates = by_instance.get(instance_id, [])
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.component_id == component_id and (scope is None or item.scope == scope)
                ),
                None,
            )
            if candidate is None:
                skipped.append(token)
                continue
            selected_key = f"{candidate.scope}:{candidate.component_id}@{candidate.instance_id}"
            if selected_key not in seen_instances:
                seen_instances.add(selected_key)
                selected.append(candidate)
            continue

        normalized_token = normalize_component_token(body)
        if not normalized_token:
            raise RuntimeError(f"Invalid component selector '{token}'.")
        candidate_lists = [
            specs
            for (candidate_scope, candidate_component_id), specs in by_scope_and_type.items()
            if candidate_component_id == normalized_token
            and (scope is None or candidate_scope == scope)
        ]
        flattened = [item for specs in candidate_lists for item in specs]
        if len(flattened) > 1:
            available = ", ".join(sorted(item.instance_id for item in flattened))
            raise RuntimeError(
                f"Component selector '{token}' matches multiple enabled instances. "
                "Use an exact resource name, target id, row id, or "
                f"'<component-id>@<resource-name-or-target-id>'. Available rows: {available}"
            )
        if len(flattened) == 1:
            only = flattened[0]
            selected_key = f"{only.scope}:{only.component_id}@{only.instance_id}"
            if selected_key not in seen_instances:
                seen_instances.add(selected_key)
                selected.append(only)
            continue

        instance_matches = [
            item
            for item in by_instance.get(normalized_token, [])
            if scope is None or item.scope == scope
        ]
        if len(instance_matches) > 1:
            available = ", ".join(
                sorted(f"{item.component_id}@{item.instance_id}" for item in instance_matches)
            )
            raise RuntimeError(
                f"Component selector '{token}' matches multiple enabled instances. "
                "Use '<component-id>@<resource-name-or-target-id>'. "
                f"Available rows: {available}"
            )
        if len(instance_matches) == 1:
            candidate = instance_matches[0]
            selected_key = f"{candidate.scope}:{candidate.component_id}@{candidate.instance_id}"
            if selected_key not in seen_instances:
                seen_instances.add(selected_key)
                selected.append(candidate)
            continue
        skipped.append(token)
    return selected, tuple(sorted(set(skipped)))


def _runtime_required_input_leaf_names(entry: ComponentEntry) -> set[str]:
    if entry.scope != "infra" or not entry.source:
        return set()
    try:
        return set(runtime_introspection.module_required_variables(entry.source))
    except Exception:
        return set()


def _parse_component_value_overrides(
    *,
    raw_values: list[str] | None,
    option_name: str,
) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for token in _split_multi_value_tokens(raw_values):
        if "=" not in token:
            raise RuntimeError(
                f"Invalid {option_name} value '{token}'. Expected '<component-id>=<value>'."
            )
        component_raw, value_raw = token.split("=", maxsplit=1)
        component_id = component_raw.strip().lower()
        value = value_raw.strip()
        if not component_id or not COMPONENT_ID_PATTERN.fullmatch(component_id):
            raise RuntimeError(
                f"Invalid {option_name} component id '{component_raw}'. "
                "Expected lowercase letters, digits, and hyphens."
            )
        if not value:
            raise RuntimeError(
                f"Invalid {option_name} value for '{component_id}'. Value cannot be empty."
            )
        if component_id in overrides:
            raise RuntimeError(f"Duplicate {option_name} override for component '{component_id}'.")
        overrides[component_id] = value
    return overrides


def _apply_app_release_overrides(
    *,
    payload: dict[str, Any],
    selected_apps: set[str],
    namespace_overrides: dict[str, str],
    release_name_overrides: dict[str, str],
) -> None:
    if not namespace_overrides and not release_name_overrides:
        return
    apps_node = payload.get("apps")
    if not isinstance(apps_node, dict):
        raise RuntimeError("Generated payload is missing apps mapping.")
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        raise RuntimeError("Generated payload is missing apps.charts list.")

    by_id: dict[str, dict[str, Any]] = {}
    for item in charts:
        if not isinstance(item, dict):
            continue
        chart_id = str(item.get("id", "")).strip().lower()
        if chart_id:
            by_id[chart_id] = item

    target_ids = set(namespace_overrides) | set(release_name_overrides)
    for chart_id in sorted(target_ids):
        if chart_id not in selected_apps:
            raise RuntimeError(
                f"Override target apps component '{chart_id}' is not enabled. "
                "Enable it with --app first."
            )
        row = by_id.get(chart_id)
        if row is None:
            raise RuntimeError(
                f"Override target apps component '{chart_id}' was not found in apps.charts."
            )
        namespace = namespace_overrides.get(chart_id)
        if namespace is not None:
            row["namespace"] = namespace
        release_name = release_name_overrides.get(chart_id)
        if release_name is not None:
            row["release-name"] = release_name


def _resolve_component_ids_from_tokens(
    *,
    scope: ComponentScope,
    tokens: list[str],
    entries: tuple[ComponentEntry, ...],
    defaults: set[str],
) -> set[str]:
    selectable_entries = [entry for entry in entries if entry.selectable]
    lookup = {entry.id: entry for entry in entries}
    selected: set[str] = set()
    normalized = [token.lower() for token in tokens]

    if len(normalized) == 1:
        keyword = normalized[0]
        if keyword in {"default", "defaults"}:
            return defaults
        if keyword == "all":
            return {entry.id for entry in entries}
        if keyword == "none":
            return set()

    for token in normalized:
        if token.isdigit():
            index = int(token)
            if index < 1 or index > len(selectable_entries):
                raise RuntimeError(
                    f"Invalid {scope} component index '{token}'. Use values between 1 and {len(selectable_entries)}."
                )
            selected.add(selectable_entries[index - 1].id)
            continue

        entry = lookup.get(token)
        if entry is None:
            available = ", ".join(entry.id for entry in selectable_entries)
            raise RuntimeError(
                f"Unknown {scope} component id '{token}'. Available ids: {available}"
            )
        if not entry.selectable:
            selected.add(entry.id)
            continue
        selected.add(entry.id)

    return selected


def _component_selector_label(entry: ComponentEntry, *, scope: ComponentScope) -> str:
    base = entry.id
    if entry.group:
        return f"{entry.group} >> {base}"
    return base


def _component_instance_selector_label(
    entry: ComponentEntry,
    *,
    instance_id: str,
) -> str:
    base = _component_selector_label(entry, scope=entry.scope)
    if instance_id == entry.id:
        return base
    return f"{base} @ {instance_id}"


def _component_selection_summary(
    *,
    selected: set[str],
    entries: tuple[ComponentEntry, ...],
) -> str:
    labels = _component_selection_labels(selected=selected, entries=entries)
    return ", ".join(labels) if labels else "(none)"


def _component_selection_labels(
    *,
    selected: set[str],
    entries: tuple[ComponentEntry, ...],
) -> list[str]:
    if not selected:
        return []
    entry_by_id = {entry.id: entry for entry in entries}
    labels: list[str] = []
    for component_id in sorted(selected):
        entry = entry_by_id.get(component_id)
        labels.append(entry.id if entry is not None else component_id)
    return labels


def _component_selection_block(
    *,
    infra_labels: Sequence[str],
    app_labels: Sequence[str],
    current_label: str = "",
    current_scope: ComponentScope | str | None = None,
) -> str:
    if current_label:
        scope_label = str(current_scope).title() if current_scope is not None else "Component"
        return (
            "[bold cyan]Wizard context:[/bold cyan] "
            "[dim]Current:[/dim] "
            f"[bold magenta]{escape(scope_label)}[/bold magenta] "
            "[dim]/[/dim] "
            f"[bold green]{escape(current_label)}[/bold green]"
        )

    lines = ["Current component selections:"]

    def _append_section(
        title: str,
        labels: Sequence[str],
        *,
        current: str = "",
    ) -> None:
        lines.append(f"  {title}:")
        if not labels:
            lines.append("    - (none)")
            return
        for label in labels:
            if current and label == current:
                lines.append(f"    * {escape(label)} (current)")
                continue
            lines.append(f"    - {escape(label)}")

    _append_section(
        "Infra",
        infra_labels,
        current=current_label if current_scope == "infra" else "",
    )
    _append_section(
        "Apps",
        app_labels,
        current=current_label if current_scope == "apps" else "",
    )
    return "\n".join(lines)


def _print_component_scope_selection_summary(
    *,
    scope: ComponentScope,
    selected: set[str],
    entries: tuple[ComponentEntry, ...],
) -> None:
    summary = _component_selection_summary(selected=selected, entries=entries)
    console.print(f"[dim]Selected {scope} components: {escape(summary)}[/dim]")


def _print_component_selection_summary(
    *,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> None:
    infra_labels = _component_selection_labels(
        selected=selected_infra,
        entries=infra_entries,
    )
    app_labels = _component_selection_labels(
        selected=selected_apps,
        entries=app_entries,
    )
    console.print(_component_selection_block(infra_labels=infra_labels, app_labels=app_labels))


def _enabled_app_instance_labels(
    payload: dict[str, Any],
    *,
    app_id: str = "",
) -> tuple[str, ...]:
    apps = payload.get("apps")
    if not isinstance(apps, Mapping):
        return ()
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return ()

    normalized_app_id = normalize_component_token(app_id)
    labels: list[str] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        chart_id = component_type_id(row)
        if normalized_app_id and chart_id != normalized_app_id:
            continue
        instance_id = component_instance_id(row)
        label = component_instance_label(chart_id, instance_id)
        if not label or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return tuple(labels)


def _ensure_mysterybox_eso_app_dependency_selection(
    payload: dict[str, Any],
    *,
    selected_apps: set[str],
    app_entries: tuple[ComponentEntry, ...],
) -> tuple[set[str], tuple[str, ...]]:
    before_selected_apps = set(selected_apps)
    before_labels = set(_enabled_app_instance_labels(payload, app_id=EXTERNAL_SECRETS_APP_ID))
    changed = ensure_mysterybox_eso_app_rows(payload, app_entries=app_entries)
    after_selected_apps = _enabled_ids_from_runtime_payload(payload=payload, entries=app_entries)
    external_secrets_was_auto_selected = (
        EXTERNAL_SECRETS_APP_ID in after_selected_apps
        and EXTERNAL_SECRETS_APP_ID not in before_selected_apps
    )
    if not changed and not external_secrets_was_auto_selected:
        return selected_apps, ()
    after_labels = set(_enabled_app_instance_labels(payload, app_id=EXTERNAL_SECRETS_APP_ID))
    auto_enabled_labels = after_labels - before_labels
    if external_secrets_was_auto_selected and not auto_enabled_labels:
        auto_enabled_labels = after_labels
    return (
        after_selected_apps,
        tuple(sorted(auto_enabled_labels)),
    )


def _print_mysterybox_eso_app_dependency_adjustment(app_labels: tuple[str, ...]) -> None:
    if not app_labels:
        return
    console.print(
        f"{warning_markup('Adjusted component selection:')} enabling "
        + ", ".join(f"'apps:{label}'" for label in app_labels)
        + " because selected MysteryBox with MK8s requires the External Secrets "
        "Operator controller."
    )


def _expand_soperator_component_selection(
    *,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
) -> set[str]:
    if _SOPERATOR_APP_ID not in selected_apps:
        return selected_infra
    available_infra = {entry.id for entry in infra_entries}
    expanded = set(selected_infra)
    for component_id in _SOPERATOR_REQUIRED_INFRA_COMPONENT_IDS:
        if component_id in available_infra:
            expanded.add(component_id)
    return expanded


def _expand_soperator_app_selection(
    *,
    selected_apps: set[str],
    app_entries: tuple[ComponentEntry, ...],
) -> set[str]:
    if _SOPERATOR_APP_ID not in selected_apps:
        return selected_apps
    available_apps = {entry.id for entry in app_entries}
    expanded = set(selected_apps)
    for app_id in _SOPERATOR_REQUIRED_APP_COMPONENT_IDS:
        if app_id in available_apps:
            expanded.add(app_id)
    return expanded


def _merge_missing_mapping(target: dict[str, Any], defaults: Mapping[str, Any]) -> None:
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        if isinstance(target[key], dict) and isinstance(value, Mapping):
            _merge_missing_mapping(target[key], value)


def _merge_replace_mapping(target: dict[str, Any], values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(target.get(key), dict) and isinstance(value, Mapping):
            _merge_replace_mapping(target[key], value)
            continue
        target[key] = copy.deepcopy(value)


def _soperator_app_target_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    target_refs = set(enabled_cluster_target_refs(payload))
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_refs and target_ref not in target_refs:
            continue
        if target_ref and target_ref not in seen:
            refs.append(target_ref)
            seen.add(target_ref)
    return tuple(refs)


def _soperator_nodesets_profiles() -> tuple[str, Mapping[str, Mapping[str, Any]]]:
    chart = helm_chart_source_by_id(_SOPERATOR_APP_ID)
    settings = getattr(chart, "soperator_nodesets", None)
    default = _non_empty_text(getattr(settings, "default", "")) if settings is not None else ""
    profiles = getattr(settings, "profiles", {}) if settings is not None else {}
    if not isinstance(profiles, Mapping):
        profiles = {}
    return default, profiles


def _soperator_profile_by_target(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    default_profile, profiles = _soperator_nodesets_profiles()
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return {}

    selected: dict[str, Mapping[str, Any]] = {}
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if not target_ref:
            continue
        profile_name = _non_empty_text(row.get("profile")) or default_profile
        if not profile_name:
            selected[target_ref] = {}
            continue
        profile = profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            available = ", ".join(sorted(str(name) for name in profiles)) or "(none)"
            raise ValueError(
                f"apps.charts[{target_ref}].profile references unknown Soperator "
                f"nodesets profile '{profile_name}'. Available profiles: {available}"
            )
        selected[target_ref] = profile
    return selected


def _profile_mapping(profile: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = profile.get(key)
    return value if isinstance(value, Mapping) else {}


def _profile_list(profile: Mapping[str, Any], key: str) -> list[Any]:
    value = profile.get(key)
    return list(value) if isinstance(value, list) else []


def _render_soperator_profile_value(value: Any, *, target_ref: str) -> Any:
    if isinstance(value, str):
        return value.replace("{target}", target_ref)
    if isinstance(value, list):
        return [_render_soperator_profile_value(item, target_ref=target_ref) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_soperator_profile_value(item, target_ref=target_ref)
            for key, item in value.items()
        }
    return copy.deepcopy(value)


def _positive_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        return int(value) if value > 0 else default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _required_profile_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Soperator nodesets profile field '{field}' must be a positive integer.")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Soperator nodesets profile field '{field}' must be a positive integer."
        ) from exc
    if parsed <= 0:
        raise ValueError(f"Soperator nodesets profile field '{field}' must be a positive integer.")
    return parsed


def _soperator_existing_nodeset_group(
    node_groups: Mapping[str, Any],
    *,
    nodeset_name: str,
    key_prefix: str,
) -> bool:
    for key, group in node_groups.items():
        key_text = str(key)
        if key_text == key_prefix or key_text.startswith(f"{key_prefix}-"):
            return True
        if (
            isinstance(group, Mapping)
            and str(group.get("nodeset_name", "")).strip() == nodeset_name
        ):
            return True
    return False


def _soperator_worker_profile_total_nodes(
    *,
    inputs: Mapping[str, Any],
    raw_worker: Mapping[str, Any],
    default_total_nodes: int,
    default_nodes_per_group: int,
) -> int:
    total_nodes_input = _non_empty_text(raw_worker.get("total_nodes_input"))
    node_groups_input = _non_empty_text(raw_worker.get("node_groups_input"))
    nodes_per_group_input = _non_empty_text(raw_worker.get("nodes_per_group_input"))
    if total_nodes_input:
        return _positive_int(inputs.get(total_nodes_input), default=default_total_nodes)
    if node_groups_input or nodes_per_group_input:
        requested_groups = _positive_int(
            inputs.get(node_groups_input),
            default=1,
        )
        requested_nodes_per_group = _positive_int(
            inputs.get(nodes_per_group_input),
            default=default_nodes_per_group,
        )
        return requested_groups * requested_nodes_per_group
    return default_total_nodes


def _materialize_soperator_worker_node_groups(
    *,
    inputs: dict[str, Any],
    node_groups: dict[str, Any],
    worker_profiles: list[Any],
) -> None:
    for raw_worker in worker_profiles:
        if not isinstance(raw_worker, Mapping):
            continue
        nodeset_name = _non_empty_text(raw_worker.get("nodeset_name")) or _non_empty_text(
            raw_worker.get("name")
        )
        if not nodeset_name:
            continue
        key_prefix = _non_empty_text(raw_worker.get("node_group_key_prefix")) or nodeset_name
        if _soperator_existing_nodeset_group(
            node_groups,
            nodeset_name=nodeset_name,
            key_prefix=key_prefix,
        ):
            continue

        default_total_nodes = _required_profile_positive_int(
            raw_worker.get("default_total_nodes"),
            field=f"worker_nodesets[{nodeset_name}].default_total_nodes",
        )
        default_nodes_per_group = _required_profile_positive_int(
            raw_worker.get("default_nodes_per_group"),
            field=f"worker_nodesets[{nodeset_name}].default_nodes_per_group",
        )
        total_nodes = _soperator_worker_profile_total_nodes(
            inputs=inputs,
            raw_worker=raw_worker,
            default_total_nodes=default_total_nodes,
            default_nodes_per_group=default_nodes_per_group,
        )
        if total_nodes <= 0:
            total_nodes = default_total_nodes

        max_nodes_per_group = _required_profile_positive_int(
            raw_worker.get("max_nodes_per_group"),
            field=f"worker_nodesets[{nodeset_name}].max_nodes_per_group",
        )
        shard_count = max(1, (total_nodes + max_nodes_per_group - 1) // max_nodes_per_group)
        base_group = raw_worker.get("node_group")
        base_group = copy.deepcopy(base_group) if isinstance(base_group, Mapping) else {}
        gpu_cluster_key = _non_empty_text(base_group.get("gpu_cluster_key"))
        gpu_clusters = inputs.get("gpu_clusters")
        if gpu_cluster_key and not (
            isinstance(gpu_clusters, Mapping) and gpu_cluster_key in gpu_clusters
        ):
            base_group.pop("gpu_cluster_key", None)
        for index in range(shard_count):
            remaining = total_nodes - (index * max_nodes_per_group)
            shard_size = min(max_nodes_per_group, remaining)
            group_key = f"{key_prefix}-{index}"
            node_groups.setdefault(
                group_key,
                {
                    **base_group,
                    "nodeset_name": nodeset_name,
                    "workload": _non_empty_text(raw_worker.get("workload")) or "worker",
                    "fixed_node_count": shard_size,
                    "gpu": bool(raw_worker.get("gpu", True)),
                    "jail": bool(raw_worker.get("jail", True)),
                },
            )


def _materialize_soperator_mk8s_profile(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
) -> None:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    defaults = _profile_mapping(mk8s_profile, "inputs")
    _merge_missing_mapping(inputs, defaults)

    gpu_clusters = _profile_mapping(mk8s_profile, "gpu_clusters")
    if gpu_clusters:
        target_gpu_clusters = inputs.setdefault("gpu_clusters", {})
        if isinstance(target_gpu_clusters, dict):
            _merge_missing_mapping(target_gpu_clusters, gpu_clusters)

    gpu_cluster_key = _non_empty_text(mk8s_profile.get("gpu_cluster_key"))
    infiniband_fabric = _non_empty_text(inputs.get("infiniband_fabric"))
    if gpu_cluster_key and infiniband_fabric:
        target_gpu_clusters = inputs.setdefault("gpu_clusters", {})
        if isinstance(target_gpu_clusters, dict):
            cluster = target_gpu_clusters.setdefault(gpu_cluster_key, {})
            if isinstance(cluster, dict):
                cluster.setdefault("infiniband_fabric", infiniband_fabric)

    node_groups = inputs.setdefault("node_groups", {})
    if isinstance(node_groups, dict):
        profile_node_groups = _profile_mapping(mk8s_profile, "node_groups")
        _merge_missing_mapping(node_groups, profile_node_groups)
        _materialize_soperator_worker_node_groups(
            inputs=inputs,
            node_groups=node_groups,
            worker_profiles=_profile_list(mk8s_profile, "worker_nodesets"),
        )

    if bool(mk8s_profile.get("use_generic_gpu_node_groups", False)):
        inputs["gpu_node_groups"] = 0
        inputs["gpu_nodes_count_per_group"] = 0
        inputs.pop("mk8s_gpu_node_group_overrides", None)


def _materialize_soperator_sfs_profile(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    sfs_profile = _profile_mapping(profile, "sfs")
    profile_filesystems = _profile_mapping(sfs_profile, "filesystems")
    rendered_filesystems = {
        key: _render_soperator_profile_value(value, target_ref=target_ref)
        for key, value in profile_filesystems.items()
    }
    filesystems = inputs.setdefault("filesystems", {})
    if isinstance(filesystems, dict):
        _merge_missing_mapping(filesystems, rendered_filesystems)
        return copy.deepcopy(filesystems)
    return {}


def _materialize_soperator_partition_profile(
    *,
    values: dict[str, Any],
    profile: Mapping[str, Any],
) -> None:
    profile_name = _non_empty_text(values.get("partitionProfile"))
    if not profile_name or profile_name == "shape-default":
        return
    chart_profile = _profile_mapping(profile, "chart")
    partition_profiles = _profile_mapping(chart_profile, "partition_profiles")
    partition_profile = partition_profiles.get(profile_name)
    if not isinstance(partition_profile, Mapping):
        available = ", ".join(sorted(str(name) for name in partition_profiles)) or "(none)"
        raise ValueError(
            f"values.partitionProfile references unknown Soperator partition "
            f"profile '{profile_name}'. Available profiles for this nodesets profile: {available}"
        )
    profile_values = _profile_mapping(partition_profile, "values")
    _merge_replace_mapping(values, profile_values)


def _materialize_soperator_component_defaults(payload: dict[str, Any]) -> bool:
    soperator_targets = _soperator_app_target_refs(payload)
    if not soperator_targets:
        return False
    changed = False
    primary_target = soperator_targets[0]
    soperator_filesystems: dict[str, Any] = {}
    profile_by_target = _soperator_profile_by_target(payload)

    infra_rows = _scope_rows(payload, scope="infra")
    for row in infra_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        inputs = row.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        before = copy.deepcopy(row)
        if component_id == "mk8s" and component_instance_id(row) in soperator_targets:
            target_ref = component_instance_id(row)
            _materialize_soperator_mk8s_profile(
                inputs=inputs,
                profile=profile_by_target.get(target_ref, {}),
            )
        elif component_id == "sfs":
            soperator_filesystems = _materialize_soperator_sfs_profile(
                inputs=inputs,
                profile=profile_by_target.get(primary_target, {}),
                target_ref=primary_target,
            )
        if row != before:
            changed = True

    apps_rows = _scope_rows(payload, scope="apps")
    for row in apps_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_ref not in soperator_targets:
            continue
        before = copy.deepcopy(row)
        values = row.setdefault("values", {})
        if isinstance(values, dict):
            chart_profile = _profile_mapping(
                _profile_mapping(profile_by_target.get(target_ref, {}), "chart"),
                "values",
            )
            _merge_missing_mapping(values, chart_profile)
            _materialize_soperator_partition_profile(
                values=values,
                profile=profile_by_target.get(target_ref, {}),
            )
            current_cluster_name = str(values.get("clusterName", "") or "").strip()
            if not current_cluster_name or (
                current_cluster_name == "mk8s" and target_ref != "mk8s"
            ):
                values["clusterName"] = target_ref
            slurm_nodes = values.setdefault("slurmNodes", {})
            if isinstance(slurm_nodes, dict):
                login = slurm_nodes.setdefault("login", {})
                if isinstance(login, dict):
                    login.setdefault("sshRootPublicKeys", [])
            if soperator_filesystems:
                sfs_values = values.setdefault("sfs", {})
                if isinstance(sfs_values, dict):
                    filesystems_values = sfs_values.setdefault("filesystems", {})
                    if isinstance(filesystems_values, dict):
                        _merge_missing_mapping(filesystems_values, soperator_filesystems)
                volume_values = values.setdefault("volume", {})
                if isinstance(volume_values, dict):
                    jail_fs = soperator_filesystems.get("jail", {})
                    if isinstance(jail_fs, Mapping):
                        jail_values = volume_values.setdefault("jail", {})
                        if isinstance(jail_values, dict):
                            if "size_gib" in jail_fs:
                                jail_values.setdefault("size", f"{jail_fs['size_gib']}Gi")
                            if str(jail_fs.get("mount_tag", "") or "").strip():
                                jail_values.setdefault("filestoreDeviceName", jail_fs["mount_tag"])
                    controller_fs = soperator_filesystems.get("controller-spool", {})
                    if isinstance(controller_fs, Mapping):
                        controller_values = volume_values.setdefault("controllerSpool", {})
                        if isinstance(controller_values, dict):
                            if "size_gib" in controller_fs:
                                controller_values.setdefault(
                                    "size",
                                    f"{controller_fs['size_gib']}Gi",
                                )
                            if str(controller_fs.get("mount_tag", "") or "").strip():
                                controller_values.setdefault(
                                    "filestoreDeviceName",
                                    controller_fs["mount_tag"],
                                )
                    accounting_fs = soperator_filesystems.get("accounting", {})
                    if isinstance(accounting_fs, Mapping):
                        accounting_values = volume_values.setdefault("accounting", {})
                        if isinstance(accounting_values, dict):
                            if "size_gib" in accounting_fs:
                                accounting_values.setdefault(
                                    "size",
                                    f"{accounting_fs['size_gib']}Gi",
                                )
                            if str(accounting_fs.get("mount_tag", "") or "").strip():
                                accounting_values.setdefault(
                                    "filestoreDeviceName",
                                    accounting_fs["mount_tag"],
                                )
        if row != before:
            changed = True
    return changed


def _scope_rows(payload: dict[str, Any], *, scope: ComponentScope) -> list[dict[str, Any]]:
    if scope == "infra":
        infra_node = payload.setdefault("infra", {})
        if not isinstance(infra_node, dict):
            raise RuntimeError("config payload infra section must be a mapping")
        rows = infra_node.setdefault("components", [])
    else:
        apps_node = payload.setdefault("apps", {})
        if not isinstance(apps_node, dict):
            raise RuntimeError("config payload apps section must be a mapping")
        rows = apps_node.setdefault("charts", [])
    if not isinstance(rows, list):
        raise RuntimeError(f"config payload {scope} rows must be a list")
    for item in rows:
        if not isinstance(item, dict):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        ensure_component_instance_id(item, default_component_id=component_id)
    return rows


def _enabled_component_add_label(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    requested_instance_id: str | None,
    allow_unassigned_app_target: bool,
) -> str | None:
    rows = _scope_rows(payload, scope=entry.scope)
    requested = normalize_component_token(requested_instance_id)

    if entry.scope == "infra":
        for row in rows:
            if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
                continue
            if component_type_id(row) != entry.id:
                continue
            instance_id = component_instance_id(row)
            if requested and instance_id != requested:
                continue
            return component_instance_label(entry.id, instance_id)
        return None

    target_refs = enabled_cluster_target_refs(payload)
    if target_refs:
        if requested:
            if requested not in target_refs:
                return None
            instance_id = requested
        elif len(target_refs) == 1:
            instance_id = target_scoped_app_instance_id(entry.id, target_ref=target_refs[0])
        elif not allow_unassigned_app_target:
            return None
        else:
            instance_id = entry.id
    else:
        instance_id = requested or entry.id

    for row in rows:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) == entry.id and component_instance_id(row) == instance_id:
            return component_instance_label(entry.id, instance_id)
    return None


def _component_instance_id_is_auto_allocated(component_id: str, instance_id: str) -> bool:
    normalized_component_id = normalize_component_token(component_id)
    normalized_instance_id = normalize_component_token(instance_id)
    if normalized_instance_id == normalized_component_id:
        return True
    suffix_prefix = f"{normalized_component_id}-"
    if not normalized_instance_id.startswith(suffix_prefix):
        return False
    suffix = normalized_instance_id[len(suffix_prefix) :]
    return bool(suffix) and suffix.isdigit()


def _is_scalar_resource_name_value(value: Any) -> bool:
    return value is not None and not isinstance(value, (bool, Mapping, list, tuple, set))


def _set_mapping_path_value(node: dict[str, Any], dotted_path: str, value: Any) -> None:
    current: dict[str, Any] = node
    segments = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
    if not segments:
        return
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = value


def _entry_scalar_resource_name_input(entry: ComponentEntry) -> str:
    if entry.scope != "infra" or entry.status is None:
        return ""
    name_input = str(entry.status.name_input or "name").strip()
    if not name_input:
        return ""

    wizard_fields = getattr(entry, "wizard_fields", {}) or {}
    for candidate in (name_input, f"inputs.{name_input}"):
        field = wizard_fields.get(candidate)
        if not isinstance(field, Mapping):
            continue
        if bool(field.get("prompt_complex")) or _is_complex_type_hint(
            _non_empty_text(field.get("type_hint"))
        ):
            return ""

    root_input = name_input.split(".", 1)[0]
    module_var = _module_variable_specs_for_entry(entry).get(_normalize_leaf_name(root_input))
    if module_var is not None and _is_complex_type_hint(module_var.type_hint):
        return ""
    return name_input


def _infra_resource_name_prompt_label(entry: ComponentEntry) -> str:
    if entry.id == "vm":
        return "VM name"
    title = str(entry.name or entry.description or entry.id).strip()
    if not title:
        title = entry.id
    if title.lower().endswith("name"):
        return title
    return f"{title} name"


def _seed_infra_resource_name_from_instance_id(row: dict[str, Any], entry: ComponentEntry) -> None:
    name_input = _entry_scalar_resource_name_input(entry)
    if not name_input:
        return
    instance_id = component_instance_id(row)
    if not instance_id:
        return
    inputs = row.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        return
    current = _mapping_path_value(inputs, name_input)
    if _is_scalar_resource_name_value(current):
        return
    _set_mapping_path_value(inputs, name_input, instance_id)


def _align_new_infra_instance_ids_with_resource_names(
    payload: dict[str, Any],
    *,
    selected_instance_ids: set[str] | None = None,
) -> dict[str, str]:
    """Rename newly scaffolded scalar named infra rows from placeholder ids to names."""
    infra_rows = _scope_rows(payload, scope="infra")
    apps_rows = _scope_rows(payload, scope="apps")
    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    used_infra_instance_ids = {
        component_instance_id(row)
        for row in infra_rows
        if isinstance(row, Mapping) and component_instance_id(row)
    }
    selected = (
        {
            normalize_component_token(item)
            for item in selected_instance_ids
            if normalize_component_token(item)
        }
        if selected_instance_ids is not None
        else None
    )
    renames: dict[str, str] = {}

    for row in infra_rows:
        component_id = component_type_id(row)
        instance_id = component_instance_id(row)
        if not component_id or not instance_id:
            continue
        if selected is not None and instance_id not in selected:
            continue
        entry = infra_entry_by_id.get(component_id)
        if entry is None:
            continue
        name_input = _entry_scalar_resource_name_input(entry)
        if not name_input:
            continue
        if not _component_instance_id_is_auto_allocated(component_id, instance_id):
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        raw_resource_name = _mapping_path_value(inputs, name_input)
        if not _is_scalar_resource_name_value(raw_resource_name):
            continue
        resource_name = str(raw_resource_name).strip()
        target_instance_id = normalize_component_token(resource_name)
        if (
            not target_instance_id
            or target_instance_id == instance_id
            or not INSTANCE_ID_PATTERN.fullmatch(target_instance_id)
        ):
            continue
        if target_instance_id in used_infra_instance_ids:
            raise RuntimeError(
                f"Cannot use {name_input} '{resource_name}' as the "
                f"instance_id for infra:{component_id}; component instance_id "
                f"'{target_instance_id}' already exists."
            )
        row[INSTANCE_ID_FIELD] = target_instance_id
        used_infra_instance_ids.remove(instance_id)
        used_infra_instance_ids.add(target_instance_id)
        renames[instance_id] = target_instance_id

    if not renames:
        return {}

    target_renames: dict[str, str] = {}
    for old, new in renames.items():
        for row in infra_rows:
            if not isinstance(row, Mapping) or component_instance_id(row) != new:
                continue
            entry = infra_entry_by_id.get(component_type_id(row))
            if entry is not None and entry.handoff is not None:
                target_renames[old] = new
            break
    if not target_renames:
        return renames

    for row in apps_rows:
        if not isinstance(row, dict):
            continue
        target_ref = component_instance_id(row)
        if target_ref not in target_renames:
            continue
        new_target_ref = target_renames[target_ref]
        app_id = component_type_id(row)
        if app_id and is_auto_target_scoped_app_instance_id(
            row.get(INSTANCE_ID_FIELD),
            app_id=app_id,
            target_ref=target_ref,
        ):
            row[INSTANCE_ID_FIELD] = target_scoped_app_instance_id(
                app_id,
                target_ref=new_target_ref,
            )
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if isinstance(targets, list):
        for row in targets:
            if not isinstance(row, dict):
                continue
            target_ref = normalize_component_token(row.get(INSTANCE_ID_FIELD))
            if target_ref in target_renames:
                row[INSTANCE_ID_FIELD] = target_renames[target_ref]
    return renames


def _append_component_instance_row(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    requested_instance_id: str | None = None,
    allow_unassigned_app_target: bool = False,
) -> dict[str, Any]:
    rows = _scope_rows(payload, scope=entry.scope)
    target_refs = enabled_cluster_target_refs(payload)
    requested = normalize_component_token(requested_instance_id)
    default_target_ref = target_refs[0] if len(target_refs) == 1 else ""
    if entry.scope == "apps" and target_refs:
        if requested:
            if requested not in target_refs:
                available = ", ".join(sorted(target_refs))
                raise ValueError(
                    f"apps component '{entry.id}' instance_id must match a cluster target. "
                    f"Use one of: {available}"
                )
            default_target_ref = requested
        elif len(target_refs) > 1 and not allow_unassigned_app_target:
            available = ", ".join(sorted(target_refs))
            raise ValueError(
                f"apps component '{entry.id}' must be added for an explicit cluster target "
                f"when multiple targets are enabled. Use '{entry.id}@<target-id>'. "
                f"Available targets: {available}"
            )
    if entry.scope == "apps":
        instance_id = requested or (
            target_scoped_app_instance_id(entry.id, target_ref=default_target_ref)
            if default_target_ref
            else entry.id
        )
        duplicate_app_instance = any(
            component_type_id(row) == entry.id and component_instance_id(row) == instance_id
            for row in rows
            if isinstance(row, Mapping)
        )
        if duplicate_app_instance and target_refs:
            target_label = default_target_ref or requested or instance_id
            raise ValueError(
                f"apps component '{entry.id}' is already enabled for cluster target "
                f"'{target_label}'. Remove that app row first, or add it for a different target."
            )
        if duplicate_app_instance:
            if requested:
                raise ValueError(
                    f"apps component '{entry.id}' instance_id '{instance_id}' already exists"
                )
            suffix = 2
            base_instance_id = instance_id
            while any(
                component_type_id(row) == entry.id
                and component_instance_id(row) == f"{base_instance_id}-{suffix}"
                for row in rows
                if isinstance(row, Mapping)
            ):
                suffix += 1
            instance_id = f"{base_instance_id}-{suffix}"
    else:
        instance_id = next_component_instance_id(
            component_id=entry.id,
            rows=rows,
            requested_instance_id=requested_instance_id,
        )
    if entry.scope == "infra":
        row: dict[str, Any] = {
            "id": entry.id,
            INSTANCE_ID_FIELD: instance_id,
            "enabled": True,
            "inputs": {},
        }
        _seed_infra_resource_name_from_instance_id(row, entry)
    else:
        chart_repo = str(entry.chart_repo or "").strip()
        chart_name = str(entry.chart_name or "").strip()
        if not chart_name:
            chart_repo, chart_name = _chart_source_parts(entry)
        if not chart_name:
            chart_name = entry.id
        chart_repo = _canonical_app_chart_repo(chart_repo=chart_repo, chart_name=chart_name)
        namespace = str(entry.default_namespace or "").strip() or entry.id
        default_release_name = str(entry.default_release_name or "").strip()
        if target_refs:
            release_name = default_release_name or entry.id
        else:
            release_name = (
                instance_id if instance_id != entry.id else (default_release_name or instance_id)
            )
        raw_group = str(entry.group or "").strip().lower()
        group = re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"
        row = {
            "id": entry.id,
            INSTANCE_ID_FIELD: instance_id,
            "group": group,
            "enabled": True,
            "repo": str(chart_repo or ""),
            "version": str(entry.version or ""),
            "namespace": namespace,
            "release-name": release_name,
            "values": {},
        }
    if entry.defaults:
        row = resolve_component_defaults(
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            include_shared=False,
        )
    rows.append(row)
    return row


def _prompt_new_infra_resource_name(
    *,
    entry: ComponentEntry,
    rows: list[Any],
) -> str | None:
    default_name = next_component_instance_id(component_id=entry.id, rows=rows)
    label = _infra_resource_name_prompt_label(entry)
    while True:
        try:
            raw = typer.prompt(
                f"{label} for new infra:{entry.id} "
                f"({WIZARD_EXIT_TOKEN}/{WIZARD_ABORT_TOKEN}=stop wizard)",
                default=default_name,
                show_default=True,
            ).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return None
        normalized = normalize_component_token(raw)
        if normalized in {WIZARD_EXIT_TOKEN, WIZARD_ABORT_TOKEN}:
            return None
        if not INSTANCE_ID_PATTERN.fullmatch(normalized):
            console.print(
                f"{error_markup('Invalid name')}. "
                "Use lowercase letters, digits, and hyphens; do not start or end with a hyphen."
            )
            continue
        try:
            return next_component_instance_id(
                component_id=entry.id,
                rows=rows,
                requested_instance_id=normalized,
            )
        except ValueError as exc:
            console.print(f"{error_markup('Invalid name')}. {exc}")


def _prompt_infra_add_resource_names(
    *,
    payload: dict[str, Any],
    add_targets: list[_ComponentAddTarget],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[_ComponentAddTarget] | None:
    infra_lookup = {entry.id: entry for entry in infra_entries}
    infra_node = payload.get("infra")
    raw_rows = infra_node.get("components") if isinstance(infra_node, Mapping) else []
    reserved_rows: list[Any] = (
        [copy.deepcopy(row) for row in raw_rows if isinstance(row, Mapping)]
        if isinstance(raw_rows, list)
        else []
    )
    enabled_infra_types = {
        component_type_id(row)
        for row in reserved_rows
        if isinstance(row, Mapping) and bool(row.get("enabled", False))
    }

    resolved_targets: list[_ComponentAddTarget] = []
    for target in add_targets:
        if (
            target.scope != "infra"
            or target.requested_instance_id is not None
            or not target.allocate_new_infra_instance_if_enabled
        ):
            resolved_targets.append(target)
            continue

        entry = infra_lookup.get(target.component_id)
        if entry is None:
            resolved_targets.append(target)
            continue

        if _entry_scalar_resource_name_input(entry):
            instance_id = _prompt_new_infra_resource_name(entry=entry, rows=reserved_rows)
            if instance_id is None:
                return None
        elif target.component_id in enabled_infra_types:
            instance_id = next_component_instance_id(
                component_id=entry.id,
                rows=reserved_rows,
            )
        else:
            resolved_targets.append(target)
            continue
        reserved_rows.append(
            {
                "id": entry.id,
                INSTANCE_ID_FIELD: instance_id,
                "enabled": True,
                "inputs": {},
            }
        )
        resolved_targets.append(
            replace(
                target,
                requested_instance_id=instance_id,
                allocate_new_infra_instance_if_enabled=False,
            )
        )
    return resolved_targets


def _remove_component_instance_row(
    *,
    payload: dict[str, Any],
    scope: ComponentScope,
    instance_id: str,
    component_id: str | None = None,
) -> dict[str, Any] | None:
    rows = _scope_rows(payload, scope=scope)
    target = normalize_component_token(instance_id)
    target_component = normalize_component_token(component_id)
    for index, row in enumerate(rows):
        if component_instance_id(row) != target:
            continue
        if target_component and component_type_id(row) != target_component:
            continue
        return rows.pop(index)
    return None


def _remove_target_scoped_app_rows(
    *,
    payload: dict[str, Any],
    target_instance_ids: set[str],
) -> list[str]:
    target_refs = {
        normalize_component_token(target_ref)
        for target_ref in target_instance_ids
        if normalize_component_token(target_ref)
    }
    if not target_refs:
        return []

    apps_node = payload.get("apps")
    if not isinstance(apps_node, dict):
        return []
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        return []

    removed_labels: list[str] = []
    retained: list[Any] = []
    for row in charts:
        if isinstance(row, Mapping) and component_instance_id(row) in target_refs:
            chart_id = component_type_id(row)
            instance_id = component_instance_id(row)
            if chart_id and instance_id:
                removed_labels.append(component_instance_label(chart_id, instance_id))
            continue
        retained.append(row)
    if len(retained) != len(charts):
        apps_node["charts"] = retained
    return removed_labels


def _remove_deploy_target_rows(
    *,
    payload: dict[str, Any],
    target_instance_ids: set[str],
) -> list[str]:
    target_refs = {
        normalize_component_token(target_ref)
        for target_ref in target_instance_ids
        if normalize_component_token(target_ref)
    }
    if not target_refs:
        return []

    deploy_node = payload.get("deploy")
    if not isinstance(deploy_node, dict):
        return []
    targets = deploy_node.get("targets")
    if not isinstance(targets, list):
        return []

    removed_refs: list[str] = []
    retained: list[Any] = []
    for row in targets:
        if isinstance(row, Mapping):
            instance_id = normalize_component_token(row.get(INSTANCE_ID_FIELD))
            if instance_id in target_refs:
                removed_refs.append(instance_id)
                continue
        retained.append(row)
    if len(retained) != len(targets):
        deploy_node["targets"] = retained
    return removed_refs


def _materialize_single_target_app_bindings(payload: dict[str, Any]) -> bool:
    target_refs = enabled_cluster_target_refs(payload)
    if len(target_refs) != 1:
        return False
    target_ref = target_refs[0]

    apps_node = payload.get("apps")
    if not isinstance(apps_node, Mapping):
        return False
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        return False

    enabled_unbound_by_chart: dict[str, list[dict[str, Any]]] = {}
    for row in charts:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        chart_id = component_type_id(row)
        if not chart_id or component_instance_id(row) == target_ref:
            continue
        enabled_unbound_by_chart.setdefault(chart_id, []).append(row)

    changed = False
    for chart_id, rows in enabled_unbound_by_chart.items():
        if len(rows) != 1:
            continue
        row = rows[0]
        instance_id = target_scoped_app_instance_id(chart_id, target_ref=target_ref)
        duplicate_target_row = any(
            other is not row
            and isinstance(other, Mapping)
            and bool(other.get("enabled", False))
            and component_type_id(other) == chart_id
            and component_instance_id(other) == instance_id
            for other in charts
        )
        if duplicate_target_row:
            continue
        if row.get(INSTANCE_ID_FIELD) != instance_id:
            row[INSTANCE_ID_FIELD] = instance_id
            changed = True
        if TARGET_REF_FIELD in row:
            row.pop(TARGET_REF_FIELD, None)
            changed = True
    return changed


def _enabled_component_instance_specs(
    payload: dict[str, Any],
    *,
    scope: ComponentScope,
    entries: tuple[ComponentEntry, ...],
) -> list[tuple[ComponentEntry, dict[str, Any]]]:
    entry_lookup = {entry.id: entry for entry in entries}
    rows = (
        _dynamic_enabled_infra_component_rows(payload)
        if scope == "infra"
        else _dynamic_enabled_app_chart_rows(payload)
    )
    specs: list[tuple[ComponentEntry, dict[str, Any]]] = []
    for row in rows:
        entry = entry_lookup.get(str(row["id"]))
        if entry is not None:
            specs.append((entry, row))
    return specs


def _with_infra_provider_groups(
    entries: tuple[ComponentEntry, ...],
) -> tuple[ComponentEntry, ...]:
    category_order = {
        "Compute": 0,
        "Storage": 1,
        "AI Services": 2,
        "Observability": 3,
        "Network": 4,
        "IAM": 5,
        "Security": 6,
        "Other": 99,
    }
    enriched: list[ComponentEntry] = []
    for entry in entries:
        enriched.append(entry if entry.group else replace(entry, group="Other"))
    return tuple(
        sorted(
            enriched,
            key=lambda entry: (
                category_order.get(entry.group or "Other", 98),
                entry.group or "Other",
                entry.id,
            ),
        )
    )


def _prompt_component_with_checkboxes(
    *,
    scope: ComponentScope,
    entries: tuple[ComponentEntry, ...],
    defaults: set[str],
) -> list[str]:
    selectable_entries = [entry for entry in entries if entry.selectable]
    default_selectable = [entry.id for entry in selectable_entries if entry.id in defaults]

    if selectable_entries and _is_tty_session():
        try:
            import questionary
        except Exception as exc:
            install_hint = f"{sys.executable} -m pip install questionary"
            console.print(
                f"{warning_markup('Interactive checkbox UI unavailable:')} "
                f"{exc}. Falling back to text prompt. "
                f"Install it with: {install_hint}"
            )
        else:
            _configure_questionary_checkbox_symbols()
            rendered_choices = [
                questionary.Choice(
                    title=(
                        f"{_component_selector_label(entry, scope=scope)}  ({entry.description})"
                    ),
                    value=entry.id,
                    checked=entry.id in default_selectable,
                )
                for entry in selectable_entries
            ]
            selected = _ask_questionary_with_wizard_navigation(
                questionary.checkbox(
                    f"Select {scope} components",
                    choices=rendered_choices,
                    instruction=("Use arrows and space to toggle; q=back; qq=quit; Enter=confirm."),
                    qmark="",
                )
            )
            if selected is None:
                raise _WizardQuitRequested()
            if selected == _WIZARD_QUIT_CHOICE or (
                isinstance(selected, list) and _WIZARD_QUIT_CHOICE in selected
            ):
                raise _WizardQuitRequested()
            if selected == _WIZARD_BACK_CHOICE or (
                isinstance(selected, list) and _WIZARD_BACK_CHOICE in selected
            ):
                raise _WizardBackRequested()
            return [str(item).strip().lower() for item in selected if str(item).strip()]

    console.print(f"\n{scope.upper()} components:")
    if not selectable_entries:
        return []
    for index, entry in enumerate(selectable_entries, start=1):
        marker = "[x]" if entry.id in default_selectable else "[ ]"
        display_id = _component_selector_label(entry, scope=scope)
        console.print(f"  {marker} [{index}] {display_id:<28} {entry.description}")
    default_prompt = ",".join(default_selectable)
    raw = typer.prompt(
        f"Select {scope} components (comma-separated ids or indexes, q=back, qq=quit wizard)",
        default=default_prompt,
    ).strip()
    if raw == WIZARD_ABORT_TOKEN:
        raise _WizardQuitRequested()
    if raw == WIZARD_EXIT_TOKEN:
        raise _WizardBackRequested()
    return _split_multi_value_tokens([raw])


def _resolve_component_ids(
    *,
    scope: ComponentScope,
    raw_values: list[str] | None,
    interactive: bool,
    entries: tuple[ComponentEntry, ...],
    seed_defaults: set[str] | None = None,
) -> set[str]:
    required_ids = {entry.id for entry in entries if not entry.selectable}
    defaults = (
        set(seed_defaults)
        if seed_defaults is not None
        else {entry.id for entry in entries if entry.default_enabled}
    )
    defaults |= required_ids
    tokens = _split_multi_value_tokens(raw_values)
    if not tokens and interactive:
        tokens = _prompt_component_with_checkboxes(scope=scope, entries=entries, defaults=defaults)
    if not tokens:
        resolved = set(defaults)
    else:
        resolved = _resolve_component_ids_from_tokens(
            scope=scope,
            tokens=tokens,
            entries=entries,
            defaults=defaults,
        )
    resolved |= required_ids
    if interactive:
        _print_component_scope_selection_summary(
            scope=scope,
            selected=resolved,
            entries=entries,
        )
    return resolved


@dataclass(frozen=True)
class _WizardPhaseDecision:
    proceed: bool
    stop: bool = False
    back: bool = False
    quit: bool = False

    def __bool__(self) -> bool:
        return self.proceed


def _wizard_phase_stop_requested(decision: object) -> bool:
    return isinstance(decision, _WizardPhaseDecision) and (decision.stop or decision.quit)


def _wizard_phase_back_requested(decision: object) -> bool:
    return isinstance(decision, _WizardPhaseDecision) and decision.back


def _wizard_continue_phase(
    prompt_label: str,
    *,
    default: bool = True,
    allow_back: bool = False,
) -> _WizardPhaseDecision:
    default_raw = "y" if default else "n"
    controls = (
        f"y/n, {WIZARD_EXIT_TOKEN}=back, {WIZARD_ABORT_TOKEN}=quit wizard"
        if allow_back
        else f"y/n, {WIZARD_EXIT_TOKEN}/{WIZARD_ABORT_TOKEN}=stop wizard"
    )
    while True:
        raw = (
            typer.prompt(
                f"{prompt_label} ({controls})",
                default=default_raw,
                show_default=True,
            )
            .strip()
            .lower()
        )
        if raw == WIZARD_ABORT_TOKEN:
            return _WizardPhaseDecision(proceed=False, stop=True, quit=True)
        if raw == WIZARD_EXIT_TOKEN:
            if allow_back:
                return _WizardPhaseDecision(proceed=False, back=True)
            return _WizardPhaseDecision(proceed=False, stop=True, quit=True)
        if raw in {"y", "yes"}:
            return _WizardPhaseDecision(proceed=True)
        if raw in {"n", "no"}:
            return _WizardPhaseDecision(proceed=False)
        console.print(
            f"{error_markup('Invalid selection')}. "
            f"Enter y, n, {WIZARD_EXIT_TOKEN}, or {WIZARD_ABORT_TOKEN}."
        )


def _resolve_payload_path(payload: dict[str, Any], config_path: str) -> PayloadPath | None:
    current: object = payload
    resolved: list[str | int] = []
    for segment in config_path.split("."):
        if not isinstance(current, dict):
            return None
        candidates = [segment]
        underscore = segment.replace("-", "_")
        hyphen = segment.replace("_", "-")
        if underscore not in candidates:
            candidates.append(underscore)
        if hyphen not in candidates:
            candidates.append(hyphen)
        matched = next((candidate for candidate in candidates if candidate in current), None)
        if matched is None:
            return None
        resolved.append(matched)
        current = current[matched]
    return tuple(resolved)


def _declared_wizard_prompt_path(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    component_path: PayloadPath | None,
    full_path_label: str,
) -> PayloadPath | None:
    resolved = _resolve_payload_path(payload, full_path_label)
    if resolved is not None:
        return resolved

    if full_path_label.startswith("deploy.targets[].") and component_path is not None:
        component_node = (
            _get_payload_value(payload, component_path)
            if _payload_path_exists(payload, component_path)
            else None
        )
        if isinstance(component_node, Mapping):
            target_ref = component_instance_id(component_node)
            deploy = payload.get("deploy")
            if not isinstance(deploy, dict):
                deploy = {}
                payload["deploy"] = deploy
            targets = deploy.get("targets")
            if not isinstance(targets, list):
                targets = []
                deploy["targets"] = targets
            if isinstance(targets, list):
                for index, target_row in enumerate(targets):
                    if not isinstance(target_row, Mapping):
                        continue
                    if normalize_component_token(target_row.get(INSTANCE_ID_FIELD)) != target_ref:
                        continue
                    indexed_label = full_path_label.replace(
                        "deploy.targets[]",
                        f"deploy.targets[{index}]",
                        1,
                    )
                    return _parse_payload_path_label(indexed_label)
                targets.append({INSTANCE_ID_FIELD: target_ref})
                indexed_label = full_path_label.replace(
                    "deploy.targets[]",
                    f"deploy.targets[{len(targets) - 1}]",
                    1,
                )
                return _parse_payload_path_label(indexed_label)

    root_token = full_path_label.split(".", 1)[0]
    if root_token in _ABSOLUTE_WIZARD_ROOTS:
        return _parse_payload_path_label(full_path_label)

    if component_path is None:
        return None

    component_path_label = _format_payload_path(component_path)
    if not component_path_label or not full_path_label.startswith(f"{component_path_label}."):
        return None

    relative = full_path_label[len(component_path_label) + 1 :]
    if entry.scope == "infra":
        if not relative.startswith("inputs."):
            return None
    elif entry.scope == "apps":
        if relative not in {
            "namespace",
            "profile",
            "release-name",
        } and not relative.startswith("values."):
            return None
    else:
        return None

    return _parse_payload_path_label(full_path_label)


def _get_payload_value(payload: object, path: PayloadPath) -> object:
    current = payload
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list):
                raise RuntimeError(f"Expected list while traversing payload at {path}")
            current = current[segment]
            continue
        if not isinstance(current, dict):
            raise RuntimeError(f"Expected mapping while traversing payload at {path}")
        current = current[segment]
    return current


def _payload_path_exists(payload: object, path: PayloadPath) -> bool:
    current = payload
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment < 0 or segment >= len(current):
                return False
            current = current[segment]
            continue
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    return True


def _set_payload_value(payload: object, path: PayloadPath, value: object) -> None:
    if not path:
        raise RuntimeError("Cannot set payload root directly")
    current = payload
    for segment in path[:-1]:
        if isinstance(segment, int):
            if not isinstance(current, list):
                raise RuntimeError(f"Expected list while traversing payload at {path}")
            current = current[segment]
            continue
        if not isinstance(current, dict):
            raise RuntimeError(f"Expected mapping while traversing payload at {path}")
        current = current[segment]

    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise RuntimeError(f"Expected list while setting payload at {path}")
        current[last] = value
        return
    if not isinstance(current, dict):
        raise RuntimeError(f"Expected mapping while setting payload at {path}")
    current[last] = value


def _set_payload_value_creating_containers(
    payload: object, path: PayloadPath, value: object
) -> None:
    if not path:
        raise RuntimeError("Cannot set payload root directly")
    current = payload
    for index, segment in enumerate(path[:-1]):
        next_segment = path[index + 1]
        expected_container: object = [] if isinstance(next_segment, int) else {}
        if isinstance(segment, int):
            if not isinstance(current, list):
                raise RuntimeError(f"Expected list while traversing payload at {path}")
            while len(current) <= segment:
                current.append(copy.deepcopy(expected_container))
            next_value = current[segment]
            if isinstance(next_segment, int):
                if not isinstance(next_value, list):
                    next_value = []
                    current[segment] = next_value
            else:
                if not isinstance(next_value, dict):
                    next_value = {}
                    current[segment] = next_value
            current = next_value
            continue

        if not isinstance(current, dict):
            raise RuntimeError(f"Expected mapping while traversing payload at {path}")
        next_value = current.get(segment)
        if isinstance(next_segment, int):
            if not isinstance(next_value, list):
                next_value = []
                current[segment] = next_value
        else:
            if not isinstance(next_value, dict):
                next_value = {}
                current[segment] = next_value
        current = next_value

    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise RuntimeError(f"Expected list while setting payload at {path}")
        while len(current) <= last:
            current.append(None)
        current[last] = value
        return
    if not isinstance(current, dict):
        raise RuntimeError(f"Expected mapping while setting payload at {path}")
    current[last] = value


def _delete_payload_value(payload: object, path: PayloadPath) -> None:
    if not path:
        raise RuntimeError("Cannot delete payload root directly")

    def _delete(current: object, remaining: PayloadPath) -> bool:
        segment = remaining[0]
        if len(remaining) == 1:
            if isinstance(segment, int):
                if not isinstance(current, list) or segment < 0 or segment >= len(current):
                    return False
                current.pop(segment)
                return len(current) == 0
            if not isinstance(current, dict) or segment not in current:
                return False
            current.pop(segment, None)
            return len(current) == 0

        if isinstance(segment, int):
            if not isinstance(current, list) or segment < 0 or segment >= len(current):
                return False
            child = current[segment]
            child_empty = _delete(child, remaining[1:])
            if child_empty and segment < len(current):
                current.pop(segment)
            return len(current) == 0

        if not isinstance(current, dict) or segment not in current:
            return False
        child = current[segment]
        child_empty = _delete(child, remaining[1:])
        if child_empty and segment in current:
            current.pop(segment, None)
        return len(current) == 0

    _delete(payload, path)


def _collect_scalar_leaf_paths(node: object, *, prefix: PayloadPath = ()) -> list[PayloadPath]:
    if isinstance(node, dict):
        leaf_paths: list[PayloadPath] = []
        for key, value in node.items():
            leaf_paths.extend(_collect_scalar_leaf_paths(value, prefix=prefix + (key,)))
        return leaf_paths
    if isinstance(node, list):
        leaf_paths: list[PayloadPath] = []
        for index, value in enumerate(node):
            leaf_paths.extend(_collect_scalar_leaf_paths(value, prefix=prefix + (index,)))
        return leaf_paths
    return [prefix]


def _collect_promptable_leaf_paths(node: object, *, prefix: PayloadPath = ()) -> list[PayloadPath]:
    if isinstance(node, dict):
        if not node:
            return [prefix]
        leaf_paths: list[PayloadPath] = []
        for key, value in node.items():
            leaf_paths.extend(_collect_promptable_leaf_paths(value, prefix=prefix + (key,)))
        return leaf_paths
    if isinstance(node, list):
        if not node:
            return [prefix]
        leaf_paths: list[PayloadPath] = []
        for index, value in enumerate(node):
            leaf_paths.extend(_collect_promptable_leaf_paths(value, prefix=prefix + (index,)))
        return leaf_paths
    return [prefix]


def _format_payload_path(path: PayloadPath) -> str:
    tokens: list[str] = []
    for segment in path:
        if isinstance(segment, int):
            if not tokens:
                tokens.append(f"[{segment}]")
            else:
                tokens[-1] = f"{tokens[-1]}[{segment}]"
            continue
        tokens.append(segment)
    return ".".join(tokens)


def _parse_payload_path_label(path_label: str) -> PayloadPath | None:
    segments: list[str | int] = []
    for raw_token in path_label.split("."):
        token = raw_token.strip()
        if not token:
            return None
        if "[" in token:
            base = token.split("[", maxsplit=1)[0]
            if base:
                segments.append(base)
            suffix = token[len(base) :]
            while suffix:
                if not suffix.startswith("["):
                    return None
                end = suffix.find("]")
                if end <= 1:
                    return None
                index_raw = suffix[1:end]
                try:
                    segments.append(int(index_raw))
                except ValueError:
                    return None
                suffix = suffix[end + 1 :]
            continue
        segments.append(token)
    return tuple(segments)


def _read_payload_field(payload: dict[str, Any], field_path: str) -> Any:
    parsed = _parse_payload_path_label(field_path)
    if parsed is not None:
        try:
            return _get_payload_value(payload, parsed)
        except Exception:
            return None
    return read_path_with_catalog(payload, field_path)


def _component_instance_path_label(
    scope: ComponentScope, component_id: str, instance_id: str
) -> str:
    label = component_instance_label(component_id, instance_id)
    if scope == "infra":
        return f"infra.components[{label}]"
    return f"apps.charts[{label}]"


def _dynamic_infra_component_path(
    payload: dict[str, Any],
    component_id: str,
    *,
    instance_id: str | None = None,
) -> PayloadPath | None:
    infra_node = payload.get("infra")
    if not isinstance(infra_node, Mapping):
        return None
    components = infra_node.get("components")
    if not isinstance(components, list):
        return None
    target = normalize_component_token(component_id)
    target_instance = normalize_component_token(instance_id)
    for index, item in enumerate(components):
        if not isinstance(item, Mapping):
            continue
        current_id = component_type_id(item)
        current_instance = component_instance_id(item)
        if target_instance:
            if current_instance == target_instance and current_id == target:
                return ("infra", "components", index)
            continue
        if current_id == target:
            return ("infra", "components", index)
    return None


def _dynamic_app_chart_path(
    payload: dict[str, Any],
    chart_id: str,
    *,
    instance_id: str | None = None,
) -> PayloadPath | None:
    apps_node = payload.get("apps")
    if not isinstance(apps_node, Mapping):
        return None
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        return None
    target = normalize_component_token(chart_id)
    target_instance = normalize_component_token(instance_id)
    for index, item in enumerate(charts):
        if not isinstance(item, Mapping):
            continue
        current_id = component_type_id(item)
        current_instance = component_instance_id(item)
        if target_instance:
            if current_instance == target_instance and current_id == target:
                return ("apps", "charts", index)
            continue
        if current_id == target:
            return ("apps", "charts", index)
    return None


def _dynamic_component_path(payload: dict[str, Any], entry: ComponentEntry) -> PayloadPath | None:
    if entry.scope == "infra":
        return _dynamic_infra_component_path(payload, entry.id)
    return _dynamic_app_chart_path(payload, entry.id)


def _co_located_input_path(full_path_label: str, input_name: str) -> str | None:
    marker = ".inputs."
    if marker not in full_path_label:
        return None
    prefix = full_path_label.split(marker, maxsplit=1)[0]
    if not prefix:
        return None
    return f"{prefix}{marker}{input_name}"


_PROVIDER_ARG_PATH_KEYS = frozenset(
    {
        "platform_path",
        "preset_path",
        "project_id_path",
        "fallback_project_id_path",
        "gpu_cluster_required_path",
    }
)

_ABSOLUTE_WIZARD_ROOTS = frozenset({"version", "client_info", "deploy", "infra", "apps"})


def _dynamic_component_prefix(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> str:
    if entry.scope == "infra":
        match = re.match(r"^(infra\.components(?:\[[0-9]+\]|\.[^.]+))(?:\..+)?$", full_path_label)
    else:
        match = re.match(r"^(apps\.charts(?:\[[0-9]+\]|\.[^.]+))(?:\..+)?$", full_path_label)
    return match.group(1) if match else ""


def _normalize_provider_arg_path(
    *,
    entry: ComponentEntry,
    full_path_label: str,
    raw_path: str,
) -> str:
    token = str(raw_path).strip()
    if not token:
        return ""
    if token.split(".", 1)[0] in _ABSOLUTE_WIZARD_ROOTS:
        return token

    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return token

    if entry.scope == "infra":
        if token.startswith("inputs."):
            return f"{component_prefix}.{token}"
        return f"{component_prefix}.inputs.{token}"

    app_root_keys = {
        "id",
        "instance_id",
        "group",
        "enabled",
        "repo",
        "profile",
        "version",
        "namespace",
        "release-name",
        "values",
    }
    if token.split(".", 1)[0] in app_root_keys:
        return f"{component_prefix}.{token}"
    return f"{component_prefix}.values.{token}"


def _normalize_provider_args(
    *,
    entry: ComponentEntry,
    full_path_label: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in args.items():
        if key in _PROVIDER_ARG_PATH_KEYS and isinstance(value, str):
            normalized[key] = _normalize_provider_arg_path(
                entry=entry,
                full_path_label=full_path_label,
                raw_path=value,
            )
            continue
        normalized[key] = value
    return normalized


def _resolve_wizard_field_spec(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> dict[str, Any] | None:
    if entry.wizard_fields:
        direct = entry.wizard_fields.get(full_path_label)
        if isinstance(direct, dict):
            return direct

        target_match = re.match(r"^deploy\.targets\[[0-9]+\]\.(.+)$", full_path_label)
        if target_match:
            placeholder = f"deploy.targets[].{target_match.group(1)}"
            placeholder_spec = entry.wizard_fields.get(placeholder)
            if isinstance(placeholder_spec, dict):
                return placeholder_spec

        prefix = f"{entry.config_path}."
        if full_path_label.startswith(prefix):
            relative = full_path_label[len(prefix) :]
            relative_spec = entry.wizard_fields.get(relative)
            if isinstance(relative_spec, dict):
                return relative_spec

        for relative in _relative_wizard_field_paths(entry=entry, full_path_label=full_path_label):
            relative_spec = entry.wizard_fields.get(relative)
            if isinstance(relative_spec, dict):
                return relative_spec
    return None


def _wizard_field_prompt_enabled(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return True
    prompt = spec.get("prompt")
    if isinstance(prompt, bool):
        return prompt
    return True


def _wizard_field_default_value(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> object:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None or "default" not in spec:
        return _WIZARD_DEFAULT_MISSING
    return copy.deepcopy(spec.get("default"))


def _wizard_field_materialize_default(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    return bool(isinstance(spec, dict) and spec.get("materialize_default") is True)


def _wizard_field_provider_default_value(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
    type_hint: str | None,
) -> object:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if provider_lookup is None or not isinstance(spec, dict):
        return _WIZARD_DEFAULT_MISSING
    default_from = spec.get("default_from")
    if not isinstance(default_from, Mapping):
        return _WIZARD_DEFAULT_MISSING
    provider = str(default_from.get("from") or default_from.get("provider") or "").strip()
    if not provider:
        return _WIZARD_DEFAULT_MISSING
    args_raw = default_from.get("args")
    args: dict[str, Any] = dict(args_raw) if isinstance(args_raw, dict) else {}
    args = _normalize_provider_args(
        entry=entry,
        full_path_label=full_path_label,
        args=args,
    )
    choices = provider_lookup.resolve(
        provider=provider,
        args=args,
        payload=payload,
        field_path=full_path_label,
    )
    if not choices:
        return _WIZARD_DEFAULT_MISSING
    if _is_string_sequence_type_hint(type_hint):
        return [choice.value for choice in choices if choice.value]
    return choices[0].value


def _resolve_dynamic_field_choices(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
) -> list[OptionChoice]:
    leaf = full_path_label.rsplit(".", maxsplit=1)[-1].strip().lower().replace("-", "_")
    if leaf in {"parent_id", "project_id"} and full_path_label.startswith("infra.components["):
        preferred_project_id = _non_empty_text(
            _read_payload_field(payload, "client_info.nebius.project_id")
        )
        current_value = _non_empty_text(_read_payload_field(payload, full_path_label))
        choices: list[OptionChoice] = []
        seen_values: set[str] = set()
        for value in (preferred_project_id, current_value):
            if not value or value in seen_values:
                continue
            label = (
                f"{value}  (from client_info.nebius.project_id)"
                if value == preferred_project_id
                else value
            )
            choices.append(OptionChoice(value=value, label=label))
            seen_values.add(value)
        if choices:
            return choices

    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return []

    # New flat `options` format: {from: <provider>, args?: {...}, filter?: <regex>}
    options = spec.get("options") if isinstance(spec, dict) else None
    if isinstance(options, dict):
        provider = str(options.get("from", "")).strip()
        if not provider or provider_lookup is None:
            return []
        args_raw = options.get("args")
        args: dict[str, Any] = dict(args_raw) if isinstance(args_raw, dict) else {}
        filter_pattern = str(options.get("filter", "")).strip()
        if filter_pattern:
            args["_filter"] = filter_pattern
        args = _normalize_provider_args(
            entry=entry,
            full_path_label=full_path_label,
            args=args,
        )
        return provider_lookup.resolve(
            provider=provider,
            args=args,
            payload=payload,
            field_path=full_path_label,
        )

    # Legacy `sources` array format
    sources = spec.get("sources")
    if not isinstance(sources, list):
        return []

    choices: list[OptionChoice] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("source", "")).strip().lower()
        if source_type == "provider":
            provider = str(source.get("provider", "")).strip()
            if not provider or provider_lookup is None:
                continue
            source_args = source.get("args")
            resolved_args = dict(source_args) if isinstance(source_args, dict) else {}
            resolved_args = _normalize_provider_args(
                entry=entry,
                full_path_label=full_path_label,
                args=resolved_args,
            )
            provider_choices = provider_lookup.resolve(
                provider=provider,
                args=resolved_args,
                payload=payload,
                field_path=full_path_label,
            )
            for item in provider_choices:
                if item.value in seen:
                    continue
                choices.append(item)
                seen.add(item.value)
            continue
        if source_type == "static":
            values = source.get("values")
            if not isinstance(values, list):
                continue
            for raw in values:
                label = ""
                if isinstance(raw, Mapping):
                    value = str(raw.get("value", "")).strip()
                    label = str(raw.get("label", "")).strip() or value
                else:
                    value = str(raw).strip()
                    label = value
                if not value or value in seen:
                    continue
                choices.append(OptionChoice(value=value, label=label))
                seen.add(value)
            continue
    return choices


def _provider_sources_for_field(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> tuple[str, ...]:
    specs = _provider_source_specs_for_field(entry=entry, full_path_label=full_path_label)
    return tuple(dict.fromkeys(provider for provider, _args in specs))


def _provider_source_specs_for_field(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return ()

    # New flat `options` format
    options = spec.get("options") if isinstance(spec, dict) else None
    if isinstance(options, dict):
        provider = str(options.get("from", "")).strip()
        if not provider:
            return ()
        filter_pattern = str(options.get("filter", "")).strip()
        args_raw = options.get("args")
        args: dict[str, Any] = dict(args_raw) if isinstance(args_raw, dict) else {}
        if filter_pattern:
            args["_filter"] = filter_pattern
        args = _normalize_provider_args(
            entry=entry,
            full_path_label=full_path_label,
            args=args,
        )
        return ((provider, args),)

    # Legacy `sources` array format
    sources = spec.get("sources")
    if not isinstance(sources, list):
        return ()
    provider_specs: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("source", "")).strip().lower() != "provider":
            continue
        provider = str(source.get("provider", "")).strip()
        if not provider:
            continue
        args_raw = source.get("args")
        source_args = dict(args_raw) if isinstance(args_raw, dict) else {}
        source_args = _normalize_provider_args(
            entry=entry,
            full_path_label=full_path_label,
            args=source_args,
        )
        key = (provider, json.dumps(source_args, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        provider_specs.append((provider, source_args))
    return tuple(provider_specs)


def _provider_allowed_values_for_field(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
) -> tuple[set[str], tuple[str, ...]]:
    provider_specs = _provider_source_specs_for_field(entry=entry, full_path_label=full_path_label)
    providers = tuple(dict.fromkeys(provider for provider, _args in provider_specs))
    if not provider_specs or provider_lookup is None:
        return set(), providers

    allowed: set[str] = set()
    for provider, resolved_args in provider_specs:
        for item in provider_lookup.resolve(
            provider=provider,
            args=resolved_args,
            payload=payload,
            field_path=full_path_label,
        ):
            value = str(item.value).strip()
            if value:
                allowed.add(value)
    return allowed, providers


def _provider_prompt_dependencies_ready(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    for _provider, resolved_args in _provider_source_specs_for_field(
        entry=entry,
        full_path_label=full_path_label,
    ):
        for key in ("platform_path", "preset_path"):
            dependency_path = _non_empty_text(resolved_args.get(key))
            if dependency_path and not _non_empty_text(
                _read_payload_field(payload, dependency_path)
            ):
                return False
    return True


def _provider_auto_select_single_enabled(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return False
    options = spec.get("options") if isinstance(spec, dict) else None
    if not isinstance(options, dict):
        return False
    return bool(options.get("auto_select_single"))


def _provider_auto_select_first_enabled(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return False
    options = spec.get("options") if isinstance(spec, dict) else None
    if not isinstance(options, dict):
        return False
    return bool(options.get("auto_select_first"))


def _provider_skip_prompt_if_no_choices_enabled(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    spec = _resolve_wizard_field_spec(entry=entry, full_path_label=full_path_label)
    if spec is None:
        return False
    options = spec.get("options") if isinstance(spec, dict) else None
    if not isinstance(options, dict):
        return False
    return bool(options.get("skip_prompt_if_no_choices"))


def _relative_wizard_field_paths(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> tuple[str, ...]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _remember(value: str) -> None:
        token = value.strip()
        if not token or token in seen:
            return
        seen.add(token)
        candidates.append(token)

    if entry.scope == "infra":
        dynamic_match = re.match(
            r"^infra\.components(?:\[[0-9]+\]|\.[^.]+)\.(.+)$", full_path_label
        )
        if dynamic_match:
            relative = dynamic_match.group(1)
            _remember(relative)
            if relative.startswith("inputs."):
                _remember(relative[len("inputs.") :])
    elif entry.scope == "apps":
        dynamic_match = re.match(r"^apps\.charts(?:\[[0-9]+\]|\.[^.]+)\.(.+)$", full_path_label)
        if dynamic_match:
            relative = dynamic_match.group(1)
            _remember(relative)
            if relative.startswith("values."):
                _remember(relative[len("values.") :])

    return tuple(candidates)


def _declared_wizard_field_labels(
    entry: ComponentEntry,
    *,
    component_path: PayloadPath | None = None,
) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    component_path_label = (
        _format_payload_path(component_path) if component_path is not None else ""
    )
    prefix = f"{entry.config_path}."
    for raw in entry.wizard_fields:
        key = raw.strip()
        if not key:
            continue
        if (
            key.startswith(prefix)
            or key == entry.config_path
            or key.split(".", 1)[0] in _ABSOLUTE_WIZARD_ROOTS
        ):
            full_label = key
        elif component_path_label:
            full_label = f"{component_path_label}.{key}"
        else:
            full_label = f"{entry.config_path}.{key}"
        if full_label in seen:
            continue
        seen.add(full_label)
        labels.append(full_label)
    return labels


def _normalize_leaf_name(token: str) -> str:
    return token.strip().lower().replace("-", "_")


def _required_leaf_names_for_entry(entry: ComponentEntry) -> set[str]:
    if entry.scope != "infra":
        return set()
    required_names: set[str] = set()
    metadata_source = _entry_module_metadata_source(
        entry,
        fallback_source=str(entry.source or ""),
    )
    if metadata_source:
        required_names |= {
            _normalize_leaf_name(name) for name in module_required_variables(metadata_source)
        }
    return required_names


def _module_variable_specs_for_entry(entry: ComponentEntry) -> dict[str, Any]:
    if entry.scope != "infra":
        return {}
    source = _entry_module_metadata_source(entry, fallback_source=str(entry.source or ""))
    if not source:
        return {}
    return {_normalize_leaf_name(item.name): item for item in module_variables(source)}


def _entry_declares_module_inputs(entry: ComponentEntry, required_inputs: set[str]) -> bool:
    if entry.scope != "infra":
        return False
    available_inputs = set(_module_variable_specs_for_entry(entry))
    return {_normalize_leaf_name(item) for item in required_inputs} <= available_inputs


def _entry_declares_compute_boot_disk_contract(entry: ComponentEntry) -> bool:
    return _entry_declares_module_inputs(
        entry,
        {
            "platform",
            "preset",
            "boot_disk_size_gib",
            "boot_disk_type",
        },
    )


def _entry_declares_compute_data_disk_contract(entry: ComponentEntry) -> bool:
    return _entry_declares_module_inputs(
        entry,
        {
            "data_disk_enabled",
            "data_disk_size_gib",
            "data_disk_type",
            "data_disk_encryption_enabled",
            "data_disk_deletion_protection",
        },
    )


def _short_type_hint(type_hint: str | None) -> str | None:
    if not type_hint:
        return None
    normalized = type_hint.strip().lower()
    if not normalized:
        return None
    aliases = {
        "string": "string",
        "number": "number",
        "bool": "bool",
        "boolean": "bool",
    }
    if normalized in aliases:
        return aliases[normalized]
    for token in ("list(", "set(", "map(", "object(", "tuple("):
        if normalized.startswith(token):
            return normalized
    return normalized


def _complex_type_kind(type_hint: str | None) -> str | None:
    hint = _short_type_hint(type_hint)
    if hint is None:
        return None
    if hint.startswith(("list(", "set(", "tuple(")):
        return "sequence"
    if hint.startswith(("map(", "object(")):
        return "mapping"
    return None


def _is_string_sequence_type_hint(type_hint: str | None) -> bool:
    hint = _short_type_hint(type_hint)
    return hint in {"list(string)", "set(string)"}


def _is_complex_type_hint(type_hint: str | None) -> bool:
    return _complex_type_kind(type_hint) is not None


def _empty_value_for_type_hint(type_hint: str | None) -> object | None:
    kind = _complex_type_kind(type_hint)
    if kind == "sequence":
        return []
    if kind == "mapping":
        return {}
    return None


def _serialize_complex_prompt_default(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value).strip()


def _empty_complex_value_label(value: object, *, type_hint: str | None) -> str | None:
    kind = _complex_type_kind(type_hint)
    if kind == "mapping" and isinstance(value, dict) and not value:
        return "empty map {}"
    if kind == "sequence" and isinstance(value, list) and not value:
        return "empty list []"
    return None


def _parse_complex_prompt_value(raw: str, *, type_hint: str | None) -> object:
    kind = _complex_type_kind(type_hint)
    parsed = yaml.safe_load(raw)
    if kind == "sequence":
        if isinstance(parsed, list):
            if _is_string_sequence_type_hint(type_hint) and not all(
                isinstance(item, str) for item in parsed
            ):
                raise ValueError("Expected a comma-separated list of strings.")
            return parsed
        if _is_string_sequence_type_hint(type_hint) and isinstance(parsed, str):
            values = [item.strip() for item in parsed.split(",")]
            if not values or any(not item for item in values):
                raise ValueError("Expected a comma-separated list of strings.")
            return values
        if _is_string_sequence_type_hint(type_hint):
            raise ValueError("Expected a comma-separated list of strings.")
        else:
            raise ValueError("Expected a YAML/JSON list value.")
    if kind == "mapping":
        if not isinstance(parsed, dict):
            raise ValueError("Expected a YAML/JSON mapping value.")
        return parsed
    return parsed


def _has_required_complex_content(value: object, *, type_hint: str | None) -> bool:
    kind = _complex_type_kind(type_hint)
    if kind == "sequence":
        return isinstance(value, list) and bool(value)
    if kind == "mapping":
        return isinstance(value, dict) and bool(value)
    return value is not None


def _coerce_raw_value_from_type_hint(raw: str, type_hint: str | None) -> object:
    hint = _short_type_hint(type_hint)
    if hint == "bool":
        lowered = raw.strip().lower()
        if lowered in {"true", "t", "1", "yes", "y"}:
            return True
        if lowered in {"false", "f", "0", "no", "n"}:
            return False
        raise ValueError("Expected boolean value (true/false).")
    if hint == "number":
        token = raw.strip()
        if not token:
            return token
        try:
            if "." in token:
                return float(token)
            return int(token)
        except ValueError as exc:
            raise ValueError("Expected numeric value.") from exc
    return raw


def _prompt_label_with_type(
    path_label: str,
    *,
    type_hint: str | None,
    required: bool,
) -> str:
    tags: list[str] = []
    short_type = _short_type_hint(type_hint)
    if short_type:
        tags.append(short_type)
    tags.append("required" if required else "optional")
    if not tags:
        return path_label
    return f"{path_label} [{', '.join(tags)}]"


def _has_required_prompt_value(value: object, *, type_hint: str | None) -> bool:
    if _is_complex_type_hint(type_hint):
        return _has_required_complex_content(value, type_hint=type_hint)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _provider_fallback_warning(
    *,
    field_path_label: str,
    provider_names: str,
    required: bool,
    provider_lookup: ProviderOptionLookup | None,
) -> str:
    warning = (
        f"{warning_markup('Dynamic provider options unavailable')} for "
        f"'{field_path_label}' via provider source(s): {escape(provider_names)}."
    )
    if provider_lookup is not None:
        last_error = provider_lookup.last_error()
        if last_error:
            warning += f" {escape(last_error)}"
    if required:
        warning += " The next prompt is manual input only, and this required field must be entered manually."
        return warning
    warning += (
        " The next prompt is manual input only. "
        "Press Enter there to keep the current value or leave the optional field unset."
    )
    return warning


def _prompt_path_sort_key(
    path: PayloadPath,
    *,
    required_leaf_names: set[str],
    required_prompt_labels: set[str] | None = None,
) -> tuple[int, int, int, str]:
    leaf_order_hints = {
        "cpu_nodes_platform": 10,
        "cpu_nodes_preset": 11,
        "cpu_nodes_os": 12,
        "cpu_nodes_boot_disk_type": 13,
        "cpu_nodes_boot_disk_size_gib": 14,
        "gpu_node_groups": 10,
        "gpu_nodes_count_per_group": 11,
        "gpu_nodes_platform": 12,
        "gpu_nodes_preset": 13,
        "gpu_stack_preset": 14,
        "gpu_nodes_os": 15,
        "infiniband_fabric": 16,
        "gpu_nodes_boot_disk_type": 17,
        "gpu_nodes_boot_disk_size_gib": 18,
        "subnet_id": 20,
        "platform": 21,
        "preset": 22,
        "source_image_family": 23,
        "boot_disk_type": 24,
        "boot_disk_encryption_enabled": 25,
        "boot_disk_deletion_protection": 26,
        "boot_disk_size_gib": 27,
        "public_ip_mode": 28,
        "gpu_cluster_infiniband_fabric": 29,
        "create_public_ip_allocation": 30,
        "public_ip_allocation_id": 31,
        "public_ip_allocation_name": 32,
        "data_disk_enabled": 33,
        "data_disk_type": 34,
        "data_disk_encryption_enabled": 35,
        "data_disk_deletion_protection": 36,
        "data_disk_size_gib": 37,
    }
    full_label = _format_payload_path(path)
    nested_order_hints = {
        "deploy.observability.enabled": 19,
        "deploy.targets[].observability.enabled": 19,
        "deploy.targets[].observability.kubernetes.logs.enabled": 20,
        "deploy.targets[].observability.kubernetes.metrics.enabled": 21,
        "deploy.targets[].observability.kubernetes.metrics.collect_k8s_cluster_metrics": 22,
        "deploy.targets[].observability.kubernetes.traces.enabled": 23,
        "deploy.targets[].validations.mk8s_gpu.operator_readiness.enabled": 30,
        "deploy.targets[].validations.mk8s_gpu.gpu_visibility.enabled": 31,
        "deploy.targets[].validations.mk8s_gpu.gpu_visibility.max_nodes": 32,
        "deploy.targets[].validations.mk8s_gpu.nccl.enabled": 33,
        "deploy.targets[].validations.mk8s_gpu.nccl.max_nodes": 34,
        "deploy.targets[].validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps": 35,
        "deploy.targets[].validations.mk8s_gpu.health_checker.enabled": 36,
        "deploy.targets[].secrets.mysterybox.enabled": 40,
        "deploy.targets[].secrets.mysterybox.allow_all_namespaces": 41,
        "deploy.targets[].secrets.mysterybox.sync_namespaces": 42,
    }
    leaf = path[-1] if path else ""
    leaf_name = _normalize_leaf_name(str(leaf)) if isinstance(leaf, str) else ""
    normalized_full_label = re.sub(r"deploy\.targets\[[0-9]+\]", "deploy.targets[]", full_label)
    required_rank = (
        0
        if (required_prompt_labels and full_label in required_prompt_labels)
        or (leaf_name and leaf_name in required_leaf_names)
        else 1
    )
    if normalized_full_label.startswith("deploy.targets[].secrets.mysterybox."):
        required_rank = 1
    toggle_rank = (
        0
        if leaf_name.endswith("_enabled")
        and leaf_name not in {"boot_disk_encryption_enabled", "data_disk_encryption_enabled"}
        else 1
    )
    leaf_rank = next(
        (rank for suffix, rank in nested_order_hints.items() if normalized_full_label == suffix),
        leaf_order_hints.get(leaf_name, 100),
    )
    return required_rank, toggle_rank, leaf_rank, full_label


def _maybe_clear_gpu_cluster_fabric_after_shape_change(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if provider_lookup is None or entry.scope != "infra":
        return
    fabric_label = ""
    disabled_reason = ""
    if entry.id == "mk8s":
        if not full_path_label.endswith(
            (".gpu_enabled", ".gpu_nodes_platform", ".gpu_nodes_preset")
        ):
            return
        component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
        if not component_prefix:
            return
        fabric_label = f"{component_prefix}.inputs.infiniband_fabric"
        gpu_shape_enabled = bool(
            _read_payload_field(payload, f"{component_prefix}.inputs.gpu_enabled")
        )
        disabled_reason = "GPU is no longer enabled"
    elif entry.id == "vm":
        if not full_path_label.endswith((".gpu_cluster_enabled", ".platform", ".preset")):
            return
        component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
        if not component_prefix:
            return
        fabric_label = f"{component_prefix}.inputs.gpu_cluster_infiniband_fabric"
        gpu_shape_enabled = bool(
            _read_payload_field(payload, f"{component_prefix}.inputs.gpu_cluster_enabled")
        )
        disabled_reason = "GPU clustering is no longer enabled"
    else:
        return

    fabric_value = _non_empty_text(_read_payload_field(payload, fabric_label))
    if not fabric_value:
        return

    if not gpu_shape_enabled:
        reason = disabled_reason
    elif not _provider_prompt_dependencies_ready(
        payload=payload,
        entry=entry,
        full_path_label=fabric_label,
    ):
        reason = "the selected GPU shape is incomplete"
    else:
        choices = _resolve_dynamic_field_choices(
            payload=payload,
            entry=entry,
            full_path_label=fabric_label,
            provider_lookup=provider_lookup,
        )
        if provider_lookup.last_error():
            return
        if any(choice.value == fabric_value for choice in choices):
            return
        reason = "the selected GPU preset does not allow GPU clustering"

    target_path = _parse_payload_path_label(fabric_label)
    if target_path is None or not _payload_path_exists(payload, target_path):
        return
    _delete_payload_value(payload, target_path)
    console.print(
        warning_markup(
            f"Cleared '{fabric_label}' because {reason} according to the live Nebius shape metadata."
        )
    )


def _is_compute_boot_disk_type_field(full_path_label: str) -> bool:
    return full_path_label.endswith(
        (
            ".cpu_nodes_boot_disk_type",
            ".gpu_nodes_boot_disk_type",
            ".boot_disk_type",
            ".data_disk_type",
        )
    )


def _is_mk8s_gpu_validation_field(full_path_label: str) -> bool:
    return bool(
        re.match(
            r"^deploy\.targets\[[0-9]+\]\.validations\.mk8s_gpu\.",
            full_path_label,
        )
        or full_path_label.startswith("deploy.targets[].validations.mk8s_gpu.")
    )


def _is_observability_field(full_path_label: str) -> bool:
    return full_path_label.startswith("deploy.observability.") or bool(
        re.match(r"^deploy\.targets(?:\[[0-9]+\]|\[\])\.observability\.", full_path_label)
    )


def _is_mysterybox_eso_field(full_path_label: str) -> bool:
    return bool(
        re.match(
            r"^deploy\.targets(?:\[[0-9]+\]|\[\])\.secrets\.mysterybox(?:\.|$)",
            full_path_label,
        )
    )


def _gpu_preset_field_context(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> tuple[str, str, str, str] | None:
    if entry.scope != "infra":
        return None
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return None

    if full_path_label.endswith(".gpu_nodes_preset"):
        platform_label = f"{component_prefix}.inputs.gpu_nodes_platform"
        preset_label = f"{component_prefix}.inputs.gpu_nodes_preset"
    elif full_path_label.endswith(".preset"):
        platform_label = f"{component_prefix}.inputs.platform"
        preset_label = full_path_label
    else:
        return None

    platform_name = _non_empty_text(_read_payload_field(payload, platform_label))
    if not platform_name.startswith("gpu-"):
        return None
    preset_name = _non_empty_text(_read_payload_field(payload, preset_label))
    return component_prefix, platform_label, preset_label, preset_name


def _maybe_print_gpu_preset_prompt_guidance(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if (
        _gpu_preset_field_context(payload=payload, entry=entry, full_path_label=full_path_label)
        is None
    ):
        return
    if "gpu_preset_interconnect" in emitted_guidance:
        return
    console.print(
        "[dim]GPU interconnect guidance: 1-GPU presets are Ethernet-only and best for "
        "testing/dev. Clusterable multi-GPU presets unlock InfiniBand / GPUDirect-RDMA "
        "for production distributed training.[/dim]"
    )
    emitted_guidance.add("gpu_preset_interconnect")


def _maybe_print_selected_gpu_preset_guidance(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
    emitted_guidance: set[str],
) -> None:
    if provider_lookup is None:
        return
    context = _gpu_preset_field_context(
        payload=payload,
        entry=entry,
        full_path_label=full_path_label,
    )
    if context is None:
        return

    _component_prefix, platform_label, preset_label, preset_name = context
    if not preset_name:
        return
    project_id = _non_empty_text(_read_payload_field(payload, "client_info.nebius.project_id"))
    platform_name = _non_empty_text(_read_payload_field(payload, platform_label))
    if not project_id or not platform_name:
        return

    compute_platform_preset_resources = getattr(
        provider_lookup, "compute_platform_preset_resources", None
    )
    compute_platform_preset_allows_gpu_clustering = getattr(
        provider_lookup, "compute_platform_preset_allows_gpu_clustering", None
    )
    if not callable(compute_platform_preset_resources) or not callable(
        compute_platform_preset_allows_gpu_clustering
    ):
        return

    resources = compute_platform_preset_resources(
        project_id=project_id,
        platform_name=platform_name,
        preset_name=preset_name,
    )
    allow_gpu_clustering = compute_platform_preset_allows_gpu_clustering(
        project_id=project_id,
        platform_name=platform_name,
        preset_name=preset_name,
    )
    gpu_count = resources[2] if resources is not None else None
    guidance_key = f"gpu_preset_selected:{preset_label}:{preset_name}"
    if guidance_key in emitted_guidance:
        return

    if gpu_count == 1 and allow_gpu_clustering is False:
        console.print(
            "[dim]Selected GPU shape uses Ethernet only with no GPUDirect-RDMA. "
            "Good for testing/dev, not production distributed training.[/dim]"
        )
        emitted_guidance.add(guidance_key)
        return

    if allow_gpu_clustering is True:
        console.print(
            "[dim]Selected GPU shape supports InfiniBand / GPUDirect-RDMA. "
            "Choose a fabric next when live capacity is available.[/dim]"
        )
        emitted_guidance.add(guidance_key)


def _payload_has_enabled_mk8s_gpu(payload: dict[str, Any]) -> bool:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False
    for item in components:
        if not isinstance(item, dict) or not bool(item.get("enabled", False)):
            continue
        if component_type_id(item) != "mk8s":
            continue
        inputs = item.get("inputs")
        if isinstance(inputs, dict) and bool(inputs.get("gpu_enabled", False)):
            return True
    return False


def _payload_has_enabled_mk8s_gpu_cluster(payload: dict[str, Any]) -> bool:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False
    for item in components:
        if not isinstance(item, dict) or not bool(item.get("enabled", False)):
            continue
        if component_type_id(item) != "mk8s":
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, dict) or not bool(inputs.get("gpu_enabled", False)):
            continue
        if _non_empty_text(inputs.get("infiniband_fabric")):
            return True
    return False


def _payload_has_enabled_mk8s(payload: dict[str, Any]) -> bool:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False
    return any(
        isinstance(item, dict)
        and bool(item.get("enabled", False))
        and component_type_id(item) == "mk8s"
        for item in components
    )


def _payload_has_enabled_mysterybox(payload: dict[str, Any]) -> bool:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False
    return any(
        isinstance(item, dict)
        and bool(item.get("enabled", False))
        and component_type_id(item) == MYSTERYBOX_INFRA_COMPONENT_ID
        for item in components
    )


def _payload_has_enabled_vm(payload: dict[str, Any]) -> bool:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, dict) else None
    if not isinstance(components, list):
        return False
    return any(
        isinstance(item, dict)
        and bool(item.get("enabled", False))
        and component_type_id(item) == "vm"
        for item in components
    )


def _maybe_print_compute_boot_disk_prompt_guidance(
    *,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if not _is_compute_boot_disk_type_field(full_path_label):
        return
    if "compute_boot_disk" in emitted_guidance:
        return
    console.print(
        "[dim]Compute disk guidance: cxcli recommends boot-disk size from the "
        "selected platform/preset and the settings-owned disk policy. Guided "
        "boot/data disk type choices are sourced from compute.boot_disk_defaults.disk_types.[/dim]"
    )
    emitted_guidance.add("compute_boot_disk")


def _maybe_print_mk8s_gpu_validation_prompt_guidance(
    *,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if not _is_mk8s_gpu_validation_field(full_path_label):
        return
    if "mk8s_gpu_validation" in emitted_guidance:
        return
    console.print(
        "[dim]MK8s GPU validation guidance: operator readiness checks the operator "
        "control-plane state plus allocatable GPUs on Ready nodes. GPU visibility "
        "runs a CUDA sample pod on selected GPU nodes. NCCL auto-selects "
        "Socket/TCPIP or RDMA transport from the configured GPU shape; the "
        "bandwidth threshold is only enforced on RDMA runs. Health checker "
        "only auto-enables a compatible app when the catalog exposes one.[/dim]"
    )
    emitted_guidance.add("mk8s_gpu_validation")


def _maybe_print_observability_prompt_guidance(
    *,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if not _is_observability_field(full_path_label):
        return
    if "observability" not in emitted_guidance:
        console.print(
            "[dim]Observability guidance: MK8s uses the Nebius observability agent "
            "chart for logs, Prometheus-style metrics, and OTLP traces. Compute VMs "
            "use the built-in Monitoring agent for service metrics. When VM "
            "journald collection is enabled, cxcli writes the supported Nebius "
            "Compute labels into the VM inputs during create/render/deploy. "
            "No VM-side collector package or cxcli-managed service account is "
            "installed for this path.[/dim]"
        )
        emitted_guidance.add("observability")
    specific_guidance = {
        "deploy.observability.vm.logs.enabled": (
            "Collect VM journald logs: answering yes applies the Nebius Compute "
            "journald labels to the VM. Blank systemd units collect all units; "
            "explicit units are written as a semicolon-separated allowlist."
        ),
    }
    message = specific_guidance.get(full_path_label)
    if message is None:
        return
    guidance_key = f"observability:{full_path_label}"
    if guidance_key in emitted_guidance:
        return
    console.print(f"[dim]{escape(message)}[/dim]")
    emitted_guidance.add(guidance_key)


def _maybe_print_ssh_jumphost_allowed_cidrs_guidance(
    *,
    entry: ComponentEntry,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if entry.scope != "infra" or entry.id != "ssh-jumphost":
        return
    if not full_path_label.endswith(".inputs.allowed_cidrs"):
        return
    guidance_key = "ssh_jumphost_allowed_cidrs"
    if guidance_key in emitted_guidance:
        return
    console.print(
        "[dim]SSH jump-host allowed CIDRs: source public IPv4 CIDRs allowed "
        "to SSH to the jump host from the internet. The wizard defaults this "
        "to the detected operator laptop public IP as a /32 when lookup is "
        "available.[/dim]"
    )
    emitted_guidance.add(guidance_key)


def _terraform_identifier_hint(value: str, *, fallback_prefix: str = "module") -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not token:
        token = fallback_prefix
    if not re.match(r"^[A-Za-z_]", token):
        token = f"{fallback_prefix}_{token}"
    return token


def _maybe_print_mysterybox_secrets_prompt_guidance(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    emitted_guidance: set[str],
) -> None:
    if entry.scope != "infra" or entry.id != "mysterybox":
        return
    if not full_path_label.endswith(".inputs.secrets"):
        return
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    guidance_key = f"mysterybox_secrets:{component_prefix or full_path_label}"
    if guidance_key in emitted_guidance:
        return
    env_var = "TF_VAR_<rendered_module_name>_payload_values"
    if component_prefix:
        module_name = _non_empty_text(
            _read_payload_field(payload, f"{component_prefix}.inputs.module_name")
        ) or _non_empty_text(_read_payload_field(payload, f"{component_prefix}.instance_id"))
        if module_name:
            env_var = f"TF_VAR_{_terraform_identifier_hint(module_name)}_payload_values"
    console.print(
        "[dim]MysteryBox: enter Secret names and payload keys only. "
        f"Values are supplied at deploy with {env_var}.[/dim]"
    )
    emitted_guidance.add(guidance_key)


def _skip_observability_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if entry.scope != "infra" or not _is_observability_field(full_path_label):
        return False
    mk8s_enabled = _payload_has_enabled_mk8s(payload)
    vm_enabled = _payload_has_enabled_vm(payload)
    if not (mk8s_enabled or vm_enabled):
        return True
    target_observability_match = re.match(
        r"^(deploy\.targets\[[0-9]+\]\.observability)(?:\.|$)",
        full_path_label,
    )
    if target_observability_match:
        if entry.id != "mk8s" or not mk8s_enabled:
            return True
        target_prefix = target_observability_match.group(1)
        if full_path_label == f"{target_prefix}.enabled":
            return False
        target_enabled = bool(_read_payload_field(payload, f"{target_prefix}.enabled"))
        if not target_enabled:
            return True
        kubernetes_prefix = f"{target_prefix}.kubernetes"
        if full_path_label.startswith(f"{kubernetes_prefix}.logs."):
            return bool(
                full_path_label != f"{kubernetes_prefix}.logs.enabled"
                and not _read_payload_field(payload, f"{kubernetes_prefix}.logs.enabled")
            )
        if full_path_label.startswith(f"{kubernetes_prefix}.metrics."):
            return bool(
                full_path_label != f"{kubernetes_prefix}.metrics.enabled"
                and not _read_payload_field(payload, f"{kubernetes_prefix}.metrics.enabled")
            )
        return False
    if full_path_label == "deploy.observability.enabled":
        return bool(entry.id != "vm" or not vm_enabled)
    if full_path_label.startswith("deploy.observability.vm."):
        if entry.id != "vm":
            return True
        if not vm_enabled:
            return True
    observability_enabled = bool(_read_payload_field(payload, "deploy.observability.enabled"))
    if not observability_enabled:
        return True
    if full_path_label == "deploy.observability.vm.logs.enabled":
        return False
    if full_path_label.startswith("deploy.observability.vm.logs."):
        return bool(
            full_path_label != "deploy.observability.vm.logs.enabled"
            and not _read_payload_field(payload, "deploy.observability.vm.logs.enabled")
        )
    return False


def _skip_mysterybox_eso_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if entry.scope != "infra" or not _is_mysterybox_eso_field(full_path_label):
        return False
    if entry.id != "mk8s" or not _payload_has_enabled_mk8s(payload):
        return True
    if not _payload_has_enabled_mysterybox(payload):
        return True
    target_match = re.match(
        r"^(deploy\.targets\[[0-9]+\]\.secrets\.mysterybox)(?:\.|$)",
        full_path_label,
    )
    if not target_match:
        return False
    target_prefix = target_match.group(1)
    if full_path_label == f"{target_prefix}.enabled":
        return False
    return not bool(_read_payload_field(payload, f"{target_prefix}.enabled"))


def _skip_vm_service_account_prompt(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if entry.scope != "infra" or entry.id != "vm":
        return False
    return full_path_label == "inputs.service_account_id"


def _skip_vm_preemptible_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if entry.scope != "infra" or entry.id != "vm":
        return False
    if not full_path_label.endswith((".preemptible_enabled", ".preemptible_priority")):
        return False
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return True
    platform = _non_empty_text(_read_payload_field(payload, f"{component_prefix}.inputs.platform"))
    return not platform.lower().startswith("gpu-")


def _boot_disk_type_for_component_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> str:
    if entry.scope != "infra":
        return ""
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return ""
    return _non_empty_text(
        _read_payload_field(payload, f"{component_prefix}.inputs.boot_disk_type")
    ).upper()


def _skip_compute_boot_disk_security_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if (
        entry.scope != "infra"
        or not _entry_declares_compute_boot_disk_contract(entry)
        or not full_path_label.endswith(
            (
                ".inputs.boot_disk_encryption_enabled",
                ".inputs.boot_disk_deletion_protection",
            )
        )
    ):
        return False
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return True
    if _non_empty_text(
        _read_payload_field(payload, f"{component_prefix}.inputs.boot_disk_existing_id")
    ):
        return True
    if full_path_label.endswith(".inputs.boot_disk_deletion_protection"):
        return False
    disk_type = _boot_disk_type_for_component_prompt(
        payload=payload,
        entry=entry,
        full_path_label=full_path_label,
    )
    if not disk_type:
        return True
    try:
        return not compute_boot_disk_type_supports_explicit_encryption(disk_type)
    except ValueError:
        return True


def _skip_compute_data_disk_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if (
        entry.scope != "infra"
        or not _entry_declares_compute_data_disk_contract(entry)
        or not full_path_label.endswith(
            (
                ".inputs.data_disk_type",
                ".inputs.data_disk_size_gib",
                ".inputs.data_disk_encryption_enabled",
                ".inputs.data_disk_deletion_protection",
            )
        )
    ):
        return False
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return True
    if _read_payload_field(payload, f"{component_prefix}.inputs.data_disk_enabled") is not True:
        return True
    if full_path_label.endswith((".inputs.data_disk_type", ".inputs.data_disk_size_gib")):
        return False
    if full_path_label.endswith(".inputs.data_disk_deletion_protection"):
        return False
    disk_type = _non_empty_text(
        _read_payload_field(payload, f"{component_prefix}.inputs.data_disk_type")
    ).upper()
    if not disk_type:
        return True
    try:
        return not compute_boot_disk_type_supports_explicit_encryption(disk_type)
    except ValueError:
        return True


def _data_disk_size_default_for_entry(entry: ComponentEntry) -> int:
    spec = _module_variable_specs_for_entry(entry).get("data_disk_size_gib")
    default = _state_positive_int(getattr(spec, "default", None))
    return default if default and default > 0 else 128


def _maybe_refresh_compute_data_disk_size_after_type_change(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> None:
    if (
        entry.scope != "infra"
        or not _entry_declares_compute_data_disk_contract(entry)
        or not full_path_label.endswith(".inputs.data_disk_type")
    ):
        return
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return
    if _read_payload_field(payload, f"{component_prefix}.inputs.data_disk_enabled") is not True:
        return
    size_label = f"{component_prefix}.inputs.data_disk_size_gib"
    if _read_payload_field(payload, size_label) is not None:
        return
    disk_type = _non_empty_text(
        _read_payload_field(payload, f"{component_prefix}.inputs.data_disk_type")
    ).upper()
    if not disk_type:
        return
    base_size = _data_disk_size_default_for_entry(entry)
    try:
        aligned_size = align_compute_disk_size_to_allocation_unit(
            base_size,
            disk_type=disk_type,
        )
    except ValueError:
        return
    if aligned_size == base_size:
        return
    parsed_size_path = _parse_payload_path_label(size_label)
    if parsed_size_path is not None:
        _set_payload_value_creating_containers(payload, parsed_size_path, aligned_size)


def _jump_host_create_public_ip_allocation_enabled(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool | None:
    if entry.scope != "infra" or entry.id not in {"ssh-jumphost", "wireguard-gw"}:
        return None
    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return None
    current = _read_payload_field(
        payload,
        f"{component_prefix}.inputs.create_public_ip_allocation",
    )
    return bool(current) if current is not None else True


def _skip_jump_host_public_ip_allocation_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    create_allocation = _jump_host_create_public_ip_allocation_enabled(
        payload=payload,
        entry=entry,
        full_path_label=full_path_label,
    )
    if create_allocation is None:
        return False
    if full_path_label.endswith(".inputs.public_ip_allocation_id"):
        return create_allocation
    if full_path_label.endswith(".inputs.public_ip_allocation_name"):
        return not create_allocation
    return False


def _dynamic_required_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if full_path_label.endswith(".inputs.public_ip_allocation_id"):
        return (
            _jump_host_create_public_ip_allocation_enabled(
                payload=payload,
                entry=entry,
                full_path_label=full_path_label,
            )
            is False
        )
    return False


def _maybe_materialize_vm_preemptible_recovery_policy(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    component_path: PayloadPath | None,
) -> None:
    if entry.scope != "infra" or entry.id != "vm" or component_path is None:
        return
    inputs_path = component_path + ("inputs",)
    preemptible_path = inputs_path + ("preemptible_enabled",)
    if not _payload_path_exists(payload, preemptible_path) or not bool(
        _get_payload_value(payload, preemptible_path)
    ):
        return
    recovery_policy_path = inputs_path + ("recovery_policy",)
    current = (
        _non_empty_text(_get_payload_value(payload, recovery_policy_path)).upper()
        if _payload_path_exists(payload, recovery_policy_path)
        else ""
    )
    if current == "FAIL":
        return
    _set_payload_value_creating_containers(payload, recovery_policy_path, "FAIL")
    recovery_policy_label = _format_payload_path(recovery_policy_path)
    console.print(
        f"{warning_markup('Adjusted VM preemptible settings:')} "
        f"set '{escape(recovery_policy_label)}' to 'FAIL' because Nebius preemptible "
        "VMs require recovery_policy=FAIL and render on_preemption=STOP."
    )
    _print_wizard_selected_field(recovery_policy_label, "FAIL")


def _skip_mk8s_gpu_validation_prompt(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
) -> bool:
    if (
        entry.scope != "infra"
        or entry.id != "mk8s"
        or not _is_mk8s_gpu_validation_field(full_path_label)
    ):
        return False
    if not _payload_has_enabled_mk8s_gpu(payload):
        return True
    if (
        full_path_label.endswith(".validations.mk8s_gpu.health_checker.enabled")
        and not has_mk8s_gpu_health_checker_app()
    ):
        return True
    return full_path_label.endswith(
        ".validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps"
    ) and not _payload_has_enabled_mk8s_gpu_cluster(payload)


def _maybe_refresh_compute_boot_disk_defaults_after_shape_change(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    previous_component_inputs: dict[str, Any] | None,
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if (
        entry.scope != "infra"
        or (entry.id != "mk8s" and not _entry_declares_compute_boot_disk_contract(entry))
        or previous_component_inputs is None
    ):
        return
    watched_suffixes = (
        (
            ".cpu_nodes_platform",
            ".cpu_nodes_preset",
            ".cpu_nodes_boot_disk_type",
            ".gpu_enabled",
            ".gpu_nodes_platform",
            ".gpu_nodes_preset",
            ".gpu_nodes_boot_disk_type",
        )
        if entry.id == "mk8s"
        else (".platform", ".preset", ".boot_disk_type", ".boot_disk_existing_id")
    )
    if not full_path_label.endswith(watched_suffixes):
        return

    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return
    component_path = _parse_payload_path_label(component_prefix)
    if component_path is None or not _payload_path_exists(payload, component_path):
        return
    component_node = _get_payload_value(payload, component_path)
    if not isinstance(component_node, dict):
        return
    inputs = component_node.get("inputs")
    if not isinstance(inputs, dict):
        return
    project_id = _non_empty_text(_read_payload_field(payload, "client_info.nebius.project_id"))
    if not project_id:
        return
    refresh_compute_boot_disk_defaults(
        inputs,
        previous_component_inputs,
        component_id=entry.id,
        instance_id=component_node.get("instance_id") or entry.id,
        project_id=project_id,
        provider_lookup=provider_lookup,
    )


def _app_chart_default_values(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    instance_id: str | None = None,
) -> dict[str, Any]:
    if entry.scope != "apps":
        return {}
    component_path = (
        _dynamic_app_chart_path(payload, entry.id, instance_id=instance_id)
        if instance_id is not None
        else _dynamic_component_path(payload, entry)
    )
    if component_path is None:
        return {}
    component_node = _get_payload_value(payload, component_path)
    if not isinstance(component_node, Mapping):
        return {}
    chart_name = _runtime_app_chart_name_for_id(
        chart_node=component_node,
        chart_id=entry.id,
        entry=entry,
    )
    chart_repo = str(component_node.get("repo", "")).strip()
    chart_version = str(component_node.get("version", "")).strip()
    if not chart_name:
        return {}

    defaults = helm_chart_default_values(
        chart_name_or_ref=chart_name,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )
    return defaults if isinstance(defaults, dict) else {}


def _prune_redundant_app_chart_default_values(
    *,
    payload: dict[str, Any],
    app_entries: tuple[ComponentEntry, ...],
) -> None:
    entry_lookup = {entry.id: entry for entry in app_entries}
    for row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(row["id"])
        instance_id = str(row["instance_id"])
        entry = entry_lookup.get(chart_id)
        if entry is None:
            continue
        component_path = _dynamic_app_chart_path(payload, chart_id, instance_id=instance_id)
        if component_path is None:
            continue
        chart_defaults = _app_chart_default_values(
            payload=payload,
            entry=entry,
            instance_id=instance_id,
        )
        if not chart_defaults:
            continue
        values_path = component_path + ("values",)
        if not _payload_path_exists(payload, values_path):
            continue
        for relative_path in _collect_scalar_leaf_paths(chart_defaults):
            full_path = values_path + relative_path
            if not _payload_path_exists(payload, full_path):
                continue
            explicit_value = _get_payload_value(payload, full_path)
            default_value = _get_payload_value(chart_defaults, relative_path)
            if explicit_value == default_value:
                _delete_payload_value(payload, full_path)


def _chart_source_parts(entry: ComponentEntry) -> tuple[str | None, str | None]:
    source = str(entry.source or "").strip().rstrip("/")
    if not source or "/" not in source:
        return None, None
    repo, chart_name = source.rsplit("/", maxsplit=1)
    if not repo or not chart_name:
        return None, None
    return repo, chart_name


def _canonical_app_chart_repo(*, chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    if repo.startswith("oci://"):
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail != chart_name.strip().lower():
            return f"{repo}/{chart_name.strip()}"
    return repo


def _seed_component_prompt_fields(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    required_leaf_names: set[str],
    instance_id: str | None = None,
) -> None:
    component_path = (
        _dynamic_infra_component_path(payload, entry.id, instance_id=instance_id)
        if entry.scope == "infra" and instance_id is not None
        else _dynamic_app_chart_path(payload, entry.id, instance_id=instance_id)
        if entry.scope == "apps" and instance_id is not None
        else _dynamic_component_path(payload, entry)
    )
    if component_path is None:
        return
    component_node = _get_payload_value(payload, component_path)
    if not isinstance(component_node, dict):
        component_node = {}
        _set_payload_value(payload, component_path, component_node)

    if entry.scope == "apps":
        # Ensure chart-backed entries discovered at runtime have editable scaffolding.
        component_node.setdefault(
            "namespace", str(entry.default_namespace or "").strip() or entry.id
        )
        component_node.setdefault(
            "release-name",
            str(entry.default_release_name or "").strip() or entry.id,
        )
        chart_repo, chart_name = _chart_source_parts(entry)
        if chart_repo and chart_name and chart_repo.startswith("oci://"):
            repo_tail = chart_repo.rsplit("/", maxsplit=1)[-1].strip().lower()
            if repo_tail != chart_name.lower():
                chart_repo = f"{chart_repo}/{chart_name}"
        if chart_repo:
            component_node.setdefault("repo", chart_repo)
        elif entry.chart_repo:
            component_node.setdefault("repo", str(entry.chart_repo).strip())
        component_node.setdefault("version", str(entry.version or ""))
        chart_values = component_node.get("values")
        if not isinstance(chart_values, dict):
            component_node["values"] = {}
        return

    if entry.scope != "infra":
        return

    inputs_node = component_node.get("inputs")
    if not isinstance(inputs_node, dict):
        component_node["inputs"] = {}


def _wizard_field_prompt_suffix(rendered_label: str) -> str:
    return (
        f"{rendered_label} (enter {WIZARD_EXIT_TOKEN} to go back; "
        f"{WIZARD_ABORT_TOKEN} quits wizard)"
    )


_WIZARD_REDACTED_FIELD_TOKENS = (
    "access_key",
    "api_key",
    "certificate",
    "credentials",
    "iam_token",
    "password",
    "private_key",
    "secret",
    "token",
)


def _wizard_field_is_sensitive(path_label: str) -> bool:
    normalized_label = re.sub(
        r"deploy\.targets\[[0-9]+\]",
        "deploy.targets[]",
        path_label.lower(),
    )
    public_mysterybox_sync_fields = {
        "deploy.targets[].secrets.mysterybox.enabled",
        "deploy.targets[].secrets.mysterybox.store_name",
        "deploy.targets[].secrets.mysterybox.api_domain",
        "deploy.targets[].secrets.mysterybox.allow_all_namespaces",
        "deploy.targets[].secrets.mysterybox.refresh_interval",
        "deploy.targets[].secrets.mysterybox.sync_namespaces",
    }
    if normalized_label in public_mysterybox_sync_fields:
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", path_label.lower())
    return any(token in normalized for token in _WIZARD_REDACTED_FIELD_TOKENS)


def _is_mysterybox_secrets_path(path_label: str) -> bool:
    return bool(re.search(r"\.inputs\.secrets$", path_label))


def _is_ssh_public_key_prompt(path_label: str) -> bool:
    return bool(re.search(r"\.inputs\.ssh_public_key$", path_label))


def _ssh_public_key_summary(value: str) -> str:
    parts = value.strip().split(None, 2)
    if not parts:
        return ""
    key_type = parts[0]
    comment = parts[2].strip() if len(parts) > 2 else ""
    return f"{key_type} {comment}".strip()


def _mysterybox_secrets_summary(value: object) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value[:3]:
        if not isinstance(item, Mapping):
            continue
        name = _non_empty_text(item.get("name")) or "<unnamed>"
        kubernetes_secret_name = _non_empty_text(item.get("kubernetes_secret_name"))
        if kubernetes_secret_name and kubernetes_secret_name != name:
            name = f"{name}->{kubernetes_secret_name}"
        payload = item.get("payload")
        payload_keys = (
            sorted(str(key).strip() for key in payload if str(key).strip())
            if isinstance(payload, Mapping)
            else []
        )
        if payload_keys:
            parts.append(f"{name} ({', '.join(payload_keys[:5])})")
        else:
            parts.append(name)
    if len(value) > 3:
        parts.append(f"+{len(value) - 3} more")
    return "; ".join(parts) if parts else "[]"


def _wizard_visible_value(value: object, *, path_label: str) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_mysterybox_secrets_path(path_label):
        summary = _mysterybox_secrets_summary(value)
        if summary:
            return summary
    if isinstance(value, str) and _is_ssh_public_key_prompt(path_label):
        summary = _ssh_public_key_summary(value)
        if summary:
            return summary
    if _wizard_field_is_sensitive(path_label):
        return "<redacted>"
    if isinstance(value, str):
        rendered = value
    else:
        rendered = str(value)
        with suppress(TypeError):
            rendered = json.dumps(value, sort_keys=True)
    rendered = rendered.strip()
    if len(rendered) > 160:
        rendered = f"{rendered[:157]}..."
    return rendered


def _print_wizard_selected_field(path_label: str, value: object) -> None:
    rendered_value = _wizard_visible_value(value, path_label=path_label)
    console.print(f"[dim]Selected {escape(path_label)} = {escape(rendered_value)}[/dim]")


def _print_wizard_section_banner(
    *,
    title: str,
    components: Sequence[tuple[ComponentEntry, str]],
) -> None:
    if not components:
        return
    labels = ", ".join(
        component_instance_label(entry.id, instance_id) for entry, instance_id in components
    )
    console.print()
    console.print(f"[bold cyan]--- {escape(title)} wizard section ---[/bold cyan]")
    console.print(f"[dim]Selected components: {escape(labels)}[/dim]")


def _wizard_backtrack_requested(value: object) -> bool:
    return value is _WIZARD_BACKTRACK


def _is_flat_module_prompt_path(
    *,
    full_path: PayloadPath,
    module_prompt_path_prefix: PayloadPath | None,
) -> bool:
    return (
        module_prompt_path_prefix is not None
        and len(full_path) == len(module_prompt_path_prefix) + 1
        and full_path[: len(module_prompt_path_prefix)] == module_prompt_path_prefix
        and isinstance(full_path[-1], str)
    )


def _wizard_previous_prompt_index(
    *,
    prompt_paths: list[PayloadPath],
    prompt_history: list[PayloadPath],
    current_path: PayloadPath | None = None,
) -> int | None:
    while prompt_history:
        previous_path = prompt_history.pop()
        if current_path is not None and previous_path == current_path:
            continue
        with suppress(ValueError):
            return prompt_paths.index(previous_path)
    return None


def _wizard_backtrack_target_index(
    *,
    prompt_paths: list[PayloadPath],
    prompt_history: list[PayloadPath],
    current_path: PayloadPath | None = None,
) -> int | None:
    previous_prompt_index = _wizard_previous_prompt_index(
        prompt_paths=prompt_paths,
        prompt_history=prompt_history,
        current_path=current_path,
    )
    if previous_prompt_index is not None:
        return previous_prompt_index
    return None


def _payload_path_has_prefix(path: PayloadPath, prefix: PayloadPath) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def _wizard_backtrack_prefix(
    *,
    component_path: PayloadPath | None,
    full_path: PayloadPath,
) -> PayloadPath | None:
    prefix = full_path[:-1]
    if component_path is None:
        return prefix or None
    if len(prefix) <= len(component_path):
        return component_path
    return prefix


def _skip_remaining_prompt_paths_for_prefix(
    *,
    prompt_paths: list[PayloadPath],
    prompt_index: int,
    prefix: PayloadPath,
) -> None:
    prompt_paths[prompt_index:] = [
        path for path in prompt_paths[prompt_index:] if not _payload_path_has_prefix(path, prefix)
    ]


def _prompt_choice_override(
    *,
    path_label: str,
    current: object,
    choices: list[OptionChoice],
    type_hint: str | None = None,
    required: bool = False,
) -> tuple[object, bool]:
    rendered_label = _prompt_label_with_type(
        path_label,
        type_hint=type_hint,
        required=required,
    )
    prompt_suffix = _wizard_field_prompt_suffix(rendered_label)
    default_value = str(current).strip() if current is not None else ""
    option_values = [choice.value for choice in choices]
    option_values_by_text = {str(choice.value).strip(): choice.value for choice in choices}
    has_explicit_current = default_value in option_values
    recommended_default = next(
        (
            choice.value
            for choice in choices
            if choice.recommended and choice.value in option_values
        ),
        "",
    )
    prompt_default = (
        default_value
        if has_explicit_current
        else (recommended_default or (option_values[0] if required and option_values else ""))
    )
    if _is_tty_session():
        try:
            import questionary

            rendered_choices = [
                questionary.Choice(title=choice.label, value=choice.value) for choice in choices
            ]
            if not required and not has_explicit_current:
                rendered_choices.insert(
                    0,
                    questionary.Choice(title="<skip / keep unset>", value="__skip__"),
                )
            selected = _ask_questionary_with_wizard_navigation(
                questionary.select(
                    rendered_label,
                    choices=rendered_choices,
                    instruction="Use arrows; q=back; qq=quit; Enter=select.",
                    default=prompt_default
                    or ("__skip__" if not required and not has_explicit_current else None),
                    qmark="",
                )
            )
            if selected is None:
                return current, True
            if selected == _WIZARD_QUIT_CHOICE:
                return current, True
            if selected == _WIZARD_BACK_CHOICE:
                return _WIZARD_BACKTRACK, False
            if selected == "__skip__":
                return current, False
            return str(selected).strip(), False
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        except Exception:
            pass

    console.print(f"[cyan]{path_label} options:[/cyan]")
    for index, choice in enumerate(choices, start=1):
        marker = "*" if choice.value == prompt_default else " "
        console.print(f"  {marker} [{index}] {choice.label}")

    prompt_detail = "index or value"
    if not required:
        prompt_detail = (
            "index or value; blank keeps current"
            if has_explicit_current
            else "index or value; blank keeps unset"
        )

    while True:
        try:
            raw = typer.prompt(f"{prompt_suffix} ({prompt_detail})", default=prompt_default).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_ABORT_TOKEN:
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        if not raw:
            if has_explicit_current or required:
                return prompt_default, False
            return current, False
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(choices):
                return choices[index - 1].value, False
            console.print(
                f"{error_markup('Invalid option index')}. Use a value between 1 and {len(choices)}."
            )
            continue
        if raw in option_values_by_text:
            return option_values_by_text[raw], False
        allowed = ", ".join(str(value) for value in option_values)
        console.print(
            f"{error_markup('Invalid option value')}. Use an option index or one of: {allowed}."
        )


def _ssh_public_key_file_choices() -> list[OptionChoice]:
    choices: list[OptionChoice] = []
    for index, candidate in enumerate(discover_ssh_public_key_files()):
        summary = candidate.key_type
        if candidate.comment:
            summary = f"{summary} {candidate.comment}"
        choices.append(
            OptionChoice(
                value=candidate.public_key,
                label=f"{candidate.display_path} ({summary})",
                recommended=index == 0,
            )
        )
    return choices


def _prompt_manual_ssh_public_key_override(
    path_label: str,
    current: object,
    *,
    required: bool,
) -> tuple[object, bool]:
    rendered_label = _prompt_label_with_type(path_label, type_hint="string", required=required)
    prompt_suffix = _wizard_field_prompt_suffix(rendered_label)
    has_current = isinstance(current, str) and bool(current.strip())
    prompt_detail = "local .pub path or inline public key"
    if has_current:
        prompt_detail = f"{prompt_detail}; blank keeps current"
    while True:
        try:
            raw = typer.prompt(f"{prompt_suffix} ({prompt_detail})", default="").strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_ABORT_TOKEN:
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        if not raw:
            if has_current:
                return current, False
            if required:
                console.print(f"{error_markup('Invalid value')}. This field is required.")
                continue
            return current, False
        try:
            return (
                normalize_ssh_public_key_value(raw, field_label=path_label),
                False,
            )
        except ValueError as exc:
            console.print(f"{error_markup('Invalid value')}. {exc}")


def _prompt_ssh_public_key_override(
    path_label: str,
    current: object,
    *,
    required: bool,
) -> tuple[object, bool]:
    choices = _ssh_public_key_file_choices()
    if not choices:
        console.print(
            "[dim]No supported SSH public key files found under ~/.ssh. "
            "Enter a readable .pub path or paste an inline public key.[/dim]"
        )
        return _prompt_manual_ssh_public_key_override(
            path_label,
            current,
            required=required,
        )

    rendered_label = _prompt_label_with_type(path_label, type_hint="string", required=required)
    prompt_suffix = _wizard_field_prompt_suffix(rendered_label)
    current_value = str(current).strip() if current is not None else ""
    current_choice_index = next(
        (index for index, choice in enumerate(choices, start=1) if choice.value == current_value),
        None,
    )
    keep_current_choice = "__keep_current_ssh_key__"
    recommended_index = next(
        (index for index, choice in enumerate(choices, start=1) if choice.recommended),
        1,
    )
    prompt_default = ""
    if current_choice_index is not None:
        prompt_default = str(current_choice_index)
    elif required and not current_value:
        prompt_default = str(recommended_index)

    if _is_tty_session():
        try:
            import questionary

            questionary_default = None
            if current_value and current_choice_index is None:
                questionary_default = keep_current_choice
            elif required or current_choice_index is not None:
                questionary_default = choices[(current_choice_index or recommended_index) - 1].value

            rendered_choices = [
                questionary.Choice(title=choice.label, value=choice.value) for choice in choices
            ]
            if current_value and current_choice_index is None:
                rendered_choices.insert(
                    0,
                    questionary.Choice(
                        title="<keep current SSH public key>",
                        value=keep_current_choice,
                    ),
                )
            rendered_choices.append(
                questionary.Choice(
                    title="<manual path or inline public key>",
                    value="__manual_ssh_key__",
                )
            )
            if not required and not current_choice_index and not current_value:
                rendered_choices.insert(
                    0,
                    questionary.Choice(title="<skip / keep unset>", value="__skip__"),
                )
            selected = _ask_questionary_with_wizard_navigation(
                questionary.select(
                    rendered_label,
                    choices=rendered_choices,
                    instruction="Use arrows; q=back; qq=quit; Enter=select.",
                    default=questionary_default,
                    qmark="",
                )
            )
            if selected is None:
                return current, True
            if selected == _WIZARD_QUIT_CHOICE:
                return current, True
            if selected == _WIZARD_BACK_CHOICE:
                return _WIZARD_BACKTRACK, False
            if selected == "__skip__":
                return current, False
            if selected == keep_current_choice:
                return current, False
            if selected == "__manual_ssh_key__":
                return _prompt_manual_ssh_public_key_override(
                    path_label,
                    current,
                    required=required,
                )
            return str(selected).strip(), False
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        except Exception:
            pass

    console.print(f"[cyan]{path_label} SSH public key files:[/cyan]")
    for index, choice in enumerate(choices, start=1):
        marker = "*" if str(index) == prompt_default else " "
        console.print(f"  {marker} [{index}] {choice.label}")

    prompt_detail = "index, local .pub path, or inline public key"
    if current_value:
        prompt_detail = f"{prompt_detail}; blank keeps current"
    elif not required:
        prompt_detail = f"{prompt_detail}; blank keeps unset"

    while True:
        try:
            raw = typer.prompt(
                f"{prompt_suffix} ({prompt_detail})",
                default=prompt_default,
            ).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_ABORT_TOKEN:
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        if not raw:
            if current_value:
                return current, False
            if required:
                raw = prompt_default
            else:
                return current, False
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(choices):
                return choices[index - 1].value, False
            console.print(
                f"{error_markup('Invalid option index')}. Use a value between 1 and {len(choices)}."
            )
            continue
        try:
            return (
                normalize_ssh_public_key_value(raw, field_label=path_label),
                False,
            )
        except ValueError as exc:
            console.print(f"{error_markup('Invalid value')}. {exc}")


def _is_mysterybox_secrets_guided_prompt(path_label: str, type_hint: str | None) -> bool:
    normalized_type = re.sub(r"\s+", " ", str(type_hint or "").strip().lower())
    return (
        _is_mysterybox_secrets_path(path_label)
        and "list(object" in normalized_type
        and "payload" in normalized_type
    )


def _prompt_mysterybox_payload_type(payload_key: str) -> tuple[str | object, bool]:
    return _prompt_choice_override(
        path_label=f"Payload type for {payload_key}",
        current="text",
        choices=[
            OptionChoice(value="text", label="text"),
            OptionChoice(value="file", label="file"),
        ],
        required=True,
    )


def _last_mysterybox_payload_key(payload: Mapping[str, object]) -> str:
    return next(reversed(payload), "")


def _default_mysterybox_kubernetes_secret_name(secret_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", secret_name.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    normalized = normalized[:63].strip("-")
    return normalized or "mysterybox-secret"


def _prompt_mysterybox_kubernetes_secret_name(secret_name: str) -> tuple[str, bool]:
    default_name = _default_mysterybox_kubernetes_secret_name(secret_name)
    while True:
        try:
            raw = typer.prompt(
                f"Kubernetes Secret name for {secret_name} (q=back, qq=quit wizard)",
                default=default_name,
            ).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return "", True
        if raw == WIZARD_ABORT_TOKEN:
            return "", True
        if raw == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        if not raw:
            raw = default_name
        if INSTANCE_ID_PATTERN.fullmatch(raw):
            return raw, False
        console.print(f"{error_markup('Invalid value')}. Enter a valid Kubernetes Secret name.")


def _prompt_mysterybox_eso_version_policy(secret_name: str) -> tuple[str | object, bool]:
    from .mysterybox_eso import (
        MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY,
        MYSTERYBOX_ESO_MANUAL_VERSION_POLICY,
    )

    return _prompt_choice_override(
        path_label=f"ESO version policy for {secret_name}",
        current=MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY,
        choices=[
            OptionChoice(
                value=MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY,
                label="auto-primary-version-pinning",
                recommended=True,
            ),
            OptionChoice(
                value=MYSTERYBOX_ESO_MANUAL_VERSION_POLICY,
                label="manual-version-pinning",
            ),
        ],
        required=True,
    )


def _prompt_mysterybox_secrets_override(
    path_label: str,
    current: object,
    *,
    required: bool,
) -> tuple[object, bool]:
    existing = copy.deepcopy(current) if isinstance(current, list) else []
    secrets: list[dict[str, Any]] = [
        copy.deepcopy(item) for item in existing if isinstance(item, Mapping)
    ]
    seen_names = {
        _non_empty_text(item.get("name"))
        for item in secrets
        if isinstance(item, Mapping) and _non_empty_text(item.get("name"))
    }
    if secrets:
        console.print(
            f"[dim]Current {escape(path_label)}: "
            f"{escape(_mysterybox_secrets_summary(secrets))}[/dim]"
        )
    while True:
        secret_prompt = (
            "MysteryBox Secret name (required, q=back, qq=quit wizard)"
            if required and not secrets
            else "MysteryBox Secret name (blank=done, q=back, qq=quit wizard)"
        )
        try:
            secret_name = typer.prompt(
                secret_prompt,
                default="",
            ).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if secret_name == WIZARD_ABORT_TOKEN:
            return current, True
        if secret_name == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        if not secret_name:
            if secrets:
                return secrets, False
            if required:
                console.print(f"{error_markup('Invalid value')}. Add at least one Secret.")
                continue
            return current, False
        if secret_name in seen_names:
            console.print(f"{error_markup('Invalid value')}. Secret names must be unique.")
            continue

        kubernetes_secret_name, should_stop = _prompt_mysterybox_kubernetes_secret_name(secret_name)
        if should_stop:
            return current, True
        if _wizard_backtrack_requested(kubernetes_secret_name):
            continue

        eso_version_policy, should_stop = _prompt_mysterybox_eso_version_policy(secret_name)
        if should_stop:
            return current, True
        if _wizard_backtrack_requested(eso_version_policy):
            continue

        payload: dict[str, dict[str, str]] = {}
        restart_secret_prompt = False
        while True:
            payload_key_prompt = (
                f"Payload key for {secret_name} (required, q=back, qq=quit wizard)"
                if not payload
                else f"Payload key for {secret_name} (blank=finish Secret, q=back, qq=quit wizard)"
            )
            try:
                payload_key = typer.prompt(
                    payload_key_prompt,
                    default="",
                ).strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if payload_key == WIZARD_ABORT_TOKEN:
                return current, True
            if payload_key == WIZARD_EXIT_TOKEN:
                last_payload_key = _last_mysterybox_payload_key(payload)
                if not last_payload_key:
                    restart_secret_prompt = True
                    break
                payload.pop(last_payload_key, None)
                console.print(
                    f"[dim]Backtracked from {escape(last_payload_key)}; "
                    "re-enter that payload key or finish the Secret.[/dim]"
                )
                continue
            if not payload_key:
                if payload:
                    break
                console.print(f"{error_markup('Invalid value')}. Add at least one payload key.")
                continue
            payload_key = payload_key.upper()
            if payload_key in payload:
                console.print(f"{error_markup('Invalid value')}. Payload keys must be unique.")
                continue
            console.print(f"[dim]Entered {escape(payload_key)} as the key.[/dim]")
            payload_type, should_stop = _prompt_mysterybox_payload_type(payload_key)
            if should_stop:
                return current, True
            if _wizard_backtrack_requested(payload_type):
                continue
            payload[payload_key] = {"type": str(payload_type)}

        if restart_secret_prompt:
            continue
        secrets.append(
            {
                "name": secret_name,
                "version_id": "n/a",
                "eso_version_policy": str(eso_version_policy),
                "kubernetes_secret_name": kubernetes_secret_name,
                "payload": payload,
            }
        )
        seen_names.add(secret_name)
        console.print(
            f"[dim]Added MysteryBox Secret {escape(secret_name)} with "
            f"{len(payload)} payload key(s), syncing to Kubernetes Secret "
            f"{escape(kubernetes_secret_name)}.[/dim]"
        )


def _prompt_scalar_override(
    path_label: str,
    current: object,
    *,
    choices: list[OptionChoice] | None = None,
    type_hint: str | None = None,
    required: bool = False,
) -> tuple[object, bool]:
    if _is_ssh_public_key_prompt(path_label):
        return _prompt_ssh_public_key_override(
            path_label,
            current,
            required=required,
        )
    if choices:
        return _prompt_choice_override(
            path_label=path_label,
            current=current,
            choices=choices,
            type_hint=type_hint,
            required=required,
        )
    if _is_mysterybox_secrets_guided_prompt(path_label, type_hint):
        return _prompt_mysterybox_secrets_override(
            path_label,
            current,
            required=required,
        )
    rendered_label = _prompt_label_with_type(
        path_label,
        type_hint=type_hint,
        required=required,
    )
    prompt_suffix = _wizard_field_prompt_suffix(rendered_label)
    if _is_complex_type_hint(type_hint) or isinstance(current, (dict, list)):
        default_value = _serialize_complex_prompt_default(current)
        if _is_string_sequence_type_hint(type_hint):
            prompt_suffix = f"{prompt_suffix}; enter a comma-separated list"
        else:
            prompt_suffix = f"{prompt_suffix}; enter a single-line YAML/JSON value"
        blank_hint = _empty_complex_value_label(current, type_hint=type_hint)
        blank_keep_text = (
            f"blank keeps current {blank_hint}"
            if blank_hint is not None and not required
            else "blank keeps current"
        )
        prompt_default = "" if blank_hint is not None and not required else default_value
        while True:
            try:
                raw = typer.prompt(
                    f"{prompt_suffix} ({blank_keep_text})",
                    default=prompt_default,
                ).strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_ABORT_TOKEN:
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return _WIZARD_BACKTRACK, False
            if not raw:
                if required and not _has_required_prompt_value(current, type_hint=type_hint):
                    console.print(f"{error_markup('Invalid value')}. This field is required.")
                    continue
                return current, False
            try:
                coerced = _parse_complex_prompt_value(raw, type_hint=type_hint)
            except ValueError as exc:
                console.print(f"{error_markup('Invalid value')}. {exc}")
                continue
            if required and not _has_required_prompt_value(coerced, type_hint=type_hint):
                console.print(
                    f"{error_markup('Invalid value')}. "
                    "This field is required and cannot be an empty YAML/JSON collection."
                )
                continue
            return coerced, False
    while True:
        if isinstance(current, bool):
            try:
                raw = (
                    typer.prompt(
                        f"{prompt_suffix} [true/false]",
                        default="true" if current else "false",
                    )
                    .strip()
                    .lower()
                )
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_ABORT_TOKEN:
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return _WIZARD_BACKTRACK, False
            if raw in {"true", "t", "1", "yes", "y"}:
                return True, False
            if raw in {"false", "f", "0", "no", "n"}:
                return False, False
            console.print(f"{error_markup('Invalid boolean')}. Expected true/false.")
            continue

        if isinstance(current, int):
            try:
                raw = typer.prompt(prompt_suffix, default=str(current)).strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_ABORT_TOKEN:
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return _WIZARD_BACKTRACK, False
            try:
                return int(raw), False
            except ValueError:
                console.print(f"{error_markup('Invalid integer')}. Enter a whole number.")
                continue

        if isinstance(current, float):
            try:
                raw = typer.prompt(prompt_suffix, default=str(current)).strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_ABORT_TOKEN:
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return _WIZARD_BACKTRACK, False
            try:
                return float(raw), False
            except ValueError:
                console.print(f"{error_markup('Invalid number')}. Enter a numeric value.")
                continue

        if current is None:
            try:
                raw = typer.prompt(f"{prompt_suffix} (blank keeps null)", default="").strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_ABORT_TOKEN:
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return _WIZARD_BACKTRACK, False
            if not raw:
                if required:
                    console.print(f"{error_markup('Invalid value')}. This field is required.")
                    continue
                return None, False
            try:
                coerced = _coerce_raw_value_from_type_hint(raw, type_hint)
            except ValueError as exc:
                console.print(f"{error_markup('Invalid value')}. {exc}")
                continue
            if required and not _has_required_prompt_value(coerced, type_hint=type_hint):
                console.print(f"{error_markup('Invalid value')}. This field is required.")
                continue
            return coerced, False

        try:
            raw = typer.prompt(prompt_suffix, default=str(current)).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_ABORT_TOKEN:
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return _WIZARD_BACKTRACK, False
        try:
            coerced = _coerce_raw_value_from_type_hint(raw, type_hint)
        except ValueError as exc:
            console.print(f"{error_markup('Invalid value')}. {exc}")
            continue
        if required and not _has_required_prompt_value(coerced, type_hint=type_hint):
            console.print(f"{error_markup('Invalid value')}. This field is required.")
            continue
        return coerced, False


def _run_component_field_wizard(
    *,
    config_yaml: str,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    provider_lookup: ProviderOptionLookup | None = None,
) -> tuple[str, bool]:
    payload = yaml.safe_load(config_yaml) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Generated config template is not a YAML mapping")

    infra_lookup = {entry.id: entry for entry in infra_entries}
    app_lookup = {entry.id: entry for entry in app_entries}
    active_selected_apps = set(selected_apps)
    wizard_auto_enabled_observability_apps: set[str] = set()

    warned_provider_fallbacks: set[str] = set()
    provider_allowed_cache: dict[str, tuple[set[str], tuple[str, ...]]] = {}

    def _selected_infra_component_ids() -> set[str]:
        selected_component_ids: set[str] = set()
        for row in _dynamic_enabled_infra_component_rows(payload):
            if component_instance_id(row) not in selected_infra:
                continue
            component_id = component_type_id(row)
            if component_id:
                selected_component_ids.add(component_id)
        return selected_component_ids

    def _selected_components_for_scope(
        scope: ComponentScope,
    ) -> list[tuple[ComponentEntry, str]]:
        selected_components: list[tuple[ComponentEntry, str]] = []
        if scope == "infra":
            for row in _dynamic_enabled_infra_component_rows(payload):
                instance_id = str(row["instance_id"])
                if instance_id not in selected_infra:
                    continue
                entry = infra_lookup.get(str(row["id"]))
                if entry is not None:
                    selected_components.append((entry, instance_id))
            return selected_components
        for row in _dynamic_enabled_app_chart_rows(payload):
            instance_id = str(row["instance_id"])
            chart_id = str(row["id"])
            exact_selector = component_instance_label(chart_id, instance_id)
            exact_selector_mode = any("@" in token for token in active_selected_apps)
            if (
                exact_selector not in active_selected_apps
                and chart_id not in active_selected_apps
                and (exact_selector_mode or instance_id not in active_selected_apps)
            ):
                continue
            entry = app_lookup.get(chart_id)
            if entry is not None:
                selected_components.append((entry, instance_id))
        return selected_components

    def _app_target_ref_for_instance(component_id: str, instance_id: str) -> str:
        for row in _dynamic_enabled_app_chart_rows(payload):
            if component_type_id(row) != component_id:
                continue
            if component_instance_id(row) != instance_id:
                continue
            return app_chart_target_ref(row)
        return ""

    def _wizard_component_label(entry: ComponentEntry, instance_id: str) -> str:
        if entry.scope == "apps":
            target_ref = _app_target_ref_for_instance(entry.id, instance_id)
            if target_ref:
                return f"{entry.id} on {target_ref}"
        return component_instance_label(entry.id, instance_id)

    def _wizard_component_selection_labels() -> tuple[list[str], list[str]]:
        return (
            [
                _wizard_component_label(entry, instance_id)
                for entry, instance_id in _selected_components_for_scope("infra")
            ],
            [
                _wizard_component_label(entry, instance_id)
                for entry, instance_id in _selected_components_for_scope("apps")
            ],
        )

    def _print_wizard_component_selection_context(
        *,
        current_label: str = "",
        current_scope: ComponentScope | str | None = None,
    ) -> None:
        infra_labels, app_labels = _wizard_component_selection_labels()
        console.print(
            _component_selection_block(
                infra_labels=infra_labels,
                app_labels=app_labels,
                current_label=current_label,
                current_scope=current_scope,
            )
        )

    def _deploy_target_context_for_prompt(
        full_path_label: str,
        *,
        entry: ComponentEntry,
        instance_id: str,
    ) -> tuple[str, str] | None:
        target_match = re.match(r"^deploy\.targets\[([0-9]+)\]\.(.+)$", full_path_label)
        if not target_match:
            return None
        target_ref = ""
        deploy = payload.get("deploy")
        targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
        index = int(target_match.group(1))
        if isinstance(targets, list) and 0 <= index < len(targets):
            target_row = targets[index]
            if isinstance(target_row, Mapping):
                target_ref = normalize_component_token(target_row.get(INSTANCE_ID_FIELD))
        if not target_ref:
            target_ref = component_instance_id({"id": entry.id, INSTANCE_ID_FIELD: instance_id})

        remainder = target_match.group(2)
        if remainder.startswith("secrets.mysterybox."):
            return "deploy target", f"{target_ref} / MysteryBox ESO sync"
        if remainder.startswith("observability."):
            return "deploy target", f"{target_ref} / observability"
        if remainder.startswith("validations.mk8s_gpu."):
            return "deploy target", f"{target_ref} / MK8s GPU validation"
        return "deploy target", target_ref

    def _wizard_prompt_context(
        full_path_label: str,
        *,
        entry: ComponentEntry,
        instance_id: str,
        component_label: str,
    ) -> tuple[ComponentScope | str, str]:
        deploy_context = _deploy_target_context_for_prompt(
            full_path_label,
            entry=entry,
            instance_id=instance_id,
        )
        if deploy_context is not None:
            return deploy_context
        return entry.scope, component_label

    def _materialize_wizard_auto_enabled_gpu_apps() -> None:
        nonlocal payload, active_selected_apps
        if not selected_infra or not app_entries:
            return
        if "mk8s" not in _selected_infra_component_ids():
            return
        gpu_app_selection = resolve_mk8s_gpu_app_selection(
            payload,
            selected_app_ids=active_selected_apps,
            app_entries=app_entries,
        )
        if not gpu_app_selection.auto_enabled_app_ids:
            return
        active_selected_apps = set(gpu_app_selection.selected_app_ids)
        (
            identity_client_name,
            identity_tenant_id,
            identity_project_id,
            identity_region_id,
            identity_email,
        ) = _identity_values_from_payload(payload)
        auto_enabled_seed = _starter_component_payload(
            client_name=identity_client_name,
            tenant_id=identity_tenant_id,
            project_id=identity_project_id,
            region_id=identity_region_id,
            email=identity_email,
            selected_infra=selected_infra,
            selected_apps=active_selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        _ensure_payload_contains_component_rows(
            payload=payload,
            seed_payload=auto_enabled_seed,
        )
        payload = _filter_runtime_payload_for_selected_components(
            payload=payload,
            selected_infra=selected_infra,
            selected_apps=active_selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        console.print(
            f"{warning_markup('Adjusted component selection:')} enabling "
            + ", ".join(f"'apps:{item}'" for item in gpu_app_selection.auto_enabled_app_ids)
            + " because the selected MK8s GPU configuration requires them."
        )
        _print_wizard_component_selection_context()

    def _filter_wizard_selected_app_rows() -> None:
        nonlocal payload
        payload = _filter_runtime_payload_for_selected_components(
            payload=payload,
            selected_infra=selected_infra,
            selected_apps=active_selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )

    def _remove_stale_wizard_observability_apps() -> None:
        nonlocal active_selected_apps
        if not wizard_auto_enabled_observability_apps:
            return
        required_selection = resolve_observability_app_selection(
            payload,
            selected_app_ids=set(),
            app_entries=app_entries,
        )
        required_app_ids = set(required_selection.selected_app_ids)
        stale_app_ids = wizard_auto_enabled_observability_apps - required_app_ids
        if not stale_app_ids:
            return
        active_selected_apps -= stale_app_ids
        wizard_auto_enabled_observability_apps.difference_update(stale_app_ids)
        _filter_wizard_selected_app_rows()

    def _materialize_wizard_auto_enabled_observability_apps() -> None:
        nonlocal payload, active_selected_apps
        if not selected_infra or not app_entries:
            return
        if "mk8s" not in _selected_infra_component_ids():
            return
        _remove_stale_wizard_observability_apps()
        observability_selection = resolve_observability_app_selection(
            payload,
            selected_app_ids=active_selected_apps,
            app_entries=app_entries,
        )
        if observability_selection.issues or not observability_selection.auto_enabled_app_ids:
            return
        active_selected_apps = set(observability_selection.selected_app_ids)
        wizard_auto_enabled_observability_apps.update(observability_selection.auto_enabled_app_ids)
        (
            identity_client_name,
            identity_tenant_id,
            identity_project_id,
            identity_region_id,
            identity_email,
        ) = _identity_values_from_payload(payload)
        auto_enabled_seed = _starter_component_payload(
            client_name=identity_client_name,
            tenant_id=identity_tenant_id,
            project_id=identity_project_id,
            region_id=identity_region_id,
            email=identity_email,
            selected_infra=selected_infra,
            selected_apps=active_selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        _ensure_payload_contains_component_rows(
            payload=payload,
            seed_payload=auto_enabled_seed,
        )
        _filter_wizard_selected_app_rows()
        console.print(
            f"{warning_markup('Adjusted component selection:')} enabling "
            + ", ".join(f"'apps:{item}'" for item in observability_selection.auto_enabled_app_ids)
            + " because observability is enabled for MK8s. The later app field prompt "
            "only controls chart value customization; answering 'n' keeps the selected app defaults."
        )
        _print_wizard_component_selection_context()

    def _run_component(entry: ComponentEntry, instance_id: str) -> str:
        component_label = _wizard_component_label(entry, instance_id)
        _print_wizard_component_selection_context(
            current_label=component_label,
            current_scope=entry.scope,
        )
        decision = _wizard_continue_phase(
            f"Configure '{component_label}' component fields now?",
            default=entry.scope == "infra",
            allow_back=True,
        )
        if _wizard_phase_back_requested(decision):
            return _WizardComponentOutcome.BACK
        if _wizard_phase_stop_requested(decision):
            return _WizardComponentOutcome.QUIT
        if not decision:
            return _WizardComponentOutcome.CONTINUE

        required_leaf_names = _required_leaf_names_for_entry(entry)
        required_leaf_names -= set(shared_default_input_sources(entry))
        _seed_component_prompt_fields(
            payload=payload,
            entry=entry,
            required_leaf_names=required_leaf_names,
            instance_id=instance_id,
        )

        component_path = (
            _dynamic_infra_component_path(payload, entry.id, instance_id=instance_id)
            if entry.scope == "infra"
            else _dynamic_app_chart_path(payload, entry.id, instance_id=instance_id)
        )
        if component_path is not None and entry.defaults:
            component_node = _get_payload_value(payload, component_path)
            resolved_component_node = resolve_component_defaults(
                component_node=component_node if isinstance(component_node, dict) else {},
                entry=entry,
                preserve_existing_literal=True,
                include_shared=False,
            )
            _set_payload_value(payload, component_path, resolved_component_node)
        bound_prompt_paths = (
            shared_default_payload_paths(component_path, entry)
            if component_path is not None
            else set()
        )
        if component_path is not None:
            bound_prompt_paths |= managed_input_binding_payload_paths(component_path, entry)

        declared_prompt_paths: list[PayloadPath] = []
        declared_prompt_defaults: dict[PayloadPath, object] = {}
        declared_prompt_type_hints: dict[PayloadPath, str | None] = {}
        declared_required_prompt_paths: set[PayloadPath] = set()
        for full_path_label in _declared_wizard_field_labels(entry, component_path=component_path):
            resolved_declared = _declared_wizard_prompt_path(
                payload=payload,
                entry=entry,
                component_path=component_path,
                full_path_label=full_path_label,
            )
            if resolved_declared is None:
                console.print(
                    warning_markup(f"Skipping wizard field '{full_path_label}'")
                    + ": path not found in config payload."
                )
                continue
            wizard_spec = _resolve_wizard_field_spec(
                entry=entry,
                full_path_label=full_path_label,
            )
            type_hint = (
                str(wizard_spec.get("type_hint")).strip()
                if isinstance(wizard_spec, dict) and wizard_spec.get("type_hint") is not None
                else None
            )
            default_value = _wizard_field_default_value(
                entry=entry,
                full_path_label=full_path_label,
            )
            if default_value is not _WIZARD_DEFAULT_MISSING and not _payload_path_exists(
                payload, resolved_declared
            ):
                declared_prompt_defaults[resolved_declared] = default_value
            value = (
                _get_payload_value(payload, resolved_declared)
                if _payload_path_exists(payload, resolved_declared)
                else declared_prompt_defaults.get(resolved_declared)
            )
            prompt_complex = bool(
                isinstance(wizard_spec, dict) and wizard_spec.get("prompt_complex") is True
            )
            if isinstance(value, (dict, list)) and not (
                prompt_complex or _is_complex_type_hint(type_hint)
            ):
                continue
            if resolved_declared in bound_prompt_paths:
                continue
            declared_prompt_type_hints[resolved_declared] = type_hint
            if isinstance(wizard_spec, dict) and wizard_spec.get("required") is True:
                declared_required_prompt_paths.add(resolved_declared)
            declared_prompt_paths.append(resolved_declared)

        prompt_paths: list[PayloadPath] = []
        seen_prompt_labels: set[str] = set()
        field_type_hints: dict[str, str | None] = {}
        required_prompt_labels: set[str] = set()
        virtual_prompt_defaults: dict[PayloadPath, object] = {}
        virtual_prompt_defaults.update(declared_prompt_defaults)
        emitted_prompt_guidance: set[str] = set()
        module_dependency_expander: Any = None
        module_prompt_path_prefix: PayloadPath | None = None
        module_field_is_enabled: Callable[[str], bool] | None = None
        if component_path is not None:
            if entry.scope == "infra":
                # Infra wizard prompts are module-input driven for source-backed modules.
                module_inputs_path = component_path + ("inputs",)
                module_inputs = (
                    _get_payload_value(payload, module_inputs_path)
                    if module_inputs_path is not None
                    else None
                )
                if module_inputs_path is not None and isinstance(module_inputs, dict):
                    module_specs_by_leaf = _module_variable_specs_for_entry(entry)
                    default_project_scope_id = _non_empty_text(
                        _read_payload_field(payload, "client_info.nebius.project_id")
                    )
                    dependent_prefixes = tuple(
                        sorted(
                            {
                                leaf_name[: -len("_enabled")]
                                for leaf_name, spec in module_specs_by_leaf.items()
                                if _short_type_hint(spec.type_hint) == "bool"
                                and leaf_name.endswith("_enabled")
                                and leaf_name[: -len("_enabled")]
                            },
                            key=len,
                            reverse=True,
                        )
                    )

                    def _seed_input_value(
                        leaf_name: str,
                        *,
                        required_only: bool,
                        module_inputs: dict[str, Any] = module_inputs,
                        default_project_scope_id: str = default_project_scope_id,
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                    ) -> None:
                        current_value = _resolve_mapping_segment(module_inputs, leaf_name)
                        if current_value is not None:
                            return
                        if leaf_name in {"parent_id", "project_id"} and default_project_scope_id:
                            module_inputs[leaf_name] = default_project_scope_id
                        if required_only:
                            return

                    def _enabled_prefixes(
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                        module_inputs: dict[str, Any] = module_inputs,
                    ) -> set[str]:
                        resolved: set[str] = set()
                        for leaf_name, spec in module_specs_by_leaf.items():
                            if _short_type_hint(spec.type_hint) != "bool":
                                continue
                            if not leaf_name.endswith("_enabled"):
                                continue
                            prefix = leaf_name[: -len("_enabled")]
                            if not prefix:
                                continue
                            current_value = _resolve_mapping_segment(module_inputs, leaf_name)
                            if isinstance(current_value, bool) and current_value:
                                resolved.add(prefix)
                        return resolved

                    def _dependent_prefix(
                        leaf_name: str,
                        *,
                        dependent_prefixes: tuple[str, ...] = dependent_prefixes,
                    ) -> str | None:
                        normalized_leaf = _normalize_leaf_name(leaf_name)
                        for prefix in dependent_prefixes:
                            if normalized_leaf == f"{prefix}_enabled":
                                return None
                            if normalized_leaf.startswith(f"{prefix}_"):
                                return prefix
                        return None

                    def _field_is_enabled(
                        leaf_name: str,
                        *,
                        module_inputs: dict[str, Any] = module_inputs,
                    ) -> bool:
                        dependency_prefix = _dependent_prefix(leaf_name)
                        if dependency_prefix is None:
                            return True
                        return dependency_prefix in _enabled_prefixes(module_inputs=module_inputs)

                    module_prompt_path_prefix = module_inputs_path
                    module_field_is_enabled = _field_is_enabled

                    current_entry = entry
                    current_payload = payload

                    def _active_required_leaf_names(
                        payload: dict[str, Any] = current_payload,
                        component_path: PayloadPath = component_path,
                        current_entry: ComponentEntry = current_entry,
                        required_leaf_names: set[str] = required_leaf_names,
                    ) -> set[str]:
                        component_node = _get_payload_value(payload, component_path)
                        if not isinstance(component_node, Mapping):
                            return set(required_leaf_names)
                        return set(required_leaf_names) | _conditionally_required_input_leaf_names(
                            entry=current_entry,
                            component_node=component_node,
                        )

                    for leaf_name in sorted(_active_required_leaf_names()):
                        _seed_input_value(leaf_name, required_only=True)

                    def _append_field_prompt(
                        leaf_name: str,
                        spec: Any | None,
                        *,
                        required: bool,
                        payload: dict[str, Any] = current_payload,
                        current_entry: ComponentEntry = current_entry,
                        bound_prompt_paths: set[PayloadPath] = bound_prompt_paths,
                        module_inputs: dict[str, Any] = module_inputs,
                        module_inputs_path: PayloadPath = module_inputs_path,
                        seen_prompt_labels: set[str] = seen_prompt_labels,
                        prompt_paths: list[PayloadPath] = prompt_paths,
                        field_type_hints: dict[str, str | None] = field_type_hints,
                        required_prompt_labels: set[str] = required_prompt_labels,
                    ) -> None:
                        key = leaf_name
                        if key not in module_inputs:
                            alt_key = leaf_name.replace("_", "-")
                            if alt_key in module_inputs:
                                key = alt_key
                            else:
                                key = leaf_name.replace("-", "_")
                        full_path = module_inputs_path + (key,)
                        if full_path in bound_prompt_paths:
                            return
                        if full_path in prompt_paths:
                            return
                        label = _format_payload_path(full_path)
                        if not required and not _wizard_field_prompt_enabled(
                            entry=current_entry,
                            full_path_label=label,
                        ):
                            return
                        if not _provider_prompt_dependencies_ready(
                            payload=payload,
                            entry=current_entry,
                            full_path_label=label,
                        ):
                            return
                        seen_prompt_labels.add(label)
                        prompt_paths.append(full_path)
                        field_type_hints[label] = None if spec is None else spec.type_hint
                        if required:
                            required_prompt_labels.add(label)

                    def _queue_module_field_prompt(
                        leaf_name: str,
                        spec: Any | None,
                        *,
                        required: bool,
                        module_inputs: dict[str, Any] = module_inputs,
                        module_inputs_path: PayloadPath = module_inputs_path,
                        virtual_prompt_defaults: dict[
                            PayloadPath, object
                        ] = virtual_prompt_defaults,
                    ) -> None:
                        full_path = module_inputs_path + (leaf_name,)
                        current_value = _resolve_mapping_segment(module_inputs, leaf_name)
                        if (
                            current_value is None
                            and spec is not None
                            and spec.has_default
                            and full_path not in virtual_prompt_defaults
                        ):
                            virtual_prompt_defaults[full_path] = copy.deepcopy(spec.default)
                        if not _field_is_enabled(leaf_name, module_inputs=module_inputs):
                            return
                        active_required_leaf_names = _active_required_leaf_names()
                        is_required = required or leaf_name in active_required_leaf_names
                        _seed_input_value(
                            leaf_name,
                            required_only=spec.required if spec is not None else is_required,
                        )
                        _append_field_prompt(leaf_name, spec, required=is_required)

                    for leaf_name, spec in sorted(
                        module_specs_by_leaf.items(),
                        key=lambda item: (0 if item[1].required else 1, item[0]),
                    ):
                        _queue_module_field_prompt(
                            leaf_name,
                            spec,
                            required=spec.required,
                        )

                    for leaf_name in sorted(_active_required_leaf_names()):
                        if leaf_name in module_specs_by_leaf:
                            continue
                        _queue_module_field_prompt(leaf_name, None, required=True)

                    current_declared_prompt_paths = tuple(declared_prompt_paths)

                    def _append_declared_module_prompt_paths(
                        payload: dict[str, Any] = current_payload,
                        current_entry: ComponentEntry = current_entry,
                        module_inputs_path: PayloadPath = module_inputs_path,
                        module_inputs: dict[str, Any] = module_inputs,
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                        declared_prompt_paths: tuple[
                            PayloadPath, ...
                        ] = current_declared_prompt_paths,
                        bound_prompt_paths: set[PayloadPath] = bound_prompt_paths,
                        prompt_paths: list[PayloadPath] = prompt_paths,
                        seen_prompt_labels: set[str] = seen_prompt_labels,
                        field_type_hints: dict[str, str | None] = field_type_hints,
                        required_prompt_labels: set[str] = required_prompt_labels,
                    ) -> None:
                        for full_path in declared_prompt_paths:
                            if full_path in bound_prompt_paths or full_path in prompt_paths:
                                continue
                            label = _format_payload_path(full_path)
                            if label in seen_prompt_labels:
                                continue
                            if (
                                label not in required_prompt_labels
                                and not _wizard_field_prompt_enabled(
                                    entry=current_entry,
                                    full_path_label=label,
                                )
                            ):
                                continue
                            if not _provider_prompt_dependencies_ready(
                                payload=payload,
                                entry=current_entry,
                                full_path_label=label,
                            ):
                                continue
                            if (
                                len(full_path) == len(module_inputs_path) + 1
                                and full_path[: len(module_inputs_path)] == module_inputs_path
                            ):
                                leaf_name = _normalize_leaf_name(str(full_path[-1]))
                                if not _field_is_enabled(leaf_name, module_inputs=module_inputs):
                                    continue
                                spec = module_specs_by_leaf.get(leaf_name)
                                if spec is not None:
                                    field_type_hints[label] = spec.type_hint
                                    if spec.required or leaf_name in _active_required_leaf_names():
                                        required_prompt_labels.add(label)
                            elif full_path in declared_prompt_type_hints:
                                field_type_hints[label] = declared_prompt_type_hints[full_path]
                            if full_path in declared_required_prompt_paths:
                                required_prompt_labels.add(label)
                            seen_prompt_labels.add(label)
                            prompt_paths.append(full_path)

                    _append_declared_module_prompt_paths()

                    def _expand_module_dependency_prompts(
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                    ) -> None:
                        for leaf_name, spec in sorted(
                            module_specs_by_leaf.items(),
                            key=lambda item: (0 if item[1].required else 1, item[0]),
                        ):
                            _queue_module_field_prompt(
                                leaf_name,
                                spec,
                                required=spec.required,
                            )
                        for leaf_name in sorted(_active_required_leaf_names()):
                            if leaf_name in module_specs_by_leaf:
                                continue
                            _queue_module_field_prompt(leaf_name, None, required=True)
                        _append_declared_module_prompt_paths()

                    module_dependency_expander = _expand_module_dependency_prompts
            elif entry.scope == "apps":
                # App wizard prompts are Helm values-driven.
                for key in ("namespace", "release-name"):
                    full_path = component_path + (key,)
                    if full_path in bound_prompt_paths:
                        continue
                    label = _format_payload_path(full_path)
                    if label in seen_prompt_labels:
                        continue
                    current_value = (
                        _get_payload_value(payload, full_path)
                        if _payload_path_exists(payload, full_path)
                        else None
                    )
                    if isinstance(current_value, (dict, list)):
                        continue
                    seen_prompt_labels.add(label)
                    prompt_paths.append(full_path)
                values_path = component_path + ("values",)
                values_node = (
                    _get_payload_value(payload, values_path) if values_path is not None else None
                )
                if values_path is not None and isinstance(values_node, dict) and values_node:
                    for relative_path in _collect_promptable_leaf_paths(values_node):
                        full_path = values_path + relative_path
                        if full_path in bound_prompt_paths:
                            continue
                        label = _format_payload_path(full_path)
                        if label in seen_prompt_labels:
                            continue
                        seen_prompt_labels.add(label)
                        prompt_paths.append(full_path)
                chart_default_values = _app_chart_default_values(
                    payload=payload,
                    entry=entry,
                    instance_id=instance_id,
                )
                if isinstance(chart_default_values, dict) and chart_default_values:
                    for relative_path in _collect_promptable_leaf_paths(chart_default_values):
                        full_path = values_path + relative_path
                        if full_path in bound_prompt_paths:
                            continue
                        label = _format_payload_path(full_path)
                        if full_path not in virtual_prompt_defaults:
                            virtual_prompt_defaults[full_path] = copy.deepcopy(
                                _get_payload_value(chart_default_values, relative_path)
                            )
                        if label in seen_prompt_labels:
                            continue
                        seen_prompt_labels.add(label)
                        prompt_paths.append(full_path)
                for full_path in declared_prompt_paths:
                    if full_path in bound_prompt_paths:
                        continue
                    label = _format_payload_path(full_path)
                    if label in seen_prompt_labels:
                        continue
                    if not _wizard_field_prompt_enabled(
                        entry=entry,
                        full_path_label=label,
                    ):
                        continue
                    if not _provider_prompt_dependencies_ready(
                        payload=payload,
                        entry=entry,
                        full_path_label=label,
                    ):
                        continue
                    if full_path in declared_prompt_type_hints:
                        field_type_hints[label] = declared_prompt_type_hints[full_path]
                    if full_path in declared_required_prompt_paths:
                        required_prompt_labels.add(label)
                    seen_prompt_labels.add(label)
                    prompt_paths.append(full_path)
            else:
                component_node = _get_payload_value(payload, component_path)
                for relative_path in _collect_scalar_leaf_paths(component_node):
                    full_path = component_path + relative_path
                    if full_path in bound_prompt_paths:
                        continue
                    label = _format_payload_path(full_path)
                    if label in seen_prompt_labels:
                        continue
                    seen_prompt_labels.add(label)
                    prompt_paths.append(full_path)
                for full_path in declared_prompt_paths:
                    if full_path in bound_prompt_paths:
                        continue
                    label = _format_payload_path(full_path)
                    if label in seen_prompt_labels:
                        continue
                    if not _wizard_field_prompt_enabled(
                        entry=entry,
                        full_path_label=label,
                    ):
                        continue
                    if not _provider_prompt_dependencies_ready(
                        payload=payload,
                        entry=entry,
                        full_path_label=label,
                    ):
                        continue
                    if full_path in declared_prompt_type_hints:
                        field_type_hints[label] = declared_prompt_type_hints[full_path]
                    if full_path in declared_required_prompt_paths:
                        required_prompt_labels.add(label)
                    seen_prompt_labels.add(label)
                    prompt_paths.append(full_path)

        prompt_paths.sort(
            key=lambda path: _prompt_path_sort_key(
                path,
                required_leaf_names=required_leaf_names,
                required_prompt_labels=required_prompt_labels,
            ),
        )

        prompt_index = 0
        prompt_history: list[PayloadPath] = []
        while True:
            while prompt_index < len(prompt_paths):
                full_path = prompt_paths[prompt_index]
                prompt_index += 1
                full_path_label = _format_payload_path(full_path)
                if (
                    module_prompt_path_prefix is not None
                    and module_field_is_enabled is not None
                    and len(full_path) == len(module_prompt_path_prefix) + 1
                    and full_path[: len(module_prompt_path_prefix)] == module_prompt_path_prefix
                    and isinstance(full_path[-1], str)
                    and not module_field_is_enabled(_normalize_leaf_name(str(full_path[-1])))
                ):
                    continue
                if _skip_mk8s_gpu_validation_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_observability_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_mysterybox_eso_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_vm_service_account_prompt(
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_vm_preemptible_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_compute_boot_disk_security_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_compute_data_disk_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _skip_jump_host_public_ip_allocation_prompt(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if not _provider_prompt_dependencies_ready(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                if _payload_path_exists(payload, full_path):
                    current = _get_payload_value(payload, full_path)
                else:
                    current = copy.deepcopy(virtual_prompt_defaults.get(full_path))
                    if current is None:
                        provider_default = _wizard_field_provider_default_value(
                            payload=payload,
                            entry=entry,
                            full_path_label=full_path_label,
                            provider_lookup=provider_lookup,
                            type_hint=field_type_hints.get(full_path_label),
                        )
                        if provider_default is not _WIZARD_DEFAULT_MISSING:
                            current = provider_default
                            virtual_prompt_defaults.setdefault(
                                full_path,
                                copy.deepcopy(provider_default),
                            )
                previous_component_inputs: dict[str, Any] | None = None
                if (
                    entry.scope == "infra"
                    and (entry.id == "mk8s" or _entry_declares_compute_boot_disk_contract(entry))
                    and component_path is not None
                ):
                    current_component = _get_payload_value(payload, component_path)
                    if isinstance(current_component, dict):
                        current_inputs = current_component.get("inputs")
                        if isinstance(current_inputs, dict):
                            previous_component_inputs = copy.deepcopy(current_inputs)
                path_existed_before_prompt = _payload_path_exists(payload, full_path)
                field_choices = _resolve_dynamic_field_choices(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    provider_lookup=provider_lookup,
                )
                if (
                    _provider_auto_select_single_enabled(
                        entry=entry,
                        full_path_label=full_path_label,
                    )
                    and len(field_choices) == 1
                    and not _has_required_prompt_value(
                        current,
                        type_hint=field_type_hints.get(full_path_label),
                    )
                ) or (
                    _provider_auto_select_first_enabled(
                        entry=entry,
                        full_path_label=full_path_label,
                    )
                    and field_choices
                    and not _has_required_prompt_value(
                        current,
                        type_hint=field_type_hints.get(full_path_label),
                    )
                ):
                    current = field_choices[0].value
                allowed_provider_values, providers = provider_allowed_cache.get(
                    full_path_label,
                    (set(), ()),
                )
                if not providers:
                    allowed_provider_values, providers = _provider_allowed_values_for_field(
                        payload=payload,
                        entry=entry,
                        full_path_label=full_path_label,
                        provider_lookup=provider_lookup,
                    )
                    provider_allowed_cache[full_path_label] = (allowed_provider_values, providers)
                prompt_required = (
                    full_path_label in required_prompt_labels
                    or _dynamic_required_prompt(
                        payload=payload,
                        entry=entry,
                        full_path_label=full_path_label,
                    )
                )
                if (
                    not field_choices
                    and providers
                    and provider_lookup is not None
                    and _provider_skip_prompt_if_no_choices_enabled(
                        entry=entry,
                        full_path_label=full_path_label,
                    )
                    and not provider_lookup.last_error()
                    and not prompt_required
                    and not _has_required_prompt_value(
                        current,
                        type_hint=field_type_hints.get(full_path_label),
                    )
                ):
                    continue
                if (
                    not field_choices
                    and providers
                    and full_path_label not in warned_provider_fallbacks
                ):
                    provider_names = ", ".join(providers)
                    warning = _provider_fallback_warning(
                        field_path_label=full_path_label,
                        provider_names=provider_names,
                        required=prompt_required,
                        provider_lookup=provider_lookup,
                    )
                    console.print(warning)
                    warned_provider_fallbacks.add(full_path_label)
                _maybe_print_compute_boot_disk_prompt_guidance(
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                _maybe_print_gpu_preset_prompt_guidance(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                _maybe_print_mk8s_gpu_validation_prompt_guidance(
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                _maybe_print_observability_prompt_guidance(
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                _maybe_print_ssh_jumphost_allowed_cidrs_guidance(
                    entry=entry,
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                _maybe_print_mysterybox_secrets_prompt_guidance(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    emitted_guidance=emitted_prompt_guidance,
                )
                context_scope, context_label = _wizard_prompt_context(
                    full_path_label,
                    entry=entry,
                    instance_id=instance_id,
                    component_label=component_label,
                )
                _print_wizard_component_selection_context(
                    current_label=context_label,
                    current_scope=context_scope,
                )
                updated, should_stop = _prompt_scalar_override(
                    full_path_label,
                    current,
                    choices=field_choices,
                    type_hint=field_type_hints.get(full_path_label),
                    required=prompt_required,
                )
                if should_stop:
                    return _WizardComponentOutcome.QUIT
                if _wizard_backtrack_requested(updated):
                    prompt_index = _wizard_backtrack_target_index(
                        prompt_paths=prompt_paths,
                        prompt_history=prompt_history,
                        current_path=full_path,
                    )
                    if prompt_index is None:
                        return _WizardComponentOutcome.BACK
                    continue
                backtracked = False
                while allowed_provider_values:
                    if not prompt_required and not _has_required_prompt_value(
                        updated,
                        type_hint=field_type_hints.get(full_path_label),
                    ):
                        break
                    updated_value = str(updated).strip()
                    if updated_value in allowed_provider_values:
                        break
                    console.print(
                        f"{error_markup('Invalid value')} for "
                        f"'{full_path_label}'. Value must exist in live provider options."
                    )
                    context_scope, context_label = _wizard_prompt_context(
                        full_path_label,
                        entry=entry,
                        instance_id=instance_id,
                        component_label=component_label,
                    )
                    _print_wizard_component_selection_context(
                        current_label=context_label,
                        current_scope=context_scope,
                    )
                    updated, should_stop = _prompt_scalar_override(
                        full_path_label,
                        updated,
                        choices=field_choices,
                        type_hint=field_type_hints.get(full_path_label),
                        required=prompt_required,
                    )
                    if should_stop:
                        return _WizardComponentOutcome.QUIT
                    if _wizard_backtrack_requested(updated):
                        prompt_index = _wizard_backtrack_target_index(
                            prompt_paths=prompt_paths,
                            prompt_history=prompt_history,
                            current_path=full_path,
                        )
                        if prompt_index is None:
                            return _WizardComponentOutcome.BACK
                        backtracked = True
                        break
                if backtracked:
                    continue
                if not prompt_history or prompt_history[-1] != full_path:
                    prompt_history.append(full_path)
                if full_path in virtual_prompt_defaults:
                    default_value = virtual_prompt_defaults[full_path]
                    if updated == default_value and not _wizard_field_materialize_default(
                        entry=entry,
                        full_path_label=full_path_label,
                    ):
                        if _payload_path_exists(payload, full_path):
                            _delete_payload_value(payload, full_path)
                    else:
                        _set_payload_value_creating_containers(payload, full_path, updated)
                elif not path_existed_before_prompt and updated is None:
                    continue
                elif not path_existed_before_prompt:
                    _set_payload_value_creating_containers(payload, full_path, updated)
                else:
                    _set_payload_value(payload, full_path, updated)
                _print_wizard_selected_field(full_path_label, updated)
                _maybe_refresh_compute_boot_disk_defaults_after_shape_change(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    previous_component_inputs=previous_component_inputs,
                    provider_lookup=provider_lookup,
                )
                _maybe_refresh_compute_data_disk_size_after_type_change(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                )
                _maybe_clear_gpu_cluster_fabric_after_shape_change(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    provider_lookup=provider_lookup,
                )
                _maybe_materialize_vm_preemptible_recovery_policy(
                    payload=payload,
                    entry=entry,
                    component_path=component_path,
                )
                _maybe_print_selected_gpu_preset_guidance(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    provider_lookup=provider_lookup,
                    emitted_guidance=emitted_prompt_guidance,
                )
                if _is_observability_field(full_path_label):
                    _materialize_wizard_auto_enabled_observability_apps()
                if module_dependency_expander is not None:
                    before_expand = len(prompt_paths)
                    module_dependency_expander()
                    if len(prompt_paths) > before_expand:
                        prompt_paths[prompt_index:] = sorted(
                            prompt_paths[prompt_index:],
                            key=lambda path: _prompt_path_sort_key(
                                path,
                                required_leaf_names=required_leaf_names,
                                required_prompt_labels=required_prompt_labels,
                            ),
                        )
            if module_dependency_expander is None:
                break
            before_expand = len(prompt_paths)
            module_dependency_expander()
            if len(prompt_paths) == before_expand:
                break
            prompt_paths[prompt_index:] = sorted(
                prompt_paths[prompt_index:],
                key=lambda path: _prompt_path_sort_key(
                    path,
                    required_leaf_names=required_leaf_names,
                    required_prompt_labels=required_prompt_labels,
                ),
            )
        return _WizardComponentOutcome.CONTINUE

    def _confirm_exit_from_first_step() -> bool:
        console.print(
            warning_markup("Already at the first wizard step.")
            + " There is no earlier component field to revisit."
        )
        while True:
            raw = (
                typer.prompt(
                    f"Exit wizard and save the current config? (y/n, {WIZARD_ABORT_TOKEN}=quit wizard)",
                    default="n",
                    show_default=True,
                )
                .strip()
                .lower()
            )
            if raw == WIZARD_ABORT_TOKEN:
                return True
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no", WIZARD_EXIT_TOKEN}:
                return False
            console.print(
                f"{error_markup('Invalid selection')}. Enter y, n, or {WIZARD_ABORT_TOKEN}."
            )

    infra_components = _selected_components_for_scope("infra")
    app_components = [] if infra_components else _selected_components_for_scope("apps")
    section: ComponentScope = "infra" if infra_components else "apps"
    infra_index = 0
    app_index = 0
    active_section: ComponentScope | None = None

    while True:
        if section == "infra":
            if active_section != "infra":
                _print_wizard_section_banner(title="Infra", components=infra_components)
                active_section = "infra"
            if not infra_components:
                section = "apps"
                active_section = None
                continue
            entry, instance_id = infra_components[infra_index]
            outcome = _run_component(entry, instance_id)
            if outcome == _WizardComponentOutcome.QUIT:
                return yaml.safe_dump(payload, sort_keys=False), False
            if outcome == _WizardComponentOutcome.BACK:
                if infra_index > 0:
                    infra_index -= 1
                    continue
                if _confirm_exit_from_first_step():
                    return yaml.safe_dump(payload, sort_keys=False), False
                continue
            infra_index += 1
            if infra_index < len(infra_components):
                continue

            _materialize_wizard_auto_enabled_gpu_apps()
            _materialize_wizard_auto_enabled_observability_apps()
            app_components = _selected_components_for_scope("apps")
            section = "apps"
            app_index = 0
            active_section = None
            continue

        if active_section != "apps":
            _print_wizard_section_banner(title="Apps", components=app_components)
            active_section = "apps"
        if not app_components:
            return yaml.safe_dump(payload, sort_keys=False), True
        entry, instance_id = app_components[app_index]
        outcome = _run_component(entry, instance_id)
        if outcome == _WizardComponentOutcome.QUIT:
            return yaml.safe_dump(payload, sort_keys=False), False
        if outcome == _WizardComponentOutcome.BACK:
            if app_index > 0:
                app_index -= 1
                continue
            if infra_components:
                section = "infra"
                infra_index = len(infra_components) - 1
                active_section = None
                continue
            if _confirm_exit_from_first_step():
                return yaml.safe_dump(payload, sort_keys=False), False
            continue
        app_index += 1
        if app_index >= len(app_components):
            return yaml.safe_dump(payload, sort_keys=False), True

    return yaml.safe_dump(payload, sort_keys=False), True


@dataclass(frozen=True)
class _AppChartDependencyAdjustment:
    source_app_id: str
    dependency_app_id: str
    dependency_chart_name: str
    dependency_kind: str = "chart"


_ChartRef = tuple[str, str, str]  # (chart_name_or_ref, chart_repo, version)
_ChartMetaCache = dict[_ChartRef, tuple[str | None, str | None, set[str], str | None]]


def _app_component_chart_ref_from_payload(
    payload: dict[str, Any],
    entry: ComponentEntry,
) -> _ChartRef | None:
    component_path = _dynamic_component_path(payload, entry)
    if component_path is None:
        return None
    chart_node = _get_payload_value(payload, component_path)
    if not isinstance(chart_node, Mapping):
        return None
    repo = str(chart_node.get("repo", "")).strip()
    name = _runtime_app_chart_name(chart_node=chart_node, entry=entry)
    if not name:
        return None
    version = str(chart_node.get("version", "")).strip()
    return name, repo, version


def _app_component_chart_name_from_payload(
    payload: dict[str, Any], entry: ComponentEntry
) -> str | None:
    component_path = _dynamic_component_path(payload, entry)
    if component_path is None:
        return None
    chart_node = _get_payload_value(payload, component_path)
    if not isinstance(chart_node, Mapping):
        return None
    name = _runtime_app_chart_name(chart_node=chart_node, entry=entry)
    if not name:
        return None
    return name.lower()


def _runtime_app_chart_name(
    *,
    chart_node: Mapping[str, Any],
    entry: ComponentEntry | None,
) -> str | None:
    configured_name = component_entry_chart_name(entry)
    repo = str(chart_node.get("repo", "")).strip().rstrip("/")
    if not repo and entry is not None:
        local_chart_source = str(entry.source or "").strip()
        if _resolve_local_chart_source_path(local_chart_source) is not None:
            return local_chart_source
    if repo.startswith("oci://") and "/" in repo:
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip()
        if configured_name and repo_tail.lower() == configured_name.lower():
            return repo_tail
        if repo_tail and not configured_name:
            return repo_tail
    if configured_name:
        return configured_name
    fallback_name = str(chart_node.get("id", "")).strip()
    if not fallback_name and entry is not None:
        fallback_name = entry.id
    return fallback_name or None


def _runtime_app_chart_name_for_id(
    *,
    chart_node: Mapping[str, Any],
    chart_id: str,
    entry: ComponentEntry | None,
) -> str:
    return _runtime_app_chart_name(chart_node=chart_node, entry=entry) or chart_id


def _source_chart_name(entry: ComponentEntry) -> str | None:
    name = component_entry_chart_name(entry)
    return name.lower() if name else None


def _chart_source_display(*, chart_name_or_ref: str, chart_repo: str) -> str:
    chart_ref = chart_name_or_ref.strip().rstrip("/")
    repo = chart_repo.strip().rstrip("/")
    if chart_ref.startswith("oci://"):
        return chart_ref
    if repo.startswith("oci://"):
        if not chart_ref:
            return repo
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail == chart_ref.lower():
            return repo
        return f"{repo}/{chart_ref}"
    if repo:
        if chart_ref:
            return f"{repo}/{chart_ref}"
        return repo
    return chart_ref


def _helm_chart_metadata(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
    cache: _ChartMetaCache,
) -> tuple[str | None, str | None, set[str], str | None]:
    cache_key: _ChartRef = (chart_name_or_ref, chart_repo, chart_version)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = HelmClient()
        chart_payload = client.show_chart(
            reference=HelmChartReference(
                chart_name=chart_name_or_ref,
                chart_repo=chart_repo,
                chart_version=chart_version,
            )
        )
    except Exception as exc:
        value = (None, None, set(), str(exc))
        cache[cache_key] = value
        return value

    if not isinstance(chart_payload, Mapping):
        value = (None, None, set(), "chart metadata is not a mapping")
        cache[cache_key] = value
        return value

    chart_name = str(chart_payload.get("name", "")).strip().lower() or None
    resolved_version = str(chart_payload.get("version", "")).strip() or None
    dependency_names: set[str] = set()
    raw_dependencies = chart_payload.get("dependencies", [])
    if isinstance(raw_dependencies, list):
        for item in raw_dependencies:
            if not isinstance(item, Mapping):
                continue
            dependency_name = str(item.get("name", "")).strip().lower()
            if dependency_name:
                dependency_names.add(dependency_name)
            dependency_alias = str(item.get("alias", "")).strip().lower()
            if dependency_alias:
                dependency_names.add(dependency_alias)

    value = (chart_name, resolved_version, dependency_names, None)
    cache[cache_key] = value
    return value


def _normalized_chart_metadata(
    value: tuple[Any, ...],
) -> tuple[str | None, str | None, set[str], str | None]:
    if len(value) == 4:
        chart_name, resolved_version, dependency_names, error = value
    elif len(value) == 3:
        chart_name, dependency_names, error = value
        resolved_version = None
    else:
        raise ValueError("chart metadata result must contain 3 or 4 fields")

    normalized_name = str(chart_name).strip().lower() or None if chart_name is not None else None
    normalized_version = (
        str(resolved_version).strip() or None if resolved_version is not None else None
    )
    normalized_dependencies = {
        str(item).strip().lower() for item in (dependency_names or set()) if str(item).strip()
    }
    normalized_error = str(error).strip() or None if error is not None else None
    return normalized_name, normalized_version, normalized_dependencies, normalized_error


def _helm_chart_dependency_names(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
    cache: _ChartMetaCache,
) -> tuple[set[str], str | None]:
    _chart_name, _resolved_version, dependency_names, error = _normalized_chart_metadata(
        _helm_chart_metadata(
            chart_name_or_ref=chart_name_or_ref,
            chart_repo=chart_repo,
            chart_version=chart_version,
            cache=cache,
        )
    )
    return dependency_names, error


def _app_component_match_names(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    include_live_chart_name: bool = False,
    cache: _ChartMetaCache | None = None,
) -> set[str]:
    names: set[str] = {entry.id.strip().lower()}
    names.update(token.strip().lower() for token in entry.dependency_match_names if token.strip())

    payload_name = _app_component_chart_name_from_payload(payload, entry)
    if payload_name:
        names.add(payload_name)

    source_name = _source_chart_name(entry)
    if source_name:
        names.add(source_name)

    chart_ref = _app_component_chart_ref_from_payload(payload, entry)
    if include_live_chart_name and cache is not None and chart_ref is not None:
        chart_name, _resolved_version, _deps, _error = _normalized_chart_metadata(
            _helm_chart_metadata(
                chart_name_or_ref=chart_ref[0],
                chart_repo=chart_ref[1],
                chart_version=chart_ref[2],
                cache=cache,
            )
        )
        if chart_name:
            names.add(chart_name)
    return names


def _resolve_dependency_component_id(
    *,
    dependency_name: str,
    matched_ids: set[str],
    entry_by_id: dict[str, ComponentEntry],
    payload: dict[str, Any],
) -> str | None:
    if len(matched_ids) == 1:
        return next(iter(matched_ids))

    if dependency_name in matched_ids:
        return dependency_name

    exact_chart_name = {
        entry_id
        for entry_id in matched_ids
        if _app_component_chart_name_from_payload(payload, entry_by_id[entry_id]) == dependency_name
    }
    if len(exact_chart_name) == 1:
        return next(iter(exact_chart_name))
    return None


def _resolve_apps_chart_dependencies(
    *,
    payload: dict[str, Any],
    selected_apps: set[str],
    app_entries: tuple[ComponentEntry, ...],
    cache: _ChartMetaCache,
    collect_warnings: bool,
) -> tuple[set[str], tuple[_AppChartDependencyAdjustment, ...], tuple[str, ...]]:
    selected = set(selected_apps)
    entry_by_id = {entry.id: entry for entry in app_entries}

    chart_name_index: dict[str, set[str]] = {}
    for entry in app_entries:
        for match_name in _app_component_match_names(payload=payload, entry=entry):
            chart_name_index.setdefault(match_name, set()).add(entry.id)
    live_index_enriched = False

    def _enrich_chart_name_index_from_live_metadata() -> None:
        nonlocal live_index_enriched
        if live_index_enriched:
            return
        for app_entry in app_entries:
            for match_name in _app_component_match_names(
                payload=payload,
                entry=app_entry,
                include_live_chart_name=True,
                cache=cache,
            ):
                chart_name_index.setdefault(match_name, set()).add(app_entry.id)
        live_index_enriched = True

    queue: deque[str] = deque(sorted(selected))
    enqueued: set[str] = set(queue)
    adjustments: list[_AppChartDependencyAdjustment] = []
    warnings: list[str] = []

    while queue:
        source_app_id = queue.popleft()
        enqueued.discard(source_app_id)
        source_entry = entry_by_id.get(source_app_id)
        if source_entry is None:
            continue

        for dependency_id in source_entry.default_release_install_after:
            dependency_id = str(dependency_id).strip().lower()
            if not dependency_id:
                continue
            if dependency_id not in entry_by_id:
                if collect_warnings:
                    warnings.append(
                        "release install_after lookup for "
                        f"apps:{source_app_id} references unknown apps component "
                        f"'{dependency_id}'"
                    )
                continue
            if dependency_id in selected:
                continue
            selected.add(dependency_id)
            adjustments.append(
                _AppChartDependencyAdjustment(
                    source_app_id=source_app_id,
                    dependency_app_id=dependency_id,
                    dependency_chart_name=dependency_id,
                    dependency_kind="install_after",
                )
            )
            if dependency_id not in enqueued:
                queue.append(dependency_id)
                enqueued.add(dependency_id)

        chart_ref = _app_component_chart_ref_from_payload(payload, source_entry)
        if chart_ref is None:
            continue

        dependency_names, error = _helm_chart_dependency_names(
            chart_name_or_ref=chart_ref[0],
            chart_repo=chart_ref[1],
            chart_version=chart_ref[2],
            cache=cache,
        )
        if error:
            if collect_warnings:
                source_display = _chart_source_display(
                    chart_name_or_ref=chart_ref[0],
                    chart_repo=chart_ref[1],
                )
                warnings.append(
                    "chart dependency lookup skipped for "
                    f"apps:{source_app_id} ({source_display}): {error}"
                )
            continue

        for dependency_name in sorted(dependency_names):
            matched_ids = chart_name_index.get(dependency_name, set())
            if not matched_ids and not live_index_enriched:
                _enrich_chart_name_index_from_live_metadata()
                matched_ids = chart_name_index.get(dependency_name, set())
            if not matched_ids:
                continue
            dependency_id = _resolve_dependency_component_id(
                dependency_name=dependency_name,
                matched_ids=matched_ids,
                entry_by_id=entry_by_id,
                payload=payload,
            )
            if dependency_id is None:
                if collect_warnings:
                    warnings.append(
                        "chart dependency lookup for "
                        f"apps:{source_app_id} matched multiple components for "
                        f"'{dependency_name}': {', '.join(sorted(matched_ids))}"
                    )
                continue
            if dependency_id in selected:
                continue
            selected.add(dependency_id)
            adjustments.append(
                _AppChartDependencyAdjustment(
                    source_app_id=source_app_id,
                    dependency_app_id=dependency_id,
                    dependency_chart_name=dependency_name,
                )
            )
            if dependency_id not in enqueued:
                queue.append(dependency_id)
                enqueued.add(dependency_id)

    return selected, tuple(adjustments), tuple(warnings)


def _normalize_component_dependencies(
    *,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    payload_for_app_chart_deps: dict[str, Any] | None = None,
) -> tuple[set[str], set[str]]:
    try:
        resolved = resolve_component_dependencies(
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    for adjustment in resolved.adjustments:
        console.print(
            f"{warning_markup('Adjusted component selection:')} "
            f"enabling '{adjustment.dependency_scope}:{adjustment.dependency_id}' "
            f"because '{adjustment.source_scope}:{adjustment.source_id}' depends on it."
        )

    normalized_infra = set(resolved.selected_infra)
    normalized_apps = set(resolved.selected_apps)

    if payload_for_app_chart_deps is not None and normalized_apps:
        chart_cache: _ChartMetaCache = {}
        normalized_apps, app_adjustments, app_warnings = _resolve_apps_chart_dependencies(
            payload=payload_for_app_chart_deps,
            selected_apps=normalized_apps,
            app_entries=app_entries,
            cache=chart_cache,
            collect_warnings=True,
        )
        for adjustment in app_adjustments:
            if adjustment.dependency_kind == "install_after":
                console.print(
                    f"{warning_markup('Adjusted component selection:')} "
                    f"enabling 'apps:{adjustment.dependency_app_id}' because "
                    f"'apps:{adjustment.source_app_id}' release.install_after requires it."
                )
            else:
                console.print(
                    f"{warning_markup('Adjusted component selection:')} "
                    f"enabling 'apps:{adjustment.dependency_app_id}' because "
                    f"'apps:{adjustment.source_app_id}' chart depends on "
                    f"'{adjustment.dependency_chart_name}'."
                )
        for warning in app_warnings:
            console.print(f"{warning_markup('Dependency lookup warning:')} {warning}")

    return normalized_infra, normalized_apps


def _enabled_component_ids(config: Any, *, scope: ComponentScope) -> set[str]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return set()
    if scope == "infra":
        return {str(row["id"]) for row in _dynamic_enabled_infra_component_rows(payload)}
    return {str(row["id"]) for row in _dynamic_enabled_app_chart_rows(payload)}


def _validation_scope_group_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Other"
    parts = re.split(r"[\s_-]+", raw)
    return " ".join(part.upper() if part.isupper() else part.capitalize() for part in parts if part)


def _validation_scope_component_labels(labels: list[str]) -> str:
    normalized = sorted({label for label in labels if str(label).strip()})
    if len(normalized) <= 3:
        return ", ".join(normalized)
    return f"{', '.join(normalized[:3])}, +{len(normalized) - 3} more"


def _validation_scope_summary_lines(
    config: Any,
    *,
    source_profile: SourceProfile,
) -> list[str] | None:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return None

    lines = ["Validated scope:"]
    for scope, rows in (
        ("infra", _dynamic_enabled_infra_component_rows(payload)),
        ("apps", _dynamic_enabled_app_chart_rows(payload)),
    ):
        lines.append(f"  {scope}:")
        if not rows:
            lines.append("    - none")
            continue
        entry_by_id = {
            entry.id: entry for entry in component_entries(scope, source_profile=source_profile)
        }
        grouped: dict[str, list[str]] = {}
        for row in rows:
            component_id = component_type_id(row)
            if not component_id:
                continue
            instance_id = component_instance_id(row)
            label = component_instance_label(component_id, instance_id)
            entry = entry_by_id.get(component_id)
            fallback_group = str(row.get("group", "")).strip()
            group_label = _validation_scope_group_label(
                str(entry.group).strip()
                if entry is not None and str(entry.group).strip()
                else fallback_group
            )
            grouped.setdefault(group_label, []).append(label)
        if not grouped:
            lines.append("    - none")
            continue
        for group, labels in sorted(grouped.items()):
            lines.append(f"    - {group}: {_validation_scope_component_labels(labels)}")
    return lines


def _component_dependency_issues_from_payload(
    payload: dict[str, Any],
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
    include_app_chart_dependencies: bool = True,
) -> list[str]:
    issues: list[str] = []
    selected_infra = {str(row["id"]) for row in _dynamic_enabled_infra_component_rows(payload)}
    selected_apps = {str(row["id"]) for row in _dynamic_enabled_app_chart_rows(payload)}
    selected_by_scope: dict[ComponentScope, set[str]] = {
        "infra": selected_infra,
        "apps": selected_apps,
    }
    for scope in ("infra", "apps"):
        for entry in component_entries(scope):
            if entry.id not in selected_by_scope[scope]:
                continue
            # Apps dependencies are resolved from Helm Chart.yaml at runtime.
            dependency_refs = entry.depends_on if scope == "infra" else ()
            for raw_dep in dependency_refs:
                dep_scope, dep_id = (
                    raw_dep.split(":", maxsplit=1) if ":" in raw_dep else (scope, raw_dep)
                )
                if dep_id not in selected_by_scope[dep_scope]:
                    issues.append(
                        f"component dependency '{scope}:{entry.id}' requires '{dep_scope}:{dep_id}' to be enabled"
                    )

    if selected_apps and include_app_chart_dependencies:
        runtime_app_entries = list(component_entries("apps"))
        known_app_ids = {entry.id for entry in runtime_app_entries}
        for chart_row in _dynamic_enabled_app_chart_rows(payload):
            chart_id = str(chart_row["id"])
            if chart_id in known_app_ids:
                continue
            group = str(chart_row.get("group", "")).strip().lower() or "workloads"
            runtime_app_entries.append(
                ComponentEntry(
                    id=chart_id,
                    scope="apps",
                    config_path=f"apps.{group}.{chart_id}",
                    description=f"Runtime chart '{chart_id}'",
                    default_enabled=False,
                    selectable=True,
                    enabled_path=None,
                    engine_type="helm_release",
                    source=None,
                    version=None,
                    depends_on=(),
                    dependency_match_names=(chart_id,),
                    group=group,
                )
            )
        chart_cache: _ChartMetaCache = chart_meta_cache if chart_meta_cache is not None else {}
        resolved_apps, app_adjustments, _ = _resolve_apps_chart_dependencies(
            payload=payload,
            selected_apps=selected_apps,
            app_entries=tuple(runtime_app_entries),
            cache=chart_cache,
            collect_warnings=False,
        )
        _ = resolved_apps
        for adjustment in app_adjustments:
            if adjustment.dependency_kind == "install_after":
                issues.append(
                    "app release dependency requires "
                    f"'apps:{adjustment.dependency_app_id}' when "
                    f"'apps:{adjustment.source_app_id}' is enabled "
                    "(release.install_after)"
                )
            else:
                issues.append(
                    "app chart dependency requires "
                    f"'apps:{adjustment.dependency_app_id}' when "
                    f"'apps:{adjustment.source_app_id}' is enabled "
                    f"(chart dependency: {adjustment.dependency_chart_name})"
                )
    issues.extend(mk8s_gpu_dependency_issues(payload))
    issues.extend(observability_dependency_issues(payload))
    return issues


def _validate_component_dependencies(
    config: Any,
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
) -> list[str]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ["Runtime config payload must be a mapping"]
    return _component_dependency_issues_from_payload(
        payload,
        chart_meta_cache=chart_meta_cache,
    )


def _provider_allowed_values(
    *,
    payload: dict[str, Any],
    provider_lookup: ProviderOptionLookup,
    provider: str,
    args: dict[str, Any],
    field_path: str,
) -> set[str]:
    return {
        str(choice.value).strip()
        for choice in provider_lookup.resolve(
            provider=provider,
            args=args,
            payload=payload,
            field_path=field_path,
        )
        if str(choice.value).strip()
    }


def _validate_provider_field(
    *,
    payload: dict[str, Any],
    provider_lookup: ProviderOptionLookup,
    field_path: str,
    provider: str,
    args: dict[str, Any],
    issues: list[str],
) -> None:
    value = _read_payload_field(payload, field_path)
    text_value = str(value).strip() if value is not None else ""
    if not text_value:
        issues.append(f"{field_path} is required")
        return
    allowed = _provider_allowed_values(
        payload=payload,
        provider_lookup=provider_lookup,
        provider=provider,
        args=args,
        field_path=field_path,
    )
    if not allowed:
        # Keep strict mode usable in offline/auth-missing environments.
        # If provider options are unavailable, skip membership checks here.
        return
    if text_value not in allowed:
        preview = ", ".join(sorted(list(allowed))[:10])
        issues.append(
            f"{field_path}='{text_value}' is not valid for provider source '{provider}'. "
            f"Available options include: {preview}"
        )


def _validate_enabled_chart_sources(
    config: Any,
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
) -> list[str]:
    issues: list[str] = []
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ["Runtime config payload must be a mapping"]
    app_entry_by_id = {entry.id: entry for entry in component_entries("apps")}

    for chart_row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(chart_row["id"])
        instance_id = str(chart_row["instance_id"])
        chart_repo = str(chart_row.get("repo", "")).strip()
        chart_version = str(chart_row.get("version", "")).strip()
        entry = app_entry_by_id.get(chart_id)
        chart_name = _runtime_app_chart_name_for_id(
            chart_node=chart_row,
            chart_id=chart_id,
            entry=entry,
        )
        if chart_meta_cache is None:
            issues_for_chart = _helm_chart_validation_issues(
                chart_name=chart_name,
                chart_repo=chart_repo,
                chart_version=chart_version,
            )
        else:
            issues_for_chart = _resolve_helm_chart_validation_issues(
                chart_name=chart_name,
                chart_repo=chart_repo,
                chart_version=chart_version,
                chart_meta_cache=chart_meta_cache,
            )
        for issue in issues_for_chart:
            issues.append(
                f"{_component_instance_path_label('apps', chart_id, instance_id)} {issue}"
            )
    return issues


_SEMVER_TAG_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _is_http_chart_repo(repo: str) -> bool:
    normalized = repo.strip().lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


def _is_github_tree_chart_repo(repo: str) -> bool:
    token = repo.strip().rstrip("/")
    return re.match(r"^https://github\.com/[^/]+/[^/]+/tree/[^/]+/.+$", token) is not None


def _is_oci_chart_repo(repo: str) -> bool:
    return repo.strip().lower().startswith("oci://")


def _resolve_local_module_source_path(module_source: str) -> Path | None:
    source = module_source.strip()
    if not source:
        return None
    if source.startswith(("git::", "http://", "https://", "oci://")):
        return None
    candidate = Path(source).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() and candidate.is_dir() else None

    roots: list[Path] = []
    with suppress(ValueError):
        roots.append(resolve_component_sources_file().parent)
    roots.extend(
        [
            Path.cwd(),
            Path(__file__).resolve().parents[1],
            Path(__file__).resolve().parents[2],
            Path(__file__).resolve().parents[3],
        ]
    )
    for root in roots:
        resolved = (root / source).resolve()
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def _resolve_local_chart_source_path(chart_source: str) -> Path | None:
    return _resolve_local_module_source_path(chart_source)


def _normalize_component_token(value: str) -> str:
    token = value.strip().lower().replace("_", "-")
    token = re.sub(r"[^a-z0-9-]+", "-", token)
    token = re.sub(r"-{2,}", "-", token).strip("-")
    return token


def _normalized_version_token(value: str) -> str:
    token = value.strip().lower()
    if token.startswith("v") and len(token) > 1 and token[1].isdigit():
        return token[1:]
    return token


def _versions_match(expected: str, resolved: str) -> bool:
    return _normalized_version_token(expected) == _normalized_version_token(resolved)


@lru_cache(maxsize=64)
def _fetch_helm_repo_index(repo: str) -> tuple[dict[str, Any] | None, str | None]:
    normalized_repo = repo.strip().rstrip("/")
    index_url = f"{normalized_repo}/index.yaml"
    try:
        with urllib.request.urlopen(index_url, timeout=12) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"failed to fetch {index_url}: {exc}"
    except Exception as exc:
        return None, f"failed to fetch {index_url}: {exc}"

    try:
        payload = yaml.safe_load(raw) or {}
    except Exception as exc:
        return None, f"failed to parse {index_url}: {exc}"
    if not isinstance(payload, dict):
        return None, f"invalid {index_url}: payload is not a mapping"
    entries = payload.get("entries")
    if not isinstance(entries, Mapping):
        return None, f"invalid {index_url}: missing 'entries' mapping"
    return payload, None


def _resolve_helm_chart_validation_issues(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
    chart_meta_cache: _ChartMetaCache | None = None,
) -> tuple[str, ...]:
    issues: list[str] = []
    chart_id = chart_name.strip()
    repo = chart_repo.strip()
    version = chart_version.strip()
    local_chart_dir = _resolve_local_chart_source_path(chart_id) if not repo else None
    github_tree_repo = _is_github_tree_chart_repo(repo)
    source_display = (
        repo
        if github_tree_repo
        else _chart_source_display(chart_name_or_ref=chart_id, chart_repo=repo)
    )

    if not chart_id:
        return ("name is required",)
    if local_chart_dir is not None:
        return ()
    if not repo:
        return ("repo is required",)

    requires_version = not github_tree_repo
    if requires_version and not version:
        issues.append("version is required for Helm repository and OCI chart sources")

    if _is_oci_chart_repo(repo):
        repo_ref = _canonical_app_chart_repo(chart_repo=repo, chart_name=chart_id)
        repo_tail = repo_ref.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail != chart_id.lower():
            issues.append(f"OCI ref basename must match chart name '{chart_id}': {repo_ref}")
        if version and not _SEMVER_TAG_PATTERN.fullmatch(version):
            issues.append(f"OCI version must be a semantic version tag (got '{version}')")
    elif _is_http_chart_repo(repo):
        if not github_tree_repo:
            index_payload, index_error = _fetch_helm_repo_index(repo)
            if index_error:
                issues.append(index_error)
            elif isinstance(index_payload, Mapping):
                entries = index_payload.get("entries", {})
                chart_entries = entries.get(chart_id) if isinstance(entries, Mapping) else None
                if not isinstance(chart_entries, list):
                    issues.append(f"was not found in repo index.yaml entries at {repo}")
                elif version:
                    available_versions = [
                        str(item.get("version", "")).strip()
                        for item in chart_entries
                        if isinstance(item, Mapping)
                    ]
                    if available_versions and not any(
                        _versions_match(version, candidate) for candidate in available_versions
                    ):
                        preview = ", ".join(sorted(available_versions)[:8])
                        issues.append(
                            f"version '{version}' was not found in index.yaml. Available versions include: {preview}"
                        )
    else:
        issues.append(
            "repo must start with 'https://' (Helm repo or supported GitHub tree URL) "
            f"or 'oci://', got '{repo}'"
        )

    if issues:
        return tuple(issues)

    metadata_cache = chart_meta_cache if chart_meta_cache is not None else {}
    resolved_name, resolved_version, _dependency_names, error = _normalized_chart_metadata(
        _helm_chart_metadata(
            chart_name_or_ref=chart_id,
            chart_repo=repo,
            chart_version=version,
            cache=metadata_cache,
        )
    )
    if error:
        missing_helm = "helm not found in PATH" in error
        if missing_helm:
            return (f"requires helm for source validation ({source_display}): {error}",)
        return (f"could not be resolved by helm ({source_display}): {error}",)

    if resolved_name and resolved_name != chart_id.lower():
        issues.append(f"resolved chart name '{resolved_name}' does not match '{chart_id}'")

    if version and resolved_version and not _versions_match(version, resolved_version):
        issues.append(
            f"resolved chart version '{resolved_version}' does not match configured version '{version}'"
        )

    return tuple(issues)


@lru_cache(maxsize=64)
def _helm_chart_validation_issues(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
) -> tuple[str, ...]:
    return _resolve_helm_chart_validation_issues(
        chart_name=chart_name,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )


def _validate_component_sources_registry(
    *,
    explicit: Path | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[Path, list[str], list[str]]:
    source_path = resolve_component_sources_file(explicit=explicit)
    sources = load_component_sources(explicit=explicit)
    chart_meta_cache: _ChartMetaCache = {}
    issues: list[str] = []
    warnings: list[str] = []
    total_items = len(sources.tf_modules) + len(sources.helm_charts)
    processed_items = 0

    def _advance(label: str) -> None:
        nonlocal processed_items
        processed_items += 1
        if progress_callback is not None:
            progress_callback(label, processed_items, total_items)

    if progress_callback is not None:
        progress_callback("init", 0, total_items)

    declared_entries: dict[str, tuple[str, Any]] = {}
    duplicate_ids: set[str] = set()

    for module in sources.tf_modules:
        module_id = module.module.strip().lower()
        module_source = module.source.strip()
        _advance(f"infra:{module_id or '?'}")
        if not module_id:
            issues.append("components.infra entry has empty component id")
            continue
        if not COMPONENT_ID_PATTERN.fullmatch(module_id):
            issues.append(
                f"components.infra.{module_id} component id must use lowercase letters, digits, and hyphens"
            )
            continue
        if module_id in declared_entries:
            duplicate_ids.add(module_id)
        else:
            declared_entries[module_id] = ("infra", module)
        if not module_source:
            issues.append(f"components.infra.{module_id} is missing source")
            continue
        source_issues = module_source_validation_issues(module_source)
        for issue in source_issues:
            issues.append(f"components.infra.{module_id} {issue}")
        if not source_issues:
            module_contract_issues, module_contract_warnings = module_cli_contract_findings(
                module_source
            )
            for issue in module_contract_issues:
                issues.append(f"components.infra.{module_id} {issue}")
            for warning in module_contract_warnings:
                warnings.append(f"components.infra.{module_id} {warning}")
        local_module_path = _resolve_local_module_source_path(module_source)
        if local_module_path is None:
            continue
        expected_from_folder = _normalize_component_token(local_module_path.name)
        if expected_from_folder and expected_from_folder != module_id:
            warnings.append(
                f"components.infra.{module_id} folder name '{local_module_path.name}' "
                f"normalizes to '{expected_from_folder}', which differs from module id '{module_id}'."
            )
        discovered_variables = module_variable_names(str(local_module_path))
        if not discovered_variables:
            warnings.append(
                f"components.infra.{module_id} has no discoverable Terraform variables; "
                "wizard field discovery may be limited."
            )

    for chart in sources.helm_charts:
        chart_component_id = chart.name.strip()
        chart_name = str(chart.chart_name or chart.name).strip()
        chart_id = _normalize_component_token(chart_component_id)
        chart_path = str(getattr(chart, "path", "") or "").strip()
        repo = str(chart.repo or "").strip()
        version = str(chart.version or "").strip()
        chart_label = f"components.apps.{chart_component_id}"
        _advance(f"apps:{chart_component_id or '?'}")

        if not chart_component_id:
            issues.append("components.apps entry has empty component id")
            continue
        if chart_id in declared_entries:
            duplicate_ids.add(chart_id)
        else:
            declared_entries[chart_id] = ("apps", chart)
        if not chart_path and not repo:
            issues.append(
                f"{chart_label} source.portable is required for portable validation; "
                "local-only charts are supported only with the local source profile"
            )
            continue
        chart_source_issues = _resolve_helm_chart_validation_issues(
            chart_name=chart_path or chart_name,
            chart_repo="" if chart_path else repo,
            chart_version="" if chart_path else version,
            chart_meta_cache=chart_meta_cache,
        )
        for issue in chart_source_issues:
            issues.append(f"{chart_label} {issue}")
        if not chart_source_issues:
            chart_contract_issues, chart_contract_warnings = chart_cli_contract_findings(
                chart_name=chart_path or chart_name,
                chart_repo="" if chart_path else repo,
                chart_version="" if chart_path else version,
                expected_chart_name=chart_name,
            )
            for issue in chart_contract_issues:
                issues.append(f"{chart_label} {issue}")
            for warning in chart_contract_warnings:
                warnings.append(f"{chart_label} {warning}")

    for component_id in sorted(duplicate_ids):
        issues.append(
            f"component id '{component_id}' is declared more than once across infra/apps. "
            "Cross-component bindings require globally unique component ids."
        )

    declared_app_ids = {
        component_id
        for component_id, (scope, _source_entry) in declared_entries.items()
        if scope == "apps"
    }
    mk8s_entry = next(
        (
            source_entry
            for component_id, (scope, source_entry) in declared_entries.items()
            if scope == "infra" and component_id == "mk8s"
        ),
        None,
    )
    mk8s_gpu_settings = getattr(mk8s_entry, "mk8s_gpu", None)
    role_to_app_ids: dict[str, list[str]] = {}
    for app_id in sorted(declared_app_ids):
        app_entry = declared_entries[app_id][1]
        app_policy = getattr(app_entry, "mk8s_gpu", None)
        role_name = _non_empty_text(getattr(app_policy, "role", ""))
        if role_name:
            role_to_app_ids.setdefault(role_name, []).append(app_id)
        for dependency_id in getattr(app_policy, "install_after", ()):
            dependency = _non_empty_text(dependency_id).lower()
            if dependency and dependency not in declared_app_ids:
                issues.append(
                    f"components.apps.{app_id}.cli.mk8s_gpu_policy.install_after references unknown apps component '{dependency}'"
                )
    for role_name, app_ids in sorted(role_to_app_ids.items()):
        if len(app_ids) > 1:
            issues.append(
                f"mk8s gpu app role '{role_name}' is declared more than once: {', '.join(sorted(app_ids))}"
            )
    if mk8s_gpu_settings is not None:
        gpu_visibility_settings = mk8s_gpu_settings.validations.gpu_visibility
        if gpu_visibility_settings.enabled_by_default and (
            not gpu_visibility_settings.namespace
            or not gpu_visibility_settings.image
            or not gpu_visibility_settings.timeout
        ):
            issues.append(
                "components.infra.mk8s.cli.gpu.validations.gpu_visibility must set namespace, image, and timeout when enabled_by_default=true"
            )
        nccl_settings = mk8s_gpu_settings.validations.nccl
        if nccl_settings.enabled_by_default and (
            not nccl_settings.chart_component_id
            or not nccl_settings.timeout
            or not nccl_settings.training_operator_manifest
            or not nccl_settings.training_operator_namespace
        ):
            issues.append(
                "components.infra.mk8s.cli.gpu.validations.nccl must set chart_component_id, timeout, and training operator settings when enabled_by_default=true"
            )

    for component_id, (scope, source_entry) in declared_entries.items():
        output_by_name = {output.name: output for output in source_entry.outputs}
        default_targets = default_target_paths(source_entry)
        declared_module_input_names: set[str] = set()

        if scope == "infra":
            module_source = str(source_entry.source or "").strip()
            metadata_source = _entry_module_metadata_source(
                None,
                fallback_source=str(getattr(source_entry, "metadata_source", "") or module_source),
            )
            declared_module_outputs = (
                set(module_output_names(metadata_source)) if metadata_source else set()
            )
            declared_module_input_names = (
                {_normalize_leaf_name(name) for name in module_variable_names(metadata_source)}
                if metadata_source
                else set()
            )
            is_local_like_source = bool(metadata_source) and not metadata_source.lower().startswith(
                ("git::", "http://", "https://", "oci://")
            )
            for output in source_entry.outputs:
                if output.kind != "terraform_output":
                    continue
                if output.source_path and (
                    (declared_module_outputs and output.source_path not in declared_module_outputs)
                    or (is_local_like_source and output.source_path not in declared_module_outputs)
                ):
                    issues.append(
                        f"infra component '{component_id}' output '{output.name}' references Terraform output "
                        f"'{output.source_path}', but module source '{module_source}' does not expose it"
                    )

        for default in source_entry.defaults:
            if default.kind != "shared" or not default.source_path.startswith("shared."):
                continue
            shared_value = read_path_with_catalog({}, default.source_path)
            if shared_value is None:
                issues.append(
                    f"{scope} component '{component_id}' shared default '{default.target_path}' references "
                    f"missing catalog shared path '{default.source_path}'"
                )
            elif isinstance(shared_value, str) and not shared_value.strip():
                warnings.append(
                    f"{scope} component '{component_id}' shared default '{default.target_path}' references "
                    f"blank catalog shared value '{default.source_path}'. Commands that need this default "
                    "will fail until the active component_sources.yaml sets it."
                )

        for binding in source_entry.input_bindings:
            declared_source_ref = component_input_binding_ref(binding)
            if binding.target_path in default_targets:
                issues.append(
                    f"{scope} component '{component_id}' target path '{binding.target_path}' is managed by both "
                    "defaults and input; choose one binding mechanism."
                )
            expected_prefix = "inputs." if scope == "infra" else "values."
            if not binding.target_path.startswith(expected_prefix):
                issues.append(
                    f"{scope} component '{component_id}' input binding target '{binding.target_path}' must start "
                    f"with '{expected_prefix}'"
                )
            if scope == "infra":
                target_segments = [
                    segment.strip() for segment in binding.target_path.split(".") if segment.strip()
                ]
                if len(target_segments) >= 2:
                    target_leaf = _normalize_leaf_name(target_segments[1])
                    if (
                        declared_module_input_names
                        and target_leaf not in declared_module_input_names
                    ):
                        issues.append(
                            f"{scope} component '{component_id}' input binding target "
                            f"'{binding.target_path}' does not match any declared module input "
                            f"for source '{source_entry.source}'"
                        )
            source_info = declared_entries.get(binding.source_component_id)
            if source_info is None:
                issues.append(
                    f"{scope} component '{component_id}' input binding '{binding.target_path}' references "
                    f"unknown component '{binding.source_component_id}'"
                )
                continue
            source_scope, source_component = source_info
            source_output = {output.name: output for output in source_component.outputs}.get(
                binding.source_output_name
            )
            if source_output is None:
                issues.append(
                    f"{scope} component '{component_id}' input binding '{binding.target_path}' references "
                    f"undeclared output '{declared_source_ref}'"
                )
                continue
            if source_output.kind == "terraform_output" and source_scope != "infra":
                issues.append(
                    f"{scope} component '{component_id}' input binding '{binding.target_path}' references "
                    f"terraform_output from non-infra component '{binding.source_component_id}'"
                )

        expected_default_prefix = "inputs." if scope == "infra" else "values."
        for target_path in sorted(default_targets):
            if not target_path.startswith(expected_default_prefix):
                issues.append(
                    f"{scope} component '{component_id}' default target '{target_path}' must start "
                    f"with '{expected_default_prefix}'"
                )
            if scope == "infra":
                target_segments = [
                    segment.strip() for segment in target_path.split(".") if segment.strip()
                ]
                if len(target_segments) >= 2:
                    target_leaf = _normalize_leaf_name(target_segments[1])
                    if (
                        declared_module_input_names
                        and target_leaf not in declared_module_input_names
                    ):
                        issues.append(
                            f"{scope} component '{component_id}' default target '{target_path}' does not "
                            f"match any declared module input for source '{source_entry.source}'"
                        )

        handoff = getattr(source_entry, "handoff", None)
        if handoff is not None:
            cluster_id_output_name = str(getattr(handoff, "cluster_id_output_name", "")).strip()
            cluster_id_output = output_by_name.get(cluster_id_output_name)
            if cluster_id_output is None:
                issues.append(
                    f"{scope} component '{component_id}' cluster handoff requires output "
                    f"'{cluster_id_output_name}', but that output is not declared"
                )
            elif cluster_id_output.kind != "terraform_output":
                issues.append(
                    f"{scope} component '{component_id}' cluster handoff output "
                    f"'{cluster_id_output_name}' must resolve from a Terraform module output"
                )
            access_kind = str(getattr(handoff, "access_kind", "")).strip().lower()
            access_source_label = _handoff_access_source_label(handoff)
            if access_kind == "input":
                if not access_source_label.startswith("inputs."):
                    issues.append(
                        f"{scope} component '{component_id}' cluster handoff access source "
                        f"'{access_source_label}' must target an infra inputs.* path"
                    )
                else:
                    access_segments = [
                        segment.strip()
                        for segment in access_source_label.split(".")
                        if segment.strip()
                    ]
                    if len(access_segments) >= 2:
                        access_leaf = _normalize_leaf_name(access_segments[1])
                        if (
                            declared_module_input_names
                            and access_leaf not in declared_module_input_names
                        ):
                            issues.append(
                                f"{scope} component '{component_id}' cluster handoff access source "
                                f"'{access_source_label}' does not match any declared module input "
                                f"for source '{source_entry.source}'"
                            )
            elif access_kind == "literal":
                try:
                    _normalize_handoff_access_value(
                        getattr(handoff, "access_value", None),
                        component_label=component_id,
                        source_label=access_source_label,
                    )
                except RuntimeError as exc:
                    issues.append(str(exc))
            else:
                issues.append(
                    f"{scope} component '{component_id}' cluster handoff uses unsupported access kind "
                    f"'{access_kind or '<empty>'}'"
                )

    if progress_callback is not None:
        progress_callback("done", total_items, total_items)
    return source_path, issues, warnings


def _resolve_mapping_segment(node: Mapping[str, Any], segment: str) -> Any:
    candidates = (segment, segment.replace("-", "_"), segment.replace("_", "-"))
    for candidate in candidates:
        if candidate in node:
            return node[candidate]
    return None


def _mapping_path_value(node: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = node
    for raw_segment in dotted_path.split("."):
        segment = raw_segment.strip()
        if not segment or not isinstance(current, Mapping):
            return None
        current = _resolve_mapping_segment(current, segment)
        if current is None:
            return None
    return current


def _positive_number_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _non_empty_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _has_generic_gpu_node_group(inputs: Mapping[str, Any]) -> bool:
    node_groups = _resolve_mapping_segment(inputs, "node_groups")
    if not isinstance(node_groups, Mapping):
        return False
    return any(
        isinstance(group, Mapping) and bool(_resolve_mapping_segment(group, "gpu"))
        for group in node_groups.values()
    )


def _mk8s_conditionally_required_input_leaf_names(component_node: Mapping[str, Any]) -> set[str]:
    inputs = component_node.get("inputs", {})
    if not isinstance(inputs, Mapping):
        return set()

    required: set[str] = set()

    cpu_count = _positive_number_value(_resolve_mapping_segment(inputs, "cpu_nodes_count"))
    cpu_overrides = _resolve_mapping_segment(inputs, "mk8s_cpu_node_group_overrides")
    cpu_autoscaling = (
        _mapping_path_value(cpu_overrides, "autoscaling")
        if isinstance(cpu_overrides, Mapping)
        else None
    )
    cpu_override_platform = (
        _non_empty_text(_mapping_path_value(cpu_overrides, "template.resources.platform"))
        if isinstance(cpu_overrides, Mapping)
        else ""
    )
    cpu_override_preset = (
        _non_empty_text(_mapping_path_value(cpu_overrides, "template.resources.preset"))
        if isinstance(cpu_overrides, Mapping)
        else ""
    )
    cpu_group_enabled = (cpu_count is not None and cpu_count > 0) or cpu_autoscaling is not None
    if cpu_group_enabled:
        if not cpu_override_platform:
            required.add("cpu_nodes_platform")
        if not cpu_override_preset:
            required.add("cpu_nodes_preset")

    gpu_enabled = bool(_resolve_mapping_segment(inputs, "gpu_enabled"))
    gpu_overrides = _resolve_mapping_segment(inputs, "mk8s_gpu_node_group_overrides")
    gpu_autoscaling = (
        _mapping_path_value(gpu_overrides, "autoscaling")
        if isinstance(gpu_overrides, Mapping)
        else None
    )
    gpu_override_platform = (
        _non_empty_text(_mapping_path_value(gpu_overrides, "template.resources.platform"))
        if isinstance(gpu_overrides, Mapping)
        else ""
    )
    gpu_override_preset = (
        _non_empty_text(_mapping_path_value(gpu_overrides, "template.resources.preset"))
        if isinstance(gpu_overrides, Mapping)
        else ""
    )
    if gpu_enabled:
        generic_gpu_node_group = _has_generic_gpu_node_group(inputs)
        if not generic_gpu_node_group:
            required.add("gpu_node_groups")
        if not generic_gpu_node_group and gpu_autoscaling is None:
            required.add("gpu_nodes_count_per_group")
        if not gpu_override_platform:
            required.add("gpu_nodes_platform")
        if not gpu_override_preset:
            required.add("gpu_nodes_preset")

    return required


def _vm_conditionally_required_input_leaf_names(component_node: Mapping[str, Any]) -> set[str]:
    inputs = component_node.get("inputs", {})
    if not isinstance(inputs, Mapping):
        return set()

    boot_disk_existing_id = _non_empty_text(
        _resolve_mapping_segment(inputs, "boot_disk_existing_id")
    )
    source_image_id = _non_empty_text(_resolve_mapping_segment(inputs, "source_image_id"))
    if boot_disk_existing_id or source_image_id:
        return set()
    return {"source_image_family"}


def _jump_host_public_ip_allocation_inputs(component_node: Mapping[str, Any]) -> tuple[bool, str]:
    inputs = component_node.get("inputs", {})
    if not isinstance(inputs, Mapping):
        return True, ""

    raw_create = _resolve_mapping_segment(inputs, "create_public_ip_allocation")
    create_allocation = bool(raw_create) if raw_create is not None else True
    allocation_id = _non_empty_text(_resolve_mapping_segment(inputs, "public_ip_allocation_id"))
    return create_allocation, allocation_id


def _jump_host_conditionally_required_input_leaf_names(
    component_node: Mapping[str, Any],
) -> set[str]:
    create_allocation, _allocation_id = _jump_host_public_ip_allocation_inputs(component_node)
    if create_allocation:
        return set()
    return {"public_ip_allocation_id"}


def _conditionally_required_input_leaf_names(
    *,
    entry: ComponentEntry | None,
    component_node: Mapping[str, Any],
) -> set[str]:
    if entry is None or entry.scope != "infra":
        return set()
    if getattr(entry, "validation_profile", "") == "mk8s_cluster":
        return _mk8s_conditionally_required_input_leaf_names(component_node)
    if getattr(entry, "validation_profile", "") == "vm_instance":
        return _vm_conditionally_required_input_leaf_names(component_node)
    if entry.id in {"ssh-jumphost", "wireguard-gw"}:
        return _jump_host_conditionally_required_input_leaf_names(component_node)
    return set()


def _provider_field_path_is_active(payload: dict[str, Any], field_path: str) -> bool:
    parsed = _parse_payload_path_label(field_path)
    if parsed is None:
        return False

    current: Any = payload
    for segment in parsed[:-1]:
        if isinstance(segment, int):
            if not isinstance(current, list):
                return False
            if segment < 0 or segment >= len(current):
                return False
            current = current[segment]
        else:
            if not isinstance(current, Mapping):
                return False
            next_node = _resolve_mapping_segment(current, segment)
            if next_node is None:
                return False
            current = next_node
        if isinstance(current, Mapping):
            enabled_value = _resolve_mapping_segment(current, "enabled")
            if isinstance(enabled_value, bool) and not enabled_value:
                return False
    return True


def _dynamic_provider_field_checks(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    checks: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, str, str]] = set()
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        source = str(row.get("source", "")).strip() or None
        entry = entry_by_id.get(component_id)
        if entry is None:
            entry = ComponentEntry(
                id=component_id,
                scope="infra",
                config_path=f"infra.components.{instance_id}",
                description=(
                    f"Runtime source-backed component "
                    f"'{component_instance_label(component_id, instance_id)}'"
                ),
                source=source,
            )
        elif source and str(entry.source or "").strip() != source:
            entry = replace(entry, source=source)
        component_path = _dynamic_infra_component_path(
            payload,
            component_id,
            instance_id=instance_id,
        )
        if component_path is None:
            continue
        if entry.defaults:
            component_node = _get_payload_value(payload, component_path)
            resolved_component_node = resolve_component_defaults(
                payload=payload,
                component_node=component_node if isinstance(component_node, dict) else {},
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
                include_shared=False,
            )
            _set_payload_value(payload, component_path, resolved_component_node)
        inputs_path = component_path + ("inputs",)
        inputs_node = _get_payload_value(payload, inputs_path)
        if not isinstance(inputs_node, (dict, list)):
            continue
        for relative_path in _collect_scalar_leaf_paths(inputs_node):
            full_path = inputs_path + relative_path
            full_path_label = _format_payload_path(full_path)
            if not _provider_field_path_is_active(payload, full_path_label):
                continue
            for provider, args in _provider_source_specs_for_field(
                entry=entry,
                full_path_label=full_path_label,
            ):
                dedupe_key = (full_path_label, provider, json.dumps(args, sort_keys=True))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                checks.append((full_path_label, provider, args))
    return tuple(checks)


def _materialize_singleton_provider_defaults(
    *,
    payload: dict[str, Any],
    selected_infra: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if provider_lookup is None or not selected_infra:
        return

    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        instance_id = str(row["instance_id"])
        if instance_id not in selected_infra:
            continue
        component_id = str(row["id"])
        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        component_path = _dynamic_infra_component_path(
            payload,
            component_id,
            instance_id=instance_id,
        )
        if component_path is None:
            continue

        for full_path_label in _declared_wizard_field_labels(entry, component_path=component_path):
            auto_select_single = _provider_auto_select_single_enabled(
                entry=entry,
                full_path_label=full_path_label,
            )
            auto_select_first = _provider_auto_select_first_enabled(
                entry=entry,
                full_path_label=full_path_label,
            )
            if not auto_select_single and not auto_select_first:
                continue
            if not _provider_field_path_is_active(payload, full_path_label):
                continue
            if not _provider_prompt_dependencies_ready(
                payload=payload,
                entry=entry,
                full_path_label=full_path_label,
            ):
                continue
            current_value = _read_payload_field(payload, full_path_label)
            if _has_required_prompt_value(current_value, type_hint=None):
                continue
            choices = _resolve_dynamic_field_choices(
                payload=payload,
                entry=entry,
                full_path_label=full_path_label,
                provider_lookup=provider_lookup,
            )
            if auto_select_single and len(choices) != 1:
                continue
            if auto_select_first and not choices:
                continue
            target_path = _parse_payload_path_label(full_path_label)
            if target_path is None:
                continue
            _set_payload_value_creating_containers(payload, target_path, choices[0].value)


def _materialize_mk8s_image_defaults(
    *,
    payload: dict[str, Any],
    selected_infra: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if provider_lookup is None or not selected_infra:
        return

    entry_by_id = {entry.id: entry for entry in infra_entries}

    def _set_first_provider_choice(
        *,
        entry: ComponentEntry,
        full_path_label: str,
        replace_if_invalid: bool = False,
    ) -> None:
        if not _provider_field_path_is_active(payload, full_path_label):
            return
        if not _provider_prompt_dependencies_ready(
            payload=payload,
            entry=entry,
            full_path_label=full_path_label,
        ):
            return
        choices = _resolve_dynamic_field_choices(
            payload=payload,
            entry=entry,
            full_path_label=full_path_label,
            provider_lookup=provider_lookup,
        )
        if not choices:
            return
        current_value = _read_payload_field(payload, full_path_label)
        allowed_values = {choice.value for choice in choices}
        if _has_required_prompt_value(current_value, type_hint=None):
            if not replace_if_invalid:
                return
            if str(current_value).strip() in allowed_values:
                return
        target_path = _parse_payload_path_label(full_path_label)
        if target_path is None:
            return
        _set_payload_value_creating_containers(payload, target_path, choices[0].value)

    for row in _dynamic_enabled_infra_component_rows(payload):
        instance_id = str(row["instance_id"])
        if instance_id not in selected_infra:
            continue
        component_id = str(row["id"])
        entry = entry_by_id.get(component_id)
        if entry is None or entry.validation_profile != "mk8s_cluster":
            continue
        component_path = _dynamic_infra_component_path(
            payload,
            component_id,
            instance_id=instance_id,
        )
        if component_path is None:
            continue
        component_path_label = _format_payload_path(component_path)

        cpu_os_field = f"{component_path_label}.inputs.cpu_nodes_os"
        _set_first_provider_choice(entry=entry, full_path_label=cpu_os_field)

        gpu_enabled = bool(
            _read_payload_field(payload, f"{component_path_label}.inputs.gpu_enabled")
        )
        if not gpu_enabled:
            continue

        stack_source_field = f"{component_path_label}.inputs.gpu_stack_source"
        stack_source = (
            _non_empty_text(_read_payload_field(payload, stack_source_field)) or "nebius_image"
        ).lower()
        if stack_source not in {"nebius_image", "operator_managed"}:
            raise RuntimeError(
                f"{stack_source_field} must be 'nebius_image' or 'operator_managed' for GPU-enabled MK8s clusters"
            )
        if not _non_empty_text(_read_payload_field(payload, stack_source_field)):
            target_path = _parse_payload_path_label(stack_source_field)
            if target_path is not None:
                _set_payload_value_creating_containers(payload, target_path, stack_source)

        gpu_stack_preset_field = f"{component_path_label}.inputs.gpu_stack_preset"
        if stack_source == "nebius_image":
            _set_first_provider_choice(
                entry=entry,
                full_path_label=gpu_stack_preset_field,
            )
        else:
            target_path = _parse_payload_path_label(gpu_stack_preset_field)
            if target_path is not None:
                _delete_payload_value(payload, target_path)

        gpu_os_field = f"{component_path_label}.inputs.gpu_nodes_os"
        _set_first_provider_choice(
            entry=entry,
            full_path_label=gpu_os_field,
            replace_if_invalid=(stack_source == "operator_managed"),
        )


def _materialize_vm_image_defaults(
    *,
    payload: dict[str, Any],
    selected_infra: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if provider_lookup is None or not selected_infra:
        return

    entry_by_id = {entry.id: entry for entry in infra_entries}

    for row in _dynamic_enabled_infra_component_rows(payload):
        instance_id = str(row["instance_id"])
        if instance_id not in selected_infra:
            continue
        component_id = str(row["id"])
        entry = entry_by_id.get(component_id)
        if entry is None or entry.validation_profile != "vm_instance":
            continue
        component_path = _dynamic_infra_component_path(
            payload,
            component_id,
            instance_id=instance_id,
        )
        if component_path is None:
            continue
        component_path_label = _format_payload_path(component_path)

        boot_disk_existing_id = _non_empty_text(
            _read_payload_field(payload, f"{component_path_label}.inputs.boot_disk_existing_id")
        )
        source_image_id = _non_empty_text(
            _read_payload_field(payload, f"{component_path_label}.inputs.source_image_id")
        )
        if boot_disk_existing_id or source_image_id:
            continue

        full_path_label = f"{component_path_label}.inputs.source_image_family"
        if not _provider_field_path_is_active(payload, full_path_label):
            continue
        if not _provider_prompt_dependencies_ready(
            payload=payload,
            entry=entry,
            full_path_label=full_path_label,
        ):
            continue
        choices = _resolve_dynamic_field_choices(
            payload=payload,
            entry=entry,
            full_path_label=full_path_label,
            provider_lookup=provider_lookup,
        )
        if not choices:
            continue
        current_value = _non_empty_text(_read_payload_field(payload, full_path_label))
        allowed_values = {choice.value for choice in choices}
        if current_value and current_value in allowed_values:
            continue
        target_path = _parse_payload_path_label(full_path_label)
        if target_path is None:
            continue
        _set_payload_value_creating_containers(payload, target_path, choices[0].value)


def _required_enabled_infra_field_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
    include_runtime_required: bool = True,
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        component_path_label = _component_instance_path_label("infra", component_id, instance_id)
        component_node: Mapping[str, Any] = row
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            inputs = {}

        source = str(row.get("source", "")).strip()
        entry = entry_by_id.get(component_id)
        if not source:
            source = str(entry.source if entry is not None else "").strip()
        if not source:
            continue
        inspection_source = _entry_module_metadata_source(entry, fallback_source=source)

        if entry is not None and entry.defaults:
            resolved_row = resolve_component_defaults(
                payload=payload,
                component_node=dict(row),
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
                include_shared=False,
            )
            component_node = resolved_row if isinstance(resolved_row, Mapping) else row
            inputs = resolved_row.get("inputs", {})
            if not isinstance(inputs, Mapping):
                inputs = {}

        required_leaf_names = {
            _normalize_leaf_name(name) for name in module_required_variables(inspection_source)
        }
        if include_runtime_required and entry is not None:
            required_leaf_names |= _runtime_required_input_leaf_names(entry)
        required_leaf_names |= _conditionally_required_input_leaf_names(
            entry=entry,
            component_node=component_node,
        )
        if not required_leaf_names:
            continue
        required_leaf_names -= input_binding_leaf_names(entry) if entry is not None else set()
        required_leaf_names -= (
            literal_default_input_leaf_names(entry) if entry is not None else set()
        )

        if not isinstance(inputs, Mapping):
            for leaf_name in sorted(required_leaf_names):
                issues.append(f"{component_path_label}.inputs.{leaf_name} is required")
            continue
        for leaf_name in sorted(required_leaf_names):
            value = _resolve_mapping_segment(inputs, leaf_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(f"{component_path_label}.inputs.{leaf_name} is required")
    return issues


def _wizard_followup_required_field_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues = _required_enabled_infra_field_issues(
        payload=payload,
        infra_entries=infra_entries,
        include_runtime_required=False,
    )
    issues.extend(
        _wizard_placeholder_resource_name_issues(
            payload=payload,
            infra_entries=infra_entries,
        )
    )
    return list(dict.fromkeys(issues))


def _wizard_placeholder_resource_name_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        if not _component_instance_id_is_auto_allocated(component_id, instance_id):
            continue
        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        name_input = _entry_scalar_resource_name_input(entry)
        if not name_input:
            continue
        source = str(row.get("source", "")).strip() or str(entry.source or "").strip()
        if not source:
            continue
        required_leaf_names = {
            _normalize_leaf_name(name)
            for name in module_required_variables(
                _entry_module_metadata_source(entry, fallback_source=source)
            )
        }
        required_leaf_names |= _conditionally_required_input_leaf_names(
            entry=entry,
            component_node=row,
        )
        required_leaf_names -= input_binding_leaf_names(entry)
        required_leaf_names -= literal_default_input_leaf_names(entry)
        if _normalize_leaf_name(name_input) not in required_leaf_names:
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        raw_name = _mapping_path_value(inputs, name_input)
        normalized_name = normalize_component_token(raw_name)
        if normalized_name != instance_id:
            continue
        component_path_label = _component_instance_path_label(
            "infra",
            component_id,
            instance_id,
        )
        issues.append(f"{component_path_label}.inputs.{name_input} is required")
    return issues


def _print_incomplete_wizard_no_write_warning(
    *,
    issues: Sequence[str],
    message: str,
    preserved_path: Path | None = None,
    skipped_path: Path | None = None,
) -> None:
    if not issues:
        return
    console.print(
        f"{warning_markup('Wizard stopped before all required fields were filled.')} {message}"
    )
    for issue in issues:
        console.print(f"  - {escape(issue)}")
    if preserved_path is not None:
        console.print(f"Existing project preserved: {preserved_path}")
    if skipped_path is not None:
        console.print(f"Project not created: {skipped_path}")


def _dynamic_enabled_infra_component_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    infra_node = payload.get("infra")
    if not isinstance(infra_node, Mapping):
        return []
    components = infra_node.get("components")
    if not isinstance(components, list):
        return []
    rows: list[dict[str, Any]] = []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for item in components:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        instance_id = component_instance_id(item)
        if not instance_id:
            continue
        inputs = dict(item.get("inputs", {})) if isinstance(item.get("inputs"), Mapping) else {}
        entry = entry_by_id.get(component_id)
        source = _effective_catalog_component_source(row=item, entry=entry)
        version = _effective_catalog_component_version(row=item, entry=entry)
        rows.append(
            {
                "id": component_id,
                "instance_id": instance_id,
                "source": source,
                "version": version,
                "inputs": inputs,
            }
        )
    return rows


def _dynamic_enabled_app_chart_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    apps_node = payload.get("apps")
    if not isinstance(apps_node, Mapping):
        return []
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        return []
    rows: list[dict[str, Any]] = []
    cluster_target_refs = set(enabled_cluster_target_refs(payload))
    for item in charts:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        chart_id = component_type_id(item)
        if not chart_id:
            continue
        instance_id = component_instance_id(item)
        if not instance_id:
            continue
        target_ref = (
            instance_id if instance_id in cluster_target_refs else app_chart_target_ref(item)
        )
        rows.append(
            {
                "id": chart_id,
                "instance_id": instance_id,
                "group": str(item.get("group", "")).strip().lower() or "workloads",
                "repo": str(item.get("repo", "")).strip(),
                "version": str(item.get("version", "")).strip(),
                TARGET_REF_FIELD: target_ref,
                "namespace": str(item.get("namespace", "")).strip(),
                "release-name": str(item.get("release-name", instance_id)).strip() or instance_id,
                "values": dict(item.get("values", {}))
                if isinstance(item.get("values"), Mapping)
                else {},
            }
        )
    return rows


def _enabled_custom_module_source_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        source = str(row.get("source", "")).strip()
        if not source:
            source = str(
                entry_by_id.get(component_id).source if component_id in entry_by_id else ""
            ).strip()
        if not source:
            issues.append(f"{component_label} is enabled but has no module source configured")
            continue
        for issue in module_source_validation_issues(source):
            issues.append(f"{component_label} {issue}")
        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        inspection_source = _entry_module_metadata_source(entry, fallback_source=source)
        declared_outputs = set(module_output_names(inspection_source))
        is_local_like_source = not inspection_source.lower().startswith(
            ("git::", "http://", "https://", "oci://")
        )
        for output in entry.outputs:
            if output.kind != "terraform_output":
                continue
            required_output = str(output.source_path).strip()
            if required_output and (
                (declared_outputs and required_output not in declared_outputs)
                or (is_local_like_source and required_output not in declared_outputs)
            ):
                issues.append(
                    f"{component_label} module source '{source}' must expose output "
                    f"'{required_output}' for declared component output '{output.name}'"
                )
    return issues


def _active_component_input_binding_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    all_entries = component_entry_lookup()
    active_rows: dict[tuple[ComponentScope, str, str], dict[str, Any]] = {}
    for row in _dynamic_enabled_infra_component_rows(payload):
        active_rows[("infra", str(row["id"]), str(row["instance_id"]))] = row
    for row in _dynamic_enabled_app_chart_rows(payload):
        active_rows[("apps", str(row["id"]), str(row["instance_id"]))] = row

    for (_scope, _row_component_id, instance_id), row in active_rows.items():
        component_id = str(row["id"])
        entry = all_entries.get(component_id)
        if entry is None or not entry.input_bindings:
            continue
        component_path_label = _component_instance_path_label(
            entry.scope, component_id, instance_id
        )
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"{component_path_label}.{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
            )
        for binding in entry.input_bindings:
            declared_source_ref = component_input_binding_ref(binding)
            source_entry = all_entries.get(binding.source_component_id)
            if source_entry is None:
                issues.append(
                    f"{entry.scope} component '{component_instance_label(component_id, instance_id)}' input binding '{binding.target_path}' references "
                    f"unknown component '{binding.source_component_id}'"
                )
                continue
            try:
                _resolved_source_entry, resolved_source_row, source_instance_id = (
                    resolve_input_binding_source(payload, binding=binding)
                )
            except ValueError as exc:
                issues.append(str(exc))
                continue
            source_ref = (
                component_output_ref(source_instance_id, binding.source_output_name)
                if source_instance_id
                else declared_source_ref
            )
            source_key = (
                source_entry.scope,
                binding.source_component_id,
                source_instance_id or binding.source_instance_id,
            )
            if not source_instance_id or source_key not in active_rows:
                issues.append(
                    f"{entry.scope} component '{component_instance_label(component_id, instance_id)}' input binding '{binding.target_path}' requires "
                    f"enabled source component matching '{declared_source_ref}'"
                )
                continue
            source_output = output_lookup(source_entry).get(binding.source_output_name)
            if source_output is None:
                issues.append(
                    f"{entry.scope} component '{component_instance_label(component_id, instance_id)}' input binding '{binding.target_path}' references "
                    f"undeclared output '{source_ref}'"
                )
                continue
            if source_output.kind != "terraform_output":
                static_value = resolve_component_output_value(
                    payload,
                    component_id=binding.source_component_id,
                    output_name=binding.source_output_name,
                    instance_id=source_instance_id or binding.source_instance_id,
                )
                if static_value is _UNRESOLVED:
                    issues.append(
                        f"{entry.scope} component '{component_instance_label(component_id, instance_id)}' input binding '{binding.target_path}' could not "
                        f"resolve non-Terraform output '{source_ref}' from the active config/catalog"
                    )
    return issues


def _active_handoff_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        component_label = component_instance_label(component_id, instance_id)
        entry = infra_entry_by_id.get(component_id)
        if entry is None or entry.handoff is None:
            continue

        access_source_label = _handoff_access_source_label(entry.handoff)
        access_value = _resolve_handoff_access_value(
            payload,
            component_id=component_id,
            instance_id=instance_id,
            handoff=entry.handoff,
        )

        if access_value is _UNRESOLVED:
            issues.append(
                f"infra component '{component_label}' cluster handoff access source "
                f"'{access_source_label}' could not be resolved from the active config/catalog"
            )
            continue
        try:
            _normalize_handoff_access_value(
                access_value,
                component_label=component_label,
                source_label=access_source_label,
            )
        except RuntimeError as exc:
            issues.append(str(exc))
    return issues


def _validate_active_component_sources(
    config: Any,
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
) -> None:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    issues: list[str] = []
    infra_entries = component_entries("infra")
    issues.extend(
        _enabled_custom_module_source_issues(payload=payload, infra_entries=infra_entries)
    )
    issues.extend(_active_component_input_binding_issues(payload))
    issues.extend(_active_handoff_issues(payload))
    issues.extend(_validate_enabled_chart_sources(config, chart_meta_cache=chart_meta_cache))
    if issues:
        raise RuntimeError(
            "Active component source validation failed:\n  - " + "\n  - ".join(issues)
        )


def _binding_conflict_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        entry = infra_entry_by_id.get(component_id)
        if entry is None:
            continue
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"{component_label}.{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
            )

    app_entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    for row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(row["id"])
        instance_id = str(row["instance_id"])
        chart_label = _component_instance_path_label("apps", chart_id, instance_id)
        entry = app_entry_by_id.get(chart_id)
        if entry is None:
            continue
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"{chart_label}.{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
            )

    return issues


def _jump_host_public_ip_allocation_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        if component_id not in {"ssh-jumphost", "wireguard-gw"}:
            continue
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        create_allocation, allocation_id = _jump_host_public_ip_allocation_inputs(row)
        if create_allocation and allocation_id:
            issues.append(
                f"{component_label}.inputs.create_public_ip_allocation must be false "
                "when inputs.public_ip_allocation_id is set"
            )
    return issues


def _compute_boot_disk_security_issues(
    payload: dict[str, Any],
    *,
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        entry = entry_by_id.get(component_id)
        if entry is None or not _entry_declares_compute_boot_disk_contract(entry):
            continue
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            continue
        existing_disk_id = _non_empty_text(
            _resolve_mapping_segment(inputs, "boot_disk_existing_id")
        )
        encryption_enabled = (
            _resolve_mapping_segment(inputs, "boot_disk_encryption_enabled") is True
        )
        deletion_protection = (
            _resolve_mapping_segment(inputs, "boot_disk_deletion_protection") is True
        )
        if existing_disk_id and (encryption_enabled or deletion_protection):
            issues.append(
                f"{component_label}.inputs.boot_disk_encryption_enabled and "
                "inputs.boot_disk_deletion_protection apply only when cxcli creates the boot disk"
            )
            continue
        if not encryption_enabled:
            continue
        disk_type = (
            _non_empty_text(_resolve_mapping_segment(inputs, "boot_disk_type")).upper()
            or "NETWORK_SSD"
        )
        try:
            encryption_supported = compute_boot_disk_type_supports_explicit_encryption(disk_type)
        except ValueError:
            encryption_supported = False
        if not encryption_supported:
            issues.append(
                f"{component_label}.inputs.boot_disk_encryption_enabled can be true only "
                "for boot disk types that support explicit encryption"
            )
    return issues


def _compute_data_disk_security_issues(
    payload: dict[str, Any],
    *,
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        entry = entry_by_id.get(component_id)
        if entry is None or not _entry_declares_compute_data_disk_contract(entry):
            continue
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            continue
        if _resolve_mapping_segment(inputs, "data_disk_enabled") is not True:
            continue
        disk_type = (
            _non_empty_text(_resolve_mapping_segment(inputs, "data_disk_type")).upper()
            or "NETWORK_SSD"
        )
        size_gib = _state_positive_int(_resolve_mapping_segment(inputs, "data_disk_size_gib"))
        if size_gib is not None:
            try:
                allocation_unit = compute_disk_type_allocation_unit_gib(disk_type)
                aligned_size = align_compute_disk_size_to_allocation_unit(
                    size_gib,
                    disk_type=disk_type,
                )
            except ValueError:
                allocation_unit = 1
                aligned_size = size_gib
            if allocation_unit > 1 and aligned_size != size_gib:
                issues.append(
                    f"{component_label}.inputs.data_disk_size_gib must be a multiple "
                    f"of {allocation_unit} GiB for {disk_type}"
                )
        if _resolve_mapping_segment(inputs, "data_disk_encryption_enabled") is not True:
            continue
        try:
            encryption_supported = compute_boot_disk_type_supports_explicit_encryption(disk_type)
        except ValueError:
            encryption_supported = False
        if not encryption_supported:
            issues.append(
                f"{component_label}.inputs.data_disk_encryption_enabled can be true only "
                "for data disk types that support explicit encryption"
            )
    return issues


def _enabled_custom_module_input_schema_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    helper_input_names = {
        "module_name",
    }
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        instance_id = str(row["instance_id"])
        component_label = _component_instance_path_label("infra", component_id, instance_id)
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            continue
        source = str(row.get("source", "")).strip()
        entry = entry_by_id.get(component_id)
        if not source:
            source = str(entry.source if entry is not None else "").strip()
        inspection_source = _entry_module_metadata_source(entry, fallback_source=source)
        if not inspection_source:
            continue
        declared_leaf_names = {
            _normalize_leaf_name(name) for name in module_variable_names(inspection_source)
        }
        if not declared_leaf_names:
            continue
        for raw_name in sorted(inputs.keys()):
            input_name = str(raw_name).strip()
            normalized_name = _normalize_leaf_name(input_name)
            if not normalized_name or normalized_name in helper_input_names:
                continue
            if normalized_name not in declared_leaf_names:
                issues.append(
                    f"{component_label}.inputs.{input_name} is not declared by module '{source}'"
                )
    return issues


def _contains_starter_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return "REPLACE-" in text or text.endswith(".example.internal")


def _placeholder_value_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for path in _collect_scalar_leaf_paths(payload):
        if not path:
            continue
        path_label = _format_payload_path(path)
        if path_label.startswith(("infra.", "apps.")) and not _provider_field_path_is_active(
            payload, path_label
        ):
            continue
        value = _get_payload_value(payload, path)
        if _contains_starter_placeholder(value):
            issues.append(f"{path_label} still uses starter placeholder value")
    return issues


def _validate_strict_config(
    config: Any,
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
    include_common_checks: bool = True,
) -> None:
    """Validate deployment-readiness constraints via runtime/provider checks."""
    issues: list[str] = []
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime config payload must be a mapping")
    materialize_compute_boot_disk_defaults(payload)

    infra_entries = component_entries("infra")
    if include_common_checks:
        issues.extend(_validate_component_dependencies(config, chart_meta_cache=chart_meta_cache))
        issues.extend(_active_component_input_binding_issues(payload))
        issues.extend(_active_handoff_issues(payload))
        issues.extend(
            _enabled_custom_module_source_issues(payload=payload, infra_entries=infra_entries)
        )
    issues.extend(
        _required_enabled_infra_field_issues(payload=payload, infra_entries=infra_entries)
    )
    issues.extend(_binding_conflict_issues(payload))
    issues.extend(_jump_host_public_ip_allocation_issues(payload))
    issues.extend(_compute_boot_disk_security_issues(payload, infra_entries=infra_entries))
    issues.extend(_compute_data_disk_security_issues(payload, infra_entries=infra_entries))
    issues.extend(nfs_csi_binding_issues(payload))
    issues.extend(
        _enabled_custom_module_input_schema_issues(payload=payload, infra_entries=infra_entries)
    )
    issues.extend(_placeholder_value_issues(payload))
    if issues:
        raise RuntimeError("Strict validation failed:\n  - " + "\n  - ".join(issues))

    enable_provider_option_checks = os.environ.get(
        "NEBIUS_CXCLI_STRICT_PROVIDER_OPTION_CHECKS", ""
    ).strip().lower() in {"1", "true", "yes"}
    if enable_provider_option_checks:
        provider_lookup = ProviderOptionLookup()
        for field_path, provider, args in _dynamic_provider_field_checks(
            payload=payload,
            infra_entries=infra_entries,
        ):
            _validate_provider_field(
                payload=payload,
                provider_lookup=provider_lookup,
                field_path=field_path,
                provider=provider,
                args=args,
                issues=issues,
            )

    if include_common_checks:
        issues.extend(_validate_enabled_chart_sources(config, chart_meta_cache=chart_meta_cache))

    if issues:
        raise RuntimeError("Strict validation failed:\n  - " + "\n  - ".join(issues))


def _ensure_private_key_file_env() -> None:
    current_path = os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE", "").strip()
    if current_path:
        key_path = Path(current_path)
        if key_path.exists() and key_path.is_file():
            return
        raise RuntimeError(
            f"NEBIUS_AUTH_PRIVATE_KEY_FILE points to a missing file: {key_path}. "
            "Unset it and rerun so nebius-cxcli can recreate a temporary key file from "
            "NEBIUS_AUTH_PRIVATE_KEY_PEM."
        )

    private_key_pem = os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_PEM", "").strip()
    if not private_key_pem:
        raise RuntimeError(
            "Missing Nebius auth private key material. "
            "Expected NEBIUS_AUTH_PRIVATE_KEY_PEM (or NEBIUS_AUTH_PRIVATE_KEY_FILE)."
        )

    fd, tmp_name = tempfile.mkstemp(prefix="nebius-cxcli-auth-", suffix=".pem")
    key_path = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(private_key_pem.rstrip() + "\n")
    key_path.chmod(0o600)
    os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(key_path)
    _TEMP_PRIVATE_KEY_FILES.append(key_path)


def _runtime_auth_cache_segment(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]", "-", value.strip().lower()).strip("-._")
    if not token:
        token = fallback
    return token


def _runtime_auth_cache_dir(*, project_id: str, client_name: str) -> Path:
    root = _runtime_auth_cache_root()
    client_token = _runtime_auth_cache_segment(client_name, fallback="client")
    project_token = _runtime_auth_cache_segment(project_id, fallback="project")
    return root / f"{client_token}-{project_token}"


def _runtime_auth_cache_root() -> Path:
    root_override = os.environ.get(_RUNTIME_AUTH_CACHE_ENV, "").strip()
    if root_override:
        return Path(root_override).expanduser().resolve()
    return (Path.home() / ".config" / "nebius-cxcli").resolve()


def _runtime_auth_cache_write_metadata(
    metadata_file: Path,
    payload: Mapping[str, Any],
) -> None:
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=metadata_file.parent,
            prefix=f".{metadata_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            json.dump(dict(payload), tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        tmp_path.chmod(0o600)
        os.replace(tmp_path, metadata_file)
        metadata_file.chmod(0o600)
    finally:
        if tmp_path is not None and tmp_path.exists():
            with suppress(OSError):
                tmp_path.unlink()


def _runtime_auth_cache_write(
    *,
    project_id: str,
    client_name: str,
    service_account_id: str,
    auth_public_key_id: str,
    private_key_pem: str,
    s3_access_key_id: str | None = None,
    s3_secret_access_key: str | None = None,
) -> None:
    cache_dir = _runtime_auth_cache_dir(project_id=project_id, client_name=client_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.chmod(0o700)

    private_key_file = cache_dir / "auth-private.pem"
    private_key_file.write_text(private_key_pem.rstrip() + "\n", encoding="utf-8")
    private_key_file.chmod(0o600)

    payload = {
        "client_name": client_name,
        "project_id": project_id,
        "service_account_id": service_account_id,
        "auth_public_key_id": auth_public_key_id,
        "private_key_file": private_key_file.name,
    }
    if s3_access_key_id:
        payload["s3_access_key_id"] = s3_access_key_id
    if s3_secret_access_key:
        payload["s3_secret_access_key"] = s3_secret_access_key
    metadata_file = cache_dir / _RUNTIME_AUTH_CACHE_FILE
    _runtime_auth_cache_write_metadata(metadata_file, payload)


def _runtime_auth_cache_load(*, project_id: str, client_name: str) -> bool:
    cache_dir = _runtime_auth_cache_dir(project_id=project_id, client_name=client_name)
    metadata_file = cache_dir / _RUNTIME_AUTH_CACHE_FILE
    if not metadata_file.exists():
        return False
    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    service_account_id = str(payload.get("service_account_id") or "").strip()
    auth_public_key_id = str(payload.get("auth_public_key_id") or "").strip()
    private_key_file_token = str(payload.get("private_key_file") or "").strip()
    s3_access_key_id = str(payload.get("s3_access_key_id") or "").strip()
    s3_secret_access_key = str(payload.get("s3_secret_access_key") or "").strip()
    if not service_account_id or not auth_public_key_id or not private_key_file_token:
        return False

    private_key_file = (cache_dir / private_key_file_token).resolve()
    if not private_key_file.exists() or not private_key_file.is_file():
        return False

    os.environ["NEBIUS_SA_ID"] = service_account_id
    os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = auth_public_key_id
    os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(private_key_file)
    if s3_access_key_id:
        os.environ["NEBIUS_S3_ACCESS_KEY_ID"] = s3_access_key_id
        os.environ["AWS_ACCESS_KEY_ID"] = s3_access_key_id
    if s3_secret_access_key:
        os.environ["NEBIUS_S3_SECRET_ACCESS_KEY"] = s3_secret_access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = s3_secret_access_key
    return True


@dataclass(frozen=True)
class RuntimeAuthProfileStatus:
    project_id: str
    client_name: str
    cache_dir: Path
    metadata_file: Path
    metadata_exists: bool
    service_account_id: str | None
    auth_public_key_id: str | None
    private_key_file: Path | None
    private_key_exists: bool
    cloud_public_key_exists: bool | None
    cloud_check_error: str | None
    issues: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeAuthCacheMaterial:
    project_id: str
    client_name: str
    service_account_id: str
    auth_public_key_id: str
    private_key_file: Path
    private_key_pem: str
    s3_access_key_id: str | None
    s3_secret_access_key: str | None


@dataclass(frozen=True)
class MysteryBoxEsoCredentials:
    service_account_id: str
    auth_public_key_id: str
    private_key_pem: str


def _runtime_auth_float_env(name: str, default: float, *, minimum: float) -> float:
    token = os.environ.get(name, "").strip()
    if not token:
        return default
    try:
        value = float(token)
    except ValueError:
        return default
    return max(minimum, value)


def _runtime_auth_token_sdk(material: RuntimeAuthCacheMaterial):
    from nebius.sdk import SDK

    kwargs: dict[str, object] = {
        "service_account_id": material.service_account_id,
        "service_account_public_key_id": material.auth_public_key_id,
        "service_account_private_key_file_name": material.private_key_file,
        "parent_id": material.project_id,
    }
    endpoint = os.environ.get("NEBIUS_ENDPOINT", "").strip()
    if endpoint:
        kwargs["domain"] = endpoint
    return SDK(**kwargs)


def _wait_for_runtime_auth_token_ready(material: RuntimeAuthCacheMaterial) -> None:
    timeout_seconds = _runtime_auth_float_env(
        _RUNTIME_AUTH_TOKEN_READY_TIMEOUT_ENV,
        _RUNTIME_AUTH_TOKEN_READY_TIMEOUT_SECONDS,
        minimum=1.0,
    )
    poll_seconds = _runtime_auth_float_env(
        _RUNTIME_AUTH_TOKEN_READY_POLL_ENV,
        _RUNTIME_AUTH_TOKEN_READY_POLL_SECONDS,
        minimum=0.25,
    )
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    announced_wait = False

    while True:
        sdk = _runtime_auth_token_sdk(material)
        try:
            remaining = max(1.0, deadline - time.monotonic())
            with suppress_expected_refresh_logs():
                sdk.get_token_sync(timeout=min(10.0, remaining))
            return
        except Exception as exc:
            last_error = exc
            if not announced_wait:
                console.print(
                    f"{warning_markup('Waiting:')} Runtime auth key is not accepted by "
                    "Nebius token service yet; waiting for propagation."
                )
                announced_wait = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Runtime auth profile was created, but Nebius token service did not "
                    f"accept auth public key '{material.auth_public_key_id}' within "
                    f"{timeout_seconds:.0f}s. Last error: {last_error}"
                ) from last_error
            time.sleep(min(poll_seconds, remaining))
        finally:
            close = getattr(sdk, "sync_close", None)
            if callable(close):
                with suppress(Exception):
                    close()


def _runtime_auth_profile_recreate_reason(
    status: RuntimeAuthProfileStatus,
) -> str | None:
    def _cloud_error_looks_like_deleted_key() -> bool:
        error = str(status.cloud_check_error or "").strip().lower()
        if not error:
            return False
        return (
            "public key not exists" in error
            or "jwtkeynotexists" in error
            or "expired or deactivated" in error
        )

    if not status.metadata_exists:
        return f"runtime-auth metadata file is missing: {status.metadata_file}"
    if not status.service_account_id:
        return "runtime-auth metadata is missing service_account_id"
    if not status.auth_public_key_id:
        return "runtime-auth metadata is missing auth_public_key_id"
    if status.private_key_file is None:
        return "runtime-auth metadata is missing private_key_file"
    if not status.private_key_exists:
        return f"runtime-auth private key file is missing: {status.private_key_file}"
    if status.cloud_public_key_exists is False:
        return (
            "cached Nebius auth public key "
            f"'{status.auth_public_key_id}' no longer exists or is not accessible"
        )
    if _cloud_error_looks_like_deleted_key():
        return (
            "cached Nebius auth public key "
            f"'{status.auth_public_key_id}' no longer exists or is not accessible"
        )
    return None


def _runtime_auth_cache_material(
    *, project_id: str, client_name: str
) -> RuntimeAuthCacheMaterial | None:
    cache_dir = _runtime_auth_cache_dir(project_id=project_id, client_name=client_name)
    metadata_file = cache_dir / _RUNTIME_AUTH_CACHE_FILE
    if not metadata_file.exists():
        return None
    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    service_account_id = _non_empty_text(payload.get("service_account_id"))
    auth_public_key_id = _non_empty_text(payload.get("auth_public_key_id"))
    private_key_file_token = _non_empty_text(payload.get("private_key_file"))
    if not service_account_id or not auth_public_key_id or not private_key_file_token:
        return None

    private_key_file = (cache_dir / private_key_file_token).resolve()
    if not private_key_file.exists() or not private_key_file.is_file():
        return None
    private_key_pem = private_key_file.read_text(encoding="utf-8").strip()
    if not private_key_pem:
        return None

    return RuntimeAuthCacheMaterial(
        project_id=project_id,
        client_name=client_name,
        service_account_id=service_account_id,
        auth_public_key_id=auth_public_key_id,
        private_key_file=private_key_file,
        private_key_pem=private_key_pem,
        s3_access_key_id=_non_empty_text(payload.get("s3_access_key_id")) or None,
        s3_secret_access_key=_non_empty_text(payload.get("s3_secret_access_key")) or None,
    )


def _create_or_recreate_runtime_auth_profile(
    *,
    project_id: str,
    client_name: str,
    recreate: bool,
    profile: str | None,
    endpoint: str | None,
    sdk_config_file: Path | None,
) -> tuple[RuntimeAuthCacheMaterial, bool]:
    existing = _runtime_auth_cache_material(project_id=project_id, client_name=client_name)
    if existing is not None and not recreate:
        return existing, False

    result = bootstrap_ci_service_account(
        project_id=project_id,
        service_account_name=_RUNTIME_TF_SERVICE_ACCOUNT_NAME,
        service_account_description="Service account used by nebius-cxcli Terraform runtime automation",
        role_ids=["editor"],
        auth_key_description="nebius-cxcli Terraform runtime authorized key",
        access_key_description="nebius-cxcli Terraform runtime Object Storage access key",
        profile=profile,
        endpoint=endpoint,
        config_file=sdk_config_file,
    )
    _runtime_auth_cache_write(
        project_id=project_id,
        client_name=client_name,
        service_account_id=result.service_account_id,
        auth_public_key_id=result.auth_public_key_id,
        private_key_pem=result.auth_private_key_pem,
        s3_access_key_id=result.s3_access_key_id,
        s3_secret_access_key=result.s3_secret_access_key,
    )
    material = _runtime_auth_cache_material(project_id=project_id, client_name=client_name)
    if material is None:
        raise RuntimeError(
            "Runtime auth profile was created but cache material could not be loaded"
        )
    return material, True


def _runtime_auth_profile_status(
    *,
    project_id: str,
    client_name: str,
    profile: str | None,
    endpoint: str | None,
    sdk_config_file: Path | None,
) -> RuntimeAuthProfileStatus:
    cache_dir = _runtime_auth_cache_dir(project_id=project_id, client_name=client_name)
    metadata_file = cache_dir / _RUNTIME_AUTH_CACHE_FILE
    metadata_exists = metadata_file.exists()

    service_account_id: str | None = None
    auth_public_key_id: str | None = None
    private_key_file: Path | None = None
    private_key_exists = False
    cloud_public_key_exists: bool | None = None
    cloud_check_error: str | None = None
    issues: list[str] = []

    payload: dict[str, Any] = {}
    if metadata_exists:
        try:
            parsed = json.loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                payload = parsed
            else:
                issues.append("runtime-auth metadata payload is not a JSON object")
        except Exception as exc:
            issues.append(f"runtime-auth metadata is not valid JSON: {exc}")
    else:
        issues.append(f"runtime-auth metadata file not found: {metadata_file}")

    service_account_id = _non_empty_text(payload.get("service_account_id"))
    auth_public_key_id = _non_empty_text(payload.get("auth_public_key_id"))
    private_key_file_token = _non_empty_text(payload.get("private_key_file"))

    if not service_account_id:
        issues.append("missing service_account_id in runtime-auth metadata")
    if not auth_public_key_id:
        issues.append("missing auth_public_key_id in runtime-auth metadata")

    if private_key_file_token:
        private_key_file = (cache_dir / private_key_file_token).resolve()
        private_key_exists = private_key_file.exists() and private_key_file.is_file()
        if not private_key_exists:
            issues.append(f"private key file missing: {private_key_file}")
    else:
        issues.append("missing private_key_file in runtime-auth metadata")

    if auth_public_key_id:
        try:
            cloud_public_key_exists = auth_public_key_exists(
                auth_public_key_id=auth_public_key_id,
                profile=profile,
                endpoint=endpoint,
                config_file=sdk_config_file,
            )
            if not cloud_public_key_exists:
                issues.append(
                    f"auth_public_key_id '{auth_public_key_id}' does not exist (or is not accessible) in Nebius"
                )
        except Exception as exc:
            cloud_check_error = str(exc)
            issues.append(f"failed Nebius auth public key verification: {exc}")

    return RuntimeAuthProfileStatus(
        project_id=project_id,
        client_name=client_name,
        cache_dir=cache_dir,
        metadata_file=metadata_file,
        metadata_exists=metadata_exists,
        service_account_id=service_account_id,
        auth_public_key_id=auth_public_key_id,
        private_key_file=private_key_file,
        private_key_exists=private_key_exists,
        cloud_public_key_exists=cloud_public_key_exists,
        cloud_check_error=cloud_check_error,
        issues=tuple(issues),
    )


def _discover_runtime_auth_profiles() -> list[tuple[str, str]]:
    root = _runtime_auth_cache_root()
    if not root.exists() or not root.is_dir():
        return []

    profiles: list[tuple[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        metadata_file = child / _RUNTIME_AUTH_CACHE_FILE
        if not metadata_file.exists():
            continue
        client_name = ""
        project_id = ""
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                client_name = _non_empty_text(payload.get("client_name")) or ""
                project_id = _non_empty_text(payload.get("project_id")) or ""
        except Exception:
            pass
        if not client_name or not project_id:
            folder = child.name.strip()
            project_marker = "-project-"
            marker_index = folder.rfind(project_marker)
            if marker_index > 0:
                inferred_client = folder[:marker_index]
                inferred_project = folder[marker_index + 1 :]
                client_name = client_name or inferred_client
                project_id = project_id or inferred_project
        if client_name and project_id:
            profiles.append((client_name, project_id))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for client_name, project_id in profiles:
        key = (client_name, project_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _resolve_client_name_for_runtime_profile(
    *,
    project_id: str,
    client_name: str | None,
    project_config: Path | None,
) -> str:
    if client_name or project_config is not None:
        return _resolve_client_name_for_auth_bootstrap(
            client_name=client_name,
            project_config=project_config,
        )
    matches = [name for name, pid in _discover_runtime_auth_profiles() if pid == project_id]
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise RuntimeError(
            "Multiple runtime auth profiles exist for this project_id. "
            "Provide --client-name (or --project-config)."
        )
    raise RuntimeError("Missing required option: --client-name (or provide --project-config)")


def _runtime_auth_missing_envs(
    *,
    need_terraform: bool,
) -> list[str]:
    required: list[str] = []
    credentials_file = os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE", "").strip()
    has_credentials_file = bool(credentials_file)
    if need_terraform and not has_credentials_file:
        required.extend(["NEBIUS_SA_ID", "NEBIUS_AUTH_PUBLIC_KEY_ID"])

    missing = [name for name in required if not os.environ.get(name)]
    has_private_key_file = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
    has_private_key_pem = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_PEM"))
    if (
        (need_terraform and not has_credentials_file)
        and not (has_private_key_file or has_private_key_pem)
        and "NEBIUS_AUTH_PRIVATE_KEY_PEM" not in missing
    ):
        missing.append("NEBIUS_AUTH_PRIVATE_KEY_PEM")
    if need_terraform:
        aws_access = (
            os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
            or os.environ.get("NEBIUS_S3_ACCESS_KEY_ID", "").strip()
        )
        aws_secret = (
            os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
            or os.environ.get("NEBIUS_S3_SECRET_ACCESS_KEY", "").strip()
        )
        if not aws_access:
            missing.append("AWS_ACCESS_KEY_ID")
        if not aws_secret:
            missing.append("AWS_SECRET_ACCESS_KEY")
    return missing


def _ensure_runtime_auth_material(
    config: Any,
    *,
    need_terraform: bool,
    auto_bootstrap: bool = False,
) -> None:
    def _export_material_to_env(material: RuntimeAuthCacheMaterial) -> None:
        os.environ["NEBIUS_SA_ID"] = material.service_account_id
        os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = material.auth_public_key_id
        os.environ["NEBIUS_AUTH_PRIVATE_KEY_PEM"] = material.private_key_pem
        os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(material.private_key_file)
        if material.s3_access_key_id:
            os.environ["NEBIUS_S3_ACCESS_KEY_ID"] = material.s3_access_key_id
            os.environ["AWS_ACCESS_KEY_ID"] = material.s3_access_key_id
        if material.s3_secret_access_key:
            os.environ["NEBIUS_S3_SECRET_ACCESS_KEY"] = material.s3_secret_access_key
            os.environ["AWS_SECRET_ACCESS_KEY"] = material.s3_secret_access_key

    missing = _runtime_auth_missing_envs(
        need_terraform=need_terraform,
    )
    project_id = str(config.client_info.nebius.project_id).strip()
    client_name = str(config.client_info.client_name).strip()
    loaded_from_cache = False
    if missing:
        loaded_from_cache = _runtime_auth_cache_load(project_id=project_id, client_name=client_name)
        missing = _runtime_auth_missing_envs(
            need_terraform=need_terraform,
        )
    if not missing and loaded_from_cache:
        status = _runtime_auth_profile_status(
            project_id=project_id,
            client_name=client_name,
            profile=None,
            endpoint=None,
            sdk_config_file=None,
        )
        recreate_reason = _runtime_auth_profile_recreate_reason(status)
        if recreate_reason:
            if not auto_bootstrap:
                raise RuntimeError(
                    "Cached runtime auth profile is stale: "
                    + recreate_reason
                    + "\nRun `nebius-cxcli auth --project-id "
                    + project_id
                    + " --client-name "
                    + client_name
                    + " --recreate`, or rerun with --auto-auth-bootstrap."
                )
            console.print(
                f"{warning_markup('WARNING:', bold=True)} Cached runtime auth profile is stale; "
                f"recreating because {recreate_reason}."
            )
            material, _ = _create_or_recreate_runtime_auth_profile(
                project_id=project_id,
                client_name=client_name,
                recreate=True,
                profile=None,
                endpoint=None,
                sdk_config_file=None,
            )
            _export_material_to_env(material)
            _wait_for_runtime_auth_token_ready(material)
            missing = _runtime_auth_missing_envs(
                need_terraform=need_terraform,
            )
    if missing:
        if not auto_bootstrap:
            raise RuntimeError(
                "Missing runtime auth environment values:\n  - "
                + "\n  - ".join(sorted(missing))
                + "\nSet these variables explicitly (or provide NEBIUS_AUTH_CREDENTIALS_FILE), "
                "or rerun with --auto-auth-bootstrap."
            )
        material, created = _create_or_recreate_runtime_auth_profile(
            project_id=project_id,
            client_name=client_name,
            recreate=False,
            profile=None,
            endpoint=None,
            sdk_config_file=None,
        )
        _export_material_to_env(material)
        if created:
            _wait_for_runtime_auth_token_ready(material)

        # Handle stale runtime-auth caches created before S3 key fields existed.
        still_missing = _runtime_auth_missing_envs(
            need_terraform=need_terraform,
        )
        if still_missing:
            material, _ = _create_or_recreate_runtime_auth_profile(
                project_id=project_id,
                client_name=client_name,
                recreate=True,
                profile=None,
                endpoint=None,
                sdk_config_file=None,
            )
            _export_material_to_env(material)
            _wait_for_runtime_auth_token_ready(material)
            still_missing = _runtime_auth_missing_envs(
                need_terraform=need_terraform,
            )
            if still_missing:
                raise RuntimeError(
                    "Runtime auth bootstrap did not provide required values:\n  - "
                    + "\n  - ".join(sorted(still_missing))
                    + "\nRun `nebius-cxcli auth --project-id "
                    + project_id
                    + " --client-name "
                    + client_name
                    + " --recreate` and retry."
                )
        console.print(
            "[green]Auto-bootstrapped runtime auth[/green] "
            "(service account + Object Storage key + auth key) for this command run."
            if created
            else "[green]Loaded runtime auth from cache[/green] for this command run."
        )

    if need_terraform and not os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE"):
        _ensure_private_key_file_env()


def _mysterybox_eso_service_account_description() -> str:
    return "Service account used by External Secrets Operator to read Nebius MysteryBox payloads"


@contextmanager
def _operator_auth_env_without_runtime_auth():
    """Avoid using Terraform runtime service-account env for operator IAM bootstrap."""
    runtime_auth_keys = (
        "NEBIUS_SA_ID",
        "NEBIUS_AUTH_PUBLIC_KEY_ID",
        "NEBIUS_AUTH_PRIVATE_KEY_FILE",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    )
    saved = {key: os.environ.get(key) for key in runtime_auth_keys}
    for key in runtime_auth_keys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _mysterybox_eso_operator_auth_env():
    with _operator_auth_env_without_runtime_auth():
        yield


def _ensure_mysterybox_eso_service_account_identity(*, project_id: str):
    try:
        with _mysterybox_eso_operator_auth_env():
            return ensure_ci_service_account_identity(
                project_id=project_id,
                service_account_name=_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME,
                service_account_description=_mysterybox_eso_service_account_description(),
                role_ids=list(_MYSTERYBOX_ESO_ROLE_IDS),
                profile=None,
                endpoint=None,
                config_file=None,
                allow_cli_token=True,
            )
    except Exception as exc:
        raise RuntimeError(
            "Failed to ensure dedicated ESO MysteryBox service account "
            f"'{_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME}' with only "
            "mysterybox.payload-viewer. The operator identity must be allowed to "
            "manage service accounts, IAM groups, and access permits in project "
            f"'{project_id}'."
        ) from exc


def _create_mysterybox_eso_credentials(*, project_id: str) -> MysteryBoxEsoCredentials:
    try:
        with _mysterybox_eso_operator_auth_env():
            result = bootstrap_service_account_auth_key(
                project_id=project_id,
                service_account_name=_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME,
                service_account_description=_mysterybox_eso_service_account_description(),
                role_ids=list(_MYSTERYBOX_ESO_ROLE_IDS),
                auth_key_description="nebius-cxcli ESO MysteryBox authorized key",
                profile=None,
                endpoint=None,
                config_file=None,
                allow_cli_token=True,
            )
    except Exception as exc:
        raise RuntimeError(
            "Failed to bootstrap dedicated ESO MysteryBox service account "
            f"'{_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME}' with only "
            "mysterybox.payload-viewer and create an authorized key. The operator "
            "identity must be allowed to manage service accounts, IAM groups, "
            f"and access permits in project '{project_id}'."
        ) from exc
    credentials = MysteryBoxEsoCredentials(
        service_account_id=result.service_account_id,
        auth_public_key_id=result.auth_public_key_id,
        private_key_pem=result.auth_private_key_pem,
    )
    _wait_for_mysterybox_eso_token_ready(project_id=project_id, credentials=credentials)
    return credentials


def _write_runtime_private_key_tempfile(private_key_pem: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(prefix="nebius-cxcli-mysterybox-eso-", suffix=".pem")
    key_path = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(private_key_pem.rstrip() + "\n")
    key_path.chmod(0o600)
    _TEMP_PRIVATE_KEY_FILES.append(key_path)
    return key_path


def _wait_for_mysterybox_eso_token_ready(
    *,
    project_id: str,
    credentials: MysteryBoxEsoCredentials,
) -> None:
    private_key_file = _write_runtime_private_key_tempfile(credentials.private_key_pem)
    material = RuntimeAuthCacheMaterial(
        project_id=project_id,
        client_name="mysterybox-eso",
        service_account_id=credentials.service_account_id,
        auth_public_key_id=credentials.auth_public_key_id,
        private_key_file=private_key_file,
        private_key_pem=credentials.private_key_pem,
        s3_access_key_id=None,
        s3_secret_access_key=None,
    )
    try:
        _wait_for_runtime_auth_token_ready(material)
    finally:
        with suppress(FileNotFoundError):
            private_key_file.unlink()
        with suppress(ValueError):
            _TEMP_PRIVATE_KEY_FILES.remove(private_key_file)


def _terraform_runtime_env(config: Any) -> dict[str, str]:
    runtime_env: dict[str, str] = {}

    project_id = str(config.client_info.nebius.project_id).strip()
    client_name = str(config.client_info.client_name).strip()
    runtime_env["TF_VAR_nebius_provider_module_name"] = build_provider_module_name(
        client_name=client_name,
        project_id=project_id,
    )
    runtime_env["TF_VAR_nebius_provider_parent_id"] = project_id

    credentials_file = os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE", "").strip()
    if credentials_file:
        runtime_env["TF_VAR_nebius_service_account_credentials_file"] = credentials_file
        return runtime_env

    service_account_id = os.environ.get("NEBIUS_SA_ID", "").strip()
    auth_public_key_id = os.environ.get("NEBIUS_AUTH_PUBLIC_KEY_ID", "").strip()
    private_key_file = os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE", "").strip()
    if service_account_id:
        runtime_env["TF_VAR_nebius_service_account_id"] = service_account_id
    if auth_public_key_id:
        runtime_env["TF_VAR_nebius_auth_public_key_id"] = auth_public_key_id
    if private_key_file:
        runtime_env["TF_VAR_nebius_auth_private_key_file"] = private_key_file
    return runtime_env


def _mysterybox_version_id_unset(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text.lower() == "n/a"


def _mysterybox_unset_version_targets(
    payload: Mapping[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return []
    components = infra.get("components")
    if not isinstance(components, list):
        return []

    targets: list[tuple[str, str, dict[str, Any]]] = []
    for row in components:
        if not isinstance(row, dict):
            continue
        if component_type_id(row) != "mysterybox" or not bool(row.get("enabled", False)):
            continue
        instance_id = component_instance_id(row)
        inputs = row.get("inputs")
        if not instance_id or not isinstance(inputs, Mapping):
            continue
        secrets = inputs.get("secrets")
        if not isinstance(secrets, list):
            continue
        for secret in secrets:
            if not isinstance(secret, dict):
                continue
            secret_name = str(secret.get("name") or "").strip()
            if secret_name and _mysterybox_version_id_unset(secret.get("version_id")):
                targets.append((instance_id, secret_name, secret))
    flat_row = infra.get("mysterybox")
    if isinstance(flat_row, dict) and bool(flat_row.get("enabled", False)):
        secrets = flat_row.get("secrets")
        if isinstance(secrets, list):
            for secret in secrets:
                if not isinstance(secret, dict):
                    continue
                secret_name = str(secret.get("name") or "").strip()
                if secret_name and _mysterybox_version_id_unset(secret.get("version_id")):
                    targets.append(("mysterybox", secret_name, secret))
    return targets


def _mysterybox_unset_version_target_keys(payload: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {
        (instance_id, secret_name)
        for instance_id, secret_name, _secret in _mysterybox_unset_version_targets(payload)
    }


def _mysterybox_module_names_by_instance(payload: Mapping[str, Any]) -> dict[str, str]:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return {}
    components = infra.get("components")
    if not isinstance(components, list):
        return {}

    used_module_names: set[str] = set()
    names: dict[str, str] = {}
    for row in components:
        if not isinstance(row, Mapping):
            continue
        if not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        instance_id = component_instance_id(row)
        if not component_id or not instance_id:
            continue
        inputs = row.get("inputs")
        raw_module_name = instance_id
        if isinstance(inputs, Mapping):
            raw_module_name = str(inputs.get("module_name") or instance_id).strip()
        module_name_base = _terraform_identifier_hint(raw_module_name or instance_id)
        module_name = module_name_base
        counter = 2
        while module_name in used_module_names:
            module_name = f"{module_name_base}_{counter}"
            counter += 1
        used_module_names.add(module_name)
        if component_id == "mysterybox":
            names[instance_id] = module_name
    return names


def _mysterybox_payload_values_env_var(instance_id: str, module_name: str | None) -> str:
    token = _terraform_identifier_hint(str(module_name or instance_id).strip() or instance_id)
    return f"TF_VAR_{token}_payload_values"


def _mysterybox_runtime_payload_requirements(
    config: Any,
) -> dict[str, list[tuple[str, str]]]:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return {}

    module_names = _mysterybox_module_names_by_instance(payload)
    requirements: dict[str, list[tuple[str, str]]] = {}
    for instance_id, secret_name, secret in _mysterybox_unset_version_targets(payload):
        payload_schema = secret.get("payload")
        if not isinstance(payload_schema, Mapping):
            continue
        env_var = _mysterybox_payload_values_env_var(
            instance_id,
            module_names.get(instance_id),
        )
        for raw_key in payload_schema:
            payload_key = str(raw_key or "").strip()
            if payload_key:
                requirements.setdefault(env_var, []).append((secret_name, payload_key))

    deduped_requirements: dict[str, list[tuple[str, str]]] = {}
    for env_var, entries in requirements.items():
        seen_entries: set[tuple[str, str]] = set()
        for entry in entries:
            if entry in seen_entries:
                continue
            seen_entries.add(entry)
            deduped_requirements.setdefault(env_var, []).append(entry)
    return deduped_requirements


def _parse_mysterybox_payload_values_env(env_var: str, raw_value: str) -> Mapping[str, Any]:
    try:
        parsed = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"{env_var} must be a JSON/YAML object shaped as "
            '{"secret-name":{"PAYLOAD_KEY":"value"}}.'
        ) from exc
    if not isinstance(parsed, Mapping):
        raise RuntimeError(
            f"{env_var} must be a JSON/YAML object shaped as "
            '{"secret-name":{"PAYLOAD_KEY":"value"}}.'
        )
    return parsed


def _mysterybox_payload_value_present(
    payload_values: Mapping[str, Any],
    *,
    secret_name: str,
    payload_key: str,
) -> bool:
    secret_values = payload_values.get(secret_name)
    if not isinstance(secret_values, Mapping):
        return False
    value = secret_values.get(payload_key)
    return isinstance(value, str) and value != ""


def _mysterybox_payload_values_error(
    missing_by_env: Mapping[str, Sequence[tuple[str, str]]],
) -> RuntimeError:
    lines = [
        "Missing MysteryBox runtime payload values for first deploy:",
    ]
    for env_var, entries in sorted(missing_by_env.items()):
        lines.append(f"  {env_var}:")
        for secret_name, payload_key in entries:
            lines.append(f"    - {secret_name}.{payload_key}")

    lines.append("Set the runtime variable before deploy, for example:")
    for env_var, entries in sorted(missing_by_env.items()):
        example: dict[str, dict[str, str]] = {}
        for secret_name, payload_key in entries:
            example.setdefault(secret_name, {})[payload_key] = "<value>"
        lines.append(f"  export {env_var}={shlex.quote(json.dumps(example, sort_keys=True))}")
    lines.append(
        "Payload values stay runtime-only and are not written to config.yaml or "
        "generated artifacts."
    )
    return RuntimeError("\n".join(lines))


def _mysterybox_prompt_runtime_payload_value(*, secret_name: str, payload_key: str) -> str:
    while True:
        value = typer.prompt(
            f"MysteryBox payload value for {secret_name}.{payload_key}",
            hide_input=True,
            show_default=False,
        )
        value_text = str(value or "")
        if value_text:
            return value_text
        console.print("[yellow]Value cannot be empty.[/yellow]")


def _collect_mysterybox_runtime_payload_values(
    config: Any,
    *,
    environ: Mapping[str, str] | None = None,
    prompt: bool = False,
) -> dict[str, str]:
    requirements = _mysterybox_runtime_payload_requirements(config)
    if not requirements:
        return {}

    runtime_environ = environ if environ is not None else os.environ
    collected_env: dict[str, str] = {}
    missing_by_env: dict[str, list[tuple[str, str]]] = {}
    for env_var, entries in requirements.items():
        raw_value = str(runtime_environ.get(env_var, "") or "").strip()
        payload_values: dict[str, Any] = {}
        if raw_value:
            payload_values = dict(_parse_mysterybox_payload_values_env(env_var, raw_value))
        for secret_name, payload_key in entries:
            if not _mysterybox_payload_value_present(
                payload_values,
                secret_name=secret_name,
                payload_key=payload_key,
            ):
                if prompt:
                    secret_values = payload_values.setdefault(secret_name, {})
                    if not isinstance(secret_values, dict):
                        secret_values = {}
                        payload_values[secret_name] = secret_values
                    secret_values[payload_key] = _mysterybox_prompt_runtime_payload_value(
                        secret_name=secret_name,
                        payload_key=payload_key,
                    )
                    continue
                missing_by_env.setdefault(env_var, []).append((secret_name, payload_key))
        if prompt:
            collected_env[env_var] = json.dumps(payload_values, sort_keys=True)

    if not missing_by_env:
        return collected_env

    raise _mysterybox_payload_values_error(missing_by_env)


def _validate_mysterybox_runtime_payload_values(
    config: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    _collect_mysterybox_runtime_payload_values(
        config,
        environ=environ,
        prompt=False,
    )


def _mysterybox_primary_version_ids_from_outputs(
    terraform_outputs: Mapping[str, Any],
    target_keys: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], str], list[str]]:
    version_ids: dict[tuple[str, str], str] = {}
    missing: list[str] = []
    for instance_id, secret_name in sorted(target_keys):
        output_name = component_output_root_name(instance_id, "primary_secret_version_ids")
        output = terraform_outputs.get(output_name)
        output_value = output.get("value") if isinstance(output, Mapping) else None
        version_id = (
            str(output_value.get(secret_name) or "").strip()
            if isinstance(output_value, Mapping)
            else ""
        )
        if not re.fullmatch(r"mbsecver-[a-z0-9]+", version_id):
            missing.append(f"{component_instance_label('mysterybox', instance_id)}.{secret_name}")
            continue
        version_ids[(instance_id, secret_name)] = version_id
    return version_ids, missing


def _set_mysterybox_version_ids_in_payload(
    payload: Mapping[str, Any],
    version_ids: Mapping[tuple[str, str], str],
) -> bool:
    changed = False
    for instance_id, secret_name, secret in _mysterybox_unset_version_targets(payload):
        version_id = version_ids.get((instance_id, secret_name))
        if not version_id:
            continue
        secret["version_id"] = version_id
        changed = True
    return changed


def _set_mysterybox_version_ids_in_tfvars(
    tfvars: Mapping[str, Any],
    *,
    runtime_payload: Mapping[str, Any],
    version_ids: Mapping[tuple[str, str], str],
) -> bool:
    module_names = _mysterybox_module_names_by_instance(runtime_payload)
    changed = False
    for (instance_id, secret_name), version_id in version_ids.items():
        module_name = module_names.get(instance_id)
        if not module_name:
            continue
        secrets = tfvars.get(f"{module_name}_secrets")
        if not isinstance(secrets, list):
            continue
        for secret in secrets:
            if not isinstance(secret, dict):
                continue
            if str(secret.get("name") or "").strip() != secret_name:
                continue
            if not _mysterybox_version_id_unset(secret.get("version_id")):
                continue
            secret["version_id"] = version_id
            changed = True
    return changed


def _sync_mysterybox_primary_version_ids_to_config(
    config: Any,
    paths: ProjectPaths,
    *,
    initialize: bool = True,
    manifest: Mapping[str, Any] | None = None,
    require_all: bool = True,
) -> bool:
    """Persist first-deploy MysteryBox primary version IDs back to source and generated files."""
    raw_payload: dict[str, Any] | None = None
    source_target_keys: set[tuple[str, str]] = set()
    if paths.config_path.exists():
        loaded_payload = yaml.safe_load(paths.config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded_payload, dict):
            raw_payload = loaded_payload
            source_target_keys = _mysterybox_unset_version_target_keys(raw_payload)

    manifest_payload: dict[str, Any] | None = None
    if manifest is not None:
        manifest_payload = copy.deepcopy(dict(manifest))
    else:
        manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
        if manifest_path.exists():
            try:
                manifest_payload = load_generated_manifest(paths.generated_dir)
            except ValueError:
                manifest_payload = None

    manifest_runtime = None
    manifest_target_keys: set[tuple[str, str]] = set()
    if isinstance(manifest_payload, dict):
        runtime_payload = manifest_payload.get("runtime_config")
        if isinstance(runtime_payload, dict):
            manifest_runtime = runtime_payload
            manifest_target_keys = _mysterybox_unset_version_target_keys(manifest_runtime)

    target_keys = source_target_keys | manifest_target_keys
    if not target_keys:
        return False

    terraform_outputs = terraform_output_json(
        paths.infra_dir,
        extra_env=_terraform_runtime_env(config),
        initialize=initialize,
    )
    version_ids, missing = _mysterybox_primary_version_ids_from_outputs(
        terraform_outputs,
        target_keys,
    )

    if missing:
        if not require_all:
            return False
        raise RuntimeError(
            "Terraform apply completed, but MysteryBox primary version IDs were not "
            "available for config.yaml update: "
            + ", ".join(missing)
            + ". Rerender the generated Terraform bundle so it exports "
            "primary_secret_version_ids, then rerun apply."
        )

    changed = False
    source_changed = False
    if raw_payload is not None:
        source_changed = _set_mysterybox_version_ids_in_payload(raw_payload, version_ids)
        changed = changed or source_changed

    manifest_changed = False
    if manifest_payload is not None and manifest_runtime is not None:
        manifest_changed = _set_mysterybox_version_ids_in_payload(
            manifest_runtime,
            version_ids,
        )
        render_payload = manifest_payload.get("render")
        tfvars = (
            render_payload.get("terraform_tfvars") if isinstance(render_payload, dict) else None
        )
        if isinstance(tfvars, dict):
            manifest_changed = (
                _set_mysterybox_version_ids_in_tfvars(
                    tfvars,
                    runtime_payload=manifest_runtime,
                    version_ids=version_ids,
                )
                or manifest_changed
            )
        changed = changed or manifest_changed

    if not changed:
        return False
    updated_locations: list[str] = []
    if source_changed and raw_payload is not None:
        paths.config_path.write_text(
            yaml.safe_dump(raw_payload, sort_keys=False),
            encoding="utf-8",
        )
        updated_locations.append(str(paths.config_path))
    if manifest_changed and manifest_payload is not None:
        manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _materialize_generated_terraform_tfvars(paths, manifest_payload)
        updated_locations.append(str(manifest_path))
        updated_locations.append(str(paths.infra_dir / "terraform.auto.tfvars.json"))
    console.print("Updated MysteryBox primary version_id values in " + ", ".join(updated_locations))
    return True


def _dedupe_component_output_specs(specs: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for spec in specs:
        key = (
            str(spec.get("component_id", "")).strip(),
            str(spec.get("instance_id", "")).strip(),
            str(spec.get("output_name", "")).strip(),
            str(spec.get("source_ref", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(spec))
    return deduped


def _refresh_flux_after_terraform_outputs(config: Any, paths: ProjectPaths) -> bool:
    """Re-render Flux after Terraform creates outputs consumed by cluster apps."""
    ensure_nfs_csi_app_rows(config)
    required_specs = _dedupe_component_output_specs(
        [
            *_required_runtime_component_output_specs(config),
            *mysterybox_eso_terraform_output_specs(config),
            *nfs_csi_terraform_output_specs(config),
        ]
    )
    if not required_specs:
        return False
    component_output_values = _runtime_component_output_values(
        config,
        paths,
        required_specs=required_specs,
    )
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values=component_output_values,
    )
    render_flux(config, paths, component_output_values=component_output_values)
    console.print("Refreshed Flux manifests with Terraform-created component outputs.")
    return True


def _refresh_mysterybox_eso_flux_after_terraform(config: Any, paths: ProjectPaths) -> bool:
    """Re-render Flux after Terraform creates outputs consumed by cluster apps."""
    return _refresh_flux_after_terraform_outputs(config, paths)


def _mysterybox_eso_credentials_json(credentials: MysteryBoxEsoCredentials) -> str:
    service_account_id = credentials.service_account_id
    auth_public_key_id = credentials.auth_public_key_id
    private_key_pem = credentials.private_key_pem
    if not service_account_id or not auth_public_key_id or not private_key_pem:
        raise RuntimeError("ESO MysteryBox credentials are incomplete.")
    return json.dumps(
        {
            "subject-credentials": {
                "alg": "RS256",
                "private-key": private_key_pem,
                "kid": auth_public_key_id,
                "iss": service_account_id,
                "sub": service_account_id,
            }
        },
        separators=(",", ":"),
        sort_keys=False,
    )


def _mysterybox_eso_credentials_from_json(value: str | None) -> MysteryBoxEsoCredentials | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    subject_credentials = payload.get("subject-credentials")
    if not isinstance(subject_credentials, Mapping):
        return None
    alg = _non_empty_text(subject_credentials.get("alg"))
    service_account_id = _non_empty_text(subject_credentials.get("iss"))
    subject_id = _non_empty_text(subject_credentials.get("sub"))
    auth_public_key_id = _non_empty_text(subject_credentials.get("kid"))
    private_key_pem = _non_empty_text(subject_credentials.get("private-key"))
    if alg != "RS256":
        return None
    if not service_account_id or subject_id != service_account_id:
        return None
    if not auth_public_key_id or not private_key_pem:
        return None
    return MysteryBoxEsoCredentials(
        service_account_id=service_account_id,
        auth_public_key_id=auth_public_key_id,
        private_key_pem=private_key_pem,
    )


def _kubectl_apply_manifest(
    manifest: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    extra_env: dict[str, str] | None,
) -> None:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for ESO MysteryBox runtime secret creation")
    documents = (
        list(manifest)
        if isinstance(manifest, Sequence) and not isinstance(manifest, Mapping)
        else [manifest]
    )
    rendered = yaml.safe_dump_all(
        [dict(document) for document in documents],
        sort_keys=False,
    )
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=rendered,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    stdout = _filter_benign_kubectl_output(completed.stdout or "")
    stderr = _filter_benign_kubectl_output(completed.stderr or "")
    if completed.returncode != 0:
        detail = _first_non_empty_line(stderr or stdout or "")
        raise RuntimeError(f"kubectl apply failed for ESO MysteryBox runtime secret: {detail}")


def _kubectl_read_secret_key(
    *,
    namespace: str,
    name: str,
    key: str,
    extra_env: dict[str, str] | None,
) -> str | None:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for ESO MysteryBox runtime secret inspection")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        ["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    stdout = _filter_benign_kubectl_output(completed.stdout or "")
    stderr = _filter_benign_kubectl_output(completed.stderr or "")
    if completed.returncode != 0:
        detail = _first_non_empty_line(stderr or stdout or "")
        if "not found" in detail.lower() or "notfound" in detail.lower():
            return None
        raise RuntimeError(
            f"kubectl get secret failed for ESO MysteryBox credentials {namespace}/{name}: {detail}"
        )
    try:
        payload = json.loads(stdout)
    except Exception as exc:
        raise RuntimeError(
            f"kubectl returned invalid JSON for ESO MysteryBox credentials {namespace}/{name}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    encoded = _non_empty_text(data.get(key))
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None


def _kubectl_validate_mysterybox_eso_tls(
    *,
    namespace: str,
    api_domain: str,
    extra_env: dict[str, str] | None,
) -> None:
    probe = _kubectl_mysterybox_eso_tls_probe(
        namespace=namespace,
        api_domain=api_domain,
        extra_env=extra_env,
    )
    domain = str(probe.get("api_domain") or "").strip()
    if not domain:
        return
    if not bool(probe.get("passed")):
        detail = str(probe.get("summary") or "").strip()
        raise RuntimeError(
            "ESO MysteryBox Nebius API TLS validation failed from inside the cluster "
            f"for https://{domain}: {detail}"
        )
    summary_lines = [str(line) for line in probe.get("summary_lines", []) if str(line).strip()]
    console.print(
        "[green]Validated ESO MysteryBox Nebius API DNS/egress/TLS[/green] "
        f"from namespace '{namespace}' for https://{domain}."
    )
    if summary_lines:
        console.print("\n".join(summary_lines))


def _kubectl_mysterybox_eso_tls_probe(
    *,
    namespace: str,
    api_domain: str,
    extra_env: dict[str, str] | None,
) -> dict[str, Any]:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for ESO MysteryBox Nebius API TLS validation")
    domain = str(api_domain or "").strip()
    if not domain:
        return {
            "name": "Nebius API TLS",
            "passed": True,
            "api_domain": "",
            "namespace": namespace,
            "summary": "No API domain configured.",
            "summary_lines": [],
        }
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    pod_name = f"nebius-tls-check-{time.time_ns()}-{os.getpid()}".lower()[:63].rstrip("-")
    script = r"""
api_domain="$1"
host="${api_domain%%:*}"
out="$(curl -vvI --connect-timeout 10 --max-time 30 "https://${api_domain}" 2>&1)"
printf "%s\n" "$out" | grep -E "SSL certificate verify ok|issuer:|subjectAltName" || true
printf "%s\n" "$out" | grep -q "SSL certificate verify ok"
printf "%s\n" "$out" | grep -q "subjectAltName: host \"${host}\" matched"
printf "%s\n" "$out" | grep -Eq "HTTP/[0-9.]+ [0-9]"
"""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        [
            "kubectl",
            "-n",
            namespace,
            "run",
            pod_name,
            "--rm",
            "-i",
            "--restart=Never",
            f"--image={_MYSTERYBOX_ESO_TLS_CHECK_IMAGE}",
            "--command",
            "--",
            "sh",
            "-ceu",
            script,
            "--",
            domain,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    stdout = _filter_benign_kubectl_output(completed.stdout or "")
    stderr = _filter_benign_kubectl_output(completed.stderr or "")
    summary_lines: list[str] = []
    seen_summary_lines: set[str] = set()
    for line in (stdout + "\n" + stderr).splitlines():
        if line in seen_summary_lines:
            continue
        if any(
            marker in line
            for marker in (
                "SSL certificate verify ok",
                "issuer:",
                "subjectAltName:",
            )
        ):
            summary_lines.append(line)
            seen_summary_lines.add(line)
    if completed.returncode != 0:
        detail = _first_non_empty_line(stderr or stdout or "") or (
            f"kubectl exited with status {completed.returncode}"
        )
        return {
            "name": "Nebius API TLS",
            "passed": False,
            "api_domain": domain,
            "namespace": namespace,
            "summary": detail,
            "summary_lines": summary_lines,
            "image": _MYSTERYBOX_ESO_TLS_CHECK_IMAGE,
        }
    return {
        "name": "Nebius API TLS",
        "passed": True,
        "api_domain": domain,
        "namespace": namespace,
        "summary": "DNS, egress, public CA trust, hostname verification, and HTTP response succeeded.",
        "summary_lines": summary_lines,
        "image": _MYSTERYBOX_ESO_TLS_CHECK_IMAGE,
    }


def _mysterybox_eso_report_path(spec: Mapping[str, Any], *, inventory_dir: Path) -> Path:
    report_file = str(spec.get("report_file", "") or "").strip()
    if report_file:
        return inventory_dir / report_file
    return inventory_dir / "mysterybox-eso-connectivity-report.json"


def _mysterybox_eso_write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _mysterybox_eso_check(
    name: str,
    *,
    passed: bool,
    summary: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "summary": summary,
        "details": dict(details or {}),
    }


def _mysterybox_eso_kubectl_json(
    args: list[str],
    *,
    extra_env: dict[str, str] | None,
) -> dict[str, Any]:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for ESO MysteryBox connectivity validation")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = _filter_benign_kubectl_output(result.stderr or "").strip()
        stdout = _filter_benign_kubectl_output(result.stdout or "").strip()
        detail = stderr or stdout or f"kubectl exited with status {result.returncode}"
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {detail}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kubectl {' '.join(args)} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"kubectl {' '.join(args)} returned a non-object JSON payload")
    return payload


def _mysterybox_eso_ready_condition(payload: Mapping[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    conditions = status.get("conditions") if isinstance(status, Mapping) else None
    if not isinstance(conditions, list):
        return {}
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if str(condition.get("type") or "").strip() == "Ready":
            return dict(condition)
    return {}


def _mysterybox_eso_ready_check(
    *,
    name: str,
    args: list[str],
    resource_label: str,
    extra_env: dict[str, str] | None,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_check: dict[str, Any] | None = None
    while True:
        try:
            payload = _mysterybox_eso_kubectl_json(args, extra_env=extra_env)
        except Exception as exc:
            last_check = _mysterybox_eso_check(
                name,
                passed=False,
                summary=f"{resource_label} lookup failed: {exc}",
                details={"resource": resource_label},
            )
        else:
            condition = _mysterybox_eso_ready_condition(payload)
            status = str(condition.get("status") or "").strip()
            reason = str(condition.get("reason") or "").strip()
            message = str(condition.get("message") or "").strip()
            passed = status.lower() == "true"
            if passed:
                summary = f"{resource_label} Ready=True ({reason or 'no reason'})"
            else:
                summary = (
                    f"{resource_label} Ready={status or 'missing'} "
                    f"({reason or 'no reason'}): {message or 'no message'}"
                )
            last_check = _mysterybox_eso_check(
                name,
                passed=passed,
                summary=summary,
                details={
                    "resource": resource_label,
                    "ready_status": status,
                    "reason": reason,
                    "message": message,
                    "observed_generation": condition.get("observedGeneration"),
                },
            )
        if bool(last_check.get("passed")) or time.monotonic() >= deadline:
            break
        time.sleep(max(interval_seconds, 0.0))
    if last_check is None:
        last_check = _mysterybox_eso_check(
            name,
            passed=False,
            summary=f"{resource_label} readiness check did not run.",
            details={"resource": resource_label},
        )
    return last_check


_MYSTERYBOX_ESO_LOG_MATCH_RE = re.compile(
    r"(nebius|mysterybox|x509|tls|certificate|unauthorized|permission|denied)",
    re.IGNORECASE,
)
_MYSTERYBOX_ESO_LOG_FAILURE_RE = re.compile(
    r"(x509|certificate|unauthorized|permission|denied|tls.*(?:error|fail)|(?:error|fail).*tls)",
    re.IGNORECASE,
)


def _mysterybox_eso_logs_check(
    *,
    namespace: str,
    extra_env: dict[str, str] | None,
    since_time: datetime | None = None,
) -> dict[str, Any]:
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for ESO MysteryBox controller log validation")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    since_arg = "--since=15m"
    if since_time is not None:
        since_arg = "--since-time=" + since_time.astimezone(UTC).isoformat().replace("+00:00", "Z")
    result = subprocess.run(
        ["kubectl", "-n", namespace, "logs", "deploy/external-secrets", since_arg],
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = _filter_benign_kubectl_output(result.stderr or "").strip()
        stdout = _filter_benign_kubectl_output(result.stdout or "").strip()
        detail = stderr or stdout or f"kubectl exited with status {result.returncode}"
        return _mysterybox_eso_check(
            "ESO controller log scan",
            passed=False,
            summary=f"Could not read external-secrets controller logs: {detail}",
            details={"namespace": namespace},
        )
    matches: list[str] = []
    failures: list[str] = []
    for line in _filter_benign_kubectl_output(result.stdout or "").splitlines():
        if _MYSTERYBOX_ESO_LOG_MATCH_RE.search(line):
            matches.append(line)
        if _MYSTERYBOX_ESO_LOG_FAILURE_RE.search(line):
            failures.append(line)
    passed = not failures
    window_label = "since validation start" if since_time is not None else "in the last 15 minutes"
    summary = (
        f"No ESO controller auth/TLS/permission errors found {window_label}."
        if passed
        else f"Found {len(failures)} ESO controller auth/TLS/permission error log line(s)."
    )
    return _mysterybox_eso_check(
        "ESO controller log scan",
        passed=passed,
        summary=summary,
        details={
            "namespace": namespace,
            "since_time": since_time.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if since_time is not None
            else "",
            "matched_lines": matches[-20:],
            "failure_lines": failures[-20:],
        },
    )


def _run_mysterybox_eso_connectivity_validation(
    spec: Mapping[str, Any],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> Path:
    validation_name = str(spec.get("name") or "").strip() or "ESO MysteryBox connectivity"
    store_name = str(spec.get("store_name") or "").strip()
    api_domain = str(spec.get("api_domain") or "").strip()
    credential_secret = spec.get("credentials_secret")
    if not isinstance(credential_secret, Mapping):
        credential_secret = {}
    credential_namespace = str(credential_secret.get("namespace") or "").strip()
    eso_namespace = str(spec.get("eso_namespace") or "").strip() or credential_namespace
    raw_external_secrets = spec.get("external_secrets")
    external_secrets = [
        dict(item)
        for item in (raw_external_secrets if isinstance(raw_external_secrets, list) else [])
        if isinstance(item, Mapping)
    ]
    if not store_name or not api_domain or not credential_namespace or not eso_namespace:
        raise RuntimeError(f"{validation_name} spec is missing required connection fields")
    validation_started_at = datetime.now(UTC)
    checks: list[dict[str, Any]] = []

    if emit:
        emit(f"Checking ESO MysteryBox API TLS for https://{api_domain}.")
    tls_probe = _kubectl_mysterybox_eso_tls_probe(
        namespace=credential_namespace,
        api_domain=api_domain,
        extra_env=extra_env,
    )
    checks.append(
        _mysterybox_eso_check(
            "Nebius API TLS",
            passed=bool(tls_probe.get("passed")),
            summary=str(tls_probe.get("summary") or "").strip(),
            details=tls_probe,
        )
    )

    if emit:
        emit(f"Checking ClusterSecretStore {store_name}.")
    checks.append(
        _mysterybox_eso_ready_check(
            name="ClusterSecretStore Ready",
            args=["get", "clustersecretstore", store_name, "-o", "json"],
            resource_label=f"ClusterSecretStore/{store_name}",
            extra_env=extra_env,
        )
    )

    for item in external_secrets:
        namespace = str(item.get("namespace") or "").strip()
        name = str(item.get("name") or "").strip()
        if not namespace or not name:
            checks.append(
                _mysterybox_eso_check(
                    "ExternalSecret Ready",
                    passed=False,
                    summary="ExternalSecret validation spec is missing namespace or name.",
                    details={"external_secret": item},
                )
            )
            continue
        if emit:
            emit(f"Checking ExternalSecret {namespace}/{name}.")
        checks.append(
            _mysterybox_eso_ready_check(
                name=f"ExternalSecret Ready ({namespace}/{name})",
                args=["-n", namespace, "get", "externalsecret", name, "-o", "json"],
                resource_label=f"ExternalSecret/{namespace}/{name}",
                extra_env=extra_env,
            )
        )

    if emit:
        emit(f"Scanning ESO controller logs in namespace {eso_namespace}.")
    checks.append(
        _mysterybox_eso_logs_check(
            namespace=eso_namespace,
            extra_env=extra_env,
            since_time=validation_started_at,
        )
    )

    passed = all(bool(check.get("passed")) for check in checks)
    report_path = _mysterybox_eso_report_path(spec, inventory_dir=inventory_dir)
    report = {
        "validation": validation_name,
        "kind": MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
        "target_ref": str(spec.get(TARGET_REF_FIELD) or "").strip(),
        "api_domain": api_domain,
        "store_name": store_name,
        "credentials_secret": dict(credential_secret),
        "eso_namespace": eso_namespace,
        "external_secrets": external_secrets,
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "checks": checks,
    }
    _mysterybox_eso_write_report(report_path, report)
    if not passed:
        failures = [str(check.get("summary")) for check in checks if not bool(check.get("passed"))]
        raise RuntimeError(
            f"{validation_name} failed: " + "; ".join(failures or ["one or more checks failed"])
        )
    return report_path


def run_mysterybox_eso_validations(
    validations: list[dict[str, Any]],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    written_reports: list[Path] = []
    total = len(validations)
    for index, spec in enumerate(validations, start=1):
        kind = str(spec.get("kind") or "").strip()
        name = str(spec.get("name") or "").strip() or kind or f"validation-{index}"
        if emit:
            emit(f"Starting validation {index}/{total}: {name}.")
        if kind == MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND:
            written_reports.append(
                _run_mysterybox_eso_connectivity_validation(
                    spec,
                    inventory_dir=inventory_dir,
                    extra_env=extra_env,
                    emit=emit,
                )
            )
    return written_reports


def _mysterybox_eso_secret_label(*, namespace: str, name: str, key: str) -> str:
    return f"{namespace}/{name}:{key}"


def _apply_mysterybox_eso_credentials_secret(
    *,
    namespace: str,
    name: str,
    key: str,
    credentials: MysteryBoxEsoCredentials,
    extra_env: dict[str, str] | None,
) -> None:
    _kubectl_apply_manifest(
        [
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": namespace},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "metadata": {"name": name, "namespace": namespace},
                "stringData": {key: _mysterybox_eso_credentials_json(credentials)},
            },
        ],
        extra_env=extra_env,
    )


def _ensure_mysterybox_eso_credentials_secret(
    config: Any,
    *,
    spec: Mapping[str, Any],
    extra_env: dict[str, str] | None,
    auto_auth_bootstrap: bool,
    fresh_credentials: MysteryBoxEsoCredentials | None,
) -> MysteryBoxEsoCredentials | None:
    project_id = str(config.client_info.nebius.project_id).strip()
    namespace = str(spec["namespace"]).strip()
    name = str(spec["name"]).strip()
    key = str(spec["key"]).strip()
    label = _mysterybox_eso_secret_label(namespace=namespace, name=name, key=key)

    raw_credentials = _kubectl_read_secret_key(
        namespace=namespace,
        name=name,
        key=key,
        extra_env=extra_env,
    )
    credentials = _mysterybox_eso_credentials_from_json(raw_credentials)
    replacing_stale_credentials = False

    if raw_credentials is not None and credentials is None and not auto_auth_bootstrap:
        raise RuntimeError(
            f"ESO MysteryBox credential Secret {label} exists but does not contain "
            "valid Subject Credentials JSON. Rerun with --auto-auth-bootstrap so cxcli "
            "can create a fresh authorized key and replace the runtime Secret."
        )

    if credentials is not None:
        stale_reasons: list[str] = []
        if auto_auth_bootstrap:
            identity = _ensure_mysterybox_eso_service_account_identity(project_id=project_id)
            if identity.roles_created:
                console.print(
                    "[green]Granted MysteryBox payload viewer role[/green] "
                    f"to {_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME} for ESO."
                )
            if credentials.service_account_id != identity.service_account_id:
                stale_reasons.append(
                    "the Secret references service account "
                    f"'{credentials.service_account_id}', but "
                    f"'{_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME}' is "
                    f"'{identity.service_account_id}'"
                )

        try:
            with _mysterybox_eso_operator_auth_env():
                public_key_exists = auth_public_key_exists(
                    auth_public_key_id=credentials.auth_public_key_id,
                    profile=None,
                    endpoint=None,
                    config_file=None,
                )
            if not public_key_exists:
                stale_reasons.append(
                    "the referenced Nebius authorized public key no longer exists "
                    "or is not accessible"
                )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to verify ESO MysteryBox authorized key for {label}: {exc}"
            ) from exc

        if not stale_reasons:
            console.print(
                f"Reused ESO MysteryBox credential Secret {namespace}/{name} for native provider."
            )
            return fresh_credentials

        if not auto_auth_bootstrap:
            raise RuntimeError(
                f"ESO MysteryBox credential Secret {label} is stale: "
                + "; ".join(stale_reasons)
                + ". Rerun with --auto-auth-bootstrap so cxcli can create a fresh "
                "authorized key and replace the runtime Secret."
            )
        console.print(
            f"{warning_markup('WARNING:', bold=True)} ESO MysteryBox credential Secret "
            f"{namespace}/{name} is stale; replacing it because " + "; ".join(stale_reasons) + "."
        )
        replacing_stale_credentials = True
        credentials = None

    if credentials is None and raw_credentials is None and not auto_auth_bootstrap:
        raise RuntimeError(
            f"ESO MysteryBox credential Secret {label} is missing. Rerun with "
            "--auto-auth-bootstrap so cxcli can create "
            f"'{_MYSTERYBOX_ESO_SERVICE_ACCOUNT_NAME}', upload an authorized public key, "
            "and store Subject Credentials in the cluster Secret."
        )

    if credentials is None:
        if raw_credentials is not None and not replacing_stale_credentials:
            console.print(
                f"{warning_markup('WARNING:', bold=True)} ESO MysteryBox credential Secret "
                f"{namespace}/{name} is invalid; replacing it with fresh Subject Credentials."
            )
        credentials = fresh_credentials or _create_mysterybox_eso_credentials(project_id=project_id)

    _apply_mysterybox_eso_credentials_secret(
        namespace=namespace,
        name=name,
        key=key,
        credentials=credentials,
        extra_env=extra_env,
    )
    console.print(
        f"Ensured ESO MysteryBox credential Secret {namespace}/{name} for native provider."
    )
    return credentials


def _ensure_mysterybox_eso_runtime_before_flux(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str | None = None,
    auto_auth_bootstrap: bool,
) -> None:
    if not mysterybox_eso_enabled(config, target_ref=target_ref):
        return
    specs = mysterybox_eso_runtime_secret_specs(config, target_ref=target_ref)
    fresh_credentials: MysteryBoxEsoCredentials | None = None
    for spec in specs:
        fresh_credentials = _ensure_mysterybox_eso_credentials_secret(
            config,
            spec=spec,
            extra_env=extra_env,
            auto_auth_bootstrap=auto_auth_bootstrap,
            fresh_credentials=fresh_credentials,
        )
    probe_namespace = specs[0]["namespace"] if specs else "external-secrets"
    for api_domain in mysterybox_eso_api_domains(config, target_ref=target_ref):
        _kubectl_validate_mysterybox_eso_tls(
            namespace=probe_namespace,
            api_domain=api_domain,
            extra_env=extra_env,
        )


def _ensure_backend_s3_env_aliases() -> None:
    access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        or os.environ.get("NEBIUS_S3_ACCESS_KEY_ID", "").strip()
    )
    secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
        or os.environ.get("NEBIUS_S3_SECRET_ACCESS_KEY", "").strip()
    )
    if access_key:
        os.environ["AWS_ACCESS_KEY_ID"] = access_key
        os.environ["NEBIUS_S3_ACCESS_KEY_ID"] = access_key
    if secret_key:
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
        os.environ["NEBIUS_S3_SECRET_ACCESS_KEY"] = secret_key


def _ensure_terraform_backend_ready(config: Any, *, auto_auth_bootstrap: bool) -> None:
    _configure_quiet_native_logs()
    _ensure_runtime_auth_material(
        config,
        need_terraform=True,
        auto_bootstrap=auto_auth_bootstrap,
    )
    _ensure_backend_s3_env_aliases()
    settings = backend_settings_from_config(config)
    created = ensure_state_bucket(settings)
    if created:
        console.print(
            "[green]Created Terraform remote state bucket[/green] "
            f"{settings.bucket} in project {settings.project_id}."
        )


def _cleanup_render_terraform_workdir(infra_dir: Path) -> None:
    terraform_dir = infra_dir / ".terraform"
    if terraform_dir.exists():
        shutil.rmtree(terraform_dir, ignore_errors=True)
    for path in (
        infra_dir / "terraform.tfstate",
        infra_dir / "terraform.tfstate.backup",
        infra_dir / ".terraform.tfstate.lock.info",
    ):
        with suppress(FileNotFoundError):
            path.unlink()


def _try_generate_terraform_lock_file(
    config: Any,
    paths: ProjectPaths,
) -> bool:
    try:
        # Render keeps lockfile generation backendless so the canonical generated bundle
        # does not retain local Terraform workdir state.
        terraform_init(paths.infra_dir, backend=False)
    except Exception as exc:
        console.print(
            f"{warning_markup('WARNING:', bold=True)} "
            f"Unable to generate Terraform lock file at {paths.infra_dir / '.terraform.lock.hcl'}: {exc}"
        )
        return False
    finally:
        _cleanup_render_terraform_workdir(paths.infra_dir)
    return (paths.infra_dir / ".terraform.lock.hcl").exists()


def _first_non_empty_line(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return None


def _rendered_module_source_payload(
    config: Any,
    *,
    source_profile: SourceProfile,
) -> list[dict[str, Any]]:
    return [
        {
            "component_id": item.component_id,
            "instance_id": item.instance_id,
            "module_name": item.module_name,
            "source": item.source,
        }
        for item in rendered_module_sources(config, source_profile=source_profile)
    ]


def _generated_bundle_module_sources(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    render = manifest.get("render")
    if not isinstance(render, Mapping):
        raise ValueError("Generated manifest is missing render metadata")

    raw_sources = render.get("module_sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raise ValueError(
            "Generated manifest is missing render.module_sources metadata. "
            "Rerun `nebius-cxcli render <config.yaml>` with the current CLI."
        )

    collected: list[dict[str, str]] = []
    for item in raw_sources:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source", "")).strip()
        if not source:
            continue
        collected.append(
            {
                "component_id": str(item.get("component_id", "")).strip(),
                "instance_id": str(item.get("instance_id", "")).strip(),
                "module_name": str(item.get("module_name", "")).strip(),
                "source": source,
            }
        )
    return collected


def _validate_generated_bundle_portability(
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
) -> None:
    module_sources = _generated_bundle_module_sources(manifest)
    non_portable = [
        item
        for item in module_sources
        if not is_portable_module_source(str(item.get("source", "")))
    ]
    if not non_portable:
        return

    formatted = ", ".join(
        (
            f"{item.get('instance_id') or item['component_id']}={item['source']}"
            if item.get("component_id") or item.get("instance_id")
            else item["source"]
        )
        for item in non_portable
    )
    raise RuntimeError(
        "Generated bundle is not portable; local Terraform module sources are present: "
        f"{formatted}. Rerender with --source-profile portable before committing or using CI."
    )


def _write_generated_runtime_manifest(
    config: Any,
    paths: ProjectPaths,
    *,
    source_profile: SourceProfile,
    quota_report: QuotaReport | None = None,
    output_path: Path | None = None,
    manifest_paths: ProjectPaths | None = None,
) -> Path:
    sources = load_component_sources()
    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    if not tfvars_path.exists():
        raise RuntimeError(
            f"Rendered Terraform inputs file is missing: {tfvars_path}. "
            "Rerun `nebius-cxcli render <config.yaml>`."
        )
    terraform_tfvars = json.loads(tfvars_path.read_text(encoding="utf-8"))
    if not isinstance(terraform_tfvars, Mapping):
        raise RuntimeError(
            f"Rendered Terraform inputs file must contain a JSON object: {tfvars_path}"
        )
    write_kwargs = dict(
        config=config,
        paths=manifest_paths or paths,
        targets=_enabled_deploy_targets(config, manifest_paths or paths),
        required_component_outputs=_required_runtime_component_output_specs(config),
        status_watchers=_enabled_status_watcher_specs(config),
        validations=_deploy_validation_specs(config),
        quota_report=quota_report.to_manifest_dict() if quota_report is not None else None,
        source_profile=source_profile.value,
        module_sources=_rendered_module_source_payload(config, source_profile=source_profile),
        terraform_tfvars=terraform_tfvars,
        flux_version=sources.cli.flux.version,
        terraform_version=sources.cli.terraform.version,
    )
    if output_path is None:
        return write_generated_manifest(**write_kwargs)
    return write_generated_manifest_to_path(output_path, **write_kwargs)


def _active_chart_count(config: Any) -> int:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return 0
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        return 0
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return 0
    return sum(
        1 for item in charts if isinstance(item, Mapping) and bool(item.get("enabled", False))
    )


def _active_chart_count_for_target(config: Any, *, target_ref: str) -> int:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return 0
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        return 0
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return 0
    normalized_target_ref = normalize_component_token(target_ref)
    return sum(
        1
        for item in charts
        if isinstance(item, Mapping)
        and bool(item.get("enabled", False))
        and (app_chart_target_ref(item) or component_instance_id(item)) == normalized_target_ref
    )


def _required_runtime_component_output_specs(config: Any) -> list[dict[str, str]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []

    all_entries = component_entry_lookup()
    required: list[dict[str, str]] = []
    seen_refs: set[str] = set()
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        return []
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return []
    for item in charts:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        chart_id = component_type_id(item)
        if not chart_id:
            continue
        entry = all_entries.get(chart_id)
        if entry is None:
            continue
        for binding in entry.input_bindings:
            source_entry = all_entries.get(binding.source_component_id)
            if source_entry is None:
                continue
            source_output = output_lookup(source_entry).get(binding.source_output_name)
            if source_output is None or source_output.kind != "terraform_output":
                continue
            _source_entry, resolved_source_row, source_instance_id = resolve_input_binding_source(
                payload,
                binding=binding,
            )
            if not source_instance_id:
                continue
            source_ref = component_output_ref(source_instance_id, binding.source_output_name)
            if source_ref in seen_refs:
                continue
            seen_refs.add(source_ref)
            required.append(
                {
                    "component_id": binding.source_component_id,
                    "instance_id": source_instance_id,
                    "output_name": binding.source_output_name,
                    "source_ref": source_ref,
                }
            )
    return required


def _runtime_component_output_values(
    config: Any,
    paths: ProjectPaths,
    *,
    required_specs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    required = (
        required_specs
        if required_specs is not None
        else _required_runtime_component_output_specs(config)
    )
    if not required:
        return {}

    try:
        terraform_outputs = terraform_output_json(
            paths.infra_dir,
            extra_env=_terraform_runtime_env(config),
        )
    except Exception as exc:
        raise RuntimeError(
            "This config declares app input bindings that depend on Terraform module outputs, but those "
            "outputs are not available yet. Run `deploy`, or rerun `render` after Terraform state exists. "
            f"Terraform output lookup failed: {exc}"
        ) from exc
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for spec in required:
        root_output_name = component_output_root_name(
            spec.get("instance_id", spec["component_id"]),
            spec["output_name"],
        )
        output_payload = terraform_outputs.get(root_output_name)
        if not isinstance(output_payload, Mapping) or "value" not in output_payload:
            missing.append(root_output_name)
            continue
        resolved[spec["source_ref"]] = to_plain_data(output_payload["value"])
    if missing:
        raise RuntimeError(
            "Terraform state is missing required rendered root outputs for app input bindings: "
            + ", ".join(sorted(missing))
            + ". Rerender infra and apply Terraform before retrying."
        )
    return resolved


def _requires_flux_terraform_state(config: Any) -> bool:
    if _active_chart_count(config) == 0:
        return False
    return bool(
        _enabled_cluster_handoffs(config) or _required_runtime_component_output_specs(config)
    )


def _enabled_deploy_targets(config: Any, paths: ProjectPaths) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for handoff in _enabled_cluster_handoffs(config):
        target_ref = str(handoff.get("instance_id", "")).strip().lower()
        targets.append(
            {
                **handoff,
                "target_ref": target_ref,
                "flux_dir": str(flux_target_dir(paths, target_ref)),
            }
        )
    return targets


def _manifest_deploy_targets(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    deploy_node = manifest.get("deploy")
    if deploy_node is None:
        return []
    if not isinstance(deploy_node, Mapping):
        raise ValueError("Generated manifest deploy must be a mapping")
    raw_targets = deploy_node.get("targets")
    if raw_targets is None:
        return []
    if not isinstance(raw_targets, list):
        raise ValueError("Generated manifest deploy.targets must be a list")
    return [
        normalize_generated_deploy_target(item, index=index)
        for index, item in enumerate(raw_targets)
    ]


def _manifest_required_component_output_specs(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    deploy_node = manifest.get("deploy")
    if not isinstance(deploy_node, Mapping):
        return []
    raw_specs = deploy_node.get("required_component_outputs")
    if not isinstance(raw_specs, list):
        return []
    specs: list[dict[str, str]] = []
    for item in raw_specs:
        if not isinstance(item, Mapping):
            continue
        specs.append(
            {
                "component_id": str(item.get("component_id", "")).strip().lower(),
                "instance_id": (
                    str(item.get("instance_id", "")).strip().lower()
                    or str(item.get("component_id", "")).strip().lower()
                ),
                "output_name": str(item.get("output_name", "")).strip(),
                "source_ref": str(item.get("source_ref", "")).strip(),
            }
        )
    return [
        item
        for item in specs
        if item["component_id"]
        and item["instance_id"]
        and item["output_name"]
        and item["source_ref"]
    ]


def _manifest_status_watchers(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    deploy_node = manifest.get("deploy")
    if not isinstance(deploy_node, Mapping):
        return []
    raw_watchers = deploy_node.get("status_watchers")
    if not isinstance(raw_watchers, list):
        return []
    watchers: list[dict[str, str]] = []
    for item in raw_watchers:
        if not isinstance(item, Mapping):
            continue
        watchers.append(
            {
                "component_id": str(item.get("component_id", "")).strip().lower(),
                "instance_id": (
                    str(item.get("instance_id", "")).strip().lower()
                    or str(item.get("component_id", "")).strip().lower()
                ),
                "kind": str(item.get("kind", "")).strip().lower(),
                "parent_id": str(item.get("parent_id", "")).strip(),
                "resource_name": str(item.get("resource_name", "")).strip(),
            }
        )
    return [
        item
        for item in watchers
        if item["component_id"]
        and item["instance_id"]
        and item["kind"]
        and item["parent_id"]
        and item["resource_name"]
    ]


def _manifest_deploy_validations(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    deploy_node = manifest.get("deploy")
    if not isinstance(deploy_node, Mapping):
        return []
    raw_validations = deploy_node.get("validations")
    if not isinstance(raw_validations, list):
        return []
    return [dict(item) for item in raw_validations if isinstance(item, Mapping)]


def _manifest_target_flux_dir(
    *,
    paths: ProjectPaths,
    target: Mapping[str, str],
) -> Path:
    manifest_flux_dir = str(target.get("flux_dir", "")).strip()
    if manifest_flux_dir:
        flux_dir_path = Path(manifest_flux_dir)
        if not flux_dir_path.is_absolute():
            return (paths.repo_root / flux_dir_path).resolve()
        return flux_dir_path.resolve()
    return flux_target_dir(paths, str(target.get("target_ref", "")).strip())


def _paths_for_target_flux_dir(paths: ProjectPaths, target: Mapping[str, str]) -> ProjectPaths:
    return replace(paths, flux_dir=_manifest_target_flux_dir(paths=paths, target=target))


def _resolve_selected_deploy_targets(
    manifest: Mapping[str, Any],
    *,
    requested_target_ref: str | None,
    all_targets: bool,
) -> list[dict[str, str]]:
    targets = _manifest_deploy_targets(manifest)
    if requested_target_ref and all_targets:
        raise ValueError("Use either --target or --all-targets, not both.")
    if not targets:
        if requested_target_ref or all_targets:
            raise RuntimeError("No built-in cluster targets are declared in this generated bundle.")
        return []
    if all_targets:
        return targets
    if requested_target_ref:
        normalized_target_ref = normalize_component_token(requested_target_ref)
        for target in targets:
            if str(target.get("target_ref", "")).strip() == normalized_target_ref:
                return [target]
        available = ", ".join(sorted(str(item["target_ref"]) for item in targets))
        raise RuntimeError(
            f"Unknown cluster target '{requested_target_ref}'. Available targets: {available}"
        )
    if len(targets) == 1:
        return targets
    available = ", ".join(sorted(str(item["target_ref"]) for item in targets))
    raise RuntimeError(
        "Multiple cluster targets are declared in this generated bundle: "
        f"{available}. Select one with --target or use --all-targets."
    )


def _filter_validations_for_target(
    validations: list[dict[str, Any]],
    *,
    target_ref: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in validations:
        item_target_ref = normalize_component_token(item.get(TARGET_REF_FIELD))
        if item_target_ref and item_target_ref != normalize_component_token(target_ref):
            continue
        selected.append(item)
    return selected


def _filter_validations_for_target_refs(
    validations: list[dict[str, Any]],
    *,
    target_refs: set[str],
) -> list[dict[str, Any]]:
    normalized_target_refs = {
        normalize_component_token(target_ref) for target_ref in target_refs if target_ref
    }
    if not normalized_target_refs:
        return [dict(item) for item in validations]
    selected: list[dict[str, Any]] = []
    for item in validations:
        item_target_ref = normalize_component_token(item.get(TARGET_REF_FIELD))
        if item_target_ref and item_target_ref not in normalized_target_refs:
            continue
        selected.append(dict(item))
    return selected


_MK8S_GPU_VALIDATION_KINDS = {
    "mk8s_gpu_operator_readiness",
    "mk8s_gpu_visibility",
    "mk8s_nccl",
}
_OBSERVABILITY_VALIDATION_KINDS = {OBSERVABILITY_INGESTION_VALIDATION_KIND}
_MYSTERYBOX_ESO_VALIDATION_KINDS = {MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND}
_DEPLOY_VALIDATION_KINDS = (
    _MK8S_GPU_VALIDATION_KINDS | _OBSERVABILITY_VALIDATION_KINDS | _MYSTERYBOX_ESO_VALIDATION_KINDS
)


def _deploy_validation_specs(config: Any) -> list[dict[str, Any]]:
    return [
        *mk8s_gpu_validation_specs(config),
        *observability_validation_specs(config),
        *mysterybox_eso_validation_specs(config),
    ]


def _run_deploy_validations(
    validations: list[dict[str, Any]],
    *,
    inventory_dir: Path,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None] | None = None,
) -> list[Path]:
    unknown = sorted(
        {
            str(item.get("kind", "") or "").strip()
            for item in validations
            if str(item.get("kind", "") or "").strip() not in _DEPLOY_VALIDATION_KINDS
        }
    )
    if unknown:
        raise RuntimeError(
            "Generated manifest contains unsupported deploy validation kind(s): "
            + ", ".join(unknown)
            + f". Rerender with `nebius-cxcli render {inventory_dir.parent.parent / 'config.yaml'}`."
        )
    written: list[Path] = []
    gpu_validations = [
        item
        for item in validations
        if str(item.get("kind", "") or "").strip() in _MK8S_GPU_VALIDATION_KINDS
    ]
    observability_validations = [
        item
        for item in validations
        if str(item.get("kind", "") or "").strip() in _OBSERVABILITY_VALIDATION_KINDS
    ]
    mysterybox_eso_validations = [
        item
        for item in validations
        if str(item.get("kind", "") or "").strip() in _MYSTERYBOX_ESO_VALIDATION_KINDS
    ]
    if gpu_validations:
        written.extend(
            run_mk8s_gpu_validations(
                gpu_validations,
                inventory_dir=inventory_dir,
                extra_env=extra_env,
                emit=emit,
            )
        )
    if observability_validations:
        written.extend(
            run_observability_validations(
                observability_validations,
                inventory_dir=inventory_dir,
                extra_env=extra_env,
                emit=emit,
            )
        )
    if mysterybox_eso_validations:
        written.extend(
            run_mysterybox_eso_validations(
                mysterybox_eso_validations,
                inventory_dir=inventory_dir,
                extra_env=extra_env,
                emit=emit,
            )
        )
    return written


_DEPLOY_VALIDATION_SKIP_KIND_MAP = {
    "operator-readiness": "mk8s_gpu_operator_readiness",
    "operator_readiness": "mk8s_gpu_operator_readiness",
    "gpu-visibility": "mk8s_gpu_visibility",
    "gpu_visibility": "mk8s_gpu_visibility",
    "nccl": "mk8s_nccl",
    "observability-ingestion": OBSERVABILITY_INGESTION_VALIDATION_KIND,
    "observability_ingestion": OBSERVABILITY_INGESTION_VALIDATION_KIND,
}


def _deploy_validation_skip_labels(kinds: set[str]) -> tuple[str, ...]:
    labels: list[str] = []
    for token, kind in _DEPLOY_VALIDATION_SKIP_KIND_MAP.items():
        if "-" not in token or kind not in kinds or token in labels:
            continue
        labels.append(token)
    return tuple(labels)


def _resolve_deploy_validation_skip_kinds(skip_validation: tuple[str, ...]) -> set[str]:
    resolved: set[str] = set()
    invalid: list[str] = []
    for raw in skip_validation:
        token = str(raw).strip().lower()
        if not token:
            continue
        kind = _DEPLOY_VALIDATION_SKIP_KIND_MAP.get(token)
        if kind is None:
            invalid.append(raw)
            continue
        resolved.add(kind)
    if invalid:
        supported = ", ".join(
            sorted({key for key in _DEPLOY_VALIDATION_SKIP_KIND_MAP if "-" in key})
        )
        raise ValueError(
            "Unsupported --skip-validation value(s): "
            + ", ".join(invalid)
            + f". Supported values: {supported}"
        )
    return resolved


def _filter_deploy_validations(
    validations: list[dict[str, Any]],
    *,
    skip_validations: bool,
    skip_kinds: set[str],
) -> list[dict[str, Any]]:
    if skip_validations:
        return [item for item in validations if bool(item.get("required"))]
    if not skip_kinds:
        return validations
    return [
        item
        for item in validations
        if bool(item.get("required")) or str(item.get("kind", "")).strip() not in skip_kinds
    ]


def _manifest_missing_deploy_validations(manifest: Mapping[str, Any]) -> bool:
    deploy_node = manifest.get("deploy")
    if not isinstance(deploy_node, Mapping):
        return True
    return not isinstance(deploy_node.get("validations"), list)


def _manifest_requires_flux_terraform_state(manifest: Mapping[str, Any]) -> bool:
    return bool(
        _manifest_deploy_targets(manifest) or _manifest_required_component_output_specs(manifest)
    )


def _enabled_cluster_handoffs(config: Any) -> list[dict[str, str]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return []
    components = infra.get("components")
    if not isinstance(components, list):
        return []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    handoffs: list[dict[str, str]] = []
    for item in components:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        instance_id = component_instance_id(item)
        if not instance_id:
            continue
        component_label = component_instance_label(component_id, instance_id)
        entry = entry_by_id.get(component_id)
        if entry is None or entry.handoff is None:
            continue
        access_source_label = _handoff_access_source_label(entry.handoff)
        access_value = _resolve_handoff_access_value(
            payload,
            component_id=component_id,
            instance_id=instance_id,
            handoff=entry.handoff,
        )
        if access_value is _UNRESOLVED:
            raise RuntimeError(
                f"infra component '{component_label}' cluster handoff access source "
                f"'{access_source_label}' could not be resolved from the active config/catalog"
            )
        normalized_access = _normalize_handoff_access_value(
            access_value,
            component_label=component_label,
            source_label=access_source_label,
        )
        handoffs.append(
            {
                "component_id": component_id,
                "instance_id": instance_id,
                "cluster_id_output_name": component_output_root_name(
                    instance_id,
                    entry.handoff.cluster_id_output_name,
                ),
                "component_output_ref": component_output_ref(
                    instance_id,
                    entry.handoff.cluster_id_output_name,
                ),
                "access": normalized_access,
            }
        )
    return handoffs


def _resolve_mapping_path_text(node: Mapping[str, Any], path: str) -> str:
    current: Any = node
    for segment in str(path).split("."):
        token = segment.strip()
        if not token:
            return ""
        if not isinstance(current, Mapping):
            return ""
        current = _resolve_mapping_segment(current, token)
        if current is None:
            return ""
    return str(current).strip() if current is not None else ""


def _status_resource_names_from_value(value: Any) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()

    def _add_name(raw: Any) -> None:
        text = str(raw).strip() if raw is not None else ""
        if not text or text in seen:
            return
        seen.add(text)
        names.append(text)

    def _visit(current: Any) -> None:
        if current is None or isinstance(current, bool):
            return
        if isinstance(current, (str, int, float)):
            _add_name(current)
            return
        if isinstance(current, Mapping):
            direct_name = _resolve_mapping_segment(current, "name")
            if isinstance(direct_name, (Mapping, Sequence)) and not isinstance(direct_name, str):
                direct_name = None
            if direct_name is not None:
                _add_name(direct_name)
                return
            for item in current.values():
                _visit(item)
            return
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            for item in current:
                _visit(item)

    _visit(value)
    return tuple(names)


def _handoff_access_source_label(handoff: Any) -> str:
    if str(getattr(handoff, "access_kind", "")).strip().lower() == "input":
        return str(getattr(handoff, "access_source_path", "")).strip()
    return "<literal>"


def _resolve_handoff_access_value(
    payload: Mapping[str, Any],
    *,
    component_id: str,
    instance_id: str,
    handoff: Any,
) -> Any:
    access_kind = str(getattr(handoff, "access_kind", "")).strip().lower()
    if access_kind == "literal":
        return getattr(handoff, "access_value", None)
    if access_kind != "input":
        return _UNRESOLVED

    _entry, row = resolved_component_row(
        payload,
        component_id=component_id,
        instance_id=instance_id,
    )
    if row is None:
        return _UNRESOLVED

    source_path = str(getattr(handoff, "access_source_path", "")).strip()
    if not source_path:
        return _UNRESOLVED
    value = read_component_path(row, source_path)
    if value is None:
        return _UNRESOLVED
    return to_plain_data(value)


def _normalize_handoff_access_value(
    access_value: Any,
    *,
    component_label: str,
    source_label: str,
) -> str:
    if isinstance(access_value, bool):
        return "external" if access_value else "internal"

    normalized_access = str(access_value).strip().lower()
    if normalized_access in {"external", "public"}:
        return "external"
    if normalized_access in {"internal", "private"}:
        return "internal"
    raise RuntimeError(
        f"infra component '{component_label}' cluster handoff access source "
        f"'{source_label}' resolved to '{access_value}'. "
        "Expected boolean public-endpoint state or one of: external, internal, public, private."
    )


def _private_cluster_handoff_note() -> str:
    return (
        "Using a private MK8s control-plane endpoint for cluster handoff. "
        "Local app deploy/bootstrap/destroy requires this machine to already have a private network path "
        "to the Nebius control-plane endpoint."
    )


def _multi_cluster_handoff_labels(handoffs: list[dict[str, str]]) -> str:
    return ", ".join(
        sorted(
            component_instance_label(
                handoff["component_id"],
                handoff.get("instance_id", handoff["component_id"]),
            )
            for handoff in handoffs
        )
    )


def _multi_cluster_handoff_skip_note(handoffs: list[dict[str, str]]) -> str:
    return (
        "Multiple cluster handoff sources are enabled: "
        f"{_multi_cluster_handoff_labels(handoffs)}. "
        "Terraform infra apply can continue, but cxcli will not run target-specific "
        "Kubernetes work until you select a target. Use --target <target-id> or "
        "--all-targets when Kubernetes access is required."
    )


def _config_uses_private_cluster_handoff(config: Any) -> bool:
    with suppress(Exception):
        for handoff in _enabled_cluster_handoffs(config):
            if str(handoff.get("access", "")).strip().lower() == "internal":
                return True
    return False


def _enabled_status_watcher_specs(config: Any) -> list[dict[str, str]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return []
    components = infra.get("components")
    if not isinstance(components, list):
        return []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    watchers: list[dict[str, str]] = []
    for item in components:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        instance_id = component_instance_id(item)
        if not instance_id:
            continue
        entry = entry_by_id.get(component_id)
        if entry is None or entry.status is None:
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        parent_id = _resolve_mapping_path_text(inputs, entry.status.parent_input)
        resource_names = _status_resource_names_from_value(
            _mapping_path_value(inputs, entry.status.name_input)
        )
        if not parent_id or not resource_names:
            continue
        for resource_name in resource_names:
            watchers.append(
                {
                    "component_id": component_id,
                    "instance_id": instance_id,
                    "kind": entry.status.kind,
                    "parent_id": parent_id,
                    "resource_name": resource_name,
                }
            )
    return watchers


@dataclass(frozen=True)
class _Mk8sKubeconfigSpec:
    cluster_entry_name: str
    user_entry_name: str
    context_name: str
    server: str
    ca_pem: str
    exec_command: str
    exec_args: tuple[str, ...]


def _truthy_env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _should_persist_local_kubeconfig() -> bool:
    if _truthy_env_flag("CI"):
        return False
    override = os.environ.get("NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG", "").strip()
    if override:
        return override.lower() in {"1", "true", "yes"}
    return True


def _runtime_auth_env_available() -> bool:
    if _non_empty_text(os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE")):
        return True
    if _non_empty_text(os.environ.get("NEBIUS_IAM_TOKEN")):
        return True
    return all(
        _non_empty_text(os.environ.get(name))
        for name in (
            "NEBIUS_SA_ID",
            "NEBIUS_AUTH_PUBLIC_KEY_ID",
            "NEBIUS_AUTH_PRIVATE_KEY_FILE",
        )
    )


def _iso8601_utc(value: object | None) -> str | None:
    if not isinstance(value, datetime):
        return None
    timestamp = value.astimezone(UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def _normalize_kube_server(endpoint: str) -> str:
    normalized = _non_empty_text(endpoint)
    if not normalized:
        return ""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", normalized):
        return normalized
    return f"https://{normalized}"


def _mk8s_token_exec_command(
    *,
    project_id: str,
    client_name: str,
    endpoint: str | None,
) -> tuple[str, tuple[str, ...]]:
    args = ["mk8s-token"]
    if project_id:
        args.extend(["--project-id", project_id])
    if client_name:
        args.extend(["--client-name", client_name])
    if endpoint:
        args.extend(["--endpoint", endpoint])
    cli_path = shutil.which("nebius-cxcli")
    if cli_path:
        return cli_path, tuple(args)
    return sys.executable, ("-m", "nebius_cxcli", *args)


def _mk8s_kubeconfig_payload(spec: _Mk8sKubeconfigSpec) -> dict[str, Any]:
    ca_data = base64.b64encode(spec.ca_pem.encode("utf-8")).decode("ascii")
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "preferences": {},
        "clusters": [
            {
                "name": spec.cluster_entry_name,
                "cluster": {
                    "server": spec.server,
                    "certificate-authority-data": ca_data,
                },
            }
        ],
        "users": [
            {
                "name": spec.user_entry_name,
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1",
                        "command": spec.exec_command,
                        "args": list(spec.exec_args),
                        "interactiveMode": "Never",
                        "provideClusterInfo": False,
                    }
                },
            }
        ],
        "contexts": [
            {
                "name": spec.context_name,
                "context": {
                    "cluster": spec.cluster_entry_name,
                    "user": spec.user_entry_name,
                },
            }
        ],
        "current-context": spec.context_name,
    }


def _write_kubeconfig_file(path: Path, spec: _Mk8sKubeconfigSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(_mk8s_kubeconfig_payload(spec), sort_keys=False),
        encoding="utf-8",
    )


def _upsert_named_kubeconfig_entry(
    items: object,
    *,
    entry_name: str,
    replacement: dict[str, Any],
) -> list[Any]:
    rendered = list(items) if isinstance(items, list) else []
    kept = [
        item
        for item in rendered
        if not (isinstance(item, dict) and _non_empty_text(item.get("name")) == entry_name)
    ]
    kept.append(replacement)
    return kept


def _mk8s_cluster_handoff_spec(
    config: Any,
    *,
    cluster_id: str,
    access: str,
) -> _Mk8sKubeconfigSpec:
    try:
        from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, GetClusterRequest
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "Nebius mk8s SDK bindings are required for cluster handoff kubeconfig generation."
        ) from exc

    project_id = str(config.client_info.nebius.project_id).strip()
    client_name = str(config.client_info.client_name).strip()
    endpoint_override = _non_empty_text(os.environ.get("NEBIUS_ENDPOINT")) or None
    sdk = init_nebius_sdk(
        parent_id=project_id or None,
        endpoint=endpoint_override,
        context="MK8s cluster handoff",
    )
    try:
        cluster = ClusterServiceClient(sdk).get(GetClusterRequest(id=cluster_id)).wait()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to resolve MK8s cluster '{cluster_id}' for cluster handoff kubeconfig generation."
        ) from exc
    finally:
        with suppress(Exception):
            sdk.sync_close()

    metadata = getattr(cluster, "metadata", None)
    status = getattr(cluster, "status", None)
    cluster_name = _non_empty_text(getattr(metadata, "name", None)) or cluster_id
    control_plane = getattr(status, "control_plane", None)
    endpoints = getattr(control_plane, "endpoints", None)
    auth = getattr(control_plane, "auth", None)
    endpoint_field = "public_endpoint" if access == "external" else "private_endpoint"
    endpoint_value = _non_empty_text(getattr(endpoints, endpoint_field, None))
    if not endpoint_value:
        raise RuntimeError(
            f"MK8s cluster '{cluster_name}' does not expose a usable {access} endpoint yet."
        )
    ca_pem = _non_empty_text(getattr(auth, "cluster_ca_certificate", None))
    if not ca_pem:
        raise RuntimeError(
            f"MK8s cluster '{cluster_name}' did not return a cluster CA certificate for handoff."
        )

    cluster_name_token = _runtime_auth_cache_segment(cluster_name, fallback="cluster")
    cluster_id_token = _runtime_auth_cache_segment(cluster_id, fallback="cluster-id")
    access_token = _runtime_auth_cache_segment(access, fallback="access")
    entry_base = f"nebius-{cluster_name_token}-{cluster_id_token}-{access_token}"
    exec_command, exec_args = _mk8s_token_exec_command(
        project_id=project_id,
        client_name=client_name,
        endpoint=endpoint_override,
    )
    return _Mk8sKubeconfigSpec(
        cluster_entry_name=f"{entry_base}-cluster",
        user_entry_name=f"{entry_base}-user",
        context_name=entry_base,
        server=_normalize_kube_server(endpoint_value),
        ca_pem=ca_pem,
        exec_command=exec_command,
        exec_args=exec_args,
    )


def _persist_cluster_handoff_kubeconfig(
    *,
    spec: _Mk8sKubeconfigSpec,
    set_current_context: bool = True,
) -> Path | None:
    if not _should_persist_local_kubeconfig():
        return None

    local_kubeconfig = Path.home().expanduser() / ".kube" / "config"
    local_kubeconfig.parent.mkdir(parents=True, exist_ok=True)

    try:
        if local_kubeconfig.exists():
            loaded = yaml.safe_load(local_kubeconfig.read_text(encoding="utf-8"))
            if loaded is None:
                payload: dict[str, Any] = {}
            elif isinstance(loaded, dict):
                payload = copy.deepcopy(loaded)
            else:
                raise RuntimeError(
                    f"Existing kubeconfig at {local_kubeconfig} is not a YAML mapping."
                )
        else:
            payload = {}

        rendered = _mk8s_kubeconfig_payload(spec)
        payload["apiVersion"] = "v1"
        payload["kind"] = "Config"
        if not isinstance(payload.get("preferences"), dict):
            payload["preferences"] = {}
        payload["clusters"] = _upsert_named_kubeconfig_entry(
            payload.get("clusters"),
            entry_name=spec.cluster_entry_name,
            replacement=rendered["clusters"][0],
        )
        payload["users"] = _upsert_named_kubeconfig_entry(
            payload.get("users"),
            entry_name=spec.user_entry_name,
            replacement=rendered["users"][0],
        )
        payload["contexts"] = _upsert_named_kubeconfig_entry(
            payload.get("contexts"),
            entry_name=spec.context_name,
            replacement=rendered["contexts"][0],
        )
        existing_current_context = payload.get("current-context")
        if (
            set_current_context
            or not isinstance(existing_current_context, str)
            or not existing_current_context.strip()
        ):
            payload["current-context"] = spec.context_name
        local_kubeconfig.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(f"{warning_markup('WARNING:', bold=True)} {exc}")
        return None

    console.print(f"Updated local kubeconfig at {local_kubeconfig}")
    return local_kubeconfig


def _prepare_cluster_handoff_kube_env(
    config: Any,
    paths: ProjectPaths,
    *,
    stack: ExitStack,
    target: Mapping[str, str] | None = None,
    persist_local_kubeconfig: bool = True,
    set_current_context: bool = True,
) -> dict[str, str] | None:
    resolved_target = target
    if resolved_target is None:
        handoffs = _enabled_cluster_handoffs(config)
        if len(handoffs) > 1:
            raise RuntimeError(
                "Multiple handoff-capable infra components are enabled for this run: "
                f"{_multi_cluster_handoff_labels(handoffs)}. Enable only one cluster handoff source "
                "before running this command."
            )
        resolved_target = handoffs[0] if handoffs else None
    if not resolved_target:
        return None

    cluster_id = terraform_output_raw(
        paths.infra_dir,
        str(resolved_target["cluster_id_output_name"]),
        extra_env=_terraform_runtime_env(config),
    )
    if not cluster_id:
        raise RuntimeError(
            f"Terraform output `{resolved_target['cluster_id_output_name']}` is empty. The rendered Terraform root must expose "
            "the cluster ID required for local cluster handoff kubeconfig generation."
        )

    if not _runtime_auth_env_available():
        project_id = str(config.client_info.nebius.project_id).strip()
        client_name = str(config.client_info.client_name).strip()
        _runtime_auth_cache_load(project_id=project_id, client_name=client_name)
    spec = _mk8s_cluster_handoff_spec(
        config,
        cluster_id=cluster_id,
        access=str(resolved_target["access"]),
    )
    if str(resolved_target["access"]) == "internal":
        console.print(f"[yellow]NOTE:[/yellow] {_private_cluster_handoff_note()}")
    kube_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="nebius-cxcli-kube-")))
    kubeconfig_path = kube_root / "config"
    _write_kubeconfig_file(kubeconfig_path, spec)
    if persist_local_kubeconfig:
        _persist_cluster_handoff_kubeconfig(
            spec=spec,
            set_current_context=set_current_context,
        )
    return {
        "KUBECONFIG": str(kubeconfig_path),
        CLUSTER_HANDOFF_ACCESS_ENV: str(resolved_target["access"]),
        GRAFANA_TARGET_CLUSTER_ID_ENV: cluster_id,
        GRAFANA_TARGET_KUBE_CONTEXT_ENV: spec.context_name,
    }


def _run_post_flux_kubectl(
    cmd: list[str],
    *,
    env: Mapping[str, str],
    timeout: int = 300,
    retries: int = 0,
    retry_delay_seconds: float = 5.0,
    retry_stderr_markers: Sequence[str] = (),
) -> None:
    max_attempts = max(1, retries + 1)
    for attempt in range(1, max_attempts + 1):
        completed = subprocess.run(
            cmd,
            env=dict(env),
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        stdout = _filter_benign_kubectl_output(completed.stdout or "")
        stderr = _filter_benign_kubectl_output(completed.stderr or "")
        if stdout:
            sys.stdout.write(stdout + ("\n" if not stdout.endswith("\n") else ""))
        if stderr:
            sys.stderr.write(stderr + ("\n" if not stderr.endswith("\n") else ""))
        if completed.returncode == 0:
            return
        failure_text = "\n".join(
            part
            for part in (
                completed.stdout or "",
                completed.stderr or "",
                stdout,
                stderr,
            )
            if part
        ).lower()
        should_retry = attempt < max_attempts and any(
            marker.lower() in failure_text for marker in retry_stderr_markers
        )
        if should_retry:
            time.sleep(retry_delay_seconds)
            continue
        raise subprocess.CalledProcessError(
            completed.returncode,
            cmd,
            output=stdout,
            stderr=stderr,
        )


def _write_post_flux_docs(path: Path, docs: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        yaml.safe_dump_all(
            [dict(doc) for doc in docs],
            explicit_start=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _coerce_priority_class_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _rendered_priority_class_value(doc: Mapping[str, Any]) -> int | None:
    if doc.get("apiVersion") != "scheduling.k8s.io/v1" or doc.get("kind") != "PriorityClass":
        return None
    return _coerce_priority_class_value(doc.get("value"))


def _current_priority_class_value(name: str, *, env: Mapping[str, str]) -> int | None:
    completed = subprocess.run(
        ["kubectl", "get", "priorityclass", name, "-o", "jsonpath={.value}"],
        env=dict(env),
        timeout=60,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return _coerce_priority_class_value(completed.stdout)


def _replace_changed_priority_classes(
    docs: Sequence[Mapping[str, Any]],
    *,
    env: Mapping[str, str],
) -> None:
    for doc in docs:
        desired_value = _rendered_priority_class_value(doc)
        if desired_value is None:
            continue
        name_namespace = _metadata_name_namespace(doc)
        if name_namespace is None:
            continue
        name, _namespace = name_namespace
        current_value = _current_priority_class_value(name, env=env)
        if current_value is None or current_value == desired_value:
            continue
        console.print(
            f"Replacing PriorityClass {name}: immutable value changed "
            f"from {current_value} to {desired_value}."
        )
        _run_post_flux_kubectl(
            ["kubectl", "delete", "priorityclass", name, "--ignore-not-found=true"],
            env=env,
            timeout=120,
        )


def _crd_resource_arg(doc: Mapping[str, Any]) -> str | None:
    metadata = doc.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return f"customresourcedefinition.apiextensions.k8s.io/{name}"


def _metadata_name_namespace(doc: Mapping[str, Any]) -> tuple[str, str] | None:
    metadata = doc.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    namespace = metadata.get("namespace")
    return name.strip(), namespace.strip() if isinstance(namespace, str) else ""


def _as_nonempty_str(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _is_post_flux_custom_resource(doc: Mapping[str, Any]) -> bool:
    api_version = doc.get("apiVersion")
    if not isinstance(api_version, str) or "/" not in api_version:
        return False
    group, _version = api_version.split("/", maxsplit=1)
    return group == "slurm.nebius.ai"


def _post_flux_custom_resource_priority(doc: Mapping[str, Any]) -> int:
    kind = str(doc.get("kind", "")).strip()
    return {
        "SlurmCluster": 10,
        "NodeConfigurator": 20,
        "NodeSet": 30,
        "NodeSetPowerState": 40,
        "JailedConfig": 50,
        "ActiveCheck": 60,
    }.get(kind, 100)


def _metadata_labels(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = doc.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    labels = metadata.get("labels")
    return labels if isinstance(labels, Mapping) else {}


def _delete_stale_nodeconfigurators(
    docs: Sequence[Mapping[str, Any]],
    *,
    env: Mapping[str, str],
) -> None:
    desired_by_scope: dict[tuple[str, str], set[str]] = {}
    deleted_stale = False
    for doc in docs:
        if doc.get("kind") != "NodeConfigurator":
            continue
        api_version = _as_nonempty_str(doc.get("apiVersion"))
        if api_version not in {"slurm.nebius.ai/v1alpha1", "slurm.nebius.ai/v1"}:
            continue
        name_namespace = _metadata_name_namespace(doc)
        if name_namespace is None:
            continue
        name, namespace = name_namespace
        instance = _as_nonempty_str(_metadata_labels(doc).get("app.kubernetes.io/instance"))
        if not instance:
            continue
        desired_by_scope.setdefault((namespace or "default", instance), set()).add(name)
    for (namespace, instance), desired_names in sorted(desired_by_scope.items()):
        completed = subprocess.run(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "nodeconfigurators.slurm.nebius.ai",
                "-l",
                f"app.kubernetes.io/instance={instance}",
                "-o",
                "json",
            ],
            env=dict(env),
            timeout=60,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            continue
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            continue
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name_namespace = _metadata_name_namespace(item)
            if name_namespace is None:
                continue
            existing_name, _existing_namespace = name_namespace
            if existing_name in desired_names:
                continue
            console.print(
                f"Deleting stale NodeConfigurator {namespace}/{existing_name} "
                f"for Helm instance {instance}."
            )
            _run_post_flux_kubectl(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "delete",
                    "nodeconfigurators.slurm.nebius.ai",
                    existing_name,
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                env=env,
                timeout=120,
            )
            deleted_stale = True
    if not deleted_stale:
        return
    for (namespace, _instance), desired_names in sorted(desired_by_scope.items()):
        for desired_name in sorted(desired_names):
            _run_post_flux_kubectl(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "delete",
                    "pod",
                    "-l",
                    (
                        "app.kubernetes.io/component=node-configurator,"
                        f"app.kubernetes.io/instance={desired_name}"
                    ),
                    "--ignore-not-found=true",
                    "--wait=false",
                ],
                env=env,
                timeout=120,
            )


def _post_flux_webhook_service_refs(
    docs: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    refs: set[tuple[str, str]] = set()
    for doc in docs:
        if doc.get("apiVersion") != "admissionregistration.k8s.io/v1":
            continue
        if doc.get("kind") not in {
            "MutatingWebhookConfiguration",
            "ValidatingWebhookConfiguration",
        }:
            continue
        webhooks = doc.get("webhooks")
        if not isinstance(webhooks, list):
            continue
        for webhook in webhooks:
            if not isinstance(webhook, Mapping):
                continue
            client_config = webhook.get("clientConfig")
            if not isinstance(client_config, Mapping):
                continue
            service = client_config.get("service")
            if not isinstance(service, Mapping):
                continue
            name = service.get("name")
            namespace = service.get("namespace")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(namespace, str) or not namespace.strip():
                continue
            refs.add((namespace.strip(), name.strip()))
    return tuple(sorted(refs))


def _wait_for_post_flux_webhook_services(
    docs: Sequence[Mapping[str, Any]],
    *,
    env: Mapping[str, str],
) -> None:
    for namespace, name in _post_flux_webhook_service_refs(docs):
        _run_post_flux_kubectl(
            [
                "kubectl",
                "-n",
                namespace,
                "wait",
                "--for=jsonpath={.subsets[0].addresses[0].ip}",
                "--timeout=120s",
                f"endpoints/{name}",
            ],
            env=env,
            timeout=150,
            retries=2,
            retry_delay_seconds=5.0,
            retry_stderr_markers=_POST_FLUX_WEBHOOK_TRANSIENT_MARKERS,
        )


def _wait_for_post_flux_deployments(
    docs: Sequence[Mapping[str, Any]],
    *,
    env: Mapping[str, str],
) -> None:
    for doc in docs:
        if doc.get("apiVersion") != "apps/v1" or doc.get("kind") != "Deployment":
            continue
        name_namespace = _metadata_name_namespace(doc)
        if name_namespace is None:
            continue
        name, namespace = name_namespace
        cmd = ["kubectl"]
        if namespace:
            cmd.extend(["-n", namespace])
        cmd.extend(["rollout", "status", f"deployment/{name}", "--timeout=180s"])
        _run_post_flux_kubectl(cmd, env=env, timeout=240)


def _apply_post_flux_manifest(manifest_path: Path, *, env: Mapping[str, str]) -> None:
    docs = [
        doc
        for doc in yaml.safe_load_all(manifest_path.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]
    if not docs:
        return
    crd_docs = [
        doc
        for doc in docs
        if doc.get("apiVersion") == "apiextensions.k8s.io/v1"
        and doc.get("kind") == "CustomResourceDefinition"
    ]
    non_crd_docs = [doc for doc in docs if doc not in crd_docs]
    custom_resource_docs = [doc for doc in non_crd_docs if _is_post_flux_custom_resource(doc)]
    base_resource_docs = [doc for doc in non_crd_docs if doc not in custom_resource_docs]
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-post-flux-") as temp_dir:
        temp_path = Path(temp_dir)
        if crd_docs:
            crd_path = temp_path / f"{manifest_path.stem}-crds.yaml"
            _write_post_flux_docs(crd_path, crd_docs)
            _run_post_flux_kubectl(
                [
                    "kubectl",
                    "apply",
                    "--server-side",
                    "--force-conflicts",
                    "-f",
                    str(crd_path),
                ],
                env=env,
            )
            crd_args = [
                resource_arg for doc in crd_docs if (resource_arg := _crd_resource_arg(doc))
            ]
            if crd_args:
                _run_post_flux_kubectl(
                    [
                        "kubectl",
                        "wait",
                        "--for=condition=Established",
                        "--timeout=120s",
                        *crd_args,
                    ],
                    env=env,
                    timeout=180,
                )
        if base_resource_docs:
            resource_path = temp_path / f"{manifest_path.stem}-resources.yaml"
            _write_post_flux_docs(resource_path, base_resource_docs)
            _replace_changed_priority_classes(base_resource_docs, env=env)
            _run_post_flux_kubectl(
                [
                    "kubectl",
                    "apply",
                    "--server-side",
                    "--force-conflicts",
                    "-f",
                    str(resource_path),
                ],
                env=env,
            )
            _wait_for_post_flux_deployments(base_resource_docs, env=env)
            _wait_for_post_flux_webhook_services(base_resource_docs, env=env)
        if custom_resource_docs:
            _delete_stale_nodeconfigurators(custom_resource_docs, env=env)
            custom_docs_by_priority: dict[int, list[Mapping[str, Any]]] = {}
            for doc in custom_resource_docs:
                custom_docs_by_priority.setdefault(
                    _post_flux_custom_resource_priority(doc),
                    [],
                ).append(doc)
            for priority in sorted(custom_docs_by_priority):
                custom_resource_path = (
                    temp_path / f"{manifest_path.stem}-custom-resources-{priority}.yaml"
                )
                _write_post_flux_docs(custom_resource_path, custom_docs_by_priority[priority])
                _run_post_flux_kubectl(
                    [
                        "kubectl",
                        "apply",
                        "--server-side",
                        "--force-conflicts",
                        "-f",
                        str(custom_resource_path),
                    ],
                    env=env,
                    retries=5,
                    retry_delay_seconds=5.0,
                    retry_stderr_markers=_POST_FLUX_WEBHOOK_TRANSIENT_MARKERS,
                )


def _apply_rendered_flux(paths: ProjectPaths, *, extra_env: dict[str, str] | None = None) -> None:
    """Apply rendered Flux manifests in local deploy mode."""
    if not flux_dir_has_rendered_resources(paths.flux_dir):
        console.print("No rendered Flux resources are present; skipping local Flux apply.")
        return
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for `deploy` but was not found in PATH")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    with console.status(
        "[cyan]Preparing Flux deployment...[/cyan]",
        spinner="dots",
    ) as status:
        last_phase = ""

        def _set_phase(message: str) -> None:
            nonlocal last_phase
            status.update(message)
            if not _console_is_terminal() and message != last_phase:
                console.print(message)
            last_phase = message

        _set_phase("[cyan]Checking target Kubernetes cluster reachability...[/cyan]")
        cluster_check = subprocess.run(
            ["kubectl", "cluster-info"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if cluster_check.returncode != 0:
            detail = _first_non_empty_line(cluster_check.stderr or cluster_check.stdout or "")
            message = "kubectl could not reach the target Kubernetes cluster for local deploy"
            if detail:
                message = f"{message}: {detail}"
            guidance = cluster_handoff_reachability_guidance(
                extra_env=extra_env,
                action="local deploy",
            )
            if guidance:
                message = f"{message}\n{guidance}"
            raise RuntimeError(message)

        flux_installed = flux_controllers_installed(extra_env=extra_env) and flux_crds_installed(
            extra_env=extra_env
        )
        if not flux_installed:
            _set_phase("[cyan]Installing Flux controllers into the target cluster...[/cyan]")
            manifest_url = install_flux_controllers(extra_env=extra_env)
            console.print(f"Installed Flux controllers in the target cluster from {manifest_url}")
        with tempfile.TemporaryDirectory(prefix="nebius-cxcli-kubectl-") as cache_dir:
            cache_path = Path(cache_dir)
            _set_phase("[cyan]Waiting for Flux resource APIs to become discoverable...[/cyan]")
            wait_for_flux_resource_apis(paths, extra_env=extra_env, cache_dir=cache_path)

            # Local deploy mode does not require a Git repository; apply generated manifests directly.
            _set_phase("[cyan]Applying rendered Flux manifests to the target cluster...[/cyan]")
            completed = subprocess.run(
                ["kubectl", "--cache-dir", str(cache_path), "apply", "-k", str(paths.flux_dir)],
                env=env,
                timeout=1800,
                capture_output=True,
                text=True,
            )
            stdout = _filter_benign_kubectl_output(completed.stdout or "")
            stderr = _filter_benign_kubectl_output(completed.stderr or "")
            if stdout:
                sys.stdout.write(stdout + ("\n" if not stdout.endswith("\n") else ""))
            if stderr:
                sys.stderr.write(stderr + ("\n" if not stderr.endswith("\n") else ""))
            if completed.returncode != 0:
                raise subprocess.CalledProcessError(
                    completed.returncode,
                    ["kubectl", "--cache-dir", str(cache_path), "apply", "-k", str(paths.flux_dir)],
                    output=stdout,
                    stderr=stderr,
                )
        _set_phase("[cyan]Waiting for rendered Flux resources to become Ready...[/cyan]")
        wait_for_rendered_flux_resources(
            paths,
            extra_env=extra_env,
            emit=lambda message: console.print(message),
        )
        post_flux_manifests = sorted(paths.flux_dir.glob("post-flux-*.yaml"))
        if post_flux_manifests:
            _set_phase("[cyan]Applying post-Flux manifests to the target cluster...[/cyan]")
        for manifest_path in post_flux_manifests:
            _apply_post_flux_manifest(manifest_path, env=env)


def _node_readiness_summary(*, extra_env: dict[str, str]) -> tuple[bool, str]:
    env = os.environ.copy()
    env.update(extra_env)
    result = subprocess.run(
        ["kubectl", "get", "nodes", "-o", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (
            _first_non_empty_line(result.stderr or result.stdout or "")
            or "kubectl get nodes failed"
        )
        return False, detail

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False, "kubectl returned unreadable node status payload"

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return False, "nodes 0/0 Ready; waiting for node registration"

    total = 0
    ready = 0
    summaries: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        name = ""
        if isinstance(metadata, Mapping):
            name = str(metadata.get("name", "")).strip()
        conditions = status.get("conditions") if isinstance(status, Mapping) else None
        ready_condition = False
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, Mapping):
                    continue
                if str(condition.get("type", "")).strip() != "Ready":
                    continue
                ready_condition = str(condition.get("status", "")).strip().lower() == "true"
                break
        total += 1
        if ready_condition:
            ready += 1
        label = name or f"node-{total}"
        summaries.append(f"{label}:{'Ready' if ready_condition else 'NotReady'}")

    if total == 0:
        return False, "nodes 0/0 Ready; waiting for node registration"

    summary = f"nodes {ready}/{total} Ready"
    if summaries:
        detail = ", ".join(summaries[:3])
        if len(summaries) > 3:
            detail += f", +{len(summaries) - 3} more"
        summary = f"{summary}; {detail}"
    return ready == total, summary


def _wait_for_cluster_nodes_ready(
    *,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None],
    timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 10.0,
) -> None:
    if not extra_env or not extra_env.get("KUBECONFIG"):
        return

    started_at = time.monotonic()
    ready, summary = _node_readiness_summary(extra_env=extra_env)
    initial_message = f"[bold white]Kubernetes[/bold white] [dim][0s][/dim] {escape(summary)}"
    if ready:
        emit(f"{initial_message}; already Ready, continuing with Flux deployment.")
        return

    emit("Target Kubernetes nodes are not Ready yet; waiting before Flux deployment.")
    emit(initial_message)
    last_summary = summary
    last_emit_at = started_at
    repeat_interval_seconds = 60.0
    while True:
        time.sleep(poll_interval_seconds)
        ready, summary = _node_readiness_summary(extra_env=extra_env)
        elapsed = int(max(0.0, time.monotonic() - started_at))
        message = f"[bold white]Kubernetes[/bold white] [dim][{elapsed}s][/dim] {escape(summary)}"
        now = time.monotonic()
        if ready:
            emit(message)
            return
        if summary != last_summary or (now - last_emit_at) >= repeat_interval_seconds:
            emit(message)
            last_summary = summary
            last_emit_at = now
        if (now - started_at) >= timeout_seconds:
            raise RuntimeError(
                "Target Kubernetes cluster nodes did not become Ready before Flux deployment. "
                f"Last known status: {summary}"
            )


def _report_cluster_nodes_status(
    *,
    extra_env: dict[str, str] | None,
    emit: Callable[[str], None],
) -> None:
    if not extra_env or not extra_env.get("KUBECONFIG"):
        return
    ready, summary = _node_readiness_summary(extra_env=extra_env)
    message = f"[bold white]Kubernetes[/bold white] {escape(summary)}"
    if ready:
        emit(f"{message}; proceeding with in-cluster deployment.")
        return
    emit(
        f"{message}; proceeding without waiting for every node because Flux and validation checks "
        "report live in-cluster progress."
    )


def _reconcile_observability_gpu_node_labels(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
) -> None:
    if not extra_env or not extra_env.get("KUBECONFIG"):
        return
    policy = (
        observability_gpu_node_label_reconciliation(config, target_ref=target_ref)
        if target_ref
        else observability_gpu_node_label_reconciliation(config)
    )
    if not policy.enabled:
        return
    selector = ",".join(f"{key}={value}" for key, value in policy.selector)
    label_args = [f"{key}={value}" for key, value in policy.labels]
    if not selector or not label_args:
        return
    env = os.environ.copy()
    env.update(extra_env)
    result = subprocess.run(
        ["kubectl", "label", "nodes", "-l", selector, *label_args, "--overwrite"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (
            _first_non_empty_line(result.stderr or result.stdout or "")
            or "kubectl node label reconciliation failed"
        )
        raise RuntimeError(f"Observability GPU node-label reconciliation failed: {detail}")
    console.print(f"Reconciled observability GPU node labels for selector `{escape(selector)}`.")


def _ensure_grafana_runtime_before_flux(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
) -> None:
    if not grafana_enabled_for_target(config, target_ref=target_ref):
        return
    ensure_grafana_runtime_secrets(
        config,
        extra_env=extra_env,
        target_ref=target_ref,
        emit=lambda message: console.print(message),
    )


def _ensure_soperator_notifier_runtime_before_flux(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
) -> None:
    if not soperator_notifier_enabled_for_target(config, target_ref=target_ref):
        return
    ensure_soperator_notifier_runtime_secrets(
        config,
        extra_env=extra_env,
        target_ref=target_ref,
        prompt=_console_is_terminal(),
        emit=lambda message: console.print(message),
    )


def _ensure_soperator_backup_runtime_before_flux(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
) -> None:
    if not soperator_backup_enabled_for_target(config, target_ref=target_ref):
        return
    ensure_soperator_backup_runtime_secrets(
        config,
        extra_env=extra_env,
        target_ref=target_ref,
        prompt=_console_is_terminal(),
        emit=lambda message: console.print(message),
    )


def _collect_grafana_status_after_flux(
    config: Any,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
    timeout_seconds: float = GRAFANA_STATUS_TIMEOUT_SECONDS,
    poll_interval_seconds: float = GRAFANA_STATUS_POLL_INTERVAL_SECONDS,
) -> tuple[dict[str, Any], ...]:
    if not grafana_enabled_for_target(config, target_ref=target_ref):
        return ()
    target_label = target_ref or "current cluster"
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_statuses: tuple[dict[str, Any], ...] = ()
    last_error: Exception | None = None
    announced_wait = False
    while True:
        try:
            statuses = collect_grafana_runtime_status(
                config,
                extra_env=extra_env,
                target_ref=target_ref,
            )
            if statuses:
                last_statuses = statuses
                last_error = None
            if statuses and all(str(status.get("base_url") or "").strip() for status in statuses):
                return statuses
        except Exception as exc:
            last_error = exc

        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            if last_statuses:
                console.print(
                    f"{warning_markup('WARNING:', bold=True)} Grafana URL for {target_label} "
                    "is still pending; deploy-report.md will keep pending links until "
                    "a later deploy or flux apply can read the Gateway/LoadBalancer status."
                )
                return last_statuses
            detail = f": {last_error}" if last_error is not None else ""
            console.print(
                f"{warning_markup('WARNING:', bold=True)} Failed to resolve Grafana URL "
                f"for {target_label}{detail}"
            )
            return ()

        if not announced_wait:
            console.print(f"Waiting for Grafana Gateway/LoadBalancer address for {target_label}...")
            announced_wait = True
        time.sleep(min(max(0.1, poll_interval_seconds), remaining))


@dataclass(frozen=True)
class DeployRunSummary:
    validation_report: DeployValidationReport | None = None
    gitops_bootstrap_commands: tuple[str, ...] = ()


def _deploy_generated_artifacts(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    auto_auth_bootstrap: bool,
    skip_validations: bool,
    skip_validation_kinds: set[str],
    requested_target_ref: str | None = None,
    all_targets: bool = False,
) -> DeployRunSummary:
    """Deploy an existing generated artifact bundle without rerendering it."""
    mysterybox_payload_env = _run_deploy_preflight(
        config,
        paths,
        auto_auth_bootstrap=auto_auth_bootstrap,
        manifest=manifest,
    )
    if _manifest_missing_deploy_validations(manifest):
        raise RuntimeError(
            "Generated manifest is missing deploy.validations metadata. "
            f"Rerender with `nebius-cxcli render {paths.config_path}` before deploy."
        )
    runtime_payload = manifest.get("runtime_config")
    if isinstance(runtime_payload, Mapping):
        _print_mk8s_gpu_validation_warnings(runtime_config_from_manifest(manifest))
    status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(config)
    declared_validations = _manifest_deploy_validations(manifest)
    deploy_validations = _filter_deploy_validations(
        declared_validations,
        skip_validations=skip_validations,
        skip_kinds=skip_validation_kinds,
    )
    has_enabled_app_charts = _active_chart_count(config) > 0
    manifest_targets = _manifest_deploy_targets(manifest)
    if not manifest_targets:
        selected_targets: list[dict[str, str]] = []
    elif (
        not has_enabled_app_charts
        and not deploy_validations
        and not requested_target_ref
        and not all_targets
    ):
        # Infra-only multi-target deploys do not need one execution target. Refresh every local
        # kubeconfig handoff so operators can switch contexts after Terraform finishes.
        selected_targets = manifest_targets
    else:
        selected_targets = _resolve_selected_deploy_targets(
            manifest,
            requested_target_ref=requested_target_ref,
            all_targets=all_targets,
        )
    report_validations = [dict(item) for item in declared_validations]
    if manifest_targets and selected_targets and len(selected_targets) < len(manifest_targets):
        report_validations = _filter_validations_for_target_refs(
            declared_validations,
            target_refs={str(target["target_ref"]) for target in selected_targets},
        )
    clear_deploy_validation_artifacts(
        declared_validations,
        inventory_dir=paths.inventory_dir,
    )
    gitops_bootstrap_commands: list[str] = []
    apply_kwargs: dict[str, Any] = {"initialize": False}
    if status_watchers:
        apply_kwargs["status_watchers"] = status_watchers
    apply_kwargs["run_mk8s_preflight"] = False
    if mysterybox_payload_env:
        apply_kwargs["extra_env"] = mysterybox_payload_env
    try:
        _run_terraform_apply_with_status(config, paths, **apply_kwargs)
    except RuntimeError:
        with suppress(Exception):
            if _sync_mysterybox_primary_version_ids_to_config(
                config,
                paths,
                initialize=False,
                manifest=manifest,
                require_all=False,
            ):
                console.print(
                    "Recovered MysteryBox primary version_id values from Terraform state; "
                    "retry deploy to continue from the refreshed generated bundle."
                )
        raise
    if _sync_mysterybox_primary_version_ids_to_config(
        config,
        paths,
        initialize=False,
        manifest=manifest,
    ):
        manifest = load_generated_manifest(paths.generated_dir)
        config = runtime_config_from_manifest(manifest)
    _refresh_mysterybox_eso_flux_after_terraform(config, paths)
    write_inventory(config, paths, validations=report_validations)
    if skip_validations and declared_validations:
        if deploy_validations:
            console.print(
                "Skipping optional deploy-time validations for this run "
                "(--skip-validations); required validations still run."
            )
        else:
            console.print(
                "Skipping optional deploy-time validations for this run (--skip-validations)."
            )
    elif skip_validation_kinds:
        skipped_labels = _deploy_validation_skip_labels(skip_validation_kinds)
        if skipped_labels:
            console.print(
                "Skipping optional deploy-time validations for this run: "
                + ", ".join(skipped_labels)
            )
    if manifest_targets and not selected_targets:
        console.print(
            f"{warning_markup('WARNING:', bold=True)} "
            f"{_multi_cluster_handoff_skip_note(manifest_targets)}"
        )
    validation_error: Exception | None = None
    grafana_statuses: list[dict[str, Any]] = []
    if selected_targets:
        persist_local_kubeconfig = True
        set_current_context = len(selected_targets) == 1 and not all_targets
        for target in selected_targets:
            target_ref = str(target["target_ref"])
            target_paths = _paths_for_target_flux_dir(paths, target)
            target_has_apps = _active_chart_count_for_target(config, target_ref=target_ref) > 0
            target_validations = _filter_validations_for_target(
                deploy_validations,
                target_ref=target_ref,
            )
            needs_cluster_ready = target_has_apps or bool(target_validations)
            if len(selected_targets) > 1:
                console.print(f"[bold]Target {target_ref}[/bold]")
            with ExitStack() as stack:
                kube_env = _prepare_cluster_handoff_kube_env(
                    config,
                    paths,
                    stack=stack,
                    target=target,
                    persist_local_kubeconfig=persist_local_kubeconfig,
                    set_current_context=set_current_context,
                )
                if target_has_apps:
                    _reconcile_observability_gpu_node_labels(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref,
                    )
                    _ensure_mysterybox_eso_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref,
                        auto_auth_bootstrap=auto_auth_bootstrap,
                    )
                    _ensure_grafana_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref,
                    )
                    _ensure_soperator_notifier_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref,
                    )
                    _ensure_soperator_backup_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref,
                    )
                if needs_cluster_ready:
                    _report_cluster_nodes_status(
                        extra_env=kube_env, emit=lambda message: console.print(message)
                    )
                if target_has_apps:
                    _apply_rendered_flux(target_paths, extra_env=kube_env)
                    grafana_statuses.extend(
                        _collect_grafana_status_after_flux(
                            config,
                            extra_env=kube_env,
                            target_ref=target_ref,
                        )
                    )
                    bootstrap_command = _warn_if_flux_gitops_not_bootstrapped(
                        config,
                        target_paths,
                        extra_env=kube_env,
                        target_ref=target_ref,
                        print_command=False,
                    )
                    if bootstrap_command:
                        gitops_bootstrap_commands.append(bootstrap_command)
                if target_validations:
                    with console.status(
                        f"[cyan]Running deploy-time validations for {target_ref}...[/cyan]",
                        spinner="dots",
                    ) as status:
                        last_validation_phase = ""

                        def _emit_validation_phase(message: str) -> None:
                            nonlocal last_validation_phase
                            status.update(message)
                            if not _console_is_terminal() and message != last_validation_phase:
                                console.print(message)
                            last_validation_phase = message

                        try:
                            _run_deploy_validations(
                                target_validations,
                                inventory_dir=paths.inventory_dir,
                                extra_env=kube_env,
                                emit=_emit_validation_phase,
                            )
                        except Exception as exc:
                            validation_error = exc
                            break
            if validation_error is not None:
                break
    elif has_enabled_app_charts or deploy_validations:
        _report_cluster_nodes_status(extra_env=None, emit=lambda message: console.print(message))
        if has_enabled_app_charts:
            _ensure_mysterybox_eso_runtime_before_flux(
                config,
                extra_env=None,
                auto_auth_bootstrap=auto_auth_bootstrap,
            )
            _ensure_grafana_runtime_before_flux(config, extra_env=None)
            _ensure_soperator_notifier_runtime_before_flux(config, extra_env=None)
            _ensure_soperator_backup_runtime_before_flux(config, extra_env=None)
            _apply_rendered_flux(paths, extra_env=None)
            grafana_statuses.extend(_collect_grafana_status_after_flux(config, extra_env=None))
            bootstrap_command = _warn_if_flux_gitops_not_bootstrapped(
                config,
                paths,
                extra_env=None,
                print_command=False,
            )
            if bootstrap_command:
                gitops_bootstrap_commands.append(bootstrap_command)
        if deploy_validations:
            with console.status(
                "[cyan]Running deploy-time validations...[/cyan]",
                spinner="dots",
            ) as status:
                last_validation_phase = ""

                def _emit_validation_phase(message: str) -> None:
                    nonlocal last_validation_phase
                    status.update(message)
                    if not _console_is_terminal() and message != last_validation_phase:
                        console.print(message)
                    last_validation_phase = message

                try:
                    _run_deploy_validations(
                        deploy_validations,
                        inventory_dir=paths.inventory_dir,
                        extra_env=None,
                        emit=_emit_validation_phase,
                    )
                except Exception as exc:
                    validation_error = exc
    if grafana_statuses:
        write_grafana_status(
            paths,
            grafana_statuses,
            preserve_existing=bool(
                manifest_targets
                and selected_targets
                and len(selected_targets) < len(manifest_targets)
            ),
        )
        write_inventory(config, paths, validations=report_validations)
    validation_report: DeployValidationReport | None = None
    if report_validations:
        artifacts = write_inventory(config, paths, validations=report_validations)
        validation_report = build_deploy_validation_report(
            report_validations,
            inventory_dir=paths.inventory_dir,
            markdown_path=artifacts.markdown,
        )
    if validation_error is not None:
        _print_deploy_command_footer(
            config,
            paths,
            DeployRunSummary(
                validation_report=validation_report,
                gitops_bootstrap_commands=tuple(gitops_bootstrap_commands),
            ),
            succeeded=False,
        )
        raise validation_error
    return DeployRunSummary(
        validation_report=validation_report,
        gitops_bootstrap_commands=tuple(gitops_bootstrap_commands),
    )


def _print_deploy_command_footer(
    config: Any,
    paths: ProjectPaths,
    summary: DeployRunSummary | None,
    *,
    succeeded: bool,
) -> None:
    summary = summary or DeployRunSummary()
    console.print("")
    console.print("[bold]Deployment summary[/bold]")
    console.print("Validation:")
    for line in _deploy_footer_validation_lines(summary.validation_report):
        console.print(line)
    console.print("Copy/paste commands:")
    for line in _deploy_footer_command_lines(config, paths, summary):
        console.print(line, soft_wrap=True)
    console.print("Important paths:")
    for line in _deploy_footer_path_lines(paths, summary):
        console.print(line, soft_wrap=True)
    status_text = "completed" if succeeded else "failed"
    console.print(f"Deploy {status_text} from {paths.generated_dir}", soft_wrap=True)


def _deploy_footer_validation_lines(report: DeployValidationReport | None) -> list[str]:
    if report is None:
        return ["  No deploy-time validations were configured for this run."]
    lines = [
        (
            "  Overall: "
            f"{status_label(report.overall_status)} "
            f"({report.completed_count}/{report.total_count} completed, "
            f"{report.not_run_count} not run)"
        )
    ]
    for item in report.results:
        lines.append(f"  {status_label(item.status)} {item.name}: {item.summary}")
    return lines


def _deploy_footer_command_lines(
    config: Any,
    paths: ProjectPaths,
    summary: DeployRunSummary,
) -> list[str]:
    lines: list[str] = []
    for hint in wireguard_access_command_hints(config, paths):
        lines.extend([f"  # {hint['label']}", f"  {hint['command']}"])
    for hint in ssh_jump_access_hints(config, paths):
        lines.extend(
            [
                f"  # SSH {hint['target_label']} via {hint['jump_host_label']}",
                f"  {hint['command']}",
            ]
        )
    for command in _unique_texts(summary.gitops_bootstrap_commands):
        lines.extend(["  # Enable GitOps sync", f"  {command}"])
    if not lines:
        lines.append("  No immediate access or follow-up commands were derived.")
    return lines


def _deploy_footer_path_lines(paths: ProjectPaths, summary: DeployRunSummary) -> list[str]:
    lines = [
        f"  Generated bundle: {paths.generated_dir}",
        f"  Deploy report: {paths.inventory_dir / DEPLOY_REPORT_FILENAME}",
        f"  Generated manifest: {manifest_path_for_generated_dir(paths.generated_dir)}",
    ]
    report = summary.validation_report
    if report is not None:
        for item in report.results:
            if item.report_exists:
                lines.append(f"  Validation JSON: {item.report_path}")
    return lines


def _unique_texts(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _validate_rendered_flux_manifests(
    paths: ProjectPaths,
    *,
    command_name: str,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    if not shutil.which("kubectl"):
        raise RuntimeError(f"kubectl is required for `{command_name}` but was not found in PATH")
    target_paths = [
        _paths_for_target_flux_dir(paths, target)
        for target in _manifest_deploy_targets(manifest or {})
    ] or [paths]
    for target_path in target_paths:
        try:
            subprocess.run(
                ["kubectl", "kustomize", str(target_path.flux_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            detail = _first_non_empty_line(exc.stderr or exc.stdout or "")
            raise RuntimeError(detail or str(exc)) from exc


def _run_deploy_preflight(
    config: Any,
    paths: ProjectPaths,
    *,
    auto_auth_bootstrap: bool,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    return _run_generated_bundle_validation(
        config,
        paths,
        auto_auth_bootstrap=auto_auth_bootstrap,
        title="Deploy preflight",
        quota_phase="deploy",
        flux_command_name="deploy",
        manifest=manifest,
        prompt_mysterybox_payload_values=_console_is_terminal(),
    )


def _run_generated_bundle_validation(
    config: Any,
    paths: ProjectPaths,
    *,
    auto_auth_bootstrap: bool,
    title: str,
    quota_phase: str,
    flux_command_name: str,
    portable: bool = False,
    manifest: Mapping[str, Any] | None = None,
    prompt_mysterybox_payload_values: bool = False,
) -> dict[str, str]:
    mysterybox_payload_requirements = _mysterybox_runtime_payload_requirements(config)
    mysterybox_payload_env: dict[str, str] = {}
    if mysterybox_payload_requirements and prompt_mysterybox_payload_values:
        console.print(
            "MysteryBox payload values are required for first deploy. "
            "Input is hidden and used only for this Terraform run."
        )
        mysterybox_payload_env = _collect_mysterybox_runtime_payload_values(
            config,
            prompt=True,
        )
    phase_defs = [
        _ValidationPhase("strict-readiness", "Validate strict deployment readiness"),
    ]
    if mysterybox_payload_requirements:
        phase_defs.append(
            _ValidationPhase(
                "mysterybox-payload-values",
                "Validate MysteryBox runtime payload values",
            )
        )
    phase_defs.extend(
        [
            _ValidationPhase("mk8s-preflight", "Validate MK8s network preflight"),
            _ValidationPhase("backend", "Prepare Terraform backend auth"),
            _ValidationPhase("quota-readiness", "Validate live Nebius quota/capacity"),
            _ValidationPhase("terraform", "Validate generated Terraform bundle"),
        ]
    )
    active_chart_count = _active_chart_count(config)
    if active_chart_count > 0:
        phase_defs.append(_ValidationPhase("flux", "Validate rendered Flux manifests"))
    if portable:
        phase_defs.append(_ValidationPhase("portable", "Validate generated bundle portability"))

    with _ValidationProgress(title=title, phases=phase_defs) as progress:
        if not paths.infra_dir.exists():
            raise RuntimeError(f"Rendered infra directory does not exist: {paths.infra_dir}")
        progress.run(
            "strict-readiness",
            lambda: _validate_strict_config(config, include_common_checks=False),
        )
        if mysterybox_payload_requirements:

            def _prepare_mysterybox_payload_values() -> None:
                nonlocal mysterybox_payload_env
                validation_env = os.environ.copy()
                validation_env.update(mysterybox_payload_env)
                if mysterybox_payload_env:
                    _validate_mysterybox_runtime_payload_values(
                        config,
                        environ=validation_env,
                    )
                    return
                mysterybox_payload_env = _collect_mysterybox_runtime_payload_values(config)

            progress.run(
                "mysterybox-payload-values",
                _prepare_mysterybox_payload_values,
            )
        progress.run("mk8s-preflight", lambda: validate_mk8s_network_preflight(config))
        progress.run(
            "backend",
            lambda: _ensure_terraform_backend_ready(
                config,
                auto_auth_bootstrap=auto_auth_bootstrap,
            ),
        )
        runtime_env = _terraform_runtime_env(config)
        progress.run(
            "quota-readiness",
            lambda: _raise_on_generated_bundle_live_quota_issues(
                config,
                paths,
                manifest=manifest,
                runtime_env=runtime_env,
                phase=quota_phase,
            ),
        )

        def _validate_generated_terraform_bundle() -> None:
            _validate_generated_mk8s_resource_name_preflight(
                config,
                paths,
                runtime_env=runtime_env,
            )
            terraform_validate(
                paths.infra_dir,
                extra_env=runtime_env,
                initialize=False,
            )

        progress.run(
            "terraform",
            _validate_generated_terraform_bundle,
        )
        if active_chart_count > 0:
            progress.run(
                "flux",
                lambda: _validate_rendered_flux_manifests(
                    paths,
                    command_name=flux_command_name,
                    manifest=manifest,
                ),
            )
        if portable:
            progress.run(
                "portable",
                lambda: _validate_generated_bundle_portability(paths, manifest or {}),
            )

    _print_mk8s_gpu_validation_warnings(config)
    return mysterybox_payload_env


_TERRAFORM_STATE_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def _terraform_state_resource_name(state_show_text: str) -> str:
    match = _TERRAFORM_STATE_NAME_RE.search(state_show_text)
    return match.group(1).strip() if match is not None else ""


def _managed_mk8s_resource_names_from_terraform_state(
    *,
    paths: ProjectPaths,
    runtime_env: Mapping[str, str] | None,
) -> tuple[set[str], set[str]]:
    managed_mk8s_cluster_names: set[str] = set()
    managed_gpu_cluster_names: set[str] = set()
    for address in terraform_state_list(
        paths.infra_dir,
        extra_env=dict(runtime_env or {}),
        initialize=False,
    ):
        normalized_address = str(address).strip()
        if not normalized_address:
            continue
        if (
            ".nebius_mk8s_v1_cluster.this" not in normalized_address
            and ".nebius_compute_v1_gpu_cluster.this" not in normalized_address
        ):
            continue
        state_show_text = terraform_state_show(
            paths.infra_dir,
            normalized_address,
            extra_env=dict(runtime_env or {}),
            initialize=False,
        )
        resource_name = _terraform_state_resource_name(state_show_text)
        if not resource_name:
            continue
        if ".nebius_mk8s_v1_cluster.this" in normalized_address:
            managed_mk8s_cluster_names.add(resource_name)
        elif ".nebius_compute_v1_gpu_cluster.this" in normalized_address:
            managed_gpu_cluster_names.add(resource_name)
    return managed_mk8s_cluster_names, managed_gpu_cluster_names


def _validate_generated_mk8s_resource_name_preflight(
    config: Any,
    paths: ProjectPaths,
    *,
    runtime_env: Mapping[str, str] | None,
) -> None:
    if not has_mk8s_resource_name_preflight_targets(config):
        return
    managed_mk8s_cluster_names, managed_gpu_cluster_names = (
        _managed_mk8s_resource_names_from_terraform_state(
            paths=paths,
            runtime_env=runtime_env,
        )
    )
    validate_mk8s_resource_name_preflight(
        config,
        managed_mk8s_cluster_names=managed_mk8s_cluster_names,
        managed_gpu_cluster_names=managed_gpu_cluster_names,
    )


def _destroy_rendered_flux_bundle(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    requested_target_ref: str | None = None,
    all_targets: bool = False,
) -> None:
    if _active_chart_count(config) == 0:
        raise RuntimeError("No enabled apps charts are configured for this project.")
    manifest_targets = _manifest_deploy_targets(manifest)
    if manifest_targets:
        selected_targets = _resolve_selected_deploy_targets(
            manifest,
            requested_target_ref=requested_target_ref,
            all_targets=all_targets,
        )
        for target in selected_targets:
            target_ref = str(target["target_ref"])
            target_paths = _paths_for_target_flux_dir(paths, target)
            if len(selected_targets) > 1:
                console.print(f"[bold]Target {target_ref}[/bold]")
            with ExitStack() as stack:
                kube_env = _prepare_cluster_handoff_kube_env(
                    config,
                    paths,
                    stack=stack,
                    target=target,
                    persist_local_kubeconfig=False,
                )
                delete_rendered_flux(target_paths, extra_env=kube_env, emit=console.print)
        return
    delete_rendered_flux(paths, extra_env=None, emit=console.print)


def _destroy_uses_cluster_teardown_for_apps(config: Any, manifest: Mapping[str, Any]) -> bool:
    return _active_chart_count(config) > 0 and bool(_manifest_deploy_targets(manifest))


def _destroy_should_delete_rendered_flux_first(config: Any, manifest: Mapping[str, Any]) -> bool:
    return _active_chart_count(config) > 0 and not bool(_manifest_deploy_targets(manifest))


def _destroy_confirmation_text(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
) -> tuple[str, str]:
    if _active_chart_count(config) == 0:
        return (
            "Continue and destroy all rendered infra resources for this project?",
            "Destroy will remove all rendered infra resources for this project by running "
            "Terraform destroy against the rendered infra bundle under "
            f"{paths.infra_dir}.",
        )
    if _destroy_uses_cluster_teardown_for_apps(config, manifest):
        return (
            "Continue and destroy all rendered app and infra resources for this project?",
            "Destroy will remove all rendered project resources represented by the generated "
            "manifest by running Terraform destroy against the rendered infra bundle under "
            f"{paths.infra_dir}. Because this bundle destroys the handed-off cluster directly, "
            "it will not delete the rendered app resources under "
            f"{paths.flux_dir} separately first.",
        )
    return (
        "Continue and destroy all rendered app and infra resources for this project?",
        "Destroy will remove all rendered project resources represented by the generated "
        "manifest by deleting the rendered app resources from the target cluster using "
        f"{paths.flux_dir} first and then running Terraform destroy against the rendered "
        f"infra bundle under {paths.infra_dir}.",
    )


def _destroy_generated_artifacts(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    auto_auth_bootstrap: bool,
    yes: bool = False,
) -> None:
    _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
    if _destroy_should_delete_rendered_flux_first(config, manifest):
        try:
            _destroy_rendered_flux_bundle(config, paths, manifest)
        except Exception as exc:
            console.print(
                f"{warning_markup('WARNING:', bold=True)} "
                "Rendered app teardown failed before infra destroy. "
                "Continuing with Terraform destroy because the generated infra bundle "
                f"remains the authoritative teardown path. Reason: {exc}"
            )
    elif _destroy_uses_cluster_teardown_for_apps(config, manifest):
        console.print(
            "Skipping rendered app teardown before infra destroy because this generated bundle "
            "destroys the handed-off cluster directly."
        )
    status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(config)
    _run_terraform_destroy_with_recovery(
        config,
        paths,
        auto_auth_bootstrap=auto_auth_bootstrap,
        yes=yes,
        initialize=True,
        status_watchers=status_watchers or None,
    )


def _run_terraform_apply_with_status(
    config: Any,
    paths: ProjectPaths,
    *,
    initialize: bool = True,
    status_watchers: list[dict[str, str]] | None = None,
    run_mk8s_preflight: bool = True,
    extra_env: Mapping[str, str] | None = None,
) -> None:
    runtime_env = _terraform_runtime_env(config)
    if extra_env:
        runtime_env.update(dict(extra_env))
    validation_env = os.environ.copy()
    validation_env.update(runtime_env)
    _validate_mysterybox_runtime_payload_values(config, environ=validation_env)
    if run_mk8s_preflight:
        validate_mk8s_network_preflight(config)
    reporting_kwargs: dict[str, Any] = {
        "emit": lambda message: console.print(message),
    }
    if status_watchers:
        reporting_kwargs["status_watchers"] = status_watchers
    with deployment_status_reporting(config, **reporting_kwargs) as reporter:
        try:
            abort_check = reporter.abort_reason if hasattr(reporter, "abort_reason") else None
            apply_kwargs: dict[str, Any] = {
                "extra_env": runtime_env,
                "initialize": initialize,
                "event_callback": reporter.handle_terraform_event,
            }
            if abort_check is not None:
                apply_kwargs["abort_check"] = abort_check
            terraform_apply(
                paths.infra_dir,
                **apply_kwargs,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\n\nLast known deploy status:\n{reporter.snapshot()}"
            ) from exc


def _run_terraform_destroy_with_status(
    config: Any,
    paths: ProjectPaths,
    *,
    initialize: bool = True,
    status_watchers: list[dict[str, str]] | None = None,
) -> None:
    runtime_env = _terraform_runtime_env(config)
    reporting_kwargs: dict[str, Any] = {
        "emit": lambda message: console.print(message),
        "operation": "destroy",
    }
    if status_watchers:
        reporting_kwargs["status_watchers"] = status_watchers
    with deployment_status_reporting(config, **reporting_kwargs) as reporter:
        try:
            abort_check = reporter.abort_reason if hasattr(reporter, "abort_reason") else None
            destroy_kwargs: dict[str, Any] = {
                "extra_env": runtime_env,
                "initialize": initialize,
                "event_callback": reporter.handle_terraform_event,
            }
            if abort_check is not None:
                destroy_kwargs["abort_check"] = abort_check
            terraform_destroy(
                paths.infra_dir,
                **destroy_kwargs,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\n\nLast known destroy status:\n{reporter.snapshot()}"
            ) from exc


def _run_terraform_destroy_with_recovery(
    config: Any,
    paths: ProjectPaths,
    *,
    auto_auth_bootstrap: bool,
    yes: bool,
    initialize: bool = True,
    status_watchers: list[dict[str, str]] | None = None,
) -> None:
    current_exc: RuntimeError | None = None
    try:
        _run_terraform_destroy_with_status(
            config,
            paths,
            initialize=initialize,
            status_watchers=status_watchers,
        )
        return
    except RuntimeError as exc:
        current_exc = exc

    if current_exc is not None and _is_terraform_state_lock_failure(current_exc):
        lock_info = _unlock_terraform_state_lock(
            config,
            paths,
            auto_auth_bootstrap=auto_auth_bootstrap,
            force=False,
        )
        if lock_info is not None:
            console.print(
                "Detected stale Terraform state lock during destroy; "
                f"cleared lock {lock_info.lock_id} owned by {lock_info.who or '(unknown)'} "
                "and retrying Terraform destroy."
            )
            try:
                _run_terraform_destroy_with_status(
                    config,
                    paths,
                    initialize=initialize,
                    status_watchers=status_watchers,
                )
                return
            except RuntimeError as exc:
                current_exc = exc

    try:
        recovered = _attempt_mk8s_node_group_destroy_recovery(
            status_watchers=status_watchers,
            yes=yes,
        )
    except Exception as recovery_exc:
        raise RuntimeError(
            f"{current_exc}\n\nBuilt-in destroy recovery could not remove the stuck MK8s node group: "
            f"{recovery_exc}"
        ) from current_exc
    if recovered:
        console.print("Retrying Terraform destroy after MK8s node-group cleanup.")
        _run_terraform_destroy_with_status(
            config,
            paths,
            initialize=initialize,
            status_watchers=status_watchers,
        )
        return

    if current_exc is None:
        raise RuntimeError("Terraform destroy recovery failed without a captured destroy error.")
    raise current_exc


def _warn_if_flux_gitops_not_bootstrapped(
    config: Any,
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None,
    target_ref: str | None = None,
    print_command: bool = True,
) -> str | None:
    if _active_chart_count(config) == 0:
        return None
    if not extra_env or not extra_env.get("KUBECONFIG"):
        return None
    if flux_bootstrap_resources_installed(extra_env=extra_env):
        return None
    command = f"nebius-cxcli flux bootstrap {shlex.quote(str(paths.generated_dir))}"
    if target_ref:
        command += f" --target {shlex.quote(target_ref)}"
    console.print(
        f"{warning_markup('WARNING:', bold=True)} Flux GitOps bootstrap is not configured for this cluster yet. "
        "Local apply succeeded, but the cluster will not continuously sync from the Git repository "
        "until you bootstrap it."
    )
    if print_command:
        console.print(
            "The command below takes the local generated bundle path; the GitHub repository is inferred "
            f"from GITHUB_REPOSITORY or the git origin under {paths.repo_root}."
        )
    else:
        console.print(
            "The GitOps command in the final Deployment summary takes the local generated bundle path; "
            "the GitHub repository is inferred "
            f"from GITHUB_REPOSITORY or the git origin under {paths.repo_root}."
        )
    console.print(
        "Commit and push the rendered generated/flux path before relying on continuous GitOps sync."
    )
    if print_command:
        console.print("Run to enable GitOps sync:")
        console.print(command, style="cyan", no_wrap=True, overflow="ignore")
    return command


def _current_lock_owner_identity() -> str:
    user = getpass.getuser().strip() or "unknown"
    host = socket.gethostname().strip() or "unknown-host"
    return f"{user}@{host}"


def _active_local_terraform_processes() -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ()

    current_pid = os.getpid()
    process_pattern = re.compile(
        r"\b(terraform (apply|plan|force-unlock)|nebius-cxcli (deploy|terraform apply|terraform plan|terraform unlock))\b"
    )
    active: list[str] = []
    for raw_line in (completed.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            pid = int(pid_text.strip())
        except ValueError:
            continue
        if pid == current_pid:
            continue
        normalized_command = command.strip()
        if not normalized_command:
            continue
        if process_pattern.search(normalized_command):
            active.append(normalized_command)
    return tuple(active)


def _unlock_terraform_state_lock(
    config: Any,
    paths: ProjectPaths,
    *,
    auto_auth_bootstrap: bool,
    force: bool,
) -> TerraformStateLockInfo | None:
    if not paths.infra_dir.exists():
        raise RuntimeError(
            f"Rendered infra directory does not exist: {paths.infra_dir}. "
            "Rerun `nebius-cxcli render <config.yaml>` first."
        )
    _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
    runtime_env = _terraform_runtime_env(config)
    settings = backend_settings_from_config(config)
    lock_info = read_state_lock_info(settings, extra_env=runtime_env)
    if lock_info is None:
        return None

    if not force:
        active_processes = _active_local_terraform_processes()
        if active_processes:
            rendered_processes = "\n".join(f"  - {command}" for command in active_processes)
            raise RuntimeError(
                "Refusing to unlock Terraform state while local Terraform/deploy operations appear active:\n"
                f"{rendered_processes}\n"
                "Wait for those commands to finish, or rerun `terraform unlock --force` only after you have confirmed the lock is stale."
            )
        current_owner = _current_lock_owner_identity()
        if lock_info.who and lock_info.who != current_owner:
            raise RuntimeError(
                "Refusing to unlock Terraform state lock owned by "
                f"`{lock_info.who}` from current identity `{current_owner}`. "
                "Rerun with `terraform unlock --force` only after you have confirmed that operation is no longer running."
            )

    terraform_force_unlock(paths.infra_dir, lock_info.lock_id, extra_env=runtime_env)
    return lock_info


def _is_terraform_state_lock_failure(exc: Exception) -> bool:
    text = str(exc)
    return (
        "Terraform never acquired the remote state lock" in text
        or "Error acquiring the state lock" in text
    )


def _mk8s_destroy_recovery_targets(
    status_watchers: Sequence[Mapping[str, Any]] | None,
) -> tuple[tuple[str, str], ...]:
    if not status_watchers:
        return ()
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for watcher in status_watchers:
        kind = str(watcher.get("kind", "")).strip().lower()
        parent_id = str(watcher.get("parent_id", "")).strip()
        resource_name = str(watcher.get("resource_name", "")).strip()
        if kind != "nebius.mk8s.cluster" or not parent_id or not resource_name:
            continue
        target = (parent_id, resource_name)
        if target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return tuple(targets)


def _confirm_mk8s_destroy_recovery_cleanup(
    *,
    yes: bool,
    candidates: Sequence[Mk8sNodeGroupDestroyCandidate],
) -> bool:
    summary = ", ".join(
        f"{candidate.cluster_name}/{candidate.node_group_name}" for candidate in candidates[:3]
    )
    if len(candidates) > 3:
        summary += f", +{len(candidates) - 3} more"
    console.print(
        f"{warning_markup('WARNING:', bold=True)} "
        "Terraform destroy still appears blocked by stuck MK8s node-group create operations: "
        f"{summary}."
    )
    if yes:
        return True
    if not _can_prompt_for_render_overwrite():
        raise RuntimeError(
            "Destroy recovery wants to delete stuck MK8s node groups directly via the Nebius API "
            "before retrying Terraform destroy. Re-run with `--yes` to confirm."
        )
    return typer.confirm(
        "Delete the stuck MK8s node groups directly via the Nebius API and retry Terraform destroy?",
        default=False,
        show_default=True,
    )


def _attempt_mk8s_node_group_destroy_recovery(
    *,
    status_watchers: Sequence[Mapping[str, Any]] | None,
    yes: bool,
) -> bool:
    candidates: list[Mk8sNodeGroupDestroyCandidate] = []
    for project_id, cluster_name in _mk8s_destroy_recovery_targets(status_watchers):
        candidates.extend(
            find_stuck_mk8s_node_groups(project_id=project_id, cluster_name=cluster_name)
        )
    if not candidates:
        return False
    if not _confirm_mk8s_destroy_recovery_cleanup(yes=yes, candidates=candidates):
        return False
    for candidate in candidates:
        console.print(
            "Deleting stuck MK8s node group "
            f"{candidate.node_group_name} ({candidate.node_group_id}) from cluster "
            f"{candidate.cluster_name} because the live API still shows an unfinished create "
            f"operation {candidate.create_operation_id}. Reason: {candidate.reason}"
        )
        operation_id = delete_stuck_mk8s_node_group(candidate)
        console.print(
            "Deleted stuck MK8s node group "
            f"{candidate.node_group_name} ({candidate.node_group_id}); "
            f"delete operation {operation_id} completed."
        )
    return True


def _resolve_project_id_for_auth_bootstrap(
    *, project_id: str | None, project_config: Path | None
) -> str:
    if project_id:
        return project_id
    if project_config is None:
        raise RuntimeError("Missing required option: --project-id (or provide --project-config)")
    config = load_config(project_config.resolve())
    return config.client_info.nebius.project_id


def _resolve_client_name_for_auth_bootstrap(
    *,
    client_name: str | None,
    project_config: Path | None,
) -> str:
    if client_name:
        normalized = client_name.strip()
        if normalized:
            return normalized
    if project_config is None:
        raise RuntimeError("Missing required option: --client-name (or provide --project-config)")
    config = load_config(project_config.resolve())
    return str(config.client_info.client_name).strip()


def _github_environment_name_for_identity(*, client_name: str, project_id: str) -> str:
    try:
        return build_github_environment_name(client_name=client_name, project_id=project_id)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _ci_github_secrets_payload(
    *,
    service_account_id: str,
    auth_public_key_id: str,
    auth_private_key_pem: str,
    s3_access_key_id: str,
    s3_secret_access_key: str,
) -> dict[str, str]:
    return {
        "NEBIUS_SA_ID": service_account_id,
        "NEBIUS_AUTH_PUBLIC_KEY_ID": auth_public_key_id,
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": auth_private_key_pem,
        "NEBIUS_S3_ACCESS_KEY_ID": s3_access_key_id,
        "NEBIUS_S3_SECRET_ACCESS_KEY": s3_secret_access_key,
    }


def _resolve_github_repo_slug(
    *,
    explicit_repo_slug: str | None,
    repo_root: Path | None,
) -> str:
    if explicit_repo_slug:
        slug = explicit_repo_slug.strip()
        if "/" not in slug:
            raise RuntimeError("--github-repo must be in '<owner>/<repo>' format")
        return slug
    if repo_root is None:
        raise RuntimeError("GitHub repo could not be resolved; provide --github-repo owner/repo")
    return detect_github_repo_slug(repo_root)


def _resolve_bootstrap_ci_github_target(
    *,
    github_repo: str | None,
    github_token_env: str,
    repo_root: Path,
) -> tuple[str, str]:
    repo_slug = _resolve_github_repo_slug(explicit_repo_slug=github_repo, repo_root=repo_root)
    github_token = read_github_token(preferred_env=github_token_env)
    if github_token:
        return repo_slug, github_token
    raise RuntimeError(
        "GitHub bootstrap reconciliation requires a GitHub token. "
        f"No token found in ${github_token_env}, $GH_TOKEN, or $GITHUB_TOKEN."
    )


def _sync_github_ci_secrets(
    *,
    repo_slug: str,
    github_environment: str,
    github_token: str,
    ci_secrets: dict[str, str],
) -> list[str]:
    payload = dict(ci_secrets)
    payload[FLUX_SECRET_KEY] = github_token
    return upsert_environment_secrets(
        repo_slug=repo_slug,
        token=github_token,
        environment_name=github_environment,
        secrets=payload,
    )


def _load_local_email_settings(*, config_path: Path | None = None) -> EmailSettings:
    return load_email_settings(explicit=config_path)


@dataclass(frozen=True)
class GitHubEmailSyncResult:
    updated_vars: list[str]
    updated_secrets: list[str]
    removed_vars: list[str]
    removed_secrets: list[str]


def _sync_github_email_settings(
    *,
    repo_slug: str,
    github_environment: str,
    github_token: str,
    settings: EmailSettings,
) -> GitHubEmailSyncResult:
    ensure_github_environment(
        repo_slug=repo_slug,
        token=github_token,
        environment_name=github_environment,
    )
    managed_vars = ("SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS", "SMTP_FROM")
    managed_secrets = ("SMTP_USERNAME", "SMTP_PASSWORD")
    desired_vars = email_environment_variables(settings) if settings.enabled else {}
    desired_secrets = email_secret_values(settings) if settings.enabled else {}
    updated_vars = (
        upsert_environment_variables(
            repo_slug=repo_slug,
            token=github_token,
            environment_name=github_environment,
            variables=desired_vars,
        )
        if desired_vars
        else []
    )
    updated_secrets = (
        upsert_environment_secrets(
            repo_slug=repo_slug,
            token=github_token,
            environment_name=github_environment,
            secrets=desired_secrets,
        )
        if desired_secrets
        else []
    )
    removed_vars = [
        name
        for name in managed_vars
        if name not in desired_vars
        and delete_environment_variable(
            repo_slug=repo_slug,
            token=github_token,
            environment_name=github_environment,
            variable_name=name,
        )
    ]
    removed_secrets = [
        name
        for name in managed_secrets
        if name not in desired_secrets
        and delete_environment_secret(
            repo_slug=repo_slug,
            token=github_token,
            environment_name=github_environment,
            secret_name=name,
        )
    ]
    return GitHubEmailSyncResult(
        updated_vars=updated_vars,
        updated_secrets=updated_secrets,
        removed_vars=removed_vars,
        removed_secrets=removed_secrets,
    )


def _sync_runtime_auth_profile_to_ci_environment(
    *,
    material: RuntimeAuthCacheMaterial,
    client_name: str,
    github_repo: str | None,
    github_token_env: str,
    repo_root_hint: Path | None,
) -> tuple[str, str, list[str]]:
    github_token = read_github_token(preferred_env=github_token_env)
    if not github_token:
        raise RuntimeError(
            "GitHub sync requires a token. "
            f"No token found in ${github_token_env}, $GH_TOKEN, or $GITHUB_TOKEN."
        )

    repo_slug = _resolve_github_repo_slug(
        explicit_repo_slug=github_repo,
        repo_root=repo_root_hint,
    )
    github_environment = _github_environment_name_for_identity(
        client_name=client_name,
        project_id=material.project_id,
    )
    ensure_github_environment(
        repo_slug=repo_slug,
        token=github_token,
        environment_name=github_environment,
    )

    ci_secrets: dict[str, str] = {
        "NEBIUS_SA_ID": material.service_account_id,
        "NEBIUS_AUTH_PUBLIC_KEY_ID": material.auth_public_key_id,
        "NEBIUS_AUTH_PRIVATE_KEY_PEM": material.private_key_pem,
    }
    if material.s3_access_key_id:
        ci_secrets["NEBIUS_S3_ACCESS_KEY_ID"] = material.s3_access_key_id
    if material.s3_secret_access_key:
        ci_secrets["NEBIUS_S3_SECRET_ACCESS_KEY"] = material.s3_secret_access_key

    updated = _sync_github_ci_secrets(
        repo_slug=repo_slug,
        github_environment=github_environment,
        github_token=github_token,
        ci_secrets=ci_secrets,
    )
    return repo_slug, github_environment, updated


def _auto_bootstrap_ci_auth_and_secrets(
    *,
    project_id: str,
    github_environment: str,
    repo_root: Path,
    service_account_name: str,
    service_account_description: str,
    role_ids: list[str],
    auth_key_description: str,
    access_key_description: str,
    github_repo: str | None,
    github_token_env: str,
    profile: str | None,
    endpoint: str | None,
    sdk_config_file: Path | None,
) -> None:
    github_token = read_github_token(preferred_env=github_token_env)
    if not github_token:
        raise RuntimeError(
            "Automatic CI auth bootstrap requires a GitHub token. "
            f"No token found in ${github_token_env}, $GH_TOKEN, or $GITHUB_TOKEN."
        )

    repo_slug = _resolve_github_repo_slug(explicit_repo_slug=github_repo, repo_root=repo_root)
    ensure_github_environment(
        repo_slug=repo_slug,
        token=github_token,
        environment_name=github_environment,
    )

    presence = environment_secrets_presence(
        repo_slug=repo_slug,
        token=github_token,
        environment_name=github_environment,
        names=[*NEBIUS_CI_SECRET_KEYS, FLUX_SECRET_KEY],
    )
    nebius_ready = all(presence.get(name, False) for name in NEBIUS_CI_SECRET_KEYS)
    flux_ready = presence.get(FLUX_SECRET_KEY, False)

    if nebius_ready and flux_ready:
        console.print(
            "CI auth secrets already configured in "
            f"{repo_slug} environment '{github_environment}'; skipping auth bootstrap."
        )
        return

    if nebius_ready and not flux_ready:
        updated = upsert_environment_secrets(
            repo_slug=repo_slug,
            token=github_token,
            environment_name=github_environment,
            secrets={FLUX_SECRET_KEY: github_token},
        )
        console.print(
            "Configured missing GitHub environment secret(s) in "
            f"{repo_slug} environment '{github_environment}' ({len(updated)} secret(s))"
        )
        return

    result = bootstrap_ci_service_account(
        project_id=project_id,
        service_account_name=service_account_name,
        service_account_description=service_account_description,
        role_ids=role_ids,
        auth_key_description=auth_key_description,
        access_key_description=access_key_description,
        profile=profile,
        endpoint=endpoint,
        config_file=sdk_config_file,
    )
    ci_secrets = _ci_github_secrets_payload(
        service_account_id=result.service_account_id,
        auth_public_key_id=result.auth_public_key_id,
        auth_private_key_pem=result.auth_private_key_pem,
        s3_access_key_id=result.s3_access_key_id,
        s3_secret_access_key=result.s3_secret_access_key,
    )
    updated = _sync_github_ci_secrets(
        repo_slug=repo_slug,
        github_environment=github_environment,
        github_token=github_token,
        ci_secrets=ci_secrets,
    )
    console.print(
        "Bootstrapped and synced CI auth secrets to "
        f"{repo_slug} environment '{github_environment}' ({len(updated)} secret(s))"
    )


@dataclass(frozen=True)
class BootstrapResult:
    deployments_root: Path
    project_path: Path
    config_path: Path
    wrote_config: bool


@dataclass(frozen=True)
class DeploymentsGitignoreResult:
    path: Path | None
    wrote: bool


_DEPLOYMENTS_GITIGNORE_BEGIN = "# >>> nebius-cxcli managed ignores >>>"
_DEPLOYMENTS_GITIGNORE_END = "# <<< nebius-cxcli managed ignores <<<"
_DEPLOYMENTS_GITIGNORE_LINES: tuple[str, ...] = (
    _DEPLOYMENTS_GITIGNORE_BEGIN,
    "# Managed by `nebius-cxcli`.",
    "# Keep config.yaml and generated/nebius-cxcli-manifest.json versioned in a private repo.",
    "# Ignore Terraform runtime files and tfvars duplicates recreated from the generated manifest.",
    "# Ignore downloaded WireGuard client configs because they contain private keys.",
    "*/*/wireguard-clients/",
    "*/*/generated/infra/.terraform/",
    "*/*/generated/infra/*.tfstate",
    "*/*/generated/infra/*.tfstate.*",
    "*/*/generated/infra/.terraform.tfstate.lock.info",
    "*/*/generated/infra/crash.log",
    "*/*/generated/infra/crash.*.log",
    "*/*/generated/infra/*.tfplan",
    "*/*/generated/infra/plan.out",
    "*/*/generated/infra/terraform.auto.tfvars.json",
    "*/*/generated/infra/*.auto.tfvars",
    "*/*/generated/infra/*.auto.tfvars.json",
    _DEPLOYMENTS_GITIGNORE_END,
)


def _render_deployments_gitignore_block() -> str:
    return "\n".join(_DEPLOYMENTS_GITIGNORE_LINES) + "\n"


def _gitignore_has_managed_deployments_block(path: Path) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    return _DEPLOYMENTS_GITIGNORE_BEGIN in content and _DEPLOYMENTS_GITIGNORE_END in content


def _parent_managed_deployments_gitignore(deployments_root: Path) -> Path | None:
    repo_root = _try_git_root(deployments_root)
    if repo_root is None:
        return None

    resolved_root = deployments_root.resolve()
    for parent in resolved_root.parents:
        try:
            parent.relative_to(repo_root)
        except ValueError:
            break
        gitignore_path = parent / ".gitignore"
        if _gitignore_has_managed_deployments_block(gitignore_path):
            return gitignore_path
        if parent == repo_root:
            break
    return None


def _assert_not_nested_deployments_root(deployments_root: Path) -> None:
    parent_gitignore = _parent_managed_deployments_gitignore(deployments_root)
    if parent_gitignore is None:
        return
    parent_root = parent_gitignore.parent
    raise RuntimeError(
        f"Deployments root '{deployments_root}' is nested under existing cxcli-managed "
        f"deployments root '{parent_root}'. Use '{parent_root}' as the deployments root, "
        "or choose a separate directory outside that root."
    )


def _ensure_deployments_gitignore(
    *,
    deployments_root: Path,
) -> DeploymentsGitignoreResult:
    if _try_git_root(deployments_root) is None:
        return DeploymentsGitignoreResult(path=None, wrote=False)

    _assert_not_nested_deployments_root(deployments_root)
    gitignore_path = deployments_root / ".gitignore"
    block = _render_deployments_gitignore_block()
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""

    if _DEPLOYMENTS_GITIGNORE_BEGIN in existing and _DEPLOYMENTS_GITIGNORE_END in existing:
        pattern = (
            re.escape(_DEPLOYMENTS_GITIGNORE_BEGIN)
            + r".*?"
            + re.escape(_DEPLOYMENTS_GITIGNORE_END)
            + r"\n?"
        )
        updated = re.sub(pattern, block, existing, count=1, flags=re.S)
    else:
        prefix = existing.rstrip("\n")
        updated = f"{prefix}\n\n{block}" if prefix else block

    if updated != existing:
        gitignore_path.write_text(updated, encoding="utf-8")
        return DeploymentsGitignoreResult(path=gitignore_path, wrote=True)
    return DeploymentsGitignoreResult(path=gitignore_path, wrote=False)


def _ensure_wireguard_output_gitignore(output_dir: Path, paths: ProjectPaths) -> Path | None:
    default_output_dir = default_wireguard_client_output_dir(paths).resolve()
    if output_dir.resolve() == default_output_dir:
        result = _ensure_deployments_gitignore(deployments_root=paths.deployments_dir)
        return result.path

    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = output_dir / ".gitignore"
    block = "# Managed by `nebius-cxcli wireguard`.\n*\n!.gitignore\n"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if existing != block:
        gitignore_path.write_text(block, encoding="utf-8")
    return gitignore_path


def _linux_distribution_ids(os_release_path: Path = Path("/etc/os-release")) -> set[str]:
    try:
        text = os_release_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    ids: set[str] = set()
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key not in {"ID", "ID_LIKE"}:
            continue
        value = raw_value.strip().strip('"').strip("'")
        ids.update(item.lower() for item in value.split() if item)
    return ids


def _wireguard_client_install_command(
    *,
    platform_name: str | None = None,
    os_release_path: Path = Path("/etc/os-release"),
) -> str:
    platform = (platform_name or sys.platform).lower()
    if platform == "darwin":
        return "brew install wireguard-tools"
    if platform.startswith("linux"):
        distro_ids = _linux_distribution_ids(os_release_path)
        if distro_ids & {"debian", "ubuntu", "linuxmint", "pop"}:
            return "sudo apt-get update && sudo apt-get install -y wireguard-tools"
        if distro_ids & {"fedora", "rhel", "centos", "rocky", "almalinux"}:
            return "sudo dnf install -y wireguard-tools"
        if distro_ids & {"arch", "manjaro"}:
            return "sudo pacman -S wireguard-tools"
        if distro_ids & {"suse", "opensuse", "opensuse-leap", "sles"}:
            return "sudo zypper install -y wireguard-tools"
        if distro_ids & {"alpine"}:
            return "sudo apk add wireguard-tools"
        return "Install the wireguard-tools package with your Linux distribution package manager"
    if platform.startswith("win"):
        return "Install WireGuard for Windows from https://www.wireguard.com/install/"
    return "Install the WireGuard client tools for your OS from https://www.wireguard.com/install/"


def _wireguard_client_tool_missing() -> bool:
    return shutil.which("wg-quick") is None


def _ensure_customer_scaffold(
    *,
    base_path: Path,
) -> Path:
    deployments_root = _resolve_deployments_root(base_path)
    deployments_root.mkdir(parents=True, exist_ok=True)
    return deployments_root


def _project_config_path(
    *,
    deployments_root: Path,
    tenant_folder: str,
    project_folder: str,
) -> Path:
    return deployments_root / tenant_folder / project_folder / "config.yaml"


def _resolve_create_target_folders(
    *,
    provider_lookup: ProviderOptionLookup,
    tenant_id: str,
    project_id: str,
) -> tuple[str, str]:
    tenant_name, project_name = provider_lookup.resolve_tenant_project_names(
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return (
        normalize_project_folder_name(tenant_name, fallback=tenant_id),
        normalize_project_folder_name(project_name, fallback=project_id),
    )


def _deep_merge_payload(base: Any, override: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = {str(key): _deep_merge_payload(value, value) for key, value in base.items()}
        for key, value in override.items():
            token = str(key)
            if token in merged:
                merged[token] = _deep_merge_payload(merged[token], value)
            else:
                merged[token] = _deep_merge_payload(value, value)
        return merged
    if isinstance(override, list):
        return [_deep_merge_payload(item, item) for item in override]
    return override


def _enabled_ids_from_runtime_payload(
    *,
    payload: dict[str, Any],
    entries: tuple[ComponentEntry, ...],
) -> set[str]:
    selected: set[str] = set()
    entry_ids = {entry.id for entry in entries}

    infra_node = payload.get("infra")
    if isinstance(infra_node, Mapping):
        components = infra_node.get("components")
        if isinstance(components, list):
            for item in components:
                if not isinstance(item, Mapping):
                    continue
                if not bool(item.get("enabled", False)):
                    continue
                component_id = component_type_id(item)
                if component_id in entry_ids:
                    selected.add(component_id)

    apps_node = payload.get("apps")
    if isinstance(apps_node, Mapping):
        charts = apps_node.get("charts")
        if isinstance(charts, list):
            for item in charts:
                if not isinstance(item, Mapping):
                    continue
                if not bool(item.get("enabled", False)):
                    continue
                chart_id = component_type_id(item)
                if chart_id in entry_ids:
                    selected.add(chart_id)

    return selected


def _filter_runtime_payload_for_selected_components(
    *,
    payload: dict[str, Any],
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
) -> dict[str, Any]:
    runtime_payload = dict(payload)
    infra = runtime_payload.get("infra")
    apps = runtime_payload.get("apps")
    if not isinstance(infra, dict) or not isinstance(apps, dict):
        return runtime_payload

    infra_components = infra.get("components")
    if not isinstance(infra_components, list):
        infra_components = []
    existing_infra_rows: list[dict[str, Any]] = []
    for item in infra_components:
        if not isinstance(item, dict):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        row = dict(item)
        ensure_component_instance_id(row, default_component_id=component_id)
        existing_infra_rows.append(row)
    selected_infra_components: list[dict[str, Any]] = []
    for entry in infra_entries:
        if entry.id not in selected_infra:
            continue
        matched_rows = [row for row in existing_infra_rows if component_type_id(row) == entry.id]
        if not matched_rows:
            row = {
                "id": entry.id,
                INSTANCE_ID_FIELD: entry.id,
                "enabled": True,
                "inputs": {},
            }
            _seed_infra_resource_name_from_instance_id(row, entry)
            matched_rows = [row]
        else:
            for row in matched_rows:
                if not isinstance(row.get("inputs"), Mapping):
                    row["inputs"] = {}
                row["enabled"] = True
                _seed_infra_resource_name_from_instance_id(row, entry)
        selected_infra_components.extend(matched_rows)
    infra["components"] = selected_infra_components
    target_refs = enabled_cluster_target_refs(runtime_payload)
    default_target_ref = target_refs[0] if len(target_refs) == 1 else ""

    app_charts = apps.get("charts")
    if not isinstance(app_charts, list):
        app_charts = []
    existing_app_rows: list[dict[str, Any]] = []
    for item in app_charts:
        if not isinstance(item, dict):
            continue
        chart_id = component_type_id(item)
        if not chart_id:
            continue
        row = dict(item)
        ensure_component_instance_id(row, default_component_id=chart_id)
        existing_app_rows.append(row)
    selected_app_charts: list[dict[str, Any]] = []
    for entry in app_entries:
        if entry.id not in selected_apps:
            continue
        matched_rows = [row for row in existing_app_rows if component_type_id(row) == entry.id]
        if not matched_rows:
            chart_repo = str(entry.chart_repo or "").strip() or None
            chart_name = str(entry.chart_name or "").strip() or None
            if not chart_name:
                chart_repo, chart_name = _chart_source_parts(entry)
            if not chart_name:
                chart_name = entry.id
            chart_repo = _canonical_app_chart_repo(
                chart_repo=str(chart_repo or ""),
                chart_name=chart_name,
            )
            namespace = str(entry.default_namespace or "").strip() or entry.id
            release_name = str(entry.default_release_name or "").strip() or entry.id
            raw_group = str(entry.group or "").strip().lower()
            group = re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"
            instance_id = (
                target_scoped_app_instance_id(entry.id, target_ref=default_target_ref)
                if default_target_ref
                else entry.id
            )
            row = {
                "id": entry.id,
                INSTANCE_ID_FIELD: instance_id,
                "group": group,
                "enabled": True,
                "repo": str(chart_repo or ""),
                "version": str(entry.version or ""),
                "namespace": namespace,
                "release-name": release_name,
                "values": {},
            }
            matched_rows = [row]
        else:
            for row in matched_rows:
                if not str(row.get("repo", "")).strip():
                    chart_repo = str(entry.chart_repo or "").strip()
                    chart_name = str(entry.chart_name or entry.id).strip()
                    if chart_repo:
                        row["repo"] = _canonical_app_chart_repo(
                            chart_repo=chart_repo,
                            chart_name=chart_name,
                        )
                if not str(row.get("version", "")).strip() and entry.version:
                    row["version"] = str(entry.version)
                if not str(row.get("namespace", "")).strip():
                    row["namespace"] = str(entry.default_namespace or "").strip() or entry.id
                if not str(row.get("release-name", "")).strip():
                    fallback_release_name = str(
                        entry.default_release_name or ""
                    ).strip() or component_instance_id(row)
                    row["release-name"] = fallback_release_name
                if "group" not in row or not str(row.get("group", "")).strip():
                    raw_group = str(entry.group or "").strip().lower()
                    row["group"] = re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"
                if (
                    target_refs
                    and component_instance_id(row) not in target_refs
                    and default_target_ref
                ):
                    row[INSTANCE_ID_FIELD] = target_scoped_app_instance_id(
                        entry.id,
                        target_ref=default_target_ref,
                    )
                if not isinstance(row.get("values"), Mapping):
                    row["values"] = {}
                row["enabled"] = True
        selected_app_charts.extend(matched_rows)
    apps["charts"] = selected_app_charts
    _materialize_single_target_app_bindings(runtime_payload)

    return runtime_payload


def _selected_cluster_target_component_ids(
    selected_infra: set[str],
    infra_entries: tuple[ComponentEntry, ...],
) -> set[str]:
    return {
        entry.id
        for entry in infra_entries
        if entry.handoff is not None and entry.id in selected_infra
    }


def _app_selection_requires_cluster_target_message() -> str:
    return (
        "Apps are Helm charts and require an enabled MK8s target in this project. "
        "Select infra:mk8s in the same session or remove the app selection."
    )


def _app_selection_without_cluster_target_issue(
    *,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
) -> str | None:
    if not selected_apps:
        return None
    if _selected_cluster_target_component_ids(selected_infra, infra_entries):
        return None
    return _app_selection_requires_cluster_target_message()


def _target_bound_app_issues_from_payload(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    target_refs = set(enabled_cluster_target_refs(payload))
    available_targets = ", ".join(sorted(target_refs)) or "(none)"
    seen_keys: dict[tuple[str, str], str] = {}

    enabled_app_rows = _dynamic_enabled_app_chart_rows(payload)
    if enabled_app_rows and not target_refs:
        issues.append(
            "apps.charts requires at least one enabled MK8s target because cxcli apps "
            "are Helm charts installed into Kubernetes"
        )
        return issues

    for row in enabled_app_rows:
        chart_id = str(row["id"])
        instance_id = str(row["instance_id"])
        label = _component_instance_path_label("apps", chart_id, instance_id)
        instance_key = (chart_id, instance_id)
        existing_label = seen_keys.get(instance_key)
        if existing_label is not None:
            issues.append(f"{label} duplicates enabled app chart instance {existing_label}")
            continue
        seen_keys[instance_key] = label

        if target_refs and instance_id not in target_refs:
            issues.append(
                f"{label}.{INSTANCE_ID_FIELD} '{instance_id}' must reference one of "
                f"the enabled cluster targets: {available_targets}"
            )
    return issues


def _active_infra_resource_identity_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    seen_names: dict[tuple[str, str], str] = {}
    seen_instance_ids: dict[str, str] = {}

    for index, row in enumerate(_scope_rows(payload, scope="infra")):
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        instance_id = component_instance_id(row)
        if not component_id or not instance_id:
            continue
        label = _component_instance_path_label("infra", component_id, instance_id)
        existing_instance_label = seen_instance_ids.get(instance_id)
        if existing_instance_label is not None:
            issues.append(
                f"{label}.{INSTANCE_ID_FIELD} duplicates enabled infra component "
                f"{existing_instance_label}"
            )
        else:
            seen_instance_ids[instance_id] = label

        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        name_input = _entry_scalar_resource_name_input(entry)
        if not name_input:
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        raw_name = _mapping_path_value(inputs, name_input)
        if not _is_scalar_resource_name_value(raw_name):
            continue
        resource_name = str(raw_name).strip()
        normalized_name = normalize_component_token(resource_name)
        if not normalized_name or not INSTANCE_ID_PATTERN.fullmatch(normalized_name):
            issues.append(
                f"infra.components[{index}].inputs.{name_input} must normalize to a valid "
                "instance_id using lowercase letters, digits, and hyphens"
            )
            continue
        name_key = (component_id, normalized_name)
        existing_name_label = seen_names.get(name_key)
        if existing_name_label is not None:
            issues.append(
                f"{label}.inputs.{name_input} duplicates scalar resource name "
                f"'{normalized_name}' already used by {existing_name_label}"
            )
        else:
            seen_names[name_key] = label
        if instance_id != normalized_name:
            issues.append(
                f"{label}.{INSTANCE_ID_FIELD} '{instance_id}' must match normalized "
                f"inputs.{name_input} '{normalized_name}'"
            )
    return issues


def _selection_change_issues(
    payload: dict[str, Any],
    *,
    include_app_chart_dependencies: bool = True,
) -> list[str]:
    issues: list[str] = []
    issues.extend(
        _component_dependency_issues_from_payload(
            payload,
            include_app_chart_dependencies=include_app_chart_dependencies,
        )
    )
    issues.extend(_active_infra_resource_identity_issues(payload))
    issues.extend(_active_component_input_binding_issues(payload))
    issues.extend(_target_bound_app_issues_from_payload(payload))
    return issues


def _write_runtime_payload_config(
    config_path: Path,
    payload: dict[str, Any],
    *,
    overwrite: bool = False,
) -> bool:
    normalize_runtime_config_payload(payload, base_dir=config_path.parent)
    next_config_text = yaml.safe_dump(payload, sort_keys=False)
    current_config_text = None
    if config_path.exists():
        current_config_text = config_path.read_text(encoding="utf-8")
    if current_config_text == next_config_text:
        if not overwrite:
            return False
        config_path.write_text(next_config_text, encoding="utf-8")
        return True
    config_path.write_text(next_config_text, encoding="utf-8")
    return True


def _seed_infra_project_scope_defaults(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> None:
    project_id = _non_empty_text(_read_payload_field(payload, "client_info.nebius.project_id"))
    if not project_id:
        return
    infra_node = payload.get("infra")
    if not isinstance(infra_node, Mapping):
        return
    components = infra_node.get("components")
    if not isinstance(components, list):
        return

    entry_by_id = {entry.id: entry for entry in infra_entries}
    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = _non_empty_text(item.get("id")).lower()
        if not component_id:
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, dict):
            continue
        entry = entry_by_id.get(component_id)
        source = _effective_catalog_component_source(row=item, entry=entry)
        if not source:
            continue
        inspection_source = _entry_module_metadata_source(entry, fallback_source=source)
        leaf_names = {
            _normalize_leaf_name(name) for name in module_variable_names(inspection_source)
        }
        if not leaf_names:
            continue
        if "parent_id" in leaf_names and "parent_id" not in inputs:
            inputs["parent_id"] = project_id
        if "project_id" in leaf_names and "project_id" not in inputs:
            inputs["project_id"] = project_id


def _seed_infra_shared_admin_ssh_public_key(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> None:
    shared_public_key = _non_empty_text(
        read_path_with_catalog(payload, "shared.admin_ssh.public_key")
    )
    if not shared_public_key:
        return
    infra_node = payload.get("infra")
    if not isinstance(infra_node, Mapping):
        return
    components = infra_node.get("components")
    if not isinstance(components, list):
        return

    entry_by_id = {entry.id: entry for entry in infra_entries}
    for item in components:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", False)):
            continue
        component_id = _non_empty_text(item.get("id")).lower()
        if not component_id:
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if _non_empty_text(inputs.get("ssh_public_key")):
            continue
        entry = entry_by_id.get(component_id)
        source = _effective_catalog_component_source(row=item, entry=entry)
        if not source:
            continue
        inspection_source = _entry_module_metadata_source(entry, fallback_source=source)
        leaf_names = {
            _normalize_leaf_name(name) for name in module_variable_names(inspection_source)
        }
        if "ssh_public_key" not in leaf_names:
            continue
        inputs["ssh_public_key"] = shared_public_key


@dataclass(frozen=True)
class CIWorkflowBootstrapResult:
    repo_root: Path
    workflow_file: Path
    wrote_workflow: bool
    replaced_workflow: bool = False


def _ensure_ci_workflow_for_deployments_root(
    *,
    deployments_root: Path,
    cli_ref: str,
) -> CIWorkflowBootstrapResult:
    def _normalize_workflow_text(text: str) -> str:
        normalized = text.replace("\r\n", "\n").rstrip("\n")
        return f"{normalized}\n"

    _assert_not_nested_deployments_root(deployments_root)
    repo_root = _require_git_root(deployments_root)
    workflows_path = repo_root / ".github" / "workflows"
    workflow_file = workflows_path / "nebius-deployments.yml"
    deployments_dir_for_ci = _relative_deployments_dir_for_ci(repo_root, deployments_root)
    discover_target_for_ci = _relative_discover_target_for_ci(repo_root, deployments_root)
    expected_workflow = customer_workflow_yaml(
        deployments_dir=deployments_dir_for_ci,
        discover_target=discover_target_for_ci,
        cli_ref=cli_ref,
    )

    workflows_path.mkdir(parents=True, exist_ok=True)

    workflow_preexisted = workflow_file.exists()
    if workflow_preexisted:
        existing_workflow = workflow_file.read_text(encoding="utf-8")
        if _normalize_workflow_text(existing_workflow) == _normalize_workflow_text(
            expected_workflow
        ):
            return CIWorkflowBootstrapResult(
                repo_root=repo_root,
                workflow_file=workflow_file,
                wrote_workflow=False,
                replaced_workflow=False,
            )

    workflow_file.write_text(expected_workflow, encoding="utf-8")
    return CIWorkflowBootstrapResult(
        repo_root=repo_root,
        workflow_file=workflow_file,
        wrote_workflow=True,
        replaced_workflow=workflow_preexisted,
    )


def _scaffold_instance(
    *,
    base_path: Path,
    client_name: str,
    tenant_folder: str,
    project_folder: str,
    tenant_id: str,
    project_id: str,
    region_id: str,
    email: str | None,
    selected_infra: set[str],
    selected_apps: set[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    force: bool,
    config_yaml: str | None = None,
) -> BootstrapResult:
    deployments_root = _ensure_customer_scaffold(
        base_path=base_path,
    )
    instance_dir = deployments_root / tenant_folder / project_folder
    config_path = instance_dir / "config.yaml"

    if force and instance_dir.exists():
        shutil.rmtree(instance_dir)

    (instance_dir / "generated" / "infra").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "flux").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "inventory").mkdir(parents=True, exist_ok=True)

    wrote_config = False
    rendered_config = config_yaml
    if rendered_config is None and (not config_path.exists() or force):
        rendered_config = starter_config_yaml(
            client_name=client_name,
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            email=email,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )

    if rendered_config is not None:
        parsed_config = yaml.safe_load(rendered_config) or {}
        if not isinstance(parsed_config, dict):
            raise RuntimeError("Generated config template must be a mapping")
        payload = _filter_runtime_payload_for_selected_components(
            payload=parsed_config,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        normalize_runtime_config_payload(payload, base_dir=config_path.parent)
        should_write = (
            force
            or not config_path.exists()
            or (config_path.read_text(encoding="utf-8") != yaml.safe_dump(payload, sort_keys=False))
        )
        if should_write:
            wrote_config = _write_runtime_payload_config(config_path, payload, overwrite=force)

    config = load_config(config_path, persist_normalized=True)
    paths = resolve_project_paths(config_path, deployments_dir_hint=str(deployments_root))
    validate_path_alignment(config, paths)
    return BootstrapResult(
        deployments_root=deployments_root,
        project_path=instance_dir,
        config_path=config_path,
        wrote_config=wrote_config,
    )


@app.command(
    "create",
    short_help="Use DEPLOYMENTS_ROOT to bootstrap one name-based tenant/project folder with config.yaml plus generated/ skeleton.",
)
def create_command(
    target_path: Annotated[
        Path,
        typer.Argument(
            metavar="DEPLOYMENTS_ROOT",
            help=_DEPLOYMENTS_ROOT_ARGUMENT_HELP,
        ),
    ],
    client_name: Annotated[
        str | None,
        typer.Option("--client-name", help="Client slug (lowercase letters/digits/hyphens)"),
    ] = None,
    tenant_id: Annotated[
        str | None, typer.Option("--tenant-id", help="Nebius tenant identifier")
    ] = None,
    project_id: Annotated[
        str | None, typer.Option("--project-id", help="Nebius project identifier")
    ] = None,
    region_id: Annotated[
        str | None,
        typer.Option(
            "--region-id",
            help="Nebius region identifier, for example eu-north1 (defaults to eu-north1)",
        ),
    ] = None,
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            help="Optional notifications email for deploy report updates",
        ),
    ] = None,
    infra_components_opt: Annotated[
        list[str] | None,
        typer.Option(
            "--infra",
            help=(
                "Infra component id(s) to enable (repeat option or pass comma-separated ids; "
                "supports ids or numeric indexes in interactive mode)"
            ),
        ),
    ] = None,
    apps_components_opt: Annotated[
        list[str] | None,
        typer.Option(
            "--app",
            help=(
                "Apps component id(s) to enable (repeat option or pass comma-separated ids; "
                "supports ids or numeric indexes in interactive mode; "
                "requires an enabled MK8s infra target in the same project; "
                "app chart dependencies are auto-resolved when chart metadata is available)"
            ),
        ),
    ] = None,
    app_namespace_opt: Annotated[
        list[str] | None,
        typer.Option(
            "--app-namespace",
            help=(
                "Override app namespace with '<app-id>=<namespace>' (repeatable or comma-separated). "
                "Applies to enabled apps."
            ),
        ),
    ] = None,
    app_releasename_opt: Annotated[
        list[str] | None,
        typer.Option(
            "--app-releasename",
            help=(
                "Override app release name with '<app-id>=<release-name>' "
                "(repeatable or comma-separated). Applies to enabled apps."
            ),
        ),
    ] = None,
    validate_sources: Annotated[
        bool,
        typer.Option(
            "--validate-sources/--no-validate-sources",
            help=(
                "Validate the full component catalog and source settings before create "
                "continues (enabled by default)."
            ),
        ),
    ] = True,
    validate_config: Annotated[
        bool,
        typer.Option(
            "--validate-config/--no-validate-config",
            help=(
                "Run post-write runtime validation against the resulting config.yaml "
                "after create finishes (enabled by default). `--no-validate-config` "
                "skips that validation only; create still runs its warning-only live "
                "quota/capacity assessment."
            ),
        ),
    ] = True,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            help=(
                "Disable wizard mode for automation/CI. "
                "When omitted, create always runs in wizard mode."
            ),
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Overwrite the resolved existing project folder from scratch using the "
                "current create inputs and component selections. Existing component values, "
                "generated artifacts, and other files under that project folder are not "
                "preserved. Does not delete the deployments root or other projects."
            ),
        ),
    ] = False,
) -> None:
    """Use DEPLOYMENTS_ROOT to bootstrap one name-based tenant/project folder with config.yaml plus generated/ skeleton, or overwrite an existing resolved project folder from scratch after confirmation.

    The post-create quota/capacity assessment is warning-only. It is not a
    reservation, not a quota request, and not a wizard-selectable deployment
    gate.
    """
    try:
        base_path = target_path.resolve()
        _validate_deployments_root_target(base_path)
        deployments_root = _resolve_deployments_root(base_path)
        _assert_not_nested_deployments_root(deployments_root)
        interactive_mode = not no_interactive
        if validate_sources:
            _preflight_component_source_tools_or_raise()
        resolved_tenant_id = _value_or_prompt(
            tenant_id,
            option_name="--tenant-id",
            prompt_text="Tenant ID",
            interactive=interactive_mode,
            default_value=None,
        )
        resolved_project_id = _value_or_prompt(
            project_id,
            option_name="--project-id",
            prompt_text="Project ID",
            interactive=interactive_mode,
            default_value=None,
        )
        provider_lookup = ProviderOptionLookup()
        resolved_tenant_id, resolved_project_id = _validate_tenant_project_ids_or_prompt(
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            interactive=interactive_mode,
            provider_lookup=provider_lookup,
        )
        resolved_tenant_folder, resolved_project_folder = _resolve_create_target_folders(
            provider_lookup=provider_lookup,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
        )

        existing_config_path = _project_config_path(
            deployments_root=deployments_root,
            tenant_folder=resolved_tenant_folder,
            project_folder=resolved_project_folder,
        )
        had_existing_config = existing_config_path.exists()
        source_validation_ran = False
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
            ) = _identity_values_from_payload(loaded_payload)
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
                _validate_component_sources_or_raise()
                source_validation_ran = True
            if interactive_mode:
                if not _confirm_existing_project_overwrite(config_path=existing_config_path):
                    console.print("No changes applied.")
                    return
            else:
                _warn_existing_project_overwrite(config_path=existing_config_path)
                console.print(
                    "[dim]`--force` confirms the overwrite in non-interactive mode. "
                    "This only affects that one resolved project folder.[/dim]"
                )
        if validate_sources and not source_validation_ran:
            _validate_component_sources_or_raise()

        resolved_client_name = _client_name_or_prompt(
            client_name,
            interactive=interactive_mode,
        )
        resolved_region_id = _region_or_prompt(
            region_id or None,
            interactive=interactive_mode,
        )
        resolved_email = _optional_email_or_prompt(
            email,
            interactive=interactive_mode,
        )

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")

        optional_wizard_mode = interactive_mode
        if interactive_mode:
            selected_infra_raw: set[str] = set()
            selected_apps_raw: set[str] = set()
            while True:
                optional_decision = _wizard_continue_phase(
                    "Continue with optional wizard phases (component selection and fields)?",
                    default=True,
                )
                if _wizard_phase_stop_requested(optional_decision) or not optional_decision:
                    optional_wizard_mode = False
                    selected_infra_raw = _resolve_component_ids(
                        scope="infra",
                        raw_values=infra_components_opt,
                        interactive=False,
                        entries=infra_entries,
                    )
                    selected_apps_raw = _resolve_component_ids(
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
                                selected_infra_raw = _resolve_component_ids(
                                    scope="infra",
                                    raw_values=infra_components_opt,
                                    interactive=True,
                                    entries=infra_entries,
                                    seed_defaults=selected_infra_raw or None,
                                )
                                selection_stage = "apps"
                            selected_apps_raw = _resolve_component_ids(
                                scope="apps",
                                raw_values=apps_components_opt,
                                interactive=True,
                                entries=app_entries,
                                seed_defaults=selected_apps_raw or None,
                            )
                            app_target_issue = _app_selection_without_cluster_target_issue(
                                selected_infra=selected_infra_raw,
                                selected_apps=selected_apps_raw,
                                infra_entries=infra_entries,
                            )
                            if app_target_issue:
                                console.print(
                                    f"{warning_markup('Invalid app selection:')} {app_target_issue}"
                                )
                                if infra_components_opt is not None:
                                    raise RuntimeError(app_target_issue)
                                selection_stage = "infra"
                                continue
                            break
                        except _WizardBackRequested:
                            if selection_stage == "apps":
                                selection_stage = "infra"
                                continue
                            raise
                except _WizardBackRequested:
                    continue
                except _WizardQuitRequested:
                    optional_wizard_mode = False
                    selected_infra_raw = _resolve_component_ids(
                        scope="infra",
                        raw_values=infra_components_opt,
                        interactive=False,
                        entries=infra_entries,
                    )
                    selected_apps_raw = _resolve_component_ids(
                        scope="apps",
                        raw_values=apps_components_opt,
                        interactive=False,
                        entries=app_entries,
                    )
                    break
                break
        else:
            selected_infra_raw = _resolve_component_ids(
                scope="infra",
                raw_values=infra_components_opt,
                interactive=False,
                entries=infra_entries,
            )
            selected_apps_raw = _resolve_component_ids(
                scope="apps",
                raw_values=apps_components_opt,
                interactive=False,
                entries=app_entries,
            )
        app_namespace_overrides = _parse_component_value_overrides(
            raw_values=app_namespace_opt,
            option_name="--app-namespace",
        )
        app_releasename_overrides = _parse_component_value_overrides(
            raw_values=app_releasename_opt,
            option_name="--app-releasename",
        )
        selected_infra_raw = _expand_soperator_component_selection(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        selected_apps_raw = _expand_soperator_app_selection(
            selected_apps=selected_apps_raw,
            app_entries=app_entries,
        )
        app_target_issue = _app_selection_without_cluster_target_issue(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        if app_target_issue:
            raise RuntimeError(app_target_issue)

        dependency_seed_payload = _dependency_seed_payload(
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
        )

        dependency_resolution_started = time.monotonic()
        if selected_apps_raw:
            with console.status("[cyan]Resolving app chart dependencies...[/cyan]"):
                selected_infra, selected_apps = _normalize_component_dependencies(
                    selected_infra=selected_infra_raw,
                    selected_apps=selected_apps_raw,
                    infra_entries=infra_entries,
                    app_entries=app_entries,
                    payload_for_app_chart_deps=dependency_seed_payload,
                )
        else:
            selected_infra, selected_apps = _normalize_component_dependencies(
                selected_infra=selected_infra_raw,
                selected_apps=selected_apps_raw,
                infra_entries=infra_entries,
                app_entries=app_entries,
                payload_for_app_chart_deps=dependency_seed_payload,
            )
        dependency_resolution_elapsed = time.monotonic() - dependency_resolution_started
        if dependency_resolution_elapsed >= 1:
            console.print(
                "[dim]App dependency resolution finished in "
                f"{dependency_resolution_elapsed:.1f}s[/dim]"
            )

        wizard_completed = True
        starter_payload = _starter_component_payload(
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
        )
        final_payload = starter_payload
        _materialize_singleton_provider_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_mk8s_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_vm_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        materialize_compute_boot_disk_defaults(
            final_payload,
            provider_lookup=provider_lookup,
        )
        selected_apps, mysterybox_eso_app_labels = _ensure_mysterybox_eso_app_dependency_selection(
            final_payload,
            selected_apps=selected_apps,
            app_entries=app_entries,
        )
        _print_mysterybox_eso_app_dependency_adjustment(mysterybox_eso_app_labels)
        if interactive_mode:
            _print_component_selection_summary(
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )

        if interactive_mode and optional_wizard_mode:
            config_yaml_override, wizard_completed = _run_component_field_wizard(
                config_yaml=yaml.safe_dump(final_payload, sort_keys=False),
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                provider_lookup=provider_lookup,
            )
            parsed_override = yaml.safe_load(config_yaml_override) or {}
            if not isinstance(parsed_override, dict):
                raise RuntimeError("Updated config payload must be a mapping")
            final_payload = parsed_override
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )

        _materialize_soperator_component_defaults(final_payload)
        _materialize_mk8s_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_vm_image_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_singleton_provider_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        materialize_compute_boot_disk_defaults(
            final_payload,
            provider_lookup=provider_lookup,
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
            auto_enabled_seed = _starter_component_payload(
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
            )
            _ensure_payload_contains_component_rows(
                payload=final_payload,
                seed_payload=auto_enabled_seed,
            )
            console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(f"'apps:{item}'" for item in gpu_app_selection.auto_enabled_app_ids)
                + " because the selected MK8s GPU configuration requires them."
            )
        if ensure_mk8s_gpu_app_rows(final_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
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
            auto_enabled_seed = _starter_component_payload(
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
            )
            _ensure_payload_contains_component_rows(
                payload=final_payload,
                seed_payload=auto_enabled_seed,
            )
            console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(
                    f"'apps:{item}'" for item in observability_selection.auto_enabled_app_ids
                )
                + " because the selected observability configuration requires them."
            )
        _align_new_infra_instance_ids_with_resource_names(final_payload)
        _materialize_soperator_component_defaults(final_payload)
        if ensure_mysterybox_eso_app_rows(final_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )
        if ensure_nfs_csi_app_rows(final_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=final_payload,
                entries=app_entries,
            )
        materialize_mk8s_gpu_app_values(final_payload)
        materialize_soperator_companion_app_values(final_payload)
        materialize_observability_infra_values(final_payload)

        create_required_field_issues = (
            _wizard_followup_required_field_issues(
                payload=final_payload,
                infra_entries=infra_entries,
            )
            if interactive_mode and (not optional_wizard_mode or not wizard_completed)
            else []
        )
        if create_required_field_issues:
            _print_incomplete_wizard_no_write_warning(
                issues=create_required_field_issues,
                message="No project config or generated output was written.",
                preserved_path=existing_config_path.parent if had_existing_config else None,
                skipped_path=existing_config_path.parent if not had_existing_config else None,
            )
            raise typer.Exit(code=1)
        _prune_redundant_app_chart_default_values(
            payload=final_payload,
            app_entries=app_entries,
        )

        result = _scaffold_instance(
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
        gitignore_result = _ensure_deployments_gitignore(
            deployments_root=result.deployments_root,
        )

        console.print(f"Deployments root: {result.deployments_root}")
        if gitignore_result.path is not None:
            if gitignore_result.wrote:
                console.print(f"Ensured deployments .gitignore: {gitignore_result.path}")
            else:
                console.print(f"Deployments .gitignore up-to-date: {gitignore_result.path}")
        if result.wrote_config:
            if had_existing_config:
                console.print(f"Overwritten project: {result.project_path}")
            else:
                console.print(f"Created project: {result.project_path}")
        else:
            if had_existing_config:
                console.print(
                    f"Project already matched the overwrite target: {result.project_path}"
                )
            else:
                console.print(f"Config up-to-date: {result.config_path}")
        if validate_config:
            _run_runtime_validation(
                config_path=result.config_path,
                strict=False,
                title="Post-create validation",
            )
        quota_report = _warn_on_live_quota_issues(final_payload, phase="create")
        if quota_report.has_confirmed_insufficiency:
            console.print(
                f"{warning_markup('Create completed with quota warnings.')} "
                "Render can continue, but deploy will fail until the required quota is available "
                "and any selected GPU shape has matching Capacity Dashboard capacity."
            )
            _print_quota_remediation_hint(result.config_path, quota_report)
        if not validate_config:
            _print_mk8s_gpu_validation_warnings(final_payload)
        console.print(
            "Enabled infra components: "
            + (", ".join(sorted(selected_infra)) if selected_infra else "(none)")
        )
        console.print(
            "Enabled apps components: "
            + (", ".join(sorted(selected_apps)) if selected_apps else "(none)")
        )
        if _active_chart_count(final_payload) > 0 and _config_uses_private_cluster_handoff(
            final_payload
        ):
            console.print(f"[yellow]NOTE:[/yellow] {_private_cluster_handoff_note()}")
        console.print(f"Ensured generated skeleton: {result.config_path.parent / 'generated'}")
        _print_create_next_steps(result.config_path)
        console.print(
            f"{warning_markup('Security warning:')} keep this customer repository private "
            "because the deployments root contains sensitive operational metadata."
        )
    except typer.Exit:
        raise
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@component_app.command(
    "list",
    short_help="Use --config CONFIG_YAML to inspect enabled instances and available catalog components.",
)
def component_list_command(
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            metavar="CONFIG_YAML",
            help=_COMPONENT_CONFIG_OPTION_HELP,
        ),
    ] = None,
) -> None:
    """List enabled component instances and reusable catalog component types.

    Flags:

      --config <config.yaml>

    Examples:

      nebius-cxcli component list --config <config.yaml>
    """
    try:
        config_path = _require_component_config_option(config_path)
        _config, _paths = _load_context_readonly(config_path)
        payload = to_plain_data(_config)
        if not isinstance(payload, dict):
            raise RuntimeError("config.yaml root must be a mapping")
        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
        enabled_infra_specs = _enabled_component_instance_specs(
            payload,
            scope="infra",
            entries=infra_entries,
        )
        enabled_apps_specs = _enabled_component_instance_specs(
            payload,
            scope="apps",
            entries=app_entries,
        )

        console.print(f"Config: {config_path.resolve()}")
        console.print()

        sections = (
            ("Enabled infra component instances", enabled_infra_specs),
            ("Enabled apps component instances", enabled_apps_specs),
        )
        for heading, specs in sections:
            console.print(f"{heading}:")
            if not specs:
                console.print("  (none)")
                console.print()
            else:
                for entry, row in specs:
                    target_suffix = ""
                    if entry.scope == "apps":
                        target_ref = app_chart_target_ref(row)
                        if target_ref:
                            target_suffix = f" on {target_ref}"
                    console.print(
                        f"  {_component_instance_selector_label(entry, instance_id=str(row['instance_id']))}"
                        f"{target_suffix}"
                        f"  ({entry.description})"
                    )
                console.print()

        available_sections = (
            ("Available infra components", infra_entries),
            ("Available apps components", app_entries),
        )
        for heading, entries in available_sections:
            console.print(f"{heading}:")
            if not entries:
                console.print("  (none)")
                console.print()
                continue
            for entry in entries:
                console.print(
                    f"  {_component_selector_label(entry, scope=entry.scope)}"
                    f"  ({entry.description})"
                )
            console.print()
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@component_app.command(
    "add",
    short_help="Add component selectors to --config CONFIG_YAML.",
)
def component_add_command(
    component_ids: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[COMPONENT_SELECTOR]...",
            help=(
                "Optional infra module or app chart selector(s) to add. Use "
                "'<id>', 'infra:<id>', 'apps:<id>', 'all', 'none', or "
                "'<id>@<resource-name-or-target-id>'. Omit to prompt "
                "interactively; infra-only interactive adds are valid. "
                "In interactive mode, scalar named infra modules prompt for the "
                "resource name first and derive the saved instance_id from that "
                "name. In non-interactive mode, bare infra selectors create the "
                "default named row when absent; use '<id>@<resource-name>' to "
                "choose the resource name or create another named infra row. "
                "For target-bound app charts, use '<app-id>@<target-id>'; one chart row "
                "is allowed per app id and cluster target, and the suffix becomes the "
                "app row instance_id. Apps are Helm charts and require an enabled MK8s "
                "target in the same project."
            ),
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            metavar="CONFIG_YAML",
            help=_COMPONENT_CONFIG_OPTION_HELP,
        ),
    ] = None,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            help="Disable interactive selection and field prompts.",
        ),
    ] = False,
    validate_sources: Annotated[
        bool,
        typer.Option(
            "--validate-sources/--no-validate-sources",
            help=(
                "Validate the full component catalog and source settings before "
                "component add continues (enabled by default)."
            ),
        ),
    ] = True,
) -> None:
    """Add source-defined components to an existing project config.yaml.

    Flags:

      --config <config.yaml>
      --no-interactive
      --validate-sources --no-validate-sources

    Examples:

      nebius-cxcli component add --config <config.yaml>

      nebius-cxcli component add infra:vm --config <config.yaml>

      nebius-cxcli component add infra:vm@worker-vm --config <config.yaml> --no-interactive

      nebius-cxcli component add managed-postgresql object-storage@logs-bucket --config <config.yaml> --no-interactive

      nebius-cxcli component add gateway-helm@serving-cluster --config <config.yaml> --no-interactive
    """
    try:
        config_path = _require_component_config_option(config_path)
        _config, _paths = _load_context(config_path)
        if validate_sources:
            _validate_component_sources_or_raise()
        payload = _load_config_payload(config_path.resolve())
        interactive_mode = not no_interactive
        client_name, tenant_id, project_id, region_id, email = _identity_values_from_payload(
            payload
        )
        provider_lookup = ProviderOptionLookup()
        provider_scope_validated = False

        def _ensure_provider_scope_validated() -> None:
            nonlocal tenant_id, project_id, provider_scope_validated
            if provider_scope_validated:
                return
            if interactive_mode:
                console.print(
                    "[dim]Validating Nebius tenant/project scope before "
                    "provider-backed field prompts...[/dim]"
                )
            tenant_id, project_id = _validate_tenant_project_ids_or_prompt(
                tenant_id=tenant_id,
                project_id=project_id,
                interactive=False,
                provider_lookup=provider_lookup,
            )
            provider_scope_validated = True

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
        enabled_infra = _enabled_ids_from_runtime_payload(payload=payload, entries=infra_entries)
        enabled_apps = _enabled_ids_from_runtime_payload(payload=payload, entries=app_entries)

        raw_tokens = _split_multi_value_tokens(component_ids)
        if not raw_tokens and interactive_mode:
            while True:
                try:
                    requested_infra = _prompt_component_scope_selection(
                        action="add",
                        scope="infra",
                        entries=infra_entries,
                    )
                except (_WizardBackRequested, _WizardQuitRequested):
                    console.print("No component changes applied.")
                    return
                requested_apps: set[str] = set()
                select_apps = not requested_infra
                if requested_infra:
                    apps_decision = _wizard_continue_phase(
                        "Select apps components to add too?",
                        default=False,
                        allow_back=True,
                    )
                    if _wizard_phase_back_requested(apps_decision):
                        continue
                    if _wizard_phase_stop_requested(apps_decision):
                        console.print("No component changes applied.")
                        return
                    select_apps = bool(apps_decision)
                if select_apps:
                    try:
                        requested_apps = _prompt_component_scope_selection(
                            action="add",
                            scope="apps",
                            entries=app_entries,
                        )
                    except _WizardBackRequested:
                        continue
                    except _WizardQuitRequested:
                        console.print("No component changes applied.")
                        return
                app_target_issue = _app_selection_without_cluster_target_issue(
                    selected_infra=set(enabled_infra) | requested_infra,
                    selected_apps=set(enabled_apps) | requested_apps,
                    infra_entries=infra_entries,
                )
                if app_target_issue:
                    console.print(f"{warning_markup('Invalid app selection:')} {app_target_issue}")
                    continue
                break
            add_targets = [
                *(
                    _ComponentAddTarget(
                        scope="infra",
                        component_id=component_id,
                        allocate_new_infra_instance_if_enabled=True,
                    )
                    for component_id in sorted(requested_infra)
                ),
                *(
                    _ComponentAddTarget(scope="apps", component_id=component_id)
                    for component_id in sorted(requested_apps)
                ),
            ]
        elif not raw_tokens and not interactive_mode:
            raise RuntimeError(
                "Specify at least one component id, or omit --no-interactive to choose components interactively."
            )
        else:
            add_targets = _resolve_component_add_targets(
                tokens=raw_tokens,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )

        if not add_targets:
            console.print("No components selected for add.")
            return

        requested_infra_types = {
            target.component_id for target in add_targets if target.scope == "infra"
        }
        requested_apps_types = {
            target.component_id for target in add_targets if target.scope == "apps"
        }
        selected_infra_raw = set(enabled_infra) | requested_infra_types
        selected_apps_raw = set(enabled_apps) | requested_apps_types
        selected_infra_raw = _expand_soperator_component_selection(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        selected_apps_raw = _expand_soperator_app_selection(
            selected_apps=selected_apps_raw,
            app_entries=app_entries,
        )
        app_target_issue = _app_selection_without_cluster_target_issue(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
        )
        if app_target_issue:
            raise RuntimeError(app_target_issue)
        dependency_seed_payload = _dependency_seed_payload(
            client_name=client_name,
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            email=email,
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
            app_entries=app_entries,
            existing_payload=payload,
            merge_existing=True,
        )
        dependency_resolution_started = time.monotonic()
        app_chart_dependency_payload = dependency_seed_payload if requested_apps_types else None
        selected_infra, selected_apps = _normalize_component_dependencies(
            selected_infra=selected_infra_raw,
            selected_apps=selected_apps_raw,
            infra_entries=infra_entries,
            app_entries=app_entries,
            payload_for_app_chart_deps=app_chart_dependency_payload,
        )
        dependency_resolution_elapsed = time.monotonic() - dependency_resolution_started
        if dependency_resolution_elapsed >= 1:
            console.print(
                "[dim]App dependency resolution finished in "
                f"{dependency_resolution_elapsed:.1f}s[/dim]"
            )

        auto_added_infra_types = (selected_infra - enabled_infra) - requested_infra_types
        auto_added_apps_types = (selected_apps - enabled_apps) - requested_apps_types
        add_targets.extend(
            _ComponentAddTarget(scope="infra", component_id=component_id)
            for component_id in sorted(auto_added_infra_types)
        )
        add_targets.extend(
            _ComponentAddTarget(scope="apps", component_id=component_id)
            for component_id in sorted(auto_added_apps_types)
        )

        if interactive_mode:
            resolved_add_targets = _prompt_infra_add_resource_names(
                payload=payload,
                add_targets=add_targets,
                infra_entries=infra_entries,
            )
            if resolved_add_targets is None:
                console.print("No component changes applied.")
                return
            add_targets = resolved_add_targets

        if interactive_mode and not _wizard_continue_phase(
            "Add selected components to config.yaml now?",
            default=True,
        ):
            console.print("No component changes applied.")
            return

        next_payload = copy.deepcopy(payload)
        _scope_rows(next_payload, scope="infra")
        _scope_rows(next_payload, scope="apps")

        added_infra_instances: list[str] = []
        added_apps_instances: list[str] = []
        added_apps_selectors: list[str] = []
        added_infra_labels: list[str] = []
        added_apps_labels: list[str] = []
        skipped_add_labels: list[str] = []
        infra_lookup = {entry.id: entry for entry in infra_entries}
        app_lookup = {entry.id: entry for entry in app_entries}
        for target in add_targets:
            entry = (
                infra_lookup.get(target.component_id)
                if target.scope == "infra"
                else app_lookup.get(target.component_id)
            )
            if entry is None:
                raise RuntimeError(
                    f"Component '{target.component_id}' is no longer available in the active catalog."
                )
            existing_label = _enabled_component_add_label(
                payload=next_payload,
                entry=entry,
                requested_instance_id=target.requested_instance_id,
                allow_unassigned_app_target=interactive_mode,
            )
            if (
                existing_label
                and target.scope == "infra"
                and target.requested_instance_id is None
                and target.allocate_new_infra_instance_if_enabled
                and interactive_mode
            ):
                existing_label = None
            if existing_label:
                if existing_label not in skipped_add_labels:
                    skipped_add_labels.append(existing_label)
                continue
            row = _append_component_instance_row(
                payload=next_payload,
                entry=entry,
                requested_instance_id=target.requested_instance_id,
                allow_unassigned_app_target=interactive_mode,
            )
            instance_id = component_instance_id(row)
            if target.scope == "infra":
                added_infra_instances.append(instance_id)
                added_infra_labels.append(component_instance_label(entry.id, instance_id))
            else:
                added_apps_instances.append(instance_id)
                app_label = component_instance_label(entry.id, instance_id)
                added_apps_selectors.append(app_label)
                added_apps_labels.append(app_label)
        if not added_infra_instances and not added_apps_instances:
            console.print(f"Config up-to-date: {config_path.resolve()}")
            console.print("Added infra components: (none)")
            console.print("Added apps components: (none)")
            if skipped_add_labels:
                console.print(
                    f"{warning_markup('Skipped already-enabled components:')} "
                    + ", ".join(skipped_add_labels)
                )
            _print_component_edit_config_only_note()
            _print_component_edit_next_steps(config_path)
            return

        _ensure_provider_scope_validated()
        _seed_infra_project_scope_defaults(
            payload=next_payload,
            infra_entries=infra_entries,
        )
        _seed_infra_shared_admin_ssh_public_key(
            payload=next_payload,
            infra_entries=infra_entries,
        )
        materialize_shared_defaults(
            payload=next_payload,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        wizard_completed = True
        _materialize_singleton_provider_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_mk8s_image_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_vm_image_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        materialize_compute_boot_disk_defaults(
            next_payload,
            provider_lookup=provider_lookup,
        )
        selected_apps, mysterybox_eso_app_labels = _ensure_mysterybox_eso_app_dependency_selection(
            next_payload,
            selected_apps=selected_apps,
            app_entries=app_entries,
        )
        if mysterybox_eso_app_labels:
            for app_label in mysterybox_eso_app_labels:
                if app_label not in added_apps_selectors:
                    added_apps_selectors.append(app_label)
                if app_label not in added_apps_labels:
                    added_apps_labels.append(app_label)
            _print_mysterybox_eso_app_dependency_adjustment(mysterybox_eso_app_labels)
        config_yaml_override = yaml.safe_dump(next_payload, sort_keys=False)
        if interactive_mode:
            config_yaml_override, wizard_completed = _run_component_field_wizard(
                config_yaml=config_yaml_override,
                selected_infra=set(added_infra_instances),
                selected_apps=set(added_apps_selectors),
                infra_entries=infra_entries,
                app_entries=app_entries,
                provider_lookup=provider_lookup,
            )
            parsed_override = yaml.safe_load(config_yaml_override) or {}
            if not isinstance(parsed_override, dict):
                raise RuntimeError("Updated config payload must be a mapping")
            next_payload = parsed_override
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=next_payload,
                entries=app_entries,
            )

        _materialize_soperator_component_defaults(next_payload)
        _materialize_mk8s_image_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_vm_image_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        _materialize_singleton_provider_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )
        materialize_compute_boot_disk_defaults(
            next_payload,
            provider_lookup=provider_lookup,
        )
        gpu_app_selection = resolve_mk8s_gpu_app_selection(
            next_payload,
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
            (
                identity_client_name,
                identity_tenant_id,
                identity_project_id,
                identity_region_id,
                identity_email,
            ) = _identity_values_from_payload(next_payload)
            auto_enabled_seed = _starter_component_payload(
                client_name=identity_client_name,
                tenant_id=identity_tenant_id,
                project_id=identity_project_id,
                region_id=identity_region_id,
                email=identity_email,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            _ensure_payload_contains_component_rows(
                payload=next_payload,
                seed_payload=auto_enabled_seed,
            )
            next_payload = _filter_runtime_payload_for_selected_components(
                payload=next_payload,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            for component_id in gpu_app_selection.auto_enabled_app_ids:
                added_apps_labels.append(component_instance_label(component_id, component_id))
            console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(f"'apps:{item}'" for item in gpu_app_selection.auto_enabled_app_ids)
                + " because the selected MK8s GPU configuration requires them."
            )
        before_gpu_app_instances = {
            component_instance_id(row)
            for row in next_payload.get("apps", {}).get("charts", [])
            if isinstance(row, dict)
        }
        if ensure_mk8s_gpu_app_rows(next_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=next_payload,
                entries=app_entries,
            )
            for row in next_payload.get("apps", {}).get("charts", []):
                if not isinstance(row, dict):
                    continue
                instance_id = component_instance_id(row)
                if instance_id in before_gpu_app_instances:
                    continue
                added_apps_labels.append(
                    component_instance_label(component_type_id(row), instance_id)
                )
        observability_selection = resolve_observability_app_selection(
            next_payload,
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
            (
                identity_client_name,
                identity_tenant_id,
                identity_project_id,
                identity_region_id,
                identity_email,
            ) = _identity_values_from_payload(next_payload)
            auto_enabled_seed = _starter_component_payload(
                client_name=identity_client_name,
                tenant_id=identity_tenant_id,
                project_id=identity_project_id,
                region_id=identity_region_id,
                email=identity_email,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            _ensure_payload_contains_component_rows(
                payload=next_payload,
                seed_payload=auto_enabled_seed,
            )
            next_payload = _filter_runtime_payload_for_selected_components(
                payload=next_payload,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            for component_id in observability_selection.auto_enabled_app_ids:
                added_apps_labels.append(component_instance_label(component_id, component_id))
            console.print(
                f"{warning_markup('Adjusted component selection:')} enabling "
                + ", ".join(
                    f"'apps:{item}'" for item in observability_selection.auto_enabled_app_ids
                )
                + " because the selected observability configuration requires them."
            )
        infra_renames = _align_new_infra_instance_ids_with_resource_names(
            next_payload,
            selected_instance_ids=set(added_infra_instances),
        )
        if infra_renames:
            added_infra_instances = [
                infra_renames.get(instance_id, instance_id) for instance_id in added_infra_instances
            ]
            added_infra_labels = [
                component_instance_label(component_type_id(row), component_instance_id(row))
                for row in next_payload.get("infra", {}).get("components", [])
                if isinstance(row, dict) and component_instance_id(row) in added_infra_instances
            ]
        _materialize_single_target_app_bindings(next_payload)
        _materialize_soperator_component_defaults(next_payload)
        if ensure_mysterybox_eso_app_rows(next_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=next_payload,
                entries=app_entries,
            )
        before_nfs_csi_app_instances = {
            (component_type_id(row), component_instance_id(row))
            for row in next_payload.get("apps", {}).get("charts", [])
            if isinstance(row, dict)
        }
        if ensure_nfs_csi_app_rows(next_payload, app_entries=app_entries):
            selected_apps = _enabled_ids_from_runtime_payload(
                payload=next_payload,
                entries=app_entries,
            )
            new_nfs_csi_labels: list[str] = []
            for row in next_payload.get("apps", {}).get("charts", []):
                if not isinstance(row, dict):
                    continue
                key = (component_type_id(row), component_instance_id(row))
                if key in before_nfs_csi_app_instances or key[0] != "csi-driver-nfs":
                    continue
                label = component_instance_label(key[0], key[1])
                if label not in added_apps_labels:
                    added_apps_labels.append(label)
                new_nfs_csi_labels.append(label)
            if new_nfs_csi_labels:
                console.print(
                    f"{warning_markup('Adjusted component selection:')} enabling "
                    + ", ".join(new_nfs_csi_labels)
                    + " because VM-backed NFS for MK8s requires the NFS CSI driver."
                )
        materialize_mk8s_gpu_app_values(next_payload)
        materialize_soperator_companion_app_values(next_payload)
        materialize_observability_infra_values(next_payload)

        add_required_field_issues = (
            _wizard_followup_required_field_issues(
                payload=next_payload,
                infra_entries=infra_entries,
            )
            if interactive_mode and not wizard_completed
            else []
        )
        if add_required_field_issues:
            _print_incomplete_wizard_no_write_warning(
                issues=add_required_field_issues,
                message="No config.yaml changes were written.",
                preserved_path=config_path.resolve(),
            )
            raise typer.Exit(code=1)

        selection_issues = _selection_change_issues(
            next_payload,
            include_app_chart_dependencies=bool(requested_apps_types),
        )
        if selection_issues:
            raise RuntimeError(
                "Component add would leave config.yaml with unresolved dependencies:\n  - "
                + "\n  - ".join(selection_issues)
            )
        _prune_redundant_app_chart_default_values(
            payload=next_payload,
            app_entries=app_entries,
        )

        wrote_config = _write_runtime_payload_config(config_path.resolve(), next_payload)
        if wrote_config:
            console.print(f"Updated: {config_path.resolve()}")
        else:
            console.print(f"Config up-to-date: {config_path.resolve()}")
        console.print(
            "Added infra components: "
            + (", ".join(added_infra_labels) if added_infra_labels else "(none)")
        )
        console.print(
            "Added apps components: "
            + (", ".join(added_apps_labels) if added_apps_labels else "(none)")
        )
        if skipped_add_labels:
            console.print(
                f"{warning_markup('Skipped already-enabled components:')} "
                + ", ".join(skipped_add_labels)
            )
        if _active_chart_count(next_payload) > 0 and _config_uses_private_cluster_handoff(
            next_payload
        ):
            console.print(f"[yellow]NOTE:[/yellow] {_private_cluster_handoff_note()}")
        _print_component_edit_config_only_note()
        _print_component_edit_next_steps(config_path)
    except typer.Exit:
        raise
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@component_app.command(
    "remove",
    short_help="Remove component selectors from --config CONFIG_YAML.",
)
def component_remove_command(
    component_ids: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[COMPONENT_SELECTOR]...",
            help=(
                "Optional enabled infra module or app chart selector(s) to remove. Use "
                "'<id>', 'infra:<id>', 'apps:<id>', 'all', 'none', '<row-id>', or "
                "'<id>@<resource-name-or-target-id>'. For scalar named infra, the "
                "row id is the normalized resource name; for target-bound app charts, "
                "it is the target id. Omit to prompt interactively. Already-absent "
                "selectors are skipped. If multiple rows match the same component "
                "type, pass the exact row id or '<id>@<resource-name-or-target-id>' "
                "to remove one config.yaml row. Removing a cluster target also "
                "removes app rows and deploy.targets[] settings bound to that target."
            ),
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            metavar="CONFIG_YAML",
            help=_COMPONENT_CONFIG_OPTION_HELP,
        ),
    ] = None,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            help="Disable interactive selection and confirmation prompts.",
        ),
    ] = False,
) -> None:
    """Remove enabled component rows from an existing project config.yaml.

    Flags:

      --config <config.yaml>
      --no-interactive

    Examples:

      nebius-cxcli component remove vm@worker-vm --config <config.yaml> --no-interactive

      nebius-cxcli component remove managed-postgresql@analytics-pg --config <config.yaml> --no-interactive

      nebius-cxcli component remove gateway-helm@serving-cluster --config <config.yaml> --no-interactive
    """
    try:
        config_path = _require_component_config_option(config_path)
        _config, _paths = _load_context(config_path)
        payload = _load_config_payload(config_path.resolve())
        interactive_mode = not no_interactive

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
        infra_lookup = {entry.id: entry for entry in infra_entries}
        enabled_infra_specs = _enabled_component_instance_specs(
            payload,
            scope="infra",
            entries=infra_entries,
        )
        enabled_app_specs = _enabled_component_instance_specs(
            payload,
            scope="apps",
            entries=app_entries,
        )

        raw_tokens = _split_multi_value_tokens(component_ids)
        skipped: tuple[str, ...] = ()
        if not raw_tokens and interactive_mode:
            requested_infra_instances = _prompt_component_instance_selection(
                action="remove",
                scope="infra",
                specs=tuple(enabled_infra_specs),
            )
            requested_app_instances = _prompt_component_instance_selection(
                action="remove",
                scope="apps",
                specs=tuple(enabled_app_specs),
            )
            remove_targets = [
                *(
                    _ComponentRemoveTarget(
                        scope="infra",
                        component_id=str(row["id"]),
                        instance_id=str(row["instance_id"]),
                    )
                    for _entry, row in enabled_infra_specs
                    if str(row["instance_id"]) in requested_infra_instances
                ),
                *(
                    _ComponentRemoveTarget(
                        scope="apps",
                        component_id=str(row["id"]),
                        instance_id=str(row["instance_id"]),
                    )
                    for _entry, row in enabled_app_specs
                    if str(row["instance_id"]) in requested_app_instances
                ),
            ]
        elif not raw_tokens and not interactive_mode:
            raise RuntimeError(
                "Specify at least one component id, or omit --no-interactive to choose components interactively."
            )
        else:
            remove_targets, skipped = _resolve_component_remove_targets(
                tokens=raw_tokens,
                payload=payload,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            for component_id in skipped:
                console.print(
                    f"{warning_markup('Skipped already-absent component:')} {component_id}"
                )

        if not remove_targets:
            console.print("No components selected for remove.")
            if skipped:
                console.print(f"Config up-to-date: {config_path.resolve()}")
                _print_component_edit_next_steps(config_path)
            return

        if interactive_mode and not _wizard_continue_phase(
            "Remove selected components from config.yaml now?",
            default=False,
        ):
            console.print("No component changes applied.")
            return

        next_payload = copy.deepcopy(payload)
        removed_infra_labels: list[str] = []
        removed_app_labels: list[str] = []
        removed_cluster_targets: set[str] = set()
        for target in remove_targets:
            removed = _remove_component_instance_row(
                payload=next_payload,
                scope=target.scope,
                instance_id=target.instance_id,
                component_id=target.component_id,
            )
            if removed is None:
                continue
            label = component_instance_label(target.component_id, target.instance_id)
            if target.scope == "infra":
                removed_infra_labels.append(label)
                removed_entry = infra_lookup.get(target.component_id)
                if removed_entry is not None and removed_entry.handoff is not None:
                    removed_cluster_targets.add(target.instance_id)
            else:
                removed_app_labels.append(label)
        if removed_cluster_targets:
            removed_target_app_labels = _remove_target_scoped_app_rows(
                payload=next_payload,
                target_instance_ids=removed_cluster_targets,
            )
            for label in removed_target_app_labels:
                if label not in removed_app_labels:
                    removed_app_labels.append(label)
            _remove_deploy_target_rows(
                payload=next_payload,
                target_instance_ids=removed_cluster_targets,
            )
        selection_issues = _selection_change_issues(next_payload)
        if selection_issues:
            raise RuntimeError(
                "Component remove would leave config.yaml with unresolved dependencies:\n  - "
                + "\n  - ".join(selection_issues)
            )

        wrote_config = _write_runtime_payload_config(config_path.resolve(), next_payload)
        if wrote_config:
            console.print(f"Updated: {config_path.resolve()}")
        else:
            console.print(f"Config up-to-date: {config_path.resolve()}")
        console.print(
            "Removed infra components: "
            + (", ".join(removed_infra_labels) if removed_infra_labels else "(none)")
        )
        console.print(
            "Removed apps components: "
            + (", ".join(removed_app_labels) if removed_app_labels else "(none)")
        )
        _print_component_edit_config_only_note()
        _print_component_edit_next_steps(config_path)
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "bootstrap-ci",
    short_help="Use CONFIG_YAML to reconcile the customer GitHub workflow, email settings, and optional CI auth.",
)
def bootstrap_ci_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=(
                "Path to project config.yaml inside the target customer git repository "
                "(<tenant-folder>/<project-folder>/config.yaml). The file must already exist inside that repo checkout."
            ),
        ),
    ],
    auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auth-bootstrap/--no-auth-bootstrap",
            help=(
                "Ensure Nebius CI service account + keys and sync GitHub environment auth secrets "
                "(enabled by default). Email settings are reconciled from local `email --setup` on every run."
            ),
        ),
    ] = True,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "Optional override for the target GitHub repository slug '<owner>/<repo>' "
                "used for workflow bootstrap reconciliation, email setting sync, and optional Nebius auth bootstrap. "
                "Normally auto-detected from the target repository origin remote."
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help=(
                "Environment variable name holding the GitHub token used for GitHub workflow/environment reconciliation, "
                "email setting sync, and optional auth bootstrap (falls back to GH_TOKEN/GITHUB_TOKEN)."
            ),
        ),
    ] = "GH_TOKEN",
    cli_ref: Annotated[
        str | None,
        typer.Option(
            "--cli-ref",
            help=(
                "Git ref used by the generated customer workflow when it installs nebius-cxcli "
                "(branch, tag, or commit SHA). Defaults to the current release tag for stable "
                "builds, otherwise 'main'."
            ),
        ),
    ] = None,
) -> None:
    """Generate or reconcile the CLI-managed customer GitHub workflow and environment settings for one project config.yaml."""
    try:
        config, paths = _load_context(config_path)
        resolved_cli_ref = str(cli_ref or "").strip() or default_cli_ref()
        repo_root = _require_git_root(paths.deployments_dir)
        github_environment = _github_environment_name_for_identity(
            client_name=str(config.client_info.client_name),
            project_id=str(config.client_info.nebius.project_id),
        )
        resolved_github_repo, github_token = _resolve_bootstrap_ci_github_target(
            github_repo=github_repo,
            github_token_env=github_token_env,
            repo_root=repo_root,
        )
        email_settings = _load_local_email_settings()
        workflow = _ensure_ci_workflow_for_deployments_root(
            deployments_root=paths.deployments_dir,
            cli_ref=resolved_cli_ref,
        )
        gitignore_result = _ensure_deployments_gitignore(
            deployments_root=paths.deployments_dir,
        )

        if auth_bootstrap:
            _auto_bootstrap_ci_auth_and_secrets(
                project_id=config.client_info.nebius.project_id,
                github_environment=github_environment,
                repo_root=workflow.repo_root,
                service_account_name="nebius-cxcli-ci",
                service_account_description="Service account used by nebius-cxcli CI automation",
                role_ids=["editor"],
                auth_key_description="nebius-cxcli CI authorized key",
                access_key_description="nebius-cxcli CI Object Storage access key",
                github_repo=github_repo,
                github_token_env=github_token_env,
                profile=None,
                endpoint=None,
                sdk_config_file=None,
            )
        email_sync = _sync_github_email_settings(
            repo_slug=resolved_github_repo,
            github_environment=github_environment,
            github_token=github_token,
            settings=email_settings,
        )

        console.print(f"Repository root: {workflow.repo_root}")
        if resolved_github_repo:
            console.print(f"GitHub repository: {resolved_github_repo}")
        if workflow.wrote_workflow:
            if workflow.replaced_workflow:
                console.print(f"Updated: {workflow.workflow_file}")
            else:
                console.print(f"Created: {workflow.workflow_file}")
        else:
            console.print(f"Workflow already aligned: {workflow.workflow_file}")
        if gitignore_result.path is not None:
            if gitignore_result.wrote:
                console.print(f"Ensured deployments .gitignore: {gitignore_result.path}")
            else:
                console.print(f"Deployments .gitignore up-to-date: {gitignore_result.path}")
        console.print(f"GitHub environment: {github_environment}")
        console.print(f"Workflow CLI ref: {resolved_cli_ref}")
        if email_settings.enabled:
            console.print(
                "Email settings synced: "
                f"{len(email_sync.updated_vars)} environment variable(s), "
                f"{len(email_sync.updated_secrets)} secret(s)"
            )
            if email_sync.removed_vars or email_sync.removed_secrets:
                console.print(
                    "Removed stale email settings: "
                    f"{len(email_sync.removed_vars)} environment variable(s), "
                    f"{len(email_sync.removed_secrets)} secret(s)"
                )
        else:
            if email_sync.removed_vars or email_sync.removed_secrets:
                console.print(
                    "Local email settings are disabled; cleared GitHub email settings: "
                    f"{len(email_sync.removed_vars)} environment variable(s), "
                    f"{len(email_sync.removed_secrets)} secret(s)"
                )
            else:
                console.print(
                    "Local email settings are disabled and GitHub email settings are already absent."
                )
        if not auth_bootstrap:
            console.print("Skipped Nebius CI auth bootstrap/secrets sync.")
        console.print("CI bootstrap completed.")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "validate",
    short_help="Use CONFIG_YAML to validate source config, deployment readiness, and live quota/capacity.",
)
def validate_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
) -> None:
    """Validate one project source config.yaml as the source config, deployment readiness, and live quota/capacity gate, including provider/chart wiring and MK8s preflight."""
    try:
        _run_runtime_validation(
            config_path=config_path,
            strict=True,
            title="Runtime validation",
        )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "quota-check",
    short_help="Use CONFIG_YAML to run a live Nebius quota/capacity assessment for enabled infra components.",
)
def quota_check_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    all_regions: Annotated[
        bool,
        typer.Option(
            "--all-regions",
            help=(
                "Also replay the current config's quota requirements across all discovered tenant/project "
                "regions and print per-region availability. This does not change pass/fail semantics: "
                "the selected config region still decides insufficiency. The replay is quota-only and "
                "does not revalidate region-specific platform or preset availability."
            ),
        ),
    ] = False,
) -> None:
    """Run a live Nebius quota/capacity assessment for the enabled infra components in one project config.

    The selected config region determines pass/fail. Use --all-regions to also print quota-only
    availability for the same shape across all discovered tenant/project regions.
    """
    try:
        config, paths = _load_context(config_path)
        report = _warn_on_config_live_quota_issues(
            config,
            paths,
            phase="quota check",
            all_regions=all_regions,
        )
        if report.has_confirmed_insufficiency:
            _print_quota_remediation_hint(paths.config_path, report)
            _print_quota_check_all_regions_hint(paths.config_path, enabled=not all_regions)
            raise RuntimeError(_quota_failure_message(report, phase="quota check"))
        if report.errors or report.coverage_gaps or report.unknown_checks:
            warning_detail = "No confirmed quota insufficiency was found."
            if report.coverage_gaps and not report.errors and not report.unknown_checks:
                warning_detail = (
                    "No confirmed quota insufficiency was found. Some quota dimensions could not be "
                    "evaluated from the current config/API surface."
                )
            console.print(
                f"{warning_markup('Quota check completed with warnings.')} {warning_detail}"
            )
            return
        console.print(f"[green]Nebius quota is sufficient:[/green] {paths.config_path}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "quota-request",
    short_help="Use CONFIG_YAML to plan and submit quota requests for confirmed insufficient Nebius quotas.",
)
def quota_request_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
) -> None:
    """Plan and submit quota requests for confirmed live quota shortages in one project config.

    The command keeps QuotaAllowance reads and QuotaRequest submission separate:
    it uses live quota allowances to confirm the shortage, then submits quota requests
    through the separate request surface when that internal path is available.
    If the current live assessment is already sufficient, quota-request is a no-op.
    It is mainly the remediation command for confirmed shortages reported by
    create, quota-check, validate, render, deploy, or validate-generated.
    Unresolved live limits and coverage gaps are reported but not requested automatically.
    When internal quota-request submission is unavailable, the command still prints the
    exact manual quota targets that should be requested in the Nebius web console.
    """
    try:
        config, paths = _load_context(config_path)
        report = _warn_on_config_live_quota_issues(config, paths, phase="quota request")
        request_result = request_quota_changes(report, context="quota request")
        planned_changes = request_result.planned_changes
        if not planned_changes:
            if report.has_confirmed_insufficiency:
                raise RuntimeError(
                    "Confirmed live quota shortages were found, but no quota request "
                    "could be derived automatically from the current quota response."
                )
            if report.errors or report.coverage_gaps or report.unknown_checks:
                console.print(
                    f"{warning_markup('No quota request was submitted.')} "
                    "No confirmed quota insufficiency was found."
                )
                return
            console.print(f"[green]No quota request needed:[/green] {paths.config_path}")
            return

        for line in format_quota_request_lines(planned_changes):
            console.print(line)
        permission_denied_failures = request_result.permission_denied_failures
        if request_result.unavailable_reason:
            console.print(
                f"{warning_markup('Automatic quota-request submission is unavailable.')} "
                "This environment could plan the quota request, but it could not create "
                "QuotaRequest resources automatically."
            )
            console.print(f"Reason: {request_result.unavailable_reason}")
            console.print(
                "Submit or track the request in the Nebius web console under Administration -> Limits -> Quotas."
            )
            console.print("Manual follow-up is still required for:")
            for line in format_quota_request_manual_followup_lines(request_result.planned_changes):
                console.print(line)
            console.print(
                "Current quota allowances remain unchanged until the request is approved."
            )
        elif permission_denied_failures:
            if request_result.submitted_changes:
                console.print(
                    f"{warning_markup('Quota request submission was only partially completed.')} "
                    "Some confirmed shortage requests still require manual follow-up because the "
                    "current identity was not permitted to create all quota-request records "
                    "through the internal request API."
                )
            else:
                console.print(
                    f"{warning_markup('Automatic quota-request submission was not permitted.')} "
                    "The current identity can see the confirmed shortage, but Nebius denied "
                    "quota-request creation through the internal request API."
                )
            console.print(
                "Submit or track the request in the Nebius web console under Administration -> Limits -> Quotas."
            )
            console.print("Manual follow-up is still required for:")
            for line in format_quota_request_manual_followup_lines(
                tuple(item.change for item in permission_denied_failures)
            ):
                console.print(line)
            console.print(
                "Current quota allowances remain unchanged until the request is approved."
            )
        else:
            console.print(f"[green]Quota request submitted:[/green] {paths.config_path}")
            console.print(
                "Current quota allowances remain unchanged until these requests are approved."
            )
            console.print(
                "Review request status in the Nebius web console under Administration -> Limits -> Quotas."
            )
        if report.errors or report.coverage_gaps or report.unknown_checks:
            console.print(
                f"{warning_markup('Additional unresolved quota findings remain.')} "
                "Only confirmed insufficient quota dimensions were planned or requested automatically."
            )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "ssh-jumphost",
    short_help="Use CONFIG_YAML to manage SSH jump-host source CIDRs.",
)
def ssh_jumphost_command(
    add_allowed_cidrs: Annotated[
        Path | None,
        typer.Option(
            "--add-allowed-cidrs",
            metavar="CONFIG_YAML",
            help=(
                "Path to config.yaml. Adds the comma-separated --allowed-cidr list "
                "to the deployed SSH jump-host firewall allowlist."
            ),
        ),
    ] = None,
    remove_allowed_cidrs: Annotated[
        Path | None,
        typer.Option(
            "--remove-allowed-cidrs",
            metavar="CONFIG_YAML",
            help=(
                "Path to config.yaml. Removes the comma-separated --allowed-cidr list "
                "from the deployed SSH jump-host firewall allowlist."
            ),
        ),
    ] = None,
    list_allowed_cidrs: Annotated[
        Path | None,
        typer.Option(
            "--list-allowed-cidrs",
            metavar="CONFIG_YAML",
            help="Path to config.yaml. Lists the deployed SSH jump-host firewall allowlist.",
        ),
    ] = None,
    component: Annotated[
        str | None,
        typer.Option(
            "--component",
            help=(
                "SSH jump-host row selector to use when config.yaml enables more "
                "than one. For scalar named rows, use the resource name or "
                "'ssh-jumphost@<resource-name>', for example ssh-jumphost@bastion."
            ),
        ),
    ] = None,
    allowed_cidr: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-cidr",
            help=(
                "Add/remove modes only. Exactly one comma-separated IPv4 CIDR list, "
                "for example 203.0.113.10/32,198.51.100.0/24."
            ),
        ),
    ] = None,
    ssh_user: Annotated[
        str | None,
        typer.Option(
            "--ssh-user",
            help=(
                "SSH username for the SSH jump host. Defaults to component inputs.ssh_user_name."
            ),
        ),
    ] = None,
    ssh_private_key: Annotated[
        Path | None,
        typer.Option(
            "--ssh-private-key",
            help="Optional SSH private key path. When omitted, ssh uses the agent/default keys.",
        ),
    ] = None,
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=("Automatically bootstrap runtime auth when Terraform output lookup needs it."),
        ),
    ] = True,
) -> None:
    """Manage day-2 SSH source CIDR access for a deployed ssh-jumphost.

    Use exactly one mode per invocation:

    - --add-allowed-cidrs CONFIG_YAML adds source CIDRs to the VM-local allowlist.
    - --remove-allowed-cidrs CONFIG_YAML removes source CIDRs from the VM-local allowlist.
    - --list-allowed-cidrs CONFIG_YAML lists the VM-local allowlist.

    Add/remove modes require exactly one comma-separated --allowed-cidr value.
    The VM-local helper refuses to apply an empty allowlist to avoid SSH lockout.
    The current config.yaml and sibling generated bundle must both contain the
    same selected component row.
    """
    try:
        selected_modes = [
            (name, path)
            for name, path in (
                ("add", add_allowed_cidrs),
                ("remove", remove_allowed_cidrs),
                ("list", list_allowed_cidrs),
            )
            if path is not None
        ]
        if len(selected_modes) != 1:
            raise RuntimeError(
                "Use exactly one of --add-allowed-cidrs, --remove-allowed-cidrs, "
                "or --list-allowed-cidrs with CONFIG_YAML."
            )
        mode, config_path = selected_modes[0]
        if mode in {"add", "remove"}:
            if len(allowed_cidr or []) != 1:
                raise RuntimeError(
                    "--add-allowed-cidrs and --remove-allowed-cidrs require exactly one "
                    "--allowed-cidr option containing a comma-separated CIDR list."
                )
            allowed_cidrs = normalize_allowed_cidr_csv((allowed_cidr or [""])[0])
        else:
            if allowed_cidr:
                raise RuntimeError("--allowed-cidr only applies to add/remove modes.")
            allowed_cidrs = ()

        source_config_path = config_path
        source_config = load_config(source_config_path, persist_normalized=False)
        source_component = select_ssh_jumphost_component(
            source_config, component_selector=component
        )
        config, paths, _manifest = _load_deploy_context(source_config_path)
        component_selection = _select_deployed_day2_component(
            config_path=source_config_path,
            generated_config=config,
            component_label=source_component.label,
            select_component=select_ssh_jumphost_component,
            operation_label="SSH jump-host",
        )
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        runtime_env = _terraform_runtime_env(config)
        terraform_outputs = terraform_output_json(paths.infra_dir, extra_env=runtime_env)
        public_ip = ssh_jumphost_public_ip_from_outputs(terraform_outputs, component_selection)

        resolved_ssh_user = (
            ssh_user or str(component_selection.inputs.get("ssh_user_name") or "")
        ).strip()
        if not resolved_ssh_user:
            raise RuntimeError(
                f"{component_selection.label} is missing inputs.ssh_user_name for SSH access"
            )
        result = update_ssh_jumphost_allowed_cidrs(
            SshJumphostAllowedCidrRequest(
                component=component_selection,
                public_ip=public_ip,
                ssh_user=resolved_ssh_user,
                ssh_private_key=ssh_private_key,
                operation=mode,
                allowed_cidrs=allowed_cidrs,
            )
        )
        if mode == "list":
            console.print("[green]SSH jump-host allowed CIDRs:[/green]")
        else:
            console.print("[green]SSH jump-host allowed CIDRs updated.[/green]")
        if result.added:
            console.print(f"Added: {', '.join(result.added)}")
        if result.removed:
            console.print(f"Removed: {', '.join(result.removed)}")
        if result.unchanged:
            console.print(f"Unchanged: {', '.join(result.unchanged)}")
        console.print(f"Current allowed CIDRs: {', '.join(result.allowed_cidrs) or '(none)'}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "wireguard",
    short_help="Use CONFIG_YAML to manage WireGuard clients and routed local subnets.",
)
def wireguard_command(
    gen_client_conf: Annotated[
        Path | None,
        typer.Option(
            "--gen-client-conf",
            metavar="CONFIG_YAML",
            help=_WIREGUARD_CONFIG_ARGUMENT_HELP,
        ),
    ] = None,
    add_local_subnets: Annotated[
        Path | None,
        typer.Option(
            "--add-local-subnets",
            metavar="CONFIG_YAML",
            help=(
                "Path to config.yaml. Adds the comma-separated --local-subnet CIDR list "
                "to the deployed VPN gateway defaults for future generated clients."
            ),
        ),
    ] = None,
    remove_local_subnets: Annotated[
        Path | None,
        typer.Option(
            "--remove-local-subnets",
            metavar="CONFIG_YAML",
            help=(
                "Path to config.yaml. Removes the comma-separated --local-subnet CIDR list "
                "from the deployed VPN gateway defaults for future generated clients."
            ),
        ),
    ] = None,
    component: Annotated[
        str | None,
        typer.Option(
            "--component",
            help=(
                "All modes. WireGuard row selector to use when config.yaml enables "
                "more than one. For scalar named rows, use the resource name or "
                "'wireguard-gw@<resource-name>', for example wireguard-gw@vpn."
            ),
        ),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option(
            "--client-name",
            help=(
                "Generation mode only. Optional stable wg-quick interface/client name "
                "(lowercase letters, digits, hyphens, max 15 chars). When omitted, "
                "cxcli generates a unique short name."
            ),
        ),
    ] = None,
    local_subnet: Annotated[
        list[str] | None,
        typer.Option(
            "--local-subnet",
            help=(
                "Private destination IPv4 CIDRs routed through WireGuard. For "
                "--gen-client-conf, repeat this option for multiple per-client CIDRs. "
                "For --add-local-subnets/--remove-local-subnets, pass exactly one "
                "comma-separated list, for example 10.20.0.0/16,10.30.0.0/16."
            ),
        ),
    ] = None,
    dns: Annotated[
        list[str] | None,
        typer.Option(
            "--dns",
            help=(
                "Generation mode only. DNS server IPv4 address for the client config. "
                "Repeat for multiple servers."
            ),
        ),
    ] = None,
    persistent_keepalive: Annotated[
        int | None,
        typer.Option(
            "--persistent-keepalive",
            help="Generation mode only. WireGuard PersistentKeepalive interval in seconds.",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help=(
                "Generation mode only. Directory for downloaded client .conf files. Defaults to "
                "<tenant>/<project>/wireguard-clients/, which cxcli ignores in the "
                "deployments-root .gitignore."
            ),
        ),
    ] = None,
    ssh_user: Annotated[
        str | None,
        typer.Option(
            "--ssh-user",
            help=(
                "All modes. SSH username for the WireGuard VPN gateway. Defaults to "
                "component inputs.ssh_user_name."
            ),
        ),
    ] = None,
    ssh_private_key: Annotated[
        Path | None,
        typer.Option(
            "--ssh-private-key",
            help=(
                "All modes. Optional SSH private key path. When omitted, ssh uses "
                "the agent/default keys."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Generation mode only. Overwrite an existing local client .conf file "
                "with the same generated name."
            ),
        ),
    ] = False,
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=(
                "All modes. Automatically bootstrap runtime auth when Terraform output "
                "lookup needs it."
            ),
        ),
    ] = True,
) -> None:
    """Manage WireGuard day-2 operations for a deployed wireguard-gw.

    Use exactly one mode per invocation:

    - --gen-client-conf CONFIG_YAML generates and downloads one client .conf.
    - --add-local-subnets CONFIG_YAML adds future-client route defaults.
    - --remove-local-subnets CONFIG_YAML removes future-client route defaults.

    Generation mode uses a wg-quick-safe filename/interface name, prints the
    local wg-quick up/down commands, and warns with an OS-specific install hint
    when wg-quick is missing.
    Add/remove subnet modes require one comma-separated --local-subnet value.
    Existing downloaded client configs are not rewritten automatically.
    The current config.yaml and sibling generated bundle must both contain the
    same selected component row.

    Examples:

    nebius-cxcli wireguard --gen-client-conf <config.yaml>

    nebius-cxcli wireguard --add-local-subnets <config.yaml> --local-subnet 10.20.0.0/16,10.30.0.0/16

    nebius-cxcli wireguard --remove-local-subnets <config.yaml> --local-subnet 10.20.0.0/16,10.30.0.0/16
    """
    try:
        selected_modes = [
            (name, path)
            for name, path in (
                ("gen-client-conf", gen_client_conf),
                ("add-local-subnets", add_local_subnets),
                ("remove-local-subnets", remove_local_subnets),
            )
            if path is not None
        ]
        if len(selected_modes) != 1:
            raise RuntimeError(
                "Use exactly one of --gen-client-conf, --add-local-subnets, "
                "or --remove-local-subnets with CONFIG_YAML."
            )
        mode, config_path = selected_modes[0]
        is_subnet_update_mode = mode in {"add-local-subnets", "remove-local-subnets"}
        update_local_subnets: tuple[str, ...] = ()
        generation_local_subnets: tuple[str, ...] = ()
        generation_dns: tuple[str, ...] = ()
        if is_subnet_update_mode:
            if client_name or dns or persistent_keepalive is not None or output_dir or force:
                raise RuntimeError(
                    "--client-name, --dns, --persistent-keepalive, --output-dir, and --force "
                    "only apply to --gen-client-conf."
                )
            if len(local_subnet or []) != 1:
                raise RuntimeError(
                    "--add-local-subnets and --remove-local-subnets require exactly one "
                    "--local-subnet option containing a comma-separated CIDR list."
                )
            update_local_subnets = normalize_local_subnet_csv((local_subnet or [""])[0])
        else:
            generation_local_subnets = normalize_local_subnets(local_subnet or [])
            generation_dns = normalize_dns(dns or [])

        source_config_path = config_path
        source_config = load_config(source_config_path, persist_normalized=False)
        source_component = select_wireguard_component(source_config, component_selector=component)
        config, paths, _manifest = _load_deploy_context(source_config_path)
        component_selection = _select_deployed_day2_component(
            config_path=source_config_path,
            generated_config=config,
            component_label=source_component.label,
            select_component=select_wireguard_component,
            operation_label="WireGuard",
        )
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        runtime_env = _terraform_runtime_env(config)
        terraform_outputs = terraform_output_json(paths.infra_dir, extra_env=runtime_env)
        public_ip = wireguard_public_ip_from_outputs(terraform_outputs, component_selection)

        resolved_ssh_user = (
            ssh_user or str(component_selection.inputs.get("ssh_user_name") or "")
        ).strip()
        if not resolved_ssh_user:
            raise RuntimeError(
                f"{component_selection.label} is missing inputs.ssh_user_name for SSH access"
            )
        if is_subnet_update_mode:
            operation = "add" if mode == "add-local-subnets" else "remove"
            update_result = update_wireguard_local_subnets(
                WireGuardLocalSubnetUpdateRequest(
                    component=component_selection,
                    public_ip=public_ip,
                    ssh_user=resolved_ssh_user,
                    ssh_private_key=ssh_private_key,
                    operation=operation,
                    local_subnets=update_local_subnets,
                )
            )
            console.print("[green]WireGuard local subnets updated.[/green]")
            if update_result.added:
                console.print(f"Added: {', '.join(update_result.added)}")
            if update_result.removed:
                console.print(f"Removed: {', '.join(update_result.removed)}")
            if update_result.unchanged:
                console.print(f"Unchanged: {', '.join(update_result.unchanged)}")
            console.print(
                f"Current local subnets: {', '.join(update_result.local_subnets) or '(none)'}"
            )
            return

        selected_output_dir = (
            output_dir.expanduser().resolve()
            if output_dir is not None
            else default_wireguard_client_output_dir(paths)
        )
        _ensure_wireguard_output_gitignore(selected_output_dir, paths)
        result = generate_wireguard_client_config(
            WireGuardClientGenerationRequest(
                component=component_selection,
                public_ip=public_ip,
                ssh_user=resolved_ssh_user,
                ssh_private_key=ssh_private_key,
                client_name=client_name,
                local_subnets=generation_local_subnets,
                dns=generation_dns,
                persistent_keepalive=persistent_keepalive,
                output_dir=selected_output_dir,
                force=force,
            )
        )

        console.print(f"[green]WireGuard client config written:[/green] {result.output_path}")
        console.print(f"Client: {result.client_name}")
        console.print(f"Tunnel address: {result.client_wg_tunnel_address}")
        if result.local_subnets:
            console.print(f"Local subnets: {', '.join(result.local_subnets)}")
        console.print(f"Server copy: {result.remote_config_path}")
        console.print(
            f"Server allocation state: {result.clients_created} client(s), "
            f"{result.remaining_client_slots} tunnel address(es) remaining"
        )
        connect_command = f"wg-quick up {shlex.quote(str(result.output_path))}"
        disconnect_command = f"wg-quick down {shlex.quote(str(result.output_path))}"
        console.print(
            f"Run this command to connect: [bold]{escape(connect_command)}[/bold]",
            soft_wrap=True,
        )
        console.print(
            f"Run this command to disconnect: [bold]{escape(disconnect_command)}[/bold]",
            soft_wrap=True,
        )
        if _wireguard_client_tool_missing():
            install_command = _wireguard_client_install_command()
            console.print("[yellow]WireGuard client tool not found locally:[/yellow] wg-quick")
            console.print(
                f"Install it with: [bold]{escape(install_command)}[/bold]",
                soft_wrap=True,
            )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "validate-generated",
    short_help="Use GENERATED_PATH to validate rendered-bundle readiness, manifests, and portability without rerendering.",
)
def validate_generated_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_PATH_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=(
                "Automatically bootstrap runtime auth for generated-bundle "
                "backend/Terraform validation when env vars are missing."
            ),
        ),
    ] = True,
    portable: Annotated[
        bool,
        typer.Option(
            "--portable",
            help=(
                "Require the generated bundle to be portable by rejecting local Terraform module "
                "sources recorded in the generated manifest."
            ),
        ),
    ] = False,
) -> None:
    """Validate generated-bundle readiness, manifests, and optional portability without rerendering.

    For bundled MK8s reruns, the live quota/capacity gate is state-aware:
    after backend init it discounts MK8s quota already managed in the current
    Terraform state, so unchanged existing-cluster reruns do not fail like
    fresh creates while real added capacity still fails fast.
    """
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        _run_generated_bundle_validation(
            config,
            paths,
            auto_auth_bootstrap=auto_auth_bootstrap,
            title="Generated artifact validation",
            quota_phase="validate-generated",
            flux_command_name="validate-generated",
            portable=portable,
            manifest=_manifest,
        )
        console.print(f"[green]Valid generated artifacts:[/green] {paths.generated_dir}")
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI surface
        detail = _first_non_empty_line(exc.stderr or exc.stdout or "")
        _exit_with_error(RuntimeError(detail or str(exc)))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


def _validate_grafana_dashboard_fits_with_progress(
    config: Any,
    *,
    target_ref: str,
    target_extra_envs: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[Any, ...]:
    if _console_is_terminal():
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task("Grafana dashboards: preparing", total=1)

            def _progress_update(label: str, completed: int, total: int) -> None:
                normalized_total = max(total, 0)
                normalized_completed = min(max(completed, 0), normalized_total)
                if label == "init":
                    description = "Grafana dashboards: preparing"
                elif label == "done":
                    description = "Grafana dashboard validation completed"
                else:
                    description = f"Grafana dashboards: {escape(label)}"
                progress.update(
                    task_id,
                    description=description,
                    completed=normalized_completed,
                    total=normalized_total,
                )

            return validate_grafana_dashboard_fits(
                config,
                target_ref=target_ref,
                target_extra_envs=target_extra_envs,
                progress_callback=_progress_update,
            )

    active_label = ""
    active_completed = 0

    def _progress_update(label: str, completed: int, total: int) -> None:
        nonlocal active_completed, active_label
        normalized_total = max(total, 0)
        if label == "init":
            active_label = ""
            active_completed = 0
            console.print(
                "[cyan]Grafana dashboards:[/cyan] "
                f"validating {normalized_total} dashboard binding(s)"
            )
            return
        if label == "done":
            console.print(
                "[cyan]Grafana dashboard validation completed:[/cyan] "
                f"{completed}/{normalized_total}"
            )
            return
        if label == active_label and completed != active_completed:
            active_completed = completed
            return
        active_label = label
        active_completed = completed
        display_index = min(completed + 1, normalized_total) if normalized_total else 0
        console.print(
            f"[cyan]Grafana dashboards:[/cyan] {label} ({display_index}/{normalized_total})"
        )

    return validate_grafana_dashboard_fits(
        config,
        target_ref=target_ref,
        target_extra_envs=target_extra_envs,
        progress_callback=_progress_update,
    )


def _kubeconfig_env_for_context(context_name: str, *, stack: ExitStack) -> dict[str, str]:
    normalized_context = str(context_name or "").strip()
    if not normalized_context:
        return {}
    for kubeconfig_path in _candidate_kubeconfig_paths():
        source_path = Path(kubeconfig_path).expanduser()
        if not source_path.exists():
            continue
        try:
            payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, Mapping):
            continue
        contexts = payload.get("contexts")
        if not isinstance(contexts, list):
            continue
        if not any(
            isinstance(item, Mapping)
            and str(dict(item).get("name") or "").strip() == normalized_context
            for item in contexts
        ):
            continue
        target_root = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="nebius-cxcli-kube-"))
        )
        target_path = target_root / "config"
        target_payload = copy.deepcopy(dict(payload))
        target_payload["current-context"] = normalized_context
        target_path.write_text(yaml.safe_dump(target_payload, sort_keys=False), encoding="utf-8")
        return {
            "KUBECONFIG": str(target_path),
            GRAFANA_TARGET_KUBE_CONTEXT_ENV: normalized_context,
        }
    return {}


def _candidate_kubeconfig_paths() -> tuple[str, ...]:
    kubeconfig_value = str(os.environ.get("KUBECONFIG") or "")
    return (
        tuple(item for item in kubeconfig_value.split(os.pathsep) if item)
        if kubeconfig_value
        else (str(Path.home().expanduser() / ".kube" / "config"),)
    )


def _known_kube_context_names() -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for kubeconfig_path in _candidate_kubeconfig_paths():
        source_path = Path(kubeconfig_path).expanduser()
        if not source_path.exists():
            continue
        try:
            payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, Mapping):
            continue
        contexts = payload.get("contexts")
        if not isinstance(contexts, list):
            continue
        for item in contexts:
            if not isinstance(item, Mapping):
                continue
            name = str(dict(item).get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return tuple(names)


def _current_kube_context_name() -> str:
    known_contexts = set(_known_kube_context_names())
    for kubeconfig_path in _candidate_kubeconfig_paths():
        source_path = Path(kubeconfig_path).expanduser()
        if not source_path.exists():
            continue
        try:
            payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, Mapping):
            continue
        context_name = str(payload.get("current-context") or "").strip()
        if context_name and context_name in known_contexts:
            return context_name
    return ""


def _cluster_id_from_kube_context_name(context_name: str) -> str:
    parts = str(context_name or "").strip().split("-")
    for index, part in enumerate(parts[:-1]):
        if part == "mk8scluster" and index + 1 < len(parts):
            return f"mk8scluster-{parts[index + 1]}"
    return ""


def _kube_context_matches_target(context_name: str, target_ref: str) -> bool:
    normalized_target_ref = normalize_component_token(target_ref)
    normalized_context_name = str(context_name or "").strip()
    if not normalized_target_ref or not normalized_context_name:
        return False
    prefix = f"nebius-{normalized_target_ref}-mk8scluster-"
    return normalized_context_name.startswith(prefix) and bool(
        _cluster_id_from_kube_context_name(normalized_context_name)
    )


def _kube_context_name_for_target(target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return ""
    current_context = _current_kube_context_name()
    if _kube_context_matches_target(current_context, normalized_target_ref):
        return current_context
    prefix = f"nebius-{normalized_target_ref}-mk8scluster-"
    candidates = [
        name
        for name in _known_kube_context_names()
        if name.startswith(prefix) and _cluster_id_from_kube_context_name(name)
    ]
    if not candidates:
        return ""
    external = [name for name in candidates if name.endswith("-external")]
    preferred = external or candidates
    if len(preferred) != 1:
        return ""
    return preferred[0]


def _kubeconfig_target_env(
    target_ref: str,
    *,
    stack: ExitStack,
    preferred_context: str = "",
    preferred_cluster_id: str = "",
) -> dict[str, str]:
    context_name = str(preferred_context or "").strip() or _kube_context_name_for_target(target_ref)
    if not context_name:
        return {}
    env = _kubeconfig_env_for_context(context_name, stack=stack)
    if not env:
        return {}
    cluster_id = str(preferred_cluster_id or "").strip() or _cluster_id_from_kube_context_name(
        context_name
    )
    if cluster_id:
        env[GRAFANA_TARGET_CLUSTER_ID_ENV] = cluster_id
    return env


_DEPLOY_REPORT_TARGET_HEADING_RE = re.compile(r"^### Target `(?P<target>[^`]+)`$")
_DEPLOY_REPORT_TARGET_MK8S_RE = re.compile(
    r"^- MK8s: cluster ID `(?P<cluster_id>[^`]+)`; kube context `(?P<context>[^`]+)`$"
)
_DEPLOY_REPORT_CLUSTER_HEADING_RE = re.compile(r"^- `(?P<target>[^`]+)` \(`[^`]+`\)$")
_DEPLOY_REPORT_CLUSTER_ID_RE = re.compile(r"^  - Cluster ID: `(?P<cluster_id>[^`]+)`$")
_DEPLOY_REPORT_CLUSTER_CONTEXT_RE = re.compile(r"^  - Kube context: `(?P<context>[^`]+)`$")


def _deploy_report_target_contexts(paths: ProjectPaths) -> dict[str, dict[str, str]]:
    report_path = paths.inventory_dir / DEPLOY_REPORT_FILENAME
    if not report_path.exists():
        return {}
    try:
        report = report_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    metadata: dict[str, dict[str, str]] = {}
    target_section = ""
    cluster_section = ""
    for line in report.splitlines():
        if match := _DEPLOY_REPORT_TARGET_HEADING_RE.match(line):
            target_section = normalize_component_token(match.group("target"))
            cluster_section = ""
            continue
        if target_section and (match := _DEPLOY_REPORT_TARGET_MK8S_RE.match(line)):
            metadata.setdefault(target_section, {}).update(
                {
                    "cluster_id": str(match.group("cluster_id") or "").strip(),
                    "kube_context": str(match.group("context") or "").strip(),
                }
            )
            continue
        if match := _DEPLOY_REPORT_CLUSTER_HEADING_RE.match(line):
            cluster_section = normalize_component_token(match.group("target"))
            target_section = ""
            metadata.setdefault(cluster_section, {})
            continue
        if cluster_section and (match := _DEPLOY_REPORT_CLUSTER_ID_RE.match(line)):
            metadata.setdefault(cluster_section, {})["cluster_id"] = str(
                match.group("cluster_id") or ""
            ).strip()
            continue
        if cluster_section and (match := _DEPLOY_REPORT_CLUSTER_CONTEXT_RE.match(line)):
            metadata.setdefault(cluster_section, {})["kube_context"] = str(
                match.group("context") or ""
            ).strip()
    metadata = {
        target_ref: fields
        for target_ref, fields in metadata.items()
        if fields.get("cluster_id") and fields.get("kube_context")
    }
    return metadata


def _grafana_status_target_envs(
    paths: ProjectPaths,
    *,
    selected_target_refs: set[str],
    stack: ExitStack,
) -> dict[str, Mapping[str, str]]:
    target_envs: dict[str, Mapping[str, str]] = {}
    for status in read_grafana_status(paths):
        target_ref = normalize_component_token(status.get("target_ref"))
        if not target_ref or target_ref in target_envs:
            continue
        if selected_target_refs and target_ref not in selected_target_refs:
            continue
        env = _kubeconfig_env_for_context(str(status.get("kube_context") or ""), stack=stack)
        if not env:
            continue
        cluster_id = str(status.get("cluster_id") or "").strip()
        if cluster_id:
            env[GRAFANA_TARGET_CLUSTER_ID_ENV] = cluster_id
        if env:
            target_envs[target_ref] = env
    return target_envs


def _grafana_dashboard_validation_required_target_refs(
    config: Any,
    *,
    target_ref: str,
) -> set[str]:
    normalized_target_ref = normalize_component_token(target_ref)
    if normalized_target_ref:
        return (
            {normalized_target_ref}
            if grafana_enabled_for_target(config, target_ref=normalized_target_ref)
            else set()
        )
    return {
        ref
        for ref in enabled_cluster_target_refs(config)
        if ref and grafana_enabled_for_target(config, target_ref=ref)
    }


def _raise_missing_grafana_target_contexts(
    missing_target_refs: set[str],
    *,
    config_path: Path | None = None,
    generated_path: Path | None = None,
    details_by_target: Mapping[str, str] | None = None,
) -> None:
    if not missing_target_refs:
        return
    missing = ", ".join(sorted(missing_target_refs))
    config_arg = _config_cli_arg(config_path) if config_path is not None else "<config.yaml>"
    generated_arg = (
        shlex.quote(str(generated_path.resolve())) if generated_path is not None else "<generated/>"
    )
    message = (
        "validate-dashboards could not resolve an explicit kube context for Grafana "
        f"target(s): {missing}. Run `nebius-cxcli deploy {config_arg}` or "
        f"`nebius-cxcli flux apply {generated_arg}` for those targets first, or make sure "
        "the matching `nebius-<target>-mk8scluster-...` context is current or "
        "unambiguous in KUBECONFIG."
    )
    detail_rows = []
    for target in sorted(missing_target_refs):
        detail = _first_non_empty_line(str((details_by_target or {}).get(target) or ""))
        if detail:
            detail_rows.append(f"{target}: {detail}")
    if detail_rows:
        message += "\nContext resolution details:\n  - " + "\n  - ".join(detail_rows)
    raise RuntimeError(message)


def _grafana_dashboard_validation_target_envs(
    config: Any,
    paths: ProjectPaths,
    *,
    target_ref: str,
    stack: ExitStack,
) -> dict[str, Mapping[str, str]]:
    normalized_target_ref = normalize_component_token(target_ref)
    selected_target_refs = {normalized_target_ref} if normalized_target_ref else set()
    required_target_refs = _grafana_dashboard_validation_required_target_refs(
        config,
        target_ref=normalized_target_ref,
    )
    target_envs = _grafana_status_target_envs(
        paths,
        selected_target_refs=selected_target_refs,
        stack=stack,
    )
    if normalized_target_ref and normalized_target_ref in target_envs:
        return target_envs
    report_target_contexts = _deploy_report_target_contexts(paths)
    for required_target_ref in sorted(required_target_refs):
        if required_target_ref in target_envs:
            continue
        report_context = report_target_contexts.get(required_target_ref, {})
        target_env = _kubeconfig_target_env(
            required_target_ref,
            stack=stack,
            preferred_context=report_context.get("kube_context", ""),
            preferred_cluster_id=report_context.get("cluster_id", ""),
        )
        if target_env:
            target_envs[required_target_ref] = target_env
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    if not manifest_path.exists():
        _raise_missing_grafana_target_contexts(
            required_target_refs - set(target_envs),
            config_path=getattr(paths, "config_path", None),
            generated_path=getattr(paths, "generated_dir", None),
        )
        return target_envs
    manifest = load_generated_manifest(paths.generated_dir)
    targets = _manifest_deploy_targets(manifest)
    if not targets:
        _raise_missing_grafana_target_contexts(
            required_target_refs - set(target_envs),
            config_path=getattr(paths, "config_path", None),
            generated_path=getattr(paths, "generated_dir", None),
        )
        return {}
    selected_targets = (
        _resolve_selected_deploy_targets(
            manifest,
            requested_target_ref=normalized_target_ref,
            all_targets=False,
        )
        if normalized_target_ref
        else targets
    )
    handoff_errors: dict[str, str] = {}
    for target in selected_targets:
        resolved_target_ref = str(target.get("target_ref", "")).strip().lower()
        if resolved_target_ref in target_envs:
            continue
        if not resolved_target_ref or not grafana_enabled_for_target(
            config,
            target_ref=resolved_target_ref,
        ):
            continue
        report_context = report_target_contexts.get(resolved_target_ref, {})
        target_env = _kubeconfig_target_env(
            resolved_target_ref,
            stack=stack,
            preferred_context=report_context.get("kube_context", ""),
            preferred_cluster_id=report_context.get("cluster_id", ""),
        )
        if target_env:
            target_envs[resolved_target_ref] = target_env
            continue
        try:
            target_env = _prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                target=target,
                persist_local_kubeconfig=False,
                set_current_context=True,
            )
        except RuntimeError as exc:
            handoff_errors[resolved_target_ref] = str(exc)
            target_env = None
        if target_env:
            target_envs[resolved_target_ref] = target_env
    _raise_missing_grafana_target_contexts(
        required_target_refs - set(target_envs),
        config_path=getattr(paths, "config_path", None),
        generated_path=getattr(paths, "generated_dir", None),
        details_by_target=handoff_errors,
    )
    return target_envs


@app.command(
    "validate-dashboards",
    short_help="Use CONFIG_YAML to validate Grafana dashboard datasource/read-endpoint fit.",
)
def validate_dashboards_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help=(
                f"Optional {_MK8S_TARGET_ID_HELP} to validate when the config has "
                "target-scoped Grafana rows. When omitted, every enabled Grafana row "
                "is checked and each target must resolve an explicit kube context."
            ),
        ),
    ] = "",
) -> None:
    """Validate Grafana dashboard datasource/read-endpoint fit against live Grafana."""
    try:
        if _console_is_terminal():
            with console.status(
                "[cyan]Grafana dashboard validation: Load config and component catalog[/cyan]"
            ):
                config, paths = _load_context_readonly(config_path)
        else:
            console.print(
                "[cyan]Grafana dashboard validation:[/cyan] Load config and component catalog"
            )
            config, paths = _load_context_readonly(config_path)
        with ExitStack() as stack:
            target_extra_envs = _grafana_dashboard_validation_target_envs(
                config,
                paths,
                target_ref=target,
                stack=stack,
            )
            results = _validate_grafana_dashboard_fits_with_progress(
                config,
                target_ref=target,
                target_extra_envs=target_extra_envs,
            )
        if not results:
            raise RuntimeError("No enabled Grafana dashboard bindings were found to validate.")
        has_errors = any(not result.ok for result in results)
        for result in results:
            target_suffix = f"@{result.target_ref}" if result.target_ref else ""
            prefix = (
                f"{result.signal}{target_suffix}: {result.dashboard_ref} -> "
                f"{result.datasource} ({result.datasource_type}, {result.read_endpoint})"
            )
            if result.ok:
                console.print(f"[green]OK:[/green] {prefix}")
            else:
                console.print(f"[red]ERROR:[/red] {prefix}")
            source = str(getattr(result, "source", "") or "").strip()
            if source:
                console.print(f"  Source: {source}")
            checks = tuple(getattr(result, "checks", ()) or ())
            if checks:
                console.print("  Checks:")
                for check in checks:
                    console.print(f"    - {check}")
            warnings = tuple(getattr(result, "warnings", ()) or ())
            if warnings:
                console.print(f"  {warning_markup('Warnings:')}")
                for warning in warnings:
                    console.print(f"    - {warning}")
            errors = tuple(getattr(result, "errors", ()) or ())
            if errors:
                console.print(f"  {error_markup('Errors:')}")
                for error in errors:
                    console.print(f"    - {error}")
        if has_errors:
            raise RuntimeError(
                "Grafana dashboard validation found dashboards that do not fit their "
                "bound datasource/read endpoint."
            )
        console.print(
            f"[green]Grafana dashboards fit live datasources:[/green] {paths.config_path}"
        )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "validate-sources",
    short_help="Validate component_sources.yaml, paired CLI settings, and resolved Terraform/Helm source contracts.",
)
def validate_sources_command(
    component_sources_path: Annotated[
        Path | None,
        typer.Argument(
            metavar="COMPONENT_SOURCES_YAML",
            help=_COMPONENT_SOURCES_ARGUMENT_HELP,
        ),
    ] = None,
) -> None:
    """Validate component_sources.yaml, sibling component_cli_settings.yaml, and resolved Terraform/Helm source contracts."""
    try:
        sources = load_component_sources(explicit=component_sources_path)
        progress_items: list[tuple[str, str]] = []
        for module in sources.tf_modules:
            module_id = module.module.strip().lower() or "?"
            progress_items.append((f"infra:{module_id}", f"TF module: {module_id}"))
        for chart in sources.helm_charts:
            chart_name = chart.name.strip() or "?"
            progress_items.append((f"apps:{chart_name}", f"Helm chart: {chart_name}"))

        with Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            total_items = max(len(progress_items), 1)
            overall_task_id = progress.add_task(
                "Validating component catalog/settings",
                total=total_items,
            )
            item_task_ids: dict[str, list[int]] = {}
            for key, title in progress_items:
                task_id = progress.add_task(f"[dim]{title}[/dim]", total=1)
                item_task_ids.setdefault(key, []).append(task_id)
            if not progress_items:
                progress.update(
                    overall_task_id,
                    description="No component source entries found",
                    completed=1,
                    total=1,
                )

            def _progress_update(label: str, completed: int, total: int) -> None:
                normalized_total = max(total, 1)
                normalized_completed = min(max(0, completed), normalized_total)
                if label == "init":
                    progress.update(
                        overall_task_id,
                        description="Validating component catalog/settings",
                        completed=normalized_completed,
                        total=normalized_total,
                    )
                    return
                if label == "done":
                    progress.update(
                        overall_task_id,
                        description="Validation completed",
                        completed=normalized_total,
                        total=normalized_total,
                    )
                    return
                pending_ids = item_task_ids.get(label, [])
                if pending_ids:
                    task_id = pending_ids.pop(0)
                    scope, _, name = label.partition(":")
                    prefix = "TF module" if scope == "infra" else "Helm chart"
                    progress.update(
                        task_id,
                        description=f"[green]{prefix}: {name}[/green]",
                        completed=1,
                        total=1,
                    )
                progress.update(
                    overall_task_id,
                    description=(
                        "Validating component catalog/settings "
                        f"({normalized_completed}/{normalized_total})"
                    ),
                    completed=normalized_completed,
                    total=normalized_total,
                )

            source_path, issues, warnings = _validate_component_sources_registry(
                explicit=component_sources_path, progress_callback=_progress_update
            )
        for warning in warnings:
            console.print(f"{warning_markup('Warning:')} {warning}")
        if issues:
            raise RuntimeError(_component_source_validation_failure_message(source_path, issues))
        console.print(f"[green]Component catalog/settings valid:[/green] {source_path}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "auth",
    short_help=(
        "Manage runtime auth profile actions; use --project-config CONFIG_YAML or "
        "--project-id, or omit both for global --validate-profile."
    ),
)
def auth_command(
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help=(
                "Project ID used by runtime auth operations "
                "(or provide --project-config to resolve it; do not pass both)."
            ),
        ),
    ] = None,
    project_config: Annotated[
        Path | None,
        typer.Option(
            "--project-config",
            help=(
                "Optional project config.yaml path (<tenant-folder>/<project-folder>/config.yaml) used to resolve "
                "project_id and client_name; do not combine with --project-id or --client-name"
            ),
        ),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option(
            "--client-name",
            help=(
                "Client name used for runtime auth cache path and --bootstrap-ci environment naming "
                "(`<client_name>-<project_id>`). Valid only with --project-id; required for "
                "--create/--recreate unless --project-id maps to one cached profile, and required "
                "when project_id maps to multiple cached profiles."
            ),
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Nebius SDK config profile name"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Optional Nebius API endpoint override"),
    ] = None,
    sdk_config_file: Annotated[
        Path | None,
        typer.Option(
            "--sdk-config-file",
            help="Optional path to Nebius SDK config file",
        ),
    ] = None,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "Optional override for the GitHub repository slug '<owner>/<repo>' used by "
                "--bootstrap-ci. When omitted, resolves from --project-config repo root or "
                "the current git origin remote."
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help="Env var name holding GitHub token for --bootstrap-ci",
        ),
    ] = "GH_TOKEN",
    validate_profile: Annotated[
        bool,
        typer.Option(
            "--validate-profile",
            help=(
                "Validate local runtime auth cache and Nebius auth key visibility. "
                "When no project/config target is provided, validates all cached profiles."
            ),
        ),
    ] = False,
    create: Annotated[
        bool,
        typer.Option(
            "--create",
            help="Create runtime auth profile when local cache does not exist",
        ),
    ] = False,
    recreate: Annotated[
        bool,
        typer.Option(
            "--recreate",
            help="Recreate runtime auth profile even when cache already exists",
        ),
    ] = False,
    bootstrap_ci: Annotated[
        bool,
        typer.Option(
            "--bootstrap-ci",
            help="Sync local runtime auth profile secrets to GitHub environment secrets",
        ),
    ] = False,
) -> None:
    """Manage runtime auth profiles, optionally scoped by config.yaml/project ID or across all cached profiles for validate-only runs."""
    try:
        if project_config is not None and project_id is not None:
            raise RuntimeError(
                "--project-config and --project-id are mutually exclusive. "
                "Use --project-config to resolve identity from config.yaml, or use "
                "--project-id with --client-name."
            )
        if project_config is not None and client_name is not None:
            raise RuntimeError(
                "--client-name is valid only with --project-id. "
                "--project-config resolves client_name from config.yaml."
            )
        if project_config is None and project_id is None and client_name is not None:
            raise RuntimeError(
                "--client-name requires --project-id, or omit both for global --validate-profile."
            )
        if not bootstrap_ci and (github_repo is not None or github_token_env != "GH_TOKEN"):
            raise RuntimeError(
                "--github-repo and --github-token-env are valid only with --bootstrap-ci."
            )
        if not any((validate_profile, create, recreate, bootstrap_ci)):
            raise RuntimeError(
                "Select at least one action: --validate-profile, --create, --recreate, --bootstrap-ci."
            )
        if create and recreate:
            raise RuntimeError("--create and --recreate are mutually exclusive.")

        only_validate_without_target = (
            validate_profile
            and not create
            and not recreate
            and not bootstrap_ci
            and project_id is None
            and project_config is None
        )
        resolved_sdk_config = sdk_config_file.resolve() if sdk_config_file else None
        material: RuntimeAuthCacheMaterial | None = None
        profile_targets: list[tuple[str, str]]
        if only_validate_without_target:
            profile_targets = _discover_runtime_auth_profiles()
            if not profile_targets:
                raise RuntimeError(
                    f"No runtime auth profiles found under {_runtime_auth_cache_root()}"
                )
        else:
            resolved_project_id = _resolve_project_id_for_auth_bootstrap(
                project_id=project_id,
                project_config=project_config,
            )
            resolved_client_name = _resolve_client_name_for_runtime_profile(
                project_id=resolved_project_id,
                client_name=client_name,
                project_config=project_config,
            )
            profile_targets = [(resolved_client_name, resolved_project_id)]

            if create or recreate:
                material, created = _create_or_recreate_runtime_auth_profile(
                    project_id=resolved_project_id,
                    client_name=resolved_client_name,
                    recreate=recreate,
                    profile=profile,
                    endpoint=endpoint,
                    sdk_config_file=resolved_sdk_config,
                )
                profile_label = "runtime auth profile"
                if recreate:
                    console.print(f"Recreated {profile_label} for project '{resolved_project_id}'.")
                else:
                    if created:
                        console.print(
                            f"Created {profile_label} for project '{resolved_project_id}'."
                        )
                    else:
                        sentence_label = profile_label[:1].upper() + profile_label[1:]
                        console.print(
                            f"{warning_markup(f'{sentence_label} already exists')} for project "
                            f"'{resolved_project_id}'."
                        )

            if bootstrap_ci:
                if material is None:
                    material = _runtime_auth_cache_material(
                        project_id=resolved_project_id,
                        client_name=resolved_client_name,
                    )
                if material is None:
                    raise RuntimeError(
                        "Runtime auth profile not found in local cache. "
                        "Run `nebius-cxcli auth --create --project-id <id> --client-name <name>` first."
                    )
                repo_root_hint: Path | None = None
                if project_config is not None:
                    repo_root_hint = _require_git_root(project_config.resolve().parent)
                elif github_repo is None:
                    repo_root_hint = _require_git_root(Path.cwd())
                synced_repo_slug, synced_environment_name, synced_secret_names = (
                    _sync_runtime_auth_profile_to_ci_environment(
                        material=material,
                        client_name=resolved_client_name,
                        github_repo=github_repo,
                        github_token_env=github_token_env,
                        repo_root_hint=repo_root_hint,
                    )
                )
                console.print(
                    "Synced GitHub environment secrets to "
                    f"{synced_repo_slug}/{synced_environment_name} "
                    f"({len(synced_secret_names)} secret(s))"
                )

        if validate_profile:
            statuses = [
                _runtime_auth_profile_status(
                    project_id=current_project_id,
                    client_name=current_client_name,
                    profile=profile,
                    endpoint=endpoint,
                    sdk_config_file=resolved_sdk_config,
                )
                for current_client_name, current_project_id in profile_targets
            ]
            for status in statuses:
                console.print(f"Project ID: {status.project_id}")
                console.print(f"  Client name: {status.client_name}")
                console.print(f"  Cache dir: {status.cache_dir}")
                console.print(f"  Metadata file: {status.metadata_file}")
                console.print(f"  Service account ID: {status.service_account_id or '(missing)'}")
                console.print(f"  Auth public key ID: {status.auth_public_key_id or '(missing)'}")
                console.print(
                    f"  Private key file: {status.private_key_file or '(missing)'} "
                    f"(exists={status.private_key_exists})"
                )
                cloud_state = (
                    "unknown"
                    if status.cloud_public_key_exists is None
                    else ("yes" if status.cloud_public_key_exists else "no")
                )
                console.print(f"  Nebius auth public key exists: {cloud_state}")
                if status.cloud_check_error:
                    console.print(f"  Nebius check error: {status.cloud_check_error}")
                if status.issues:
                    for issue in status.issues:
                        console.print(f"  {error_markup(f'- {issue}')}")
                else:
                    console.print("  [green]Profile status: OK[/green]")
                console.print("")

            failed = [status for status in statuses if status.issues]
            if failed:
                raise RuntimeError(
                    "Runtime auth profile validation failed for project(s): "
                    + ", ".join(status.project_id for status in failed)
                )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "render",
    short_help="Use CONFIG_YAML to render and transactionally replace generated/ artifacts.",
)
def render_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite an existing generated bundle without interactive confirmation.",
        ),
    ] = False,
) -> None:
    """Render and transactionally replace generated artifacts from one project config.yaml, prompting before overwrite unless --force is provided."""
    try:
        config, paths = _load_runtime_context(config_path)
        materialize_mk8s_gpu_app_values(config)
        materialize_soperator_companion_app_values(config)
        materialize_observability_infra_values(config)
        materialize_observability_app_values(config)
        materialize_mysterybox_eso_app_values(config)
        resolved_source_profile = resolve_component_sources_profile()
        _assert_not_nested_deployments_root(paths.deployments_dir)
        if not _confirm_render_overwrite(paths, force=force):
            console.print(
                "[yellow]Render cancelled[/yellow]; existing generated artifacts were left untouched."
            )
            raise typer.Exit(code=0)
        gitignore_result = _ensure_deployments_gitignore(
            deployments_root=paths.deployments_dir,
        )
        component_output_values = _runtime_component_output_values(config, paths)
        staged_paths = staged_generated_paths(paths)
        try:
            staged_paths.infra_dir.mkdir(parents=True, exist_ok=True)
            staged_paths.flux_dir.mkdir(parents=True, exist_ok=True)
            staged_paths.inventory_dir.mkdir(parents=True, exist_ok=True)
            written: list[Path] = []
            written.extend(
                render_terraform_artifacts(
                    config,
                    staged_paths,
                    source_profile=resolved_source_profile,
                )
            )
            written.extend(
                render_flux(
                    config,
                    staged_paths,
                    component_output_values=component_output_values,
                )
            )
            _print_mk8s_gpu_validation_warnings(config)
            quota_report = _warn_on_config_live_quota_issues(config, paths, phase="render")
            _write_generated_runtime_manifest(
                config,
                staged_paths,
                source_profile=resolved_source_profile,
                quota_report=quota_report,
                output_path=manifest_path_for_generated_dir(staged_paths.generated_dir),
                manifest_paths=paths,
            )
            promote_staged_generated_paths(staged_paths, paths)
        except Exception:
            reset_generated_bundle(staged_paths)
            raise
        manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
        lock_generated = _try_generate_terraform_lock_file(config, paths)
        console.print(f"Rendered {len(sorted(written))} file(s) under {paths.generated_dir}")
        console.print(f"Source profile: {resolved_source_profile.value}")
        if resolved_source_profile == SourceProfile.LOCAL:
            console.print(
                f"{warning_markup('WARNING:', bold=True)} local source profile may embed local Terraform "
                "module paths; do not commit or use these generated artifacts in CI."
            )
        console.print(f"Generated deployment manifest: {manifest_path}")
        if quota_report.has_confirmed_insufficiency:
            console.print(
                f"{warning_markup('Render completed with quota warnings.')} "
                "The generated manifest includes the report, and deploy will fail until the "
                "required quota is available and any selected GPU shape has matching Capacity "
                "Dashboard capacity."
            )
        if gitignore_result.path is not None:
            if gitignore_result.wrote:
                console.print(f"Ensured deployments .gitignore: {gitignore_result.path}")
            else:
                console.print(f"Deployments .gitignore up-to-date: {gitignore_result.path}")
        if lock_generated:
            console.print(
                f"Generated Terraform lock file: {paths.infra_dir / '.terraform.lock.hcl'}"
            )
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("mk8s-token", hidden=True)
def mk8s_token_command(
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Project ID used to resolve cached runtime auth."),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option("--client-name", help="Client name used to resolve cached runtime auth."),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Optional Nebius API endpoint override."),
    ] = None,
) -> None:
    """Emit ExecCredential JSON for MK8s kubeconfig exec auth."""
    try:
        if not _runtime_auth_env_available() and project_id and client_name:
            _runtime_auth_cache_load(project_id=project_id, client_name=client_name)
        sdk = init_nebius_sdk(
            parent_id=project_id or None,
            endpoint=endpoint,
            context="MK8s exec auth",
        )
        try:
            token = sdk.get_token_sync(timeout=20.0)
        finally:
            with suppress(Exception):
                sdk.sync_close()
        token_value = _non_empty_text(getattr(token, "token", None))
        if not token_value:
            raise RuntimeError("Nebius SDK returned an empty IAM token for MK8s exec auth.")
        status: dict[str, str] = {"token": token_value}
        expiration = _iso8601_utc(getattr(token, "expiration", None))
        if expiration:
            status["expirationTimestamp"] = expiration
        print(
            json.dumps(
                {
                    "apiVersion": "client.authentication.k8s.io/v1",
                    "kind": "ExecCredential",
                    "status": status,
                }
            )
        )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "deploy",
    short_help="Use CONFIG_YAML to deploy locally from the sibling rendered bundle.",
)
def deploy_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_DEPLOY_CONFIG_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=("Automatically bootstrap runtime auth material when required values are missing"),
        ),
    ] = True,
    skip_validations: Annotated[
        bool,
        typer.Option(
            "--skip-validations",
            help=(
                "Skip optional deploy-time validations from config.yaml/generated manifest "
                "for this run only. Required platform validations still run."
            ),
        ),
    ] = False,
    skip_validation: Annotated[
        list[str] | None,
        typer.Option(
            "--skip-validation",
            help=(
                "Skip one optional deploy-time validation for this run only. "
                "Repeatable. Supported values: operator-readiness, gpu-visibility, nccl, "
                "observability-ingestion."
            ),
        ),
    ] = None,
    target_ref: Annotated[
        str | None,
        typer.Option(
            "--target",
            help=(
                f"Explicit {_MK8S_TARGET_ID_HELP} for Flux/app work and deploy-time "
                "validations when the bundle declares more than one built-in cluster target."
            ),
        ),
    ] = None,
    all_targets: Annotated[
        bool,
        typer.Option(
            "--all-targets",
            help="Run target-scoped Flux/app work and deploy-time validations for every built-in cluster target in the bundle.",
        ),
    ] = False,
) -> None:
    """Deploy an existing generated artifact bundle locally from config.yaml.

    This command is a reconcile/apply path against the rendered bundle.
    Deploy resolves the sibling `generated/` directory and still uses
    `generated/nebius-cxcli-manifest.json` as the authoritative deploy
    contract so source-config changes after render do not silently alter
    the deployed bundle. Before Terraform apply it runs a generated-bundle
    deploy preflight covering strict readiness checks, MK8s network preflight,
    live Nebius quota/capacity validation, Terraform validation, and rendered
    Flux manifest validation when apps are enabled. For bundled MK8s reruns,
    that quota/capacity phase initializes the backend and discounts MK8s quota
    already managed in the current Terraform state, so unchanged existing
    clusters do not fail like fresh creates while real added capacity still
    fails fast. Terraform apply runs next, refresh the deploy report runs after
    that, and when app charts are enabled Flux then converges the existing
    generated bundle onto live infrastructure and workloads. When a built-in
    cluster handoff such as MK8s is enabled, deploy also refreshes local
    kubeconfig access for that cluster even if no app charts are configured.
    When more than one built-in cluster target is present, use `--target
    <target-id>` or `--all-targets` for Flux/app work and deploy-time
    validations. The target id is the normalized cluster resource name stored as
    that MK8s row's `instance_id`. For a single-target run, the refreshed
    validation summary and deploy report include only validations for that
    selected target; `--all-targets` reports every selected target.
    Existing managed resources may be updated when the bundle differs from live
    state. Use `nebius-cxcli terraform plan
    <generated>` first when you need a non-mutating preview. It does not run
    `flux bootstrap` or configure GitOps sync, and it does not create or
    update GitHub workflows, environments, or CI secrets; use `nebius-cxcli
    bootstrap-ci <config.yaml>` explicitly for that. Use
    `--skip-validations` or repeatable `--skip-validation <kind>` only when
    you want a one-run override for optional checks without changing the
    persisted project config. Required platform validations, including native
    ESO MysteryBox connectivity when that sync path is configured, still run.
    Deploy-time validations include configured MK8s GPU checks, the generated
    Observability Agent ingestion guardrail for observability-enabled MK8s
    targets, and required ESO MysteryBox connectivity checks for native
    MysteryBox sync targets. When deploy-time validations are configured, deploy
    keeps the machine-readable JSON detail files under `generated/inventory/`
    and refreshes the combined `generated/inventory/deploy-report.md`. The final
    terminal footer groups validation PASS/FAIL, copy-paste commands, and
    important generated paths.
    """
    try:
        config, paths, manifest = _load_deploy_context(config_path)
        skip_validation_kinds = _resolve_deploy_validation_skip_kinds(tuple(skip_validation or ()))
        summary = _deploy_generated_artifacts(
            config,
            paths,
            manifest,
            auto_auth_bootstrap=auto_auth_bootstrap,
            skip_validations=skip_validations,
            skip_validation_kinds=skip_validation_kinds,
            requested_target_ref=target_ref,
            all_targets=all_targets,
        )
        _print_deploy_command_footer(config, paths, summary, succeeded=True)
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "destroy",
    short_help="Use CONFIG_YAML to destroy all rendered project resources.",
)
def destroy_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_GENERATED_BUNDLE_CONFIG_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=("Automatically bootstrap runtime auth material when required values are missing"),
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the destructive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Destroy all rendered project resources locally from config.yaml.

    This command is the destructive inverse of `deploy`: it resolves the
    sibling `generated/` directory from the project `config.yaml`, then uses
    `generated/nebius-cxcli-manifest.json` as the authoritative teardown
    contract for the whole rendered project. When apps target an external or
    current cluster, it deletes the rendered Flux manifests first and then runs
    Terraform destroy against the rendered infra bundle. When the generated
    bundle destroys the handed-off cluster directly, it skips the separate Flux
    delete step and relies on cluster teardown instead. It does not rerender
    from `config.yaml`, and it does not uninstall Flux controllers or bootstrap
    GitHub/CI state.
    """
    try:
        config, paths, manifest = _load_destroy_context(config_path)
        prompt_text, warning_text = _destroy_confirmation_text(config, paths, manifest)
        if not _confirm_generated_destroy(
            yes=yes,
            action_label="Destroy",
            prompt_text=prompt_text,
            warning_text=warning_text,
        ):
            console.print("No changes applied.")
            return
        _destroy_generated_artifacts(
            config,
            paths,
            manifest,
            auto_auth_bootstrap=auto_auth_bootstrap,
            yes=yes,
        )
        console.print(f"Local destroy completed from {paths.generated_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command(
    "plan",
    short_help="Use GENERATED_PATH to run Terraform plan from generated/infra.",
)
def terraform_plan_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_INFRA_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Run Terraform plan against an existing generated/infra bundle."""
    try:
        config, paths, _manifest = _load_generated_infra_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        runtime_env = _terraform_runtime_env(config)
        runtime_env.update(
            _collect_mysterybox_runtime_payload_values(
                config,
                prompt=_console_is_terminal(),
            )
        )
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        _validate_generated_mk8s_resource_name_preflight(
            config,
            paths,
            runtime_env=runtime_env,
        )
        terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
        terraform_plan(paths.infra_dir, extra_env=runtime_env, initialize=False)
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command(
    "apply",
    short_help="Use GENERATED_PATH to run Terraform apply from generated/infra.",
)
def terraform_apply_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_INFRA_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Refresh the deploy report, then run Terraform apply against an existing generated/infra bundle."""
    try:
        config, paths, manifest = _load_generated_infra_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths, validations=_manifest_deploy_validations(manifest))
        runtime_env = _terraform_runtime_env(config)
        mysterybox_payload_env = _collect_mysterybox_runtime_payload_values(
            config,
            prompt=_console_is_terminal(),
        )
        runtime_env.update(mysterybox_payload_env)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        _validate_generated_mk8s_resource_name_preflight(
            config,
            paths,
            runtime_env=runtime_env,
        )
        terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
        status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(
            config
        )
        apply_kwargs: dict[str, Any] = {"initialize": False}
        if status_watchers:
            apply_kwargs["status_watchers"] = status_watchers
        if mysterybox_payload_env:
            apply_kwargs["extra_env"] = mysterybox_payload_env
        _run_terraform_apply_with_status(config, paths, **apply_kwargs)
        _sync_mysterybox_primary_version_ids_to_config(config, paths, initialize=False)
        write_inventory(config, paths, validations=_manifest_deploy_validations(manifest))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command(
    "destroy",
    short_help="Use GENERATED_PATH to run Terraform destroy from generated/infra.",
)
def terraform_destroy_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_INFRA_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the destructive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Run Terraform destroy against an existing generated/infra bundle."""
    try:
        config, paths, manifest = _load_generated_infra_context(generated_path)
        if not _confirm_generated_destroy(
            yes=yes,
            action_label="Terraform destroy",
            prompt_text="Continue and destroy the rendered infra resources?",
            warning_text=(
                "Terraform destroy will destroy the rendered infra resources under "
                f"{paths.infra_dir}."
            ),
        ):
            console.print("No changes applied.")
            return
        status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(
            config
        )
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        _run_terraform_destroy_with_recovery(
            config,
            paths,
            auto_auth_bootstrap=auto_auth_bootstrap,
            yes=yes,
            initialize=True,
            status_watchers=status_watchers or None,
        )
        console.print(f"Terraform destroy completed from {paths.infra_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command(
    "unlock",
    short_help="Use GENERATED_PATH to inspect or clear a Terraform lock in generated/infra.",
)
def terraform_unlock_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_INFRA_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Override local safety checks and force-unlock even when the lock owner is different "
                "or local Terraform/deploy processes are still detected"
            ),
        ),
    ] = False,
) -> None:
    """Clear a stale remote Terraform state lock for an existing generated/infra bundle."""
    try:
        config, paths, _manifest = _load_generated_infra_context(generated_path)
        lock_info = _unlock_terraform_state_lock(
            config,
            paths,
            auto_auth_bootstrap=auto_auth_bootstrap,
            force=force,
        )
        if lock_info is None:
            settings = backend_settings_from_config(config)
            console.print(
                "No remote Terraform state lock is present for "
                f"{settings.bucket}/{settings.key}.tflock."
            )
            return
        console.print(
            "Terraform state lock cleared: "
            f"id={lock_info.lock_id} "
            f"owner={lock_info.who or '(unknown)'} "
            f"created={lock_info.created or '(unknown)'} "
            f"object={lock_info.bucket}/{lock_info.object_key}"
        )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command(
    "destroy",
    short_help="Use GENERATED_PATH to delete rendered Flux resources from generated/flux.",
)
def flux_destroy_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_FLUX_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the destructive confirmation prompt.",
        ),
    ] = False,
    target_ref: Annotated[
        str | None,
        typer.Option(
            "--target",
            help=(
                f"Explicit {_MK8S_TARGET_ID_HELP} when the bundle declares more than "
                "one built-in cluster target."
            ),
        ),
    ] = None,
    all_targets: Annotated[
        bool,
        typer.Option(
            "--all-targets",
            help="Delete rendered app resources from every built-in cluster target in the bundle.",
        ),
    ] = False,
) -> None:
    """Delete rendered Flux resources directly from an existing generated/flux bundle."""
    try:
        config, paths, manifest = _load_generated_flux_context(generated_path)
        if _active_chart_count(config) == 0:
            raise RuntimeError("No enabled apps charts are configured for this project.")
        if not _confirm_generated_destroy(
            yes=yes,
            action_label="Flux destroy",
            prompt_text="Continue and delete the rendered app resources from the target cluster?",
            warning_text=(
                "Flux destroy will delete the rendered app resources declared under "
                f"{paths.flux_dir}."
            ),
        ):
            console.print("No changes applied.")
            return
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        _destroy_rendered_flux_bundle(
            config,
            paths,
            manifest,
            requested_target_ref=target_ref,
            all_targets=all_targets,
        )
        console.print(f"Flux resources deleted from {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command(
    "bootstrap",
    short_help="Use GENERATED_PATH to bootstrap or reconcile Flux from generated/flux.",
)
def flux_bootstrap_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_FLUX_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = False,
    target_ref: Annotated[
        str | None,
        typer.Option(
            "--target",
            help=(
                f"Explicit {_MK8S_TARGET_ID_HELP} when the bundle declares more than "
                "one built-in cluster target."
            ),
        ),
    ] = None,
    all_targets: Annotated[
        bool,
        typer.Option(
            "--all-targets",
            help="Bootstrap or reconcile Flux for every built-in cluster target in the bundle.",
        ),
    ] = False,
) -> None:
    """Refresh the deploy report, then bootstrap or reconcile Flux from an existing generated/flux bundle."""
    try:
        config, paths, manifest = _load_generated_flux_context(generated_path)
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        else:
            _ensure_runtime_auth_material(
                config,
                need_terraform=False,
                auto_bootstrap=auto_auth_bootstrap,
            )
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths, validations=_manifest_deploy_validations(manifest))
        manifest_targets = _manifest_deploy_targets(manifest)
        if manifest_targets:
            selected_targets = _resolve_selected_deploy_targets(
                manifest,
                requested_target_ref=target_ref,
                all_targets=all_targets,
            )
            persist_local_kubeconfig = True
            set_current_context = len(selected_targets) == 1 and not all_targets
            for target in selected_targets:
                target_ref_value = str(target["target_ref"])
                target_paths = _paths_for_target_flux_dir(paths, target)
                if len(selected_targets) > 1:
                    console.print(f"[bold]Target {target_ref_value}[/bold]")
                with ExitStack() as stack:
                    kube_env = _prepare_cluster_handoff_kube_env(
                        config,
                        paths,
                        stack=stack,
                        target=target,
                        persist_local_kubeconfig=persist_local_kubeconfig,
                        set_current_context=set_current_context,
                    )
                    _report_cluster_nodes_status(
                        extra_env=kube_env, emit=lambda message: console.print(message)
                    )
                    _ensure_mysterybox_eso_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                        auto_auth_bootstrap=auto_auth_bootstrap,
                    )
                    _ensure_grafana_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                    )
                    action = ensure_flux(target_paths, extra_env=kube_env)
                console.print(f"Flux {action} for {target_paths.flux_dir}")
        else:
            _report_cluster_nodes_status(
                extra_env=None, emit=lambda message: console.print(message)
            )
            _ensure_mysterybox_eso_runtime_before_flux(
                config,
                extra_env=None,
                auto_auth_bootstrap=auto_auth_bootstrap,
            )
            _ensure_grafana_runtime_before_flux(config, extra_env=None)
            action = ensure_flux(paths, extra_env=None)
            console.print(f"Flux {action} for {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command(
    "apply",
    short_help="Use GENERATED_PATH to apply Flux directly from generated/flux.",
)
def flux_apply_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_FLUX_ARGUMENT_HELP,
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
    target_ref: Annotated[
        str | None,
        typer.Option(
            "--target",
            help=(
                f"Explicit {_MK8S_TARGET_ID_HELP} when the bundle declares more than "
                "one built-in cluster target."
            ),
        ),
    ] = None,
    all_targets: Annotated[
        bool,
        typer.Option(
            "--all-targets",
            help="Apply rendered Flux resources to every built-in cluster target in the bundle.",
        ),
    ] = False,
) -> None:
    """Refresh the deploy report and apply an existing generated/flux bundle directly."""
    try:
        config, paths, manifest = _load_generated_flux_context(generated_path)
        if _active_chart_count(config) == 0:
            raise RuntimeError("No enabled apps charts are configured for this project.")
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths, validations=_manifest_deploy_validations(manifest))
        manifest_targets = _manifest_deploy_targets(manifest)
        grafana_statuses: list[dict[str, Any]] = []
        if manifest_targets:
            selected_targets = _resolve_selected_deploy_targets(
                manifest,
                requested_target_ref=target_ref,
                all_targets=all_targets,
            )
            persist_local_kubeconfig = True
            set_current_context = len(selected_targets) == 1 and not all_targets
            for target in selected_targets:
                target_ref_value = str(target["target_ref"])
                target_paths = _paths_for_target_flux_dir(paths, target)
                if len(selected_targets) > 1:
                    console.print(f"[bold]Target {target_ref_value}[/bold]")
                with ExitStack() as stack:
                    kube_env = _prepare_cluster_handoff_kube_env(
                        config,
                        paths,
                        stack=stack,
                        target=target,
                        persist_local_kubeconfig=persist_local_kubeconfig,
                        set_current_context=set_current_context,
                    )
                    _report_cluster_nodes_status(
                        extra_env=kube_env, emit=lambda message: console.print(message)
                    )
                    _ensure_mysterybox_eso_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                        auto_auth_bootstrap=auto_auth_bootstrap,
                    )
                    _ensure_grafana_runtime_before_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                    )
                    _apply_rendered_flux(target_paths, extra_env=kube_env)
                    grafana_statuses.extend(
                        _collect_grafana_status_after_flux(
                            config,
                            extra_env=kube_env,
                            target_ref=target_ref_value,
                        )
                    )
                    _warn_if_flux_gitops_not_bootstrapped(
                        config,
                        target_paths,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                    )
                console.print(f"Flux applied from {target_paths.flux_dir}")
        else:
            _report_cluster_nodes_status(
                extra_env=None, emit=lambda message: console.print(message)
            )
            _ensure_mysterybox_eso_runtime_before_flux(
                config,
                extra_env=None,
                auto_auth_bootstrap=auto_auth_bootstrap,
            )
            _ensure_grafana_runtime_before_flux(config, extra_env=None)
            _apply_rendered_flux(paths, extra_env=None)
            grafana_statuses.extend(_collect_grafana_status_after_flux(config, extra_env=None))
            _warn_if_flux_gitops_not_bootstrapped(
                config,
                paths,
                extra_env=None,
            )
            console.print(f"Flux applied from {paths.flux_dir}")
        if grafana_statuses:
            write_grafana_status(
                paths,
                grafana_statuses,
                preserve_existing=bool(
                    manifest_targets
                    and selected_targets
                    and len(selected_targets) < len(manifest_targets)
                ),
            )
            write_inventory(config, paths, validations=_manifest_deploy_validations(manifest))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "discover",
    short_help="Use DEPLOYMENT_SCOPE to emit changed-project discovery JSON for CI.",
)
def discover_command(
    target_path: Annotated[
        Path,
        typer.Argument(
            metavar="DEPLOYMENT_SCOPE",
            help=(
                "Path to the deployments root or any narrower directory under it, including a single "
                "project directory or generated/. When inside a git repository, discover uses git "
                "change detection for changed <tenant-folder>/<project-folder>/config.yaml and generated/** paths under that scope; "
                "otherwise it scans all config.yaml files under the scope."
            ),
        ),
    ],
    include_all: Annotated[
        bool,
        typer.Option("--all", help="Include all config.yaml files instead of changed only"),
    ] = False,
) -> None:
    """Print discovery JSON for changed projects under a deployment scope directory."""
    try:
        base_path = target_path.resolve()
        _validate_deployments_root_target(base_path)
        repo_root = _try_git_root(base_path)
        deployments_root = _resolve_deployments_root(base_path)
        deployments_dir_for_ci = (
            _relative_deployments_dir_for_ci(repo_root, deployments_root)
            if repo_root is not None
            else str(deployments_root)
        )
        payload = discover_configs(
            deployments_dir=deployments_dir_for_ci,
            include_all=include_all,
            repo_root=repo_root,
        )
        print(json.dumps(payload, sort_keys=True))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


def _interactive_email_settings_setup(*, config_path: Path | None) -> tuple[EmailSettings, Path]:
    resolved_path = resolve_email_config_file(explicit=config_path)
    current = load_email_settings(explicit=config_path)
    host = typer.prompt(
        "SMTP host (blank disables local email config)",
        default=current.host,
        show_default=bool(current.host),
    ).strip()
    if not host:
        disable_email_settings(explicit=config_path)
        return EmailSettings(), resolved_path

    port_text = typer.prompt("SMTP port", default=str(current.port)).strip()
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("SMTP port must be a positive integer.") from exc
    if port <= 0:
        raise RuntimeError("SMTP port must be a positive integer.")

    starttls = typer.confirm("Use STARTTLS?", default=current.starttls)
    from_addr = typer.prompt(
        "SMTP from address (blank uses username or noreply@localhost)",
        default=current.from_addr,
        show_default=bool(current.from_addr),
    ).strip()
    username = typer.prompt(
        "SMTP username (blank disables SMTP auth)",
        default=current.username,
        show_default=bool(current.username),
    ).strip()
    password = ""
    if username:
        password = typer.prompt(
            "SMTP password (blank keeps existing password)",
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        if not password:
            password = current.password
        if not password:
            raise RuntimeError("SMTP password is required when SMTP username is set.")
    settings = EmailSettings(
        host=host,
        port=port,
        starttls=starttls,
        from_addr=from_addr,
        username=username,
        password=password,
    )
    write_email_settings(settings, explicit=config_path)
    return settings, resolved_path


@app.command(
    "email",
    short_help="Use CONFIG_YAML to send the deploy report email, or omit it with --setup.",
)
def email_command(
    config_path: Annotated[
        Path | None,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=(
                f"{_GENERATED_BUNDLE_CONFIG_ARGUMENT_HELP} Omit the path only when using --setup."
            ),
        ),
    ] = None,
    setup: Annotated[
        bool,
        typer.Option(
            "--setup",
            help=(
                "Interactively create, update, or remove local SMTP settings in "
                "~/.config/nebius-cxcli/email.yaml"
            ),
        ),
    ] = False,
) -> None:
    """Send the existing deploy report from config.yaml, or manage local SMTP settings with --setup.

    When a project config path is provided, the command resolves sibling
    `generated/`, sends the existing `deploy-report.md`, and uses the generated
    manifest runtime snapshot for the recipient/runtime contract.
    """
    try:
        if setup:
            settings, written_path = _interactive_email_settings_setup(config_path=None)
            if settings.enabled:
                console.print(f"Configured local email settings: {written_path}")
            else:
                console.print(f"Removed local email settings: {written_path}")
            if config_path is None:
                return
        if config_path is None:
            raise RuntimeError("config_path is required unless --setup is used.")
        config_obj, paths, _manifest = _load_email_context(config_path)
        settings = load_email_settings()
        result: DeployReportEmailResult = send_deploy_report_email(
            config_obj,
            paths,
            smtp_settings=email_runtime_settings(settings),
        )
        if result.sent:
            console.print(result.message)
        elif result.reason in {"smtp_unconfigured", "recipient_missing"}:
            console.print(f"{warning_markup('WARNING:', bold=True)} {result.message}")
        else:
            console.print(result.message)
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


def main() -> None:
    app()
