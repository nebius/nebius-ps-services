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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from . import __version__, native_logs
from .component_defaults import (
    default_target_paths,
    literal_default_input_leaf_names,
    managed_default_payload_paths,
    resolve_component_defaults,
    shared_default_conflicts,
    shared_default_input_sources,
)
from .component_sources import (
    component_output_root_name,
    load_component_sources,
    resolve_component_sources_file,
    set_component_sources_file_override,
)
from .component_wiring import (
    _UNRESOLVED,
    component_entry_lookup,
    component_output_ref,
    input_binding_conflicts,
    input_binding_leaf_names,
    managed_input_binding_payload_paths,
    output_lookup,
    resolve_static_component_output,
)
from .components import (
    COMPONENT_ID_PATTERN,
    ComponentEntry,
    ComponentScope,
    component_entries,
    resolve_component_dependencies,
)
from .config_loader import load_config
from .config_template import starter_config_yaml
from .deployment_status import deployment_status_reporting
from .discover_ops import discover_configs
from .flux_ops import (
    ensure_flux,
    flux_bootstrap_resources_installed,
    flux_controllers_installed,
    flux_crds_installed,
    install_flux_controllers,
    wait_for_flux_resource_apis,
    wait_for_rendered_flux_resources,
)
from .flux_render import render_flux
from .generated_manifest import (
    load_generated_manifest,
    runtime_config_from_manifest,
    terraform_tfvars_from_manifest,
    write_generated_manifest,
)
from .github_secrets import (
    build_github_environment_name,
    detect_github_repo_slug,
    ensure_github_environment,
    environment_secrets_presence,
    read_github_token,
    upsert_environment_secrets,
)
from .helm_client import HelmChartReference, HelmClient
from .iam_bootstrap import (
    auth_public_key_exists,
    bootstrap_ci_service_account,
)
from .infra_render import (
    RenderProfile,
    is_portable_module_source,
    render_terraform_artifacts,
    rendered_module_sources,
)
from .inventory_ops import write_inventory
from .managed_tools import FLUX_VERSION_ENV, TERRAFORM_VERSION_ENV
from .mk8s_preflight import validate_mk8s_network_preflight
from .notify_ops import send_inventory_email
from .paths import (
    InstancePaths,
    resolve_generated_paths,
    resolve_instance_paths,
    validate_path_alignment,
)
from .provider_components import (
    infer_infra_component_category,
    provider_component_match_status,
    provider_resource_exists,
    required_provider_leaf_names_for_component,
    required_provider_leaf_names_for_resource,
)
from .provider_options import (
    OptionChoice,
    ProviderOptionLookup,
    TenantProjectValidationResult,
)
from .render import reset_generated_bundle
from .runtime_config import read_path_with_catalog, to_plain_data
from .runtime_introspection import (
    helm_chart_default_values,
    merge_chart_defaults_with_overrides,
    module_output_names,
    module_required_variables,
    module_source_validation_issues,
    module_variable_names,
    module_variables,
)
from .sdk_auth import init_nebius_sdk
from .templates import customer_workflow_yaml, default_cli_ref
from .terraform_backend import (
    TerraformStateLockInfo,
    backend_settings_from_config,
    ensure_state_bucket,
    read_state_lock_info,
)
from .terraform_ops import (
    terraform_apply,
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
app = typer.Typer(
    add_completion=False,
    help="Nebius artifact generator and deployer: render from config.yaml, then deploy generated artifacts.",
)
terraform_app = typer.Typer(help="Run infra-only Terraform operations against generated artifacts")
flux_app = typer.Typer(help="Apply or bootstrap Flux using generated artifacts")
inventory_app = typer.Typer(help="Refresh local inventory artifacts from generated bundle metadata")

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
) -> None:
    _ = version
    try:
        set_component_sources_file_override(component_sources_file)
    except ValueError as exc:
        _exit_with_error(RuntimeError(str(exc)))


def _load_context(config_path: Path) -> tuple:
    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    return config, paths


def _load_runtime_context(config_path: Path) -> tuple:
    config, paths = _load_context(config_path)
    _validate_active_component_sources(config)
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
    paths: InstancePaths,
    manifest: Mapping[str, Any],
) -> Path:
    payload = terraform_tfvars_from_manifest(manifest)
    tfvars_path = paths.infra_dir / "terraform.auto.tfvars.json"
    tfvars_path.parent.mkdir(parents=True, exist_ok=True)
    tfvars_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return tfvars_path


def _render_overwrite_warning(paths: InstancePaths) -> str | None:
    if not paths.generated_dir.exists():
        return None
    existing_files = sorted(path for path in paths.generated_dir.rglob("*") if path.is_file())
    if not existing_files:
        return None
    return (
        "Render will overwrite existing generated artifacts under "
        f"{paths.generated_dir}. Keep using `config.yaml` as the original render contract, "
        "but treat the generated files as the deployable customer artifacts. "
        "Bootstrap-owned `generated/flux/flux-system` is preserved."
    )


def _can_prompt_for_render_overwrite() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm_render_overwrite(paths: InstancePaths, *, force: bool) -> bool:
    overwrite_warning = _render_overwrite_warning(paths)
    if not overwrite_warning:
        return True
    console.print(f"[yellow]WARNING:[/yellow] {overwrite_warning}")
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


def _exit_with_error(exc: Exception) -> None:
    console.print(f"[red]ERROR:[/red] {exc}")
    raise typer.Exit(code=1) from exc


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
            f"Deployments root does not exist: {path}. "
            "Create an empty folder and pass that path to create/discover. "
            "For CI workflow generation, use a path inside the customer git repository."
        )
    if not path.is_dir():
        raise RuntimeError(f"Deployments root must be a directory: {path}")


def _value_or_prompt(
    value: str | None, *, option_name: str, prompt_text: str, interactive: bool
) -> str:
    if value:
        return value
    if interactive:
        prompted = typer.prompt(prompt_text).strip()
        if prompted:
            return prompted
    raise RuntimeError(f"Missing required option: {option_name}")


def _optional_email_or_prompt(value: str | None, *, interactive: bool) -> str | None:
    if value is not None:
        return value
    if not interactive:
        return None
    prompted = typer.prompt("Notifications email (optional)", default="").strip()
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
                "Nebius scope validation failed for tenant/project selection: "
                f"{result.message}"
            )

        if not result.retryable:
            raise RuntimeError(
                "Nebius scope validation failed: "
                f"{result.message}"
            )

        console.print(
            "[yellow]Nebius scope validation warning[/yellow]: "
            f"{result.message}"
        )
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
                typer.prompt("Region ID", default=DEFAULT_REGION_ID).strip()
                or DEFAULT_REGION_ID
            )
            if selected in SUPPORTED_REGION_IDS:
                return selected
            console.print(
                "[red]Invalid region[/red]. "
                f"Expected one of: {available}"
            )
    return DEFAULT_REGION_ID


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
            raise RuntimeError(
                f"Duplicate {option_name} override for component '{component_id}'."
            )
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
    origin = entry.origin.strip().lower() if entry.origin else ""
    badge = f"[{origin}]" if origin in {"provider", "custom", "helm"} else ""
    base = entry.id if not badge else f"{entry.id} {badge}"
    if entry.group:
        return f"{entry.group} >> {base}"
    return base


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
        if entry.group:
            enriched.append(entry)
            continue
        category = infer_infra_component_category(entry.id)
        if category == entry.group:
            enriched.append(entry)
            continue
        enriched.append(replace(entry, group=category))
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
                "[yellow]Interactive checkbox UI unavailable:[/yellow] "
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
        raw = typer.prompt(
            f"{prompt_label} (y/n, {WIZARD_EXIT_TOKEN}=stop wizard)",
            default=default_raw,
            show_default=True,
        ).strip().lower()
        if raw == WIZARD_EXIT_TOKEN:
            return False
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print(f"[red]Invalid selection[/red]. Enter y, n, or {WIZARD_EXIT_TOKEN}.")


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


def _dynamic_infra_component_path(payload: dict[str, Any], component_id: str) -> PayloadPath | None:
    infra_node = payload.get("infra")
    if not isinstance(infra_node, Mapping):
        return None
    components = infra_node.get("components")
    if not isinstance(components, list):
        return None
    target = component_id.strip().lower()
    for index, item in enumerate(components):
        if not isinstance(item, Mapping):
            continue
        current_id = str(item.get("id", "")).strip().lower()
        if current_id == target:
            return ("infra", "components", index)
    return None


def _dynamic_app_chart_path(payload: dict[str, Any], chart_id: str) -> PayloadPath | None:
    apps_node = payload.get("apps")
    if not isinstance(apps_node, Mapping):
        return None
    charts = apps_node.get("charts")
    if not isinstance(charts, list):
        return None
    target = chart_id.strip().lower()
    for index, item in enumerate(charts):
        if not isinstance(item, Mapping):
            continue
        current_id = str(item.get("id", "")).strip().lower()
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


def _co_located_project_args(full_path_label: str) -> dict[str, Any]:
    sibling_parent_id = _co_located_input_path(full_path_label, "parent_id")
    if not sibling_parent_id:
        return {}
    if sibling_parent_id == full_path_label:
        return {}
    return {
        "project_id_path": sibling_parent_id,
        "fallback_project_id_path": "client_info.nebius.project_id",
    }


