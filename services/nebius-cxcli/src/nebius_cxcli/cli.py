"""Typer CLI for nebius-cxcli."""

from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

from . import __version__
from .config_loader import load_config
from .config_template import starter_config_yaml
from .discover_ops import discover_configs
from .flux_ops import ensure_flux
from .github_secrets import (
    detect_github_repo_slug,
    read_github_token,
    repo_secrets_presence,
    upsert_repo_secrets,
)
from .iam_bootstrap import bootstrap_ci_service_account
from .inventory_ops import upload_inventory, write_inventory
from .notify_ops import send_inventory_email
from .paths import InstancePaths, resolve_instance_paths, validate_path_alignment
from .render import render_instance
from .schema import ConfigV1, Environment
from .schema_catalog import list_schema_fields
from .templates import customer_workflow_yaml, default_cli_ref
from .terraform_ops import terraform_apply, terraform_plan

console = Console()
INTERACTIVE_SUBNET_PLACEHOLDER = "subnet-REPLACE-ME"
NEBIUS_CI_SECRET_KEYS = [
    "NEBIUS_SA_ID",
    "NEBIUS_AUTH_PUBLIC_KEY_ID",
    "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
]
FLUX_SECRET_KEY = "FLUX_GITHUB_TOKEN"
_TEMP_PRIVATE_KEY_FILES: list[Path] = []
app = typer.Typer(
    add_completion=False,
    help="Nebius Customer Experience CLI for easy deployments.",
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
) -> None:
    _ = version


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


def _require_git_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return Path(result.stdout.strip()).resolve()
    except Exception as exc:
        raise RuntimeError(
            "Target path must be inside a git repository. "
            "Clone the customer private repo and rerun this command."
        ) from exc


