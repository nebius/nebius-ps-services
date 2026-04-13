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
from contextlib import ExitStack, suppress
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

from . import __version__, native_logs
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
    resolve_component_dependencies,
)
from .config_loader import load_config, normalize_runtime_config_payload
from .config_template import starter_config_yaml
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
from .helm_client import HelmChartReference, HelmClient, chart_cli_contract_findings
from .iam_bootstrap import (
    auth_public_key_exists,
    bootstrap_ci_service_account,
)
from .infra_render import (
    is_portable_module_source,
    render_terraform_artifacts,
    rendered_module_sources,
)
from .inventory_ops import write_inventory
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
from .mk8s_preflight import validate_mk8s_network_preflight
from .notify_ops import InventoryEmailResult, send_inventory_email
from .paths import (
    ProjectPaths,
    resolve_generated_paths,
    resolve_project_paths,
    validate_path_alignment,
)
from .provider_options import (
    OptionChoice,
    ProviderOptionLookup,
    TenantProjectValidationResult,
)
from .quota_checks import QuotaReport, assess_live_quotas, format_quota_report_lines
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
from .sdk_auth import init_nebius_sdk
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
    terraform_validate,
)
from .terraform_provider import build_provider_module_name

console = Console()


def _console_is_terminal() -> bool:
    return bool(console.is_terminal)


def _quota_failure_message(report: QuotaReport, *, phase: str) -> str:
    lines = [
        f"Nebius quota is insufficient for {phase}. Increase the quota and retry.",
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
    return phase == "quota check"


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
    try:
        report = assess_live_quotas(
            config,
            context=f"{phase} quota assessment",
            all_regions=all_regions,
        )
    except Exception as exc:
        report = QuotaReport(
            tenant_id="",
            project_id="",
            region_id="",
            checked_at=datetime.now(UTC).isoformat(),
            errors=(f"{phase} quota assessment failed: {exc}",),
        )
    _print_live_quota_report(report, phase=phase)
    return report


def _raise_on_live_quota_issues(config: Any, *, phase: str) -> QuotaReport:
    report = _warn_on_live_quota_issues(config, phase=phase)
    if report.has_confirmed_insufficiency:
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
PayloadPath = tuple[str | int, ...]
_TEMP_PRIVATE_KEY_FILES: list[Path] = []
_RUNTIME_TF_SERVICE_ACCOUNT_NAME = "nebius-cxcli-tf-sa"
_RUNTIME_AUTH_CACHE_ENV = "NEBIUS_CXCLI_RUNTIME_AUTH_DIR"
_RUNTIME_AUTH_CACHE_FILE = "runtime-auth.json"
_BENIGN_KUBECTL_OUTPUT_MARKERS = (
    "token from NEBIUS_IAM_TOKEN env is used",
    "missing the kubectl.kubernetes.io/last-applied-configuration annotation",
    "The missing annotation will be patched automatically.",
)
_DEPLOYMENTS_ROOT_ARGUMENT_HELP = (
    "Deployments root directory. Pass the folder that contains or will contain "
    "<tenant>/<project>/config.yaml; any existing directory works."
)
_CONFIG_YAML_ARGUMENT_HELP = (
    "Path to project config.yaml under the deployments root "
    "(<tenant>/<project>/config.yaml)."
)
_GENERATED_PATH_ARGUMENT_HELP = (
    "Path to generated/, one of its subdirectories, or a file under generated/."
)
_GENERATED_INFRA_ARGUMENT_HELP = "Path to generated/ or generated/infra."
_GENERATED_FLUX_ARGUMENT_HELP = "Path to generated/ or generated/flux."
_GENERATED_INVENTORY_ARGUMENT_HELP = "Path to generated/ or generated/inventory."
_GENERATED_INVENTORY_SCAFFOLD_TEXT = "# Inventory\n\nGenerated by `nebius-cxcli inventory write`.\n"
_COMPONENT_SOURCES_ARGUMENT_HELP = (
    "Optional explicit component_sources.yaml path. "
    "When omitted, validate-sources uses the normal component source resolution order."
)
app = typer.Typer(
    add_completion=False,
    help=(
        "Nebius artifact generator and deployer. Target guide: create bootstraps one "
        "tenant/project folder from a deployments root directory and overwrites existing "
        "resolved tenant/project folders only with confirmation; component list/add/remove are "
        "the day-2 config.yaml editing surface; "
        "discover uses a deployment-scope directory; validate/quota-check/render/bootstrap-ci use config.yaml; "
        "validate-generated/deploy/destroy/terraform/flux/inventory/email use generated/, "
        "validate-sources accepts optional component_sources.yaml, and "
        "auth has no positional path."
    ),
)
component_app = typer.Typer(
    help=(
        "Inspect or edit enabled source-driven infra/app component instances in an "
        "existing config.yaml. Use this after create for day-2 add/remove/list changes."
    )
)
terraform_app = typer.Typer(
    help="Run infra-only Terraform operations against generated/ or generated/infra."
)
flux_app = typer.Typer(
    help="Apply, bootstrap, or destroy Flux resources using generated/ or generated/flux."
)
inventory_app = typer.Typer(
    help="Refresh local inventory artifacts from generated/ or generated/inventory."
)

app.add_typer(component_app, name="component")
app.add_typer(terraform_app, name="terraform")
app.add_typer(flux_app, name="flux")
app.add_typer(inventory_app, name="inventory")


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
        _ValidationPhase("active-sources", "Validate active component sources"),
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
    meaningful_files: list[Path] = []
    for path in existing_files:
        if path == paths.inventory_dir / "inventory.md":
            try:
                if path.read_text(encoding="utf-8") == _GENERATED_INVENTORY_SCAFFOLD_TEXT:
                    continue
            except OSError:
                pass
        meaningful_files.append(path)
    if not meaningful_files:
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
    paths: ProjectPaths,
    *,
    yes: bool,
    action_label: str,
    prompt_text: str,
    warning_text: str,
) -> bool:
    console.print(f"{warning_markup('WARNING:', bold=True)} {warning_text} {paths.generated_dir}.")
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
        _ValidationPhase("active-sources", "Validate active component sources"),
        _ValidationPhase("dependencies", "Validate component dependencies"),
        _ValidationPhase("module-schema", "Validate Terraform module inputs"),
    ]
    if strict:
        phase_defs.extend(
            [
                _ValidationPhase("strict-readiness", "Validate strict deployment readiness"),
                _ValidationPhase("mk8s-preflight", "Validate MK8s network preflight"),
            ]
        )

    validation_cache = _ValidationWorkCache()
    resolved_source_profile = resolve_component_sources_profile()
    with _ValidationProgress(title=title, phases=phase_defs) as progress:
        config, _ = progress.run("load-config", lambda: _load_context(config_path))
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

    if strict:
        console.print(f"[green]Valid (strict):[/green] {config_path}")
        return
    console.print(f"[green]Valid:[/green] {config_path}")


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


@dataclass(frozen=True)
class _ExistingProjectCreateDefaults:
    config_path: Path
    tenant_id: str
    project_id: str


def _existing_project_config_paths(deployments_root: Path) -> tuple[Path, ...]:
    if not deployments_root.is_dir():
        return ()
    return tuple(sorted(path.resolve() for path in deployments_root.glob("*/*/config.yaml")))

def _single_existing_project_create_defaults(
    deployments_root: Path,
) -> _ExistingProjectCreateDefaults | None:
    config_paths = _existing_project_config_paths(deployments_root)
    if len(config_paths) != 1:
        return None

    config_path = config_paths[0]
    try:
        project_paths = resolve_project_paths(
            config_path,
            deployments_dir_hint=str(deployments_root),
        )
    except Exception:
        return None

    return _ExistingProjectCreateDefaults(
        config_path=config_path,
        tenant_id=project_paths.path_tenant_id,
        project_id=project_paths.path_project_id,
    )


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
        f"Re-running `create` will replace the resolved tenant/project folder [bold]{project_path}[/bold] from scratch."
    )
    console.print(
        "[dim]Existing infra/apps selections, generated artifacts, and any other files under "
        "that resolved tenant/project folder will not be preserved. Only the current client_info "
        "values are reused as create defaults.[/dim]"
    )
    console.print(
        "[dim]Use `component list/add/remove` for day-2 component edits without replacing the tenant/project folder.[/dim]"
    )