def _infer_infra_provider_field_spec(
    *,
    entry: ComponentEntry,
    full_path_label: str,
) -> dict[str, Any] | None:
    """Infer provider option sources from infra field path conventions."""
    if not full_path_label.startswith("infra."):
        return None

    leaf = full_path_label.rsplit(".", maxsplit=1)[-1].strip().lower()
    normalized_leaf = leaf.replace("-", "_")
    co_located_project_args = _co_located_project_args(full_path_label)

    if normalized_leaf in {"parent_id", "project_id"} or normalized_leaf.endswith(
        ("_parent_id", "_project_id")
    ):
        return {"sources": [{"source": "provider", "provider": "tenant_projects"}]}

    if normalized_leaf == "network_id" or normalized_leaf.endswith("_network_id"):
        source: dict[str, Any] = {"source": "provider", "provider": "project_networks"}
        if co_located_project_args:
            source["args"] = co_located_project_args
        return {"sources": [source]}

    if normalized_leaf == "subnet_id" or normalized_leaf.endswith("_subnet_id"):
        source = {"source": "provider", "provider": "project_subnets"}
        if co_located_project_args:
            source["args"] = co_located_project_args
        return {"sources": [source]}

    if normalized_leaf in {"k8s_version", "kubernetes_version", "control_plane_version"}:
        return {
            "sources": [
                {
                    "source": "provider",
                    "provider": "mk8s_control_plane_versions",
                }
            ]
        }

    if leaf in {"platform"} or leaf.endswith(("_platform", "-platform")):
        is_gpu_path = (
            leaf.startswith("gpu")
            or "_gpu_" in leaf
            or "-gpu-" in leaf
            or ".gpu_" in full_path_label
            or ".gpu-" in full_path_label
        )
        prefix = "gpu-" if is_gpu_path else "cpu-"
        provider = "mk8s_compatible_platforms" if entry.id == "mk8s" else "compute_platforms"
        source: dict[str, Any] = {"source": "provider", "provider": provider}
        if prefix:
            source["args"] = {"platform_prefix": prefix}
        return {"sources": [source]}

    if leaf in {"preset"} or leaf.endswith(("_preset", "-preset")):
        platform_path = ""
        if full_path_label.endswith(".preset"):
            platform_path = f"{full_path_label[:-len('.preset')]}.platform"
        elif full_path_label.endswith("_preset"):
            platform_path = f"{full_path_label[:-len('_preset')]}_platform"
        elif full_path_label.endswith("-preset"):
            platform_path = f"{full_path_label[:-len('-preset')]}-platform"
        if not platform_path:
            return None
        return {
            "sources": [
                {
                    "source": "provider",
                    "provider": "compute_platform_presets",
                    "args": {"platform_path": platform_path},
                }
            ]
        }

    return None


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

    if entry.scope == "infra":
        return _infer_infra_provider_field_spec(entry=entry, full_path_label=full_path_label)
    return None


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
            args = source.get("args")
            resolved_args = dict(args) if isinstance(args, dict) else {}
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
        args = dict(args_raw) if isinstance(args_raw, dict) else {}
        key = (provider, json.dumps(args, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        provider_specs.append((provider, args))
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


def _declared_wizard_field_labels(entry: ComponentEntry) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    absolute_roots = {"version", "client_info", "infra", "apps"}
    prefix = f"{entry.config_path}."
    for raw in entry.wizard_fields:
        key = raw.strip()
        if not key:
            continue
        if key.startswith(prefix) or key == entry.config_path or key.split(".", 1)[0] in absolute_roots:
            full_label = key
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
    if entry.origin == "provider":
        required_names = {
            _normalize_leaf_name(name)
            for name in required_provider_leaf_names_for_component(entry.id)
        }
        source_hint = str(entry.source or "").strip()
        if source_hint and "_" in source_hint:
            required_names.update(
                _normalize_leaf_name(name)
                for name in required_provider_leaf_names_for_resource(source_hint)
            )
        return required_names
    if entry.origin == "custom":
        return {
            _normalize_leaf_name(name)
            for name in module_required_variables(str(entry.source or ""))
        }
    return set()


def _module_variable_specs_for_entry(entry: ComponentEntry) -> dict[str, Any]:
    if entry.scope != "infra" or entry.origin != "custom":
        return {}
    source = str(entry.source or "").strip()
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
    if required:
        tags.append("required")
    if not tags:
        return path_label
    return f"{path_label} [{', '.join(tags)}]"


def _prompt_path_sort_key(path: PayloadPath, *, required_leaf_names: set[str]) -> tuple[int, str]:
    leaf = path[-1] if path else ""
    leaf_name = _normalize_leaf_name(str(leaf)) if isinstance(leaf, str) else ""
    required_rank = 0 if leaf_name and leaf_name in required_leaf_names else 1
    return required_rank, _format_payload_path(path)


def _hydrate_app_component_values_from_chart_defaults(
    *,
    payload: dict[str, Any],
    entry: ComponentEntry,
) -> None:
    if entry.scope != "apps" or entry.origin != "helm":
        return
    component_path = _dynamic_component_path(payload, entry)
    if component_path is None:
        return
    component_node = _get_payload_value(payload, component_path)
    if not isinstance(component_node, Mapping):
        return
    chart_name = str(component_node.get("id", "")).strip() or entry.id
    chart_repo = str(component_node.get("repo", "")).strip()
    chart_version = str(component_node.get("version", "")).strip()
    if not chart_name:
        return

    defaults = helm_chart_default_values(
        chart_name_or_ref=chart_name,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )
    if not defaults:
        return

    chart_values_path = component_path + ("values",)
    current_values = _get_payload_value(payload, chart_values_path)
    if not isinstance(current_values, dict):
        current_values = {}
    merged_values = merge_chart_defaults_with_overrides(
        chart_defaults=defaults,
        current_values=current_values,
    )
    _set_payload_value(payload, chart_values_path, merged_values)


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
) -> None:
    component_path = _dynamic_component_path(payload, entry)
    if component_path is None:
        return
    component_node = _get_payload_value(payload, component_path)
    if not isinstance(component_node, dict):
        component_node = {}
        _set_payload_value(payload, component_path, component_node)

    if entry.scope == "apps":
        # Ensure chart-backed entries discovered at runtime have editable scaffolding.
        component_node.setdefault("namespace", str(entry.default_namespace or "").strip() or entry.id)
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

    # For source-defined terraform modules, seed required variables under component inputs.
    if entry.origin == "custom":
        inputs_node = component_node.get("inputs")
        if not isinstance(inputs_node, dict):
            inputs_node = {}
            component_node["inputs"] = inputs_node
        for leaf_name in sorted(required_leaf_names):
            key = leaf_name.replace("-", "_")
            inputs_node.setdefault(key, "")
        return

    scalar_paths = _collect_scalar_leaf_paths(component_node)
    visible_leaf_names = {
        _normalize_leaf_name(str(path[-1]))
        for path in scalar_paths
        if path and isinstance(path[-1], str)
    }
    minimal_block = visible_leaf_names <= {"enabled"}
    if not minimal_block:
        return
    for leaf_name in sorted(required_leaf_names):
        key = leaf_name.replace("-", "_")
        component_node.setdefault(key, "")


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
    prompt_default = default_value if default_value in option_values else (
        option_values[0] if option_values else ""
    )
    if _is_tty_session():
        try:
            import questionary

            rendered_choices = [
                questionary.Choice(title=choice.label, value=choice.value) for choice in choices
            ]
            rendered_choices.append(questionary.Choice(title="<manual input>", value="__manual__"))
            selected = questionary.select(
                rendered_label,
                choices=rendered_choices,
                instruction="Select one option (or choose manual input).",
                default=prompt_default or None,
                qmark="",
            ).ask()
            if selected is None:
                return current, True
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

    while True:
        try:
            raw = typer.prompt(f"{prompt_suffix} (index or value)", default=prompt_default).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if raw == WIZARD_EXIT_TOKEN:
            return current, True
        if not raw:
            return (prompt_default or current), False
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(choices):
                return choices[index - 1].value, False
            console.print(
                f"[red]Invalid option index[/red]. Use a value between 1 and {len(choices)}."
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
    while True:
        if isinstance(current, bool):
            try:
                raw = typer.prompt(
                    f"{prompt_suffix} [true/false]",
                    default="true" if current else "false",
                ).strip().lower()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
            if raw in {"true", "t", "1", "yes", "y"}:
                return True, False
            if raw in {"false", "f", "0", "no", "n"}:
                return False, False
            console.print("[red]Invalid boolean[/red]. Expected true/false.")
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
                console.print("[red]Invalid integer[/red]. Enter a whole number.")
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
                console.print("[red]Invalid number[/red]. Enter a numeric value.")
                continue

        if current is None:
            try:
                raw = typer.prompt(f"{prompt_suffix} (blank keeps null)", default="").strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return current, True
            if raw == WIZARD_EXIT_TOKEN:
                return current, True
            if not raw:
                return None, False
            try:
                coerced = _coerce_raw_value_from_type_hint(raw, type_hint)
            except ValueError as exc:
                console.print(f"[red]Invalid value[/red]. {exc}")
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
            console.print(f"[red]Invalid value[/red]. {exc}")
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

    selected_components: list[ComponentEntry] = []
    selected_components.extend(entry for entry in infra_entries if entry.id in selected_infra)
    selected_components.extend(entry for entry in app_entries if entry.id in selected_apps)

    warned_provider_fallbacks: set[str] = set()
    provider_allowed_cache: dict[str, tuple[set[str], tuple[str, ...]]] = {}
    for entry in selected_components:
        if not _wizard_continue_phase(
            f"Configure '{entry.id}' component fields now?",
            default=True,
        ):
            return yaml.safe_dump(payload, sort_keys=False), False

        _hydrate_app_component_values_from_chart_defaults(payload=payload, entry=entry)
        required_leaf_names = _required_leaf_names_for_entry(entry)
        required_leaf_names -= set(shared_default_input_sources(entry))
        _seed_component_prompt_fields(
            payload=payload,
            entry=entry,
            required_leaf_names=required_leaf_names,
        )

        component_path = _dynamic_component_path(payload, entry)
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
            managed_default_payload_paths(component_path, entry) if component_path is not None else set()
        )
        if component_path is not None:
            bound_prompt_paths |= managed_input_binding_payload_paths(component_path, entry)

        declared_prompt_paths: list[PayloadPath] = []
        for full_path_label in _declared_wizard_field_labels(entry):
            resolved_declared = _resolve_payload_path(payload, full_path_label)
            if resolved_declared is None:
                console.print(
                    f"[yellow]Skipping wizard field '{full_path_label}'[/yellow]: path not found in config payload."
                )
                continue
            value = _get_payload_value(payload, resolved_declared)
            if isinstance(value, (dict, list)):
                continue
            if resolved_declared in bound_prompt_paths:
                continue
            declared_prompt_paths.append(resolved_declared)

        prompt_paths: list[PayloadPath] = []
        seen_prompt_labels: set[str] = set()
        field_type_hints: dict[str, str | None] = {}
        required_prompt_labels: set[str] = set()
        for path in declared_prompt_paths:
            label = _format_payload_path(path)
            if label in seen_prompt_labels:
                continue
            seen_prompt_labels.add(label)
            prompt_paths.append(path)

        module_dependency_expander: Any = None
        if component_path is not None:
            if entry.scope == "infra" and entry.origin == "custom":
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
                            return
                        spec = module_specs_by_leaf.get(leaf_name)
                        if spec is not None and spec.has_default:
                            module_inputs[leaf_name] = copy.deepcopy(spec.default)
                            return
                        if required_only:
                            module_inputs[leaf_name] = ""

                    for leaf_name in sorted(required_leaf_names):
                        _seed_input_value(leaf_name, required_only=True)

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

                    def _append_field_prompt(
                        leaf_name: str,
                        spec: Any,
                        *,
                        required: bool,
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
                        seen_prompt_labels.add(label)
                        prompt_paths.append(full_path)
                        field_type_hints[label] = spec.type_hint
                        if required:
                            required_prompt_labels.add(label)

                    for leaf_name, spec in sorted(
                        module_specs_by_leaf.items(),
                        key=lambda item: (0 if item[1].required else 1, item[0]),
                    ):
                        current_value = _resolve_mapping_segment(module_inputs, leaf_name)
                        has_current = not (
                            current_value is None
                            or (isinstance(current_value, str) and not current_value.strip())
                        )
                        include_optional_by_dependency = any(
                            leaf_name.startswith(f"{prefix}_")
                            and leaf_name != f"{prefix}_enabled"
                            for prefix in _enabled_prefixes()
                        )
                        is_toggle = (
                            _short_type_hint(spec.type_hint) == "bool"
                            and leaf_name.endswith("_enabled")
                        )
                        should_prompt = (
                            spec.required
                            or has_current
                            or include_optional_by_dependency
                            or is_toggle
                        )
                        if not should_prompt:
                            continue
                        _seed_input_value(leaf_name, required_only=spec.required)
                        _append_field_prompt(leaf_name, spec, required=spec.required)

                    def _expand_dependency_fields_for_enabled_prefixes(
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                    ) -> None:
                        prefixes = _enabled_prefixes()
                        if not prefixes:
                            return
                        for leaf_name, spec in sorted(
                            module_specs_by_leaf.items(),
                            key=lambda item: item[0],
                        ):
                            if spec.required:
                                continue
                            if not any(
                                leaf_name.startswith(f"{prefix}_")
                                and leaf_name != f"{prefix}_enabled"
                                for prefix in prefixes
                            ):
                                continue
                            _seed_input_value(leaf_name, required_only=False)
                            _append_field_prompt(leaf_name, spec, required=False)

                    module_dependency_expander = _expand_dependency_fields_for_enabled_prefixes
            elif entry.scope == "apps" and entry.origin == "helm":
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
                if values_path is not None and isinstance(values_node, dict):
                    for relative_path in _collect_scalar_leaf_paths(values_node):
                        full_path = values_path + relative_path
                        if full_path in bound_prompt_paths:
                            continue
                        label = _format_payload_path(full_path)
                        if label in seen_prompt_labels:
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

        prompt_paths.sort(
            key=lambda path: _prompt_path_sort_key(path, required_leaf_names=required_leaf_names),
        )

        prompt_index = 0
        while True:
            while prompt_index < len(prompt_paths):
                full_path = prompt_paths[prompt_index]
                prompt_index += 1
                current = _get_payload_value(payload, full_path)
                full_path_label = _format_payload_path(full_path)
                field_choices = _resolve_dynamic_field_choices(
                    payload=payload,
                    entry=entry,
                    full_path_label=full_path_label,
                    provider_lookup=provider_lookup,
                )
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
                if not field_choices and providers and full_path_label not in warned_provider_fallbacks:
                    provider_names = ", ".join(providers)
                    console.print(
                        "[yellow]Dynamic provider options unavailable[/yellow] for "
                        f"'{full_path_label}' via [{provider_names}]. Falling back to manual input."
                    )
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
                    updated_value = str(updated).strip()
                    if updated_value in allowed_provider_values:
                        break
                    console.print(
                        "[red]Invalid value[/red] for "
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
                _set_payload_value(payload, full_path, updated)
            if module_dependency_expander is None:
                break
            before_expand = len(prompt_paths)
            module_dependency_expander()
            if len(prompt_paths) == before_expand:
                break

    return yaml.safe_dump(payload, sort_keys=False), True


@dataclass(frozen=True)
class _AppChartDependencyAdjustment:
    source_app_id: str
    dependency_app_id: str
    dependency_chart_name: str


_ChartRef = tuple[str, str, str]  # (chart_name_or_ref, chart_repo, version)
_ChartMetaCache = dict[_ChartRef, tuple[str | None, set[str], str | None]]


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


def _app_component_chart_name_from_payload(payload: dict[str, Any], entry: ComponentEntry) -> str | None:
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
) -> tuple[str | None, set[str], str | None]:
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
        value = (None, set(), str(exc))
        cache[cache_key] = value
        return value

    if not isinstance(chart_payload, Mapping):
        value = (None, set(), "chart metadata is not a mapping")
        cache[cache_key] = value
        return value

    chart_name = str(chart_payload.get("name", "")).strip().lower() or None
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

    value = (chart_name, dependency_names, None)
    cache[cache_key] = value
    return value


def _helm_chart_dependency_names(
    *,
    chart_name_or_ref: str,
    chart_repo: str,
    chart_version: str,
    cache: _ChartMetaCache,
) -> tuple[set[str], str | None]:
    _chart_name, dependency_names, error = _helm_chart_metadata(
        chart_name_or_ref=chart_name_or_ref,
        chart_repo=chart_repo,
        chart_version=chart_version,
        cache=cache,
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
    names.update(
        token.strip().lower()
        for token in entry.dependency_match_names
        if token.strip()
    )

    payload_name = _app_component_chart_name_from_payload(payload, entry)
    if payload_name:
        names.add(payload_name)

    source_name = _source_chart_name(entry)
    if source_name:
        names.add(source_name)

    chart_ref = _app_component_chart_ref_from_payload(payload, entry)
    if include_live_chart_name and cache is not None and chart_ref is not None:
        chart_name, _deps, _error = _helm_chart_metadata(
            chart_name_or_ref=chart_ref[0],
            chart_repo=chart_ref[1],
            chart_version=chart_ref[2],
            cache=cache,
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
            "[yellow]Adjusted component selection:[/yellow] "
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
                "[yellow]Adjusted component selection:[/yellow] "
                f"enabling 'apps:{adjustment.dependency_app_id}' because "
                f"'apps:{adjustment.source_app_id}' chart depends on "
                f"'{adjustment.dependency_chart_name}'."
            )
        for warning in app_warnings:
            console.print(f"[yellow]Dependency lookup warning:[/yellow] {warning}")

    return normalized_infra, normalized_apps


def _enabled_component_ids(config: Any, *, scope: ComponentScope) -> set[str]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return set()
    if scope == "infra":
        return {str(row["id"]) for row in _dynamic_enabled_infra_component_rows(payload)}
    return {str(row["id"]) for row in _dynamic_enabled_app_chart_rows(payload)}


def _validate_component_dependencies(config: Any) -> list[str]:
    issues: list[str] = []
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ["Runtime config payload must be a mapping"]

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
                dep_scope, dep_id = raw_dep.split(":", maxsplit=1) if ":" in raw_dep else (scope, raw_dep)
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
                    origin="helm",
                    group=group,
                )
            )
        chart_cache: _ChartMetaCache = {}
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


def _validate_enabled_chart_sources(config: Any) -> list[str]:
    issues: list[str] = []
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return ["Runtime config payload must be a mapping"]

    for chart_row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(chart_row["id"])
        chart_repo = str(chart_row.get("repo", "")).strip()
        chart_version = str(chart_row.get("version", "")).strip()
        for issue in _helm_chart_validation_issues(
            chart_name=chart_id,
            chart_repo=chart_repo,
            chart_version=chart_version,
        ):
            issues.append(f"apps.charts[{chart_id}] {issue}")
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


@lru_cache(maxsize=64)
def _helm_chart_validation_issues(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
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
            issues.append(
                f"OCI ref basename must match chart name '{chart_id}': {repo_ref}"
            )
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

    try:
        helm_client = HelmClient()
    except Exception as exc:
        return (
            f"requires helm for source validation ({source_display}): {str(exc).strip() or 'helm unavailable'}",
        )

    try:
        chart_meta = helm_client.show_chart(
            reference=HelmChartReference(
                chart_name=chart_id,
                chart_repo=repo,
                chart_version=version,
            )
        )
    except Exception as exc:
        return (f"could not be resolved by helm ({source_display}): {exc}",)

    resolved_name = str(chart_meta.get("name", "")).strip().lower()
    if resolved_name and resolved_name != chart_id.lower():
        issues.append(f"resolved chart name '{resolved_name}' does not match '{chart_id}'")

    resolved_version = str(chart_meta.get("version", "")).strip()
    if version and resolved_version and not _versions_match(version, resolved_version):
        issues.append(
            f"resolved chart version '{resolved_version}' does not match configured version '{version}'"
        )

    return tuple(issues)


def _validate_component_sources_registry(
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[Path, list[str], list[str]]:
    source_path = resolve_component_sources_file()
    sources = load_component_sources()
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
            issues.append("infra.tf_modules[] entry has empty module id")
            continue
        if not COMPONENT_ID_PATTERN.fullmatch(module_id):
            issues.append(
                f"infra.tf_modules[{module_id}] module name must use lowercase letters, digits, and hyphens"
            )
            continue
        if module_id in declared_entries:
            duplicate_ids.add(module_id)
        else:
            declared_entries[module_id] = ("infra", module)
        if not module_source:
            issues.append(f"infra.tf_modules[{module_id}] is missing source")
            continue
        for issue in module_source_validation_issues(module_source):
            issues.append(f"infra.tf_modules[{module_id}] {issue}")
        local_module_path = _resolve_local_module_source_path(module_source)
        if local_module_path is None:
            continue
        if not (local_module_path / "main.tf").exists():
            warnings.append(
                f"infra.tf_modules[{module_id}] is missing main.tf in {local_module_path}"
            )
        if not (local_module_path / "variables.tf").exists():
            warnings.append(
                f"infra.tf_modules[{module_id}] is missing variables.tf in {local_module_path}"
            )
        expected_from_folder = _normalize_component_token(local_module_path.name)
        if expected_from_folder and expected_from_folder != module_id:
            warnings.append(
                f"infra.tf_modules[{module_id}] folder name '{local_module_path.name}' "
                f"normalizes to '{expected_from_folder}', which differs from module id '{module_id}'."
            )
        discovered_variables = module_variable_names(str(local_module_path))
        if not discovered_variables:
            warnings.append(
                f"infra.tf_modules[{module_id}] has no discoverable Terraform variables; "
                "wizard field discovery may be limited."
            )

    for chart in sources.helm_charts:
        chart_name = chart.name.strip()
        chart_id = _normalize_component_token(chart_name)
        repo = str(chart.repo or "").strip()
        version = str(chart.version or "").strip()
        chart_label = f"apps.helm_charts[{chart_name}]"
        _advance(f"apps:{chart_name or '?'}")

        if not chart_name:
            issues.append("apps.helm_charts[] entry has empty name")
            continue
        if chart_id in declared_entries:
            duplicate_ids.add(chart_id)
        else:
            declared_entries[chart_id] = ("apps", chart)
        for issue in _helm_chart_validation_issues(
            chart_name=chart_name,
            chart_repo=repo,
            chart_version=version,
        ):
            issues.append(f"{chart_label} {issue}")

    for component_id in sorted(duplicate_ids):
        issues.append(
            f"component id '{component_id}' is declared more than once across infra/apps. "
            "Cross-component bindings require globally unique component ids."
        )

    for component_id, (scope, source_entry) in declared_entries.items():
        output_by_name = {output.name: output for output in source_entry.outputs}
        default_targets = default_target_paths(source_entry)
        declared_module_input_names: set[str] = set()

        if scope == "apps":
            for output in source_entry.outputs:
                if output.kind == "terraform_output":
                    issues.append(
                        f"apps component '{component_id}' output '{output.name}' cannot use "
                        "Terraform-backed outputs; Helm chart sources may export config/static values only."
                    )
        else:
            module_source = str(source_entry.source or "").strip()
            declared_module_outputs = set(module_output_names(module_source)) if module_source else set()
            declared_module_input_names = {
                _normalize_leaf_name(name) for name in module_variable_names(module_source)
            } if module_source else set()
            is_local_like_source = bool(module_source) and not module_source.lower().startswith(
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
                    if declared_module_input_names and target_leaf not in declared_module_input_names:
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
                    f"undeclared output '{binding.source_component_id}.{binding.source_output_name}'"
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
                target_segments = [segment.strip() for segment in target_path.split(".") if segment.strip()]
                if len(target_segments) >= 2:
                    target_leaf = _normalize_leaf_name(target_segments[1])
                    if declared_module_input_names and target_leaf not in declared_module_input_names:
                        issues.append(
                            f"{scope} component '{component_id}' default target '{target_path}' does not "
                            f"match any declared module input for source '{source_entry.source}'"
                        )

        handoff = getattr(source_entry, "handoff", None)
        if handoff is not None:
            cluster_id_output = output_by_name.get(handoff.cluster_id_output_name)
            if cluster_id_output is None:
                issues.append(
                    f"{scope} component '{component_id}' handoff.cluster_id "
                    f"'{handoff.cluster_id_output_name}' is not declared under outputs"
                )
            elif cluster_id_output.kind != "terraform_output":
                issues.append(
                    f"{scope} component '{component_id}' handoff.cluster_id "
                    f"'{handoff.cluster_id_output_name}' must point to a Terraform-backed exported output alias"
                )

            access_output = output_by_name.get(handoff.access_output_name)
            if access_output is None:
                issues.append(
                    f"{scope} component '{component_id}' handoff.access "
                    f"'{handoff.access_output_name}' is not declared under outputs"
                )
            elif access_output.kind == "terraform_output":
                issues.append(
                    f"{scope} component '{component_id}' handoff.access "
                    f"'{handoff.access_output_name}' must point to a config/static exported output alias"
                )
            elif access_output.kind == "static":
                access_value = str(access_output.value or "").strip().lower()
                if access_value not in {"external", "internal"}:
                    issues.append(
                        f"{scope} component '{component_id}' handoff.access "
                        f"'{handoff.access_output_name}' resolves to invalid static value "
                        f"'{access_output.value}'. Expected 'external' or 'internal'."
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
        mode = str(row.get("mode", "")).strip().lower()
        if mode not in {"provider", "custom"}:
            continue
        component_id = str(row["id"])
        source = str(row.get("source", "")).strip() or None
        entry = entry_by_id.get(component_id)
        if entry is None:
            entry_origin = "provider" if mode == "provider" else "custom"
            entry_label = (
                "Runtime provider component"
                if entry_origin == "provider"
                else "Runtime source-backed component"
            )
            entry = ComponentEntry(
                id=component_id,
                scope="infra",
                config_path=f"infra.components.{component_id}",
                description=f"{entry_label} '{component_id}'",
                origin=entry_origin,
                source=source,
            )
        elif mode == "custom" and source and str(entry.source or "").strip() != source:
            entry = replace(entry, origin="custom", source=source)
        component_path = _dynamic_infra_component_path(payload, component_id)
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


def _required_enabled_infra_field_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    entry_by_id = {entry.id: entry for entry in infra_entries}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        mode = str(row.get("mode", "")).strip().lower()
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            inputs = {}

        if mode == "provider":
            entry = entry_by_id.get(component_id)
            if entry is None:
                entry = ComponentEntry(
                    id=component_id,
                    scope="infra",
                    config_path=f"infra.components.{component_id}",
                    description=f"Runtime provider component '{component_id}'",
                    origin="provider",
                    source=None,
                )
            component_path = _dynamic_infra_component_path(payload, component_id)
            if component_path is None:
                continue
            inputs_path = component_path + ("inputs",)
            inputs_node = _get_payload_value(payload, inputs_path)
            if not isinstance(inputs_node, (dict, list)):
                continue
            for relative_path in _collect_scalar_leaf_paths(inputs_node):
                full_path = inputs_path + relative_path
                full_path_label = _format_payload_path(full_path)
                if not _provider_field_path_is_active(payload, full_path_label):
                    continue
                if not _provider_source_specs_for_field(
                    entry=entry,
                    full_path_label=full_path_label,
                ):
                    continue
                value = _get_payload_value(payload, full_path)
                if value is None or (isinstance(value, str) and not value.strip()):
                    issues.append(f"{full_path_label} is required")
            continue

        if mode != "custom":
            continue

        source = str(row.get("source", "")).strip()
        entry = entry_by_id.get(component_id)
        if not source:
            source = str(entry.source if entry is not None else "").strip()
        if not source:
            continue

        if entry is not None and entry.defaults:
            resolved_row = resolve_component_defaults(
                payload=payload,
                component_node=dict(row),
                entry=entry,
                preserve_existing_literal=True,
                preserve_existing_shared=False,
            )
            inputs = resolved_row.get("inputs", {})
            if not isinstance(inputs, Mapping):
                inputs = {}

        required_leaf_names = {_normalize_leaf_name(name) for name in module_required_variables(source)}
        if not required_leaf_names:
            continue
        binding_by_leaf = shared_default_input_sources(entry) if entry is not None else {}
        required_leaf_names -= input_binding_leaf_names(entry) if entry is not None else set()
        required_leaf_names -= (
            literal_default_input_leaf_names(entry) if entry is not None else set()
        )

        if not isinstance(inputs, Mapping):
            for leaf_name in sorted(required_leaf_names):
                binding_source = binding_by_leaf.get(leaf_name)
                if binding_source:
                    bound_value = _read_payload_field(payload, binding_source)
                    if bound_value is not None and not (
                        isinstance(bound_value, str) and not bound_value.strip()
                    ):
                        continue
                    issues.append(
                        f"{binding_source} is required for infra.components[{component_id}].inputs.{leaf_name}"
                    )
                    continue
                issues.append(
                    f"infra.components[{component_id}].inputs.{leaf_name} is required"
                )
            continue
        for leaf_name in sorted(required_leaf_names):
            value = _resolve_mapping_segment(inputs, leaf_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                binding_source = binding_by_leaf.get(leaf_name)
                if binding_source:
                    bound_value = _read_payload_field(payload, binding_source)
                    if bound_value is not None and not (
                        isinstance(bound_value, str) and not bound_value.strip()
                    ):
                        continue
                    issues.append(
                        f"{binding_source} is required for infra.components[{component_id}].inputs.{leaf_name}"
                    )
                    continue
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(
                    f"infra.components[{component_id}].inputs.{leaf_name} is required"
                )
    return issues


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
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        inputs = dict(item.get("inputs", {})) if isinstance(item.get("inputs"), Mapping) else {}
        entry = entry_by_id.get(component_id)
        source = _effective_catalog_component_source(row=item, entry=entry)
        version = _effective_catalog_component_version(row=item, entry=entry)
        if source:
            inferred_mode = "custom"
        elif any(key in inputs for key in ("resource_type", "provider_resource_type")) or (
            entry is not None and entry.origin == "provider"
        ):
            inferred_mode = "provider"
        else:
            inferred_mode = "custom"
        rows.append(
            {
                "id": component_id,
                "mode": inferred_mode,
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
        chart_id = str(item.get("id", "")).strip().lower()
        if not chart_id:
            continue
        rows.append(
            {
                "id": chart_id,
                "group": str(item.get("group", "")).strip().lower() or "workloads",
                "repo": str(item.get("repo", "")).strip(),
                "version": str(item.get("version", "")).strip(),
                "namespace": str(item.get("namespace", "")).strip(),
                "release-name": str(
                    item.get("release-name", item.get("release_name", chart_id))
                ).strip()
                or chart_id,
                "values": dict(item.get("values", {})) if isinstance(item.get("values"), Mapping) else {},
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
        if row.get("mode") != "custom":
            continue
        component_id = str(row["id"])
        source = str(row.get("source", "")).strip()
        if not source:
            source = str(entry_by_id.get(component_id).source if component_id in entry_by_id else "").strip()
        if not source:
            issues.append(
                f"infra.components[{component_id}] is enabled but has no module source configured"
            )
            continue
        for issue in module_source_validation_issues(source):
            issues.append(f"infra.components[{component_id}] {issue}")
        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        declared_outputs = set(module_output_names(source))
        is_local_like_source = not source.lower().startswith(("git::", "http://", "https://", "oci://"))
        for output in entry.outputs:
            if output.kind != "terraform_output":
                continue
            required_output = str(output.source_path).strip()
            if required_output and (
                (declared_outputs and required_output not in declared_outputs)
                or (is_local_like_source and required_output not in declared_outputs)
            ):
                issues.append(
                    f"infra.components[{component_id}] module source '{source}' must expose output "
                    f"'{required_output}' for declared component output '{output.name}'"
                )
    return issues


def _active_component_input_binding_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    all_entries = component_entry_lookup()
    active_rows: dict[str, dict[str, Any]] = {}
    for row in _dynamic_enabled_infra_component_rows(payload):
        active_rows[str(row["id"])] = row
    for row in _dynamic_enabled_app_chart_rows(payload):
        active_rows[str(row["id"])] = row

    for component_id, row in active_rows.items():
        entry = all_entries.get(component_id)
        if entry is None or not entry.input_bindings:
            continue
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"{entry.scope}.components[{component_id}].{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
                if entry.scope == "infra"
                else f"apps.charts[{component_id}].{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
            )
        for binding in entry.input_bindings:
            source_entry = all_entries.get(binding.source_component_id)
            source_ref = component_output_ref(binding.source_component_id, binding.source_output_name)
            if source_entry is None:
                issues.append(
                    f"{entry.scope} component '{component_id}' input binding '{binding.target_path}' references "
                    f"unknown component '{binding.source_component_id}'"
                )
                continue
            if binding.source_component_id not in active_rows:
                issues.append(
                    f"{entry.scope} component '{component_id}' input binding '{binding.target_path}' requires "
                    f"enabled source component '{binding.source_component_id}'"
                )
                continue
            source_output = output_lookup(source_entry).get(binding.source_output_name)
            if source_output is None:
                issues.append(
                    f"{entry.scope} component '{component_id}' input binding '{binding.target_path}' references "
                    f"undeclared output '{source_ref}'"
                )
                continue
            if source_output.kind != "terraform_output":
                static_value = resolve_static_component_output(
                    payload,
                    component_id=binding.source_component_id,
                    output_name=binding.source_output_name,
                )
                if static_value is _UNRESOLVED:
                    issues.append(
                        f"{entry.scope} component '{component_id}' input binding '{binding.target_path}' could not "
                        f"resolve non-Terraform output '{source_ref}' from the active config/catalog"
                    )
    return issues


def _active_handoff_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        entry = infra_entry_by_id.get(component_id)
        if entry is None or entry.handoff is None:
            continue

        access_output = output_lookup(entry).get(entry.handoff.access_output_name)
        if access_output is None:
            issues.append(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' is not declared under outputs"
            )
            continue
        if access_output.kind == "terraform_output":
            issues.append(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' must resolve from config/static output, not Terraform state"
            )
            continue

        access_value = resolve_static_component_output(
            payload,
            component_id=component_id,
            output_name=entry.handoff.access_output_name,
        )
        if access_value is _UNRESOLVED:
            issues.append(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' could not be resolved from the active config/catalog"
            )
            continue
        normalized_access = str(access_value).strip().lower()
        if normalized_access not in {"external", "internal"}:
            issues.append(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' resolved to '{access_value}'. "
                "Expected 'external' or 'internal'."
            )
    return issues


def _validate_active_component_sources(config: Any) -> None:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    issues: list[str] = []
    infra_entries = component_entries("infra")
    issues.extend(_enabled_custom_module_source_issues(payload=payload, infra_entries=infra_entries))
    issues.extend(_active_component_input_binding_issues(payload))
    issues.extend(_active_handoff_issues(payload))
    issues.extend(_validate_enabled_chart_sources(config))
    if issues:
        raise RuntimeError("Active component source validation failed:\n  - " + "\n  - ".join(issues))


def _binding_conflict_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    infra_entry_by_id = {entry.id: entry for entry in component_entries("infra")}
    for row in _dynamic_enabled_infra_component_rows(payload):
        component_id = str(row["id"])
        entry = infra_entry_by_id.get(component_id)
        if entry is None:
            continue
        for target_path, source_path in shared_default_conflicts(row, entry):
            issues.append(
                f"infra.components[{component_id}].{target_path} is managed by shared default "
                f"'{source_path}' and must not be set explicitly"
            )
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"infra.components[{component_id}].{target_path} is managed by component input binding "
                f"'{source_ref}' and must not be set explicitly"
            )

    app_entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    for row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(row["id"])
        entry = app_entry_by_id.get(chart_id)
        if entry is None:
            continue
        for target_path, source_path in shared_default_conflicts(row, entry):
            issues.append(
                f"apps.charts[{chart_id}].{target_path} is managed by shared default "
                f"'{source_path}' and must not be set explicitly"
            )
        for target_path, source_ref in input_binding_conflicts(row, entry):
            issues.append(
                f"apps.charts[{chart_id}].{target_path} is managed by component input binding "
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
        "depends_on_platform",
        "resource_type",
        "provider_resource_type",
    }
    for row in _dynamic_enabled_infra_component_rows(payload):
        if row.get("mode") != "custom":
            continue
        component_id = str(row["id"])
        inputs = row.get("inputs", {})
        if not isinstance(inputs, Mapping):
            continue
        source = str(row.get("source", "")).strip()
        if not source:
            source = str(entry_by_id.get(component_id).source if component_id in entry_by_id else "").strip()
        if not source:
            continue
        declared_leaf_names = {
            _normalize_leaf_name(name)
            for name in module_variable_names(source)
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
                    f"infra.components[{component_id}].inputs.{input_name} is not declared by module '{source}'"
                )
    return issues


def _enabled_provider_schema_match_issues(
    *,
    payload: dict[str, Any],
    infra_entries: tuple[ComponentEntry, ...],
) -> list[str]:
    issues: list[str] = []
    checked_dynamic: set[str] = set()
    for row in _dynamic_enabled_infra_component_rows(payload):
        if row.get("mode") != "provider":
            continue
        component_id = str(row["id"])
        if component_id in checked_dynamic:
            continue
        checked_dynamic.add(component_id)
        match_status = provider_component_match_status(component_id)
        if match_status is False:
            issues.append(
                f"infra component '{component_id}' does not match any live Terraform provider resource"
            )
        inputs = row.get("inputs", {})
        if isinstance(inputs, Mapping):
            resource_type = str(
                inputs.get("resource_type") or inputs.get("provider_resource_type") or ""
            ).strip()
            if resource_type:
                exists = provider_resource_exists(resource_type)
                if exists is False:
                    issues.append(
                        f"infra component '{component_id}' declares unknown provider resource_type '{resource_type}'"
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


def _validate_strict_config(config: Any) -> None:
    """Validate deployment-readiness constraints via runtime/provider checks."""
    issues: list[str] = []
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime config payload must be a mapping")

    infra_entries = component_entries("infra")
    issues.extend(_validate_component_dependencies(config))
    issues.extend(_required_enabled_infra_field_issues(payload=payload, infra_entries=infra_entries))
    issues.extend(_binding_conflict_issues(payload))
    issues.extend(_active_component_input_binding_issues(payload))
    issues.extend(_active_handoff_issues(payload))
    issues.extend(_enabled_custom_module_source_issues(payload=payload, infra_entries=infra_entries))
    issues.extend(_enabled_custom_module_input_schema_issues(payload=payload, infra_entries=infra_entries))
    issues.extend(_enabled_provider_schema_match_issues(payload=payload, infra_entries=infra_entries))
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

    issues.extend(_validate_enabled_chart_sources(config))

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


def _runtime_auth_cache_material(*, project_id: str, client_name: str) -> RuntimeAuthCacheMaterial | None:
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
        raise RuntimeError("Runtime auth profile was created but cache material could not be loaded")
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
    instance_config: Path | None,
) -> str:
    if client_name or instance_config is not None:
        return _resolve_client_name_for_auth_bootstrap(
            client_name=client_name,
            instance_config=instance_config,
        )
    matches = [name for name, pid in _discover_runtime_auth_profiles() if pid == project_id]
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise RuntimeError(
            "Multiple runtime auth profiles exist for this project_id. "
            "Provide --client-name (or --instance-config)."
        )
    raise RuntimeError(
        "Missing required option: --client-name (or provide --instance-config)"
    )


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
        aws_access = os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or os.environ.get(
            "NEBIUS_S3_ACCESS_KEY_ID", ""
        ).strip()
        aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or os.environ.get(
            "NEBIUS_S3_SECRET_ACCESS_KEY", ""
        ).strip()
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

    if (need_terraform and not os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE")) or need_eso_mysterybox:
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
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or os.environ.get(
        "NEBIUS_S3_ACCESS_KEY_ID", ""
    ).strip()
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or os.environ.get(
        "NEBIUS_S3_SECRET_ACCESS_KEY", ""
    ).strip()
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


def _try_generate_terraform_lock_file(
    config: Any,
    paths: InstancePaths,
) -> bool:
    try:
        # Render performs create-if-missing runtime auth bootstrap for lockfile generation
        # and can use the managed Terraform binary when Terraform is not already in PATH.
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=True)
        terraform_init(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:
        console.print(
            "[yellow]WARNING:[/yellow] "
            f"Unable to generate Terraform lock file at {paths.infra_dir / '.terraform.lock.hcl'}: {exc}"
        )
        return False
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
    render_profile: RenderProfile,
) -> list[dict[str, Any]]:
    return [
        {
            "component_id": item.component_id,
            "module_name": item.module_name,
            "source": item.source,
            "portable": item.portable,
        }
        for item in rendered_module_sources(config, render_profile=render_profile)
    ]


def _generated_bundle_module_sources(
    paths: InstancePaths,
    manifest: Mapping[str, Any],
) -> list[dict[str, str]]:
    render = manifest.get("render")
    if isinstance(render, Mapping):
        raw_sources = render.get("module_sources")
        if isinstance(raw_sources, Sequence):
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
                        "module_name": str(item.get("module_name", "")).strip(),
                        "source": source,
                    }
                )
            if collected:
                return collected

    main_tf = paths.infra_dir / "main.tf"
    if not main_tf.exists():
        return []

    pattern = re.compile(r'^\s*source\s*=\s*"([^"]+)"', re.MULTILINE)
    return [
        {
            "component_id": "",
            "module_name": "",
            "source": match.group(1).strip(),
        }
        for match in pattern.finditer(main_tf.read_text(encoding="utf-8"))
        if match.group(1).strip()
    ]


def _validate_generated_bundle_portability(
    paths: InstancePaths,
    manifest: Mapping[str, Any],
) -> None:
    module_sources = _generated_bundle_module_sources(paths, manifest)
    non_portable = [
        item
        for item in module_sources
        if not is_portable_module_source(str(item.get("source", "")))
    ]
    if not non_portable:
        return

    formatted = ", ".join(
        (
            f"{item['component_id']}={item['source']}"
            if item.get("component_id")
            else item["source"]
        )
        for item in non_portable
    )
    raise RuntimeError(
        "Generated bundle is not portable; local Terraform module sources are present: "
        f"{formatted}. Rerender with --render-profile portable before committing or using CI."
    )


def _write_generated_runtime_manifest(
    config: Any,
    paths: InstancePaths,
    *,
    render_profile: RenderProfile,
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
        raise RuntimeError(f"Rendered Terraform inputs file must contain a JSON object: {tfvars_path}")
    return write_generated_manifest(
        config=config,
        paths=paths,
        handoffs=_enabled_cluster_handoffs(config),
        required_component_outputs=_required_runtime_component_output_specs(config),
        render_profile=render_profile.value,
        module_sources=_rendered_module_source_payload(config, render_profile=render_profile),
        terraform_tfvars=terraform_tfvars,
        flux_version=sources.cli.flux.version,
        terraform_version=sources.cli.terraform.version,
    )


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
    return sum(1 for item in charts if isinstance(item, Mapping) and bool(item.get("enabled", False)))


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
        chart_id = str(item.get("id", "")).strip().lower()
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
            source_ref = component_output_ref(binding.source_component_id, binding.source_output_name)
            if source_ref in seen_refs:
                continue
            seen_refs.add(source_ref)
            required.append(
                {
                    "component_id": binding.source_component_id,
                    "output_name": binding.source_output_name,
                    "source_ref": source_ref,
                }
            )
    return required


def _runtime_component_output_values(
    config: Any,
    paths: InstancePaths,
    *,
    required_specs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    required = required_specs if required_specs is not None else _required_runtime_component_output_specs(config)
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
            spec["component_id"],
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
    return bool(_enabled_cluster_handoffs(config) or _required_runtime_component_output_specs(config))


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
                "output_name": str(item.get("output_name", "")).strip(),
                "source_ref": str(item.get("source_ref", "")).strip(),
            }
        )
    return [item for item in specs if item["component_id"] and item["output_name"] and item["source_ref"]]


def _manifest_requires_flux_terraform_state(manifest: Mapping[str, Any]) -> bool:
    return bool(
        _manifest_cluster_handoffs(manifest)
        or _manifest_required_component_output_specs(manifest)
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
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        entry = entry_by_id.get(component_id)
        if entry is None or entry.handoff is None:
            continue
        access_value = resolve_static_component_output(
            payload,
            component_id=component_id,
            output_name=entry.handoff.access_output_name,
        )
        if access_value is _UNRESOLVED:
            raise RuntimeError(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' could not be resolved from the active config/catalog"
            )
        normalized_access = str(access_value).strip().lower()
        if normalized_access not in {"external", "internal"}:
            raise RuntimeError(
                f"infra component '{component_id}' handoff.access "
                f"'{entry.handoff.access_output_name}' resolved to '{access_value}'. "
                "Expected 'external' or 'internal'."
            )
        handoffs.append(
            {
                "component_id": component_id,
                "cluster_id_output_name": component_output_root_name(
                    component_id,
                    entry.handoff.cluster_id_output_name,
                ),
                "component_output_ref": component_output_ref(
                    component_id,
                    entry.handoff.cluster_id_output_name,
                ),
                "access": normalized_access,
            }
        )
    return handoffs


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
        if not (
            isinstance(item, dict) and _non_empty_text(item.get("name")) == entry_name
        )
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
        console.print(f"[yellow]WARNING:[/yellow] {exc}")
        return None

    console.print(f"Updated local kubeconfig at {local_kubeconfig}")
    return local_kubeconfig


def _prepare_cluster_handoff_kube_env(
    config: Any,
    paths: InstancePaths,
    *,
    stack: ExitStack,
    handoffs: list[dict[str, str]] | None = None,
) -> dict[str, str] | None:
    if _active_chart_count(config) == 0:
        return None

    handoffs = handoffs if handoffs is not None else _enabled_cluster_handoffs(config)
    if not handoffs:
        return None
    if len(handoffs) > 1:
        component_ids = ", ".join(sorted(handoff["component_id"] for handoff in handoffs))
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
            "the cluster ID required for kubeconfig handoff before applying Flux manifests."
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
    kube_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="nebius-cxcli-kube-")))
    kubeconfig_path = kube_root / "config"
    _write_kubeconfig_file(kubeconfig_path, spec)
    _persist_cluster_handoff_kubeconfig(spec=spec)
    return {"KUBECONFIG": str(kubeconfig_path)}


def _apply_rendered_flux(paths: InstancePaths, *, extra_env: dict[str, str] | None = None) -> None:
    """Apply rendered Flux manifests in local deploy mode."""
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
            raise RuntimeError(message)

        flux_installed = flux_controllers_installed(extra_env=extra_env) and flux_crds_installed(
            extra_env=extra_env
        )
        if not flux_installed:
            _set_phase("[cyan]Installing Flux controllers into the target cluster...[/cyan]")
            manifest_url = install_flux_controllers(extra_env=extra_env)
            console.print(
                "Installed Flux controllers in the target cluster from "
                f"{manifest_url}"
            )
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
        detail = _first_non_empty_line(result.stderr or result.stdout or "") or "kubectl get nodes failed"
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
        message = (
            f"[bold white]Kubernetes[/bold white] [dim][{elapsed}s][/dim] {escape(summary)}"
        )
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
    paths: InstancePaths,
    manifest: Mapping[str, Any],
    *,
    auto_auth_bootstrap: bool,
) -> None:
    """Deploy an existing generated artifact bundle without rerendering it."""
    _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
    runtime_env = _terraform_runtime_env(config)
    terraform_init(paths.infra_dir, extra_env=runtime_env)
    terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
    _run_terraform_apply_with_status(config, paths, initialize=False)
    write_inventory(config, paths)
    with ExitStack() as stack:
        kube_env = _prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            handoffs=_manifest_cluster_handoffs(manifest),
        )
        _wait_for_cluster_nodes_ready(extra_env=kube_env, emit=lambda message: console.print(message))
        _apply_rendered_flux(paths, extra_env=kube_env)
        _warn_if_flux_gitops_not_bootstrapped(config, paths, extra_env=kube_env)


def _run_terraform_apply_with_status(
    config: Any,
    paths: InstancePaths,
    *,
    initialize: bool = True,
) -> None:
    runtime_env = _terraform_runtime_env(config)
    validate_mk8s_network_preflight(config)
    with deployment_status_reporting(
        config,
        emit=lambda message: console.print(message),
    ) as reporter:
        try:
            terraform_apply(
                paths.infra_dir,
                extra_env=runtime_env,
                initialize=initialize,
                event_callback=reporter.handle_terraform_event,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\n\nLast known deploy status:\n{reporter.snapshot()}"
            ) from exc


def _warn_if_flux_gitops_not_bootstrapped(
    config: Any,
    paths: InstancePaths,
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
        "[yellow]WARNING:[/yellow] Flux GitOps bootstrap is not configured for this cluster yet. "
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
    paths: InstancePaths,
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


def _resolve_project_id_for_auth_bootstrap(
    *, project_id: str | None, instance_config: Path | None
) -> str:
    if project_id:
        return project_id
    if instance_config is None:
        raise RuntimeError("Missing required option: --project-id (or provide --instance-config)")
    config = load_config(instance_config.resolve())
    return config.client_info.nebius.project_id


def _resolve_client_name_for_auth_bootstrap(
    *,
    client_name: str | None,
    instance_config: Path | None,
) -> str:
    if client_name:
        normalized = client_name.strip()
        if normalized:
            return normalized
    if instance_config is None:
        raise RuntimeError(
            "Missing required option: --client-name (or provide --instance-config)"
        )
    config = load_config(instance_config.resolve())
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


def _preflight_bootstrap_ci_auth(
    *,
    github_repo: str | None,
    github_token_env: str,
    repo_root: Path,
) -> str:
    repo_slug = _resolve_github_repo_slug(explicit_repo_slug=github_repo, repo_root=repo_root)
    github_token = read_github_token(preferred_env=github_token_env)
    if github_token:
        return repo_slug
    raise RuntimeError(
        "Automatic CI auth bootstrap requires a GitHub token. "
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
    "# Keep the canonical instance config versioned in a private repo; ignore generated Terraform runtime files.",
    "instances/*/*/generated/infra/.terraform/",
    "instances/*/*/generated/infra/*.tfstate",
    "instances/*/*/generated/infra/*.tfstate.*",
    "instances/*/*/generated/infra/.terraform.tfstate.lock.info",
    "instances/*/*/generated/infra/crash.log",
    "instances/*/*/generated/infra/*.tfplan",
    "instances/*/*/generated/infra/plan.out",
    "instances/*/*/generated/infra/terraform.auto.tfvars.json",
    "instances/*/*/generated/infra/*.auto.tfvars",
    "instances/*/*/generated/infra/*.auto.tfvars.json",
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
    deployments_path = deployments_root / "instances"
    deployments_path.mkdir(parents=True, exist_ok=True)
    return deployments_root


def _instance_config_path(
    *,
    deployments_root: Path,
    client_name: str,
    tenant_id: str,
    project_id: str,
) -> Path:
    return (
        deployments_root
        / "instances"
        / f"{client_name}--{tenant_id}"
        / project_id
        / "config.yaml"
    )


def _deep_merge_payload(base: Any, override: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = {
            str(key): _deep_merge_payload(value, value)
            for key, value in base.items()
        }
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
                component_id = str(item.get("id", "")).strip().lower()
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
                chart_id = str(item.get("id", "")).strip().lower()
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
    infra_by_id: dict[str, dict[str, Any]] = {}
    for item in infra_components:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("id", "")).strip().lower()
        if not component_id:
            continue
        infra_by_id[component_id] = item
    selected_infra_components: list[dict[str, Any]] = []
    for entry in infra_entries:
        if entry.id not in selected_infra:
            continue
        row = infra_by_id.get(entry.id)
        if row is None:
            row = {
                "id": entry.id,
                "enabled": True,
                "inputs": {},
            }
            infra_by_id[entry.id] = row
        else:
            if not isinstance(row.get("inputs"), Mapping):
                row["inputs"] = {}
            row["enabled"] = True
        selected_infra_components.append(row)
    infra["components"] = selected_infra_components

    app_charts = apps.get("charts")
    if not isinstance(app_charts, list):
        app_charts = []
    apps_by_id: dict[str, dict[str, Any]] = {}
    for item in app_charts:
        if not isinstance(item, dict):
            continue
        chart_id = str(item.get("id", "")).strip().lower()
        if not chart_id:
            continue
        apps_by_id[chart_id] = item
    selected_app_charts: list[dict[str, Any]] = []
    for entry in app_entries:
        if entry.id not in selected_apps:
            continue
        row = apps_by_id.get(entry.id)
        if row is None:
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
                "group": group,
                "enabled": True,
                "repo": str(chart_repo or ""),
                "version": str(entry.version or ""),
                "namespace": namespace,
                "release-name": release_name,
                "values": {},
            }
            apps_by_id[entry.id] = row
        else:
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
            if not str(row.get("release-name", row.get("release_name", ""))).strip():
                row["release-name"] = str(entry.default_release_name or "").strip() or entry.id
            if "group" not in row or not str(row.get("group", "")).strip():
                raw_group = str(entry.group or "").strip().lower()
                row["group"] = re.sub(r"[^a-z0-9]+", "-", raw_group).strip("-") or "workloads"
            if not isinstance(row.get("values"), Mapping):
                row["values"] = {}
            row["enabled"] = True
        selected_app_charts.append(row)
    apps["charts"] = selected_app_charts

    return runtime_payload


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
        leaf_names = {_normalize_leaf_name(name) for name in module_variable_names(source)}
        if not leaf_names:
            continue
        if "parent_id" in leaf_names and all(
            alias not in inputs for alias in ("parent_id", "parent-id")
        ):
            inputs["parent_id"] = project_id
        if "project_id" in leaf_names and all(
            alias not in inputs for alias in ("project_id", "project-id")
        ):
            inputs["project_id"] = project_id


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
        if _normalize_workflow_text(existing_workflow) == _normalize_workflow_text(expected_workflow):
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
    instance_dir = deployments_root / "instances" / f"{client_name}--{tenant_id}" / project_id
    config_path = instance_dir / "config.yaml"

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
        next_config_text = yaml.safe_dump(payload, sort_keys=False)
        current_config_text = (
            config_path.read_text(encoding="utf-8") if config_path.exists() else None
        )
        should_write = force or current_config_text != next_config_text
        if should_write:
            config_path.write_text(next_config_text, encoding="utf-8")
            wrote_config = True

    inventory_path = instance_dir / "generated" / "inventory" / "inventory.md"
    if not inventory_path.exists():
        inventory_path.write_text(
            "# Inventory\n\nGenerated by `nebius-cxcli inventory write`.\n",
            encoding="utf-8",
        )

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path, deployments_dir_hint=str(deployments_root))
    validate_path_alignment(config, paths)
    return BootstrapResult(
        deployments_root=deployments_root,
        config_path=config_path,
        wrote_config=wrote_config,
    )


@app.command("create")
def create_command(
    target_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Deployments root folder path. Any existing directory works."
            )
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
            help=(
                "Validate component_sources.yaml before create runs "
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
                "Reset and overwrite existing config.yaml from current create inputs "
                "and selected components."
            ),
        ),
    ] = False,
) -> None:
    """Create or reconcile one instance config via provider-driven component wizard (scaffold only)."""
    try:
        base_path = target_path.resolve()
        _validate_deployments_root_target(base_path)
        if validate_sources:
            with console.status("[cyan]Validating component_sources.yaml...[/cyan]"):
                source_path, source_issues, source_warnings = _validate_component_sources_registry()
            for warning in source_warnings:
                console.print(f"[yellow]Source validation warning:[/yellow] {warning}")
            if source_issues:
                raise RuntimeError(
                    f"Component sources validation failed for {source_path}:\n  - "
                    + "\n  - ".join(source_issues)
                )

        interactive_mode = not no_interactive
        resolved_client_name = _value_or_prompt(
            client_name,
            option_name="--client-name",
            prompt_text="Client name",
            interactive=interactive_mode,
        )
        resolved_tenant_id = _value_or_prompt(
            tenant_id,
            option_name="--tenant-id",
            prompt_text="Tenant ID",
            interactive=interactive_mode,
        )
        resolved_project_id = _value_or_prompt(
            project_id,
            option_name="--project-id",
            prompt_text="Project ID",
            interactive=interactive_mode,
        )
        provider_lookup = ProviderOptionLookup()
        resolved_tenant_id, resolved_project_id = _validate_tenant_project_ids_or_prompt(
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            interactive=interactive_mode,
            provider_lookup=provider_lookup,
        )
        resolved_region_id = _region_or_prompt(region_id, interactive=interactive_mode)
        resolved_email = _optional_email_or_prompt(email, interactive=interactive_mode)

        deployments_root = _resolve_deployments_root(base_path)
        existing_config_path = _instance_config_path(
            deployments_root=deployments_root,
            client_name=resolved_client_name,
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

        infra_entries = _with_infra_provider_groups(component_entries("infra"))
        app_entries = component_entries("apps")
        existing_infra_selection = (
            _enabled_ids_from_runtime_payload(payload=existing_payload, entries=infra_entries)
            if existing_payload is not None
            else set()
        )
        existing_apps_selection = (
            _enabled_ids_from_runtime_payload(payload=existing_payload, entries=app_entries)
            if existing_payload is not None
            else set()
        )

        optional_wizard_mode = interactive_mode
        if interactive_mode:
            optional_wizard_mode = _wizard_continue_phase(
                "Continue with optional wizard phases (component selection and fields)?",
                default=True,
            )
            if not optional_wizard_mode:
                console.print(
                    "[yellow]Wizard optional phases skipped.[/yellow] "
                    "A starter config will be generated and you can continue editing config.yaml manually."
                )
        selected_infra_raw = _resolve_component_ids(
            scope="infra",
            raw_values=infra_components_opt,
            interactive=optional_wizard_mode,
            entries=infra_entries,
            seed_defaults=existing_infra_selection if existing_payload is not None and not force else None,
        )
        selected_apps_raw = _resolve_component_ids(
            scope="apps",
            raw_values=apps_components_opt,
            interactive=optional_wizard_mode,
            entries=app_entries,
            seed_defaults=existing_apps_selection if existing_payload is not None and not force else None,
        )
        app_namespace_overrides = _parse_component_value_overrides(
            raw_values=app_namespace_opt,
            option_name="--app-namespace",
        )
        app_releasename_overrides = _parse_component_value_overrides(
            raw_values=app_releasename_opt,
            option_name="--app-releasename",
        )

        dependency_seed_payload: dict[str, Any] | None = None
        if selected_apps_raw:
            dependency_seed_yaml = starter_config_yaml(
                client_name=resolved_client_name,
                tenant_id=resolved_tenant_id,
                project_id=resolved_project_id,
                region_id=resolved_region_id,
                email=resolved_email,
                selected_infra=selected_infra_raw,
                selected_apps=selected_apps_raw,
                infra_entries=infra_entries,
                app_entries=app_entries,
            )
            parsed_seed_payload = yaml.safe_load(dependency_seed_yaml) or {}
            if isinstance(parsed_seed_payload, dict):
                dependency_seed_payload = parsed_seed_payload
        if dependency_seed_payload is not None and existing_payload is not None and not force:
            dependency_seed_payload = _deep_merge_payload(dependency_seed_payload, existing_payload)
            dependency_seed_payload = _filter_runtime_payload_for_selected_components(
                payload=dependency_seed_payload,
                selected_infra=selected_infra_raw,
                selected_apps=selected_apps_raw,
                infra_entries=infra_entries,
                app_entries=app_entries,
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

        config_yaml_override: str | None = None
        wizard_completed = True
        starter_yaml = starter_config_yaml(
            client_name=resolved_client_name,
            tenant_id=resolved_tenant_id,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            email=resolved_email,
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=infra_entries,
            app_entries=app_entries,
        )
        starter_payload = yaml.safe_load(starter_yaml) or {}
        if not isinstance(starter_payload, dict):
            raise RuntimeError("Generated starter config payload must be a mapping")
        if existing_payload is not None and not force:
            starter_payload = _deep_merge_payload(starter_payload, existing_payload)
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
            namespace_overrides=app_namespace_overrides,
            release_name_overrides=app_releasename_overrides,
        )
        _seed_infra_project_scope_defaults(
            payload=starter_payload,
            infra_entries=infra_entries,
        )
        config_yaml_override = yaml.safe_dump(starter_payload, sort_keys=False)

        if interactive_mode and optional_wizard_mode:
            config_yaml_override, wizard_completed = _run_component_field_wizard(
                config_yaml=config_yaml_override,
                selected_infra=selected_infra,
                selected_apps=selected_apps,
                infra_entries=infra_entries,
                app_entries=app_entries,
                provider_lookup=provider_lookup,
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
            force=force,
            config_yaml=config_yaml_override,
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
                console.print(f"Updated: {result.config_path}")
            else:
                console.print(f"Created: {result.config_path}")
        else:
            console.print(f"Config up-to-date: {result.config_path}")
        console.print(
            "Enabled infra components: "
            + (", ".join(sorted(selected_infra)) if selected_infra else "(none)")
        )
        console.print(
            "Enabled apps components: "
            + (", ".join(sorted(selected_apps)) if selected_apps else "(none)")
        )
        console.print(f"Ensured generated skeleton: {result.config_path.parent / 'generated'}")
        if interactive_mode and not wizard_completed:
            console.print(
                "[yellow]Wizard exited early.[/yellow] Remaining fields keep defaults. "
                "Edit config.yaml manually before validate/render."
            )
        console.print(
            "Next steps: run `nebius-cxcli validate <config.yaml>`, "
            "`nebius-cxcli render <config.yaml>`, "
            "`nebius-cxcli bootstrap-ci <config.yaml>` (optional), then deploy from "
            "`<instance>/generated` with `nebius-cxcli deploy <generated-dir>`."
        )
        console.print(
            "[yellow]Security warning:[/yellow] keep this customer repository private "
            "because the deployments root contains sensitive operational metadata."
        )
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[yellow]Cancelled by user[/yellow].")
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("bootstrap-ci")
def bootstrap_ci_command(
    config_path: Annotated[
        Path,
        typer.Argument(
            help="Path to instance config.yaml inside the target customer git repository"
        ),
    ],
    auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auth-bootstrap/--no-auth-bootstrap",
            help=(
                "Full CI bootstrap: ensure Nebius CI service account + keys and sync GitHub environment secrets "
                "(enabled by default)"
            ),
        ),
    ] = True,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "Optional override for the target GitHub repository slug '<owner>/<repo>' "
                "used for auth bootstrap and environment secret sync. Normally auto-detected "
                "from the target repository origin remote; valid only when "
                "--auth-bootstrap is enabled."
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help=(
                "Environment variable name holding the GitHub token used for auth bootstrap "
                "and environment secret sync (falls back to GH_TOKEN/GITHUB_TOKEN; valid only "
                "when --auth-bootstrap is enabled)."
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
    """Generate or reconcile the CLI-managed customer GitHub workflow and optionally bootstrap CI auth."""
    try:
        if not auth_bootstrap and (github_repo is not None or github_token_env != "GH_TOKEN"):
            raise RuntimeError(
                "--github-repo and --github-token-env are valid only when --auth-bootstrap is enabled."
            )

        config, paths = _load_context(config_path)
        resolved_cli_ref = str(cli_ref or "").strip() or default_cli_ref()
        repo_root = _require_git_root(paths.deployments_dir)
        github_environment = _github_environment_name_for_identity(
            client_name=str(config.client_info.client_name),
            project_id=str(config.client_info.nebius.project_id),
        )
        resolved_github_repo: str | None = None
        if auth_bootstrap:
            resolved_github_repo = _preflight_bootstrap_ci_auth(
                github_repo=github_repo,
                github_token_env=github_token_env,
                repo_root=repo_root,
            )
        workflow = _ensure_ci_workflow_for_deployments_root(
            deployments_root=paths.deployments_dir,
            cli_ref=resolved_cli_ref,
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
        console.print(f"GitHub environment: {github_environment}")
        console.print(f"Workflow CLI ref: {resolved_cli_ref}")
        if not auth_bootstrap:
            console.print("Skipped CI auth bootstrap/secrets sync.")
        console.print("CI bootstrap completed.")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("validate")
def validate_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Enable deployment-readiness checks (reject starter placeholders)",
        ),
    ] = False,
    render_profile: Annotated[
        RenderProfile,
        typer.Option(
            "--render-profile",
            help=(
                "Render contract to validate against: portable rejects bundles that would depend "
                "on local Terraform module paths, local-dev allows checked-out module paths."
            ),
            case_sensitive=False,
        ),
    ] = RenderProfile.PORTABLE,
) -> None:
    """Validate config.yaml with runtime source + provider/chart checks."""
    try:
        config, _ = _load_runtime_context(config_path)
        dependency_issues = _validate_component_dependencies(config)
        if dependency_issues:
            raise RuntimeError("Runtime validation failed:\n  - " + "\n  - ".join(dependency_issues))
        if strict:
            _validate_strict_config(config)
            validate_mk8s_network_preflight(config)
        rendered_module_sources(config, render_profile=render_profile)
        if strict:
            console.print(f"[green]Valid (strict):[/green] {config_path}")
            return
        console.print(f"[green]Valid:[/green] {config_path}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("validate-generated")
def validate_generated_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or one of its subdirectories"),
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
                "sources in generated/infra/main.tf or the generated manifest."
            ),
        ),
    ] = False,
) -> None:
    """Validate an existing generated artifact bundle without rerendering it."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        if not paths.infra_dir.exists():
            raise RuntimeError(f"Rendered infra directory does not exist: {paths.infra_dir}")
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        terraform_validate(paths.infra_dir, extra_env=_terraform_runtime_env(config))
        if _active_chart_count(config) > 0:
            if not shutil.which("kubectl"):
                raise RuntimeError("kubectl is required for `validate-generated` but was not found in PATH")
            subprocess.run(
                ["kubectl", "kustomize", str(paths.flux_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        if portable:
            _validate_generated_bundle_portability(paths, _manifest)
        console.print(f"[green]Valid generated artifacts:[/green] {paths.generated_dir}")
    except subprocess.CalledProcessError as exc:  # pragma: no cover - CLI surface
        detail = _first_non_empty_line(exc.stderr or exc.stdout or "")
        _exit_with_error(RuntimeError(detail or str(exc)))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("validate-sources")
def validate_sources_command() -> None:
    """Validate component_sources.yaml (Terraform module paths and Helm chart refs)."""
    try:
        sources = load_component_sources()
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
                progress_callback=_progress_update
            )
        for warning in warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
        if issues:
            raise RuntimeError(
                f"Component sources validation failed for {source_path}:\n  - "
                + "\n  - ".join(issues)
            )
        console.print(f"[green]Component sources valid:[/green] {source_path}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("auth")
def auth_command(
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help=(
                "Project ID used by runtime auth operations "
                "(or provide --instance-config to resolve it)."
            ),
        ),
    ] = None,
    instance_config: Annotated[
        Path | None,
        typer.Option(
            "--instance-config",
            help="Optional config.yaml path used to resolve project_id and client_name",
        ),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option(
            "--client-name",
            help=(
                "Client name used for runtime auth cache path and --bootstrap-ci environment naming "
                "(`<client_name>-<project_id>`). Required for --create/--recreate unless "
                "--instance-config is provided, or when project_id maps to multiple cached profiles."
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
                "--bootstrap-ci. When omitted, resolves from --instance-config repo root or "
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
            help="Validate local runtime auth cache and Nebius auth key visibility",
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
    """Manage runtime auth profile and optional CI environment-secret sync."""
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
            and instance_config is None
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
                instance_config=instance_config,
            )
            resolved_client_name = _resolve_client_name_for_runtime_profile(
                project_id=resolved_project_id,
                client_name=client_name,
                instance_config=instance_config,
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
                            f"[yellow]Runtime auth profile already exists[/yellow] for project "
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
                if instance_config is not None:
                    repo_root_hint = _require_git_root(instance_config.resolve().parent)
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
                        console.print(f"  [red]- {issue}[/red]")
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


@app.command("render")
def render_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite an existing generated bundle without interactive confirmation.",
        ),
    ] = False,
    render_profile: Annotated[
        RenderProfile,
        typer.Option(
            "--render-profile",
            help=(
                "Generated artifact profile: portable emits Git/registry Terraform module sources "
                "safe for CI and other machines, local-dev preserves resolved local module paths."
            ),
            case_sensitive=False,
        ),
    ] = RenderProfile.PORTABLE,
) -> None:
    """Render and overwrite generated artifacts from config.yaml, prompting before a reset unless --force is provided."""
    try:
        config, paths = _load_runtime_context(config_path)
        if not _confirm_render_overwrite(paths, force=force):
            console.print(
                "[yellow]Render cancelled[/yellow]; existing generated artifacts were left untouched."
            )
            raise typer.Exit(code=0)
        reset_generated_bundle(paths)
        gitignore_result = _ensure_deployments_gitignore(
            deployments_root=paths.deployments_dir,
        )
        paths.infra_dir.mkdir(parents=True, exist_ok=True)
        paths.flux_dir.mkdir(parents=True, exist_ok=True)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        written.extend(render_terraform_artifacts(config, paths, render_profile=render_profile))
        component_output_values = _runtime_component_output_values(config, paths)
        written.extend(render_flux(config, paths, component_output_values=component_output_values))
        write_inventory(config, paths)
        manifest_path = _write_generated_runtime_manifest(
            config,
            paths,
            render_profile=render_profile,
        )
        lock_generated = _try_generate_terraform_lock_file(config, paths)
        console.print(f"Rendered {len(sorted(written))} file(s) under {paths.generated_dir}")
        console.print(f"Render profile: {render_profile.value}")
        if render_profile == RenderProfile.LOCAL_DEV:
            console.print(
                "[yellow]WARNING:[/yellow] local-dev render profile may embed local Terraform "
                "module paths; do not commit or use these generated artifacts in CI."
            )
        console.print(f"Generated deployment manifest: {manifest_path}")
        if gitignore_result.path is not None:
            if gitignore_result.wrote:
                console.print(f"Ensured deployments .gitignore: {gitignore_result.path}")
            else:
                console.print(f"Deployments .gitignore up-to-date: {gitignore_result.path}")
        if lock_generated:
            console.print(f"Generated Terraform lock file: {paths.infra_dir / '.terraform.lock.hcl'}")
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


@app.command("deploy")
def deploy_command(
    generated_path: Annotated[
        Path,
        typer.Argument(
            help="Path to generated/, generated/infra, generated/flux, or a file under generated/"
        ),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=(
                "Automatically bootstrap runtime auth material when required values are missing"
            ),
        ),
    ] = True,
) -> None:
    """Deploy an existing generated artifact bundle locally.

    This command runs Terraform apply, refresh inventory, and applies Flux
    from the committed generated bundle only. It does not create or update
    GitHub workflows, environments, or CI secrets; use
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