def _validate_deployments_root_target(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(
            f"Deployments root does not exist: {path}. "
            "Create an empty folder in the private repo and pass that path to create/discover."
        )
    if not path.is_dir():
        raise RuntimeError(f"Deployments root must be a directory: {path}")


def _parse_env_or_prompt(value: Environment | None, *, interactive: bool) -> Environment:
    if value is not None:
        return value
    if not interactive:
        raise RuntimeError("Missing required option: --env")
    while True:
        raw = typer.prompt("Environment (dev|stage|prod)", default=Environment.PROD.value).strip()
        try:
            return Environment(raw)
        except ValueError:
            console.print("[red]Invalid environment[/red]. Expected one of: dev, stage, prod")


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


def _region_or_prompt(value: str | None, *, interactive: bool) -> str:
    if value:
        return value
    if interactive:
        return typer.prompt("Region ID", default="eu-north1").strip() or "eu-north1"
    return "eu-north1"


def _subnet_or_prompt(value: str | None, *, interactive: bool) -> str:
    if value:
        return value
    if interactive:
        return INTERACTIVE_SUBNET_PLACEHOLDER
    raise RuntimeError("Missing required option: --subnet-id")


def _validate_strict_config(config: ConfigV1) -> None:
    """Validate deployment-readiness constraints beyond schema shape."""
    issues: list[str] = []

    if config.infra.mk8s.enabled and config.infra.mk8s.subnet_id == INTERACTIVE_SUBNET_PLACEHOLDER:
        issues.append(
            "infra.mk8s.subnet_id still uses starter placeholder "
            f"'{INTERACTIVE_SUBNET_PLACEHOLDER}'"
        )

    ssh_key = config.infra.ssh_public_key
    if "REPLACE-WITH-YOUR-KEY" in ssh_key:
        issues.append("infra.ssh_public_key still uses starter placeholder key")

    if config.apps.workloads.n8n.enabled and config.apps.workloads.n8n.route.hostname.endswith(
        ".example.internal"
    ):
        issues.append(
            "apps.workloads.n8n.route.hostname still uses starter placeholder domain "
            "'.example.internal'"
        )

    if issues:
        raise RuntimeError("Strict validation failed:\n  - " + "\n  - ".join(issues))


def _mysterybox_secret_values_from_env(config: ConfigV1) -> dict[str, dict[str, str]]:
    if not config.infra.mysterybox.enabled:
        return {}

    values: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for secret in config.infra.mysterybox.secrets:
        secret_values: dict[str, str] = {}
        for entry in secret.entries:
            env_name = entry.value_from_env
            env_value = os.environ.get(env_name)
            if env_value is None or not env_value:
                missing.append(f"{secret.id}.{entry.key} <- ${env_name}")
                continue
            secret_values[entry.key] = env_value
        values[secret.id] = secret_values

    if missing:
        raise RuntimeError(
            "Missing environment values for MysteryBox payload entries:\n  - "
            + "\n  - ".join(missing)
        )
    return values


def _mysterybox_secret_metadata_for_tf_var(config: ConfigV1) -> list[dict[str, object]]:
    metadata: list[dict[str, object]] = []
    for secret in config.infra.mysterybox.secrets:
        item: dict[str, object] = {
            "id": secret.id,
            "scope": secret.scope,
            "name": secret.name,
            "labels": secret.labels,
            "set_primary": secret.set_primary,
            "payload_keys": [entry.key for entry in secret.entries],
        }
        if secret.description is not None:
            item["description"] = secret.description
        if secret.version_description is not None:
            item["version_description"] = secret.version_description
        metadata.append(item)
    return metadata


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


def _requires_eso_mysterybox_auth(config: ConfigV1) -> bool:
    external_secrets = config.apps.platform.external_secrets
    return bool(external_secrets.enabled and external_secrets.mysterybox.enabled)


def _runtime_auth_missing_envs(
    *,
    need_terraform: bool,
    need_eso_mysterybox: bool,
) -> list[str]:
    # Reuse NEBIUS_S3_* as source of truth when AWS_* are not set.
    if not os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("NEBIUS_S3_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = os.environ["NEBIUS_S3_ACCESS_KEY_ID"]
    if not os.environ.get("AWS_SECRET_ACCESS_KEY") and os.environ.get(
        "NEBIUS_S3_SECRET_ACCESS_KEY"
    ):
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["NEBIUS_S3_SECRET_ACCESS_KEY"]

    required: list[str] = []
    if need_terraform or need_eso_mysterybox:
        required.extend(["NEBIUS_SA_ID", "NEBIUS_AUTH_PUBLIC_KEY_ID"])
    if need_terraform:
        required.extend(["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
    if need_eso_mysterybox:
        required.append("NEBIUS_AUTH_PRIVATE_KEY_PEM")

    missing = [name for name in required if not os.environ.get(name)]
    has_private_key_file = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
    has_private_key_pem = bool(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_PEM"))
    if (
        (need_terraform or need_eso_mysterybox)
        and not (has_private_key_file or has_private_key_pem)
        and "NEBIUS_AUTH_PRIVATE_KEY_PEM" not in missing
    ):
        missing.append("NEBIUS_AUTH_PRIVATE_KEY_PEM")
    return missing


def _ensure_runtime_auth_material(
    config: ConfigV1,
    *,
    need_terraform: bool,
    need_eso_mysterybox: bool,
) -> None:
    missing = _runtime_auth_missing_envs(
        need_terraform=need_terraform,
        need_eso_mysterybox=need_eso_mysterybox,
    )
    if missing:
        result = bootstrap_ci_service_account(
            project_id=config.client_info.nebius.project_id,
            service_account_name="nebius-cxcli-runtime",
            service_account_description=(
                "Service account used by nebius-cxcli local runtime automation"
            ),
            role_ids=["roles/editor"],
            auth_key_description="nebius-cxcli local runtime authorized key",
            access_key_description="nebius-cxcli local runtime Object Storage access key",
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
        console.print(
            "[green]Auto-bootstrapped runtime auth[/green] "
            "(service account + Object Storage key + auth key) for this command run."
        )

    if need_terraform or need_eso_mysterybox:
        _ensure_private_key_file_env()


def _terraform_runtime_env(config: ConfigV1) -> dict[str, str]:
    if not config.infra.mysterybox.enabled:
        return {}
    secret_metadata = _mysterybox_secret_metadata_for_tf_var(config)
    secret_values = _mysterybox_secret_values_from_env(config)
    return {
        "TF_VAR_mysterybox_secrets": json.dumps(secret_metadata, sort_keys=True),
        "TF_VAR_mysterybox_secret_values": json.dumps(secret_values, sort_keys=True),
    }


def _apply_rendered_flux(paths: InstancePaths) -> None:
    """Apply rendered Flux manifests in local deploy mode."""
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required for --deploy but was not found in PATH")

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
                "Install Flux CLI (`flux`) and rerun --deploy, or use --bootstrap-ci for CI-driven bootstrap."
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


def _apply_kubernetes_doc(doc: dict[str, object]) -> None:
    subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml.safe_dump(doc, sort_keys=False),
        text=True,
        check=True,
        timeout=120,
    )


def _seed_external_secrets_mysterybox_auth_secret(config: ConfigV1) -> None:
    external_secrets = config.apps.platform.external_secrets
    if not (external_secrets.enabled and external_secrets.mysterybox.enabled):
        return

    required_env_names = [
        "NEBIUS_SA_ID",
        "NEBIUS_AUTH_PUBLIC_KEY_ID",
        "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    ]
    missing = [name for name in required_env_names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing environment values required for ESO MysteryBox sync auth secret:\n  - "
            + "\n  - ".join(missing)
            + "\nSet these env vars before running local deploy/flux bootstrap."
        )

    mysterybox = external_secrets.mysterybox
    bridge_auth = mysterybox.bridge.auth
    bridge_namespace = external_secrets.namespace
    secret_namespace = (
        external_secrets.mysterybox.auth_secret_namespace or external_secrets.namespace
    )
    bridge_auth_namespace = bridge_auth.secret_namespace or bridge_namespace
    namespaces = {secret_namespace}
    if bridge_auth.enabled:
        namespaces.add(bridge_auth_namespace)
        namespaces.add(bridge_namespace)
    for namespace in sorted(namespaces):
        _apply_kubernetes_doc(
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
        )

    endpoint = os.environ.get("NEBIUS_ENDPOINT", "api.nebius.cloud:443").strip()
    secret_doc = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": mysterybox.auth_secret_name,
            "namespace": secret_namespace,
        },
        "type": "Opaque",
        "stringData": {
            "NEBIUS_PROJECT_ID": config.client_info.nebius.project_id,
            "NEBIUS_SA_ID": os.environ["NEBIUS_SA_ID"],
            "NEBIUS_AUTH_PUBLIC_KEY_ID": os.environ["NEBIUS_AUTH_PUBLIC_KEY_ID"],
            "NEBIUS_AUTH_PRIVATE_KEY_PEM": os.environ["NEBIUS_AUTH_PRIVATE_KEY_PEM"],
            "NEBIUS_ENDPOINT": endpoint or "api.nebius.cloud:443",
        },
    }
    _apply_kubernetes_doc(secret_doc)

    if bridge_auth.enabled:
        bridge_token = os.environ.get(
            "NEBIUS_MYSTERYBOX_WEBHOOK_TOKEN", ""
        ).strip() or secrets.token_urlsafe(32)
        bridge_token_secret_doc = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": bridge_auth.secret_name,
                "namespace": bridge_auth_namespace,
                "labels": {"external-secrets.io/type": "webhook"},
            },
            "type": "Opaque",
            "stringData": {
                bridge_auth.secret_key: bridge_token,
            },
        }
        _apply_kubernetes_doc(bridge_token_secret_doc)

        # Pod secretKeyRef cannot read across namespaces; keep a local copy for bridge pod if needed.
        if bridge_auth_namespace != bridge_namespace:
            local_bridge_secret_doc = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": bridge_auth.secret_name,
                    "namespace": bridge_namespace,
                    "labels": {"external-secrets.io/type": "webhook"},
                },
                "type": "Opaque",
                "stringData": {
                    bridge_auth.secret_key: bridge_token,
                },
            }
            _apply_kubernetes_doc(local_bridge_secret_doc)