def _confirm_existing_project_overwrite(*, config_path: Path) -> bool:
    _warn_existing_project_overwrite(config_path=config_path)
    console.print(
        "[dim]This only affects that one resolved tenant/project folder. "
        "It does not delete the deployments root or unrelated projects.[/dim]"
    )
    return _wizard_continue_phase(
        "Continue and overwrite the existing tenant/project folder from scratch?",
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


def _validate_component_sources_or_raise() -> None:
    with console.status("[cyan]Validating component_sources.yaml...[/cyan]"):
        source_path, source_issues, source_warnings = _validate_component_sources_registry()
    for warning in source_warnings:
        console.print(f"{warning_markup('Source validation warning:')} {warning}")
    if source_issues:
        raise RuntimeError(
            f"Component sources validation failed for {source_path}:\n  - "
            + "\n  - ".join(source_issues)
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
    return starter_payload


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
    return {token for token in selected if token}


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


@dataclass(frozen=True)
class _ComponentRemoveTarget:
    scope: ComponentScope
    component_id: str
    instance_id: str


def _resolve_component_targets(
    *,
    action: str,
    tokens: list[str],
    infra_entries: tuple[ComponentEntry, ...],
    app_entries: tuple[ComponentEntry, ...],
    existing_infra: set[str],
    existing_apps: set[str],
) -> tuple[set[str], set[str], tuple[str, ...]]:
    infra_lookup = {entry.id: entry for entry in infra_entries}
    app_lookup = {entry.id: entry for entry in app_entries}
    lookup = {**infra_lookup, **app_lookup}
    eligible_ids = (
        {entry.id for entry in infra_entries if entry.id not in existing_infra}
        | {entry.id for entry in app_entries if entry.id not in existing_apps}
        if action == "add"
        else {entry.id for entry in infra_entries if entry.id in existing_infra}
        | {entry.id for entry in app_entries if entry.id in existing_apps}
    )

    normalized = [token.strip().lower() for token in tokens if token.strip()]
    if len(normalized) == 1:
        if normalized[0] == "none":
            return set(), set(), ()
        if normalized[0] == "all":
            return (
                {entry_id for entry_id in eligible_ids if entry_id in infra_lookup},
                {entry_id for entry_id in eligible_ids if entry_id in app_lookup},
                (),
            )

    resolved_infra: set[str] = set()
    resolved_apps: set[str] = set()
    skipped: list[str] = []
    for token in normalized:
        scope: ComponentScope | None = None
        component_id = token
        if ":" in token:
            scope_raw, component_raw = token.split(":", maxsplit=1)
            scope = cast(ComponentScope, scope_raw)
            if scope not in {"infra", "apps"}:
                raise RuntimeError(
                    f"Invalid component selector '{token}'. Use '<component-id>' or 'infra:<id>' / 'apps:<id>'."
                )
            component_id = component_raw.strip().lower()

        entry = lookup.get(component_id)
        if entry is None:
            available = ", ".join(sorted(lookup))
            raise RuntimeError(f"Unknown component id '{component_id}'. Available ids: {available}")
        if scope is not None and entry.scope != scope:
            raise RuntimeError(
                f"Component selector '{token}' targets scope '{scope}', but '{component_id}' is declared under '{entry.scope}'."
            )
        currently_selected = (
            component_id in existing_infra
            if entry.scope == "infra"
            else component_id in existing_apps
        )
        if action == "add" and currently_selected:
            skipped.append(component_id)
            continue
        if action == "remove" and not currently_selected:
            skipped.append(component_id)
            continue
        if entry.scope == "infra":
            resolved_infra.add(component_id)
        else:
            resolved_apps.add(component_id)
    return resolved_infra, resolved_apps, tuple(sorted(set(skipped)))


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
                f"Invalid instance id '{instance_raw}'. Expected lowercase letters, digits, and hyphens."
            )
        targets.append(
            _ComponentAddTarget(
                scope=entry.scope,
                component_id=component_id,
                requested_instance_id=requested_instance_id or None,
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
    by_instance: dict[str, _ComponentRemoveTarget] = {}
    by_scope_and_type: dict[tuple[ComponentScope, str], list[_ComponentRemoveTarget]] = {}
    for entry, row in enabled_specs:
        instance_id = str(row["instance_id"])
        target = _ComponentRemoveTarget(
            scope=entry.scope,
            component_id=entry.id,
            instance_id=instance_id,
        )
        by_instance[instance_id] = target
        by_scope_and_type.setdefault((entry.scope, entry.id), []).append(target)

    normalized = [token.strip() for token in tokens if token.strip()]
    if len(normalized) == 1:
        keyword = normalized[0].lower()
        if keyword == "none":
            return [], ()
        if keyword == "all":
            return list(by_instance.values()), ()

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
                    f"Invalid component selector '{token}'. Use '<instance-id>' or '<component-id>@<instance-id>'."
                )
            candidate = by_instance.get(instance_id)
            if (
                candidate is None
                or candidate.component_id != component_id
                or (scope is not None and candidate.scope != scope)
            ):
                skipped.append(token)
                continue
            if candidate.instance_id not in seen_instances:
                seen_instances.add(candidate.instance_id)
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
                f"Use an instance id or '<component-id>@<instance-id>'. Available instances: {available}"
            )
        if len(flattened) == 1:
            only = flattened[0]
            if only.instance_id not in seen_instances:
                seen_instances.add(only.instance_id)
                selected.append(only)
            continue

        candidate = by_instance.get(normalized_token)
        if candidate is not None and (scope is None or candidate.scope == scope):
            if candidate.instance_id not in seen_instances:
                seen_instances.add(candidate.instance_id)
                selected.append(candidate)
            continue
        skipped.append(token)
    return selected, tuple(sorted(set(skipped)))


def _runtime_required_input_leaf_names(entry: ComponentEntry) -> set[str]:
    if entry.scope != "infra" or not entry.source:
        return set()
    from .runtime_introspection import module_required_variables

    try:
        return set(module_required_variables(entry.source))
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


def _append_component_instance_row(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    requested_instance_id: str | None = None,
) -> dict[str, Any]:
    rows = _scope_rows(payload, scope=entry.scope)
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


def _remove_component_instance_row(
    *,
    payload: dict[str, Any],
    scope: ComponentScope,
    instance_id: str,
) -> dict[str, Any] | None:
    rows = _scope_rows(payload, scope=scope)
    target = normalize_component_token(instance_id)
    for index, row in enumerate(rows):
        if component_instance_id(row) != target:
            continue
        return rows.pop(index)
    return None


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
            selected = questionary.checkbox(
                f"Select {scope} components",
                choices=[
                    questionary.Choice(
                        title=f"{_component_selector_label(entry, scope=scope)}  ({entry.description})",
                        value=entry.id,
                        checked=entry.id in default_selectable,
                    )
                    for entry in selectable_entries
                ],
                instruction="Use arrows and space to toggle; press Enter to confirm.",
                qmark="",
            ).ask()
            if selected is None:
                raise typer.Abort()
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
        f"Select {scope} components (comma-separated ids or indexes)",
        default=default_prompt,
    ).strip()
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
        return defaults
    resolved = _resolve_component_ids_from_tokens(
        scope=scope,
        tokens=tokens,
        entries=entries,
        defaults=defaults,
    )
    return resolved | required_ids


