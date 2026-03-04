"""Typer CLI for nebius-cxcli."""

from __future__ import annotations

import atexit
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from . import __version__
from .component_sources import (
    load_component_sources,
    resolve_component_sources_file,
    set_component_sources_file_override,
)
from .components import (
    COMPONENT_ID_PATTERN,
    ComponentEntry,
    ComponentScope,
    component_entries,
    resolve_component_dependencies,
)
from .config_loader import load_config
from .config_template import DEFAULT_SSH_PUBLIC_KEY, starter_config_yaml
from .discover_ops import discover_configs
from .flux_ops import ensure_flux
from .github_secrets import (
    detect_github_repo_slug,
    read_github_token,
    repo_secrets_presence,
    upsert_repo_secrets,
)
from .helm_client import HelmChartReference, HelmClient
from .iam_bootstrap import bootstrap_ci_service_account, ensure_ci_service_account_identity
from .inventory_ops import upload_inventory, write_inventory
from .notify_ops import send_inventory_email
from .paths import InstancePaths, resolve_instance_paths, validate_path_alignment
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
from .render import render_instance
from .runtime_config import read_path, to_plain_data
from .runtime_introspection import (
    helm_chart_default_values,
    merge_chart_defaults_with_overrides,
    module_required_variables,
    module_variable_names,
    module_variables,
)
from .templates import customer_workflow_yaml, default_cli_ref
from .terraform_ops import terraform_apply, terraform_init_backendless, terraform_plan

console = Console()
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
app = typer.Typer(
    add_completion=False,
    help="Provider-driven Nebius automation CLI (single config.yaml workflow).",
)
terraform_app = typer.Typer(help="Run Terraform operations in generated/infra")
flux_app = typer.Typer(help="Bootstrap or reconcile Flux")
inventory_app = typer.Typer(help="Inventory output commands")
auth_app = typer.Typer(help="Authentication and IAM helper commands")

app.add_typer(terraform_app, name="terraform")
app.add_typer(flux_app, name="flux")
app.add_typer(inventory_app, name="inventory")
app.add_typer(auth_app, name="auth")


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
                "Path to component_sources.yaml. "
                "Used by create starter flow "
                "(fallback order: cwd ./component_sources.yaml -> env -> user/global -> repo/bundled). "
                "Used for source-backed validation and create flows."
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


def _exit_with_error(exc: Exception) -> None:
    console.print(f"[red]ERROR:[/red] {exc}")
    raise typer.Exit(code=1) from exc


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


def _infer_default_ssh_public_key() -> str | None:
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists() or not ssh_dir.is_dir():
        return None

    preferred_files = (
        "id_ed25519.pub",
        "id_ecdsa.pub",
        "id_rsa.pub",
        "id_dsa.pub",
    )
    candidate_paths: list[Path] = [ssh_dir / name for name in preferred_files]
    candidate_paths.extend(sorted(path for path in ssh_dir.glob("*.pub") if path.is_file()))

    seen: set[Path] = set()
    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if value:
            return value
    return None


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
    return read_path(payload, field_path)


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
        if entry.source and not component_node.get("source"):
            component_node["source"] = str(entry.source)
        if entry.version and not component_node.get("version"):
            component_node["version"] = str(entry.version)
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

    if not _wizard_continue_phase("Continue to shared infra settings?", default=True):
        return yaml.safe_dump(payload, sort_keys=False), False

    for shared_path in ("infra.ssh_user_name", "infra.ssh_public_key"):
        resolved = _resolve_payload_path(payload, shared_path)
        if resolved is None:
            continue
        current = _get_payload_value(payload, resolved)
        updated, should_stop = _prompt_scalar_override(_format_payload_path(resolved), current)
        if should_stop:
            return yaml.safe_dump(payload, sort_keys=False), False
        _set_payload_value(payload, resolved, updated)

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
        _seed_component_prompt_fields(
            payload=payload,
            entry=entry,
            required_leaf_names=required_leaf_names,
        )

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
        component_path = _dynamic_component_path(payload, entry)
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
                    default_shared_ssh_public_key = _non_empty_text(
                        _read_payload_field(payload, "infra.ssh_public_key")
                    )

                    def _seed_input_value(
                        leaf_name: str,
                        *,
                        required_only: bool,
                        module_inputs: dict[str, Any] = module_inputs,
                        default_project_scope_id: str = default_project_scope_id,
                        default_shared_ssh_public_key: str = default_shared_ssh_public_key,
                        module_specs_by_leaf: dict[str, Any] = module_specs_by_leaf,
                    ) -> None:
                        current_value = _resolve_mapping_segment(module_inputs, leaf_name)
                        if current_value is not None:
                            return
                        if leaf_name in {"parent_id", "project_id"} and default_project_scope_id:
                            module_inputs[leaf_name] = default_project_scope_id
                            return
                        if leaf_name == "ssh_public_key" and default_shared_ssh_public_key:
                            module_inputs[leaf_name] = default_shared_ssh_public_key
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
                        label = _format_payload_path(full_path)
                        if label in seen_prompt_labels:
                            continue
                        seen_prompt_labels.add(label)
                        prompt_paths.append(full_path)
            else:
                component_node = _get_payload_value(payload, component_path)
                for relative_path in _collect_scalar_leaf_paths(component_node):
                    full_path = component_path + relative_path
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

    checks: list[tuple[str, str, str, str]] = []
    for chart_row in _dynamic_enabled_app_chart_rows(payload):
        chart_id = str(chart_row["id"])
        chart_repo = str(chart_row.get("repo", "")).strip()
        chart_version = str(chart_row.get("version", "")).strip()
        if not chart_repo:
            issues.append(f"chart validation failed for apps:{chart_id}: repo is required")
            continue
        checks.append(
            (
                f"apps:{chart_id}",
                chart_id,
                chart_repo,
                chart_version,
            )
        )

    cache: _ChartMetaCache = {}
    for component_id, chart_name_or_ref, chart_repo, chart_version in checks:
        _deps, error = _helm_chart_dependency_names(
            chart_name_or_ref=chart_name_or_ref,
            chart_repo=chart_repo,
            chart_version=chart_version,
            cache=cache,
        )
        if error:
            source_display = _chart_source_display(
                chart_name_or_ref=chart_name_or_ref,
                chart_repo=chart_repo,
            )
            issues.append(
                f"chart validation failed for {component_id} ({source_display}): {error}"
            )
    return issues