def _render_and_local_deploy(config: ConfigV1, paths: InstancePaths) -> int:
    """Run strict validation, render, Terraform apply, then apply Flux manifests."""
    _validate_strict_config(config)
    _ensure_runtime_auth_material(
        config,
        need_terraform=True,
        need_eso_mysterybox=_requires_eso_mysterybox_auth(config),
    )
    result = render_instance(config, paths)
    terraform_apply(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    _seed_external_secrets_mysterybox_auth_secret(config)
    _apply_rendered_flux(paths)
    return len(result.files_written)


def _resolve_keep_client_config_path(*, deployments_root: Path, config_file: Path | None) -> Path:
    if config_file is None:
        raise RuntimeError("--config-file is required when --keep-client-info is set")

    candidate = config_file
    if not candidate.is_absolute():
        candidate = deployments_root / candidate
    candidate = candidate.resolve()

    if not candidate.exists():
        raise RuntimeError(f"--config-file does not exist: {candidate}")
    if not candidate.is_file():
        raise RuntimeError(f"--config-file must point to a file: {candidate}")
    if candidate.name != "config.yaml":
        raise RuntimeError(f"--config-file must point to a config.yaml file: {candidate}")

    try:
        candidate.relative_to(deployments_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"--config-file must be inside deployments root '{deployments_root}': {candidate}"
        ) from exc

    return candidate


def _infer_deployments_root_from_config_file(config_file: Path | None) -> Path:
    if config_file is None:
        raise RuntimeError("--config-file is required when TARGET_PATH is omitted")

    candidate = config_file if config_file.is_absolute() else (Path.cwd() / config_file)
    candidate = candidate.resolve()

    if not candidate.exists():
        raise RuntimeError(f"--config-file does not exist: {candidate}")
    if not candidate.is_file():
        raise RuntimeError(f"--config-file must point to a file: {candidate}")
    if candidate.name != "config.yaml":
        raise RuntimeError(f"--config-file must point to a config.yaml file: {candidate}")

    for parent in candidate.parents:
        instances_dir = parent / "instances"
        if not instances_dir.is_dir():
            continue
        try:
            rel = candidate.relative_to(parent)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "instances":
            return parent

    raise RuntimeError(
        "Could not infer deployments root from --config-file. "
        "Expected path like <deployments-root>/instances/.../config.yaml."
    )


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
        console.print(f"Configured missing GitHub secret(s) in {repo_slug}: {', '.join(updated)}")
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
    console.print(f"Bootstrapped and synced CI auth secrets to {repo_slug}: {', '.join(updated)}")


@dataclass(frozen=True)
class BootstrapResult:
    repo_root: Path | None
    deployments_root: Path
    workflow_file: Path | None
    wrote_workflow: bool
    config_path: Path
    wrote_config: bool


def _ensure_customer_scaffold(
    *,
    base_path: Path,
    force: bool,
    bootstrap_ci: bool,
) -> tuple[Path | None, Path, Path | None, bool]:
    deployments_root = _resolve_deployments_root(base_path)
    deployments_path = deployments_root / "instances"

    deployments_path.mkdir(parents=True, exist_ok=True)

    if not bootstrap_ci:
        return None, deployments_root, None, False

    repo_root = _require_git_root(base_path)
    workflows_path = repo_root / ".github" / "workflows"
    workflow_file = workflows_path / "nebius-deployments.yml"
    deployments_dir_for_ci = _relative_deployments_dir_for_ci(repo_root, deployments_root)
    discover_target_for_ci = _relative_discover_target_for_ci(repo_root, deployments_root)

    workflows_path.mkdir(parents=True, exist_ok=True)

    wrote_workflow = False
    if workflow_file.exists() and not force:
        return repo_root, deployments_root, workflow_file, wrote_workflow

    workflow_file.write_text(
        customer_workflow_yaml(
            deployments_dir=deployments_dir_for_ci,
            discover_target=discover_target_for_ci,
            cli_ref=default_cli_ref(),
        ),
        encoding="utf-8",
    )
    wrote_workflow = True
    return repo_root, deployments_root, workflow_file, wrote_workflow


def _scaffold_instance(
    *,
    base_path: Path,
    client_name: str,
    tenant_id: str,
    env: Environment,
    cluster_name: str,
    project_id: str,
    region_id: str,
    subnet_id: str,
    email: str | None,
    force: bool,
    bootstrap_ci: bool,
) -> BootstrapResult:
    repo_root, deployments_root, workflow_file, wrote_workflow = _ensure_customer_scaffold(
        base_path=base_path,
        force=force,
        bootstrap_ci=bootstrap_ci,
    )
    instance_dir = (
        deployments_root / "instances" / f"{client_name}--{tenant_id}" / env.value / cluster_name
    )
    config_path = instance_dir / "config.yaml"

    (instance_dir / "generated" / "infra").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "flux" / "sources").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "flux" / "apps" / "platform").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "flux" / "apps" / "workloads").mkdir(parents=True, exist_ok=True)
    (instance_dir / "generated" / "inventory").mkdir(parents=True, exist_ok=True)

    wrote_config = False
    if not config_path.exists() or force:
        config_path.write_text(
            starter_config_yaml(
                client_name=client_name,
                tenant_id=tenant_id,
                env=env.value,
                cluster_name=cluster_name,
                project_id=project_id,
                region_id=region_id,
                subnet_id=subnet_id,
                email=email,
            ),
            encoding="utf-8",
        )
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
        repo_root=repo_root,
        deployments_root=deployments_root,
        workflow_file=workflow_file,
        wrote_workflow=wrote_workflow,
        config_path=config_path,
        wrote_config=wrote_config,
    )