def _wizard_continue_phase(prompt_label: str, *, default: bool = True) -> bool:
    default_raw = "y" if default else "n"
    while True:
        raw = (
            typer.prompt(
                f"{prompt_label} (y/n, {WIZARD_EXIT_TOKEN}=stop wizard)",
                default=default_raw,
                show_default=True,
            )
            .strip()
            .lower()
        )
        if raw == WIZARD_EXIT_TOKEN:
            return False
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print(
            f"{error_markup('Invalid selection')}. Enter y, n, or {WIZARD_EXIT_TOKEN}."
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
        if relative not in {"namespace", "release-name"} and not relative.startswith("values."):
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
    if token.split(".", 1)[0] in {"version", "client_info", "infra", "apps"}:
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
                value = str(raw).strip()
                if not value or value in seen:
                    continue
                choices.append(OptionChoice(value=value, label=value))
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
            if dependency_path and not _non_empty_text(_read_payload_field(payload, dependency_path)):
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
    absolute_roots = {"version", "client_info", "infra", "apps"}
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
            or key.split(".", 1)[0] in absolute_roots
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
    required_names = set(_runtime_required_input_leaf_names(entry))
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
        if not isinstance(parsed, list):
            raise ValueError("Expected a YAML/JSON list value.")
        return parsed
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
        "gpu_node_groups": 10,
        "gpu_nodes_count_per_group": 11,
        "gpu_nodes_platform": 12,
        "gpu_nodes_preset": 13,
        "infiniband_fabric": 14,
        "gpu_drivers_preset": 15,
    }
    full_label = _format_payload_path(path)
    leaf = path[-1] if path else ""
    leaf_name = _normalize_leaf_name(str(leaf)) if isinstance(leaf, str) else ""
    required_rank = (
        0
        if (required_prompt_labels and full_label in required_prompt_labels)
        or (leaf_name and leaf_name in required_leaf_names)
        else 1
    )
    toggle_rank = 0 if leaf_name.endswith("_enabled") else 1
    leaf_rank = leaf_order_hints.get(leaf_name, 100)
    return required_rank, toggle_rank, leaf_rank, full_label


def _maybe_clear_mk8s_infiniband_fabric_after_gpu_shape_change(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider_lookup: ProviderOptionLookup | None,
) -> None:
    if provider_lookup is None or entry.scope != "infra" or entry.id != "mk8s":
        return
    if not full_path_label.endswith(
        (".gpu_enabled", ".gpu_nodes_platform", ".gpu_nodes_preset")
    ):
        return

    component_prefix = _dynamic_component_prefix(entry=entry, full_path_label=full_path_label)
    if not component_prefix:
        return
    fabric_label = f"{component_prefix}.inputs.infiniband_fabric"
    fabric_value = _non_empty_text(_read_payload_field(payload, fabric_label))
    if not fabric_value:
        return

    gpu_enabled = bool(_read_payload_field(payload, f"{component_prefix}.inputs.gpu_enabled"))
    if not gpu_enabled:
        reason = "GPU is no longer enabled"
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
    chart_name = str(component_node.get("id", "")).strip() or entry.id
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
    prompt_suffix = f"{rendered_label} (enter {WIZARD_EXIT_TOKEN} to stop wizard)"
    default_value = str(current).strip() if current is not None else ""
    option_values = [choice.value for choice in choices]
    prompt_default = (
        default_value
        if default_value in option_values
        else (option_values[0] if required and option_values else "")
    )
    if _is_tty_session():
        try:
            import questionary

            rendered_choices = [
                questionary.Choice(title=choice.label, value=choice.value) for choice in choices
            ]
            if not required and not prompt_default:
                rendered_choices.insert(
                    0,
                    questionary.Choice(title="<skip / keep unset>", value="__skip__"),
                )
            rendered_choices.append(questionary.Choice(title="<manual input>", value="__manual__"))
            selected = questionary.select(
                rendered_label,
                choices=rendered_choices,
                instruction="Select one option (or choose manual input).",
                default="__skip__"
                if not required and not prompt_default
                else (prompt_default or None),
                qmark="",
            ).ask()
            if selected is None:
                return current, True
            if selected == "__skip__":
                return current, False
            if selected != "__manual__":
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
            "index or value; blank keeps current" if prompt_default else "index or value; blank keeps unset"
        )

    while True:
        try:
            raw = typer.prompt(f"{prompt_suffix} ({prompt_detail})", default=prompt_default).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return current, True
        if not raw:
            if prompt_default:
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
        return raw, False