@terraform_app.command("plan")
def terraform_plan_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/infra"),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Run terraform init and plan against an existing generated infra bundle; auto-download Terraform if missing."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        runtime_env = _terraform_runtime_env(config)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
        terraform_plan(paths.infra_dir, extra_env=runtime_env, initialize=False)
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("apply")
def terraform_apply_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/infra"),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Refresh inventory, then run convergent terraform apply against existing generated infra; auto-download Terraform if missing."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        _ensure_terraform_backend_ready(config, auto_auth_bootstrap=auto_auth_bootstrap)
        paths.inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(config, paths)
        runtime_env = _terraform_runtime_env(config)
        terraform_init(paths.infra_dir, extra_env=runtime_env)
        terraform_validate(paths.infra_dir, extra_env=runtime_env, initialize=False)
        _run_terraform_apply_with_status(config, paths, initialize=False)
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("unlock")
def terraform_unlock_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/infra"),
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
    """Clear a stale remote Terraform state lock for an existing generated infra bundle; auto-download Terraform if missing."""
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


@flux_app.command("bootstrap")
def flux_bootstrap_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/flux"),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = False,
) -> None:
    """Refresh inventory, then bootstrap or reconcile Flux against an existing generated flux bundle; auto-download Flux CLI if missing."""
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
            _wait_for_cluster_nodes_ready(extra_env=kube_env, emit=lambda message: console.print(message))
            action = ensure_flux(paths, extra_env=kube_env)
        console.print(f"Flux {action} for {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command("apply")
def flux_apply_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/flux"),
    ],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Refresh inventory (including apps artifacts) and apply an existing generated flux bundle directly for idempotent day-2 runs."""
    try:
        config, paths, manifest = _load_generated_context(generated_path)
        if _active_chart_count(config) == 0:
            raise RuntimeError("No enabled apps charts are configured for this instance.")
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
            _wait_for_cluster_nodes_ready(extra_env=kube_env, emit=lambda message: console.print(message))
            _apply_rendered_flux(paths, extra_env=kube_env)
            _warn_if_flux_gitops_not_bootstrapped(config, paths, extra_env=kube_env)
        console.print(f"Flux applied from {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("discover")
def discover_command(
    target_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Deployments root folder path. Works with any existing directory; "
                "uses git change detection for changed config.yaml and generated/** paths when target "
                "path is inside a git repository, otherwise scans all config.yaml files."
            )
        ),
    ],
    include_all: Annotated[
        bool,
        typer.Option("--all", help="Include all config.yaml files instead of changed only"),
    ] = False,
) -> None:
    """Print discover JSON payload for changed deployment instances in this run."""
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


@inventory_app.command("write")
def inventory_write_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/inventory"),
    ],
) -> None:
    """Refresh local non-sensitive inventory artifacts."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        artifacts = write_inventory(config, paths)
        console.print(f"Inventory written: {artifacts.markdown}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("email")
def email_command(
    generated_path: Annotated[
        Path,
        typer.Argument(help="Path to generated/ or generated/inventory"),
    ],
) -> None:
    """Send inventory markdown via SMTP to client_info.notifications.email."""
    try:
        config, paths, _manifest = _load_generated_context(generated_path)
        sent = send_inventory_email(config, paths)
        if sent:
            console.print("Inventory email sent")
        else:
            console.print("client_info.notifications.email not configured; nothing sent")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


def main() -> None:
    app()