@app.command("create")
def create_command(
    target_path: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Deployments root folder path in the customer private repository. "
                "Optional only when using --keep-client-info with --config-file."
            )
        ),
    ] = None,
    client_name: Annotated[
        str | None,
        typer.Option("--client-name", help="Client slug (lowercase letters/digits/hyphens)"),
    ] = None,
    tenant_id: Annotated[
        str | None, typer.Option("--tenant-id", help="Nebius tenant identifier")
    ] = None,
    env: Annotated[
        Environment | None, typer.Option("--env", help="Environment: dev | stage | prod")
    ] = None,
    cluster_name: Annotated[
        str | None, typer.Option("--cluster-name", help="Cluster name slug")
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
    subnet_id: Annotated[
        str | None,
        typer.Option("--subnet-id", help="VPC subnet ID for MK8s control plane"),
    ] = None,
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            help="Optional notifications email for inventory updates",
        ),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", help="Prompt for missing instance values"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Overwrite existing workflow/config.yaml files "
                "(required when refreshing an existing instance config)"
            ),
        ),
    ] = False,
    keep_client_info: Annotated[
        bool,
        typer.Option(
            "--keep-client-info",
            help=(
                "When an instance config already exists, prefill missing create values from it "
                "(client_info identity, nebius IDs, region, email). "
                "This intentionally does not keep subnet_id; pass --subnet-id to keep/set it. "
                "Requires --config-file. Use --force to overwrite an existing target config.yaml."
            ),
        ),
    ] = False,
    config_file: Annotated[
        Path | None,
        typer.Option(
            "--config-file",
            help=(
                "Existing instance config.yaml path to use with --keep-client-info "
                "(absolute path; or relative to deployments root when TARGET_PATH is set; "
                "or relative to current directory when TARGET_PATH is omitted)"
            ),
        ),
    ] = None,
    auto_auth_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-auth-bootstrap/--no-auto-auth-bootstrap",
            help=(
                "Automatically bootstrap Nebius CI auth and sync required GitHub "
                "Actions secrets (used only with --bootstrap-ci; "
                "fails if GitHub token/repo context is unavailable)"
            ),
        ),
    ] = True,
    github_repo: Annotated[
        str | None,
        typer.Option(
            "--github-repo",
            help=(
                "GitHub repository slug '<owner>/<repo>' for secret sync "
                "(optional; used only with --bootstrap-ci)"
            ),
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help=(
                "Environment variable name holding GitHub token for secret sync "
                "(used only with --bootstrap-ci)"
            ),
        ),
    ] = "GH_TOKEN",
    auth_profile: Annotated[
        str | None,
        typer.Option(
            "--auth-profile",
            help="Nebius profile used for automatic auth bootstrap (optional; --bootstrap-ci)",
        ),
    ] = None,
    auth_endpoint: Annotated[
        str | None,
        typer.Option(
            "--auth-endpoint",
            help="Nebius API endpoint override used for automatic auth bootstrap (--bootstrap-ci)",
        ),
    ] = None,
    auth_sdk_config_file: Annotated[
        Path | None,
        typer.Option(
            "--auth-sdk-config-file",
            help="Nebius SDK/CLI config path used for automatic auth bootstrap (--bootstrap-ci)",
        ),
    ] = None,
    deploy: Annotated[
        bool,
        typer.Option(
            "--deploy",
            help=(
                "After create, run local deployment steps: validate --strict, render, "
                "terraform apply, and apply generated Flux manifests with kubectl"
            ),
        ),
    ] = False,
    bootstrap_ci: Annotated[
        bool,
        typer.Option(
            "--bootstrap-ci",
            help=(
                "Generate customer GitHub Actions workflow at repository root "
                "(requires target path inside a git repository)"
            ),
        ),
    ] = False,
) -> None:
    """Create one instance under deployments root (scaffold-only by default)."""
    try:
        if target_path is None:
            if keep_client_info and config_file is not None:
                base_path = _infer_deployments_root_from_config_file(config_file)
            else:
                raise RuntimeError(
                    "Missing TARGET_PATH. Provide deployments root path, or use "
                    "--keep-client-info with --config-file to infer it."
                )
        else:
            base_path = target_path.resolve()
        _validate_deployments_root_target(base_path)

        if deploy and bootstrap_ci:
            raise RuntimeError(
                "--deploy and --bootstrap-ci are mutually exclusive. "
                "Use --bootstrap-ci for CI automation, or --deploy for local one-shot deployment."
            )

        if config_file is not None and not keep_client_info:
            raise RuntimeError("--config-file can only be used together with --keep-client-info")

        if keep_client_info:
            selected_config = _resolve_keep_client_config_path(
                deployments_root=base_path,
                config_file=config_file,
            )
            existing = load_config(selected_config)
            client_name = client_name or existing.client_info.client_name
            tenant_id = tenant_id or existing.client_info.nebius.tenant_id
            env = env or existing.client_info.env
            cluster_name = cluster_name or existing.client_info.cluster_name
            project_id = project_id or existing.client_info.nebius.project_id
            region_id = region_id or existing.client_info.nebius.region_id
            if email is None:
                email = existing.client_info.notifications.email
            if subnet_id is None:
                subnet_id = INTERACTIVE_SUBNET_PLACEHOLDER

        resolved_client_name = _value_or_prompt(
            client_name,
            option_name="--client-name",
            prompt_text="Client name",
            interactive=interactive,
        )
        resolved_tenant_id = _value_or_prompt(
            tenant_id,
            option_name="--tenant-id",
            prompt_text="Tenant ID",
            interactive=interactive,
        )
        resolved_env = _parse_env_or_prompt(env, interactive=interactive)
        resolved_cluster_name = _value_or_prompt(
            cluster_name,
            option_name="--cluster-name",
            prompt_text="Cluster name",
            interactive=interactive,
        )
        resolved_project_id = _value_or_prompt(
            project_id,
            option_name="--project-id",
            prompt_text="Project ID",
            interactive=interactive,
        )
        resolved_region_id = _region_or_prompt(region_id, interactive=interactive)
        resolved_subnet_id = _subnet_or_prompt(subnet_id, interactive=interactive)
        resolved_email = _optional_email_or_prompt(email, interactive=interactive)

        result = _scaffold_instance(
            base_path=base_path,
            client_name=resolved_client_name,
            tenant_id=resolved_tenant_id,
            env=resolved_env,
            cluster_name=resolved_cluster_name,
            project_id=resolved_project_id,
            region_id=resolved_region_id,
            subnet_id=resolved_subnet_id,
            email=resolved_email,
            force=force,
            bootstrap_ci=bootstrap_ci,
        )

        if bootstrap_ci and auto_auth_bootstrap:
            if result.repo_root is None:
                raise RuntimeError(
                    "Internal error: repository root is required for automatic auth bootstrap"
                )
            _auto_bootstrap_ci_auth_and_secrets(
                project_id=resolved_project_id,
                repo_root=result.repo_root,
                service_account_name="nebius-cxcli-ci",
                service_account_description="Service account used by nebius-cxcli CI automation",
                role_ids=["roles/editor"],
                auth_key_description="nebius-cxcli CI authorized key",
                access_key_description="nebius-cxcli CI Object Storage access key",
                github_repo=github_repo,
                github_token_env=github_token_env,
                profile=auth_profile,
                endpoint=auth_endpoint,
                sdk_config_file=auth_sdk_config_file.resolve() if auth_sdk_config_file else None,
            )

        console.print(f"Deployments root: {result.deployments_root}")
        if bootstrap_ci and result.repo_root is not None and result.workflow_file is not None:
            console.print(f"Repository root: {result.repo_root}")
            if result.wrote_workflow:
                console.print(f"Created: {result.workflow_file}")
            else:
                console.print(f"Workflow exists, keeping current file: {result.workflow_file}")
        if result.wrote_config:
            console.print(f"Created: {result.config_path}")
        else:
            console.print(f"Config exists, keeping current file: {result.config_path}")
        console.print(f"Ensured generated skeleton: {result.config_path.parent / 'generated'}")
        if deploy:
            validate_config, paths = _load_context(result.config_path)
            rendered_count = _render_and_local_deploy(validate_config, paths)
            console.print(f"Rendered {rendered_count} file(s) under {paths.generated_dir}")
            console.print("Local deploy completed.")
        else:
            console.print("Next step: run `nebius-cxcli validate <config.yaml>`.")
        console.print(
            "[yellow]Security warning:[/yellow] keep this customer repository private "
            "because the deployments root contains sensitive operational metadata."
        )
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
    """Validate config.yaml schema and path alignment."""
    try:
        config, _ = _load_context(config_path)
        if strict:
            _validate_strict_config(config)
            console.print(f"[green]Valid (strict):[/green] {config_path}")
            return
        console.print(f"[green]Valid:[/green] {config_path}")
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
            help="Optional file path to write the generated authorized private key (chmod 600)",
        ),
    ] = None,
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
            help="GitHub repository slug '<owner>/<repo>' for secret sync (optional)",
        ),
    ] = None,
    github_token_env: Annotated[
        str,
        typer.Option(
            "--github-token-env",
            help="Environment variable name holding GitHub token for secret sync",
        ),
    ] = "GH_TOKEN",
    github_set_flux_token: Annotated[
        bool,
        typer.Option(
            "--github-set-flux-token/--no-github-set-flux-token",
            help="Also set FLUX_GITHUB_TOKEN to the GitHub API token used for sync",
        ),
    ] = True,
    print_secrets: Annotated[
        bool,
        typer.Option(
            "--print-secrets",
            help="Print secret values to stdout (disabled by default for safer operation)",
        ),
    ] = False,
) -> None:
    """Create/reuse CI service account and output GitHub secret values."""
    try:
        resolved_project_id = _resolve_project_id_for_auth_bootstrap(
            project_id=project_id,
            instance_config=instance_config,
        )
        resolved_roles = role_id or ["roles/editor"]

        result = bootstrap_ci_service_account(
            project_id=resolved_project_id,
            service_account_name=service_account_name,
            service_account_description=service_account_description,
            role_ids=resolved_roles,
            auth_key_description=auth_key_description,
            access_key_description=access_key_description,
            profile=profile,
            endpoint=endpoint,
            config_file=sdk_config_file.resolve() if sdk_config_file else None,
        )

        if private_key_out is not None:
            private_key_path = private_key_out.resolve()
            private_key_path.parent.mkdir(parents=True, exist_ok=True)
            private_key_path.write_text(result.auth_private_key_pem, encoding="utf-8")
            private_key_path.chmod(0o600)

        ci_secrets = _ci_github_secrets_payload(
            service_account_id=result.service_account_id,
            auth_public_key_id=result.auth_public_key_id,
            auth_private_key_pem=result.auth_private_key_pem,
            s3_access_key_id=result.s3_access_key_id,
            s3_secret_access_key=result.s3_secret_access_key,
        )

        synced_secret_names: list[str] = []
        synced_repo_slug: str | None = None
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
            synced_secret_names = _sync_github_ci_secrets(
                repo_slug=synced_repo_slug,
                github_token=github_token,
                ci_secrets=ci_secrets,
                include_flux_token=github_set_flux_token,
            )

        if json_output:
            safe_summary = {
                "status": "ok",
                "project_id": resolved_project_id,
                "github_secret_keys": sorted(NEBIUS_CI_SECRET_KEYS),
                "github_sync": github_sync,
                "github_synced_repo": synced_repo_slug,
                "github_synced_secret_count": len(synced_secret_names),
                "private_key_written": private_key_out is not None,
            }
            print(json.dumps(safe_summary, sort_keys=True))
            return

        console.print(
            f"Service account: {result.service_account_id} "
            f"({'created' if result.service_account_created else 'existing'})"
        )
        if result.roles_created:
            console.print(f"Roles granted: {', '.join(result.roles_created)}")
        if result.roles_already_present:
            console.print(f"Roles already present: {', '.join(result.roles_already_present)}")
        console.print(f"Authorized key created: {result.auth_public_key_id}")
        console.print(f"Object Storage access key created: {result.s3_access_key_id}")
        if private_key_out is not None:
            console.print(f"Private key written: {private_key_out.resolve()}")
        if github_sync and synced_repo_slug is not None:
            console.print(
                f"Synced GitHub Actions secrets to {synced_repo_slug}: "
                f"{', '.join(synced_secret_names)}"
            )

        if (not github_sync) or print_secrets:
            console.print("\nSet these GitHub Actions secrets:")
            console.print(f"NEBIUS_SA_ID={result.service_account_id}")
            console.print(f"NEBIUS_AUTH_PUBLIC_KEY_ID={result.auth_public_key_id}")
            console.print(f"NEBIUS_S3_ACCESS_KEY_ID={result.s3_access_key_id}")
            console.print(f"NEBIUS_S3_SECRET_ACCESS_KEY={result.s3_secret_access_key}")
            console.print("NEBIUS_AUTH_PRIVATE_KEY_PEM<<EOF")
            console.print(result.auth_private_key_pem.rstrip())
            console.print("EOF")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("render")