def _prompt_scalar_override(
    path_label: str,
    current: object,
    *,
    choices: list[OptionChoice] | None = None,
    type_hint: str | None = None,
    required: bool = False,
) -> tuple[object, bool]:
    if choices:
        return _prompt_choice_override(
            path_label=path_label,
            current=current,
            choices=choices,
            type_hint=type_hint,
            required=required,
        )
    rendered_label = _prompt_label_with_type(
        path_label,
        type_hint=type_hint,
        required=required,
    )
    prompt_suffix = f"{rendered_label} (enter {WIZARD_EXIT_TOKEN} to stop wizard)"
    if _is_complex_type_hint(type_hint) or isinstance(current, (dict, list)):
        default_value = _serialize_complex_prompt_default(current)
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
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
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
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
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
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
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
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
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
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
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
        if raw == WIZARD_EXIT_TOKEN:
            return current, True
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

    selected_components: list[tuple[ComponentEntry, str]] = []
    infra_lookup = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        instance_id = str(row["instance_id"])
        if instance_id not in selected_infra:
            continue
        entry = infra_lookup.get(str(row["id"]))
        if entry is not None:
            selected_components.append((entry, instance_id))
    app_lookup = {entry.id: entry for entry in app_entries}
    for row in _dynamic_enabled_app_chart_rows(payload):
        instance_id = str(row["instance_id"])
        if instance_id not in selected_apps:
            continue
        entry = app_lookup.get(str(row["id"]))
        if entry is not None:
            selected_components.append((entry, instance_id))

    warned_provider_fallbacks: set[str] = set()
    provider_allowed_cache: dict[str, tuple[set[str], tuple[str, ...]]] = {}
    for entry, instance_id in selected_components:
        component_label = component_instance_label(entry.id, instance_id)
        if not _wizard_continue_phase(
            f"Configure '{component_label}' component fields now?",
            default=True,
        ):
            return yaml.safe_dump(payload, sort_keys=False), False

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
            value = (
                _get_payload_value(payload, resolved_declared)
                if _payload_path_exists(payload, resolved_declared)
                else None
            )
            if isinstance(value, (dict, list)):
                continue
            if resolved_declared in bound_prompt_paths:
                continue
            declared_prompt_paths.append(resolved_declared)

        prompt_paths: list[PayloadPath] = []
        seen_prompt_labels: set[str] = set()
        field_type_hints: dict[str, str | None] = {}
        required_prompt_labels: set[str] = set()
        virtual_prompt_defaults: dict[PayloadPath, object] = {}
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
                        declared_prompt_paths: tuple[PayloadPath, ...] = current_declared_prompt_paths,
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
                            if label not in required_prompt_labels and not _wizard_field_prompt_enabled(
                                entry=current_entry,
                                full_path_label=label,
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

                    module_dependency_expander = (
                        _expand_module_dependency_prompts if dependent_prefixes else None
                    )
            elif entry.scope == "apps":
                # App wizard prompts are Helm values-driven.
                for key in ("namespace", "release-name"):
                    full_path = component_path + (key,)
                    if full_path in bound_prompt_paths:
                        continue
                    label = _format_payload_path(full_path)
                    if label in seen_prompt_labels:
                        continue
                    current_value = _get_payload_value(payload, full_path)
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
                if (
                    not field_choices
                    and providers
                    and provider_lookup is not None
                    and _provider_skip_prompt_if_no_choices_enabled(
                        entry=entry,
                        full_path_label=full_path_label,
                    )
                    and not provider_lookup.last_error()
                    and full_path_label not in required_prompt_labels
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
                        required=full_path_label in required_prompt_labels,
                        provider_lookup=provider_lookup,
                    )
                    console.print(warning)
                    warned_provider_fallbacks.add(full_path_label)
                updated, should_stop = _prompt_scalar_override(
                    full_path_label,
                    current,
                    choices=field_choices,
                    type_hint=field_type_hints.get(full_path_label),
                    required=full_path_label in required_prompt_labels,
                )
                if should_stop:
                    return yaml.safe_dump(payload, sort_keys=False), False
                while allowed_provider_values:
                    if (
                        full_path_label not in required_prompt_labels
                        and not _has_required_prompt_value(
                            updated,
                            type_hint=field_type_hints.get(full_path_label),
                        )
                    ):
                        break
                    updated_value = str(updated).strip()
                    if updated_value in allowed_provider_values:
                        break
                    console.print(
                        f"{error_markup('Invalid value')} for "
                        f"'{full_path_label}'. Value must exist in live provider options."
                    )
                    updated, should_stop = _prompt_scalar_override(
                        full_path_label,
                        updated,
                        choices=field_choices,
                        type_hint=field_type_hints.get(full_path_label),
                        required=full_path_label in required_prompt_labels,
                    )
                    if should_stop:
                        return yaml.safe_dump(payload, sort_keys=False), False
                if full_path in virtual_prompt_defaults:
                    default_value = virtual_prompt_defaults[full_path]
                    if updated == default_value:
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
                _maybe_clear_mk8s_infiniband_fabric_after_gpu_shape_change(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    provider_lookup=provider_lookup,
                )
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

    return yaml.safe_dump(payload, sort_keys=False), True


@dataclass(frozen=True)
class _AppChartDependencyAdjustment:
    source_app_id: str
    dependency_app_id: str
    dependency_chart_name: str


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
    name = str(chart_node.get("id", "")).strip() or entry.id
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
    name = str(chart_node.get("id", "")).strip().lower() or entry.id
    return name or None


def _source_chart_name(entry: ComponentEntry) -> str | None:
    source = str(entry.source or "").strip().rstrip("/")
    if not source:
        return None
    token = source.rsplit("/", maxsplit=1)[-1].strip().lower()
    return token or None


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


def _component_dependency_issues_from_payload(
    payload: dict[str, Any],
    *,
    chart_meta_cache: _ChartMetaCache | None = None,
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

    if selected_apps:
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
            issues.append(
                "app chart dependency requires "
                f"'apps:{adjustment.dependency_app_id}' when "
                f"'apps:{adjustment.source_app_id}' is enabled "
                f"(chart dependency: {adjustment.dependency_chart_name})"
            )
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

    for chart_row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(chart_row["id"])
        instance_id = str(chart_row["instance_id"])
        chart_repo = str(chart_row.get("repo", "")).strip()
        chart_version = str(chart_row.get("version", "")).strip()
        if chart_meta_cache is None:
            issues_for_chart = _helm_chart_validation_issues(
                chart_name=chart_id,
                chart_repo=chart_repo,
                chart_version=chart_version,
            )
        else:
            issues_for_chart = _resolve_helm_chart_validation_issues(
                chart_name=chart_id,
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
    github_tree_repo = _is_github_tree_chart_repo(repo)
    source_display = (
        repo
        if github_tree_repo
        else _chart_source_display(chart_name_or_ref=chart_id, chart_repo=repo)
    )

    if not chart_id:
        return ("name is required",)
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
        chart_source_issues = _resolve_helm_chart_validation_issues(
            chart_name=chart_name,
            chart_repo=repo,
            chart_version=version,
            chart_meta_cache=chart_meta_cache,
        )
        for issue in chart_source_issues:
            issues.append(f"{chart_label} {issue}")
        if not chart_source_issues:
            chart_contract_issues, chart_contract_warnings = chart_cli_contract_findings(
                chart_name=chart_name,
                chart_repo=repo,
                chart_version=version,
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
                        segment.strip() for segment in access_source_label.split(".") if segment.strip()
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
        required.add("gpu_node_groups")
        if gpu_autoscaling is None:
            required.add("gpu_nodes_count_per_group")
        if not gpu_override_platform:
            required.add("gpu_nodes_platform")
        if not gpu_override_preset:
            required.add("gpu_nodes_preset")

    return required


def _conditionally_required_input_leaf_names(
    *,
    entry: ComponentEntry | None,
    component_node: Mapping[str, Any],
) -> set[str]:
    if entry is None or entry.scope != "infra":
        return set()
    if getattr(entry, "validation_profile", "") == "mk8s_cluster":
        return _mk8s_conditionally_required_input_leaf_names(component_node)
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
            if not _provider_auto_select_single_enabled(
                entry=entry,
                full_path_label=full_path_label,
            ):
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
            if len(choices) != 1:
                continue
            target_path = _parse_payload_path_label(full_path_label)
            if target_path is None:
                continue
            _set_payload_value_creating_containers(payload, target_path, choices[0].value)


def _required_enabled_infra_field_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
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
        if entry is not None:
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
    return list(
        dict.fromkeys(
            _required_enabled_infra_field_issues(
                payload=payload,
                infra_entries=infra_entries,
            )
        )
    )


def _print_wizard_required_field_warning(issues: Sequence[str]) -> None:
    if not issues:
        return
    console.print(
        f"{warning_markup('Wizard stopped before all required fields were filled.')} "
        "Validate/render will fail until you set:"
    )
    for issue in issues:
        console.print(f"  - {escape(issue)}")


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
        rows.append(
            {
                "id": chart_id,
                "instance_id": instance_id,
                "group": str(item.get("group", "")).strip().lower() or "workloads",
                "repo": str(item.get("repo", "")).strip(),
                "version": str(item.get("version", "")).strip(),
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
    active_rows: dict[str, dict[str, Any]] = {}
    for row in _dynamic_enabled_infra_component_rows(payload):
        active_rows[str(row["instance_id"])] = row
    for row in _dynamic_enabled_app_chart_rows(payload):
        active_rows[str(row["instance_id"])] = row

    for instance_id, row in active_rows.items():
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
            if not source_instance_id or source_instance_id not in active_rows:
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
    metadata_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_file.chmod(0o600)


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
        s3_access_key_id=_non_empty_text(payload.get("s3_access_key_id")),
        s3_secret_access_key=_non_empty_text(payload.get("s3_secret_access_key")),
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
    need_eso_mysterybox: bool,
) -> list[str]:
    required: list[str] = []
    credentials_file = os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE", "").strip()
    has_credentials_file = bool(credentials_file)
    if (need_terraform and not has_credentials_file) or need_eso_mysterybox:
        required.extend(["NEBIUS_SA_ID", "NEBIUS_AUTH_PUBLIC_KEY_ID"])
    if need_eso_mysterybox:
        required.append("NEBIUS_AUTH_PRIVATE_KEY_PEM")

    missing = [name for name in required if not os.environ.get(name)]
    has_private_key_file = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
    has_private_key_pem = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_PEM"))
    if (
        ((need_terraform and not has_credentials_file) or need_eso_mysterybox)
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
    need_eso_mysterybox: bool,
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
        need_eso_mysterybox=need_eso_mysterybox,
    )
    project_id = str(config.client_info.nebius.project_id).strip()
    client_name = str(config.client_info.client_name).strip()
    if missing:
        _runtime_auth_cache_load(project_id=project_id, client_name=client_name)
        missing = _runtime_auth_missing_envs(
            need_terraform=need_terraform,
            need_eso_mysterybox=need_eso_mysterybox,
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

        # Handle stale runtime-auth caches created before S3 key fields existed.
        still_missing = _runtime_auth_missing_envs(
            need_terraform=need_terraform,
            need_eso_mysterybox=need_eso_mysterybox,
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
            still_missing = _runtime_auth_missing_envs(
                need_terraform=need_terraform,
                need_eso_mysterybox=need_eso_mysterybox,
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

    if (
        need_terraform and not os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE")
    ) or need_eso_mysterybox:
        _ensure_private_key_file_env()


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
        need_eso_mysterybox=False,
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
        handoffs=_enabled_cluster_handoffs(config),
        required_component_outputs=_required_runtime_component_output_specs(config),
        status_watchers=_enabled_status_watcher_specs(config),
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


def _manifest_cluster_handoffs(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    deploy_node = manifest.get("deploy")
    if not isinstance(deploy_node, Mapping):
        return []
    raw_handoffs = deploy_node.get("handoffs")
    if not isinstance(raw_handoffs, list):
        return []
    handoffs: list[dict[str, str]] = []
    for item in raw_handoffs:
        if not isinstance(item, Mapping):
            continue
        handoffs.append(
            {
                "component_id": str(item.get("component_id", "")).strip().lower(),
                "instance_id": (
                    str(item.get("instance_id", "")).strip().lower()
                    or str(item.get("component_id", "")).strip().lower()
                ),
                "cluster_id_output_name": str(item.get("cluster_id_output_name", "")).strip(),
                "component_output_ref": str(item.get("component_output_ref", "")).strip(),
                "access": str(item.get("access", "")).strip().lower(),
            }
        )
    return [item for item in handoffs if item["cluster_id_output_name"]]


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


def _manifest_requires_flux_terraform_state(manifest: Mapping[str, Any]) -> bool:
    return bool(
        _manifest_cluster_handoffs(manifest) or _manifest_required_component_output_specs(manifest)
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
    handoffs: list[dict[str, str]] | None = None,
    persist_local_kubeconfig: bool = True,
) -> dict[str, str] | None:
    handoffs = handoffs if handoffs is not None else _enabled_cluster_handoffs(config)
    if not handoffs:
        return None
    if len(handoffs) > 1:
        component_ids = ", ".join(
            sorted(
                component_instance_label(
                    handoff["component_id"],
                    handoff.get("instance_id", handoff["component_id"]),
                )
                for handoff in handoffs
            )
        )
        raise RuntimeError(
            "Multiple handoff-capable infra components are enabled for this run: "
            f"{component_ids}. Enable only one cluster handoff source before running this command."
        )
    handoff = handoffs[0]

    cluster_id = terraform_output_raw(
        paths.infra_dir,
        handoff["cluster_id_output_name"],
        extra_env=_terraform_runtime_env(config),
    )
    if not cluster_id:
        raise RuntimeError(
            f"Terraform output `{handoff['cluster_id_output_name']}` is empty. The rendered Terraform root must expose "
            "the cluster ID required for local cluster handoff kubeconfig generation."
        )

    if not _runtime_auth_env_available():
        project_id = str(config.client_info.nebius.project_id).strip()
        client_name = str(config.client_info.client_name).strip()
        _runtime_auth_cache_load(project_id=project_id, client_name=client_name)
    spec = _mk8s_cluster_handoff_spec(
        config,
        cluster_id=cluster_id,
        access=handoff["access"],
    )
    if handoff["access"] == "internal":
        console.print(f"[yellow]NOTE:[/yellow] {_private_cluster_handoff_note()}")
    kube_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="nebius-cxcli-kube-")))
    kubeconfig_path = kube_root / "config"
    _write_kubeconfig_file(kubeconfig_path, spec)
    if persist_local_kubeconfig:
        _persist_cluster_handoff_kubeconfig(spec=spec)
    return {
        "KUBECONFIG": str(kubeconfig_path),
        CLUSTER_HANDOFF_ACCESS_ENV: str(handoff["access"]),
    }


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


def _deploy_generated_artifacts(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    auto_auth_bootstrap: bool,
) -> None:
    """Deploy an existing generated artifact bundle without rerendering it."""
    _raise_on_live_quota_issues(config, phase="deploy")
    _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
    runtime_env = _terraform_runtime_env(config)
    terraform_init(paths.infra_dir, extra_env=runtime_env)
    terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
    status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(config)
    apply_kwargs: dict[str, Any] = {"initialize": False}
    if status_watchers:
        apply_kwargs["status_watchers"] = status_watchers
    _run_terraform_apply_with_status(config, paths, **apply_kwargs)
    write_inventory(config, paths)
    has_enabled_app_charts = _active_chart_count(config) > 0
    with ExitStack() as stack:
        kube_env = _prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            handoffs=_manifest_cluster_handoffs(manifest),
        )
        if has_enabled_app_charts:
            _wait_for_cluster_nodes_ready(
                extra_env=kube_env, emit=lambda message: console.print(message)
            )
            _apply_rendered_flux(paths, extra_env=kube_env)
            _warn_if_flux_gitops_not_bootstrapped(config, paths, extra_env=kube_env)


def _destroy_rendered_flux_bundle(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
) -> None:
    if _active_chart_count(config) == 0:
        raise RuntimeError("No enabled apps charts are configured for this project.")
    with ExitStack() as stack:
        kube_env = _prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            handoffs=_manifest_cluster_handoffs(manifest),
            persist_local_kubeconfig=False,
        )
        delete_rendered_flux(paths, extra_env=kube_env)


def _destroy_generated_artifacts(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    auto_auth_bootstrap: bool,
    yes: bool = False,
) -> None:
    _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
    if _active_chart_count(config) > 0:
        try:
            _destroy_rendered_flux_bundle(config, paths, manifest)
        except Exception as exc:
            console.print(
                f"{warning_markup('WARNING:', bold=True)} "
                "Rendered app teardown failed before infra destroy. "
                "Continuing with Terraform destroy because the generated infra bundle "
                f"remains the authoritative teardown path. Reason: {exc}"
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
) -> None:
    runtime_env = _terraform_runtime_env(config)
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
) -> None:
    if _active_chart_count(config) == 0:
        return
    if not extra_env or not extra_env.get("KUBECONFIG"):
        return
    if flux_bootstrap_resources_installed(extra_env=extra_env):
        return
    command = f"nebius-cxcli flux bootstrap {shlex.quote(str(paths.generated_dir))}"
    console.print(
        f"{warning_markup('WARNING:', bold=True)} Flux GitOps bootstrap is not configured for this cluster yet. "
        "Local apply succeeded, but the cluster will not continuously sync from the Git repository "
        "until you bootstrap it."
    )
    console.print("Run to enable GitOps sync:")
    console.print(command, style="cyan", no_wrap=True, overflow="ignore")


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
    "# Generated by `nebius-cxcli create`.",
    "# Keep the canonical project config versioned in a private repo; ignore generated Terraform runtime files.",
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


def _ensure_deployments_gitignore(
    *,
    deployments_root: Path,
) -> DeploymentsGitignoreResult:
    if _try_git_root(deployments_root) is None:
        return DeploymentsGitignoreResult(path=None, wrote=False)

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
    tenant_id: str,
    project_id: str,
) -> Path:
    return deployments_root / tenant_id / project_id / "config.yaml"


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
            matched_rows = [row]
        else:
            for row in matched_rows:
                if not isinstance(row.get("inputs"), Mapping):
                    row["inputs"] = {}
                row["enabled"] = True
        selected_infra_components.extend(matched_rows)
    infra["components"] = selected_infra_components

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
            row = {
                "id": entry.id,
                INSTANCE_ID_FIELD: entry.id,
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
                if not isinstance(row.get("values"), Mapping):
                    row["values"] = {}
                row["enabled"] = True
        selected_app_charts.extend(matched_rows)
    apps["charts"] = selected_app_charts

    return runtime_payload


def _selection_change_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    issues.extend(_component_dependency_issues_from_payload(payload))
    issues.extend(_active_component_input_binding_issues(payload))
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
    shared_public_key = _non_empty_text(read_path_with_catalog(payload, "shared.admin_ssh.public_key"))
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
    instance_dir = deployments_root / tenant_id / project_id
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

    inventory_path = instance_dir / "generated" / "inventory" / "inventory.md"
    if not inventory_path.exists():
        inventory_path.write_text(_GENERATED_INVENTORY_SCAFFOLD_TEXT, encoding="utf-8")

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
    short_help="Use DEPLOYMENTS_ROOT to bootstrap one tenant/project folder with config.yaml plus generated/ skeleton.",
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
            help="Optional notifications email for inventory updates",
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
            help=("Validate component_sources.yaml before create runs (enabled by default)."),
        ),
    ] = True,
    validate_config: Annotated[
        bool,
        typer.Option(
            "--validate-config/--no-validate-config",
            help=(
                "Run `validate` against the resulting config.yaml after create finishes "
                "(enabled by default)."
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
                "Overwrite the resolved existing tenant/project folder from scratch using the "
                "current create inputs and component selections. Existing component values, "
                "generated artifacts, and other files under that tenant/project folder are not "
                "preserved. Does not delete the deployments root or other projects."
            ),
        ),
    ] = False,
) -> None:
    """Use DEPLOYMENTS_ROOT to bootstrap one tenant/project folder with config.yaml plus generated/ skeleton, or overwrite an existing resolved tenant/project folder from scratch after confirmation."""
    try:
        base_path = target_path.resolve()
        _validate_deployments_root_target(base_path)
        deployments_root = _resolve_deployments_root(base_path)
        interactive_mode = not no_interactive
        single_existing_defaults = (
            _single_existing_project_create_defaults(deployments_root)
            if interactive_mode
            and not any(
                value is not None and str(value).strip()
                for value in (tenant_id, project_id)
            )
            else None
        )
        resolved_tenant_id = _value_or_prompt(
            tenant_id,
            option_name="--tenant-id",
            prompt_text="Tenant ID",
            interactive=interactive_mode,
            default_value=single_existing_defaults.tenant_id if single_existing_defaults else None,
        )
        resolved_project_id = _value_or_prompt(
            project_id,
            option_name="--project-id",
            prompt_text="Project ID",
            interactive=interactive_mode,
            default_value=single_existing_defaults.project_id if single_existing_defaults else None,
        )
        provider_lookup = ProviderOptionLookup()
        resolved_tenant_id, resolved_project_id = _validate_tenant_project_ids_or_prompt(
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            interactive=interactive_mode,
            provider_lookup=provider_lookup,
        )

        existing_config_path = _project_config_path(
            deployments_root=deployments_root,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
        )
        existing_payload: dict[str, Any] | None = None
        had_existing_config = existing_config_path.exists()
        if had_existing_config:
            with existing_config_path.open("r", encoding="utf-8") as handle:
                loaded_payload = yaml.safe_load(handle) or {}
            if not isinstance(loaded_payload, dict):
                raise RuntimeError("Existing config.yaml payload must be a mapping")
            existing_payload = loaded_payload
            if interactive_mode:
                if not _confirm_existing_project_overwrite(config_path=existing_config_path):
                    console.print("No changes applied.")
                    return
            elif not force:
                raise RuntimeError(
                    "Existing project found: "
                    f"{existing_config_path.parent}. `create` no longer reconciles existing configs. "
                    "Use `component list/add/remove` for day-2 component edits, or rerun with "
                    "`--force` to overwrite this one tenant/project folder from scratch."
                )
            else:
                _warn_existing_project_overwrite(config_path=existing_config_path)
                console.print(
                    "[dim]`--force` confirms the overwrite in non-interactive mode. "
                    "This only affects that one resolved tenant/project folder.[/dim]"
                )
        if validate_sources:
            _validate_component_sources_or_raise()

        existing_identity_payload = existing_payload if isinstance(existing_payload, dict) else {}
        existing_client_name = _non_empty_text(
            _read_payload_field(existing_identity_payload, "client_info.client_name")
        )
        existing_region_id = _non_empty_text(
            _read_payload_field(existing_identity_payload, "client_info.nebius.region_id")
        )
        existing_email_value = _read_payload_field(
            existing_identity_payload,
            "client_info.notifications.email",
        )
        existing_email = (
            str(existing_email_value).strip() if isinstance(existing_email_value, str) else None
        )
        resolved_client_name = _value_or_prompt(
            client_name,
            option_name="--client-name",
            prompt_text="Client name",
            interactive=interactive_mode,
            default_value=existing_client_name,
        )
        resolved_region_id = _region_or_prompt(
            region_id or existing_region_id or None,
            interactive=interactive_mode,
        )
        resolved_email = _optional_email_or_prompt(
            email if email is not None else existing_email,
            interactive=interactive_mode,
        )

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")

        optional_wizard_mode = interactive_mode
        if interactive_mode:
            optional_wizard_mode = _wizard_continue_phase(
                "Continue with optional wizard phases (component selection and fields)?",
                default=True,
            )
        selected_infra_raw = _resolve_component_ids(
            scope="infra",
            raw_values=infra_components_opt,
            interactive=optional_wizard_mode,
            entries=infra_entries,
        )
        selected_apps_raw = _resolve_component_ids(
            scope="apps",
            raw_values=apps_components_opt,
            interactive=optional_wizard_mode,
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

        _materialize_singleton_provider_defaults(
            payload=final_payload,
            selected_infra=selected_infra,
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )

        create_required_field_issues = (
            _wizard_followup_required_field_issues(
                payload=final_payload,
                infra_entries=infra_entries,
            )
            if interactive_mode and (not optional_wizard_mode or not wizard_completed)
            else []
        )
        _prune_redundant_app_chart_default_values(
            payload=final_payload,
            app_entries=app_entries,
        )

        result = _scaffold_instance(
            base_path=base_path,
            client_name=resolved_client_name,
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
                console.print(f"Project already matched the overwrite target: {result.project_path}")
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
                "Render can continue, but deploy will fail until the quota is increased."
            )
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
        if interactive_mode and (not optional_wizard_mode or not wizard_completed):
            _print_wizard_required_field_warning(create_required_field_issues)
        console.print(
            "Next steps: optionally run `nebius-cxcli validate --strict <config.yaml>`, "
            "`nebius-cxcli render <config.yaml>`, "
            "`nebius-cxcli bootstrap-ci <config.yaml>` (optional), then deploy from "
            "`<project>/generated` with `nebius-cxcli deploy <generated-dir>`."
        )
        console.print(
            f"{warning_markup('Security warning:')} keep this customer repository private "
            "because the deployments root contains sensitive operational metadata."
        )
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@component_app.command(
    "list",
    short_help="Use CONFIG_YAML to inspect enabled instances and available catalog components.",
)
def component_list_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
) -> None:
    """List enabled component instances and reusable catalog component types."""
    try:
        _config, _paths = _load_context(config_path)
        payload = _load_config_payload(config_path.resolve())
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
                    console.print(
                        f"  {_component_instance_selector_label(entry, instance_id=str(row['instance_id']))}"
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
    short_help="Use CONFIG_YAML for day-2 additive infra/app component changes.",
)
def component_add_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    component_ids: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[COMPONENT_ID]...",
            help=(
                "Optional infra module or app chart id(s) to add. When omitted, "
                "component add prompts interactively using separate infra/apps selections. "
                "Repeat a component id to add another instance, or use "
                "'<component-id>@<instance-id>' to request an explicit instance id."
            ),
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
                "Validate component_sources.yaml before component add runs (enabled by default)."
            ),
        ),
    ] = True,
) -> None:
    """Use CONFIG_YAML for day-2 additive infra/app component changes in an existing project config.yaml."""
    try:
        _config, _paths = _load_context(config_path)
        if validate_sources:
            _validate_component_sources_or_raise()
        payload = _load_config_payload(config_path.resolve())
        interactive_mode = not no_interactive
        client_name, tenant_id, project_id, region_id, email = _identity_values_from_payload(
            payload
        )
        provider_lookup = ProviderOptionLookup()
        tenant_id, project_id = _validate_tenant_project_ids_or_prompt(
            tenant_id=tenant_id,
            project_id=project_id,
            interactive=False,
            provider_lookup=provider_lookup,
        )

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
        enabled_infra = _enabled_ids_from_runtime_payload(payload=payload, entries=infra_entries)
        enabled_apps = _enabled_ids_from_runtime_payload(payload=payload, entries=app_entries)

        raw_tokens = _split_multi_value_tokens(component_ids)
        if not raw_tokens and interactive_mode:
            requested_infra = _prompt_component_scope_selection(
                action="add",
                scope="infra",
                entries=infra_entries,
            )
            requested_apps = _prompt_component_scope_selection(
                action="add",
                scope="apps",
                entries=app_entries,
            )
            add_targets = [
                *(
                    _ComponentAddTarget(scope="infra", component_id=component_id)
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
        added_infra_labels: list[str] = []
        added_apps_labels: list[str] = []
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
            row = _append_component_instance_row(
                payload=next_payload,
                entry=entry,
                requested_instance_id=target.requested_instance_id,
            )
            instance_id = component_instance_id(row)
            if target.scope == "infra":
                added_infra_instances.append(instance_id)
                added_infra_labels.append(component_instance_label(entry.id, instance_id))
            else:
                added_apps_instances.append(instance_id)
                added_apps_labels.append(component_instance_label(entry.id, instance_id))
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
        config_yaml_override = yaml.safe_dump(next_payload, sort_keys=False)
        wizard_completed = True
        if interactive_mode:
            config_yaml_override, wizard_completed = _run_component_field_wizard(
                config_yaml=config_yaml_override,
                selected_infra=set(added_infra_instances),
                selected_apps=set(added_apps_instances),
                infra_entries=infra_entries,
                app_entries=app_entries,
                provider_lookup=provider_lookup,
            )
            parsed_override = yaml.safe_load(config_yaml_override) or {}
            if not isinstance(parsed_override, dict):
                raise RuntimeError("Updated config payload must be a mapping")
            next_payload = parsed_override

        _materialize_singleton_provider_defaults(
            payload=next_payload,
            selected_infra=set(added_infra_instances),
            infra_entries=infra_entries,
            provider_lookup=provider_lookup,
        )

        add_required_field_issues = (
            _wizard_followup_required_field_issues(
                payload=next_payload,
                infra_entries=infra_entries,
            )
            if interactive_mode and not wizard_completed
            else []
        )

        selection_issues = _selection_change_issues(next_payload)
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
        if _active_chart_count(next_payload) > 0 and _config_uses_private_cluster_handoff(
            next_payload
        ):
            console.print(f"[yellow]NOTE:[/yellow] {_private_cluster_handoff_note()}")
        if interactive_mode and not wizard_completed:
            _print_wizard_required_field_warning(add_required_field_issues)
        console.print(
            "Next steps: run `nebius-cxcli validate <config.yaml>` and "
            "`nebius-cxcli render <config.yaml>`."
        )
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@component_app.command(
    "remove",
    short_help="Use CONFIG_YAML for day-2 infra/app component removal from config.",
)
def component_remove_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    component_ids: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[COMPONENT_ID]...",
            help=(
                "Optional infra module or app chart id(s) to remove. When omitted, "
                "component remove prompts interactively using separate infra/apps selections. "
                "If multiple instances of the same component type are enabled, pass the "
                "instance id or '<component-id>@<instance-id>' to remove one exact instance."
            ),
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
    """Use CONFIG_YAML for day-2 infra/app component removal from an existing project config.yaml."""
    try:
        _config, _paths = _load_context(config_path)
        payload = _load_config_payload(config_path.resolve())
        interactive_mode = not no_interactive

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
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
        for target in remove_targets:
            removed = _remove_component_instance_row(
                payload=next_payload,
                scope=target.scope,
                instance_id=target.instance_id,
            )
            if removed is None:
                continue
            label = component_instance_label(target.component_id, target.instance_id)
            if target.scope == "infra":
                removed_infra_labels.append(label)
            else:
                removed_app_labels.append(label)
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
        console.print(
            "Next steps: run `nebius-cxcli validate <config.yaml>` and "
            "`nebius-cxcli render <config.yaml>`."
        )
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
                "(<tenant>/<project>/config.yaml). The file must already exist inside that repo checkout."
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
    short_help="Use CONFIG_YAML to validate runtime config, sources, and provider/chart wiring.",
)
def validate_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help=_CONFIG_YAML_ARGUMENT_HELP,
        ),
    ],
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Enable deployment-readiness checks (reject starter placeholders)",
        ),
    ] = False,
) -> None:
    """Validate one project config.yaml with runtime source and provider/chart checks."""
    try:
        _run_runtime_validation(
            config_path=config_path,
            strict=strict,
            title="Runtime validation",
        )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "quota-check",
    short_help="Use CONFIG_YAML to run a live Nebius quota assessment for enabled infra components.",
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
    """Run a live Nebius quota assessment for the enabled infra components in one project config.

    The selected config region determines pass/fail. Use --all-regions to also print quota-only
    availability for the same shape across all discovered tenant/project regions.
    """
    try:
        config, paths = _load_context(config_path)
        report = _warn_on_live_quota_issues(
            config,
            phase="quota check",
            all_regions=all_regions,
        )
        if report.has_confirmed_insufficiency:
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
    "validate-generated",
    short_help="Use GENERATED_PATH to validate an existing rendered bundle without rerendering.",
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
            help="Automatically bootstrap runtime auth when env vars are missing",
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
    """Validate an existing generated/ bundle or subpath without rerendering it."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        phase_defs = [
            _ValidationPhase("backend", "Prepare Terraform backend auth"),
            _ValidationPhase("terraform", "Validate generated Terraform bundle"),
        ]
        if _active_chart_count(config) > 0:
            phase_defs.append(_ValidationPhase("flux", "Validate rendered Flux manifests"))
        if portable:
            phase_defs.append(_ValidationPhase("portable", "Validate generated bundle portability"))

        with _ValidationProgress(
            title="Generated artifact validation", phases=phase_defs
        ) as progress:
            if not paths.infra_dir.exists():
                raise RuntimeError(f"Rendered infra directory does not exist: {paths.infra_dir}")
            progress.run(
                "backend",
                lambda: _ensure_terraform_backend_ready(
                    config,
                    auto_auth_bootstrap=auto_auth_bootstrap,
                ),
            )
            progress.run(
                "terraform",
                lambda: terraform_validate(
                    paths.infra_dir,
                    extra_env=_terraform_runtime_env(config),
                ),
            )
            if _active_chart_count(config) > 0:

                def _validate_flux_manifests() -> None:
                    if not shutil.which("kubectl"):
                        raise RuntimeError(
                            "kubectl is required for `validate-generated` but was not found in PATH"
                        )
                    subprocess.run(
                        ["kubectl", "kustomize", str(paths.flux_dir)],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

                progress.run("flux", _validate_flux_manifests)
            if portable:
                progress.run(
                    "portable",
                    lambda: _validate_generated_bundle_portability(paths, _manifest),
                )
        console.print(f"[green]Valid generated artifacts:[/green] {paths.generated_dir}")
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI surface
        detail = _first_non_empty_line(exc.stderr or exc.stdout or "")
        _exit_with_error(RuntimeError(detail or str(exc)))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "validate-sources",
    short_help="Validate component_sources.yaml plus resolved Terraform module and Helm chart source contracts.",
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
    """Validate component_sources.yaml plus resolved Terraform module and Helm chart source contracts."""
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
            overall_task_id = progress.add_task("Validating component sources", total=total_items)
            item_task_ids: dict[str, list[int]] = {}
            for key, title in progress_items:
                task_id = progress.add_task(f"[dim]{title}[/dim]", total=1)
                item_task_ids.setdefault(key, []).append(task_id)
            if not progress_items:
                progress.update(
                    overall_task_id,
                    description="No component sources entries found",
                    completed=1,
                    total=1,
                )

            def _progress_update(label: str, completed: int, total: int) -> None:
                normalized_total = max(total, 1)
                normalized_completed = min(max(0, completed), normalized_total)
                if label == "init":
                    progress.update(
                        overall_task_id,
                        description="Validating component sources",
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
                    description=f"Validating component sources ({normalized_completed}/{normalized_total})",
                    completed=normalized_completed,
                    total=normalized_total,
                )

            source_path, issues, warnings = _validate_component_sources_registry(
                explicit=component_sources_path, progress_callback=_progress_update
            )
        for warning in warnings:
            console.print(f"{warning_markup('Warning:')} {warning}")
        if issues:
            raise RuntimeError(
                f"Component sources validation failed for {source_path}:\n  - "
                + "\n  - ".join(issues)
            )
        console.print(f"[green]Component sources valid:[/green] {source_path}")
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
                "(or provide --project-config to resolve it)."
            ),
        ),
    ] = None,
    project_config: Annotated[
        Path | None,
        typer.Option(
            "--project-config",
            help=(
                "Optional project config.yaml path (<tenant>/<project>/config.yaml) used to resolve "
                "project_id and client_name"
            ),
        ),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option(
            "--client-name",
            help=(
                "Client name used for runtime auth cache path and --bootstrap-ci environment naming "
                "(`<client_name>-<project_id>`). Required for --create/--recreate unless "
                "--project-config is provided, or when project_id maps to multiple cached profiles."
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
                if recreate:
                    console.print(
                        f"Recreated runtime auth profile for project '{resolved_project_id}'."
                    )
                else:
                    if created:
                        console.print(
                            f"Created runtime auth profile for project '{resolved_project_id}'."
                        )
                    else:
                        console.print(
                            f"{warning_markup('Runtime auth profile already exists')} for project "
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
        resolved_source_profile = resolve_component_sources_profile()
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
            write_inventory(config, staged_paths)
            quota_report = _warn_on_live_quota_issues(config, phase="render")
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
                "quota is increased."
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
    short_help="Use GENERATED_PATH to deploy locally from an existing rendered bundle.",
)
def deploy_command(
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
            help=("Automatically bootstrap runtime auth material when required values are missing"),
        ),
    ] = True,
) -> None:
    """Deploy an existing generated artifact bundle locally from generated/ or a subpath.

    This command is a reconcile/apply path: Terraform apply runs first,
    refresh inventory runs next, and when app charts are enabled Flux then
    converges the existing generated bundle onto live infrastructure and
    workloads. When a built-in cluster handoff such as MK8s is enabled,
    deploy also refreshes local kubeconfig access for that cluster even if no
    app charts are configured. Existing managed resources may be updated when
    the bundle differs from live state. Use
    `nebius-cxcli terraform plan <generated>` first when you need a
    non-mutating preview. It does not run `flux bootstrap` or configure GitOps
    sync, and it does not create or update GitHub workflows, environments, or
    CI secrets; use
    `nebius-cxcli bootstrap-ci <config.yaml>` explicitly for that.
    """
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        _deploy_generated_artifacts(
            config,
            paths,
            manifest,
            auto_auth_bootstrap=auto_auth_bootstrap,
        )
        console.print(f"Local deploy completed from {paths.generated_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command(
    "destroy",
    short_help="Use GENERATED_PATH to destroy apps first and then infra from an existing rendered bundle.",
)
def destroy_command(
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
    """Destroy an existing generated artifact bundle locally from generated/ or a subpath.

    This command is the destructive inverse of `deploy`: it first deletes the
    rendered Flux manifests from the target cluster when apps are enabled, and
    then runs Terraform destroy against the rendered infra bundle. It does not
    rerender from `config.yaml`, and it does not uninstall Flux controllers or
    bootstrap GitHub/CI state.
    """
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        if not _confirm_generated_destroy(
            paths,
            yes=yes,
            action_label="Destroy",
            prompt_text="Continue and destroy the rendered apps and infra?",
            warning_text=(
                "Destroy will delete the rendered app resources from the target cluster first and then "
                "run Terraform destroy against the rendered infra bundle under"
            ),
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
        config, paths, _manifest = _load_generated_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        runtime_env = _terraform_runtime_env(config)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
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
    """Refresh inventory, then run Terraform apply against an existing generated/infra bundle."""
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths)
        runtime_env = _terraform_runtime_env(config)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
        status_watchers = _manifest_status_watchers(manifest) or _enabled_status_watcher_specs(
            config
        )
        apply_kwargs: dict[str, Any] = {"initialize": False}
        if status_watchers:
            apply_kwargs["status_watchers"] = status_watchers
        _run_terraform_apply_with_status(config, paths, **apply_kwargs)
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
        config, paths, manifest = _load_generated_context(generated_path)
        if not _confirm_generated_destroy(
            paths,
            yes=yes,
            action_label="Terraform destroy",
            prompt_text="Continue and destroy the rendered infra resources?",
            warning_text="Terraform destroy will destroy the rendered infra resources under",
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
        config, paths, _manifest = _load_generated_context(generated_path)
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
) -> None:
    """Delete rendered Flux resources directly from an existing generated/flux bundle."""
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        if _active_chart_count(config) == 0:
            raise RuntimeError("No enabled apps charts are configured for this project.")
        if not _confirm_generated_destroy(
            paths,
            yes=yes,
            action_label="Flux destroy",
            prompt_text="Continue and delete the rendered app resources from the target cluster?",
            warning_text="Flux destroy will delete the rendered app resources declared under",
        ):
            console.print("No changes applied.")
            return
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        _destroy_rendered_flux_bundle(config, paths, manifest)
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
) -> None:
    """Refresh inventory, then bootstrap or reconcile Flux from an existing generated/flux bundle."""
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        requires_cluster_handoff = bool(
            _active_chart_count(config) and _manifest_cluster_handoffs(manifest)
        )
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        else:
            _ensure_runtime_auth_material(
                config,
                need_terraform=False,
                need_eso_mysterybox=False,
                auto_bootstrap=auto_auth_bootstrap,
            )
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths)
        with ExitStack() as stack:
            kube_env = (
                _prepare_cluster_handoff_kube_env(
                    config,
                    paths,
                    stack=stack,
                    handoffs=_manifest_cluster_handoffs(manifest),
                )
                if requires_cluster_handoff
                else None
            )
            _wait_for_cluster_nodes_ready(
                extra_env=kube_env, emit=lambda message: console.print(message)
            )
            action = ensure_flux(paths, extra_env=kube_env)
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
) -> None:
    """Refresh inventory and apply an existing generated/flux bundle directly."""
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        if _active_chart_count(config) == 0:
            raise RuntimeError("No enabled apps charts are configured for this project.")
        if _manifest_requires_flux_terraform_state(manifest):
            _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths)
        with ExitStack() as stack:
            kube_env = _prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                handoffs=_manifest_cluster_handoffs(manifest),
            )
            _wait_for_cluster_nodes_ready(
                extra_env=kube_env, emit=lambda message: console.print(message)
            )
            _apply_rendered_flux(paths, extra_env=kube_env)
            _warn_if_flux_gitops_not_bootstrapped(config, paths, extra_env=kube_env)
        console.print(f"Flux applied from {paths.flux_dir}")
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
                "change detection for changed <tenant>/<project>/config.yaml and generated/** paths under that scope; "
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


@inventory_app.command(
    "write",
    short_help="Use GENERATED_PATH to refresh generated/inventory artifacts.",
)
def inventory_write_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=_GENERATED_INVENTORY_ARGUMENT_HELP,
        ),
    ],
) -> None:
    """Refresh local non-sensitive inventory artifacts."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        artifacts = write_inventory(config, paths)
        console.print(f"Inventory written: {artifacts.markdown}")
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
    short_help="Use GENERATED_PATH to send inventory email, or omit it with --setup.",
)
def email_command(
    generated_path: Annotated[
        Path | None,
        typer.Argument(
            metavar="GENERATED_PATH",
            help=(f"{_GENERATED_INVENTORY_ARGUMENT_HELP} Omit the path only when using --setup."),
        ),
    ] = None,
    setup: Annotated[
        bool,
        typer.Option(
            "--setup",
            help="Interactively create, update, or remove local email settings under ~/.config/nebius-cxcli/",
        ),
    ] = False,
) -> None:
    """Send inventory email from a generated/ bundle, or manage local SMTP settings in ~/.config/nebius-cxcli/email.yaml with --setup."""
    try:
        if setup:
            settings, written_path = _interactive_email_settings_setup(config_path=None)
            if settings.enabled:
                console.print(f"Configured local email settings: {written_path}")
            else:
                console.print(f"Removed local email settings: {written_path}")
            if generated_path is None:
                return
        if generated_path is None:
            raise RuntimeError("generated_path is required unless --setup is used.")
        config_obj, paths, _manifest = _load_generated_context(generated_path)
        settings = load_email_settings()
        result: InventoryEmailResult = send_inventory_email(
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