_SEMVER_TAG_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _is_http_chart_repo(repo: str) -> bool:
    normalized = repo.strip().lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


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

    roots = (
        Path.cwd(),
        Path(__file__).resolve().parents[1],
        Path(__file__).resolve().parents[2],
        Path(__file__).resolve().parents[3],
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
        if not module_source:
            issues.append(f"infra.tf_modules[{module_id}] is missing source")
            continue
        local_module_path = _resolve_local_module_source_path(module_source)
        if module_source.startswith(("git::", "http://", "https://", "oci://")):
            warnings.append(
                f"infra.tf_modules[{module_id}] uses remote source '{module_source}'; "
                "local module structure checks were skipped."
            )
            continue
        if local_module_path is None:
            issues.append(
                f"infra.tf_modules[{module_id}] source does not resolve to an existing directory: {module_source}"
            )
            continue
        tf_files = sorted(local_module_path.glob("*.tf"))
        if not tf_files:
            issues.append(
                f"infra.tf_modules[{module_id}] source directory has no Terraform files (*.tf): {local_module_path}"
            )
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

    helm_client: HelmClient | None = None
    helm_unavailable_reason = ""
    try:
        helm_client = HelmClient()
    except Exception as exc:
        helm_unavailable_reason = str(exc).strip() or "helm unavailable"
        warnings.append(
            "Helm chart reachability checks were skipped because helm is not available: "
            f"{helm_unavailable_reason}"
        )

    for chart in sources.helm_charts:
        chart_name = chart.name.strip()
        repo = str(chart.repo or "").strip()
        version = str(chart.version or "").strip()
        chart_label = f"apps.helm_charts[{chart_name}]"
        _advance(f"apps:{chart_name or '?'}")

        if not chart_name:
            issues.append("apps.helm_charts[] entry has empty name")
            continue
        if not repo:
            issues.append(f"{chart_label} is missing repo")
            continue
        if not version:
            issues.append(f"{chart_label} is missing version")
            continue

        if _is_oci_chart_repo(repo):
            repo_ref = _canonical_app_chart_repo(chart_repo=repo, chart_name=chart_name)
            repo_tail = repo_ref.rsplit("/", maxsplit=1)[-1].strip().lower()
            if repo_tail != chart_name.lower():
                issues.append(
                    f"{chart_label} OCI ref basename must match chart name "
                    f"('{chart_name}'): {repo_ref}"
                )
            if not _SEMVER_TAG_PATTERN.fullmatch(version):
                issues.append(
                    f"{chart_label} OCI version must be a semantic version tag (got '{version}')"
                )
            if helm_client is not None:
                try:
                    chart_meta = helm_client.show_chart(
                        reference=HelmChartReference(
                            chart_name=chart_name,
                            chart_repo=repo,
                            chart_version=version,
                        )
                    )
                except Exception as exc:
                    warnings.append(f"{chart_label} OCI lookup warning: {exc}")
                    continue
                resolved_name = str(chart_meta.get("name", "")).strip().lower()
                if resolved_name and resolved_name != chart_name.lower():
                    issues.append(
                        f"{chart_label} resolved chart name '{resolved_name}' does not match '{chart_name}'"
                    )
                resolved_version = str(chart_meta.get("version", "")).strip()
                if resolved_version and not _versions_match(version, resolved_version):
                    issues.append(
                        f"{chart_label} resolved chart version '{resolved_version}' "
                        f"does not match configured version '{version}'"
                    )
            continue

        if _is_http_chart_repo(repo):
            index_payload, index_error = _fetch_helm_repo_index(repo)
            if index_error:
                warnings.append(f"{chart_label} {index_error}")
            elif isinstance(index_payload, Mapping):
                entries = index_payload.get("entries", {})
                chart_entries = entries.get(chart_name) if isinstance(entries, Mapping) else None
                if not isinstance(chart_entries, list):
                    issues.append(
                        f"{chart_label} was not found in repo index.yaml entries at {repo}"
                    )
                else:
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
                            f"{chart_label} version '{version}' was not found in index.yaml. "
                            f"Available versions include: {preview}"
                        )
            if helm_client is not None:
                search_rows = helm_client.search_repo(chart_name=chart_name, chart_repo=repo)
                if not search_rows:
                    warnings.append(
                        f"{chart_label} was not found by 'helm search repo' in {repo}. "
                        "Confirm the chart name and repository index."
                    )
                try:
                    chart_meta = helm_client.show_chart(
                        reference=HelmChartReference(
                            chart_name=chart_name,
                            chart_repo=repo,
                            chart_version=version,
                        )
                    )
                except Exception as exc:
                    warnings.append(f"{chart_label} HTTP repo lookup warning: {exc}")
                else:
                    resolved_name = str(chart_meta.get("name", "")).strip().lower()
                    if resolved_name and resolved_name != chart_name.lower():
                        issues.append(
                            f"{chart_label} resolved chart name '{resolved_name}' does not match '{chart_name}'"
                        )
                    resolved_version = str(chart_meta.get("version", "")).strip()
                    if resolved_version and not _versions_match(version, resolved_version):
                        issues.append(
                            f"{chart_label} resolved chart version '{resolved_version}' "
                            f"does not match configured version '{version}'"
                        )
            continue

        issues.append(
            f"{chart_label} repo must start with 'https://' (Helm index repo) "
            f"or 'oci://' (direct OCI chart), got '{repo}'"
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
        if not source:
            source = str(entry_by_id.get(component_id).source if component_id in entry_by_id else "").strip()
        if not source:
            continue

        required_leaf_names = {_normalize_leaf_name(name) for name in module_required_variables(source)}
        if not required_leaf_names:
            continue

        if not isinstance(inputs, Mapping):
            for leaf_name in sorted(required_leaf_names):
                issues.append(
                    f"infra.components[{component_id}].inputs.{leaf_name} is required"
                )
            continue
        for leaf_name in sorted(required_leaf_names):
            value = _resolve_mapping_segment(inputs, leaf_name)
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
        source = str(item.get("source", "")).strip()
        version = str(item.get("version", "")).strip()
        entry = entry_by_id.get(component_id)
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
                f"infra component '{component_id}' is enabled but has no module source configured"
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
    issues.extend(_enabled_custom_module_source_issues(payload=payload, infra_entries=infra_entries))
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


def _runtime_auth_cache_dir(project_id: str) -> Path:
    root_override = os.environ.get(_RUNTIME_AUTH_CACHE_ENV, "").strip()
    if root_override:
        root = Path(root_override).expanduser().resolve()
    else:
        root = (Path.home() / ".config" / "nebius-cxcli" / "runtime-auth").resolve()
    token = re.sub(r"[^A-Za-z0-9._-]", "-", project_id.strip()).strip("-")
    if not token:
        token = "project"
    return root / token


def _runtime_auth_cache_write(
    *,
    project_id: str,
    service_account_id: str,
    auth_public_key_id: str,
    private_key_pem: str,
) -> None:
    cache_dir = _runtime_auth_cache_dir(project_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.chmod(0o700)

    private_key_file = cache_dir / "auth-private.pem"
    private_key_file.write_text(private_key_pem.rstrip() + "\n", encoding="utf-8")
    private_key_file.chmod(0o600)

    payload = {
        "project_id": project_id,
        "service_account_id": service_account_id,
        "auth_public_key_id": auth_public_key_id,
        "private_key_file": private_key_file.name,
    }
    metadata_file = cache_dir / _RUNTIME_AUTH_CACHE_FILE
    metadata_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_file.chmod(0o600)


def _runtime_auth_cache_load(project_id: str) -> bool:
    cache_dir = _runtime_auth_cache_dir(project_id)
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
    if not service_account_id or not auth_public_key_id or not private_key_file_token:
        return False

    private_key_file = (cache_dir / private_key_file_token).resolve()
    if not private_key_file.exists() or not private_key_file.is_file():
        return False

    os.environ["NEBIUS_SA_ID"] = service_account_id
    os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = auth_public_key_id
    os.environ["NEBIUS_AUTH_PRIVATE_KEY_FILE"] = str(private_key_file)
    return True


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
    return missing


def _ensure_runtime_auth_material(
    config: Any,
    *,
    need_terraform: bool,
    need_eso_mysterybox: bool,
    auto_bootstrap: bool = False,
) -> None:
    missing = _runtime_auth_missing_envs(
        need_terraform=need_terraform,
        need_eso_mysterybox=need_eso_mysterybox,
    )
    project_id = str(config.client_info.nebius.project_id).strip()
    if missing:
        _runtime_auth_cache_load(project_id)
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
        result = bootstrap_ci_service_account(
            project_id=project_id,
            service_account_name=_RUNTIME_TF_SERVICE_ACCOUNT_NAME,
            service_account_description=(
                "Service account used by nebius-cxcli Terraform runtime automation"
            ),
            role_ids=["roles/editor"],
            auth_key_description="nebius-cxcli Terraform runtime authorized key",
            access_key_description="nebius-cxcli Terraform runtime Object Storage access key",
            profile=None,
            endpoint=None,
            config_file=None,
        )
        os.environ["NEBIUS_SA_ID"] = result.service_account_id
        os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"] = result.auth_public_key_id
        os.environ["NEBIUS_AUTH_PRIVATE_KEY_PEM"] = result.auth_private_key_pem
        os.environ["NEBIUS_S3_ACCESS_KEY_ID"] = result.s3_access_key_id
        os.environ["NEBIUS_S3_SECRET_ACCESS_KEY"] = result.s3_secret_access_key
        os.environ["AWS_ACCESS_KEY_ID"] = result.s3_access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = result.s3_secret_access_key
        _runtime_auth_cache_write(
            project_id=project_id,
            service_account_id=result.service_account_id,
            auth_public_key_id=result.auth_public_key_id,
            private_key_pem=result.auth_private_key_pem,
        )
        console.print(
            "[green]Auto-bootstrapped runtime auth[/green] "
            "(service account + Object Storage key + auth key) for this command run."
        )

    if (need_terraform and not os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE")) or need_eso_mysterybox:
        _ensure_private_key_file_env()


def _terraform_runtime_env(config: Any) -> dict[str, str]:
    runtime_env: dict[str, str] = {}

    project_id = str(config.client_info.nebius.project_id).strip()
    client_name = str(config.client_info.client_name).strip()
    module_name = "-".join(token for token in ("nebius-cxcli", client_name, project_id) if token)
    module_name = re.sub(r"[^A-Za-z0-9._/-]", "-", module_name)
    module_name = re.sub(r"-+", "-", module_name).strip("-") or "nebius-cxcli"
    runtime_env["TF_VAR_nebius_provider_module_name"] = module_name
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


def _try_generate_terraform_lock_file(config: Any, paths: InstancePaths) -> bool:
    if not shutil.which("terraform"):
        return False
    try:
        terraform_init_backendless(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:
        console.print(
            "[yellow]WARNING:[/yellow] "
            f"Unable to generate Terraform lock file at {paths.infra_dir / '.terraform.lock.hcl'}: {exc}"
        )
        return False
    return (paths.infra_dir / ".terraform.lock.hcl").exists()


def _apply_rendered_flux(paths: InstancePaths) -> None:
    """Apply rendered Flux manifests in local deploy mode."""
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for `deploy` but was not found in PATH")

    flux_installed = (
        subprocess.run(
            ["kubectl", "get", "namespace", "flux-system"],
            capture_output=True,
            text=True,
            timeout=30,
        ).returncode
        == 0
    )
    if not flux_installed:
        if not shutil.which("flux"):
            raise RuntimeError(
                "Flux controllers are not installed in the target cluster. "
                "Install Flux CLI (`flux`) and rerun `deploy`, "
                "or run `nebius-cxcli bootstrap-ci <config.yaml>` for CI-driven bootstrap."
            )
        subprocess.run(
            ["flux", "install"],
            check=True,
            timeout=1800,
        )

    # Local deploy mode does not require a Git repository; apply generated manifests directly.
    subprocess.run(
        ["kubectl", "apply", "-k", str(paths.flux_dir)],
        check=True,
        timeout=1800,
    )


def _render_and_local_deploy(
    config: Any,
    paths: InstancePaths,
    *,
    auto_auth_bootstrap: bool,
) -> int:
    """Run strict validation, render, Terraform apply, then apply Flux manifests."""
    _validate_strict_config(config)
    _ensure_runtime_auth_material(
        config,
        need_terraform=True,
        need_eso_mysterybox=False,
        auto_bootstrap=auto_auth_bootstrap,
    )
    result = render_instance(config, paths)
    terraform_apply(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    _apply_rendered_flux(paths)
    return len(result.files_written)


def _resolve_project_id_for_auth_bootstrap(
    *, project_id: str | None, instance_config: Path | None
) -> str:
    if project_id:
        return project_id
    if instance_config is None:
        raise RuntimeError("Missing required option: --project-id (or provide --instance-config)")
    config = load_config(instance_config.resolve())
    return config.client_info.nebius.project_id


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


def _sync_github_ci_secrets(
    *,
    repo_slug: str,
    github_token: str,
    ci_secrets: dict[str, str],
    include_flux_token: bool,
) -> list[str]:
    payload = dict(ci_secrets)
    if include_flux_token:
        payload[FLUX_SECRET_KEY] = github_token
    return upsert_repo_secrets(repo_slug=repo_slug, token=github_token, secrets=payload)


def _auto_bootstrap_ci_auth_and_secrets(
    *,
    project_id: str,
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

    presence = repo_secrets_presence(
        repo_slug=repo_slug,
        token=github_token,
        names=[*NEBIUS_CI_SECRET_KEYS, FLUX_SECRET_KEY],
    )
    nebius_ready = all(presence.get(name, False) for name in NEBIUS_CI_SECRET_KEYS)
    flux_ready = presence.get(FLUX_SECRET_KEY, False)

    if nebius_ready and flux_ready:
        console.print(
            f"CI auth secrets already configured in {repo_slug}; skipping auth bootstrap."
        )
        return

    if nebius_ready and not flux_ready:
        updated = upsert_repo_secrets(
            repo_slug=repo_slug,
            token=github_token,
            secrets={FLUX_SECRET_KEY: github_token},
        )
        console.print(
            f"Configured missing GitHub secret(s) in {repo_slug} ({len(updated)} secret(s))"
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
        github_token=github_token,
        ci_secrets=ci_secrets,
        include_flux_token=True,
    )
    console.print(
        f"Bootstrapped and synced CI auth secrets to {repo_slug} ({len(updated)} secret(s))"
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
    "# Ignore generated Terraform/Flux/inventory artifacts for all instances.",
    "instances/*/*/generated/",
    "# Ignore Terraform working directory and runtime outputs.",
    "instances/*/*/generated/infra/.terraform/",
    "instances/*/*/generated/infra/*.tfstate",
    "instances/*/*/generated/infra/*.tfstate.*",
    "instances/*/*/generated/infra/.terraform.tfstate.lock.info",
    "instances/*/*/generated/infra/crash.log",
    "instances/*/*/generated/infra/*.tfplan",
    "instances/*/*/generated/infra/plan.out",
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
            if entry.source:
                row["source"] = str(entry.source)
            if entry.version:
                row["version"] = str(entry.version)
            infra_by_id[entry.id] = row
        else:
            if entry.source and not str(row.get("source", "")).strip():
                row["source"] = str(entry.source)
            if entry.version and not str(row.get("version", "")).strip():
                row["version"] = str(entry.version)
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


def _seed_default_shared_ssh_public_key(
    *,
    payload: dict[str, Any],
    inferred_key: str | None,
) -> None:
    if not inferred_key:
        return
    infra_node = payload.get("infra")
    if not isinstance(infra_node, dict):
        return
    current_value = _non_empty_text(infra_node.get("ssh_public_key"))
    if current_value and current_value != DEFAULT_SSH_PUBLIC_KEY:
        return
    infra_node["ssh_public_key"] = inferred_key


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
        source = _non_empty_text(item.get("source")) or _non_empty_text(entry.source if entry else None)
        if not source:
            continue
        leaf_names = {_normalize_leaf_name(name) for name in module_variable_names(source)}
        if not leaf_names:
            continue
        shared_ssh_public_key = _non_empty_text(
            _read_payload_field(payload, "infra.ssh_public_key")
        )

        if "parent_id" in leaf_names and all(
            alias not in inputs for alias in ("parent_id", "parent-id")
        ):
            inputs["parent_id"] = project_id
        if "project_id" in leaf_names and all(
            alias not in inputs for alias in ("project_id", "project-id")
        ):
            inputs["project_id"] = project_id
        if (
            "ssh_public_key" in leaf_names
            and shared_ssh_public_key
            and all(alias not in inputs for alias in ("ssh_public_key", "ssh-public-key"))
        ):
            inputs["ssh_public_key"] = shared_ssh_public_key


@dataclass(frozen=True)
class CIWorkflowBootstrapResult:
    repo_root: Path
    workflow_file: Path
    wrote_workflow: bool


def _ensure_ci_workflow_for_deployments_root(
    *,
    deployments_root: Path,
    force: bool,
) -> CIWorkflowBootstrapResult:
    repo_root = _require_git_root(deployments_root)
    workflows_path = repo_root / ".github" / "workflows"
    workflow_file = workflows_path / "nebius-deployments.yml"
    deployments_dir_for_ci = _relative_deployments_dir_for_ci(repo_root, deployments_root)
    discover_target_for_ci = _relative_discover_target_for_ci(repo_root, deployments_root)

    workflows_path.mkdir(parents=True, exist_ok=True)

    wrote_workflow = False
    if workflow_file.exists() and not force:
        return CIWorkflowBootstrapResult(
            repo_root=repo_root,
            workflow_file=workflow_file,
            wrote_workflow=wrote_workflow,
        )

    workflow_file.write_text(
        customer_workflow_yaml(
            deployments_dir=deployments_dir_for_ci,
            discover_target=discover_target_for_ci,
            cli_ref=default_cli_ref(),
        ),
        encoding="utf-8",
    )
    wrote_workflow = True
    return CIWorkflowBootstrapResult(
        repo_root=repo_root,
        workflow_file=workflow_file,
        wrote_workflow=wrote_workflow,
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
        inferred_ssh_public_key = _infer_default_ssh_public_key()

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
        _seed_default_shared_ssh_public_key(
            payload=starter_payload,
            inferred_key=inferred_ssh_public_key,
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
                "Edit config.yaml manually before validate/deploy."
            )
        console.print(
            "Next steps: run `nebius-cxcli validate <config.yaml>`, "
            "`nebius-cxcli bootstrap-ci <config.yaml>` (optional), then "
            "`nebius-cxcli deploy <config.yaml>`."
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
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite existing .github/workflows/nebius-deployments.yml when present",
        ),
    ] = False,
    auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auth-bootstrap/--no-auth-bootstrap",
            help=(
                "Bootstrap Nebius CI auth material and sync GitHub Actions secrets "
                "(enabled by default)"
            ),
        ),
    ] = True,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "GitHub repository slug '<owner>/<repo>' for secret sync "
                "(optional; falls back to git origin remote; "
                "valid only when --auth-bootstrap is enabled)"
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help=(
                "Environment variable name holding GitHub token for secret sync "
                "(falls back to GH_TOKEN/GITHUB_TOKEN; "
                "valid only when --auth-bootstrap is enabled)"
            ),
        ),
    ] = "GH_TOKEN",
) -> None:
    """Generate customer CI workflow and optionally bootstrap CI auth/secrets."""
    try:
        if not auth_bootstrap and (github_repo is not None or github_token_env != "GH_TOKEN"):
            raise RuntimeError(
                "--github-repo and --github-token-env are valid only when --auth-bootstrap is enabled."
            )

        config, paths = _load_context(config_path)
        workflow = _ensure_ci_workflow_for_deployments_root(
            deployments_root=paths.deployments_dir,
            force=force,
        )

        if auth_bootstrap:
            _auto_bootstrap_ci_auth_and_secrets(
                project_id=config.client_info.nebius.project_id,
                repo_root=workflow.repo_root,
                service_account_name="nebius-cxcli-ci",
                service_account_description="Service account used by nebius-cxcli CI automation",
                role_ids=["roles/editor"],
                auth_key_description="nebius-cxcli CI authorized key",
                access_key_description="nebius-cxcli CI Object Storage access key",
                github_repo=github_repo,
                github_token_env=github_token_env,
                profile=None,
                endpoint=None,
                sdk_config_file=None,
            )

        console.print(f"Repository root: {workflow.repo_root}")
        if workflow.wrote_workflow:
            console.print(f"Created: {workflow.workflow_file}")
        else:
            console.print(f"Workflow exists, keeping current file: {workflow.workflow_file}")
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
) -> None:
    """Validate config.yaml with runtime source + provider/chart checks."""
    try:
        config, _ = _load_context(config_path)
        dependency_issues = _validate_component_dependencies(config)
        if dependency_issues:
            raise RuntimeError("Runtime validation failed:\n  - " + "\n  - ".join(dependency_issues))
        if strict:
            _validate_strict_config(config)
            console.print(f"[green]Valid (strict):[/green] {config_path}")
            return
        console.print(f"[green]Valid:[/green] {config_path}")
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


@auth_app.command("bootstrap")
def auth_bootstrap_command(
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help="Nebius project ID for the service account and generated keys",
        ),
    ] = None,
    instance_config: Annotated[
        Path | None,
        typer.Option(
            "--instance-config",
            help=(
                "Optional path to an existing instance config.yaml; "
                "used to read client_info.nebius.project_id when --project-id is omitted"
            ),
        ),
    ] = None,
    service_account_name: Annotated[
        str,
        typer.Option(
            "--service-account-name",
            help="Service account name to create/reuse for CI",
        ),
    ] = "nebius-cxcli-ci",
    service_account_description: Annotated[
        str,
        typer.Option(
            "--service-account-description",
            help="Description applied when creating the service account",
        ),
    ] = "Service account used by nebius-cxcli CI automation",
    role_id: Annotated[
        list[str] | None,
        typer.Option(
            "--role-id",
            help=("Role to grant on the target project (repeatable). Default: roles/editor"),
        ),
    ] = None,
    auth_key_description: Annotated[
        str,
        typer.Option(
            "--auth-key-description",
            help="Description for the created authorized key",
        ),
    ] = "nebius-cxcli CI authorized key",
    access_key_description: Annotated[
        str,
        typer.Option(
            "--access-key-description",
            help="Description for the created S3 access key",
        ),
    ] = "nebius-cxcli CI Object Storage access key",
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Nebius CLI profile name used by Nebius SDK"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Optional Nebius API endpoint override"),
    ] = None,
    sdk_config_file: Annotated[
        Path | None,
        typer.Option(
            "--sdk-config-file",
            help="Optional path to Nebius SDK/CLI config file",
        ),
    ] = None,
    private_key_out: Annotated[
        Path | None,
        typer.Option(
            "--private-key-out",
            help="Optional file path to write a newly created authorized private key (chmod 600)",
        ),
    ] = None,
    create_keys: Annotated[
        bool,
        typer.Option(
            "--create-keys/--no-create-keys",
            help=(
                "Create fresh Nebius auth/Object Storage keys. "
                "By default, auth bootstrap is identity-only unless GitHub sync "
                "needs missing NEBIUS_* secrets."
            ),
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print machine-readable JSON output (secret values are omitted)",
        ),
    ] = False,
    github_sync: Annotated[
        bool,
        typer.Option(
            "--github-sync/--no-github-sync",
            help="Automatically sync generated CI secrets to GitHub Actions repository secrets",
        ),
    ] = True,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "GitHub repository slug '<owner>/<repo>' for secret sync "
                "(optional; used only when --github-sync is enabled)"
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help=(
                "Environment variable name holding GitHub token for secret sync "
                "(used only when --github-sync is enabled)"
            ),
        ),
    ] = "GH_TOKEN",
    github_set_flux_token: Annotated[
        bool,
        typer.Option(
            "--github-set-flux-token/--no-github-set-flux-token",
            help="Also set FLUX_GITHUB_TOKEN to the GitHub API token used for sync",
        ),
    ] = True,
) -> None:
    """Idempotently ensure CI identity and optionally provision/sync CI secrets."""
    try:
        if not github_sync and (github_repo is not None or github_token_env != "GH_TOKEN"):
            raise RuntimeError(
                "--github-repo and --github-token-env are valid only when --github-sync is enabled."
            )

        resolved_project_id = _resolve_project_id_for_auth_bootstrap(
            project_id=project_id,
            instance_config=instance_config,
        )
        resolved_roles = role_id or ["roles/editor"]
        resolved_sdk_config = sdk_config_file.resolve() if sdk_config_file else None

        synced_secret_names: list[str] = []
        synced_repo_slug: str | None = None
        identity_result = None
        bootstrap_result = None
        created_keys = False
        synced_flux_only = False
        skipped_secret_sync = False

        if github_sync:
            github_token = read_github_token(preferred_env=github_token_env)
            if not github_token:
                raise RuntimeError(
                    "GitHub sync enabled but no token found. "
                    f"Set ${github_token_env}, $GH_TOKEN, or $GITHUB_TOKEN; "
                    "or rerun with --no-github-sync."
                )

            repo_root_hint: Path | None = None
            if instance_config is not None:
                repo_root_hint = _require_git_root(instance_config.resolve().parent)
            elif github_repo is None:
                repo_root_hint = _require_git_root(Path.cwd())

            synced_repo_slug = _resolve_github_repo_slug(
                explicit_repo_slug=github_repo,
                repo_root=repo_root_hint,
            )
            required_secret_names = list(NEBIUS_CI_SECRET_KEYS)
            if github_set_flux_token:
                required_secret_names.append(FLUX_SECRET_KEY)
            presence = repo_secrets_presence(
                repo_slug=synced_repo_slug,
                token=github_token,
                names=required_secret_names,
            )
            nebius_ready = all(presence.get(name, False) for name in NEBIUS_CI_SECRET_KEYS)
            flux_ready = True if not github_set_flux_token else bool(
                presence.get(FLUX_SECRET_KEY, False)
            )
            needs_nebius_secret_sync = not nebius_ready
            needs_flux_secret_sync = github_set_flux_token and not flux_ready
            force_rotate = bool(create_keys)

            if needs_nebius_secret_sync or force_rotate:
                bootstrap_result = bootstrap_ci_service_account(
                    project_id=resolved_project_id,
                    service_account_name=service_account_name,
                    service_account_description=service_account_description,
                    role_ids=resolved_roles,
                    auth_key_description=auth_key_description,
                    access_key_description=access_key_description,
                    profile=profile,
                    endpoint=endpoint,
                    config_file=resolved_sdk_config,
                )
                created_keys = True
                ci_secrets = _ci_github_secrets_payload(
                    service_account_id=bootstrap_result.service_account_id,
                    auth_public_key_id=bootstrap_result.auth_public_key_id,
                    auth_private_key_pem=bootstrap_result.auth_private_key_pem,
                    s3_access_key_id=bootstrap_result.s3_access_key_id,
                    s3_secret_access_key=bootstrap_result.s3_secret_access_key,
                )
                synced_secret_names = _sync_github_ci_secrets(
                    repo_slug=synced_repo_slug,
                    github_token=github_token,
                    ci_secrets=ci_secrets,
                    include_flux_token=github_set_flux_token,
                )
                if private_key_out is not None:
                    private_key_path = private_key_out.resolve()
                    private_key_path.parent.mkdir(parents=True, exist_ok=True)
                    private_key_path.write_text(
                        bootstrap_result.auth_private_key_pem, encoding="utf-8"
                    )
                    private_key_path.chmod(0o600)
            elif needs_flux_secret_sync:
                identity_result = ensure_ci_service_account_identity(
                    project_id=resolved_project_id,
                    service_account_name=service_account_name,
                    service_account_description=service_account_description,
                    role_ids=resolved_roles,
                    profile=profile,
                    endpoint=endpoint,
                    config_file=resolved_sdk_config,
                )
                synced_secret_names = upsert_repo_secrets(
                    repo_slug=synced_repo_slug,
                    token=github_token,
                    secrets={FLUX_SECRET_KEY: github_token},
                )
                synced_flux_only = True
            else:
                identity_result = ensure_ci_service_account_identity(
                    project_id=resolved_project_id,
                    service_account_name=service_account_name,
                    service_account_description=service_account_description,
                    role_ids=resolved_roles,
                    profile=profile,
                    endpoint=endpoint,
                    config_file=resolved_sdk_config,
                )
                skipped_secret_sync = True
        else:
            if create_keys:
                bootstrap_result = bootstrap_ci_service_account(
                    project_id=resolved_project_id,
                    service_account_name=service_account_name,
                    service_account_description=service_account_description,
                    role_ids=resolved_roles,
                    auth_key_description=auth_key_description,
                    access_key_description=access_key_description,
                    profile=profile,
                    endpoint=endpoint,
                    config_file=resolved_sdk_config,
                )
                created_keys = True
                if private_key_out is not None:
                    private_key_path = private_key_out.resolve()
                    private_key_path.parent.mkdir(parents=True, exist_ok=True)
                    private_key_path.write_text(
                        bootstrap_result.auth_private_key_pem, encoding="utf-8"
                    )
                    private_key_path.chmod(0o600)
            else:
                identity_result = ensure_ci_service_account_identity(
                    project_id=resolved_project_id,
                    service_account_name=service_account_name,
                    service_account_description=service_account_description,
                    role_ids=resolved_roles,
                    profile=profile,
                    endpoint=endpoint,
                    config_file=resolved_sdk_config,
                )

        if json_output:
            # Keep JSON output constant and non-secret-bearing.
            print(json.dumps({"status": "ok"}, sort_keys=True))
            return

        console.print("CI auth bootstrap completed.")
        result_identity = identity_result or bootstrap_result
        if result_identity and result_identity.service_account_created:
            console.print("Service account created.")
        else:
            console.print("Service account reused.")
        if result_identity and result_identity.roles_created:
            console.print("Role grants applied.")
        if result_identity and result_identity.roles_already_present:
            console.print("Role grants already present.")
        if created_keys:
            console.print("Authorized key created.")
            console.print("Object Storage access key created.")
        else:
            console.print("No key rotation performed.")
        if private_key_out is not None and created_keys:
            console.print("Private key file written.")
        if github_sync and synced_repo_slug is not None:
            if skipped_secret_sync:
                console.print(
                    f"GitHub Actions secrets already present in {synced_repo_slug}; no sync changes."
                )
            elif synced_flux_only:
                console.print(
                    f"Synced missing FLUX_GITHUB_TOKEN to {synced_repo_slug}."
                )
            else:
                console.print(
                    f"Synced GitHub Actions secrets to {synced_repo_slug} "
                    f"({len(synced_secret_names)} secret(s))"
                )

        if not github_sync:
            console.print(
                "GitHub sync is disabled. Re-run with --create-keys when you need fresh key material."
            )
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("render")
def render_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Render deterministic Terraform and Flux artifacts under generated/."""
    try:
        config, paths = _load_context(config_path)
        result = render_instance(config, paths)
        lock_generated = _try_generate_terraform_lock_file(config, paths)
        console.print(f"Rendered {len(result.files_written)} file(s) under {paths.generated_dir}")
        if lock_generated:
            console.print(f"Generated Terraform lock file: {paths.infra_dir / '.terraform.lock.hcl'}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("deploy")
def deploy_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
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
    """Validate (strict), render, terraform apply, and apply generated Flux manifests."""
    try:
        config, paths = _load_context(config_path)
        rendered_count = _render_and_local_deploy(
            config,
            paths,
            auto_auth_bootstrap=auto_auth_bootstrap,
        )
        console.print(f"Rendered {rendered_count} file(s) under {paths.generated_dir}")
        console.print("Local deploy completed.")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("plan")
def terraform_plan_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Run terraform init and terraform plan in generated/infra."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(
            config,
            need_terraform=True,
            need_eso_mysterybox=False,
            auto_bootstrap=auto_auth_bootstrap,
        )
        terraform_plan(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("apply")
def terraform_apply_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = True,
) -> None:
    """Run terraform init and terraform apply in generated/infra."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(
            config,
            need_terraform=True,
            need_eso_mysterybox=False,
            auto_bootstrap=auto_auth_bootstrap,
        )
        terraform_apply(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command("bootstrap")
def flux_bootstrap_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap",
            help="Automatically bootstrap runtime auth when env vars are missing",
        ),
    ] = False,
) -> None:
    """Bootstrap Flux if missing, otherwise reconcile for idempotent day-2 runs."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(
            config,
            need_terraform=False,
            need_eso_mysterybox=False,
            auto_bootstrap=auto_auth_bootstrap,
        )
        action = ensure_flux(paths)
        console.print(f"Flux {action} for {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("discover")
def discover_command(
    target_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Deployments root folder path. Works with any existing directory; "
                "uses git change detection when target path is inside a git repository, "
                "otherwise scans all config.yaml files."
            )
        ),
    ],
    include_all: Annotated[
        bool,
        typer.Option("--all", help="Include all config.yaml files instead of changed only"),
    ] = False,
) -> None:
    """Print discover JSON payload for config.yaml files in this run."""
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
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Write local non-sensitive inventory files."""
    try:
        config, paths = _load_context(config_path)
        artifacts = write_inventory(config, paths)
        console.print(f"Inventory written: {artifacts.markdown}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@inventory_app.command("upload")
def inventory_upload_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Upload inventory files to Nebius Object Storage."""
    try:
        config, paths = _load_context(config_path)
        keys = upload_inventory(config, paths)
        console.print(f"Uploaded {len(keys)} inventory object(s)")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("email")
def email_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Send inventory markdown via SMTP to client_info.notifications.email."""
    try:
        config, paths = _load_context(config_path)
        sent = send_inventory_email(config, paths)
        if sent:
            console.print("Inventory email sent")
        else:
            console.print("client_info.notifications.email not configured; nothing sent")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


def main() -> None:
    app()