def render_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
    deploy: Annotated[
        bool,
        typer.Option(
            "--deploy",
            help=(
                "After render, run local deployment steps: validate --strict, terraform apply, "
                "and apply generated Flux manifests with kubectl"
            ),
        ),
    ] = False,
) -> None:
    """Render Terraform and Flux files under generated/."""
    try:
        config, paths = _load_context(config_path)
        if deploy:
            rendered_count = _render_and_local_deploy(config, paths)
            console.print(f"Rendered {rendered_count} file(s) under {paths.generated_dir}")
            console.print("Local deploy completed.")
            return
        result = render_instance(config, paths)
        console.print(f"Rendered {len(result.files_written)} file(s) under {paths.generated_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("plan")
def terraform_plan_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Run terraform init and terraform plan in generated/infra."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(config, need_terraform=True, need_eso_mysterybox=False)
        terraform_plan(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@terraform_app.command("apply")
def terraform_apply_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Run terraform init and terraform apply in generated/infra."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(config, need_terraform=True, need_eso_mysterybox=False)
        terraform_apply(paths.infra_dir, extra_env=_terraform_runtime_env(config))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@flux_app.command("bootstrap")
def flux_bootstrap_command(
    config_path: Annotated[Path, typer.Argument(help="Path to instance config.yaml")],
) -> None:
    """Bootstrap Flux if missing, otherwise reconcile for idempotent day-2 runs."""
    try:
        config, paths = _load_context(config_path)
        _ensure_runtime_auth_material(
            config,
            need_terraform=False,
            need_eso_mysterybox=_requires_eso_mysterybox_auth(config),
        )
        _seed_external_secrets_mysterybox_auth_secret(config)
        action = ensure_flux(paths)
        console.print(f"Flux {action} for {paths.flux_dir}")
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("discover")
def discover_command(
    target_path: Annotated[
        Path,
        typer.Argument(help="Deployments root folder path in the customer private repository"),
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
        repo_root = _require_git_root(base_path)
        deployments_root = _resolve_deployments_root(base_path)
        deployments_dir_for_ci = _relative_deployments_dir_for_ci(repo_root, deployments_root)
        payload = discover_configs(
            deployments_dir=deployments_dir_for_ci,
            include_all=include_all,
            repo_root=repo_root,
        )
        print(json.dumps(payload, sort_keys=True))
    except Exception as exc:  # pragma: no cover - CLI surface
        _exit_with_error(exc)


@app.command("list")
def list_command(
    schema_path: Annotated[
        str,
        typer.Argument(
            help="Schema path to inspect, for example infra.mk8s (or use 'config' for root)"
        ),
    ] = "config",
    required_only: Annotated[
        bool,
        typer.Option("--required", help="Show only required fields"),
    ] = False,
    optional_only: Annotated[
        bool,
        typer.Option("--optional", help="Show only optional fields"),
    ] = False,
    all_fields: Annotated[
        bool,
        typer.Option("--all", help="Show all fields"),
    ] = False,
) -> None:
    """List schema fields and whether each field is required or optional."""
    try:
        selected_filters = sum(int(flag) for flag in (required_only, optional_only, all_fields))
        if selected_filters > 1:
            raise ValueError("Use only one of --required, --optional, or --all")

        entries = list_schema_fields(schema_path)
        child_map: dict[str, list] = {}
        for entry in entries:
            parent = entry.path.rsplit(".", maxsplit=1)[0] if "." in entry.path else ""
            child_map.setdefault(parent, []).append(entry)

        def _has_children(path: str) -> bool:
            return path in child_map

        def _has_required_descendant(path: str) -> bool:
            prefix = f"{path}."
            return any(
                candidate.required and candidate.path.startswith(prefix) for candidate in entries
            )

        if required_only:
            entries = [
                entry
                for entry in entries
                if entry.required
                and (
                    not _has_children(entry.path)
                    or (_has_children(entry.path) and not _has_required_descendant(entry.path))
                )
            ]
        elif optional_only:
            entries = [
                entry for entry in entries if not entry.required and not _has_children(entry.path)
            ]

        if not entries:
            console.print("No fields match the current filter.")
            return

        headers = ("FIELD", "STATUS", "TYPE", "DEFAULT")
        rows: list[tuple[str, str, str, str]] = []
        for entry in entries:
            status = "required" if entry.required else "optional"
            default = "-" if entry.default_value is None else entry.default_value
            rows.append((entry.path, status, entry.type_name, default))

        # Keep FIELD intact; shrink TYPE/DEFAULT first when terminal width is limited.
        max_allowed = [10_000, 8, 30, 24]
        min_allowed = [24, 8, 12, 8]
        separator = "  "
        widths = []
        for col in range(len(headers)):
            content_width = max(len(headers[col]), *(len(row[col]) for row in rows))
            widths.append(min(max(content_width, min_allowed[col]), max_allowed[col]))

        terminal_width = shutil.get_terminal_size(fallback=(120, 20)).columns
        total_width = sum(widths) + (len(widths) - 1) * len(separator)
        while total_width > terminal_width:
            reduced = False
            for col in (2, 3):
                if widths[col] > min_allowed[col] and total_width > terminal_width:
                    widths[col] -= 1
                    total_width -= 1
                    reduced = True
            if not reduced:
                break

        def _clip(value: str, width: int) -> str:
            if len(value) <= width:
                return value
            if width <= 3:
                return "." * width
            return value[: width - 3] + "..."

        def _format_row(values: tuple[str, str, str, str]) -> str:
            rendered: list[str] = []
            for idx, value in enumerate(values):
                if idx == 0:
                    # Do not truncate FIELD column.
                    rendered.append(value.ljust(widths[idx]))
                    continue
                rendered.append(_clip(value, widths[idx]).ljust(widths[idx]))
            return separator.join(rendered)

        print(_format_row(headers))
        print(
            _format_row(
                tuple("-" * widths[idx] for idx in range(len(headers)))  # type: ignore[arg-type]
            )
        )
        for row in rows:
            print(_format_row(row))
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
